"""
BOS -- Break of Structure. A break that CONTINUES the prevailing
direction, as opposed to an MSS, which reverses it.

    Raid -> Displacement -> MSS -> [BOS] -> ...

    MSS   break of the swing OPPOSING the raid.   The turn.
    BOS   break of a further swing the SAME way.  The continuation.

The mechanic is nearly identical to MSS and the meaning is opposite,
which is exactly why they are separate modules rather than one function
with a flag. Collapsing them would make the two most important numbers
in the model -- does the reversal happen, and does it then run --
inseparable in every report that followed.

After a buy-side raid and a bearish MSS the new direction is down, so a
BOS is a break of a FURTHER swing LOW: price making a lower low,
confirming the move is structural rather than a single leg. MSS and BOS
after the same raid therefore break the same TYPE of swing. What
differs is that BOS comes after the shift and must break a level the
shift did not.

WHAT KEEPS BOS FROM RE-COUNTING THE MSS: the swing it breaks must not
be the one the MSS broke, and by default must sit beyond it in the
direction of travel. Without that rule every bar that merely stayed
below the shift's level would log a fresh BOS and the continuation
count would measure elapsed bars rather than structure.

DIRECTION IS INHERITED, NEVER RE-DERIVED. It comes from the MssEvent,
which took it from the raid's side and refused to proceed when the
expansion contradicted it. Re-deriving direction here from price action
would give a second opinion that could disagree with the shift it
claims to continue.

BOS CAN ALSO EXIST WITHOUT ANY RAID -- ordinary trending structure.
This module only handles the post-MSS case, because that is what the
model uses it for. A standalone trend BOS is a different question and
does not belong to this chain.

SHARES MSS'S MACHINERY ON PURPOSE. The five confirmation methods, the
strict comparisons and the break predicate are imported from lrx_mss
rather than re-stated, so the two detectors cannot drift apart in how
they define "broke". Price convention is MSS's: bid-based OHLC from
MqlRates via VO_Bridge, identical in backtest and live.

NO LOOKAHEAD: evaluated on bars[<= index], against swings confirmed no
later than the configured cutoff -- see StructureCutoff, which is the
one genuinely ambiguous choice in this detector and is therefore
explicit, configurable, and recorded on every event.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

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


class StructureCutoff(Enum):
    """How recent a swing may be and still count as structure the
    continuation broke.

    This is a real definitional fork, not a tuning knob, so it is named
    rather than buried in a comparison.
    """

    AT_MSS = "AT_MSS"
    """Only structure that already existed when the shift occurred. The
    strict reading: the trend is breaking levels that pre-date the
    reversal, so a BOS cannot be manufactured out of swings the move
    itself created."""
    AT_BREAK_BAR = "AT_BREAK_BAR"
    """Any swing confirmed by the evaluated bar, including ones formed
    during the move. The looser, more conventional reading -- a
    stair-step down breaking each new low it makes. Not lookahead: such
    a swing is knowable when the breaking bar closes. It does admit
    structure the move authored itself, which is why it is not the
    default."""

    def __str__(self) -> str:
        return self.value


class BosQualification(Enum):
    PASS = "PASS"
    REJECTED = "REJECTED"

    def __str__(self) -> str:
        return self.value


class BosRejectionReason(Enum):
    """Why no continuation was recorded. Named rather than collapsed
    into a bare None so a move that failed to extend can be attributed
    later."""

    NO_ELIGIBLE_SWING = "NO_ELIGIBLE_SWING"
    """No untaken swing of the right type remained beyond the shift."""
    ATR_UNAVAILABLE = "ATR_UNAVAILABLE"
    NOT_BROKEN = "NOT_BROKEN"
    BEFORE_SHIFT = "BEFORE_SHIFT"
    """The evaluated bar is at or before the MSS break, or past the end
    of the series."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BosConfig:
    method: ConfirmationMethod = ConfirmationMethod.CANDLE_CLOSE
    cutoff: StructureCutoff = StructureCutoff.AT_MSS
    min_break_points: float = 0.0
    min_break_atr: float = 0.0
    atr_period: int = 14
    require_beyond_mss_swing: bool = True
    """Whether the broken swing must sit beyond the one the MSS broke.
    Configurable only so the looser definition can be MEASURED and
    dismissed with evidence; switching it off turns the continuation
    count into something close to a bar count."""


@dataclass(frozen=True, slots=True)
class BosEvent:
    """One confirmed continuation break.

    Field names deliberately mirror MssEvent. The two are sibling
    records that end up in the same event log and the same report; a
    reader should not have to remember which one calls it broken_price
    and which calls it swing_price.
    """

    bos_id: str
    sweep_id: str
    displacement_id: str
    mss_id: str
    direction: MssDirection
    qualification: BosQualification
    sequence: int
    """1 for the first BOS after the shift, 2 for the next, and so on.
    A second BOS is a stronger statement than a first; conflating them
    would lose that."""

    broken_swing_id: str
    broken_price: float
    swing_event_time: datetime
    swing_confirmation_time: datetime
    structure_cutoff: StructureCutoff

    distance_from_mss_swing: float
    """How much further this break carried structure beyond the shift's
    own swing. Zero would mean it was the same level."""

    break_index: int
    break_price: float
    break_time: datetime
    break_distance_points: float
    break_distance_atr: float | None
    """None when ATR is unavailable at this bar -- NOT 0.0, which would
    be indistinguishable from a break that genuinely covered no ATR."""
    bars_since_mss: int
    confirmation_method: ConfirmationMethod

    @property
    def event_at_utc(self) -> datetime:
        return self.break_time

    @property
    def confirmation_at_utc(self) -> datetime:
        return self.break_time


@dataclass(frozen=True, slots=True)
class BosVerdict:
    """The full answer for one evaluated bar, PASS or not."""

    qualification: BosQualification
    rejection_reason: BosRejectionReason | None
    mss_id: str
    direction: MssDirection
    candidate_swing_id: str | None
    event: BosEvent | None

    @property
    def passed(self) -> bool:
        return self.qualification is BosQualification.PASS


def eligible_swings(
    swings: Sequence[CanonicalSwing],
    mss: MssEvent,
    *,
    cutoff_at: datetime,
    require_beyond: bool,
) -> tuple[CanonicalSwing, ...]:
    """Swings a continuation break could legitimately take.

    Same TYPE as the one the MSS broke -- after a bearish shift, further
    lows. Never the shift's own swing. Confirmed no later than
    `cutoff_at`. And, by default, beyond the MSS's own swing, so the
    shift is not re-counted as its own continuation.
    """
    wanted = SwingType.LOW if mss.direction is MssDirection.BEARISH else SwingType.HIGH
    candidates = [
        swing
        for swing in visible_swings(swings, at=cutoff_at)
        if swing.swing_type is wanted and swing.swing_id != mss.broken_swing_id
    ]
    if not require_beyond:
        return tuple(candidates)
    if mss.direction is MssDirection.BEARISH:
        return tuple(s for s in candidates if s.price < mss.broken_price)
    return tuple(s for s in candidates if s.price > mss.broken_price)


def evaluate_bos(
    bars: Sequence[Bar],
    index: int,
    mss: MssEvent,
    swings: Sequence[CanonicalSwing],
    config: BosConfig,
    *,
    tick_size: float,
    already_broken: Sequence[str] = (),
    sequence: int = 1,
) -> BosVerdict:
    """
    Whether structure continued at `index` after the shift `mss`, and if
    not, why not.

    `already_broken` carries the swing ids previous BOS events in this
    chain consumed, so a run of bars below the same level logs one
    continuation rather than one per bar. The caller owns that list
    because the caller owns the chain; `detect_bos_run` does it for you.
    """

    def reject(
        reason: BosRejectionReason, *, candidate: str | None = None
    ) -> BosVerdict:
        return BosVerdict(
            qualification=BosQualification.REJECTED,
            rejection_reason=reason,
            mss_id=mss.mss_id,
            direction=mss.direction,
            candidate_swing_id=candidate,
            event=None,
        )

    if index <= mss.break_index or index >= len(bars):
        return reject(BosRejectionReason.BEFORE_SHIFT)

    bar = bars[index]
    cutoff_at = (
        mss.break_time
        if config.cutoff is StructureCutoff.AT_MSS
        else bar.open_time_utc
    )

    consumed = set(already_broken)
    candidates = [
        swing
        for swing in eligible_swings(
            swings,
            mss,
            cutoff_at=cutoff_at,
            require_beyond=config.require_beyond_mss_swing,
        )
        if swing.swing_id not in consumed
    ]
    if not candidates:
        return reject(BosRejectionReason.NO_ELIGIBLE_SWING)

    # The NEAREST untaken level in the direction of travel: the next one
    # price would actually reach. Taking the furthest would skip the
    # levels crossed on the way and overstate each break's reach.
    bearish = mss.direction is MssDirection.BEARISH
    if bearish:
        target = max(candidates, key=lambda s: s.price)
    else:
        target = min(candidates, key=lambda s: s.price)

    min_atr_points = 0.0
    if config.method is ConfirmationMethod.CLOSE_PLUS_ATR_THRESHOLD:
        atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
        if atr is None:
            return reject(
                BosRejectionReason.ATR_UNAVAILABLE, candidate=target.swing_id
            )
        min_atr_points = atr * tick_size * config.min_break_atr

    if not broke_level(
        config.method,
        bar,
        target.price,
        mss.direction,
        min_points=config.min_break_points,
        min_atr_points=min_atr_points,
    ):
        return reject(BosRejectionReason.NOT_BROKEN, candidate=target.swing_id)

    if config.method is ConfirmationMethod.WICK_BREAK:
        break_price = bar.low if bearish else bar.high
    else:
        break_price = bar.close
    break_distance = abs(break_price - target.price)

    atr_for_report = atr_ticks(
        bars, index, period=config.atr_period, tick_size=tick_size
    )
    atr_price = (atr_for_report or 0) * tick_size

    event = BosEvent(
        bos_id=f"BOS:{mss.mss_id}:{sequence}",
        sweep_id=mss.sweep_id,
        displacement_id=mss.displacement_id,
        mss_id=mss.mss_id,
        direction=mss.direction,
        qualification=BosQualification.PASS,
        sequence=sequence,
        broken_swing_id=target.swing_id,
        broken_price=target.price,
        swing_event_time=target.occurred_at,
        swing_confirmation_time=target.available_at,
        structure_cutoff=config.cutoff,
        distance_from_mss_swing=abs(target.price - mss.broken_price),
        break_index=index,
        break_price=break_price,
        break_time=bar.open_time_utc,
        break_distance_points=break_distance,
        break_distance_atr=(break_distance / atr_price if atr_price > 0 else None),
        bars_since_mss=index - mss.break_index,
        confirmation_method=config.method,
    )
    return BosVerdict(
        qualification=BosQualification.PASS,
        rejection_reason=None,
        mss_id=mss.mss_id,
        direction=mss.direction,
        candidate_swing_id=target.swing_id,
        event=event,
    )


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
    """The confirmed continuation at `index`, or None. Thin wrapper over
    `evaluate_bos` for callers that do not keep rejections."""
    return evaluate_bos(
        bars,
        index,
        mss,
        swings,
        config,
        tick_size=tick_size,
        already_broken=already_broken,
        sequence=sequence,
    ).event


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
            consumed.append(event.broken_swing_id)
    return tuple(events)
