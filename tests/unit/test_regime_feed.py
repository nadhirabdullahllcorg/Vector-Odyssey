"""Unit tests for vo.telemetry.regime_feed -- the Phase 13a regime chart
feed. These pin the segment-collapse logic and the delimited line format
the MQL5 indicator parses; the RegimeEngine itself is tested elsewhere."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import (
    OBJECT_TYPE_REGIME_STATE,
    AnticipatedResolution,
    RegimeDirection,
    RegimeFeature,
    RegimeState,
    RegimeType,
)
from vo.telemetry.regime_feed import (
    FEED_DELIMITER,
    build_regime_markers,
    build_regime_segments,
    build_session_boundaries,
    feed_line,
    marker_line,
    render_feed_lines,
    session_boundary_line,
    session_stat_line,
    trim_warmup,
)
from vo.telemetry.regime_report import SessionRegimeStats
from vo.time.sessions import SessionConfig, SessionWindow

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
    supporting_features: tuple[RegimeFeature, ...] = (),
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
        supporting_features=supporting_features,
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


def test_segment_bounds_correct_across_more_than_two_runs() -> None:
    """build_regime_segments walks bar_view once with a single advancing
    run-pointer (replacing an earlier full-history rescan per run -- see
    its own comment for why). Four runs of uneven bar counts, several
    sharing a boundary instant, is enough to catch a pointer left one run
    behind or advanced one run too far."""
    bars = [
        _bar(0, high=10.0, low=9.0),
        _bar(1, high=11.0, low=9.5),
        _bar(2, high=20.0, low=15.0),
        _bar(3, high=30.0, low=25.0),
        _bar(4, high=32.0, low=24.0),
        _bar(5, high=31.0, low=26.0),
        _bar(6, high=40.0, low=39.0),
    ]
    states = [
        _state(RegimeType.CONSOLIDATION, 0),
        _state(RegimeType.EXPANSION, 2, direction=RegimeDirection.UP),
        _state(RegimeType.PULLBACK_UNRESOLVED, 3, anticipated=AnticipatedResolution.RETRACEMENT),
        _state(RegimeType.EXPANSION, 6, direction=RegimeDirection.UP),
    ]
    segments = build_regime_segments(states, bars)

    assert [s.regime for s in segments] == [
        RegimeType.CONSOLIDATION,
        RegimeType.EXPANSION,
        RegimeType.PULLBACK_UNRESOLVED,
        RegimeType.EXPANSION,
    ]
    consolidation, first_expansion, pullback, second_expansion = segments

    assert (consolidation.high, consolidation.low) == (11.0, 9.0)  # bars 0,1
    assert (first_expansion.high, first_expansion.low) == (20.0, 15.0)  # bar 2 only
    assert (pullback.high, pullback.low) == (32.0, 24.0)  # bars 3,4,5
    assert second_expansion.end_utc is None
    assert (second_expansion.high, second_expansion.low) == (40.0, 39.0)  # bar 6 only


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

    # Each band line: a "BAND" tag then 11 delimited fields (12 total).
    for line in lines[1:]:
        fields = line.split(FEED_DELIMITER)
        assert fields[0] == "BAND"
        assert len(fields) == 12

    # The open-ended final band carries end_epoch 0.
    last = lines[-1].split(FEED_DELIMITER)
    assert last[2] == "RETRACEMENT"
    assert last[5] == "0"


def test_feed_line_encodes_absent_direction_and_anticipation() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)

    consolidation = segments[0]  # no direction, no anticipation, no ER/Hurst
    line = feed_line(consolidation, start_epoch=10, end_epoch=20)
    fields = line.split(FEED_DELIMITER)
    assert fields[0] == "BAND"
    assert fields[3] == "NONE"  # direction
    assert fields[9] == ""  # anticipated
    assert fields[10] == ""  # efficiency_ratio -- _scenario()'s states carry no evidence
    assert fields[11] == ""  # hurst_exponent


def test_feed_line_encodes_efficiency_ratio_and_hurst_exponent_when_present() -> None:
    """v5: a band's evidence fields mirror the freshest record's
    RegimeState.supporting_features -- the same [VO-D] evidence already
    recorded, not a new computation, per the user's request to see it
    on the chart."""
    state = _state(
        RegimeType.EXPANSION,
        0,
        direction=RegimeDirection.UP,
        supporting_features=(
            RegimeFeature(name="efficiency_ratio", value=0.42, methodology="kaufman/period=10"),
            RegimeFeature(
                name="hurst_exponent", value=0.61, methodology="structure_function/period=20"
            ),
        ),
    )
    bars = [_bar(0, high=101.0, low=99.0), _bar(1, high=102.0, low=100.0)]
    segments = build_regime_segments([state], bars)
    assert len(segments) == 1

    line = feed_line(segments[0], start_epoch=0, end_epoch=0)
    fields = line.split(FEED_DELIMITER)
    assert fields[10] == repr(0.42)
    assert fields[11] == repr(0.61)


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
        efficiency_ratio=segment.efficiency_ratio,
        hurst_exponent=segment.hurst_exponent,
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
    assert len(fields) == 9
    assert fields[2] == "RETRACEMENT"
    assert fields[3] == "UP"
    assert fields[4] == "42"
    assert fields[7] == ""  # efficiency_ratio -- _resolution_scenario()'s states carry no evidence
    assert fields[8] == ""  # hurst_exponent

    poisoned = type(marker)(
        regime=marker.regime,
        direction=marker.direction,
        at_utc=marker.at_utc,
        price=marker.price,
        confidence=marker.confidence,
        efficiency_ratio=marker.efficiency_ratio,
        hurst_exponent=marker.hurst_exponent,
        object_id=f"bad{FEED_DELIMITER}id",
        methodology_version=marker.methodology_version,
        instrument_key=marker.instrument_key,
        timeframe_canonical=marker.timeframe_canonical,
    )
    with pytest.raises(ValueError, match="delimiter"):
        marker_line(poisoned, at_epoch=1)


def test_marker_line_encodes_efficiency_ratio_and_hurst_exponent_when_present() -> None:
    """v5: the resolution marker carries the same evidence a band does --
    it is the single instant the [VO-H] anticipation lean is checked
    against reality, so the evidence belongs there too."""
    bars = [
        _bar(0, high=100.0, low=99.0),
        _bar(1, high=101.0, low=99.5),
        _bar(2, high=104.0, low=100.5),
        _bar(3, high=107.0, low=103.0),
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
            supporting_features=(
                RegimeFeature(
                    name="efficiency_ratio", value=0.75, methodology="kaufman/period=10"
                ),
                RegimeFeature(
                    name="hurst_exponent", value=0.55, methodology="structure_function/period=20"
                ),
            ),
        ),
        _state(RegimeType.EXPANSION, 2, direction=RegimeDirection.UP, suffix="b"),
    ]
    marker = build_regime_markers(states, bars)[0]

    line = marker_line(marker, at_epoch=42)
    fields = line.split(FEED_DELIMITER)
    assert fields[7] == repr(0.75)
    assert fields[8] == repr(0.55)


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
            assert len(fields) == 9
        else:
            assert len(fields) == 12


def _session_config() -> SessionConfig:
    """UTC MORNING 00:00-00:03 / AFTERNOON 00:03-00:06 -- matches
    _scenario()'s six one-minute bars so the boundary lands mid-scenario."""
    return SessionConfig(
        instrument_symbol="TEST",
        timezone="UTC",
        trading_day_opens=time(0, 0),
        sessions=(
            SessionWindow(name="MORNING", start=time(0, 0), end=time(0, 3)),
            SessionWindow(name="AFTERNOON", start=time(0, 3), end=time(0, 6)),
        ),
        rth=SessionWindow(name="RTH", start=time(0, 0), end=time(0, 6)),
    )


def test_build_session_boundaries_emits_one_per_change_only() -> None:
    _states, bars = _scenario()  # 6 bars, minutes 0-5
    boundaries = build_session_boundaries(bars, _session_config())

    # MORNING covers minutes 0-2, AFTERNOON covers 3-5 -- exactly one
    # crossing, at minute 3, not one per bar.
    assert len(boundaries) == 1
    boundary = boundaries[0]
    assert boundary.at_utc == _at(3)
    assert boundary.from_session == "MORNING"
    assert boundary.to_session == "AFTERNOON"


def test_session_boundary_line_format() -> None:
    _states, bars = _scenario()
    boundary = build_session_boundaries(bars, _session_config())[0]

    line = session_boundary_line(boundary, at_epoch=99)
    fields = line.split(FEED_DELIMITER)
    assert fields == ["SESN", "99", "MORNING", "AFTERNOON"]


def test_session_stat_line_format() -> None:
    stat = SessionRegimeStats(
        session="MORNING",
        regime=RegimeType.EXPANSION,
        bar_count=120,
        total_minutes=120.0,
        share_of_session=0.5,
        segments_started=3,
    )
    line = session_stat_line(stat)
    fields = line.split(FEED_DELIMITER)
    assert fields == ["SSTAT", "MORNING", "EXPANSION", "120", "120.0", "0.5", "3"]


def test_session_stat_line_encodes_unclassified_regime_as_none() -> None:
    stat = SessionRegimeStats(
        session="OFF_SESSION",
        regime=None,
        bar_count=5,
        total_minutes=5.0,
        share_of_session=1.0,
        segments_started=0,
    )
    fields = session_stat_line(stat).split(FEED_DELIMITER)
    assert fields[2] == "UNCLASSIFIED"


def test_feed_header_reports_session_stats_count() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)
    stats = (
        SessionRegimeStats(
            session="MORNING",
            regime=RegimeType.CONSOLIDATION,
            bar_count=10,
            total_minutes=10.0,
            share_of_session=1.0,
            segments_started=1,
        ),
    )

    lines = render_feed_lines(
        segments,
        session_stats=stats,
        epoch_of=lambda dt: int(dt.timestamp()),
        generated_utc=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
    )
    assert "session_stats=1" in lines[0]


def test_render_feed_lines_appends_session_stats_after_chronological_events() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)
    markers = build_regime_markers(states, bars)
    boundaries = build_session_boundaries(bars, _session_config())
    stats = (
        SessionRegimeStats(
            session="MORNING",
            regime=RegimeType.CONSOLIDATION,
            bar_count=10,
            total_minutes=10.0,
            share_of_session=1.0,
            segments_started=1,
        ),
        SessionRegimeStats(
            session="AFTERNOON",
            regime=RegimeType.EXPANSION,
            bar_count=20,
            total_minutes=20.0,
            share_of_session=1.0,
            segments_started=2,
        ),
    )

    lines = render_feed_lines(
        segments,
        markers,
        boundaries,
        stats,
        epoch_of=lambda dt: int(dt.timestamp()),
        generated_utc=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
    )
    total_events = len(segments) + len(markers) + len(boundaries)
    tail = lines[1 + total_events :]
    assert len(tail) == len(stats)
    assert [line.split(FEED_DELIMITER)[0] for line in tail] == ["SSTAT", "SSTAT"]
    assert tail[0].split(FEED_DELIMITER)[1] == "MORNING"
    assert tail[1].split(FEED_DELIMITER)[1] == "AFTERNOON"


def test_render_feed_lines_interleaves_all_three_event_types() -> None:
    states, bars = _scenario()
    segments = build_regime_segments(states, bars)
    markers = build_regime_markers(states, bars)
    boundaries = build_session_boundaries(bars, _session_config())

    lines = render_feed_lines(
        segments,
        markers,
        boundaries,
        epoch_of=lambda dt: int(dt.timestamp()),
        generated_utc=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
    )
    assert f"boundaries={len(boundaries)}" in lines[0]
    assert len(lines) == 1 + len(segments) + len(markers) + len(boundaries)

    tags = [line.split(FEED_DELIMITER)[0] for line in lines[1:]]
    assert tags.count("SESN") == len(boundaries)

    # SESN lines never get mistaken for BAND/MARK by field count either.
    for line in lines[1:]:
        fields = line.split(FEED_DELIMITER)
        if fields[0] == "SESN":
            assert len(fields) == 4


# ── warm-up trim (2026-09-18 audit, finding C3) ───────────────────────────


def test_trim_warmup_drops_clips_and_keeps_the_right_segments_and_markers() -> None:
    states, bars = _resolution_scenario()
    segments = build_regime_segments(states, bars)
    markers = build_regime_markers(states, bars)
    assert [s.regime for s in segments] == [
        RegimeType.EXPANSION,
        RegimeType.PULLBACK_UNRESOLVED,
        RegimeType.EXPANSION,
    ]
    # Warm-up ends at bar 1: the first EXPANSION (bars 0..1) ends exactly at
    # the boundary -> dropped; the PULLBACK (bars 1..2) starts at it -> kept
    # unchanged; the final EXPANSION untouched; the RETRACEMENT marker at
    # bar 2 is after the boundary -> kept.
    kept, kept_markers = trim_warmup(segments, markers, warmup_end_utc=_at(1))
    assert [s.regime for s in kept] == [RegimeType.PULLBACK_UNRESOLVED, RegimeType.EXPANSION]
    assert kept[0].start_utc == _at(1)
    assert len(kept_markers) == 1

    # Warm-up ends mid-way through the pullback (bar 1.5): pullback is clipped
    # to start there, its high/low untouched; the marker at bar 2 survives.
    from datetime import timedelta

    mid = _at(1) + timedelta(seconds=30)
    kept, kept_markers = trim_warmup(segments, markers, warmup_end_utc=mid)
    assert kept[0].regime is RegimeType.PULLBACK_UNRESOLVED
    assert kept[0].start_utc == mid
    assert kept[0].high == segments[1].high and kept[0].low == segments[1].low
    assert len(kept_markers) == 1

    # Warm-up past everything: nothing but the open-ended tail survives.
    kept, kept_markers = trim_warmup(segments, markers, warmup_end_utc=_at(3))
    assert [s.regime for s in kept] == [RegimeType.EXPANSION]
    assert kept[0].start_utc == _at(3)
    assert kept_markers == ()


def test_feed_header_carries_last_bar_epoch_when_given() -> None:
    """2026-09-19: the indicator stops an open-ended band at the last bar
    the feed covers, read from this header field."""
    states, bars = _resolution_scenario()
    segments = build_regime_segments(states, bars)
    lines = render_feed_lines(
        segments, epoch_of=lambda dt: int(dt.timestamp()), generated_utc=_at(9),
        last_bar_epoch=1234567890,
    )
    assert lines[0].endswith(" last_bar_epoch=1234567890")
    lines = render_feed_lines(
        segments, epoch_of=lambda dt: int(dt.timestamp()), generated_utc=_at(9)
    )
    assert "last_bar_epoch" not in lines[0]
