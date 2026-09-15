from pathlib import Path

from typing import Iterator



from .deserialization import json_to_record

from .records import BarRecord, SymbolRecord, TickRecord





MarketRecord = TickRecord | BarRecord | SymbolRecord





def read_jsonl(path: str | Path) -> Iterator[MarketRecord]:

    """

    Read canonical VO records from a JSONL file.



    Each non-empty line must contain exactly one JSON market-data

    record accepted by the canonical deserializer.



    This function performs ingestion only.

    It does not interpret, transform, sort, or deduplicate records.

    """



    file_path = Path(path)



    with file_path.open("r", encoding="utf-8") as file:

        for line in file:

            line = line.strip()



            if not line:

                continue



            yield json_to_record(line)