"""
Replay harness -- Phase 9. Closes D16, brings gate G3 live mechanically.

G3 says "no engine sees data after the current bar", and Phase 5's
CandleWindow already makes most of that structural: current/prev/last/
relation/separation all read `self.sequence.bars[<= self.index]` or raise,
with "no next, forward, or peek anywhere" (vo/market/sequence.py's own
docstring). But CandleWindow.sequence is a public field holding the WHOLE
BarSequence -- `separation()` needs that to read two arbitrary past
indices, and dataclass field privacy was never used anywhere else in this
codebase either. That means a probe that ignores CandleWindow's own bounded
methods and reaches into `window.sequence.bars[window.index + k]` directly
is not stopped by the type system. Nothing at the Python level can prevent
that without breaking `separation()`'s own implementation, so this phase
does not try to prevent it -- it detects it, empirically, which is what
"a deliberately forward-peeking probe fails under replay" (this phase's own
gate text) means in practice.

THE DETECTION TECHNIQUE: perturb the future, keep the past identical, and
require the past output not to change. Run the same probe (fresh instance)
twice across two sequences that agree on every bar at or before some
`cutoff` and disagree on every bar after it. If the probe's result at or
before `cutoff` differs between the two runs, the only way that could have
happened is that it read a bar after `cutoff` -- the past cannot be a
function of a future that changed. This is the same idea long-established
in walk-forward and leakage testing generally, applied here as a literal,
importable assertion rather than a spreadsheet exercise.

Phase 25 ("Backtest fill simulation -- extends P9 with spread, slippage,
commission") is expected to build directly on ReplayHarness; nothing here
assumes a specific engine shape, since none exists yet at Phase 9.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from vo.market.sequence import BarSequence, CandleWindow


@runtime_checkable
class ReplayProbe(Protocol):
    """
    Anything the harness can drive one bar at a time. Receives nothing but
    a CandleWindow bounded at the current step -- never the harness, never
    a raw BarSequence, never anything else the runtime itself has. What it
    does with that window is its own business; see the module docstring
    for why that cannot be made structurally airtight, and is checked
    empirically instead by `assert_no_lookahead`.
    """

    def on_bar(self, window: CandleWindow) -> object:
        """Called once per bar, strictly in order. Returns whatever this
        probe wants recorded for the step -- the harness never
        interprets it, only compares it for equality across runs."""
        ...


@dataclasses.dataclass(frozen=True)
class ReplayStep:
    """One probe result, tagged with the index it was produced at."""

    index: int
    result: object


class ReplayHarness:
    """Drives one probe across one BarSequence, exactly once, in order,
    handing it nothing but a CandleWindow bounded at each step."""

    def __init__(self, sequence: BarSequence) -> None:
        self._sequence = sequence

    def __len__(self) -> int:
        return len(self._sequence)

    def run(self, probe: ReplayProbe) -> tuple[ReplayStep, ...]:
        steps: list[ReplayStep] = []

        for index in range(len(self._sequence)):
            window = self._sequence.window_at(index)
            steps.append(ReplayStep(index=index, result=probe.on_bar(window)))

        return tuple(steps)


class LookaheadDetectedError(AssertionError):
    """
    Raised by assert_no_lookahead when a probe's result at or before the
    cutoff changed depending on data strictly after it -- proof the probe
    read a bar it should not have been able to see yet.
    """


def perturb_tail(sequence: BarSequence, *, after_index: int, shift: float) -> BarSequence:
    """
    A new BarSequence identical to `sequence` up to and including
    `after_index`; every bar strictly after it has its OHLC prices shifted
    by `shift` (open_time_utc, instrument_id, timeframe and volumes are
    untouched). Shifting every one of open/high/low/close by the same
    constant preserves Bar's own geometry invariants (high >= open/close
    >= low) exactly, so the perturbed bars remain individually valid; the
    unchanged timestamps keep BarSequence.append's ordering check
    satisfied identically to the original.
    """
    perturbed = BarSequence()

    for index, bar in enumerate(sequence.bars):
        if index <= after_index or shift == 0.0:
            perturbed = perturbed.append(bar)
            continue

        perturbed = perturbed.append(
            dataclasses.replace(
                bar,
                open=bar.open + shift,
                high=bar.high + shift,
                low=bar.low + shift,
                close=bar.close + shift,
            )
        )

    return perturbed


def assert_no_lookahead(
    sequence: BarSequence,
    probe_factory: Callable[[], ReplayProbe],
    *,
    cutoff: int,
    shift: float = 1000.0,
) -> None:
    """
    Runs a fresh probe against `sequence`, then a second fresh probe
    against a copy whose bars strictly after `cutoff` are price-shifted
    (see perturb_tail). Raises LookaheadDetectedError if any step at or
    before `cutoff` differs between the two runs.

    A fresh probe per run, via `probe_factory`, matters: a probe carrying
    its own internal state (a running total, say) must never continue from
    one run into the other -- that would manufacture a difference that has
    nothing to do with lookahead. Callers pass a zero-argument constructor,
    not an instance.
    """
    if not (0 <= cutoff < len(sequence)):
        raise ValueError(f"cutoff {cutoff} is out of range for a sequence of {len(sequence)} bars")

    honest_steps = ReplayHarness(sequence).run(probe_factory())

    perturbed_sequence = perturb_tail(sequence, after_index=cutoff, shift=shift)
    perturbed_steps = ReplayHarness(perturbed_sequence).run(probe_factory())

    for index in range(cutoff + 1):
        honest_result = honest_steps[index].result
        perturbed_result = perturbed_steps[index].result

        if honest_result != perturbed_result:
            raise LookaheadDetectedError(
                f"step {index} (<= cutoff {cutoff}) produced {honest_result!r} "
                f"against the real future and {perturbed_result!r} against a "
                "perturbed one -- this probe read data from after its "
                "current bar"
            )
