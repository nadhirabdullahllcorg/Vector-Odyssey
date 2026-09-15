//+------------------------------------------------------------------+
//| VO_Transport.mqh                                                 |
//|                                                                  |
//| Durable output for VO records. Before Phase 3 the only transport |
//| was Print() to the Experts tab, which the user then had to       |
//| "Save As" by hand to capture anything - workable for a one-off   |
//| probe, not for a bridge meant to run continuously. This writes   |
//| append-only JSONL to MQL5/Files/<subdir>/, which lands under the |
//| terminal's sandboxed data folder and is what a capture script    |
//| should read from for tests/fixtures/golden/.                    |
//|                                                                  |
//| Still Prints every line too - that live view is worth keeping    |
//| for development and does not conflict with the file transport.   |
//+------------------------------------------------------------------+
#property strict

int VO_OpenSink(const string subdir, const string base_filename)
{
   string dir = subdir;
   if(!FolderCreate(dir))
   {
      int err = GetLastError();
      // ERR_DIRECTORY_ALREADY_EXISTS-shaped cases are fine; anything else
      // is worth knowing about, but is not fatal - Print still works.
      if(err != 0 && err != 5019)
         Print("VO_Transport: FolderCreate(", dir, ") reported error ", err);
   }

   string path = dir + "\\" + base_filename;

   // FILE_READ is required alongside FILE_WRITE to open an existing file
   // for appending rather than truncating it; FILE_SHARE_READ lets a
   // capture script read the file while the EA still has it open.
   int handle = FileOpen(
      path,
      FILE_READ | FILE_WRITE | FILE_TXT | FILE_SHARE_READ | FILE_ANSI
   );

   if(handle == INVALID_HANDLE)
   {
      Print("VO_Transport: FileOpen(", path, ") failed, error=", GetLastError());
      return INVALID_HANDLE;
   }

   FileSeek(handle, 0, SEEK_END);
   return handle;
}

void VO_WriteLine(const int handle, const string line)
{
   if(handle == INVALID_HANDLE)
      return;

   FileWriteString(handle, line + "\r\n");
   FileFlush(handle);
}

void VO_CloseSink(int &handle)
{
   if(handle != INVALID_HANDLE)
   {
      FileClose(handle);
      handle = INVALID_HANDLE;
   }
}
//+------------------------------------------------------------------+
