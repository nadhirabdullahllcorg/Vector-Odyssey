"""
MSS -- Market Structure Shift. The first meaningful structural break in
the direction opposing the liquidity raid.

    Raid -> Displacement -> [MSS] -> Inefficiency -> Rebalance -> Objective

CONSUMES CONFIRMED SWINGS, NEVER FINDS ITS OWN. The spec is explicit and
so is the architecture: the SwingEngine is the one of record, the Swing
Adapter normalises it, and MSS asks that map a question. There is no
pivot detection in this file. The failure that rule prevents is two
systems computing structure and disagreeing about it -- at which point
neither can be trusted and the disagreement is invisible.

WHICH SWING GETS BROKEN IS THE HARD PART, and the spec warns against the
lazy answer. "Most recent" is not automatically right, and a swing that
was not yet CONFIRMED when the displacement began cannot be the one that
was broken -- the strategy could not have known about it. So selection
is: among swings of the opposing type that were AVAILABLE at the
displacement's start, take the configured one. Both candidate rules are
recorded either way.

  MOST_RECENT   the last opposing swing confirmed before the leg began.
                The usual reading: "the low that was made before the
                raid".
  NEAREST_PRICE the opposing swing price would reach first -- for a
                bearish break, the highest swing low below the origin.

These differ often enough to matter, which is why choosing silently
would be wrong.

CONFIRMATION METHOD IS CONFIGURABLE AND SINGULAR. Five definitions of
"broke the swing" (section 15), and the spec's instruction not to mix
them inside one backtest is honoured by putting the method on the config
rather than letting a caller pass a different one per call. A result
that mixed wick breaks and close breaks would be unattributable.

MSS IS NOT BOS. This module answers "did structure shift after the
raid". Whether the new direction then continues breaking structure is
the BOS detector's question, kept separate so the transition and the
continuation stay independently measurable.

NO LOOKAHEAD, TWICE OVER: the break is evaluated on bars[<= index], and
the swing it breaks must have been confirmed before the displacement
began.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.observation.swings import SwingType
from vo.valco.lrx_displacement import DisplacementEvent, ExpansionDirection
from vo.valco.lrx_sweep import SweepEvent
from vo.valco.lrx_swings import CanonicalSwing, most_recent_opposing, visible_swings


class MssDirection(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    def __str__(self) -> str:
        return self.value


class ConfirmationMethod(Enum):
    """The five definitions of "broke the swing" (spec section 15). One
    per backtest -- mixing them makes a result unattributable."""

    WICK = "WICK"
    """Price traded beyond the swing at all."""
    CLOSE = "CLOSE"
    """A bar closed beyond it."""
    BODY_CLOSE = "BODY_CLOSE"
    """The bar's body -- both open and close -- finished beyond it, so a
    bar that opened beyond and closed back does not count."""
    CLOSE_PLUS_POINTS = "CLOSE_PLUS_POINTS"
    CLOSE_PLUS_ATR = "CLOSE_PLUS_ATR"

    def __str__(self) -> str:
        return self.value


class SwingSelection(Enum):
    """Which opposing swing the shift must break (spec section 14)."""

    MOST_RECENT = "MOST_RECENT"
    NEAREST_PRICE = "NEAREST_PRICE"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MssConfig:
    method: ConfirmationMethod = ConfirmationMethod.CLOSE
    selection: SwingSelection = SwingSelection.MOST_RECENT
    min_break_points: float = 0.0
    min_break_atr: float = 0.0
    atr_period: int = 14


@dataclass(frozen=True, slots=True)
class MssEvent:
    """One confirmed structural shift, with everything section 14 asks to
    be recorded."""

    mss_id: str
    sweep_id: str
    displacement_id: str
    direction: MssDirection

    swing_id: str
    swing_price: float
    swing_occurred_at: datetime
    swing_confirmed_at: datetime
    swing_selection: SwingSelection
    alternate_swing_id: str | None
    """What the OTHER selection rule would have chosen, when it differs.
    Recorded so the choice can be studied rather than trusted."""

    distance_from_displacement_origin: float
    distance_from_sweep: float
    """Between the broken swing and the RAIDED LEVEL's price -- not the
    displacement's own reach, which the displacement event already
    carries under the same name."""

    break_index: int
    break_price: float
    break_at_utc: datetime
    break_distance: float
    break_distance_atr: float
    confirmation_method: ConfirmationMethod

    @property
    def event_at_utc(self) -> datetime:
        return self.break_at_utc

    @property
    def confirmation_at_utc(self) -> datetime:
        """A break is knowable on the bar that produced it -- unlike a
        swing, there is no later confirmation to wait for."""
        return self.break_at_utc


def broke_level(
    method: ConfirmationMethod,
    bar: Bar,
    swing_price: float,
    direction: MssDirection,
    *,
    min_points: float,
    min_atr_points: float,
) -> bool:
    """Did `bar` break `swing_price` in `direction`, under `method`?

    Public because the BOS detector shares it. The two detectors mean
    opposite things but must agree on what "broke" is -- one definition,
    one place, so they cannot drift apart."""
    bearish = direction is MssDirection.BEARISH

    if method is ConfirmationMethod.WICK:
        return bar.low < swing_price if bearish else bar.high > swing_price

    if method is ConfirmationMethod.CLOSE:
        return bar.close < swing_price if bearish else bar.close > swing_price

    if method is ConfirmationMethod.BODY_CLOSE:
        if bearish:
            return bar.close < swing_price and bar.open < swing_price
        return bar.close > swing_price and bar.open > swing_price

    threshold = (
        min_points
        if method is ConfirmationMethod.CLOSE_PLUS_POINTS
        else min_atr_points
    )
    if bearish:
        return bar.close < swing_price - threshold
    return bar.close > swing_price + threshold


def select_swing(
    swings: Sequence[CanonicalSwing],
    *,
    displacement: DisplacementEvent,
    selection: SwingSelection,
) -> tuple[CanonicalSwing | None, CanonicalSwing | None]:
    """The swing an MSS must break, and what the other rule would have
    picked.

    Availability is measured at the DISPLACEMENT'S START, not the current
    bar: a swing confirmed while the leg was already running was not
    known when the setup began, so breaking it is not the structural
    shift the model describes.
    """
    wanted = (
        SwingType.LOW
        if displacement.direction is ExpansionDirection.DOWN
        else SwingType.HIGH
    )
    at = displacement.event_at_utc
    available = [
        swing for swing in visible_swings(swings, at=at) if swing.swing_type is wanted
    ]
    if not available:
        return None, None

    most_recent = most_recent_opposing(
        swings, at=at, raid_was_buy_side=displacement.direction is ExpansionDirection.DOWN
    )

    origin = displacement.expansion_origin
    if displacement.direction is ExpansionDirection.DOWN:
        below = [s for s in available if s.price < origin]
        nearest = max(below, key=lambda s: s.price) if below else None
    else:
        above = [s for s in available if s.price > origin]
        nearest = min(above, key=lambda s: s.price) if above else None

    chosen = most_recent if selection is SwingSelection.MOST_RECENT else nearest
    other = nearest if selection is SwingSelection.MOST_RECENT else most_recent
    if chosen is None:
        return None, None

    alternate = other if other is not None and other.swing_id != chosen.swing_id else None
    return chosen, alternate


def detect_mss(
    bars: Sequence[Bar],
    index: int,
    displacement: DisplacementEvent,
    sweep: SweepEvent,
    swings: Sequence[CanonicalSwing],
    config: MssConfig,
    *,
    tick_size: float,
) -> MssEvent | None:
    """
    Whether the displacement broke the relevant confirmed opposing swing
    at `index`.

    Returns None when no break occurred there, when no eligible swing
    exists, or when ATR is unavailable for an ATR-based method -- a
    threshold with no scale to measure against cannot be applied.
    """
    if index <= displacement.start_index or index >= len(bars):
        return None
    if sweep.sweep_id != displacement.sweep_id:
        raise ValueError(
            f"sweep {sweep.sweep_id} is not the raid behind displacement "
            f"{displacement.displacement_id} ({displacement.sweep_id}); "
            "a chain assembled from mismatched parts is not attributable"
        )

    chosen, alternate = select_swing(
        swings, displacement=displacement, selection=config.selection
    )
    if chosen is None:
        return None

    direction = (
        MssDirection.BEARISH
        if displacement.direction is ExpansionDirection.DOWN
        else MssDirection.BULLISH
    )

    min_atr_points = 0.0
    if config.method is ConfirmationMethod.CLOSE_PLUS_ATR:
        atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
        if atr is None:
            return None
        min_atr_points = atr * tick_size * config.min_break_atr

    bar = bars[index]
    if not broke_level(
        config.method,
        bar,
        chosen.price,
        direction,
        min_points=config.min_break_points,
        min_atr_points=min_atr_points,
    ):
        return None

    break_price = (
        bar.low
        if config.method is ConfirmationMethod.WICK and direction is MssDirection.BEARISH
        else bar.high
        if config.method is ConfirmationMethod.WICK
        else bar.close
    )
    break_distance = abs(break_price - chosen.price)

    atr_for_report = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
    atr_price = (atr_for_report or 0) * tick_size
    break_distance_atr = break_distance / atr_price if atr_price > 0 else 0.0

    return MssEvent(
        mss_id=f"MSS:{displacement.displacement_id}:{bar.open_time_utc.isoformat()}",
        sweep_id=displacement.sweep_id,
        displacement_id=displacement.displacement_id,
        direction=direction,
        swing_id=chosen.swing_id,
        swing_price=chosen.price,
        swing_occurred_at=chosen.occurred_at,
        swing_confirmed_at=chosen.available_at,
        swing_selection=config.selection,
        alternate_swing_id=alternate.swing_id if alternate is not None else None,
        distance_from_displacement_origin=abs(
            chosen.price - displacement.expansion_origin
        ),
        distance_from_sweep=abs(chosen.price - sweep.level.price),
        break_index=index,
        break_price=break_price,
        break_at_utc=bar.open_time_utc,
        break_distance=break_distance,
        break_distance_atr=break_distance_atr,
        confirmation_method=config.method,
    )
