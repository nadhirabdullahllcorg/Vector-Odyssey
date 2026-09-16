from datetime import UTC, datetime

import pytest

from vo.market import Bar, DataQuality, InstrumentId, TickFeed, Timeframe

INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")


def _bar(**overrides):
    kwargs = {
        "instrument_id": INSTRUMENT,
        "timeframe": Timeframe.M1,
        "open_time_utc": datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC),
        "open": 21430.00,
        "high": 21440.00,
        "low": 21425.00,
        "close": 21437.50,
        "tick_volume": 1500,
        "real_volume": 0,
    }
    kwargs.update(overrides)
    return Bar(**kwargs)


def test_bar_creation():
    timestamp = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)

    bar = _bar()

    assert bar.instrument_id == INSTRUMENT
    assert bar.timeframe is Timeframe.M1
    assert bar.open_time_utc == timestamp
    assert bar.open == 21430.00
    assert bar.high == 21440.00
    assert bar.low == 21425.00
    assert bar.close == 21437.50
    assert bar.tick_volume == 1500
    assert bar.real_volume == 0

    # defaults for fields no current wire record can populate yet
    assert bar.spread is None
    assert bar.source_feed is TickFeed.NONE
    assert bar.source_tick_start is None
    assert bar.source_tick_end is None
    assert bar.captured_tick_count is None
    assert bar.quality is DataQuality.VALID
    assert bar.quality_reason is None


def test_bar_id_is_deterministic_and_reproducible():
    bar_a = _bar()
    bar_b = _bar()  # identical inputs, separately constructed

    assert bar_a.bar_id == bar_b.bar_id
    assert bar_a.bar_id == (
        "MT5:1xTrade-Server:US100.n:M1:2026-09-10T07:00:00+00:00"
    )


def test_bar_id_changes_when_identity_or_time_changes():
    base = _bar()
    other_symbol = _bar(
        instrument_id=InstrumentId(
            platform="MT5", broker_server="1xTrade-Server", broker_symbol="US30.n"
        )
    )
    other_timeframe = _bar(timeframe=Timeframe.M5)
    other_time = _bar(open_time_utc=datetime(2026, 9, 10, 7, 1, 0, tzinfo=UTC))

    ids = {base.bar_id, other_symbol.bar_id, other_timeframe.bar_id, other_time.bar_id}
    assert len(ids) == 4


def test_bar_has_no_derived_geometry_fields():
    """Wire purity (C2): Candle's derived attributes must never become Bar
    fields, or they would leak into anything that serializes Bar by walking
    its dataclass fields."""
    from dataclasses import fields

    field_names = {f.name for f in fields(Bar)}
    derived_names = {
        "body_size",
        "body_high",
        "body_low",
        "total_range",
        "upper_wick",
        "lower_wick",
        "body_ratio",
        "direction",
    }

    assert field_names.isdisjoint(derived_names)


def test_bar_has_no_lookahead_fields():
    """C1: no next_bar_id, no previous_bar_id — ordering is a property of
    the sequence, not of the bar."""
    from dataclasses import fields

    field_names = {f.name for f in fields(Bar)}

    assert "next_bar_id" not in field_names
    assert "previous_bar_id" not in field_names


def test_bar_rejects_raw_string_instrument_id():
    with pytest.raises(TypeError):
        _bar(instrument_id="MT5:US100.n")


def test_bar_rejects_raw_string_timeframe():
    with pytest.raises(TypeError):
        _bar(timeframe="M1")


def test_bar_rejects_high_below_low():
    with pytest.raises(ValueError):
        _bar(open=21430.00, high=21420.00, low=21425.00, close=21427.50)


def test_bar_rejects_open_above_high():
    with pytest.raises(ValueError):
        _bar(open=21450.00, high=21440.00, low=21425.00, close=21437.50)


def test_bar_rejects_open_below_low():
    with pytest.raises(ValueError):
        _bar(open=21420.00, high=21440.00, low=21425.00, close=21437.50)


def test_bar_rejects_close_above_high():
    with pytest.raises(ValueError):
        _bar(open=21430.00, high=21440.00, low=21425.00, close=21450.00)


def test_bar_rejects_close_below_low():
    with pytest.raises(ValueError):
        _bar(open=21430.00, high=21440.00, low=21425.00, close=21420.00)


def test_bar_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        _bar(open_time_utc=datetime(2026, 9, 10, 7, 0, 0))


def test_bar_rejects_negative_tick_volume():
    with pytest.raises(ValueError):
        _bar(tick_volume=-1)


def test_bar_rejects_negative_spread():
    with pytest.raises(ValueError):
        _bar(spread=-1)


def test_bar_rejects_negative_captured_tick_count():
    with pytest.raises(ValueError):
        _bar(captured_tick_count=-1)


def test_bar_accepts_zero_captured_tick_count_distinct_from_none():
    """C7: captured=0 (a feed was present but caught nothing) must not
    collide with captured=None (no feed was ever attempted)."""
    unknown = _bar()
    none_captured = _bar(captured_tick_count=0, source_feed=TickFeed.LIVE_ONTICK)

    assert unknown.captured_tick_count is None
    assert none_captured.captured_tick_count == 0
    assert unknown.captured_tick_count != none_captured.captured_tick_count
