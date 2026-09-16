"""
BarRelation / Separation — the pairwise test matrix from
architecture/vo-candle-layer.md §12: gaps, overlaps, expansion/contraction,
non-adjacent separation.
"""

from datetime import UTC, datetime, timedelta

import pytest

from vo.market import Bar, BarRelation, InstrumentId, Timeframe
from vo.market.relation import separation_of

INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
T0 = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)
TICK_SIZE = 0.01


def _bar(open_, high, low, close, *, minute=0):
    return Bar(
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=T0 + timedelta(minutes=minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
    )


def test_positive_gap():
    a = _bar(99.50, 100.00, 99.00, 100.00, minute=0)  # close = 100.00
    b = _bar(100.25, 100.50, 100.10, 100.40, minute=1)  # open = 100.25

    rel = BarRelation(a, b, tick_size=TICK_SIZE)

    assert a.close == 100.00
    assert b.open == 100.25
    assert rel.open_gap_ticks == 25


def test_negative_gap():
    a = _bar(99.50, 100.50, 99.00, 100.25, minute=0)  # close = 100.25
    b = _bar(100.00, 100.10, 99.80, 100.05, minute=1)  # open = 100.00

    rel = BarRelation(a, b, tick_size=TICK_SIZE)

    assert rel.open_gap_ticks == -25


def test_body_overlap_independent_of_range_overlap():
    # a: body 100-105, range 95-110.  b: body 102-107, range 90-120.
    # Bodies overlap 102-105 (3), ranges overlap 95-110 (15) - different numbers.
    a = _bar(100, 110, 95, 105, minute=0)
    b = _bar(102, 120, 90, 107, minute=1)

    rel = BarRelation(a, b, tick_size=TICK_SIZE)

    assert rel.body_overlap_ticks == pytest.approx(300)  # 3.00 price / 0.01 tick_size
    assert rel.range_overlap_ticks == pytest.approx(1500)  # 15.00 / 0.01
    assert rel.body_overlap_ticks != rel.range_overlap_ticks


def test_wick_only_overlap_ranges_touch_bodies_do_not():
    # a: body 100-102 (bullish), wick up to 105. b: body 106-108, wick down to 103.
    # Ranges overlap (103-105), bodies do not.
    a = _bar(100, 105, 99, 102, minute=0)
    b = _bar(106, 109, 103, 108, minute=1)

    rel = BarRelation(a, b, tick_size=TICK_SIZE)

    assert rel.range_overlap_ticks > 0
    assert rel.body_overlap_ticks == 0
    assert rel.body_separation_ticks > 0  # bodies gap up


def test_range_expansion_and_contraction():
    small = _bar(100, 102, 99, 101, minute=0)  # range 3
    big = _bar(100, 110, 90, 105, minute=1)  # range 20

    expanding = BarRelation(small, big, tick_size=TICK_SIZE)
    contracting = BarRelation(big, small, tick_size=TICK_SIZE)

    assert expanding.range_expansion is True
    assert expanding.range_contraction is False
    assert contracting.range_expansion is False
    assert contracting.range_contraction is True


def test_range_ratio_is_none_when_a_has_zero_range():
    flat = _bar(100, 100, 100, 100, minute=0)
    other = _bar(100, 110, 95, 105, minute=1)

    rel = BarRelation(flat, other, tick_size=TICK_SIZE)

    assert rel.range_ratio is None


def test_continuation_and_reversal():
    bullish_a = _bar(100, 110, 95, 108, minute=0)
    bullish_b = _bar(105, 115, 100, 112, minute=1)
    bearish_b = _bar(112, 115, 100, 105, minute=1)

    same_direction = BarRelation(bullish_a, bullish_b, tick_size=TICK_SIZE)
    opposite_direction = BarRelation(bullish_a, bearish_b, tick_size=TICK_SIZE)

    assert same_direction.continuation is True
    assert same_direction.reversal is False
    assert opposite_direction.reversal is True
    assert opposite_direction.continuation is False


def test_contains_and_contained_by():
    inner = _bar(101, 104, 99, 102, minute=0)
    outer = _bar(100, 110, 90, 105, minute=1)

    outer_relation = BarRelation(inner, outer, tick_size=TICK_SIZE)
    inner_relation = BarRelation(outer, inner, tick_size=TICK_SIZE)

    assert outer_relation.contains is True
    assert outer_relation.contained_by is False
    assert inner_relation.contained_by is True
    assert inner_relation.contains is False


def test_separation_of_reports_the_fact_not_a_name():
    a = _bar(100, 102, 99, 101, minute=0)  # high = 102
    b = _bar(105, 107, 104, 106, minute=2)  # low = 104, separated from a.high by 2.00

    sep = separation_of(a, b, bars_between=1, tick_size=TICK_SIZE)

    assert sep.high_low_ticks == 200  # 2.00 / 0.01, b.low above a.high
    assert sep.bars_between == 1
    assert sep.time_between == timedelta(minutes=2)


def test_relation_rejects_non_positive_tick_size():
    a = _bar(100, 102, 99, 101, minute=0)
    b = _bar(103, 105, 102, 104, minute=1)

    with pytest.raises(ValueError):
        BarRelation(a, b, tick_size=0)
