# 03 — Backtest engine, metrics, SQLite infra, backtest page

**What to build:** The user picks a registered strategy, a date range, and an initial cash on the Backtest page, clicks Run, and sees a PnL curve (portfolio vs benchmark), six metric cards (total return, annualized, Sharpe, max drawdown, Calmar, win rate, profit-loss ratio), and a fills table. Numbers match `empyrical` reference outputs.

**Blocked by:** 02 — needs the `Strategy` protocol + `Context` to call `generate()`.

**Status:** ready-for-agent

- [ ] `framework/engine.py` implements the event loop per `specs/spec-a-stock-quant/backtest-engine.md` (T+1 simplified, fill at next-day open, drop queues on the last bar, weighted-average cost applied).
- [ ] `framework/metrics.py` wraps `empyrical` returning the six-key dict; values match direct empyrical calls on a synthetic daily-returns series to 1e-6.
- [ ] `framework/persistence/schema.sql` defines `backtests` (id, strategy_id, start, end, initial_cash, metrics_json, equity_path, ran_at, status NULLABLE, error TEXT NULLABLE) and is run on startup via `IF NOT EXISTS`.
- [ ] `framework/persistence/db.py` opens `data/app.db` with `PRAGMA journal_mode=WAL`; one connection per request via `@st.cache_resource` or equivalent.
- [ ] `pages/4_回测.py`: form (strategy dropdown, two date pickers, initial-cash number input), Run button, result panel with Pyecharts PnL line (portfolio_value vs benchmark_value from `000300`), six `st.metric` cards, fills table.
- [ ] Run that raises an exception writes `backtests.status = 'failed'` + `error` traceback; the UI surfaces it in `st.error` instead of crashing.
- [ ] Equity time series is written to `data/backtests/<id>.parquet` and the path stored in `backtests.equity_path`.
- [ ] Lot-size handling distinguishes stocks (round-down to 100), ETFs (whole shares), funds (no lot) — see `backtest-engine.md` matching assumptions table.
- [ ] Backfill against a known fixture (MA-cross on `600519`, 2023-01 to 2023-12) is reproducible: re-running produces the same Equity within rounding.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-2 + Constraints 7/8 (no matching precision + single-process) + activation guard
- `specs/spec-a-stock-quant/backtest-engine.md` — Equity + event loop order + matching assumptions + metrics dict schema + `backtests` schema
- `specs/spec-a-stock-quant/strategy-interface.md` § Activation guard — a strategy must have a backtest in the last 30 days before it can be made active
- `specs/spec-a-stock-quant/ui-pages.md` § Page 5 — Backtest page wireframe
