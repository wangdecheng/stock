"""framework/persistence/config.py

Generic key-value config store (T5). The dashboard reads the active
strategy id from here; T6's scheduler will write to it on activation
changes. Kept tiny on purpose — anything more structured than
``json.dumps``-able should live in a real table.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional


def get_config(
    conn: sqlite3.Connection,
    key: str,
    *,
    default: Optional[Any] = None,
) -> Optional[Any]:
    """Return the JSON-decoded value for ``key``, or ``default`` if unset."""
    row = conn.execute(
        "SELECT value FROM config WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default
    return json.loads(row[0])


def set_config(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Upsert a JSON-encoded value for ``key``.

    Uses SQLite's UPSERT so the row is created on first call and updated
    on subsequent calls (no race window between SELECT and INSERT).
    """
    payload = json.dumps(value, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          updated_at = CURRENT_TIMESTAMP
        """,
        (key, payload),
    )


def delete_config(conn: sqlite3.Connection, key: str) -> None:
    """Remove a key. No-op if it doesn't exist."""
    conn.execute("DELETE FROM config WHERE key = ?", (key,))


__all__ = ["get_config", "set_config", "delete_config"]
