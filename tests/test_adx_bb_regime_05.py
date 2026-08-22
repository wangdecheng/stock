"""tests/test_adx_bb_regime_05.py

T05 — Phased Exit (2-day linear sell on held-state transition).

Covers the four acceptance bullets from
``.scratch/adx-bb-regime/issues/05-phased-exit.md``:

  1. Synthetic state transition ``TREND_UP (1.0) → TREND_DOWN (0.0)``:
     two consecutive bars emit ``0.5`` then ``0.0``; ``exit_in_progress``
     is cleared after the second bar.
  2. Transition ``TREND_UP → RANGE_BULL``: two bars emit ``0.75``
     then ``0.5``.
  3. Mid-exit reversal: state flips back to a held state after one bar
     of phasing — ``exit_in_progress`` is overwritten with the new
     target and ``days_left = 2``.
  4. Boundary: a transition between held states with equal natural
     weights (e.g. ``RANGE_BULL → RANGE_BULL`` with default 0.5) keeps
     ``exit_in_progress`` set, but the linear interpolation is a no-op
     (weight stays at 0.5 across both bars).

Test fixtures mirror ``tests/test_adx_bb_regime_02.py`` …
``tests/test_adx_bb_regime_04.py`` (``FakeBarsResult`` / ``FakeAdapter``).
The phasing math depends on the *previous bar's* emitted weight
(``prev_weight``) and the *current bar's* natural weight (``weight``).
The tests seed ``ctx.state["current_state"]`` directly to bypass the
cascade and pin ``prev_state`` for the first bar of each scenario. The
mid-exit reversal test (test 3) monkey-patches ``_classify`` to force a
specific sequence of states — the same swap-pattern used in T04.

A note on the ``prev_weight`` / ``weight`` split
-----------------------------------------------
The interpolation formula is ``weight = prev_weight + (target - prev_weight) / days_left``
where ``prev_weight`` is the *previous bar's emitted weight* (yesterday's
strategy output, not today's classification) and ``target`` is the
*current bar's natural weight* (today's classification, post-cascade +
HalvedStage). On the transition day these differ: e.g. on
``TREND_UP → TREND_DOWN`` ``prev_weight = 1.0`` and ``target = 0.0``,
giving ``weight = 1.0 + (0 - 1) / 2 = 0.5``. On the second bar of the
phase-out ``prev_weight`` is yesterday's emitted 0.5 (now the new
state's natural weight, since the cascade classifies the new state
again), and ``weight = 0.5 + (0 - 0.5) / 1 = 0.0``.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

import framework.indicators as _indicators_mod
from framework.indicators import register as indicators_register
from framework.strategy.context import Context


# ---------------------------------------------------------------------------
# Fakes — same pattern as tests/test_adx_bb_regime_02.py / _03.py / _04.py
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
# Synthetic OHLCV builders (for the natural-state series tests)
# ---------------------------------------------------------------------------


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


def _rising_bars(n: int = 200) -> pd.DataFrame:
    """Monotonically rising closes — last bar is the highest. ``Close >
    MA20`` and ``Close > MA10`` hold on every bar of the rising tail."""
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


# ---------------------------------------------------------------------------
# Indicator swap helper — same pattern as tests/test_adx_bb_regime_03.py
# ---------------------------------------------------------------------------


class _SwapIndicator:
    """Context manager that swaps ``name`` in the indicators registry
    for ``fn``, restoring the original registration on exit."""

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


def _fake_bollinger_low_mid(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Stand-in Bollinger Bands whose ``mid`` is pinned at 0.0 — well
    below every positive synthetic close — so the Hysteresis Gate's
    ``Close > BB mid`` check always passes. Required for tests that
    force the cascade into ``TREND_UP`` via ``_classify``."""
    n = len(df)
    return pd.DataFrame(
        {
            "lower": [-1000.0] * n,
            "mid": [0.0] * n,
            "upper": [1000.0] * n,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# _classify monkey-patch helper — same pattern as tests/test_adx_bb_regime_04.py
# ---------------------------------------------------------------------------


class _SwapClassify:
    """Context manager that replaces ``AdxBbRegimeStrategy._classify``
    with ``fn`` for the duration of the block, restoring the original
    on exit.

    The strategy calls ``AdxBbRegimeStrategy._classify`` (class-attribute
    lookup, not instance attribute), so reassigning the class attribute
    is sufficient — no need to touch any instance.
    """

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
    """Build a ``ctx.state`` dict pre-populated for a phased-exit test.
    Anything not provided falls back to the D5 defaults — matches what
    the strategy would have written after a warm-up run."""
    state = {
        "current_state": "RANGE_BEAR",
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


def test_trend_up_to_trend_down_two_bar_phaseout():
    """``TREND_UP (1.0) → TREND_DOWN (0.0)`` — two consecutive bars emit
    ``0.5`` then ``0.0``; ``exit_in_progress`` cleared after the second
    bar.

    Seeds ``current_state = "TREND_UP"`` and ``trend_up_stage = "full"``
    so the previous bar was a full TREND_UP. Uses ``_falling_bars()`` so
    the cascade naturally classifies the *current* bar as TREND_DOWN
    (no monkey-patch needed). Bar 1 detects the held → non-held
    transition, sets ``exit_in_progress = {target: 0.0, days_left: 2}``,
    and interpolates from ``prev_weight = 1.0`` (TREND_UP full) to the
    target. Bar 2 continues the interpolation and clears the dict when
    ``days_left`` reaches 0.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _falling_bars()
    ctx = _make_ctx(df, state=_seed_state(current_state="TREND_UP"))

    strat = AdxBbRegimeStrategy()

    # Bar 1 — transition bar.
    out1 = strat.generate(ctx)
    assert out1 == {"510300": 0.5}, (
        f"bar 1 (TREND_UP → TREND_DOWN) should interpolate to 0.5, "
        f"got {out1}"
    )
    # After decrementing days_left (2 → 1).
    assert ctx.state["exit_in_progress"] == {"target": 0.0, "days_left": 1}
    assert ctx.state["current_state"] == "TREND_DOWN"

    # Bar 2 — phase-out completes; weight lands on target and the dict
    # is cleared.
    out2 = strat.generate(ctx)
    assert out2 == {"510300": 0.0}, (
        f"bar 2 (continuing TREND_DOWN) should land on target 0.0, "
        f"got {out2}"
    )
    assert ctx.state["exit_in_progress"] is None
    assert ctx.state["current_state"] == "TREND_DOWN"


def test_trend_up_to_range_bull_two_bar_phaseout():
    """``TREND_UP → RANGE_BULL`` — two bars emit ``0.75`` then ``0.5``.

    Seeds ``current_state = "TREND_UP"`` and uses ``_range_bull_bars()``
    so the cascade naturally classifies the current bar as RANGE_BULL
    (no monkey-patch needed). Bar 1 detects the held → held transition
    (TREND_UP → RANGE_BULL), sets ``exit_in_progress = {target: 0.5,
    days_left: 2}``, and interpolates from ``prev_weight = 1.0`` to the
    new state's default 0.5 — emitting 0.75. Bar 2 continues the
    interpolation and clears the dict.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _range_bull_bars()
    ctx = _make_ctx(df, state=_seed_state(current_state="TREND_UP"))

    strat = AdxBbRegimeStrategy()

    # Bar 1 — transition bar.
    out1 = strat.generate(ctx)
    assert out1 == {"510300": 0.75}, (
        f"bar 1 (TREND_UP → RANGE_BULL) should interpolate to 0.75, "
        f"got {out1}"
    )
    assert ctx.state["exit_in_progress"] == {"target": 0.5, "days_left": 1}
    assert ctx.state["current_state"] == "RANGE_BULL"

    # Bar 2 — phase-out completes; weight lands on target and the dict
    # is cleared.
    out2 = strat.generate(ctx)
    assert out2 == {"510300": 0.5}, (
        f"bar 2 (continuing RANGE_BULL) should land on target 0.5, "
        f"got {out2}"
    )
    assert ctx.state["exit_in_progress"] is None
    assert ctx.state["current_state"] == "RANGE_BULL"


def test_mid_exit_reversal_overwrites_exit_in_progress():
    """Mid-exit reversal — state flips back to a held state after one
    bar of phasing. ``exit_in_progress`` is overwritten with the new
    target and ``days_left = 2``.

    The setup is a three-bar sequence:
      bar 1: prev_state = TREND_UP (seeded), new_state forced to
             RANGE_BULL via ``_classify`` monkey-patch — phase-out
             starts (exit_in_progress = {target: 0.5, days_left: 2}).
      bar 2: prev_state = RANGE_BULL (from bar 1), new_state forced to
             TREND_UP — held → held-different. The rule fires because
             ``exit_in_progress`` is already active, overwriting the
             dict with the new target = 1.0 (TREND_UP full, fresh re-
             entry resets HalvedStage) and resetting ``days_left = 2``.

    The test asserts the *destination* of the overwrite. The exact
    interpolated weight on bar 2 is not pinned — T05's rule only
    requires the overwrite + days_left reset, not a specific weight.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_bars()  # close > BB mid naturally → Hysteresis Gate passes
    ctx = _make_ctx(
        df,
        state=_seed_state(current_state="TREND_UP", trend_up_stage="full"),
    )

    strat = AdxBbRegimeStrategy()

    # Bar 1 — force cascade → RANGE_BULL. Phase-out starts.
    with _swap_classify(lambda *a, **k: "RANGE_BULL"), _swap_indicator(
        "bollinger", _fake_bollinger_low_mid
    ):
        out1 = strat.generate(ctx)

    assert ctx.state["current_state"] == "RANGE_BULL"
    # After bar 1's interpolation, days_left has been decremented 2 → 1.
    assert ctx.state["exit_in_progress"] == {"target": 0.5, "days_left": 1}
    # Bar 1 weight: 1.0 (TREND_UP full prev_weight) + (0.5 - 1.0) / 2 = 0.75.
    assert out1 == {"510300": 0.75}, (
        f"bar 1 (TREND_UP → RANGE_BULL) should interpolate to 0.75, "
        f"got {out1}"
    )

    # Bar 2 — force cascade → TREND_UP. Held → held-different: the rule
    # overwrites ``exit_in_progress`` with the new destination and
    # resets days_left.
    with _swap_classify(lambda *a, **k: "TREND_UP"), _swap_indicator(
        "bollinger", _fake_bollinger_low_mid
    ):
        out2 = strat.generate(ctx)

    assert ctx.state["current_state"] == "TREND_UP"
    # The reversal must overwrite the in-progress phase-out with the
    # new held-state destination (target = 1.0, the TREND_UP full re-
    # entry weight) and reset ``days_left`` to ``exit_phased_days = 2``.
    # The interpolation then decrements 2 → 1, so the post-bar state
    # is ``{"target": 1.0, "days_left": 1}``. The "reset to 2" is
    # visible on the NEXT bar: the phase-out now has 1 full bar
    # remaining toward the new target, identical to where bar 1 of
    # any other 2-bar phase-out would be.
    assert ctx.state["exit_in_progress"] == {"target": 1.0, "days_left": 1}, (
        f"mid-exit reversal must overwrite exit_in_progress with the "
        f"new held-state destination (target=1.0) and reset days_left "
        f"(post-decrement: 1), got {ctx.state['exit_in_progress']}"
    )


def test_boundary_range_bull_to_range_bull_interpolation_is_noop():
    """Boundary case — a held state with the same natural weight on both
    sides keeps ``exit_in_progress`` set, but the linear interpolation
    is a no-op (weight stays at 0.5 across both bars).

    This test pre-seeds ``exit_in_progress = {target: 0.5,
    days_left: 2}`` and runs the strategy on a RANGE_BULL series. The
    cascade classifies every bar as RANGE_BULL naturally. Since
    ``prev_state == new_state == RANGE_BULL`` on every bar, T05's
    transition rule does NOT fire — ``exit_in_progress`` is left
    untouched. The interpolation block runs (because the dict is
    non-``None``) and computes ``weight = 0.5 + (0.5 - 0.5) / 2 = 0.5``
    on bar 1 and ``weight = 0.5 + (0.5 - 0.5) / 1 = 0.5`` on bar 2.
    Bar 2 decrements ``days_left`` to 0 and clears the dict.

    The functional requirement is: ``weight`` stays at 0.5 across both
    bars. We also assert ``exit_in_progress`` is cleared after bar 2
    (since ``days_left`` reaches 0) — this is consistent with the
    spec's "When it reaches 0, set exit_in_progress = None" rule.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _range_bull_bars()
    ctx = _make_ctx(
        df,
        state=_seed_state(
            current_state="RANGE_BULL",
            exit_in_progress={"target": 0.5, "days_left": 2},
        ),
    )

    strat = AdxBbRegimeStrategy()

    # Bar 1 — prev == new, rule does not fire, but interpolation
    # applies (no-op).
    out1 = strat.generate(ctx)
    assert out1 == {"510300": 0.5}, (
        f"bar 1 (RANGE_BULL → RANGE_BULL, target == natural weight) "
        f"should emit 0.5 (no-op interpolation), got {out1}"
    )
    assert ctx.state["current_state"] == "RANGE_BULL"
    # days_left decremented from 2 → 1.
    assert ctx.state["exit_in_progress"] == {"target": 0.5, "days_left": 1}

    # Bar 2 — same: weight stays 0.5, days_left decremented to 0, dict
    # cleared.
    out2 = strat.generate(ctx)
    assert out2 == {"510300": 0.5}, (
        f"bar 2 (RANGE_BULL → RANGE_BULL, target == natural weight) "
        f"should still emit 0.5 (no-op interpolation), got {out2}"
    )
    assert ctx.state["exit_in_progress"] is None
