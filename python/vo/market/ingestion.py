from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True)
class QuarantinedLine:
    """One line JsonlTailer could not turn into a record, and why -- the
    same quarantine-not-abort discipline vo.market.mapping.map_records and
    vo.market.sequence.build_bar_sequence already use (E.7): one bad line
    never blocks the lines around it."""

    line: str
    reason: str


@dataclass(frozen=True)
class TailResult:
    """One poll()'s worth of newly-available JSONL records."""

    records: tuple[AnyRecord, ...]
    quarantined: tuple[QuarantinedLine, ...]


class JsonlTailer:
    """
    Incremental "tail -f" reader for a JSONL wire file that another
    process (VO_Bridge.mq5) keeps appending to while VO reads it.

    Unlike read_jsonl (batch, whole-file, one-shot), this is stateful:
    each poll() call reads only the bytes appended since the previous
    poll and remembers its own byte offset across calls, so it is safe
    to call repeatedly against a file that keeps growing for the
    lifetime of a live process (Phase 10).

    Deliberately reads in binary mode and tracks a byte offset, rather
    than iterating text-mode lines: VO_Transport.mqh writes one line per
    FileWriteString + FileFlush call, so a poll can legitimately catch
    the file mid-append with a trailing partial line. That partial line
    is never parsed -- only bytes up to the last newline seen are ever
    consumed, and the remainder is picked up whole on the next poll.

    If the file is shorter than the last recorded offset (rotated or
    truncated by something else since the previous poll), the offset
    resets to 0 rather than raising or reading from a now-meaningless
    position -- the same "state the honest thing, don't guess" default
    TickCoverage and UnresolvedServerTimeError already use elsewhere in
    this project.

    A file that does not exist yet returns an empty TailResult rather
    than raising: the wire file does not exist until VO_Bridge.mq5's
    OnInit has run, and "not started yet" is an expected, non-fatal
    state for a process that may start before or after the terminal
    does.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._offset = 0

    @property
    def offset(self) -> int:
        return self._offset

    def poll(self) -> TailResult:
        if not self.path.exists():
            return TailResult(records=(), quarantined=())

        size = self.path.stat().st_size
        if size < self._offset:
            self._offset = 0

        with self.path.open("rb") as file:
            file.seek(self._offset)
            chunk = file.read()

        parts = chunk.split(b"\n")
        complete, remainder = parts[:-1], parts[-1]
        self._offset += len(chunk) - len(remainder)

        records: list[AnyRecord] = []
        quarantined: list[QuarantinedLine] = []

        for raw in complete:
            try:
                text = raw.decode("utf-8").strip("\r").strip()
            except UnicodeDecodeError as exc:
                quarantined.append(
                    QuarantinedLine(line=repr(raw), reason=f"utf-8 decode error: {exc}")
                )
                continue

            if not text:
                continue

            try:
                records.append(json_to_record(text))
            except (ValueError, TypeError) as exc:
                quarantined.append(QuarantinedLine(line=text, reason=str(exc)))

        return TailResult(records=tuple(records), quarantined=tuple(quarantined))
