"""
Month 1 concept engines (Phases 8, 11, 13, 21, 23, 24, 27, 28).

vo.month01 is layer 4: it may import vo.market, vo.time and vo.observation,
and nothing above it may import it back except vo.research and vo.core.

Phase 8 populates this package with `ontology` only -- the formal
registration of Month 1's concept vocabulary against the source curriculum
(architecture/vo-curriculum.md). It deliberately does not yet define the
dataclass/enum shape for any concept that a later phase already owns as its
stated deliverable (RegimeState is Phase 13's, PriceSwing is Phase 11's,
and so on) -- see vo/interfaces/canonical.py's module docstring for why.
"""
