"""vo.market.aggregate -- M1 -> higher-timeframe bars (§13b, deliverable A)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from vo.core.replay import ReplayHarness, assert_no_lookahead
from vo.market.aggregate import (
    AggregationError,
    Aggregator,
    aggregate_all,
    aggregate_bars,
    bucket_start,
)
from vo.market.bar import Bar, DataQuality
from vo.market.identity import InstrumentId
from vo.market.sequence import BarSequence, CandleWindow
from vo.market.timeframe import Timeframe
from vo.time.calendars import trading_day_of

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")
_NY = ZoneInfo("America/New_York")
_OPENS = time(18, 0)


def _bar(when: datetime, *, o: float, h: float, lo: float, c: float, vol: int = 1, **over) -> Bar:
    fields = dict(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=when,
        open=o,
        high=h,
        low=lo,
        close=c,
        tick_volume=vol,
        real_volume=0,
    )
    fields.update(over)
    return Bar(**fields)


def _minutes(start: datetime, n: int, *, base: float = 100.0) -> list[Bar]:
    """n consecutive M1 bars: close climbs by 1 per bar, each bar a 2-point range."""
    out = []
    for i in range(n):
        p = base + i
        out.append(_bar(start + timedelta(minutes=i), o=p, h=p + 1.5, lo=p - 0.5, c=p + 1, vol=10))
    return out


# ── bucket alignment ───────────────────────────────────────────────────


def test_clock_alignment_floors_to_the_bucket_in_utc() -> None:
    t = datetime(2026, 9, 18, 14, 37, tzinfo=UTC)
    assert bucket_start(t, Timeframe.M5) == datetime(2026, 9, 18, 14, 35, tzinfo=UTC)
    assert bucket_start(t, Timeframe.M15) == datetime(2026, 9, 18, 14, 30, tzinfo=UTC)
    assert bucket_start(t, Timeframe.H1) == datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    assert bucket_start(t, Timeframe.H4) == datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def test_d1_requires_a_trading_day_model() -> None:
    with pytest.raises(AggregationError, match="D1 aggregation needs"):
        bucket_start(datetime(2026, 9, 18, 14, 37, tzinfo=UTC), Timeframe.D1)


def test_trading_day_alignment_anchors_h4_at_the_18_00_et_open() -> None:
    # 18 Sep 2026, 09:31 ET (13:31 UTC, EDT): inside the trading day that
    # opened 17 Sep 18:00 ET. H4 buckets from that open: 18, 22, 02, 06, 10, 14.
    t = datetime(2026, 9, 18, 13, 31, tzinfo=UTC)
    h4 = bucket_start(t, Timeframe.H4, zone=_NY, day_opens=_OPENS)
    assert h4.astimezone(_NY).hour == 6 and h4.astimezone(_NY).minute == 0
    h1 = bucket_start(t, Timeframe.H1, zone=_NY, day_opens=_OPENS)
    assert h1.astimezone(_NY).hour == 9


def test_d1_bucket_is_the_trading_day_and_agrees_with_calendars() -> None:
    zone = _NY
    for utc in (
        datetime(2026, 9, 17, 22, 0, tzinfo=UTC),  # 18:00 ET on the 17th -> trading day 18th
        datetime(2026, 9, 18, 3, 59, tzinfo=UTC),  # 23:59 ET on the 17th -> still the 18th
        datetime(2026, 9, 18, 13, 30, tzinfo=UTC),  # 09:30 ET on the 18th -> the 18th
        datetime(2026, 9, 18, 21, 59, tzinfo=UTC),  # 17:59 ET on the 18th -> the 18th
        datetime(2026, 9, 18, 22, 0, tzinfo=UTC),  # 18:00 ET on the 18th -> the 19th
    ):
        start = bucket_start(utc, Timeframe.D1, zone=zone, day_opens=_OPENS)
        local = start.astimezone(zone)
        assert local.timetz().replace(tzinfo=None) == _OPENS
        # The trading day's date, per the CME convention, is the day AFTER the open.
        expected = trading_day_of(utc.astimezone(zone), _OPENS)
        assert local.date() + timedelta(days=1) == expected


# ── aggregation ────────────────────────────────────────────────────────


def test_m5_bars_have_bucket_start_ohlc_and_summed_volume() -> None:
    start = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    m1 = _minutes(start, 11)  # 14:00..14:10 -> two closed M5 buckets, one open
    m5 = aggregate_bars(m1, Timeframe.M5)
    assert [b.open_time_utc for b in m5] == [start, start + timedelta(minutes=5)]
    first = m5[0]
    assert first.timeframe is Timeframe.M1 or first.timeframe is Timeframe.M5
    assert first.timeframe is Timeframe.M5
    assert first.open == m1[0].open
    assert first.close == m1[4].close
    assert first.high == max(b.high for b in m1[:5])
    assert first.low == min(b.low for b in m1[:5])
    assert first.tick_volume == 50
    assert first.provenance is None and first.spread is None


def test_the_open_bucket_is_never_emitted_as_a_bar() -> None:
    start = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    agg = Aggregator(Timeframe.M5)
    emitted = [agg.push(b) for b in _minutes(start, 5)]  # exactly one full bucket, not closed
    assert emitted == [None] * 5
    assert agg.partial is not None and agg.partial.constituents == 5
    closed = agg.push(_minutes(start + timedelta(minutes=5), 1)[0])  # next bucket opens
    assert closed is not None and closed.open_time_utc == start


def test_a_bucket_with_missing_minutes_is_still_one_bar() -> None:
    start = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    m1 = _minutes(start, 3) + _minutes(start + timedelta(minutes=7), 4)  # 14:00-02, 14:07-10
    m5 = aggregate_bars(m1, Timeframe.M5)
    assert len(m5) == 2
    assert m5[0].tick_volume == 30 and m5[1].tick_volume == 30
    assert m5[1].open_time_utc == start + timedelta(minutes=5)  # bucket start, not 14:07


def test_worst_constituent_quality_taints_the_aggregate() -> None:
    start = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    m1 = _minutes(start, 6)
    m1[2] = _bar(
        m1[2].open_time_utc, o=102, h=103.5, lo=101.5, c=103,
        quality=DataQuality.SUSPECT, quality_reason="gap",
    )
    m5 = aggregate_bars(m1, Timeframe.M5)
    assert m5[0].quality is DataQuality.SUSPECT
    assert "SUSPECT" in (m5[0].quality_reason or "")


def test_out_of_order_and_mixed_inputs_are_rejected() -> None:
    start = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    agg = Aggregator(Timeframe.M5)
    agg.push(_minutes(start, 1)[0])
    with pytest.raises(AggregationError, match="strictly increasing"):
        agg.push(_minutes(start, 1)[0])
    with pytest.raises(AggregationError, match="cannot aggregate"):
        agg.push(_bar(start + timedelta(hours=1), o=1, h=2, lo=0, c=1, timeframe=Timeframe.H1))
    with pytest.raises(AggregationError, match="unsupported"):
        Aggregator(Timeframe.MN1)


def test_aggregate_all_returns_every_target_from_one_source() -> None:
    start = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)  # 18:00 ET: a trading-day open
    m1 = _minutes(start, 60 * 5 + 1)  # five hours and a minute
    out = aggregate_all(m1, (Timeframe.M15, Timeframe.H1, Timeframe.H4), zone=_NY, day_opens=_OPENS)
    assert len(out[Timeframe.M15]) == 20
    assert len(out[Timeframe.H1]) == 5
    assert len(out[Timeframe.H4]) == 1
    assert out[Timeframe.H4][0].open_time_utc == start


def test_d1_aggregation_splits_at_the_18_00_et_open_not_midnight() -> None:
    start = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)  # 18:00 ET Sep 17
    m1 = _minutes(start, 24 * 60 + 1)  # through 18:00 ET Sep 18 inclusive
    d1 = aggregate_bars(m1, Timeframe.D1, zone=_NY, day_opens=_OPENS)
    assert len(d1) == 1
    assert d1[0].open_time_utc == start
    assert d1[0].tick_volume == 24 * 60 * 10


# ── gate G3: no lookahead, driven by the same harness as every engine ──


class _AggregatingProbe:
    """Emits the closed M5 bars known as of each M1 bar -- a probe whose
    output at bar i must not depend on any bar after i."""

    def __init__(self) -> None:
        self._agg = Aggregator(Timeframe.M5)

    def on_bar(self, window: CandleWindow) -> object:
        closed = self._agg.push(window.current)
        if closed is None:
            return None
        return (closed.open_time_utc, closed.high, closed.low, closed.close)


def test_aggregator_passes_the_no_lookahead_harness() -> None:
    start = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)
    seq = BarSequence()
    for b in _minutes(start, 40):
        seq = seq.append(b)
    ReplayHarness(seq).run(_AggregatingProbe())  # runs clean
    assert_no_lookahead(seq, _AggregatingProbe, cutoff=17)
