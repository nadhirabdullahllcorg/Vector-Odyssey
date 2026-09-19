"""Unit tests for vo.research.structure_range_agreement -- the
validation-only structure-range-rule-vs-Phase-13 agreement study
(confirmed 2026-09-19: report-only, RegimeEngine untouched). Mirrors
test_phase_agreement.py's own style, since this module mirrors
phase_agreement.py's own shape (see the module's own "DELIBERATE
DIVERGENCE" note for the one intentional difference: no window_label)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.observation.regime import RegimeType
from vo.observation.structure_range import RangeStructureType
from vo.research.regime_windows import RegimeInterval
from vo.research.structure_range_agreement import (
    RangeComparisonSample,
    build_range_agreement_report,
    build_range_comparison_samples,
    render_range_agreement_markdown,
)

_BASE = datetime(2023, 6, 1, 13, 30, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


# ── build_range_comparison_samples ───────────────────────────────────


def test_build_range_comparison_samples_looks_up_actual_phase() -> None:
    intervals = (
        RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=_at(10)),
        RegimeInterval(regime=RegimeType.CONSOLIDATION, start_utc=_at(10), end_utc=None),
    )
    range_states = (
        (_at(2), RangeStructureType.EXPANSION),
        (_at(12), RangeStructureType.CONSOLIDATION),
    )

    comparisons = build_range_comparison_samples(intervals, range_states)

    assert len(comparisons) == 2
    assert comparisons[0].actual_phase is RegimeType.EXPANSION
    assert comparisons[0].range_phase is RangeStructureType.EXPANSION  # agrees
    assert comparisons[1].actual_phase is RegimeType.CONSOLIDATION
    assert comparisons[1].range_phase is RangeStructureType.CONSOLIDATION  # agrees


def test_build_range_comparison_samples_no_covering_interval_is_none_actual() -> None:
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(50), end_utc=None),)
    range_states = ((_at(0), RangeStructureType.CONSOLIDATION),)  # before any interval starts

    comparisons = build_range_comparison_samples(intervals, range_states)

    assert comparisons[0].actual_phase is None
    assert comparisons[0].range_phase is RangeStructureType.CONSOLIDATION


def test_build_range_comparison_samples_preserves_order_and_timestamps() -> None:
    intervals = (RegimeInterval(regime=RegimeType.EXPANSION, start_utc=_at(0), end_utc=None),)
    range_states = (
        (_at(1), RangeStructureType.CONSOLIDATION),
        (_at(2), RangeStructureType.EXPANSION),
    )

    comparisons = build_range_comparison_samples(intervals, range_states)

    assert [c.when for c in comparisons] == [_at(1), _at(2)]


# ── build_range_agreement_report / render ────────────────────────────


def test_build_range_agreement_report_computes_rates_and_confusions() -> None:
    samples = (
        RangeComparisonSample(
            when=_at(0),
            actual_phase=RegimeType.EXPANSION,
            range_phase=RangeStructureType.EXPANSION,
        ),
        RangeComparisonSample(
            when=_at(1),
            actual_phase=RegimeType.EXPANSION,
            range_phase=RangeStructureType.RETRACEMENT,
        ),
        RangeComparisonSample(when=_at(2), actual_phase=RegimeType.EXPANSION, range_phase=None),
        RangeComparisonSample(
            when=_at(3),
            actual_phase=RegimeType.CONSOLIDATION,
            range_phase=RangeStructureType.CONSOLIDATION,
        ),
        RangeComparisonSample(
            when=_at(4), actual_phase=None, range_phase=RangeStructureType.EXPANSION
        ),
    )

    report = build_range_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=datetime(2026, 9, 19, tzinfo=UTC),
    )

    assert report.n_samples == 5
    assert report.n_comparable == 4  # the one with actual_phase=None is excluded
    assert report.overall_agreement_rate == pytest.approx(2 / 4)

    by_phase = {b.actual_phase: b for b in report.by_phase}
    assert by_phase[RegimeType.EXPANSION].n == 3
    assert by_phase[RegimeType.EXPANSION].agreed == 1
    assert by_phase[RegimeType.EXPANSION].guessed_instead == {
        "RETRACEMENT": 1,
        "NO RANGE ESTABLISHED": 1,
    }
    assert by_phase[RegimeType.CONSOLIDATION].n == 1
    assert by_phase[RegimeType.CONSOLIDATION].agreed == 1
    assert by_phase[RegimeType.CONSOLIDATION].guessed_instead == {}


def test_build_range_agreement_report_pullback_unresolved_always_mismatches() -> None:
    # PULLBACK_UNRESOLVED has no RangeStructureType counterpart -- see the
    # module's own "COMPARISON IS BY NAME" note -- so it can never be
    # counted as agreed, whatever the rule says.
    samples = (
        RangeComparisonSample(
            when=_at(0),
            actual_phase=RegimeType.PULLBACK_UNRESOLVED,
            range_phase=RangeStructureType.RETRACEMENT,
        ),
    )
    report = build_range_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=datetime(2026, 9, 19, tzinfo=UTC),
    )
    by_phase = {b.actual_phase: b for b in report.by_phase}
    assert by_phase[RegimeType.PULLBACK_UNRESOLVED].agreed == 0
    assert by_phase[RegimeType.PULLBACK_UNRESOLVED].guessed_instead == {"RETRACEMENT": 1}


def test_build_range_agreement_report_handles_no_comparable_samples() -> None:
    samples = (RangeComparisonSample(when=_at(0), actual_phase=None, range_phase=None),)
    report = build_range_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=datetime(2026, 9, 19, tzinfo=UTC),
    )
    assert report.n_comparable == 0
    assert report.overall_agreement_rate is None
    markdown = render_range_agreement_markdown(report)
    assert "n/a" in markdown


def test_render_range_agreement_markdown_states_validation_only() -> None:
    samples = (
        RangeComparisonSample(
            when=_at(0),
            actual_phase=RegimeType.EXPANSION,
            range_phase=RangeStructureType.EXPANSION,
        ),
    )
    report = build_range_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=datetime(2026, 9, 19, tzinfo=UTC),
    )
    markdown = render_range_agreement_markdown(report)
    assert "VALIDATION ONLY" in markdown
    assert "never" in markdown.lower()


def test_render_range_agreement_markdown_has_no_window_line() -> None:
    # DELIBERATE DIVERGENCE from render_phase_agreement_markdown: no
    # window_label field/line -- see the module's own docstring note.
    samples = (
        RangeComparisonSample(
            when=_at(0),
            actual_phase=RegimeType.EXPANSION,
            range_phase=RangeStructureType.EXPANSION,
        ),
    )
    report = build_range_agreement_report(
        samples,
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=datetime(2026, 9, 19, tzinfo=UTC),
    )
    markdown = render_range_agreement_markdown(report)
    assert "Window:" not in markdown
