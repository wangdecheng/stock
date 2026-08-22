# 07 — Multi-universe batch backtest runner

**What to build:** A CLI tool `tools/run_batch_backtest.py` that takes a strategy class and a list of universes, runs `framework.backtest.Engine.run` once per universe, and writes one equity report per universe to `reports/<strategy>_<symbol>.json`. End-to-end demo: `python tools/run_batch_backtest.py` with the regime strategy and the 6-ETF basket produces 6 JSON files in `reports/` without error.

**Blocked by:** 04, 05, 06 — needs the full strategy (all states + hysteresis + phased exit + range signals) to produce meaningful per-symbol behaviour.

**Status:** ready-for-agent

- [x] File `tools/run_batch_backtest.py` exists; `tools/__init__.py` exists if needed for module discovery (verify project convention).
- [x] CLI accepts: `--strategy-class <dotted.path.to.Class>` and `--universes <sym> <sym> ...`. Also accepts a config-file variant (JSON or TOML) listing the strategy + universes, but the explicit CLI form must work first.
- [x] For each universe, the runner:
  - Instantiates the strategy class with default `__init__` params.
  - Builds a per-symbol trading calendar (uses the project's existing adapter / calendar builder — verify by reading `framework/runner/scheduled_run.py` or `framework/backtest/engine.py` for how calendars are produced today).
  - Runs `Engine(strategy=strategy_instance, universe=universe, adapter=adapter, calendar=calendar).run(initial_cash=100_000)` (or the project's default).
  - Serializes the resulting `Equity` object to `reports/<strategy>_<symbol>.json` (per-equity `dates`, `portfolio_value`, `benchmark_value`, `fills`, and aggregate stats).
- [x] Per-symbol trading calendars are computed independently (each ETF has its own trading-day set).
- [x] The 6-ETF basket is encoded as a default: if the user invokes without `--universes`, the runner uses `[510300, 510500, 159915, 512760, 512000, 511010]` (and prints a notice that the default basket was used).
- [x] CLI exit code is 0 only if all 6 backtests succeeded; a per-universe failure prints to stderr and continues (does not abort the batch).
- [x] Output directory `reports/` is created if missing; existing files for the same `(strategy, symbol)` are overwritten.
- [x] `reports/` is added to `.gitignore`.
- [x] Integration test: running the CLI end-to-end on a stub adapter (using existing test fixtures) produces 6 valid JSON files.
- [x] Doc-string / README line in the runner file describing usage.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D1 (module boundary), D6 (basket)
- `framework/backtest/engine.py` — how `Engine.run` is called and what `Equity` returns
- `framework/runner/scheduled_run.py` — reference for how the project already orchestrates a single run
- `framework/data/adapter/*` — how the data adapter is wired for ad-hoc use
- Tickets 04, 05, 06 — full strategy required
