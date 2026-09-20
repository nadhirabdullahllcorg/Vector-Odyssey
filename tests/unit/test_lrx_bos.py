"""vo.valco.lrx_bos -- did the shift become a trend?

Audit case list, mirroring MSS: continuation in both directions, the
rule that a BOS may not re-count the shift's own swing, one break per
level across a run of bars, sequence numbering, both structure cutoffs,
eligibility and availability, all five confirmation modes, the boundary
cases where a break lands exactly on a level or a threshold, gaps,
rejection attribution, determinism, and the shared break predicate that
keeps BOS and MSS from drifting apart."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.swings import SwingLevel, SwingStatus, SwingType
from vo.valco.lrx_bos import (
    BosConfig,
    BosQualification,
    BosRejectionReason,
    StructureCutoff,
    detect_bos,
    detect_bos_run,
    eligible_swings,
    evaluate_bos,
)
from vo.valco.lrx_mss import (
    ConfirmationMethod,
    MssDirection,
    MssEvent,
    MssQualification,
    SwingSelection,
)
from vo.valco.lrx_swings import CanonicalSwing

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_MSS_SWING = 19_975.0
_NEXT_LOW = 19_950.0


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


def _swing(
    name: str,
    swing_type: SwingType,
    price: float,
    *,
    confirmed_minute: int,
    status: SwingStatus = SwingStatus.CONFIRMED,
) -> CanonicalSwing:
    return CanonicalSwing(
        swing_id=name,
        swing_type=swing_type,
        level=SwingLevel.SWING,
        price=price,
        body_price=price,
        occurred_at=_at(max(confirmed_minute - 2, 0)),
        available_at=_at(confirmed_minute),
        status=status,
    )


def _mss(
    *,
    broken_price: float = _MSS_SWING,
    break_index: int = 22,
    direction: MssDirection = MssDirection.BEARISH,
    swing_id: str = "mss-swing",
) -> MssEvent:
    return MssEvent(
        mss_id="MSS:test",
        sweep_id="SWEEP:test",
        displacement_id="DISP:test",
        direction=direction,
        qualification=MssQualification.PASS,
        broken_swing_id=swing_id,
        broken_price=broken_price,
        swing_event_time=_at(16),
        swing_confirmation_time=_at(18),
        selection_method=SwingSelection.MOST_RECENT,
        alternate_swing_id=None,
        distance_from_displacement_origin=75.0,
        distance_from_sweep=75.0,
        break_index=break_index,
        break_price=19_965.0,
        break_time=_at(break_index),
        break_distance_points=10.0,
        break_distance_atr=1.0,
        confirmation_method=ConfirmationMethod.CANDLE_CLOSE,
    )


def _next_low(price: float = _NEXT_LOW, minute: int = 14) -> list[CanonicalSwing]:
    return [_swing("low-deeper", SwingType.LOW, price, confirmed_minute=minute)]


def _detect(bars, mss, swings, index=None, **overrides):
    config = BosConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return detect_bos(
        bars,
        len(bars) - 1 if index is None else index,
        mss,
        swings,
        config,
        tick_size=_TICK,
    )


def _verdict(bars, mss, swings, index=None, **overrides):
    config = BosConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return evaluate_bos(
        bars,
        len(bars) - 1 if index is None else index,
        mss,
        swings,
        config,
        tick_size=_TICK,
    )


# ── continuation, both directions ─────────────────────────────────────────


def test_a_lower_low_after_a_bearish_shift_is_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    bos = _detect(bars, _mss(), _next_low())

    assert bos is not None
    assert bos.direction is MssDirection.BEARISH
    assert bos.qualification is BosQualification.PASS
    assert bos.broken_swing_id == "low-deeper"
    assert bos.broken_price == _NEXT_LOW
    assert bos.sequence == 1
    assert bos.bars_since_mss == 1
    assert bos.distance_from_mss_swing == pytest.approx(25.0)
    assert bos.mss_id == "MSS:test"
    assert bos.sweep_id == "SWEEP:test"
    assert bos.displacement_id == "DISP:test"


def test_a_higher_high_after_a_bullish_shift_is_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=20_040.0, high=20_075.0, low=20_038.0, close=20_070.0))
    mss = _mss(broken_price=20_025.0, direction=MssDirection.BULLISH)
    swings = [_swing("high-higher", SwingType.HIGH, 20_050.0, confirmed_minute=14)]

    bos = _detect(bars, mss, swings)

    assert bos is not None
    assert bos.direction is MssDirection.BULLISH
    assert bos.broken_swing_id == "high-higher"


def test_direction_is_inherited_from_the_shift_not_re_derived() -> None:
    """A second opinion on direction could disagree with the shift it
    claims to continue. BOS has no direction logic of its own."""
    import ast
    import inspect

    import vo.valco.lrx_bos as module

    source = inspect.getsource(module)
    assert "expected_direction" not in source
    assert "LevelSide" not in source

    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for banned in (
        "vo.observation.hurst",
        "vo.observation.regime",
        "vo.compliance.news_gate",
    ):
        assert banned not in imported, banned


def test_price_holding_above_the_next_low_is_not_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_955.0, close=19_958.0))

    verdict = _verdict(bars, _mss(), _next_low())
    assert verdict.rejection_reason is BosRejectionReason.NOT_BROKEN
    assert verdict.candidate_swing_id == "low-deeper"
    assert verdict.event is None


# ── the rule that keeps BOS from re-counting the MSS ──────────────────────


def test_the_swing_the_mss_broke_cannot_be_broken_again_as_a_bos() -> None:
    """Otherwise every bar that stayed below the shift's own level would
    log a fresh continuation, and the count would measure elapsed bars."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("mss-swing", SwingType.LOW, _MSS_SWING, confirmed_minute=18)]

    verdict = _verdict(bars, _mss(), swings)
    assert verdict.rejection_reason is BosRejectionReason.NO_ELIGIBLE_SWING


def test_a_swing_short_of_the_mss_level_is_not_a_continuation() -> None:
    """A low ABOVE the one the shift broke is behind price, not ahead of
    it -- breaking it carries structure nowhere new."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    shallow = [_swing("low-shallow", SwingType.LOW, 19_990.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), shallow) is None
    # ...and the looser definition exists only so that can be measured.
    assert _detect(bars, _mss(), shallow, require_beyond_mss_swing=False) is not None


def test_the_nearest_untaken_level_is_taken_first() -> None:
    """Breaking straight to the furthest level would skip the ones
    crossed on the way and overstate each break's reach."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_900.0, close=19_905.0))
    swings = [
        _swing("low-near", SwingType.LOW, _NEXT_LOW, confirmed_minute=14),
        _swing("low-far", SwingType.LOW, 19_920.0, confirmed_minute=12),
    ]

    bos = _detect(bars, _mss(), swings)

    assert bos is not None
    assert bos.broken_swing_id == "low-near"


# ── structure cutoff: the one real ambiguity ──────────────────────────────


def test_at_mss_refuses_structure_the_move_itself_created() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    mid_move = _next_low(minute=23)

    verdict = _verdict(bars, _mss(), mid_move, cutoff=StructureCutoff.AT_MSS)
    assert verdict.rejection_reason is BosRejectionReason.NO_ELIGIBLE_SWING


def test_at_break_bar_admits_it_and_records_which_rule_applied() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    mid_move = _next_low(minute=23)

    bos = _detect(bars, _mss(), mid_move, cutoff=StructureCutoff.AT_BREAK_BAR)

    assert bos is not None
    assert bos.structure_cutoff is StructureCutoff.AT_BREAK_BAR


def test_the_default_cutoff_is_the_strict_one() -> None:
    assert BosConfig().cutoff is StructureCutoff.AT_MSS


def test_neither_cutoff_admits_a_swing_confirmed_after_the_break_bar() -> None:
    """The looser rule is looser, not absent. A swing confirmed at bar
    24 is not knowable when bar 23 closes, under either setting."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    future = _next_low(minute=24)

    for cutoff in StructureCutoff:
        assert _detect(bars, _mss(), future, index=23, cutoff=cutoff) is None, cutoff


def test_a_broken_swing_is_not_eligible() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    gone = [
        _swing(
            "low-gone",
            SwingType.LOW,
            _NEXT_LOW,
            confirmed_minute=14,
            status=SwingStatus.BROKEN,
        )
    ]

    assert _detect(bars, _mss(), gone) is None


def test_a_swing_of_the_opposing_type_is_not_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    highs = [_swing("high-a", SwingType.HIGH, _NEXT_LOW, confirmed_minute=14)]

    assert _detect(bars, _mss(), highs) is None


def test_eligibility_is_inspectable_on_its_own() -> None:
    swings = [
        _swing("low-deeper", SwingType.LOW, _NEXT_LOW, confirmed_minute=14),
        _swing("low-shallow", SwingType.LOW, 19_990.0, confirmed_minute=14),
        _swing("mss-swing", SwingType.LOW, _MSS_SWING, confirmed_minute=18),
    ]

    strict = eligible_swings(
        swings, _mss(), cutoff_at=_at(22), require_beyond=True
    )
    loose = eligible_swings(
        swings, _mss(), cutoff_at=_at(22), require_beyond=False
    )

    assert [s.swing_id for s in strict] == ["low-deeper"]
    assert sorted(s.swing_id for s in loose) == ["low-deeper", "low-shallow"]


# ── one break per level ───────────────────────────────────────────────────


def test_a_consumed_level_is_not_broken_twice() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    assert _detect(bars, _mss(), _next_low()) is not None
    assert (
        detect_bos(
            bars,
            23,
            _mss(),
            _next_low(),
            BosConfig(),
            tick_size=_TICK,
            already_broken=["low-deeper"],
        )
        is None
    )


def test_a_run_of_bars_below_one_level_logs_one_continuation() -> None:
    bars = _baseline(23)
    for minute, low, close in (
        (23, 19_930.0, 19_935.0),
        (24, 19_928.0, 19_932.0),
        (25, 19_925.0, 19_931.0),
    ):
        bars.append(_bar(minute, open_=19_940.0, high=19_942.0, low=low, close=close))

    events = detect_bos_run(bars, _mss(), _next_low(), BosConfig(), tick_size=_TICK)

    assert len(events) == 1
    assert events[0].break_index == 23


def test_successive_levels_are_numbered_in_sequence() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_940.0, close=19_945.0))
    bars.append(_bar(24, open_=19_945.0, high=19_947.0, low=19_910.0, close=19_915.0))
    swings = [
        _swing("low-near", SwingType.LOW, _NEXT_LOW, confirmed_minute=14),
        _swing("low-far", SwingType.LOW, 19_920.0, confirmed_minute=12),
    ]

    events = detect_bos_run(bars, _mss(), swings, BosConfig(), tick_size=_TICK)

    assert [e.broken_swing_id for e in events] == ["low-near", "low-far"]
    assert [e.sequence for e in events] == [1, 2]
    assert [e.bars_since_mss for e in events] == [1, 2]
    assert len({e.bos_id for e in events}) == 2


def test_a_run_stops_at_until_index() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_940.0, close=19_945.0))
    bars.append(_bar(24, open_=19_945.0, high=19_947.0, low=19_910.0, close=19_915.0))
    swings = [
        _swing("low-near", SwingType.LOW, _NEXT_LOW, confirmed_minute=14),
        _swing("low-far", SwingType.LOW, 19_920.0, confirmed_minute=12),
    ]

    events = detect_bos_run(
        bars, _mss(), swings, BosConfig(), tick_size=_TICK, until_index=23
    )

    assert [e.broken_swing_id for e in events] == ["low-near"]


# ── confirmation modes, shared with MSS ───────────────────────────────────


def test_bos_and_mss_agree_on_what_broke_means() -> None:
    """Both import one predicate. If BOS ever grew its own, the two
    detectors could disagree about the same bar and no report would
    show it."""
    import inspect

    import vo.valco.lrx_bos as module

    source = inspect.getsource(module)
    assert "broke_level" in source
    assert "def broke_level" not in source


def test_wick_break_confirms_where_candle_close_does_not() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_945.0, close=19_955.0))

    assert _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.CANDLE_CLOSE) is None
    wick = _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.WICK_BREAK)
    assert wick is not None
    assert wick.break_price == 19_945.0


def test_body_close_rejects_a_bar_that_opened_above_the_level() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    assert _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.CANDLE_CLOSE) is not None
    assert _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.BODY_CLOSE) is None


def test_close_plus_min_distance_needs_the_extra_points() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_940.0, close=19_945.0))
    method = ConfirmationMethod.CLOSE_PLUS_MIN_DISTANCE

    assert _detect(bars, _mss(), _next_low(), method=method, min_break_points=3.0) is not None
    assert _detect(bars, _mss(), _next_low(), method=method, min_break_points=8.0) is None


def test_every_confirmation_mode_is_reachable_and_recorded() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_930.0, high=19_932.0, low=19_900.0, close=19_905.0))

    for method in ConfirmationMethod:
        bos = _detect(
            bars,
            _mss(),
            _next_low(),
            method=method,
            min_break_points=1.0,
            min_break_atr=0.1,
        )
        assert bos is not None, method
        assert bos.confirmation_method is method


def test_an_atr_threshold_with_no_atr_declines_rather_than_guessing() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    verdict = _verdict(
        bars,
        _mss(),
        _next_low(),
        method=ConfirmationMethod.CLOSE_PLUS_ATR_THRESHOLD,
        min_break_atr=0.5,
        atr_period=500,
    )
    assert verdict.rejection_reason is BosRejectionReason.ATR_UNAVAILABLE
    assert verdict.candidate_swing_id == "low-deeper"


def test_an_unmeasurable_atr_distance_is_reported_as_none_not_zero() -> None:
    """A reported 0.0 would be indistinguishable from a break that
    genuinely covered no ATR, and would drag any average computed over
    it toward zero while looking like data."""
    bars = _baseline(3)
    bars.append(_bar(3, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    mss = _mss(break_index=2)

    bos = _detect(bars, mss, _next_low(minute=1), atr_period=500)

    assert bos is not None
    assert bos.break_distance_atr is None
    assert bos.break_distance_points > 0.0


# ── boundaries: touch, one tick, exact threshold, gap ─────────────────────


def test_an_exact_touch_of_the_level_is_not_a_break() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=_NEXT_LOW, close=19_958.0))

    assert _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.WICK_BREAK) is None


def test_one_tick_beyond_the_level_is_a_break() -> None:
    bars = _baseline(23)
    bars.append(
        _bar(23, open_=19_960.0, high=19_962.0, low=_NEXT_LOW - _TICK, close=19_958.0)
    )

    bos = _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.WICK_BREAK)
    assert bos is not None
    assert bos.break_distance_points == pytest.approx(_TICK)


def test_a_close_exactly_at_the_level_is_not_a_break() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_940.0, close=_NEXT_LOW))

    assert _detect(bars, _mss(), _next_low(), method=ConfirmationMethod.CANDLE_CLOSE) is None


def test_a_close_exactly_at_the_threshold_is_not_a_break() -> None:
    bars = _baseline(23)
    bars.append(
        _bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=_NEXT_LOW - 5.0)
    )
    method = ConfirmationMethod.CLOSE_PLUS_MIN_DISTANCE

    assert _detect(bars, _mss(), _next_low(), method=method, min_break_points=5.0) is None


def test_a_bar_gapping_wholly_through_the_level_is_a_break() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_920.0, high=19_925.0, low=19_900.0, close=19_905.0))

    for method in (
        ConfirmationMethod.WICK_BREAK,
        ConfirmationMethod.CANDLE_CLOSE,
        ConfirmationMethod.BODY_CLOSE,
    ):
        assert _detect(bars, _mss(), _next_low(), method=method) is not None, method


# ── bounds and determinism ────────────────────────────────────────────────


def test_a_continuation_cannot_predate_the_shift() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    for index in (22, 10):
        verdict = _verdict(bars, _mss(), _next_low(), index=index)
        assert verdict.rejection_reason is BosRejectionReason.BEFORE_SHIFT


def test_an_index_past_the_series_is_refused() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    verdict = _verdict(bars, _mss(), _next_low(), index=len(bars) + 5)
    assert verdict.rejection_reason is BosRejectionReason.BEFORE_SHIFT


def test_no_eligible_swing_produces_nothing() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    verdict = _verdict(bars, _mss(), [])
    assert verdict.rejection_reason is BosRejectionReason.NO_ELIGIBLE_SWING
    assert verdict.candidate_swing_id is None


def test_the_event_is_knowable_on_the_bar_that_produced_it() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    bos = _detect(bars, _mss(), _next_low())

    assert bos is not None
    assert bos.confirmation_at_utc == bos.event_at_utc == bos.break_time == _at(23)


def test_identical_inputs_produce_identical_output() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_900.0, close=19_905.0))
    swings = [
        _swing("low-near", SwingType.LOW, _NEXT_LOW, confirmed_minute=14),
        _swing("low-far", SwingType.LOW, 19_920.0, confirmed_minute=12),
    ]

    runs = [_detect(bars, _mss(), swings) for _ in range(5)]
    assert all(run is not None for run in runs)
    assert all(run == runs[0] for run in runs)

    sequences = [
        detect_bos_run(bars, _mss(), swings, BosConfig(), tick_size=_TICK)
        for _ in range(5)
    ]
    assert all(seq == sequences[0] for seq in sequences)


def test_rejections_are_deterministic_too() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_955.0, close=19_958.0))

    runs = [_verdict(bars, _mss(), _next_low()) for _ in range(5)]
    assert all(run == runs[0] for run in runs)
