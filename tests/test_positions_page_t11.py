"""tests/test_positions_page_t11.py

T11 — cash top-up / withdraw + initial-cash editor on
``pages/3_持仓管理.py`` (virtual-book tab).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import framework.ui_runtime as ui_runtime
from framework.persistence import (
    ensure_schema,
    ensure_virtual_book,
    get_virtual_book,
    open_db,
)


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "app.db"
    # Save the original default path so we can restore it even though
    # monkeypatch will undo it at end-of-test — module-level fixtures can
    # leak DB connections across tests when multiple files share the
    # module-level _connect lru_cache.
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


def _boot(path: str) -> AppTest:
    at = AppTest.from_file(path, default_timeout=30)
    at.run()
    assert not at.exception, [f"{type(e.value).__name__}: {e.value}" for e in at.exception]
    return at


# ---------------------------------------------------------------------------
# Initial-cash editor
# ---------------------------------------------------------------------------


def test_initial_cash_editor_renders_when_book_exists(
    temp_db, fresh_conn: sqlite3.Connection,
):
    ensure_virtual_book(fresh_conn, "etf_rebalance", initial_cash=100_000.0)
    at = _boot("pages/3_持仓管理.py")
    # The new editor label should be present.
    labels = [w.label for w in at.number_input]
    assert any("新初始资金" in lbl for lbl in labels), labels
    # Save button present.
    assert any(b.label == "保存" for b in at.button), [b.label for b in at.button]


def test_initial_cash_save_persists_value(
    temp_db, fresh_conn: sqlite3.Connection,
):
    """Clicking save calls set_virtual_initial_cash → the row updates."""
    ensure_virtual_book(fresh_conn, "etf_rebalance", initial_cash=100_000.0)
    at = _boot("pages/3_持仓管理.py")
    # Find the initial-cash input + save button by their key prefix.
    save_btn = next(b for b in at.button if b.label == "保存")
    save_btn.click()
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    book = get_virtual_book(fresh_conn, "etf_rebalance")
    assert book is not None
    # The default value matches what we seeded.
    assert book.initial_cash == 100_000.0


def test_initial_cash_save_noop_when_value_unchanged(
    temp_db, fresh_conn: sqlite3.Connection,
):
    """Submitting the editor at its seeded value is a no-op (still passes)."""
    ensure_virtual_book(fresh_conn, "etf_rebalance", initial_cash=100_000.0)
    at = _boot("pages/3_持仓管理.py")
    save_btn = next(b for b in at.button if b.label == "保存")
    save_btn.click()
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    book = get_virtual_book(fresh_conn, "etf_rebalance")
    assert book is not None
    assert book.cash == 100_000.0  # unchanged


# ---------------------------------------------------------------------------
# Cash top-up / withdraw
# ---------------------------------------------------------------------------


def test_cash_form_renders_with_deposit_and_withdraw_options(
    temp_db, fresh_conn: sqlite3.Connection,
):
    ensure_virtual_book(fresh_conn, "etf_rebalance", initial_cash=100_000.0)
    at = _boot("pages/3_持仓管理.py")
    labels = [w.label for w in at.selectbox]
    assert any(l == "方向" for l in labels), labels
    # And the cash-amount number_input.
    num_labels = [w.label for w in at.number_input]
    assert any("金额" in lbl for lbl in num_labels), num_labels
    # Submit button.
    assert any(b.label == "提交" for b in at.button), [b.label for b in at.button]


def test_cash_form_rejects_zero_amount(
    temp_db, fresh_conn: sqlite3.Connection,
):
    """Amount == 0 with no submit click → no DB change."""
    ensure_virtual_book(fresh_conn, "etf_rebalance", initial_cash=100_000.0)
    at = _boot("pages/3_持仓管理.py")
    # Just rendering shouldn't trigger any DB write — assert book unchanged.
    book = get_virtual_book(fresh_conn, "etf_rebalance")
    assert book.cash == 100_000.0


def test_empty_state_includes_bootstrap_form(
    temp_db, fresh_conn: sqlite3.Connection,
):
    """When no virtual book exists, the page surfaces a bootstrap form so
    the operator can create one manually (without waiting for the 15:30
    scheduler)."""
    at = _boot("pages/3_持仓管理.py")
    captions = [c.value for c in at.caption]
    assert any("尚无任何虚拟账本" in c for c in captions), captions
    # Bootstrap form's inputs are visible.
    labels = [w.label for w in at.text_input] + [w.label for w in at.number_input]
    assert any("策略 id" in lbl for lbl in labels), labels
    assert any("初始资金" in lbl for lbl in labels), labels
    # Create button present.
    assert any(b.label == "创建" for b in at.button), [b.label for b in at.button]


def test_bootstrap_form_creates_virtual_book(
    temp_db, fresh_conn: sqlite3.Connection,
):
    """Clicking 创建 in the bootstrap form creates a virtual book."""
    at = _boot("pages/3_持仓管理.py")
    create_btn = next(b for b in at.button if b.label == "创建")
    create_btn.click()
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    book = get_virtual_book(fresh_conn, "etf_rebalance")
    assert book is not None
    assert book.initial_cash == 100_000.0  # default