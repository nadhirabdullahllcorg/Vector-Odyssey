"""
ReferenceLevelEngine — the *what price at that when* to the Time
Engine's *when* (architecture/vo-time-engine.md §6).

Deliberately not in vo.market, despite the doc's own suggested file path
(`vo/market/levels.py`): every computation here needs trading-day, week,
month, RTH and settlement boundaries — all vo.time (layer 2) concepts —
and vo.market (layer 1) may not import vo.time. This is the same
reasoning that put `TemporalBar` and `resolve_record` in vo.time rather
than vo.market in Phase 6. The value *types* this engine returns
(PeriodOHLC, AnchorPrice, AnchorComparison, SessionOpens, BoundaryPair)
stay in `vo.market.levels`, since they carry no time-engine dependency
of their own — only building them does.

Grouping (trading day / ISO week / month) is entirely data-driven: it
walks whatever bars were actually observed rather than assuming every
calendar day, week or month has one. That is what makes "previous day"
correct across a weekend (Friday precedes Monday with no Saturday/Sunday
group in between) and "previous month" correct across a year boundary,
for free, with no calendar-specific logic anywhere in this module.

Settlement vs RTH open — opens use the *opening* price of the first bar
at/after the boundary; settlement uses the *closing* price of the last
bar strictly before the boundary. These are genuinely different
measurements (an opening print vs a closing print), not two names for
the same lookup.

A subtlety worth being explicit about: for an instrument whose trading
day opens before midnight (US100.n opens 18:00 NY), a trading day's own
RTH and settlement wall-clock times fall on the *next* calendar date, not
the trading day's own date — trading_day_of already encodes this (see
vo.time.calendars), and `_instant_for_trading_day` below asks the Time
Engine itself which calendar date to use rather than assuming +1 day,
so this stays correct for any instrument's configuration, not just this
one's.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.levels import (
    AnchorComparison,
    AnchorPrice,
    BoundaryPair,
    PeriodOHLC,
    SessionOpens,
)
from vo.market.sequence import BarSequence
from vo.time.engine import VOTimeEngine


def _ohlc_of(period_start: date, bars: Sequence[Bar]) -> PeriodOHLC:
    return PeriodOHLC(
        period_start=period_start,
        open=bars[0].open,
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=bars[-1].close,
    )


def _open_at_or_after(
    bars: Sequence[Bar], instant: datetime, label: str, trading_day: date
) -> AnchorPrice | None:
    """The opening price of the first bar starting at or after `instant`."""
    for bar in bars:
        if bar.open_time_utc >= instant:
            return AnchorPrice(
                label=label,
                trading_day=trading_day,
                utc_timestamp=bar.open_time_utc,
                price=bar.open,
            )
    return None


def _close_at_or_before(
    bars: Sequence[Bar], instant: datetime, label: str, trading_day: date
) -> AnchorPrice | None:
    """The closing price of the last bar starting strictly before
    `instant` — for M1 (or finer) bars with no gap at the boundary, this
    is the bar whose close is the price observed at `instant`."""
    candidate: Bar | None = None
    for bar in bars:
        if bar.open_time_utc >= instant:
            break
        candidate = bar
    if candidate is None:
        return None
    return AnchorPrice(
        label=label,
        trading_day=trading_day,
        utc_timestamp=candidate.open_time_utc,
        price=candidate.close,
    )


@dataclass(frozen=True)
class _DayGroup:
    trading_day: date
    bars: tuple[Bar, ...]  # chronological, non-empty


class ReferenceLevelEngine:
    """
    Built once from a single instrument's ordered bars (a `BarSequence`
    already guarantees strictly-increasing, non-duplicate, single-
    (instrument, timeframe) bars — the exact precondition this engine
    needs, so it is reused rather than re-validated here).
    """

    def __init__(self, time_engine: VOTimeEngine, bars: BarSequence) -> None:
        self._time_engine = time_engine
        self._bars: tuple[Bar, ...] = bars.bars
        self._instrument: InstrumentId | None = bars.bars[0].instrument_id if bars.bars else None
        self._day_groups: tuple[_DayGroup, ...] = self._group_by_trading_day()

    def _group_by_trading_day(self) -> tuple[_DayGroup, ...]:
        if self._instrument is None:
            return ()

        by_day: dict[date, list[Bar]] = {}
        for bar in self._bars:
            day = self._time_engine.trading_day_of(bar.open_time_utc, self._instrument)
            by_day.setdefault(day, []).append(bar)

        return tuple(_DayGroup(trading_day=day, bars=tuple(by_day[day])) for day in sorted(by_day))

    def _group_for(self, trading_day: date) -> _DayGroup | None:
        for group in self._day_groups:
            if group.trading_day == trading_day:
                return group
        return None

    def _earlier_groups(self, trading_day: date) -> tuple[_DayGroup, ...]:
        days = [group.trading_day for group in self._day_groups]
        cutoff = bisect_left(days, trading_day)
        return self._day_groups[:cutoff]

    def _instant_for_trading_day(
        self, trading_day: date, time_of_day: time, zone: ZoneInfo
    ) -> datetime:
        """
        The UTC instant `time_of_day` (in `zone`) occurs on, *for this
        trading_day specifically* — asking the Time Engine which calendar
        date to combine with, rather than assuming a fixed +0/+1 day
        offset, so this stays correct for any trading_day_opens value.
        """
        assert self._instrument is not None
        candidate = datetime.combine(trading_day, time_of_day, tzinfo=zone).astimezone(UTC)
        if self._time_engine.trading_day_of(candidate, self._instrument) == trading_day:
            return candidate
        return datetime.combine(
            trading_day + timedelta(days=1), time_of_day, tzinfo=zone
        ).astimezone(UTC)

    # ── previous day/week/month OHLC ────────────────────────────────

    def previous_day_ohlc(self, trading_day: date) -> PeriodOHLC | None:
        earlier = self._earlier_groups(trading_day)
        if not earlier:
            return None
        last_day = earlier[-1]
        return _ohlc_of(last_day.trading_day, last_day.bars)

    def previous_week_ohlc(self, trading_day: date) -> PeriodOHLC | None:
        target_week = trading_day.isocalendar()[:2]
        earlier_weeks = [
            g for g in self._day_groups if g.trading_day.isocalendar()[:2] < target_week
        ]
        if not earlier_weeks:
            return None
        most_recent_week = earlier_weeks[-1].trading_day.isocalendar()[:2]
        week_groups = [
            g for g in earlier_weeks if g.trading_day.isocalendar()[:2] == most_recent_week
        ]
        bars = [bar for group in week_groups for bar in group.bars]
        return _ohlc_of(week_groups[0].trading_day, bars)

    def previous_month_ohlc(self, trading_day: date) -> PeriodOHLC | None:
        target_month = (trading_day.year, trading_day.month)
        earlier_months = [
            g for g in self._day_groups if (g.trading_day.year, g.trading_day.month) < target_month
        ]
        if not earlier_months:
            return None
        most_recent_month = (
            earlier_months[-1].trading_day.year,
            earlier_months[-1].trading_day.month,
        )
        month_groups = [
            g
            for g in earlier_months
            if (g.trading_day.year, g.trading_day.month) == most_recent_month
        ]
        bars = [bar for group in month_groups for bar in group.bars]
        return _ohlc_of(month_groups[0].trading_day, bars)

    # ── session opens / settlement ──────────────────────────────────

    def session_opens(self, trading_day: date) -> SessionOpens | None:
        group = self._group_for(trading_day)
        if group is None or self._instrument is None:
            return None

        config = self._time_engine.config_for(self._instrument)
        zone = ZoneInfo(config.timezone)

        trading_day_open = AnchorPrice(
            label="trading_day_open",
            trading_day=trading_day,
            utc_timestamp=group.bars[0].open_time_utc,
            price=group.bars[0].open,
        )

        week_groups_up_to = [
            g
            for g in self._day_groups
            if g.trading_day.isocalendar()[:2] == trading_day.isocalendar()[:2]
            and g.trading_day <= trading_day
        ]
        week_first = week_groups_up_to[0]
        week_open = AnchorPrice(
            label="week_open",
            trading_day=trading_day,
            utc_timestamp=week_first.bars[0].open_time_utc,
            price=week_first.bars[0].open,
        )

        month_groups_up_to = [
            g
            for g in self._day_groups
            if (g.trading_day.year, g.trading_day.month) == (trading_day.year, trading_day.month)
            and g.trading_day <= trading_day
        ]
        month_first = month_groups_up_to[0]
        month_open = AnchorPrice(
            label="month_open",
            trading_day=trading_day,
            utc_timestamp=month_first.bars[0].open_time_utc,
            price=month_first.bars[0].open,
        )

        rth_open_instant = self._instant_for_trading_day(trading_day, config.rth.start, zone)
        rth_open = _open_at_or_after(group.bars, rth_open_instant, "rth_open", trading_day)

        return SessionOpens(
            trading_day=trading_day,
            trading_day_open=trading_day_open,
            week_open=week_open,
            month_open=month_open,
            rth_open=rth_open,
        )

    def settlement(self, trading_day: date) -> AnchorPrice | None:
        group = self._group_for(trading_day)
        if group is None or self._instrument is None:
            return None

        config = self._time_engine.config_for(self._instrument)
        if config.settlement is None:
            return None

        zone = ZoneInfo(config.timezone)
        instant = self._instant_for_trading_day(trading_day, config.settlement, zone)
        return _close_at_or_before(group.bars, instant, "settlement", trading_day)

    def rth_open_settlement_comparison(self, trading_day: date) -> AnchorComparison | None:
        opens = self.session_opens(trading_day)
        settlement_price = self.settlement(trading_day)
        if opens is None or opens.rth_open is None or settlement_price is None:
            return None
        return AnchorComparison(
            trading_day=trading_day, first=opens.rth_open, second=settlement_price
        )

    # ── boundary pairs ───────────────────────────────────────────────

    def boundary_pairs(self) -> tuple[BoundaryPair, ...]:
        if self._instrument is None or len(self._bars) < 2:
            return ()

        pairs: list[BoundaryPair] = []
        previous_session = self._time_engine.session_at(
            self._bars[0].open_time_utc, self._instrument
        )

        for prev_bar, curr_bar in zip(self._bars, self._bars[1:], strict=False):
            current_session = self._time_engine.session_at(curr_bar.open_time_utc, self._instrument)
            if current_session != previous_session:
                pairs.append(
                    BoundaryPair(
                        previous_session=previous_session,
                        session=current_session,
                        prior_close=prev_bar.close,
                        prior_close_at_utc=prev_bar.open_time_utc,
                        next_open=curr_bar.open,
                        next_open_at_utc=curr_bar.open_time_utc,
                    )
                )
            previous_session = current_session

        return tuple(pairs)
