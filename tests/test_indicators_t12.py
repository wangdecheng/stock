"""tests/test_indicators_t12.py

Contract tests for T12 (tech-indicators). Spec:
``specs/spec-a-stock-quant/indicators-and-metrics.md``.

Seams covered
=============

* Nine required functions: ``ma``, ``ema``, ``macd``, ``bollinger``,
  ``rsi``, ``atr``, ``adx``, ``kdj``, ``obv`` — all accept an AKShare-
  style OHLCV DataFrame directly (no column rename) and return shapes
  that match the spec exactly.
* Registry seam: every function above is also reachable via
  ``framework.indicators.compute(name, df, *args, **kwargs)``; calling
  it with an unknown name raises ``UnknownIndicatorError`` once it has
  travelled through ``Context.indicator``.
* Pandas-ta reference parity — ``test_*_matches_pandas_ta_reference``
  tests in this file assert output equals pandas-ta's direct call.
  ``pandas-ta>=0.4.67b0`` requires Python >=3.12 (T12 decision: defer
  install); on Python 3.11 those parity tests are skipped via the
  ``@requires_pandas_ta`` marker below. The pure-pandas implementations
  are pinned against a synthetic OHLCV fixture so behaviour is
  deterministic and an upgrade to Python 3.12 can be cross-checked by
  re-running the (then-passing) parity tests.

Why pure-pandas and not TA-Lib / pandas-ta now
==============================================

* T12 decision (closed ticket): pandas-ta in production once base
  Python is >=3.12. Until then this file's reference-vs-pandas-ta
  comparisons are skipped.
* ``requirements.txt`` carries the seam comment — do not uncomment
  ``pandas-ta`` on this Python. The smoke tests in this file double as
  the regression gate so a future uncomment is a one-line change with
  an immediate confidence check.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from framework.indicators import (
    adx,
    atr,
    bollinger,
    compute as indicators_compute,
    ema,
    kdj,
    ma,
    macd,
    obv,
    rsi,
)
from framework.strategy.context import Context, UnknownIndicatorError


# ---------------------------------------------------------------------------
# Skipif: pandas-ta is gated on Python >=3.12 (T12 decision).
# ---------------------------------------------------------------------------

try:
    import pandas_ta as pta  # type: ignore

    _HAS_PANDAS_TA = True
except ImportError:  # pragma: no cover - skip path on Python <3.12
    _HAS_PANDAS_TA = False


requires_pandas_ta = pytest.mark.skipif(
    not _HAS_PANDAS_TA,
    reason="pandas-ta not installed (T12: requires Python >=3.12)",
)


# ---------------------------------------------------------------------------
# Shared fixture — synthetic OHLCV, deterministic
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 60) -> pd.DataFrame:
    """A deterministic, monotone-up OHLCV series with realistic intraday
    range and growing volume. 60 rows is enough that every indicator
    above has a stable trailing window.

    Columns are exactly what AKShare's daily bars emit (``open / high /
    low / close / volume / amount`` plus ``date``), so the indicator
    implementations exercise the contract that they accept AKShare-
    direct output with zero format conversion.
    """
    rng = np.random.default_rng(seed=20240822)
    base = np.linspace(10.0, 13.0, n)              # trend up
    noise = rng.normal(0.0, 0.15, n)               # small wobble
    close = np.maximum(base + noise, 1.0)
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, 0.05, n)
    high = np.maximum(close, open_) + np.abs(rng.normal(0, 0.1, n))
    low = np.minimum(close, open_) - np.abs(rng.normal(0, 0.1, n))
    low = np.maximum(low, 0.1)
    volume = (rng.integers(800, 1500, n) * 1000).astype(float)
    amount = volume * close
    return pd.DataFrame(
        {
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": open_,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "amount": amount,
        }
    )


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return _make_ohlcv(60)


# ---------------------------------------------------------------------------
# 1. Module surface — every required function exists and is callable
# ---------------------------------------------------------------------------


def test_module_exposes_all_nine_functions():
    """T12 / indicators-and-metrics.md mandates the nine signatures. Importing
    the module must give us each by name — these are the strategy-facing
    public surface."""
    from framework import indicators

    for name in ("ma", "ema", "macd", "bollinger", "rsi", "atr", "adx", "kdj", "obv"):
        assert hasattr(indicators, name), f"indicators.{name} is missing"
        assert callable(getattr(indicators, name)), f"indicators.{name} is not callable"


def test_registry_contains_all_nine_indicators():
    """All nine wrappers auto-register themselves at import so
    ``Context.indicator(name, df)`` (CAP-3) reaches them without any
    explicit wiring at the strategy layer."""
    from framework import indicators

    registered = set(indicators.allowed_names())
    expected = {"ma", "ema", "macd", "bollinger", "rsi", "atr", "adx", "kdj", "obv"}
    assert expected <= registered, f"missing registrations: {expected - registered}"


def test_unknown_indicator_raises_via_context():
    """The strategy-facing dispatcher must reject typos. Context.indicator
    is the only path strategies use; see strategy-interface.md §"Signal
    contract"."""
    from framework.strategy.context import Context, UnknownIndicatorError

    ctx = Context(now=date(2024, 1, 1), universe=["x"])
    with pytest.raises(UnknownIndicatorError):
        ctx.indicator("not_a_real_indicator", _make_ohlcv(10))


# ---------------------------------------------------------------------------
# 2. ma / ema — single-line series, simple & exponential moving averages
# ---------------------------------------------------------------------------


def test_ma_returns_close_sma(ohlcv):
    s = ma(ohlcv, length=10)
    assert isinstance(s, pd.Series)
    # ``rolling.mean()`` on a named column preserves the column name; pin it.
    assert s.name == "close"
    # Reference: rolling mean of close, length=10
    expected = ohlcv["close"].rolling(10).mean()
    pd.testing.assert_series_equal(s, expected)


def test_ema_returns_close_ewm(ohlcv):
    s = ema(ohlcv, length=10)
    assert isinstance(s, pd.Series)
    expected = ohlcv["close"].ewm(span=10, adjust=False).mean()
    pd.testing.assert_series_equal(s, expected)


def test_ma_default_length_is_20():
    """Spec signature: ``ma(df, length=20)``. The default must be 20 — the
    UI form (CAP-3) and backtest-engine readers rely on it being
    industry-standard."""
    df = _make_ohlcv(40)
    s_default = ma(df)
    s_explicit = ma(df, length=20)
    pd.testing.assert_series_equal(s_default, s_explicit)


# ---------------------------------------------------------------------------
# 3. macd — DataFrame with macd / hist / signal columns
# ---------------------------------------------------------------------------


def test_macd_returns_three_columns(ohlcv):
    out = macd(ohlcv)
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == ["macd", "hist", "signal"]


def test_macd_hist_equals_macd_minus_signal(ohlcv):
    """hist = macd_line - signal_line is the canonical relationship and
    what every chart layer (T10) expects to plot."""
    out = macd(ohlcv)
    pd.testing.assert_series_equal(
        out["hist"],
        out["macd"] - out["signal"],
        check_names=False,
    )


# ---------------------------------------------------------------------------
# 4. bollinger — DataFrame with lower / mid / upper
# ---------------------------------------------------------------------------


def test_bollinger_returns_three_columns(ohlcv):
    out = bollinger(ohlcv, length=20, std=2.0)
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == ["lower", "mid", "upper"]


def test_bollinger_mid_equals_sma_and_band_symmetry(ohlcv):
    """mid == SMA(n); upper/lower are symmetric about mid with width
    ``2 * std * sigma``."""
    out = bollinger(ohlcv, length=20, std=2.0)
    pd.testing.assert_series_equal(
        out["mid"],
        ohlcv["close"].rolling(20).mean(),
        check_names=False,
    )
    # symmetry
    upper_gap = out["upper"] - out["mid"]
    lower_gap = out["mid"] - out["lower"]
    pd.testing.assert_series_equal(upper_gap, lower_gap, check_names=False)


# ---------------------------------------------------------------------------
# 5. rsi — Wilder's RSI in [0, 100]
# ---------------------------------------------------------------------------


def test_rsi_returns_series_within_bounds(ohlcv):
    s = rsi(ohlcv, length=14)
    assert isinstance(s, pd.Series)
    finite = s.dropna()
    assert ((finite >= 0.0) & (finite <= 100.0)).all(), "RSI out of [0,100]"


def test_rsi_default_length_is_14():
    """Spec signature: ``rsi(df, length=14)``."""
    df = _make_ohlcv(40)
    pd.testing.assert_series_equal(rsi(df), rsi(df, length=14))


# ---------------------------------------------------------------------------
# 6. atr — True Range, Wilder smoothing
# ---------------------------------------------------------------------------


def test_atr_non_negative_and_default_length_14(ohlcv):
    s = atr(ohlcv, length=14)
    assert isinstance(s, pd.Series)
    finite = s.dropna()
    assert (finite >= 0.0).all(), "ATR must be non-negative"
    pd.testing.assert_series_equal(atr(ohlcv), s)  # default 14


# ---------------------------------------------------------------------------
# 7. adx — DataFrame with adx / plus_di / minus_di
# ---------------------------------------------------------------------------


def test_adx_returns_three_columns(ohlcv):
    out = adx(ohlcv, length=14)
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == ["adx", "plus_di", "minus_di"]


def test_adx_values_in_zero_to_one_hundred(ohlcv):
    """ADX and DI components are percentages in [0, 100]."""
    out = adx(ohlcv, length=14)
    finite = out.dropna()
    for col in ("adx", "plus_di", "minus_di"):
        v = finite[col]
        assert ((v >= 0.0) & (v <= 100.0)).all(), f"{col} out of [0,100]"


# ---------------------------------------------------------------------------
# 8. kdj — DataFrame with k / d / j, j = 3k - 2d
# ---------------------------------------------------------------------------


def test_kdj_returns_three_columns(ohlcv):
    out = kdj(ohlcv)
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == ["k", "d", "j"]


def test_kdj_j_equals_3k_minus_2d(ohlcv):
    out = kdj(ohlcv)
    pd.testing.assert_series_equal(
        out["j"],
        3 * out["k"] - 2 * out["d"],
        check_names=False,
    )


# ---------------------------------------------------------------------------
# 9. obv — single Series, monotonic cumsum of signed volume
# ---------------------------------------------------------------------------


def test_obv_returns_series(ohlcv):
    s = obv(ohlcv)
    assert isinstance(s, pd.Series)


def test_obv_step_sign_matches_close_direction(ohlcv):
    """On a day close[t] > close[t-1], OBV must step up by volume[t];
    on a down-day, step down; unchanged close → 0 step. The first bar
    has no previous close so the step is undefined (NaN); check from
    bar 1 onward."""
    s = obv(ohlcv)
    step = s.diff()
    expected = pd.Series(np.where(
        ohlcv["close"].diff() > 0, ohlcv["volume"],
        np.where(ohlcv["close"].diff() < 0, -ohlcv["volume"], 0.0),
    ), index=ohlcv.index)
    pd.testing.assert_series_equal(
        step.iloc[1:],
        expected.iloc[1:],
        check_names=False,
    )


# ---------------------------------------------------------------------------
# 10. AKShare-direct contract — input must not be mutated or rewritten
# ---------------------------------------------------------------------------


def test_akshare_input_is_not_mutated():
    """Spec: ``DataFrame is AKShare direct output, zero format conversion''.
    The wrappers must read from the input, not rename or coerce its
    columns."""
    df = _make_ohlcv(20)
    snapshot = df.copy(deep=True)
    from framework import indicators

    # exercise every function on the same frame
    indicators.ma(df)
    indicators.ema(df)
    indicators.macd(df)
    indicators.bollinger(df)
    indicators.rsi(df)
    indicators.atr(df)
    indicators.adx(df)
    indicators.kdj(df)
    indicators.obv(df)

    pd.testing.assert_frame_equal(df, snapshot)


# ---------------------------------------------------------------------------
# 11. Output alignment — every output shares the input index, even when NaN
# ---------------------------------------------------------------------------


def test_outputs_share_input_index(ohlcv):
    from framework import indicators

    s = indicators.ma(ohlcv, length=20)
    assert (s.index == ohlcv.index).all()

    out = indicators.macd(ohlcv)
    for col in out.columns:
        assert (out[col].index == ohlcv.index).all()


# ---------------------------------------------------------------------------
# 12. Pandas-ta reference parity — skipped until base Python moves to 3.12+
# ---------------------------------------------------------------------------


@requires_pandas_ta
def test_ma_matches_pandas_ta_reference():
    from framework.indicators import ma

    df = _make_ohlcv(60)
    out = ma(df, length=20)
    ref = pta.sma(df["close"], length=20)
    pd.testing.assert_series_equal(out, ref)


@requires_pandas_ta
def test_ema_matches_pandas_ta_reference():
    from framework.indicators import ema

    df = _make_ohlcv(60)
    out = ema(df, length=20)
    ref = pta.ema(df["close"], length=20)
    pd.testing.assert_series_equal(out, ref)


@requires_pandas_ta
def test_macd_matches_pandas_ta_reference():
    from framework.indicators import macd

    df = _make_ohlcv(60)
    out = macd(df)
    ref = pta.macd(df["close"], fast=12, slow=26, signal=9)
    # pandas-ta names the columns MACD_12_26_9 / MACDh_12_26_9 / MACDs_12_26_9
    # — only the three computed lines are compared by positional alignment.
    assert list(out.columns) == ["macd", "hist", "signal"]
    pd.testing.assert_series_equal(out["macd"], ref.iloc[:, 0], check_names=False)
    pd.testing.assert_series_equal(out["hist"], ref.iloc[:, 1], check_names=False)
    pd.testing.assert_series_equal(out["signal"], ref.iloc[:, 2], check_names=False)


@requires_pandas_ta
def test_bollinger_matches_pandas_ta_reference():
    from framework.indicators import bollinger

    df = _make_ohlcv(60)
    out = bollinger(df, length=20, std=2.0)
    ref = pta.bbands(df["close"], length=20, std=2.0)
    assert list(out.columns) == ["lower", "mid", "upper"]
    # pandas-ta returns columns BBL_20_2.0 / BBM_20_2.0 / BBU_20_2.0
    pd.testing.assert_series_equal(out["lower"], ref.iloc[:, 0], check_names=False)
    pd.testing.assert_series_equal(out["mid"], ref.iloc[:, 1], check_names=False)
    pd.testing.assert_series_equal(out["upper"], ref.iloc[:, 2], check_names=False)


@requires_pandas_ta
def test_rsi_matches_pandas_ta_reference():
    from framework.indicators import rsi

    df = _make_ohlcv(60)
    out = rsi(df, length=14)
    ref = pta.rsi(df["close"], length=14)
    pd.testing.assert_series_equal(out, ref)


@requires_pandas_ta
def test_atr_matches_pandas_ta_reference():
    from framework.indicators import atr

    df = _make_ohlcv(60)
    out = atr(df, length=14)
    ref = pta.atr(df["high"], df["low"], df["close"], length=14)
    pd.testing.assert_series_equal(out, ref)


@requires_pandas_ta
def test_adx_matches_pandas_ta_reference():
    from framework.indicators import adx

    df = _make_ohlcv(60)
    out = adx(df, length=14)
    ref = pta.adx(df["high"], df["low"], df["close"], length=14)
    # pandas-ta returns ADX_14 / DMP_14 / DMN_14
    pd.testing.assert_series_equal(out["adx"], ref.iloc[:, 0], check_names=False)
    pd.testing.assert_series_equal(out["plus_di"], ref.iloc[:, 1], check_names=False)
    pd.testing.assert_series_equal(out["minus_di"], ref.iloc[:, 2], check_names=False)


@requires_pandas_ta
def test_kdj_matches_pandas_ta_reference():
    from framework.indicators import kdj

    df = _make_ohlcv(60)
    out = kdj(df)
    # pandas-ta's ``stoch`` returns K/D in that order. Its ``k`` is the
    # lookback window, ``smooth_k`` is the SMA window for K, and ``d`` is
    # the SMA window for D. Our ``kdj(n=9, m1=3, m2=3)`` therefore matches
    # ``pta.stoch(..., k=9, d=3, smooth_k=3)``. J is our derivation, not
    # checked here.
    stoch = pta.stoch(df["high"], df["low"], df["close"], k=9, d=3, smooth_k=3)
    pd.testing.assert_series_equal(out["k"], stoch.iloc[:, 0], check_names=False)
    pd.testing.assert_series_equal(out["d"], stoch.iloc[:, 1], check_names=False)


@requires_pandas_ta
def test_obv_matches_pandas_ta_reference():
    from framework.indicators import obv

    df = _make_ohlcv(60)
    out = obv(df)
    ref = pta.obv(df["close"], df["volume"])
    pd.testing.assert_series_equal(out, ref)