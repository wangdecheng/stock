# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Self-hosted Web app for an individual A-share investor: write strategies → backtest → daily 15:00 suggestion plan → view in browser. Single-user, AKShare for data, manual positions, no live order routing. The MVP closes the strategy → plan loop; production-grade accuracy is explicitly out of scope. The full capability contract is in [`specs/spec-a-stock-quant/SPEC.md`](specs/spec-a-stock-quant/SPEC.md) — read it before changing the public surface.

The currently active feature under construction is the **ADX+Bollinger regime strategy** (`.scratch/adx-bb-regime/spec.md`). It defines domain vocabulary in [`CONTEXT.md`](CONTEXT.md) and is implemented across tickets T01–T09 (see `.scratch/adx-bb-regime/issues/`).

## Commands

### Setup
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` pins `streamlit>=1.30,<1.49`, `pyecharts==2.0.9`, `streamlit-echarts>=0.4,<0.5`, `akshare`, `pandas`, `empyrical`, `pytest`. The `pandas-ta` placeholder is **commented out** — base Python is 3.11 and `pandas-ta>=0.4.67b0` requires 3.12+. Until that lifts, `framework/indicators/__init__.py` ships a pure-pandas port of the nine registered wrappers; don't "fix" this by hand-installing.

### Run the app
```bash
streamlit run app.py               # UI on http://localhost:8501
python -m framework.runner         # 15:00 suggestion plan; exit code is meaningful (see scheduler.md)
```

### Run the batch backtest + comparison toolchain
```bash
python tools/run_batch_backtest.py                                # 6-ETF default basket
python tools/run_batch_backtest.py --universes 510300 159915      # override basket
python tools/compare_to_baseline.py                               # markdown table from reports/
python tools/compare_to_baseline.py --format csv --out cmp.csv    # CSV variant
```

Default curated basket from `.scratch/adx-bb-regime/spec.md` D6: `["510300", "510500", "159915", "512760", "512000", "511010"]`. The sixth (`511010` 国债ETF) doubles as the zero-volatility regression test from T09.

### Tests
```bash
pytest                                              # everything
pytest tests/test_adx_bb_regime_03.py -k hysteresis # single test by node
pytest -k "compare_to_baseline"                    # by keyword across files
pytest tests/test_data_layer.py -x                 # fail-fast on the data-layer suite
```

Test layout is per-ticket (`test_adx_bb_regime_NN.py`, `test_run_batch_backtest_07.py`, `test_compare_to_baseline_08.py`) plus the older `test_*_tN.py` files for tickets T2–T12.

## Architecture

### Layering — read this before touching `framework/`

```
strategies/*.py            user-authored; only allowed to import Strategy/Context/Position/Trade
   │
   ▼
framework/strategy/        Strategy protocol + Context dataclass + discover() + JSON persistence
framework/indicators/      pure-pandas wrappers for adx, bollinger, rsi, ma, ema, macd, atr, kdj, obv
framework/backtest/        Engine (event loop) + metrics (empyrical wrapper) + store
framework/data/            AKShareAdapter (the ONLY akshare importer) + ratelimit + parquet cache
framework/persistence/     SQLite schema, repos for real_trades / virtual_books / suggestions / backtests
framework/runner/          15:00 scheduler — runs in its OWN process, must not be imported by UI
framework/charts/          Pyecharts v2.0.9 charts (K-line, volume, equity curve)
framework/ui_runtime.py    Streamlit-only seam: SQLite connection + active-strategy pointer
framework/metrics/         (currently empty — empyrical lives in framework/backtest/metrics.py)
   ▲
   │
pages/1_股票详情.py / 2_策略管理.py / 3_持仓管理.py / 4_回测.py + app.py (dashboard)
tools/run_batch_backtest.py / compare_to_baseline.py
```

Two non-negotiable rules (CAP-1 + CAP-3/4 in `SPEC.md`):

1. **AKShare single source.** Only `framework/data/adapter.py` may `import akshare`. The CI grep is load-bearing; don't move the import.
2. **Strategy decoupling.** `strategies/*.py` may only import `Strategy`, `Context`, `Position`, `Trade` from `framework.strategy`. `framework/strategy/discover.py::_check_decoupling` is an AST scan (not regex) so docstrings/comments mentioning forbidden names don't false-positive.

### Event-loop contract (CAP-4 / `framework/backtest/engine.py`)

```
for t in calendar:
    1. apply pending orders queued at t-1 at t.open  (T+1 fill; queue, fill, snapshot)
    2. snapshot today_closes, bench, pv
    3. build Context (post-fill book, t-1 state, today adapter)
    4. signal = strategy.generate(ctx)             # {sym: target_weight in [0,1]}
    5. validate_signal(signal, universe)           # sum<=1, in-universe, 0<=w<=1
    6. diff at today's close → queue orders for t+1
       (orders queued on the LAST bar are dropped — no forward-equity leakage)
```

Default benchmark = `000300` (CSI 300). Default commission = 0.0003. Default adj = `qfq`. Lookback default = 60 bars; the regime strategy overrides to 120 to let ADX(14) + Wilder smoothing converge.

### Strategy interface (the seam you must respect)

- `class Strategy` (Protocol): must have class-level `name: str` and `generate(self, ctx) -> dict[str, float]`. Subclassing is optional — duck-typing is enough.
- `class Context`: `now`, `universe`, `adapter` (injected), `positions`, `cash`, `portfolio_value`, `trades`, `state`. Methods: `bars(sym, lookback=60)`, `price(sym)`, `indicator(name, *args, **kwargs)`.
- `state` is a free-form dict loaded from `strategies/<name>.state.json` and written back atomically (`framework/strategy/persistence.py`). Use `ctx.state.setdefault("k", default)` so pre-populated values aren't clobbered — only the live classification field (`current_state`) should be rewritten each bar.
- `params` (separate from `state`) live in `strategies/<name>.params.json`; the param form on page 2 builds widgets from `__init__` annotations. Adding a new UI knob = add a typed kwarg to `__init__` with a default.

### Data layer (`framework/data/adapter.py`)

- AKShare eastmoney (`ak.stock_zh_a_hist`) is primary; Tencent (`ak.stock_zh_a_hist_tx`) is the daily-only fallback. Minute frequencies have no fallback. On exception → read `data/cache/<sym>/<freq>_<adj>.parquet` and return `BarsResult(stale_seconds=N, cache_hit=True)`. UI renders a "数据延迟" badge.
- `EmptyBarsError` (recognized symbol, empty response) does **not** consult cache — the upstream made a decision. `UnknownSymbolError` and `DataAdapterUnavailable` are the two unrecoverable outcomes.
- **Rate limit hard wall: 20 req/min/IP.** `framework/data/ratelimit.py` implements a strict sliding-window limiter (not a token bucket — the SPEC's token bucket would admit 40 calls in the first 60 s). Every public adapter method is `@ratelimit`-wrapped; no call site may bypass.
- Proxy: on macOS, if ClashX/Surge is down AKShare fails with `ProxyError`. Set `AKSHARE_DIRECT=1` to patch `requests.Session.__init__` to `trust_env=False`. Default = leave the system proxy path intact.

### Persistence (`framework/persistence/`)

Single SQLite file at `data/app.db` (gitignored, WAL mode). Schema in `schema.sql` is hand-written and applied idempotently on startup via `ensure_schema`. No Alembic.

Two position tracks, two sets of tables — framework code must never cross-write them:
- **Real**: `real_trades` (user-entered). UI page 3 edits via `st.data_editor`.
- **Virtual**: `virtual_books` (one per `strategy_id`), `virtual_positions` (per-symbol qty + weighted-avg cost), `strategy_suggestions` (the 15:00 plan, one row per symbol). Runner writes suggestions; UI's "Apply all to virtual book" advances the book atomically.
- Activation guard (`framework/backtest/store.py::has_recent_run`): a strategy cannot be activated for the 15:30 schedule unless at least one backtest exists for it in the last 30 days.

### Scheduler (`framework/runner/scheduled_run.py`)

Hard rule (CAP-7): the runner **must not** be imported from inside the Streamlit UI process. The dashboard's "Run now" button launches it as a subprocess. The CLI exit code is meaningful (`Outcome` enum → POSIX exit code via `_outcome_to_exit_code`); cron treats non-zero as failure. Outcomes that do **not** trigger a non-zero exit: `NO_ACTIVE_STRATEGY`, `NOT_TRADING_DAY`.

Universe for a run comes from `strategies/<name>.state.json["universe"]` first, then the strategy class's `default_universe` attribute. Missing both → `NO_UNIVERSE` + non-zero exit (operator must fix config).

## Conventions and gotchas

- **One symbol per backtest invocation.** `.scratch/adx-bb-regime/spec.md` D2: the regime strategy assumes `ctx.universe[0]` is the contract. `BuyAndHoldStrategy` follows the same shape. Multi-symbol baskets are not basket-weighted; the batch runner just runs the engine once per universe.
- **`data/cache/` and `data/app.db` are gitignored.** So are `strategies/*.params.json` and `strategies/*.state.json`. Never commit those.
- **Atomic writes.** `framework/strategy/persistence.py::_atomic_write` and `framework/data/cache.py::write_cache` write to `<path>.tmp` then `os.replace` — never refactor to a non-atomic write or a cross-filesystem rename.
- **Streamlit caching.** Pages should not call `@st.cache_resource` on the SQLite connection — the AppTest harness wraps every script run in its own context. `framework/ui_runtime.py` uses `functools.lru_cache` instead, and exposes `_connect.cache_clear()` as a test seam.
- **Pyecharts render seam.** `framework/charts/` builds the chart and returns it; pages call `st_pyecharts(chart)` directly. We do **not** call `chart.render()` from inside framework code.
- **No "improvements" to the MVP backtest engine.** Price limits, slippage, partial fills, dividends, shorts are explicitly accepted as missing (see `backtest-engine.md` §"Matching assumptions"). A future production-grade engine is a separate code path; don't retrofit the MVP.
- **No live order routing, no auth in Streamlit.** Auth lives at the reverse proxy (Nginx/Caddy + basic auth + TLS). The Streamlit process binds plain HTTP to `0.0.0.0:8501`.
- **Decimal ETF symbols** (`510300`, `159915`, …) are strings, notints — keep the type stable across JSON, CLI argparse, and SQLite.

## Where to look when changing something

- Adding/changing a strategy class → `strategies/*.py` + `framework/strategy/discover.py` + `tests/test_adx_bb_regime_NN.py`.
- Adding an indicator → `framework/indicators/__init__.py` (add to `_INDICATORS`, implement, `__all__` is auto-generated).
- Changing the DB schema → append `CREATE TABLE/INDEX IF NOT EXISTS ...` to `framework/persistence/schema.sql`. No Alembic.
- Changing what the runner does → `framework/runner/scheduled_run.py`. Tests in `tests/test_runner_t6.py`.
- Changing UI behavior → `pages/N_*.py` + `framework/ui_runtime.py` (the only framework module Streamlit pages should import).
- New design contract → update `specs/spec-a-stock-quant/*.md` companion files first, then `CONTEXT.md` if new domain terms are introduced, then code.
- Architecture decisions → `docs/adr/NNNN-*.md` (existing: 0001 three-layer data-source, 0002 config surface, 0003 per-datatype routing, 0004 explicit fallback, 0005 shared cache with sidecar).

## Tests worth knowing about

- `tests/test_data_layer.py` — proves the rate limiter satisfies the 20/60 s invariant under burst, plus the EmptyBars/UnknownSymbol/DataAdapterUnavailable paths.
- `tests/test_strategy_t2.py` — pin the decoupling scan; `test_state_keys_not_clobbered_on_second_run` is the contract for hand-edited state files.
- `tests/test_adx_bb_regime_*.py` — one file per ticket (T02–T06, T09). T02 covers the StateMachine classifier, T03 the Hysteresis Gate, T04 the HalvedStage, T05 the Phased Exit, T06 the Range_Bull signal stack, T09 the zero-volatility regression (regression target: `511010`).
- `tests/test_run_batch_backtest_07.py` and `tests/test_compare_to_baseline_08.py` — pin the CLI contracts (default basket, exit codes, JSON shape).
- `tests/test_regime_zero_vol_regression.py` — `511010` (国债ETF) must not generate spurious signals.
