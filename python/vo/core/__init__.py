"""
Runtime wiring and the pipeline (Phases 9, 10, 18+).

vo.core is layer 6, the top of the original stack (vo.telemetry, added in
the dashboard scaffold, sits above it at layer 7 since it summarizes every
other layer including this one). vo.core may import anything below it;
nothing below it may import vo.core back.

Phase 9 populates this package with `replay` only -- driving a probe across
historical bars exactly the way the live runtime will drive a strategy
across live ones, and proving that doing so refuses to let a probe see
data after its current bar (gate G3).
"""
