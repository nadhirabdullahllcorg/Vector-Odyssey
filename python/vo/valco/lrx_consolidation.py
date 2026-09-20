"""
Consolidation -- the contained, directionally inefficient period a CERR
cycle begins from.

    [CONSOLIDATION] -> Expansion -> Retracement -> Reversal

CONSOLIDATION IS NOT "ATR IS LOW". A quiet drift is small and perfectly
directional; a violent chop inside a tight box is large and goes
nowhere. Only the second is consolidation, so containment and
directional inefficiency are measured separately and both must hold.

THIS MODULE ONLY MEASURES AND CLASSIFIES. It knows nothing about cycles,
trades or phases -- the CERR state machine consumes its output. It also
does not turn range_high/range_low into liquidity levels: those are
geometry, and the Reference Level and Liquidity engines remain solely
responsible for deciding what price is a level.

TWO EFFICIENCY RATIOS, NAMED APART, RECORDED TOGETHER.

    kaufman_efficiency_ratio   |net close-to-close move| / sum|close-to-close move|
    body_efficiency_ratio      |last close - first open| / sum|close - open|

They measure different things -- path efficiency versus body efficiency
-- and are not interchangeable, so neither is ever called plainly
"efficiency_ratio" on a record that carries both. A report column that
could be either is a result nobody can interpret afterwards.
 VO already has a
calibrated Kaufman ER (vo.observation.efficiency_ratio) driving the
regime engine and entry timing. The CERR specification names a
different, body-anchored formula. Shipping the second as *the*
efficiency ratio would be the two-systems-quietly-disagreeing failure
this project keeps removing, and dropping it would ignore the spec. So
both are measured on every window, EfficiencyMeasure selects which one
qualifies, and a research run can compare them on identical data.

NO THRESHOLD IS INVENTED HERE. Every limit is None by default and a
config with none set returns UNCONFIGURED -- "measured, but no
definition of consolidation is in force" -- rather than silently
passing everything. Thresholds are strategy parameters to be chosen
from the distributions over real history, not facts to be guessed now.

MEASUREMENTS SURVIVE REJECTION. A window that failed one limit keeps
every number that was calculable, because "how close was it" is the
question the threshold research needs answered.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.observation.efficiency_ratio import efficiency_ratio


class EfficiencyMeasure(Enum):
    """Which efficiency ratio a run's qualification uses. Both are always
    recorded; this only chooses which one binds."""

    KAUFMAN = "KAUFMAN"
    """Close-to-close path efficiency, as used elsewhere in VO. The
    calibrated one."""
    BODY = "BODY"
    """|last close - first open| / sum|close - open|, per the CERR
    specification. Anchored on the window's opening price and blind to
    gaps between bars."""

    def __str__(self) -> str:
        return self.value


class ConsolidationQualification(Enum):
    CONSOLIDATION = "CONSOLIDATION"
    NOT_CONSOLIDATION = "NOT_CONSOLIDATION"
    UNMEASURABLE = "UNMEASURABLE"
    """A configured limit needed a value this window could not produce --
    an ATR-relative limit with no ATR, or an efficiency limit where the
    selected ratio is undefined. The limit was never tested, which is
    not the same as the window failing it."""
    UNCONFIGURED = "UNCONFIGURED"
    """No limit is set, so no definition of consolidation is in force.
    Deliberately not a pass: a detector that called every window
    consolidation because nobody had chosen thresholds yet would be
    worse than one that refuses to answer."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ConsolidationConfig:
    """Every limit is optional and unset by default. See the module
    docstring on why no number is guessed here."""

    window_bars: int = 20
    minimum_bars: int = 10
    max_range_atr: float | None = None
    max_net_move_atr: float | None = None
    max_efficiency_ratio: float | None = None
    efficiency_measure: EfficiencyMeasure = EfficiencyMeasure.KAUFMAN
    atr_period: int = 14

    def __post_init__(self) -> None:
        if self.window_bars < 2:
            raise ValueError(f"window_bars must be >= 2, got {self.window_bars}")
        if self.minimum_bars < 2:
            raise ValueError(f"minimum_bars must be >= 2, got {self.minimum_bars}")
        if self.minimum_bars > self.window_bars:
            raise ValueError(
                f"minimum_bars ({self.minimum_bars}) cannot exceed window_bars "
                f"({self.window_bars})"
            )

    @property
    def configured(self) -> bool:
        return (
            self.max_range_atr is not None
            or self.max_net_move_atr is not None
            or self.max_efficiency_ratio is not None
        )


@dataclass(frozen=True, slots=True)
class ConsolidationMeasurement:
    """What a window is, before anyone decides what it means."""

    start_index: int
    end_index: int
    start_time: datetime
    end_time: datetime
    bar_count: int

    range_high: float
    range_low: float
    range_points: float
    range_atr: float | None
    """None when ATR is unavailable -- never 0.0, which would read as a
    window of no height."""

    net_move_points: float
    """Signed: positive when the window closed above where it opened."""
    net_move_atr: float | None

    total_path_points: float
    """Sum of per-bar body movement -- the distance price actually
    travelled, against which net movement is judged."""

    kaufman_efficiency_ratio: float | None
    """Directional efficiency of the price PATH: |net close-to-close
    displacement| / sum|close-to-close movement|. None when history is
    too short. Never named plainly "efficiency_ratio" on a record that
    carries two of them -- a report column that could be either is a
    result nobody can interpret afterwards."""
    body_efficiency_ratio: float | None
    """The CERR specification's formula. None when its denominator is
    zero, i.e. every bar closed exactly at its open -- undefined, not
    zero."""

    mean_range_points: float
    mean_range_atr: float | None

    def efficiency(self, measure: EfficiencyMeasure) -> float | None:
        return (
            self.kaufman_efficiency_ratio
            if measure is EfficiencyMeasure.KAUFMAN
            else self.body_efficiency_ratio
        )


@dataclass(frozen=True, slots=True)
class ConsolidationVerdict:
    """The classification and why, alongside the numbers that produced
    it. Measurements are present whatever the verdict."""

    qualification: ConsolidationQualification
    reason: str | None
    measurement: ConsolidationMeasurement
    efficiency_measure: EfficiencyMeasure

    @property
    def is_consolidation(self) -> bool:
        return self.qualification is ConsolidationQualification.CONSOLIDATION


@dataclass(frozen=True, slots=True)
class ConsolidationEvent:
    """An immutable record of one qualified consolidation.

    range_high and range_low are GEOMETRY. They are not liquidity levels
    and must not be treated as such -- that judgement belongs to the
    Reference Level and Liquidity engines.
    """

    consolidation_id: str
    start_time: datetime
    end_time: datetime
    start_index: int
    end_index: int

    range_high: float
    range_low: float
    range_points: float
    range_atr: float | None

    net_move_points: float
    net_move_atr: float | None

    kaufman_efficiency_ratio: float | None
    body_efficiency_ratio: float | None
    efficiency_measure: EfficiencyMeasure
    """Which of the two ratios qualification used. Recorded on the event
    so a result can never be read against the wrong one. The YAML key
    when this reaches config is `consolidation_efficiency_measure`."""
    bar_count: int

    qualification: ConsolidationQualification

    @property
    def event_at_utc(self) -> datetime:
        return self.end_time

    @property
    def confirmation_at_utc(self) -> datetime:
        """The label of the last bar in the window, whose close completed
        the measurement. See Bar.close_time_utc for the convention."""
        return self.end_time

    def contains(self, price: float) -> bool:
        return self.range_low <= price <= self.range_high

    def escapes_above(self, price: float) -> bool:
        return price > self.range_high

    def escapes_below(self, price: float) -> bool:
        return price < self.range_low


def measure_consolidation(
    bars: Sequence[Bar],
    index: int,
    config: ConsolidationConfig,
    *,
    tick_size: float,
) -> ConsolidationMeasurement | None:
    """
    Measure the window of `config.window_bars` bars ending at and
    including `index`.

    None when fewer than `config.minimum_bars` exist up to `index` --
    never a partial-window figure, matching atr_ticks and
    efficiency_ratio. Reads nothing past `index`.
    """
    if index < 0 or index >= len(bars):
        return None

    start = max(0, index - config.window_bars + 1)
    window = bars[start : index + 1]
    if len(window) < config.minimum_bars:
        return None

    range_high = max(bar.high for bar in window)
    range_low = min(bar.low for bar in window)
    range_points = range_high - range_low

    net_move_points = window[-1].close - window[0].open
    total_path_points = sum(abs(bar.close - bar.open) for bar in window)

    body_er = (
        abs(net_move_points) / total_path_points
        if total_path_points > 0.0
        else None
    )

    atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
    atr_price = atr * tick_size if atr is not None else None
    scaled = atr_price if atr_price is not None and atr_price > 0 else None

    mean_range_points = sum(bar.high - bar.low for bar in window) / len(window)

    return ConsolidationMeasurement(
        start_index=start,
        end_index=index,
        start_time=window[0].open_time_utc,
        end_time=window[-1].open_time_utc,
        bar_count=len(window),
        range_high=range_high,
        range_low=range_low,
        range_points=range_points,
        range_atr=range_points / scaled if scaled is not None else None,
        net_move_points=net_move_points,
        net_move_atr=(
            abs(net_move_points) / scaled if scaled is not None else None
        ),
        total_path_points=total_path_points,
        kaufman_efficiency_ratio=efficiency_ratio(
            bars, index, period=len(window) - 1
        ),
        body_efficiency_ratio=body_er,
        mean_range_points=mean_range_points,
        mean_range_atr=(
            mean_range_points / scaled if scaled is not None else None
        ),
    )


def qualify_consolidation(
    measurement: ConsolidationMeasurement, config: ConsolidationConfig
) -> ConsolidationVerdict:
    """Apply whichever limits are configured. Unset limits are not
    applied; no limits at all is UNCONFIGURED, not a pass."""

    def verdict(
        qualification: ConsolidationQualification, reason: str | None
    ) -> ConsolidationVerdict:
        return ConsolidationVerdict(
            qualification=qualification,
            reason=reason,
            measurement=measurement,
            efficiency_measure=config.efficiency_measure,
        )

    if not config.configured:
        return verdict(
            ConsolidationQualification.UNCONFIGURED,
            "no consolidation limits are set; measurements only",
        )

    unmeasurable: list[str] = []
    failures: list[str] = []

    if config.max_range_atr is not None:
        if measurement.range_atr is None:
            unmeasurable.append("range limit set but ATR unavailable")
        elif measurement.range_atr > config.max_range_atr:
            failures.append(
                f"range {measurement.range_atr:.2f} ATR above "
                f"{config.max_range_atr:.2f}"
            )

    if config.max_net_move_atr is not None:
        if measurement.net_move_atr is None:
            unmeasurable.append("net move limit set but ATR unavailable")
        elif measurement.net_move_atr > config.max_net_move_atr:
            failures.append(
                f"net move {measurement.net_move_atr:.2f} ATR above "
                f"{config.max_net_move_atr:.2f}"
            )

    if config.max_efficiency_ratio is not None:
        value = measurement.efficiency(config.efficiency_measure)
        if value is None:
            unmeasurable.append(
                f"efficiency limit set but {config.efficiency_measure} ratio "
                "is undefined"
            )
        elif value > config.max_efficiency_ratio:
            failures.append(
                f"{config.efficiency_measure} efficiency {value:.3f} above "
                f"{config.max_efficiency_ratio:.3f}"
            )

    if unmeasurable:
        return verdict(
            ConsolidationQualification.UNMEASURABLE, "; ".join(unmeasurable)
        )
    if failures:
        return verdict(
            ConsolidationQualification.NOT_CONSOLIDATION, "; ".join(failures)
        )
    return verdict(ConsolidationQualification.CONSOLIDATION, None)


def detect_consolidation(
    bars: Sequence[Bar],
    index: int,
    config: ConsolidationConfig,
    *,
    tick_size: float,
) -> ConsolidationVerdict | None:
    """Measure and classify in one call. None only when the window
    itself cannot be measured (too little history)."""
    measurement = measure_consolidation(bars, index, config, tick_size=tick_size)
    if measurement is None:
        return None
    return qualify_consolidation(measurement, config)


def build_consolidation_event(verdict: ConsolidationVerdict) -> ConsolidationEvent:
    """Promote a qualified verdict to an immutable event.

    Raises on anything but CONSOLIDATION: an event means "this window IS
    a consolidation", and manufacturing one from a rejected or
    unconfigured verdict would let the CERR machine start a cycle from a
    window nobody classified.
    """
    if verdict.qualification is not ConsolidationQualification.CONSOLIDATION:
        raise ValueError(
            f"cannot build a consolidation event from {verdict.qualification}: "
            f"{verdict.reason}"
        )
    m = verdict.measurement
    return ConsolidationEvent(
        consolidation_id=f"CONS:{m.start_time.isoformat()}:{m.end_index}",
        start_time=m.start_time,
        end_time=m.end_time,
        start_index=m.start_index,
        end_index=m.end_index,
        range_high=m.range_high,
        range_low=m.range_low,
        range_points=m.range_points,
        range_atr=m.range_atr,
        net_move_points=m.net_move_points,
        net_move_atr=m.net_move_atr,
        kaufman_efficiency_ratio=m.kaufman_efficiency_ratio,
        body_efficiency_ratio=m.body_efficiency_ratio,
        efficiency_measure=verdict.efficiency_measure,
        bar_count=m.bar_count,
        qualification=verdict.qualification,
    )
