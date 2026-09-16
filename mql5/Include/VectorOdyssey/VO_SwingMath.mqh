//+------------------------------------------------------------------+
//| VO_SwingMath.mqh                                                 |
//|                                                                  |
//| Pure calculation for the Phase 11 swing-drawing indicator - no   |
//| ObjectCreate, no chart access, no time-zone conversion, nothing  |
//| stateful. Mirrors VO_ReferenceLevelsMath.mqh's own split (see    |
//| that file's header): this is the "engine" half, ported from the |
//| Python side (vo.observation.atr / vo.observation.swings); the    |
//| indicator file (VO_Swings.mq5) is the "draw it on a chart" half  |
//| and owns no detection math of its own.                           |
//|                                                                  |
//| Deliberately timezone-agnostic: swing detection needs only bar   |
//| INDICES and OHLC prices, never wall-clock time, so unlike        |
//| VO_ReferenceLevelsMath.mqh this file has no DST/UTC concern at    |
//| all - that only enters at the labeling layer, in VO_Swings.mq5.  |
//|                                                                  |
//| WHY THE MATH IS RE-DERIVED HERE INSTEAD OF ASKING PYTHON:        |
//| Same reasoning as VO_ReferenceLevelsMath.mqh's own header - the  |
//| MT5<->Python bridge is one-way (MT5 -> Python only), so there is |
//| no channel for Python to hand this indicator a computed swing    |
//| list. vo.observation.swings.SwingEngine remains the single       |
//| source of truth for anything that *feeds a decision* later; this |
//| file only ever draws a picture on a chart.                       |
//|                                                                  |
//| FIDELITY TO THE PYTHON ALGORITHM - read before changing anything |
//| here without also changing python/vo/observation/atr.py or       |
//| swings.py, or vice versa:                                        |
//|   - True range / ATR: a plain simple moving average of true      |
//|     range in integer TICKS, exactly matching atr.py - not        |
//|     Wilder's smoothing. TR[0] (no previous bar) is just its own  |
//|     high-low range. ATR is "unavailable" (not a guessed partial  |
//|     average) for any index before a full period of history       |
//|     exists - see VO_AtrTicks's `available` out-parameter.        |
//|   - Fractal pivot test: STRICT inequality against every other    |
//|     bar in [pivot-k, pivot+k], both sides - matches              |
//|     swings.py::_check_new_pivot exactly (`>` for a high pivot,   |
//|     `<` for a low pivot; a tie anywhere in the window fails it). |
//|   - ATR filter: reversal_ticks >= atr_multiplier * atr_ticks,    |
//|     compared in floating point, exactly as swings.py::           |
//|     _try_confirm does it - NOT rounding the threshold to an      |
//|     integer first (an earlier draft of this file did that and   |
//|     it was wrong to; left as this comment so it isn't            |
//|     reintroduced by a future edit).                              |
//|   - The one KNOWN, NARROW divergence from the Python side:       |
//|     Python's round() is round-half-to-even (banker's rounding);  |
//|     MQL5's MathRound() is round-half-away-from-zero. The ATR     |
//|     average (`total ticks / period`) can therefore differ by     |
//|     exactly 1 tick between the two implementations on the rare   |
//|     input where that average lands exactly on a .5 boundary.     |
//|     A 1-tick ATR difference can only ever shift a confirmation   |
//|     decision that was already sitting exactly on the             |
//|     atr_multiplier threshold - stated here rather than silently  |
//|     hoped never to matter, the same "known, stated limits"       |
//|     discipline test_replay.py already uses for the no-lookahead  |
//|     detector's own edge cases.                                   |
//|   - CONFIRMED -> BROKEN lifecycle: a HIGH swing breaks the first  |
//|     bar whose high trades above it; a LOW swing breaks the first |
//|     bar whose low trades below it. Exactly one active swing per  |
//|     (level, type) at a time, matching SwingEngine's own          |
//|     `_active_high`/`_active_low` state.                          |
//|   - Processing order per bar: BROKEN check first, THEN new-pivot |
//|     check - matches SwingEngine.on_bar's own                     |
//|     `_check_breaks` then `_check_new_pivot` ordering.             |
//|                                                                  |
//| NO LOOKAHEAD: VO_DetectSwings processes `rates[]` as a single     |
//| forward pass, index i only ever reading rates[j] for j <= i (the |
//| pivot check at step i reads at most rates[i - 2k .. i], the      |
//| break check at step i reads only rates[i] against an already-    |
//| decided earlier swing). This is the same bound                   |
//| SwingEngine.on_bar enforces via CandleWindow on the Python side,  |
//| just expressed as an array-index invariant instead of a runtime   |
//| type - there is no MQL5 analogue of CandleWindow to enforce it    |
//| structurally, so this comment is the discipline instead.          |
//+------------------------------------------------------------------+
#property strict

enum ENUM_VO_SwingLevel
  {
   VO_SWING_LEVEL_INTERNAL = 0,
   VO_SWING_LEVEL_SWING    = 1
  };

enum ENUM_VO_SwingType
  {
   VO_SWING_TYPE_HIGH = 0,
   VO_SWING_TYPE_LOW  = 1
  };

enum ENUM_VO_SwingStatus
  {
   VO_SWING_STATUS_CONFIRMED = 0,
   VO_SWING_STATUS_BROKEN    = 1
  };

//--- One swing event - a CONFIRMED record, or its later BROKEN
//    correction. Mirrors vo.observation.swings.SwingPoint's fields
//    that matter for drawing; deliberately not a full port of every
//    CanonicalRecord field (object_id/observed_at/recorded_at are
//    reconstructed by the indicator itself for labeling, not carried
//    here - see VO_Swings.mq5).
struct VO_SwingEvent
  {
   ENUM_VO_SwingLevel  level;
   ENUM_VO_SwingType   swing_type;
   ENUM_VO_SwingStatus status;
   int                 pivot_bar;              // index into rates[]
   double              price;                  // pivot.high or pivot.low
   int                 confirmed_bar;          // CONFIRMED: the confirming bar; BROKEN: the breaking bar
   long                reversal_ticks;         // CONFIRMED only, 0 on BROKEN
   long                atr_ticks_at_pivot;      // CONFIRMED only, 0 on BROKEN
   double              reversal_extreme_price; // CONFIRMED only, 0 on BROKEN
   int                 reversal_extreme_bar;   // CONFIRMED only, -1 on BROKEN
   int                 broken_event_index;     // BROKEN only: index into the same events[] array
                                                // of the CONFIRMED event this one supersedes; -1 on CONFIRMED
  };

long VO_ToTicks(const double price, const double tick_size)
  {
   return (long)MathRound(price / tick_size);
  }

long VO_TrueRangeTicks(const long high_ticks, const long low_ticks,
                        const bool has_previous, const long prev_close_ticks)
  {
   long result = high_ticks - low_ticks;
   if(!has_previous)
      return result;
   const long gap_high = MathAbs(high_ticks - prev_close_ticks);
   const long gap_low  = MathAbs(low_ticks - prev_close_ticks);
   if(gap_high > result)
      result = gap_high;
   if(gap_low > result)
      result = gap_low;
   return result;
  }

//--- SMA of true-range ticks over `period` bars ending at (and
//    including) `index`. `available=false` (not a guessed partial
//    average) when fewer than `period` bars exist up to `index` -
//    matches atr.py::atr_ticks returning None for the same case.
void VO_AtrTicks(const long &tr_ticks[], const int index, const int period,
                  long &atr_out, bool &available)
  {
   if(period < 1 || index < period - 1)
     {
      atr_out = 0;
      available = false;
      return;
     }
   long total = 0;
   for(int i = index - period + 1; i <= index; i++)
      total += tr_ticks[i];
   atr_out = (long)MathRound((double)total / (double)period);
   available = true;
  }

//+------------------------------------------------------------------+
//| Detects every swing (both CONFIRMED and its later BROKEN, if any)|
//| for ONE tier (level/k/atr_multiplier) over the whole `rates[]`    |
//| array, appending to `events[]` (resized as needed; starts empty).|
//| Mirrors constructing one SwingEngine instance per tier on the     |
//| Python side (SwingEngine.for_level) and running it bar by bar.    |
//+------------------------------------------------------------------+
void VO_DetectSwings(const MqlRates &rates[], const int count,
                     const ENUM_VO_SwingLevel level, const int k,
                     const int atr_period, const double atr_multiplier,
                     const double tick_size, VO_SwingEvent &events[])
  {
   ArrayResize(events, 0);
   if(count < 1 || k < 1 || atr_period < 1 || atr_multiplier <= 0.0 || tick_size <= 0.0)
      return;

   long tr_ticks[];
   ArrayResize(tr_ticks, count);
   for(int i = 0; i < count; i++)
     {
      const long h = VO_ToTicks(rates[i].high, tick_size);
      const long l = VO_ToTicks(rates[i].low, tick_size);
      if(i == 0)
         tr_ticks[i] = h - l;
      else
        {
         const long pc = VO_ToTicks(rates[i - 1].close, tick_size);
         tr_ticks[i] = VO_TrueRangeTicks(h, l, true, pc);
        }
     }

   int active_high_event = -1; // index into events[], -1 = none active
   int active_low_event  = -1;
   int event_count = 0;

   for(int i = 0; i < count; i++)
     {
      // --- BROKEN check against currently active swings, at this bar
      // (matches SwingEngine._check_breaks, run before the pivot check).
      if(active_high_event >= 0 && rates[i].high > events[active_high_event].price)
        {
         VO_AppendBrokenEvent(events, event_count, active_high_event, i);
         active_high_event = -1;
        }
      if(active_low_event >= 0 && rates[i].low < events[active_low_event].price)
        {
         VO_AppendBrokenEvent(events, event_count, active_low_event, i);
         active_low_event = -1;
        }

      // --- New pivot check (matches SwingEngine._check_new_pivot).
      const int pivot_index = i - k;
      if(pivot_index < k)
         continue;

      bool is_high_pivot = true;
      bool is_low_pivot  = true;
      for(int j = pivot_index - k; j <= pivot_index + k; j++)
        {
         if(j == pivot_index)
            continue;
         if(rates[j].high >= rates[pivot_index].high)
            is_high_pivot = false;
         if(rates[j].low <= rates[pivot_index].low)
            is_low_pivot = false;
        }

      if(!is_high_pivot && !is_low_pivot)
         continue;

      long atr_ticks_val;
      bool atr_ready;
      VO_AtrTicks(tr_ticks, pivot_index, atr_period, atr_ticks_val, atr_ready);
      if(!atr_ready)
         continue;

      if(is_high_pivot)
        {
         int  extreme_bar = pivot_index + 1;
         long extreme_low_ticks = VO_ToTicks(rates[extreme_bar].low, tick_size);
         for(int j = pivot_index + 2; j <= pivot_index + k; j++)
           {
            const long lt = VO_ToTicks(rates[j].low, tick_size);
            if(lt < extreme_low_ticks)
              {
               extreme_low_ticks = lt;
               extreme_bar = j;
              }
           }
         const long pivot_high_ticks = VO_ToTicks(rates[pivot_index].high, tick_size);
         const long reversal_ticks = pivot_high_ticks - extreme_low_ticks;
         // Float comparison, matching swings.py::_try_confirm exactly -
         // the threshold itself is never rounded (see header comment).
         if((double)reversal_ticks >= atr_multiplier * (double)atr_ticks_val)
           {
            active_high_event = VO_AppendConfirmedEvent(
               events, event_count, level, VO_SWING_TYPE_HIGH, pivot_index,
               rates[pivot_index].high, i, reversal_ticks, atr_ticks_val,
               rates[extreme_bar].low, extreme_bar);
           }
        }

      if(is_low_pivot)
        {
         int  extreme_bar = pivot_index + 1;
         long extreme_high_ticks = VO_ToTicks(rates[extreme_bar].high, tick_size);
         for(int j = pivot_index + 2; j <= pivot_index + k; j++)
           {
            const long ht = VO_ToTicks(rates[j].high, tick_size);
            if(ht > extreme_high_ticks)
              {
               extreme_high_ticks = ht;
               extreme_bar = j;
              }
           }
         const long pivot_low_ticks = VO_ToTicks(rates[pivot_index].low, tick_size);
         const long reversal_ticks = extreme_high_ticks - pivot_low_ticks;
         if((double)reversal_ticks >= atr_multiplier * (double)atr_ticks_val)
           {
            active_low_event = VO_AppendConfirmedEvent(
               events, event_count, level, VO_SWING_TYPE_LOW, pivot_index,
               rates[pivot_index].low, i, reversal_ticks, atr_ticks_val,
               rates[extreme_bar].high, extreme_bar);
           }
        }
     }
  }

//--- Appends a CONFIRMED event, returns its index in events[].
int VO_AppendConfirmedEvent(VO_SwingEvent &events[], int &event_count,
                            const ENUM_VO_SwingLevel level, const ENUM_VO_SwingType swing_type,
                            const int pivot_bar, const double price, const int confirmed_bar,
                            const long reversal_ticks, const long atr_ticks_at_pivot,
                            const double reversal_extreme_price, const int reversal_extreme_bar)
  {
   event_count++;
   ArrayResize(events, event_count);
   const int idx = event_count - 1;
   events[idx].level = level;
   events[idx].swing_type = swing_type;
   events[idx].status = VO_SWING_STATUS_CONFIRMED;
   events[idx].pivot_bar = pivot_bar;
   events[idx].price = price;
   events[idx].confirmed_bar = confirmed_bar;
   events[idx].reversal_ticks = reversal_ticks;
   events[idx].atr_ticks_at_pivot = atr_ticks_at_pivot;
   events[idx].reversal_extreme_price = reversal_extreme_price;
   events[idx].reversal_extreme_bar = reversal_extreme_bar;
   events[idx].broken_event_index = -1;
   return idx;
  }

//--- Appends a BROKEN event superseding events[confirmed_index].
void VO_AppendBrokenEvent(VO_SwingEvent &events[], int &event_count,
                          const int confirmed_index, const int breaking_bar)
  {
   event_count++;
   ArrayResize(events, event_count);
   const int idx = event_count - 1;
   events[idx].level = events[confirmed_index].level;
   events[idx].swing_type = events[confirmed_index].swing_type;
   events[idx].status = VO_SWING_STATUS_BROKEN;
   events[idx].pivot_bar = events[confirmed_index].pivot_bar;
   events[idx].price = events[confirmed_index].price;
   events[idx].confirmed_bar = breaking_bar;
   events[idx].reversal_ticks = 0;
   events[idx].atr_ticks_at_pivot = 0;
   events[idx].reversal_extreme_price = 0;
   events[idx].reversal_extreme_bar = -1;
   events[idx].broken_event_index = confirmed_index;
  }
