"""framework/data/adapter.py

The single entry point for fetching market data. Strategies, engine, runner,
and UI all go through `AKShareAdapter`. **This file is the only place in the
codebase that imports `akshare`** (CAP-1, Constraint 1; CI grep enforces it).

AKShare is the primary source. Accepted gaps (T1, do not silently fix):
  * North-bound capital (`stock_hsgt_hist_em`) dead since 2024-08-19.
  * Adjustment-factor precision (ST / IPO / restructuring) ≥10% cumulative
    error on some names.

Data-source fallback:
  * Eastmoney (`ak.stock_zh_a_hist`) is primary. On any exception, daily
    frequency falls back to Tencent (`ak.stock_zh_a_hist_tx`) before the
    parquet-cache fallback kicks in. Tencent is reachable from a wider set
    of egress IPs than eastmoney's kline API and serves both A-shares and
    Shenzhen/Shanghai ETFs through one endpoint.
  * Minute frequencies have no Tencent fallback — eastmoney remains the
    sole source. On failure, the cache fallback applies as before.
  * Tencent does not expose share-count `volume`; we approximate it from
    `amount / (close × 100)` (手). Precision is approximate but visually
    equivalent for the volume sub-chart and OBV indicator.

Failure contract (per `data-adapter.md`):
  * AKShare exception → return cache (with `stale_seconds > 0`) if present;
    raise `DataAdapterUnavailable` if cache is also missing.
  * AKShare returns empty DataFrame → raise `EmptyBarsError` (do NOT silently
    substitute cache).
  * Unknown symbol → raise `UnknownSymbolError`.
  * Rate-limit would be exceeded → block (handled by `ratelimit` decorator
    on the global token bucket; never silently drop).

Implementation note on the proxy bypass
---------------------------------------
On macOS, `requests` auto-loads the system proxy via `urllib.request.getproxies()`
(the `_scproxy` module reads `scutil --proxy`). If the proxy daemon (ClashX /
Surge / etc.) is not running, every AKShare HTTP call fails with
`ProxyError('Unable to connect to proxy', RemoteDisconnected(...))`.

By default this module does NOT bypass the proxy — the system proxy is usually
the correct egress path to eastmoney from this region. Set `AKSHARE_DIRECT=1`
in the environment to opt into `trust_env=False` on every `requests.Session`,
which makes AKShare ignore both env proxy vars and the macOS system proxy and
go straight to eastmoney. Use this only when the proxy daemon is reliably down
AND the upstream accepts direct calls from your IP; some eastmoney endpoints
refuse direct connections from non-Chinese or WAF-flagged IPs.

Tests stub `_fetch_bars` and never construct a real Session, so this is
invisible to the test suite.
"""

from __future__ import annotations

import os
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


# ---------------------------------------------------------------------------
# Proxy bypass — see module docstring, "Implementation note on the proxy bypass".
# Opt-in via env var `AKSHARE_DIRECT=1`. Default: leave the proxy path intact.
# ---------------------------------------------------------------------------


def _force_direct_connection() -> None:
    """Patch `requests.Session.__init__` so every Session created anywhere in
    the process defaults to `trust_env=False`.

    This must run at module import time so that any Session AKShare constructs
    per-call (inside its `ak.stock_zh_a_hist` / `ak.stock_zh_a_hist_min_em` /
    `ak.stock_zh_a_spot_em` / etc. functions) inherits the bypass.
    """
    import requests

    _original_init = requests.Session.__init__

    def _patched_init(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        self.trust_env = False

    requests.Session.__init__ = _patched_init  # type: ignore[assignment]


if os.environ.get("AKSHARE_DIRECT") == "1":
    _force_direct_connection()


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
        """Call AKShare and return a normalized DataFrame (English columns).

        Eastmoney is the primary source. On exception, fall back to Tencent
        (`ak.stock_zh_a_hist_tx`) for daily frequency — Tencent is reachable
        from a wider set of egress IPs than eastmoney's kline API, and serves
        both stocks and ETFs through the same endpoint.

        If both fail, the original eastmoney exception propagates so the
        caller's cache fallback (`get_bars`) still kicks in.
        """
        # Import only here — keeps the rest of the codebase akshare-free.
        import akshare as ak

        try:
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
        except Exception as eastmoney_exc:
            # Eastmoney unreachable / refused (e.g. egress IP is WAF-flagged).
            # Try Tencent — daily only; minute frequencies have no fallback.
            if frequency != "daily":
                raise
            try:
                tx_df = _fetch_bars_tx(symbol, start, end, adj)
            except Exception as tx_exc:
                # Both upstreams failed — surface the original eastmoney error
                # so the message is the one the operator can act on.
                raise eastmoney_exc from tx_exc
            if tx_df is None or tx_df.empty:
                # Tencent doesn't recognize the symbol; let the caller treat
                # this as an empty response.
                return tx_df
            return tx_df

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


# ---------------------------------------------------------------------------
# Tencent fallback (ak.stock_zh_a_hist_tx)
# ---------------------------------------------------------------------------


def _tencent_symbol(symbol: str) -> str:
    """Add the `sh` / `sz` market prefix that `ak.stock_zh_a_hist_tx` requires.

    A-share convention by first digit:
      * `5/6/9` → Shanghai (A-shares, ETFs, B-shares, STAR)
      * `0/1/2/3` → Shenzhen (A-shares, ETFs, B-shares, ChiNext)
    """
    s = symbol.strip().lower()
    if s.startswith(("sh", "sz")):
        return s
    return ("sh" if s[0] in "569" else "sz") + s


def _fetch_bars_tx(
    symbol: str, start: date, end: date, adj: Adj
) -> pd.DataFrame | None:
    """Daily bars via Tencent (`ak.stock_zh_a_hist_tx`).

    Columns are `[date, open, close, high, low, amount]` — Tencent does not
    expose share-count volume. We approximate `volume` from `amount / close`
    so the volume sub-chart and OBV indicator keep working. Precision is
    approximate (uses close instead of VWAP) but visually equivalent.
    """
    import akshare as ak

    df = ak.stock_zh_a_hist_tx(
        symbol=_tencent_symbol(symbol),
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        adjust="" if adj == "none" else adj,
    )
    if df is None or df.empty:
        return df
    # Cast numeric columns (AKShare returns strings / objects).
    for col in ("open", "close", "high", "low", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    # Approximate volume (手 = 100 股). NaN-safe: NaN close → 0; no row drops.
    close_safe = df["close"].where(df["close"] > 0)
    df["volume"] = (df["amount"] / (close_safe * 100.0)).where(
        close_safe.notna(), other=pd.NA
    )
    df = df[_OUT_COLUMNS].copy()
    df = df.sort_values("date").reset_index(drop=True)
    return df