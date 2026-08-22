-- framework/persistence/schema.sql
--
-- Canonical schema for ``data/app.db`` (per
-- ``specs/spec-a-stock-quant/position-schema.md`` §"Database"). Hand-written
-- and applied idempotently on startup via
-- ``framework.persistence.db.ensure_schema`` — no Alembic for MVP.
--
-- Tables owned by each ticket:
--   T3 — backtests
--   T4 — real_trades, virtual_books, virtual_positions, strategy_suggestions
--   T5 — config (UI/runner key-value store; e.g. active_strategy_id)
--
-- Apply by calling ``framework.persistence.db.ensure_schema(conn)``.

-- =================================================================
-- Backtests (per backtest-engine.md §"Backtest storage")
-- =================================================================
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

CREATE INDEX IF NOT EXISTS idx_backtests_strategy_ran_at
  ON backtests (strategy_id, ran_at DESC);

-- ============================================================
-- Real track: user-entered trades (T4)
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

-- ============================================================
-- Generic key-value config (T5)
-- ============================================================
-- Lightweight store for UI/runner coordination. Key use: the dashboard's
-- "currently-active strategy" pointer and any T6 scheduler config that
-- needs to survive across processes. Values are JSON-encoded so callers
-- can stash strings, numbers, or small dicts.
CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,                     -- JSON-encoded
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);