"""
Displacement -- did the raid produce measurable opposing expansion?

    Raid -> [DISPLACEMENT] -> MSS -> Inefficiency -> Rebalance -> Objective

ONE QUESTION, AND NOT THE NEXT ONE. This detector answers "how far, how
fast, how cleanly did price move away from the swept level" and stops
there. It does NOT decide whether structure broke -- that is the MSS
detector's question, and it needs the confirmed swing map to answer it.
Keeping them apart is what makes raid, expansion and structural break
three independently testable events rather than one opaque verdict. A
displacement that moved 1.7 ATR and created a gap is a fact worth having
whether or not an MSS followed.

MEASUREMENTS ARE ALWAYS COMPUTED, INCLUDING ON FAILURE. A binary
"displaced: true/false" throws away exactly the data needed to ask
whether the threshold is in the right place. So a DisplacementEvent is
produced for every evaluated leg, carrying its full measurement set, and
`qualification` is a separate field with a stated reason. A run of FAILs
with range_atr around 1.4 against a 1.5 threshold is a finding; a run of
Falses is not.

QUALIFICATION IS A CONJUNCTION, and each clause exists for a reason:
  - range_atr: the move must be large relative to current volatility,
    not large in points. A fixed point threshold silently tightens in a
    quiet session and loosens in a violent one.
  - body_ratio: a bar that travelled far and closed mid-range is
    indecision wearing a big range. Bodies are what displacement means.
  - net_move_atr: total distance covered net of retracement, so a leg
    that went up 2 ATR and came back 1.8 does not qualify on range alone.

FVG PRESENCE, NOT THE FVG OBJECT. `fvg_created` is a boolean derived
from three-bar geometry, because "did this expansion leave an
inefficiency" is part of judging the expansion. The full inefficiency --
type, CE, mitigation, invalidation -- belongs to the Inefficiency engine,
which will consume `three_bar_gap` from here rather than re-deriving the
geometry a second time.

NO LOOKAHEAD. A leg is measured from the sweep's return bar to `index`,
reading bars[<= index] only. Evaluating at successive indices is how the
caller sees several candidate expansions develop from one raid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.valco.lrx_levels import LevelSide
from vo.valco.lrx_sweep import SweepEvent


class ExpansionDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"

    def __str__(self) -> str:
        return self.value


class Qualification(Enum):
    PASS = "PASS"
    FAIL = "FAIL"

    def __str__(self) -> str:
        return self.value


class QualificationMode(Enum):
    """Which definition of "displaced" a run is testing.

    The spec is explicit that no ATR figure is a canonical ICT rule --
    these are VO operational definitions, and which one is in force must
    be a config choice rather than a constant baked into the detector.
    One mode per backtest; mixing them makes a result unattributable.
    """

    ATR_RANGE = "ATR_RANGE"
    ATR_BODY = "ATR_BODY"
    BODY_RATIO = "BODY_RATIO"
    CONSECUTIVE_BARS = "CONSECUTIVE_BARS"
    VELOCITY = "VELOCITY"
    COMBINED = "COMBINED"
    """Every clause must pass -- the strictest reading, and the baseline."""

    def __str__(self) -> str:
        return self.value


class OriginModel(Enum):
    """Where the expansion is measured FROM (spec section 11).

    Deliberately several: the spec warns against assuming one candle is
    always the correct origin, so each is computed as a candidate and the
    configured one drives the event's `expansion_origin`.
    """

    SWEEP_EXTREME = "SWEEP_EXTREME"
    """The raid's own furthest penetration -- the price the reversal is
    measured away from."""
    LEG_START_OPEN = "LEG_START_OPEN"
    """The open of the first bar of the expansion leg."""
    LAST_OPPOSING_EXTREME = "LAST_OPPOSING_EXTREME"
    """The furthest adverse price inside the leg before it committed --
    the region ICT discretion usually points at."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DisplacementConfig:
    """The qualification definition in force for one run.

    Every threshold is here rather than passed loose, so a research run
    records exactly which definition produced its events.
    """

    mode: QualificationMode = QualificationMode.COMBINED
    origin_model: OriginModel = OriginModel.SWEEP_EXTREME
    min_atr_range: float = 1.5
    min_atr_body: float = 1.0
    min_body_ratio: float = 0.5
    min_net_move_atr: float = 1.0
    min_consecutive_bars: int = 2
    min_velocity_atr_per_minute: float = 0.1
    max_bars: int = 5
    atr_period: int = 14


@dataclass(frozen=True, slots=True)
class GapGeometry:
    """A three-bar inefficiency's bounds. Presence and shape only -- the
    Inefficiency engine owns lifecycle (mitigation, invalidation)."""

    upper: float
    lower: float
    created_index: int

    @property
    def size(self) -> float:
        return self.upper - self.lower

    @property
    def consequent_encroachment(self) -> float:
        """The midpoint -- CE in ICT's vocabulary."""
        return (self.upper + self.lower) / 2.0


def three_bar_gap(
    bars: Sequence[Bar], index: int, direction: ExpansionDirection
) -> GapGeometry | None:
    """The classic three-bar gap at `index`, where bar index-2 and bar
    index do not overlap in the direction of travel.

    Shared deliberately: the Inefficiency engine consumes this rather
    than deriving the same geometry a second time, so the two can never
    disagree about whether a gap exists.
    """
    if index < 2 or index >= len(bars):
        return None

    first, third = bars[index - 2], bars[index]

    if direction is ExpansionDirection.UP:
        if third.low > first.high:
            return GapGeometry(upper=third.low, lower=first.high, created_index=index)
        return None

    if third.high < first.low:
        return GapGeometry(upper=first.low, lower=third.high, created_index=index)
    return None


@dataclass(frozen=True, slots=True)
class DisplacementEvent:
    """One measured expansion leg following a raid.

    Produced whether or not it qualifies -- see the module docstring on
    why a FAIL carrying its numbers is worth more than a False.
    """

    displacement_id: str
    sweep_id: str
    """Links this expansion to the raid that preceded it (spec section 3).
    An MSS and an FVG will carry it too, so the whole chain is
    attributable rather than merely co-occurring."""
    sweep_level_kind: str
    direction: ExpansionDirection
    start_index: int
    end_index: int
    event_at_utc: datetime
    """When the expansion began."""
    confirmation_at_utc: datetime
    """When it became MEASURABLE -- the close of the evaluated bar.
    Nothing may consume this event before it (spec section 4). Distinct
    from event_at_utc on purpose: a leg that started at 10:31 is not
    knowable at 10:31."""
    start_at_utc: datetime
    end_at_utc: datetime
    start_price: float
    end_price: float
    high: float
    low: float
    range_points: float
    range_atr: float
    body_points: float
    body_atr: float
    body_ratio: float
    wick_points: float
    net_move_points: float
    net_move_atr: float
    distance_from_sweep: float
    """How far the leg's far extreme travelled from the swept level
    itself -- the reversal's reach, distinct from the leg's own range."""
    directional_bars: int
    """Total bars closing in the direction of travel, not just the
    longest run."""
    bar_count: int
    elapsed_seconds: float
    velocity_atr_per_minute: float
    max_favorable_excursion: float
    close_location: float
    consecutive_directional_bars: int
    gap: GapGeometry | None
    expansion_origin: float
    """Per the configured OriginModel."""
    origin_candidates: dict[str, float]
    """Every origin model's answer, so a later study can compare them
    without re-running the detector (spec section 11)."""
    expansion_equilibrium: float
    """CE: the midpoint of the measured expansion RANGE,
    (high + low) / 2, per spec section 12."""
    mode: QualificationMode
    qualification: Qualification
    qualification_reason: str

    @property
    def qualified(self) -> bool:
        return self.qualification is Qualification.PASS

    @property
    def fvg_created(self) -> bool:
        return self.gap is not None


def _close_location(direction: ExpansionDirection, close: float, high: float, low: float) -> float:
    """Where the leg's final close sits in its range: 1.0 means it closed
    at the favourable extreme, 0.0 at the adverse one. A displacement
    that closes mid-range travelled without committing."""
    span = high - low
    if span <= 0:
        return 0.5
    if direction is ExpansionDirection.UP:
        return (close - low) / span
    return (high - close) / span


def _consecutive_directional(bars: Sequence[Bar], direction: ExpansionDirection) -> int:
    """Longest run of bars closing in the direction of travel."""
    best = 0
    current = 0
    for bar in bars:
        went_our_way = (
            bar.close > bar.open
            if direction is ExpansionDirection.UP
            else bar.close < bar.open
        )
        current = current + 1 if went_our_way else 0
        best = max(best, current)
    return best


def _qualify(
    config: DisplacementConfig,
    *,
    range_atr: float,
    body_atr: float,
    body_ratio: float,
    net_move_atr: float,
    consecutive: int,
    velocity: float,
) -> tuple[Qualification, str]:
    """Apply the configured definition. Each clause states its own
    shortfall so a FAIL is diagnostic rather than a verdict."""
    clauses: list[tuple[bool, str]] = []

    def range_clause() -> tuple[bool, str]:
        return (
            range_atr >= config.min_atr_range,
            f"range {range_atr:.2f} ATR below {config.min_atr_range:.2f}",
        )

    def body_clause() -> tuple[bool, str]:
        return (
            body_atr >= config.min_atr_body,
            f"body {body_atr:.2f} ATR below {config.min_atr_body:.2f}",
        )

    def ratio_clause() -> tuple[bool, str]:
        return (
            body_ratio >= config.min_body_ratio,
            f"body ratio {body_ratio:.2f} below {config.min_body_ratio:.2f}",
        )

    def bars_clause() -> tuple[bool, str]:
        return (
            consecutive >= config.min_consecutive_bars,
            f"{consecutive} consecutive bars below {config.min_consecutive_bars}",
        )

    def velocity_clause() -> tuple[bool, str]:
        return (
            velocity >= config.min_velocity_atr_per_minute,
            f"velocity {velocity:.3f} ATR/min below "
            f"{config.min_velocity_atr_per_minute:.3f}",
        )

    def net_clause() -> tuple[bool, str]:
        return (
            net_move_atr >= config.min_net_move_atr,
            f"net move {net_move_atr:.2f} ATR below {config.min_net_move_atr:.2f}",
        )

    if config.mode is QualificationMode.ATR_RANGE:
        clauses = [range_clause()]
    elif config.mode is QualificationMode.ATR_BODY:
        clauses = [body_clause()]
    elif config.mode is QualificationMode.BODY_RATIO:
        clauses = [ratio_clause()]
    elif config.mode is QualificationMode.CONSECUTIVE_BARS:
        clauses = [bars_clause()]
    elif config.mode is QualificationMode.VELOCITY:
        clauses = [velocity_clause()]
    else:  # COMBINED
        clauses = [range_clause(), ratio_clause(), net_clause()]

    failures = [reason for ok, reason in clauses if not ok]
    if failures:
        return Qualification.FAIL, "; ".join(failures)
    return Qualification.PASS, f"{config.mode} satisfied"


def measure_displacement(
    bars: Sequence[Bar],
    index: int,
    sweep: SweepEvent,
    config: DisplacementConfig,
    *,
    tick_size: float,
) -> DisplacementEvent | None:
    """
    Measure the expansion leg from the raid's return bar to `index`, and
    say whether it meets the configured definition.

    Returns None only when the leg cannot be MEASURED -- `index` at or
    before the raid, leg longer than the budget, ATR unavailable or zero.
    A measurable-but-weak leg comes back as a FAIL carrying its numbers.
    """
    start = sweep.returned_index
    if index <= start or index >= len(bars):
        return None
    if index - start > config.max_bars:
        return None

    atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
    if atr is None:
        return None
    atr_price = atr * tick_size
    if atr_price <= 0:
        return None

    direction = (
        ExpansionDirection.DOWN
        if sweep.side is LevelSide.BUY_SIDE
        else ExpansionDirection.UP
    )

    leg = bars[start : index + 1]
    first, last = leg[0], leg[-1]

    high = max(bar.high for bar in leg)
    low = min(bar.low for bar in leg)
    range_points = high - low

    start_price = first.open
    end_price = last.close
    net_move_points = (
        end_price - start_price
        if direction is ExpansionDirection.UP
        else start_price - end_price
    )

    body_points = sum(abs(bar.close - bar.open) for bar in leg)
    total_range = sum(bar.high - bar.low for bar in leg)
    wick_points = max(0.0, total_range - body_points)
    body_ratio = body_points / total_range if total_range > 0 else 0.0

    elapsed_seconds = (last.open_time_utc - first.open_time_utc).total_seconds()
    elapsed_minutes = elapsed_seconds / 60.0
    net_move_atr = net_move_points / atr_price
    velocity = net_move_atr / elapsed_minutes if elapsed_minutes > 0 else 0.0

    far = low if direction is ExpansionDirection.DOWN else high
    mfe = abs(far - start_price)
    distance_from_sweep = abs(far - sweep.level.price)

    gap: GapGeometry | None = None
    for probe in range(start + 2, index + 1):
        found = three_bar_gap(bars, probe, direction)
        if found is not None:
            gap = found
            break

    # Every origin model computed, the configured one selected -- the
    # spec warns against assuming one candle is always right.
    adverse = (
        max(bar.high for bar in leg)
        if direction is ExpansionDirection.DOWN
        else min(bar.low for bar in leg)
    )
    candidates = {
        str(OriginModel.SWEEP_EXTREME): sweep.penetration_price,
        str(OriginModel.LEG_START_OPEN): start_price,
        str(OriginModel.LAST_OPPOSING_EXTREME): adverse,
    }
    origin = candidates[str(config.origin_model)]

    consecutive = _consecutive_directional(leg, direction)
    directional_bars = sum(
        1
        for bar in leg
        if (bar.close > bar.open)
        is (direction is ExpansionDirection.UP)
        and bar.close != bar.open
    )
    range_atr = range_points / atr_price
    body_atr = body_points / atr_price

    qualification, reason = _qualify(
        config,
        range_atr=range_atr,
        body_atr=body_atr,
        body_ratio=body_ratio,
        net_move_atr=net_move_atr,
        consecutive=consecutive,
        velocity=velocity,
    )

    return DisplacementEvent(
        displacement_id=f"DISP:{sweep.sweep_id}:{last.open_time_utc.isoformat()}",
        sweep_id=sweep.sweep_id,
        sweep_level_kind=str(sweep.level.kind),
        direction=direction,
        start_index=start,
        end_index=index,
        event_at_utc=first.open_time_utc,
        confirmation_at_utc=last.open_time_utc,
        start_at_utc=first.open_time_utc,
        end_at_utc=last.open_time_utc,
        start_price=start_price,
        end_price=end_price,
        high=high,
        low=low,
        range_points=range_points,
        range_atr=range_atr,
        body_points=body_points,
        body_atr=body_atr,
        body_ratio=body_ratio,
        wick_points=wick_points,
        net_move_points=net_move_points,
        net_move_atr=net_move_atr,
        distance_from_sweep=distance_from_sweep,
        directional_bars=directional_bars,
        bar_count=len(leg),
        elapsed_seconds=elapsed_seconds,
        velocity_atr_per_minute=velocity,
        max_favorable_excursion=mfe,
        close_location=_close_location(direction, end_price, high, low),
        consecutive_directional_bars=consecutive,
        gap=gap,
        expansion_origin=origin,
        origin_candidates=candidates,
        # Spec section 12: CE is the midpoint of the measured expansion
        # RANGE, not of origin-to-extreme.
        expansion_equilibrium=(high + low) / 2.0,
        mode=config.mode,
        qualification=qualification,
        qualification_reason=reason,
    )
