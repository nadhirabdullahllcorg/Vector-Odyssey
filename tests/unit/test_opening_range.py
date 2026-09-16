"""
OpeningRangeGap (ORG, [VO-D]) -- vo.market.opening_range and the engine
method vo.time.levels.ReferenceLevelEngine.opening_range_gap.

The ORG is the previous trading day's settlement (its 16:14 closing
print) against the current trading day's RTH open (09:30). Today's open
is the gap's high when it opened above prior settlement (gap up) and its
low when below (gap down); equilibrium is the 50% midpoint. CME
trade-date convention throughout: a session opening 18:00 ET the previous
calendar day is that trading day's session.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.opening_range import OpenPosition
from vo.market.sequence import BarSequence
from vo.market.timeframe import Timeframe
from vo.time.engine import VOTimeEngine
from vo.time.levels import ReferenceLevelEngine
from vo.time.sessions import load_session_configs

_NY = ZoneInfo("America/New_York")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")


def _ny(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=_NY).astimezone(UTC)


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


def _engine(bars: list[Bar]) -> ReferenceLevelEngine:
    seq = BarSequence()
    for b in sorted(bars, key=lambda x: x.open_time_utc):
        seq = seq.append(b)
    cfgs = load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")
    return ReferenceLevelEngine(VOTimeEngine(cfgs), seq)


def _two_days(prior_settlement_close: float, today_open: float) -> list[Bar]:
    """Trading day 2026-07-01 (its settlement closes at prior_settlement_close)
    then trading day 2026-07-02 (its RTH opens at today_open). Bars are
    laid out CME trade-date: each session opens 18:00 the previous calendar
    day, RTH/settlement fall on the trading day's own date."""
    return [
        # trading day 2026-07-01
        _bar(_ny(2026, 6, 30, 18, 0), 100, 101, 99, 100),  # day open (prev cal day)
        _bar(_ny(2026, 7, 1, 9, 30), 100, 101, 99, 100),  # rth open (not the ORG anchor here)
        _bar(_ny(2026, 7, 1, 16, 13), 100, 101, 99, prior_settlement_close),  # settlement close
        # trading day 2026-07-02
        _bar(_ny(2026, 7, 1, 18, 0), 100, 101, 99, 100),  # day open (prev cal day)
        _bar(
            _ny(2026, 7, 2, 9, 30),
            today_open,
            today_open + 1,
            today_open - 1,
            today_open,
        ),  # rth open -- the ORG anchor
        _bar(_ny(2026, 7, 2, 16, 13), 100, 101, 99, 100),  # settlement (unused here)
    ]


def test_gap_up_makes_the_open_the_org_high():
    engine = _engine(_two_days(prior_settlement_close=100, today_open=110))
    org = engine.opening_range_gap(date(2026, 7, 2))

    assert org is not None
    assert org.open_position is OpenPosition.HIGH
    assert org.high == 110  # today's open
    assert org.low == 100  # prior settlement
    assert org.equilibrium == 105
    assert org.session_open.price == 110
    assert org.prior_settlement.price == 100
    assert org.prior_settlement.label == "settlement"


def test_gap_down_makes_the_open_the_org_low():
    engine = _engine(_two_days(prior_settlement_close=100, today_open=90))
    org = engine.opening_range_gap(date(2026, 7, 2))

    assert org is not None
    assert org.open_position is OpenPosition.LOW
    assert org.high == 100  # prior settlement
    assert org.low == 90  # today's open
    assert org.equilibrium == 95


def test_exact_tie_is_flat_with_a_degenerate_gap():
    engine = _engine(_two_days(prior_settlement_close=100, today_open=100))
    org = engine.opening_range_gap(date(2026, 7, 2))

    assert org is not None
    assert org.open_position is OpenPosition.FLAT
    assert org.high == org.low == org.equilibrium == 100


def test_org_is_none_for_the_first_observed_trading_day():
    """No earlier trading day means no prior settlement to gap against."""
    engine = _engine(_two_days(prior_settlement_close=100, today_open=110))
    assert engine.opening_range_gap(date(2026, 7, 1)) is None


def test_org_is_none_when_today_has_no_rth_open():
    """A trading day whose 09:30 bar was never observed has no open anchor
    -- honest None, not a guess."""
    bars = [
        # trading day 2026-07-01 (has a settlement)
        _bar(_ny(2026, 6, 30, 18, 0), 100, 101, 99, 100),
        _bar(_ny(2026, 7, 1, 16, 13), 100, 101, 99, 100),
        # trading day 2026-07-02 -- only a pre-open bar, no 09:30
        _bar(_ny(2026, 7, 1, 18, 0), 100, 101, 99, 100),
        _bar(_ny(2026, 7, 2, 8, 0), 100, 101, 99, 100),  # before RTH open
    ]
    engine = _engine(bars)
    assert engine.opening_range_gap(date(2026, 7, 2)) is None
