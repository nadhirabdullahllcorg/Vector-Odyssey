"""The timestamp convention, pinned across the whole LRX event chain.

Every event timestamp in VO is a bar LABEL -- the open time of the bar
whose CLOSE made the fact knowable. That is a convention, not an
accident, and it is only safe because it is applied uniformly: both
sides of any comparison mean "knowable at the close of the bar with this
label", so ordering labels orders availability.

These tests exist because a docstring once claimed one of these fields
was a literal close instant while the value was always the label -- one
bar apart, in the direction that would have made a lookahead look
legal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.swings import SwingLevel, SwingStatus, SwingType
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    ExpansionDirection,
    measure_displacement,
)
from vo.valco.lrx_inefficiency import InefficiencyConfig, detect_inefficiency
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_mss import MssConfig, detect_mss
from vo.valco.lrx_sweep import SweepEvent
from vo.valco.lrx_swings import CanonicalSwing

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_LEVEL = 20_050.0


def _at(minute: int) -> datetime:
    return _START + timedelta(minutes=minute)


def _bar(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    timeframe: Timeframe = Timeframe.M1,
) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=timeframe,
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


def _sweep(returned_index: int) -> SweepEvent:
    return SweepEvent(
        sweep_id=f"SWEEP:test:{returned_index}",
        level=ReferenceLevel(
            kind=LevelKind.PREV_DAY_HIGH,
            price=_LEVEL,
            established_at=None,
            trading_day=_START.date(),
        ),
        side=LevelSide.BUY_SIDE,
        penetration_index=returned_index - 1,
        penetration_price=_LEVEL + 8.0,
        penetration_distance=8.0,
        penetration_atr_multiple=0.8,
        closed_beyond=False,
        returned_index=returned_index,
        returned_at_utc=_at(returned_index),
        bars_beyond=1,
    )


def _bearish() -> tuple[list[Bar], object, SweepEvent]:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))
    sweep = _sweep(20)
    event = measure_displacement(bars, 21, sweep, DisplacementConfig(), tick_size=_TICK)
    assert event is not None
    return bars, event, sweep


# ── the data model's escape hatch ─────────────────────────────────────────


def test_a_bar_knows_when_its_information_became_final() -> None:
    bar = _bar(10, open_=20_000.0, high=20_005.0, low=19_995.0, close=20_002.0)

    assert bar.open_time_utc == _at(10)
    assert bar.close_time_utc == _at(11)
    assert bar.close_time_utc != bar.open_time_utc


def test_close_time_is_none_on_a_timeframe_with_no_fixed_length() -> None:
    """A month has no constant duration. Guessing one would be the same
    class of error as every sentinel this project has removed."""
    monthly = _bar(
        0, open_=1.0, high=2.0, low=0.5, close=1.5, timeframe=Timeframe.MN1
    )

    assert Timeframe.MN1.seconds is None
    assert monthly.close_time_utc is None


def test_close_time_matches_the_timeframe() -> None:
    for timeframe, minutes in (
        (Timeframe.M1, 1),
        (Timeframe.M5, 5),
        (Timeframe.M15, 15),
        (Timeframe.H1, 60),
    ):
        bar = _bar(
            0, open_=1.0, high=2.0, low=0.5, close=1.5, timeframe=timeframe
        )
        assert bar.close_time_utc == _at(minutes), timeframe


# ── every event timestamp is a label, not a close ─────────────────────────


def test_displacement_timestamps_are_bar_labels() -> None:
    bars, event, _sweep_unused = _bearish()
    first, last = bars[event.start_index], bars[event.end_index]

    assert event.start_at_utc == first.open_time_utc
    assert event.end_at_utc == last.open_time_utc
    assert event.event_at_utc == first.open_time_utc
    assert event.confirmation_at_utc == last.open_time_utc

    # The docstring once claimed this WAS the close. It is one bar
    # earlier than that, and the real instant is reachable explicitly.
    assert event.confirmation_at_utc != last.close_time_utc
    assert last.close_time_utc is not None
    assert event.confirmation_at_utc < last.close_time_utc


def test_an_mss_break_time_is_the_breaking_bars_label() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [
        CanonicalSwing(
            swing_id="low-a",
            swing_type=SwingType.LOW,
            level=SwingLevel.SWING,
            price=19_975.0,
            body_price=19_975.0,
            occurred_at=_at(16),
            available_at=_at(18),
            status=SwingStatus.CONFIRMED,
        )
    ]

    mss = detect_mss(bars, 22, event, sweep, swings, MssConfig(), tick_size=_TICK)

    assert mss is not None
    assert mss.break_time == bars[22].open_time_utc
    assert mss.confirmation_at_utc == mss.break_time


def test_an_fvg_creation_time_is_the_third_candles_label() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_995.0, high=20_000.0, low=19_980.0, close=19_985.0))
    bars.append(_bar(21, open_=19_985.0, high=19_986.0, low=19_965.0, close=19_968.0))
    bars.append(_bar(22, open_=19_968.0, high=19_970.0, low=19_950.0, close=19_955.0))

    fvg = detect_inefficiency(
        bars, 22, ExpansionDirection.DOWN, InefficiencyConfig(), tick_size=_TICK
    )

    assert fvg is not None
    assert fvg.creation_time == bars[22].open_time_utc


def test_an_fvg_is_not_information_available_at_its_own_creation_label() -> None:
    """The distinction the whole convention rests on. The gap needs
    candle 3's completed high, so it is knowable at candle 3's CLOSE --
    strictly after the label it carries. Downstream logic that treated
    creation_time as the moment of availability would act one bar early."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_995.0, high=20_000.0, low=19_980.0, close=19_985.0))
    bars.append(_bar(21, open_=19_985.0, high=19_986.0, low=19_965.0, close=19_968.0))
    bars.append(_bar(22, open_=19_968.0, high=19_970.0, low=19_950.0, close=19_955.0))

    fvg = detect_inefficiency(
        bars, 22, ExpansionDirection.DOWN, InefficiencyConfig(), tick_size=_TICK
    )
    third = bars[22]

    assert fvg is not None
    assert third.close_time_utc is not None
    assert fvg.creation_time < third.close_time_utc
    # And the detector cannot produce it at all before that bar exists.
    assert (
        detect_inefficiency(
            bars[:22], 22, ExpansionDirection.DOWN, InefficiencyConfig(),
            tick_size=_TICK,
        )
        is None
    )


# ── the comparisons the convention makes safe ─────────────────────────────


def test_label_comparison_orders_availability_correctly() -> None:
    """Both sides of every causal comparison in the chain mean "knowable
    at the close of the bar with this label", so comparing labels is
    comparing availability. This is what makes the convention safe, and
    it is the reason a piecemeal migration to real close times would be
    dangerous rather than merely verbose."""
    bars = _baseline(5)

    for earlier, later in pairwise(bars):
        assert earlier.open_time_utc < later.open_time_utc
        assert earlier.close_time_utc is not None
        assert later.close_time_utc is not None
        assert earlier.close_time_utc < later.close_time_utc
        # Ordering is identical whichever representation is used, which
        # is precisely why mixing them in one comparison is the hazard.
        assert (earlier.open_time_utc < later.open_time_utc) == (
            earlier.close_time_utc < later.close_time_utc
        )


def test_a_swing_confirmed_by_the_breaking_bar_is_available_to_that_break() -> None:
    """Same-label availability is NOT lookahead: the swing's confirmation
    and the break both become knowable at the same bar's close."""
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    same_bar = CanonicalSwing(
        swing_id="low-same-bar",
        swing_type=SwingType.LOW,
        level=SwingLevel.SWING,
        price=19_975.0,
        body_price=19_975.0,
        occurred_at=_at(16),
        available_at=event.start_at_utc,
        status=SwingStatus.CONFIRMED,
    )

    mss = detect_mss(
        bars, 22, event, sweep, [same_bar], MssConfig(), tick_size=_TICK
    )

    assert mss is not None
    assert mss.swing_confirmation_time == event.start_at_utc
