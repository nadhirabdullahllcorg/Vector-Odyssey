
import json

from datetime import datetime

from typing import Any



from .records import BarRecord, SymbolRecord, TickRecord





def _parse_timestamp(value: Any) -> datetime:

    if not isinstance(value, str):

        raise TypeError("Timestamp must be a string")



    if not value.endswith("Z"):

        raise ValueError("Timestamp must use UTC Z format")



    return datetime.fromisoformat(value[:-1] + "+00:00")





def _require_fields(

    data: dict[str, Any],

    required: tuple[str, ...],

) -> None:

    missing = [field for field in required if field not in data]



    if missing:

        raise ValueError(

            f"Missing required fields: {', '.join(missing)}"

        )





def dict_to_record(

    data: dict[str, Any],

) -> TickRecord | BarRecord | SymbolRecord:



    if not isinstance(data, dict):

        raise TypeError("Record data must be a dictionary")



    record_type = data.get("record_type")



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



    raise ValueError(

        f"Unsupported record_type: {record_type!r}"

    )





def json_to_record(

    value: str,

) -> TickRecord | BarRecord | SymbolRecord:

    if not isinstance(value, str):

        raise TypeError("JSON input must be a string")



    data = json.loads(value)



    return dict_to_record(data)

