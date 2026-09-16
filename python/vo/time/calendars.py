"""
Trading-day boundaries.

architecture/vo-time-engine.md §2: `trading_day` is deliberately separate
from the NY calendar date. An instrument whose trading day opens 18:00 ET
the previous calendar day (a nearly-24h CFD) has a trading day that
disagrees with the calendar date for six hours out of every twenty-four;
conflating them silently misfiles a sixth of all bars.

Holiday awareness is not implemented here - nothing in Phase 6's acceptance
gate (architecture/vo-time-engine.md §9) exercises it, and inventing a
holiday calendar without a concrete requirement would be exactly the kind
of unrequested scope this project's discipline warns against.
"""

from __future__ import annotations

from datetime import date, datetime, time


def trading_day_of(ny_timestamp: datetime, trading_day_opens: time) -> date:
    """
    The calendar date this instant's trading day is keyed to — the CME
    "trade date" convention.

    A CFD/futures session that opens at `trading_day_opens` (e.g. 18:00 ET)
    is dated to the *next* calendar day: the session opening 18:00 ET on
    the 16th is trading day the 17th, running through ~17:00 ET on the
    17th (its settlement/trade date). So:

      - At or after `trading_day_opens` (NY wall clock), the trading day is
        tomorrow's date (the session that just opened settles tomorrow).
      - Before it, the trading day is today's date (still inside the
        session that opened yesterday evening).

    This is why the trading day disagrees with the NY calendar date for
    exactly the six hours between the open and midnight — matching the
    intraday RTH open (09:30) and settlement (16:14) falling on the
    trading day's own calendar date, while the open itself falls on the
    previous one.
    """
    if ny_timestamp.timetz().replace(tzinfo=None) >= trading_day_opens:
        return date.fromordinal(ny_timestamp.date().toordinal() + 1)
    return ny_timestamp.date()
