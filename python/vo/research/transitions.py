"""
Shared transition-matrix and nearest-sample lookup -- vo.research (layer 5).

WHY THIS EXISTS. `TransitionMatrix`/`build_transition_matrix` were built
in `vo.telemetry.regime_validation` (v33, Phase 13a's Validation Report
v1) as the UNCONDITIONAL from/to grid over a run's full transitions log.
Phase 17's conditional Markov study (`vo.research.markov_report`) needs
the exact same matrix-building logic applied repeatedly -- once per
condition bucket (by session, by Hurst/ER tercile, by preceding-pullback-
duration quartile), plus once more for the unconditional baseline every
conditional report compares against. Rather than duplicate a real
formula a second time, it moved here, and `regime_validation.py` was
updated in the same change to import it instead of defining its own copy
-- a behavior-preserving refactor, not a second implementation, matching
this project's own established pattern for `vo.research.statistics` and
`vo.research.regime_windows`.

`nearest_sample_before` is the second piece: hurst_report.py's
`build_hurst_preceding_transitions` and efficiency_ratio_report.py's
`build_er_preceding_transitions` each independently implemented "the
nearest (timestamp, value) sample at or before a given instant, honoring
a maximum gap" via the same bisect lookup. `markov_report.py` needs the
identical lookup a third time, to bucket each transition by the Hurst/ER
value that preceded it -- a third occurrence of the same real logic is
exactly the case this project's duplication convention reserves for
extraction, not the tiny-helper exception. Both report modules were
updated to call this shared version instead of their own inline copies.

LAYERING. vo.research is layer 5; vo.telemetry (regime_validation,
regime_feed, regime_report) is layer 7 -- a HIGHER layer, so this module
may not import it (see tests/unit/test_architecture.py's LAYERS dict).
`TransitionMatrix`/`build_transition_matrix` take a plain
Sequence[RegimeTransition] (vo.observation.regime, layer 3) and know
nothing about vo.telemetry; regime_validation.py (layer 7) imports
UPWARD from here, which is the direction layering allows.
"""

from __future__ import annotations

import bisect
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.observation.regime import RegimeTransition, RegimeType


@dataclass(frozen=True)
class TransitionMatrix:
    """The full from/to grid (all five regimes, both axes), not just a
    "most frequent first" flat list -- including the zero cells, since
    "this never happens" is itself part of validating the Month 1
    transition-structure constraint (see vo-phase-plan.md's §13-notes:
    CONSOLIDATION should never transition directly into RETRACEMENT/
    REVERSAL, for example -- a matrix makes that either visibly true or
    visibly violated, a flat list does not)."""

    counts: dict[tuple[RegimeType, RegimeType], int]
    row_totals: dict[RegimeType, int]

    def probability(self, frm: RegimeType, to: RegimeType) -> float | None:
        """P(next state = to | current state = frm), or None if `frm`
        never occurred as a transition source in this (possibly
        conditioned) set (no denominator)."""
        total = self.row_totals.get(frm, 0)
        if not total:
            return None
        return self.counts.get((frm, to), 0) / total


def build_transition_matrix(transitions_log: Sequence[RegimeTransition]) -> TransitionMatrix:
    counts: dict[tuple[RegimeType, RegimeType], int] = Counter(
        (t.from_state, t.to_state) for t in transitions_log
    )
    row_totals: dict[RegimeType, int] = Counter()
    for (frm, _to), n in counts.items():
        row_totals[frm] += n
    return TransitionMatrix(counts=dict(counts), row_totals=dict(row_totals))


def nearest_sample_before(
    samples: Sequence[tuple[datetime, float]], when: datetime, *, max_gap_minutes: float
) -> tuple[datetime, float] | None:
    """The nearest (timestamp, value) pair in `samples` (assumed already
    chronological) at or before `when` -- no lookahead across `when`
    itself. None if there is no earlier sample at all, or if the nearest
    one is more than `max_gap_minutes` away (a real data gap, e.g. a
    weekend, rather than a stale match)."""
    sample_times = [t for t, _v in samples]
    idx = bisect.bisect_right(sample_times, when) - 1
    if idx < 0:
        return None
    at, value = samples[idx]
    gap_minutes = (when - at).total_seconds() / 60.0
    if gap_minutes < 0 or gap_minutes > max_gap_minutes:
        return None
    return (at, value)
