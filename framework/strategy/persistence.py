"""framework/strategy/persistence.py

Per-strategy JSON side-fires that live next to the strategy Python file:

  * ``strategies/<name>.params.json`` — UI-editable parameter values
  * ``strategies/<name>.state.json``  — strategy-private persistent state

Writes are atomic (write to ``.tmp``, ``os.replace``) so a crash mid-write
cannot leave a half-written JSON on disk — a half-written JSON would crash
``json.loads`` and force a manual edit. Both files are listed in
``.gitignore`` (constraint CAP-3 / Strategy protocol).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_PARAMS_SUFFIX = ".params.json"
_STATE_SUFFIX = ".state.json"
_TMP_SUFFIX = ".tmp"


def params_path(strategies_dir: Path | str, name: str) -> Path:
    return Path(strategies_dir) / f"{name}{_PARAMS_SUFFIX}"


def state_path(strategies_dir: Path | str, name: str) -> Path:
    return Path(strategies_dir) / f"{name}{_STATE_SUFFIX}"


def _atomic_write(path: Path, payload: Any) -> None:
    """Serialize ``payload`` as JSON and write atomically.

    1. Write to ``<path>.tmp`` in the same directory (same filesystem).
    2. ``os.replace(tmp, path)`` — POSIX atomic rename.

    Same-dir tmp placement matters: a cross-filesystem rename would degrade
    to copy + unlink and lose atomicity.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=_TMP_SUFFIX, dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=False)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup — leave nothing behind on the failure path.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# params.json
# ---------------------------------------------------------------------------


def load_params(strategies_dir: Path | str, name: str) -> dict | None:
    """Return the params payload for a strategy, or ``None`` if never edited."""
    path = params_path(strategies_dir, name)
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_params(strategies_dir: Path | str, name: str, payload: dict) -> None:
    """Atomically write the params payload. ``payload`` is passed through
    ``json.dumps``; non-serializable values raise ``TypeError``."""
    path = params_path(strategies_dir, name)
    _atomic_write(path, payload)


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


def load_state(strategies_dir: Path | str, name: str) -> dict:
    """Return the persistent state for a strategy, or ``{}`` if never written."""
    path = state_path(strategies_dir, name)
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        # A corrupted or manually edited file with a non-dict root would
        # break the strategy's assumption that ctx.state is a dict.
        raise ValueError(f"{path} must contain a JSON object at the root")
    return loaded


def save_state(strategies_dir: Path | str, name: str, state: dict) -> None:
    """Atomically write the strategy's persistent state."""
    if not isinstance(state, dict):
        raise TypeError("save_state() requires a dict; strategies mutate ctx.state freely")
    path = state_path(strategies_dir, name)
    _atomic_write(path, state)
