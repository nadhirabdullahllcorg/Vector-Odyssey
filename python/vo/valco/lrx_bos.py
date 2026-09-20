"""
BOS -- Break of Structure. A break that CONTINUES the prevailing
direction, as opposed to an MSS, which reverses it.

The mechanic is nearly identical to MSS and the meaning is opposite,
which is exactly why they are separate modules rather than one function
with a flag. Collapsing them would make the two most important numbers
in the model -- does the reversal happen, and does it then run --
inseparable in every report that followed.

    MSS   break of the swing OPPOSING the raid.   The turn.
    BOS   break of the swing in the NEW direction. The continuation.

After a buy-side raid and a bearish MSS, the new direction is down. A
subsequent BOS is a break of a further swing LOW -- price making a lower
low, confirming the down move is structural rather than a single leg.
So MSS and BOS after the same raid break the SAME TYPE of swing; what
differs is that BOS comes after the shift and must break a level the MSS
did not already break.

WHAT MAKES A BOS DISTINCT FROM "the MSS again": the swing it breaks must
be one the MSS did not, and it must be beyond the MSS's own swing in the
direction of travel. Without that rule, every bar that stayed below the
MSS swing would log a fresh BOS and the continuation count would measure
bar count rather than structure.

BOS CAN ALSO EXIST WITHOUT ANY RAID -- ordinary trending structure. This
module only handles the post-MSS case, because that is what the model
uses it for: confirming the reversal became a trend. A standalone trend
BOS is a different question and does not belong to this chain.

SHARES MSS'S MACHINERY ON PURPOSE. The five confirmation methods and the
availability rule are imported from lrx_mss rather than re-stated, so
the two detectors cannot drift apart in how they define "broke".

NO LOOKAHEAD: evaluated on bars[<= index], against swings confirmed
before the MSS occurred.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.observation.swings import SwingType
from vo.valco.lrx_mss import (
    ConfirmationMethod,
    MssDirection,
    MssEvent,
    broke_level,
)
from vo.valco.lrx_swings import CanonicalSwing, visible_swings


@dataclass(frozen=True, slots=True)
class BosConfig:
    method: ConfirmationMethod = ConfirmationMethod.CLOSE
    min_break_points: float = 0.0
    min_break_atr: float = 0.0
    atr_period: int = 14
    require_beyond_mss_swing: bool = True
    """Whether the broken swing must sit beyond the one the MSS broke.
    Configurable only so the looser definition can be MEASURED; leaving
    it off turns the continuation count into a bar count."""


@dataclass(frozen=True, slots=True)
class BosEvent:
    """One confirmed continuation break after a structural shift."""

    bos_id: str
    sweep_id: str
    displacement_id: str
    mss_id: str
    direction: MssDirection
    sequence: int
    """1 for the first BOS after the shift, 2 for the next, and so on.
    A second BOS is a stronger statement than a first; conflating them
    would lose that."""

    swing_id: str
    swing_price: float
    swing_occurred_at: datetime
    swing_confirmed_at: datetime

    distance_from_mss_swing: float
    """How much further this break carried structure beyond the shift's
    own swing. Zero would mean it was the same level."""

    break_index: int
    break_price: float
    break_at_utc: datetime
    break_distance: float
    break_distance_atr: float
    bars_since_mss: int
    confirmation_method: ConfirmationMethod

    @property
    def event_at_utc(self) -> datetime:
        return self.break_at_utc

    @property
    def confirmation_at_utc(self) -> datetime:
        return self.break_at_utc


def _eligible_swings(
    swings: Sequence[CanonicalSwing],
    mss: MssEvent,
    *,
    require_beyond: bool,
) -> list[CanonicalSwing]:
    """Swings a continuation break could legitimately take.

    Same TYPE as the one the MSS broke -- after a bearish shift, further
    lows. Confirmed before the shift, so the break is of known
    structure. And, by default, beyond the MSS's own swing, so the shift
    itself is not re-counted as its own continuation.
    """
    wanted = SwingType.LOW if mss.direction is MssDirection.BEARISH else SwingType.HIGH
    candidates = [
        swing
        for swing in visible_swings(swings, at=mss.break_at_utc)
        if swing.swing_type is wanted and swing.swing_id != mss.swing_id
    ]
    if not require_beyond:
        return candidates
    if mss.direction is MssDirection.BEARISH:
        return [s for s in candidates if s.price < mss.swing_price]
    return [s for s in candidates if s.price > mss.swing_price]


def detect_bos(
    bars: Sequence[Bar],
    index: int,
    mss: MssEvent,
    swings: Sequence[CanonicalSwing],
    config: BosConfig,
    *,
    tick_size: float,
    already_broken: Sequence[str] = (),
    sequence: int = 1,
) -> BosEvent | None:
    """
    Whether structure continued at `index`, after the shift `mss`.

    `already_broken` carries the swing ids previous BOS events in this
    chain consumed, so a run of bars below the same level logs one
    continuation rather than one per bar. The caller owns that list
    because the caller owns the chain.

    Returns None when nothing broke, when no eligible swing remains, or
    when an ATR-scaled threshold has no ATR to scale against.
    """
    if index <= mss.break_index or index >= len(bars):
        return None

    consumed = set(already_broken)
    candidates = [
        swing
        for swing in _eligible_swings(
            swings, mss, require_beyond=config.require_beyond_mss_swing
        )
        if swing.swing_id not in consumed
    ]
    if not candidates:
        return None

    # The NEAREST untaken level in the direction of travel: the next one
    # price would actually reach. Taking the furthest would skip levels
    # that were broken on the way and overstate each break's reach.
    if mss.direction is MssDirection.BEARISH:
        target = max(candidates, key=lambda s: s.price)
    else:
        target = min(candidates, key=lambda s: s.price)

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
        target.price,
        mss.direction,
        min_points=config.min_break_points,
        min_atr_points=min_atr_points,
    ):
        return None

    bearish = mss.direction is MssDirection.BEARISH
    if config.method is ConfirmationMethod.WICK:
        break_price = bar.low if bearish else bar.high
    else:
        break_price = bar.close
    break_distance = abs(break_price - target.price)

    atr_for_report = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
    atr_price = (atr_for_report or 0) * tick_size

    return BosEvent(
        bos_id=f"BOS:{mss.mss_id}:{sequence}",
        sweep_id=mss.sweep_id,
        displacement_id=mss.displacement_id,
        mss_id=mss.mss_id,
        direction=mss.direction,
        sequence=sequence,
        swing_id=target.swing_id,
        swing_price=target.price,
        swing_occurred_at=target.occurred_at,
        swing_confirmed_at=target.available_at,
        distance_from_mss_swing=abs(target.price - mss.swing_price),
        break_index=index,
        break_price=break_price,
        break_at_utc=bar.open_time_utc,
        break_distance=break_distance,
        break_distance_atr=break_distance / atr_price if atr_price > 0 else 0.0,
        bars_since_mss=index - mss.break_index,
        confirmation_method=config.method,
    )


def detect_bos_run(
    bars: Sequence[Bar],
    mss: MssEvent,
    swings: Sequence[CanonicalSwing],
    config: BosConfig,
    *,
    tick_size: float,
    until_index: int | None = None,
) -> tuple[BosEvent, ...]:
    """Every continuation break from the shift forward, each level taken
    at most once. Sequence numbering and the consumed-level bookkeeping
    live here so callers do not each re-derive them."""
    end = len(bars) - 1 if until_index is None else min(until_index, len(bars) - 1)
    events: list[BosEvent] = []
    consumed: list[str] = []
    for index in range(mss.break_index + 1, end + 1):
        event = detect_bos(
            bars,
            index,
            mss,
            swings,
            config,
            tick_size=tick_size,
            already_broken=consumed,
            sequence=len(events) + 1,
        )
        if event is not None:
            events.append(event)
            consumed.append(event.swing_id)
    return tuple(events)
