//+------------------------------------------------------------------+
//| VO_DataIngestionDiagnostic.mq5|
//| Purpose: Inspect raw broker data available to Vector Odyssey |
//| No trading functions |
//+------------------------------------------------------------------+
#property strict

ulong tick_count = 0;
datetime last_summary = 0;
long previous_tick_msc = 0;

double previous_bid = 0.0;
double previous_ask = 0.0;

int OnInit()
{
Print("==================================================");
Print("VO DATA INGESTION DIAGNOSTIC STARTED");
Print("==================================================");

Print("Symbol: ", _Symbol);
Print("Digits: ", _Digits);
Print("Point: ", DoubleToString(_Point, _Digits));

Print("Tick Size: ",
DoubleToString(
SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE),
_Digits));

Print("Tick Value: ",
DoubleToString(
SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE),
8));

Print("Contract Size: ",
DoubleToString(
SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE),
4));

Print("Volume Min: ",
DoubleToString(
SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN),
2));

Print("Volume Step: ",
DoubleToString(
SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP),
2));

Print("Volume Max: ",
DoubleToString(
SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX),
2));

Print("==================================================");

return(INIT_SUCCEEDED);
}

void OnTick()
{
MqlTick tick;

if(!SymbolInfoTick(_Symbol, tick))
{
Print("ERROR: SymbolInfoTick() failed.");
return;
}

tick_count++;

long tick_time_msc = (long)tick.time_msc;

long milliseconds_since_previous = 0;

if(previous_tick_msc > 0)
milliseconds_since_previous =
tick_time_msc - previous_tick_msc;

double bid_change = 0.0;
double ask_change = 0.0;

if(previous_bid != 0.0)
bid_change = tick.bid - previous_bid;

if(previous_ask != 0.0)
ask_change = tick.ask - previous_ask;

double midpoint = (tick.bid + tick.ask) / 2.0;
double spread = tick.ask - tick.bid;

// Print first 20 ticks in detail
if(tick_count <= 20)
{
Print(
"TICK #", tick_count,
" | Server=",
TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),

" | TickMS=",
tick_time_msc,

" | Bid=",
DoubleToString(tick.bid, _Digits),

" | Ask=",
DoubleToString(tick.ask, _Digits),

" | Mid=",
DoubleToString(midpoint, _Digits),

" | Spread=",
DoubleToString(spread, _Digits),

" | BidChange=",
DoubleToString(bid_change, _Digits),

" | AskChange=",
DoubleToString(ask_change, _Digits),

" | Δms=",
milliseconds_since_previous,

" | Volume=",
tick.volume,

" | Flags=",
tick.flags
);
}

// One-minute summary
datetime server_time = TimeCurrent();

if(last_summary == 0 ||
server_time - last_summary >= 60)
{
last_summary = server_time;

Print(
"SUMMARY",
" | Server=",
TimeToString(server_time, TIME_DATE | TIME_SECONDS),

" | TickCount=",
tick_count,

" | Bid=",
DoubleToString(tick.bid, _Digits),

" | Ask=",
DoubleToString(tick.ask, _Digits),

" | Mid=",
DoubleToString(midpoint, _Digits),

" | Spread=",
DoubleToString(spread, _Digits),

" | TickMS=",
tick_time_msc
);
}

previous_tick_msc = tick_time_msc;
previous_bid = tick.bid;
previous_ask = tick.ask;
}
