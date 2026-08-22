"""framework/runner/scheduled_run.py

The 15:00 scheduled run (CAP-7, ``specs/spec-a-stock-quant/scheduler.md``).

A-share regular session closes at 15:00; the trigger fires immediately at
the close so today's suggestion plan is ready by 15:05, well before the
operator reviews it.

Hard rule: this module is the **only** entry point for the scheduler; it
must not be imported from inside the Streamlit UI process. The runner is a
separate process (system cron / systemd) so a long strategy run cannot
freeze the UI thread.

Public surface
--------------
* :class:`Outcome` — enum the orchestrator returns. The CLI maps these to
  exit codes; tests assert directly.
* :func:`diff_signal` — pure signal→suggestion diff. The "weight → qty"
  algorithm ported from the backtest engine; the runner writes a
  ``strategy_suggestions`` row per output instead of a fill.
* :func:`load_active_strategy_id` — read the dashboard's "current strategy"
  pointer from the ``config`` table.
* :func:`resolve_universe` — pick the universe from ``state.json`` first,
  fall back to the strategy class's optional ``default_universe`` attr.
* :func:`run_once` — the full orchestrator. Returns an :class:`Outcome`.
* :func:`main` — argparse CLI; ``python -m framework.runner.scheduled_run``.

Universe contract
-----------------
The runner needs to know the tradeable universe so it can (a) build the
strategy's ``Context.universe`` and (b) fetch today's close for each
member. Two sources, in priority order:

  1. ``strategies/<name>.state.json`` key ``"universe"`` — operator-editable.
  2. Class attribute ``default_universe`` on the strategy class — strategy
     author's static default (e.g. the canonical ETF rebalance strategy).

If neither is set, the runner writes a sentinel row ``reason="no_universe"``
and exits non-zero. Per scheduler.md, exit non-zero is correct here: the
operator must fix the strategy config before the next 15:00 trigger.
"""

from __future__ import annotations

import argparse
import enum
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from framework.persistence import (
    DEFAULT_INITIAL_CASH,
    ensure_schema,
    get_config,
    get_virtual_book,
    insert_suggestion,
    list_virtual_positions,
    open_db,
)
from framework.strategy import Context, Position, validate_signal
from framework.strategy.discover import discover_strategies
from framework.strategy.persistence import load_params, load_state

# Imported lazily inside ``_resolve_adapter`` so test environments can
# ``monkeypatch.setattr(scheduled_run, "AKShareAdapter", lambda: fake)``
# before calling ``main``. Defining the name at module scope makes the
# patch site stable; the lazy import is purely a perf concern (avoiding
# akshare import when no real adapter is needed).
AKShareAdapter: Optional[type] = None


_log = logging.getLogger("framework.runner.scheduled_run")


# ---------------------------------------------------------------------------
# Outcomes (mapped to exit codes by main())
# ---------------------------------------------------------------------------


class Outcome(enum.Enum):
    """What one scheduled run produced. Tests assert equality; the CLI maps
    to POSIX exit codes via :func:`_outcome_to_exit_code`."""

    NO_ACTIVE_STRATEGY = "no_active_strategy"
    NOT_TRADING_DAY = "not_trading_day"
    RAN = "ran"
    STRATEGY_EXCEPTION = "strategy_exception"
    DATA_UNAVAILABLE = "data_unavailable"
    NO_UNIVERSE = "no_universe"
    MISSING_STRATEGY = "missing_strategy"


# Outcomes that should NOT trigger a non-zero cron exit (per scheduler.md).
_INFO_OUTCOMES = frozenset({Outcome.NO_ACTIVE_STRATEGY, Outcome.NOT_TRADING_DAY})


def _outcome_to_exit_code(outcome: Outcome) -> int:
    if outcome in _INFO_OUTCOMES or outcome is Outcome.RAN:
        return 0
    if outcome is Outcome.MISSING_STRATEGY:
        # Config error: the operator pointed at a strategy that isn't on
        # disk. Distinct exit code so monitoring can flag config drift
        # separately from data/strategy failures.
        return 2
    return 1


# ---------------------------------------------------------------------------
# Pure helper: signal → suggestion rows
# ---------------------------------------------------------------------------


def diff_signal(
    signal: dict[str, float],
    current_qty: dict[str, int],
    closes: dict[str, float],
    portfolio_value: float,
) -> list[dict]:
    """Translate a ``{symbol: target_weight}`` signal into a list of
    suggestion-row payloads suitable for ``insert_suggestion(...)``.

    Algorithm (mirrors the backtest engine's
    ``framework/backtest/engine.py::_diff_orders``):

        target_dollars  = weight * portfolio_value
        target_qty      = int(target_dollars // close)   # floor (no fractional shares)
        delta           = target_qty - current_qty

    Per-row rules:

    * ``delta == 0`` AND ``weight > 0`` AND ``current > 0`` → ``hold``
      (audit-trail row; the strategy considered the symbol today).
    * ``delta == 0`` AND ``current == 0`` → skip (selling 0 of 0 is a no-op).
    * ``delta > 0`` → ``buy`` with ``target_qty``.
    * ``delta < 0`` → ``sell`` with ``target_qty``.

    Symbols whose ``close`` is missing are silently dropped (the runner
    has already logged the data gap; UI renders the "数据延迟" badge).
    Symbols the strategy did not mention are NOT auto-sold — by spec
    (strategy-interface.md §"Signal contract") the strategy's signal
    represents the desired end state, and omission is a strategy decision.

    Returns a list of dicts with keys ``symbol``, ``action``,
    ``target_qty``, ``target_price`` — the exact kwargs ``insert_suggestion``
    accepts (plus ``reason=None``; strategies have no per-symbol reason
    channel in MVP).
    """
    out: list[dict] = []
    for symbol, weight in signal.items():
        close = closes.get(symbol)
        if close is None or close <= 0:
            continue
        target_dollars = float(weight) * float(portfolio_value)
        target_qty = int(target_dollars // close) if target_dollars > 0 else 0
        current = int(current_qty.get(symbol, 0))
        delta = target_qty - current
        if delta == 0:
            # Audit-trail hold only when there's something to hold.
            if weight > 0 and current > 0:
                out.append({
                    "symbol": symbol,
                    "action": "hold",
                    "target_qty": current,
                    "target_price": float(close),
                    "reason": None,
                })
            continue
        if delta > 0:
            out.append({
                "symbol": symbol,
                "action": "buy",
                "target_qty": target_qty,
                "target_price": float(close),
                "reason": None,
            })
        else:  # delta < 0
            out.append({
                "symbol": symbol,
                "action": "sell",
                "target_qty": target_qty,
                "target_price": float(close),
                "reason": None,
            })
    return out


# ---------------------------------------------------------------------------
# Active strategy pointer
# ---------------------------------------------------------------------------


CONFIG_KEY_ACTIVE_STRATEGY = "active_strategy_id"


def load_active_strategy_id(db_path: Path | str) -> Optional[str]:
    """Read the dashboard's "current strategy" pointer from the ``config``
    table. ``None`` if the user has not activated a strategy yet.

    Opens its own connection (the runner is a standalone process per
    CAP-7; it must not import ``framework.ui_runtime``).
    """
    p = str(db_path)
    conn = open_db(p)
    try:
        ensure_schema(conn)                # idempotent on a fresh DB
        return get_config(conn, CONFIG_KEY_ACTIVE_STRATEGY)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------


def resolve_universe(strategy_cls: type, state: dict) -> list[str]:
    """Pick the tradeable universe, preferring ``state.json`` overrides.

    Returns a list (possibly empty). An empty return triggers a sentinel
    row in :func:`run_once` (NO_UNIVERSE outcome).
    """
    state_universe = state.get("universe")
    if isinstance(state_universe, list) and state_universe:
        return [str(s) for s in state_universe]
    default = getattr(strategy_cls, "default_universe", None)
    if isinstance(default, list) and default:
        return [str(s) for s in default]
    return []


# ---------------------------------------------------------------------------
# Run-once orchestrator
# ---------------------------------------------------------------------------


# Sentinel reason prefixes (the UI reads these via list_suggestions /
# list_pending_suggestions to render badges — see CAP-7 success).
_SENTINEL_DATA = "data_unavailable"
_SENTINEL_STRATEGY = "strategy_exception"
_SENTINEL_UNIVERSE = "no_universe"


@dataclass
class _BookSnapshot:
    """In-memory copy of a strategy's virtual book, used to build the
    Context the strategy receives. Pulled out so the function reads as a
    small pipeline (load → snapshot → context → generate → diff → write)."""

    cash: float
    portfolio_value: float
    current_qty: dict[str, int]
    current_avg_cost: dict[str, float]
    opened_at: dict[str, date]


def _snapshot_book(
    conn: sqlite3.Connection,
    strategy_id: str,
    closes: dict[str, float],
) -> _BookSnapshot:
    """Read the virtual book + today's closes into the shape the Context
    expects.

    Falls back to a freshly bootstrapped ``DEFAULT_INITIAL_CASH`` book if
    the strategy has never run before — the runner is the first place a
    strategy's book gets created when no backtest preceded it (the
    apply path also bootstraps; this matches).
    """
    book = get_virtual_book(conn, strategy_id)
    if book is None:
        cash = DEFAULT_INITIAL_CASH
    else:
        cash = float(book.cash)

    positions = list_virtual_positions(conn, strategy_id)
    current_qty = {p.symbol: int(p.qty) for p in positions}
    current_avg_cost = {p.symbol: float(p.avg_cost) for p in positions}
    opened_at = {p.symbol: p.opened_at for p in positions}

    # portfolio_value = cash + sum(qty * close) for symbols with a close.
    # Symbols without today's close contribute 0 — they're a data gap the
    # sentinel row already flagged.
    market_value = sum(
        current_qty[s] * closes[s]
        for s in current_qty
        if s in closes and closes[s] > 0
    )
    return _BookSnapshot(
        cash=cash,
        portfolio_value=cash + market_value,
        current_qty=current_qty,
        current_avg_cost=current_avg_cost,
        opened_at=opened_at,
    )


def _fetch_closes(
    adapter: Any,
    universe: list[str],
    today: date,
) -> dict[str, float]:
    """Today's close for each universe member. Skip symbols the adapter
    can't resolve (network error, unknown symbol) — ``diff_signal`` will
    drop those rows silently.

    Raises whatever the adapter raises on a *catastrophic* failure (e.g.
    AKShare exception on the first symbol). The caller catches that and
    writes the data_unavailable sentinel.
    """
    closes: dict[str, float] = {}
    start = today - timedelta(days=10)            # 10-day lookback; we take the last bar
    for symbol in universe:
        result = adapter.get_bars(
            symbol,
            start=start,
            end=today,
            adj="qfq",
            frequency="daily",
        )
        df = result.df
        if df is None or df.empty:
            continue
        last_close = float(df.iloc[-1]["close"])
        if last_close > 0:
            closes[symbol] = last_close
    return closes


def _write_sentinel(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    reason_prefix: str,
    detail: str,
) -> None:
    """Insert a single ``hold``-action sentinel row with ``reason`` carrying
    the failure mode. ``applied`` stays 0 — the UI can render these as
    "今日调度未跑通，原因：…" without polluting the action plan.

    Ensures the strategy's ``virtual_books`` row exists first: the FK from
    ``strategy_suggestions`` requires it, and the apply path also bootstraps
    on first use — we mirror that to keep the schema consistent regardless
    of which code path touched the strategy first.
    """
    _ensure_book_row(conn, strategy_id)
    insert_suggestion(
        conn,
        strategy_id=strategy_id,
        symbol="-",                            # sentinel; not a real symbol
        action="hold",
        target_qty=None,
        target_price=None,
        confidence=None,
        reason=f"{reason_prefix}: {detail}",
    )


def _write_sentinel_and_commit(
    db_path: str,
    *,
    strategy_id: str,
    reason_prefix: str,
    detail: str,
) -> None:
    """Open a short-lived connection, write a sentinel row, commit, close.

    Used by failure branches that don't already hold a connection
    (NO_UNIVERSE before the snapshot is taken, __init__ raising before
    the per-run connection opens). Idempotent against the per-run path:
    ``_ensure_book_row`` makes the INSERT safe regardless of whether the
    book row already exists from a prior half-run.
    """
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        _write_sentinel(
            conn, strategy_id=strategy_id,
            reason_prefix=reason_prefix, detail=detail,
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_book_row(conn: sqlite3.Connection, strategy_id: str) -> None:
    """Insert a fresh ``virtual_books`` row with the default initial cash
    iff the strategy has never run before. Idempotent — matches
    ``framework.persistence.repo.ensure_virtual_book``."""
    row = conn.execute(
        "SELECT 1 FROM virtual_books WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchone()
    if row is not None:
        return
    conn.execute(
        "INSERT INTO virtual_books (strategy_id, cash, initial_cash) "
        "VALUES (?, ?, ?)",
        (strategy_id, DEFAULT_INITIAL_CASH, DEFAULT_INITIAL_CASH),
    )


def _resolve_adapter(adapter: Optional[Any]) -> Any:
    """Lazy-import AKShareAdapter so test runs (and any environment without
    network) can inject a fake without triggering ``import akshare`` at
    module load. Production callers leave ``adapter=None`` to get the
    real one.

    ``AKShareAdapter`` is bound to this module on first resolution so
    subsequent calls reuse the class. Tests ``monkeypatch.setattr`` it.
    """
    global AKShareAdapter
    if adapter is not None:
        return adapter
    if AKShareAdapter is None:
        from framework.data.adapter import AKShareAdapter as _AK
        AKShareAdapter = _AK
    return AKShareAdapter()


def run_once(
    *,
    db_path: Path | str,
    strategies_dir: Path | str,
    adapter: Optional[Any] = None,
    today: Optional[date] = None,
    strategy_id: Optional[str] = None,
) -> Outcome:
    """Execute the 15:00 run, end-to-end.

    Parameters
    ----------
    db_path
        Path to the SQLite file. The runner opens its own connection;
        it never shares one with the Streamlit process (CAP-7).
    strategies_dir
        Path to the ``strategies/`` package whose ``.py`` files the
        discoverer scans.
    adapter
        Optional pre-built data adapter. Tests inject a fake; production
        passes ``None`` to get a real ``AKShareAdapter``.
    today
        Override the "what day is it" clock. ``None`` → ``date.today()``.
        Tests pin a deterministic date.
    strategy_id
        Override the ``active_strategy_id`` config pointer. ``None`` → read
        from the ``config`` table.

    Returns
    -------
    :class:`Outcome`. Side effects: zero or more rows in
    ``strategy_suggestions`` (always committed before return).

    The contract — what rows appear per outcome:

    =====================  ==========================================
    Outcome                ``strategy_suggestions`` rows
    =====================  ==========================================
    NO_ACTIVE_STRATEGY     none
    NOT_TRADING_DAY        none
    RAN                    one row per (symbol, action) emitted by
                           ``diff_signal``; no sentinels
    STRATEGY_EXCEPTION     one sentinel ``hold`` row, reason
                           ``strategy_exception: <detail>``
    DATA_UNAVAILABLE       one sentinel ``hold`` row, reason
                           ``data_unavailable: <detail>``
    NO_UNIVERSE            one sentinel ``hold`` row, reason
                           ``no_universe: <detail>``
    MISSING_STRATEGY       none
    =====================  ==========================================
    """
    today = today or date.today()
    db_path = str(db_path)
    strategies_dir_p = Path(strategies_dir)

    # ---- 1) Resolve strategy id -----------------------------------------
    if strategy_id is None:
        strategy_id = load_active_strategy_id(db_path)
    if not strategy_id:
        _log.info("no active strategy in config; skipping")
        return Outcome.NO_ACTIVE_STRATEGY

    # ---- 2) Discover + instantiate the strategy ------------------------
    registry = discover_strategies(strategies_dir_p, force_reload=False)
    strategy_cls = registry.get(strategy_id)
    if strategy_cls is None:
        _log.error(
            "active strategy %r not found in %s; check strategies_dir and config",
            strategy_id, strategies_dir_p,
        )
        return Outcome.MISSING_STRATEGY

    # ---- 3) Trading-day check (CAP-7 §"Trading-day awareness") ----------
    adapter = _resolve_adapter(adapter)
    try:
        cal = adapter.get_calendar(today, today)
    except Exception as exc:
        _log.warning("get_calendar failed: %s; treating as non-trading day", exc)
        return Outcome.NOT_TRADING_DAY
    if today not in cal:
        _log.info("%s is not a trading day; skipping", today.isoformat())
        return Outcome.NOT_TRADING_DAY

    # ---- 4) Load state + params + universe -----------------------------
    state = load_state(strategies_dir_p, strategy_id)
    universe = resolve_universe(strategy_cls, state)
    if not universe:
        _log.error(
            "strategy %r has no universe (state.json['universe'] and "
            "default_universe both missing/empty)", strategy_id,
        )
        _write_sentinel_and_commit(
            db_path, strategy_id=strategy_id,
            reason_prefix=_SENTINEL_UNIVERSE,
            detail="state.json['universe'] and default_universe both missing",
        )
        return Outcome.NO_UNIVERSE

    params = load_params(strategies_dir_p, strategy_id) or {}
    try:
        instance = strategy_cls(**params)
    except Exception as exc:
        _log.exception("strategy %r __init__ raised", strategy_id)
        _write_sentinel_and_commit(
            db_path, strategy_id=strategy_id,
            reason_prefix=_SENTINEL_STRATEGY,
            detail=f"__init__ raised {type(exc).__name__}: {exc}",
        )
        return Outcome.STRATEGY_EXCEPTION

    # ---- 5) Fetch today's closes ---------------------------------------
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        try:
            closes = _fetch_closes(adapter, universe, today)
        except Exception as exc:
            _log.exception("data adapter failed while fetching closes")
            _write_sentinel(
                conn, strategy_id=strategy_id,
                reason_prefix=_SENTINEL_DATA,
                detail=f"{type(exc).__name__}: {exc}",
            )
            conn.commit()
            return Outcome.DATA_UNAVAILABLE

        # If the adapter silently returned nothing for the universe, treat
        # the same as a hard failure (sentinel + DATA_UNAVAILABLE). The
        # runner can't quote prices, so it can't diff the signal.
        if not closes:
            _write_sentinel(
                conn, strategy_id=strategy_id,
                reason_prefix=_SENTINEL_DATA,
                detail="no closes returned for any universe symbol",
            )
            conn.commit()
            return Outcome.DATA_UNAVAILABLE

        # ---- 6) Build context + call generate ----------------------------
        snapshot = _snapshot_book(conn, strategy_id, closes)
        ctx = Context(
            now=today,
            universe=universe,
            adapter=adapter,
            positions={},                # rebuilt below only if needed by the strategy
            cash=snapshot.cash,
            portfolio_value=snapshot.portfolio_value,
            trades=[],
            state=state,
        )
        # Populate ctx.positions with the strategy's view of its own book,
        # matching the shape Context expects: symbol -> Position.
        from framework.strategy import Position
        ctx.positions = {
            s: Position(
                symbol=s,
                qty=snapshot.current_qty.get(s, 0),
                avg_cost=snapshot.current_avg_cost.get(s, 0.0),
                opened_at=snapshot.opened_at.get(s, today),
            )
            for s in snapshot.current_qty
        }

        try:
            signal = instance.generate(ctx)
        except Exception as exc:
            _log.exception("strategy %r generate() raised", strategy_id)
            _write_sentinel(
                conn, strategy_id=strategy_id,
                reason_prefix=_SENTINEL_STRATEGY,
                detail=f"generate() raised {type(exc).__name__}: {exc}",
            )
            conn.commit()
            return Outcome.STRATEGY_EXCEPTION

        # ---- 7) Validate the signal (out-of-universe, weight>1, sum>1) -
        try:
            validate_signal(signal, universe)
        except Exception as exc:
            _log.exception("strategy %r signal invalid", strategy_id)
            _write_sentinel(
                conn, strategy_id=strategy_id,
                reason_prefix=_SENTINEL_STRATEGY,
                detail=f"signal invalid: {exc}",
            )
            conn.commit()
            return Outcome.STRATEGY_EXCEPTION

        # ---- 8) Diff + write -------------------------------------------
        actions = diff_signal(
            signal=signal,
            current_qty=snapshot.current_qty,
            closes=closes,
            portfolio_value=snapshot.portfolio_value,
        )
        for row in actions:
            insert_suggestion(
                conn,
                strategy_id=strategy_id,
                **row,
            )
        conn.commit()
        _log.info(
            "wrote %d suggestion rows for strategy %r on %s",
            len(actions), strategy_id, today.isoformat(),
        )
    finally:
        conn.close()

    return Outcome.RAN


# ---------------------------------------------------------------------------
# CLI (python -m framework.runner.scheduled_run)
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m framework.runner.scheduled_run",
        description=(
            "Run the active strategy once and write its daily action plan "
            "to data/app.db. Called by the 15:00 cron (scheduler.md). "
            "Exit 0 = happy / nothing-to-do; exit 1 = data or strategy "
            "failure (sentinel row written); exit 2 = config error "
            "(strategy id points at a missing file)."
        ),
    )
    p.add_argument(
        "--db-path", default="data/app.db",
        help="Path to the SQLite database (default: %(default)s).",
    )
    p.add_argument(
        "--strategies-dir", default="strategies",
        help="Path to the strategies/ package (default: %(default)s).",
    )
    p.add_argument(
        "--today", default=None,
        help="Override 'today' for tests / dry-runs (YYYY-MM-DD).",
    )
    p.add_argument(
        "--strategy", default=None,
        help="Override the active-strategy config pointer. "
             "Useful for backfilling a specific strategy.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        help="Logging level (default: %(default)s).",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. ``argv=None`` reads ``sys.argv[1:]`` (the standard
    pattern so ``python -m framework.runner.scheduled_run`` works)."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    today: Optional[date] = None
    if args.today:
        today = date.fromisoformat(args.today)

    outcome = run_once(
        db_path=args.db_path,
        strategies_dir=args.strategies_dir,
        today=today,
        strategy_id=args.strategy,
    )
    rc = _outcome_to_exit_code(outcome)
    _log.info("outcome=%s exit_code=%d", outcome.value, rc)
    return rc


__all__ = [
    "Outcome",
    "diff_signal",
    "load_active_strategy_id",
    "resolve_universe",
    "run_once",
    "main",
    "CONFIG_KEY_ACTIVE_STRATEGY",
]