//+------------------------------------------------------------------+
//| VO_SymbolBridge.mq5                                              |
//| Purpose: Export canonical MT5 symbol metadata for Vector Odyssey |
//| No trading functions                                             |
//+------------------------------------------------------------------+
#property strict


void OnStart()
{

   string json =

      "{"

      "\"record_type\":\"symbol\","

      "\"broker_symbol\":\"" + _Symbol + "\","

      "\"description\":\"" +

      StringSubstr(SymbolInfoString(_Symbol, SYMBOL_DESCRIPTION), 0) +

      "\","

      "\"digits\":" + IntegerToString(_Digits) + ","

      "\"point\":" + DoubleToString(_Point, 10) + ","

      "\"tick_size\":" +

         DoubleToString(

            SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE),

            10

         ) + ","

      "\"tick_value\":" +

         DoubleToString(

            SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE),

            10

         ) + ","

      "\"contract_size\":" +

         DoubleToString(

            SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE),

            10

         ) + ","

      "\"source\":\"MT5\""

      "}";



   Print("VO SYMBOL BRIDGE:");

   Print(json);

}