"""framework/backtest/store.py

Write a backtest result (Equity + metrics) to the SQLite `backtests` table
and a parquet of the Equity time series. The activation guard in
``strategy-interface.md`` queries this table for "any run in last 30 days"
via ``has_recent_run(strategy_id, days=30)`` (CAP-3 prerequisite per spec).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from framework.backtest.engine import Equity


def save_run(
    conn: sqlite3.Connection,
    equity: Equity,
    metrics: dict,
    *,
    strategy_id: str,
    parquet_dir: Path | str,
) -> int:
    """Persist the backtest result. Returns the new ``backtests.id``.

    Strategy: write the parquet first (it's idempotent — if the SQLite row
    fails to commit, the worst case is an orphan parquet, easily pruned).
    Then insert the row referencing the parquet path.
    """
    parquet_dir = Path(parquet_dir)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    # 1) parquet — Equity time series for the UI to render the PnL curve
    df = pd.DataFrame({
        "date": [d.isoformat() for d in equity.dates],
        "portfolio_value": equity.portfolio_value,
        "benchmark_value": equity.benchmark_value,
        "cash": equity.cash,
    })
    parquet_path = parquet_dir / f"{strategy_id}__{equity.start.isoformat()}__{equity.end.isoformat()}__{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}.parquet"
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(parquet_path))

    # 2) SQLite row
    cur = conn.execute(
        """
        INSERT INTO backtests (strategy_id, start, end, initial_cash, metrics_json, equity_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            equity.start.isoformat(),
            equity.end.isoformat(),
            equity.initial_cash,
            json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            str(parquet_path),
        ),
    )
    return cur.lastrowid


def has_recent_run(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    days: int = 30,
) -> bool:
    """Activation guard. True iff at least one row for ``strategy_id`` was
    inserted within the last ``days`` days (per spec: default 30)."""
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """
        SELECT 1 FROM backtests
        WHERE strategy_id = ? AND ran_at >= ?
        LIMIT 1
        """,
        (strategy_id, cutoff),
    )
    return cur.fetchone() is not None


__all__ = ["save_run", "has_recent_run"]
