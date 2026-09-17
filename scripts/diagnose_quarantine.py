#!/usr/bin/env python3
"""
Explain VO_EA's `quarantined=N` number -- a throwaway diagnostic.

    python scripts/diagnose_quarantine.py [config/settings/vo_ea.yaml]

`run_vo_ea.py` reports how MANY records the ObservationPipeline
quarantined but not WHY (the reason is stored on each
QuarantinedInboundRecord and never logged). This reads the same three
wire files through the *same* pipeline `build_runtime` wires up, then
prints the quarantine reasons grouped and counted, with a couple of
example raw records per reason and a record_type breakdown -- enough to
tell "thousands of crossed-quote ticks" from "duplicate bars" from a
real bug in seconds. Reads the files whole (not tailing); sends nothing;
touches no terminal. Delete whenever.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from vo.core.config import load_ea_config  # noqa: E402
from vo.market.deserialization import json_to_record  # noqa: E402
from vo.telemetry.ea_runtime import build_runtime  # noqa: E402


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        print(f"  (missing: {path.name})")
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/settings/vo_ea.yaml"
    config = load_ea_config(config_path)
    runtime = build_runtime(config)
    pipeline = runtime.pipeline

    print(f"wire dir: {config.wire.dir}")
    print(f"broker_symbol: {config.broker_symbol}\n")

    seen = 0
    unparsed = 0
    per_type_seen: Counter[str] = Counter()
    for wire_path in (
        config.bar_wire_path(),
        config.tick_wire_path(),
        config.meta_wire_path(),
    ):
        for line in _read_lines(wire_path):
            seen += 1
            try:
                record = json_to_record(line)
            except Exception:  # noqa: BLE001 -- a malformed/partial JSON line
                unparsed += 1
                continue
            per_type_seen[type(record).__name__] += 1
            pipeline.ingest(record)

    q = pipeline.quarantined
    print(f"records seen:       {seen}")
    print(f"unparsed (JSON):    {unparsed}")
    print(f"parsed by type:     {dict(per_type_seen)}")
    print(f"bars in sequence:   {len(pipeline.sequence)}")
    print(f"QUARANTINED:        {len(q)}\n")

    if not q:
        print("Nothing quarantined -- clean.")
        return

    by_type: Counter[str] = Counter(type(item.record).__name__ for item in q)
    print(f"quarantined by record type: {dict(by_type)}\n")

    reasons: Counter[str] = Counter(item.reason for item in q)
    print("quarantine reasons (most common first):")
    for reason, count in reasons.most_common(12):
        print(f"  {count:>6}  {reason}")

    # A couple of concrete offenders for the top reason, so the fix is obvious.
    top_reason = reasons.most_common(1)[0][0]
    print(f"\nexamples for: {top_reason!r}")
    shown = 0
    for item in q:
        if item.reason == top_reason:
            print(f"  {item.record}")
            shown += 1
            if shown >= 3:
                break


if __name__ == "__main__":
    main()
