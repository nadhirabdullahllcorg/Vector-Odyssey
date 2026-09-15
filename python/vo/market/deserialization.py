"""
Wire dict / JSON -> canonical *Record dataclasses.

Dispatch is on `schema_version` (default 1, for every input written before
v2 existed — hand fixtures, any v1 golden capture) then on `record_type`.
The v1 branch is untouched from before Phase 3: same required-field tuples,
same construction, same errors. It exists so that anything already emitting
schema v1 keeps parsing exactly as it always did, and so
test_bridge_contract_v1_history.py keeps testing real, unmodified behavior
rather than a re-creation of it.
"""

import json
from datetime import datetime
from typing import Any

from .records import (
    BarRecord,
    BarRecordV2,
    SourceCapabilitiesRecord,
    SymbolRecord,
    SymbolRecordV2,
    TickRecord,
    TickRecordV2,
)

AnyRecord = (
    TickRecord
    | BarRecord
    | SymbolRecord
    | TickRecordV2
    | BarRecordV2
    | SymbolRecordV2
    | SourceCapabilitiesRecord
)


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Timestamp must be a string")

    if not value.endswith("Z"):
        raise ValueError("Timestamp must use UTC Z format")

    return datetime.fromisoformat(value[:-1] + "+00:00")


def _parse_server_timestamp(value: Any) -> datetime:
    """
    v2 bar timestamps are broker server wall-clock with no UTC claim, so
    unlike _parse_timestamp there is no "Z" to strip and no +00:00 to
    attach. The result is a naive datetime on purpose: attaching any
    tzinfo here would itself be an uncited claim about what timezone the
    server is in, which is exactly what schema v2 exists to stop doing at
    this layer. Resolving it to a real timezone is the Time Engine's job.
    """
    if not isinstance(value, str):
        raise TypeError("Timestamp must be a string")

    if value.endswith("Z"):
        raise ValueError(
            "v2 server timestamps must not carry a Z — that would claim a "
            "UTC offset this layer has not established. See schema.py."
        )

    return datetime.fromisoformat(value)


def _require_fields(
    data: dict[str, Any],
    required: tuple[str, ...],
) -> None:
    missing = [field for field in required if field not in data]

    if missing:
        raise ValueError(
            f"Missing required fields: {', '.join(missing)}"
        )


def _dict_to_record_v1(data: dict[str, Any]) -> TickRecord | BarRecord | SymbolRecord:
    """Unmodified since before Phase 3. See the module docstring."""
    record_type = data.get("record_type")
    required: tuple[str, ...]

    if record_type == "tick":
        required = (
            "timestamp_ms",
            "bid",
            "ask",
            "last",
            "volume",
            "source",
        )
        _require_fields(data, required)

        return TickRecord(
            timestamp_ms=data["timestamp_ms"],
            bid=data["bid"],
            ask=data["ask"],
            last=data["last"],
            volume=data["volume"],
            source=data["source"],
        )

    if record_type == "bar":
        required = (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "real_volume",
            "timeframe",
            "source",
        )
        _require_fields(data, required)

        return BarRecord(
            timestamp=_parse_timestamp(data["timestamp"]),
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            tick_volume=data["tick_volume"],
            real_volume=data["real_volume"],
            timeframe=data["timeframe"],
            source=data["source"],
        )

    if record_type == "symbol":
        required = (
            "broker_symbol",
            "description",
            "digits",
            "point",
            "tick_size",
            "tick_value",
            "contract_size",
            "source",
        )
        _require_fields(data, required)

        return SymbolRecord(
            broker_symbol=data["broker_symbol"],
            description=data["description"],
            digits=data["digits"],
            point=data["point"],
            tick_size=data["tick_size"],
            tick_value=data["tick_value"],
            contract_size=data["contract_size"],
            source=data["source"],
        )

    raise ValueError(f"Unsupported record_type: {record_type!r}")


def _dict_to_record_v2(
    data: dict[str, Any],
) -> TickRecordV2 | BarRecordV2 | SymbolRecordV2 | SourceCapabilitiesRecord:
    record_type = data.get("record_type")
    required: tuple[str, ...]

    if record_type == "tick":
        required = (
            "timestamp_server_ms",
            "bid",
            "ask",
            "last",
            "volume",
            "volume_real",
            "flags",
            "seq",
            "source_feed",
            "platform",
            "broker_server",
            "broker_symbol",
        )
        _require_fields(data, required)

        return TickRecordV2(
            timestamp_server_ms=data["timestamp_server_ms"],
            bid=data["bid"],
            ask=data["ask"],
            last=data["last"],
            volume=data["volume"],
            volume_real=data["volume_real"],
            flags=data["flags"],
            seq=data["seq"],
            source_feed=data["source_feed"],
            platform=data["platform"],
            broker_server=data["broker_server"],
            broker_symbol=data["broker_symbol"],
        )

    if record_type == "bar":
        required = (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "real_volume",
            "spread",
            "timeframe",
            "seq",
            "source_feed",
            "platform",
            "broker_server",
            "broker_symbol",
        )
        _require_fields(data, required)

        return BarRecordV2(
            timestamp=_parse_server_timestamp(data["timestamp"]),
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            tick_volume=data["tick_volume"],
            real_volume=data["real_volume"],
            spread=data["spread"],
            timeframe=data["timeframe"],
            seq=data["seq"],
            source_feed=data["source_feed"],
            platform=data["platform"],
            broker_server=data["broker_server"],
            broker_symbol=data["broker_symbol"],
        )

    if record_type == "symbol":
        required = (
            "broker_symbol",
            "description",
            "digits",
            "point",
            "tick_size",
            "tick_value",
            "contract_size",
            "platform",
            "broker_server",
        )
        _require_fields(data, required)

        return SymbolRecordV2(
            broker_symbol=data["broker_symbol"],
            description=data["description"],
            digits=data["digits"],
            point=data["point"],
            tick_size=data["tick_size"],
            tick_value=data["tick_value"],
            contract_size=data["contract_size"],
            platform=data["platform"],
            broker_server=data["broker_server"],
        )

    if record_type == "source_capabilities":
        required = (
            "platform",
            "broker_server",
            "broker_symbol",
            "real_volume_available",
            "tick_level_available",
        )
        _require_fields(data, required)

        return SourceCapabilitiesRecord(
            platform=data["platform"],
            broker_server=data["broker_server"],
            broker_symbol=data["broker_symbol"],
            real_volume_available=data["real_volume_available"],
            tick_level_available=data["tick_level_available"],
        )

    raise ValueError(f"Unsupported record_type: {record_type!r}")


def dict_to_record(data: dict[str, Any]) -> AnyRecord:
    if not isinstance(data, dict):
        raise TypeError("Record data must be a dictionary")

    version = data.get("schema_version", 1)

    if version == 1:
        return _dict_to_record_v1(data)

    if version == 2:
        return _dict_to_record_v2(data)

    raise ValueError(f"Unsupported schema_version: {version!r}")


def json_to_record(value: str) -> AnyRecord:
    if not isinstance(value, str):
        raise TypeError("JSON input must be a string")

    data = json.loads(value)

    return dict_to_record(data)
