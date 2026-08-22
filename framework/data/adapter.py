"""framework/data/adapter.py

The single entry point for fetching market data. Strategies, engine, runner,
and UI all go through `AKShareAdapter`. **This file is the only place in the
codebase that imports `akshare`** (CAP-1, Constraint 1; CI grep enforces it).

AKShare is the sole source. Accepted gaps (T1, do not silently fix):
  * North-bound capital (`stock_hsgt_hist_em`) dead since 2024-08-19.
  * Adjustment-factor precision (ST / IPO / restructuring) ≥10% cumulative
    error on some names.

Failure contract (per `data-adapter.md`):
  * AKShare exception → return cache (with `stale_seconds > 0`) if present;
    raise `DataAdapterUnavailable` if cache is also missing.
  * AKShare returns empty DataFrame → raise `EmptyBarsError` (do NOT silently
    substitute cache).
  * Unknown symbol → raise `UnknownSymbolError`.
  * Rate-limit would be exceeded → block (handled by `ratelimit` decorator
    on the global token bucket; never silently drop).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from framework.data.cache import (
    cache_file_age_seconds,
    cache_path,
    read_cache,
    write_cache,
)
from framework.data.ratelimit import ratelimit

Frequency = Literal["daily", "1m", "5m", "15m", "60m"]
Adj = Literal["qfq", "hfq", "none"]

# AKShare period strings per data-adapter.md. "daily" stays empty for the
# daily endpoint (period="daily" selects the bar kind, not the per-bar frequency).
_FREQ_TO_AKSHARE_PERIOD: dict[Frequency, str] = {
    "daily": "",
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "60m": "60",
}

# AKShare Chinese → normalized English column map (CAP-1 schema).
_DAILY_COLUMN_MAP: dict[str, str] = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}
_MINUTE_COLUMN_MAP: dict[str, str] = {
    "时间": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}
_OUT_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DataAdapterError(Exception):
    """Base class for DataAdapter failures."""


class EmptyBarsError(DataAdapterError):
    """AKShare returned an empty DataFrame for a recognized symbol."""


class DataAdapterUnavailable(DataAdapterError):
    """Both AKShare and the parquet cache failed to satisfy the request."""


class UnknownSymbolError(DataAdapterError):
    """AKShare does not recognize the symbol."""


# ---------------------------------------------------------------------------
# Result wrapper
# ---------------------------------------------------------------------------


@dataclass
class BarsResult:
    """`get_bars` return value.

    `stale_seconds > 0` means the result came from the parquet cache after an
    AKShare failure. The UI reads this to render the "数据延迟" badge.
    """

    df: pd.DataFrame
    stale_seconds: int
    cache_hit: bool


# ---------------------------------------------------------------------------
# AKShare-backed adapter
# ---------------------------------------------------------------------------


class AKShareAdapter:
    """AKShare-only DataAdapter implementation.

    Stateless apart from the cache directory. Safe to share a single instance
    across the process — the `ratelimit` decorator synchronizes the upstream
    calls.
    """

    def __init__(self, cache_dir: Path | str | None = None):
        self._cache_dir = Path(cache_dir) if cache_dir is not None else Path("data/cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ----- bars ----------------------------------------------------------

    @ratelimit
    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adj: Adj = "qfq",
        frequency: Frequency = "daily",
    ) -> BarsResult:
        """Fetch OHLCV bars for `[start, end]`.

        On AKShare exception, falls back to the most recent parquet overlap and
        returns `stale_seconds = seconds_since_cache_refresh`. On empty
        AKShare response, raises `EmptyBarsError` (cache NOT consulted).
        """
        try:
            df = self._fetch_bars(symbol, start, end, adj, frequency)
        except UnknownSymbolError:
            raise
        except _AKShareUnknownSymbol:
            raise UnknownSymbolError(f"symbol {symbol!r} not recognized by AKShare")
        except Exception as exc:  # network, DNS, HTTP, parse, anything
            cached = read_cache(self._cache_dir, symbol, frequency, adj)
            if cached is None or cached.empty:
                raise DataAdapterUnavailable(
                    f"AKShare failed ({type(exc).__name__}: {exc}) and no cache for {symbol}"
                ) from exc
            age = cache_file_age_seconds(cache_path(self._cache_dir, symbol, frequency, adj))
            return BarsResult(df=cached, stale_seconds=age, cache_hit=True)

        if df is None or df.empty:
            raise EmptyBarsError(f"AKShare returned empty bars for {symbol}")

        write_cache(self._cache_dir, symbol, frequency, adj, df)
        return BarsResult(df=df, stale_seconds=0, cache_hit=False)

    def _fetch_bars(
        self, symbol: str, start: date, end: date, adj: Adj, frequency: Frequency
    ) -> pd.DataFrame | None:
        """Call AKShare and return a normalized DataFrame (English columns)."""
        # Import only here — keeps the rest of the codebase akshare-free.
        import akshare as ak

        if frequency == "daily":
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="" if adj == "none" else adj,
            )
            return _normalize(df, _DAILY_COLUMN_MAP)
        # minute / minute-em endpoint
        period_str = _FREQ_TO_AKSHARE_PERIOD[frequency]
        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol,
            period=period_str,
            start_date=start.strftime("%Y-%m-%d") + " 09:30:00",
            end_date=end.strftime("%Y-%m-%d") + " 15:00:00",
            adjust="" if adj == "none" else adj,
        )
        return _normalize(df, _MINUTE_COLUMN_MAP)

    # ----- fundamentals --------------------------------------------------

    @ratelimit
    def get_fundamentals(self, symbol: str) -> dict[str, Any]:
        """Return at least `pe` / `pb` / `dividend_yield` keys.

        Best-effort: each upstream call is wrapped in try/except. If a metric
        is unavailable, the value is `None` but the key is always present.
        Raises `UnknownSymbolError` only when AKShare definitively rejects the
        symbol (no fallback cache for fundamentals).
        """
        import akshare as ak

        result: dict[str, Any] = {"pe": None, "pb": None, "dividend_yield": None}

        # PE / PB from long-term indicator source
        try:
            df = ak.stock_a_indicator_lg(symbol=symbol)
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                for src_key, dst_key in (("pe", "pe"), ("pb", "pb")):
                    if src_key in df.columns:
                        v = latest[src_key]
                        if pd.notna(v):
                            try:
                                result[dst_key] = float(v)
                            except (TypeError, ValueError):
                                pass
        except Exception:
            pass

        # Dividend yield: try the spot-quote source which carries 股息率.
        # NOTE: this fetches the entire A-share spot table once; for MVP this
        # is acceptable (called once per page view, not per bar). If this
        # becomes a hotspot we should switch to a per-symbol call.
        try:
            spot = ak.stock_zh_a_spot_em()
            if spot is not None and not spot.empty and "代码" in spot.columns:
                row = spot[spot["代码"] == symbol]
                if not row.empty and "股息率" in spot.columns:
                    v = row.iloc[0]["股息率"]
                    if pd.notna(v):
                        try:
                            result["dividend_yield"] = float(v)
                        except (TypeError, ValueError):
                            pass
        except Exception:
            pass

        # If we got nothing useful at all, treat as unknown symbol
        if all(v is None for v in result.values()):
            raise UnknownSymbolError(
                f"no fundamentals available for {symbol!r} (AKShare returned empty)"
            )

        return result

    # ----- calendar ------------------------------------------------------

    @ratelimit
    def get_calendar(self, start: date, end: date) -> list[date]:
        """Trading days in `[start, end]` (inclusive)."""
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return []
        date_col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        dates = pd.to_datetime(df[date_col]).dt.date
        return sorted(d for d in dates if start <= d <= end)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AKShareUnknownSymbol(Exception):
    """Internal sentinel — never raised to callers."""


def _normalize(df: pd.DataFrame | None, colmap: dict[str, str]) -> pd.DataFrame | None:
    """Rename AKShare's Chinese columns to the canonical English schema and
    sort by date. Returns None for empty input."""
    if df is None or df.empty:
        return None
    out = df.rename(columns=colmap)
    # AKShare sometimes appends extra columns; keep only the canonical schema
    missing = [c for c in _OUT_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"AKShare response missing expected columns: {missing}")
    out = out[_OUT_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out = out.sort_values("date").reset_index(drop=True)
    return out