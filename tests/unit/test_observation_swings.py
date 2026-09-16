"""
SwingEngine / SwingPoint -- Phase 11, gate G6.

Covers the agreed hybrid algorithm (K-bar structural confirmation AND an
independent ATR-scaled minimum reversal distance, both required), the
CONFIRMED -> BROKEN v1.0 lifecycle, the `active()` query, SwingPoint's own
validation, and -- the flagship check -- that the engine genuinely has no
lookahead (G3), proven the same empirical way Phase 9 built its harness
for: perturb the future, require the past unchanged
(vo.core.replay.assert_no_lookahead).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from vo.core.replay import assert_no_lookahead
from vo.interfaces import CanonicalRecordError
from vo.market import Bar, BarSequence, InstrumentId, Timeframe
from vo.observation.swings import (
    OBJECT_TYPE_SWING,
    SwingEngine,
    SwingLevel,
    SwingPoint,
    SwingStatus,
    SwingType,
)

INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
T0 = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)


def _bar(minute: int, *, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=T0 + timedelta(minutes=minute),
        open=close,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
    )


def _sequence(bars: list[Bar]) -> BarSequence:
    sequence = BarSequence()
    for bar in bars:
        sequence = sequence.append(bar)
    return sequence


def _engine() -> SwingEngine:
    return SwingEngine(
        level=SwingLevel.INTERNAL, k=2, atr_period=1, atr_multiplier=1.0, tick_size=1.0
    )


def _high_swing_bars() -> list[Bar]:
    """A clean, structurally confirmed (K=2) high at idx2 whose reversal
    (13 ticks against an ATR of 11) clears an atr_multiplier=1.0 filter."""
    return [
        _bar(0, high=100, low=99, close=99),
        _bar(1, high=100, low=99, close=99),
        _bar(2, high=110, low=99, close=100),  # pivot: TR/ATR = 11
        _bar(3, high=100, low=97, close=98),
        _bar(4, high=100, low=97, close=98),
    ]


def _run(engine: SwingEngine, sequence: BarSequence) -> list[tuple[SwingPoint, ...]]:
    return [engine.on_bar(sequence.window_at(i)) for i in range(len(sequence))]


# ── hybrid detection: both filters required ─────────────────────────────


def test_structurally_confirmed_and_atr_significant_reversal_is_confirmed():
    bars = _high_swing_bars()
    sequence = _sequence(bars)
    engine = _engine()

    steps = _run(engine, sequence)
    confirmed = [event for step in steps for event in step]

    assert len(confirmed) == 1
    swing = confirmed[0]
    assert swing.object_type == OBJECT_TYPE_SWING
    assert swing.level is SwingLevel.INTERNAL
    assert swing.swing_type is SwingType.HIGH
    assert swing.status is SwingStatus.CONFIRMED
    assert swing.price == 110
    assert swing.reversal_ticks == 13
    assert swing.atr_ticks_at_pivot == 11
    assert swing.reversal_extreme_price == 97
    assert swing.reversal_extreme_bar_id == bars[3].bar_id  # first tied low wins, deterministically
    assert swing.pivot_bar_id == bars[2].bar_id
    assert swing.confirmed_at_bar_id == bars[4].bar_id
    assert swing.observed_at == bars[2].open_time_utc
    assert swing.recorded_at == bars[4].open_time_utc
    # emitted exactly at the confirming step, index 4 -- not before.
    assert steps[4] == (swing,)
    assert all(step == () for i, step in enumerate(steps) if i != 4)


def test_structurally_confirmed_but_atr_insignificant_reversal_is_not_a_swing():
    """Same K-bar fractal shape, but the reversal (4 ticks) does not clear
    the ATR filter (11) -- proves the ATR filter is a real, independent
    gate, not a relabeling of K."""
    bars = [
        _bar(0, high=100, low=99, close=99),
        _bar(1, high=100, low=99, close=99),
        _bar(2, high=110, low=99, close=100),  # same pivot, ATR=11
        _bar(3, high=108, low=106, close=107),
        _bar(4, high=108, low=107, close=107),
    ]
    sequence = _sequence(bars)
    engine = _engine()

    steps = _run(engine, sequence)
    assert all(step == () for step in steps)


def test_active_query_reflects_the_confirmed_swing():
    engine = _engine()
    sequence = _sequence(_high_swing_bars())

    assert engine.active(SwingType.HIGH) is None
    _run(engine, sequence)

    active = engine.active(SwingType.HIGH)
    assert active is not None
    assert active.status is SwingStatus.CONFIRMED
    assert engine.active(SwingType.LOW) is None


# ── BROKEN lifecycle ─────────────────────────────────────────────────────


def test_a_later_bar_trading_beyond_the_swing_breaks_it():
    bars = [
        *_high_swing_bars(),
        _bar(5, high=115, low=100, close=112),  # trades above the 110 swing
    ]
    sequence = _sequence(bars)
    engine = _engine()

    steps = _run(engine, sequence)
    confirmed = steps[4][0]

    assert steps[5] == (
        SwingPoint(
            object_type=OBJECT_TYPE_SWING,
            object_id=f"{confirmed.object_id}#BROKEN@{bars[5].open_time_utc.isoformat()}",
            observed_at=bars[5].open_time_utc,
            recorded_at=bars[5].open_time_utc,
            methodology_version=confirmed.methodology_version,
            supersedes=confirmed.object_id,
            instrument_id=INSTRUMENT,
            timeframe=Timeframe.M1,
            level=SwingLevel.INTERNAL,
            swing_type=SwingType.HIGH,
            status=SwingStatus.BROKEN,
            price=110,
            pivot_bar_id=confirmed.pivot_bar_id,
            confirmed_at_bar_id=bars[5].bar_id,
        ),
    )

    assert engine.active(SwingType.HIGH) is None
    assert engine.log.is_current(confirmed.object_id) is False
    assert engine.log.is_current(steps[5][0].object_id) is True


# ── SwingPoint's own validation ──────────────────────────────────────────


def _kwargs(**overrides):
    base = dict(
        object_type=OBJECT_TYPE_SWING,
        object_id="X",
        observed_at=T0,
        recorded_at=T0,
        methodology_version=1,
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=110.0,
        pivot_bar_id="pivot",
        confirmed_at_bar_id="confirm",
        reversal_ticks=13,
        atr_ticks_at_pivot=11,
        reversal_extreme_price=97.0,
        reversal_extreme_bar_id="extreme",
    )
    base.update(overrides)
    return base


def test_confirmed_swing_point_requires_reversal_and_atr():
    with pytest.raises(CanonicalRecordError):
        SwingPoint(**_kwargs(reversal_ticks=None))


def test_confirmed_swing_point_requires_reversal_extreme_price_and_bar_id():
    with pytest.raises(CanonicalRecordError):
        SwingPoint(**_kwargs(reversal_extreme_price=None))
    with pytest.raises(CanonicalRecordError):
        SwingPoint(**_kwargs(reversal_extreme_bar_id=None))


def test_broken_swing_point_requires_supersedes():
    with pytest.raises(CanonicalRecordError):
        SwingPoint(
            **_kwargs(status=SwingStatus.BROKEN, reversal_ticks=None, atr_ticks_at_pivot=None)
        )


def test_swing_point_rejects_wrong_object_type():
    with pytest.raises(CanonicalRecordError):
        SwingPoint(**_kwargs(object_type="NOT_A_SWING"))


# ── G3: no lookahead, driven through the real Phase 9 harness ───────────


def _zigzag_bars(n: int, *, seed: int) -> BarSequence:
    rng = random.Random(seed)
    sequence = BarSequence()
    price = 100.0

    for i in range(n):
        price = max(50.0, price + rng.uniform(-1.5, 1.5))
        high = round(price + rng.uniform(0.2, 2.5), 2)
        low = round(price - rng.uniform(0.2, 2.5), 2)
        open_ = round(rng.uniform(low, high), 2)
        close = round(rng.uniform(low, high), 2)

        bar = Bar(
            instrument_id=INSTRUMENT,
            timeframe=Timeframe.M1,
            open_time_utc=T0 + timedelta(minutes=i),
            open=open_,
            high=high,
            low=low,
            close=close,
            tick_volume=100,
            real_volume=0,
        )
        sequence = sequence.append(bar)

    return sequence


@pytest.mark.parametrize("cutoff", [5, 15, 25, 38])
def test_swing_engine_has_no_lookahead(cutoff: int):
    sequence = _zigzag_bars(40, seed=1234)

    def probe_factory() -> SwingEngine:
        return SwingEngine(
            level=SwingLevel.SWING, k=5, atr_period=10, atr_multiplier=1.5, tick_size=0.01
        )

    assert_no_lookahead(sequence, probe_factory, cutoff=cutoff)
