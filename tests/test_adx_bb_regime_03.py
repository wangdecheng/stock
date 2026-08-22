"""tests/test_adx_bb_regime_03.py

T03 — All 4 states + Hysteresis Gate.

Covers the four acceptance bullets from
``.scratch/adx-bb-regime/issues/03-full-state-machine-and-hysteresis.md``:

  1. A one-bar ADX spike (sustained for only one bar) does NOT enter
     TREND_UP because the Hysteresis Gate requires ``hysteresis_days``
     consecutive TREND_UP bars.
  2. A 2-bar sustained ADX configuration with Close > BB mid DOES enter
     TREND_UP on the second bar (weight ``1.0``, ``current_state ==
     "TREND_UP"``).
  3. Each of the four states (TREND_UP, TREND_DOWN, RANGE_BULL,
     RANGE_BEAR) is reachable from a hand-crafted synthetic series.
  4. The state priority cascade is respected: a bar whose raw inputs
     could match TREND_UP wins over TREND_DOWN.

Test fixtures mirror ``tests/test_adx_bb_regime_02.py``
(``FakeBarsResult`` / ``FakeAdapter``). The Hysteresis Gate is data-
driven (``_count_consecutive`` walks the indicator history), so the
boundary cases (test 1, test 2, test 4) inject specific ADX / DI values
via ``framework.indicators.register`` — the same swap-pattern used in
``tests/test_strategy_t2.py::test_context_indicator_dispatches_to_registry``.
The reachable-states test (test 3) builds real OHLCV series so the
indicators compute naturally.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

import framework.indicators as _indicators_mod
from framework.indicators import register as indicators_register
from framework.strategy.context import Context


# ---------------------------------------------------------------------------
# Fakes — same pattern as tests/test_adx_bb_regime_02.py
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
# Synthetic OHLCV builders (for the "all 4 states reachable" test)
# ---------------------------------------------------------------------------


def _rising_bars(n: int = 200) -> pd.DataFrame:
    """Monotonically rising closes — TREND_UP candidate on every bar."""
    close = [10.0 + 0.1 * i for i in range(n)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [c - 0.05 for c in close],
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


def _falling_bars(n: int = 200) -> pd.DataFrame:
    """Monotonically falling closes — TREND_DOWN candidate on every bar
    (ADX stays high from the sustained move, +DI < -DI)."""
    close = [20.0 - 0.1 * i for i in range(n)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [c + 0.05 for c in close],
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


def _range_bull_bars(n: int = 200, *, seed: int = 42) -> pd.DataFrame:
    """Random walk around a flat 10.0 baseline — no directional drift,
    so ADX stays low (<20), but the seed lands the final close *above*
    the MA60 (the cascade's RANGE_BULL requirement)."""
    rng = np.random.default_rng(seed)
    close = [10.0]
    for _ in range(1, n):
        delta = rng.normal(0.0, 0.1)
        close.append(max(close[-1] + delta, 0.1))
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [c for c in close],
            "high": [c + 0.15 for c in close],
            "low": [c - 0.15 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


def _range_bear_bars(n: int = 200, *, seed: int = 7) -> pd.DataFrame:
    """Random walk with a small downward bias — ADX stays low (<20),
    and the bias + seed land the final close *below* MA60 (the cascade
    falls all the way through to RANGE_BEAR)."""
    rng = np.random.default_rng(seed)
    close = [10.0]
    for _ in range(1, n):
        delta = rng.normal(-0.005, 0.1)
        close.append(max(close[-1] + delta, 0.1))
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [c for c in close],
            "high": [c + 0.15 for c in close],
            "low": [c - 0.15 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


# ---------------------------------------------------------------------------
# Indicator mocks — for the precise-ADX hysteresis boundary tests
# ---------------------------------------------------------------------------


def _make_adx_mock(
    *,
    adx: float,
    plus_di: float,
    minus_di: float,
    n_bars: int,
    tail_count: int = 1,
) -> "callable":
    """Build a stand-in ``adx(df, length=...)`` function whose last
    ``tail_count`` bars carry the given ADX/DI values and whose earlier
    bars are flat at a baseline. This lets us engineer "one-day spike"
    vs "two-day sustained" ADX patterns without depending on the
    indicator's Wilder smoothing over synthetic OHLCV.

    ``adx`` is a constant for the tail (``tail_count`` bars at the end).
    Earlier bars use ``baseline_adx`` (lower).
    """
    baseline_adx = max(15.0, adx - 13.0)  # 15 if adx=28, etc.
    baseline_plus_di = max(plus_di - 5.0, 1.0)
    baseline_minus_di = max(minus_di + 5.0, 1.0)

    def fake_adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        n = len(df)
        out_adx = np.full(n, baseline_adx, dtype=float)
        out_plus = np.full(n, baseline_plus_di, dtype=float)
        out_minus = np.full(n, baseline_minus_di, dtype=float)
        # Tail: last ``tail_count`` bars carry the configured values.
        out_adx[-tail_count:] = adx
        out_plus[-tail_count:] = plus_di
        out_minus[-tail_count:] = minus_di
        return pd.DataFrame(
            {"adx": out_adx, "plus_di": out_plus, "minus_di": out_minus},
            index=df.index,
        )

    return fake_adx


# ---------------------------------------------------------------------------
# Indicator swap helper — saves and restores the original registration
# ---------------------------------------------------------------------------


class _SwapIndicator:
    """Context manager that swaps ``name`` in the indicators registry
    for ``fn``, restoring the original registration on exit. Without
    this the tests would permanently replace e.g. ``adx`` with a fake,
    breaking any test that runs after.
    """

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


# ---------------------------------------------------------------------------
# Context factory — wraps an arbitrary OHLCV + indicator mocks
# ---------------------------------------------------------------------------


def _make_ctx(
    df: pd.DataFrame,
    *,
    symbol: str = "510300",
    state: dict | None = None,
) -> Context:
    """Build a Context wired to a FakeAdapter returning ``df``. ``now`` is
    set past the start of the synthetic series so ``Context.bars(...)``
    computes a sane date window — the adapter ignores it anyway."""
    return Context(
        now=date(2024, 7, 18),
        universe=[symbol],
        adapter=FakeAdapter({symbol: df}),
        state=state if state is not None else {},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_state_priority_cascade_is_explicit():
    """All four states are reachable on hand-crafted synthetic series;
    each produces the documented default weight. ``current_state``
    matches the classification."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    cases = [
        (_rising_bars(), "TREND_UP", 1.0),
        (_falling_bars(), "TREND_DOWN", 0.0),
        (_range_bull_bars(), "RANGE_BULL", 0.5),
        (_range_bear_bars(), "RANGE_BEAR", 0.0),
    ]
    strat = AdxBbRegimeStrategy()
    for df, expected_state, expected_weight in cases:
        ctx = _make_ctx(df)
        out = strat.generate(ctx)
        assert ctx.state["current_state"] == expected_state, (
            f"expected {expected_state} for {expected_state.lower()} series, "
            f"got {ctx.state['current_state']}"
        )
        assert out == {"510300": expected_weight}, (
            f"expected weight {expected_weight} for {expected_state}, got {out}"
        )


def test_one_day_adx_spike_does_not_enter_trend_up():
    """A single bar where ADX=28 (above 25) surrounded by ADX=15 bars:
    the cascade classifies the LAST bar as TREND_UP candidate (ADX > 25
    AND +DI > -DI), but the second-to-last bar classifies as RANGE_BEAR
    (ADX=15 < 25), so n_consecutive = 1, the Hysteresis Gate does not
    fire, and weight remains 0.0 — TREND_UP is NOT entered."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_bars()
    fake_adx = _make_adx_mock(
        adx=28.0, plus_di=30.0, minus_di=10.0, n_bars=len(df), tail_count=1
    )
    with _swap_indicator("adx", fake_adx):
        ctx = _make_ctx(df)
        out = AdxBbRegimeStrategy().generate(ctx)

    assert out == {"510300": 0.0}
    # The candidate was TREND_UP (ADX > 25 etc.) but the gate didn't fire,
    # so the active state is the RANGE_BEAR fallback — NOT TREND_UP.
    assert ctx.state["current_state"] != "TREND_UP"


def test_sustained_two_day_rising_enters_trend_up():
    """Two consecutive trailing bars classified as TREND_UP, with
    Close > BB mid: Hysteresis Gate fires (n_consecutive >= 2), the
    state transitions to TREND_UP, and weight is 1.0."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_bars()
    fake_adx = _make_adx_mock(
        adx=28.0, plus_di=30.0, minus_di=10.0, n_bars=len(df), tail_count=2
    )
    with _swap_indicator("adx", fake_adx):
        ctx = _make_ctx(df)
        out = AdxBbRegimeStrategy().generate(ctx)

    assert out == {"510300": 1.0}
    assert ctx.state["current_state"] == "TREND_UP"


def test_state_priority_boundary_resolves_to_trend_up():
    """A bar with ADX > 25 and ``plus_di == minus_di`` is on the boundary
    between TREND_UP and TREND_DOWN (strict inequalities ``+DI > -DI``
    and ``+DI < -DI`` both fail). With both DI columns equal, neither
    trend branch wins and the cascade falls through to RANGE_BEAR.

    The next moment of the cascade is "if either strict inequality
    holds, the priority order picks TREND_UP first". We test that by
    constructing plus_di slightly above minus_di — the cascade must
    resolve to TREND_UP, not fall through to TREND_DOWN or any
    RANGE_* state."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_bars()

    # Mock: ADX > 25, +DI slightly above -DI → TREND_UP conditions hold
    # (+DI > -DI strict). TREND_DOWN's +DI < -DI strict is False, so the
    # cascade resolves to TREND_UP per the priority order.
    def fake_adx_up(df_in: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        n = len(df_in)
        return pd.DataFrame(
            {
                "adx": np.full(n, 30.0),
                "plus_di": np.full(n, 25.0),
                "minus_di": np.full(n, 24.9),  # +DI > -DI strictly
            },
            index=df_in.index,
        )

    with _swap_indicator("adx", fake_adx_up):
        ctx_up = _make_ctx(df)
        out_up = AdxBbRegimeStrategy().generate(ctx_up)

    # Same construction but with the DI signs swapped — verifies the
    # cascade is actually picking TREND_UP on the strict inequality,
    # not falling through to a non-trend state by accident.
    def fake_adx_down(df_in: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        n = len(df_in)
        return pd.DataFrame(
            {
                "adx": np.full(n, 30.0),
                "plus_di": np.full(n, 24.9),
                "minus_di": np.full(n, 25.0),  # +DI < -DI strictly
            },
            index=df_in.index,
        )

    with _swap_indicator("adx", fake_adx_down):
        ctx_down = _make_ctx(df)
        out_down = AdxBbRegimeStrategy().generate(ctx_down)

    # With +DI > -DI, the cascade picks TREND_UP (priority 1) — gate
    # fires because every bar matches and close > BB mid on a rising
    # series. ``current_state`` is TREND_UP, weight is 1.0.
    assert ctx_up.state["current_state"] == "TREND_UP"
    assert out_up == {"510300": 1.0}

    # With +DI < -DI, the cascade picks TREND_DOWN (priority 2) — the
    # transition is instant (no hysteresis), weight is 0.0.
    assert ctx_down.state["current_state"] == "TREND_DOWN"
    assert out_down == {"510300": 0.0}
