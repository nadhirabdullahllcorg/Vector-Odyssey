//+------------------------------------------------------------------+
//| VO_BrokerTimeProbe.mq5                                           |
//|                                                                  |
//| Emits raw temporal observations about this broker's server time. |
//| It classifies NOTHING. Every conclusion - which DST calendar the  |
//| server follows, what its offset pair is, what its daily bar is    |
//| aligned to - is drawn in python/vo/time/probe.py, where it can be |
//| tested without a terminal.                                       |
//|                                                                  |
//| No trading functions. Read-only.                                 |
//|                                                                  |
//| WHY WEEKLY OPENS.                                                |
//| A CFD week opens at a fixed NEW YORK wall-clock time. Written as  |
//| server wall time that instant is:                                |
//|                                                                  |
//|     server_open_time_of_day = T_ny - ny_offset + server_offset    |
//|                                                                  |
//| so watching it across a year separates the three cases without    |
//| ever needing to know UTC for a historical bar:                    |
//|                                                                  |
//|   server follows US DST  -> both offsets move together, CONSTANT  |
//|   server follows EU DST  -> they move on different dates, so it   |
//|                             shifts an hour for the gap weeks      |
//|   server offset is fixed -> only NY moves, so it shifts at the    |
//|                             US DST dates                          |
//|                                                                  |
//| ISO-8601 NOTE.                                                   |
//| IsoUtc() below is the fix for defect D1. MQL5 TimeToString()      |
//| emits "2026.09.10 18:49:00" - dots, and a space instead of a T -  |
//| which is not ISO-8601 and which the Python deserializer rejects.  |
//| StringFormat gives the correct shape. VO_Json.mqh adopts this.    |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input int    InpMonthsBack   = 14;          // history to scan (>12 covers a full DST cycle)
input ENUM_TIMEFRAMES InpScanTf = PERIOD_M5;  // M5 resolves a 1-hour shift with room to spare
input int    InpGapHours     = 12;          // a break this long marks a weekend

//+------------------------------------------------------------------+
//| ISO-8601. This is the D1 fix.                                    |
//+------------------------------------------------------------------+
string IsoUtc(datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       s.year, s.mon, s.day, s.hour, s.min, s.sec);
}

// Server-local wall clock, no timezone claim attached.
string WallClock(datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
                       s.year, s.mon, s.day, s.hour, s.min, s.sec);
}

int MinuteOfDay(datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   return s.hour * 60 + s.min;
}

string JsonEscape(string v)
{
   string out = v;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\r", "\\r");
   StringReplace(out, "\t", "\\t");
   return out;
}

//+------------------------------------------------------------------+
void OnStart()
{
   string symbol = _Symbol;

   //--- 1. clock readings, all of them, so disagreement is visible ---
   //
   // TimeTradeServer() and TimeGMT() are BOTH computed in the terminal
   // from this PC's clock and timezone. Neither is fetched from the
   // server. Recording all four lets the analyser see whether they are
   // mutually consistent instead of trusting one difference.

   datetime t_current = TimeCurrent();       // last known server time, from tick data
   datetime t_server  = TimeTradeServer();   // calculated server time
   datetime t_gmt     = TimeGMT();           // GMT per this PC
   datetime t_local   = TimeLocal();         // this PC's local time

   long off_server_gmt  = (long)(t_server  - t_gmt);
   long off_current_gmt = (long)(t_current - t_gmt);

   Print("{\"record_type\":\"probe_clock\","
         "\"symbol\":\"", JsonEscape(symbol), "\","
         "\"server_name\":\"", JsonEscape(AccountInfoString(ACCOUNT_SERVER)), "\","
         "\"time_current_server\":\"", WallClock(t_current), "\","
         "\"time_trade_server\":\"", WallClock(t_server), "\","
         "\"time_gmt\":\"", IsoUtc(t_gmt), "\","
         "\"time_local\":\"", WallClock(t_local), "\","
         "\"offset_tradeserver_minus_gmt_seconds\":", (string)off_server_gmt, ","
         "\"offset_timecurrent_minus_gmt_seconds\":", (string)off_current_gmt, ","
         "\"pc_gmt_offset_seconds\":", (string)TimeGMTOffset(), ","
         "\"pc_daylight_savings_seconds\":", (string)TimeDaylightSavings(),
         "}");

   //--- 2. weekly opens across history ---

   datetime scan_to   = t_current;
   datetime scan_from = scan_to - (datetime)InpMonthsBack * 31 * 24 * 60 * 60;

   int gap_seconds = InpGapHours * 60 * 60;

   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   int emitted = 0;
   int chunks_failed = 0;

   // Walk in ~30-day chunks so CopyRates stays well inside its limits.
   datetime chunk_from = scan_from;
   datetime prev_bar_time = 0;

   while(chunk_from < scan_to)
   {
      datetime chunk_to = chunk_from + 30 * 24 * 60 * 60;
      if(chunk_to > scan_to)
         chunk_to = scan_to;

      int copied = CopyRates(symbol, InpScanTf, chunk_from, chunk_to, rates);

      if(copied <= 0)
      {
         chunks_failed++;
         Print("{\"record_type\":\"probe_gap_in_history\","
               "\"from\":\"", WallClock(chunk_from), "\","
               "\"to\":\"", WallClock(chunk_to), "\","
               "\"error\":", (string)GetLastError(), "}");
         chunk_from = chunk_to;
         continue;
      }

      for(int i = 0; i < copied; i++)
      {
         datetime bar = rates[i].time;

         if(prev_bar_time > 0 && (long)(bar - prev_bar_time) >= gap_seconds)
         {
            MqlDateTime s;
            TimeToStruct(bar, s);

            Print("{\"record_type\":\"probe_weekly_open\","
                  "\"server_time\":\"", WallClock(bar), "\","
                  "\"minute_of_day\":", (string)MinuteOfDay(bar), ","
                  "\"day_of_week\":", (string)s.day_of_week, ","
                  "\"gap_seconds\":", (string)(long)(bar - prev_bar_time), ","
                  "\"prev_bar_server_time\":\"", WallClock(prev_bar_time), "\"}");
            emitted++;
         }

         prev_bar_time = bar;
      }

      chunk_from = chunk_to;
   }

   //--- 3. daily bar boundaries, for the close-convention cross-check ---

   MqlRates daily[];
   ArraySetAsSeries(daily, false);

   int d_copied = CopyRates(symbol, PERIOD_D1, 0, 30, daily);

   for(int i = 0; i < d_copied; i++)
   {
      Print("{\"record_type\":\"probe_daily_open\","
            "\"server_time\":\"", WallClock(daily[i].time), "\","
            "\"minute_of_day\":", (string)MinuteOfDay(daily[i].time), "}");
   }

   //--- 4. what this run covered, so a thin result is not mistaken for a clean one ---

   Print("{\"record_type\":\"probe_summary\","
         "\"symbol\":\"", JsonEscape(symbol), "\","
         "\"scan_from\":\"", WallClock(scan_from), "\","
         "\"scan_to\":\"", WallClock(scan_to), "\","
         "\"scan_timeframe\":\"", EnumToString(InpScanTf), "\","
         "\"weekly_opens_found\":", (string)emitted, ","
         "\"history_chunks_failed\":", (string)chunks_failed, ","
         "\"daily_bars_found\":", (string)d_copied,
         "}");

   Print("VO BROKER TIME PROBE COMPLETE. ",
         emitted, " weekly opens over ", InpMonthsBack, " months. ",
         "Save the Experts tab and run the lines through vo.time.probe.");
}
//+------------------------------------------------------------------+
