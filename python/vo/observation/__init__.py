"""
vo.observation -- layer 3.

Phase 11's home: the Swing Engine, and whatever later observation-layer
engines join it. May import vo.interfaces (0), vo.market (1) and vo.time
(2); may not import vo.month01 (4), vo.research (5), vo.core (6) or
vo.telemetry (7) -- see tests/unit/test_architecture.py's LAYERS dict,
which already reserves this slot.

Nothing here decides what a swing MEANS to a trade. It decides what a
swing IS, mechanically, from price already observed -- the same
object/context/event/execution separation vo.month01.ontology registers
as its own ICT-sourced principle (Month01/L05, `object_context_event_
execution_separation`).
"""

from __future__ import annotations
