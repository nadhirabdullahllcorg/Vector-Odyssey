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

# Characters that must be escaped before a value is placed inside a JSON string.
JSON_STRING_HAZARDS = ('"', "\\", "\n", "\r", "\t")


class FieldKind(Enum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    NULLABLE_FLOAT = "nullable_float"
    TIMESTAMP_ISO_UTC = "timestamp_iso_utc"
    TIMESTAMP_EPOCH_MS = "timestamp_epoch_ms"

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
        FieldSpec("real_volume", FieldKind.INT, "0 where the broker provides none"),
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


def spec_for(record_type: str) -> RecordSpec | None:
    return SCHEMA_V1.get(record_type)


def sample_wire_dict(spec: RecordSpec) -> dict[str, Any]:
    """
    A minimal schema-conformant record.

    Used by the tests that check the deserializer and this schema still agree
    about which fields are required. Values are placeholders; only shape and
    type matter.
    """
    placeholders: dict[FieldKind, Any] = {
        FieldKind.STRING: "x",
        FieldKind.INT: 1,
        FieldKind.FLOAT: 1.0,
        FieldKind.NULLABLE_FLOAT: None,
        FieldKind.TIMESTAMP_ISO_UTC: TIMESTAMP_ISO_EXAMPLE,
        FieldKind.TIMESTAMP_EPOCH_MS: 1789025880000,
    }

    record: dict[str, Any] = {"record_type": spec.record_type}

    for field in spec.fields:
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

    spec = spec_for(record_type)

    if spec is None:
        return [f"unknown record_type {record_type!r}"]

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

    if field.kind is FieldKind.TIMESTAMP_EPOCH_MS:
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{name} must be epoch milliseconds as an integer"]
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

    return [f"{name} has an unhandled field kind {field.kind}"]


def needs_json_escaping(value: str) -> bool:
    """True when placing this value into a JSON string by concatenation breaks it."""
    return any(hazard in value for hazard in JSON_STRING_HAZARDS)
