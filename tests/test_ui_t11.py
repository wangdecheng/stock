"""tests/test_ui_t11.py

End-to-end smoke for T11 dashboard additions:

  * Top-5 holdings + market value + today's P&L render in the
    dashboard when real positions exist.
  * The virtual-book equity curve renders when a backtest parquet is
    available, and shows a no-data caption when not.
  * The 立即运行当前策略 button is wired to a *subprocess* call to
    ``framework.runner.scheduled_run`` — not to an in-process import
    (per scheduler.md §"Hard rule"). We patch ``subprocess.run`` in
    Streamlit's ``app`` module to assert the command shape.
  * The 跑批 button stays disabled until the confirm checkbox is checked.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import framework.ui_runtime as ui_runtime
from framework.persistence import (
    ensure_schema,
    ensure_virtual_book,
    open_db,
    record_real_trade,
    set_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "app.db"
    original_default = ui_runtime.DEFAULT_DB_PATH
    ui_runtime._connect.cache_clear()
    monkeypatch.setattr(ui_runtime, "DEFAULT_DB_PATH", db_path)
    conn = open_db(str(db_path))
    ensure_schema(conn)
    yield conn
    conn.close()
    ui_runtime.DEFAULT_DB_PATH = original_default
    ui_runtime._connect.cache_clear()


@pytest.fixture
def fresh_conn(temp_db):
    return temp_db


@pytest.fixture
def boot():
    """Factory: returns a function that boots an AppTest against the
    given script path and asserts no exception."""
    def _boot(path: str) -> AppTest:
        at = AppTest.from_file(path, default_timeout=30)
        at.run()
        assert not at.exception, [
            f"{type(e.value).__name__}: {e.value}" for e in at.exception
        ]
        return at
    return _boot


def _seed_backtest(
    conn: sqlite3.Connection,
    *,
    strategy_id: str = "etf_rebalance",
    parquet_path: Path,
    initial_cash: float = 100_000.0,
) -> None:
    df = pd.DataFrame({
        "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(30)],
        "portfolio_value": [100_000.0 + i * 100 for i in range(30)],
        "benchmark_value": [100_000.0 + i * 50 for i in range(30)],
    })
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet_path)
    conn.execute(
        "INSERT INTO backtests (strategy_id, start, end, initial_cash, "
        "metrics_json, equity_path) VALUES (?, ?, ?, ?, ?, ?)",
        (strategy_id, "2024-01-01", "2024-01-30", initial_cash,
         json.dumps({"total_return": 0.03}), str(parquet_path.resolve())),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Dashboard — virtual-book equity curve
# ---------------------------------------------------------------------------


def test_dashboard_no_backtest_renders_no_data_caption(
    temp_db, fresh_conn: sqlite3.Connection, boot,
):
    """When the active strategy has no backtest row, the equity-curve
    section shows the no-data caption instead of crashing."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    at = boot("app.py")
    captions = [c.value for c in at.caption]
    assert any("尚未找到该策略的回测净值曲线" in c for c in captions), captions


def test_dashboard_with_backtest_renders_equity_curve(
    temp_db, fresh_conn: sqlite3.Connection, boot, tmp_path: Path,
):
    """When a backtest parquet exists, the dashboard renders an ECharts
    element for the equity curve (we assert the data-source caption —
    Streamlit-echarts renders via the iframe/echarts path which AppTest
    doesn't expose as a DOM element)."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    parquet = tmp_path / "data" / "backtests" / "etf.parquet"
    _seed_backtest(fresh_conn, parquet_path=parquet)
    # Force a fresh cache read in the AppTest worker thread by patching
    # the cached helper to return the synthetic frame directly.
    fake_equity = {
        "dates": [f"2024-01-{i + 1:02d}" for i in range(5)],
        "portfolio": [100_000.0 + i * 100 for i in range(5)],
        "benchmark": [100_000.0 + i * 50 for i in range(5)],
        "run_id": 1,
        "ran_at": "2024-01-30 12:00:00",
    }
    with patch("app._latest_equity_cached", return_value=fake_equity):
        at = boot("app.py")
    captions = [c.value for c in at.caption]
    assert any("数据源" in c for c in captions), captions
    # And the no-data caption should NOT be present.
    assert not any("尚未找到该策略的回测净值曲线" in c for c in captions), captions


def test_dashboard_equity_curve_handles_missing_parquet(
    temp_db, fresh_conn: sqlite3.Connection, boot, tmp_path: Path,
):
    """If the parquet path in the DB doesn't exist on disk, the dashboard
    surfaces the no-data caption rather than crashing."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    # Insert a row pointing at a path that doesn't exist
    fresh_conn.execute(
        "INSERT INTO backtests (strategy_id, start, end, initial_cash, "
        "metrics_json, equity_path) VALUES (?, ?, ?, ?, ?, ?)",
        ("etf_rebalance", "2024-01-01", "2024-01-30", 100_000.0,
         "{}", "/nonexistent/path/that/does/not/exist.parquet"),
    )
    fresh_conn.commit()
    at = boot("app.py")
    captions = [c.value for c in at.caption]
    assert any("尚未找到该策略的回测净值曲线" in c for c in captions), captions


# ---------------------------------------------------------------------------
# Dashboard — real-position market value + top-5
# ---------------------------------------------------------------------------


def test_dashboard_top5_and_market_value_with_real_position(
    temp_db, fresh_conn: sqlite3.Connection, boot,
):
    """With real positions in the journal, the dashboard surfaces
    总市值 + 今日浮动盈亏 + Top-5 holdings table."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    ensure_virtual_book(fresh_conn, "etf_rebalance")
    record_real_trade(
        fresh_conn, symbol="510300", side="buy",
        qty=1000, price=4.0, executed_at=date.today(),
    )
    record_real_trade(
        fresh_conn, symbol="600519", side="buy",
        qty=10, price=1800.0, executed_at=date.today(),
    )
    # AppTest runs the script in a different thread than the test, so
    # we patch BOTH helpers that touch the DB/network to avoid the
    # SQLite "created in a thread" guard and AKShare in test envs.
    fake_prices = {
        "510300": (4.5, "akshare"),
        "600519": (1820.0, "akshare"),
    }
    fake_fb = {
        "510300": {"price": 4.0, "executed_at": date.today()},
        "600519": {"price": 1800.0, "executed_at": date.today()},
    }
    with patch("app._fetch_latest_prices", return_value=fake_prices), \
         patch("app.latest_real_trades", return_value=fake_fb):
        at = boot("app.py")
    labels = [m.label for m in at.metric]
    # "现金余额" + "初始资金" from virtual-book card, "总市值" + "今日浮动盈亏"
    # from the new market-value block.
    assert "总市值" in labels, labels
    assert any("浮动盈亏" in lbl for lbl in labels), labels
    # Top-5 is rendered via st.markdown; AppTest surfaces it under at.markdown.
    md = [m.value for m in at.markdown]
    assert any("Top-5" in m for m in md), md


def test_dashboard_no_real_positions_no_market_value_block(
    temp_db, fresh_conn: sqlite3.Connection, boot,
):
    """Empty real_trades → no market-value / top-5 metrics, no crash."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    at = boot("app.py")
    labels = [m.label for m in at.metric]
    assert "总市值" not in labels, labels
    assert "今日浮动盈亏" not in labels, labels


# ---------------------------------------------------------------------------
# Dashboard — 跑批 button (subprocess wiring)
# ---------------------------------------------------------------------------


def test_dashboard_run_now_button_disabled_without_confirm(
    temp_db, fresh_conn: sqlite3.Connection, boot,
):
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    at = boot("app.py")
    run_btn = next(b for b in at.button if b.label == "立即运行当前策略")
    assert run_btn.disabled is True


def test_dashboard_run_now_spawns_subprocess(
    temp_db, fresh_conn: sqlite3.Connection, boot,
):
    """When the user confirms + clicks, app.py spawns a *subprocess*
    rather than importing the runner in-process. We assert the command
    shape matches the scheduler.md contract."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")

    fake_completed = MagicMock()
    fake_completed.returncode = 0
    fake_completed.stdout = "outcome=ran exit_code=0\n"
    fake_completed.stderr = ""

    with patch("app.subprocess.run", return_value=fake_completed) as run_mock:
        at = boot("app.py")
        # Check the confirm + click the button.
        cb = next(c for c in at.checkbox if "立即跑批" in c.label)
        cb.check()
        run_btn = next(b for b in at.button if b.label == "立即运行当前策略")
        run_btn.click()
        at.run()

    assert run_mock.called, "subprocess.run was not invoked"
    cmd = run_mock.call_args[0][0]
    # Command shape: [python, runner/__main__.py, --db-path, --strategies-dir, --strategy, ...]
    assert cmd[0] == "python" or cmd[0].endswith("python") or cmd[0].endswith("python3")
    assert any("framework/runner/__main__.py" in str(x) for x in cmd), cmd
    assert "--strategy" in cmd, cmd
    assert "etf_rebalance" in cmd, cmd
    # scheduler.md hard rule: timeout exists
    assert "timeout" in run_mock.call_args.kwargs, run_mock.call_args.kwargs
    assert run_mock.call_args.kwargs["timeout"] >= 60


def test_dashboard_run_now_subprocess_failure_shows_error(
    temp_db, fresh_conn: sqlite3.Connection, boot,
):
    """Subprocess failure surfaces as an error message; the dashboard
    does not crash."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    fake_completed = MagicMock()
    fake_completed.returncode = 1
    fake_completed.stdout = ""
    fake_completed.stderr = "AKShare failed: HTTPError"
    with patch("app.subprocess.run", return_value=fake_completed):
        at = boot("app.py")
        cb = next(c for c in at.checkbox if "立即跑批" in c.label)
        cb.check()
        run_btn = next(b for b in at.button if b.label == "立即运行当前策略")
        run_btn.click()
        at.run()
    errs = [e.value for e in at.error]
    assert any("运行失败" in e for e in errs), errs


# ---------------------------------------------------------------------------
# Sanity: active-strategy card still works
# ---------------------------------------------------------------------------


def test_dashboard_active_strategy_card_with_stale_backtest(
    temp_db, fresh_conn: sqlite3.Connection, boot, tmp_path: Path,
):
    """When the latest backtest ran > 24h ago, the header shows the
    stale-data warning."""
    set_config(fresh_conn, "active_strategy_id", "etf_rebalance")
    parquet = tmp_path / "data" / "backtests" / "stale.parquet"
    _seed_backtest(fresh_conn, parquet_path=parquet)
    # Force ran_at to 30 days ago so the > 24h check fires.
    fresh_conn.execute(
        "UPDATE backtests SET ran_at = datetime('now', '-30 days') "
        "WHERE strategy_id = 'etf_rebalance'",
    )
    fresh_conn.commit()
    at = boot("app.py")
    captions = [c.value for c in at.caption]
    assert any("数据陈旧" in c for c in captions), captions