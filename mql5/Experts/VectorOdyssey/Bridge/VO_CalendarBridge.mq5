//+------------------------------------------------------------------+
//| VO_CalendarBridge.mq5                                            |
//|                                                                  |
//| The MQL5-side data source for vo.compliance.news_gate (built     |
//| 2026-09-19), chosen by the user over a third-party calendar API  |
//| or a manually maintained event list: the Python MetaTrader5      |
//| package has no native calendar access at all (confirmed directly |
//| against MQL5's own docs -- see vo.interfaces.economic_events's   |
//| own module docstring), but MQL5 itself does                      |
//| (CalendarValueHistory/CalendarEventById/CalendarCountryById).    |
//| This EA reads that native calendar and writes it to a file the   |
//| Python side reads (vo.market.economic_calendar_ingestion.        |
//| read_calendar_snapshot) -- the same file-bridge pattern           |
//| VO_Bridge.mq5 already uses for price data, applied to a           |
//| completely different MT5 API surface.                            |
//|                                                                  |
//| SNAPSHOT, NOT APPEND-ONLY -- a deliberate divergence from         |
//| VO_Bridge.mq5's own append-only tick/bar log. Economic events get |
//| added, revised (actual values populate after release), and drop  |
//| out of the lookahead window over time; an append-only log would   |
//| accumulate stale duplicate copies of the same event forever. This |
//| EA truncates and rewrites its output file whole on every refresh  |
//| (FileOpen with FILE_WRITE alone, no FILE_READ, truncates -- see   |
//| VO_Transport.mqh's own comment on the opposite convention for     |
//| why that combination means "append"). The Python-side reader is   |
//| therefore a plain batch read of current contents, not a tail.     |
//|                                                                  |
//| TWO THINGS THIS BUILD COULD NOT VERIFY LIVE (no Windows/MetaEditor |
//| in this development environment -- same standing caveat as        |
//| vo.core.mt5's own "RETCODE CLASSIFICATION" note):                 |
//|                                                                  |
//|   1. STRUCT FIELD NAMES. MqlCalendarEvent/MqlCalendarValue/        |
//|      MqlCalendarCountry's field names below are transcribed from  |
//|      MQL5's public reference docs, not checked against a          |
//|      compiler. Expect to fix a few on first compile -- use        |
//|      MetaEditor's autocomplete/Ctrl+click-to-definition on these   |
//|      three struct types rather than assuming a compile error      |
//|      here means the whole approach is wrong.                      |
//|                                                                  |
//|   2. TIMEZONE. Whether CalendarValueHistory's `time` field is      |
//|      UTC or some other reference was not confirmed against live   |
//|      documentation access. InpCalendarUtcOffsetMinutes exists so  |
//|      the ONE calibration this needs -- compare a known upcoming   |
//|      event's real-world release time (e.g. the next NFP, published|
//|      well in advance) against what this EA's Experts-log line      |
//|      prints for it on first live run -- is a config change, not   |
//|      a code change. DO NOT TRUST THE NEWS GATE'S TIMING until this |
//|      is calibrated at least once against a real, known event.     |
//|                                                                  |
//| actual_value/forecast_value/previous_value are deliberately        |
//| ALWAYS emitted null in v1 -- MQL5's sentinel convention for "this  |
//| value has not been released/populated yet" on MqlCalendarValue's  |
//| long fields was not verified live either, and guessing wrong here |
//| would silently fabricate a number rather than honestly omit one.  |
//| The news gate itself never reads these fields; only a future      |
//| research consumer would, and this is flagged there rather than    |
//| guessed at (see vo.interfaces.economic_events's own docstring).   |
//|                                                                  |
//| No trading functions. This EA observes; it does not decide.       |
//+------------------------------------------------------------------+
#property strict

#include <VectorOdyssey/VO_Json.mqh>

input int    InpRefreshMinutes            = 5;    // how often to rewrite the snapshot
input int    InpLookbackHours             = 2;    // include events released this recently
input int    InpLookaheadDays             = 14;   // include events scheduled this far ahead
input ENUM_CALENDAR_EVENT_IMPORTANCE InpMinImportance = CALENDAR_IMPORTANCE_HIGH;
input string InpCurrencyFilter            = "USD"; // comma-separated; empty = every currency
input int    InpCalendarUtcOffsetMinutes  = 0;     // CALIBRATE against a known event -- see header
input string InpOutputSubdir              = "VectorOdyssey"; // under MQL5\Files\
input string InpOutputFilename            = "calendar.jsonl";

string g_currencies[];

//+------------------------------------------------------------------+
void ParseCurrencyFilter(const string filter, string &out[])
{
   string trimmed = filter;
   StringTrimLeft(trimmed);
   StringTrimRight(trimmed);

   if(trimmed == "")
   {
      ArrayResize(out, 0);
      return;
   }

   int n = StringSplit(trimmed, ',', out);
   for(int i = 0; i < n; i++)
   {
      StringTrimLeft(out[i]);
      StringTrimRight(out[i]);
   }
}

//+------------------------------------------------------------------+
bool CurrencyInScope(const string currency)
{
   if(ArraySize(g_currencies) == 0)
      return true; // empty filter = every currency counts

   for(int i = 0; i < ArraySize(g_currencies); i++)
      if(g_currencies[i] == currency)
         return true;

   return false;
}

//+------------------------------------------------------------------+
string ImportanceToString(const ENUM_CALENDAR_EVENT_IMPORTANCE imp)
{
   switch(imp)
   {
      case CALENDAR_IMPORTANCE_HIGH:     return "HIGH";
      case CALENDAR_IMPORTANCE_MODERATE: return "MODERATE";
      case CALENDAR_IMPORTANCE_LOW:      return "LOW";
      case CALENDAR_IMPORTANCE_NONE:     return "NONE";
      default:
         Print("VO CALENDAR BRIDGE: unrecognized importance value ", (int)imp,
               " -- treating as NONE rather than guessing");
         return "NONE";
   }
}

//+------------------------------------------------------------------+
//| See this file's own header, caveat 2. Emits a string LABELED utc |
//| -- honest only once InpCalendarUtcOffsetMinutes is actually       |
//| calibrated against a known real event.                            |
//+------------------------------------------------------------------+
string CalendarTimeToUtcIso(const datetime raw_time)
{
   datetime shifted = raw_time + InpCalendarUtcOffsetMinutes * 60;
   MqlDateTime s;
   TimeToStruct(shifted, s);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",
                        s.year, s.mon, s.day, s.hour, s.min, s.sec);
}

//+------------------------------------------------------------------+
int OnInit()
{
   ParseCurrencyFilter(InpCurrencyFilter, g_currencies);

   Print("VO CALENDAR BRIDGE STARTED. currencies=", InpCurrencyFilter,
         " min_importance=", EnumToString(InpMinImportance),
         " utc_offset_minutes=", InpCalendarUtcOffsetMinutes,
         " -- CALIBRATE utc_offset_minutes against a known event before "
         "trusting the news gate's timing (see this file's own header).");

   EventSetTimer(InpRefreshMinutes * 60);
   RefreshSnapshot(); // write one immediately -- don't wait for the first timer tick

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnTimer()
{
   RefreshSnapshot();
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
}

//+------------------------------------------------------------------+
void RefreshSnapshot()
{
   datetime from = TimeCurrent() - InpLookbackHours * 3600;
   datetime to   = TimeCurrent() + InpLookaheadDays * 24 * 3600;

   MqlCalendarValue values[];
   int copied = CalendarValueHistory(values, from, to, NULL, NULL);

   if(copied < 0)
   {
      Print("VO CALENDAR BRIDGE: CalendarValueHistory() failed, error=", GetLastError(),
            " -- is the Community-tab calendar toggle enabled on this terminal? "
            "(Settings > Community, or Options > Community in older builds.)");
      return;
   }

   string dir = InpOutputSubdir;
   if(!FolderCreate(dir))
   {
      int err = GetLastError();
      // ERR_DIRECTORY_ALREADY_EXISTS-shaped cases are fine; anything else is
      // worth knowing about, but is not fatal -- Print still works.
      if(err != 0 && err != 5019)
         Print("VO CALENDAR BRIDGE: FolderCreate(", dir, ") reported error ", err);
   }

   string path = dir + "\\" + InpOutputFilename;

   // FILE_WRITE alone (no FILE_READ) truncates the existing file -- this is
   // a snapshot rewritten whole each refresh, not an append-only log. See
   // this file's own header comment for why that is deliberate here,
   // opposite to VO_Transport.mqh's own append convention.
   int handle = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("VO CALENDAR BRIDGE: FileOpen(", path, ") failed, error=", GetLastError());
      return;
   }

   int written = 0;
   for(int i = 0; i < copied; i++)
   {
      MqlCalendarEvent event;
      if(!CalendarEventById(values[i].event_id, event))
      {
         Print("VO CALENDAR BRIDGE: CalendarEventById(", values[i].event_id,
               ") failed, error=", GetLastError());
         continue;
      }

      if(event.importance < InpMinImportance)
         continue;

      MqlCalendarCountry country;
      string currency = "";
      if(CalendarCountryById(event.country_id, country))
         currency = country.currency;

      if(!CurrencyInScope(currency))
         continue;

      string line =
         "{\"record_type\":\"economic_event\","
         "\"schema_version\":1,"
         "\"event_id\":\"" + (string)values[i].event_id + "\","
         "\"name\":" + VO_JsonString(event.name) + ","
         "\"currency\":" + VO_JsonString(currency) + ","
         "\"importance\":" + VO_JsonString(ImportanceToString(event.importance)) + ","
         "\"scheduled_at_utc\":\"" + CalendarTimeToUtcIso(values[i].time) + "\","
         "\"duration_minutes\":0.0,"
         "\"actual_value\":null,"
         "\"forecast_value\":null,"
         "\"previous_value\":null,"
         "\"revision_value\":null"
         "}";

      FileWriteString(handle, line + "\r\n");
      written++;
   }

   FileClose(handle);
   Print("VO CALENDAR BRIDGE: wrote ", written, " of ", copied,
         " calendar values (post-filter) to ", path);
}
//+------------------------------------------------------------------+
