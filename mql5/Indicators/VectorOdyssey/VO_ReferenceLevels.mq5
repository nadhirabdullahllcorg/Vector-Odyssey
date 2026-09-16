//+------------------------------------------------------------------+
//| VO_ReferenceLevels.mq5                                           |
//|                                                                  |
//| Draws Phase 7's reference levels (architecture/vo-time-engine.md |
//| S6) directly on an MT5 chart: previous day/week/month O/H/L/C,   |
//| the four session opens (trading-day/week/month/RTH), and         |
//| settlement - one dated object set per completed period, kept for |
//| a rolling calendar-day window ("ICT-style PD arrays").           |
//|                                                                  |
//| SCOPE - READ BEFORE CHANGING ANYTHING HERE:                      |
//| This is a Custom INDICATOR, not an Expert Advisor, and that is a |
//| deliberate choice, not an accident of where the file lives.      |
//| Indicators have no order-management API in MT5 at all - there is |
//| no OrderSend/OrderCheck/PositionOpen to call even by mistake. So  |
//| unlike VO_EA (which observes only *by discipline*, per its own   |
//| header comment), this file observes only *by construction*: it   |
//| cannot become a trade-decision path even if someone tried to     |
//| extend it into one. That is exactly what makes it safe to be the |
//| one place in this repository where the standing "EA/dashboard    |
//| never touches MT5 chart objects" rule is deliberately reversed - |
//| the reversal is scoped to visualization of reference levels only,|
//| lives entirely outside VO_EA's own process, and never feeds back |
//| into it. VO_EA continues to run, unaffected, with this indicator |
//| removed from the chart entirely - exactly the same independence  |
//| the phase plan already calls for between VO_EA and the dashboard |
//| (P21/P22: "EA verified fully functional with dashboard closed").  |
//|                                                                  |
//| WHY THE MATH IS RE-DERIVED HERE INSTEAD OF ASKING PYTHON:        |
//| See VO_ReferenceLevelsMath.mqh's own header - short version: MT5 |
//| chart objects are MQL5-only, this project's MT5<->Python bridge  |
//| is one-way (MT5 -> Python), and building a reverse command       |
//| channel just to draw lines would be significant new              |
//| infrastructure for a purely visual concern. The reference-level  |
//| math (vo.time.levels.ReferenceLevelEngine on the Python side) is |
//| therefore reimplemented natively here, against the exact same    |
//| config values as config/settings/sessions.yaml (RTH 09:30-16:00, |
//| settlement 16:14 ET, trading day opens 18:00 ET) - kept in sync  |
//| by hand, the same way this file's DST rule is kept in sync with  |
//| vo/time/probe.py's by hand (both are short, legislated, rarely-  |
//| changing facts, not a live database). If sessions.yaml ever      |
//| changes, the InpRthStart/InpSettlementTime/InpTradingDayOpens     |
//| inputs below must change with it - they are not read from the    |
//| YAML file itself, since MT5 has no Python-config reader.          |
//|                                                                  |
//| DRAWING CONVENTION (confirmed with the user before writing this):|
//| - Every distinct (period, level) gets its own object, dated with |
//|   the period it was MEASURED from - not re-drawn or overwritten  |
//|   for every later day that references it as "previous X". This   |
//|   is what "one persistent line per level but also past trading   |
//|   days with their own reference lines" resolves to: daily levels |
//|   (previous-day O/H/L/C, trading-day open, RTH open, settlement) |
//|   get one object set per TRADING DAY; weekly/monthly levels get  |
//|   one object set per completed WEEK/MONTH, not duplicated every  |
//|   day that references them - matching the user's own correction  |
//|   ("for all previous WEEKS up to 60 days back", not per-day).    |
//| - Each level is a horizontal ray (OBJ_TREND, ray_right=true)      |
//|   anchored at the first bar of the period it describes, plus a   |
//|   small OBJ_TEXT label at that anchor carrying the date and the  |
//|   level's name - this is the "titled with dates" requirement;    |
//|   object *names* embed the date too (for lookup/cleanup) but are |
//|   not themselves visible on the chart, hence the paired label.   |
//| - Objects are anchored at REAL observed bar times (never a       |
//|   synthesized instant) - the same "measured, not synthesized"    |
//|   discipline vo.market.levels.AnchorPrice documents on the       |
//|   Python side.                                                   |
//| - Kept for InpLookbackDays calendar days, then deleted - not     |
//|   left to accumulate forever.                                    |
//| - Time/price-anchored objects are visible on every timeframe of  |
//|   the chart they're attached to (MT5 object coordinates are      |
//|   absolute time/price, not bar-index-based), which is what       |
//|   satisfies "on all timeframes" without per-timeframe            |
//|   duplication - attach this indicator once per chart you want    |
//|   levels on, not once per timeframe.                             |
//|                                                                  |
//| COMPILE/VERIFY NOTE: this file has not been compiled or run      |
//| against a live terminal from this session (no MT5 terminal is    |
//| reachable here) - it was written to match VO_Bridge.mq5's own    |
//| syntax and structure closely, but needs the same validation step |
//| VO_Bridge.mq5 got: compile in MetaEditor, attach to a chart with  |
//| sufficient M1 history loaded, and visually confirm the levels    |
//| against a known day before trusting it.                          |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

#include <VectorOdyssey/VO_ReferenceLevelsMath.mqh>

//--- Object naming/versioning. Bumping the prefix on a breaking change
//    to the drawing convention orphans old objects cleanly (they stop
//    matching the cleanup scan and can be removed by hand) instead of
//    silently reinterpreting them.
#define VO_REF_PREFIX "VO_REF_v1"

input group "=== Broker clock (must match config/settings/brokers.yaml) ==="
input string InpBrokerDstCalendar        = "US";     // "US" | "EU" | "NONE"
input double InpBrokerStandardUtcOffset  = 2.0;      // broker standard (winter) UTC offset, hours
input double InpBrokerDstUtcOffset       = 3.0;      // broker DST (summer) UTC offset, hours

input group "=== Session model (must match config/settings/sessions.yaml) ==="
input string InpTradingDayOpens = "18:00";  // NY wall-clock trading-day boundary
input string InpRthStart        = "09:30";  // NY wall-clock RTH open
input string InpSettlementTime  = "16:14";  // NY wall-clock settlement (a close, not an open)

input group "=== Drawing scope ==="
input int    InpLookbackDays            = 60;     // calendar days of history to keep drawn
input int    InpMaxM1BarsToScan         = 200000; // safety cap (~60d window + ~40d lookback buffer)
input bool   InpDrawPreviousDayLevels   = true;
input bool   InpDrawPreviousWeekLevels  = true;
input bool   InpDrawPreviousMonthLevels = true;
input bool   InpDrawSessionOpens        = true;
input bool   InpDrawSettlement          = true;

input group "=== Style ==="
input color  InpColorDay        = clrDodgerBlue;
input color  InpColorWeek       = clrOrange;
input color  InpColorMonth      = clrMagenta;
input color  InpColorSessionOpen= clrSilver;
input color  InpColorSettlement = clrGold;
input ENUM_LINE_STYLE InpLineStyle = STYLE_DOT;
input int    InpLineWidth = 1;
input int    InpFontSize  = 7;

//--- Derived, parsed once in OnInit.
ENUM_VO_DstCalendar g_broker_calendar;
int g_trading_day_opens_min;
int g_rth_start_min;
int g_settlement_min;

//--- Rescan bookkeeping - a full rescan is O(bars), so it only runs
//    once per new M1 bar (levels cannot change faster than that), plus
//    once at startup. Cleanup of expired objects runs once per new
//    trading day, not every rescan - it is a chart-wide object scan.
datetime g_last_seen_m1_open = 0;
datetime g_last_cleanup_trading_day = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpBrokerDstCalendar == "US")
      g_broker_calendar = VO_DST_US;
   else if(InpBrokerDstCalendar == "EU")
      g_broker_calendar = VO_DST_EU;
   else
      g_broker_calendar = VO_DST_NONE;

   g_trading_day_opens_min = VO_ParseHHMM(InpTradingDayOpens);
   g_rth_start_min         = VO_ParseHHMM(InpRthStart);
   g_settlement_min        = VO_ParseHHMM(InpSettlementTime);

   IndicatorSetString(INDICATOR_SHORTNAME, "VO Reference Levels");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // Leave objects in place on ordinary chart-close/recompile/timeframe
   // change; remove them only when the user explicitly detaches this
   // indicator (or reloads a template) - standard MT5 indicator hygiene,
   // matching how most chart-drawing indicators behave.
   if(reason == REASON_REMOVE || reason == REASON_TEMPLATE)
      VO_DeleteAllObjects();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[],
                const double &close[], const long &tick_volume[], const long &volume[],
                const int &spread[])
  {
   const datetime current_m1_open = iTime(_Symbol, PERIOD_M1, 0);
   if(current_m1_open != g_last_seen_m1_open)
     {
      g_last_seen_m1_open = current_m1_open;
      VO_RescanAndDraw();
     }
   return(rates_total);
  }

//+------------------------------------------------------------------+
//| The one heavy pass: fetch M1 history, classify every bar into a  |
//| trading day (and that day's week/month), then draw/refresh every |
//| level within the lookback window and clean up anything expired.  |
//+------------------------------------------------------------------+
void VO_RescanAndDraw()
  {
   MqlRates rates[];
   ArraySetAsSeries(rates, false); // oldest-to-newest indexing, explicit
   const int copied = CopyRates(_Symbol, PERIOD_M1, 0, InpMaxM1BarsToScan, rates);
   if(copied <= 1)
      return; // not enough M1 history loaded yet - nothing to draw

   // ---- Pass 1: classify every bar (NY-naive time, for grouping only -
   // object anchors below use the bar's own native chart time, never
   // this NY-naive value directly).
   datetime bar_ny_naive[];
   ArrayResize(bar_ny_naive, copied);
   for(int i = 0; i < copied; i++)
     {
      int status;
      const datetime utc = VO_BrokerNaiveToUtc(rates[i].time, g_broker_calendar,
                                                InpBrokerStandardUtcOffset, InpBrokerDstUtcOffset, status);
      bar_ny_naive[i] = VO_UtcToNyNaive(utc);
     }

   // ---- Pass 2: group into trading days. Monotonic non-decreasing
   // classification (see VO_ReferenceLevelsMath.mqh header) means a
   // single forward pass suffices - identical in spirit to the Python
   // side's dict-based grouping, just array-indexed instead.
   datetime day_key[];    // NY midnight, one entry per distinct trading day
   int      day_start[];  // first bar index (into rates[]) of that day
   int      day_end[];    // last bar index (inclusive) of that day
   int      day_count = 0;

   for(int i = 0; i < copied; i++)
     {
      const datetime td = VO_TradingDayOf(bar_ny_naive[i], g_trading_day_opens_min);
      if(day_count == 0 || td != day_key[day_count - 1])
        {
         day_count++;
         ArrayResize(day_key, day_count);
         ArrayResize(day_start, day_count);
         ArrayResize(day_end, day_count);
         day_key[day_count - 1] = td;
         day_start[day_count - 1] = i;
        }
      day_end[day_count - 1] = i;
     }

   if(day_count < 1)
      return;

   // ---- Per-day week/month grouping keys (Monday-of-week; first-of-month).
   datetime week_key[];
   datetime month_key[];
   ArrayResize(week_key, day_count);
   ArrayResize(month_key, day_count);
   for(int d = 0; d < day_count; d++)
     {
      const int dow = VO_DayOfWeek(day_key[d]);              // 0=Sunday..6=Saturday
      const int days_since_monday = (dow + 6) % 7;            // Mon=0 .. Sun=6
      week_key[d] = day_key[d] - days_since_monday * 86400;
      MqlDateTime dt;
      TimeToStruct(day_key[d], dt);
      month_key[d] = VO_MakeNaive(dt.year, dt.mon, 1);
     }

   const datetime today_ny_midnight = day_key[day_count - 1];
   const datetime cutoff = today_ny_midnight - (datetime)InpLookbackDays * 86400;

   // ---- Daily levels: previous-day O/H/L/C, trading-day open, RTH
   // open, settlement - one object set per trading day in [cutoff, today].
   for(int d = 0; d < day_count; d++)
     {
      if(day_key[d] < cutoff)
         continue;
      VO_DrawDailyLevels(rates, bar_ny_naive, day_key, day_start, day_end, d);
     }

   // ---- Weekly levels: previous-week O/H/L/C + week open - one object
   // set per COMPLETED week (never the current, still-in-progress one),
   // whose Monday falls in [cutoff, today].
   if(InpDrawPreviousWeekLevels || InpDrawSessionOpens)
     {
      const datetime current_week_key = week_key[day_count - 1];
      datetime last_drawn_week = 0;
      for(int d = 0; d < day_count; d++)
        {
         if(week_key[d] == current_week_key)
            continue; // in progress - never "previous" yet
         if(week_key[d] == last_drawn_week)
            continue; // already drawn for this week
         if(week_key[d] < cutoff)
            continue;
         last_drawn_week = week_key[d];
         VO_DrawWeeklyLevels(rates, day_start, day_end, week_key, day_count, d);
        }
     }

   // ---- Monthly levels: same pattern, one object set per completed month.
   if(InpDrawPreviousMonthLevels || InpDrawSessionOpens)
     {
      const datetime current_month_key = month_key[day_count - 1];
      datetime last_drawn_month = 0;
      for(int d = 0; d < day_count; d++)
        {
         if(month_key[d] == current_month_key)
            continue;
         if(month_key[d] == last_drawn_month)
            continue;
         if(month_key[d] < cutoff)
            continue;
         last_drawn_month = month_key[d];
         VO_DrawMonthlyLevels(rates, day_start, day_end, month_key, day_count, d);
        }
     }

   ChartRedraw(0);

   // ---- Cleanup: once per new trading day only (chart-wide object scan).
   if(today_ny_midnight != g_last_cleanup_trading_day)
     {
      g_last_cleanup_trading_day = today_ny_midnight;
      VO_DeleteExpiredObjects(cutoff);
     }
  }

//+------------------------------------------------------------------+
//| Daily levels for trading day index d: previous-day O/H/L/C (needs |
//| d>=1), trading-day open, RTH open, settlement.                    |
//+------------------------------------------------------------------+
void VO_DrawDailyLevels(const MqlRates &rates[], const datetime &bar_ny_naive[],
                         const datetime &day_key[], const int &day_start[], const int &day_end[],
                         const int d)
  {
   const string date_str = VO_FormatDate(day_key[d]);

   if(InpDrawPreviousDayLevels && d >= 1)
     {
      const int ps = day_start[d - 1];
      const int pe = day_end[d - 1];
      double hi = rates[ps].high, lo = rates[ps].low;
      for(int i = ps; i <= pe; i++)
        {
         if(rates[i].high > hi) hi = rates[i].high;
         if(rates[i].low  < lo) lo = rates[i].low;
        }
      const datetime anchor = rates[ps].time;
      VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|pd_open",  anchor, rates[ps].open,
                   "PDO " + date_str, InpColorDay);
      VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|pd_high",  anchor, hi,
                   "PDH " + date_str, InpColorDay);
      VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|pd_low",   anchor, lo,
                   "PDL " + date_str, InpColorDay);
      VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|pd_close", anchor, rates[pe].close,
                   "PDC " + date_str, InpColorDay);
     }

   if(InpDrawSessionOpens)
     {
      const int ds = day_start[d];
      VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|s_trading_day_open", rates[ds].time, rates[ds].open,
                   "Day Open " + date_str, InpColorSessionOpen);

      const datetime target_ny = VO_InstantForTradingDay(day_key[d], g_rth_start_min, g_trading_day_opens_min);
      int rth_idx = -1;
      for(int i = day_start[d]; i <= day_end[d]; i++)
        {
         if(bar_ny_naive[i] >= target_ny) { rth_idx = i; break; }
        }
      if(rth_idx >= 0)
         VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|s_rth_open", rates[rth_idx].time, rates[rth_idx].open,
                      "RTH Open " + date_str, InpColorSessionOpen);
     }

   if(InpDrawSettlement)
     {
      const datetime target_ny = VO_InstantForTradingDay(day_key[d], g_settlement_min, g_trading_day_opens_min);
      int settle_idx = -1;
      for(int i = day_start[d]; i <= day_end[d]; i++)
        {
         if(bar_ny_naive[i] >= target_ny) break;
         settle_idx = i;
        }
      if(settle_idx >= 0)
         VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|settlement", rates[settle_idx].time, rates[settle_idx].close,
                      "Settlement " + date_str, InpColorSettlement);
     }
  }

//+------------------------------------------------------------------+
//| The NY-naive instant `time_of_day_minutes` occurs at, for trading  |
//| day `trading_day` specifically - ported from _instant_for_trading_ |
//| day (vo/time/levels.py): asks whether combining trading_day's own  |
//| calendar date with the time-of-day still resolves back to          |
//| trading_day (it does, unless trading_day_opens is itself later     |
//| than time_of_day - e.g. RTH/settlement fall on trading_day+1 for   |
//| an 18:00-opening instrument like US100.n) - never assumes a fixed  |
//| +0/+1 day offset.                                                  |
//+------------------------------------------------------------------+
datetime VO_InstantForTradingDay(const datetime trading_day_midnight, const int time_of_day_minutes,
                                  const int trading_day_opens_minutes)
  {
   const datetime candidate = trading_day_midnight + (datetime)time_of_day_minutes * 60;
   if(VO_TradingDayOf(candidate, trading_day_opens_minutes) == trading_day_midnight)
      return candidate;
   return trading_day_midnight + 86400 + (datetime)time_of_day_minutes * 60;
  }

//+------------------------------------------------------------------+
//| Weekly levels for the completed week that day index d belongs to: |
//| previous-week O/H/L/C over every day sharing week_key[d], plus     |
//| week open (the first bar of that week's own first day).            |
//+------------------------------------------------------------------+
void VO_DrawWeeklyLevels(const MqlRates &rates[], const int &day_start[], const int &day_end[],
                         const datetime &week_key[], const int day_count, const int d)
  {
   const datetime target_week = week_key[d];
   int first_day = -1, last_day = -1;
   for(int i = 0; i < day_count; i++)
     {
      if(week_key[i] != target_week)
         continue;
      if(first_day < 0)
         first_day = i;
      last_day = i;
     }
   if(first_day < 0)
      return;

   const string date_str = VO_FormatDate(target_week); // Monday of that week

   if(InpDrawPreviousWeekLevels)
     {
      const int ps = day_start[first_day];
      const int pe = day_end[last_day];
      double hi = rates[ps].high, lo = rates[ps].low;
      for(int i = ps; i <= pe; i++)
        {
         if(rates[i].high > hi) hi = rates[i].high;
         if(rates[i].low  < lo) lo = rates[i].low;
        }
      const datetime anchor = rates[ps].time;
      VO_DrawLevel(VO_REF_PREFIX + "|W|" + date_str + "|pw_open",  anchor, rates[ps].open,
                   "PWO wk " + date_str, InpColorWeek);
      VO_DrawLevel(VO_REF_PREFIX + "|W|" + date_str + "|pw_high",  anchor, hi,
                   "PWH wk " + date_str, InpColorWeek);
      VO_DrawLevel(VO_REF_PREFIX + "|W|" + date_str + "|pw_low",   anchor, lo,
                   "PWL wk " + date_str, InpColorWeek);
      VO_DrawLevel(VO_REF_PREFIX + "|W|" + date_str + "|pw_close", anchor, rates[pe].close,
                   "PWC wk " + date_str, InpColorWeek);
     }

   if(InpDrawSessionOpens)
     {
      const int ds = day_start[first_day];
      VO_DrawLevel(VO_REF_PREFIX + "|W|" + date_str + "|s_week_open", rates[ds].time, rates[ds].open,
                   "Week Open " + date_str, InpColorSessionOpen);
     }
  }

//+------------------------------------------------------------------+
//| Monthly levels - same pattern as weekly, keyed by first-of-month. |
//+------------------------------------------------------------------+
void VO_DrawMonthlyLevels(const MqlRates &rates[], const int &day_start[], const int &day_end[],
                          const datetime &month_key[], const int day_count, const int d)
  {
   const datetime target_month = month_key[d];
   int first_day = -1, last_day = -1;
   for(int i = 0; i < day_count; i++)
     {
      if(month_key[i] != target_month)
         continue;
      if(first_day < 0)
         first_day = i;
      last_day = i;
     }
   if(first_day < 0)
      return;

   const string date_str = VO_FormatYearMonth(target_month);

   if(InpDrawPreviousMonthLevels)
     {
      const int ps = day_start[first_day];
      const int pe = day_end[last_day];
      double hi = rates[ps].high, lo = rates[ps].low;
      for(int i = ps; i <= pe; i++)
        {
         if(rates[i].high > hi) hi = rates[i].high;
         if(rates[i].low  < lo) lo = rates[i].low;
        }
      const datetime anchor = rates[ps].time;
      VO_DrawLevel(VO_REF_PREFIX + "|M|" + date_str + "|pm_open",  anchor, rates[ps].open,
                   "PMO " + date_str, InpColorMonth);
      VO_DrawLevel(VO_REF_PREFIX + "|M|" + date_str + "|pm_high",  anchor, hi,
                   "PMH " + date_str, InpColorMonth);
      VO_DrawLevel(VO_REF_PREFIX + "|M|" + date_str + "|pm_low",   anchor, lo,
                   "PML " + date_str, InpColorMonth);
      VO_DrawLevel(VO_REF_PREFIX + "|M|" + date_str + "|pm_close", anchor, rates[pe].close,
                   "PMC " + date_str, InpColorMonth);
     }

   if(InpDrawSessionOpens)
     {
      const int ds = day_start[first_day];
      VO_DrawLevel(VO_REF_PREFIX + "|M|" + date_str + "|s_month_open", rates[ds].time, rates[ds].open,
                   "Month Open " + date_str, InpColorSessionOpen);
     }
  }

//+------------------------------------------------------------------+
//| Create-or-update one horizontal ray + its dated text label. Ray   |
//| left-anchored at `anchor_time`/`price`, ray_right=true so it       |
//| extends forward indefinitely - idempotent: if the named objects   |
//| already exist (a re-scan of an unchanged historical day), their   |
//| price/time/text are simply reset to the same values, never        |
//| duplicated.                                                       |
//+------------------------------------------------------------------+
void VO_DrawLevel(const string name, const datetime anchor_time, const double price,
                  const string label_text, const color clr)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_TREND, 0, anchor_time, price, anchor_time + 60, price);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, true);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
     }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, anchor_time);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, price);
   ObjectSetInteger(0, name, OBJPROP_TIME, 1, anchor_time + 60);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 1, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, InpLineStyle);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, InpLineWidth);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, label_text + StringFormat(": %s", DoubleToString(price, _Digits)));

   const string label_name = name + "|lbl";
   if(ObjectFind(0, label_name) < 0)
     {
      ObjectCreate(0, label_name, OBJ_TEXT, 0, anchor_time, price);
      ObjectSetInteger(0, label_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, label_name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, label_name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, label_name, OBJPROP_FONTSIZE, InpFontSize);
     }
   ObjectSetInteger(0, label_name, OBJPROP_TIME, 0, anchor_time);
   ObjectSetDouble(0, label_name, OBJPROP_PRICE, 0, price);
   ObjectSetString(0, label_name, OBJPROP_TEXT, label_text);
   ObjectSetInteger(0, label_name, OBJPROP_COLOR, clr);
  }

//+------------------------------------------------------------------+
//| Delete every VO_REF object whose embedded date is older than      |
//| `cutoff` (an NY midnight). Names are `PREFIX|D|YYYY-MM-DD|...`,   |
//| `PREFIX|W|YYYY-MM-DD|...` or `PREFIX|M|YYYY-MM|...` - the date     |
//| token is always the 3rd `|`-delimited field.                       |
//+------------------------------------------------------------------+
void VO_DeleteExpiredObjects(const datetime cutoff)
  {
   const string prefix = VO_REF_PREFIX + "|";
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
     {
      const string name = ObjectName(0, i);
      if(StringFind(name, prefix) != 0)
         continue;
      const datetime object_date = VO_ParseDateToken(name);
      if(object_date != 0 && object_date < cutoff)
         ObjectDelete(0, name);
     }
  }

void VO_DeleteAllObjects()
  {
   const string prefix = VO_REF_PREFIX + "|";
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
     {
      const string name = ObjectName(0, i);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
     }
  }

//+------------------------------------------------------------------+
//| Extracts the date token from an object name and parses it as an   |
//| NY midnight. Returns 0 if the name doesn't match the expected      |
//| shape - callers must treat 0 as "don't touch it", not "epoch".     |
//+------------------------------------------------------------------+
datetime VO_ParseDateToken(const string name)
  {
   string parts[];
   const int n = StringSplit(name, StringGetCharacter("|", 0), parts);
   if(n < 3)
      return 0;
   const string token = parts[2]; // "YYYY-MM-DD" or "YYYY-MM"
   const int first_dash = StringFind(token, "-");
   const int second_dash = StringFind(token, "-", first_dash + 1);
   if(first_dash < 0)
      return 0;
   const int year = (int)StringToInteger(StringSubstr(token, 0, first_dash));
   if(second_dash < 0)
     {
      const int month = (int)StringToInteger(StringSubstr(token, first_dash + 1));
      return VO_MakeNaive(year, month, 1);
     }
   const int month = (int)StringToInteger(StringSubstr(token, first_dash + 1, second_dash - first_dash - 1));
   const int day   = (int)StringToInteger(StringSubstr(token, second_dash + 1));
   return VO_MakeNaive(year, month, day);
  }

//+------------------------------------------------------------------+
string VO_FormatDate(const datetime naive)
  {
   MqlDateTime dt;
   TimeToStruct(naive, dt);
   return StringFormat("%04d-%02d-%02d", dt.year, dt.mon, dt.day);
  }

string VO_FormatYearMonth(const datetime naive)
  {
   MqlDateTime dt;
   TimeToStruct(naive, dt);
   return StringFormat("%04d-%02d", dt.year, dt.mon);
  }
