"""Unit tests for vo.telemetry.regime_feed -- the Phase 13a regime chart
feed. These pin the segment-collapse logic and the delimited line format
the MQL5 indicator parses; the RegimeEngine itself is tested elsewhere."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import (
    OBJECT_TYPE_REGIME_STATE,
    AnticipatedResolution,
    RegimeDirection,
    RegimeState,
    RegimeType,
)
from vo.telemetry.regime_feed import (
    FEED_DELIMITER,
    build_regime_segments,
    feed_line,
    render_feed_lines,
)

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test-Server", broker_symbol="US100")


def _at(minute: int) -> datetime:
    return datetime(2026, 1, 1, 0, minute, tzinfo=UTC)


def _bar(minute: int, high: float, low: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        tick_volume=1,
        real_volume=0,
        spread=1,
    )


def _state(
    regime: RegimeType,
    minute: int,
    *,
    direction: RegimeDirection | None = None,
    confidence: float = 0.6,
    anticipated: AnticipatedResolution | None = None,
    supersedes: str | None = None,
    suffix: str = "",
) -> RegimeState:
    return RegimeState(
        object_type=OBJECT_TYPE_REGIME_STATE,
        object_id=f"US100:M1:REGIME:{regime.name}:{minute}{suffix}",
        observed_at=_at(minute),
        recorded_at=_at(minute),
        methodology_version=1,
        supersedes=supersedes,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        regime=regime,
        direction=direction,
        confidence=confidence,
        evidence="test",
        anticipated_resolution=anticipated,
    )


def _scenario() -> tuple[list[RegimeState], list[Bar]]:
    """CONSOLIDATION -> EXPANSION -> pullback (two records, one refresh) ->
    RETRACEMENT, over six bars with distinct highs/lows."""
    bars = [
        _bar(0, high=100.0, low=99.0),
        _bar(1, high=101.0, low=99.5),
        _bar(2, high=105.0, low=100.0),  # expansion pushes the high
        _bar(3, high=104.0, low=102.0),  # pullback
        _bar(4, high=103.5, low=101.5),  # pullback, deeper low
        _bar(5, high=106.0, low=103.0),  # retracement makes a new high
    ]
    states = [
        _state(RegimeType.CONSOLIDATION, 0),
        _state(RegimeType.EXPANSION, 2, direction=RegimeDirection.UP),
        _state(
            RegimeType.PULLBACK_UNRESOLVED,
            3,
            anticipated=AnticipatedResolution.RETRACEMENT,
        ),
        _state(
            RegimeType.PULLBACK_UNRESOLVED,
            4,
            anticipated=AnticipatedResolution.REVERSAL,
            supersedes="US100:M1:REGIME:PULLBACK_UNRESOLVED:3",
            suffix="b",
        ),
        _state(
            RegimeType.RETRACEMENT,
            5,
            direction=RegimeDirection.UP,
            confidence=1.0,
            supersedes="US100:M1:REGIME:PULLBACK_UNRESOLVED:4b",
        ),
    ]
    return states, bars


def test_consecutive_same_regime_records_collapse_into_one_segment() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    assert [s.regime for s in segments] == [
        RegimeType.CONSOLIDATION,
        RegimeType.EXPANSION,
        RegimeType.PULLBACK_UNRESOLVED,
        RegimeType.RETRACEMENT,
    ]
    # The two pullback records became a single band.
    pullback = segments[2]
    assert pullback.start_utc == _at(3)
    assert pullback.end_utc == _at(5)
    # Its representative record is the FRESHEST (superseding) one: G8 identity
    # and the freshest lean travel with the band.
    assert pullback.object_id.endswith(":4b")
    assert pullback.anticipated is AnticipatedResolution.REVERSAL


def test_segment_high_low_bounds_only_its_own_bars() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    consolidation = segments[0]  # bars at minutes 0,1 -> [99.0..101.0]
    assert consolidation.high == 101.0
    assert consolidation.low == 99.0

    expansion = segments[1]  # only bar at minute 2 -> [100.0..105.0]
    assert expansion.high == 105.0
    assert expansion.low == 100.0

    pullback = segments[2]  # bars 3,4 -> high 104.0, low 101.5
    assert pullback.high == 104.0
    assert pullback.low == 101.5


def test_final_open_ended_segment_extends_to_last_bar() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    retracement = segments[-1]
    assert retracement.regime is RegimeType.RETRACEMENT
    assert retracement.start_utc == _at(5)
    # No later record: the band stays open-ended (the indicator extends it to
    # the live chart edge). high/low still bound its own bars.
    assert retracement.end_utc is None
    assert retracement.high == 106.0
    assert retracement.low == 103.0


def test_empty_states_yield_no_segments() -> None:
    _states, bars = _scenario()
    assert build_regime_segments([], bars) == ()


def test_render_feed_lines_has_header_then_one_line_per_band() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    lines = render_feed_lines(
        segments,
        epoch_of=lambda dt: int(dt.timestamp()),
        generated_utc=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
    )
    assert lines[0].startswith("#")
    assert "segments=4" in lines[0]
    assert len(lines) == 1 + len(segments)

    # Each band line: 9 delimited fields.
    for line in lines[1:]:
        assert len(line.split(FEED_DELIMITER)) == 9

    # The open-ended final band carries end_epoch 0.
    last = lines[-1].split(FEED_DELIMITER)
    assert last[1] == "RETRACEMENT"
    assert last[4] == "0"


def test_feed_line_encodes_absent_direction_and_anticipation() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    consolidation = segments[0]  # no direction, no anticipation
    line = feed_line(consolidation, start_epoch=10, end_epoch=20)
    fields = line.split(FEED_DELIMITER)
    assert fields[2] == "NONE"  # direction
    assert fields[8] == ""  # anticipated


def test_feed_line_rejects_object_id_containing_the_delimiter() -> None:
    states, bars = _scenario()
    segment = build_regime_segments(states, bars)[0]
    poisoned = type(segment)(
        regime=segment.regime,
        direction=segment.direction,
        start_utc=segment.start_utc,
        end_utc=segment.end_utc,
        high=segment.high,
        low=segment.low,
        confidence=segment.confidence,
        anticipated=segment.anticipated,
        object_id=f"bad{FEED_DELIMITER}id",
        methodology_version=segment.methodology_version,
        instrument_key=segment.instrument_key,
        timeframe_canonical=segment.timeframe_canonical,
    )
    with pytest.raises(ValueError, match="delimiter"):
        feed_line(poisoned, start_epoch=1, end_epoch=2)
