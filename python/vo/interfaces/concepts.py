"""
Concept provenance registry.

Every VO concept carries a tag recording where it came from, so that an
engineering assumption can never silently become an "ICT rule".

    ICT     directly supported by ICT source material; a source reference is
            REQUIRED and is checked at import time
    VO-I    an objective computational interpretation of something ICT describes
    VO-D    an engineering definition, created so the computer can measure an
            otherwise qualitative concept
    VO-H    a hypothesis; must be empirically validated before it is permitted
            to influence a trade decision

This module contains no market interpretation and no trading logic. It records
classification only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum


class ConceptTag(Enum):
    """Where a concept comes from. See module docstring."""

    ICT = "ICT"
    VO_I = "VO-I"
    VO_D = "VO-D"
    VO_H = "VO-H"

    def __str__(self) -> str:
        return self.value


class ConceptError(Exception):
    """Raised when a concept is declared incorrectly."""


@dataclass(frozen=True)
class ConceptRecord:
    """One classified concept, as declared at its definition site."""

    name: str
    tag: ConceptTag
    module: str
    qualname: str
    source: str | None = None
    note: str | None = None

    @property
    def key(self) -> str:
        return f"{self.module}.{self.qualname}"

    @property
    def is_hypothesis(self) -> bool:
        return self.tag is ConceptTag.VO_H


class ConceptRegistry:
    """
    The set of declared concepts.

    Populated at import time by the ``@concept`` decorator. Queried by the
    architecture tests that enforce gates G1 and G2.
    """

    def __init__(self) -> None:
        self._records: dict[str, ConceptRecord] = {}

    def register(self, record: ConceptRecord) -> None:
        existing = self._records.get(record.key)

        if existing is not None:
            raise ConceptError(
                f"Concept {record.key!r} is already registered "
                f"as {existing.tag}. Each concept is declared once."
            )

        self._records[record.key] = record

    def get(self, key: str) -> ConceptRecord | None:
        return self._records.get(key)

    def all(self) -> tuple[ConceptRecord, ...]:
        return tuple(self._records.values())

    def by_tag(self, tag: ConceptTag) -> tuple[ConceptRecord, ...]:
        return tuple(r for r in self._records.values() if r.tag is tag)

    def hypotheses(self) -> tuple[ConceptRecord, ...]:
        return self.by_tag(ConceptTag.VO_H)

    def modules_declaring(self, tag: ConceptTag) -> frozenset[str]:
        """Modules that declare at least one concept with this tag."""
        return frozenset(r.module for r in self.by_tag(tag))

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[ConceptRecord]:
        return iter(self._records.values())

    def clear(self) -> None:
        """Reset. For tests only — never call this from production code."""
        self._records.clear()


REGISTRY = ConceptRegistry()


def concept[F: Callable[..., object]](
    *,
    tag: ConceptTag,
    source: str | None = None,
    note: str | None = None,
) -> Callable[[F], F]:
    """
    Declare the provenance of a concept.

        @concept(tag=ConceptTag.ICT, source="Month01/L04-Equilibrium-Vs-Discount")
        def equilibrium(high: float, low: float) -> float:
            return (high + low) / 2.0

        @concept(tag=ConceptTag.VO_H)
        def liquidity_resistance_score(...) -> float:
            ...

    An ICT-tagged concept without a source reference is an error, because the
    claim "ICT teaches this" has to be checkable against the material.
    """
    if not isinstance(tag, ConceptTag):
        raise ConceptError(f"tag must be a ConceptTag, got {type(tag).__name__}")

    if tag is ConceptTag.ICT and not (source and source.strip()):
        raise ConceptError(
            "An [ICT] concept requires a source reference naming the lesson "
            "it comes from. Use VO-I, VO-D or VO-H if it is not directly "
            "supported by the material."
        )

    if source is not None and not source.strip():
        raise ConceptError("source cannot be blank; omit it instead")

    def decorate(obj: F) -> F:
        record = ConceptRecord(
            name=getattr(obj, "__name__", repr(obj)),
            tag=tag,
            module=getattr(obj, "__module__", "<unknown>"),
            qualname=getattr(obj, "__qualname__", getattr(obj, "__name__", "<unknown>")),
            source=source,
            note=note,
        )
        REGISTRY.register(record)
        obj.__vo_concept__ = record  # type: ignore[attr-defined]
        return obj

    return decorate


def decision_path[F: Callable[..., object]](obj: F) -> F:
    """
    Mark a function as part of the trade decision path.

    Gate G2: no [VO-H] concept may be reachable from anything marked this way
    until it has been promoted with evidence. Enforced by
    tests/unit/test_architecture.py.
    """
    obj.__vo_decision_path__ = True  # type: ignore[attr-defined]
    return obj
