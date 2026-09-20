"""vo.valco.lrx_swings -- the swing adapter.

The availability tests are the ones that matter. A swing used before it
was knowable is silent lookahead, and it would make every backtest look
better than the strategy is."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.swings import (
    OBJECT_TYPE_SWING,
    SwingLevel,
    SwingPoint,
    SwingStatus,
    SwingType,
)
from vo.valco.lrx_swings import (
    StructureLabel,
    adapt_swings,
    most_recent_opposing,
    visible_swings,
)

_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)


def _at(minute: int) -> datetime:
    return _START + timedelta(minutes=minute)


def _bar_times(count: int = 60) -> dict[str, datetime]:
    return {f"bar{i}": _at(i) for i in range(count)}


def _swing(
    name: str,
    swing_type: SwingType,
    price: float,
    *,
    pivot_minute: int,
    confirmed_minute: int,
    level: SwingLevel = SwingLevel.SWING,
    status: SwingStatus = SwingStatus.CONFIRMED,
) -> SwingPoint:
    confirmed_only: dict[str, object] = (
        {
            # A CONFIRMED SwingPoint must carry the measurement that
            # passed the ATR filter; a BROKEN one must not (a break is a
            # price fact, not a new reversal measurement).
            "reversal_ticks": 500,
            "atr_ticks_at_pivot": 1000,
            "reversal_extreme_price": price - 5.0,
            "reversal_extreme_bar_id": f"bar{confirmed_minute}",
        }
        if status is SwingStatus.CONFIRMED
        else {"supersedes": "the-confirmed-record-this-corrects"}
    )

    return SwingPoint(
        object_type=OBJECT_TYPE_SWING,
        object_id=name,
        observed_at=_at(confirmed_minute),
        recorded_at=_at(confirmed_minute),
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        level=level,
        swing_type=swing_type,
        status=status,
        price=price,
        body_price=price,
        pivot_bar_id=f"bar{pivot_minute}",
        confirmed_at_bar_id=f"bar{confirmed_minute}",
        **confirmed_only,  # type: ignore[arg-type]
    )


# ── availability: the lookahead rule ──────────────────────────────────────


def test_a_swing_is_not_available_at_the_moment_its_extreme_printed() -> None:
    """The high printed at :10 but the engine only confirmed it at :15.
    Anything acting on it at :10 is reading the future."""
    swings = adapt_swings(
        [_swing("s1", SwingType.HIGH, 20_100.0, pivot_minute=10, confirmed_minute=15)],
        bar_time_of=_bar_times(),
    )

    swing = swings[0]
    assert swing.occurred_at == _at(10)
    assert swing.available_at == _at(15)
    assert swing.visible_at(_at(10)) is False
    assert swing.visible_at(_at(14)) is False
    assert swing.visible_at(_at(15)) is True


def test_visible_swings_filters_by_availability_not_occurrence() -> None:
    swings = adapt_swings(
        [
            _swing("s1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=10),
            _swing("s2", SwingType.HIGH, 20_120.0, pivot_minute=12, confirmed_minute=20),
        ],
        bar_time_of=_bar_times(),
    )

    at_fifteen = visible_swings(swings, at=_at(15))

    assert [s.swing_id for s in at_fifteen] == ["s1"]


def test_a_swing_with_an_unknown_confirmation_bar_is_dropped() -> None:
    """A guessed availability is worse than no swing -- it reintroduces
    exactly the lookahead this adapter prevents."""
    swings = adapt_swings(
        [_swing("s1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=99)],
        bar_time_of=_bar_times(count=20),
    )

    assert swings == ()


# ── structure derivation ──────────────────────────────────────────────────


def test_consecutive_highs_are_labelled_higher_or_lower() -> None:
    swings = adapt_swings(
        [
            _swing("h1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=10),
            _swing("h2", SwingType.HIGH, 20_150.0, pivot_minute=15, confirmed_minute=20),
            _swing("h3", SwingType.HIGH, 20_120.0, pivot_minute=25, confirmed_minute=30),
        ],
        bar_time_of=_bar_times(),
    )
    by_id = {s.swing_id: s for s in swings}

    assert by_id["h1"].structure is StructureLabel.UNDEFINED
    assert by_id["h2"].structure is StructureLabel.HIGHER_HIGH
    assert by_id["h3"].structure is StructureLabel.LOWER_HIGH


def test_lows_are_labelled_against_lows_not_highs() -> None:
    swings = adapt_swings(
        [
            _swing("l1", SwingType.LOW, 20_000.0, pivot_minute=5, confirmed_minute=10),
            _swing("h1", SwingType.HIGH, 20_200.0, pivot_minute=12, confirmed_minute=16),
            _swing("l2", SwingType.LOW, 20_040.0, pivot_minute=20, confirmed_minute=25),
        ],
        bar_time_of=_bar_times(),
    )
    by_id = {s.swing_id: s for s in swings}

    assert by_id["l2"].structure is StructureLabel.HIGHER_LOW


def test_the_first_swing_of_its_type_is_undefined_not_guessed() -> None:
    swings = adapt_swings(
        [_swing("l1", SwingType.LOW, 20_000.0, pivot_minute=5, confirmed_minute=10)],
        bar_time_of=_bar_times(),
    )

    assert swings[0].structure is StructureLabel.UNDEFINED


# ── equal highs: the stop pool ────────────────────────────────────────────


def test_swings_at_effectively_the_same_price_are_marked_equal() -> None:
    """A pair of highs at one price is a pool of stops in a way that one
    high is not."""
    swings = adapt_swings(
        [
            _swing("h1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=10),
            _swing("h2", SwingType.HIGH, 20_100.5, pivot_minute=15, confirmed_minute=20),
        ],
        bar_time_of=_bar_times(),
        equal_tolerance=2.0,
    )
    by_id = {s.swing_id: s for s in swings}

    assert by_id["h1"].equal_with == ("h2",)
    assert by_id["h2"].equal_with == ("h1",)
    assert by_id["h2"].structure is StructureLabel.EQUAL


def test_highs_far_apart_are_not_equal() -> None:
    swings = adapt_swings(
        [
            _swing("h1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=10),
            _swing("h2", SwingType.HIGH, 20_180.0, pivot_minute=15, confirmed_minute=20),
        ],
        bar_time_of=_bar_times(),
        equal_tolerance=2.0,
    )

    assert all(s.equal_with == () for s in swings)


def test_a_high_is_never_equal_to_a_low_at_the_same_price() -> None:
    swings = adapt_swings(
        [
            _swing("h1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=10),
            _swing("l1", SwingType.LOW, 20_100.0, pivot_minute=15, confirmed_minute=20),
        ],
        bar_time_of=_bar_times(),
        equal_tolerance=2.0,
    )

    assert all(s.equal_with == () for s in swings)


# ── which swing an MSS must break ─────────────────────────────────────────


def test_a_buy_side_raid_points_at_the_most_recent_confirmed_low() -> None:
    """After price spiked above a high and failed, a bearish shift means
    breaking the last swing low."""
    swings = adapt_swings(
        [
            _swing("l1", SwingType.LOW, 20_000.0, pivot_minute=5, confirmed_minute=10),
            _swing("l2", SwingType.LOW, 20_040.0, pivot_minute=15, confirmed_minute=20),
            _swing("h1", SwingType.HIGH, 20_200.0, pivot_minute=22, confirmed_minute=26),
        ],
        bar_time_of=_bar_times(),
    )

    relevant = most_recent_opposing(swings, at=_at(30), raid_was_buy_side=True)

    assert relevant is not None
    assert relevant.swing_id == "l2"


def test_the_relevant_swing_respects_availability() -> None:
    """A low confirmed after the moment we are asking about cannot be the
    one an MSS breaks."""
    swings = adapt_swings(
        [
            _swing("l1", SwingType.LOW, 20_000.0, pivot_minute=5, confirmed_minute=10),
            _swing("l2", SwingType.LOW, 20_040.0, pivot_minute=15, confirmed_minute=40),
        ],
        bar_time_of=_bar_times(),
    )

    relevant = most_recent_opposing(swings, at=_at(20), raid_was_buy_side=True)

    assert relevant is not None
    assert relevant.swing_id == "l1"


def test_a_broken_swing_is_not_liquidity_waiting_to_be_taken() -> None:
    """G5 in action: a BROKEN record supersedes its CONFIRMED one rather
    than overwriting it, so both the original read and the correction
    survive. Either way a level price has already traded through is not
    a pool of stops waiting to be taken."""
    swings = adapt_swings(
        [
            _swing(
                "l1", SwingType.LOW, 20_000.0, pivot_minute=5, confirmed_minute=10,
                status=SwingStatus.BROKEN,
            )
        ],
        bar_time_of=_bar_times(),
    )

    assert visible_swings(swings, at=_at(30)) == ()
    assert most_recent_opposing(swings, at=_at(30), raid_was_buy_side=True) is None


def test_internal_and_external_swings_are_distinguishable() -> None:
    swings = adapt_swings(
        [
            _swing(
                "i1", SwingType.HIGH, 20_100.0, pivot_minute=5, confirmed_minute=10,
                level=SwingLevel.INTERNAL,
            ),
            _swing("e1", SwingType.HIGH, 20_200.0, pivot_minute=15, confirmed_minute=20),
        ],
        bar_time_of=_bar_times(),
    )
    by_id = {s.swing_id: s for s in swings}

    assert by_id["i1"].external is False
    assert by_id["e1"].external is True
