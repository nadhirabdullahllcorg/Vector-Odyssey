"""
vo.research -- layer 5.

Phase 15/17/17a's reserved home: statistics primitives, and the Hurst,
Efficiency Ratio, Markov and HMM research modules built on top of them.
May import vo.interfaces (0), vo.market (1), vo.time (2), vo.observation
(3) and vo.month01 (4); may not import vo.core (6) or vo.telemetry (7) --
see tests/unit/test_architecture.py's LAYERS dict, which already reserves
this slot.

Nothing here decides a trade. This layer characterizes and compares --
distributions, transitions, latent states -- as independent evidence
research, never as a classifier or a decision path (G2). See
vo.observation.regime's own module docstring and
architecture/vo-phase-plan.md's §13-notes for the exact, already-agreed
split: the Basic Regime Engine (vo.observation.regime, layer 3) decides
what regime VO is observing; everything in this layer studies that
observation and the raw data it comes from, and reports back -- it never
writes RegimeState.
"""

from __future__ import annotations
