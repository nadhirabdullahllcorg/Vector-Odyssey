"""vo.valco.lrx_bos -- did the shift become a trend?

Covers: continuation in both directions, the rule that a BOS may not
re-count the MSS's own swing, one-break-per-level across a run of bars,
sequence numbering, eligibility and availability, unusable ATR, and the
separation from MSS that makes the two independently measurable."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.swings import SwingLevel, SwingStatus, SwingType
from vo.valco.lrx_bos import BosConfig, detect_bos, detect_bos_run
from vo.valco.lrx_mss import ConfirmationMethod, MssDirection, MssEvent, SwingSelection
from vo.valco.lrx_swings import CanonicalSwing

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01


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
    swing_price: float = 19_975.0,
    break_index: int = 22,
    direction: MssDirection = MssDirection.BEARISH,
    swing_id: str = "mss-swing",
) -> MssEvent:
    return MssEvent(
        mss_id="MSS:test",
        sweep_id="SWEEP:test",
        displacement_id="DISP:test",
        direction=direction,
        swing_id=swing_id,
        swing_price=swing_price,
        swing_occurred_at=_at(16),
        swing_confirmed_at=_at(18),
        swing_selection=SwingSelection.MOST_RECENT,
        alternate_swing_id=None,
        distance_from_displacement_origin=75.0,
        distance_from_sweep=75.0,
        break_index=break_index,
        break_price=19_965.0,
        break_at_utc=_at(break_index),
        break_distance=10.0,
        break_distance_atr=1.0,
        confirmation_method=ConfirmationMethod.CLOSE,
    )


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


# ── continuation, both directions ─────────────────────────────────────────


def test_a_lower_low_after_a_bearish_shift_is_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    bos = _detect(bars, _mss(), swings)

    assert bos is not None
    assert bos.direction is MssDirection.BEARISH
    assert bos.swing_id == "low-deeper"
    assert bos.sequence == 1
    assert bos.bars_since_mss == 1
    assert bos.distance_from_mss_swing == pytest.approx(25.0)
    assert bos.mss_id == "MSS:test"
    assert bos.sweep_id == "SWEEP:test"


def test_a_higher_high_after_a_bullish_shift_is_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=20_040.0, high=20_075.0, low=20_038.0, close=20_070.0))
    mss = _mss(swing_price=20_025.0, direction=MssDirection.BULLISH)
    swings = [_swing("high-higher", SwingType.HIGH, 20_050.0, confirmed_minute=14)]

    bos = _detect(bars, mss, swings)

    assert bos is not None
    assert bos.direction is MssDirection.BULLISH
    assert bos.swing_id == "high-higher"


def test_price_holding_above_the_next_low_is_not_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_955.0, close=19_958.0))
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), swings) is None


# ── the rule that keeps BOS from re-counting the MSS ──────────────────────


def test_the_swing_the_mss_broke_cannot_be_broken_again_as_a_bos() -> None:
    """Otherwise every bar that stayed below the shift's own level would
    log a fresh continuation, and the BOS count would measure elapsed
    bars rather than structure."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("mss-swing", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    assert _detect(bars, _mss(), swings) is None


def test_a_swing_short_of_the_mss_level_is_not_a_continuation() -> None:
    """A low ABOVE the one the shift broke is behind price, not ahead of
    it -- breaking it carries structure nowhere new."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("low-shallow", SwingType.LOW, 19_990.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), swings) is None
    # ...and the looser definition exists only so that can be measured.
    loose = _detect(bars, _mss(), swings, require_beyond_mss_swing=False)
    assert loose is not None


def test_the_nearest_untaken_level_is_taken_first() -> None:
    """Breaking straight to the furthest level would skip the ones
    crossed on the way and overstate each break's reach."""
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_900.0, close=19_905.0))
    swings = [
        _swing("low-near", SwingType.LOW, 19_950.0, confirmed_minute=14),
        _swing("low-far", SwingType.LOW, 19_920.0, confirmed_minute=12),
    ]

    bos = _detect(bars, _mss(), swings)

    assert bos is not None
    assert bos.swing_id == "low-near"


# ── one break per level ───────────────────────────────────────────────────


def test_a_consumed_level_is_not_broken_twice() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), swings) is not None
    assert (
        detect_bos(
            bars,
            23,
            _mss(),
            swings,
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
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    events = detect_bos_run(bars, _mss(), swings, BosConfig(), tick_size=_TICK)

    assert len(events) == 1
    assert events[0].break_index == 23


def test_successive_levels_are_numbered_in_sequence() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_940.0, close=19_945.0))
    bars.append(_bar(24, open_=19_945.0, high=19_947.0, low=19_910.0, close=19_915.0))
    swings = [
        _swing("low-near", SwingType.LOW, 19_950.0, confirmed_minute=14),
        _swing("low-far", SwingType.LOW, 19_920.0, confirmed_minute=12),
    ]

    events = detect_bos_run(bars, _mss(), swings, BosConfig(), tick_size=_TICK)

    assert [e.swing_id for e in events] == ["low-near", "low-far"]
    assert [e.sequence for e in events] == [1, 2]
    assert [e.bars_since_mss for e in events] == [1, 2]
    assert events[0].bos_id != events[1].bos_id


# ── eligibility, availability, bounds ─────────────────────────────────────


def test_a_swing_confirmed_after_the_shift_is_not_eligible() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    late = [_swing("low-late", SwingType.LOW, 19_950.0, confirmed_minute=23)]

    assert _detect(bars, _mss(break_index=22), late) is None


def test_a_broken_swing_is_not_eligible() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [
        _swing(
            "low-gone",
            SwingType.LOW,
            19_950.0,
            confirmed_minute=14,
            status=SwingStatus.BROKEN,
        )
    ]

    assert _detect(bars, _mss(), swings) is None


def test_a_swing_of_the_opposing_type_is_not_a_continuation() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    highs = [_swing("high-a", SwingType.HIGH, 19_950.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), highs) is None


def test_a_continuation_cannot_predate_the_shift() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), swings, index=22) is None
    assert _detect(bars, _mss(), swings, index=10) is None


def test_no_eligible_swing_produces_nothing() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))

    assert _detect(bars, _mss(), []) is None


# ── confirmation methods are shared with MSS, not restated ────────────────


def test_bos_and_mss_agree_on_what_broke_means() -> None:
    """Both import one predicate. If BOS ever grew its own, the two
    detectors could disagree about the same bar and no report would
    show it."""
    import inspect

    import vo.valco.lrx_bos as bos_module

    source = inspect.getsource(bos_module)
    assert "broke_level" in source
    assert "def broke_level" not in source


def test_wick_confirms_where_close_does_not() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_945.0, close=19_955.0))
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    assert _detect(bars, _mss(), swings, method=ConfirmationMethod.CLOSE) is None
    assert _detect(bars, _mss(), swings, method=ConfirmationMethod.WICK) is not None


def test_an_atr_threshold_with_no_atr_available_declines_rather_than_guesses() -> None:
    bars = _baseline(23)
    bars.append(_bar(23, open_=19_960.0, high=19_962.0, low=19_930.0, close=19_935.0))
    swings = [_swing("low-deeper", SwingType.LOW, 19_950.0, confirmed_minute=14)]

    config = BosConfig(
        method=ConfirmationMethod.CLOSE_PLUS_ATR, min_break_atr=0.5, atr_period=500
    )
    assert detect_bos(bars, 23, _mss(), swings, config, tick_size=_TICK) is None
    assert _detect(bars, _mss(), swings) is not None
