//+------------------------------------------------------------------+
//| VO_ReferenceLevels.mq5                                           |
//|                                                                  |
//| Draws Phase 7's reference levels (architecture/vo-time-engine.md |
//| S6) on an MT5 chart: previous day/week/month O/H/L/C, the four    |
//| session opens (trading-day/week/month/RTH), settlement, the       |
//| Opening Range Gap (ORG, [VO-D]), and session-boundary lines       |
//| (ASIA/LONDON/NY_AM/NY_PM) for context.                             |
//|                                                                  |
//| SPLIT NOTE (2026-09-18): this file previously ALSO drew Phase     |
//| 13's regime bands/markers/session-stat panel (merged in here at   |
//| the user's own explicit v30/v31 direction). The user has since     |
//| asked for the opposite: this file should carry ONLY reference     |
//| levels plus day/session boundaries -- regime bands, markers, the   |
//| session-stat panel, and the ER/Hurst evidence tooltips have moved  |
//| back into a standalone VO_Regime.mq5 (recreated, not the same      |
//| retired v30 file verbatim, but built from it). Session-boundary    |
//| (SESN) lines stay HERE exclusively, per the user's own explicit    |
//| choice -- VO_Regime.mq5 reads the same feed file but ignores SESN  |
//| lines entirely, so attaching both indicators to one chart never    |
//| double-draws a session line. See VO_Regime.mq5's own header for    |
//| the regime-side detail.                                            |
//|                                                                  |
//| OPEN ITEM, NOT YET BUILT: the user has separately asked whether    |
//| "regime change and settings can reference the levels and session   |
//| breaks... for back testing and forward testing and back            |
//| reference." Both indicators already share one feed file and can    |
//| be attached to the same chart, so a band/marker can already be     |
//| read visually against a reference level or session line on the     |
//| same screen. Whether the regime CLASSIFIER                         |
//| (vo.observation.regime.RegimeEngine) should additionally take       |
//| reference levels as an input signal is a separate, gate-G6-         |
//| reviewed design decision that has NOT been made and is NOT          |
//| implemented anywhere in this file or in Python -- doing so without  |
//| an explicit design-lock conversation would be exactly the           |
//| premature-import drift the curriculum discipline exists to          |
//| prevent. Revisit only once that scope is confirmed.                 |
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
//| the reversal is scoped to visualization only, lives entirely      |
//| outside VO_EA's own process, and never feeds back into it.        |
//| VO_EA continues to run, unaffected, with this indicator removed   |
//| from the chart entirely.                                          |
//|                                                                  |
//| REFERENCE LEVELS -- WHY THE MATH IS RE-DERIVED HERE INSTEAD OF   |
//| ASKING PYTHON: See VO_ReferenceLevelsMath.mqh's own header -      |
//| short version: MT5 chart objects are MQL5-only, this project's    |
//| MT5<->Python bridge is one-way (MT5 -> Python), and building a    |
//| reverse command channel just to draw lines would be significant   |
//| new infrastructure for a purely visual concern. The reference-    |
//| level math (vo.time.levels.ReferenceLevelEngine on the Python     |
//| side) is therefore reimplemented natively here, against the exact |
//| same config values as config/settings/sessions.yaml (RTH          |
//| 09:30-16:00, settlement 16:14 ET, trading day opens 18:00 ET) -    |
//| kept in sync by hand, the same way this file's DST rule is kept   |
//| in sync with vo/time/probe.py's by hand.                          |
//|                                                                  |
//| REFERENCE LEVELS -- DRAWING CONVENTION (confirmed with the user   |
//| before writing this):                                             |
//| - Every distinct (period, level) gets its own object, dated with |
//|   the period it was MEASURED from - not re-drawn or overwritten  |
//|   for every later day that references it as "previous X". Daily  |
//|   levels get one object set per TRADING DAY; weekly/monthly       |
//|   levels get one object set per completed WEEK/MONTH.             |
//| - Each level is a horizontal ray (OBJ_TREND, ray_right=true)      |
//|   anchored at the first bar of the period it describes, plus a   |
//|   small OBJ_TEXT label at that anchor carrying the date and the  |
//|   level's name.                                                  |
//| - Objects are anchored at REAL observed bar times (never a       |
//|   synthesized instant).                                          |
//| - Kept for InpLookbackDays calendar days, then deleted.           |
//| - Time/price-anchored objects are visible on every timeframe of  |
//|   the chart they're attached to - attach this indicator once per |
//|   chart you want levels on, not once per timeframe.               |
//|                                                                  |
//| SESSION-BOUNDARY LINES -- this file draws only the SESN line type  |
//| from vo.telemetry.regime_feed's shared feed file (the same feed    |
//| VO_Regime.mq5 also reads, for its own BAND/MARK/SSTAT lines --     |
//| see that file's own header for the full feed-format reference).    |
//| A session transition (ASIA->LONDON, etc) is "a clock event,        |
//| nothing more" (vo-time-engine.md SS4) -- it carries no canonical    |
//| record and plays no role in RegimeEngine (gate G2). It is drawn     |
//| here so price action can be read directly against the session it    |
//| fell in, using the identical vo.time.sessions lookup the backtest   |
//| report's session breakdown already uses -- never a second session   |
//| concept. Format: SESN | at_epoch | from_session | to_session        |
//| (4 fields). at_epoch is a broker-SERVER epoch second (what MT5      |
//| chart time uses).                                                   |
//|                                                                  |
//| SYMBOL: by default the feed for the chart's own _Symbol is read    |
//| (<_Symbol>_regime.feed). Set InpSymbolOverride to read a different  |
//| symbol's feed.                                                      |
//|                                                                  |
//| REFRESH: re-read on every new chart bar and on a short timer, so   |
//| session lines appear as scripts/publish_regime.py --watch           |
//| republishes. A full delete-then-redraw runs each pass -- a run       |
//| whose session boundaries changed retroactively never leaves a       |
//| stale line behind.                                                  |
//|                                                                  |
//| CLEAN REMOVAL: OnDeinit sweeps every VO_SESN_PREFIX-tagged chart    |
//| object (this file's own session-line objects) when the user         |
//| explicitly removes this indicator or applies a new template          |
//| (REASON_REMOVE / REASON_TEMPLATE) -- nothing is left behind. The     |
//| reference-level objects (VO_REF_PREFIX) get the same treatment via   |
//| VO_DeleteAllObjects. Objects are intentionally left in place on an   |
//| ordinary recompile/timeframe-change/chart-close -- standard MT5      |
//| indicator hygiene.                                                   |
//|                                                                  |
//| COMPILE/VERIFY NOTE: not yet compiled or run against a live         |
//| terminal from this environment since the split (no MT5 terminal is  |
//| reachable here). Needs: compile in MetaEditor, attach to a chart     |
//| with sufficient M1 history loaded, run scripts/backtest_regime.py    |
//| or publish_regime.py so a feed exists, remove/re-add this indicator  |
//| once (clears any stale VO_RGM_v3-prefixed band/marker/panel objects  |
//| left over from before the split -- those now belong to a separate    |
//| VO_Regime.mq5 attachment), and visually confirm levels, ORG, and     |
//| session-boundary lines all land correctly, with no regime bands/     |
//| markers/panel drawn by this file (those come from VO_Regime.mq5      |
//| alone, if attached).                                                 |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

#include <VectorOdyssey/VO_ReferenceLevelsMath.mqh>

//--- Object naming/versioning. Bumping a prefix on a breaking change to
//    that concern's OWN drawing convention orphans its old objects
//    cleanly (they stop matching the cleanup scan) instead of silently
//    reinterpreting them. The two concerns keep independent prefixes
//    and independent version numbers -- a regime feed-format bump never
//    needs to touch reference-level objects and vice versa.
#define VO_REF_PREFIX "VO_REF_v1"
// Own prefix for this file's session-line objects, distinct from
// VO_Regime.mq5's VO_RGM_v3 (its BAND/MARK/SSTAT objects) -- the two
// indicators' delete-then-redraw sweeps must never touch each other's
// objects, even though both read the same feed file.
#define VO_SESN_PREFIX "VO_REF_SESN_v1"
#define VO_TAG_SESN "SESN"
#define VO_FEED_SESSION_FIELDS 4

input group "=== Broker clock (must match config/settings/brokers.yaml) ==="
input string InpBrokerDstCalendar        = "US";     // "US" | "EU" | "NONE"
input double InpBrokerStandardUtcOffset  = 2.0;      // broker standard (winter) UTC offset, hours
input double InpBrokerDstUtcOffset       = 3.0;      // broker DST (summer) UTC offset, hours

input group "=== Session model (must match config/settings/sessions.yaml) ==="
input string InpTradingDayOpens = "18:00";  // NY wall-clock trading-day boundary
input string InpRthStart        = "09:30";  // NY wall-clock RTH open
input string InpSettlementTime  = "16:14";  // NY wall-clock settlement (a close, not an open)

input group "=== Reference-level drawing scope ==="
input int    InpLookbackDays            = 60;     // calendar days of history to keep drawn
input int    InpMaxM1BarsToScan         = 200000; // safety cap (~60d window + ~40d lookback buffer)
input bool   InpDrawPreviousDayLevels   = true;
input bool   InpDrawPreviousWeekLevels  = true;
input bool   InpDrawPreviousMonthLevels = true;
input bool   InpDrawSessionOpens        = true;
input bool   InpDrawSettlement          = true;
input bool   InpDrawOpeningRangeGap      = true;  // ORG: prior settlement <-> today's RTH open

input group "=== Reference-level style ==="
input color  InpColorDay        = clrDodgerBlue;
input color  InpColorWeek       = clrOrange;
input color  InpColorMonth      = clrMagenta;
input color  InpColorSessionOpen= clrSilver;
input color  InpColorSettlement = clrGold;
input color  InpColorORG        = clrAqua;
input ENUM_LINE_STYLE InpLineStyle = STYLE_DOT;
input int    InpLineWidth = 1;
input int    InpFontSize  = 7;

input group "=== Feed source, shared with VO_Regime.mq5 (MQL5\\Files\\<subdir>\\<symbol>_regime.feed) ==="
input string InpFeedSubdir     = "VectorOdyssey"; // must match VO_Bridge InpOutputSubdir / vo_ea.yaml wire.dir
input string InpSymbolOverride = "";              // blank = this chart's symbol; else e.g. "US100"

input group "=== Feed refresh ==="
input int    InpRefreshSeconds = 5;   // re-read the feed on this timer (publish_regime --watch interval)

input group "=== Session boundary lines (ASIA/LONDON/NY_AM/NY_PM, config/settings/sessions.yaml) ==="
input bool   InpShowSessionLines  = true;
input color  InpSessionLineColor  = clrSlateGray;
input int    InpSessionLineWidth  = 1;
input bool   InpShowSessionLabel  = true;

//--- Derived, parsed once in OnInit.
ENUM_VO_DstCalendar g_broker_calendar;
int g_trading_day_opens_min;
int g_rth_start_min;
int g_settlement_min;

//--- Rescan bookkeeping - a full reference-level rescan is O(bars), so it
//    only runs once per new M1 bar (levels cannot change faster than
//    that), plus once at startup. Cleanup of expired objects runs once
//    per new trading day, not every rescan - it is a chart-wide scan.
//    The regime feed re-read is independent -- keyed off the CHART's
//    own current timeframe bar, not M1 -- since a band is drawn on
//    whatever period the chart is actually showing.
datetime g_last_seen_m1_open = 0;
datetime g_last_cleanup_trading_day = 0;
datetime g_last_seen_bar_open = 0;
// The most recent M1 bar time seen by the last VO_RescanAndDraw pass --
// every reference-level ray's TEXT label (never the ray itself, which
// stays anchored at its own dated origin) is repositioned here each
// rescan, so the label always reads near the chart's live/right edge
// instead of buried at the ray's left-hand origin.
datetime g_live_edge_time = 0;

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

   if(InpRefreshSeconds > 0)
      EventSetTimer(InpRefreshSeconds);
   VO_ReadAndDraw(); // session lines: draw once immediately, don't wait for a timer/new bar

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   // Leave objects in place on ordinary chart-close/recompile/timeframe
   // change; remove them only when the user explicitly detaches this
   // indicator (or reloads a template) - standard MT5 indicator hygiene.
   if(reason == REASON_REMOVE || reason == REASON_TEMPLATE)
     {
      VO_DeleteAllObjects();
      VO_DeleteAllSessionLineObjects();
     }
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   VO_ReadAndDraw();
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

   const datetime current_bar_open = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(current_bar_open != g_last_seen_bar_open)
     {
      g_last_seen_bar_open = current_bar_open;
      VO_ReadAndDraw();
     }

   return(rates_total);
  }

//+------------------------------------------------------------------+
//| REFERENCE LEVELS                                                  |
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

   // Every level label drawn this pass reads its x-position from here,
   // so all of them - even a PDH from days ago - track the live edge.
   g_live_edge_time = rates[copied - 1].time;

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

   // ORG (Opening Range Gap, [VO-D]): the previous trading day's settlement
   // (its 16:14 close) against THIS trading day's RTH open (09:30). The open
   // is the gap's high (gap up) or low (gap down); equilibrium is the 50%
   // midpoint. Native reimplementation of vo.market.opening_range /
   // vo.time.levels.ReferenceLevelEngine.opening_range_gap (same one-way-wire
   // reasoning as the rest of this file). Needs a previous day (d>=1).
   if(InpDrawOpeningRangeGap && d >= 1)
     {
      // prior settlement = close of the last bar before 16:14 on day d-1.
      const datetime prior_settle_ny = VO_InstantForTradingDay(day_key[d - 1], g_settlement_min, g_trading_day_opens_min);
      int prior_settle_idx = -1;
      for(int i = day_start[d - 1]; i <= day_end[d - 1]; i++)
        {
         if(bar_ny_naive[i] >= prior_settle_ny) break;
         prior_settle_idx = i;
        }
      // today's RTH open = open of the first bar at/after 09:30 on day d.
      const datetime rth_ny = VO_InstantForTradingDay(day_key[d], g_rth_start_min, g_trading_day_opens_min);
      int rth_idx = -1;
      for(int i = day_start[d]; i <= day_end[d]; i++)
        {
         if(bar_ny_naive[i] >= rth_ny) { rth_idx = i; break; }
        }
      if(prior_settle_idx >= 0 && rth_idx >= 0)
        {
         const double settle_price = rates[prior_settle_idx].close;
         const double open_price   = rates[rth_idx].open;
         const double org_high = MathMax(open_price, settle_price);
         const double org_low  = MathMin(open_price, settle_price);
         const double org_eq   = (org_high + org_low) / 2.0;
         const datetime anchor = rates[rth_idx].time;  // ORG is defined from the RTH open onward

         const string open_side = (open_price > settle_price) ? " (open)"
                                : (open_price < settle_price) ? ""
                                : " (flat)";
         const string low_side  = (open_price < settle_price) ? " (open)" : "";

         VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|org_high", anchor, org_high,
                      "ORG H " + date_str + open_side, InpColorORG);
         VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|org_low", anchor, org_low,
                      "ORG L " + date_str + low_side, InpColorORG);
         VO_DrawLevel(VO_REF_PREFIX + "|D|" + date_str + "|org_eq", anchor, org_eq,
                      "ORG EQ " + date_str, InpColorORG);
        }
     }
  }

//+------------------------------------------------------------------+
//| The NY-naive instant `time_of_day_minutes` occurs at, for trading  |
//| day `trading_day` specifically - ported from _instant_for_trading_ |
//| day (vo/time/levels.py): asks whether combining trading_day's own  |
//| calendar date with the time-of-day still resolves back to          |
//| trading_day (under the CME trade-date convention it does for       |
//| RTH 09:30 and settlement 16:14, which fall on trading_day's own    |
//| date; it is the trading-day OPEN 18:00 that falls on trading_day-1)|
//| - never assumes a fixed +0/+1 day offset.                          |
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
//| duplicated. The RAY's own anchor stays at anchor_time (where the  |
//| level was measured, never moved) - only the TEXT label's x-      |
//| position tracks g_live_edge_time, right-anchored, so the label   |
//| always reads near the chart's live/right edge rather than        |
//| sitting at the ray's dated origin on the left.                   |
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

   // The label's x-position is g_live_edge_time, not anchor_time - see
   // this function's own header comment. Falls back to anchor_time only
   // on the (should-never-happen) first call before any rescan has run.
   const datetime label_time = (g_live_edge_time > 0) ? g_live_edge_time : anchor_time;

   const string label_name = name + "|lbl";
   if(ObjectFind(0, label_name) < 0)
     {
      ObjectCreate(0, label_name, OBJ_TEXT, 0, label_time, price);
      ObjectSetInteger(0, label_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, label_name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, label_name, OBJPROP_ANCHOR, ANCHOR_RIGHT_LOWER);
      ObjectSetInteger(0, label_name, OBJPROP_FONTSIZE, InpFontSize);
     }
   ObjectSetInteger(0, label_name, OBJPROP_TIME, 0, label_time);
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
   // Prefix-filtered bulk delete, one terminal call (2026-09-18, finding C1).
   // VO_DeleteExpiredObjects above still walks, because it must parse each
   // name's date token -- it runs at day rollover only, not per refresh.
   ObjectsDeleteAll(0, VO_REF_PREFIX + "|", -1, -1);
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

//+------------------------------------------------------------------+
//| SESSION BOUNDARY LINES                                            |
//+------------------------------------------------------------------+
//| Feed path relative to MQL5\Files\ -- FileOpen resolves it there.  |
//| Shared with VO_Regime.mq5 -- same feed file, each indicator reads |
//| only the tags it cares about.                                     |
//+------------------------------------------------------------------+
string VO_FeedPath()
  {
   const string sym = (StringLen(InpSymbolOverride) > 0) ? InpSymbolOverride : _Symbol;
   return StringFormat("%s\\%s_regime.feed", InpFeedSubdir, sym);
  }

//+------------------------------------------------------------------+
//| Read the whole feed and repaint the session-boundary lines only.  |
//| Delete-then-redraw so a run whose session boundaries changed       |
//| retroactively never leaves a stale line behind. BAND/MARK/SSTAT    |
//| lines are present in the same feed but deliberately ignored here   |
//| -- VO_Regime.mq5's own job (see this file's header note).          |
//+------------------------------------------------------------------+
void VO_ReadAndDraw()
  {
   const string path = VO_FeedPath();
   const int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI |
                               FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
      return; // feed not published yet -- leave whatever is on the chart

   // Read everything, then CLOSE, then touch chart objects (2026-09-18
   // audit, finding C1 -- see VO_Regime.mq5's VO_ReadAndDraw for why).
   string lines[];
   int line_count = 0;
   while(!FileIsEnding(handle))
     {
      const string line = FileReadString(handle);
      if(StringLen(line) == 0)
         continue;
      if(StringGetCharacter(line, 0) == '#')
         continue; // provenance comment(s)
      ArrayResize(lines, line_count + 1);
      lines[line_count] = line;
      line_count++;
     }
   FileClose(handle);

   VO_DeleteAllSessionLineObjects();

   int drawn = 0;
   for(int li = 0; li < line_count; li++)
     {
      string f[];
      const int n = StringSplit(lines[li], '|', f);
      if(n < 1)
         continue; // empty/malformed line -- skip defensively

      if(f[0] == VO_TAG_SESN && n == VO_FEED_SESSION_FIELDS && InpShowSessionLines)
        {
         VO_DrawSessionBoundary(f, drawn);
         drawn++;
        }
      // else: BAND/MARK/SSTAT (VO_Regime.mq5's own tags) or an
      // unrecognized tag/field-count drift -- skip defensively, same
      // "the bug is in the Python engine or the feed, never here"
      // discipline as everywhere else in this file.
     }
  }

//+------------------------------------------------------------------+
//| Draw one session-boundary vertical line from four already-split    |
//| SESN fields. A thin, dotted line -- deliberately unobtrusive next   |
//| to whatever else is on the chart, not competing with it. Carries    |
//| no object_id (a session transition traces back to no canonical      |
//| record -- see the file header's session-boundary-lines note); the   |
//| tooltip/label show the transition itself.                           |
//+------------------------------------------------------------------+
void VO_DrawSessionBoundary(const string &f[], const int ordinal)
  {
   // f[0] is the "SESN" tag (already checked by the caller).
   const datetime at_t        = (datetime)StringToInteger(f[1]);
   const string from_session  = f[2];
   const string to_session    = f[3];

   const string name = StringFormat("%s|SESN|%d|%d", VO_SESN_PREFIX, ordinal, (long)at_t);

   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_VLINE, 0, at_t, 0);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
     }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, at_t);
   ObjectSetInteger(0, name, OBJPROP_COLOR, InpSessionLineColor);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, InpSessionLineWidth);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DOT);

   const string tooltip = StringFormat("Session: %s -> %s", from_session, to_session);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);

   if(InpShowSessionLabel)
     {
      // Anchored at that bar's own high, just above it -- simple and
      // robust (no chart-window price-range lookup needed), and it
      // scrolls with the chart exactly like the vline itself does.
      const int bar_shift = iBarShift(_Symbol, PERIOD_CURRENT, at_t, true);
      const double bar_high = (bar_shift >= 0) ? iHigh(_Symbol, PERIOD_CURRENT, bar_shift) : 0.0;

      const string label_name = name + "|lbl";
      if(ObjectFind(0, label_name) < 0)
        {
         ObjectCreate(0, label_name, OBJ_TEXT, 0, at_t, bar_high);
         ObjectSetInteger(0, label_name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, label_name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, label_name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
         // Reuses the reference-level InpFontSize input (own group,
         // above) rather than a second font-size input just for this --
         // one file, one font-size knob, now that regime style inputs
         // have moved to VO_Regime.mq5.
         ObjectSetInteger(0, label_name, OBJPROP_FONTSIZE, InpFontSize);
        }
      ObjectSetInteger(0, label_name, OBJPROP_TIME, 0, at_t);
      ObjectSetDouble(0, label_name, OBJPROP_PRICE, 0, bar_high);
      ObjectSetString(0, label_name, OBJPROP_TEXT, to_session);
      ObjectSetInteger(0, label_name, OBJPROP_COLOR, InpSessionLineColor);
     }
  }

//+------------------------------------------------------------------+
void VO_DeleteAllSessionLineObjects()
  {
   // Prefix-filtered bulk delete, one terminal call (finding C1).
   ObjectsDeleteAll(0, VO_SESN_PREFIX + "|", -1, -1);
  }
//+------------------------------------------------------------------+
