"""tests/test_positions_t11.py

TDD cover for ``latest_real_trades`` — the dashboard's mark-to-market
fallback when today's bar isn't available yet (intraday render).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from framework.persistence import (
    open_db,
    record_real_trade,
)
from framework.persistence.db import ensure_schema
from framework.persistence.positions import latest_real_trades


@pytest.fixture
def conn(tmp_path: Path):
    c = open_db(str(tmp_path / "app.db"))
    ensure_schema(c)
    yield c
    c.close()


def test_empty_symbols_returns_empty(conn):
    assert latest_real_trades(conn, []) == {}


def test_no_trades_returns_empty(conn):
    assert latest_real_trades(conn, ["000001"]) == {}


def test_returns_most_recent_trade_per_symbol(conn):
    older = date.today() - timedelta(days=30)
    newer = date.today() - timedelta(days=1)
    record_real_trade(conn, symbol="000001", side="buy", qty=100, price=10.0, executed_at=older)
    record_real_trade(conn, symbol="000001", side="buy", qty=200, price=12.0, executed_at=newer)
    out = latest_real_trades(conn, ["000001"])
    assert out["000001"]["price"] == 12.0


def test_returns_multiple_symbols(conn):
    record_real_trade(
        conn, symbol="510300", side="buy", qty=100,
        price=4.0, executed_at=date.today(),
    )
    record_real_trade(
        conn, symbol="600519", side="buy", qty=10,
        price=1800.0, executed_at=date.today(),
    )
    out = latest_real_trades(conn, ["510300", "600519"])
    assert set(out.keys()) == {"510300", "600519"}
    assert out["510300"]["price"] == 4.0
    assert out["600519"]["price"] == 1800.0


def test_ignores_symbols_without_trades(conn):
    record_real_trade(
        conn, symbol="510300", side="buy", qty=100,
        price=4.0, executed_at=date.today(),
    )
    out = latest_real_trades(conn, ["510300", "000001"])
    assert set(out.keys()) == {"510300"}