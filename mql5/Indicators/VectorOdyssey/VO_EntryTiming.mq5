//+------------------------------------------------------------------+
//| VO_EntryTiming.mq5                                               |
//|                                                                  |
//| Efficiency Ratio and ATR, together, in their own subwindow.      |
//|                                                                  |
//| WHY THIS IS ITS OWN INDICATOR. ER and Hurst also ride along on   |
//| RegimeState as recorded evidence for a classification, and that  |
//| is the right place for them in THAT role. But                    |
//| vo-trade-logic-and-brain-plan.md Section 5.7 gave ER a second,   |
//| different job at the user's own direction: entry timing,         |
//| considered relative to ATR, once a regime change is already in   |
//| effect -- never bias, never setup selection. Two jobs in one     |
//| home means every change made for one perturbs the other, which   |
//| is exactly the entanglement that made splitting VO_Regime out of |
//| VO_ReferenceLevels worth doing. Same reasoning, same split.      |
//|                                                                  |
//| COMPUTED NATIVELY, NOT READ FROM A FEED -- deliberately, and     |
//| unlike VO_Regime. The regime classifier is real logic with a     |
//| state machine and Month 1 provenance behind it; a second MQL5    |
//| copy of it would drift from the Python one of record, so it      |
//| reads a feed instead (gate G6). ER and ATR are two short         |
//| formulas with no state and no interpretation, so a native        |
//| computation cannot meaningfully drift, and computing here buys   |
//| a live read on every timeframe with no feed to go stale -- the   |
//| same trade VO_SwingMath.mqh already makes for swing math.        |
//|                                                                  |
//| BOTH FORMULAS MIRROR THE PYTHON EXACTLY, and the Python is the   |
//| one of record if they ever disagree:                             |
//|                                                                  |
//|   ER  = |close[i] - close[i-N]| / SUM |close[k] - close[k-1]|    |
//|         over k in (i-N, i]   -- vo.observation.efficiency_ratio  |
//|         A flat window is 0.0, not undefined: no movement is      |
//|         maximally non-directional, the consolidation extreme.    |
//|                                                                  |
//|   ATR = SIMPLE moving average of true range over N bars.         |
//|         NOT iATR(), and this matters: MT5's built-in ATR uses    |
//|         Wilder's recursive smoothing, which is a different       |
//|         number. vo.observation.atr uses an SMA deliberately, for |
//|         reproducibility with no seed ambiguity, so this does     |
//|         too. Using iATR() here would silently put a different    |
//|         value on the chart than the engine records.               |
//|                                                                  |
//| DECIDES NOTHING. Section 5.7 names the ingredients -- ER against |
//| ATR -- and stops there. No threshold, ratio or trigger rule has  |
//| been designed, so none is drawn here: no signal arrows, no       |
//| colour changes at a level, no "entry zone" shading. Two honest   |
//| lines and the numbers. The rule that eventually reads them is a  |
//| separate reviewable decision (G6), and showing a threshold       |
//| before one exists would invent it by suggestion.                  |
//|                                                                  |
//| Section 2a compliance: this is a subwindow oscillator pane, not  |
//| a band or channel drawn over price. No envelope is plotted on    |
//| the price chart itself.                                          |
//+------------------------------------------------------------------+
#property strict
#property indicator_separate_window
#property indicator_buffers 2
#property indicator_plots   2

#property indicator_label1  "Efficiency Ratio"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDodgerBlue
#property indicator_width1  2

#property indicator_label2  "ATR (normalised)"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrDarkOrange
#property indicator_width2  1

input int  InpErPeriod          = 14;   // ER window, bars (mirrors regime.yaml v3)
input int  InpAtrPeriod         = 14;   // ATR window, bars (SMA of true range)
input int  InpAtrScalePeriod    = 200;  // bars used to scale ATR onto ER's 0..1 axis
input bool InpShowReadout       = true; // corner readout of the raw values

double g_er[];
double g_atr_scaled[];
double g_atr_raw[];

string g_readout_name = "VO_ETM_readout";

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, g_er,         INDICATOR_DATA);
   SetIndexBuffer(1, g_atr_scaled, INDICATOR_DATA);
   SetIndexBuffer(2, g_atr_raw,    INDICATOR_CALCULATIONS);

   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   IndicatorSetString(INDICATOR_SHORTNAME,
                      StringFormat("VO Entry Timing (ER %d / ATR %d)",
                                   InpErPeriod, InpAtrPeriod));
   IndicatorSetInteger(INDICATOR_DIGITS, 4);

   // ER is bounded [0,1] by construction; fixing the scale stops the
   // pane rescaling every bar, which makes the series unreadable.
   IndicatorSetDouble(INDICATOR_MINIMUM, 0.0);
   IndicatorSetDouble(INDICATOR_MAXIMUM, 1.0);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(reason == REASON_REMOVE || reason == REASON_TEMPLATE)
      ObjectDelete(0, g_readout_name);
}

//+------------------------------------------------------------------+
//| Kaufman ER over the `period` closes ending at `i`. Mirrors        |
//| vo.observation.efficiency_ratio exactly, including the flat-window |
//| 0.0 case.                                                         |
//+------------------------------------------------------------------+
double VO_EfficiencyRatio(const double &close[], const int i, const int period)
{
   if(i < period)
      return(EMPTY_VALUE);

   double direction = MathAbs(close[i] - close[i - period]);
   double volatility = 0.0;
   for(int k = i - period + 1; k <= i; k++)
      volatility += MathAbs(close[k] - close[k - 1]);

   if(volatility == 0.0)
      return(0.0);   // no movement is maximally non-directional

   return(direction / volatility);
}

//+------------------------------------------------------------------+
//| SIMPLE moving average of true range -- NOT Wilder's. See header.  |
//+------------------------------------------------------------------+
double VO_AtrSma(const double &high[], const double &low[], const double &close[],
                 const int i, const int period)
{
   if(i < period)
      return(EMPTY_VALUE);

   double total = 0.0;
   for(int k = i - period + 1; k <= i; k++)
   {
      double range = high[k] - low[k];
      // The first observed bar has no prior close to gap from; every bar
      // reached here does, since k >= 1 whenever i >= period >= 1.
      double up   = MathAbs(high[k] - close[k - 1]);
      double down = MathAbs(low[k]  - close[k - 1]);
      total += MathMax(range, MathMax(up, down));
   }

   return(total / period);
}

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   int warmup = MathMax(InpErPeriod, InpAtrPeriod) + 1;
   if(rates_total <= warmup)
      return(0);

   int start = (prev_calculated > warmup) ? prev_calculated - 1 : warmup;

   for(int i = start; i < rates_total; i++)
   {
      g_er[i]      = VO_EfficiencyRatio(close, i, InpErPeriod);
      g_atr_raw[i] = VO_AtrSma(high, low, close, i, InpAtrPeriod);

      // ATR is a price magnitude and ER is a ratio, so they share no
      // natural axis. Rather than a second scale (which invites reading
      // a crossing as meaningful when it is an artefact of scaling),
      // ATR is shown as its own rank within a recent window: 0 = the
      // quietest this has been lately, 1 = the most volatile. That is a
      // comparison the eye can trust.
      double scaled = EMPTY_VALUE;
      if(g_atr_raw[i] != EMPTY_VALUE)
      {
         int from = MathMax(warmup, i - InpAtrScalePeriod + 1);
         double lowest = g_atr_raw[i], highest = g_atr_raw[i];
         for(int k = from; k <= i; k++)
         {
            if(g_atr_raw[k] == EMPTY_VALUE)
               continue;
            if(g_atr_raw[k] < lowest)  lowest  = g_atr_raw[k];
            if(g_atr_raw[k] > highest) highest = g_atr_raw[k];
         }
         scaled = (highest > lowest) ? (g_atr_raw[i] - lowest) / (highest - lowest) : 0.5;
      }
      g_atr_scaled[i] = scaled;
   }

   if(InpShowReadout && rates_total > 0)
   {
      int last = rates_total - 1;
      string text = StringFormat("ER %.4f    ATR %.2f (rank %.2f)",
                                 g_er[last], g_atr_raw[last], g_atr_scaled[last]);
      if(ObjectFind(0, g_readout_name) < 0)
      {
         ObjectCreate(0, g_readout_name, OBJ_LABEL, ChartWindowFind(), 0, 0);
         ObjectSetInteger(0, g_readout_name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
         ObjectSetInteger(0, g_readout_name, OBJPROP_XDISTANCE, 10);
         ObjectSetInteger(0, g_readout_name, OBJPROP_YDISTANCE, 14);
         ObjectSetInteger(0, g_readout_name, OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER);
         ObjectSetInteger(0, g_readout_name, OBJPROP_COLOR, clrSilver);
         ObjectSetInteger(0, g_readout_name, OBJPROP_FONTSIZE, 9);
         ObjectSetInteger(0, g_readout_name, OBJPROP_SELECTABLE, false);
      }
      ObjectSetString(0, g_readout_name, OBJPROP_TEXT, text);
   }

   return(rates_total);
}
//+------------------------------------------------------------------+
