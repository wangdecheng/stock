"""framework/indicators/__init__.py

Indicator surface for strategies (T12 / spec-a-stock-quant
``indicators-and-metrics.md``).

Architecture
------------

This package exposes nine strategy-facing functions and a small
dispatcher that routes ``ctx.indicator(name, ...)`` calls through an
allowlist (CAP-3 prevents silent typos). Each wrapper accepts an
AKShare-style OHLCV DataFrame directly — zero format conversion —
and returns either a single ``pd.Series`` (one-line indicators) or a
``pd.DataFrame`` whose column names are pinned by the spec.

The underlying library is documented in T12's resolution:
**``pandas-ta``** was chosen for production. ``pandas-ta>=0.4.67b0``
requires Python >=3.12; on this project (currently Python 3.11) the
seam is documented in ``requirements.txt`` as a commented-out
placeholder. Until that gate lifts, the implementations below are a
pure-pandas port using the textbook formulas so behaviour is
deterministic, testable, and produces values that the (future)
``test_*_matches_pandas_ta_reference`` parity tests in
``tests/test_indicators_t12.py`` can cross-validate against once the
install unblocks. Those parity tests are ``@requires_pandas_ta``-
guarded so they auto-skip today and pass automatically once the
library lands.

Public surface
--------------

Functions
    ma, ema, macd, bollinger, rsi, atr, adx, kdj, obv — see signatures
    below.

Dispatcher
    ``register(name, fn)``     — register or unregister a wrapper.
    ``allowed_names()``        — sorted registry names (CAP-3 UI surface).
    ``compute(name, df, *a, **kw)`` — dispatcher; ``KeyError`` for unknown.

The nine wrappers auto-register themselves at import. Strategies
never call ``register``; tests use it to swap a fake (see
``tests/test_strategy_t2.py::test_context_indicator_dispatches_to_registry``).
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Registry seam — dispatcher for ``ctx.indicator(name, ...)``.
# ---------------------------------------------------------------------------

# name -> callable(df: pd.DataFrame, *args, **kwargs) -> pd.Series | pd.DataFrame
_REGISTRY: dict[str, Callable] = {}


def register(name: str, fn: Optional[Callable]) -> Callable:
    """Register or unregister an indicator.

    Tests call ``register("ma", fake_ma)`` to inject a stand-in and
    ``register("ma", None)`` to clear. Production wrappers below
    register themselves at import time, so callers don't need to do
    anything.
    """
    if fn is None:
        _REGISTRY.pop(name, None)
        return lambda *a, **k: None
    _REGISTRY[name] = fn
    return fn


def allowed_names() -> list[str]:
    """Sorted list of currently-registered indicator names (CAP-3 UI surface)."""
    return sorted(_REGISTRY)


def compute(name: str, df, *args, **kwargs):
    """Resolve ``name`` through the registry and call it with ``df``.

    Raises ``KeyError`` for an unknown name; the wrapper ``Context.indicator``
    translates that into ``UnknownIndicatorError`` (a friendlier type for
    strategy authors).
    """
    fn = _REGISTRY[name]
    return fn(df, *args, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _true_range(df: pd.DataFrame) -> pd.Series:
    """Element-wise max of (high-low), |high-prev_close|, |low-prev_close|.
    Used by ``atr`` and ``adx`` (they share the formula)."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _wilder_smooth(series: pd.Series, length: int) -> pd.Series:
    """Wilder's exponential smoothing — equivalent to ``alpha = 1 / length``
    with ``adjust=False`` on a pandas ``ewm``. ATR and ADX rely on this
    formula; pandas-ta uses the same convention."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


# ---------------------------------------------------------------------------
# 1. ma — Simple moving average over ``close``.
# ---------------------------------------------------------------------------


def ma(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Simple moving average of the close column.

    Args:
        df: AKShare-style OHLCV DataFrame (zero format conversion).
        length: window in bars. Default 20.
    """
    return df["close"].rolling(length).mean()


# ---------------------------------------------------------------------------
# 2. ema — Exponential moving average over ``close`` (Wilder-style span).
# ---------------------------------------------------------------------------


def ema(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Exponential moving average of the close column.

    ``adjust=False`` mirrors pandas-ta's convention so the reference
    parity tests align byte-for-byte once pandas-ta is installed.
    """
    return df["close"].ewm(span=length, adjust=False).mean()


# ---------------------------------------------------------------------------
# 3. macd — Moving Average Convergence / Divergence.
# ---------------------------------------------------------------------------


def macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD with three columns: ``macd``, ``hist``, ``signal``.

    Returns:
        ``pd.DataFrame`` aligned to the input index. ``hist`` is the
        bar histogram (``macd - signal``) used by every A-share chart
        UI for the colored MACD bars under the K-line.
    """
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "hist": hist, "signal": signal_line},
        index=df.index,
    )


# ---------------------------------------------------------------------------
# 4. bollinger — Bollinger Bands (lower / mid / upper).
# ---------------------------------------------------------------------------


def bollinger(
    df: pd.DataFrame,
    length: int = 20,
    std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands with three columns: ``lower``, ``mid``, ``upper``.

    The standard deviation uses ``ddof=0`` (population) to match
    pandas-ta's convention.
    """
    mid = df["close"].rolling(length).mean()
    sigma = df["close"].rolling(length).std(ddof=0)
    return pd.DataFrame(
        {
            "lower": mid - std * sigma,
            "mid": mid,
            "upper": mid + std * sigma,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# 5. rsi — Relative Strength Index (Wilder smoothing).
# ---------------------------------------------------------------------------


def rsi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder's RSI in [0, 100].

    Uses ``ewm(alpha=1/length, adjust=False)`` for the average gains /
    losses smoothing — same as pandas-ta.
    """
    delta = df["close"].diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    avg_up = _wilder_smooth(up, length)
    avg_dn = _wilder_smooth(down, length)
    # ``avg_up / 0`` yields ``+inf`` in pandas; ``100/(1+inf) == 0``, so
    # rsi correctly evaluates to 100 on an all-up streak (matches
    # pandas-ta). The ``0/0`` case (both smoothing averages zero on a
    # flat stretch) yields NaN, which propagates — that's the convention.
    rs = avg_up / avg_dn
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# 6. atr — Average True Range (Wilder smoothing).
# ---------------------------------------------------------------------------


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """True Range smoothed with Wilder's EMA. Non-negative."""
    tr = _true_range(df)
    return _wilder_smooth(tr, length)


# ---------------------------------------------------------------------------
# 7. adx — Average Directional Index (+DI / -DI / ADX).
# ---------------------------------------------------------------------------


def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """Average Directional Index with three columns: ``adx``,
    ``plus_di``, ``minus_di``. ADX and the two DIs are percentages
    in [0, 100]."""
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    tr = _true_range(df)
    atr_n = _wilder_smooth(tr, length)
    plus_di = 100.0 * _wilder_smooth(plus_dm, length) / atr_n
    minus_di = 100.0 * _wilder_smooth(minus_dm, length) / atr_n
    dx_denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / dx_denom
    adx_val = _wilder_smooth(dx, length)
    return pd.DataFrame(
        {"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di},
        index=df.index,
    )


# ---------------------------------------------------------------------------
# 8. kdj — KDJ stochastic oscillator (K / D / J).
# ---------------------------------------------------------------------------


def kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> pd.DataFrame:
    """KDJ stochastic oscillator with three columns: ``k``, ``d``, ``j``.

    Defaults match the A-share UI: 9-period raw stochastic, 3-bar SMA
    smoothing for K, 3-bar SMA smoothing for D. ``j = 3k - 2d`` is the
    canonical extension.
    """
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rng = (high_n - low_n).replace(0.0, np.nan)
    rsv = (df["close"] - low_n) / rng * 100.0
    k = rsv.rolling(m1).mean()
    d = k.rolling(m2).mean()
    j = 3.0 * k - 2.0 * d
    return pd.DataFrame({"k": k, "d": d, "j": j}, index=df.index)


# ---------------------------------------------------------------------------
# 9. obv — On-Balance Volume.
# ---------------------------------------------------------------------------


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume.

    Step rule (matches pandas-ta):
        close[t] > close[t-1]  → +volume[t]
        close[t] < close[t-1]  → -volume[t]
        close[t] == close[t-1] → 0

    Starts at 0 on bar 0 (the diff is NaN there, so the cumsum skips
    it cleanly with ``fill_value=0``).
    """
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"]).cumsum()


# ---------------------------------------------------------------------------
# Auto-register the nine wrappers in the dispatcher.
# ---------------------------------------------------------------------------

_INDICATORS: tuple[tuple[str, Callable], ...] = (
    ("ma", ma),
    ("ema", ema),
    ("macd", macd),
    ("bollinger", bollinger),
    ("rsi", rsi),
    ("atr", atr),
    ("adx", adx),
    ("kdj", kdj),
    ("obv", obv),
)

for _name, _fn in _INDICATORS:
    register(_name, _fn)


# ``__all__`` is built from the registry so adding a tenth wrapper is a
# single edit (in ``_INDICATORS``); the public surface cannot drift from
# the dispatcher.
__all__ = [
    "register",
    "allowed_names",
    "compute",
    *(name for name, _ in _INDICATORS),
]