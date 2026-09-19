"""
vo.compliance -- the Account Compliance Engine (layer 9, between vo.risk
(8) and vo.core (9->10)/vo.execution (10->11)/vo.telemetry (11->12),
which all shift up by one to make room -- the identical pattern Phase 14
used to insert vo.signals/vo.allocation/vo.risk between vo.research (5)
and vo.core/vo.telemetry).

Confirmed with the user 2026-09-19, prompted by research into commercial
and open-source prop-firm "guardian" EAs (PropFirmGuard and the MQL5
Compliance Monitor article series, both fetched and read directly, not
assumed from secondhand description): this project's own architecture-
audit pipeline diagram (architecture/vo-architecture-audit.md F.2/F.3)
had no veto gate enforcing the ACCOUNT's own survival rules (daily loss,
total drawdown) between vo.risk's TradeSignal and vo.execution -- only
per-trade risk (vo.risk) and strategy correctness (vo.signals) existed.
See vo.interfaces.compliance's own module docstring for the full pipeline
diagram and the new G15 gate, and vo.compliance.engine's own module
docstring for exactly what this v1 does and does not implement.

Nothing here is wired into an automatic/unattended runtime path yet --
same posture as Phase 14/16's own ExecutionRouter.place()/
execute_reconciliation_actions(), deliberately left to Phase 18's own
wiring decision (the first live trade).
"""

from __future__ import annotations
