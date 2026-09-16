"""
ReplayHarness / assert_no_lookahead (Phase 9) -- gate G3, proven mechanically.

No real engine exists yet to check (Phase 10's VO_EA v1 and everything
after it), so this proves the detector itself works with two synthetic
probes, the same pattern test_architecture.py's
test_the_hypothesis_checker_actually_catches_a_violation already uses for
G2: an honest probe that must pass, and a deliberately forward-peeking one
that must not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.core.replay import (
    LookaheadDetectedError,
    ReplayHarness,
    assert_no_lookahead,
    perturb_tail,
)
from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.sequence import BarSequence, CandleWindow
from vo.market.timeframe import Timeframe

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
_T0 = datetime(2026, 9, 10, 7, 0, tzinfo=UTC)


def _bar(minute: int, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0 + timedelta(minutes=minute),
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        tick_volume=10,
        real_volume=0,
    )


def _sequence(closes: list[float]) -> BarSequence:
    sequence = BarSequence()
    for minute, close in enumerate(closes):
        sequence = sequence.append(_bar(minute, close))
    return sequence


_CLOSES = [100.0, 101.0, 99.0, 103.0, 102.0, 98.0, 107.0, 105.0]


class _HonestMovingAverageProbe:
    """Only ever reads through CandleWindow's own bounded methods."""

    def on_bar(self, window: CandleWindow) -> object:
        closes = [bar.close for bar in window.last(3)]
        return sum(closes) / len(closes)


class _CheatingProbe:
    """Deliberately bypasses CandleWindow's bounded methods and reaches
    straight into the public `.sequence` field -- the exact bypass the
    module docstring says the type system cannot prevent."""

    def on_bar(self, window: CandleWindow) -> object:
        future_index = window.index + 2
        bars = window.sequence.bars
        if future_index < len(bars):
            return bars[future_index].close
        return window.current.close


# ── ReplayHarness ────────────────────────────────────────────────────────


def test_harness_runs_the_probe_once_per_bar_in_order() -> None:
    sequence = _sequence(_CLOSES)
    steps = ReplayHarness(sequence).run(_HonestMovingAverageProbe())

    assert [step.index for step in steps] == list(range(len(_CLOSES)))


def test_harness_length_matches_the_sequence() -> None:
    sequence = _sequence(_CLOSES)
    assert len(ReplayHarness(sequence)) == len(_CLOSES)


def test_two_honest_runs_of_the_same_sequence_are_identical() -> None:
    """Baseline determinism: no perturbation at all, same probe class,
    fresh instances -- results must match exactly."""
    sequence = _sequence(_CLOSES)
    first = ReplayHarness(sequence).run(_HonestMovingAverageProbe())
    second = ReplayHarness(sequence).run(_HonestMovingAverageProbe())

    assert first == second


# ── perturb_tail ─────────────────────────────────────────────────────────


def test_perturb_tail_leaves_the_head_bit_for_bit_identical() -> None:
    sequence = _sequence(_CLOSES)
    perturbed = perturb_tail(sequence, after_index=3, shift=1000.0)

    assert perturbed.bars[: 3 + 1] == sequence.bars[: 3 + 1]


def test_perturb_tail_shifts_every_bar_after_the_cutoff() -> None:
    sequence = _sequence(_CLOSES)
    perturbed = perturb_tail(sequence, after_index=3, shift=1000.0)

    for index in range(4, len(sequence)):
        assert perturbed.bars[index].close == pytest.approx(sequence.bars[index].close + 1000.0)


def test_perturb_tail_preserves_bar_geometry_invariants() -> None:
    """A shifted bar must still be constructible at all -- Bar.__post_init__
    would reject a broken high/low/open/close relationship."""
    sequence = _sequence(_CLOSES)
    perturbed = perturb_tail(sequence, after_index=0, shift=-5000.0)

    for bar in perturbed.bars:
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high


def test_perturb_tail_keeps_ordering_and_identity_intact() -> None:
    """Same timestamps/instrument/timeframe throughout -- perturbation
    must not itself trip BarSequence's own append invariants."""
    sequence = _sequence(_CLOSES)
    perturbed = perturb_tail(sequence, after_index=2, shift=250.0)

    assert len(perturbed) == len(sequence)
    for original, shifted in zip(sequence.bars, perturbed.bars, strict=True):
        assert shifted.open_time_utc == original.open_time_utc
        assert shifted.instrument_id == original.instrument_id
        assert shifted.timeframe == original.timeframe


# ── assert_no_lookahead: the actual G3 gate ─────────────────────────────


def test_an_honest_probe_passes_the_no_lookahead_check() -> None:
    sequence = _sequence(_CLOSES)
    assert_no_lookahead(sequence, _HonestMovingAverageProbe, cutoff=4)


def test_a_deliberately_forward_peeking_probe_fails_under_replay() -> None:
    """This is Phase 9's own gate text, made literal: the cheating probe
    must be caught, not silently tolerated."""
    sequence = _sequence(_CLOSES)

    with pytest.raises(LookaheadDetectedError, match="read data from after its current bar"):
        assert_no_lookahead(sequence, _CheatingProbe, cutoff=4)


def test_cheating_probe_is_fine_at_the_very_last_bar() -> None:
    """When cutoff is the sequence's final index, there is no future left
    to peek at (future_index is always out of range), so even the cheating
    probe cannot be caught here -- this documents the technique's real
    limit rather than overclaiming it, and confirms the detector doesn't
    false-positive when there's genuinely nothing to leak."""
    sequence = _sequence(_CLOSES)
    last_index = len(sequence) - 1

    assert_no_lookahead(sequence, _CheatingProbe, cutoff=last_index)


def test_out_of_range_cutoff_is_rejected() -> None:
    sequence = _sequence(_CLOSES)

    with pytest.raises(ValueError, match="out of range"):
        assert_no_lookahead(sequence, _HonestMovingAverageProbe, cutoff=len(sequence))
