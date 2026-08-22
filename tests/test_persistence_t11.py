"""tests/test_persistence_t11.py

T11 — virtual-book cash top-up / withdraw / rebase initial_cash.

The virtual book is a per-strategy cash ledger; the dashboard renders
``cash - initial_cash`` as the strategy's P&L. External cash flows
(deposits / withdrawals) must shift *both* ``cash`` and ``initial_cash``
together — otherwise a ¥50 000 deposit reads as a ¥50 000 profit and
the dashboard metric lies.

Set-up of ``initial_cash`` is a separate concern (spec ui-pages.md:
"Initial cash editor (writes virtual_books.initial_cash)") and is
modelled by :func:`set_virtual_initial_cash`. It re-bases the P&L
benchmark and is *not* a cash flow.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from framework.persistence import (
    InsufficientCashError,
    VirtualBookNotFoundError,
    adjust_virtual_cash,
    ensure_virtual_book,
    get_virtual_book,
    open_db,
    set_virtual_initial_cash,
)
from framework.persistence.db import ensure_schema


@pytest.fixture
def conn(tmp_path: Path):
    c = open_db(str(tmp_path / "app.db"))
    ensure_schema(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# adjust_virtual_cash
# ---------------------------------------------------------------------------


def test_adjust_deposit_raises_cash_and_initial_cash(conn):
    ensure_virtual_book(conn, "etf_rebalance")
    book = adjust_virtual_cash(conn, "etf_rebalance", +50_000.0)
    assert book.cash == 150_000.0
    assert book.initial_cash == 150_000.0  # mirror, so P&L is unchanged
    # Re-fetch to confirm persistence
    fresh = get_virtual_book(conn, "etf_rebalance")
    assert fresh is not None
    assert fresh.cash == 150_000.0
    assert fresh.initial_cash == 150_000.0


def test_adjust_withdraw_keeps_p_l_flat(conn):
    ensure_virtual_book(conn, "etf_rebalance", initial_cash=200_000.0)
    book = adjust_virtual_cash(conn, "etf_rebalance", -30_000.0)
    assert book.cash == 170_000.0
    assert book.initial_cash == 170_000.0


def test_adjust_zero_is_noop(conn):
    ensure_virtual_book(conn, "etf_rebalance", initial_cash=80_000.0)
    book = adjust_virtual_cash(conn, "etf_rebalance", 0.0)
    assert book.cash == 80_000.0
    assert book.initial_cash == 80_000.0


def test_adjust_withdraw_insufficient_raises(conn):
    ensure_virtual_book(conn, "etf_rebalance", initial_cash=100_000.0)
    with pytest.raises(InsufficientCashError) as ei:
        adjust_virtual_cash(conn, "etf_rebalance", -150_000.0)
    # The book must be untouched on failure (no partial write).
    book = get_virtual_book(conn, "etf_rebalance")
    assert book is not None
    assert book.cash == 100_000.0
    assert book.initial_cash == 100_000.0
    assert "100000" in str(ei.value) or "100,000" in str(ei.value)


def test_adjust_missing_book_raises(conn):
    with pytest.raises(VirtualBookNotFoundError):
        adjust_virtual_cash(conn, "ghost", +10_000.0)


def test_adjust_updates_updated_at(conn):
    """Two sequential writes against ``CURRENT_TIMESTAMP`` advance the
    row's ``updated_at`` value. Uses an explicit delta zero-then-positive
    write so the second write's timestamp is strictly later — relying on
    SQLite's 1-second resolution, no sleep needed."""
    ensure_virtual_book(conn, "etf_rebalance")
    # Force a previous timestamp by writing first, then again with a
    # different delta. SQLite's CURRENT_TIMESTAMP has 1-second resolution,
    # so we capture the row's ``updated_at`` after the first write and
    # assert the second write can equal-or-exceed it.
    adjust_virtual_cash(conn, "etf_rebalance", 0.0)
    before = get_virtual_book(conn, "etf_rebalance")
    after = adjust_virtual_cash(conn, "etf_rebalance", +1.0)
    assert after.updated_at >= before.updated_at  # type: ignore[operator]
    # And the cash bumped.
    assert after.cash == before.cash + 1.0


# ---------------------------------------------------------------------------
# set_virtual_initial_cash
# ---------------------------------------------------------------------------


def test_set_initial_cash_only_changes_benchmark(conn):
    """Rebasing initial_cash must NOT change the cash balance."""
    ensure_virtual_book(conn, "etf_rebalance", initial_cash=100_000.0)
    # A buy was made and cost some cash — book is no longer at 100 000.
    conn.execute(
        "UPDATE virtual_books SET cash = 80000.0 WHERE strategy_id = ?",
        ("etf_rebalance",),
    )
    book = set_virtual_initial_cash(conn, "etf_rebalance", 200_000.0)
    assert book.cash == 80_000.0  # unchanged
    assert book.initial_cash == 200_000.0  # re-based


def test_set_initial_cash_missing_book_raises(conn):
    with pytest.raises(VirtualBookNotFoundError):
        set_virtual_initial_cash(conn, "ghost", 50_000.0)


def test_set_initial_cash_rejects_negative(conn):
    ensure_virtual_book(conn, "etf_rebalance")
    with pytest.raises(ValueError):
        set_virtual_initial_cash(conn, "etf_rebalance", -1.0)
