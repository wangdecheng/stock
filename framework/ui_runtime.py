"""framework/ui_runtime.py

Shared runtime helpers for the Streamlit pages (T5). Thin layer that:

* opens (and caches) the SQLite connection
* resolves the currently-active strategy id from the ``config`` table
* exposes the strategies dir as a stable path

Pages import only ``from framework.ui_runtime import ...`` — they do NOT
import framework.persistence or framework.strategy directly so the wiring
is in one place and easy to swap out for tests (see
``tests/test_ui_t5.py`` for the monkeypatch points).
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Optional

from framework.persistence import (
    ensure_schema,
    get_config,
    open_db,
)


# Default DB lives next to the repo per spec (architecture-diagrams.md
# §"File layout"). The path is overridable through ``AISTOCK_DB_PATH`` so
# tests can point at a tempdir without touching ``data/app.db``.
DEFAULT_DB_PATH = Path("data/app.db")

# strategies/ sits at the repo root. Same override-via-env approach as
# the DB path, so tests can sandbox strategy discovery.
DEFAULT_STRATEGIES_DIR = Path("strategies")

CONFIG_KEY_ACTIVE_STRATEGY = "active_strategy_id"


@lru_cache(maxsize=1)
def _connect(db_path: str) -> sqlite3.Connection:
    """Open the SQLite connection once per process and run migrations.

    ``lru_cache`` keyed by the resolved path string so a second call with
    the same path returns the same connection — Streamlit re-runs the
    script on every widget change, and a fresh ``open_db()`` on each run
    would multiply file handles and race the WAL writer.
    """
    conn = open_db(db_path)
    ensure_schema(conn)
    return conn


def get_connection(
    db_path: Optional[Path | str] = None,
) -> sqlite3.Connection:
    """Process-wide SQLite connection. ``@st.cache_resource`` is intentionally
    avoided because the testing harness wraps every script run inside its own
    ``AppTest`` context; ``lru_cache`` keeps the conn stable across reruns
    within one process and easy to clear in tests via ``_connect.cache_clear()``.
    """
    p = str(db_path) if db_path is not None else str(DEFAULT_DB_PATH)
    return _connect(p)


def strategies_dir() -> Path:
    """Project's ``strategies/`` dir. Override via ``AISTOCK_STRATEGIES_DIR``."""
    import os
    override = os.environ.get("AISTOCK_STRATEGIES_DIR")
    if override:
        return Path(override)
    return DEFAULT_STRATEGIES_DIR


def get_active_strategy_id(
    db_path: Optional[Path | str] = None,
) -> Optional[str]:
    """Return the active strategy id from the ``config`` table, or ``None``
    if the user has not picked one yet (the dashboard shows a "请到策略
    管理页激活" prompt in that case).
    """
    conn = get_connection(db_path)
    return get_config(conn, CONFIG_KEY_ACTIVE_STRATEGY)


def set_active_strategy_id(
    strategy_id: str,
    db_path: Optional[Path | str] = None,
) -> None:
    """Persist the active strategy id. Idempotent — re-activation of the
    same id is a no-op semantically."""
    from framework.persistence import set_config
    conn = get_connection(db_path)
    set_config(conn, CONFIG_KEY_ACTIVE_STRATEGY, strategy_id)


__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_STRATEGIES_DIR",
    "CONFIG_KEY_ACTIVE_STRATEGY",
    "get_connection",
    "strategies_dir",
    "get_active_strategy_id",
    "set_active_strategy_id",
]
