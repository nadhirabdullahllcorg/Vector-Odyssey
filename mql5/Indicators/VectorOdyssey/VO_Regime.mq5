//+------------------------------------------------------------------+
//| VO_Regime.mq5                                                    |
//|                                                                  |
//| Draws Phase 13's Regime Engine (architecture/vo-phase-plan.md,    |
//| Gate G6) as colored background bands on an MT5 chart -- one band   |
//| per regime segment, from where the regime took hold to where it   |
//| resolved (or to the live chart edge, if it is still current) --   |
//| plus RETRACEMENT/REVERSAL resolution markers and a right-anchored |
//| session/regime breakdown panel.                                   |
//|                                                                  |
//| SPLIT BACK OUT OF VO_ReferenceLevels.mq5 (2026-09-18). v31/v32     |
//| had merged this indicator's bands/markers/session-stat panel/     |
//| ER-Hurst tooltips into VO_ReferenceLevels.mq5, at the user's own   |
//| explicit direction at the time. The user has since asked for the   |
//| opposite: VO_ReferenceLevels.mq5 should carry ONLY reference       |
//| levels plus day/session boundaries -- regime bands, markers, the   |
//| session-stat panel, and the ER/Hurst evidence tooltips belong      |
//| here, standalone, same as before v31. Session-boundary (SESN)      |
//| lines stay in VO_ReferenceLevels.mq5 exclusively (the user's own   |
//| explicit choice) -- this file does NOT read or draw SESN lines,   |
//| so attaching both indicators to one chart never double-draws       |
//| them. Everything else in this file is otherwise unchanged from     |
//| the merged version: same feed, same VO_RGM_v3 object prefix (the   |
//| BAND/MARK field shapes/tooltips haven't changed, only which file   |
//| draws them), same v5 ER/Hurst evidence display.                   |
//|                                                                  |
//| PHASE 13a -- "PYTHON COMPUTES, THE INDICATOR READS A FILE"         |
//| (the approach the user chose). This file contains NO regime        |
//| classification logic at all -- unlike VO_Swings.mq5, which ports   |
//| the swing math into MQL5, the regime state machine is far too      |
//| stateful (bitemporal supersession, ER/Hurst evidence, an           |
//| anticipation lean) to port faithfully, and a second drifting copy  |
//| is exactly what gate G6 exists to prevent. Instead the REAL        |
//| vo.observation.regime.RegimeEngine runs in Python                  |
//| (scripts/publish_regime.py or scripts/backtest_regime.py) over     |
//| the same wire bars VO_EA tails, and writes its emitted bands to     |
//| <symbol>_regime.feed in the terminal's MQL5\Files\<subdir>\         |
//| folder. This file only reads that feed and paints it -- and reads   |
//| only the BAND/MARK/SSTAT lines from it, ignoring SESN entirely     |
//| (VO_ReferenceLevels.mq5's job, not this file's). If a band looks   |
//| wrong, the bug is in the Python engine or the feed, never here.    |
//|                                                                  |
//| FEED FORMAT (vo.telemetry.regime_feed, v5). Lines beginning '#'    |
//| are provenance comments and are skipped. Every other line's FIRST  |
//| field is a tag; this file reacts to "BAND", "MARK" and "SSTAT"     |
//| only (SESN lines are present in the same feed file but skipped      |
//| here on purpose):                                                  |
//|                                                                  |
//|   BAND  | object_id | regime | direction | start_epoch |          |
//|         end_epoch | high | low | confidence | anticipated |      |
//|         efficiency_ratio | hurst_exponent      (12 fields)        |
//|   MARK  | object_id | regime | direction | at_epoch | price |      |
//|         confidence | efficiency_ratio | hurst_exponent (9 fields) |
//|   SSTAT | session | regime | bar_count | total_minutes |           |
//|         share_of_session | segments_started    (7 fields)         |
//|                                                                  |
//| - object_id: the real RegimeState.object_id (gate G8: the chart    |
//|   object embeds it, so any band/marker is traceable to one          |
//|   canonical record on the Python side). SSTAT has no object_id --  |
//|   it is a summary OVER many records, not one of them.              |
//| - direction: UP / DOWN / NONE.                                    |
//| - start_epoch / end_epoch / at_epoch: broker-SERVER epoch seconds   |
//|   (what MT5 chart time uses). A band's end_epoch 0 means it is     |
//|   still current -- extended to the latest chart bar so it tracks   |
//|   the live edge instead of freezing at the last published bar.    |
//| - high / low: a band's vertical extent (its own bars' range).      |
//| - price: a marker's single price (the resolving bar's close --     |
//|   RETRACEMENT/REVERSAL are instantaneous, so there is no bar range  |
//|   to bound a high/low with).                                       |
//| - anticipated: RETRACEMENT / REVERSAL / UNCLEAR / empty -- only    |
//|   ever set on a PULLBACK_UNRESOLVED band (the [VO-H] lean).        |
//| - efficiency_ratio / hurst_exponent (v5): the same [VO-D] evidence |
//|   RegimeState.supporting_features already records -- not a new     |
//|   computation, exposed on request so the evidence behind a band's  |
//|   confidence/lean is readable on the chart, never a decision-path  |
//|   input (gate G2 unchanged). Empty when the engine had not yet     |
//|   computed a value (its own ER/Hurst warmup window).               |
//|                                                                  |
//| WHY MARKERS EXIST. RETRACEMENT/REVERSAL are not periods the market  |
//| spends time in -- the engine resolves an ambiguous pullback and     |
//| re-enters EXPANSION in the SAME bar (regime.py: "... -> RETRACEMENT |
//| -> EXPANSION"). That run has zero width, so build_regime_segments   |
//| correctly drops it rather than draw a degenerate band -- but the    |
//| moment itself (the single most decision-relevant instant in the     |
//| whole model: exactly where an ambiguous pullback got resolved) is   |
//| still worth seeing. A MARK line draws a small arrow there instead.  |
//|                                                                  |
//| WHY THE SESSION-STAT PANEL EXISTS. build_session_breakdown         |
//| (vo.telemetry.regime_report) already computes, per session, how     |
//| much time each regime occupied -- previously only rendered into     |
//| the markdown backtest report. The SSTAT feed lines carry that same  |
//| computation to the live/backtest chart. Corner-anchored, right      |
//| side of the chart, per the user's explicit instruction that this    |
//| text sit "to the right and not to the left place of origin".       |
//| Purely additive telemetry (gate G2) -- changes nothing about how    |
//| a regime is classified or drawn.                                   |
//|                                                                  |
//| SCOPE: a Custom Indicator, not an Expert Advisor -- same reasoning |
//| as VO_ReferenceLevels.mq5 / VO_Swings.mq5: indicators have no      |
//| order API in MT5, so this cannot become a trade-decision path by   |
//| construction. The feed is viz-only (gate G2). VO_EA runs           |
//| unaffected with this indicator removed.                           |
//|                                                                  |
//| SYMBOL: by default the feed for the chart's own _Symbol is read    |
//| (<_Symbol>_regime.feed). Set InpSymbolOverride to read a different |
//| symbol's feed (e.g. draw US100's regime on a US100.n chart).       |
//|                                                                  |
//| REFRESH: re-read on every new chart bar and on a short timer, so   |
//| bands appear as scripts/publish_regime.py --watch republishes.     |
//| A full delete-then-redraw runs each pass (bands are few) -- the    |
//| same delete-then-redraw discipline as VO_Swings.mq5, so a band     |
//| that changed retroactively (a pullback that resolved) never        |
//| leaves a stale object behind.                                     |
//|                                                                  |
//| CLEAN REMOVAL: OnDeinit sweeps every VO_RGM_PREFIX-tagged chart     |
//| object when the user explicitly removes this indicator or applies  |
//| a new template (REASON_REMOVE / REASON_TEMPLATE) -- nothing is      |
//| left behind. Objects are intentionally left in place on an          |
//| ordinary recompile/timeframe-change/chart-close, matching standard  |
//| MT5 indicator hygiene and VO_ReferenceLevels.mq5's own convention.  |
//|                                                                  |
//| COMPILE/VERIFY NOTE: not yet compiled or run against a live         |
//| terminal from this environment (no MT5 terminal is reachable        |
//| here). Needs: compile in MetaEditor, run scripts/publish_regime.py  |
//| or backtest_regime.py so a v5 feed exists, remove/re-add this       |
//| indicator once (clears any stale VO_RGM_v3 objects VO_ReferenceLevels|
//| .mq5 may still hold from before the split -- see that file's own    |
//| note), and visually confirm bands/markers/session-stat panel and    |
//| ER/Hurst tooltips all land correctly, with no session lines drawn   |
//| by this file (those come from VO_ReferenceLevels.mq5 alone).       |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

//--- Object naming/versioning -- same "bump on breaking change" rule as
//    VO_SWG_PREFIX in VO_Swings.mq5 / VO_REF_PREFIX in VO_ReferenceLevels.mq5.
//    Unchanged from the merged file's own VO_RGM_v3 -- the BAND/MARK/SSTAT
//    object shapes this file draws haven't changed, only which .mq5 file
//    draws them, so there is nothing to orphan by bumping the version.
#define VO_RGM_PREFIX "VO_RGM_v3"
// Build tag: shown in the indicator shortname and the on-chart status line
// so a screenshot can prove WHICH compiled build is on the chart (a stale
// .ex5 looks identical otherwise). Bump on every behavior change.
#define VO_RGM_BUILD  "b39"

// Which feed file this chart draws. Each is written by a different
// publisher; the names match scripts/publish_regime.py / backtest_regime.py.
enum ENUM_VO_FEED_SOURCE
  {
   VO_FEED_LIVE_SWING    = 0, // live, configured tier:  <symbol>_regime.feed          (publish_regime.py --watch)
   VO_FEED_LIVE_INTERNAL = 1, // live, INTERNAL tier:    <symbol>_regime_internal.feed (publish_regime.py --watch --tier INTERNAL)
   VO_FEED_HISTORY       = 2  // deep-history backtest:  <symbol>_regime_history.feed  (backtest_regime.py)
  };
#define VO_TAG_BAND "BAND"
#define VO_TAG_MARK "MARK"
#define VO_TAG_SSTAT "SSTAT"
#define VO_FEED_BAND_FIELDS 12
#define VO_FEED_MARKER_FIELDS 9
#define VO_FEED_SESSION_STAT_FIELDS 7

input group "=== Feed source (MQL5\\Files\\<subdir>\\<symbol>_regime.feed) ==="
input string InpFeedSubdir     = "VectorOdyssey"; // must match VO_Bridge InpOutputSubdir / vo_ea.yaml wire.dir
input string InpSymbolOverride = "";              // blank = this chart's symbol; else e.g. "US100"
input ENUM_VO_FEED_SOURCE InpFeedSource = VO_FEED_LIVE_SWING; // which feed to draw (dropdown; overrides nothing if InpFeedName is set)
input string InpFeedName       = "";              // advanced: an explicit file name under the subdir; leave blank to use InpFeedSource

input group "=== Refresh ==="
input int    InpRefreshSeconds = 5;   // re-read the feed on this timer (publish_regime --watch interval)
input int    InpStaleAfterMinutes = 15; // status line flags the feed STALE when its last bar is older than this

input group "=== Regime band colors (Phase 13a convention) ==="
input color  InpColorConsolidation = clrLightBlue;  // CONSOLIDATION -- light blue
input color  InpColorExpansion     = clrOrange;     // EXPANSION -- orange
input color  InpColorRetracement   = clrKhaki;      // RETRACEMENT -- yellow
input color  InpColorReversal      = clrLightGreen; // REVERSAL -- light green
input color  InpColorPullback      = clrPlum;       // PULLBACK_UNRESOLVED -- (not in the 4-color spec; distinct "unsure")
input color  InpColorUnknown       = clrGainsboro;  // any future/unmapped regime name

input group "=== Style ==="
input bool   InpFillBands = true;   // filled band behind price (false = outline only)
input int    InpLineWidth = 1;
input int    InpFontSize  = 7;
input color  InpLabelColor = clrDimGray;

input group "=== Resolution markers (RETRACEMENT/REVERSAL confirm instants) ==="
input int    InpRetracementArrowCode = 159;  // wingdings code, RETRACEMENT (continuation) -- small filled dot
input int    InpReversalArrowCode    = 108;  // wingdings code, REVERSAL (structure shift) -- more prominent
input int    InpMarkerSize           = 2;

input group "=== Session-stat summary panel (right side of chart, not left/origin) ==="
input bool   InpShowSessionStats     = true;
input ENUM_BASE_CORNER InpSessionStatsCorner = CORNER_RIGHT_UPPER; // right-side corner, per explicit user request
input int    InpSessionStatsXDistance = 14;   // pixels inward from that corner's vertical edge
input int    InpSessionStatsYDistance = 20;   // pixels inward from that corner's horizontal edge
input int    InpSessionStatsLineHeight= 14;   // pixel spacing between stacked rows
input int    InpSessionStatsFontSize  = 8;
input color  InpSessionStatsColor     = clrWhiteSmoke;
input color  InpSessionStatsHeaderColor = clrSilver;

//--- Re-read bookkeeping (same pattern as VO_Swings.mq5's g_last_seen_bar_open).
datetime g_last_seen_bar_open = 0;
string   g_last_feed_header  = "";  // last feed header drawn; "" forces a redraw
datetime g_feed_last_bar     = 0;   // header last_bar_epoch: the last bar the feed covers (0 = unknown)

//+------------------------------------------------------------------+
int OnInit()
  {
   IndicatorSetString(INDICATOR_SHORTNAME, "VO Regime " + VO_RGM_BUILD);
   if(InpRefreshSeconds > 0)
      EventSetTimer(InpRefreshSeconds);
   VO_ReadAndDraw();
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   // Leave objects in place on ordinary chart-close/recompile/timeframe
   // change; remove them only when the user explicitly detaches this
   // indicator (or reloads a template) - standard MT5 indicator hygiene,
   // same convention as VO_ReferenceLevels.mq5 and VO_Swings.mq5.
   if(reason == REASON_REMOVE || reason == REASON_TEMPLATE)
      VO_DeleteAllRegimeObjects();
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
   const datetime current_bar_open = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(current_bar_open != g_last_seen_bar_open)
     {
      g_last_seen_bar_open = current_bar_open;
      g_last_feed_header = "";   // new bar: the live-edge band must be re-extended
      VO_ReadAndDraw();
     }
   return(rates_total);
  }

//+------------------------------------------------------------------+
//| Feed path relative to MQL5\Files\ -- FileOpen resolves it there.  |
//+------------------------------------------------------------------+
string VO_FeedPath()
  {
   const string sym = (StringLen(InpSymbolOverride) > 0) ? InpSymbolOverride : _Symbol;
   string file = sym + "_regime.feed";
   if(InpFeedSource == VO_FEED_LIVE_INTERNAL) file = sym + "_regime_internal.feed";
   if(InpFeedSource == VO_FEED_HISTORY)       file = sym + "_regime_history.feed";
   if(StringLen(InpFeedName) > 0)             file = InpFeedName; // explicit name wins
   return StringFormat("%s\\%s", InpFeedSubdir, file);
  }

//+------------------------------------------------------------------+
//| Map a regime name from the feed to its band color.               |
//+------------------------------------------------------------------+
color VO_RegimeColor(const string regime)
  {
   if(regime == "CONSOLIDATION")       return InpColorConsolidation;
   if(regime == "EXPANSION")           return InpColorExpansion;
   if(regime == "RETRACEMENT")         return InpColorRetracement;
   if(regime == "REVERSAL")            return InpColorReversal;
   if(regime == "PULLBACK_UNRESOLVED") return InpColorPullback;
   return InpColorUnknown;
  }

//+------------------------------------------------------------------+
//| Read the whole feed and repaint. Delete-then-redraw: a band that  |
//| the Python engine revised (a pullback that resolved) never leaves  |
//| a stale object behind. SSTAT lines are collected as they're seen   |
//| and drawn as one summary panel after the loop, since they are      |
//| aggregates (no chart-time position of their own) rather than       |
//| chronologically interleaved events. SESN lines are present in the  |
//| feed but deliberately ignored here -- VO_ReferenceLevels.mq5's     |
//| own job (see this file's header note).                            |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| On-chart status line (top-left): build tag, feed path, what was    |
//| drawn (or why nothing was). Silence is never ambiguous again.      |
//+------------------------------------------------------------------+
void VO_DrawStatus(const string text)
  {
   const string name = VO_RGM_PREFIX + "|status";
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 8);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 4);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
     }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, InpLabelColor);
  }

//+------------------------------------------------------------------+
//| Read the whole feed into memory and CLOSE IT before touching a     |
//| single chart object (2026-09-18 audit, finding C1: holding the     |
//| handle open across an ObjectsTotal walk of a chart full of swing   |
//| boxes kept the file locked for seconds per refresh, which is what  |
//| starved publish_regime --watch's rename into WinError 5).          |
//+------------------------------------------------------------------+
void VO_ReadAndDraw()
  {
   const string path = VO_FeedPath();
   const int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI |
                               FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
     {
      // Feed not published yet -- say so on the chart instead of drawing nothing silently.
      VO_DrawStatus(StringFormat("VO Regime %s | no feed at MQL5\\Files\\%s (run publish_regime.py or backtest_regime.py)",
                                 VO_RGM_BUILD, path));
      return;
     }

   string lines[];
   int line_count = 0;
   string header = "";
   while(!FileIsEnding(handle))
     {
      const string line = FileReadString(handle);
      if(StringLen(line) == 0)
         continue;
      if(StringGetCharacter(line, 0) == '#')
        {
         if(StringLen(header) == 0)
            header = line;
         continue; // provenance comment(s)
        }
      ArrayResize(lines, line_count + 1);
      lines[line_count] = line;
      line_count++;
     }
   FileClose(handle);   // <-- closed BEFORE any chart-object work

   // Unchanged feed (same provenance header) -> nothing to redraw. With the
   // deep-history feed (tens of thousands of bands) a full delete/recreate
   // every timer tick is the single most expensive thing this indicator can
   // do, and it is pure waste when the publisher wrote the same file again.
   // The open-ended final band still tracks the live edge: force a redraw
   // when the current bar changed (OnCalculate resets g_last_feed_header).
   if(header == g_last_feed_header && StringLen(header) > 0)
      return;

   // An open-ended (end_epoch 0) band is extended only as far as the LAST
   // BAR THE FEED COVERS (header last_bar_epoch), never to the live chart
   // edge: a feed nobody has refreshed for hours must not look like a live
   // claim about the bars since (2026-09-19). Older feeds without the
   // field fall back to the live edge, as before.
   g_feed_last_bar = 0;
   const int lb_pos = StringFind(header, "last_bar_epoch=");
   if(lb_pos >= 0)
      g_feed_last_bar = (datetime)StringToInteger(StringSubstr(header, lb_pos + 15));
   const datetime chart_edge = iTime(_Symbol, PERIOD_CURRENT, 0);
   const datetime live_edge = (g_feed_last_bar > 0 && g_feed_last_bar < chart_edge)
                              ? g_feed_last_bar : chart_edge;

   VO_DeleteAllRegimeObjects();

   int drawn = 0;
   int bands = 0;
   int marks = 0;
   string sstat_rows[];
   int sstat_count = 0;
   bool printed_sample = (header == g_last_feed_header);

   for(int li = 0; li < line_count; li++)
     {
      string f[];
      const int n = StringSplit(lines[li], '|', f);
      if(n < 1)
         continue; // empty/malformed line -- skip defensively

      if(f[0] == VO_TAG_BAND && n == VO_FEED_BAND_FIELDS)
        {
         if(!printed_sample)
           {
            // One Experts-log line per new feed: the parsed fields and the color
            // the regime name resolved to -- a screenshot plus this line settles
            // "what did the indicator actually draw" without guessing.
            PrintFormat("VO Regime %s: first band regime=%s dir=%s start=%s end=%s high=%s low=%s color=%s (fill=%s)",
                        VO_RGM_BUILD, f[2], f[3], f[4], f[5], f[6], f[7],
                        ColorToString(VO_RegimeColor(f[2]), true), InpFillBands ? "true" : "false");
            printed_sample = true;
           }
         VO_DrawBand(f, live_edge, drawn);
         drawn++;
         bands++;
        }
      else if(f[0] == VO_TAG_MARK && n == VO_FEED_MARKER_FIELDS)
        {
         VO_DrawMarker(f, drawn);
         drawn++;
         marks++;
        }
      else if(f[0] == VO_TAG_SSTAT && n == VO_FEED_SESSION_STAT_FIELDS && InpShowSessionStats)
        {
         ArrayResize(sstat_rows, sstat_count + 1);
         sstat_rows[sstat_count] = VO_FormatSessionStatRow(f);
         sstat_count++;
        }
      // else: unrecognized tag (or SESN, deliberately skipped) or
      // field-count drift -- skip defensively, same "the bug is in the
      // Python engine or the feed, never here" discipline as everywhere
      // else in this file.
     }
   g_last_feed_header = header;

   if(InpShowSessionStats)
      VO_DrawSessionStatsPanel(sstat_rows, sstat_count);

   string age_text = "feed age: unknown (no last_bar_epoch in header)";
   if(g_feed_last_bar > 0)
     {
      const long age_min = ((long)TimeCurrent() - (long)g_feed_last_bar) / 60;
      age_text = StringFormat("feed covers bars up to %s (%d min ago)",
                              TimeToString(g_feed_last_bar, TIME_DATE | TIME_MINUTES), (int)age_min);
      if(age_min > InpStaleAfterMinutes)
         age_text = "STALE FEED -- " + age_text + " -- is publish_regime.py --watch running?";
     }
   VO_DrawStatus(StringFormat("VO Regime %s | %s | %d bands, %d markers | %s",
                              VO_RGM_BUILD, path, bands, marks, age_text));
  }

//+------------------------------------------------------------------+
//| Draw one band + its label from twelve already-split feed fields.  |
//| `ordinal` disambiguates object names (object_id is embedded in a   |
//| tooltip for G8, and in the name too, but two segments of the same  |
//| regime at the same start would otherwise collide).                |
//+------------------------------------------------------------------+
void VO_DrawBand(const string &f[], const datetime live_edge, const int ordinal)
  {
   // f[0] is the "BAND" tag (already checked by the caller).
   const string object_id       = f[1];
   const string regime          = f[2];
   const string direction       = f[3];
   const datetime start_t       = (datetime)StringToInteger(f[4]);
   const long   end_epoch       = StringToInteger(f[5]);
   const double high            = StringToDouble(f[6]);
   const double low             = StringToDouble(f[7]);
   const double confidence      = StringToDouble(f[8]);
   const string anticipated     = f[9];
   const string efficiency_ratio = f[10]; // v5: [VO-D] evidence, empty during warmup
   const string hurst_exponent   = f[11];

   const datetime end_t = (end_epoch == 0) ? live_edge : (datetime)end_epoch;
   const color clr = VO_RegimeColor(regime);

   const string name = StringFormat("%s|%d|%s|%d", VO_RGM_PREFIX, ordinal, regime, (long)start_t);

   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, start_t, high, end_t, low);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true); // behind price
     }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, start_t);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, high);
   ObjectSetInteger(0, name, OBJPROP_TIME, 1, end_t);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 1, low);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FILL, InpFillBands);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, InpLineWidth);

   const string dir_text = (direction == "NONE") ? "" : (" " + direction);
   const string ant_text = (StringLen(anticipated) > 0) ? ("\nlean: " + anticipated) : "";
   // v5: the same ER/Hurst evidence RegimeState.supporting_features already
   // records, shown here on request -- never fed back into the classifier.
   const string evidence_text = (StringLen(efficiency_ratio) > 0 || StringLen(hurst_exponent) > 0)
      ? StringFormat("\nER: %s  Hurst: %s",
                      (StringLen(efficiency_ratio) > 0) ? efficiency_ratio : "n/a",
                      (StringLen(hurst_exponent) > 0) ? hurst_exponent : "n/a")
      : "";
   const string tooltip = StringFormat(
      "VO Regime: %s%s\nconfidence: %s%s%s\nid: %s",
      regime, dir_text, DoubleToString(confidence, 2), ant_text, evidence_text, object_id);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);

   // Dated label at the band's top-left, so the chart is readable
   // without hovering. Text = regime + direction (+ lean if unresolved).
   const string label_name = name + "|lbl";
   const string lean_tag = (StringLen(anticipated) > 0) ? ("?" + anticipated) : "";
   const string label_text = regime + dir_text + lean_tag;
   if(ObjectFind(0, label_name) < 0)
     {
      ObjectCreate(0, label_name, OBJ_TEXT, 0, start_t, high);
      ObjectSetInteger(0, label_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, label_name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, label_name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, label_name, OBJPROP_FONTSIZE, InpFontSize);
     }
   ObjectSetInteger(0, label_name, OBJPROP_TIME, 0, start_t);
   ObjectSetDouble(0, label_name, OBJPROP_PRICE, 0, high);
   ObjectSetString(0, label_name, OBJPROP_TEXT, label_text);
   ObjectSetInteger(0, label_name, OBJPROP_COLOR, InpLabelColor);
  }

//+------------------------------------------------------------------+
//| Draw one resolution marker from nine already-split MARK fields.   |
//| RETRACEMENT/REVERSAL are instantaneous (see the file header's      |
//| WHY MARKERS EXIST note) -- one arrow at one bar/price, not a band.  |
//| REVERSAL is drawn larger/more prominent than RETRACEMENT: a         |
//| structure shift is a bigger deal than a trend continuing.          |
//+------------------------------------------------------------------+
void VO_DrawMarker(const string &f[], const int ordinal)
  {
   // f[0] is the "MARK" tag (already checked by the caller).
   const string object_id  = f[1];
   const string regime     = f[2]; // RETRACEMENT or REVERSAL
   const string direction  = f[3];
   const datetime at_t     = (datetime)StringToInteger(f[4]);
   const double price      = StringToDouble(f[5]);
   const double confidence = StringToDouble(f[6]);
   const string efficiency_ratio = f[7]; // v5: [VO-D] evidence, empty during warmup
   const string hurst_exponent   = f[8];

   const bool is_reversal   = (regime == "REVERSAL");
   const color clr          = is_reversal ? InpColorReversal : InpColorRetracement;
   const int   arrow_code   = is_reversal ? InpReversalArrowCode : InpRetracementArrowCode;
   const int   arrow_size   = is_reversal ? InpMarkerSize + 1 : InpMarkerSize;

   const string name = StringFormat("%s|MARK|%d|%s|%d", VO_RGM_PREFIX, ordinal, regime, (long)at_t);

   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_ARROW, 0, at_t, price);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, at_t);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, price);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, arrow_code);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, arrow_size);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_CENTER);

   const string dir_text = (direction == "NONE") ? "" : (" " + direction);
   // v5: same ER/Hurst evidence as VO_DrawBand -- read-only display, never
   // fed back into the classifier (gate G2 unchanged).
   const string evidence_text = (StringLen(efficiency_ratio) > 0 || StringLen(hurst_exponent) > 0)
      ? StringFormat("\nER: %s  Hurst: %s",
                      (StringLen(efficiency_ratio) > 0) ? efficiency_ratio : "n/a",
                      (StringLen(hurst_exponent) > 0) ? hurst_exponent : "n/a")
      : "";
   const string tooltip = StringFormat(
      "VO Regime resolution: %s%s\nconfidence: %s%s\nid: %s",
      regime, dir_text, DoubleToString(confidence, 2), evidence_text, object_id);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);

   // Small text label, same convention as VO_DrawBand's -- readable
   // without hovering. REVERSAL labels sit above the price, RETRACEMENT
   // below, so the two never overlap when they land close together.
   const string label_name = name + "|lbl";
   const string label_text = regime + dir_text;
   if(ObjectFind(0, label_name) < 0)
     {
      ObjectCreate(0, label_name, OBJ_TEXT, 0, at_t, price);
      ObjectSetInteger(0, label_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, label_name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, label_name, OBJPROP_ANCHOR,
                        is_reversal ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
      ObjectSetInteger(0, label_name, OBJPROP_FONTSIZE, InpFontSize);
     }
   ObjectSetInteger(0, label_name, OBJPROP_TIME, 0, at_t);
   ObjectSetDouble(0, label_name, OBJPROP_PRICE, 0, price);
   ObjectSetString(0, label_name, OBJPROP_TEXT, label_text);
   ObjectSetInteger(0, label_name, OBJPROP_COLOR, clr);
  }

//+------------------------------------------------------------------+
//| Format one SSTAT feed line's already-split fields into one         |
//| display row for the summary panel. f[0] is the "SSTAT" tag         |
//| (already checked by the caller); f[1..6] are session, regime,      |
//| bar_count, total_minutes, share_of_session, segments_started --    |
//| see vo.telemetry.regime_feed.session_stat_line for the exact       |
//| encoding this mirrors. share_of_session arrives as a [0,1]          |
//| fraction and is displayed as a percentage here only -- the raw      |
//| feed value is never rounded before this formatting step.           |
//+------------------------------------------------------------------+
string VO_FormatSessionStatRow(const string &f[])
  {
   const string session    = f[1];
   const string regime     = f[2];
   const int    bar_count  = (int)StringToInteger(f[3]);
   const double minutes    = StringToDouble(f[4]);
   const double share_pct  = StringToDouble(f[5]) * 100.0;
   const int    segs       = (int)StringToInteger(f[6]);
   return StringFormat("%-10s %-13s bars=%-6d min=%-7.1f share=%4.1f%% segs=%d",
                        session, regime, bar_count, minutes, share_pct, segs);
  }

//+------------------------------------------------------------------+
//| Draw the session-stat summary panel: one header row plus one row   |
//| per SSTAT line, stacked vertically, corner-anchored (default       |
//| CORNER_RIGHT_UPPER) with right-aligned text -- per the user's       |
//| explicit instruction that this text sit "to the right and not to   |
//| the left place of origin", i.e. not the default upper-left corner  |
//| most indicators use for on-chart panels. Pixel-anchored (OBJ_LABEL |
//| + OBJPROP_CORNER/XDISTANCE/YDISTANCE), unlike the band/marker       |
//| objects, which are chart-time/price anchored -- this panel is a    |
//| fixed summary, not tied to any bar. VO_ReadAndDraw already runs    |
//| a full delete-then-redraw of every VO_RGM_PREFIX object before      |
//| calling this, so a shrinking row count (fewer sessions than last    |
//| pass) never leaves a stale row behind.                              |
//+------------------------------------------------------------------+
void VO_DrawSessionStatsPanel(const string &rows[], const int row_count)
  {
   if(row_count == 0)
      return;

   VO_DrawCornerLabel(VO_RGM_PREFIX + "|SSTAT|hdr", "Session / Regime breakdown",
                       InpSessionStatsHeaderColor, 0);

   for(int i = 0; i < row_count; i++)
     {
      const string name = StringFormat("%s|SSTAT|%d", VO_RGM_PREFIX, i);
      VO_DrawCornerLabel(name, rows[i], InpSessionStatsColor, i + 1);
     }
  }

//+------------------------------------------------------------------+
//| Create-or-update one row of the corner-anchored panel. `row` is    |
//| the 0-based stack position (0 = header); rows stack downward from   |
//| InpSessionStatsYDistance using InpSessionStatsLineHeight spacing.   |
//+------------------------------------------------------------------+
void VO_DrawCornerLabel(const string name, const string text, const color clr, const int row)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_CORNER, InpSessionStatsCorner);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpSessionStatsFontSize);
     }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, InpSessionStatsXDistance);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, InpSessionStatsYDistance + row * InpSessionStatsLineHeight);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
  }

//+------------------------------------------------------------------+
void VO_DeleteAllRegimeObjects()
  {
   // One terminal call, prefix-filtered -- never a per-object walk of the
   // whole chart (which scales with every VO_Swings box and reference-level
   // ray on it, not with this indicator's own objects). Finding C1.
   ObjectsDeleteAll(0, VO_RGM_PREFIX + "|", -1, -1);
  }
//+------------------------------------------------------------------+
