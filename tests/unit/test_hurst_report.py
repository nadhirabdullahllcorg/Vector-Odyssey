"""Unit tests for vo.research.hurst_report -- Phase 15a's standalone Hurst
characterization report. Hand-built inputs; formula correctness for the
shared statistics primitives (Mann-Whitney U, percentiles, ...) is
already covered in test_research_statistics.py, so these tests focus on
this module's own orchestration: rolling sampling, regime-interval
membership, session bucketing, transition matching, and the
out-of-sample split."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from itertools import pairwise

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import OBJECT_TYPE_REGIME_TRANSITION, RegimeTransition, RegimeType
from vo.research.hurst_report import (
    build_hurst_by_regime,
    build_hurst_by_regime_report,
    build_hurst_by_session,
    build_hurst_preceding_transitions,
    build_rolling_hurst,
    build_transition_hurst_report,
    render_hurst_report_markdown,
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


def _wiggly_bars(n: int) -> tuple[Bar, ...]:
    """Bars with real (non-flat, non-monotonic) movement -- hurst_exponent
    returns None on a perfectly flat window, so tests need genuine
    variation, not just any close price."""
    return tuple(_bar(i, 10.0 + (i % 7) * 0.3 + i * 0.01) for i in range(n))


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


# ── rolling Hurst ──────────────────────────────────────────────────────


def test_build_rolling_hurst_samples_at_stride_and_drops_warmup() -> None:
    bars = _wiggly_bars(200)
    rolling = build_rolling_hurst(bars, window_lengths=(10,), stride=20)

    samples = rolling[10]
    assert len(samples) > 0
    # chronological, evenly spaced by the stride (20 minutes between bars here)
    times = [t for t, _v in samples]
    assert times == sorted(times)
    for a, b in pairwise(times):
        assert (b - a).total_seconds() / 60.0 == pytest.approx(20.0)
    # no sample before the window has enough history (period=10)
    assert times[0] >= _at(10)


def test_build_rolling_hurst_rejects_stride_below_one() -> None:
    with pytest.raises(ValueError, match="stride"):
        build_rolling_hurst(_wiggly_bars(20), window_lengths=(4,), stride=0)


def test_build_window_distributions_includes_lag1_autocorrelation() -> None:
    bars = _wiggly_bars(300)
    rolling = build_rolling_hurst(bars, window_lengths=(10, 20), stride=10)
    dists = build_window_distributions(rolling, window_lengths=(10, 20))

    windows = {d.window for d in dists}
    assert windows == {10, 20}
    for d in dists:
        assert d.stats.n > 0
        # not asserting a specific value -- just that it computed, since the
        # exact number depends on the synthetic series' own structure
        corr = d.stability_lag1_autocorrelation
        assert corr is None or -1.0 <= corr <= 1.0


# ── regime-interval membership / by-regime grouping ──────────────────────


def test_build_hurst_by_regime_groups_samples_by_covering_interval() -> None:
    intervals = (
        RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=_at(100)),
        RegimeInterval(regime=RegimeType.RETRACEMENT, start_utc=_at(100), end_utc=None),
    )
    samples = [
        (_at(10), 0.6),  # inside EXPANSION
        (_at(99), 0.65),  # inside EXPANSION, right at the edge
        (_at(100), 0.3),  # exactly at the boundary -- belongs to RETRACEMENT
        (_at(500), 0.35),  # deep into the open-ended RETRACEMENT interval
    ]

    grouped = build_hurst_by_regime(samples, intervals)

    assert grouped[RegimeType.EXPANSION] == [0.6, 0.65]
    assert grouped[RegimeType.RETRACEMENT] == [0.3, 0.35]
    assert RegimeType.CONSOLIDATION not in grouped


def test_build_hurst_by_regime_drops_samples_before_the_first_interval() -> None:
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(50), end_utc=None),)
    samples = [(_at(10), 0.5), (_at(60), 0.7)]

    grouped = build_hurst_by_regime(samples, intervals)

    assert grouped == {RegimeType.EXPANSION: [0.7]}


def test_build_hurst_by_regime_report_produces_sensitivity_detail_and_pairwise() -> None:
    intervals = (
        RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=_at(100)),
        RegimeInterval(regime=RegimeType.RETRACEMENT, start_utc=_at(100), end_utc=None),
    )
    high = [(_at(t), 0.7) for t in range(0, 100, 10)]
    low = [(_at(t), 0.2) for t in range(100, 200, 10)]
    high20 = [(_at(t), 0.65) for t in range(0, 100, 20)]
    low20 = [(_at(t), 0.25) for t in range(100, 200, 20)]
    rolling = {10: high + low, 20: high20 + low20}

    report = build_hurst_by_regime_report(
        rolling, intervals, window_lengths=(10, 20), primary_window=10
    )

    assert {row.window for row in report.sensitivity} == {10, 20}
    assert len(report.by_regime_detail) == 2  # both regimes present at the primary window
    assert len(report.pairwise) == 1  # exactly one pair for two present regimes
    mw = report.pairwise[0]
    assert mw.p_value is not None
    assert mw.p_value < 0.05  # fully separated groups (0.7s vs 0.2s)


# ── Hurst preceding transitions ───────────────────────────────────────────


def test_build_hurst_preceding_transitions_matches_nearest_earlier_sample() -> None:
    samples = [(_at(0), 0.5), (_at(10), 0.6), (_at(20), 0.7)]
    transitions = (
        _transition(RegimeType.EXPANSION, RegimeType.RETRACEMENT, _at(15)),  # nearest earlier: t=10
        _transition(RegimeType.EXPANSION, RegimeType.REVERSAL, _at(21)),  # nearest earlier: t=20
    )

    grouped = build_hurst_preceding_transitions(samples, transitions, max_gap_minutes=30.0)

    assert grouped[RegimeType.RETRACEMENT] == [0.6]
    assert grouped[RegimeType.REVERSAL] == [0.7]


def test_build_hurst_preceding_transitions_skips_when_gap_too_large_or_early() -> None:
    samples = [(_at(0), 0.5)]
    transitions = (
        _transition(RegimeType.EXPANSION, RegimeType.RETRACEMENT, _at(-5)),  # before any sample
        _transition(RegimeType.EXPANSION, RegimeType.REVERSAL, _at(1000)),  # gap way too large
    )

    grouped = build_hurst_preceding_transitions(samples, transitions, max_gap_minutes=30.0)

    assert grouped == {}


def test_build_transition_hurst_report_groups_by_to_state_and_compares() -> None:
    samples = [(_at(t), 0.7) for t in range(0, 100, 10)] + [
        (_at(t), 0.2) for t in range(100, 200, 10)
    ]
    transitions = tuple(
        _transition(RegimeType.EXPANSION, RegimeType.RETRACEMENT, _at(t + 1))
        for t in range(0, 100, 10)
    ) + tuple(
        _transition(RegimeType.EXPANSION, RegimeType.REVERSAL, _at(t + 1))
        for t in range(100, 200, 10)
    )

    report = build_transition_hurst_report(samples, transitions, max_gap_minutes=5.0)

    labels = {s.label for s in report.by_to_state}
    assert labels == {"RETRACEMENT", "REVERSAL"}
    assert len(report.pairwise) == 1


# ── by session ────────────────────────────────────────────────────────────


def test_build_hurst_by_session_buckets_including_off_session() -> None:
    config = _session_config()
    samples = [
        (_at(60), 0.5),  # 01:00 UTC -- MORNING
        (_at(60 * 20), 0.6),  # 20:00 UTC -- EVENING
        (_at(60 * 14), 0.7),  # 14:00 UTC -- outside both windows -- OFF_SESSION
    ]

    dists = build_hurst_by_session(samples, config)

    labels = {d.label: d for d in dists}
    assert labels["MORNING"].n == 1
    assert labels["EVENING"].n == 1
    assert labels[OFF_SESSION_LABEL].n == 1


# ── out-of-sample ──────────────────────────────────────────────────────────


def test_build_out_of_sample_report_splits_chronologically() -> None:
    samples = [(_at(i), float(i)) for i in range(10)]
    rolling = {10: samples}

    report = build_out_of_sample_report(rolling, window_lengths=(10,), split_fraction=0.7)

    assert len(report) == 1
    row = report[0]
    assert row.train.n == 7
    assert row.test.n == 3
    assert row.train.median == pytest.approx(3.0)
    assert row.test.median == pytest.approx(8.0)
    assert row.median_delta == pytest.approx(5.0)


def test_build_out_of_sample_report_skips_series_shorter_than_four() -> None:
    rolling = {10: [(_at(0), 0.5), (_at(1), 0.6)]}

    report = build_out_of_sample_report(rolling, window_lengths=(10,))

    assert report == ()


def test_build_out_of_sample_report_rejects_split_fraction_out_of_range() -> None:
    with pytest.raises(ValueError, match="split_fraction"):
        build_out_of_sample_report({}, window_lengths=(), split_fraction=1.0)


# ── rendering ────────────────────────────────────────────────────────────


def test_render_hurst_report_markdown_smoke() -> None:
    bars = _wiggly_bars(300)
    rolling = build_rolling_hurst(bars, window_lengths=(10, 20), stride=15)
    window_dists = build_window_distributions(rolling, window_lengths=(10, 20))
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=None),)
    by_regime = build_hurst_by_regime_report(
        rolling, intervals, window_lengths=(10, 20), primary_window=10
    )
    transitions = build_transition_hurst_report((), (), max_gap_minutes=30.0)
    by_session = build_hurst_by_session(rolling[10], _session_config())
    out_of_sample = build_out_of_sample_report(rolling, window_lengths=(10, 20))

    markdown = render_hurst_report_markdown(
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
        "# Hurst Exponent Research Report v1",
        "## 1. Rolling Hurst distribution",
        "## 2. Hurst by regime",
        "## 3. Hurst preceding regime transitions",
        "## 4. Hurst by session",
        "## 5. Out-of-sample stability",
        "## 6. Hurst by timeframe — deferred",
        "## Conclusions this report deliberately does not draw",
    ):
        assert heading in markdown
    assert "vote on the regime" in markdown
