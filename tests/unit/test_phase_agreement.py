"""Unit tests for vo.research.phase_agreement -- the validation-only
statistical-vs-Phase-13 phase agreement study (confirmed 2026-09-18:
validation study only, never a standalone classifier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.observation.regime import RegimeType
from vo.research.hmm_report import HMMSample
from vo.research.phase_agreement import (
    build_phase_agreement_report,
    build_phase_comparison_samples,
    render_phase_agreement_markdown,
    statistical_phase_guess,
)
from vo.research.regime_windows import RegimeInterval

_BASE = datetime(2023, 6, 1, 13, 30, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


# ── statistical_phase_guess ──────────────────────────────────────────


def test_statistical_phase_guess_none_hurst_is_always_ambiguous() -> None:
    assert (
        statistical_phase_guess(
            hurst=None, er=0.9, is_displacement_state=True, is_accumulation_state=False
        )
        is None
    )


def test_statistical_phase_guess_accumulation_state_is_consolidation() -> None:
    assert (
        statistical_phase_guess(
            hurst=0.5, er=0.9, is_displacement_state=False, is_accumulation_state=True
        )
        is RegimeType.CONSOLIDATION
    )


def test_statistical_phase_guess_low_hurst_low_er_is_consolidation_even_without_state() -> None:
    assert (
        statistical_phase_guess(
            hurst=0.30, er=0.20, is_displacement_state=False, is_accumulation_state=False
        )
        is RegimeType.CONSOLIDATION
    )


def test_statistical_phase_guess_displacement_high_hurst_high_er_is_expansion() -> None:
    assert (
        statistical_phase_guess(
            hurst=0.60, er=0.70, is_displacement_state=True, is_accumulation_state=False
        )
        is RegimeType.EXPANSION
    )


def test_statistical_phase_guess_mid_range_non_displacement_is_retracement() -> None:
    assert (
        statistical_phase_guess(
            hurst=0.50, er=0.45, is_displacement_state=False, is_accumulation_state=False
        )
        is RegimeType.RETRACEMENT
    )


def test_statistical_phase_guess_displacement_losing_efficiency_is_reversal() -> None:
    # hurst/er both stay above the CONSOLIDATION branch's (< 0.45 / < 0.35)
    # thresholds, checked first, so this isolates the REVERSAL profile.
    assert (
        statistical_phase_guess(
            hurst=0.48, er=0.40, is_displacement_state=True, is_accumulation_state=False
        )
        is RegimeType.REVERSAL
    )


def test_statistical_phase_guess_no_matching_profile_is_ambiguous() -> None:
    # High ER but mid Hurst, not the displacement state, not the
    # accumulation state either -- no profile below claims this point.
    assert (
        statistical_phase_guess(
            hurst=0.50, er=0.62, is_displacement_state=False, is_accumulation_state=False
        )
        is None
    )


# ── build_phase_comparison_samples ───────────────────────────────────


def _sample(minute: int, er: float) -> HMMSample:
    return HMMSample(when=_at(minute), log_return=0.001, norm_return=0.5, er=er)


def test_build_phase_comparison_samples_looks_up_actual_and_statistical_phase() -> None:
    intervals = (
        RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=_at(10)),
        RegimeInterval(regime=RegimeType.CONSOLIDATION, start_utc=_at(10), end_utc=None),
    )
    hmm_samples = (_sample(2, er=0.70), _sample(12, er=0.20))
    hmm_states = (0, 1)  # 0 = displacement, 1 = accumulation
    hurst_samples = ((_at(2), 0.60), (_at(12), 0.30))

    comparisons = build_phase_comparison_samples(
        intervals,
        hmm_samples,
        hmm_states,
        hurst_samples,
        displacement_state=0,
        accumulation_state=1,
    )

    assert len(comparisons) == 2
    assert comparisons[0].actual_phase is RegimeType.EXPANSION
    assert comparisons[0].statistical_phase is RegimeType.EXPANSION  # agrees
    assert comparisons[1].actual_phase is RegimeType.CONSOLIDATION
    assert comparisons[1].statistical_phase is RegimeType.CONSOLIDATION  # agrees


def test_build_phase_comparison_samples_honors_hurst_max_gap() -> None:
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=None),)
    hmm_samples = (_sample(100, er=0.70),)
    hmm_states = (0,)
    # Hurst sample is 200 minutes stale -- far outside the default 90-minute gap.
    hurst_samples = ((_at(0), 0.60),)

    comparisons = build_phase_comparison_samples(
        intervals,
        hmm_samples,
        hmm_states,
        hurst_samples,
        displacement_state=0,
        accumulation_state=None,
    )

    assert comparisons[0].statistical_phase is None  # no usable Hurst -> ambiguous


def test_build_phase_comparison_samples_rejects_misaligned_lengths() -> None:
    with pytest.raises(ValueError, match="align"):
        build_phase_comparison_samples(
            (),
            (_sample(0, er=0.5),),
            (0, 1),
            (),
            displacement_state=None,
            accumulation_state=None,
        )


def test_build_phase_comparison_samples_no_covering_interval_is_none_actual() -> None:
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(50), end_utc=None),)
    hmm_samples = (_sample(0, er=0.7),)  # before any interval starts
    hurst_samples = ((_at(0), 0.6),)

    comparisons = build_phase_comparison_samples(
        intervals, hmm_samples, (0,), hurst_samples, displacement_state=0, accumulation_state=None
    )
    assert comparisons[0].actual_phase is None


# ── build_phase_agreement_report / render ────────────────────────────


def test_build_phase_agreement_report_computes_rates_and_confusions() -> None:
    from vo.research.phase_agreement import PhaseComparisonSample

    samples = (
        PhaseComparisonSample(
            when=_at(0), actual_phase=RegimeType.EXPANSION, statistical_phase=RegimeType.EXPANSION
        ),
        PhaseComparisonSample(
            when=_at(1), actual_phase=RegimeType.EXPANSION, statistical_phase=RegimeType.RETRACEMENT
        ),
        PhaseComparisonSample(
            when=_at(2), actual_phase=RegimeType.EXPANSION, statistical_phase=None
        ),
        PhaseComparisonSample(
            when=_at(3),
            actual_phase=RegimeType.CONSOLIDATION,
            statistical_phase=RegimeType.CONSOLIDATION,
        ),
        PhaseComparisonSample(
            when=_at(4), actual_phase=None, statistical_phase=RegimeType.EXPANSION
        ),
    )

    report = build_phase_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=UTC),
        window_label="NY_AM killzone",
    )

    assert report.n_samples == 5
    assert report.n_comparable == 4  # the one with actual_phase=None is excluded
    assert report.n_statistically_ambiguous == 1
    assert report.overall_agreement_rate == pytest.approx(2 / 4)

    by_phase = {b.actual_phase: b for b in report.by_phase}
    assert by_phase[RegimeType.EXPANSION].n == 3
    assert by_phase[RegimeType.EXPANSION].agreed == 1
    assert by_phase[RegimeType.EXPANSION].guessed_instead == {"RETRACEMENT": 1, "AMBIGUOUS": 1}
    assert by_phase[RegimeType.CONSOLIDATION].n == 1
    assert by_phase[RegimeType.CONSOLIDATION].agreed == 1
    assert by_phase[RegimeType.CONSOLIDATION].guessed_instead == {}


def test_build_phase_agreement_report_handles_no_comparable_samples() -> None:
    from vo.research.phase_agreement import PhaseComparisonSample

    samples = (PhaseComparisonSample(when=_at(0), actual_phase=None, statistical_phase=None),)
    report = build_phase_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=UTC),
        window_label="NY_AM killzone",
    )
    assert report.n_comparable == 0
    assert report.overall_agreement_rate is None
    markdown = render_phase_agreement_markdown(report)
    assert "n/a" in markdown


def test_render_phase_agreement_markdown_states_validation_only() -> None:
    from vo.research.phase_agreement import PhaseComparisonSample

    samples = (
        PhaseComparisonSample(
            when=_at(0), actual_phase=RegimeType.EXPANSION, statistical_phase=RegimeType.EXPANSION
        ),
    )
    report = build_phase_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=UTC),
        window_label="NY_AM killzone",
    )
    markdown = render_phase_agreement_markdown(report)
    assert "VALIDATION ONLY" in markdown
    assert "never" in markdown.lower()
