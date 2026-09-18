"""Unit tests for vo.research.hmm_report -- Phase 17a's IHMMEngine
persistence-character classifier. The pure orchestration functions
(killzone filtering, state profiling, validation-check logic) are tested
directly with fabricated data; the actual hmmlearn fit is exercised in
one end-to-end test, skipped when the optional [hmm] extra is not
installed -- same pattern test_dashboard_backend.py uses for fastapi."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.research.hmm_report import (
    ACCUMULATION_ER_TARGET,
    ACCUMULATION_HURST_TARGET,
    DISPLACEMENT_ER_TARGET,
    DISPLACEMENT_HURST_TARGET,
    KILLZONE_NY_AM_END,
    KILLZONE_NY_AM_START,
    HMMSample,
    HMMStateProfile,
    build_hmm_validation_report,
    build_killzone_samples,
    build_state_profiles,
    render_hmm_report_markdown,
    train_hmm,
)

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")
_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def _ny_bar(day: int, hour: int, minute: int, close: float) -> Bar:
    local = datetime(2023, 6, 1 + day, hour, minute, tzinfo=_NY)
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=local.astimezone(_UTC),
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        tick_volume=1,
        real_volume=0,
    )


def test_build_killzone_samples_keeps_only_the_ny_am_window() -> None:
    bars = [
        _ny_bar(0, 8, 0, 100.0),  # before window
        _ny_bar(0, 8, 30, 101.0),  # window start, inclusive
        _ny_bar(0, 10, 0, 102.0),  # inside
        _ny_bar(0, 11, 30, 103.0),  # window end, inclusive
        _ny_bar(0, 12, 0, 104.0),  # after window
    ]
    atr_samples = [(b.open_time_utc, 1.0) for b in bars]
    er_samples = [(b.open_time_utc, 0.5) for b in bars]

    samples = build_killzone_samples(bars, atr_samples, er_samples)

    kept_times = {s.when for s in samples}
    assert bars[0].open_time_utc not in kept_times
    assert bars[1].open_time_utc in kept_times
    assert bars[2].open_time_utc in kept_times
    assert bars[3].open_time_utc in kept_times
    assert bars[4].open_time_utc not in kept_times


def test_build_killzone_samples_computes_normalized_return() -> None:
    bars = [_ny_bar(0, 9, 0, 100.0), _ny_bar(0, 9, 1, 102.0)]
    atr_samples = [(bars[0].open_time_utc, 2.0), (bars[1].open_time_utc, 2.0)]
    er_samples = [(bars[0].open_time_utc, 0.7), (bars[1].open_time_utc, 0.7)]

    samples = build_killzone_samples(bars, atr_samples, er_samples)

    assert len(samples) == 1  # first bar has no prior bar to compute a return from
    sample = samples[0]
    expected_log_return = math.log(102.0 / 100.0)
    assert sample.log_return == pytest.approx(expected_log_return)
    assert sample.norm_return == pytest.approx(expected_log_return / 2.0)
    assert sample.er == 0.7


def test_build_killzone_samples_drops_missing_atr_or_er() -> None:
    bars = [_ny_bar(0, 9, 0, 100.0), _ny_bar(0, 9, 1, 101.0)]
    samples = build_killzone_samples(bars, atr_samples=(), er_samples=())
    assert samples == ()


def test_build_killzone_samples_window_bounds_are_configurable() -> None:
    assert KILLZONE_NY_AM_START.hour == 8
    assert KILLZONE_NY_AM_START.minute == 30
    assert KILLZONE_NY_AM_END.hour == 11
    assert KILLZONE_NY_AM_END.minute == 30


def test_build_state_profiles_groups_and_aggregates_by_state() -> None:
    when0 = datetime(2023, 6, 1, 13, 30, tzinfo=_UTC)
    samples = (
        HMMSample(when=when0, log_return=0.01, norm_return=1.0, er=0.8),
        HMMSample(when=when0 + timedelta(minutes=1), log_return=0.02, norm_return=1.5, er=0.9),
        HMMSample(when=when0 + timedelta(minutes=2), log_return=-0.01, norm_return=-0.4, er=0.2),
    )
    states = (0, 0, 1)
    hurst_samples = (
        (when0, 0.7),
        (when0 + timedelta(minutes=1), 0.75),
        (when0 + timedelta(minutes=2), 0.3),
    )

    profiles = build_state_profiles(samples, states, hurst_samples)

    by_state = {p.state: p for p in profiles}
    assert by_state[0].n == 2
    assert by_state[0].mean_er == pytest.approx((0.8 + 0.9) / 2)
    assert by_state[0].mean_hurst == pytest.approx((0.7 + 0.75) / 2)
    assert by_state[1].n == 1
    assert by_state[1].mean_er == pytest.approx(0.2)


def test_build_state_profiles_rejects_misaligned_lengths() -> None:
    when0 = datetime(2023, 6, 1, 13, 30, tzinfo=_UTC)
    samples = (HMMSample(when=when0, log_return=0.0, norm_return=0.0, er=0.5),)
    with pytest.raises(ValueError, match="align"):
        build_state_profiles(samples, states=(0, 1), hurst_samples=())


class _FakeModel:
    """Stand-in for a fitted GaussianHMM -- only `transmat_` is read by
    build_hmm_validation_report, so the real hmmlearn dependency is not
    needed to test this function's own logic."""

    def __init__(self, transmat: tuple[tuple[float, ...], ...]) -> None:
        self.transmat_ = transmat


def test_build_hmm_validation_report_identifies_displacement_and_accumulation() -> None:
    profiles = (
        HMMStateProfile(
            state=0,
            n=50,
            mean_er=0.75,
            mean_hurst=0.6,
            mean_atr=1.0,
            return_volatility=0.01,
        ),
        HMMStateProfile(
            state=1,
            n=40,
            mean_er=0.20,
            mean_hurst=0.30,
            mean_atr=0.8,
            return_volatility=0.02,
        ),
        HMMStateProfile(
            state=2,
            n=30,
            mean_er=0.50,
            mean_hurst=0.50,
            mean_atr=0.9,
            return_volatility=0.015,
        ),
    )
    transmat = ((0.85, 0.10, 0.05), (0.10, 0.80, 0.10), (0.20, 0.20, 0.60))
    model = _FakeModel(transmat)

    report = build_hmm_validation_report(
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=_UTC),
        window_label="NY_AM killzone 08:30-11:30 ET",
        model=model,
        samples=(),
        profiles=profiles,
    )

    assert report.displacement_state == 0  # highest mean ER
    assert report.accumulation_state == 1  # lowest mean ER
    assert report.self_transition_displacement_ok is True  # transmat[0][0] = 0.85 > 0.80
    assert report.displacement_clustering_ok is True  # ER 0.75 > 0.60, Hurst 0.6 > 0.55
    assert report.accumulation_clustering_ok is True  # ER 0.20 < 0.35, Hurst 0.30 < 0.45


def test_build_hmm_validation_report_reports_fail_honestly() -> None:
    # DISPLACEMENT-candidate state's self-transition is well below target.
    profiles = (
        HMMStateProfile(
            state=0,
            n=20,
            mean_er=0.65,
            mean_hurst=0.60,
            mean_atr=1.0,
            return_volatility=0.01,
        ),
        HMMStateProfile(
            state=1,
            n=20,
            mean_er=0.30,
            mean_hurst=0.40,
            mean_atr=0.9,
            return_volatility=0.02,
        ),
    )
    transmat = ((0.40, 0.60), (0.60, 0.40))
    model = _FakeModel(transmat)

    report = build_hmm_validation_report(
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=_UTC),
        window_label="NY_AM killzone 08:30-11:30 ET",
        model=model,
        samples=(),
        profiles=profiles,
    )

    assert report.self_transition_displacement_ok is False
    markdown = render_hmm_report_markdown(report)
    assert "FAIL" in markdown
    assert "DISPLACEMENT" in markdown
    assert "ACCUMULATION" in markdown


def test_build_hmm_validation_report_handles_no_profiles() -> None:
    report = build_hmm_validation_report(
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=_UTC),
        window_label="NY_AM killzone 08:30-11:30 ET",
        model=_FakeModel(()),
        samples=(),
        profiles=(),
    )
    assert report.displacement_state is None
    assert report.accumulation_state is None
    markdown = render_hmm_report_markdown(report)
    assert "n/a" in markdown


def test_render_hmm_report_markdown_never_uses_phase13_regime_vocabulary() -> None:
    """G2/Sec 5.6 discipline check: the rendered report must never claim
    CONSOLIDATION/EXPANSION/RETRACEMENT/REVERSAL as an HMM state label --
    that vocabulary belongs to Phase 13's RegimeType, a different axis."""
    profiles = (
        HMMStateProfile(
            state=0,
            n=10,
            mean_er=0.7,
            mean_hurst=0.6,
            mean_atr=1.0,
            return_volatility=0.01,
        ),
        HMMStateProfile(
            state=1,
            n=10,
            mean_er=0.2,
            mean_hurst=0.3,
            mean_atr=0.9,
            return_volatility=0.02,
        ),
    )
    report = build_hmm_validation_report(
        instrument_key="US100",
        timeframe_canonical="M5",
        generated_utc=datetime(2026, 9, 18, tzinfo=_UTC),
        window_label="NY_AM killzone 08:30-11:30 ET",
        model=_FakeModel(((0.9, 0.1), (0.1, 0.9))),
        samples=(),
        profiles=profiles,
    )
    markdown = render_hmm_report_markdown(report)
    for forbidden in ("CONSOLIDATION", "EXPANSION", "RETRACEMENT", "REVERSAL"):
        assert forbidden not in markdown


def test_targets_match_confirmed_benchmarks() -> None:
    # Pins the confirmed 2026-09-18 Sec 5.7-revision benchmark values so a
    # future edit to these constants is a deliberate, visible test change.
    assert DISPLACEMENT_ER_TARGET == 0.60
    assert DISPLACEMENT_HURST_TARGET == 0.55
    assert ACCUMULATION_ER_TARGET == 0.35
    assert ACCUMULATION_HURST_TARGET == 0.45


def test_train_hmm_end_to_end_with_real_hmmlearn() -> None:
    pytest.importorskip("hmmlearn")

    samples: list[HMMSample] = []
    when0 = datetime(2023, 6, 1, 13, 30, tzinfo=_UTC)
    for i in range(120):
        when = when0 + timedelta(minutes=i)
        if i % 3 == 0:
            # a "displacement-like" burst: persistent normalized return, high ER
            samples.append(HMMSample(when=when, log_return=0.002, norm_return=1.2, er=0.75))
        else:
            # "accumulation-like" chop: alternating sign, low ER
            sign = 1.0 if i % 2 == 0 else -1.0
            samples.append(
                HMMSample(
                    when=when, log_return=sign * 0.0005, norm_return=sign * 0.3, er=0.15
                )
            )

    model, states = train_hmm(tuple(samples), n_states=3, min_samples_per_state=10)

    assert len(states) == len(samples)
    assert all(0 <= s < 3 for s in states)
    assert model.transmat_.shape == (3, 3)

    profiles = build_state_profiles(tuple(samples), states, hurst_samples=())
    assert sum(p.n for p in profiles) == len(samples)

    report = build_hmm_validation_report(
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=datetime(2026, 9, 18, tzinfo=_UTC),
        window_label="NY_AM killzone 08:30-11:30 ET (synthetic test data)",
        model=model,
        samples=tuple(samples),
        profiles=profiles,
    )
    # Just confirm it renders without error on a real fit -- the specific
    # pass/fail is not asserted here since a 120-sample synthetic fit is
    # not a real back-test.
    markdown = render_hmm_report_markdown(report)
    assert "Transition matrix" in markdown


def test_train_hmm_refuses_too_few_samples() -> None:
    pytest.importorskip("hmmlearn")
    when0 = datetime(2023, 6, 1, 13, 30, tzinfo=_UTC)
    samples = tuple(
        HMMSample(when=when0 + timedelta(minutes=i), log_return=0.0, norm_return=0.0, er=0.5)
        for i in range(5)
    )
    with pytest.raises(ValueError, match="killzone samples"):
        train_hmm(samples, n_states=3, min_samples_per_state=10)
