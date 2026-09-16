//+------------------------------------------------------------------+
//| VO_HistoryDiagnostic.mq5 |
//| Purpose: Inspect historical OHLC data available to VO |
//| No trading functions |
//+------------------------------------------------------------------+
#property strict

void PrintBarData(ENUM_TIMEFRAMES timeframe, int count)
{
MqlRates rates[];

ArraySetAsSeries(rates, true);

int copied = CopyRates(_Symbol, timeframe, 0, count, rates);

Print("--------------------------------------------------");
Print("TIMEFRAME: ", EnumToString(timeframe));
Print("Requested bars: ", count);
Print("Retrieved bars: ", copied);

if(copied <= 0)
{
Print("ERROR: No historical data retrieved.");
return;
}

for(int i = 0; i < MathMin(copied, 10); i++)
{
Print(
"BAR #", i,
" | Time=",
TimeToString(rates[i].time, TIME_DATE | TIME_SECONDS),

" | Open=",
DoubleToString(rates[i].open, _Digits),

" | High=",
DoubleToString(rates[i].high, _Digits),

" | Low=",
DoubleToString(rates[i].low, _Digits),

" | Close=",
DoubleToString(rates[i].close, _Digits),

" | TickVolume=",
rates[i].tick_volume,

" | Spread=",
rates[i].spread,

" | RealVolume=",
rates[i].real_volume
);
}
}

void CheckHistoryDepth(ENUM_TIMEFRAMES timeframe)
{
MqlRates rates[];

ArraySetAsSeries(rates, true);

int copied = CopyRates(_Symbol, timeframe, 0, 100000, rates);

Print(
"HISTORY DEPTH | ",
EnumToString(timeframe),
" | Requested=100000 | Retrieved=",
copied
);

if(copied > 0)
{
Print(
"OLDEST BAR: ",
TimeToString(
rates[copied - 1].time,
TIME_DATE | TIME_SECONDS
)
);

Print(
"NEWEST BAR: ",
TimeToString(
rates[0].time,
TIME_DATE | TIME_SECONDS
)
);
}
}

int OnInit()
{
Print("==================================================");
Print("VO HISTORY DIAGNOSTIC STARTED");
Print("==================================================");

Print("Symbol: ", _Symbol);
Print("Digits: ", _Digits);
Print("Point: ", DoubleToString(_Point, _Digits));

// Test several timeframes
PrintBarData(PERIOD_M1, 10);
PrintBarData(PERIOD_M5, 10);
PrintBarData(PERIOD_M15, 10);
PrintBarData(PERIOD_H1, 10);
PrintBarData(PERIOD_H4, 10);
PrintBarData(PERIOD_D1, 10);

// Test available history
CheckHistoryDepth(PERIOD_M1);
CheckHistoryDepth(PERIOD_M5);
CheckHistoryDepth(PERIOD_H1);
CheckHistoryDepth(PERIOD_D1);

Print("==================================================");
Print("VO HISTORY DIAGNOSTIC COMPLETE");
Print("==================================================");

return(INIT_SUCCEEDED);
}

void OnTick()
{
// No action required.
}
