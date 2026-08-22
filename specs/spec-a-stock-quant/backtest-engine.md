# Companion — Backtest Engine

This companion is part of `SPEC-a-stock-quant` (CAP-2). Locked in T2.

## Two files, one package

```
engine.py    # ~200 lines: event loop, broker stub, fill model, Equity emission
metrics.py   # ~50 lines: empyrical wrapper, returns dict for UI cards
```

Both are pure-Python with no side effects beyond the caller's `Equity` and SQLite writes.

## `Equity` object (emitted per run)

```python
@dataclass
class Equity:
    # Time series, daily resolution
    dates: list[date]
    portfolio_value: list[float]
    benchmark_value: list[float]               # parallel: same-day close of chosen benchmark (default 沪深300 = '000300')
    cash: list[float]

    # Per-fill record
    fills: list["Fill"]

    # Metadata
    strategy_name: str
    start: date
    end: date
    initial_cash: float
    final_value: float


@dataclass
class Fill:
    date: date                                 # fill date (next-day open after the signal bar)
    symbol: str
    side: str                                  # 'buy' | 'sell'
    qty: int
    price: float                               # open of fill date
    fee: float
```

## Event loop (canonical order)

For each bar date `t` from `start` to `end`:

1. **Sync state.** Build `Context` with positions / cash / portfolio_value as-of end-of `t-1`'s close; `state` from disk.
2. **Strategy generates.** Call `strategy.generate(ctx)` → `{symbol: target_weight}` (T3).
3. **Engine diffs.** Compute target dollars, target qty, delta qty, using today's close (last known value).
4. **Fill at next-day open.** Queue orders; fill them on `t+1`'s open. Orders queued on the last bar are **dropped** (no forward-equity leakage).
5. **Update virtual book.** Apply fills to positions + cash + portfolio value (per `position-schema.md` cost-basis).
6. **Append.** Push `portfolio_value`, `cash`, `benchmark_value` for date `t+1`.
7. **Persist if interactive.** Every N bars (default 20), write `state.json`.

Benchmark: `000300.SH` close on each day (CSI 300). Configurable later.

## Matching assumptions (simplified, MVP, locked)

| Concern | MVP behavior |
|---|---|
| Settlement | T+1 (next-day fill only) |
| Price-limit up (10% / 20%) | **Not modeled.** Order fills regardless. Surfaces as PnL error in extreme events. |
| Slippage | Zero |
| Partial fills | All-or-nothing (rounded shares only) |
| Lot size | Round down to A-share board lot for stocks (typically 100 shares); for ETFs and other 1-share-lot instruments, round to whole shares; fund symbols (15xxxx / 51xxxx) are not subject to lot rules — documented exceptions handled by instrument class |
| Commission | 0.0003 of cash flow on each fill; configurable in EngineConfig |
| Dividends / splits | Not applied. Adjustment factors carry historical splits; live dividends are ignored in PnL |
| Borrow / margin | None. Long-only. No short. |
| Trading calendar | `DataAdapter.get_calendar(start, end)` — skip non-trading days |

These are explicitly accepted limitations, not future-work. A future "production-grade" engine should be a separate code path; do not "improve" the MVP engine in place.

## Metrics (CAP-2 success criteria)

`metrics.py` is a thin wrapper over `empyrical`:

```python
def compute(equity: Equity, *, risk_free: float = 0.02) -> dict:
    returns = ...                             # daily simple returns from portfolio_value
    return {
        "total_return":     empyrical.cum_returns(returns).iloc[-1],
        "annualized":       empyrical.annual_return(returns),
        "sharpe":           empyrical.sharpe_ratio(returns, risk_free=risk_free),
        "max_drawdown":     empyrical.max_drawdown(returns),
        "calmar":           empyrical.calmar_ratio(returns),
        "win_rate":         _win_rate_from_fills(equity),
        "profit_loss_ratio": _pl_ratio_from_fills(equity),
    }
```

UI shows six cards; values must match a reference empyrical run on a synthetic dataset (test mandatory).

## Backtest storage (required for activation guard)

```sql
CREATE TABLE IF NOT EXISTS backtests (
  id INTEGER PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  start DATE NOT NULL,
  end DATE NOT NULL,
  initial_cash REAL NOT NULL,
  metrics_json TEXT NOT NULL,                 -- result of compute()
  equity_path TEXT NOT NULL,                  -- path to parquet with Equity time series
  ran_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

The activation guard in `strategy-interface.md` queries this for "any run in last 30 days for this strategy_id".

## UI integration

- Inputs: select strategy (from registered list), start date, end date, initial cash.
- Run button → fills the form, kicks off the backtest on a background thread (Streamlit `st.spinner`), writes result row, renders:
  - PnL curve (Pyecharts line, portfolio_value vs benchmark_value)
  - Six metric cards (Streamlit `st.metric`)
  - Fills table (Streamlit `st.dataframe`)

A run that fails (exception in `generate` / data miss) writes `error` to a `backtests.status` column (nullable) and the UI surfaces the exception traceback in an `st.error`.
