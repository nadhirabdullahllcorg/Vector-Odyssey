"""vo.research.markov_validation -- the checks that say whether a
transition matrix describes the market or only its own sample.

Same discipline as the Hurst calibration: the checks are exercised
against SYNTHETIC chains built with known properties, because a check
that has only ever seen real data has never been shown to detect the
thing it claims to detect."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import (
    OBJECT_TYPE_REGIME_TRANSITION,
    RegimeTransition,
    RegimeType,
)
from vo.research.markov_validation import (
    build_first_order_check,
    build_out_of_sample_check,
    build_stability_check,
    expected_holding_time,
    steady_state,
    wilson_interval_for_cell,
)
from vo.research.transitions import build_transition_matrix

_START = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100.n")


def _transition(frm: RegimeType, to: RegimeType, minute: int) -> RegimeTransition:
    when = _START + timedelta(minutes=minute)
    return RegimeTransition(
        object_type=OBJECT_TYPE_REGIME_TRANSITION,
        object_id=f"tr:{frm.name}->{to.name}:{minute}",
        observed_at=when,
        recorded_at=when,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        from_state=frm,
        to_state=to,
        evidence="synthetic",
    )


def _chain(transition_table: dict[RegimeType, list[tuple[RegimeType, float]]],
           length: int, seed: int = 0) -> list[RegimeTransition]:
    """Walk a known Markov chain, emitting the transitions it produced."""
    rng = random.Random(seed)
    state = next(iter(transition_table))
    out: list[RegimeTransition] = []
    for minute in range(length):
        destinations, weights = zip(*transition_table[state], strict=False)
        nxt = rng.choices(list(destinations), weights=list(weights), k=1)[0]
        out.append(_transition(state, nxt, minute))
        state = nxt
    return out


_C = RegimeType.CONSOLIDATION
_E = RegimeType.EXPANSION


# ── out-of-sample ─────────────────────────────────────────────────────────


def test_a_genuinely_markov_chain_beats_the_uniform_baseline() -> None:
    """A chain whose dynamics really are stable should survive being
    scored on data it was not built from."""
    table = {_C: [(_C, 0.9), (_E, 0.1)], _E: [(_E, 0.8), (_C, 0.2)]}
    check = build_out_of_sample_check(_chain(table, 1200, seed=1))

    assert check.beats_uniform is True
    assert check.test_transitions > 0


def test_a_chain_that_changes_its_dynamics_scores_worse_than_a_stable_one() -> None:
    """The check has to be able to FAIL something -- otherwise passing
    means nothing."""
    stable = _chain({_C: [(_C, 0.9), (_E, 0.1)], _E: [(_E, 0.9), (_C, 0.1)]}, 600, seed=2)

    # Second half behaves oppositely to the first.
    first = _chain({_C: [(_C, 0.95), (_E, 0.05)], _E: [(_E, 0.95), (_C, 0.05)]}, 300, seed=3)
    second = _chain({_C: [(_E, 0.95), (_C, 0.05)], _E: [(_C, 0.95), (_E, 0.05)]}, 300, seed=4)
    shifting = first + [
        _transition(t.from_state, t.to_state, 300 + i) for i, t in enumerate(second)
    ]

    assert (
        build_out_of_sample_check(shifting).model_log_loss
        > build_out_of_sample_check(stable).model_log_loss
    )


def test_the_split_is_chronological_not_random() -> None:
    """Shuffling would let the model learn from the future -- the same
    lookahead error G3 forbids everywhere else."""
    log = _chain({_C: [(_C, 0.8), (_E, 0.2)], _E: [(_E, 0.8), (_C, 0.2)]}, 100, seed=5)
    check = build_out_of_sample_check(log, train_fraction=0.7)

    assert check.train_transitions == 70
    assert check.test_transitions == 30


def test_too_short_a_log_is_refused_rather_than_split_meaninglessly() -> None:
    with pytest.raises(ValueError, match="at least 10 transitions"):
        build_out_of_sample_check(_chain({_C: [(_C, 1.0)]}, 5))


# ── first-order sufficiency ───────────────────────────────────────────────


def test_a_true_first_order_chain_is_found_sufficient() -> None:
    table = {_C: [(_C, 0.7), (_E, 0.3)], _E: [(_E, 0.7), (_C, 0.3)]}
    check = build_first_order_check(_chain(table, 3000, seed=6))

    assert check.pairs_tested > 0
    assert check.first_order_is_sufficient is True


def test_a_second_order_chain_is_detected() -> None:
    """Built so the next state depends on the PREVIOUS one too. If the
    check cannot catch this, it cannot catch anything."""
    rng = random.Random(7)
    out: list[RegimeTransition] = []
    previous, current = _C, _C
    for minute in range(3000):
        # After C->E the chain almost always returns to C; after E->E it
        # almost always continues. Same `current`, different futures.
        if current is _E and previous is _C:
            nxt = _C if rng.random() < 0.9 else _E
        elif current is _E:
            nxt = _E if rng.random() < 0.9 else _C
        else:
            nxt = _E if rng.random() < 0.5 else _C
        out.append(_transition(current, nxt, minute))
        previous, current = current, nxt

    check = build_first_order_check(out)

    assert check.first_order_is_sufficient is False
    assert check.worst_pair is not None


# ── stability ─────────────────────────────────────────────────────────────


def test_periods_drawn_from_one_chain_are_stable() -> None:
    table = {_C: [(_C, 0.8), (_E, 0.2)], _E: [(_E, 0.8), (_C, 0.2)]}
    periods = [_chain(table, 500, seed=s) for s in (10, 11, 12)]

    assert build_stability_check(periods).stable is True


def test_periods_with_different_dynamics_are_flagged_unstable() -> None:
    calm = _chain({_C: [(_C, 0.95), (_E, 0.05)], _E: [(_E, 0.95), (_C, 0.05)]}, 500, seed=13)
    wild = _chain({_C: [(_E, 0.9), (_C, 0.1)], _E: [(_C, 0.9), (_E, 0.1)]}, 500, seed=14)

    check = build_stability_check([calm, wild])

    assert check.stable is False
    assert check.worst_state is not None


# ── uncertainty and descriptives ──────────────────────────────────────────


def test_a_thin_row_gives_a_wide_interval_and_a_fat_row_a_narrow_one() -> None:
    """The point of the interval: 0.62 off 40 samples and 0.62 off 4,000
    are different claims."""
    table = {_C: [(_C, 0.6), (_E, 0.4)], _E: [(_C, 1.0)]}
    thin = build_transition_matrix(_chain(table, 60, seed=15))
    fat = build_transition_matrix(_chain(table, 4000, seed=15))

    thin_interval = wilson_interval_for_cell(thin, _C, _C)
    fat_interval = wilson_interval_for_cell(fat, _C, _C)

    assert thin_interval is not None and fat_interval is not None
    assert (thin_interval[1] - thin_interval[0]) > (fat_interval[1] - fat_interval[0])


def test_an_interval_stays_inside_zero_and_one() -> None:
    """Why Wilson and not the textbook normal interval: near 0 and 1 the
    normal one produces impossible bounds."""
    certain = build_transition_matrix(_chain({_C: [(_C, 1.0)]}, 50, seed=16))
    interval = wilson_interval_for_cell(certain, _C, _C)

    assert interval is not None
    assert 0.0 <= interval[0] <= interval[1] <= 1.0


def test_a_row_that_never_occurred_has_no_interval() -> None:
    matrix = build_transition_matrix(_chain({_C: [(_C, 1.0)]}, 20, seed=17))

    assert wilson_interval_for_cell(matrix, _E, _C) is None


def test_a_stickier_state_is_held_longer() -> None:
    sticky = build_transition_matrix(
        _chain({_C: [(_C, 0.9), (_E, 0.1)], _E: [(_C, 1.0)]}, 2000, seed=18)
    )
    jumpy = build_transition_matrix(
        _chain({_C: [(_C, 0.5), (_E, 0.5)], _E: [(_C, 1.0)]}, 2000, seed=18)
    )

    sticky_time = expected_holding_time(sticky, _C)
    jumpy_time = expected_holding_time(jumpy, _C)

    assert sticky_time is not None and jumpy_time is not None
    assert sticky_time > jumpy_time


def test_a_state_never_seen_to_leave_holds_forever_rather_than_rounding() -> None:
    matrix = build_transition_matrix(_chain({_C: [(_C, 1.0)]}, 50, seed=19))

    assert expected_holding_time(matrix, _C) == float("inf")


def test_steady_state_favours_the_state_that_is_harder_to_leave() -> None:
    table = {_C: [(_C, 0.95), (_E, 0.05)], _E: [(_E, 0.5), (_C, 0.5)]}
    distribution = steady_state(build_transition_matrix(_chain(table, 4000, seed=20)))

    assert distribution is not None
    assert sum(distribution.values()) == pytest.approx(1.0)
    assert distribution[_C] > distribution[_E]
