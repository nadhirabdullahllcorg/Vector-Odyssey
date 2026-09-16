"""Timeframe — C4: one typed vocabulary instead of two spellings."""

import pytest

from vo.market import Timeframe


@pytest.mark.parametrize(
    "mt5_spelling,expected",
    [
        ("PERIOD_M1", Timeframe.M1),
        ("PERIOD_M5", Timeframe.M5),
        ("PERIOD_M15", Timeframe.M15),
        ("PERIOD_M30", Timeframe.M30),
        ("PERIOD_H1", Timeframe.H1),
        ("PERIOD_H4", Timeframe.H4),
        ("PERIOD_D1", Timeframe.D1),
        ("PERIOD_W1", Timeframe.W1),
        ("PERIOD_MN1", Timeframe.MN1),
    ],
)
def test_from_mt5_maps_period_spelling(mt5_spelling, expected):
    assert Timeframe.from_mt5(mt5_spelling) is expected


@pytest.mark.parametrize(
    "short_spelling,expected",
    [
        ("M1", Timeframe.M1),
        ("M5", Timeframe.M5),
        ("H1", Timeframe.H1),
        ("D1", Timeframe.D1),
    ],
)
def test_from_mt5_also_accepts_short_spelling_already_in_the_wild(short_spelling, expected):
    """Both "M1" (tests/fixtures) and "PERIOD_M1" (the live bridge) already
    circulate in the repo today — see architecture/vo-candle-layer.md §8."""
    assert Timeframe.from_mt5(short_spelling) is expected


def test_from_mt5_rejects_unrecognized_spelling():
    with pytest.raises(ValueError):
        Timeframe.from_mt5("PERIOD_S30")


def test_canonical_is_the_short_form():
    assert Timeframe.M1.canonical == "M1"
    assert Timeframe.MN1.canonical == "MN1"


def test_seconds_for_fixed_timeframes():
    assert Timeframe.M1.seconds == 60
    assert Timeframe.M5.seconds == 300
    assert Timeframe.H1.seconds == 3600
    assert Timeframe.D1.seconds == 86400
    assert Timeframe.W1.seconds == 604800


def test_seconds_is_none_for_variable_length_timeframes():
    """A month has no fixed length; CUSTOM has none by definition. Both are
    None rather than a wrong-most-of-the-time guess."""
    assert Timeframe.MN1.seconds is None
    assert Timeframe.CUSTOM.seconds is None
