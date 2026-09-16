//+------------------------------------------------------------------+
//| VO_ReferenceLevelsMath.mqh                                       |
//|                                                                  |
//| Pure calculation for the reference-level chart drawing tool -    |
//| no ObjectCreate, no chart access, nothing stateful. Mirrors the  |
//| split already used on the Python side (vo.market.levels, pure    |
//| value types, vs vo.time.levels, the engine that builds them):    |
//| this file is the "engine" half, ported to MQL5; the indicator    |
//| file (VO_ReferenceLevels.mq5) is the "draw it on a chart" half   |
//| and owns no calculation of its own.                              |
//|                                                                  |
//| WHY THIS EXISTS AS A PORT, NOT A CALL-OUT TO PYTHON:             |
//| MT5 chart objects (ObjectCreate et al.) are MQL5-only, and this  |
//| project's MT5<->Python bridge (VO_Bridge.mq5) is one-way,        |
//| MT5 -> Python (VO_Transport.mqh only ever writes files out). No  |
//| Python -> MT5 command channel exists. Building one just to draw  |
//| lines would be significant new infrastructure for a purely       |
//| visual, non-decision concern - so the reference-level math       |
//| (architecture/vo-time-engine.md S6, implemented on the Python    |
//| side as vo.time.levels.ReferenceLevelEngine) is reimplemented    |
//| here natively. This mirrors the same reimplementation-vs-bridge  |
//| tradeoff already open for Phase 10 (MQL5-vs-Python strategy      |
//| brain), but is lower-stakes: nothing here ever reaches a trade   |
//| decision, execution stays exactly where it already is (VO_EA),  |
//| and vo.time.levels.ReferenceLevelEngine remains the single       |
//| source of truth for anything that *does* feed a decision later.  |
//| If VO_EA ever needs these levels for real (not just to look at), |
//| that is a Python-side consumer of ReferenceLevelEngine, not this |
//| file - this file draws pictures, nothing more.                   |
//|                                                                  |
//| DST HANDLING - WHY THIS IS A FAITHFUL PORT, NOT AN APPROXIMATION:|
//| Phase 6 (vo/time/brokers.py) determines DST transition instants  |
//| by scanning Python's zoneinfo (the IANA tzdata database), because|
//| a *general* broker-timezone port needs that database. MQL5 has   |
//| no equivalent database. But this file only ever needs two        |
//| specific, legislated calendars - US and EU - and both rules are  |
//| fixed, simple calendar law that has not changed since 2007 (US)  |
//| / 1996 (EU):                                                     |
//|   US: 2nd Sunday of March, 02:00 local STANDARD time -> DST;     |
//|       1st Sunday of November, 02:00 local DST time -> standard.  |
//|   EU: last Sunday of March, 01:00 UTC -> DST;                    |
//|       last Sunday of October, 01:00 UTC -> standard.             |
//| These are exactly the rules vo/time/probe.py's us_dst_bounds()/  |
//| eu_dst_bounds() already encode as pure calendar arithmetic (no   |
//| zoneinfo scan needed even on the Python side for *these*         |
//| specific transition dates - only the exact transition HOUR in    |
//| UTC terms benefits from a zoneinfo scan there, and that hour is  |
//| itself a fixed legislative fact, verified against Phase 6's own  |
//| test fixtures: US spring 2026 = 2026-03-08T07:00Z, US fall 2026  |
//| = 2026-11-01T06:00Z). Porting this narrow, fixed rule is a       |
//| like-for-like port, not a simplification of the general Time     |
//| Engine.                                                          |
//|                                                                  |
//| resolve_broker_utc's gap/repeat handling (spring-forward gap has |
//| no valid local reading; fall-back repeat is ambiguous) is ported |
//| faithfully below as VO_ResolveOffsetHours, the same algorithm    |
//| validated by Phase 6's 8 dedicated DST tests - not a rewrite.    |
//+------------------------------------------------------------------+
#property strict

//--- DST calendar selector - mirrors config/settings/brokers.yaml's
//    dst_calendar field (vo.time.brokers.DstCalendar).
enum ENUM_VO_DstCalendar
  {
   VO_DST_NONE = 0,
   VO_DST_US   = 1,
   VO_DST_EU   = 2
  };

//--- NY is always America/New_York in this project (vo-time-engine.md
//    S1) - never configurable, unlike a broker's own offsets.
#define VO_NY_STANDARD_OFFSET_HOURS  (-5.0)
#define VO_NY_DST_OFFSET_HOURS       (-4.0)
#define VO_NY_DST_CALENDAR           VO_DST_US

//+------------------------------------------------------------------+
//| Calendar-date helpers. MQL5's `datetime` is a raw epoch-seconds  |
//| scalar with no attached timezone; StructToTime/TimeToStruct treat|
//| the Y/M/D/H/M/S fields as a naive wall-clock reading, so we can  |
//| use them to do plain calendar arithmetic and then apply our own  |
//| UTC-offset bookkeeping explicitly, the same way the Python side  |
//| builds `datetime.combine(date, time, tzinfo=zone)` explicitly    |
//| rather than relying on any implicit timezone.                    |
//+------------------------------------------------------------------+
datetime VO_MakeNaive(const int year, const int month, const int day,
                      const int hour = 0, const int minute = 0)
  {
   MqlDateTime dt;
   ZeroMemory(dt);
   dt.year = year; dt.mon = month; dt.day = day;
   dt.hour = hour; dt.min = minute; dt.sec = 0;
   return StructToTime(dt);
  }

//--- 0=Sunday..6=Saturday, matching MqlDateTime.day_of_week.
int VO_DayOfWeek(const datetime naive)
  {
   MqlDateTime dt;
   TimeToStruct(naive, dt);
   return dt.day_of_week;
  }

int VO_DaysInMonth(const int year, const int month)
  {
   const int next_month = (month == 12) ? 1 : month + 1;
   const int next_year  = (month == 12) ? year + 1 : year;
   const datetime first_of_next = VO_MakeNaive(next_year, next_month, 1);
   return (int)((first_of_next - VO_MakeNaive(year, month, 1)) / 86400);
  }

//--- The nth (1-based) Sunday of a month, at 00:00 naive local.
datetime VO_NthSundayOfMonth(const int year, const int month, const int n)
  {
   const datetime first = VO_MakeNaive(year, month, 1);
   const int first_dow = VO_DayOfWeek(first);              // 0=Sunday
   const int day_of_first_sunday = 1 + ((7 - first_dow) % 7);
   const int day = day_of_first_sunday + (n - 1) * 7;
   return VO_MakeNaive(year, month, day);
  }

//--- The last Sunday of a month, at 00:00 naive local.
datetime VO_LastSundayOfMonth(const int year, const int month)
  {
   const int days_in_month = VO_DaysInMonth(year, month);
   const datetime last_day = VO_MakeNaive(year, month, days_in_month);
   const int dow = VO_DayOfWeek(last_day);                  // 0=Sunday
   return last_day - dow * 86400;
  }

//+------------------------------------------------------------------+
//| DST transition instants, in true UTC epoch seconds, for the      |
//| given calendar and year. VO_DST_NONE returns (0, 0) - callers    |
//| must check the calendar before trusting these.                   |
//|                                                                  |
//| US: 2nd Sunday of March, 02:00 EST (UTC-5) -> spring = day+2h+5h |
//|     = day 07:00 UTC. 1st Sunday of November, 02:00 EDT (UTC-4)   |
//|     -> fall = day 02:00+4h = day 06:00 UTC. Verified against     |
//|     Phase 6 fixtures: 2026 spring 03-08T07:00Z, fall 11-01T06:00Z|
//| EU: last Sunday of March / October, 01:00 UTC exactly - the EU   |
//|     rule is already defined in UTC terms, no local-offset step   |
//|     needed (matches vo/time/brokers.py's own EU handling).       |
//+------------------------------------------------------------------+
void VO_DstTransitionInstants(const ENUM_VO_DstCalendar calendar, const int year,
                               datetime &spring_utc, datetime &fall_utc)
  {
   if(calendar == VO_DST_US)
     {
      spring_utc = VO_NthSundayOfMonth(year, 3, 2) + (2 * 3600) + (5 * 3600);
      fall_utc   = VO_NthSundayOfMonth(year, 11, 1) + (2 * 3600) + (4 * 3600);
     }
   else if(calendar == VO_DST_EU)
     {
      spring_utc = VO_LastSundayOfMonth(year, 3) + (1 * 3600);
      fall_utc   = VO_LastSundayOfMonth(year, 10) + (1 * 3600);
     }
   else
     {
      spring_utc = 0;
      fall_utc = 0;
     }
  }

//+------------------------------------------------------------------+
//| Faithful port of vo.time.brokers.resolve_broker_utc's offset      |
//| selection. Given a NAIVE local wall-clock reading (already known |
//| to belong to `calendar`/`standard_hours`/`dst_hours`), and the   |
//| year to evaluate transitions for, returns the UTC offset (hours) |
//| that reading is under, and whether it fell in the spring gap     |
//| (never happened - INVALID) or the fall repeat (ambiguous -       |
//| resolved to the DST/earlier reading, same as the Python side's   |
//| documented choice: prefer the earlier, still-DST offset).        |
//|                                                                  |
//| status: 0 = VALID, 1 = GAP (invalid local time), 2 = AMBIGUOUS   |
//| (fall-back repeat; offset_hours is still filled in with the      |
//| chosen - DST - reading, matching Python's resolve_broker_utc).   |
//+------------------------------------------------------------------+
void VO_ResolveOffsetHours(const datetime naive_local, const ENUM_VO_DstCalendar calendar,
                            const double standard_hours, const double dst_hours,
                            double &offset_hours, int &status)
  {
   if(calendar == VO_DST_NONE)
     {
      offset_hours = standard_hours;
      status = 0;
      return;
     }

   MqlDateTime dt;
   TimeToStruct(naive_local, dt);
   const int year = dt.year;

   datetime spring_utc, fall_utc;
   VO_DstTransitionInstants(calendar, year, spring_utc, fall_utc);

   // Project both transition instants through BOTH candidate offsets to
   // get this local clock's own gap/repeat window boundaries, exactly as
   // resolve_broker_utc does - the gap/repeat window is a property of the
   // local clock, not of UTC.
   const datetime gap_start    = spring_utc + (datetime)(standard_hours * 3600);
   const datetime gap_end      = spring_utc + (datetime)(dst_hours * 3600);
   const datetime repeat_start = fall_utc + (datetime)(dst_hours * 3600);
   const datetime repeat_end   = fall_utc + (datetime)(standard_hours * 3600);

   if(naive_local >= gap_start && naive_local < gap_end)
     {
      // Spring-forward gap: this local time never occurred. No valid
      // offset - caller must treat this as UNKNOWN, matching Python's
      // TemporalStatus.INVALID. We still return a best-effort DST offset
      // so callers that cannot abstain (chart drawing) degrade gracefully
      // rather than crashing, but `status` tells them it was a guess.
      offset_hours = dst_hours;
      status = 1;
      return;
     }

   if(naive_local >= repeat_start && naive_local < repeat_end)
     {
      // Fall-back repeat: this local time occurred twice. Resolve to the
      // earlier (still-DST) reading, matching resolve_broker_utc's
      // documented choice.
      offset_hours = dst_hours;
      status = 2;
      return;
     }

   if(naive_local >= gap_end && naive_local < repeat_start)
     {
      offset_hours = dst_hours;
     }
   else
     {
      offset_hours = standard_hours;
     }
   status = 0;
  }

//+------------------------------------------------------------------+
//| Broker-local naive wall-clock -> true UTC epoch seconds.          |
//+------------------------------------------------------------------+
datetime VO_BrokerNaiveToUtc(const datetime broker_naive, const ENUM_VO_DstCalendar calendar,
                              const double standard_hours, const double dst_hours,
                              int &status)
  {
   double offset_hours;
   VO_ResolveOffsetHours(broker_naive, calendar, standard_hours, dst_hours, offset_hours, status);
   return broker_naive - (datetime)(offset_hours * 3600);
  }

//+------------------------------------------------------------------+
//| True UTC epoch seconds -> NY naive wall-clock. NY's own calendar  |
//| is always US/-5/-4 (see #define block above) - never configurable|
//+------------------------------------------------------------------+
datetime VO_UtcToNyNaive(const datetime utc)
  {
   MqlDateTime dt;
   TimeToStruct(utc, dt);
   const int year = dt.year;

   datetime spring_utc, fall_utc;
   VO_DstTransitionInstants(VO_NY_DST_CALENDAR, year, spring_utc, fall_utc);

   const double offset_hours = (utc >= spring_utc && utc < fall_utc)
                                ? VO_NY_DST_OFFSET_HOURS
                                : VO_NY_STANDARD_OFFSET_HOURS;
   return utc + (datetime)(offset_hours * 3600);
  }

//+------------------------------------------------------------------+
//| NY naive wall-clock -> true UTC epoch seconds (the inverse of the |
//| above; needed to place chart objects, whose anchor times must be |
//| converted back into the CHART's own time axis - broker server    |
//| time by default in MT5 - not left as NY time).                   |
//+------------------------------------------------------------------+
datetime VO_NyNaiveToUtc(const datetime ny_naive, int &status)
  {
   double offset_hours;
   VO_ResolveOffsetHours(ny_naive, VO_NY_DST_CALENDAR, VO_NY_STANDARD_OFFSET_HOURS,
                         VO_NY_DST_OFFSET_HOURS, offset_hours, status);
   return ny_naive - (datetime)(offset_hours * 3600);
  }

//+------------------------------------------------------------------+
//| True UTC epoch seconds -> broker-local naive wall-clock (the      |
//| inverse conversion needed to turn a computed NY/UTC instant back  |
//| into the chart's own time axis for ObjectCreate/ObjectMove).      |
//+------------------------------------------------------------------+
datetime VO_UtcToBrokerNaive(const datetime utc, const ENUM_VO_DstCalendar calendar,
                              const double standard_hours, const double dst_hours)
  {
   if(calendar == VO_DST_NONE)
      return utc + (datetime)(standard_hours * 3600);

   MqlDateTime dt;
   TimeToStruct(utc, dt);
   datetime spring_utc, fall_utc;
   VO_DstTransitionInstants(calendar, dt.year, spring_utc, fall_utc);
   const double offset_hours = (utc >= spring_utc && utc < fall_utc) ? dst_hours : standard_hours;
   return utc + (datetime)(offset_hours * 3600);
  }

//+------------------------------------------------------------------+
//| "hh:mm" -> minutes since local midnight. No validation beyond     |
//| what StringToInteger already gives - inputs come from `input`     |
//| strings the user sets once, not untrusted data.                   |
//+------------------------------------------------------------------+
int VO_ParseHHMM(const string hhmm)
  {
   const int colon = StringFind(hhmm, ":");
   if(colon < 0)
      return 0;
   const int hh = (int)StringToInteger(StringSubstr(hhmm, 0, colon));
   const int mm = (int)StringToInteger(StringSubstr(hhmm, colon + 1));
   return hh * 60 + mm;
  }

//+------------------------------------------------------------------+
//| trading_day_of, ported from vo.time.calendars: given an NY naive  |
//| instant and the trading-day-opens threshold (minutes since local  |
//| midnight), returns the NY-naive MIDNIGHT of the trading day this  |
//| instant belongs to. CME trade-date convention, mirroring the      |
//| Python rule exactly: a session opening at the threshold (18:00 ET)|
//| is dated to the NEXT calendar day. On/after the threshold ->      |
//| tomorrow's date (the session that just opened settles tomorrow);  |
//| before it -> today's date.                                        |
//+------------------------------------------------------------------+
datetime VO_TradingDayOf(const datetime ny_naive, const int trading_day_opens_minutes)
  {
   MqlDateTime dt;
   TimeToStruct(ny_naive, dt);
   const int minutes_of_day = dt.hour * 60 + dt.min;
   const datetime midnight = VO_MakeNaive(dt.year, dt.mon, dt.day);
   if(minutes_of_day >= trading_day_opens_minutes)
      return midnight + 86400;
   return midnight;
  }
