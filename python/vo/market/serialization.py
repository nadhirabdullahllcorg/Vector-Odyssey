import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .records import BarRecord, SymbolRecord, TickRecord


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")

    return value


def record_to_dict(
    record: TickRecord | BarRecord | SymbolRecord,
) -> dict[str, Any]:
    data = asdict(record)

    if isinstance(record, TickRecord):
        data["record_type"] = "tick"
    elif isinstance(record, BarRecord):
        data["record_type"] = "bar"
    elif isinstance(record, SymbolRecord):
        data["record_type"] = "symbol"
    else:
        raise TypeError(f"Unsupported record type: {type(record).__name__}")

    return {
        key: _serialize_value(value)
        for key, value in data.items()
    }


def record_to_json(
    record: TickRecord | BarRecord | SymbolRecord,
) -> str:
    return json.dumps(
        record_to_dict(record),
        separators=(",", ":"),
    )
