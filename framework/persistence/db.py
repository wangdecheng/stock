"""framework/persistence/db.py

SQLite open + schema bootstrap. Spec: ``position-schema.md`` §"Database".

Single-file SQLite in WAL mode (`data/app.db`, gitignored). Schema lives
in ``schema.sql`` (T3 ships only the ``backtests`` table; T4 adds the rest).
No Alembic for MVP — schema is hand-written and idempotent (CREATE TABLE
IF NOT EXISTS), so a fresh database and an existing one converge on the
same shape.
"""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path


def open_db(path: Path | str) -> sqlite3.Connection:
    """Open (or create) the SQLite file and enable WAL.

    WAL mode makes concurrent reader/writer accesses (UI thread reading while
    a backtest thread writes) durable. We don't open across processes here —
    the Streamlit app and the 15:30 scheduler are separate processes by spec
    (CAP-7) and SQLite's locking handles that.

    ``check_same_thread=False`` lets Streamlit's worker thread share the
    same connection as the test thread (AppTest runs the page in a
    separate worker thread). SQLite serializes access internally — for
    a single-process MVP that's safe and simpler than per-thread
    connections that need explicit visibility coordination via WAL.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Execute the bundled ``schema.sql`` once. Idempotent."""
    sql_text = files("framework.persistence").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql_text)


__all__ = ["open_db", "ensure_schema"]
