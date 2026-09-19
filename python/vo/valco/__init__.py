"""
vo.valco -- the LRX liquidity/reference-level reversal test strategy.

DISPOSABLE BY DESIGN. This package is a temporary, experimental strategy
bolted onto VO's finished infrastructure while the real VO strategy
engine is still being built. Its whole point is that it can be deleted:
nothing in vo.* imports it, so removing this directory plus the one
binding that attaches it to VOEaRuntime removes it completely, leaving
execution, risk, compliance, time, market and telemetry untouched.

WHY IT LIVES INSIDE vo/ RATHER THAN BESIDE IT. Disposability comes from
the dependency direction, not from the folder. Every architecture gate
in this project scans python/vo and nothing else (tests/unit/
test_architecture.py's VO_ROOT), and mypy's own scope is files =
["python/vo"]. A strategy parked outside that tree would silently lose
the no-lookahead harness, the G2 hypothesis-leak scan, the layering
check and strict typing -- exactly the protections a strategy that will
touch real money needs most.

THE ONE RULE THAT KEEPS VO CLEAN. Concepts, thresholds and shortcuts
invented here do NOT get to become VO concepts by proximity. Anything
that earns promotion into the real strategy engine goes through the same
evidence gates as anything else (G2/G6, and Phase 30 for a [VO-H]).
Convenience here is not evidence there.

Named for the EA_Valco project. Every module, config key and chart-object
prefix this package owns carries the valco/LRX name, so nothing it
produces can be mistaken for VO's own output.
"""
