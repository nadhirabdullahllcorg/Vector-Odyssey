"""
MSS -- Market Structure Shift. The structural transition that follows a
liquidity raid.

    Raid -> Displacement -> [MSS] -> Inefficiency -> Rebalance -> Objective

    BULLISH   sell-side sweep -> bullish displacement
              -> break ABOVE the relevant confirmed opposing swing HIGH
    BEARISH   buy-side sweep  -> bearish displacement
              -> break BELOW the relevant confirmed opposing swing LOW

MSS IS NOT BOS. This module answers "did structure turn after the raid".
Whether the new direction then continues breaking structure is the BOS
detector's question, kept separate so the transition and the
continuation stay independently measurable.

CONSUMES CONFIRMED SWINGS, NEVER FINDS ITS OWN. The SwingEngine is the
one of record, the Swing Adapter normalises it, and MSS asks that map a
question. There is no pivot detection in this file. The failure that
rule prevents is two systems computing structure and disagreeing about
it -- at which point neither can be trusted and the disagreement is
invisible.

DIRECTION COMES FROM THE SWEEP, NOT THE DISPLACEMENT. A sell-side raid
followed by a BEARISH leg is not a setup -- it is price continuing down
through liquidity it just took. Deriving direction from the displacement
alone would silently manufacture a shift out of that, so the two are
required to agree and a contradiction is REJECTED by name rather than
resolved.

WHICH SWING GETS BROKEN IS THE HARD PART, and the lazy answer is wrong.
"Most recent" is not automatically right, and a swing not yet CONFIRMED
when the displacement began cannot be the one that was broken -- the
strategy could not have known about it. Selection is therefore: among
swings of the opposing type AVAILABLE at the displacement's start, take
the configured one. Both candidate rules are recorded either way.

  MOST_RECENT   the last opposing swing confirmed before the leg began.
  NEAREST_PRICE the opposing swing price would reach first.

PRICE CONVENTION. Breaks are evaluated on Bar OHLC. Those bars originate
from MT5 `CopyRates`/`MqlRates` via VO_Bridge, which MetaTrader builds
from the BID series. Backtest and live consume the same Bar objects from
the same bridge, so the convention cannot diverge between them. Note the
consequence: a BUY fills at ask, so a bullish break confirmed on a
bid-based close is reached at roughly close + spread in execution. That
asymmetry belongs to execution and is deliberately NOT compensated for
here -- a detector that quietly shaded its own threshold by a spread
would make the confirmation mode untestable.

NOTHING ELSE. No HTF filter, no SMT, no Hurst, Markov or regime input,
no volume, no order blocks, no news. MSS stays a clean structural
detector; confluence belongs to whatever assembles setups, where it can
be switched off and measured. The one indicator imported is ATR, which
is intrinsic to confirmation mode 5 and to reporting break distance in
ATR terms.

NO LOOKAHEAD, TWICE OVER: the break is evaluated on bars[<= index], and
the swing it breaks must have been confirmed at or before the
displacement's start.
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
from vo.valco.lrx_levels import LevelSide
from vo.valco.lrx_sweep import SweepEvent
from vo.valco.lrx_swings import CanonicalSwing, most_recent_opposing, visible_swings


class MssDirection(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    def __str__(self) -> str:
        return self.value


class ConfirmationMethod(Enum):
    """The five definitions of "broke the swing". One per backtest --
    mixing them makes a result unattributable, which is why the method
    lives on the config rather than the call signature.

    Every one is evaluated against bid-based OHLC; see the module
    docstring on the execution asymmetry that follows from that.
    """

    WICK_BREAK = "WICK_BREAK"
    """Price traded beyond the swing at all: bar low < swing (bearish),
    bar high > swing (bullish)."""
    CANDLE_CLOSE = "CANDLE_CLOSE"
    """The bar's close finished beyond the swing."""
    BODY_CLOSE = "BODY_CLOSE"
    """The bar's whole body -- open AND close -- finished beyond it, so a
    bar that opened beyond and closed back does not count."""
    CLOSE_PLUS_MIN_DISTANCE = "CLOSE_PLUS_MIN_DISTANCE"
    """Close beyond the swing by at least `min_break_points` price
    units. Strictly greater: a close exactly at the threshold does not
    confirm."""
    CLOSE_PLUS_ATR_THRESHOLD = "CLOSE_PLUS_ATR_THRESHOLD"
    """Close beyond the swing by at least `min_break_atr` x ATR. Same
    strict comparison. Declines rather than confirming when ATR is
    unavailable."""

    def __str__(self) -> str:
        return self.value


class SwingSelection(Enum):
    """Which opposing swing the shift must break."""

    MOST_RECENT = "MOST_RECENT"
    NEAREST_PRICE = "NEAREST_PRICE"

    def __str__(self) -> str:
        return self.value


class MssQualification(Enum):
    PASS = "PASS"
    REJECTED = "REJECTED"

    def __str__(self) -> str:
        return self.value


class MssRejectionReason(Enum):
    """Why no shift was recorded. Named rather than collapsed into a
    bare None so a setup that died can be attributed later."""

    CONTRADICTORY_DIRECTION = "CONTRADICTORY_DIRECTION"
    """The raid and the expansion point the same way -- continuation
    through liquidity, not a reversal."""
    NO_ELIGIBLE_SWING = "NO_ELIGIBLE_SWING"
    """No confirmed opposing swing was available at the displacement's
    start."""
    ATR_UNAVAILABLE = "ATR_UNAVAILABLE"
    """An ATR-scaled threshold with no ATR to scale against. Declined
    rather than treated as zero."""
    NOT_BROKEN = "NOT_BROKEN"
    """A candidate swing existed; price did not break it under the
    configured method."""
    BEFORE_DISPLACEMENT = "BEFORE_DISPLACEMENT"
    """The evaluated bar is at or before the leg's start, or past the
    end of the series."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MssConfig:
    method: ConfirmationMethod = ConfirmationMethod.CANDLE_CLOSE
    selection: SwingSelection = SwingSelection.MOST_RECENT
    min_break_points: float = 0.0
    min_break_atr: float = 0.0
    atr_period: int = 14


@dataclass(frozen=True, slots=True)
class MssEvent:
    """One confirmed structural shift, carrying everything needed to
    reproduce the decision that made it.

    Only ever constructed for a PASS: `qualification` is present because
    the record should state its own standing, not because a rejected
    shift is an MssEvent. Rejections are MssVerdict rows -- see
    `evaluate_mss` -- which keeps every field here non-optional and
    means a consumer holding an MssEvent holds a real break.
    """

    mss_id: str
    sweep_id: str
    displacement_id: str
    direction: MssDirection
    qualification: MssQualification

    broken_swing_id: str
    broken_price: float
    swing_event_time: datetime
    """When the swing's pivot actually occurred."""
    swing_confirmation_time: datetime
    """When it became KNOWABLE. The lookahead cutoff is measured against
    this, never against swing_event_time."""
    selection_method: SwingSelection
    alternate_swing_id: str | None
    """What the OTHER selection rule would have chosen, when it differs.
    Recorded so the choice can be studied rather than trusted."""

    distance_from_displacement_origin: float
    distance_from_sweep: float
    """Between the broken swing and the RAIDED LEVEL's price -- not the
    displacement's own reach, which the displacement event already
    carries under a similar name."""

    break_index: int
    break_price: float
    break_time: datetime
    break_distance_points: float
    break_distance_atr: float | None
    """None when ATR is unavailable at this bar -- NOT 0.0. A reported
    zero would be indistinguishable from a break that genuinely covered
    no ATR, and would drag any average computed over it toward zero
    while looking like data."""
    confirmation_method: ConfirmationMethod

    @property
    def event_at_utc(self) -> datetime:
        return self.break_time

    @property
    def confirmation_at_utc(self) -> datetime:
        """A break is knowable on the bar that produced it -- unlike a
        swing, there is no later confirmation to wait for."""
        return self.break_time


@dataclass(frozen=True, slots=True)
class MssVerdict:
    """The full answer for one evaluated bar, PASS or not.

    `detect_mss` throws the rejections away for the hot path; research
    keeps them, because "no trade" is a finding and the reason it
    happened is the whole point of the event log.
    """

    qualification: MssQualification
    rejection_reason: MssRejectionReason | None
    sweep_id: str
    displacement_id: str
    direction: MssDirection | None
    """None only when the raid and the leg contradict each other, in
    which case the setup has no direction to speak of."""
    candidate_swing_id: str | None
    event: MssEvent | None

    @property
    def passed(self) -> bool:
        return self.qualification is MssQualification.PASS


def expected_direction(sweep: SweepEvent) -> MssDirection:
    """The only shift direction a raid of this side can produce.

    Buy-side liquidity is taken ABOVE price; the reversal that follows
    is down. The inverse for sell-side.
    """
    return (
        MssDirection.BEARISH
        if sweep.side is LevelSide.BUY_SIDE
        else MssDirection.BULLISH
    )


def directions_agree(sweep: SweepEvent, displacement: DisplacementEvent) -> bool:
    """Whether the expansion opposes the raid, as a reversal must.

    A sell-side raid with a bearish leg, or a buy-side raid with a
    bullish leg, is continuation through liquidity. It is not a shift
    and must not be converted into one by reading direction off the
    displacement alone.
    """
    wanted = (
        ExpansionDirection.DOWN
        if sweep.side is LevelSide.BUY_SIDE
        else ExpansionDirection.UP
    )
    return displacement.direction is wanted


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
    one place, so they cannot drift apart.

    Comparisons are strict throughout: a bar that exactly TOUCHES the
    swing price has not broken it, and a close exactly AT a configured
    threshold has not cleared it. Exact equality is common at round
    numbers and prior extremes, so letting it confirm would add trades
    precisely where the level is most contested.
    """
    bearish = direction is MssDirection.BEARISH

    if method is ConfirmationMethod.WICK_BREAK:
        return bar.low < swing_price if bearish else bar.high > swing_price

    if method is ConfirmationMethod.CANDLE_CLOSE:
        return bar.close < swing_price if bearish else bar.close > swing_price

    if method is ConfirmationMethod.BODY_CLOSE:
        if bearish:
            return bar.close < swing_price and bar.open < swing_price
        return bar.close > swing_price and bar.open > swing_price

    threshold = (
        min_points
        if method is ConfirmationMethod.CLOSE_PLUS_MIN_DISTANCE
        else min_atr_points
    )
    if bearish:
        return bar.close < swing_price - threshold
    return bar.close > swing_price + threshold


def select_swing(
    swings: Sequence[CanonicalSwing],
    *,
    sweep: SweepEvent,
    displacement: DisplacementEvent,
    selection: SwingSelection,
) -> tuple[CanonicalSwing | None, CanonicalSwing | None]:
    """The swing an MSS must break, and what the other rule would have
    picked.

    The opposing type is derived from the SWEEP's side, so selection
    cannot be steered by a displacement that disagrees with its own
    raid.

    Availability is measured at the DISPLACEMENT'S START
    (`start_at_utc`), not the current bar: a swing confirmed while the
    leg was already running was not known when the setup began, so
    breaking it is not the structural shift the model describes.
    """
    direction = expected_direction(sweep)
    wanted = (
        SwingType.LOW if direction is MssDirection.BEARISH else SwingType.HIGH
    )
    at = displacement.start_at_utc
    available = [
        swing for swing in visible_swings(swings, at=at) if swing.swing_type is wanted
    ]
    if not available:
        return None, None

    most_recent = most_recent_opposing(
        swings, at=at, raid_was_buy_side=sweep.side is LevelSide.BUY_SIDE
    )

    origin = displacement.expansion_origin
    if direction is MssDirection.BEARISH:
        below = [s for s in available if s.price < origin]
        nearest = max(below, key=lambda s: s.price) if below else None
    else:
        above = [s for s in available if s.price > origin]
        nearest = min(above, key=lambda s: s.price) if above else None

    chosen = most_recent if selection is SwingSelection.MOST_RECENT else nearest
    other = nearest if selection is SwingSelection.MOST_RECENT else most_recent
    if chosen is None:
        return None, None

    alternate = (
        other if other is not None and other.swing_id != chosen.swing_id else None
    )
    return chosen, alternate


def evaluate_mss(
    bars: Sequence[Bar],
    index: int,
    displacement: DisplacementEvent,
    sweep: SweepEvent,
    swings: Sequence[CanonicalSwing],
    config: MssConfig,
    *,
    tick_size: float,
) -> MssVerdict:
    """
    Whether the displacement broke the relevant confirmed opposing swing
    at `index`, and if not, why not.

    Raises when `sweep` is not the raid the displacement references: a
    chain assembled from mismatched parts is not attributable, and
    silently proceeding would corrupt every downstream statistic rather
    than failing where the mistake was made.
    """
    if sweep.sweep_id != displacement.sweep_id:
        raise ValueError(
            f"sweep {sweep.sweep_id} is not the raid behind displacement "
            f"{displacement.displacement_id} ({displacement.sweep_id}); "
            "a chain assembled from mismatched parts is not attributable"
        )

    def reject(
        reason: MssRejectionReason,
        *,
        direction: MssDirection | None,
        candidate: str | None = None,
    ) -> MssVerdict:
        return MssVerdict(
            qualification=MssQualification.REJECTED,
            rejection_reason=reason,
            sweep_id=sweep.sweep_id,
            displacement_id=displacement.displacement_id,
            direction=direction,
            candidate_swing_id=candidate,
            event=None,
        )

    if not directions_agree(sweep, displacement):
        return reject(MssRejectionReason.CONTRADICTORY_DIRECTION, direction=None)

    direction = expected_direction(sweep)

    if index <= displacement.start_index or index >= len(bars):
        return reject(MssRejectionReason.BEFORE_DISPLACEMENT, direction=direction)

    chosen, alternate = select_swing(
        swings, sweep=sweep, displacement=displacement, selection=config.selection
    )
    if chosen is None:
        return reject(MssRejectionReason.NO_ELIGIBLE_SWING, direction=direction)

    min_atr_points = 0.0
    if config.method is ConfirmationMethod.CLOSE_PLUS_ATR_THRESHOLD:
        atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
        if atr is None:
            return reject(
                MssRejectionReason.ATR_UNAVAILABLE,
                direction=direction,
                candidate=chosen.swing_id,
            )
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
        return reject(
            MssRejectionReason.NOT_BROKEN,
            direction=direction,
            candidate=chosen.swing_id,
        )

    bearish = direction is MssDirection.BEARISH
    if config.method is ConfirmationMethod.WICK_BREAK:
        break_price = bar.low if bearish else bar.high
    else:
        break_price = bar.close
    break_distance = abs(break_price - chosen.price)

    atr_for_report = atr_ticks(
        bars, index, period=config.atr_period, tick_size=tick_size
    )
    atr_price = (atr_for_report or 0) * tick_size

    event = MssEvent(
        mss_id=f"MSS:{displacement.displacement_id}:{bar.open_time_utc.isoformat()}",
        sweep_id=displacement.sweep_id,
        displacement_id=displacement.displacement_id,
        direction=direction,
        qualification=MssQualification.PASS,
        broken_swing_id=chosen.swing_id,
        broken_price=chosen.price,
        swing_event_time=chosen.occurred_at,
        swing_confirmation_time=chosen.available_at,
        selection_method=config.selection,
        alternate_swing_id=alternate.swing_id if alternate is not None else None,
        distance_from_displacement_origin=abs(
            chosen.price - displacement.expansion_origin
        ),
        distance_from_sweep=abs(chosen.price - sweep.level.price),
        break_index=index,
        break_price=break_price,
        break_time=bar.open_time_utc,
        break_distance_points=break_distance,
        break_distance_atr=(break_distance / atr_price if atr_price > 0 else None),
        confirmation_method=config.method,
    )
    return MssVerdict(
        qualification=MssQualification.PASS,
        rejection_reason=None,
        sweep_id=sweep.sweep_id,
        displacement_id=displacement.displacement_id,
        direction=direction,
        candidate_swing_id=chosen.swing_id,
        event=event,
    )


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
    """The confirmed shift at `index`, or None. Thin wrapper over
    `evaluate_mss` for callers that do not keep rejections."""
    return evaluate_mss(
        bars, index, displacement, sweep, swings, config, tick_size=tick_size
    ).event
