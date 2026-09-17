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
    build_regime_markers,
    build_regime_segments,
    feed_line,
    marker_line,
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
    assert "markers=0" in lines[0]  # this scenario's lone RETRACEMENT is open-ended, not dropped
    assert len(lines) == 1 + len(segments)

    # Each band line: a "BAND" tag then 9 delimited fields (10 total).
    for line in lines[1:]:
        fields = line.split(FEED_DELIMITER)
        assert fields[0] == "BAND"
        assert len(fields) == 10

    # The open-ended final band carries end_epoch 0.
    last = lines[-1].split(FEED_DELIMITER)
    assert last[2] == "RETRACEMENT"
    assert last[5] == "0"


def test_feed_line_encodes_absent_direction_and_anticipation() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    consolidation = segments[0]  # no direction, no anticipation
    line = feed_line(consolidation, start_epoch=10, end_epoch=20)
    fields = line.split(FEED_DELIMITER)
    assert fields[0] == "BAND"
    assert fields[3] == "NONE"  # direction
    assert fields[9] == ""  # anticipated


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


def _resolution_scenario() -> tuple[list[RegimeState], list[Bar]]:
    """EXPANSION -> PULLBACK_UNRESOLVED -> RETRACEMENT, with the
    RETRACEMENT immediately followed (same bar) by a new EXPANSION --
    the real production shape (RegimeEngine._resolve_retracement emits
    both in the same on_bar call). The RETRACEMENT run is zero-width
    (its own start_utc == the following EXPANSION run's start_utc) and
    must be dropped as a segment but still produce a marker."""
    bars = [
        _bar(0, high=100.0, low=99.0),
        _bar(1, high=101.0, low=99.5),
        _bar(2, high=104.0, low=100.5),  # the resolving bar
        _bar(3, high=107.0, low=103.0),  # new expansion continues
    ]
    states = [
        _state(RegimeType.EXPANSION, 0, direction=RegimeDirection.UP),
        _state(RegimeType.PULLBACK_UNRESOLVED, 1),
        _state(
            RegimeType.RETRACEMENT,
            2,
            direction=RegimeDirection.UP,
            confidence=1.0,
            supersedes="US100:M1:REGIME:PULLBACK_UNRESOLVED:1",
        ),
        _state(RegimeType.EXPANSION, 2, direction=RegimeDirection.UP, suffix="b"),
    ]
    return states, bars


def test_build_regime_markers_emits_one_per_retracement_reversal_state() -> None:
    states, bars = _resolution_scenario()

    segments = build_regime_segments(states, bars)
    markers = build_regime_markers(states, bars)

    # The RETRACEMENT run is zero-width (shares its start with the very
    # next EXPANSION run) and correctly dropped as a segment...
    assert RegimeType.RETRACEMENT not in [s.regime for s in segments]
    # ...but it still produced a marker, priced at its own bar's close.
    assert len(markers) == 1
    marker = markers[0]
    assert marker.regime is RegimeType.RETRACEMENT
    assert marker.at_utc == _at(2)
    assert marker.price == bars[2].close
    assert marker.object_id == "US100:M1:REGIME:RETRACEMENT:2"


def test_marker_line_format_and_delimiter_guard() -> None:
    states, bars = _resolution_scenario()
    marker = build_regime_markers(states, bars)[0]

    line = marker_line(marker, at_epoch=42)
    fields = line.split(FEED_DELIMITER)
    assert fields[0] == "MARK"
    assert len(fields) == 7
    assert fields[2] == "RETRACEMENT"
    assert fields[3] == "UP"
    assert fields[4] == "42"

    poisoned = type(marker)(
        regime=marker.regime,
        direction=marker.direction,
        at_utc=marker.at_utc,
        price=marker.price,
        confidence=marker.confidence,
        object_id=f"bad{FEED_DELIMITER}id",
        methodology_version=marker.methodology_version,
        instrument_key=marker.instrument_key,
        timeframe_canonical=marker.timeframe_canonical,
    )
    with pytest.raises(ValueError, match="delimiter"):
        marker_line(poisoned, at_epoch=1)


def test_render_feed_lines_tags_and_counts_bands_and_markers_separately() -> None:
    states, bars = _resolution_scenario()
    segments = build_regime_segments(states, bars)
    markers = build_regime_markers(states, bars)

    lines = render_feed_lines(
        segments,
        markers,
        epoch_of=lambda dt: int(dt.timestamp()),
        generated_utc=datetime(2026, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert f"segments={len(segments)}" in lines[0]
    assert f"markers={len(markers)}" in lines[0]
    assert len(lines) == 1 + len(segments) + len(markers)

    tags = [line.split(FEED_DELIMITER)[0] for line in lines[1:]]
    assert tags.count("BAND") == len(segments)
    assert tags.count("MARK") == len(markers)
    # A marker line can never be mistaken for a band even by field count.
    for line in lines[1:]:
        fields = line.split(FEED_DELIMITER)
        if fields[0] == "MARK":
            assert len(fields) == 7
        else:
            assert len(fields) == 10
