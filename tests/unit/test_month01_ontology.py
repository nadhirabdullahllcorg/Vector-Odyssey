"""
vo.month01.ontology -- Month 1's concept vocabulary, registered against the
source curriculum (architecture/vo-curriculum.md), is mechanically checkable
the same way any other concept is (gate G1).
"""

from __future__ import annotations

import vo.month01.ontology as ontology
from vo.interfaces import REGISTRY, ConceptTag

_EXPECTED_CONCEPT_NAMES = {
    "consolidation",
    "expansion",
    "retracement",
    "reversal",
    "pullback_unresolved",
    "condition_reference_pairing",
    "market_state_transition_structure",
    "price_delivery_conditioning",
    "time_as_market_variable",
    "institutional_intent_inferred",
    "observation_maturity_pipeline",
    "untested_level_lifecycle",
    "liquidity_reference_catalogue",
    "equilibrium",
    "discount",
    "active_swing_selection_criterion",
    "optimal_trade_entry_levels",
    "premium",
    "object_context_event_execution_separation",
    "delivery_fair_value",
    "liquidity_void",
    "fair_value_gap",
    "delivery_inefficiency_lifecycle",
    "buy_side_liquidity",
    "sell_side_liquidity",
    "high_resistance_liquidity_run",
    "low_resistance_liquidity_run",
    "liquidity_resistance_score",
    "impulse_price_swing",
    "internal_swing_nesting",
    "market_protraction",
    "protraction_windows_are_computed_not_hardcoded",
    "hurst_regime_instrumentation",
    "efficiency_ratio_regime_instrumentation",
    "markov_transition_study",
    "ihmm_engine",
    "canonical_relationship_memory",
    "provisional_structure_range_forecast",
}


def _month01_records() -> tuple[object, ...]:
    return tuple(r for r in REGISTRY if r.module == "vo.month01.ontology")


def test_every_expected_month01_concept_is_registered() -> None:
    registered_names = {r.name for r in _month01_records()}
    missing = _EXPECTED_CONCEPT_NAMES - registered_names

    assert not missing, f"Expected Month 1 concepts never registered: {sorted(missing)}"


def test_no_unexpected_extra_month01_concepts() -> None:
    """Catches a typo'd or orphaned @concept -- every registration should be
    intentional and named in this test's own expected set."""
    registered_names = {r.name for r in _month01_records()}
    extra = registered_names - _EXPECTED_CONCEPT_NAMES

    assert not extra, f"Unexpected Month 1 concepts, update this test: {sorted(extra)}"


def test_every_ict_tagged_month01_concept_names_a_real_lesson() -> None:
    for record in _month01_records():
        if record.tag is not ConceptTag.ICT:
            continue

        assert record.source is not None
        assert record.source.startswith("Month01/L0"), (
            f"{record.name} is [ICT] but its source {record.source!r} doesn't "
            "look like a Month 1 lesson reference"
        )


def test_liquidity_resistance_score_is_a_hypothesis_not_a_fact() -> None:
    """The one concept the curriculum is explicit has no ICT formula behind
    it -- any resistance score is [VO-H] until empirically validated."""
    record = REGISTRY.get("vo.month01.ontology.liquidity_resistance_score")

    assert record is not None
    assert record.tag is ConceptTag.VO_H


def test_pullback_unresolved_is_vo_own_label_not_ict() -> None:
    """VO's honest interim state for real-time retracement/reversal
    ambiguity is engineering, never an ICT term."""
    record = REGISTRY.get("vo.month01.ontology.pullback_unresolved")

    assert record is not None
    assert record.tag is ConceptTag.VO_D


def test_the_four_market_conditions_are_all_ict_tagged() -> None:
    for name in ("consolidation", "expansion", "retracement", "reversal"):
        record = REGISTRY.get(f"vo.month01.ontology.{name}")
        assert record is not None
        assert record.tag is ConceptTag.ICT


def test_the_decorator_stamps_the_record_directly_onto_the_function() -> None:
    """@concept also sets __vo_concept__ on the function itself (concepts.py) --
    checked here via the actually-imported module, not just the registry."""
    record = ontology.consolidation.__vo_concept__  # type: ignore[attr-defined]

    assert record.tag is ConceptTag.ICT
    assert record.name == "consolidation"

