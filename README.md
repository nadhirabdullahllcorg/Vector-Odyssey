# Vector Odyssey

An MT5-native trading and research system.

Vector Odyssey defines the trading system; MT5 is the execution platform. The two stay
separate, so the same canonical market representation serves live trading, historical
replay, backtesting and research without any of them reinterpreting the market differently.

## Status

Canonical market-data substrate: `Tick`, `Bar`, `Symbol` and their wire records, JSON
serialization and deserialization, JSONL ingestion, three read-only MQL5 bridges, 40 tests.

> **Known issue.** The MQL5 bar bridge emits timestamps via `TimeToString()`, which produces
> dot-separated dates that the Python deserializer rejects. The current fixtures are
> hand-written in ISO-8601, so the suite passes over a path that has never carried a live
> message. Fixing the bridge and adding a golden-corpus contract test comes before anything
> is built on top.

## Layout

```
python/vo/market/   canonical market data - tick, bar, symbol, records, codec, ingestion
python/vo/time/     time engine
python/vo/          core, interfaces, month01, observation, research
mql5/               MT5 bridges and the shared Include/ library
tests/              unit, integration, fixtures
data/               captured market data (git-ignored, re-capturable)
docs/               source notes, ontology, experiments
configs/            market and experiment settings
```

## Development

```bash
pip install -e .
pytest
```

Python 3.13+. MQL5 sources sync into the terminal with `sync_mql5.ps1`.

## Principle

> Do not code the strategy before the computer can reliably observe the market.

A human can look at a chart and say "this consolidated, expanded, retraced into discount,
ran liquidity, and reversed." A computer cannot use that sentence. It needs measurable
primitives, built in order, each tested before the next is laid on top.
