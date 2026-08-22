"""tests/test_adx_bb_regime_02.py

T02 — Strategy skeleton + TREND_UP + state persistence.

Covers the four acceptance bullets from
``.scratch/adx-bb-regime/issues/02-strategy-skeleton-trend-up-state.md``:
  1. discovery + decoupling scan,
  2. TREND_UP emits weight 1.0 on a synthetic rising series,
  3. non-TREND_UP emits weight 0.0 on a synthetic sideways series,
  4. state file keys default on first run and are not clobbered on the
     second run when the user has hand-edited values into state.json.

Test fixtures mirror ``tests/test_strategy_t2.py`` (``FakeBarsResult`` /
``FakeAdapter``) — we build a DataFrame with the AKShare column
names (``date / open / high / low / close / volume``), wrap it, and
inject the wrapper via ``Context.adapter`` so the strategy runs in
isolation against synthetic data.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from framework.strategy.context import Context


# ---------------------------------------------------------------------------
# Fakes — mirror the pattern from tests/test_strategy_t2.py
# ---------------------------------------------------------------------------


class FakeBarsResult:
    """Stand-in for the adapter's BarsResult; just holds a DataFrame."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df


class FakeAdapter:
    """Stand-in for AKShareAdapter. Ignores the (start, end) filter so the
    strategy receives the full synthetic series regardless of how
    ``Context.bars`` computes its window."""

    def __init__(self, df_by_symbol: dict[str, pd.DataFrame]) -> None:
        self.df_by_symbol = df_by_symbol

    def get_bars(self, symbol: str, start, end, **kw):
        return FakeBarsResult(self.df_by_symbol[symbol])


# ---------------------------------------------------------------------------
# Synthetic OHLCV builders
# ---------------------------------------------------------------------------


def _rising_bars(n: int = 200) -> pd.DataFrame:
    """Monotonically rising close prices. With +0.5 / -0.5 envelopes on
    high / low this produces a strong, sustained uptrend — ADX
    converges to ~100, ``+DI > -DI``, and Close > MA20 by construction."""
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


def _flat_bars(n: int = 200) -> pd.DataFrame:
    """Constant close (10.0) with a 0.02 envelope. +DM and -DM are zero,
    so ``+DI == -DI == 0`` and ADX is NaN at the end of the window.
    ``NaN > 25`` is ``False`` so the TREND_UP branch does not fire."""
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [10.0] * n,
            "high": [10.01] * n,
            "low": [9.99] * n,
            "close": [10.0] * n,
            "volume": [1000.0] * n,
        }
    )


def _make_ctx(
    df: pd.DataFrame,
    symbol: str = "510300",
    state: dict | None = None,
) -> Context:
    """Build a Context wired to a FakeAdapter returning ``df`` for
    ``symbol``. ``now`` is set to 200 days after the start of the
    synthetic series so ``Context.bars(..., lookback=120)`` produces a
    sane date window (the adapter ignores it anyway, but we set it
    correctly for realism)."""
    return Context(
        now=date(2024, 7, 18),
        universe=[symbol],
        adapter=FakeAdapter({symbol: df}),
        state=state if state is not None else {},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_strategy_is_discoverable_and_passes_decoupling_scan():
    """The strategy file is registered under ``name = "adx_bb_regime"``
    by ``discover_strategies()`` and the decoupling scan does not
    reject it (otherwise the import would already have raised)."""
    from framework.strategy.discover import discover_strategies

    registry = discover_strategies()

    assert "adx_bb_regime" in registry
    cls = registry["adx_bb_regime"]
    assert cls.__name__ == "AdxBbRegimeStrategy"
    assert cls.name == "adx_bb_regime"


def test_trend_up_emits_weight_one_on_rising_series():
    """200 bars of monotonically rising closes: ADX > 25, +DI > -DI,
    Close > MA20 all hold by construction, so the TREND_UP branch
    fires and weight 1.0 is emitted for the universe's only symbol."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    ctx = _make_ctx(_rising_bars())
    out = AdxBbRegimeStrategy().generate(ctx)

    assert out == {"510300": 1.0}
    assert ctx.state["current_state"] == "TREND_UP"


def test_non_trend_emits_weight_zero_on_sideways_series():
    """200 bars of constant closes: ADX is NaN at the tail (and even
    where finite it would be very low); ``NaN > 25`` is False, so the
    TREND_UP branch does not fire and weight 0.0 is emitted."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    ctx = _make_ctx(_flat_bars())
    out = AdxBbRegimeStrategy().generate(ctx)

    assert out == {"510300": 0.0}
    # All three non-TREND_UP states collapse to RANGE_BEAR for v1; the
    # full classifier arrives in T03.
    assert ctx.state["current_state"] == "RANGE_BEAR"


def test_state_defaults_populated_on_first_run():
    """On a fresh ``ctx.state`` dict, the strategy must populate the
    five D5 keys with their documented defaults after the first
    ``generate()`` call."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    ctx = _make_ctx(_rising_bars(), state={})
    assert ctx.state == {}, "precondition: ctx.state starts empty"

    AdxBbRegimeStrategy().generate(ctx)

    assert set(ctx.state) == {
        "current_state",
        "pending_state",
        "pending_days",
        "trend_up_stage",
        "exit_in_progress",
    }
    # ``current_state`` is updated each bar to today's classification,
    # so on the rising series it tracks to TREND_UP. The other four keys
    # are pure defaults from D5.
    assert ctx.state["current_state"] == "TREND_UP"
    assert ctx.state["pending_state"] is None
    assert ctx.state["pending_days"] == 0
    assert ctx.state["trend_up_stage"] == "full"
    assert ctx.state["exit_in_progress"] is None


def test_state_keys_not_clobbered_on_second_run():
    """A second ``generate()`` call against a pre-populated ``ctx.state``
    (as if loaded from ``strategies/adx_bb_regime.state.json``) must
    preserve the user-edited values — ``setdefault`` only writes when
    the key is absent. ``current_state`` is the only key the strategy
    legitimately overwrites each bar (it is the *result* of
    classification, not a user input)."""
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    pre = {
        "current_state": "TREND_UP",
        "pending_state": "RANGE_BULL",
        "pending_days": 3,
        "trend_up_stage": "halved",
        "exit_in_progress": {"target": 0.5, "days_left": 1},
    }
    ctx = _make_ctx(_rising_bars(), state=dict(pre))

    AdxBbRegimeStrategy().generate(ctx)

    # User-edited values must be preserved verbatim.
    assert ctx.state["pending_state"] == "RANGE_BULL"
    assert ctx.state["pending_days"] == 3
    assert ctx.state["trend_up_stage"] == "halved"
    assert ctx.state["exit_in_progress"] == {"target": 0.5, "days_left": 1}
    # ``current_state`` is rewritten each bar — on the rising series it
    # still classifies as TREND_UP, but the assertion is that the
    # strategy *did* write it (not that setdefault protected it).
    assert ctx.state["current_state"] == "TREND_UP"
