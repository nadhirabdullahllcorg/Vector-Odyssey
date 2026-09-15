"""
Vector Odyssey — an MT5-native trading and research system.

Vector Odyssey defines the trading system; MT5 is the execution platform. The
package is layered so that the same canonical market representation serves live
trading, historical replay, backtesting and research without any of them
reinterpreting the market differently.

Layer order (a module may import its own layer or lower, never higher):

    vo.interfaces    contracts — depends on nothing
    vo.market        canonical market data
    vo.time          temporal context
    vo.observation   swings, market state
    vo.month01       Month 1 concept engines
    vo.research      statistics
    vo.core          runtime wiring

Enforced by tests/unit/test_architecture.py.
"""

__version__ = "0.1.0"
