"""
The wire contract between MQL5 and Python — the single source of truth.

Before this module existed the schema lived in two places: hand-concatenated
strings inside the .mq5 emitters, and field tuples inside the deserializer.
Nothing reconciled them, and they had already drifted. This file is what both
sides are now checked against.

Schema v1 describes the format *as the bridges emit it today*, including the
parts that are wrong. Freezing reality first is deliberate: you cannot prove a
contract is broken against a contract that has been quietly corrected.

KNOWN DEFECTS IN v1 — see tests/unit/test_bridge_contract.py, fixed in Phase 3:

    D1  BAR timestamps use MQL5 TimeToString(), which emits
        "2026.09.10 18:49:00" — dots and a space. ISO-8601 requires
        "2026-09-10T18:49:00". The Python deserializer rejects the former.

    D2  String fields are concatenated into JSON without escaping. A symbol
        description containing a quote or a backslash produces a malformed
        record.

SCHEMA V2 fixes D1 and D2, plus F2 from the original audit (broker time was
silently relabeled UTC using a live, PC-derived offset). See the SCHEMA_V2
section below for what changed and why. v1 stays exactly as it was: a fixed
record of what the pre-Phase-3 bridges actually emitted, still exercised by
tests/unit/test_bridge_contract_v1_history.py so that history is not lost
when the code that produced it is replaced.

PLATFORM SCOPE.
v1 is MT5-shaped because MT5 is the only source so far, but MT5 is the first
platform, not the only intended one. Two things in v1 are therefore known to be
provisional:

    `source` fuses platform, server and symbol into one string
    ("MT5:US100.n"). v2 decomposes it, because a provenance string that has to
    be parsed is not provenance.

    `timeframe` carries MT5's own spelling. The codec maps it to a canonical
    form in Phase 5, so nothing above the codec learns MT5's vocabulary.

Anything a second platform would spell differently belongs behind the codec.
Anything a second platform might not provide at all belongs in source
capabilities, not in a per-record sentinel value.

This module contains no market interpretation and no trading logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

WIRE_SCHEMA_VERSION = 1

# What ISO-8601 means here, exactly. Both sides are held to this.
TIMESTAMP_ISO_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$"
)
TIMESTAMP_ISO_EXAMPLE = "2026-09-10T18:49:00Z"

# No Z: this is a wall-clock reading with no timezone claim, not a UTC instant.
TIMESTAMP_ISO_SERVER_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?$"
)
TIMESTAMP_ISO_SERVER_EXAMPLE = "2026-09-10T18:49:00"

# Characters that must be escaped before a value is placed inside a JSON string.
JSON_STRING_HAZARDS = ('"', "\\", "\n", "\r", "\t")


class FieldKind(Enum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    NULLABLE_FLOAT = "nullable_float"
    BOOL = "bool"
    TIMESTAMP_ISO_UTC = "timestamp_iso_utc"
    TIMESTAMP_EPOCH_MS = "timestamp_epoch_ms"

    # v2 only. Broker server wall-clock, no timezone claim attached. Bar and
    # tick times are NOT converted to UTC at the bridge (that was defect F2 in
    # the original audit: TimeTradeServer() - TimeGMT() is a client-PC-derived
    # estimate, not an authority, and baking it in silently threw the raw
    # server time away). True UTC conversion, with full provenance, is the
    # Time Engine's job (Phase 6), using an observed broker calendar profile
    # (vo.time.probe / config/settings/brokers.yaml) rather than a live guess.
    TIMESTAMP_ISO_SERVER = "timestamp_iso_server"
    TIMESTAMP_EPOCH_SERVER_MS = "timestamp_epoch_server_ms"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: FieldKind
    note: str | None = None


@dataclass(frozen=True)
class RecordSpec:
    """One record type on the wire."""

    record_type: str
    fields: tuple[FieldSpec, ...]
    note: str | None = None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def wire_keys(self) -> frozenset[str]:
        """Every key a valid record carries, discriminator included."""
        return frozenset({"record_type", *self.field_names})

    def field(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


TICK_V1 = RecordSpec(
    record_type="tick",
    fields=(
        FieldSpec("timestamp_ms", FieldKind.TIMESTAMP_EPOCH_MS,
                  "UTC epoch milliseconds"),
        FieldSpec("bid", FieldKind.FLOAT),
        FieldSpec("ask", FieldKind.FLOAT),
        FieldSpec("last", FieldKind.NULLABLE_FLOAT,
                  "null when the broker reports no last price"),
        FieldSpec("volume", FieldKind.FLOAT),
        FieldSpec("source", FieldKind.STRING, 'e.g. "MT5:US100.n"'),
    ),
)

BAR_V1 = RecordSpec(
    record_type="bar",
    fields=(
        FieldSpec("timestamp", FieldKind.TIMESTAMP_ISO_UTC,
                  "bar open time. DEFECT D1: the bridge emits dotted, non-ISO"),
        FieldSpec("open", FieldKind.FLOAT),
        FieldSpec("high", FieldKind.FLOAT),
        FieldSpec("low", FieldKind.FLOAT),
        FieldSpec("close", FieldKind.FLOAT),
        FieldSpec("tick_volume", FieldKind.INT,
                  "BROKER-REPORTED price-change count, not a count of ticks held"),
        FieldSpec("real_volume", FieldKind.INT,
                  "raw as reported. 1xTrade-Server does not provide real "
                  "volume and always reports 0, which is a correct raw "
                  "observation but is INDISTINGUISHABLE from a genuine zero. "
                  "Whether a source provides real volume is a property of the "
                  "source, not of the bar: recorded once in source "
                  "capabilities (Phase 3), never inferred per-bar"),
        FieldSpec("timeframe", FieldKind.STRING,
                  "the bridge emits MT5 spelling (PERIOD_M1); hand-written "
                  "fixtures use the short form (M1). v1 accepts both; Phase 5 "
                  "resolves it with a Timeframe enum mapped at the codec"),
        FieldSpec("source", FieldKind.STRING),
    ),
)

SYMBOL_V1 = RecordSpec(
    record_type="symbol",
    fields=(
        FieldSpec("broker_symbol", FieldKind.STRING),
        FieldSpec("description", FieldKind.STRING,
                  "DEFECT D2: emitted unescaped"),
        FieldSpec("digits", FieldKind.INT),
        FieldSpec("point", FieldKind.FLOAT),
        FieldSpec("tick_size", FieldKind.FLOAT),
        FieldSpec("tick_value", FieldKind.FLOAT),
        FieldSpec("contract_size", FieldKind.FLOAT),
        FieldSpec("source", FieldKind.STRING),
    ),
)

SCHEMA_V1: dict[str, RecordSpec] = {
    spec.record_type: spec for spec in (TICK_V1, BAR_V1, SYMBOL_V1)
}


# ── schema v2 — the merged VO_Bridge.mq5, Phase 3 ──────────────────────────
#
# v2 fixes D1, D2, and one defect the original audit named but v1 hadn't yet
# addressed: F2. VO_BarBridge.mq5 and VO_TickBridge.mq5 both computed
# `TimeTradeServer() - TimeGMT()` — two values MQL5 derives from the local
# PC's clock, not from the server — and used that live guess to relabel the
# emitted timestamp as UTC ("...Z"). The correction happened silently: the
# raw server time and the offset used to "fix" it were both thrown away, so
# a wrong guess left no trace to catch later.
#
# v2 does not convert. Bar and tick times are emitted as broker server
# wall-clock, honestly typed as TIMESTAMP_ISO_SERVER / TIMESTAMP_EPOCH_SERVER_MS
# (no Z, no UTC claim). Real UTC conversion happens once, in the Time Engine
# (Phase 6), against an observed broker calendar profile — see
# vo.time.probe and config/settings/brokers.yaml — where the assumption is
# versioned, tested, and visible instead of buried in a bridge script.
#
# v2 also decomposes the fused `source` string ("MT5:US100.n") into
# `platform` / `broker_server` / `broker_symbol`, since a provenance value
# that must be parsed back apart is not provenance. And it adds the fields
# named in the Phase 3 deliverable list: `spread` (from MqlRates.spread,
# previously discarded), a per-source monotonic `seq` (gap/reorder
# detection), tick `flags` (MT5's own per-tick validity bitmask — which of
# bid/ask/last/volume actually changed on this tick), and `source_feed`
# ("live" from OnTick, or "history" from a CopyTicksRange/CopyRates backfill
# pass), so a record's collection method travels with it instead of being
# assumed.

TICK_V2 = RecordSpec(
    record_type="tick",
    fields=(
        FieldSpec("schema_version", FieldKind.INT, "always 2 for this spec"),
        FieldSpec("timestamp_server_ms", FieldKind.TIMESTAMP_EPOCH_SERVER_MS,
                  "raw MqlTick.time_msc: broker server wall-clock "
                  "milliseconds. NOT UTC — see the v2 module note (F2)"),
        FieldSpec("bid", FieldKind.FLOAT),
        FieldSpec("ask", FieldKind.FLOAT),
        FieldSpec("last", FieldKind.NULLABLE_FLOAT,
                  "null when the broker reports no last price"),
        FieldSpec("volume", FieldKind.FLOAT, "MqlTick.volume: tick volume"),
        FieldSpec("volume_real", FieldKind.FLOAT,
                  "MqlTick.volume_real, raw as reported. Whether this "
                  "source provides real volume at all is a source fact, "
                  "recorded once per (platform, broker_server, "
                  "broker_symbol) in a source_capabilities record, never "
                  "inferred from this value being zero"),
        FieldSpec("flags", FieldKind.INT,
                  "raw MqlTick.flags bitmask (TICK_FLAG_BID=2, ASK=4, "
                  "LAST=8, VOLUME=16, BUY=32, SELL=64): which fields this "
                  "tick actually updated"),
        FieldSpec("seq", FieldKind.INT,
                  "monotonic per (platform, broker_server, broker_symbol) "
                  "for this bridge run; resets on EA restart"),
        FieldSpec("source_feed", FieldKind.STRING, '"live" or "history"'),
        FieldSpec("platform", FieldKind.STRING, 'e.g. "MT5"'),
        FieldSpec("broker_server", FieldKind.STRING, 'e.g. "1xTrade-Server"'),
        FieldSpec("broker_symbol", FieldKind.STRING, 'e.g. "US100.n"'),
    ),
)

BAR_V2 = RecordSpec(
    record_type="bar",
    fields=(
        FieldSpec("schema_version", FieldKind.INT, "always 2 for this spec"),
        FieldSpec("timestamp", FieldKind.TIMESTAMP_ISO_SERVER,
                  "bar open time, broker server wall-clock. NOT UTC — "
                  "see the v2 module note (F2). This is also the D1 fix: "
                  "correctly formatted, whatever timezone it is in"),
        FieldSpec("open", FieldKind.FLOAT),
        FieldSpec("high", FieldKind.FLOAT),
        FieldSpec("low", FieldKind.FLOAT),
        FieldSpec("close", FieldKind.FLOAT),
        FieldSpec("tick_volume", FieldKind.INT,
                  "BROKER-REPORTED price-change count, not a count of ticks held"),
        FieldSpec("real_volume", FieldKind.INT,
                  "raw as reported; see source_capabilities for whether "
                  "this source provides real volume at all"),
        FieldSpec("spread", FieldKind.INT, "MqlRates.spread, in points"),
        FieldSpec("timeframe", FieldKind.STRING,
                  "MT5 spelling (e.g. PERIOD_M1); Phase 5 maps this to a "
                  "canonical Timeframe enum at the codec"),
        FieldSpec("seq", FieldKind.INT,
                  "monotonic per (platform, broker_server, broker_symbol, "
                  "timeframe) for this bridge run"),
        FieldSpec("source_feed", FieldKind.STRING, '"live" or "history"'),
        FieldSpec("platform", FieldKind.STRING),
        FieldSpec("broker_server", FieldKind.STRING),
        FieldSpec("broker_symbol", FieldKind.STRING),
    ),
)

SYMBOL_V2 = RecordSpec(
    record_type="symbol",
    fields=(
        FieldSpec("schema_version", FieldKind.INT, "always 2 for this spec"),
        FieldSpec("broker_symbol", FieldKind.STRING),
        FieldSpec("description", FieldKind.STRING,
                  "escaped by VO_Json.mqh; this is the D2 fix"),
        FieldSpec("digits", FieldKind.INT),
        FieldSpec("point", FieldKind.FLOAT),
        FieldSpec("tick_size", FieldKind.FLOAT),
        FieldSpec("tick_value", FieldKind.FLOAT),
        FieldSpec("contract_size", FieldKind.FLOAT),
        FieldSpec("platform", FieldKind.STRING),
        FieldSpec("broker_server", FieldKind.STRING),
    ),
)

# New in v2: real_volume_available is a fact about the SOURCE, observed once,
# not inferred per-bar from an ambiguous zero. Emitted once at EA startup.
SOURCE_CAPABILITIES_V1 = RecordSpec(
    record_type="source_capabilities",
    fields=(
        FieldSpec("schema_version", FieldKind.INT, "always 2 for this spec"),
        FieldSpec("platform", FieldKind.STRING),
        FieldSpec("broker_server", FieldKind.STRING),
        FieldSpec("broker_symbol", FieldKind.STRING),
        FieldSpec("real_volume_available", FieldKind.BOOL,
                  "[VO-D] observed nonzero real_volume anywhere in a "
                  "bounded recent-history scan at startup. This is a "
                  "lower bound, not a guarantee: absence of evidence over "
                  "a bounded window is not proof of absence. A later "
                  "session may raise this from false to true; it should "
                  "never need to lower it"),
        FieldSpec("tick_level_available", FieldKind.BOOL,
                  "[VO-D] a short CopyTicksRange probe at startup "
                  "returned at least one tick"),
    ),
)

SCHEMA_V2: dict[str, RecordSpec] = {
    spec.record_type: spec
    for spec in (TICK_V2, BAR_V2, SYMBOL_V2, SOURCE_CAPABILITIES_V1)
}

# What "current" means to code that doesn't care about history.
CURRENT_WIRE_SCHEMA_VERSION = 2
CURRENT_SCHEMA = SCHEMA_V2


def spec_for(record_type: str, version: int = 1) -> RecordSpec | None:
    """
    Look up a record's shape by discriminator and schema version.

    Version defaults to 1 for backward compatibility with every caller
    written before v2 existed. New code that has a `schema_version` field
    in hand should pass it explicitly rather than relying on the default.
    """
    schema = SCHEMA_V2 if version == 2 else SCHEMA_V1
    return schema.get(record_type)


def sample_wire_dict(spec: RecordSpec) -> dict[str, Any]:
    """
    A minimal schema-conformant record.

    Used by the tests that check the deserializer and this schema still agree
    about which fields are required. Values are placeholders; only shape and
    type matter, except `schema_version`, which must be the real version or a
    v2 sample would round-trip through the deserializer as if it were v1.
    """
    placeholders: dict[FieldKind, Any] = {
        FieldKind.STRING: "x",
        FieldKind.INT: 1,
        FieldKind.FLOAT: 1.0,
        FieldKind.NULLABLE_FLOAT: None,
        FieldKind.BOOL: True,
        FieldKind.TIMESTAMP_ISO_UTC: TIMESTAMP_ISO_EXAMPLE,
        FieldKind.TIMESTAMP_EPOCH_MS: 1789025880000,
        FieldKind.TIMESTAMP_ISO_SERVER: TIMESTAMP_ISO_SERVER_EXAMPLE,
        FieldKind.TIMESTAMP_EPOCH_SERVER_MS: 1789025880000,
    }

    record: dict[str, Any] = {"record_type": spec.record_type}
    is_v2 = spec in SCHEMA_V2.values()

    for field in spec.fields:
        if field.name == "schema_version":
            record[field.name] = 2 if is_v2 else 1
        else:
            record[field.name] = placeholders[field.kind]

    return record


def validate_wire_dict(data: dict[str, Any]) -> list[str]:
    """
    Check one decoded record against the schema.

    Returns a list of problems; empty means conformant. Reporting every problem
    rather than raising on the first keeps a malformed record diagnosable in
    one pass — which is what the quarantine path in Phase 4 needs.
    """
    problems: list[str] = []

    record_type = data.get("record_type")

    if not isinstance(record_type, str):
        return ["record_type is missing or not a string"]

    version = data.get("schema_version", 1)
    spec = spec_for(record_type, version=version)

    if spec is None:
        return [f"unknown record_type {record_type!r} at schema_version {version!r}"]

    for name in spec.field_names:
        if name not in data:
            problems.append(f"missing required field {name!r}")

    for key in data:
        if key not in spec.wire_keys:
            problems.append(f"unexpected field {key!r}")

    for field in spec.fields:
        if field.name not in data:
            continue

        value = data[field.name]
        problems.extend(_check_value(field, value))

    return problems


def _check_value(field: FieldSpec, value: Any) -> list[str]:
    name = field.name

    if field.kind is FieldKind.STRING:
        if not isinstance(value, str):
            return [f"{name} must be a string"]
        return []

    if field.kind is FieldKind.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{name} must be an integer"]
        return []

    if field.kind is FieldKind.FLOAT:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return [f"{name} must be a number"]
        return []

    if field.kind is FieldKind.NULLABLE_FLOAT:
        if value is None:
            return []
        if isinstance(value, bool) or not isinstance(value, int | float):
            return [f"{name} must be a number or null"]
        return []

    if field.kind is FieldKind.BOOL:
        if not isinstance(value, bool):
            return [f"{name} must be a boolean"]
        return []

    if field.kind is FieldKind.TIMESTAMP_EPOCH_MS:
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{name} must be epoch milliseconds as an integer"]
        return []

    if field.kind is FieldKind.TIMESTAMP_EPOCH_SERVER_MS:
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{name} must be server epoch milliseconds as an integer"]
        return []

    if field.kind is FieldKind.TIMESTAMP_ISO_UTC:
        if not isinstance(value, str):
            return [f"{name} must be an ISO-8601 UTC string"]
        if not TIMESTAMP_ISO_PATTERN.match(value):
            return [
                f"{name} is not ISO-8601 UTC: {value!r} "
                f"(expected the form {TIMESTAMP_ISO_EXAMPLE!r})"
            ]
        return []

    if field.kind is FieldKind.TIMESTAMP_ISO_SERVER:
        if not isinstance(value, str):
            return [f"{name} must be an ISO-8601-shaped server timestamp string"]
        if not TIMESTAMP_ISO_SERVER_PATTERN.match(value):
            return [
                f"{name} is not a server timestamp in the expected shape: "
                f"{value!r} (expected the form "
                f"{TIMESTAMP_ISO_SERVER_EXAMPLE!r} — no trailing Z, since "
                f"this value makes no UTC claim)"
            ]
        return []

    return [f"{name} has an unhandled field kind {field.kind}"]


def needs_json_escaping(value: str) -> bool:
    """True when placing this value into a JSON string by concatenation breaks it."""
    return any(hazard in value for hazard in JSON_STRING_HAZARDS)
