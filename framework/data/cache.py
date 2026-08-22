"""framework/data/cache.py

Parquet cache for AKShare bars.

Advisory only (per `data-adapter.md`):
- Every successful `get_bars` writes through to the cache.
- On `akshare` exception, the adapter reads the most recent parquet overlap and
  returns it with a `stale_seconds` flag the UI uses to render "数据延迟".
- Cache is never used as a primary source when AKShare is healthy.
- Empty AKShare responses do NOT silently fall back to cache; they raise
  `EmptyBarsError`.

Layout: `cache_dir/<symbol>/<freq>_<adj>.parquet`. The whole directory is
gitignored (see root `.gitignore`).
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd


def cache_path(cache_dir: Path, symbol: str, freq: str, adj: str) -> Path:
    """Path to the parquet file for the given (symbol, freq, adj)."""
    return cache_dir / symbol / f"{freq}_{adj}.parquet"


def write_cache(
    cache_dir: Path, symbol: str, freq: str, adj: str, df: pd.DataFrame
) -> Path:
    """Atomically overwrite the cache file. Returns the final path."""
    path = cache_path(cache_dir, symbol, freq, adj)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)  # atomic on POSIX
    return path


def read_cache(cache_dir: Path, symbol: str, freq: str, adj: str) -> pd.DataFrame | None:
    """Read the cache if present; return None if missing."""
    path = cache_path(cache_dir, symbol, freq, adj)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def cache_file_age_seconds(path: Path) -> int:
    """Seconds since the cache file was last modified (refreshed)."""
    return max(0, int(time.time() - path.stat().st_mtime))