//+------------------------------------------------------------------+
//| VO_Regime.mq5                                                    |
//|                                                                  |
//| Draws Phase 13's Regime Engine (architecture/vo-phase-plan.md,    |
//| Gate G6) as colored background bands on an MT5 chart -- one band   |
//| per regime segment, from where the regime took hold to where it   |
//| resolved (or to the live chart edge, if it is still current).     |
//|                                                                  |
//| PHASE 13a -- "PYTHON COMPUTES, THE INDICATOR READS A FILE"         |
//| (the approach the user chose). This file contains NO regime        |
//| classification logic at all -- unlike VO_Swings.mq5, which ports   |
//| the swing math into MQL5, the regime state machine is far too      |
//| stateful (bitemporal supersession, ER/Hurst evidence, an           |
//| anticipation lean) to port faithfully, and a second drifting copy  |
//| is exactly what gate G6 exists to prevent. Instead the REAL        |
//| vo.observation.regime.RegimeEngine runs in Python                  |
//| (scripts/publish_regime.py) over the same wire bars VO_EA tails,   |
//| and writes its emitted bands to <symbol>_regime.feed in the        |
//| terminal's MQL5\Files\<subdir>\ folder. This file only reads that  |
//| feed and paints it. If a band looks wrong, the bug is in the       |
//| Python engine or the feed, never here.                            |
//|                                                                  |
//| FEED FORMAT (vo.telemetry.regime_feed, v2). Lines beginning '#'    |
//| are provenance comments and are skipped. Every other line's FIRST  |
//| field is a tag, "BAND" or "MARK", so the two can never be confused |
//| even by field count alone:                                        |
//|                                                                  |
//|   BAND | object_id | regime | direction | start_epoch | end_epoch |
//|        | high | low | confidence | anticipated   (10 fields)      |
//|   MARK | object_id | regime | direction | at_epoch | price |       |
//|        confidence                        (7 fields)                |
//|                                                                  |
//| - object_id: the real RegimeState.object_id (gate G8: the chart    |
//|   object embeds it, so any band/marker is traceable to one          |
//|   canonical record on the Python side).                            |
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
//| COMPILE/VERIFY NOTE: not yet compiled or run against a live        |
//| terminal from this session. Written to match VO_Swings.mq5's       |
//| structure closely; needs the same validation: compile in           |
//| MetaEditor, run scripts/publish_regime.py so the feed exists, then |
//| attach to the matching chart and confirm the bands land on the     |
//| right bars/prices.                                                |
//+------------------------------------------------------------------+
#property strict
#property indicator_chart_window
#property indicator_plots 0

//--- Object naming/versioning -- same "bump on breaking change" rule as
//    VO_SWG_PREFIX in VO_Swings.mq5 / VO_REF_PREFIX in VO_ReferenceLevels.mq5.
//    v1 -> v2: feed format gained the BAND/MARK tag and resolution markers;
//    old v1 chart objects are orphaned by this prefix bump, same as any
//    other breaking change here -- remove/re-add the indicator once to
//    clear them.
#define VO_RGM_PREFIX "VO_RGM_v2"
#define VO_TAG_BAND "BAND"
#define VO_TAG_MARK "MARK"
#define VO_FEED_BAND_FIELDS 10
#define VO_FEED_MARKER_FIELDS 7

input group "=== Feed source (MQL5\\Files\\<subdir>\\<symbol>_regime.feed) ==="
input string InpFeedSubdir     = "VectorOdyssey"; // must match VO_Bridge InpOutputSubdir / vo_ea.yaml wire.dir
input string InpSymbolOverride = "";              // blank = this chart's symbol; else e.g. "US100"

input group "=== Refresh ==="
input int    InpRefreshSeconds = 5;   // re-read the feed on this timer (publish_regime --watch interval)

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

//--- Re-read bookkeeping (same pattern as VO_Swings.mq5's g_last_seen_bar_open).
datetime g_last_seen_bar_open = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   IndicatorSetString(INDICATOR_SHORTNAME, "VO Regime");
   if(InpRefreshSeconds > 0)
      EventSetTimer(InpRefreshSeconds);
   VO_ReadAndDraw();
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   // Same hygiene as VO_Swings.mq5: keep objects on ordinary
   // close/recompile/timeframe change; remove only on explicit detach.
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
   return StringFormat("%s\\%s_regime.feed", InpFeedSubdir, sym);
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
//| a stale object behind.                                            |
//+------------------------------------------------------------------+
void VO_ReadAndDraw()
  {
   const string path = VO_FeedPath();
   // FILE_SHARE_WRITE so a concurrent publish_regime --watch write does
   // not lock us out; the publisher writes atomically (tmp + rename) so
   // we never see a half-written feed anyway.
   const int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI |
                               FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(handle == INVALID_HANDLE)
      return; // feed not published yet -- leave whatever is on the chart

   // The live edge a still-current (end_epoch 0) band is extended to.
   const datetime live_edge = iTime(_Symbol, PERIOD_CURRENT, 0);

   VO_DeleteAllRegimeObjects();

   int drawn = 0;
   while(!FileIsEnding(handle))
     {
      const string line = FileReadString(handle);
      if(StringLen(line) == 0)
         continue;
      if(StringGetCharacter(line, 0) == '#')
         continue; // provenance comment

      string f[];
      const int n = StringSplit(line, '|', f);
      if(n < 1)
         continue; // empty/malformed line -- skip defensively

      if(f[0] == VO_TAG_BAND && n == VO_FEED_BAND_FIELDS)
        {
         VO_DrawBand(f, live_edge, drawn);
         drawn++;
        }
      else if(f[0] == VO_TAG_MARK && n == VO_FEED_MARKER_FIELDS)
        {
         VO_DrawMarker(f, drawn);
         drawn++;
        }
      // else: unrecognized tag or field-count drift -- skip defensively,
      // same "the bug is in the Python engine or the feed, never here"
      // discipline as everywhere else in this file.
     }

   FileClose(handle);
  }

//+------------------------------------------------------------------+
//| Draw one band + its label from nine already-split feed fields.    |
//| `ordinal` disambiguates object names (object_id is embedded in a   |
//| tooltip for G8, and in the name too, but two segments of the same  |
//| regime at the same start would otherwise collide).                |
//+------------------------------------------------------------------+
void VO_DrawBand(const string &f[], const datetime live_edge, const int ordinal)
  {
   // f[0] is the "BAND" tag (already checked by the caller).
   const string object_id   = f[1];
   const string regime      = f[2];
   const string direction   = f[3];
   const datetime start_t   = (datetime)StringToInteger(f[4]);
   const long   end_epoch   = StringToInteger(f[5]);
   const double high        = StringToDouble(f[6]);
   const double low         = StringToDouble(f[7]);
   const double confidence  = StringToDouble(f[8]);
   const string anticipated = f[9];

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
   const string tooltip = StringFormat(
      "VO Regime: %s%s\nconfidence: %s%s\nid: %s",
      regime, dir_text, DoubleToString(confidence, 2), ant_text, object_id);
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
//| Draw one resolution marker from six already-split MARK fields.    |
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
   const string tooltip = StringFormat(
      "VO Regime resolution: %s%s\nconfidence: %s\nid: %s",
      regime, dir_text, DoubleToString(confidence, 2), object_id);
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
void VO_DeleteAllRegimeObjects()
  {
   const string prefix = VO_RGM_PREFIX + "|";
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
     {
      const string name = ObjectName(0, i);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
     }
  }
//+------------------------------------------------------------------+
