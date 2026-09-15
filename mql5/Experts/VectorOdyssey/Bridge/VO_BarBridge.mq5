#property strict



input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M1;



datetime last_emitted_bar_time = 0;





int OnInit()

{

   Print("VO BAR BRIDGE STARTED");

   Print("Symbol: ", _Symbol);

   Print("Timeframe: ", EnumToString(InpTimeframe));



   return(INIT_SUCCEEDED);

}





void OnTick()

{

   MqlRates rates[];



   ArraySetAsSeries(rates, true);



   // Shift 1 = most recently completed bar.

   int copied = CopyRates(_Symbol, InpTimeframe, 1, 1, rates);



   if(copied != 1)

   {

      Print("VO BAR BRIDGE ERROR: CopyRates() failed. Error=",

            GetLastError());

      return;

   }



   datetime server_bar_time = rates[0].time;



   // Do not emit the same completed bar more than once.

   if(server_bar_time == last_emitted_bar_time)

      return;





   // Convert broker/server time to UTC.

   //

   // This is the current live normalization mechanism.

   // A dedicated Time Engine will later own historical/session

   // timezone normalization rather than leaving it in the bridge.

   long server_offset_seconds =

      (long)(TimeTradeServer() - TimeGMT());



   datetime utc_bar_time =
     (datetime)(server_bar_time - server_offset_seconds);


   string timestamp =

      TimeToString(

         utc_bar_time,

         TIME_DATE | TIME_SECONDS

      );





   string json =

      "{"

      "\"record_type\":\"bar\","

      "\"timestamp\":\"" +

         timestamp +

         "Z" +

         "\","

      "\"open\":" +

         DoubleToString(rates[0].open, _Digits) +

         ","

      "\"high\":" +

         DoubleToString(rates[0].high, _Digits) +

         ","

      "\"low\":" +

         DoubleToString(rates[0].low, _Digits) +

         ","

      "\"close\":" +

         DoubleToString(rates[0].close, _Digits) +

         ","

      "\"tick_volume\":" +

         (string)rates[0].tick_volume +

         ","

      "\"real_volume\":" +

         (string)rates[0].real_volume +

         ","

      "\"timeframe\":\"" +

         EnumToString(InpTimeframe) +

         "\","

      "\"source\":\"MT5:" +

         _Symbol +

         "\""

      "}";





   Print(json);



   last_emitted_bar_time = server_bar_time;

}