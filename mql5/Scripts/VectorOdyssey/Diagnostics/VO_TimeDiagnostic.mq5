//+------------------------------------------------------------------+
//| VO_TimeDiagnostic.mq5 |
//| Purpose: Determine broker/server time vs UTC vs PC local time |
//| No trading functions |
//+------------------------------------------------------------------+
#property strict

void OnInit()
{
Print("========================================");
Print("VO TIME DIAGNOSTIC STARTED");
Print("========================================");

Print("TimeCurrent(): ", TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS));
Print("TimeTradeServer(): ", TimeToString(TimeTradeServer(), TIME_DATE | TIME_SECONDS));
Print("TimeGMT(): ", TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS));
Print("TimeLocal(): ", TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS));

Print("========================================");
}

void OnTick()
{
static datetime last_print = 0;

datetime now = TimeCurrent();

// Print once per minute
if(now - last_print >= 60)
{
last_print = now;

Print("SERVER: ", TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
" | TRADE SERVER: ", TimeToString(TimeTradeServer(), TIME_DATE | TIME_SECONDS),
" | GMT: ", TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS),
" | LOCAL: ", TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS));
}
}
