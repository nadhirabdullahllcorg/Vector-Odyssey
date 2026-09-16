# Golden corpus — captured output only

Files in this directory are **captured verbatim from a running MT5 terminal**.
Nothing here is hand-written, hand-corrected, or reformatted. Ever.

## Why this folder exists separately

Every file in `tests/fixtures/` is synthetic — written by hand to look like what
the bridge produces. They were written in correct ISO-8601. The pre-Phase-3
bar bridge did not emit correct ISO-8601 (see
`tests/unit/test_bridge_contract_v1_history.py`). So 40 tests passed for weeks
over a path that had never carried a real message.

Keeping captured output in its own directory is the guard against that
recurring. If you find yourself editing a file in here to make a test pass, the
test is telling you something true and the edit is the mistake.

## How to capture

Since Phase 3, VO_Bridge.mq5 writes its own durable JSONL files under the
terminal's `MQL5\Files\VectorOdyssey\` folder (`<symbol>_ticks.jsonl`,
`<symbol>_bars.jsonl`, `<symbol>_meta.jsonl` — the latter holding both the
symbol and source_capabilities records). That is the preferred capture
source now: copy the file directly, no Experts-tab extraction needed.

1. Attach VO_Bridge.mq5 to a chart and let it run long enough to emit the
   record types you need — ticks, at least one completed bar, and the
   startup symbol / source_capabilities records.
2. Find the file under the terminal's data folder (File → Open Data Folder
   → `MQL5\Files\VectorOdyssey\`) and copy the `.jsonl` file(s) you need
   straight into this directory.

If you are still capturing from an older single-purpose bridge or from the
Experts tab (e.g. VO_BrokerTimeProbe.mq5, which only ever prints), extract
JSON lines from a saved log the old way instead:

   ```powershell
   Select-String -Path .\experts.log -Pattern '^\{.*\}$' |
       ForEach-Object { $_.Line } |
       Set-Content -Encoding utf8 .\tests\fixtures\golden\<name>.jsonl
   ```

3. Name the file for what it contains and where it came from:

   ```
   <symbol>_<timeframe>_<yyyymmdd>.jsonl        us100n_m1_20260910.jsonl
   ```

4. Commit it. Captured data is small, and it is the only evidence of what the
   broker actually sent on that day.

## Provenance

Record what produced each file, so a surprising value can be traced later.

| File | Symbol | Broker / server | Bridge version | Captured (server time) |
|---|---|---|---|---|
| `us100n_ticks_20260915.jsonl` | US100.n | 1xTrade-Server | VO_Bridge.mq5 (commit `48923fb`) | 2026-09-15, startup CopyTicksRange backfill, 60 min window, 8523 ticks |
| `us100n_m1_bars_20260915.jsonl` | US100.n | 1xTrade-Server | VO_Bridge.mq5 (commit `48923fb`) | 2026-09-15/16, startup CopyRates backfill, 500 bars, PERIOD_CURRENT |
| `us100n_meta_20260915.jsonl` | US100.n | 1xTrade-Server | VO_Bridge.mq5 (commit `48923fb`) | 2026-09-15, one symbol record + one source_capabilities record from OnInit |

**First real evidence on `real_volume`:** `source_capabilities` for US100.n on 1xTrade-Server reports `real_volume_available: false`, `tick_level_available: true` — observed by VO_Bridge.mq5's startup scan, not assumed. Every bar in the capture reports `real_volume: 0`, consistent with that finding.

Verified against `vo.market.schema.validate_wire_dict` and `vo.market.deserialization.json_to_record` (the real code, not a read-through): 9,026 records, 0 schema problems, 0 deserialize failures, 0 seq gaps/duplicates in either stream, 0 OHLC geometry violations, 0 crossed-market ticks.

## What consumes this

`tests/unit/test_golden_corpus.py` parses every `.jsonl` here through the
canonical deserializer and reports on conformance. With the directory empty it
skips, and says so loudly rather than passing silently — an empty corpus is a
gap, not a success.

Derived samples — reconstructed from the `.mq5` source rather than captured —
live in `tests/unit/test_bridge_contract.py` and are labelled as such. They
prove the *format* contract. This corpus proves the *values*, and catches the
broker quirks no amount of reading the source will predict.
