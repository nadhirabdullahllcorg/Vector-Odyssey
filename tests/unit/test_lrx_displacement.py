"""vo.valco.lrx_displacement -- did the raid produce real opposing
expansion?

Covers the case list specified for this detector: expansion both ways,
weak reactions, single- and multi-bar legs, gaps present and absent,
stalls, reversals, multiple candidates from one raid, unusable ATR,
session boundaries, and no-lookahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    ExpansionDirection,
    OriginModel,
    Qualification,
    QualificationMode,
    measure_displacement,
    three_bar_gap,
)
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_sweep import SweepEvent

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_LEVEL = 20_050.0


def _bar(minute: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_START + timedelta(minutes=minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _baseline(count: int, centre: float = 20_000.0, half_range: float = 5.0) -> list[Bar]:
    """Quiet bars establishing an ATR of roughly 2 * half_range."""
    return [
        _bar(i, open_=centre, high=centre + half_range, low=centre - half_range, close=centre)
        for i in range(count)
    ]


def _sweep(returned_index: int, side: LevelSide = LevelSide.BUY_SIDE) -> SweepEvent:
    kind = LevelKind.PREV_DAY_HIGH if side is LevelSide.BUY_SIDE else LevelKind.PREV_DAY_LOW
    return SweepEvent(
        sweep_id=f"SWEEP:test:{returned_index}",
        level=ReferenceLevel(
            kind=kind, price=_LEVEL, established_at=None, trading_day=_START.date()
        ),
        side=side,
        penetration_index=returned_index - 1,
        penetration_price=_LEVEL + 8.0 if side is LevelSide.BUY_SIDE else _LEVEL - 8.0,
        penetration_distance=8.0,
        penetration_atr_multiple=0.8,
        closed_beyond=False,
        returned_index=returned_index,
        returned_at_utc=_START + timedelta(minutes=returned_index),
        bars_beyond=1,
    )


def _measure(bars: list[Bar], index: int, sweep: SweepEvent, **overrides: object):
    import dataclasses

    config = DisplacementConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)  # type: ignore[arg-type]
    return measure_displacement(bars, index, sweep, config, tick_size=_TICK)


# ── expansion in both directions ──────────────────────────────────────────


def test_bearish_expansion_after_a_buy_side_raid_qualifies() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.direction is ExpansionDirection.DOWN
    assert event.qualification is Qualification.PASS
    assert event.net_move_atr > 1.0


def test_bullish_expansion_after_a_sell_side_raid_qualifies() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_952.0, high=19_980.0, low=19_950.0, close=19_978.0))
    bars.append(_bar(21, open_=19_978.0, high=20_012.0, low=19_977.0, close=20_010.0))

    event = _measure(bars, 21, _sweep(20, side=LevelSide.SELL_SIDE))

    assert event is not None
    assert event.direction is ExpansionDirection.UP
    assert event.qualification is Qualification.PASS


# ── failure carries its numbers ───────────────────────────────────────────


def test_a_weak_reaction_fails_but_is_still_measured() -> None:
    """The whole point: a FAIL with its numbers is a finding; a False is
    not."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_044.0, close=20_046.0))
    bars.append(_bar(21, open_=20_046.0, high=20_047.0, low=20_042.0, close=20_045.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert event.range_atr > 0.0
    assert event.net_move_atr > 0.0
    assert "below" in event.qualification_reason


def test_a_big_range_that_closes_mid_range_fails_on_body_ratio() -> None:
    """Travelled far, committed to nothing -- indecision wearing a big
    range."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_045.0, high=20_050.0, low=19_990.0, close=20_044.0))
    bars.append(_bar(21, open_=20_044.0, high=20_048.0, low=19_995.0, close=20_020.0))

    event = _measure(bars, 21, _sweep(20), min_atr_range=0.5, min_net_move_atr=0.1)

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert "body ratio" in event.qualification_reason


def test_a_move_that_retraces_most_of_itself_fails_on_net_move() -> None:
    """Range alone would pass; net distance covered is what matters."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=19_990.0, close=19_992.0))
    bars.append(_bar(21, open_=19_992.0, high=20_046.0, low=19_991.0, close=20_044.0))

    event = _measure(bars, 21, _sweep(20), min_body_ratio=0.1)

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert "net move" in event.qualification_reason


# ── leg shape ─────────────────────────────────────────────────────────────


def test_a_single_bar_displacement_is_measurable() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 20, _sweep(19))

    assert event is not None
    assert event.bar_count == 2


def test_a_multi_bar_expansion_counts_its_directional_run() -> None:
    bars = _baseline(20)
    for i, close in enumerate((20_030.0, 20_010.0, 19_990.0), start=20):
        bars.append(_bar(i, open_=close + 18.0, high=close + 19.0, low=close - 1.0, close=close))

    event = _measure(bars, 22, _sweep(20))

    assert event is not None
    assert event.consecutive_directional_bars >= 2


def test_a_leg_longer_than_the_budget_is_not_measured() -> None:
    """An expansion that took twenty bars is not the impulsive move this
    model is looking for."""
    bars = _baseline(20)
    for i in range(20, 32):
        bars.append(_bar(i, open_=20_040.0, high=20_042.0, low=20_030.0, close=20_032.0))

    assert _measure(bars, 31, _sweep(20), max_bars=5) is None


def test_a_stall_produces_a_measured_failure_not_an_absence() -> None:
    bars = _baseline(20)
    for i in range(20, 24):
        bars.append(_bar(i, open_=20_045.0, high=20_047.0, low=20_043.0, close=20_045.0))

    event = _measure(bars, 23, _sweep(20))

    assert event is not None
    assert event.qualification is Qualification.FAIL


# ── gaps ──────────────────────────────────────────────────────────────────


def test_an_expansion_that_leaves_a_gap_records_it() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=20_000.0, close=20_002.0))
    # Third bar's high stays below the first bar's low -> bearish gap.
    bars.append(_bar(22, open_=20_002.0, high=20_020.0, low=19_980.0, close=19_985.0))

    event = _measure(bars, 22, _sweep(20))

    assert event is not None
    assert event.fvg_created is True
    assert event.gap is not None
    assert event.gap.lower < event.gap.upper
    assert event.gap.lower < event.gap.consequent_encroachment < event.gap.upper


def test_an_overlapping_expansion_leaves_no_gap() -> None:
    bars = _baseline(20)
    for i, close in enumerate((20_040.0, 20_030.0, 20_020.0), start=20):
        bars.append(_bar(i, open_=close + 10.0, high=close + 14.0, low=close - 12.0, close=close))

    event = _measure(bars, 22, _sweep(20))

    assert event is not None
    assert event.fvg_created is False


def test_gap_geometry_is_direction_aware() -> None:
    bars = [
        _bar(0, open_=20_000.0, high=20_010.0, low=19_990.0, close=20_005.0),
        _bar(1, open_=20_005.0, high=20_040.0, low=20_004.0, close=20_035.0),
        _bar(2, open_=20_035.0, high=20_050.0, low=20_020.0, close=20_045.0),
    ]

    assert three_bar_gap(bars, 2, ExpansionDirection.UP) is not None
    assert three_bar_gap(bars, 2, ExpansionDirection.DOWN) is None


# ── boundaries and data quality ───────────────────────────────────────────


def test_an_unusable_atr_yields_no_measurement_rather_than_a_guess() -> None:
    """Too little history for ATR means the thresholds have no scale to
    be measured against."""
    bars = [
        _bar(0, open_=20_048.0, high=20_050.0, low=20_040.0, close=20_042.0),
        _bar(1, open_=20_042.0, high=20_043.0, low=20_000.0, close=20_002.0),
    ]

    assert _measure(bars, 1, _sweep(0)) is None


def test_a_flat_market_with_zero_atr_is_refused() -> None:
    flat = [
        _bar(i, open_=20_000.0, high=20_000.0, low=20_000.0, close=20_000.0)
        for i in range(20)
    ]
    flat.append(_bar(20, open_=20_000.0, high=20_000.0, low=20_000.0, close=20_000.0))

    assert _measure(flat, 20, _sweep(19)) is None


def test_an_index_at_or_before_the_raid_is_not_a_leg() -> None:
    bars = _baseline(22)

    assert _measure(bars, 20, _sweep(20)) is None
    assert _measure(bars, 19, _sweep(20)) is None


def test_a_leg_spanning_a_session_gap_still_measures_elapsed_time() -> None:
    """Bars either side of a closure are hours apart; velocity must
    reflect that rather than assuming one minute per bar."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    late = _bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0)
    bars.append(
        Bar(
            instrument_id=late.instrument_id,
            timeframe=late.timeframe,
            open_time_utc=late.open_time_utc + timedelta(hours=17),
            open=late.open,
            high=late.high,
            low=late.low,
            close=late.close,
            tick_volume=late.tick_volume,
            real_volume=late.real_volume,
            spread=late.spread,
        )
    )

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.elapsed_seconds > 3600
    assert event.velocity_atr_per_minute < 0.1


# ── multiple candidates and lookahead ─────────────────────────────────────


def test_one_raid_can_produce_several_candidate_legs() -> None:
    """Evaluating at successive indices is how a caller watches an
    expansion develop."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_040.0, close=20_042.0))
    bars.append(_bar(21, open_=20_042.0, high=20_043.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(22, open_=20_022.0, high=20_023.0, low=19_985.0, close=19_988.0))

    sweep = _sweep(20)
    legs = [_measure(bars, i, sweep) for i in (21, 22)]

    assert all(leg is not None for leg in legs)
    assert legs[0].net_move_atr < legs[1].net_move_atr  # type: ignore[union-attr]


def test_measurement_never_reads_past_the_evaluated_bar() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    before = _measure(bars, 21, _sweep(20))
    bars.append(_bar(22, open_=19_992.0, high=21_500.0, low=19_000.0, close=21_400.0))

    assert _measure(bars, 21, _sweep(20)) == before


def test_the_origin_and_equilibrium_bracket_the_leg() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.low < event.expansion_equilibrium < event.expansion_origin


# ── spec section 3: causal attribution ────────────────────────────────────


def test_every_displacement_carries_the_id_of_the_raid_that_caused_it() -> None:
    """Without this, the research log records what happened but not what
    caused what -- and the FVG/MSS attribution rules cannot be enforced."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))
    sweep = _sweep(20)

    event = _measure(bars, 21, sweep)

    assert event is not None
    assert event.sweep_id == sweep.sweep_id
    assert event.displacement_id.startswith("DISP:")
    assert sweep.sweep_id in event.displacement_id


# ── spec section 4: event time vs confirmation time ───────────────────────


def test_the_event_time_and_confirmation_time_are_distinct() -> None:
    """A leg that started at 10:31 is not knowable at 10:31."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.event_at_utc < event.confirmation_at_utc


# ── spec section 12: CE is the midpoint of the expansion RANGE ────────────


def test_equilibrium_is_the_midpoint_of_the_expansion_range() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.expansion_equilibrium == (event.high + event.low) / 2.0


# ── spec section 10: qualification modes are configurable ─────────────────


def test_one_leg_can_pass_under_one_definition_and_fail_under_another() -> None:
    """Which definition of 'displaced' is in force must be a config
    choice, not a constant baked into the detector."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_049.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_988.0, close=19_990.0))

    by_range = _measure(bars, 21, _sweep(20), mode=QualificationMode.ATR_RANGE)
    by_velocity = _measure(
        bars,
        21,
        _sweep(20),
        mode=QualificationMode.VELOCITY,
        min_velocity_atr_per_minute=99.0,
    )

    assert by_range is not None and by_velocity is not None
    assert by_range.qualification is Qualification.PASS
    assert by_velocity.qualification is Qualification.FAIL
    assert by_range.mode is QualificationMode.ATR_RANGE


def test_the_active_mode_is_recorded_on_the_event() -> None:
    """A run's events must say which definition produced them."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20), mode=QualificationMode.ATR_BODY)

    assert event is not None
    assert event.mode is QualificationMode.ATR_BODY


# ── spec section 11: origin is a model, not an assumption ─────────────────


def test_every_origin_model_is_computed_and_the_configured_one_selected() -> None:
    """The spec warns against assuming one candle is always the correct
    origin, so all candidates are kept for later comparison."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    by_sweep = _measure(bars, 21, _sweep(20), origin_model=OriginModel.SWEEP_EXTREME)
    by_leg = _measure(bars, 21, _sweep(20), origin_model=OriginModel.LEG_START_OPEN)

    assert by_sweep is not None and by_leg is not None
    assert len(by_sweep.origin_candidates) == 3
    assert by_sweep.expansion_origin != by_leg.expansion_origin
    assert by_sweep.origin_candidates == by_leg.origin_candidates


# ── spec section 10: the remaining required measurements ──────────────────


def test_the_full_measurement_set_is_recorded() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.wick_points >= 0.0
    assert event.body_atr > 0.0
    assert event.distance_from_sweep > 0.0
    assert event.directional_bars >= event.consecutive_directional_bars - 1


def test_distance_from_sweep_measures_reach_beyond_the_level_not_leg_range() -> None:
    """The reversal's reach away from the swept level is a different
    fact from how tall the leg was."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.distance_from_sweep == abs(event.low - _LEVEL)


# ── data integrity: measured, missing, and genuinely zero ─────────────────
#
# Added by the pre-Inefficiency data-integrity audit. The suite above
# verified that these fields EXIST; none of it verified what they say
# when the thing they measure is unavailable, which is how velocity spent
# its whole life being wrong without a red test.


def test_elapsed_time_spans_the_leg_not_the_gaps_between_its_opens() -> None:
    """Open-to-open measures the intervals BETWEEN a leg's bars. A leg
    runs from its first bar's open to its last bar's CLOSE, which is one
    timeframe past the last open -- so an n-bar M1 leg lasts n minutes,
    not n-1."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.bar_count == 2
    assert event.elapsed_seconds == 60.0 * event.bar_count
    # The old formula. Kept explicit so a regression names itself.
    assert event.elapsed_seconds != 60.0 * (event.bar_count - 1)


def test_a_leg_never_reports_zero_elapsed_time() -> None:
    """The defect this replaces: a one-bar leg spanned zero seconds, so
    velocity read 0.00 -- the slowest possible value -- for the explosive
    single candle that is the archetypal displacement."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 20, _sweep(19))

    assert event is not None
    assert event.elapsed_seconds is not None
    assert event.elapsed_seconds > 0.0


def test_velocity_is_net_movement_over_the_legs_real_duration() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.velocity_atr_per_minute is not None
    assert event.elapsed_seconds is not None
    assert event.velocity_atr_per_minute == pytest.approx(
        event.net_move_atr / (event.elapsed_seconds / 60.0)
    )
    # Two bars, two minutes -- not one.
    assert event.velocity_atr_per_minute == pytest.approx(event.net_move_atr / 2.0)


def test_velocity_qualification_accepts_a_fast_leg() -> None:
    """Under the old arithmetic a short sharp leg could be scored against
    an elapsed time shorter than it really took, and a one-bar leg was
    rejected outright. Both are qualification-affecting, not cosmetic."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20), mode=QualificationMode.VELOCITY)

    assert event is not None
    assert event.qualification is Qualification.PASS


def test_a_true_zero_survives_as_zero() -> None:
    """A marubozu genuinely has no wick. Missing values became None in
    this audit; measurements that really are zero must stay zero."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_050.0, high=20_050.0, low=20_020.0, close=20_020.0))
    bars.append(_bar(21, open_=20_020.0, high=20_020.0, low=19_990.0, close=19_990.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.wick_points == 0.0
    assert event.body_ratio == pytest.approx(1.0)
    assert event.close_location == pytest.approx(1.0)


def test_qualification_separates_unmeasurable_from_failed() -> None:
    """Three outcomes, not two: the threshold was met, the threshold was
    tested and missed, or the threshold was never testable. A report that
    merged the last two would bury them in one bucket."""
    assert {q.value for q in Qualification} == {"PASS", "FAIL", "UNMEASURABLE"}
    assert Qualification.UNMEASURABLE is not Qualification.FAIL


def test_a_failed_leg_still_carries_the_measurements_it_could_take() -> None:
    """A FAIL that erased its own numbers would make every near-miss
    invisible."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_000.0, high=20_001.0, low=19_997.0, close=19_998.0))

    event = _measure(bars, 20, _sweep(19))

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert event.range_atr > 0.0
    assert event.elapsed_seconds is not None
    assert event.velocity_atr_per_minute is not None
    assert event.qualification_reason != ""
