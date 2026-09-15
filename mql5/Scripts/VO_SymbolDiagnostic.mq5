#property script_show_inputs

void OnStart()
{
    string symbol = _Symbol;

    Print("=== VO SYMBOL DIAGNOSTIC ===");
    Print("Symbol: ", symbol);
    Print("Description: ", SymbolInfoString(symbol, SYMBOL_DESCRIPTION));
    Print("Digits: ", SymbolInfoInteger(symbol, SYMBOL_DIGITS));
    Print("Point: ", SymbolInfoDouble(symbol, SYMBOL_POINT));
    Print("Tick Size: ", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE));
    Print("Tick Value: ", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE));
    Print("Contract Size: ", SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE));
    Print("=== VO SYMBOL DIAGNOSTIC COMPLETE ===");
}