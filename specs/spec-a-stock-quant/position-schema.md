# Companion — Position Schema

This companion is part of `SPEC-a-stock-quant` (CAP-5, CAP-7). Locked in T6.

## Two tracks, two sets of tables

**Real track** = what the user actually holds. Edited manually.

**Virtual track** = per-strategy simulated books; each strategy_id has its own `(cash, positions, suggestions)` triple.

Framework code must never write to the real track on behalf of a strategy run, and must never read the real track to influence a virtual-book decision. If you find yourself wanting to do either, that's a bug, not a feature.

## Database

- Engine: **SQLite** (single file, WAL mode acceptable).
- Location: `data/app.db` (gitignored).
- Migration strategy: hand-written `schema.sql` run on startup; new tables / columns added by appending `IF NOT EXISTS` blocks. No Alembic for MVP.

## Tables

```sql
-- ============================================================
-- Real track: user-entered trades
-- ============================================================
CREATE TABLE IF NOT EXISTS real_trades (
  id INTEGER PRIMARY KEY,
  symbol TEXT NOT NULL,                 -- '600519', '510300' (any AKShare identifier)
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  qty INTEGER NOT NULL CHECK (qty > 0),
  price REAL NOT NULL CHECK (price > 0),
  fee REAL NOT NULL DEFAULT 0,
  executed_at DATE NOT NULL,
  note TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_real_trades_symbol_date
  ON real_trades (symbol, executed_at DESC);

-- ============================================================
-- Virtual track: per-strategy book (one row per strategy_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS virtual_books (
  strategy_id TEXT PRIMARY KEY,         -- 'ma_cross' derived from strategies/ma_cross.py
  initial_cash REAL NOT NULL DEFAULT 100000,   -- configurable in UI
  cash REAL NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Virtual track: per-(strategy, symbol) position
-- ============================================================
CREATE TABLE IF NOT EXISTS virtual_positions (
  id INTEGER PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty >= 0),
  avg_cost REAL NOT NULL,               -- weighted-average cost
  opened_at DATE NOT NULL,
  FOREIGN KEY (strategy_id) REFERENCES virtual_books(strategy_id) ON DELETE CASCADE,
  UNIQUE (strategy_id, symbol)
);

-- ============================================================
-- Virtual track: per-strategy daily suggestion (action plan)
-- ============================================================
CREATE TABLE IF NOT EXISTS strategy_suggestions (
  id INTEGER PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold')),
  target_qty INTEGER,                   -- target position qty after this action
  target_price REAL,                    -- suggested price (strategy hint, not fill)
  confidence REAL,                      -- 0-1; nullable
  reason TEXT,                          -- strategy output in human-readable form
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied BOOLEAN NOT NULL DEFAULT 0,    -- 1 iff user clicked "应用到虚拟账本"
  applied_at TIMESTAMP,
  FOREIGN KEY (strategy_id) REFERENCES virtual_books(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_suggestions_active
  ON strategy_suggestions (strategy_id, generated_at DESC, applied);
```

## Schema decisions and the rationale

| Decision | Rationale (T6) |
|---|---|
| `virtual_books.strategy_id` is the filename stem | One-to-one identity with the strategy file under `strategies/`; stable across restarts and re-imports |
| Weighted-average cost (`avg_cost`) | T6 sub-decision — multi-lot buys merge into a single cost basis for PnL |
| `strategy_suggestions.applied` defaults to 0 | Suggestions are not auto-executed; user opts in via dashboard |
| No FK from `real_trades` to anything | Real track is a journal; positions are derived downstream |
| `note`, `reason`, `confidence` are nullable free-text | Strategies vary widely in how much justification they emit |
| `CHECK` constraints on side / action enums | Fail fast at write time rather than carry garbage downstream |

## Cost-basis algorithm (mandatory)

Multi-lot buy:

```
new_avg = (old_qty * old_avg + new_qty * new_price + new_fee) / (old_qty + new_qty)
new_qty = old_qty + new_qty
```

Multi-lot sell:

```
qty decreases; avg_cost stays unchanged; realized PnL = (sell_price - avg_cost) * sold_qty - fee
```

These two formulas are referenced by both the backtest engine (CAP-2 / `backtest-engine.md`) and the "apply suggestion" UI action (CAP-5). Treat them as the **one** implementation; do not fork the algorithm.

## Real position aggregate (derived; not stored)

The `real_trades` table alone holds truth. A view (or ad-hoc query) computes:

```
real_position(symbol) = SUM(qty * side_sign) GROUP BY symbol
real_avg_cost(symbol) = SUM(qty*price) / SUM(qty * side_sign)  -- for longs only
```

The dashboard reads this each render. Do not denormalize into a separate table for MVP.

## "Apply suggestions to virtual book" — atomicity contract

When the user clicks "Apply" on `strategy_suggestions`:

1. Open SQLite transaction.
2. Verify `applied = 0` on every row being applied.
3. Update `virtual_books.cash` and `virtual_positions` per each row's (action, target_qty, target_price) using the cost-basis algorithm.
4. Set `applied = 1`, `applied_at = now`.
5. Commit.

If any row's verification step fails, the whole batch rolls back. Half-applied books are forbidden.

## Out-of-scope (do not add tables for these)

- No `users` table (single user — auth at proxy).
- No `audit_log` table (MVP — may add later; out of MVP).
- No `instruments` / `symbols` master table. Symbol strings are passed through; AKShare is the canonical dictionary.
- No FX / multi-currency columns. A-shares trade in CNY only.
