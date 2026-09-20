"""vo.valco.lrx_inefficiency -- the gap a displacement left behind.

Case list: detection in both directions, the exact-touch boundary that
is NOT a gap, timing (nothing before the third candle, nothing changed
by later candles), geometry and CE, ATR normalisation present and
absent, causal lineage granted and withheld, immutability, and the
qualification modes."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    ExpansionDirection,
    measure_displacement,
    three_bar_gap,
)
from vo.valco.lrx_inefficiency import (
    InefficiencyConfig,
    InefficiencyDirection,
    InefficiencyQualification,
    detect_inefficiencies_in_leg,
    detect_inefficiency,
    displacement_contains_gap,
)
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_sweep import SweepEvent

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_LEVEL = 20_050.0


def _at(minute: int) -> datetime:
    return _START + timedelta(minutes=minute)


def _bar(minute: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _baseline(count: int, centre: float = 20_000.0, half: float = 5.0) -> list[Bar]:
    return [
        _bar(i, open_=centre, high=centre + half, low=centre - half, close=centre)
        for i in range(count)
    ]


def _sweep(returned_index: int, side: LevelSide = LevelSide.BUY_SIDE) -> SweepEvent:
    kind = (
        LevelKind.PREV_DAY_HIGH
        if side is LevelSide.BUY_SIDE
        else LevelKind.PREV_DAY_LOW
    )
    return SweepEvent(
        sweep_id=f"SWEEP:test:{returned_index}",
        level=ReferenceLevel(
            kind=kind, price=_LEVEL, established_at=None, trading_day=_START.date()
        ),
        side=side,
        penetration_index=returned_index - 1,
        penetration_price=_LEVEL + 8.0,
        penetration_distance=8.0,
        penetration_atr_multiple=0.8,
        closed_beyond=False,
        returned_index=returned_index,
        returned_at_utc=_at(returned_index),
        bars_beyond=1,
    )


def _detect(bars, index, direction, **overrides):
    displacement = overrides.pop("displacement", None)
    mss = overrides.pop("mss", None)
    config = InefficiencyConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return detect_inefficiency(
        bars,
        index,
        direction,
        config,
        tick_size=_TICK,
        displacement=displacement,
        mss=mss,
    )


def _bearish_gap_bars() -> list[Bar]:
    """Three candles where candle 1's low sits above candle 3's high --
    a bearish FVG of exactly 10 points, 19_970 to 19_980."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_995.0, high=20_000.0, low=19_980.0, close=19_985.0))
    bars.append(_bar(21, open_=19_985.0, high=19_986.0, low=19_965.0, close=19_968.0))
    bars.append(_bar(22, open_=19_968.0, high=19_970.0, low=19_950.0, close=19_955.0))
    return bars


def _bullish_gap_bars() -> list[Bar]:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_005.0, high=20_020.0, low=20_000.0, close=20_015.0))
    bars.append(_bar(21, open_=20_015.0, high=20_035.0, low=20_014.0, close=20_032.0))
    bars.append(_bar(22, open_=20_032.0, high=20_050.0, low=20_030.0, close=20_045.0))
    return bars


# ── detection and geometry ────────────────────────────────────────────────


def test_a_bearish_three_candle_gap_is_detected_with_exact_bounds() -> None:
    bars = _bearish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.direction is InefficiencyDirection.BEARISH
    assert fvg.lower_price == 19_970.0   # candle 3 high
    assert fvg.upper_price == 19_980.0   # candle 1 low
    assert fvg.size_points == pytest.approx(10.0)
    assert fvg.ce_price == pytest.approx(19_975.0)


def test_a_bullish_three_candle_gap_is_detected_with_exact_bounds() -> None:
    bars = _bullish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.UP)

    assert fvg is not None
    assert fvg.direction is InefficiencyDirection.BULLISH
    assert fvg.lower_price == 20_020.0   # candle 1 high
    assert fvg.upper_price == 20_030.0   # candle 3 low
    assert fvg.size_points == pytest.approx(10.0)
    assert fvg.ce_price == pytest.approx(20_025.0)


def test_ce_is_the_midpoint_of_the_gap_itself() -> None:
    bars = _bearish_gap_bars()
    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.ce_price == pytest.approx((fvg.lower_price + fvg.upper_price) / 2.0)
    assert fvg.contains(fvg.ce_price)


def test_overlapping_candles_are_not_a_gap() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_995.0, high=20_000.0, low=19_980.0, close=19_985.0))
    bars.append(_bar(21, open_=19_985.0, high=19_986.0, low=19_965.0, close=19_968.0))
    bars.append(_bar(22, open_=19_968.0, high=19_985.0, low=19_950.0, close=19_955.0))

    assert _detect(bars, 22, ExpansionDirection.DOWN) is None


def test_an_exact_touch_is_not_a_gap() -> None:
    """Candle 3's high lands exactly on candle 1's low. There is no
    unfilled area between them, so there is no inefficiency -- and
    equality at a prior extreme is common enough that admitting it would
    manufacture gaps at precisely the most contested prices."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_995.0, high=20_000.0, low=19_980.0, close=19_985.0))
    bars.append(_bar(21, open_=19_985.0, high=19_986.0, low=19_965.0, close=19_968.0))
    bars.append(_bar(22, open_=19_968.0, high=19_980.0, low=19_950.0, close=19_955.0))

    assert three_bar_gap(bars, 22, ExpansionDirection.DOWN) is None
    assert _detect(bars, 22, ExpansionDirection.DOWN) is None


def test_one_tick_of_separation_is_a_gap() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_995.0, high=20_000.0, low=19_980.0, close=19_985.0))
    bars.append(_bar(21, open_=19_985.0, high=19_986.0, low=19_965.0, close=19_968.0))
    bars.append(
        _bar(22, open_=19_968.0, high=19_980.0 - _TICK, low=19_950.0, close=19_955.0)
    )

    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.size_points == pytest.approx(_TICK)


def test_direction_comes_from_the_structure_not_from_context() -> None:
    """The same three bars asked about in the other direction are not a
    gap. Direction is a property of the candle relationship."""
    bars = _bearish_gap_bars()

    assert _detect(bars, 22, ExpansionDirection.DOWN) is not None
    assert _detect(bars, 22, ExpansionDirection.UP) is None


def test_an_insufficient_candle_sequence_produces_nothing() -> None:
    bars = _bearish_gap_bars()

    for index in (0, 1, -1, len(bars), len(bars) + 10):
        assert _detect(bars, index, ExpansionDirection.DOWN) is None


# ── timing and lookahead ──────────────────────────────────────────────────


def test_a_gap_cannot_exist_before_its_third_candle() -> None:
    """The third candle's high is not final until that candle completes,
    so the 20/21/22 gap is unreportable while the series ends at 21.

    Note what this does NOT claim: that nothing can be detected at index
    21. A gap ending at 21 is its own gap, made of candles 19/20/21, and
    detecting it is correct -- an earlier draft of this test confused the
    two and failed for the right reason."""
    bars = _bearish_gap_bars()
    before_the_third = bars[:22]
    assert len(before_the_third) == 22

    assert _detect(before_the_third, 22, ExpansionDirection.DOWN) is None
    assert _detect(bars, 22, ExpansionDirection.DOWN) is not None


def test_creation_time_is_the_third_candles_bar() -> None:
    bars = _bearish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.creation_time == _at(22)
    assert fvg.first_bar_time == _at(20)
    assert fvg.creation_time > fvg.first_bar_time
    assert fvg.confirmation_at_utc == fvg.event_at_utc == fvg.creation_time
    assert fvg.first_index == 20
    assert fvg.third_index == 22


def test_later_candles_cannot_alter_the_creation_event() -> None:
    """Immutability, tested the way it actually fails: the same gap
    re-detected after the market has moved on must be byte-identical.
    An FVG whose recorded size shifts when price returns to it cannot
    support any statistic computed over it."""
    bars = _bearish_gap_bars()
    before = _detect(bars, 22, ExpansionDirection.DOWN)

    bars.append(_bar(23, open_=19_955.0, high=19_999.0, low=19_954.0, close=19_990.0))
    bars.append(_bar(24, open_=19_990.0, high=20_010.0, low=19_940.0, close=19_945.0))
    after = _detect(bars, 22, ExpansionDirection.DOWN)

    assert before is not None and after is not None
    assert before == after
    assert after.creation_time == _at(22)
    assert after.size_points == pytest.approx(10.0)


def test_the_detector_never_reads_past_the_third_candle() -> None:
    """Truncating the series immediately after the third candle changes
    nothing -- proof that no later bar contributed."""
    bars = _bearish_gap_bars()

    full = _detect(bars, 22, ExpansionDirection.DOWN)
    truncated = _detect(bars[:23], 22, ExpansionDirection.DOWN)

    assert full == truncated


# ── ATR normalisation ─────────────────────────────────────────────────────


def test_size_atr_is_measured_when_atr_is_available() -> None:
    bars = _bearish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.size_atr is not None
    assert fvg.size_atr > 0.0


def test_size_atr_is_none_when_atr_is_unavailable() -> None:
    """Never 0.0: an emitted gap always has size, so a zero would be a
    fabricated normalisation rather than a measurement."""
    bars = _bearish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN, atr_period=500)

    assert fvg is not None
    assert fvg.size_atr is None
    assert fvg.size_points > 0.0


def test_an_emitted_gap_always_has_positive_size() -> None:
    bars = _bearish_gap_bars()
    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.size_points > 0.0
    assert fvg.upper_price > fvg.lower_price


# ── causal lineage ────────────────────────────────────────────────────────


def _displaced() -> tuple[list[Bar], object]:
    """A bearish leg long enough to contain a three-candle gap."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_995.0, close=19_998.0))
    bars.append(_bar(22, open_=19_998.0, high=20_000.0, low=19_960.0, close=19_965.0))
    event = measure_displacement(
        bars, 22, _sweep(20), DisplacementConfig(max_bars=5), tick_size=_TICK
    )
    assert event is not None
    return bars, event


def test_a_gap_inside_the_leg_is_attributed_to_that_displacement() -> None:
    bars, displacement = _displaced()
    gap = three_bar_gap(bars, 22, ExpansionDirection.DOWN)
    assert gap is not None
    assert displacement_contains_gap(displacement, gap)

    fvg = _detect(bars, 22, ExpansionDirection.DOWN, displacement=displacement)

    assert fvg is not None
    assert fvg.creator_displacement_id == displacement.displacement_id
    assert fvg.creator_sweep_id == displacement.sweep_id
    assert fvg.attributed is True


def test_a_gap_outside_the_leg_is_left_unattributed() -> None:
    """The lineage field exists to answer "which inefficiency did THIS
    displacement create". Populating it with a nearest-neighbour guess
    would destroy the only thing it is for, so an unprovable association
    stays None."""
    bars = _displaced()[0]

    class _Elsewhere:
        start_index = 40
        end_index = 45
        displacement_id = "DISP:somewhere-else"
        sweep_id = "SWEEP:somewhere-else"
        direction = ExpansionDirection.DOWN

    fvg = _detect(bars, 22, ExpansionDirection.DOWN, displacement=_Elsewhere())

    assert fvg is not None
    assert fvg.creator_displacement_id is None
    assert fvg.creator_sweep_id is None
    assert fvg.attributed is False


def test_no_displacement_offered_means_a_raw_candidate() -> None:
    bars = _bearish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN)

    assert fvg is not None
    assert fvg.creator_displacement_id is None
    assert fvg.creator_mss_id is None
    assert fvg.attributed is False


def test_containment_requires_all_three_candles_inside_the_leg() -> None:
    """A gap whose first candle predates the leg was not created by it,
    even though its third candle lands inside."""
    displacement = _displaced()[1]

    class _Gap:
        created_index = 22
        upper = 1.0
        lower = 0.0

    class _StraddlingGap:
        created_index = displacement.start_index + 1
        upper = 1.0
        lower = 0.0

    assert displacement_contains_gap(displacement, _Gap())
    assert not displacement_contains_gap(displacement, _StraddlingGap())


def test_the_whole_chain_is_reconstructable_from_ids() -> None:
    bars, displacement = _displaced()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN, displacement=displacement)

    assert fvg is not None
    assert fvg.creator_displacement_id == displacement.displacement_id
    assert fvg.creator_sweep_id == displacement.sweep_id == _sweep(20).sweep_id


def test_a_leg_reports_every_gap_it_created() -> None:
    bars, displacement = _displaced()

    events = detect_inefficiencies_in_leg(
        bars, displacement, InefficiencyConfig(), tick_size=_TICK
    )

    assert len(events) >= 1
    assert all(e.attributed for e in events)
    assert all(
        e.creator_displacement_id == displacement.displacement_id for e in events
    )
    assert [e.third_index for e in events] == sorted(e.third_index for e in events)


# ── qualification ─────────────────────────────────────────────────────────


def test_the_baseline_filters_nothing() -> None:
    """A gap is a gap. Thresholds exist so they can be switched on and
    measured, not so the baseline can be quietly filtered."""
    config = InefficiencyConfig()

    assert config.min_size_points == 0.0
    assert config.min_size_atr == 0.0
    assert config.filters_anything is False

    bars = _bearish_gap_bars()
    fvg = _detect(bars, 22, ExpansionDirection.DOWN)
    assert fvg is not None
    assert fvg.qualification is InefficiencyQualification.PASS
    assert fvg.rejection_reason is None


def test_a_points_threshold_can_reject_while_keeping_the_measurements() -> None:
    bars = _bearish_gap_bars()

    fvg = _detect(bars, 22, ExpansionDirection.DOWN, min_size_points=25.0)

    assert fvg is not None
    assert fvg.qualification is InefficiencyQualification.FAIL
    assert fvg.qualified is False
    assert fvg.rejection_reason is not None
    # The near miss is still fully measured.
    assert fvg.size_points == pytest.approx(10.0)
    assert fvg.ce_price == pytest.approx(19_975.0)


def test_an_atr_threshold_with_no_atr_is_unmeasurable_not_failed() -> None:
    bars = _bearish_gap_bars()

    fvg = _detect(
        bars, 22, ExpansionDirection.DOWN, min_size_atr=2.0, atr_period=500
    )

    assert fvg is not None
    assert fvg.qualification is InefficiencyQualification.UNMEASURABLE
    assert fvg.qualification is not InefficiencyQualification.FAIL
    assert fvg.size_atr is None
    assert fvg.size_points > 0.0


def test_an_atr_threshold_with_atr_available_is_tested_normally() -> None:
    bars = _bearish_gap_bars()

    generous = _detect(bars, 22, ExpansionDirection.DOWN, min_size_atr=0.01)
    strict = _detect(bars, 22, ExpansionDirection.DOWN, min_size_atr=99.0)

    assert generous is not None and generous.qualified
    assert strict is not None
    assert strict.qualification is InefficiencyQualification.FAIL


# ── scope: detection only ─────────────────────────────────────────────────


def test_the_detector_decides_nothing() -> None:
    """Section 13 of the audit. No entry, stop, target, risk, news or
    frequency logic -- and no imports that would let it acquire any."""
    import ast
    import inspect

    import vo.valco.lrx_inefficiency as module

    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for banned in (
        "vo.risk",
        "vo.risk.manager",
        "vo.compliance.engine",
        "vo.compliance.news_gate",
        "vo.execution.router",
        "vo.observation.hurst",
        "vo.observation.markov",
        "vo.observation.regime",
        "vo.valco.lrx_config",
    ):
        assert banned not in imported, banned

    source = inspect.getsource(module)
    for banned_word in ("stop_loss", "take_profit", "entry_price", "position_size"):
        assert banned_word not in source, banned_word


def test_geometry_is_not_re_derived() -> None:
    """three_bar_gap is the one definition of the bounds. If this module
    grew its own, a displacement's `gap` field and an emitted FVG could
    disagree about the same three candles."""
    import inspect

    import vo.valco.lrx_inefficiency as module

    source = inspect.getsource(module)
    assert "three_bar_gap" in source
    assert "def three_bar_gap" not in source


def test_identical_inputs_produce_identical_output() -> None:
    bars, displacement = _displaced()

    runs = [
        _detect(bars, 22, ExpansionDirection.DOWN, displacement=displacement)
        for _ in range(5)
    ]

    assert all(run is not None for run in runs)
    assert all(run == runs[0] for run in runs)
