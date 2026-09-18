# reports/ -- generated output, organized

Everything under `reports/` is generated/regenerable, never committed
(see `.gitignore`'s "GENERATED REPORTS" section) -- this file and
`.gitkeep` markers are the only tracked exceptions, so the convention
itself survives even though the content doesn't.

## reports/backtests/

Research/backtest markdown reports from `scripts/backtest_regime.py`,
one set per run, named `<report>_<symbol>.md`:

- `regime_backtest_<symbol>.md` -- always written (Phase 13's own
  backtest report: time in each regime, transitions, ER/Hurst per
  regime, anticipation-lean accuracy).
- `regime_validation_<symbol>.md` -- `--validate`.
- `hurst_report_<symbol>.md` -- `--hurst-report` (Phase 15a).
- `efficiency_ratio_report_<symbol>.md` -- `--er-report` (Phase 15b).
- `markov_report_<symbol>.md` -- `--markov-report` (Phase 17, the
  existing empirical RegimeType transition matrix).
- `hmm_report_<symbol>.md` -- `--hmm-report` (Phase 17a's IHMMEngine,
  the Gaussian HMM DISPLACEMENT/ACCUMULATION validation report).
  Needs the optional `[hmm]` extra (`pip install hmmlearn`, or
  `pip install -e ".[hmm]"`) -- skipped with a clear message otherwise.
- `phase_agreement_<symbol>.md` -- `--phase-report` (VALIDATION ONLY,
  confirmed 2026-09-18: statistical Hurst+ER+HMM 4-phase guess vs Phase
  13's real classification, agreement rate only -- never a decision
  path). Same optional-dependency handling as `--hmm-report`.

Re-running the script overwrites the previous set for that symbol --
these are point-in-time snapshots, not an append-only history. Rename
or copy a report out of `reports/backtests/` before the next run if you
need to keep it.

## reports/pytest/

Test/lint/type-check run logs, timestamped
(`YYYY-MM-DD_HHMM_<what>.log`), so past runs are easy to find without
re-running everything. To regenerate the same four, from the repo
root:

```
.venv/bin/python3 -m pytest tests/ -v > reports/pytest/$(date +%Y-%m-%d_%H%M)_full-suite.log 2>&1
.venv/bin/python3 -m ruff check python/ scripts/ tests/ > reports/pytest/$(date +%Y-%m-%d_%H%M)_ruff.log 2>&1
.venv/bin/python3 -m mypy > reports/pytest/$(date +%Y-%m-%d_%H%M)_mypy.log 2>&1
```

`*_hmm-modules-with-hmmlearn.log` runs only
`test_atr_series.py`/`test_hmm_report.py`/`test_phase_agreement.py`,
against an interpreter that actually has the optional `hmmlearn`
extra installed (the repo's own `.venv` does not, by design -- see
`pyproject.toml`'s `[hmm]` extra) -- this is how the real
`hmmlearn.GaussianHMM` fit path gets exercised, not just the
pure-Python orchestration around it, without making the core dev/CI
environment depend on a heavier optional package it does not need.

## Known pre-existing, unrelated finding (2026-09-18)

The 2026-09-18 ruff log surfaces one pre-existing issue, unrelated to
that day's work: `scripts/diagnose_quarantine.py:60` has a stale
`# noqa: BLE001` referencing a rule not enabled in this repo's ruff
config (`RUF100`, unused-noqa). Left as-is rather than fixed in
passing -- out of scope for the change that surfaced it.
