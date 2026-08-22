"""framework/persistence/positions.py

Derived aggregations over the ``real_trades`` journal. Per
``specs/spec-a-stock-quant/position-schema.md`` §"Real position aggregate
(derived; not stored)":

    real_position(symbol) = SUM(qty * side_sign)            GROUP BY symbol
    real_avg_cost(symbol) = SUM(qty*price) / SUM(qty * side_sign)   -- longs only

These are NOT materialized into a separate table — the dashboard reads
them each render (MVP simplification). Keeping the SQL here (rather than
in repo.py) makes the "derived, not stored" intent obvious to readers.

Returns plain ints / floats / dicts — no framework-internal types — so
this module stays importable from anywhere without dragging the rest of
``framework`` along.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Optional

from framework.persistence.repo import _parse_date


# ---------------------------------------------------------------------------
# Single-symbol aggregates
# ---------------------------------------------------------------------------


def real_position(conn: sqlite3.Connection, symbol: str) -> int:
    """Net qty for ``symbol`` (buys positive, sells negative).

    Zero means the user has fully closed the position (or never held it).
    Negative net qty is technically possible if a user records sells
    before buys (e.g. data-entry mistake), but A-share cash accounts can't
    short — a negative net usually means the journal is wrong.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN side = 'buy' THEN qty ELSE -qty END), 0)
        FROM real_trades
        WHERE symbol = ?
        """,
        (symbol,),
    ).fetchone()
    return int(row[0])


def real_avg_cost(conn: sqlite3.Connection, symbol: str) -> Optional[float]:
    """Weighted-average cost for ``symbol`` *iff* the net position is long.

    Returns ``None`` when the net is zero or short — the spec formula
    ``SUM(qty*price) / SUM(qty * side_sign)`` is only meaningful for
    longs (denominator must be > 0). The formula uses
    ``qty * price`` on the buy side (per spec) — fees are NOT included
    in the real-track cost basis (they are an explicit column on each
    trade row and can be summed separately if the UI wants to show total
    fees paid).

    For an A-share cash account the spec assumes net qty ≥ 0; negative
    net is treated as "no avg cost available".
    """
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN side = 'buy' THEN qty ELSE 0 END), 0) AS buy_qty,
            COALESCE(SUM(CASE WHEN side = 'sell' THEN qty ELSE 0 END), 0) AS sell_qty,
            COALESCE(SUM(CASE WHEN side = 'buy' THEN qty * price ELSE 0 END), 0) AS buy_notional
        FROM real_trades
        WHERE symbol = ?
        """,
        (symbol,),
    ).fetchone()
    buy_qty, sell_qty, buy_notional = row
    net = int(buy_qty) - int(sell_qty)
    if net <= 0:
        return None
    return float(buy_notional) / net


# ---------------------------------------------------------------------------
# Bulk read (dashboard render)
# ---------------------------------------------------------------------------


def list_real_positions(conn: sqlite3.Connection) -> list[dict]:
    """All symbols with non-zero net qty, with their weighted-average cost.

    Sorted by symbol for stable UI rendering. Used by the dashboard to
    render the "持仓" panel (CAP-5); runs each render rather than
    maintaining a materialized view (position-schema.md explicitly
    forbids denormalization for MVP).
    """
    rows = conn.execute(
        """
        SELECT
            symbol,
            SUM(CASE WHEN side = 'buy' THEN qty ELSE -qty END) AS net_qty,
            SUM(CASE WHEN side = 'buy' THEN qty * price ELSE 0 END) AS buy_notional,
            SUM(CASE WHEN side = 'buy' THEN qty ELSE 0 END) AS buy_qty,
            SUM(CASE WHEN side = 'sell' THEN qty ELSE 0 END) AS sell_qty
        FROM real_trades
        GROUP BY symbol
        HAVING net_qty != 0
        ORDER BY symbol
        """
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        net = int(r[1])
        avg_cost: Optional[float] = float(r[2]) / net if net > 0 else None
        out.append({
            "symbol": r[0],
            "qty": net,
            "avg_cost": avg_cost,
            "buy_qty": int(r[3]),
            "sell_qty": int(r[4]),
        })
    return out


# ---------------------------------------------------------------------------
# Latest trade lookup (dashboard market-value / today's P&L)
# ---------------------------------------------------------------------------


def latest_real_trades(
    conn: sqlite3.Connection,
    symbols: list[str],
) -> dict[str, dict]:
    """For each symbol, return the most recent real-trade row's
    ``(price, executed_at)``.

    Used by the dashboard's market-value helper to mark-to-market a
    position when today's bar hasn't been fetched yet (intraday render)
    — i.e. it falls back to the last fill price. Returns a dict keyed by
    symbol; symbols with no trades are omitted from the result.
    """
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"""
        SELECT symbol, price, executed_at FROM real_trades
        WHERE symbol IN ({placeholders})
          AND id IN (
            SELECT MAX(id) FROM real_trades
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
          )
        """,
        [*symbols, *symbols],
    ).fetchall()
    return {
        r[0]: {"price": float(r[1]), "executed_at": _parse_date(r[2])}
        for r in rows
    }


__all__ = [
    "real_position",
    "real_avg_cost",
    "list_real_positions",
    "latest_real_trades",
]