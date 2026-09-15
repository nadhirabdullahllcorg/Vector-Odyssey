from collections.abc import Iterator
from pathlib import Path

from .deserialization import AnyRecord, json_to_record


def read_jsonl(path: str | Path) -> Iterator[AnyRecord]:
    """
    Read canonical VO records from a JSONL file.

    Each non-empty line must contain exactly one JSON market-data
    record accepted by the canonical deserializer, schema v1 or v2.

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
