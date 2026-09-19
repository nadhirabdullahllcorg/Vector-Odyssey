"""
Does VO's transition matrix mean anything?

The matrix itself has existed since v33 and the conditional study since
v35. What has never existed is any check that its numbers survive
contact with data they were not built from. A transition matrix ALWAYS
produces confident-looking probabilities -- it is a tally, and a tally
cannot fail. That is exactly why it needs testing from outside: nothing
about building one tells you whether it describes the market or only the
sample.

Four checks, each answering a question that can invalidate the matrix in
a different way.

1. OUT-OF-SAMPLE (build_out_of_sample_check). Build the matrix on the
   first part of history, score it on the rest. If the probabilities
   only describe the past, this is where that shows. Scored by log
   loss against the matrix's own predictions, compared with two
   baselines: a uniform guess, and the majority-class rate. Beating
   uniform is easy and means little; beating the majority baseline is
   the bar that matters, and it is the one most "predictive" findings
   quietly fail.

2. FIRST-ORDER SUFFICIENCY (build_first_order_check). The matrix's whole
   premise is that where you go next depends only on where you ARE, not
   on how you got here. That is an assumption, and nobody had tested it.
   This compares P(next | current) against P(next | previous, current).
   If knowing the previous state materially changes the distribution,
   the first-order matrix is a misleading average over situations that
   behave differently.

3. TEMPORAL STABILITY (build_stability_check). The validation report
   slices REGIMES by year; it never asks whether the transition
   probabilities themselves hold still. A matrix averaged across a
   structural break describes a market that never existed. This
   compares per-period matrices against the pooled one.

4. UNCERTAINTY (wilson_interval_for_cell). A probability from 40
   samples and the same probability from 4,000 are different claims,
   and a bare "0.62" hides which one you have. Existing reports flag
   thin rows, which is binary; an interval says how thin.

Also here because it falls straight out of the matrix and is directly
interpretable: expected_holding_time (how long a state persists) and
steady_state (the long-run share of time in each state). Both are
descriptive, neither is a forecast.

NONE OF THIS PROMOTES MARKOV TO A DECISION. Gate G2 is untouched: this
is read-only research analysis several layers from any trade, and a
matrix that passes every check here is still evidence, not a signal.
Passing these is the MINIMUM for the numbers to be discussable at all,
not a licence to act on them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from vo.observation.regime import RegimeTransition, RegimeType
from vo.research.transitions import TransitionMatrix, build_transition_matrix

# Probability floor used when scoring. A matrix that assigns zero to an
# outcome that then occurs would score infinitely badly, which says more
# about the sample than the model -- an unobserved transition is "not
# seen yet", not "impossible".
_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class OutOfSampleCheck:
    """How the matrix scored on data it was not built from.

    Lower log loss is better. `beats_majority` is the one that matters:
    a model that cannot outperform always-guessing-the-commonest-outcome
    has not demonstrated anything, whatever its accuracy looks like.
    """

    train_transitions: int
    test_transitions: int
    model_log_loss: float
    uniform_log_loss: float
    majority_log_loss: float

    @property
    def beats_uniform(self) -> bool:
        return self.model_log_loss < self.uniform_log_loss

    @property
    def beats_majority(self) -> bool:
        return self.model_log_loss < self.majority_log_loss


def build_out_of_sample_check(
    transitions_log: Sequence[RegimeTransition], *, train_fraction: float = 0.7
) -> OutOfSampleCheck:
    """Chronological split -- never random. Shuffling would let the model
    learn from the future to predict the past, which is the same
    lookahead error G3 forbids everywhere else in this codebase."""
    if not (0.0 < train_fraction < 1.0):
        raise ValueError(f"train_fraction must be in (0, 1), got {train_fraction}")
    if len(transitions_log) < 10:
        raise ValueError(
            f"need at least 10 transitions to split meaningfully, got {len(transitions_log)}"
        )

    ordered = sorted(transitions_log, key=lambda t: t.observed_at)
    cut = int(len(ordered) * train_fraction)
    train, test = ordered[:cut], ordered[cut:]
    if not train or not test:
        raise ValueError("split produced an empty side")

    matrix = build_transition_matrix(train)

    # Majority baseline: the single commonest destination in training,
    # predicted every time regardless of the current state.
    destination_counts: dict[RegimeType, int] = {}
    for transition in train:
        destination_counts[transition.to_state] = (
            destination_counts.get(transition.to_state, 0) + 1
        )
    majority_rate = max(destination_counts.values()) / len(train)

    observed_states = {t.to_state for t in ordered} | {t.from_state for t in ordered}
    uniform_probability = 1.0 / max(1, len(observed_states))

    model_loss = 0.0
    for transition in test:
        probability = matrix.probability(transition.from_state, transition.to_state)
        model_loss -= math.log(max(probability or 0.0, _EPSILON))

    return OutOfSampleCheck(
        train_transitions=len(train),
        test_transitions=len(test),
        model_log_loss=model_loss / len(test),
        uniform_log_loss=-math.log(uniform_probability),
        majority_log_loss=-math.log(max(majority_rate, _EPSILON)),
    )


@dataclass(frozen=True, slots=True)
class FirstOrderCheck:
    """Whether the previous state changes what comes next.

    `max_divergence` is the largest gap, across all (previous, current)
    pairs with enough samples, between the second-order distribution and
    the first-order one it is supposed to collapse into. Measured as
    total variation distance: 0.0 means identical, 1.0 means completely
    different.
    """

    pairs_tested: int
    max_divergence: float
    worst_pair: tuple[RegimeType, RegimeType] | None

    @property
    def first_order_is_sufficient(self) -> bool:
        """A divergence under 0.20 means knowing the previous state moves
        the next-state distribution by less than a fifth of its mass --
        small enough that the first-order matrix is a fair summary. The
        threshold is a judgement, stated here rather than hidden."""
        return self.max_divergence < 0.20


def build_first_order_check(
    transitions_log: Sequence[RegimeTransition], *, min_samples: int = 30
) -> FirstOrderCheck:
    """Compare P(next | current) with P(next | previous, current).

    Pairs seen fewer than `min_samples` times are skipped: a divergence
    measured off five observations is noise, and letting it set
    max_divergence would fail the check for the wrong reason.
    """
    ordered = sorted(transitions_log, key=lambda t: t.observed_at)
    first_order = build_transition_matrix(ordered)

    # Second-order tally: (previous from_state, current from_state) -> next.
    second: dict[tuple[RegimeType, RegimeType], dict[RegimeType, int]] = {}
    for earlier, later in pairwise(ordered):
        key = (earlier.from_state, later.from_state)
        bucket = second.setdefault(key, {})
        bucket[later.to_state] = bucket.get(later.to_state, 0) + 1

    pairs_tested = 0
    max_divergence = 0.0
    worst: tuple[RegimeType, RegimeType] | None = None

    for (previous, current), destinations in second.items():
        total = sum(destinations.values())
        if total < min_samples:
            continue
        pairs_tested += 1

        divergence = 0.0
        for state in RegimeType:
            second_order_probability = destinations.get(state, 0) / total
            first_order_probability = first_order.probability(current, state) or 0.0
            divergence += abs(second_order_probability - first_order_probability)
        divergence /= 2.0  # total variation distance

        if divergence > max_divergence:
            max_divergence = divergence
            worst = (previous, current)

    return FirstOrderCheck(
        pairs_tested=pairs_tested, max_divergence=max_divergence, worst_pair=worst
    )


@dataclass(frozen=True, slots=True)
class StabilityCheck:
    """Whether the matrix holds still across periods. `max_divergence` is
    the largest total variation distance between any period's row and the
    pooled row for the same state."""

    periods_tested: int
    max_divergence: float
    worst_state: RegimeType | None

    @property
    def stable(self) -> bool:
        return self.max_divergence < 0.20


def build_stability_check(
    periods: Sequence[Sequence[RegimeTransition]], *, min_samples: int = 30
) -> StabilityCheck:
    """`periods` is the transitions log already split (by year, quarter,
    whatever the caller finds meaningful) -- this module does not impose
    a calendar."""
    pooled = build_transition_matrix([t for period in periods for t in period])

    periods_tested = 0
    max_divergence = 0.0
    worst: RegimeType | None = None

    for period in periods:
        if len(period) < min_samples:
            continue
        periods_tested += 1
        matrix = build_transition_matrix(period)

        for state in RegimeType:
            if matrix.row_totals.get(state, 0) < min_samples:
                continue
            divergence = 0.0
            for destination in RegimeType:
                period_probability = matrix.probability(state, destination) or 0.0
                pooled_probability = pooled.probability(state, destination) or 0.0
                divergence += abs(period_probability - pooled_probability)
            divergence /= 2.0
            if divergence > max_divergence:
                max_divergence = divergence
                worst = state

    return StabilityCheck(
        periods_tested=periods_tested, max_divergence=max_divergence, worst_state=worst
    )


def wilson_interval_for_cell(
    matrix: TransitionMatrix, frm: RegimeType, to: RegimeType, *, z: float = 1.96
) -> tuple[float, float] | None:
    """95% Wilson score interval around one cell's probability -- how
    much of that number is real and how much is sample size. None when
    the row never occurred.

    Wilson rather than the textbook normal interval because transition
    probabilities sit near 0 and 1 often, where the normal interval
    produces bounds outside [0, 1] and quietly embarrasses itself."""
    total = matrix.row_totals.get(frm, 0)
    if not total:
        return None

    successes = matrix.counts.get((frm, to), 0)
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def expected_holding_time(matrix: TransitionMatrix, state: RegimeType) -> float | None:
    """Expected number of transitions spent in `state` before leaving it,
    under the matrix's own self-transition probability. None when the
    state never occurred; inf when it is never observed to leave, which
    is a real thing a tally can say and should not be rounded away."""
    self_probability = matrix.probability(state, state)
    if self_probability is None:
        return None
    if self_probability >= 1.0:
        return float("inf")
    return 1.0 / (1.0 - self_probability)


def steady_state(
    matrix: TransitionMatrix, *, iterations: int = 500, tolerance: float = 1e-9
) -> dict[RegimeType, float] | None:
    """Long-run share of time in each state, by power iteration.

    Descriptive, not a forecast: it says what the tallied dynamics imply
    about time spent, IF those dynamics hold -- which is precisely what
    the out-of-sample and stability checks above exist to question.
    Returns None when any state has no outgoing row, since the chain is
    then not fully specified and an answer would be invented.
    """
    states = [s for s in RegimeType if matrix.row_totals.get(s, 0) > 0]
    if not states:
        return None
    if any(matrix.row_totals.get(s, 0) == 0 for s in states):
        return None

    distribution = {state: 1.0 / len(states) for state in states}

    for _ in range(iterations):
        updated = {state: 0.0 for state in states}
        for frm in states:
            mass = distribution[frm]
            if mass == 0.0:
                continue
            for to in states:
                probability = matrix.probability(frm, to) or 0.0
                updated[to] += mass * probability

        total = sum(updated.values())
        if total == 0.0:
            return None
        updated = {state: value / total for state, value in updated.items()}

        shift = sum(abs(updated[state] - distribution[state]) for state in states)
        distribution = updated
        if shift < tolerance:
            break

    return distribution
