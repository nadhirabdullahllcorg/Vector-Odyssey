"""
Month 1 ontology -- the formal concept registration Phase 8 exists to add.

architecture/vo-curriculum.md is the narrative reconstruction: which lesson
taught what, what VO extracted, what got classified [ICT]/[VO-I]/[VO-D]/
[VO-H] and why. This module is that same classification made mechanically
checkable: every name below is registered with vo.interfaces.concept, so
gate G1's existing test (test_every_ict_concept_names_its_source) now
covers the whole Month 1 vocabulary, not just whatever a later phase
happens to declare in passing.

Every function here is a marker, never called. It exists only to carry a
name, a docstring, and a `@concept` declaration -- the concept's
computational shape (a dataclass, an enum, an engine) belongs to whichever
phase already owns it as a stated deliverable; seed vo-phase-plan.md's own
phase table for that phase number, cited in each docstring below. Defining
that shape here, ahead of that phase, would be exactly the kind of
build-order jump this project's source discipline exists to prevent.

Source references use the pattern "Month01/L0<n>-<slug>", matching the
lesson titles in vo-curriculum.md §3.
"""

from __future__ import annotations

from vo.interfaces import ConceptTag, concept

# ── Lesson 1 — Elements Of A Trade Setup ────────────────────────────────────


@concept(tag=ConceptTag.ICT, source="Month01/L01-Elements-Of-A-Trade-Setup")
def consolidation() -> None:
    """One of Month 1's four market conditions. Shape: Phase 13 (Basic Regime Engine)."""


@concept(tag=ConceptTag.ICT, source="Month01/L01-Elements-Of-A-Trade-Setup")
def expansion() -> None:
    """One of Month 1's four market conditions. Shape: Phase 13."""


@concept(tag=ConceptTag.ICT, source="Month01/L01-Elements-Of-A-Trade-Setup")
def retracement() -> None:
    """One of Month 1's four market conditions. Shape: Phase 13."""


@concept(tag=ConceptTag.ICT, source="Month01/L01-Elements-Of-A-Trade-Setup")
def reversal() -> None:
    """One of Month 1's four market conditions. Shape: Phase 13."""


@concept(
    tag=ConceptTag.VO_D,
    note=(
        "VO's own interim label: retracement and reversal are the same "
        "observation in real time, per vo-phase-plan.md SS3. Never an ICT term."
    ),
)
def pullback_unresolved() -> None:
    """Shape: Phase 13's RegimeState; already live as a stated state name."""


@concept(
    tag=ConceptTag.VO_I,
    note="A condition (EXPANSION) and its reference (Order Block) are never the same object.",
    source="Month01/L01-Elements-Of-A-Trade-Setup",
)
def condition_reference_pairing() -> None:
    """The ontology principle behind every later object/context/event split."""


@concept(
    tag=ConceptTag.ICT,
    source="Month01/L01-Elements-Of-A-Trade-Setup",
    note="CONSOLIDATION -> EXPANSION -> {RETRACEMENT,REVERSAL} -> EXPANSION.",
)
def market_state_transition_structure() -> None:
    """Shape: vo-phase-plan.md SS13-notes' Month 1 transition-structure constraint."""


# ── Lesson 2 — How Market Makers Condition The Market ───────────────────────


@concept(tag=ConceptTag.ICT, source="Month01/L02-How-Market-Makers-Condition-The-Market")
def price_delivery_conditioning() -> None:
    """How price delivery is conditioned, not merely which condition holds."""


@concept(tag=ConceptTag.ICT, source="Month01/L02-How-Market-Makers-Condition-The-Market")
def time_as_market_variable() -> None:
    """Origin of the Time Engine (Phase 6) as a concern separate from price."""


@concept(
    tag=ConceptTag.VO_I,
    source="Month01/L02-How-Market-Makers-Condition-The-Market",
    note="Institutional intent is inferred from observable behavior, never observed directly.",
)
def institutional_intent_inferred() -> None:
    """Governs how any 'intent'-flavored feature may ever be framed."""


# ── Lesson 3 — What To Focus On Right Now ───────────────────────────────────


@concept(
    tag=ConceptTag.VO_D,
    source="Month01/L03-What-To-Focus-On-Right-Now",
    note="RAW FACT -> DERIVED FEATURE -> INTERPRETATION -> HYPOTHESIS -> PREDICTION -> EXECUTION.",
)
def observation_maturity_pipeline() -> None:
    """The epistemic staging every VO concept must pass through. See G2."""


@concept(tag=ConceptTag.VO_D, source="Month01/L03-What-To-Focus-On-Right-Now")
def untested_level_lifecycle() -> None:
    """CREATED->UNTESTED->APPROACHED->TESTED->SWEPT/VIOLATED->RECLAIMED/
    ACCEPTED/REJECTED->CONSUMED/ARCHIVED. Shape: Phase 21."""


@concept(tag=ConceptTag.ICT, source="Month01/L03-What-To-Focus-On-Right-Now")
def liquidity_reference_catalogue() -> None:
    """Recent highs/lows, equal highs/lows, prior-day H/L, and similar. Shape: Phase 21."""


# ── Lesson 4 — Equilibrium Vs. Discount ─────────────────────────────────────


@concept(tag=ConceptTag.ICT, source="Month01/L04-Equilibrium-Vs-Discount")
def equilibrium() -> None:
    """50% of a defined swing range. Shape: Phase 24 (Valuation Engine)."""


@concept(tag=ConceptTag.ICT, source="Month01/L04-Equilibrium-Vs-Discount")
def discount() -> None:
    """Below 50% of a defined bullish swing -- a location, never an automatic BUY."""


@concept(
    tag=ConceptTag.VO_D,
    note="Which swing is 'active' is a harder problem than the 50% math itself.",
)
def active_swing_selection_criterion() -> None:
    """The open engineering question Phase 11 (Swing Engine, G6) exists to answer."""


@concept(
    tag=ConceptTag.ICT,
    source="Month01/L04-Equilibrium-Vs-Discount",
    note="62% / 70.5% / 79%, recorded as valuation references, never automatic entries.",
)
def optimal_trade_entry_levels() -> None:
    """Shape: Phase 24's OTE config (62/70.5/79)."""


# ── Lesson 5 — Equilibrium Vs. Premium ──────────────────────────────────────


@concept(tag=ConceptTag.ICT, source="Month01/L05-Equilibrium-Vs-Premium")
def premium() -> None:
    """Above 50% of a defined bearish swing -- a location, never an automatic SELL."""


@concept(
    tag=ConceptTag.VO_D,
    source="Month01/L05-Equilibrium-Vs-Premium",
    note="Premium=location, Old High=reference, Stop Run=event, Turtle Soup=model, Sell=execution.",
)
def object_context_event_execution_separation() -> None:
    """The general ontology principle applied throughout every later engine."""


# ── Lesson 6 — Fair Valuation ────────────────────────────────────────────────


@concept(tag=ConceptTag.ICT, source="Month01/L06-Fair-Valuation")
def delivery_fair_value() -> None:
    """Fair value created by rapid/inefficient price delivery, distinct from equilibrium."""


@concept(tag=ConceptTag.ICT, source="Month01/L06-Fair-Valuation")
def liquidity_void() -> None:
    """Shape: Phase 27 (Delivery Inefficiency Engine)."""


@concept(tag=ConceptTag.ICT, source="Month01/L06-Fair-Valuation")
def fair_value_gap() -> None:
    """Shape: Phase 27. Never encoded as 'must fill' or an automatic entry."""


@concept(tag=ConceptTag.VO_D, source="Month01/L06-Fair-Valuation")
def delivery_inefficiency_lifecycle() -> None:
    """CREATED->UNTESTED->APPROACHED->PARTIALLY TRADED->REBALANCED->
    FULLY TRADED->INVALIDATED/ARCHIVED. Shape: Phase 27."""


# ── Lesson 7 — Liquidity Runs ────────────────────────────────────────────────


@concept(tag=ConceptTag.ICT, source="Month01/L07-Liquidity-Runs")
def buy_side_liquidity() -> None:
    """Liquidity resting above highs. Shape: Phase 21/23."""


@concept(tag=ConceptTag.ICT, source="Month01/L07-Liquidity-Runs")
def sell_side_liquidity() -> None:
    """Liquidity resting below lows. Shape: Phase 21/23."""


@concept(tag=ConceptTag.ICT, source="Month01/L07-Liquidity-Runs")
def high_resistance_liquidity_run() -> None:
    """Intervening structure/previous trading/defended levels. Shape: Phase 23."""


@concept(tag=ConceptTag.ICT, source="Month01/L07-Liquidity-Runs")
def low_resistance_liquidity_run() -> None:
    """Strong one-way expansion, limited retracement. Shape: Phase 23."""


@concept(
    tag=ConceptTag.VO_H,
    note="No ICT formula in Month 1 says resistance = X. Any score requires empirical validation.",
)
def liquidity_resistance_score() -> None:
    """Shape: Phase 23. G2: cannot reach a trade decision until promoted."""


# ── Lesson 8 — Impulse Price Swings & Market Protraction ────────────────────


@concept(
    tag=ConceptTag.ICT,
    source="Month01/L08-Impulse-Price-Swings-And-Market-Protraction",
)
def impulse_price_swing() -> None:
    """A directional move from one swing point to another. Shape: Phase 11."""


@concept(
    tag=ConceptTag.ICT,
    source="Month01/L08-Impulse-Price-Swings-And-Market-Protraction",
    note="Large impulse swings contain smaller internal swings.",
)
def internal_swing_nesting() -> None:
    """Not every candle is an equivalent unit of structure. Shape: Phase 11."""


@concept(
    tag=ConceptTag.ICT,
    source="Month01/L08-Impulse-Price-Swings-And-Market-Protraction",
)
def market_protraction() -> None:
    """Time-sensitive protractionary movement. Shape: Phase 28."""


@concept(
    tag=ConceptTag.VO_D,
    source="Month01/L08-Impulse-Price-Swings-And-Market-Protraction",
    note="Protraction windows are computed from the Time Engine, never hardcoded broker times.",
)
def protraction_windows_are_computed_not_hardcoded() -> None:
    """Shape: Phase 28; mirrors vo/time/brokers.py's DST-as-computed-rule discipline."""


# ── VO's own research/memory layer (not ICT lessons) ────────────────────────


@concept(tag=ConceptTag.VO_I, note="Regime instrumentation only -- never redefines RegimeState.")
def hurst_regime_instrumentation() -> None:
    """Shape: Phase 15."""


@concept(tag=ConceptTag.VO_I, note="Regime instrumentation only -- never redefines RegimeState.")
def efficiency_ratio_regime_instrumentation() -> None:
    """Shape: Phase 15."""


@concept(tag=ConceptTag.VO_I, note="Studies observed RegimeState sequences only.")
def markov_transition_study() -> None:
    """Shape: Phase 17."""


@concept(tag=ConceptTag.VO_H, note="No path to a trade decision, by construction. See G2.")
def ihmm_engine() -> None:
    """Shape: Phase 17a."""


@concept(
    tag=ConceptTag.VO_D,
    note="Stores objects/relationships/evidence/provenance; never rewrites the strategy.",
)
def canonical_relationship_memory() -> None:
    """Shape: Phase 26, built on this phase's CanonicalRecord/AppendOnlyLog."""


@concept(
    tag=ConceptTag.VO_H,
    note=(
        "No path to a trade decision, by construction. See G2. Scored against "
        "Phase 13's RegimeEngine as a forecast, never a replacement of it."
    ),
)
def provisional_structure_range_forecast() -> None:
    """Shape: Phase 13b deliverable (C) -- vo.observation.structure_range."""
