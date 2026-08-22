"""framework/persistence/repo.py

Typed accessors over the four T4 tables in ``position-schema.md``:

  * ``real_trades``        — user-entered journal of executed trades
  * ``virtual_books``      — per-strategy cash ledger (one row per strategy_id)
  * ``virtual_positions``  — per-(strategy, symbol) weighted-average positions
  * ``strategy_suggestions`` — per-strategy action plan (apply-able to book)

The split (real vs virtual) is enforced at the API surface — this module
has no function that writes to both tracks, so framework code cannot
accidentally cross-pollinate the two ledgers (CAP-5 / position-schema.md
§"Two tracks, two sets of tables").

Atomic apply
------------
``apply_suggestions_to_book`` is the centerpiece: per
``position-schema.md`` §"Apply suggestions to virtual book" it must run in
a single SQLite transaction. If any row's ``applied = 1`` verification
fails, the entire batch rolls back. Half-applied books are forbidden, so
we use ``BEGIN IMMEDIATE`` to take a write lock up front (instead of
``DEFERRED``, which would only lock at first write and risk interleaving
with another writer).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Default constants (per spec)
# ---------------------------------------------------------------------------


DEFAULT_INITIAL_CASH: float = 100_000.0
"""``virtual_books.initial_cash`` default — pinned in position-schema.md."""

DEFAULT_COMMISSION: float = 0.0003
"""Cost-basis commission rate when callers don't pass one. Matches the
backtest engine's default (``EngineConfig.commission``) and the SPEC's
"commission defaults to 0.0003 (configurable)".
"""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoError(Exception):
    """Base for repo-level errors."""


class AlreadyAppliedError(RepoError):
    """At least one suggestion in the batch already has ``applied = 1``.

    Per spec: half-applied books are forbidden. The whole batch is rolled
    back; the caller may retry after resolving the duplicate apply.
    """


class SuggestionNotFoundError(RepoError):
    """One or more suggestion ids in the batch do not belong to the strategy.

    Same atomicity guarantee as ``AlreadyAppliedError``: full rollback.
    """


class MissingTargetPriceError(RepoError):
    """A suggestion with ``action ∈ {buy, sell}`` has no ``target_price``.

    Apply cannot price the fill without a price hint. Whole batch rolls
    back; caller should fix upstream (the runner should always set
    ``target_price`` for non-``hold`` actions).
    """


# ---------------------------------------------------------------------------
# Dataclasses (DB → typed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealTrade:
    id: int
    symbol: str
    side: str
    qty: int
    price: float
    fee: float
    executed_at: date
    note: Optional[str]
    created_at: datetime


@dataclass(frozen=True)
class VirtualBook:
    strategy_id: str
    initial_cash: float
    cash: float
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class VirtualPosition:
    id: int
    strategy_id: str
    symbol: str
    qty: int
    avg_cost: float
    opened_at: date


@dataclass(frozen=True)
class StrategySuggestion:
    id: int
    strategy_id: str
    symbol: str
    action: str           # 'buy' | 'sell' | 'hold'
    target_qty: Optional[int]
    target_price: Optional[float]
    confidence: Optional[float]
    reason: Optional[str]
    generated_at: datetime
    applied: bool
    applied_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Row mappers (DB row → dataclass)
# ---------------------------------------------------------------------------


def _row_to_real_trade(row) -> RealTrade:
    return RealTrade(
        id=row["id"],
        symbol=row["symbol"],
        side=row["side"],
        qty=row["qty"],
        price=row["price"],
        fee=row["fee"],
        executed_at=_parse_date(row["executed_at"]),
        note=row["note"],
        created_at=_parse_dt(row["created_at"]),
    )


def _row_to_virtual_book(row) -> VirtualBook:
    return VirtualBook(
        strategy_id=row["strategy_id"],
        initial_cash=row["initial_cash"],
        cash=row["cash"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_virtual_position(row) -> VirtualPosition:
    return VirtualPosition(
        id=row["id"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        qty=row["qty"],
        avg_cost=row["avg_cost"],
        opened_at=_parse_date(row["opened_at"]),
    )


def _row_to_suggestion(row) -> StrategySuggestion:
    return StrategySuggestion(
        id=row["id"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        action=row["action"],
        target_qty=row["target_qty"],
        target_price=row["target_price"],
        confidence=row["confidence"],
        reason=row["reason"],
        generated_at=_parse_dt(row["generated_at"]),
        applied=bool(row["applied"]),
        applied_at=_parse_dt(row["applied_at"]) if row["applied_at"] else None,
    )


def _parse_date(v) -> date:
    if isinstance(v, date):
        return v
    return datetime.strptime(v, "%Y-%m-%d").date()


def _parse_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v
    # SQLite TIMESTAMP defaults look like 'YYYY-MM-DD HH:MM:SS'.
    return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Real track — user-entered trades
# ---------------------------------------------------------------------------


def record_real_trade(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    qty: int,
    price: float,
    fee: float = 0.0,
    executed_at: date,
    note: Optional[str] = None,
) -> int:
    """Insert one real-trade row. Returns the new ``id``.

    The CHECK constraints (side ∈ {'buy','sell'}, qty>0, price>0) catch
    bad input at write time; we don't pre-validate here so the database
    stays the single source of truth for the schema.
    """
    cur = conn.execute(
        """
        INSERT INTO real_trades
          (symbol, side, qty, price, fee, executed_at, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, side, qty, price, fee, executed_at.isoformat(), note),
    )
    return cur.lastrowid


def update_real_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    qty: Optional[int] = None,
    price: Optional[float] = None,
    fee: Optional[float] = None,
    executed_at: Optional[date] = None,
    note: Optional[str] = None,
) -> None:
    """Update a subset of the row's editable columns.

    Fields passed as ``None`` are left untouched (distinct from ``note=None``
    which sets the column to NULL — the caller must pass an explicit
    sentinel if they want to null a column).
    """
    fields: list[tuple[str, object]] = []
    if symbol is not None:
        fields.append(("symbol", symbol))
    if side is not None:
        fields.append(("side", side))
    if qty is not None:
        fields.append(("qty", qty))
    if price is not None:
        fields.append(("price", price))
    if fee is not None:
        fields.append(("fee", fee))
    if executed_at is not None:
        fields.append(("executed_at", executed_at.isoformat()))
    if note is not None:
        fields.append(("note", note))

    if not fields:
        return  # no-op; avoid an UPDATE that wouldn't change anything

    set_clause = ", ".join(f"{col} = ?" for col, _ in fields)
    params = [val for _, val in fields]
    params.append(trade_id)
    conn.execute(
        f"UPDATE real_trades SET {set_clause} WHERE id = ?",
        params,
    )


def delete_real_trade(conn: sqlite3.Connection, trade_id: int) -> None:
    """Hard-delete the row by id. No soft-delete / audit log in MVP."""
    conn.execute("DELETE FROM real_trades WHERE id = ?", (trade_id,))


def list_real_trades(
    conn: sqlite3.Connection,
    *,
    symbol: Optional[str] = None,
) -> list[RealTrade]:
    """Return real trades, newest-first. Optional filter by symbol."""
    conn.row_factory = sqlite3.Row  # column-name access in _row_to_real_trade
    if symbol is not None:
        rows = conn.execute(
            "SELECT * FROM real_trades WHERE symbol = ? "
            "ORDER BY executed_at DESC, id DESC",
            (symbol,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM real_trades ORDER BY executed_at DESC, id DESC"
        ).fetchall()
    return [_row_to_real_trade(r) for r in rows]


# ---------------------------------------------------------------------------
# Virtual book — cash + positions
# ---------------------------------------------------------------------------


def get_virtual_book(
    conn: sqlite3.Connection,
    strategy_id: str,
) -> Optional[VirtualBook]:
    """Return the book's row, or ``None`` if it has not been bootstrapped.

    A book is only created on first apply (or via ``ensure_virtual_book``);
    before that the strategy has no cash ledger.
    """
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM virtual_books WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchone()
    return _row_to_virtual_book(row) if row else None


def ensure_virtual_book(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    initial_cash: float = DEFAULT_INITIAL_CASH,
) -> VirtualBook:
    """Idempotent: create the book with ``initial_cash`` if missing, else
    return the existing row unchanged.

    The UI may call this on strategy activation; ``apply_suggestions_to_book``
    also calls it implicitly. The book is **not** re-bootstrapped if it
    already exists — callers wanting a fresh book must ``DELETE`` the
    row themselves.
    """
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM virtual_books WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchone()
    if row is not None:
        return _row_to_virtual_book(row)
    conn.execute(
        "INSERT INTO virtual_books (strategy_id, cash, initial_cash) "
        "VALUES (?, ?, ?)",
        (strategy_id, initial_cash, initial_cash),
    )
    row = conn.execute(
        "SELECT * FROM virtual_books WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchone()
    return _row_to_virtual_book(row)


def list_virtual_positions(
    conn: sqlite3.Connection,
    strategy_id: str,
) -> list[VirtualPosition]:
    """Return all (qty > 0) positions for a strategy.

    Rows with ``qty == 0`` are filtered out — they can exist transiently
    inside ``apply_suggestions_to_book`` before the position is updated,
    but the public read surface never returns a "not held" row.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM virtual_positions "
        "WHERE strategy_id = ? AND qty > 0 "
        "ORDER BY symbol",
        (strategy_id,),
    ).fetchall()
    return [_row_to_virtual_position(r) for r in rows]


# ---------------------------------------------------------------------------
# Strategy suggestions — write (runner) / read (UI)
# ---------------------------------------------------------------------------


def insert_suggestion(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    symbol: str,
    action: str,
    target_qty: Optional[int] = None,
    target_price: Optional[float] = None,
    confidence: Optional[float] = None,
    reason: Optional[str] = None,
) -> int:
    """Insert a single suggestion row. Returns the new ``id``.

    The runner calls this once per (strategy, symbol) per scheduled run.
    Per spec, suggestions default to ``applied = 0`` — the user opts in via
    the dashboard.
    """
    cur = conn.execute(
        """
        INSERT INTO strategy_suggestions
          (strategy_id, symbol, action, target_qty, target_price,
           confidence, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (strategy_id, symbol, action, target_qty, target_price,
         confidence, reason),
    )
    return cur.lastrowid


def insert_suggestions(
    conn: sqlite3.Connection,
    suggestions: Iterable[dict],
) -> list[int]:
    """Bulk insert. Each ``dict`` is a row payload (no ``id`` /
    ``generated_at`` / ``applied`` — those are DB defaults)."""
    ids: list[int] = []
    for payload in suggestions:
        ids.append(insert_suggestion(conn, **payload))
    return ids


def list_pending_suggestions(
    conn: sqlite3.Connection,
    strategy_id: str,
) -> list[StrategySuggestion]:
    """Return ``applied = 0`` rows for the strategy, newest first."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM strategy_suggestions "
        "WHERE strategy_id = ? AND applied = 0 "
        "ORDER BY generated_at DESC, id DESC",
        (strategy_id,),
    ).fetchall()
    return [_row_to_suggestion(r) for r in rows]


def list_suggestions(
    conn: sqlite3.Connection,
    strategy_id: str,
    *,
    include_applied: bool = False,
) -> list[StrategySuggestion]:
    """Return all rows for the strategy, newest first.

    Default excludes ``applied = 1`` so the dashboard's "today's action
    plan" view stays focused. Pass ``include_applied=True`` for history.
    """
    conn.row_factory = sqlite3.Row
    if include_applied:
        rows = conn.execute(
            "SELECT * FROM strategy_suggestions WHERE strategy_id = ? "
            "ORDER BY generated_at DESC, id DESC",
            (strategy_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM strategy_suggestions "
            "WHERE strategy_id = ? AND applied = 0 "
            "ORDER BY generated_at DESC, id DESC",
            (strategy_id,),
        ).fetchall()
    return [_row_to_suggestion(r) for r in rows]


# ---------------------------------------------------------------------------
# Apply suggestions to virtual book (atomic; CAP-5 / position-schema.md)
# ---------------------------------------------------------------------------


def apply_suggestions_to_book(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    suggestion_ids: list[int],
    commission: float = DEFAULT_COMMISSION,
) -> dict[int, tuple[int, float]]:
    """Apply a batch of suggestions to the strategy's virtual book.

    Returns ``{suggestion_id: (qty_delta, cash_delta)}`` summarising the
    fill each suggestion caused. The summary lets the UI render a "what
    changed" diff; the source of truth remains the tables themselves.

    Atomicity contract (position-schema.md §"Apply suggestions to virtual
    book"):

      1. ``BEGIN IMMEDIATE`` — write lock up front to prevent races.
      2. Verify ``applied = 0`` on every row in the batch (else
         ``AlreadyAppliedError``, full rollback).
      3. Verify every id belongs to ``strategy_id`` (else
         ``SuggestionNotFoundError``, full rollback).
      4. Compute weighted-average cost updates; require ``target_price``
         for any non-``hold`` action (else ``MissingTargetPriceError``,
         full rollback).
      5. Write ``virtual_positions``, ``virtual_books.cash``, mark rows
         ``applied = 1`` with ``applied_at = now``.
      6. ``COMMIT``. On any exception: ``ROLLBACK`` and re-raise.

    Half-applied books are forbidden. The contract is the spec's, not a
    implementation detail.
    """
    if not suggestion_ids:
        return {}

    conn.row_factory = sqlite3.Row  # idempotent; needed for _row_to_* helpers
    # Snapshot the row ids in their original order so the return value
    # matches caller expectations (we sort by id internally for batching).
    unique_ids = list(dict.fromkeys(suggestion_ids))

    conn.execute("BEGIN IMMEDIATE")
    try:
        placeholders = ",".join("?" * len(unique_ids))

        # (1) + (2) atomic verify — every row must be present and unapplied.
        rows = conn.execute(
            f"SELECT id, symbol, action, target_qty, target_price "
            f"FROM strategy_suggestions "
            f"WHERE strategy_id = ? AND id IN ({placeholders})",
            (strategy_id, *unique_ids),
        ).fetchall()

        found_ids = {r["id"] for r in rows}
        missing = [i for i in unique_ids if i not in found_ids]
        if missing:
            raise SuggestionNotFoundError(
                f"suggestions not found for strategy {strategy_id!r}: {missing}"
            )

        already = conn.execute(
            f"SELECT id FROM strategy_suggestions "
            f"WHERE strategy_id = ? AND id IN ({placeholders}) AND applied = 1",
            (strategy_id, *unique_ids),
        ).fetchall()
        if already:
            raise AlreadyAppliedError(
                f"suggestions already applied: {[r['id'] for r in already]}; "
                f"whole batch rolled back"
            )

        # (3) ensure the book exists. Created with default initial_cash if
        # the strategy has never run before.
        book_row = conn.execute(
            "SELECT cash FROM virtual_books WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        if book_row is None:
            conn.execute(
                "INSERT INTO virtual_books (strategy_id, cash, initial_cash) "
                "VALUES (?, ?, ?)",
                (strategy_id, DEFAULT_INITIAL_CASH, DEFAULT_INITIAL_CASH),
            )
            cash = DEFAULT_INITIAL_CASH
        else:
            cash = float(book_row["cash"])

        # (4) load positions into a local dict (mutate in place, write back).
        positions: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT id, symbol, qty, avg_cost, opened_at FROM virtual_positions "
            "WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall():
            positions[r["symbol"]] = {
                "id": r["id"],
                "qty": int(r["qty"]),
                "avg_cost": float(r["avg_cost"]),
                "opened_at": _parse_date(r["opened_at"]),
            }

        # (5) compute + apply. We require target_price for buy/sell actions
        # because the cost-basis algorithm needs it (sell also uses it for
        # realized PnL). 'hold' rows are skipped silently — they don't
        # touch the book.
        summary: dict[int, tuple[int, float]] = {}
        applied_date = date.today()  # used as opened_at for new positions

        for r in rows:
            sid = r["id"]
            symbol = r["symbol"]
            action = r["action"]
            target_qty = r["target_qty"]
            target_price = r["target_price"]

            if action == "hold":
                summary[sid] = (0, 0.0)
                continue

            if target_price is None:
                raise MissingTargetPriceError(
                    f"suggestion {sid} (action={action!r}) requires target_price; "
                    f"whole batch rolled back"
                )

            qty_delta, cash_delta = _apply_suggestion_to_position(
                positions=positions,
                symbol=symbol,
                action=action,
                target_qty=target_qty,
                target_price=float(target_price),
                commission=commission,
                applied_date=applied_date,
            )
            cash += cash_delta
            summary[sid] = (qty_delta, cash_delta)

        # (6) write back positions
        for sym, p in positions.items():
            if p.get("id") is None:
                # Newly inserted row in this transaction.
                conn.execute(
                    "INSERT INTO virtual_positions "
                    "  (strategy_id, symbol, qty, avg_cost, opened_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (strategy_id, sym, p["qty"], p["avg_cost"],
                     p["opened_at"].isoformat()),
                )
            else:
                conn.execute(
                    "UPDATE virtual_positions "
                    "SET qty = ?, avg_cost = ?, opened_at = ? "
                    "WHERE id = ?",
                    (p["qty"], p["avg_cost"], p["opened_at"].isoformat(), p["id"]),
                )

        # (7) book cash + bump updated_at
        conn.execute(
            "UPDATE virtual_books SET cash = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE strategy_id = ?",
            (cash, strategy_id),
        )

        # (8) mark suggestions applied
        conn.execute(
            f"UPDATE strategy_suggestions "
            f"SET applied = 1, applied_at = CURRENT_TIMESTAMP "
            f"WHERE strategy_id = ? AND id IN ({placeholders})",
            (strategy_id, *unique_ids),
        )

        conn.execute("COMMIT")
        return summary
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _apply_suggestion_to_position(
    *,
    positions: dict[str, dict],
    symbol: str,
    action: str,
    target_qty: Optional[int],
    target_price: float,
    commission: float,
    applied_date: date,
) -> tuple[int, float]:
    """Apply one suggestion to the in-memory ``positions`` dict.

    Returns ``(qty_delta, cash_delta)``. ``positions[symbol]`` is mutated
    in place. Per the spec's cost-basis algorithm:

        buy  : new_avg = (old_qty * old_avg + new_qty * new_price + fee) /
                          (old_qty + new_qty)
        sell : qty decreases; avg_cost unchanged; cash += qty*price - fee
    """
    pos = positions.get(symbol)
    if pos is None:
        pos = {"id": None, "qty": 0, "avg_cost": 0.0, "opened_at": applied_date}
        positions[symbol] = pos

    current_qty = pos["qty"]

    if action == "buy":
        # Buy up to target_qty. If already at-or-above target, no-op.
        if target_qty is None or target_qty <= current_qty:
            return 0, 0.0
        buy_qty = target_qty - current_qty
        gross = buy_qty * target_price
        fee = gross * commission
        new_qty = current_qty + buy_qty
        new_avg = (
            (current_qty * pos["avg_cost"] + buy_qty * target_price + fee)
            / new_qty
        )
        pos["qty"] = new_qty
        pos["avg_cost"] = new_avg
        if current_qty == 0:
            pos["opened_at"] = applied_date
        return buy_qty, -(gross + fee)

    if action == "sell":
        # Sell down to target_qty. If already at-or-below target, no-op.
        if target_qty is None or target_qty >= current_qty:
            return 0, 0.0
        sell_qty = current_qty - target_qty
        gross = sell_qty * target_price
        fee = gross * commission
        # avg_cost stays unchanged per spec. Realized PnL = (price-avg)*qty - fee.
        pos["qty"] = target_qty
        return -sell_qty, gross - fee

    raise ValueError(f"unknown action: {action!r}")


__all__ = [
    # constants
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_COMMISSION",
    # exceptions
    "RepoError",
    "AlreadyAppliedError",
    "SuggestionNotFoundError",
    "MissingTargetPriceError",
    # dataclasses
    "RealTrade",
    "VirtualBook",
    "VirtualPosition",
    "StrategySuggestion",
    # real track
    "record_real_trade",
    "update_real_trade",
    "delete_real_trade",
    "list_real_trades",
    # virtual book
    "get_virtual_book",
    "ensure_virtual_book",
    "list_virtual_positions",
    # suggestions
    "insert_suggestion",
    "insert_suggestions",
    "list_pending_suggestions",
    "list_suggestions",
    # apply
    "apply_suggestions_to_book",
]