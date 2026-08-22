"""tests/test_runner_t6.py

Contract tests for T6 (the 15:30 scheduler / runner). Spec:
``specs/spec-a-stock-quant/scheduler.md``.

Seams covered:
  * diff_signal — pure signal→suggestion diff; the backtest engine's
                  weight→qty algorithm ported to "produce a suggestion row"
                  instead of "produce a fill".
  * load_active_strategy_id — config table read.
  * run_once — full orchestrator with mocked adapter and a fake strategy:
                  * NO_ACTIVE_STRATEGY when config is empty
                  * NOT_TRADING_DAY when today is not in the calendar
                  * DATA_UNAVAILABLE → sentinel row + non-zero outcome
                  * STRATEGY_EXCEPTION → sentinel row + non-zero outcome
                  * happy path: writes one row per (symbol, action) with
                    correct (action, target_qty, target_price) into
                    strategy_suggestions; applied = 0.
  * main() CLI: ``--strategy NAME`` overrides the config-table active
    pointer; missing strategy id → exit code 2 (config error).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    """Fresh on-disk DB at ``tmp_path/app.db`` with schema bootstrapped.
    Returns the db path so tests can pass it to the runner explicitly (the
    runner must NOT depend on ui_runtime — it must take an explicit db_path
    so it runs as a standalone process per CAP-7)."""
    db_path = tmp_path / "app.db"
    from framework.persistence import ensure_schema, open_db
    conn = open_db(db_path)
    ensure_schema(conn)
    conn.close()
    return db_path


@pytest.fixture
def isolated_strategies(tmp_path: Path, monkeypatch):
    """A temp strategies/ package so test strategies don't pollute the real
    ``strategies/`` directory. Mirrors the fixture in test_strategy_t2.py."""
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "__init__.py").write_text("", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    for mod_name in list(sys.modules):
        if mod_name == "strategies" or mod_name.startswith("strategies."):
            del sys.modules[mod_name]
    yield strategies_dir
    for mod_name in list(sys.modules):
        if mod_name == "strategies" or mod_name.startswith("strategies."):
            del sys.modules[mod_name]
    sys.path.remove(str(tmp_path))


def _write_strategy(strategies_dir: Path, name: str, body: str) -> Path:
    """Write ``strategies_dir/<name>.py``. ``name`` is the stem (no .py)."""
    stem = name.removesuffix(".py")
    p = strategies_dir / f"{stem}.py"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------


@dataclass
class FakeAdapter:
    """Minimal stand-in for AKShareAdapter. Tests program the responses
    before invoking the runner; the runner should never touch real AKShare."""

    calendar: list[date] = field(default_factory=list)
    bars_by_symbol: dict[str, Any] = field(default_factory=dict)
    # Force an exception from get_bars (simulates network outage).
    raise_on_bars: Optional[Exception] = None
    raise_on_calendar: Optional[Exception] = None

    def get_calendar(self, start: date, end: date) -> list[date]:
        if self.raise_on_calendar is not None:
            raise self.raise_on_calendar
        return [d for d in self.calendar if start <= d <= end]

    def get_bars(self, symbol, start, end, adj="qfq", frequency="daily", **kwargs):
        if self.raise_on_bars is not None:
            raise self.raise_on_bars
        if symbol in self.bars_by_symbol:
            return self.bars_by_symbol[symbol]
        raise LookupError(f"no bars programmed for {symbol}")


# ---------------------------------------------------------------------------
# Seam 1 — diff_signal (pure)
# ---------------------------------------------------------------------------


def test_diff_signal_buy_increases_position_when_weight_positive():
    from framework.runner.scheduled_run import diff_signal

    # 100% in 510300 at price 5.0 → portfolio 100,000 → target 20,000 shares
    actions = diff_signal(
        signal={"510300": 1.0},
        current_qty={},                 # no current position
        closes={"510300": 5.0},
        portfolio_value=100_000.0,
    )
    assert len(actions) == 1
    a = actions[0]
    assert a["symbol"] == "510300"
    assert a["action"] == "buy"
    assert a["target_qty"] == 20_000
    assert a["target_price"] == 5.0


def test_diff_signal_sell_reduces_position_when_weight_zero():
    from framework.runner.scheduled_run import diff_signal

    # Currently hold 1000 of 510300; signal says 0% → sell all.
    actions = diff_signal(
        signal={"510300": 0.0},
        current_qty={"510300": 1000},
        closes={"510300": 5.0},
        portfolio_value=100_000.0,
    )
    assert len(actions) == 1
    a = actions[0]
    assert a["action"] == "sell"
    assert a["target_qty"] == 0


def test_diff_signal_skips_when_signal_dict_is_empty():
    """Empty signal → no rows. Strategy said 'no action'."""
    from framework.runner.scheduled_run import diff_signal

    actions = diff_signal(
        signal={},
        current_qty={"510300": 1000},
        closes={"510300": 5.0},
        portfolio_value=100_000.0,
    )
    assert actions == []


def test_diff_signal_skips_symbols_missing_a_close():
    """Symbol in signal but no close today → drop silently (the runner
    already logged the data gap; UI shows '数据延迟'). No garbage rows."""
    from framework.runner.scheduled_run import diff_signal

    actions = diff_signal(
        signal={"510300": 1.0, "513500": 0.5},
        current_qty={},
        closes={"510300": 5.0},                 # 513500 missing
        portfolio_value=100_000.0,
    )
    assert [a["symbol"] for a in actions] == ["510300"]


def test_diff_signal_drops_nonpositive_weight_for_unheld_symbol():
    """Strategy returned a symbol with weight 0 but the runner does not
    currently hold it. No action needed — no row."""
    from framework.runner.scheduled_run import diff_signal

    actions = diff_signal(
        signal={"510300": 0.0},
        current_qty={},                          # don't hold it
        closes={"510300": 5.0},
        portfolio_value=100_000.0,
    )
    assert actions == []


def test_diff_signal_holds_action_emitted_when_diff_is_zero_but_signal_lists_symbol():
    """When the strategy explicitly lists a symbol at non-zero weight but
    the target qty equals the current qty exactly, emit an action='hold'
    row so the audit log records that the strategy considered it."""
    from framework.runner.scheduled_run import diff_signal

    # 50% of 100k at price 5 → exactly 10,000 shares (no rounding). Already
    # hold 10,000.
    actions = diff_signal(
        signal={"510300": 0.5},
        current_qty={"510300": 10_000},
        closes={"510300": 5.0},
        portfolio_value=100_000.0,
    )
    assert len(actions) == 1
    assert actions[0]["action"] == "hold"
    assert actions[0]["target_qty"] == 10_000


def test_diff_signal_floors_share_quantities():
    """target_qty = int(weight * portfolio_value // close) per the backtest
    engine's algorithm. Fractional shares are forbidden (A-share lot size)."""
    from framework.runner.scheduled_run import diff_signal

    # 0.3333 * 100_000 = 33,333.33 → / 5.0 = 6,666.66 → floor to 6,666.
    actions = diff_signal(
        signal={"510300": 0.3333},
        current_qty={},
        closes={"510300": 5.0},
        portfolio_value=100_000.0,
    )
    assert actions[0]["target_qty"] == 6_666


# ---------------------------------------------------------------------------
# Seam 2 — load_active_strategy_id
# ---------------------------------------------------------------------------


def test_load_active_strategy_id_returns_none_when_unset(temp_db: Path):
    from framework.runner.scheduled_run import load_active_strategy_id
    assert load_active_strategy_id(temp_db) is None


def test_load_active_strategy_id_reads_config_value(temp_db: Path):
    from framework.persistence import set_config
    from framework.runner.scheduled_run import load_active_strategy_id

    conn = sqlite3.connect(str(temp_db))
    set_config(conn, "active_strategy_id", "etf_rebalance")
    conn.commit()
    conn.close()
    assert load_active_strategy_id(temp_db) == "etf_rebalance"


# ---------------------------------------------------------------------------
# Seam 3 — run_once orchestrator (happy path + failure modes)
# ---------------------------------------------------------------------------


# A trivial strategy that the runner can drive without any indicator / adapter
# coupling — the Context.adapter is set but the strategy ignores it.
_STRATEGY_BUY_ONE = textwrap.dedent(
    """
    from framework.strategy import Context

    class BuyOne:
        name = "buy_one"
        default_universe = ["510300"]

        def __init__(self):
            pass

        def generate(self, ctx: Context):
            return {"510300": 1.0}
    """
).strip() + "\n"


_STRATEGY_THROWS = textwrap.dedent(
    """
    from framework.strategy import Context

    class Boom:
        name = "boom"
        default_universe = ["510300"]

        def __init__(self):
            pass

        def generate(self, ctx: Context):
            raise RuntimeError("strategy exploded")
    """
).strip() + "\n"


_STRATEGY_NO_UNIVERSE = textwrap.dedent(
    """
    from framework.strategy import Context

    class NoUniverse:
        name = "no_universe"

        def __init__(self):
            pass

        def generate(self, ctx: Context):
            return {"510300": 1.0}
    """
).strip() + "\n"


def _seed_virtual_book(
    db_path: Path,
    *,
    strategy_id: str = "buy_one",
    cash: float = 100_000.0,
    positions: dict[str, tuple[int, float]] | None = None,
):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO virtual_books (strategy_id, initial_cash, cash) "
        "VALUES (?, ?, ?)",
        (strategy_id, cash, cash),
    )
    for sym, (qty, avg_cost) in (positions or {}).items():
        conn.execute(
            "INSERT INTO virtual_positions "
            "  (strategy_id, symbol, qty, avg_cost, opened_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (strategy_id, sym, qty, avg_cost, date(2024, 1, 1).isoformat()),
        )
    conn.commit()
    conn.close()


def _active_strategy(db_path: Path, name: str):
    conn = sqlite3.connect(str(db_path))
    from framework.persistence import set_config
    set_config(conn, "active_strategy_id", name)
    conn.commit()
    conn.close()


def _adapter_with_close(symbol: str, close: float, today: date) -> FakeAdapter:
    import pandas as pd
    df = pd.DataFrame([{
        "date": today, "open": close, "high": close,
        "low": close, "close": close, "volume": 0, "amount": 0.0,
    }])
    return FakeAdapter(
        calendar=[today],
        bars_by_symbol={
            symbol: type("R", (), {"df": df, "stale_seconds": 0, "cache_hit": False})(),
        },
    )


def test_run_once_no_active_strategy_returns_no_active_strategy_outcome(
    temp_db: Path, isolated_strategies: Path,
):
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    out = run_once(
        db_path=temp_db,
        strategies_dir=isolated_strategies,
        adapter=FakeAdapter(calendar=[date.today()]),
    )
    assert out == Outcome.NO_ACTIVE_STRATEGY
    # Nothing written.
    conn = sqlite3.connect(str(temp_db))
    n = conn.execute("SELECT COUNT(*) FROM strategy_suggestions").fetchone()[0]
    conn.close()
    assert n == 0


def test_run_once_non_trading_day_returns_not_trading_day_outcome(
    temp_db: Path, isolated_strategies: Path,
):
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    _write_strategy(isolated_strategies, "buy_one.py", _STRATEGY_BUY_ONE)
    _active_strategy(temp_db, "buy_one")

    # Calendar has NO entry for today → NOT_TRADING_DAY.
    out = run_once(
        db_path=temp_db,
        strategies_dir=isolated_strategies,
        adapter=FakeAdapter(calendar=[]),
        today=date(2024, 3, 16),                 # a Saturday
    )
    assert out == Outcome.NOT_TRADING_DAY

    conn = sqlite3.connect(str(temp_db))
    n = conn.execute("SELECT COUNT(*) FROM strategy_suggestions").fetchone()[0]
    conn.close()
    assert n == 0


def test_run_once_happy_path_writes_one_buy_suggestion(
    temp_db: Path, isolated_strategies: Path,
):
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    _write_strategy(isolated_strategies, "buy_one.py", _STRATEGY_BUY_ONE)
    _active_strategy(temp_db, "buy_one")
    today = date(2024, 3, 15)                     # a Friday in the calendar
    _seed_virtual_book(temp_db, strategy_id="buy_one", cash=100_000.0)

    adapter = _adapter_with_close("510300", close=5.0, today=today)

    out = run_once(
        db_path=temp_db,
        strategies_dir=isolated_strategies,
        adapter=adapter,
        today=today,
    )

    assert out == Outcome.RAN, "happy path should report RAN"

    conn = sqlite3.connect(str(temp_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM strategy_suggestions ORDER BY id"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    r = rows[0]
    assert r["strategy_id"] == "buy_one"
    assert r["symbol"] == "510300"
    assert r["action"] == "buy"
    # 1.0 * 100,000 / 5.0 = 20,000 shares
    assert r["target_qty"] == 20_000
    assert r["target_price"] == pytest.approx(5.0)
    assert r["applied"] == 0
    # No `reason` provided by the strategy → NULL.
    assert r["reason"] is None or r["reason"] == ""
    assert r["generated_at"] is not None


def test_run_once_holds_action_emitted_when_already_at_target(
    temp_db: Path, isolated_strategies: Path,
):
    """Strategy wants 50% in 510300 at price 5; current qty already 10,000
    → diff is zero → write a hold row, NOT nothing (audit trail).

    The book is seeded so portfolio_value = cash + market_value = 100,000
    exactly: cash=50,000 and 10,000 shares at ¥5 = ¥50,000 market value.
    That way 50% target × 100,000 / ¥5 = 10,000 shares = current qty."""
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    strategy = textwrap.dedent(
        """
        from framework.strategy import Context
        class Half:
            name = "half"
            default_universe = ["510300"]
            def __init__(self):
                pass
            def generate(self, ctx):
                return {"510300": 0.5}
        """
    ).strip() + "\n"
    _write_strategy(isolated_strategies, "half.py", strategy)
    _active_strategy(temp_db, "half")

    today = date(2024, 3, 15)
    _seed_virtual_book(
        temp_db, strategy_id="half",
        cash=50_000.0,
        positions={"510300": (10_000, 5.0)},
    )
    adapter = _adapter_with_close("510300", close=5.0, today=today)

    out = run_once(
        db_path=temp_db, strategies_dir=isolated_strategies,
        adapter=adapter, today=today,
    )
    assert out == Outcome.RAN

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT * FROM strategy_suggestions"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    sym, action, qty, price = rows[0][2], rows[0][3], rows[0][4], rows[0][5]
    assert (sym, action, qty, price) == ("510300", "hold", 10_000, 5.0)


def test_run_once_strategy_exception_emits_sentinel(
    temp_db: Path, isolated_strategies: Path,
):
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    _write_strategy(isolated_strategies, "boom.py", _STRATEGY_THROWS)
    _active_strategy(temp_db, "boom")
    today = date(2024, 3, 15)

    out = run_once(
        db_path=temp_db,
        strategies_dir=isolated_strategies,
        adapter=_adapter_with_close("510300", close=5.0, today=today),
        today=today,
    )
    assert out == Outcome.STRATEGY_EXCEPTION

    conn = sqlite3.connect(str(temp_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM strategy_suggestions"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    r = rows[0]
    # Sentinel: action='hold' (no real action), no target qty/price; reason
    # surfaces the failure mode.
    assert r["action"] == "hold"
    assert r["target_qty"] is None
    assert r["target_price"] is None
    assert "strategy_exception" in (r["reason"] or "")
    assert "exploded" in (r["reason"] or "")


def test_run_once_data_unavailable_emits_sentinel(
    temp_db: Path, isolated_strategies: Path,
):
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    _write_strategy(isolated_strategies, "buy_one.py", _STRATEGY_BUY_ONE)
    _active_strategy(temp_db, "buy_one")
    today = date(2024, 3, 15)

    adapter = FakeAdapter(
        calendar=[today],
        raise_on_bars=RuntimeError("network down"),
    )

    out = run_once(
        db_path=temp_db,
        strategies_dir=isolated_strategies,
        adapter=adapter,
        today=today,
    )
    assert out == Outcome.DATA_UNAVAILABLE

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT * FROM strategy_suggestions"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][3] == "hold"
    assert "data_unavailable" in (rows[0][7] or "")


def test_run_once_no_universe_emits_sentinel(
    temp_db: Path, isolated_strategies: Path,
):
    """Strategy does not declare default_universe AND state.json has no
    'universe' key → runner can't price anything → sentinel."""
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    _write_strategy(isolated_strategies, "no_universe.py", _STRATEGY_NO_UNIVERSE)
    _active_strategy(temp_db, "no_universe")
    today = date(2024, 3, 15)

    out = run_once(
        db_path=temp_db,
        strategies_dir=isolated_strategies,
        adapter=_adapter_with_close("510300", close=5.0, today=today),
        today=today,
    )
    assert out == Outcome.NO_UNIVERSE

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT * FROM strategy_suggestions"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert "no_universe" in (rows[0][7] or "")


def test_run_once_universe_can_be_overridden_via_state_json(
    temp_db: Path, isolated_strategies: Path,
):
    """state.json['universe'] wins over the class-level default."""
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    strategy = textwrap.dedent(
        """
        from framework.strategy import Context
        class Override:
            name = "override"
            default_universe = ["510300"]
            def __init__(self):
                pass
            def generate(self, ctx):
                # Return equal weights for whatever universe the runner gave us.
                n = len(ctx.universe)
                if n == 0:
                    return {}
                return {s: 1.0 / n for s in ctx.universe}
        """
    ).strip() + "\n"
    _write_strategy(isolated_strategies, "override.py", strategy)
    _active_strategy(temp_db, "override")

    # state.json overrides default_universe with a single different symbol
    (isolated_strategies / "override.state.json").write_text(
        json.dumps({"universe": ["513500"]}), encoding="utf-8",
    )

    today = date(2024, 3, 15)
    _seed_virtual_book(temp_db, strategy_id="override")

    import pandas as pd
    df = pd.DataFrame([{
        "date": today, "open": 2.0, "high": 2.0, "low": 2.0,
        "close": 2.0, "volume": 0, "amount": 0.0,
    }])
    adapter = FakeAdapter(
        calendar=[today],
        bars_by_symbol={
            "513500": type("R", (), {"df": df, "stale_seconds": 0, "cache_hit": False})(),
        },
    )

    out = run_once(
        db_path=temp_db, strategies_dir=isolated_strategies,
        adapter=adapter, today=today,
    )
    assert out == Outcome.RAN

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT symbol, action, target_qty FROM strategy_suggestions"
    ).fetchall()
    conn.close()
    # Only 513500 in suggestions, not 510300 (the default).
    assert rows == [("513500", "buy", 50_000)]


def test_run_once_loads_params_json_into_strategy_constructor(
    temp_db: Path, isolated_strategies: Path,
):
    """The runner instantiates the strategy with the persisted params.json
    payload so UI edits to the param form take effect without restarting
    the scheduler."""
    from framework.runner.scheduled_run import (
        Outcome, run_once,
    )

    strategy = textwrap.dedent(
        """
        from framework.strategy import Context
        class WithParams:
            name = "with_params"
            default_universe = ["510300"]
            def __init__(self, threshold=0.5):
                self.threshold = threshold
            def generate(self, ctx):
                return {"510300": self.threshold}
        """
    ).strip() + "\n"
    _write_strategy(isolated_strategies, "with_params.py", strategy)
    _active_strategy(temp_db, "with_params")
    (isolated_strategies / "with_params.params.json").write_text(
        json.dumps({"threshold": 0.25}), encoding="utf-8",
    )

    today = date(2024, 3, 15)
    _seed_virtual_book(temp_db, strategy_id="with_params")
    adapter = _adapter_with_close("510300", close=4.0, today=today)

    out = run_once(
        db_path=temp_db, strategies_dir=isolated_strategies,
        adapter=adapter, today=today,
    )
    assert out == Outcome.RAN

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT target_qty FROM strategy_suggestions"
    ).fetchall()
    conn.close()
    # 0.25 * 100,000 / 4.0 = 6,250
    assert rows[0][0] == 6_250


# ---------------------------------------------------------------------------
# Seam 4 — main() CLI
# ---------------------------------------------------------------------------


def test_main_no_args_uses_active_strategy_from_config(
    temp_db: Path, isolated_strategies: Path, monkeypatch,
):
    from framework.runner import scheduled_run

    _write_strategy(isolated_strategies, "buy_one.py", _STRATEGY_BUY_ONE)
    _active_strategy(temp_db, "buy_one")
    today = date(2024, 3, 15)
    _seed_virtual_book(temp_db, strategy_id="buy_one")
    adapter = _adapter_with_close("510300", close=5.0, today=today)

    monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: adapter)

    rc = scheduled_run.main([
        "--db-path", str(temp_db),
        "--strategies-dir", str(isolated_strategies),
        "--today", today.isoformat(),
    ])
    assert rc == 0

    conn = sqlite3.connect(str(temp_db))
    n = conn.execute("SELECT COUNT(*) FROM strategy_suggestions").fetchone()[0]
    conn.close()
    assert n == 1


def test_main_strategy_arg_overrides_config(
    temp_db: Path, isolated_strategies: Path, monkeypatch,
):
    from framework.runner import scheduled_run

    _write_strategy(isolated_strategies, "buy_one.py", _STRATEGY_BUY_ONE)
    _active_strategy(temp_db, "buy_one")
    today = date(2024, 3, 15)
    _seed_virtual_book(temp_db, strategy_id="buy_one")
    adapter = _adapter_with_close("510300", close=5.0, today=today)

    monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: adapter)

    rc = scheduled_run.main([
        "--db-path", str(temp_db),
        "--strategies-dir", str(isolated_strategies),
        "--today", today.isoformat(),
        "--strategy", "buy_one",
    ])
    assert rc == 0


def test_main_not_trading_day_exits_zero(
    temp_db: Path, isolated_strategies: Path, monkeypatch,
):
    from framework.runner import scheduled_run

    _write_strategy(isolated_strategies, "buy_one.py", _STRATEGY_BUY_ONE)
    _active_strategy(temp_db, "buy_one")
    adapter = FakeAdapter(calendar=[])               # empty calendar

    monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: adapter)

    rc = scheduled_run.main([
        "--db-path", str(temp_db),
        "--strategies-dir", str(isolated_strategies),
        "--today", "2024-03-16",
    ])
    assert rc == 0                                 # NOT_TRADING_DAY → exit 0
    conn = sqlite3.connect(str(temp_db))
    n = conn.execute("SELECT COUNT(*) FROM strategy_suggestions").fetchone()[0]
    conn.close()
    assert n == 0


def test_main_strategy_exception_exits_nonzero(
    temp_db: Path, isolated_strategies: Path, monkeypatch,
):
    from framework.runner import scheduled_run

    _write_strategy(isolated_strategies, "boom.py", _STRATEGY_THROWS)
    _active_strategy(temp_db, "boom")
    today = date(2024, 3, 15)
    adapter = _adapter_with_close("510300", close=5.0, today=today)
    monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: adapter)

    rc = scheduled_run.main([
        "--db-path", str(temp_db),
        "--strategies-dir", str(isolated_strategies),
        "--today", today.isoformat(),
    ])
    assert rc != 0


def test_main_no_active_strategy_exits_zero(
    temp_db: Path, isolated_strategies: Path, monkeypatch,
):
    """Empty config + no --strategy flag → exit 0 (info log + skip).
    A 15:30 cron that no-ops on a weekend is still a successful cron."""
    from framework.runner import scheduled_run

    adapter = FakeAdapter(calendar=[date.today()])
    monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: adapter)

    rc = scheduled_run.main([
        "--db-path", str(temp_db),
        "--strategies-dir", str(isolated_strategies),
    ])
    assert rc == 0


def test_main_missing_strategy_exits_two(
    temp_db: Path, isolated_strategies: Path, monkeypatch,
):
    """Strategy id supplied (via --strategy or config) but the strategies
    dir has no matching .py → exit 2 (config error: misconfigured)."""
    from framework.runner import scheduled_run

    _active_strategy(temp_db, "ghost")              # not in isolated_strategies
    adapter = FakeAdapter(calendar=[date.today()])
    monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: adapter)

    rc = scheduled_run.main([
        "--db-path", str(temp_db),
        "--strategies-dir", str(isolated_strategies),
        "--today", date.today().isoformat(),
    ])
    assert rc == 2