"""
The concept registry — gate G1.

The point of this machinery is that "VO must never silently turn an engineering
assumption into an ICT rule" stops being a discipline someone has to remember
and becomes a condition the build enforces.
"""

from __future__ import annotations

import pytest

from vo.interfaces import ConceptError, ConceptTag, concept, decision_path
from vo.interfaces.concepts import ConceptRecord, ConceptRegistry


def test_tags_are_the_four_classifications() -> None:
    assert {t.value for t in ConceptTag} == {"ICT", "VO-I", "VO-D", "VO-H"}


def test_ict_concept_requires_a_source_reference() -> None:
    with pytest.raises(ConceptError, match="source reference"):

        @concept(tag=ConceptTag.ICT)
        def equilibrium() -> float:
            return 0.0


def test_ict_concept_rejects_a_blank_source() -> None:
    with pytest.raises(ConceptError):

        @concept(tag=ConceptTag.ICT, source="   ")
        def equilibrium() -> float:
            return 0.0


def test_non_ict_tags_do_not_require_a_source() -> None:
    @concept(tag=ConceptTag.VO_D, note="engineering definition")
    def swing_lookback() -> int:
        return 3

    assert swing_lookback.__vo_concept__.tag is ConceptTag.VO_D
    assert swing_lookback.__vo_concept__.source is None


def test_tag_must_be_a_concept_tag() -> None:
    with pytest.raises(ConceptError, match="must be a ConceptTag"):

        @concept(tag="ICT")  # type: ignore[arg-type]
        def wrong() -> None:
            return None


def test_decoration_preserves_the_function() -> None:
    @concept(tag=ConceptTag.VO_I)
    def double(x: int) -> int:
        return x * 2

    assert double(21) == 42
    assert double.__name__ == "double"


def test_record_carries_provenance() -> None:
    @concept(
        tag=ConceptTag.ICT,
        source="Month01/L04-Equilibrium-Vs-Discount",
    )
    def midpoint(high: float, low: float) -> float:
        return (high + low) / 2.0

    record = midpoint.__vo_concept__

    assert record.name == "midpoint"
    assert record.tag is ConceptTag.ICT
    assert record.source == "Month01/L04-Equilibrium-Vs-Discount"
    assert record.module == __name__
    assert record.is_hypothesis is False


def test_hypothesis_is_flagged_as_such() -> None:
    @concept(tag=ConceptTag.VO_H)
    def resistance_score() -> float:
        return 0.0

    assert resistance_score.__vo_concept__.is_hypothesis is True


def test_registry_rejects_duplicate_declarations() -> None:
    registry = ConceptRegistry()

    record = ConceptRecord(
        name="x",
        tag=ConceptTag.VO_D,
        module="m",
        qualname="x",
    )

    registry.register(record)

    with pytest.raises(ConceptError, match="already registered"):
        registry.register(record)


def test_registry_queries() -> None:
    registry = ConceptRegistry()

    registry.register(
        ConceptRecord(
            name="a",
            tag=ConceptTag.ICT,
            module="m1",
            qualname="a",
            source="Month01/L01",
        )
    )
    registry.register(
        ConceptRecord(name="b", tag=ConceptTag.VO_H, module="m2", qualname="b")
    )
    registry.register(
        ConceptRecord(name="c", tag=ConceptTag.VO_H, module="m2", qualname="c")
    )

    assert len(registry) == 3
    assert len(registry.by_tag(ConceptTag.ICT)) == 1
    assert len(registry.hypotheses()) == 2
    assert registry.modules_declaring(ConceptTag.VO_H) == frozenset({"m2"})
    assert registry.get("m1.a") is not None
    assert registry.get("nope") is None


def test_decision_path_marks_the_function() -> None:
    @decision_path
    def decide() -> str:
        return "NO_TRADE"

    assert decide.__vo_decision_path__ is True
    assert decide() == "NO_TRADE"
