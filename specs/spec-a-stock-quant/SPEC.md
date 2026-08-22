---
id: SPEC-a-stock-quant
companions:
  - data-adapter.md
  - position-schema.md
  - strategy-interface.md
  - backtest-engine.md
  - indicators-and-metrics.md
  - ui-pages.md
  - scheduler.md
  - architecture-diagrams.md
sources:
  - /Users/wangdecheng/ai/stock/wayfinder/MAP.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T1-data-source.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T2-backtest-engine.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T3-strategy-interface.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T4-research-akshare.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T5-web-stack.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T6-position-model.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T10-research-charts.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T11-ui-info-arch.md
  - /Users/wangdecheng/ai/stock/wayfinder/tickets/T12-tech-indicators.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability only.

# A 股量化 Web 框架（MVP）

## Why

A **vision to realize**: one self-hosted Web app gives an individual A-share investor every step from strategy authoring through daily plan generation to position view. The user already has a cloud server; the missing piece is the application that (a) treats strategies as drop-in Python files decoupled from framework internals, (b) generates an action plan at 15:30 every trading day without manual babysitting, and (c) surfaces it in a browser with K-line + buy/sell markers alongside real positions and per-strategy virtual books so the user can decide what to actually do.

This is a single-user, non-institutional, non-HFT effort. Free data, manual positions, no live order routing. The MVP exists to close the loop: write strategy → backtest → run on schedule → review plan in browser → user executes by hand.

## Capabilities

- id: CAP-1
  intent: System can fetch A-share K-line (daily + minute) and fundamentals from AKShare through a single `DataAdapter` interface, with rate-limit and cache-fallback guarantees.
  success: Given `(symbol, start, end, adj)`, returns a complete OHLCV+adj DataFrame; under network outage, returns the last cached parquet and the UI shows a "数据延迟" badge; AKShare's 20 req/min/IP ceiling is never exceeded.

- id: CAP-2
  intent: System can run a backtest — strategy + time window + initial cash → outputs an `Equity` series and a metrics card.
  success: Selecting a registered strategy with a date range and an initial cash value renders (a) a PnL curve vs benchmark, (b) a metrics card with total return / annualized / Sharpe / max drawdown / Calmar / win-rate / profit-loss ratio, with numbers matching `empyrical` reference values.

- id: CAP-3
  intent: System can auto-discover and hot-plug strategies from `strategies/*.py`, and a strategy can declare its parameters through `__init__` defaults that the UI renders as a form.
  success: Adding a new `.py` file defining a `Strategy` subclass with a `name` class attribute and reflecting its `__init__` annotations makes that strategy appear in the UI dropdown after a refresh, and its parameters render as native Streamlit widgets (slider/selectbox/number-input) with values persisted to `strategies/<name>.params.json`.

- id: CAP-4
  intent: A strategy can read its context (positions / cash / trades / state / bars / current price) and emit a `{symbol: target_weight}` (0–1) signal; the engine computes the diff and fills at next-day open.
  success: An example "ETF 目标权重再平衡" strategy returning `{"510300": 0.4, "513500": 0.3, "511010": 0.3}` produces a virtual book's transactions that, post-fill, sum to within 1% of those target weights (drift bounded by share-rounding and the next-day-open fill price).

- id: CAP-5
  intent: System can store user-entered real trades and per-strategy virtual books (initial cash + positions + cost basis), and write each daily strategy run's suggestions to a suggestions table.
  success: After the user edits real trades via `st.data_editor`, the dashboard's real-position market value updates; running a strategy writes one row per (strategy_id, symbol, action) to `strategy_suggestions` with `applied=false`; "apply to virtual book" advances that strategy's `virtual_positions`/`virtual_books.cash` accordingly.

- id: CAP-6
  intent: System provides a 5-page Streamlit multipage UI (Dashboard / Stock Detail / Strategy / Positions / Backtest) with Pyecharts K-line + MarkPoint buy/sell markers and Grid multi-chart linkage.
  success: Browser visits all 5 pages without error; K-line overlays buy/sell markers in a single MarkPoint call and supports linked sub-charts (volume + indicator); iPad-width layout does not break.

- id: CAP-7
  intent: System schedules the active strategy to run at 15:30 on every trading day, **outside the Streamlit process**, and writes its suggestions to the database.
  success: After deployment, on each trading day the 15:30 trigger fires, the chosen strategy's `generate()` completes against fresh data, and new rows appear in `strategy_suggestions` within 5 minutes; the Streamlit UI thread is not blocked.

- id: CAP-8
  intent: System deploys as a single Streamlit process on the user's existing cloud server, fronted by Nginx/Caddy with basic auth + HTTPS, so a one-line start command brings the MVP online.
  success: `streamlit run app.py --server.port 8501 --server.address 0.0.0.0` starts the app; reverse-proxy + basic-auth credentials gate the browser; killing and restarting the process preserves all SQLite rows and `strategies/*.state.json` files.

## Constraints

- AKShare is the sole data source. North-bound capital is unavailable (the AKShare `stock_hsgt_hist_em` interface is dead since 2024-08-19; Tushare Pro would be needed to restore it but is out of MVP scope). Adjustment-factor accuracy is accepted at ≥10% cumulative error on ST / IPO / restructuring names.
- DataAdapter must rate-limit at ≤ 20 req/min/IP via a `ratelimit` decorator + global token bucket; tests must prove the ceiling is not exceeded under burst load.
- Network outage path: read from local parquet cache, render a "数据延迟" badge in the UI; do not crash.
- Strategy code is framework-decoupled: only the `Strategy` base class and the `Context` dataclass may be imported. No `import` from framework internals.
- Per-strategy files `strategies/<name>.params.json` (UI-editable parameters) and `strategies/<name>.state.json` (persistent strategy state) must be ignored by git.
- The signal format is `target_position` (0–1 percentages). The engine converts to dollars at fill time using the current `portfolio_value`; strategies do not state order quantities.
- No matching-model precision: T+1 settlement, price-limit (10%/20%) handling, slippage, and partial fills are all simplified assumptions. Single-process, single-threaded.
- Strategies must clear a backtest before they can be activated for live scheduling (hard prerequisite, not advisory).
- Two parallel position tracks: `real_trades` (user-entered) and `(virtual_books, virtual_positions, strategy_suggestions)` (per-strategy). They are separate SQL tables and must never be cross-written by framework code.
- Real and virtual must use weighted-average cost on multi-lot buys. `virtual_books.initial_cash` defaults to 100 000 (configurable); commission defaults to 0.0003 (configurable).
- Streamlit only (no FastAPI/React); multipage via `pages/` directory; file-name digit prefix controls order; `st.session_state` carries page-local state.
- Charts must use Pyecharts v2.0.9 + `streamlit-echarts[pyecharts]` `st_pyecharts()`; do not hand-roll a JS bridge. Plotly only if Pyecharts cannot satisfy a future requirement.
- Indicators (`pandas-ta`) and portfolio metrics (`empyrical`) live in separate modules (`indicators/` and `metrics/`); do not couple them.
- The 15:30 scheduler runs in its own process (system cron, APScheduler daemon, or equivalent), writing results to SQLite — it must never run inside the Streamlit UI process.
- Authentication is delegated to a reverse proxy (Nginx/Caddy) with basic auth + HTTPS terminated at the proxy. Streamlit itself has no auth.

## Non-goals

- Live order routing / broker API integration. The system produces an action plan; a human executes.
- Multi-user / per-user permission models. Single-user system, reverse-proxy basic auth is the access boundary.
- Mobile-first or phone-screen optimization. iPad-width layouts are acceptable; phones are read-only "good enough."
- Sub-day high-frequency / tick-level strategies. Daily resolution only in MVP.
- Production-grade data accuracy (Tushare Pro north-bound + precise adjustment). AKShare's gaps are accepted.
- Distributed / multi-server deployment. Single cloud server, single process.
- Real-time intraday push. UI is pull-based; intraday refresh granularity is the upstream data's choice.
- Stock-pool / watchlist grouping and strategy-to-pool binding (see Open Questions).
- Auto-tuner / walk-forward optimization (no auto-tuner is part of MVP; if added later, it must not break the protocol in `strategy-interface.md`).

## Success signal

Deploy the Streamlit process on the user's cloud server, point Nginx with basic auth at it. On any trading day at 09:00 the user opens the dashboard in a browser, sees real position market value alongside the active strategy's virtual book curve, and a one-click view shows today's `strategy_suggestions` action plan. The strategy-management page lists every strategy under `strategies/` with its persisted parameters; the backtest page produces a PnL curve + Sharpe + max-drawdown card for any (strategy, range, initial cash) tuple within seconds. Drop a new `strategies/my_strategy.py` into the directory, refresh, and the UI surfaces it without code changes to framework internals.

## Assumptions

- AKShare's v1.18.x maintenance trajectory keeps K-line / financial / minute interfaces stable even where the north-bound interface has rotted.
- Single user with one broker account on mainland A-shares only; no HK / US symbols.
- Desktop Chrome (and iPad Safari) are the primary browsers; auth happens at the reverse proxy, so the Streamlit process trusts its caller.
- The cloud server has a fixed public address and a TLS cert managed at the reverse proxy (Nginx/Caddy); the Streamlit process binds plain HTTP to localhost / 0.0.0.0:8501.
- Chinese-language UI is appropriate; English is not a UI requirement for MVP.
- The user reads and edits Python files directly. There is no GUI strategy-author tool in MVP.

## Open Questions

- **Stock-pool / watchlist grouping**: is "universe per strategy" sufficient, or must the user also maintain named pools and bind them to strategies? (Affects Strategy.protocol.universe source — currently "config list at strategy activation time".)
- **Backtest default time window**: hardcoded 1Y / 3Y, or always a UI prompt with a saved default? (Affects CAP-2's "selected a … date range" wording in success criteria.)
- **Adjustment method default**: 前复权 / 后复权 / 不复权 — which is the framework default when callers do not specify? `DataAdapter.get_bars` accepts `adj`; the default currently has to be picked.
- **Buy fund allocation fallback**: when a strategy returns `target_position` and some symbols have no clear weight signal, does the engine default to (a) equal-weight residual, (b) leave cash unallocated, or (c) error? CAP-4 currently leans toward "leave cash" but a UI-er is wanted.
- **Strategy parameter form UX**: type annotations choose Streamlit widgets — is that enough, or do we need ranges / regex validation / per-param help text?
- **Failure notification**: when the 15:30 run fails (data fetch / strategy exception / DB write), how does the user find out? (Dashboard badge vs. log scrape vs. external notifier.) Scheduler companion assumes a worst-case default; this needs a UX call.
- **Reverse-proxy basic-auth rotation**: who issues and rotates basic-auth credentials? (Out of effort per the handoff, but the SPEC should acknowledge that some credential store exists.)

---

Next step after this SPEC: `/to-tickets` to cut it into 5–8 implementation tickets (data adapter → backtest engine → position schema → strategy interface → 5-page UI → scheduler/deploy) without losing context across the cut.
