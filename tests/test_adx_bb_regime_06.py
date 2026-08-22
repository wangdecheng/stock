"""tests/test_adx_bb_regime_06.py

T06 — Range_Bull multi-source signal logic (BB + RSI + K-line).

Covers the eight acceptance bullets from
``.scratch/adx-bb-regime/issues/06-range-bull-bb-rsi-kline-signals.md``:

  1. RANGE_BULL default bar (no signal): weight `0.5`.
  2. BB lower touch with RSI=25: weight `0.75`
     (= 0.5 + 0.5 * (30-25)/10).
  3. BB lower touch with RSI=15: weight `1.0`
     ((30-15)/10 = 1.5, clamped to 1).
  4. BB lower touch with RSI=35: weight `0.5`
     ((30-35)/10 = -0.5, clamped to 0; default applies).
  5. BB upper touch: weight `0`.
  6. Long lower shadow on a non-touch bar: buy trigger fires.
  7. Bullish engulfing on a non-touch bar: buy trigger fires.
  8. Both buy and sell on the same bar (engineered volatility): sell wins.

Test fixtures mirror ``tests/test_adx_bb_regime_05.py`` — the same
``FakeBarsResult`` / ``FakeAdapter`` / ``_swap_indicator`` pattern, and
the same ``_swap_classify`` trick used by T04 to bypass the cascade and
pin ``new_state == "RANGE_BULL"`` directly. The synthetic OHLCV is
engineered bar-by-bar so each test exercises a specific (dis)junct of
the T06 signal stack.

Why ``_swap_classify`` rather than natural RANGE_BULL classification
---------------------------------------------------------------
T06 is the *signal logic*, not the cascade. Forcing
``AdxBbRegimeStrategy._classify`` to always return ``"RANGE_BULL"``
guarantees the strategy enters the RANGE_BULL branch on every test
regardless of the underlying OHLCV (which is engineered to trigger
specific band / K-line patterns, not natural regime states).

A note on ``prev_state`` and T05 composition
-------------------------------------------
Tests seed ``ctx.state["current_state"] = "RANGE_BULL"`` so
``prev_state == new_state == "RANGE_BULL"``. T05's transition rule
requires ``prev_state != new_state`` to fire, so the Phased Exit
interpolation does NOT run — the weight the strategy emits equals the
natural weight from the T06 signal stack. This pins the assertion to
the exact value the spec dictates. Composing T05 on top of T06 is
covered by the existing T05 tests (T05's phased-exit targets are the
natural weights of the held states — 0.5 for RANGE_BULL is unchanged
whether the signal stack fires or not, since the *natural* default is
still 0.5 and the *natural* buy target is in [0.5, 1.0]).

Indicator injection for test isolation
---------------------------------------
The Bollinger Bands and RSI are injected via ``framework.indicators.
register`` so each test can pin the bands / RSI to the exact values
its engineered bar needs. ADX / MA are left real — the test
bypasses the cascade via ``_swap_classify`` so their values are
inert on the outcome.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

import framework.indicators as _indicators_mod
from framework.indicators import register as indicators_register
from framework.strategy.context import Context


# ---------------------------------------------------------------------------
# Fakes — same pattern as tests/test_adx_bb_regime_02.py / _03.py / _04.py / _05.py
# ---------------------------------------------------------------------------


class FakeBarsResult:
    """Stand-in for the adapter's BarsResult; just holds a DataFrame."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df


class FakeAdapter:
    """Stand-in for AKShareAdapter. Ignores the (start, end) filter so the
    strategy receives the full synthetic series."""

    def __init__(self, df_by_symbol: dict[str, pd.DataFrame]) -> None:
        self.df_by_symbol = df_by_symbol

    def get_bars(self, symbol: str, start, end, **kw):
        return FakeBarsResult(self.df_by_symbol[symbol])


# ---------------------------------------------------------------------------
# Synthetic OHLCV builder — constant bars with engineered last-bar OHLCV
# ---------------------------------------------------------------------------


def _constant_then_last_bar(
    n: int,
    *,
    last_open: float,
    last_high: float,
    last_low: float,
    last_close: float,
    prev_open: float | None = None,
    prev_close: float | None = None,
    level: float = 10.0,
) -> pd.DataFrame:
    """Build an n-bar OHLCV frame where bars ``[0 .. n-2]`` are constant
    at ``level`` and the last bar carries the engineered OHLCV.

    ``prev_open`` / ``prev_close`` override bar ``n-2``'s open / close —
    used by the bullish-engulfing test to engineer yesterday's body.
    The default constant at ``level`` ensures ``ADX`` and the
    ``MA(60)`` are well-defined (n=200 is well above the 60-bar warmup)
    and the Hysteresis Gate's data-driven ``_count_consecutive`` walk
    does not trip on NaNs.
    """
    open_ = [level] * n
    high = [level] * n
    low = [level] * n
    close = [level] * n

    open_[-1] = last_open
    high[-1] = last_high
    low[-1] = last_low
    close[-1] = last_close

    if prev_open is not None:
        open_[-2] = prev_open
    if prev_close is not None:
        close[-2] = prev_close

    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": [1000.0] * n,
        }
    )


# ---------------------------------------------------------------------------
# Indicator swap helpers
# ---------------------------------------------------------------------------


class _SwapIndicator:
    """Context manager that swaps ``name`` in the indicators registry
    for ``fn``, restoring the original registration on exit. Without
    this the tests would permanently replace e.g. ``rsi`` with a fake,
    breaking any test that runs after."""

    def __init__(self, name: str, fn) -> None:
        self._name = name
        self._fn = fn
        self._original = _indicators_mod._REGISTRY.get(name)

    def __enter__(self):
        indicators_register(self._name, self._fn)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._original is None:
            indicators_register(self._name, None)
        else:
            indicators_register(self._name, self._original)
        return False


def _swap_indicator(name: str, fn):
    return _SwapIndicator(name, fn)


def _fake_bollinger(
    *,
    lower: float,
    mid: float,
    upper: float,
):
    """Build a stand-in Bollinger Bands function that pins ``lower /
    mid / upper`` at the given constants for every bar.

    Returns a closure so tests can vary the bands per scenario. The
    function's signature matches ``bollinger(df, length, std)`` so it
    can drop into the registry unchanged.
    """

    def fake_bollinger(df: pd.DataFrame, length: int = 20, std: float = 2.0):
        n = len(df)
        return pd.DataFrame(
            {
                "lower": [lower] * n,
                "mid": [mid] * n,
                "upper": [upper] * n,
            },
            index=df.index,
        )

    return fake_bollinger


def _fake_rsi(value: float):
    """Build a stand-in RSI function that returns a constant ``value``
    for every bar. The strategy reads ``rsi.iloc[-1]`` to drive the
    Signal Modulator, so the constant pins the modulator formula to
    a deterministic scalar."""

    def fake_rsi(df: pd.DataFrame, length: int = 14):
        n = len(df)
        return pd.Series([value] * n, index=df.index)

    return fake_rsi


# ---------------------------------------------------------------------------
# _classify monkey-patch helper — same pattern as tests/test_adx_bb_regime_04.py
# ---------------------------------------------------------------------------


class _SwapClassify:
    """Context manager that replaces ``AdxBbRegimeStrategy._classify``
    with ``fn`` for the duration of the block, restoring the original
    on exit. The strategy calls ``AdxBbRegimeStrategy._classify`` as a
    class-attribute lookup, so reassigning the class attribute is
    sufficient — no need to touch any instance."""

    def __init__(self, fn) -> None:
        from strategies import adx_bb_regime

        self._mod = adx_bb_regime
        self._fn = fn
        self._original = adx_bb_regime.AdxBbRegimeStrategy.__dict__[
            "_classify"
        ]

    def __enter__(self):
        self._mod.AdxBbRegimeStrategy._classify = staticmethod(self._fn)
        return self

    def __exit__(self, exc_type, exc, tb):
        self._mod.AdxBbRegimeStrategy._classify = self._original
        return False


def _swap_classify(fn):
    return _SwapClassify(fn)


# ---------------------------------------------------------------------------
# Context factory
# ---------------------------------------------------------------------------


def _make_ctx(
    df: pd.DataFrame,
    *,
    symbol: str = "510300",
    state: dict | None = None,
) -> Context:
    """Build a Context wired to a FakeAdapter returning ``df``. ``now`` is
    set past the start of the synthetic series so ``Context.bars(...)``
    computes a sane date window."""
    return Context(
        now=date(2024, 7, 18),
        universe=[symbol],
        adapter=FakeAdapter({symbol: df}),
        state=state if state is not None else {},
    )


def _seed_state(**overrides) -> dict:
    """Build a ``ctx.state`` dict pre-populated for a RANGE_BULL signal-
    logic test. Anything not provided falls back to the D5 defaults.
    Seeding ``current_state = "RANGE_BULL"`` ensures ``prev_state ==
    new_state == "RANGE_BULL"`` on the test bar, which means T05's
    transition rule does NOT fire — the emitted weight equals the
    natural weight from the T06 signal stack."""
    state = {
        "current_state": "RANGE_BULL",
        "pending_state": None,
        "pending_days": 0,
        "trend_up_stage": "full",
        "exit_in_progress": None,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Standard band pinning for tests 1–7. bb_lower=9.5 / bb_upper=10.5 with
# the constant close at 10.0 keeps the bars safely inside the band by
# default — each test then engineers the specific OHLCV that triggers
# (or doesn't trigger) its target signal.
DEFAULT_LOWER = 9.5
DEFAULT_UPPER = 10.5
DEFAULT_MID = 10.0


def test_range_bull_default_bar_emits_weight_half():
    """Test 1 — A RANGE_BULL bar with no signal fires: BB lower not
    touched (low > 9.5), BB upper not touched (high < 10.5), no long
    lower shadow (body dominates the lower shadow), no bullish
    engulfing (today opens higher than yesterday so today's body
    cannot fully contain yesterday's body).
    The strategy emits the default half-position weight ``0.5``.

    The RSI value is irrelevant to the outcome (no buy trigger fires),
    but we pin it to 25 anyway so the test is reproducible."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    # Bar: open=10.05, high=10.15, low=10.0, close=10.10.
    #   body = |close - open| = 0.05
    #   shadow = close - low = 0.10; 0.10 > 2*0.05 = 0.10 → False
    #     (strict ``>``, so equal does not trigger — no long shadow).
    #   engulfing: prev (default constant) has open=close=10.0; today
    #     opens at 10.05 > prev_open=10.0, so containment fails on the
    #     low edge — no engulfing.
    #   band: low=10.0 > 9.5 ✓, high=10.15 < 10.5 ✓ (no touch).
    df = _constant_then_last_bar(
        200,
        last_open=10.05,
        last_high=10.2,
        last_low=10.05,
        last_close=10.15,
        prev_open=10.0,
        prev_close=10.2,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(25.0)):
        out = strat.generate(ctx)

    assert out == {"510300": 0.5}, (
        f"default RANGE_BULL bar (no signal) should emit 0.5, got {out}"
    )
    assert ctx.state["current_state"] == "RANGE_BULL"


def test_bb_lower_touch_with_rsi_25_emits_weight_075():
    """Test 2 — BB lower touch (low <= bb_lower) with RSI=25: the
    Signal Modulator yields ``0.5 + 0.5 * (30-25)/10 = 0.75``.

    ``(30-25)/10 = 0.5`` is in [0, 1] so no clamp applies — the
    modulator lands exactly on 0.75."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    # Bar: open=10.0, high=10.1 (< 10.5 → no upper touch), low=9.4
    # (<= 9.5 → lower touch fires), close=10.0.
    df = _constant_then_last_bar(
        200,
        last_open=10.0,
        last_high=10.1,
        last_low=9.4,
        last_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(25.0)):
        out = strat.generate(ctx)

    assert out == {"510300": 0.75}, (
        f"BB lower touch with RSI=25 should emit 0.75, got {out}"
    )


def test_bb_lower_touch_with_rsi_15_clamps_to_weight_one():
    """Test 3 — BB lower touch with RSI=15: ``(30-15)/10 = 1.5`` is
    above the [0, 1] clamp ceiling, so the modulator caps the scalar
    at 1.0 and emits ``0.5 + 0.5 * 1 = 1.0`` (the full buy-up,
    matching the TREND_UP default weight)."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _constant_then_last_bar(
        200,
        last_open=10.0,
        last_high=10.1,
        last_low=9.4,
        last_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(15.0)):
        out = strat.generate(ctx)

    assert out == {"510300": 1.0}, (
        f"BB lower touch with RSI=15 should clamp to weight 1.0, got {out}"
    )


def test_bb_lower_touch_with_rsi_35_falls_back_to_default_half():
    """Test 4 — BB lower touch with RSI=35: ``(30-35)/10 = -0.5`` is
    below the [0, 1] clamp floor, so the modulator floors the scalar
    at 0 and the default half-position ``0.5`` applies. The lower
    band touch still fires as a buy *trigger*, but the RSI is too
    high for the modulator to scale the position up — RSI is a
    position-size knob, not a gate."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _constant_then_last_bar(
        200,
        last_open=10.0,
        last_high=10.1,
        last_low=9.4,
        last_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(35.0)):
        out = strat.generate(ctx)

    # NOTE: the lower-touch *trigger* does fire here (low=9.4 <= 9.5),
    # but the modulator's scalar clamps to 0 — so the resulting
    # weight equals the 0.5 default exactly. The spec's priority
    # (sell > buy > default) does not apply because we are in the
    # "buy trigger fires" path, not the default path; the modulator
    # formula ``0.5 + 0.5 * clamp(scalar, 0, 1)`` simply evaluates
    # to 0.5 when the scalar clamps to 0.
    assert out == {"510300": 0.5}, (
        f"BB lower touch with RSI=35 should clamp scalar to 0 and "
        f"emit 0.5, got {out}"
    )


def test_bb_upper_touch_emits_weight_zero():
    """Test 5 — BB upper touch: ``High >= bb_upper`` wins outright,
    regardless of whether any buy trigger also fires. Weight = 0.

    The bar is engineered so the lower-touch ALSO triggers (low <=
    bb_lower) — verifying that the sell branch takes priority over buy."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    # Bar: open=10.0, high=10.6 (>= 10.5 → upper touch / sell), low=9.4
    # (<= 9.5 → lower touch / buy), close=10.0.
    df = _constant_then_last_bar(
        200,
        last_open=10.0,
        last_high=10.6,
        last_low=9.4,
        last_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(15.0)):
        out = strat.generate(ctx)

    # Sell priority — even though a buy trigger would also fire, the
    # upper-touch returns 0.0 unconditionally.
    assert out == {"510300": 0.0}, (
        f"BB upper touch should emit 0.0, got {out}"
    )


def test_long_lower_shadow_on_non_touch_bar_fires_buy():
    """Test 6 — Long lower shadow on a non-touch bar: the buy trigger
    fires via the K-line pattern (no BB lower / upper touch), and the
    Signal Modulator produces ``0.5 + 0.5 * clamp((30-25)/10, 0, 1) = 0.75``.

    The engineered bar:
        open=10.05, high=10.1, low=9.6, close=10.0
        body = |close - open| = 0.05
        shadow = close - low = 0.4
        0.4 > 2 * 0.05 = 0.1 → long shadow fires ✓
    The bar sits inside the 9.5 / 10.5 band (low=9.6 > 9.5, high=10.1
    < 10.5) so neither band-touch fires. The shadow is also too long
    to trigger bullish engulfing in a useful way; engulfing needs
    yesterday's body fully contained, and bar[-2] has the default
    constant open=close=10.0, so today's body [10.0, 10.05] does NOT
    fully contain yesterday's body [10.0, 10.0] (close-edge equal,
    which is fine; but today_open=10.05 > prev_open=10.0, so the
    containment check fails — no engulfing fires either)."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _constant_then_last_bar(
        200,
        last_open=10.05,
        last_high=10.1,
        last_low=9.6,
        last_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(25.0)):
        out = strat.generate(ctx)

    assert out == {"510300": 0.75}, (
        f"long lower shadow with RSI=25 should fire buy → 0.75, "
        f"got {out}"
    )


def test_bullish_engulfing_on_non_touch_bar_fires_buy():
    """Test 7 — Bullish engulfing on a non-touch bar: today's body
    fully contains yesterday's body AND today is bullish, so the buy
    trigger fires. The bar sits inside the 9.5 / 10.5 band (no
    BB touch), and the engineered shadow/body ratio does NOT trigger
    the long-lower-shadow rule either — so the only active trigger
    is engulfing.

    The engineered pair:
        yesterday: open=10.1, close=10.0 (bearish, body [10.0, 10.1])
        today:     open=9.97, high=10.1, low=9.95, close=10.05
        body = |close - open| = 0.08
        shadow = close - low = 0.10
        0.10 > 2 * 0.08 = 0.16 → False (no long shadow) ✓
        engulfing:
            today_open (9.97) <= prev_open (10.1) ✓
            today_close (10.05) >= prev_close (10.0) ✓
            today_close (10.05) > today_open (9.97) ✓ (bullish)
    Signal Modulator with RSI=25 yields ``0.5 + 0.5 * 0.5 = 0.75``."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _constant_then_last_bar(
        200,
        last_open=9.97,
        last_high=10.1,
        last_low=9.95,
        last_close=10.05,
        prev_open=10.1,
        prev_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(25.0)):
        out = strat.generate(ctx)

    assert out == {"510300": 0.75}, (
        f"bullish engulfing with RSI=25 should fire buy → 0.75, "
        f"got {out}"
    )


def test_both_buy_and_sell_on_same_bar_sell_wins():
    """Test 8 — Engineered volatility bar with both a BB upper touch
    (sell trigger) and a BB lower touch + long shadow (multiple buy
    triggers): sell wins, weight = 0.

    The bar:
        open=10.0, high=10.6 (>= 10.5 → sell), low=9.0 (<= 9.5 →
        lower-touch buy AND the shadow of 1.0 ≫ 2 * 0 = body, also a
        buy via long-shadow), close=10.0.
    The strategy must check the sell trigger first and return 0.0
    regardless of the buy triggers."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _constant_then_last_bar(
        200,
        last_open=10.0,
        last_high=10.6,
        last_low=9.0,
        last_close=10.0,
    )
    ctx = _make_ctx(df, state=_seed_state())

    strat = AdxBbRegimeStrategy()
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger",
        _fake_bollinger(lower=DEFAULT_LOWER, mid=DEFAULT_MID, upper=DEFAULT_UPPER),
    ), _swap_indicator("rsi", _fake_rsi(15.0)):
        out = strat.generate(ctx)

    # Sell priority — even though multiple buy triggers would have
    # produced 1.0 (RSI=15 clamps to the cap), the upper-touch
    # short-circuits to 0.0.
    assert out == {"510300": 0.0}, (
        f"engineered volatility bar (both buy and sell) should emit "
        f"0.0 (sell wins), got {out}"
    )


# ---------------------------------------------------------------------------
# Bonus: spec-pinned unit tests for the private K-line detectors
# ---------------------------------------------------------------------------
# These exercise the helper functions directly with pinned inputs. They
# are not in the ticket's checklist, but they pin the boundary
# conditions of the helpers (doji body=0, bearish engulfing rejection,
# containment edge cases) so future regressions surface clearly.


def test_is_long_lower_shadow_pure_helper():
    """Direct unit test of ``_is_long_lower_shadow`` covering three
    pinned cases: shadow > 2*body → True, shadow == 2*body → False
    (strict ``>``), doji body=0 → True for any positive shadow."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    is_lls = AdxBbRegimeStrategy._is_long_lower_shadow

    # shadow > 2*body → True
    assert is_lls(close=10.0, low=9.5, open=10.05) is True  # shadow=0.5, body=0.05, 2*body=0.1
    # shadow == 2*body → False (strict)
    assert is_lls(close=10.0, low=9.9, open=10.05) is False  # shadow=0.1, body=0.05, 2*body=0.1
    # doji (body=0): any positive shadow fires
    assert is_lls(close=10.0, low=9.99, open=10.0) is True
    # no shadow at all → False
    assert is_lls(close=10.0, low=10.0, open=10.05) is False


def test_is_bullish_engulfing_pure_helper():
    """Direct unit test of ``_is_bullish_engulfing`` covering the
    containment edge cases: bearish today → False (today not
    bullish), body containment strict-equality edges, prior-bar body
    smaller than today's body (typical engulfing)."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    is_be = AdxBbRegimeStrategy._is_bullish_engulfing

    # Bullish today + fully contains prev body → True
    assert (
        is_be(
            today_open=9.97,
            today_close=10.05,
            today_high=10.1,
            today_low=9.95,
            prev_open=10.1,
            prev_close=10.0,
        )
        is True
    )
    # Bearish today → False (not bullish, regardless of containment)
    assert (
        is_be(
            today_open=10.05,
            today_close=9.97,
            today_high=10.1,
            today_low=9.95,
            prev_open=10.1,
            prev_close=10.0,
        )
        is False
    )
    # today_open > prev_open → containment fails on the low side
    assert (
        is_be(
            today_open=10.15,
            today_close=10.20,
            today_high=10.25,
            today_low=10.10,
            prev_open=10.1,
            prev_close=10.0,
        )
        is False
    )
    # today_close < prev_close → containment fails on the high side
    assert (
        is_be(
            today_open=9.95,
            today_close=9.99,
            today_high=10.0,
            today_low=9.90,
            prev_open=10.1,
            prev_close=10.0,
        )
        is False
    )
    # Boundary equality on both edges (exact body overlap, today
    # bullish): today_open == prev_open AND today_close == prev_close
    # AND today bullish — the helper uses <= / >=, so equal bodies
    # are "fully contained". Verifies the equality-edge of the
    # containment check.
    assert (
        is_be(
            today_open=10.0,
            today_close=10.2,
            today_high=10.25,
            today_low=9.95,
            prev_open=10.0,
            prev_close=10.2,
        )
        is True
    )