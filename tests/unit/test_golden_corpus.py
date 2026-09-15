"""
Every captured record must parse.

The corpus lives in tests/fixtures/golden/ and contains only output captured
verbatim from a running terminal. See the README there for how to capture.

While the corpus is empty these tests skip loudly. An empty corpus is a known
gap, not a pass — so the skip reason says what is missing and how to fix it,
rather than disappearing into a dot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vo.market.deserialization import json_to_record
from vo.market.schema import validate_wire_dict

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"

NO_CORPUS = (
    "No captured output in tests/fixtures/golden/ yet. This is a gap: the "
    "MQL5 to Python path has never been exercised against real terminal "
    "output. See tests/fixtures/golden/README.md for the capture procedure."
)


def _golden_files() -> list[Path]:
    if not GOLDEN_DIR.exists():
        return []
    return sorted(GOLDEN_DIR.glob("*.jsonl"))


def _records() -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []

    for path in _golden_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if stripped:
                out.append((path, lineno, stripped))

    return out


def test_corpus_is_present() -> None:
    if not _golden_files():
        pytest.skip(NO_CORPUS)

    assert _records(), "golden files exist but contain no records"


def test_every_captured_record_is_valid_json() -> None:
    records = _records()

    if not records:
        pytest.skip(NO_CORPUS)

    failures = []

    for path, lineno, line in records:
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            failures.append(f"{path.name}:{lineno} {exc}")

    assert not failures, "captured records that are not valid JSON:\n  " + "\n  ".join(
        failures
    )


def test_every_captured_record_conforms_to_the_schema() -> None:
    records = _records()

    if not records:
        pytest.skip(NO_CORPUS)

    failures = []

    for path, lineno, line in records:
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            continue  # reported by the test above

        for problem in validate_wire_dict(decoded):
            failures.append(f"{path.name}:{lineno} {problem}")

    assert not failures, "captured records that violate the schema:\n  " + "\n  ".join(
        failures
    )


def test_every_captured_record_deserializes() -> None:
    """
    The whole point. A record the terminal really sent must become a canonical
    object without anyone touching the file first.
    """
    records = _records()

    if not records:
        pytest.skip(NO_CORPUS)

    failures = []

    for path, lineno, line in records:
        try:
            json_to_record(line)
        except (TypeError, ValueError) as exc:
            failures.append(f"{path.name}:{lineno} {type(exc).__name__}: {exc}")

    assert not failures, (
        "captured records the deserializer rejects:\n  " + "\n  ".join(failures)
    )


def test_golden_files_are_never_hand_edited() -> None:
    """
    A weak but useful guard: captured lines are single-line compact JSON with
    no spaces after separators. Reformatting by an editor shows up here.
    """
    records = _records()

    if not records:
        pytest.skip(NO_CORPUS)

    suspicious = [
        f"{path.name}:{lineno}"
        for path, lineno, line in records
        if '": ' in line or '", "' in line
    ]

    assert not suspicious, (
        "these lines look reformatted rather than captured — golden files are "
        "verbatim:\n  " + "\n  ".join(suspicious)
    )
