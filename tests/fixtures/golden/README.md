# Golden corpus — captured output only

Files in this directory are **captured verbatim from a running MT5 terminal**.
Nothing here is hand-written, hand-corrected, or reformatted. Ever.

## Why this folder exists separately

Every file in `tests/fixtures/` is synthetic — written by hand to look like what
the bridge produces. They were written in correct ISO-8601. The bar bridge does
not emit correct ISO-8601. So 40 tests passed for weeks over a path that had
never carried a real message.

Keeping captured output in its own directory is the guard against that
recurring. If you find yourself editing a file in here to make a test pass, the
test is telling you something true and the edit is the mistake.

## How to capture

1. Attach the bridge to a chart in MetaTrader 5 and let it run long enough to
   emit the record types you need — a tick, at least one completed bar, and the
   symbol record on init.
2. Open the **Experts** tab, right-click, *Save As* → a `.log` file.
3. Extract the JSON lines. Each emitted record is a single line beginning `{`
   and ending `}`:

   ```powershell
   Select-String -Path .\experts.log -Pattern '^\{.*\}$' |
       ForEach-Object { $_.Line } |
       Set-Content -Encoding utf8 .\tests\fixtures\golden\<name>.jsonl
   ```

4. Name the file for what it contains and where it came from:

   ```
   <symbol>_<timeframe>_<yyyymmdd>.jsonl        us100n_m1_20260910.jsonl
   ```

5. Commit it. Captured data is small, and it is the only evidence of what the
   broker actually sent on that day.

## Provenance

Record what produced each file, so a surprising value can be traced later.

| File | Symbol | Broker / server | Bridge version | Captured (UTC) |
|---|---|---|---|---|
| _(none yet)_ | | | | |

## What consumes this

`tests/unit/test_golden_corpus.py` parses every `.jsonl` here through the
canonical deserializer and reports on conformance. With the directory empty it
skips, and says so loudly rather than passing silently — an empty corpus is a
gap, not a success.

Derived samples — reconstructed from the `.mq5` source rather than captured —
live in `tests/unit/test_bridge_contract.py` and are labelled as such. They
prove the *format* contract. This corpus proves the *values*, and catches the
broker quirks no amount of reading the source will predict.
