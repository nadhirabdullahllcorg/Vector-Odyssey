//+------------------------------------------------------------------+
//| VO_Json.mqh                                                      |
//|                                                                  |
//| Shared string/JSON primitives for every VO emitter. Nothing in   |
//| here is market data or a record shape — see VO_Records.mqh for   |
//| that. This file exists so the escaping and time-formatting fixes |
//| (defects D1, D2, F2) are written once and used everywhere,       |
//| instead of copy-pasted per bridge the way the three separate     |
//| bridges did before Phase 3.                                      |
//+------------------------------------------------------------------+
#property strict

//+------------------------------------------------------------------+
//| DEFECT D2 FIX.                                                   |
//| The old bridges built JSON by string concatenation with no       |
//| escaping at all. A description containing a quote or a backslash |
//| produced a malformed record. Every string value placed into a    |
//| JSON string by any VO emitter must go through this function.     |
//+------------------------------------------------------------------+
string VO_JsonEscape(const string value)
{
   string out = value;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\r", "\\r");
   StringReplace(out, "\t", "\\t");
   return out;
}

//+------------------------------------------------------------------+
//| Wrap a raw value as an escaped JSON string field, quotes         |
//| included, so callers never quote-and-escape separately and risk  |
//| doing it in the wrong order.                                     |
//+------------------------------------------------------------------+
string VO_JsonString(const string value)
{
   return "\"" + VO_JsonEscape(value) + "\"";
}

//+------------------------------------------------------------------+
//| DEFECT D1 FIX.                                                   |
//| MQL5 TimeToString() emits "yyyy.mm.dd hh:mi:ss" - dots, and a     |
//| space instead of a T. That is not ISO-8601 and the Python         |
//| deserializer correctly rejects it. This does the same job with   |
//| the right punctuation.                                           |
//|                                                                  |
//| F2 FIX.                                                          |
//| This function names its output "server", never "UTC" or "Z",     |
//| because the datetime passed in is a broker server-clock reading, |
//| not a UTC instant. The old bridges computed                      |
//| TimeTradeServer() - TimeGMT() - both of which MQL5 derives from  |
//| THIS PC's clock and timezone, not from the server - and used     |
//| that live, unlogged guess to relabel server time as UTC. VO_Bar  |
//| Bridge and VO_TickBridge did this silently; when the guess was   |
//| wrong, the raw server time was already gone. VO_Bridge.mq5 does  |
//| not convert at all: it emits the server reading honestly, and    |
//| the Time Engine (Python, Phase 6) resolves it to UTC later using |
//| an OBSERVED broker calendar profile instead of a live one.       |
//+------------------------------------------------------------------+
string VO_IsoServerTime(const datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
                        s.year, s.mon, s.day, s.hour, s.min, s.sec);
}

//+------------------------------------------------------------------+
//| For values that ARE genuinely UTC already - specifically         |
//| TimeGMT(), which MQL5 documents as the PC's GMT estimate, not a   |
//| server reading at all. Only ever call this on a TimeGMT() value;  |
//| calling it on server time is exactly the F2 mistake this file     |
//| exists to stop.                                                  |
//+------------------------------------------------------------------+
string VO_IsoUtc(const datetime t)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                        s.year, s.mon, s.day, s.hour, s.min, s.sec);
}
//+------------------------------------------------------------------+
