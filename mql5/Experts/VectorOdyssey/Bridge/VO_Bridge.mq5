//+------------------------------------------------------------------+
//| VO_Bridge.mq5                                                    |
//|                                                                  |
//| The single Vector Odyssey market-data bridge, replacing          |
//| VO_TickBridge.mq5, VO_BarBridge.mq5 and VO_SymbolBridge.mq5.      |
//|                                                                  |
//| WHY ONE EA INSTEAD OF THREE (defect F6 / Phase 3, collision C6 in |
//| the candle-layer design).                                        |
//| MT5 runs one EA per chart. Three separate EAs meant tick and bar  |
//| records for the same instrument came from unrelated processes    |
//| with no shared state, so nothing could tie a bar to the ticks     |
//| that built it, and nothing could tell a live tick from one        |
//| replayed out of history. One EA can carry a shared seq counter,   |
//| a shared source identity, and a shared knowledge of whether it is |
//| currently backfilling or live.                                   |
//|                                                                  |
//| WHAT THIS FIXES FROM THE PRE-PHASE-3 BRIDGES.                    |
//|   D1  Bar timestamps used TimeToString() - not ISO-8601.          |
//|       Fixed: VO_Json.mqh's VO_IsoServerTime().                    |
//|   D2  String fields were concatenated with no escaping.           |
//|       Fixed: VO_Json.mqh's VO_JsonEscape() / VO_JsonString(),      |
//|       used inside every VO_Records.mqh builder.                   |
//|   F2  Bar/tick times were silently relabeled "UTC" using a live,  |
//|       PC-derived TimeTradeServer()-TimeGMT() guess, discarding    |
//|       the raw server time in the process.                         |
//|       Fixed: this EA does not convert timezones at all. It emits  |
//|       broker server wall-clock, honestly typed, and leaves UTC    |
//|       resolution to the Time Engine (Phase 6), which uses an      |
//|       OBSERVED broker calendar profile instead of a live guess.   |
//|                                                                  |
//| WHAT THIS ADDS (the Phase 3 deliverable list).                   |
//|   spread, a monotonic seq per source, tick flags, volume_real,    |
//|   source_feed (live vs. history), decomposed provenance           |
//|   (platform / broker_server / broker_symbol instead of one fused  |
//|   "source" string), a durable file transport instead of only      |
//|   Print(), a startup CopyTicksRange/CopyRates backfill so a       |
//|   restart does not silently lose the gap, and a one-time          |
//|   source_capabilities record so "does this broker provide real    |
//|   volume" is an observed fact recorded once, not guessed per bar. |
//|                                                                  |
//| No trading functions. This EA observes; it does not decide.       |
//+------------------------------------------------------------------+
#property strict

#include <VectorOdyssey/VO_Json.mqh>
#include <VectorOdyssey/VO_Records.mqh>
#include <VectorOdyssey/VO_Transport.mqh>

input bool             InpEmitTicks           = true;
input bool             InpEmitBars            = true;
input ENUM_TIMEFRAMES  InpBarTimeframe        = PERIOD_CURRENT;
input bool             InpBackfillOnStart     = true;
input int              InpBarBackfillCount    = 500;   // closed bars to replay at startup
input int              InpTickBackfillMinutes = 60;    // CopyTicksRange window at startup
input string           InpOutputSubdir        = "VectorOdyssey"; // under MQL5\Files\

// ── identity, shared by every record this EA emits ─────────────────
string g_platform;
string g_broker_server;
string g_broker_symbol;

// ── per-source monotonic counters (Phase 3 deliverable: seq) ───────
long g_tick_seq = 0;
long g_bar_seq  = 0;

// ── file sinks ───────────────────────────────────────────────────
int g_tick_sink = INVALID_HANDLE;
int g_bar_sink  = INVALID_HANDLE;
int g_meta_sink = INVALID_HANDLE;

datetime g_last_bar_time = 0;

// Effective bar timeframe. InpBarTimeframe defaults to PERIOD_CURRENT,
// whose EnumToString() is the literal "PERIOD_CURRENT" -- not a real,
// fixed timeframe. The Python pipeline (Timeframe.from_mt5) correctly
// refuses that string and quarantines every such bar. Resolving
// PERIOD_CURRENT to the chart's actual period here (in OnInit) means the
// emitted timeframe is always a concrete, mappable value like
// "PERIOD_M1", whatever the input is left at.
ENUM_TIMEFRAMES g_bar_tf = PERIOD_CURRENT;

//+------------------------------------------------------------------+
void EmitLine(int &sink, const string line)
{
   Print(line);
   VO_WriteLine(sink, line);
}

//+------------------------------------------------------------------+
//| [VO-D] Evidence-based, not clairvoyant. A bounded history scan    |
//| finding only zeros is not proof this source never reports real    |
//| volume - it is a lower bound. See schema.py's                     |
//| SOURCE_CAPABILITIES_V1 note. This function only ever asserts       |
//| "observed" true; it never asserts a confident false.               |
//+------------------------------------------------------------------+
bool ProbeRealVolumeAvailable()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int copied = CopyRates(_Symbol, InpBarTimeframe, 0, 500, rates);

   for(int i = 0; i < copied; i++)
      if(rates[i].real_volume > 0)
         return true;

   return false;
}

bool ProbeTickLevelAvailable()
{
   MqlTick ticks[];
   datetime to   = TimeCurrent();
   datetime from = to - 60 * 60; // last hour
   ulong from_ms = (ulong)from * 1000;
   ulong to_ms   = (ulong)to * 1000;

   int copied = CopyTicksRange(_Symbol, ticks, COPY_TICKS_ALL, from_ms, to_ms);

   return copied > 0;
}

//+------------------------------------------------------------------+
int OnInit()
{
   g_platform      = "MT5";
   g_broker_server = AccountInfoString(ACCOUNT_SERVER);
   g_broker_symbol = _Symbol;
   g_bar_tf        = (InpBarTimeframe == PERIOD_CURRENT) ? Period() : InpBarTimeframe;

   g_tick_sink = VO_OpenSink(InpOutputSubdir, g_broker_symbol + "_ticks.jsonl");
   g_bar_sink  = VO_OpenSink(InpOutputSubdir, g_broker_symbol + "_bars.jsonl");
   g_meta_sink = VO_OpenSink(InpOutputSubdir, g_broker_symbol + "_meta.jsonl");

   Print("VO BRIDGE STARTED. symbol=", g_broker_symbol,
         " server=", g_broker_server,
         " bar_timeframe=", EnumToString(g_bar_tf));

   // ── symbol metadata, once ──────────────────────────────────────
   string symbol_record = VO_BuildSymbolRecord(
      g_broker_symbol,
      SymbolInfoString(_Symbol, SYMBOL_DESCRIPTION),
      (int)_Digits,
      _Point,
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE),
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE),
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE),
      g_platform,
      g_broker_server
   );
   EmitLine(g_meta_sink, symbol_record);

   // ── source capabilities, once ──────────────────────────────────
   bool real_volume_available = ProbeRealVolumeAvailable();
   bool tick_level_available  = ProbeTickLevelAvailable();

   string capabilities_record = VO_BuildSourceCapabilitiesRecord(
      g_platform, g_broker_server, g_broker_symbol,
      real_volume_available, tick_level_available
   );
   EmitLine(g_meta_sink, capabilities_record);

   // ── startup backfill, so an EA restart does not lose the gap ───
   if(InpBackfillOnStart)
   {
      if(InpEmitBars)
         BackfillBars();

      if(InpEmitTicks)
         BackfillTicks();
   }

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void BackfillBars()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   // Shift 1 = most recently completed bar; skip shift 0, the forming one.
   int copied = CopyRates(_Symbol, g_bar_tf, 1, InpBarBackfillCount, rates);

   if(copied <= 0)
   {
      Print("VO BRIDGE: bar backfill CopyRates() failed, error=", GetLastError());
      return;
   }

   // CopyRates with ArraySetAsSeries(true) returns newest first; emit
   // oldest first so seq and file order both read chronologically.
   for(int i = copied - 1; i >= 0; i--)
   {
      string record = VO_BuildBarRecord(
         rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close,
         (int)_Digits, rates[i].tick_volume, rates[i].real_volume, rates[i].spread,
         EnumToString(g_bar_tf), ++g_bar_seq, "history",
         g_platform, g_broker_server, g_broker_symbol
      );
      EmitLine(g_bar_sink, record);
   }

   g_last_bar_time = rates[0].time;
}

//+------------------------------------------------------------------+
void BackfillTicks()
{
   MqlTick ticks[];
   datetime to   = TimeCurrent();
   datetime from = to - InpTickBackfillMinutes * 60;
   ulong from_ms = (ulong)from * 1000;
   ulong to_ms   = (ulong)to * 1000;

   int copied = CopyTicksRange(_Symbol, ticks, COPY_TICKS_ALL, from_ms, to_ms);

   if(copied <= 0)
   {
      Print("VO BRIDGE: tick backfill CopyTicksRange() found nothing "
            "(error=", GetLastError(), "). Not fatal - many brokers keep "
            "little or no tick history.");
      return;
   }

   for(int i = 0; i < copied; i++)
   {
      string record = VO_BuildTickRecord(
         ticks[i].time_msc, ticks[i].bid, ticks[i].ask, ticks[i].last,
         (int)_Digits, ticks[i].volume, ticks[i].volume_real, ticks[i].flags,
         ++g_tick_seq, "history",
         g_platform, g_broker_server, g_broker_symbol
      );
      EmitLine(g_tick_sink, record);
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(InpEmitTicks)
   {
      MqlTick tick;

      if(!SymbolInfoTick(_Symbol, tick))
      {
         Print("VO BRIDGE ERROR: SymbolInfoTick() failed, error=", GetLastError());
      }
      else
      {
         string tick_record = VO_BuildTickRecord(
            tick.time_msc, tick.bid, tick.ask, tick.last,
            (int)_Digits, tick.volume, tick.volume_real, tick.flags,
            ++g_tick_seq, "live",
            g_platform, g_broker_server, g_broker_symbol
         );
         EmitLine(g_tick_sink, tick_record);
      }
   }

   if(InpEmitBars)
      EmitBarIfClosed();
}

//+------------------------------------------------------------------+
void EmitBarIfClosed()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   // Shift 1 = most recently completed bar.
   int copied = CopyRates(_Symbol, g_bar_tf, 1, 1, rates);

   if(copied != 1)
   {
      Print("VO BRIDGE ERROR: CopyRates() failed, error=", GetLastError());
      return;
   }

   if(rates[0].time == g_last_bar_time)
      return; // already emitted this bar

   string bar_record = VO_BuildBarRecord(
      rates[0].time, rates[0].open, rates[0].high, rates[0].low, rates[0].close,
      (int)_Digits, rates[0].tick_volume, rates[0].real_volume, rates[0].spread,
      EnumToString(g_bar_tf), ++g_bar_seq, "live",
      g_platform, g_broker_server, g_broker_symbol
   );
   EmitLine(g_bar_sink, bar_record);

   g_last_bar_time = rates[0].time;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   VO_CloseSink(g_tick_sink);
   VO_CloseSink(g_bar_sink);
   VO_CloseSink(g_meta_sink);
}
//+------------------------------------------------------------------+
