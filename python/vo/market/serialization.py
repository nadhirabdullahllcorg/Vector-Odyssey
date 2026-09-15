import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .deserialization import AnyRecord
from .records import (
    BarRecord,
    BarRecordV2,
    SourceCapabilitiesRecord,
    SymbolRecord,
    SymbolRecordV2,
    TickRecord,
    TickRecordV2,
)

_RECORD_TYPE_BY_CLASS: dict[type, tuple[str, int]] = {
    TickRecord: ("tick", 1),
    BarRecord: ("bar", 1),
    SymbolRecord: ("symbol", 1),
    TickRecordV2: ("tick", 2),
    BarRecordV2: ("bar", 2),
    SymbolRecordV2: ("symbol", 2),
    SourceCapabilitiesRecord: ("source_capabilities", 2),
}


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # v2 server-wall-clock timestamps: no offset to render, so no Z.
            return value.isoformat()
        return value.isoformat().replace("+00:00", "Z")

    return value


def record_to_dict(record: AnyRecord) -> dict[str, Any]:
    data = asdict(record)

    entry = _RECORD_TYPE_BY_CLASS.get(type(record))
    if entry is None:
        raise TypeError(f"Unsupported record type: {type(record).__name__}")

    record_type, version = entry
    data["record_type"] = record_type

    if version >= 2:
        data = {"schema_version": version, **data}

    return {
        key: _serialize_value(value)
        for key, value in data.items()
    }


def record_to_json(record: AnyRecord) -> str:
    return json.dumps(
        record_to_dict(record),
        separators=(",", ":"),
    )
