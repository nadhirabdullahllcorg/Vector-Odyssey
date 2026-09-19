"""
Progress against the account's high-water mark -- REPORTING ONLY.

WHY THIS MODULE EXISTS, AND WHY IT IS QUARANTINED HERE. After a real
drawdown it is natural to want the EA to "target break even". Measuring
the gap is legitimate and useful; letting that gap influence what gets
traded or how big is the single most reliable way a drawdown becomes a
larger one, and this project's own strategy spec already bans every
mechanical form of it (section 20: no martingale, no loss-based lot
escalation, no averaging down; section 18: profit targets are account
control, never a signal-engine input).

So the distinction this module enforces is: REPORTING IS NOT TARGETING.
BenchmarkProgress describes where the account stands relative to its
prior peak. Nothing here returns a size, a multiplier, a "needed" R, or
any other quantity that could be multiplied into a position -- and that
absence is deliberate, not an oversight to be helpfully filled in later.

THE WALL IS MECHANICAL, NOT A PROMISE. vo.telemetry is the top layer
(13). vo.valco (6), vo.signals (7), vo.allocation (8), vo.risk (9) and
vo.compliance (10) all sit below it, and
tests/unit/test_architecture.py::test_modules_do_not_import_higher_layers
already forbids a lower layer importing a higher one. So no strategy,
allocator, risk manager or compliance path can import this module even
by accident -- the build fails first. Gate G16
(test_benchmark_progress_never_reaches_the_sizing_path) names that
guarantee explicitly rather than leaving it as a side effect of the
numbering.

The high-water mark ITSELF is a different thing and lives elsewhere, in
ComplianceConfig.high_water_mark_currency, where it serves as a risk
anchor: it makes drawdown limits stricter by measuring from a higher
peak. That is the opposite of a recovery target, and the two must not be
confused because they happen to reference the same number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vo.market.account import AccountState


@dataclass(frozen=True, slots=True)
class BenchmarkProgress:
    """Where the account stands against its prior peak, at one instant.

    Every field is a plain observation. There is deliberately no field
    expressing what "should" be done about the gap -- no required return,
    no suggested size, no trades-to-recovery. Those are exactly the
    numbers that turn a report into a targeting signal.
    """

    observed_at_utc: datetime
    high_water_mark: float
    current_equity: float

    @property
    def distance_to_high_water_mark(self) -> float:
        """Account currency still missing. 0.0 at or above the mark --
        never negative, since "ahead of the peak" is not a shortfall."""
        return max(0.0, self.high_water_mark - self.current_equity)

    @property
    def fraction_of_high_water_mark(self) -> float:
        """Current equity as a fraction of the peak. 1.0 is recovered."""
        return self.current_equity / self.high_water_mark

    @property
    def recovered(self) -> bool:
        return self.current_equity >= self.high_water_mark


def measure_benchmark_progress(
    account: AccountState, *, high_water_mark: float, observed_at_utc: datetime
) -> BenchmarkProgress:
    """One reading. `observed_at_utc` is given, not fetched, matching the
    no-internal-clock-read discipline used across this codebase."""
    if high_water_mark <= 0:
        raise ValueError(f"high_water_mark must be > 0, got {high_water_mark}")

    return BenchmarkProgress(
        observed_at_utc=observed_at_utc,
        high_water_mark=high_water_mark,
        current_equity=account.equity,
    )


def high_water_mark_from_drawdown(starting_equity: float, drawdown: float) -> float:
    """Resolve "I am down X from my peak" into the absolute peak figure
    BenchmarkProgress needs.

    Called ONCE, at startup, against the equity observed then -- not per
    snapshot. Recomputing it as equity moves would make the mark chase
    the account and the gap never close, which would be a report that can
    never deliver good news: the exact shape of a treadmill.
    """
    if starting_equity <= 0:
        raise ValueError(f"starting_equity must be > 0, got {starting_equity}")
    if drawdown < 0:
        raise ValueError(f"drawdown cannot be negative, got {drawdown}")
    return starting_equity + drawdown


def render_progress_line(progress: BenchmarkProgress) -> str:
    """A single human-readable line for a log or dashboard panel. States
    the gap plainly and stops -- no encouragement, no projection, no
    implied plan. A reader deserves the number, not a nudge."""
    if progress.recovered:
        return (
            f"at or above the high-water mark "
            f"({progress.current_equity:.2f} vs {progress.high_water_mark:.2f})"
        )
    return (
        f"{progress.distance_to_high_water_mark:.2f} below the high-water mark "
        f"({progress.current_equity:.2f} vs {progress.high_water_mark:.2f}, "
        f"{progress.fraction_of_high_water_mark:.1%})"
    )
