//+------------------------------------------------------------------+
//| VO_Swings.mq5                                                    |
//|                                                                  |
//| Draws Phase 11's Swing Engine (architecture/vo-phase-plan.md,     |
//| Gate G6) directly on an MT5 chart: one rectangle box per          |
//| confirmed swing, from its pivot price to the extreme price of     |
//| the reversal that confirmed it - recolored once a later bar       |
//| breaks it. This is a plain rendering choice, NOT Phase 24's       |
//| "swing range"/equilibrium concept (vo.month01.ontology's own      |
//| `equilibrium()` marker, "Shape: Phase 24") - confirmed with the   |
//| user directly: asked whether the box should carry that meaning,   |
//| they chose "Just a visual box per swing (Recommended)", i.e. no   |
//| equilibrium/50% line, no Phase-24 vocabulary anywhere in this      |
//| file. If Phase 24 is ever built, it owns its own drawing - this   |
//| file does not get extended to grow one.                           |
//|                                                                  |
//| SCOPE: a Custom Indicator, not an Expert Advisor - same reasoning |
//| as VO_ReferenceLevels.mq5's own header (indicators have no        |
//| order-management API in MT5 at all, so this cannot become a       |
//| trade-decision path by construction, not just by discipline).     |
//| VO_EA runs unaffected with this indicator removed from the chart. |
//|                                                                  |
//| WHY THE MATH IS RE-DERIVED HERE INSTEAD OF ASKING PYTHON: see     |
//| VO_ReferenceLevelsMath.mqh's header - short version: the MT5<->    |
//| Python bridge is one-way (MT5 -> Python only). The detection math |
//| itself lives in VO_SwingMath.mqh, ported from                     |
//| vo.observation.swings.SwingEngine / vo.observation.atr - see that |
//| file's header for the fidelity notes (fractal rule, ATR filter,   |
//| tie-breaking, the one documented MathRound-vs-round() divergence).|
//| This file owns ONLY chart drawing; it contains no detection logic |
//| of its own - if a number here looks wrong, the bug is almost      |
//| certainly in VO_SwingMath.mqh, not in this file.                  |
//|                                                                  |
//| CONFIG SYNC: InpAtrPeriod / InpInternalK / InpInternalAtrMultiplier|
//| / InpSwingK / InpSwingAtrMultiplier below must match               |
//| config/settings/swings.yaml by hand (kept in sync manually, the    |
//| same way VO_ReferenceLevels.mq5's session inputs are kept in sync  |
//| with config/settings/sessions.yaml - MT5 has no Python-config       |
//| reader). swings.yaml itself is heavily commented as PROVISIONAL -  |
//| if it changes, these defaults must change with it.                 |
//|                                                                  |
//| WHAT "TIMEFRAME" MEANS HERE: this indicator detects swings on      |
//| whichever timeframe the chart it is attached to is set to           |
//| (PERIOD_CURRENT) - it does not hardcode M1 the way                 |
//| VO_ReferenceLevels.mq5 does, because Phase 11's SwingEngine is      |
//| itself a per-timeframe engine on the Python side (SwingPoint        |
//| carries its own `timeframe` field) - there is no single canonical   |
//| timeframe for swings the way M1 is canonical for reference-level    |
//| bar classification. Attach one instance per chart/timeframe you     |
//| want swings drawn on.                                               |
//|                                                                  |
//| DRAWING CONVENTION (confirmed with the user before writing this): |
//| - One OBJ_RECTANGLE per swing: corner 1 = (pivot bar time, pivot   |
//|   price); corner 2 = (extreme bar time, reversal_extreme_price) -  |
//|   exactly the two prices/times the Python SwingPoint / this file's |
//|   VO_SwingEvent already carry, nothing synthesized or interpreted. |
//| - Outline only (OBJPROP_FILL false) so the box never obscures      |
//|   price action underneath it.                                     |
//| - CONFIRMED and still active: solid outline, the tier's own color. |
//|   BROKEN (a later bar traded past the pivot): dotted outline,      |
//|   InpColorBroken, and the box's right edge is extended out to the  |
//|   breaking bar so the box visibly spans "how long this swing held".|
//| - A full rescan-and-redraw runs once per new bar of the chart's    |
//|   own timeframe (swing counts are small enough - InpMaxBarsToScan, |
//|   default 5000 - that this is cheap, unlike VO_ReferenceLevels.mq5 |
//|   which needs incremental expiry over a much larger window).       |
//| - Object names embed level/type/pivot-bar-epoch/status for G8      |
//|   traceability, plus a tooltip carrying the full detail (reversal   |
//|   ticks, ATR ticks at pivot, methodology). The embedded ID is a     |
//|   BROKER-LOCAL display id, not the byte-identical UTC-based         |
//|   `object_id` scheme the Python side would use for a canonical      |
//|   record - this indicator draws a picture, it does not assert a     |
//|   CanonicalRecord, so cross-referencing task #35's Python reference |
//|   dump against this chart is done by matching bar/price, not by     |
//|   string-equal IDs across languages.                                |
//|                                                                  |
//| COMPILE/VERIFY NOTE: not yet compiled or run against a live        |
//| terminal from this session (no MT5 terminal is reachable here) -   |
//| written to match VO_ReferenceLevels.mq5's own structure closely,   |
//| but needs the same validation step that file still needs: compile  |
//| in MetaEditor, attach to a chart with sufficient history loaded,    |
//| and visually cross-check a handful of boxes against task #35's     |
//| Python reference-swings dump before trusting it.                   |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

#include <VectorOdyssey/VO_SwingMath.mqh>

//--- Object naming/versioning - same "bump on breaking change" rule as
//    VO_REF_PREFIX in VO_ReferenceLevels.mq5.
#define VO_SWG_PREFIX "VO_SWG_v1"

input group "=== ATR & tiers (must match config/settings/swings.yaml) ==="
input int    InpAtrPeriod              = 20;    // swings.yaml: atr_period

input group "--- Internal tier (swings.yaml: levels.internal) ---"
input bool   InpShowInternal           = true;
input int    InpInternalK              = 2;     // levels.internal.k
input double InpInternalAtrMultiplier  = 1.0;   // levels.internal.atr_multiplier

input group "--- Swing tier (swings.yaml: levels.swing) ---"
input bool   InpShowSwing              = true;
input int    InpSwingK                 = 5;     // levels.swing.k
input double InpSwingAtrMultiplier     = 3.0;   // levels.swing.atr_multiplier

input group "=== Drawing scope ==="
input int    InpMaxBarsToScan          = 5000;  // chart-timeframe bars to rescan each pass

input group "=== Style ==="
input color  InpColorInternal   = clrDodgerBlue;
input color  InpColorSwing      = clrOrangeRed;
input color  InpColorBroken     = clrGray;
input ENUM_LINE_STYLE InpConfirmedStyle = STYLE_SOLID;
input ENUM_LINE_STYLE InpBrokenStyle    = STYLE_DOT;
input int    InpLineWidth = 1;
input int    InpFontSize  = 7;

//--- Rescan bookkeeping - a full rescan is O(bars), so it only runs
//    once per new bar of the chart's own timeframe, plus once at
//    startup (same pattern as VO_ReferenceLevels.mq5's g_last_seen_m1_open,
//    generalized to PERIOD_CURRENT since this indicator is not M1-only).
datetime g_last_seen_bar_open = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   IndicatorSetString(INDICATOR_SHORTNAME, "VO Swings");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // Same hygiene as VO_ReferenceLevels.mq5: leave objects in place on
   // ordinary chart-close/recompile/timeframe change, remove them only
   // on explicit detach or template reload.
   if(reason == REASON_REMOVE || reason == REASON_TEMPLATE)
      VO_DeleteAllSwingObjects();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[],
                const double &close[], const long &tick_volume[], const long &volume[],
                const int &spread[])
  {
   const datetime current_bar_open = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(current_bar_open != g_last_seen_bar_open)
     {
      g_last_seen_bar_open = current_bar_open;
      VO_RescanAndDraw();
     }
   return(rates_total);
  }

//+------------------------------------------------------------------+
//| The one heavy pass: fetch chart-timeframe history, run the ported |
//| detector per enabled tier, and redraw every box from scratch.     |
//| Full delete-then-redraw (not incremental diffing) is deliberate - |
//| InpMaxBarsToScan keeps this cheap, and it sidesteps ever having a  |
//| stale object left over from a detection result that changed       |
//| retroactively near the scan window's edge.                        |
//+------------------------------------------------------------------+
void VO_RescanAndDraw()
  {
   MqlRates rates[];
   ArraySetAsSeries(rates, false); // oldest-to-newest indexing, explicit
   const int copied = CopyRates(_Symbol, PERIOD_CURRENT, 0, InpMaxBarsToScan, rates);

   const int min_bars_needed = InpAtrPeriod + 2 * MathMax(InpInternalK, InpSwingK) + 2;
   if(copied < min_bars_needed)
      return; // not enough history loaded yet - nothing to draw

   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0.0)
      tick_size = _Point;

   VO_DeleteAllSwingObjects();

   if(InpShowInternal)
     {
      VO_SwingEvent events[];
      VO_DetectSwings(rates, copied, VO_SWING_LEVEL_INTERNAL, InpInternalK, InpAtrPeriod,
                       InpInternalAtrMultiplier, tick_size, events);
      VO_DrawEvents(rates, events, "I", InpColorInternal);
     }

   if(InpShowSwing)
     {
      VO_SwingEvent events[];
      VO_DetectSwings(rates, copied, VO_SWING_LEVEL_SWING, InpSwingK, InpAtrPeriod,
                       InpSwingAtrMultiplier, tick_size, events);
      VO_DrawEvents(rates, events, "S", InpColorSwing);
     }
  }

//+------------------------------------------------------------------+
//| Draws every swing in `events[]` for one tier. A CONFIRMED event    |
//| that a later BROKEN event supersedes is skipped in its own right - |
//| the BROKEN event draws the box instead, using the CONFIRMED        |
//| event's geometry (via broken_event_index) so there is exactly one  |
//| box per pivot, never two.                                          |
//+------------------------------------------------------------------+
void VO_DrawEvents(const MqlRates &rates[], const VO_SwingEvent &events[],
                   const string level_code, const color base_color)
  {
   const int n = ArraySize(events);
   if(n == 0)
      return;

   bool superseded[];
   ArrayResize(superseded, n);
   ArrayInitialize(superseded, false);
   for(int i = 0; i < n; i++)
      if(events[i].status == VO_SWING_STATUS_BROKEN)
         superseded[events[i].broken_event_index] = true;

   for(int i = 0; i < n; i++)
     {
      if(events[i].status == VO_SWING_STATUS_CONFIRMED)
        {
         if(superseded[i])
            continue; // drawn by its BROKEN counterpart below instead
         VO_DrawOneSwing(rates, events[i], events[i], level_code, base_color, false);
        }
      else // BROKEN
        {
         const int src = events[i].broken_event_index;
         if(src < 0 || src >= n)
            continue; // defensive - should never happen, see VO_AppendBrokenEvent
         VO_DrawOneSwing(rates, events[src], events[i], level_code, base_color, true);
        }
     }
  }

//+------------------------------------------------------------------+
//| Draws one box. `geom` supplies the pivot/extreme geometry (always  |
//| a CONFIRMED event); `status_ev` supplies the status to label and,  |
//| when broken, the breaking bar to extend the box's right edge to.   |
//+------------------------------------------------------------------+
void VO_DrawOneSwing(const MqlRates &rates[], const VO_SwingEvent &geom,
                     const VO_SwingEvent &status_ev, const string level_code,
                     const color base_color, const bool is_broken)
  {
   const string type_code = (geom.swing_type == VO_SWING_TYPE_HIGH) ? "H" : "L";
   const string status_text = is_broken ? "BROKEN" : "CONFIRMED";
   const long pivot_epoch = (long)rates[geom.pivot_bar].time;
   const string name = StringFormat("%s|%s|%s|%d|%s", VO_SWG_PREFIX, level_code, type_code,
                                    pivot_epoch, status_text);

   const datetime time1 = rates[geom.pivot_bar].time;
   const double   price1 = geom.price;
   int right_bar = geom.reversal_extreme_bar;
   if(is_broken && status_ev.confirmed_bar > right_bar)
      right_bar = status_ev.confirmed_bar;
   const datetime time2 = rates[right_bar].time;
   const double   price2 = geom.reversal_extreme_price;

   const color clr = is_broken ? InpColorBroken : base_color;
   const ENUM_LINE_STYLE style = is_broken ? InpBrokenStyle : InpConfirmedStyle;

   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, time1, price1, time2, price2);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_FILL, false);
     }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, time1);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, price1);
   ObjectSetInteger(0, name, OBJPROP_TIME, 1, time2);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 1, price2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, InpLineWidth);

   const string level_name = (level_code == "I") ? "internal" : "swing";
   const string type_name  = (geom.swing_type == VO_SWING_TYPE_HIGH) ? "high" : "low";
   const string tooltip = StringFormat(
      "VO Swing [%s] %s %s\npivot: %s\nprice: %s\nreversal: %d ticks (ATR at pivot: %d ticks)\nstatus: %s",
      level_code, level_name, type_name,
      TimeToString(time1, TIME_DATE | TIME_MINUTES),
      DoubleToString(price1, _Digits),
      (int)geom.reversal_ticks, (int)geom.atr_ticks_at_pivot,
      status_text);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);

   const string label_name = name + "|lbl";
   const string label_text = StringFormat("%s-%s", level_code, type_code);
   if(ObjectFind(0, label_name) < 0)
     {
      ObjectCreate(0, label_name, OBJ_TEXT, 0, time1, price1);
      ObjectSetInteger(0, label_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, label_name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, label_name, OBJPROP_ANCHOR,
                       (geom.swing_type == VO_SWING_TYPE_HIGH) ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
      ObjectSetInteger(0, label_name, OBJPROP_FONTSIZE, InpFontSize);
     }
   ObjectSetInteger(0, label_name, OBJPROP_TIME, 0, time1);
   ObjectSetDouble(0, label_name, OBJPROP_PRICE, 0, price1);
   ObjectSetString(0, label_name, OBJPROP_TEXT, label_text);
   ObjectSetInteger(0, label_name, OBJPROP_COLOR, clr);
  }

//+------------------------------------------------------------------+
void VO_DeleteAllSwingObjects()
  {
   const string prefix = VO_SWG_PREFIX + "|";
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
     {
      const string name = ObjectName(0, i);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
     }
  }
