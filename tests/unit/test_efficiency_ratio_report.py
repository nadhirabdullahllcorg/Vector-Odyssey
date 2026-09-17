"""Unit tests for vo.research.efficiency_ratio_report -- Phase 15b's
standalone Efficiency Ratio characterization report. Hand-built inputs;
formula correctness for the shared statistics primitives (Mann-Whitney U,
percentiles, window distributions, out-of-sample split, ...) is already
covered in test_research_statistics.py, and the regime-interval
membership lookup is covered in test_hurst_report.py (same shared
vo.research.regime_windows.interval_at), so these tests focus on this
module's own orchestration: rolling sampling, session bucketing, and
transition matching -- mirroring test_hurst_report.py's own structure."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from itertools import pairwise

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import OBJECT_TYPE_REGIME_TRANSITION, RegimeTransition, RegimeType
from vo.research.efficiency_ratio_report import (
    build_er_by_regime,
    build_er_by_regime_report,
    build_er_by_session,
    build_er_preceding_transitions,
    build_rolling_er,
    build_transition_er_report,
    render_er_report_markdown,
)
from vo.research.regime_windows import RegimeInterval
from vo.research.statistics import build_out_of_sample_report, build_window_distributions
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, SessionWindow

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")
_BASE = datetime(2023, 1, 1, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _bar(minute: int, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        tick_volume=1,
        real_volume=0,
    )


def _trending_bars(n: int) -> tuple[Bar, ...]:
    """Monotonic, evenly-spaced closes -- a clean straight run, ER should
    read close to 1.0 for these regardless of window length."""
    return tuple(_bar(i, 10.0 + i * 0.1) for i in range(n))


def _choppy_bars(n: int) -> tuple[Bar, ...]:
    """Oscillating closes that keep returning near the start -- a long
    path, little net direction, ER should read low."""
    return tuple(_bar(i, 10.0 + (1.0 if i % 2 == 0 else -1.0)) for i in range(n))


def _transition(from_state: RegimeType, to_state: RegimeType, when: datetime) -> RegimeTransition:
    return RegimeTransition(
        object_type=OBJECT_TYPE_REGIME_TRANSITION,
        object_id=f"tr:{from_state.name}->{to_state.name}:{when.isoformat()}",
        observed_at=when,
        recorded_at=when,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        from_state=from_state,
        to_state=to_state,
        evidence="test",
    )


def _session_config() -> SessionConfig:
    return SessionConfig(
        instrument_symbol="TEST",
        timezone="UTC",
        trading_day_opens=time(0, 0),
        sessions=(
            SessionWindow(name="MORNING", start=time(0, 0), end=time(12, 0)),
            SessionWindow(name="EVENING", start=time(18, 0), end=time(23, 59, 59)),
        ),
        rth=SessionWindow(name="RTH", start=time(0, 0), end=time(23, 59, 59)),
    )


# ── rolling ER ────────────────────────────────────────────────────────


def test_build_rolling_er_samples_at_stride_and_drops_warmup() -> None:
    bars = _trending_bars(200)
    rolling = build_rolling_er(bars, window_lengths=(10,), stride=20)

    samples = rolling[10]
    assert len(samples) > 0
    times = [t for t, _v in samples]
    assert times == sorted(times)
    for a, b in pairwise(times):
        assert (b - a).total_seconds() / 60.0 == pytest.approx(20.0)
    # no sample before the window has enough history (period=10)
    assert times[0] >= _at(10)


def test_build_rolling_er_rejects_stride_below_one() -> None:
    with pytest.raises(ValueError, match="stride"):
        build_rolling_er(_trending_bars(20), window_lengths=(4,), stride=0)


def test_build_rolling_er_reads_near_one_for_a_clean_trend_and_low_for_chop() -> None:
    trending = build_rolling_er(_trending_bars(60), window_lengths=(10,), stride=10)
    choppy = build_rolling_er(_choppy_bars(60), window_lengths=(10,), stride=10)

    trending_values = [v for _t, v in trending[10]]
    choppy_values = [v for _t, v in choppy[10]]
    assert min(trending_values) > 0.9
    assert max(choppy_values) < 0.3


def test_build_window_distributions_reused_from_statistics() -> None:
    bars = _trending_bars(300)
    rolling = build_rolling_er(bars, window_lengths=(5, 10), stride=10)
    dists = build_window_distributions(rolling, window_lengths=(5, 10))

    windows = {d.window for d in dists}
    assert windows == {5, 10}
    for d in dists:
        assert d.stats.n > 0
        assert 0.0 <= d.stats.mean <= 1.0  # ER is bounded [0, 1] by construction


# ── ER by regime ──────────────────────────────────────────────────────


def test_build_er_by_regime_groups_samples_by_covering_interval() -> None:
    intervals = (
        RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=_at(100)),
        RegimeInterval(regime=RegimeType.RETRACEMENT, start_utc=_at(100), end_utc=None),
    )
    samples = [
        (_at(10), 0.9),
        (_at(99), 0.85),
        (_at(100), 0.1),
        (_at(500), 0.15),
    ]

    grouped = build_er_by_regime(samples, intervals)

    assert grouped[RegimeType.EXPANSION] == [0.9, 0.85]
    assert grouped[RegimeType.RETRACEMENT] == [0.1, 0.15]
    assert RegimeType.CONSOLIDATION not in grouped


def test_build_er_by_regime_report_produces_sensitivity_detail_and_pairwise() -> None:
    intervals = (
        RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=_at(100)),
        RegimeInterval(regime=RegimeType.RETRACEMENT, start_utc=_at(100), end_utc=None),
    )
    high = [(_at(t), 0.9) for t in range(0, 100, 10)]
    low = [(_at(t), 0.1) for t in range(100, 200, 10)]
    high5 = [(_at(t), 0.85) for t in range(0, 100, 20)]
    low5 = [(_at(t), 0.15) for t in range(100, 200, 20)]
    rolling = {10: high + low, 5: high5 + low5}

    report = build_er_by_regime_report(
        rolling, intervals, window_lengths=(5, 10), primary_window=10
    )

    assert {row.window for row in report.sensitivity} == {5, 10}
    assert len(report.by_regime_detail) == 2
    assert len(report.pairwise) == 1
    mw = report.pairwise[0]
    assert mw.p_value is not None
    assert mw.p_value < 0.05  # fully separated groups (0.9s vs 0.1s)


# ── ER preceding transitions ─────────────────────────────────────────────


def test_build_er_preceding_transitions_matches_nearest_earlier_sample() -> None:
    samples = [(_at(0), 0.5), (_at(10), 0.6), (_at(20), 0.7)]
    transitions = (
        _transition(RegimeType.EXPANSION, RegimeType.RETRACEMENT, _at(15)),
        _transition(RegimeType.EXPANSION, RegimeType.REVERSAL, _at(21)),
    )

    grouped = build_er_preceding_transitions(samples, transitions, max_gap_minutes=30.0)

    assert grouped[RegimeType.RETRACEMENT] == [0.6]
    assert grouped[RegimeType.REVERSAL] == [0.7]


def test_build_er_preceding_transitions_skips_when_gap_too_large_or_early() -> None:
    samples = [(_at(0), 0.5)]
    transitions = (
        _transition(RegimeType.EXPANSION, RegimeType.RETRACEMENT, _at(-5)),
        _transition(RegimeType.EXPANSION, RegimeType.REVERSAL, _at(1000)),
    )

    grouped = build_er_preceding_transitions(samples, transitions, max_gap_minutes=30.0)

    assert grouped == {}


def test_build_transition_er_report_groups_by_to_state_and_compares() -> None:
    samples = [(_at(t), 0.9) for t in range(0, 100, 10)] + [
        (_at(t), 0.1) for t in range(100, 200, 10)
    ]
    transitions = tuple(
        _transition(RegimeType.EXPANSION, RegimeType.RETRACEMENT, _at(t + 1))
        for t in range(0, 100, 10)
    ) + tuple(
        _transition(RegimeType.EXPANSION, RegimeType.REVERSAL, _at(t + 1))
        for t in range(100, 200, 10)
    )

    report = build_transition_er_report(samples, transitions, max_gap_minutes=5.0)

    labels = {s.label for s in report.by_to_state}
    assert labels == {"RETRACEMENT", "REVERSAL"}
    assert len(report.pairwise) == 1


# ── by session ────────────────────────────────────────────────────────────


def test_build_er_by_session_buckets_including_off_session() -> None:
    config = _session_config()
    samples = [
        (_at(60), 0.5),  # 01:00 UTC -- MORNING
        (_at(60 * 20), 0.6),  # 20:00 UTC -- EVENING
        (_at(60 * 14), 0.7),  # 14:00 UTC -- outside both windows -- OFF_SESSION
    ]

    dists = build_er_by_session(samples, config)

    labels = {d.label: d for d in dists}
    assert labels["MORNING"].n == 1
    assert labels["EVENING"].n == 1
    assert labels[OFF_SESSION_LABEL].n == 1


# ── out-of-sample (reused from vo.research.statistics; smoke-check only) ──


def test_build_out_of_sample_report_reused_from_statistics() -> None:
    samples = [(_at(i), float(i) / 10.0) for i in range(10)]
    rolling = {10: samples}

    report = build_out_of_sample_report(rolling, window_lengths=(10,), split_fraction=0.7)

    assert len(report) == 1
    assert report[0].train.n == 7
    assert report[0].test.n == 3


# ── rendering ────────────────────────────────────────────────────────────


def test_render_er_report_markdown_smoke() -> None:
    bars = _trending_bars(300)
    rolling = build_rolling_er(bars, window_lengths=(5, 10), stride=15)
    window_dists = build_window_distributions(rolling, window_lengths=(5, 10))
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=None),)
    by_regime = build_er_by_regime_report(
        rolling, intervals, window_lengths=(5, 10), primary_window=10
    )
    transitions = build_transition_er_report((), (), max_gap_minutes=30.0)
    by_session = build_er_by_session(rolling[10], _session_config())
    out_of_sample = build_out_of_sample_report(rolling, window_lengths=(5, 10))

    markdown = render_er_report_markdown(
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=_at(0),
        primary_window=10,
        stride=15,
        window_distributions=window_dists,
        by_regime=by_regime,
        transitions=transitions,
        by_session=by_session,
        out_of_sample=out_of_sample,
    )

    for heading in (
        "# Efficiency Ratio Research Report v1",
        "## 1. Rolling Efficiency Ratio distribution",
        "## 2. Efficiency Ratio by regime",
        "## 3. Efficiency Ratio preceding regime transitions",
        "## 4. Efficiency Ratio by session",
        "## 5. Out-of-sample stability",
        "## 6. Efficiency Ratio by timeframe — deferred",
        "## Conclusions this report deliberately does not draw",
    ):
        assert heading in markdown
    assert "vote on the regime" in markdown
