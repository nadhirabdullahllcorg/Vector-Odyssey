//+------------------------------------------------------------------+
//| VO_TickBridge.mq5                                                |
//| Purpose: Export canonical MT5 tick records for Vector Odyssey    |
//| No trading functions                                             |
//+------------------------------------------------------------------+
#property strict



ulong tick_count = 0;



int OnInit()

{

   Print("VO TICK BRIDGE STARTED");

   return(INIT_SUCCEEDED);

}



void OnTick()

{

   MqlTick tick;



   if(!SymbolInfoTick(_Symbol, tick))

   {

      Print("VO TICK BRIDGE ERROR: SymbolInfoTick() failed.");
      return;

   }

long server_offset_ms =

   (long)(TimeTradeServer() - TimeGMT()) * 1000;



long utc_time_msc =

   tick.time_msc - server_offset_ms;



Print(

   "VO UTC TEST:",

   " raw_server_time=",

   TimeToString(tick.time, TIME_DATE | TIME_SECONDS),

   " raw_time_msc=",

   (string)tick.time_msc,

   " server_offset_ms=",

   (string)server_offset_ms,

   " utc_time_msc=",

   (string)utc_time_msc

);


   tick_count++;



   string last_value = "null";



   if(tick.last > 0.0)

      last_value = DoubleToString(tick.last, _Digits);



   string json =

      "{"

      "\"record_type\":\"tick\","

      "\"timestamp_ms\":" +

   (string)utc_time_msc + ","

      "\"bid\":" +

         DoubleToString(tick.bid, _Digits) + ","

      "\"ask\":" +

         DoubleToString(tick.ask, _Digits) + ","

      "\"last\":" +

         last_value + ","

      "\"volume\":" +

         DoubleToString(tick.volume, 8) + ","

      "\"source\":\"MT5:" +

         _Symbol +

         "\""

      "}";



   Print("VO TICK BRIDGE #", tick_count, ":");

   Print(json);

}