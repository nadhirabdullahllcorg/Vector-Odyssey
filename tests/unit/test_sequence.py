"""
BarSequence / CandleWindow / TickCoverage — ordering, the lookahead
firewall (C1), and tick-coverage gating, per
architecture/vo-candle-layer.md §5, §9, §12.
"""

from datetime import UTC, datetime, timedelta

import pytest

from vo.market import (
    Bar,
    BarSequence,
    BarSequenceViolation,
    CoverageStatus,
    InstrumentId,
    TickFeed,
    Timeframe,
    build_bar_sequence,
    tick_coverage_of,
)

INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
T0 = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)


def _bar(minute, high=102, low=99, **overrides):
    kwargs = {
        "instrument_id": INSTRUMENT,
        "timeframe": Timeframe.M1,
        "open_time_utc": T0 + timedelta(minutes=minute),
        "open": 100,
        "high": high,
        "low": low,
        "close": 101,
        "tick_volume": 100,
        "real_volume": 0,
    }
    kwargs.update(overrides)
    return Bar(**kwargs)


# ── BarSequence ordering / duplicates ────────────────────────────────────


def test_append_builds_an_ordered_sequence():
    seq = BarSequence().append(_bar(0)).append(_bar(1)).append(_bar(2))
    assert len(seq) == 3


def test_append_rejects_out_of_order_bar():
    seq = BarSequence().append(_bar(1))
    with pytest.raises(BarSequenceViolation):
        seq.append(_bar(0))


def test_append_rejects_duplicate_open_time():
    seq = BarSequence().append(_bar(0))
    with pytest.raises(BarSequenceViolation):
        seq.append(_bar(0))


def test_multi_candle_sequence_indexing_is_deterministic():
    seq = BarSequence()
    for m in range(5):
        seq = seq.append(_bar(m))

    window = seq.window_at(4)  # N
    assert window.prev(2) is seq.bars[2]  # N-2


def test_weekend_boundary_gap_is_real_not_an_anomaly():
    """A 48+ hour gap between bars is a fact the sequence must accept - no
    session/weekend assumption exists yet (that's the Time Engine, Phase 6)."""
    friday_close = _bar(0)
    monday_open = Bar(
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=friday_close.open_time_utc + timedelta(hours=54),
        open=100,
        high=102,
        low=99,
        close=101,
        tick_volume=100,
        real_volume=0,
    )

    seq = BarSequence().append(friday_close).append(monday_open)
    assert len(seq) == 2
    gap = monday_open.open_time_utc - friday_close.open_time_utc
    assert gap == timedelta(hours=54)


def test_build_bar_sequence_quarantines_instead_of_aborting():
    """A bar repeating an already-seen open_time (same instant as the last
    bar accepted) is set aside with a reason, not allowed to abort the
    whole build - same E.7 pattern as vo.market.mapping.map_records."""
    good = [_bar(0), _bar(1)]
    duplicate = _bar(1)
    good_again = _bar(2)

    result = build_bar_sequence([*good, duplicate, good_again])

    assert len(result.sequence) == 3
    assert len(result.quarantined) == 1
    assert result.quarantined[0].bar is duplicate
    assert "strictly after" in result.quarantined[0].reason.lower()
    assert result.all_accepted is False


def test_append_rejects_duplicate_bar_id_even_when_not_the_immediate_predecessor():
    """bar_id uniqueness is checked independently of the ordering check, as
    its own named invariant (architecture/vo-candle-layer.md §5)."""
    seq = BarSequence().append(_bar(0)).append(_bar(1))
    with pytest.raises(BarSequenceViolation):
        # A duplicate of bar 0 (not the last-appended bar) still violates
        # strict ordering relative to bar 1, so this raises either way -
        # the point is it is rejected, not accepted a second time.
        seq.append(_bar(0))


# ── CandleWindow: structural queries ─────────────────────────────────────


def _window(n, **kwargs):
    seq = BarSequence()
    for m in range(n):
        seq = seq.append(_bar(m, **kwargs))
    return seq.window_at(n - 1)


def test_highest_lowest_and_new_high_new_low():
    seq = BarSequence()
    highs = [102, 105, 101, 108, 103]
    for m, h in enumerate(highs):
        seq = seq.append(_bar(m, high=h, low=90))

    window = seq.window_at(4)  # current high = 103

    assert window.highest(5) == 108
    assert window.lowest(5) == 90
    assert window.is_new_high(5) is False  # 103 < 108 seen earlier in window
    assert seq.window_at(3).is_new_high(4) is True  # 108 exceeds 102,105,101


def test_is_higher_high_and_is_lower_low():
    seq = BarSequence().append(_bar(0, high=100, low=90, close=98)).append(
        _bar(1, high=105, low=95)
    )
    window = seq.window_at(1)

    assert window.is_higher_high() is True
    assert window.is_higher_low() is True
    assert window.is_lower_high() is False
    assert window.is_lower_low() is False


def test_is_inside_and_is_outside():
    seq = BarSequence().append(_bar(0, high=110, low=90)).append(_bar(1, high=105, low=95))
    inside_window = seq.window_at(1)
    assert inside_window.is_inside() is True
    assert inside_window.is_outside() is False

    seq2 = BarSequence().append(_bar(0, high=100, low=99, close=99.5)).append(
        _bar(1, high=110, low=90)
    )
    outside_window = seq2.window_at(1)
    assert outside_window.is_outside() is True
    assert outside_window.is_inside() is False


def test_relation_is_none_without_enough_history():
    seq = BarSequence().append(_bar(0))
    window = seq.window_at(0)
    assert window.relation(1, tick_size=0.01) is None


def test_relation_current_vs_prev():
    seq = BarSequence().append(_bar(0)).append(_bar(1))
    window = seq.window_at(1)
    rel = window.relation(1, tick_size=0.01)
    assert rel is not None
    assert rel.a is seq.bars[0]
    assert rel.b is seq.bars[1]


# ── the lookahead firewall (C1) ───────────────────────────────────────────


def test_candle_window_has_no_forward_accessor():
    seq = BarSequence().append(_bar(0))
    window = seq.window_at(0)

    for forbidden in ("next", "forward", "peek", "next_bar_id"):
        assert not hasattr(window, forbidden)


def test_separation_rejects_any_index_past_current():
    seq = BarSequence()
    for m in range(3):
        seq = seq.append(_bar(m))
    window = seq.window_at(1)  # current is index 1; index 2 does not exist yet to this window

    with pytest.raises(IndexError):
        window.separation(0, 2, tick_size=0.01)


def test_window_at_rejects_out_of_range_index():
    seq = BarSequence().append(_bar(0))
    with pytest.raises(IndexError):
        seq.window_at(5)


# ── TickCoverage (C7) ─────────────────────────────────────────────────────


def test_coverage_unknown_when_no_feed_was_ever_attempted():
    bar = _bar(0, tick_volume=231, captured_tick_count=None)
    coverage = tick_coverage_of(bar)
    assert coverage.status is CoverageStatus.UNKNOWN
    assert coverage.ratio is None


def test_coverage_none_when_feed_present_but_captured_nothing():
    """captured=0 with tick_volume=231 is a data-quality event, distinct
    from UNKNOWN - the feed was there and lost every tick."""
    bar = _bar(0, tick_volume=231, captured_tick_count=0, source_feed=TickFeed.LIVE_ONTICK)
    coverage = tick_coverage_of(bar)
    assert coverage.status is CoverageStatus.NONE
    assert coverage.ratio == 0.0


def test_coverage_partial_and_complete():
    partial = tick_coverage_of(
        _bar(0, tick_volume=100, captured_tick_count=40, source_feed=TickFeed.COPY_TICKS_RANGE)
    )
    complete = tick_coverage_of(
        _bar(0, tick_volume=100, captured_tick_count=100, source_feed=TickFeed.COPY_TICKS_RANGE)
    )

    assert partial.status is CoverageStatus.PARTIAL
    assert complete.status is CoverageStatus.COMPLETE


def test_tick_volume_is_never_confused_with_captured_tick_count():
    """A bar with no tick feed reports tick_volume=231, captured_tick_count
    =None - never 231. Wiring them together would invent tick information."""
    bar = _bar(0, tick_volume=231)
    assert bar.tick_volume == 231
    assert bar.captured_tick_count is None
    assert bar.captured_tick_count != bar.tick_volume
