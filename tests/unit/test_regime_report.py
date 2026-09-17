"""Unit tests for vo.telemetry.regime_report -- the deep-history backtest
summary. Segment durations/shares/transitions, ER/Hurst aggregation, and
the anticipation-lean accuracy, all over hand-built inputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from vo.telemetry.regime_feed import RegimeSegment
from vo.telemetry.regime_report import build_regime_report

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")


_BASE = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _seg(regime: RegimeType, start_min: int, end_min: int | None) -> RegimeSegment:
    return RegimeSegment(
        regime=regime,
        direction=None,
        start_utc=_at(start_min),
        end_utc=_at(end_min) if end_min is not None else None,
        high=10.0,
        low=9.0,
        confidence=0.6,
        anticipated=None,
        object_id=f"seg:{regime.name}:{start_min}",
        methodology_version=1,
        instrument_key="MT5:Test:US100",
        timeframe_canonical="M1",
    )


def _state(
    regime: RegimeType,
    minute: int,
    *,
    supersedes: str | None = None,
    anticipated: AnticipatedResolution | None = None,
    features: tuple[RegimeFeature, ...] = (),
    suffix: str = "",
) -> RegimeState:
    return RegimeState(
        object_type=OBJECT_TYPE_REGIME_STATE,
        object_id=f"st:{regime.name}:{minute}{suffix}",
        observed_at=_at(minute),
        recorded_at=_at(minute),
        methodology_version=1,
        supersedes=supersedes,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        regime=regime,
        direction=RegimeDirection.UP if regime is RegimeType.RETRACEMENT else None,
        confidence=1.0 if regime in (RegimeType.RETRACEMENT, RegimeType.REVERSAL) else 0.5,
        evidence="test",
        supporting_features=features,
        anticipated_resolution=anticipated,
    )


def test_shares_and_durations_from_segments() -> None:
    segments = (
        _seg(RegimeType.CONSOLIDATION, 0, 10),   # 10 min
        _seg(RegimeType.EXPANSION, 10, 40),      # 30 min
        _seg(RegimeType.CONSOLIDATION, 40, None),  # closed by history_end at 60 -> 20 min
    )
    report = build_regime_report(segments, (), history_end_utc=_at(60), bar_count=60)

    by_regime = {s.regime: s for s in report.per_regime}
    cons = by_regime[RegimeType.CONSOLIDATION]
    exp = by_regime[RegimeType.EXPANSION]

    assert cons.segment_count == 2
    assert cons.total_minutes == 30.0  # 10 + 20
    assert exp.total_minutes == 30.0
    # Two regimes split 30/60 each.
    assert abs(cons.share - 0.5) < 1e-9
    assert abs(exp.share - 0.5) < 1e-9
    assert exp.mean_minutes == 30.0


def test_transitions_counted_between_consecutive_segments() -> None:
    segments = (
        _seg(RegimeType.CONSOLIDATION, 0, 10),
        _seg(RegimeType.EXPANSION, 10, 20),
        _seg(RegimeType.CONSOLIDATION, 20, 30),
        _seg(RegimeType.EXPANSION, 30, None),
    )
    report = build_regime_report(segments, (), history_end_utc=_at(40))
    counts = {(f.name, t.name): c for f, t, c in report.transitions}
    assert counts[("CONSOLIDATION", "EXPANSION")] == 2
    assert counts[("EXPANSION", "CONSOLIDATION")] == 1


def test_er_and_hurst_means_per_regime() -> None:
    def feat(er: float, hurst: float) -> tuple[RegimeFeature, ...]:
        return (
            RegimeFeature(name="efficiency_ratio", value=er, methodology="er"),
            RegimeFeature(name="hurst_exponent", value=hurst, methodology="h"),
        )

    segments = (_seg(RegimeType.EXPANSION, 0, None),)
    states = (
        _state(RegimeType.EXPANSION, 1, features=feat(0.6, 0.7)),
        _state(RegimeType.EXPANSION, 2, features=feat(0.8, 0.5), suffix="b"),
    )
    report = build_regime_report(segments, states, history_end_utc=_at(3))
    exp = next(s for s in report.per_regime if s.regime is RegimeType.EXPANSION)
    assert exp.mean_efficiency_ratio == 0.7  # (0.6 + 0.8) / 2
    assert exp.mean_hurst == 0.6  # (0.7 + 0.5) / 2


def test_anticipation_accuracy_matches_lean_against_outcome() -> None:
    # Pullback A leaned RETRACEMENT, resolved RETRACEMENT -> match.
    # Pullback B leaned REVERSAL, resolved RETRACEMENT -> miss.
    states = (
        _state(
            RegimeType.PULLBACK_UNRESOLVED,
            1,
            anticipated=AnticipatedResolution.RETRACEMENT,
            suffix="A",
        ),
        _state(RegimeType.RETRACEMENT, 2, supersedes="st:PULLBACK_UNRESOLVED:1A"),
        _state(
            RegimeType.PULLBACK_UNRESOLVED,
            3,
            anticipated=AnticipatedResolution.REVERSAL,
            suffix="B",
        ),
        _state(RegimeType.RETRACEMENT, 4, supersedes="st:PULLBACK_UNRESOLVED:3B", suffix="b"),
    )
    report = build_regime_report((), states, history_end_utc=None)
    a = report.anticipation
    assert a.resolutions == 2
    assert a.to_retracement == 2
    assert a.leaned == 2
    assert a.matched == 1
    assert a.rate == 0.5
