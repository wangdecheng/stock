"""tests/test_persistence_t4.py

Contract tests for T4 (real + virtual track persistence, position schema,
apply-to-virtual-book atomicity). Each section exercises one seam in
isolation so failures point at a specific module:

  * schema migration — ensure_schema creates all four T4 tables
  * real_trades CRUD + CHECK constraints
  * virtual_books + virtual_positions read/write
  * strategy_suggestions write/read
  * apply_suggestions_to_book — weighted-average cost, sell avg_cost unchanged,
                                  applied=1, applied_at set
  * apply atomicity — already-applied / missing-id / missing-price all roll
                       back without partial state
  * real_position / real_avg_cost derived aggregates
  * FK CASCADE on virtual_books delete
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path):
    """Open a fresh on-disk DB per test. Closes on teardown so the file
    handle is released before subsequent tests reopen the same path."""
    from framework.persistence.db import ensure_schema, open_db

    db_path = tmp_path / "app.db"
    c = open_db(db_path)
    ensure_schema(c)
    yield c
    c.close()


def _all_table_names(conn) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Seam 1 — schema
# ---------------------------------------------------------------------------


def test_ensure_schema_creates_all_four_t4_tables(conn):
    names = _all_table_names(conn)
    assert {
        "real_trades",
        "virtual_books",
        "virtual_positions",
        "strategy_suggestions",
        # T3's backtests table must still exist (per T3 ownership rule)
        "backtests",
    }.issubset(names)


def test_ensure_schema_is_idempotent(conn):
    """Running ensure_schema twice must not raise or duplicate indexes."""
    from framework.persistence.db import ensure_schema

    ensure_schema(conn)  # second time
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    index_names = [r[0] for r in rows]
    # No duplicates — each CREATE INDEX IF NOT EXISTS appears at most once.
    assert len(index_names) == len(set(index_names))


# ---------------------------------------------------------------------------
# Seam 2 — real_trades CRUD + CHECK constraints
# ---------------------------------------------------------------------------


def test_record_real_trade_round_trips(conn):
    from framework.persistence import record_real_trade, list_real_trades

    tid = record_real_trade(
        conn,
        symbol="600519",
        side="buy",
        qty=100,
        price=1700.5,
        fee=5.10,
        executed_at=date(2024, 1, 15),
        note="建仓",
    )
    assert isinstance(tid, int) and tid > 0
    rows = list_real_trades(conn)
    assert len(rows) == 1
    assert rows[0].symbol == "600519"
    assert rows[0].side == "buy"
    assert rows[0].qty == 100
    assert rows[0].price == pytest.approx(1700.5)
    assert rows[0].fee == pytest.approx(5.10)
    assert rows[0].executed_at == date(2024, 1, 15)
    assert rows[0].note == "建仓"


def test_list_real_trades_orders_newest_first(conn):
    from framework.persistence import record_real_trade, list_real_trades

    record_real_trade(conn, symbol="X", side="buy", qty=10, price=10.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="X", side="buy", qty=10, price=11.0,
                      executed_at=date(2024, 1, 5))
    record_real_trade(conn, symbol="X", side="buy", qty=10, price=12.0,
                      executed_at=date(2024, 1, 3))

    rows = list_real_trades(conn)
    assert [r.executed_at for r in rows] == [
        date(2024, 1, 5),
        date(2024, 1, 3),
        date(2024, 1, 1),
    ]


def test_list_real_trades_filters_by_symbol(conn):
    from framework.persistence import list_real_trades, record_real_trade

    record_real_trade(conn, symbol="600519", side="buy", qty=10, price=1.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="000001", side="buy", qty=20, price=2.0,
                      executed_at=date(2024, 1, 2))

    a = list_real_trades(conn, symbol="600519")
    b = list_real_trades(conn, symbol="000001")
    assert {r.symbol for r in a} == {"600519"}
    assert {r.symbol for r in b} == {"000001"}


def test_update_real_trade_partial(conn):
    from framework.persistence import list_real_trades, record_real_trade, update_real_trade

    tid = record_real_trade(
        conn, symbol="X", side="buy", qty=100, price=10.0,
        fee=0.3, executed_at=date(2024, 1, 1), note="orig",
    )
    update_real_trade(conn, tid, price=11.0, note="corrected")
    row = list_real_trades(conn)[0]
    assert row.price == pytest.approx(11.0)
    assert row.note == "corrected"
    # Untouched fields stay
    assert row.qty == 100
    assert row.fee == pytest.approx(0.3)


def test_update_real_trade_no_args_is_noop(conn):
    from framework.persistence import record_real_trade, update_real_trade, list_real_trades

    record_real_trade(conn, symbol="X", side="buy", qty=10, price=10.0,
                      executed_at=date(2024, 1, 1))
    update_real_trade(conn, 1)  # no kwargs
    assert list_real_trades(conn)[0].price == pytest.approx(10.0)


def test_delete_real_trade(conn):
    from framework.persistence import (
        delete_real_trade, list_real_trades, record_real_trade,
    )

    tid = record_real_trade(conn, symbol="X", side="buy", qty=10, price=10.0,
                            executed_at=date(2024, 1, 1))
    delete_real_trade(conn, tid)
    assert list_real_trades(conn) == []


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"side": "hold"},     # not in {buy, sell}
        {"qty": 0},           # CHECK (qty > 0)
        {"qty": -1},
        {"price": 0},         # CHECK (price > 0)
        {"price": -1.0},
    ],
)
def test_real_trades_check_constraints_reject_garbage(conn, bad_kwargs):
    """Spec rationale: 'CHECK constraints on side / action enums — fail
    fast at write time rather than carry garbage downstream.'"""
    from framework.persistence import record_real_trade

    payload = dict(
        symbol="X", side="buy", qty=10, price=10.0,
        executed_at=date(2024, 1, 1),
    )
    payload.update(bad_kwargs)
    with pytest.raises(Exception) as excinfo:  # sqlite IntegrityError
        record_real_trade(conn, **payload)
    # The DB raised; that's the contract — no need to assert specific message.
    assert "constraint" in str(excinfo.value).lower() or "check" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Seam 3 — virtual_books + virtual_positions
# ---------------------------------------------------------------------------


def test_get_virtual_book_returns_none_until_created(conn):
    from framework.persistence import get_virtual_book
    assert get_virtual_book(conn, "ma_cross") is None


def test_ensure_virtual_book_uses_default_initial_cash(conn):
    from framework.persistence import DEFAULT_INITIAL_CASH, ensure_virtual_book

    book = ensure_virtual_book(conn, "ma_cross")
    assert book.strategy_id == "ma_cross"
    assert book.cash == pytest.approx(DEFAULT_INITIAL_CASH)
    assert book.initial_cash == pytest.approx(DEFAULT_INITIAL_CASH)
    assert book.cash == 100_000.0  # spec pin


def test_ensure_virtual_book_is_idempotent(conn):
    """Re-calling must not reset cash — caller's prior writes survive."""
    from framework.persistence import ensure_virtual_book

    ensure_virtual_book(conn, "ma_cross")
    # Tamper with cash to simulate a stateful session
    conn.execute(
        "UPDATE virtual_books SET cash = 75000 WHERE strategy_id = ?",
        ("ma_cross",),
    )
    conn.commit()
    book2 = ensure_virtual_book(conn, "ma_cross")
    assert book2.cash == pytest.approx(75000.0)
    # And there's still only one row for this strategy_id.
    n = conn.execute(
        "SELECT COUNT(*) FROM virtual_books WHERE strategy_id = ?",
        ("ma_cross",),
    ).fetchone()[0]
    assert n == 1


def test_ensure_virtual_book_accepts_custom_initial_cash_on_first_create(conn):
    from framework.persistence import ensure_virtual_book

    book = ensure_virtual_book(conn, "ma_cross", initial_cash=50_000.0)
    assert book.initial_cash == pytest.approx(50_000.0)
    assert book.cash == pytest.approx(50_000.0)


def test_virtual_positions_unique_per_strategy_symbol(conn):
    """The UNIQUE (strategy_id, symbol) constraint must reject duplicates."""
    from framework.persistence import ensure_virtual_book

    ensure_virtual_book(conn, "ma_cross")
    conn.execute(
        "INSERT INTO virtual_positions (strategy_id, symbol, qty, avg_cost, opened_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("ma_cross", "600519", 100, 10.0, "2024-01-01"),
    )
    with pytest.raises(Exception):  # sqlite IntegrityError
        conn.execute(
            "INSERT INTO virtual_positions (strategy_id, symbol, qty, avg_cost, opened_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("ma_cross", "600519", 50, 11.0, "2024-01-02"),
        )


def test_list_virtual_positions_filters_qty_zero(conn):
    from framework.persistence import (
        ensure_virtual_book, list_virtual_positions,
    )

    ensure_virtual_book(conn, "ma_cross")
    conn.execute(
        "INSERT INTO virtual_positions (strategy_id, symbol, qty, avg_cost, opened_at) "
        "VALUES ('ma_cross', 'X', 100, 10.0, '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO virtual_positions (strategy_id, symbol, qty, avg_cost, opened_at) "
        "VALUES ('ma_cross', 'Y', 0, 0.0, '2024-01-01')"
    )
    rows = list_virtual_positions(conn, "ma_cross")
    assert [r.symbol for r in rows] == ["X"]
    assert rows[0].qty == 100


# ---------------------------------------------------------------------------
# Seam 4 — strategy_suggestions read/write
# ---------------------------------------------------------------------------


def test_insert_suggestion_defaults_applied_false(conn):
    from framework.persistence import (
        ensure_virtual_book, insert_suggestion, list_pending_suggestions,
    )

    ensure_virtual_book(conn, "ma_cross")
    sid = insert_suggestion(
        conn, strategy_id="ma_cross", symbol="600519", action="buy",
        target_qty=100, target_price=10.0, confidence=0.8,
        reason="breakout",
    )
    rows = list_pending_suggestions(conn, "ma_cross")
    assert len(rows) == 1
    assert rows[0].id == sid
    assert rows[0].applied is False
    assert rows[0].action == "buy"
    assert rows[0].reason == "breakout"


def test_list_suggestions_include_applied_toggle(conn):
    from framework.persistence import (
        ensure_virtual_book, insert_suggestion, list_suggestions,
    )

    ensure_virtual_book(conn, "ma_cross")
    insert_suggestion(conn, strategy_id="ma_cross", symbol="A",
                      action="buy", target_qty=100, target_price=10.0)
    sid = insert_suggestion(conn, strategy_id="ma_cross", symbol="B",
                            action="sell", target_qty=0, target_price=11.0)
    conn.execute(
        "UPDATE strategy_suggestions SET applied = 1, applied_at = ? WHERE id = ?",
        ("2024-01-05 12:00:00", sid),
    )

    assert len(list_suggestions(conn, "ma_cross", include_applied=False)) == 1
    assert len(list_suggestions(conn, "ma_cross", include_applied=True)) == 2


@pytest.mark.parametrize("bad_action", ["drop", "", "BUY", "Hold"])
def test_suggestions_check_action_enum(conn, bad_action):
    from framework.persistence import ensure_virtual_book, insert_suggestion

    ensure_virtual_book(conn, "x")
    with pytest.raises(Exception):
        insert_suggestion(conn, strategy_id="x", symbol="y", action=bad_action,
                          target_qty=1, target_price=1.0)


# ---------------------------------------------------------------------------
# Seam 5 — apply_suggestions_to_book
# ---------------------------------------------------------------------------


def _make_suggestion(conn, *, strategy_id, symbol, action, target_qty,
                     target_price=None, confidence=None, reason=None) -> int:
    from framework.persistence import insert_suggestion
    return insert_suggestion(
        conn, strategy_id=strategy_id, symbol=symbol, action=action,
        target_qty=target_qty, target_price=target_price,
        confidence=confidence, reason=reason,
    )


def test_apply_buy_creates_book_with_default_initial_cash_and_updates_position(conn):
    """First-apply path: book is bootstrapped with the spec's 100k default
    when no virtual_books row exists for the strategy."""
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, get_virtual_book,
        list_pending_suggestions, list_virtual_positions,
    )

    # Runner-style: ensure book exists before writing suggestions (FK).
    ensure_virtual_book(conn, "ma_cross")
    sid = _make_suggestion(conn, strategy_id="ma_cross", symbol="600519",
                           action="buy", target_qty=100, target_price=10.0)
    summary = apply_suggestions_to_book(conn, strategy_id="ma_cross",
                                        suggestion_ids=[sid])

    book = get_virtual_book(conn, "ma_cross")
    assert book is not None
    # default initial, minus gross cost, minus commission = 100000 - 1000 - 0.30
    assert book.cash == pytest.approx(100_000.0 - 100 * 10.0 - 100 * 10.0 * 0.0003)

    pos = list_virtual_positions(conn, "ma_cross")
    assert len(pos) == 1
    assert pos[0].symbol == "600519"
    assert pos[0].qty == 100
    # avg_cost with commission baked in: (100 * 10 + 0.30) / 100 = 10.003
    assert pos[0].avg_cost == pytest.approx(10.003)

    # No more pending rows
    assert list_pending_suggestions(conn, "ma_cross") == []

    # Summary reflects the qty/cash delta (gross + commission baked in)
    assert summary[sid] == (100, pytest.approx(-(1000.0 + 1000.0 * 0.0003)))


def test_apply_weighted_average_buy_matches_position_schema_formula(conn):
    """The spec's cost-basis formula:
        new_avg = (old_qty*old_avg + new_qty*new_price + new_fee) /
                  (old_qty + new_qty)
    Two sequential buys must produce the documented merged avg cost.
    """
    from framework.persistence import apply_suggestions_to_book, list_virtual_positions

    # Pre-seed: book with 100k cash, no positions
    from framework.persistence import ensure_virtual_book
    ensure_virtual_book(conn, "ma_cross")

    # First lot: 100 @ 10, second: 200 @ 12. Commission baked in.
    sid1 = _make_suggestion(conn, strategy_id="ma_cross", symbol="X",
                            action="buy", target_qty=100, target_price=10.0)
    sid2 = _make_suggestion(conn, strategy_id="ma_cross", symbol="X",
                            action="buy", target_qty=300, target_price=12.0)

    apply_suggestions_to_book(conn, strategy_id="ma_cross",
                              suggestion_ids=[sid1], commission=0.0003)
    apply_suggestions_to_book(conn, strategy_id="ma_cross",
                              suggestion_ids=[sid2], commission=0.0003)

    pos = list_virtual_positions(conn, "ma_cross")
    assert len(pos) == 1
    assert pos[0].qty == 300
    # (100 * 10 + 200 * 12 + 0.30 + 0.72) / 300 ≈ 11.0034
    expected = (100 * 10 + 200 * 12 + 200 * 12 * 0.0003 + 100 * 10 * 0.0003) / 300
    assert pos[0].avg_cost == pytest.approx(expected, rel=1e-9)


def test_apply_sell_does_not_change_avg_cost(conn):
    """Per spec: 'Multi-lot sell: qty decreases; avg_cost stays unchanged;
    realized PnL = (sell_price - avg_cost) * sold_qty - fee'."""
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, list_virtual_positions,
    )

    ensure_virtual_book(conn, "ma_cross")
    buy = _make_suggestion(conn, strategy_id="ma_cross", symbol="X",
                           action="buy", target_qty=100, target_price=10.0)
    apply_suggestions_to_book(conn, strategy_id="ma_cross", suggestion_ids=[buy])

    avg_before = list_virtual_positions(conn, "ma_cross")[0].avg_cost
    cash_before = conn.execute(
        "SELECT cash FROM virtual_books WHERE strategy_id = ?", ("ma_cross",)
    ).fetchone()[0]

    sell = _make_suggestion(conn, strategy_id="ma_cross", symbol="X",
                            action="sell", target_qty=60, target_price=15.0)
    summary = apply_suggestions_to_book(conn, strategy_id="ma_cross",
                                        suggestion_ids=[sell])

    pos = list_virtual_positions(conn, "ma_cross")
    assert pos[0].qty == 60
    assert pos[0].avg_cost == pytest.approx(avg_before)

    # cash delta = sell_qty * sell_price - fee = 40 * 15 - 40*15*0.0003
    expected_delta = 40 * 15.0 - 40 * 15.0 * 0.0003
    assert summary[sell] == (-40, pytest.approx(expected_delta, rel=1e-9))
    assert conn.execute(
        "SELECT cash FROM virtual_books WHERE strategy_id = ?", ("ma_cross",)
    ).fetchone()[0] == pytest.approx(cash_before + expected_delta, rel=1e-9)


def test_apply_hold_is_noop(conn):
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, get_virtual_book,
        list_virtual_positions,
    )

    ensure_virtual_book(conn, "ma_cross")
    # Pre-seed: 100 shares of X
    buy = _make_suggestion(conn, strategy_id="ma_cross", symbol="X",
                           action="buy", target_qty=100, target_price=10.0)
    apply_suggestions_to_book(conn, strategy_id="ma_cross",
                              suggestion_ids=[buy])
    cash_before = get_virtual_book(conn, "ma_cross").cash
    pos_before = list_virtual_positions(conn, "ma_cross")

    hold = _make_suggestion(conn, strategy_id="ma_cross", symbol="X",
                            action="hold", target_qty=100, target_price=10.0)
    summary = apply_suggestions_to_book(conn, strategy_id="ma_cross",
                                        suggestion_ids=[hold])

    assert summary[hold] == (0, 0.0)
    assert get_virtual_book(conn, "ma_cross").cash == pytest.approx(cash_before)
    assert list_virtual_positions(conn, "ma_cross")[0].qty == pos_before[0].qty


def test_apply_multi_symbol_batch_is_atomic(conn):
    """Two symbols in one batch: both apply, both marked applied=1, cash
    reflects both deltas."""
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, get_virtual_book,
        list_pending_suggestions, list_virtual_positions,
    )

    ensure_virtual_book(conn, "s")
    sid_a = _make_suggestion(conn, strategy_id="s", symbol="A", action="buy",
                             target_qty=100, target_price=10.0)
    sid_b = _make_suggestion(conn, strategy_id="s", symbol="B", action="buy",
                             target_qty=50, target_price=20.0)
    apply_suggestions_to_book(conn, strategy_id="s",
                              suggestion_ids=[sid_a, sid_b])

    assert len(list_virtual_positions(conn, "s")) == 2
    assert list_pending_suggestions(conn, "s") == []
    book = get_virtual_book(conn, "s")
    gross = 100 * 10.0 + 50 * 20.0
    assert book.cash == pytest.approx(100_000.0 - gross - gross * 0.0003)


def test_apply_marks_suggestions_applied_with_timestamp(conn):
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, list_suggestions,
    )

    ensure_virtual_book(conn, "s")
    sid = _make_suggestion(conn, strategy_id="s", symbol="A", action="buy",
                           target_qty=10, target_price=10.0)
    apply_suggestions_to_book(conn, strategy_id="s", suggestion_ids=[sid])

    rows = list_suggestions(conn, "s", include_applied=True)
    assert len(rows) == 1
    assert rows[0].applied is True
    assert rows[0].applied_at is not None
    assert isinstance(rows[0].applied_at, datetime)


# ---------------------------------------------------------------------------
# Seam 6 — apply atomicity: rollback on bad input
# ---------------------------------------------------------------------------


def test_apply_rolls_back_when_one_already_applied(conn):
    """The centerpiece of the spec's atomicity contract. A batch that
    contains an already-applied row must roll back the *entire* batch —
    not just the already-applied row, not just the suggestions after it."""
    from framework.persistence import (
        AlreadyAppliedError, apply_suggestions_to_book, ensure_virtual_book,
        get_virtual_book, list_pending_suggestions, list_suggestions,
        list_virtual_positions,
    )

    ensure_virtual_book(conn, "s")
    sid_a = _make_suggestion(conn, strategy_id="s", symbol="A", action="buy",
                             target_qty=100, target_price=10.0)
    sid_b = _make_suggestion(conn, strategy_id="s", symbol="B", action="buy",
                             target_qty=50, target_price=20.0)
    # Apply the first one cleanly.
    apply_suggestions_to_book(conn, strategy_id="s", suggestion_ids=[sid_a])

    # Now try to apply BOTH in a single batch — A is already applied, so the
    # whole batch must roll back. B must NOT be marked applied.
    cash_before = get_virtual_book(conn, "s").cash
    with pytest.raises(AlreadyAppliedError):
        apply_suggestions_to_book(conn, strategy_id="s",
                                  suggestion_ids=[sid_a, sid_b])

    # Invariants post-rollback:
    # (a) B is still pending
    pending = list_pending_suggestions(conn, "s")
    assert {p.id for p in pending} == {sid_b}
    # (b) B has not been marked applied
    all_rows = list_suggestions(conn, "s", include_applied=True)
    sid_b_row = next(r for r in all_rows if r.id == sid_b)
    assert sid_b_row.applied is False
    # (c) B's symbol did not get a position
    pos_symbols = {p.symbol for p in list_virtual_positions(conn, "s")}
    assert "B" not in pos_symbols
    # (d) cash is unchanged from before the failed call
    assert get_virtual_book(conn, "s").cash == pytest.approx(cash_before)


def test_apply_rolls_back_when_suggestion_id_missing(conn):
    from framework.persistence import (
        SuggestionNotFoundError, apply_suggestions_to_book, ensure_virtual_book,
        get_virtual_book, list_pending_suggestions,
    )

    ensure_virtual_book(conn, "s")
    sid_real = _make_suggestion(conn, strategy_id="s", symbol="A", action="buy",
                                target_qty=100, target_price=10.0)
    sid_fake = 9999

    with pytest.raises(SuggestionNotFoundError):
        apply_suggestions_to_book(conn, strategy_id="s",
                                  suggestion_ids=[sid_real, sid_fake])

    # No book was created (first apply rolled back before any writes).
    assert get_virtual_book(conn, "s") is not None  # book exists (we ensured it)
    # The real suggestion is still pending.
    assert {p.id for p in list_pending_suggestions(conn, "s")} == {sid_real}


def test_apply_rolls_back_when_target_price_missing(conn):
    """A buy/sell row without target_price violates the cost-basis
    contract — apply must reject with full rollback."""
    from framework.persistence import (
        MissingTargetPriceError, apply_suggestions_to_book, ensure_virtual_book,
        get_virtual_book, list_pending_suggestions,
    )

    ensure_virtual_book(conn, "s")
    sid_no_price = _make_suggestion(conn, strategy_id="s", symbol="A",
                                    action="buy", target_qty=100,
                                    target_price=None)
    with pytest.raises(MissingTargetPriceError):
        apply_suggestions_to_book(conn, strategy_id="s",
                                  suggestion_ids=[sid_no_price])

    # Invariants post-rollback
    book = get_virtual_book(conn, "s")
    assert book is not None
    pending = list_pending_suggestions(conn, "s")
    assert len(pending) == 1
    assert pending[0].applied is False


def test_apply_rolls_back_when_target_qty_exceeds_holdings(conn):
    """action='sell' with target_qty > current qty must be a no-op (cannot
    short via the cost-basis algorithm in MVP)."""
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, get_virtual_book,
        list_virtual_positions,
    )

    ensure_virtual_book(conn, "s")
    buy = _make_suggestion(conn, strategy_id="s", symbol="A", action="buy",
                           target_qty=100, target_price=10.0)
    apply_suggestions_to_book(conn, strategy_id="s", suggestion_ids=[buy])

    cash_before = get_virtual_book(conn, "s").cash
    sell_too_much = _make_suggestion(conn, strategy_id="s", symbol="A",
                                     action="sell", target_qty=200,
                                     target_price=20.0)
    summary = apply_suggestions_to_book(conn, strategy_id="s",
                                        suggestion_ids=[sell_too_much])
    assert summary[sell_too_much] == (0, 0.0)
    # No state changed
    assert get_virtual_book(conn, "s").cash == pytest.approx(cash_before)
    assert list_virtual_positions(conn, "s")[0].qty == 100


def test_apply_with_duplicate_ids_in_batch_dedupes(conn):
    """Defensive: if the caller passes the same id twice, we should
    deduplicate rather than raise."""
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, list_virtual_positions,
    )

    ensure_virtual_book(conn, "s")
    sid = _make_suggestion(conn, strategy_id="s", symbol="A", action="buy",
                           target_qty=100, target_price=10.0)
    summary = apply_suggestions_to_book(conn, strategy_id="s",
                                        suggestion_ids=[sid, sid])
    # Summary has the single id, and the position was filled once.
    assert list(summary.keys()) == [sid]
    assert list_virtual_positions(conn, "s")[0].qty == 100


def test_apply_empty_batch_is_noop(conn):
    from framework.persistence import apply_suggestions_to_book, get_virtual_book

    # No book created, no error
    out = apply_suggestions_to_book(conn, strategy_id="s", suggestion_ids=[])
    assert out == {}
    assert get_virtual_book(conn, "s") is None


# ---------------------------------------------------------------------------
# Seam 7 — real_position / real_avg_cost derived aggregates
# ---------------------------------------------------------------------------


def test_real_position_net_qty(conn):
    from framework.persistence import (
        real_position, record_real_trade,
    )

    record_real_trade(conn, symbol="X", side="buy", qty=100, price=10.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="X", side="buy", qty=50, price=11.0,
                      executed_at=date(2024, 1, 2))
    record_real_trade(conn, symbol="X", side="sell", qty=30, price=12.0,
                      executed_at=date(2024, 1, 3))
    assert real_position(conn, "X") == 120  # 100 + 50 - 30


def test_real_position_no_trades_returns_zero(conn):
    from framework.persistence import real_position
    assert real_position(conn, "ghost") == 0


def test_real_avg_cost_weighted(conn):
    """Two buys of (100 @ 10) and (50 @ 14) → avg = (100*10 + 50*14) / 150 = 8.0.
    Actually: (1000 + 700) / 150 = 1700 / 150 ≈ 11.333."""
    from framework.persistence import real_avg_cost, record_real_trade

    record_real_trade(conn, symbol="X", side="buy", qty=100, price=10.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="X", side="buy", qty=50, price=14.0,
                      executed_at=date(2024, 1, 2))
    assert real_avg_cost(conn, "X") == pytest.approx(1700.0 / 150.0)


def test_real_avg_cost_none_when_flat_or_short(conn):
    from framework.persistence import real_avg_cost, record_real_trade

    # Fully closed
    record_real_trade(conn, symbol="X", side="buy", qty=100, price=10.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="X", side="sell", qty=100, price=11.0,
                      executed_at=date(2024, 1, 2))
    assert real_avg_cost(conn, "X") is None

    # Sells outnumber buys (rare data entry error; cash accounts shouldn't allow)
    record_real_trade(conn, symbol="Y", side="sell", qty=10, price=10.0,
                      executed_at=date(2024, 1, 1))
    assert real_avg_cost(conn, "Y") is None

    # No trades at all
    assert real_avg_cost(conn, "ghost") is None


def test_list_real_positions_returns_only_net_long_symbols(conn):
    from framework.persistence import (
        list_real_positions, record_real_trade,
    )

    record_real_trade(conn, symbol="A", side="buy", qty=100, price=10.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="B", side="buy", qty=200, price=20.0,
                      executed_at=date(2024, 1, 1))
    record_real_trade(conn, symbol="B", side="sell", qty=200, price=22.0,
                      executed_at=date(2024, 1, 2))  # closed
    record_real_trade(conn, symbol="C", side="buy", qty=50, price=5.0,
                      fee=1.0, executed_at=date(2024, 1, 3))

    rows = list_real_positions(conn)
    assert [r["symbol"] for r in rows] == ["A", "C"]
    a = rows[0]
    assert a["qty"] == 100
    assert a["avg_cost"] == pytest.approx(10.0)
    c = rows[1]
    assert c["qty"] == 50
    assert c["avg_cost"] == pytest.approx(5.0)
    assert c["buy_qty"] == 50
    assert c["sell_qty"] == 0


# ---------------------------------------------------------------------------
# Seam 8 — FK CASCADE
# ---------------------------------------------------------------------------


def test_deleting_virtual_book_cascades_to_positions_and_suggestions(conn):
    """Position-schema.md: positions and suggestions FK to virtual_books
    with ON DELETE CASCADE. Deleting a book must clean up both."""
    from framework.persistence import apply_suggestions_to_book, ensure_virtual_book

    ensure_virtual_book(conn, "s")
    sid_buy = _make_suggestion(conn, strategy_id="s", symbol="A",
                               action="buy", target_qty=100, target_price=10.0)
    apply_suggestions_to_book(conn, strategy_id="s", suggestion_ids=[sid_buy])

    n_pos_before = conn.execute(
        "SELECT COUNT(*) FROM virtual_positions WHERE strategy_id = ?",
        ("s",),
    ).fetchone()[0]
    n_sugg_before = conn.execute(
        "SELECT COUNT(*) FROM strategy_suggestions WHERE strategy_id = ?",
        ("s",),
    ).fetchone()[0]
    assert n_pos_before == 1
    assert n_sugg_before == 1

    conn.execute("DELETE FROM virtual_books WHERE strategy_id = ?", ("s",))
    conn.commit()

    assert conn.execute(
        "SELECT COUNT(*) FROM virtual_positions WHERE strategy_id = ?",
        ("s",),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM strategy_suggestions WHERE strategy_id = ?",
        ("s",),
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Seam 10 — End-to-end smoke (runner-style batch)
# ---------------------------------------------------------------------------


def test_end_to_end_runner_writes_suggestions_then_user_applies(conn):
    """Mirrors the spec's CAP-5 daily loop: a runner writes a batch of
    suggestions, the user reviews them and applies a subset, the
    virtual book reflects the change."""
    from framework.persistence import (
        apply_suggestions_to_book, ensure_virtual_book, get_virtual_book,
        insert_suggestion, list_pending_suggestions, list_virtual_positions,
    )

    # Runner-style bootstrap: book first, then suggestions.
    ensure_virtual_book(conn, "etf_rebalance")

    # Simulated runner output for strategy "etf_rebalance" on day D:
    insert_suggestion(conn, strategy_id="etf_rebalance", symbol="510300",
                      action="buy", target_qty=4000, target_price=4.0,
                      confidence=0.7, reason="沪深300ETF target 40%")
    insert_suggestion(conn, strategy_id="etf_rebalance", symbol="513500",
                      action="buy", target_qty=1500, target_price=8.0,
                      confidence=0.7, reason="标普500ETF target 30%")
    insert_suggestion(conn, strategy_id="etf_rebalance", symbol="511010",
                      action="buy", target_qty=300, target_price=110.0,
                      confidence=0.7, reason="国债ETF target 30%")
    insert_suggestion(conn, strategy_id="etf_rebalance", symbol="CASH",
                      action="hold", target_qty=0, target_price=None,
                      reason="keep remainder in cash")

    pending = list_pending_suggestions(conn, "etf_rebalance")
    assert len(pending) == 4
    sid_to_apply = [s.id for s in pending if s.action != "hold"]

    apply_suggestions_to_book(conn, strategy_id="etf_rebalance",
                              suggestion_ids=sid_to_apply)
    book = get_virtual_book(conn, "etf_rebalance")
    gross = 4000 * 4.0 + 1500 * 8.0 + 300 * 110.0
    assert book.cash == pytest.approx(100_000.0 - gross - gross * 0.0003)
    pos = {p.symbol: p for p in list_virtual_positions(conn, "etf_rebalance")}
    assert pos["510300"].qty == 4000
    assert pos["513500"].qty == 1500
    assert pos["511010"].qty == 300

    # The hold row is still pending (not auto-applied).
    leftover = list_pending_suggestions(conn, "etf_rebalance")
    assert {s.action for s in leftover} == {"hold"}