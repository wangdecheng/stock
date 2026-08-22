# 08 — Buy-and-hold baseline + comparison report

**What to build:** A minimal baseline strategy `strategies/buy_and_hold.py` that emits `{"<sym>": 1.0}` for the universe's single symbol on every bar after the warmup window, plus a CLI `tools/compare_to_baseline.py` that reads two sets of `reports/*.json` files (regime strategy + baseline) and produces a markdown comparison table with `total_return`, `annualized`, `sharpe`, `max_drawdown` columns × 6 ETFs × 2 strategies. End-to-end demo: `python tools/compare_to_baseline.py` reads the outputs from ticket 07 and prints a markdown table to stdout (or writes to a file).

**Blocked by:** 07 — needs the per-symbol equity reports from the multi-universe runner.

**Status:** done (T08 implemented)

- [x] File `strategies/buy_and_hold.py` exists with `BuyAndHoldStrategy` class.
- [x] `name = "buy_and_hold"`.
- [x] `generate(ctx)` returns `{ctx.universe[0]: 1.0}` for every bar where the data adapter returns a non-empty frame; otherwise returns `{}`.
- [x] No state persistence required (this strategy has no internal state).
- [x] File passes the decoupling scan.
- [x] File `tools/compare_to_baseline.py` exists.
- [x] The tool reads `reports/adx_bb_regime_<sym>.json` and `reports/buy_and_hold_<sym>.json` for each symbol in the basket.
- [x] For each pair, it computes the four metrics using `framework.metrics.compute` (or the project's existing portfolio-metric helper) on the `portfolio_value` series.
- [x] Output is a markdown table:
  ```
  | Symbol | Strategy | Total Return | Annualized | Sharpe | Max DD |
  | --- | --- | --- | --- | --- | --- |
  | 510300 | adx_bb_regime | ... | ... | ... | ... |
  | 510300 | buy_and_hold   | ... | ... | ... | ... |
  | ...    | ...            | ... | ... | ... | ... |
  ```
  with one row per (symbol, strategy) pair.
- [x] Tool can write the table to stdout AND optionally to a file via `--out <path>`.
- [x] Tool can also write the table as CSV via `--format csv` for downstream analysis.
- [x] Test `tests/test_compare_to_baseline_08.py`:
  - Synthetic two-series equity data → expected markdown table.
  - Missing report file for one (symbol, strategy) pair → that row is omitted and a warning is printed to stderr.

## References (read alongside this ticket)

- `.scratch/adx-bb-regime/spec.md` — D1, D6
- `framework/metrics/` — metric computation API (`compute(equity, *, risk_free=0.02)`)
- `framework/backtest/engine.py` — `Equity` serialization shape
- `strategies/etf_rebalance.py` — prior art for a "trivial" baseline strategy
- Ticket 07 — produces the JSON input this ticket consumes
