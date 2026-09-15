//+------------------------------------------------------------------+
//| VO_Records.mqh                                                   |
//|                                                                  |
//| Builds one wire-schema-v2 JSON line per record type. All string  |
//| escaping happens inside these functions, not at call sites, so a |
//| caller cannot forget it (that omission is exactly defect D2).    |
//| This mirrors python/vo/market/schema.py's SCHEMA_V2 field-by-    |
//| field: if a field is added or renamed there, it must be added or |
//| renamed here in the same change, or the contract test in         |
//| tests/unit/test_bridge_contract.py will fail on the next capture.|
//+------------------------------------------------------------------+
#property strict

#include <VectorOdyssey/VO_Json.mqh>

//+------------------------------------------------------------------+
string VO_BuildSourceCapabilitiesRecord(
   const string platform,
   const string broker_server,
   const string broker_symbol,
   const bool   real_volume_available,
   const bool   tick_level_available)
{
   return
      "{\"record_type\":\"source_capabilities\","
      "\"schema_version\":2,"
      "\"platform\":" + VO_JsonString(platform) + ","
      "\"broker_server\":" + VO_JsonString(broker_server) + ","
      "\"broker_symbol\":" + VO_JsonString(broker_symbol) + ","
      "\"real_volume_available\":" + (real_volume_available ? "true" : "false") + ","
      "\"tick_level_available\":" + (tick_level_available ? "true" : "false") +
      "}";
}

//+------------------------------------------------------------------+
string VO_BuildTickRecord(
   const long   timestamp_server_ms,
   const double bid,
   const double ask,
   const double last,          // pass <= 0 for "no last price"
   const int    digits,
   const double volume,
   const double volume_real,
   const uint   flags,
   const long   seq,
   const string source_feed,   // "live" | "history"
   const string platform,
   const string broker_server,
   const string broker_symbol)
{
   string last_field = (last > 0.0) ? DoubleToString(last, digits) : "null";

   return
      "{\"record_type\":\"tick\","
      "\"schema_version\":2,"
      "\"timestamp_server_ms\":" + (string)timestamp_server_ms + ","
      "\"bid\":" + DoubleToString(bid, digits) + ","
      "\"ask\":" + DoubleToString(ask, digits) + ","
      "\"last\":" + last_field + ","
      "\"volume\":" + DoubleToString(volume, 8) + ","
      "\"volume_real\":" + DoubleToString(volume_real, 8) + ","
      "\"flags\":" + (string)flags + ","
      "\"seq\":" + (string)seq + ","
      "\"source_feed\":" + VO_JsonString(source_feed) + ","
      "\"platform\":" + VO_JsonString(platform) + ","
      "\"broker_server\":" + VO_JsonString(broker_server) + ","
      "\"broker_symbol\":" + VO_JsonString(broker_symbol) +
      "}";
}

//+------------------------------------------------------------------+
string VO_BuildBarRecord(
   const datetime server_time,
   const double   open,
   const double   high,
   const double   low,
   const double   close,
   const int      digits,
   const long     tick_volume,
   const long     real_volume,
   const int      spread,
   const string   timeframe,       // EnumToString(ENUM_TIMEFRAMES)
   const long     seq,
   const string   source_feed,     // "live" | "history"
   const string   platform,
   const string   broker_server,
   const string   broker_symbol)
{
   return
      "{\"record_type\":\"bar\","
      "\"schema_version\":2,"
      "\"timestamp\":" + VO_JsonString(VO_IsoServerTime(server_time)) + ","
      "\"open\":" + DoubleToString(open, digits) + ","
      "\"high\":" + DoubleToString(high, digits) + ","
      "\"low\":" + DoubleToString(low, digits) + ","
      "\"close\":" + DoubleToString(close, digits) + ","
      "\"tick_volume\":" + (string)tick_volume + ","
      "\"real_volume\":" + (string)real_volume + ","
      "\"spread\":" + (string)spread + ","
      "\"timeframe\":" + VO_JsonString(timeframe) + ","
      "\"seq\":" + (string)seq + ","
      "\"source_feed\":" + VO_JsonString(source_feed) + ","
      "\"platform\":" + VO_JsonString(platform) + ","
      "\"broker_server\":" + VO_JsonString(broker_server) + ","
      "\"broker_symbol\":" + VO_JsonString(broker_symbol) +
      "}";
}

//+------------------------------------------------------------------+
string VO_BuildSymbolRecord(
   const string broker_symbol,
   const string description,
   const int    digits,
   const double point,
   const double tick_size,
   const double tick_value,
   const double contract_size,
   const string platform,
   const string broker_server)
{
   return
      "{\"record_type\":\"symbol\","
      "\"schema_version\":2,"
      "\"broker_symbol\":" + VO_JsonString(broker_symbol) + ","
      "\"description\":" + VO_JsonString(description) + ","
      "\"digits\":" + IntegerToString(digits) + ","
      "\"point\":" + DoubleToString(point, 10) + ","
      "\"tick_size\":" + DoubleToString(tick_size, 10) + ","
      "\"tick_value\":" + DoubleToString(tick_value, 10) + ","
      "\"contract_size\":" + DoubleToString(contract_size, 10) + ","
      "\"platform\":" + VO_JsonString(platform) + ","
      "\"broker_server\":" + VO_JsonString(broker_server) +
      "}";
}
//+------------------------------------------------------------------+
