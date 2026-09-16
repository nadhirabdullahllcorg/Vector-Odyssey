"""
ReferenceLevelEngine (architecture/vo-time-engine.md §6, §9 "Levels"):
previous day/week/month values correct across weekends and month ends,
boundary pairs preserve both raw prices, and nothing is named after a
strategy concept.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.sequence import BarSequence
from vo.market.timeframe import Timeframe
from vo.time.engine import VOTimeEngine
from vo.time.levels import ReferenceLevelEngine
from vo.time.sessions import load_session_configs

_NY = ZoneInfo("America/New_York")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")

# One trading day D's four representative bars (sparse - BarSequence only
# needs strictly-increasing timestamps, not full M1 density, to exercise
# grouping/aggregation correctly). CME trade-date convention: the session
# opening 18:00 ET the PREVIOUS calendar day (D-1) is trading day D, whose
# RTH open, settlement and end all fall on D's own calendar date:
#   day_open        NY (D - 1,  18:00)  -- trading_day_open
#   rth_open_bar    NY (D,      09:30)  -- rth_open
#   pre_settlement  NY (D,      16:13)  -- its CLOSE is "settlement" (16:14)
#   day_end         NY (D,      17:59)  -- last bar before the next rollover
_TRADING_DAYS = [
    date(2026, 6, 26),  # Fri, week 26, June
    date(2026, 6, 29),  # Mon, week 27, June
    date(2026, 6, 30),  # Tue, week 27, June
    date(2026, 7, 1),  # Wed, week 27, July  <- month boundary mid-week
    date(2026, 7, 2),  # Thu, week 27, July
    date(2026, 7, 3),  # Fri, week 27, July
    date(2026, 7, 6),  # Mon, week 28, July  <- weekend gap from Fri
]


def _ny(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(UTC)


def _bar(ny_dt: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=ny_dt,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=10,
        real_volume=0,
    )


def _build_sequence() -> BarSequence:
    bars: list[Bar] = []
    for i, trading_day in enumerate(_TRADING_DAYS):
        base = 100 + i * 10
        prev_cal = trading_day - timedelta(days=1)

        # day_open: the session opens 18:00 ET the PREVIOUS calendar day.
        bars.append(
            _bar(
                _ny(prev_cal.year, prev_cal.month, prev_cal.day, 18, 0),
                base,
                base + 1,
                base - 1,
                base + 0.5,
            )
        )
        # rth_open / pre_settlement / day_end all fall on trading_day's own date.
        bars.append(
            _bar(
                _ny(trading_day.year, trading_day.month, trading_day.day, 9, 30),
                base + 2,
                base + 3,
                base + 1,
                base + 2.5,
            )
        )
        bars.append(
            _bar(
                _ny(trading_day.year, trading_day.month, trading_day.day, 16, 13),
                base + 3,
                base + 5,
                base + 2,
                base + 4,
            )
        )
        bars.append(
            _bar(
                _ny(trading_day.year, trading_day.month, trading_day.day, 17, 59),
                base + 4,
                base + 6,
                base + 3,
                base + 5,
            )
        )

    sequence = BarSequence()
    for bar in sorted(bars, key=lambda b: b.open_time_utc):
        sequence = sequence.append(bar)
    return sequence


def _engine() -> ReferenceLevelEngine:
    configs = load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")
    time_engine = VOTimeEngine(configs)
    return ReferenceLevelEngine(time_engine, _build_sequence())


def _engine_for(bars: list[Bar]) -> ReferenceLevelEngine:
    sequence = BarSequence()
    for bar in bars:
        sequence = sequence.append(bar)
    configs = load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")
    return ReferenceLevelEngine(VOTimeEngine(configs), sequence)


# ── previous day/week/month OHLC ─────────────────────────────────────


def test_previous_day_ohlc_is_none_for_the_first_observed_trading_day():
    engine = _engine()
    assert engine.previous_day_ohlc(date(2026, 6, 26)) is None


def test_previous_day_ohlc_skips_the_weekend():
    """Monday 2026-07-06's previous day is Friday 2026-07-03, not Saturday
    or Sunday - there is no group for either, so the data-driven grouping
    steps straight over the gap."""
    engine = _engine()
    previous = engine.previous_day_ohlc(date(2026, 7, 6))

    assert previous is not None
    assert previous.period_start == date(2026, 7, 3)
    # index 5 (2026-07-03) -> base = 100 + 5*10 = 150
    assert previous.open == 150
    assert previous.high == 156
    assert previous.low == 149
    assert previous.close == 155


def test_previous_day_ohlc_is_the_immediately_preceding_trading_day():
    engine = _engine()
    previous = engine.previous_day_ohlc(date(2026, 7, 1))  # -> 2026-06-30, base=120

    assert previous is not None
    assert previous.period_start == date(2026, 6, 30)
    assert previous.open == 120
    assert previous.high == 126
    assert previous.low == 119
    assert previous.close == 125


def test_previous_week_ohlc_differs_from_previous_day_and_previous_month():
    """2026-07-01 is week 27 (so are 2026-06-29 and 2026-06-30) - the
    previous *week* is week 26, which only contains 2026-06-26 (base=100),
    a single-day aggregate distinct from both previous_day (2026-06-30,
    base=120) and previous_month (all of June: 100/110/120)."""
    engine = _engine()
    previous_week = engine.previous_week_ohlc(date(2026, 7, 1))

    assert previous_week is not None
    assert previous_week.period_start == date(2026, 6, 26)
    assert previous_week.open == 100
    assert previous_week.high == 106
    assert previous_week.low == 99
    assert previous_week.close == 105


def test_previous_week_ohlc_aggregates_the_full_prior_week_across_a_month_boundary():
    """2026-07-06 is week 28; the previous week (27) spans 2026-06-29
    through 2026-07-03 - five trading days, crossing June -> July mid-week -
    and the aggregate must cover all five, not stop at the month boundary."""
    engine = _engine()
    previous_week = engine.previous_week_ohlc(date(2026, 7, 6))

    assert previous_week is not None
    assert previous_week.period_start == date(2026, 6, 29)
    # bases 110, 120, 130, 140, 150 (2026-06-29 .. 2026-07-03)
    assert previous_week.open == 110  # first bar of 2026-06-29
    assert previous_week.high == 156  # base 150's high (150 + 6)
    assert previous_week.low == 109  # base 110's low (110 - 1)
    assert previous_week.close == 155  # last bar of 2026-07-03


def test_previous_month_ohlc_aggregates_every_observed_day_in_the_prior_month():
    """Previous month for 2026-07-06 (July) is June: 2026-06-26/29/30 -
    bases 100, 110, 120 - three days, not one."""
    engine = _engine()
    previous_month = engine.previous_month_ohlc(date(2026, 7, 6))

    assert previous_month is not None
    assert previous_month.period_start == date(2026, 6, 26)
    assert previous_month.open == 100  # first bar of 2026-06-26
    assert previous_month.high == 126  # base 120's high
    assert previous_month.low == 99  # base 100's low
    assert previous_month.close == 125  # last bar of 2026-06-30


def test_previous_month_ohlc_is_none_when_the_prior_month_was_never_observed():
    engine = _engine()
    assert engine.previous_month_ohlc(date(2026, 6, 26)) is None


# ── session opens / settlement ───────────────────────────────────────


def test_session_opens_reports_all_four_anchors_correctly():
    """2026-07-01 (base=130): trading_day_open is its own first bar;
    week_open is 2026-06-29's first bar (start of week 27, base=110);
    month_open is 2026-07-01's own first bar too, since July only starts
    with this trading day; rth_open is the 09:30 bar's own open."""
    engine = _engine()
    opens = engine.session_opens(date(2026, 7, 1))

    assert opens is not None
    assert opens.trading_day_open is not None
    assert opens.trading_day_open.price == 130
    assert opens.trading_day_open.label == "trading_day_open"

    assert opens.week_open is not None
    assert opens.week_open.price == 110  # 2026-06-29's day-open bar
    assert opens.week_open.label == "week_open"

    assert opens.month_open is not None
    assert opens.month_open.price == 130  # July's first observed trading day is this one
    assert opens.month_open.label == "month_open"

    assert opens.rth_open is not None
    assert opens.rth_open.price == 132  # base + 2
    assert opens.rth_open.label == "rth_open"
    assert opens.rth_open.utc_timestamp == _ny(2026, 7, 1, 9, 30)


def test_session_opens_is_none_for_an_unobserved_trading_day():
    engine = _engine()
    assert engine.session_opens(date(2026, 8, 1)) is None


def test_settlement_uses_the_closing_price_at_1614_not_the_opening_price():
    """16:14 ET is a *closing* price - the close of the last bar strictly
    before the settlement instant, not the open of a bar starting at it."""
    engine = _engine()
    settlement = engine.settlement(date(2026, 7, 1))  # base=130

    assert settlement is not None
    assert settlement.label == "settlement"
    assert settlement.price == 134  # base + 4 == pre_settlement bar's close
    assert settlement.utc_timestamp == _ny(2026, 7, 1, 16, 13)


def test_settlement_is_none_for_an_unobserved_trading_day():
    engine = _engine()
    assert engine.settlement(date(2026, 8, 1)) is None


# ── the RTH-open / settlement comparison (never named "ORG") ─────────


def test_rth_open_settlement_comparison_marks_the_higher_and_lower_anchor():
    """In this dataset settlement (base+4) always exceeds rth_open
    (base+2) - the comparison must mark settlement as higher, rth_open
    as lower, honestly, without assuming which side wins in general."""
    engine = _engine()
    comparison = engine.rth_open_settlement_comparison(date(2026, 7, 1))

    assert comparison is not None
    assert comparison.trading_day == date(2026, 7, 1)
    assert comparison.higher is not None
    assert comparison.higher.label == "settlement"
    assert comparison.higher.price == 134
    assert comparison.lower is not None
    assert comparison.lower.label == "rth_open"
    assert comparison.lower.price == 132


def test_rth_open_settlement_comparison_marks_rth_open_higher_when_it_is():
    """The reverse ordering - an isolated one-day sequence proving the
    comparison logic is direction-agnostic, not just correct for this
    module's own (settlement > rth_open) dataset shape."""
    bars = [
        _bar(_ny(2026, 6, 30, 18, 0), 100, 101, 99, 100),  # day_open (prev cal day)
        _bar(_ny(2026, 7, 1, 9, 30), 150, 151, 149, 150),  # rth_open = 150, high
        _bar(_ny(2026, 7, 1, 16, 13), 90, 91, 89, 90),  # settlement = 90, low
    ]
    engine = _engine_for(bars)
    comparison = engine.rth_open_settlement_comparison(date(2026, 7, 1))

    assert comparison is not None
    assert comparison.higher is not None
    assert comparison.higher.label == "rth_open"
    assert comparison.lower is not None
    assert comparison.lower.label == "settlement"


def test_rth_open_settlement_comparison_is_none_not_a_guess_on_an_exact_tie():
    bars = [
        _bar(_ny(2026, 6, 30, 18, 0), 100, 101, 99, 100),  # day_open (prev cal day)
        _bar(_ny(2026, 7, 1, 9, 30), 125, 126, 124, 125),  # rth_open = 125
        _bar(_ny(2026, 7, 1, 16, 13), 124, 126, 123, 125),  # settlement = 125, exact tie
    ]
    engine = _engine_for(bars)
    comparison = engine.rth_open_settlement_comparison(date(2026, 7, 1))

    assert comparison is not None
    assert comparison.higher is None
    assert comparison.lower is None


# ── boundary pairs ────────────────────────────────────────────────────


def test_boundary_pairs_preserve_both_raw_prices_across_a_normal_transition():
    """Trading day 2026-07-01 (base=130): its day_open is the prior
    evening (2026-06-30 18:00, ASIA); its rth_open (2026-07-01 09:30) is
    NY_AM; its pre_settlement and day_end (2026-07-01 16:13 / 17:59) fall
    in the 16:00-18:00 gap with no configured session. The NY_AM -> None
    crossing between rth_open and pre_settlement is a normal same-day
    transition, both raw prices preserved exactly."""
    engine = _engine()
    pairs = engine.boundary_pairs()

    matches = [
        p
        for p in pairs
        if p.prior_close_at_utc == _ny(2026, 7, 1, 9, 30)
        and p.next_open_at_utc == _ny(2026, 7, 1, 16, 13)
    ]

    assert len(matches) == 1
    pair = matches[0]
    assert pair.previous_session == "NY_AM"
    assert pair.session is None
    assert pair.prior_close == 132.5  # rth_open bar's close (base=130)
    assert pair.next_open == 133  # pre_settlement bar's open (base=130)


def test_boundary_pairs_capture_the_weekend_gap_with_raw_prices_intact():
    """Trading day 2026-07-03 (Friday)'s day_end bar is at 2026-07-03
    17:59 NY (its session opened 2026-07-02 18:00). The very next observed
    bar is trading day 2026-07-06 (Monday)'s day_open at 2026-07-05
    (Sunday) 18:00 - the session opening Sunday evening is Monday's trading
    day (CME). A real two-calendar-day weekend jump with nothing invented
    to fill it."""
    engine = _engine()
    pairs = engine.boundary_pairs()

    weekend_pairs = [
        p
        for p in pairs
        if p.prior_close_at_utc == _ny(2026, 7, 3, 17, 59)
        and p.next_open_at_utc == _ny(2026, 7, 5, 18, 0)
    ]

    assert len(weekend_pairs) == 1
    pair = weekend_pairs[0]
    assert pair.prior_close == 155  # trading_day 2026-07-03's day_end close, unchanged
    assert pair.next_open == 160  # trading_day 2026-07-06's day_open open, unchanged
    assert pair.previous_session is None
    assert pair.session == "ASIA"


def test_boundary_pairs_is_empty_for_fewer_than_two_bars():
    engine = _engine_for([])
    assert engine.boundary_pairs() == ()


# ── naming discipline (architecture/vo-time-engine.md §6, §9) ────────


@pytest.mark.parametrize(
    "forbidden", ["org", "opening_range", "gap", "fvg", "imbalance", "vi", "nwog", "ndog"]
)
def test_no_level_type_is_named_after_a_strategy_concept(forbidden):
    """Word-boundary match, not substring: "vi" must not flag a field
    like `previous_session`, which merely contains those two letters."""
    import re

    import vo.market.levels as levels_module

    pattern = r"\b" + re.escape(forbidden.replace("_", " ")) + r"\b"

    for name in dir(levels_module):
        if name.startswith("_"):
            continue
        obj = getattr(levels_module, name)
        if not hasattr(obj, "__dataclass_fields__"):
            continue
        for field_name in obj.__dataclass_fields__:
            haystack = field_name.lower().replace("_", " ")
            assert not re.search(pattern, haystack), (
                f"{obj.__name__}.{field_name} looks like a strategy-named field"
            )
