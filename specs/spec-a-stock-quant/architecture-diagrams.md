# Companion — Architecture Diagrams

This companion is part of `SPEC-a-stock-quant`. Per Spec Law: diagrams always live in a companion.

## Module map

```
                          ┌──────────────────────────────┐
                          │  Nginx/Caddy (reverse proxy) │
                          │  basic-auth  +  HTTPS        │
                          └──────────────┬───────────────┘
                                         │  http://localhost:8501
                                         ▼
                          ┌──────────────────────────────┐
                          │  Streamlit (app.py + pages/) │
                          │  - 5 pages                   │
                          │  - Pyecharts rendering       │
                          └──────────────┬───────────────┘
                                         │
              ┌──────────────────────────┼────────────────────────────┐
              ▼                          ▼                            ▼
       ┌─────────────┐          ┌──────────────────┐         ┌─────────────────┐
       │  DataAdapter│          │ Strategy loader /│         │ Engine (backtest │
       │  (AKShare)  │          │  runner (cron)   │         │  + scheduled)   │
       └──────┬──────┘          └────────┬─────────┘         └────────┬────────┘
              │                          │                            │
              ▼                          ▼                            ▼
       ┌─────────────┐          ┌──────────────────┐         ┌─────────────────┐
       │parquet cache│          │ strategies/*.py  │         │  indicators/    │
       └─────────────┘          │ strategies/*.json│         │  metrics/       │
                                 └──────────────────┘         └─────────────────┘
                                         │                            │
                                         ▼                            ▼
                                  ┌─────────────────────────────────────┐
                                  │      SQLite: data/app.db           │
                                  │  real_trades · virtual_books ·      │
                                  │  virtual_positions ·               │
                                  │  strategy_suggestions ·             │
                                  │  backtests · config                 │
                                  └─────────────────────────────────────┘
```

## Backtest data flow (per CAP-2)

```
[User clicks Run on /回测]
        │
        ▼
[Read strategy.params.json] ─── instantiate Strategy ─── ctx = Context.from_book(virtual_books)
        │                                                          │
        ▼                                                          ▼
[engine.event_loop()] ◄──────────────── ctx.bars(symbol, lookback) via DataAdapter
        │                                                          │
        │ daily:                                                   ▼
        │   strategy.generate(ctx) → {symbol: weight}       [AKShare daily bars]
        │   engine.diff → orders queued
        │   next-day open → fill (writes virtual_positions/cash if live, Equity if backtest)
        ▼
[empyrical.compute(equity)] → metrics dict
        │
        ▼
[Write backtests row] → render PnL curve + 6 metric cards
```

## Scheduled run data flow (per CAP-7)

```
[15:30 cron] → python -m runner.scheduled_run
        │
        ▼
[config.active_strategy_id]
        │
        ▼
[DataAdapter.get_calendar(today)] ─── if not trading day → exit 0
        │
        ▼
[strategy.generate(ctx)] ─── 3x exponential-backoff retry on data failures
        │
        ▼
[Write strategy_suggestions rows] ─── success
                                     └── failure → sentinel row + exit 1
```

## Strategy discovery (per CAP-3)

```
[Startup / "Refresh strategies" click]
        │
        ▼
[for each *.py in strategies/]:
   1. importlib.import_module (cached)
   2. inspect source: reject if forbidden imports
   3. find classes with class-attr `name: str` and `generate(self, ctx)` method
   4. register in global registry
        │
        ▼
[UI dropdown]  [Engine backtest picker]  [Runner active picker]
```

## File layout (canonical)

```
/opt/app/                          ← assumed deploy root
├── app.py                         ← Page 1
├── pages/
│   ├── 1_股票详情.py
│   ├── 2_策略管理.py
│   ├── 3_持仓管理.py
│   └── 4_回测.py
├── framework/                     ← framework internals (forbidden in strategies/*)
│   ├── data/adapter.py            ← CAP-1
│   ├── engine.py                  ← CAP-2
│   ├── metrics/__init__.py        ← indicators-and-metrics.md
│   ├── indicators/__init__.py     ← indicators-and-metrics.md
│   ├── strategy/
│   │   ├── base.py                ← CAP-3 + strategy-interface.md
│   │   ├── context.py
│   │   ├── discover.py
│   │   └── persistence.py         ← params.json / state.json
│   ├── persistence/
│   │   ├── schema.sql
│   │   └── db.py                  ← SQLite WAL open
│   ├── backtest/
│   │   └── store.py               ← backtests table
│   └── runner/
│       └── scheduled_run.py       ← CAP-7
├── strategies/                    ← user-editable; gitignored aside from .py
│   ├── ma_cross.py
│   ├── ma_cross.params.json       ← gitignored
│   ├── ma_cross.state.json        ← gitignored
│   ├── etf_rebalance.py
│   └── ...
├── data/
│   ├── app.db                     ← gitignored
│   └── cache/
│       └── <symbol>/...parquet    ← gitignored
├── pyproject.toml / requirements.txt
└── .gitignore                     ← includes *.state.json, *.params.json, data/, __pycache__/
```
