"""tests/test_adx_bb_regime_04.py

T04 — Trend_Up HalvedStage internal stop machine.

Covers the five acceptance bullets from
``.scratch/adx-bb-regime/issues/04-trend-up-two-stage-stop.md``:

  1. Stage ``full`` + ``Close < MA20``  →  stage ``halved``, weight 0.5.
  2. Stage ``halved`` + ``Close < MA10``  →  stage ``cleared``, weight 0.0.
  3. Stage ``halved`` + ``Close > MA20`` (no breach)  →  stage stays
     ``halved``, weight stays 0.5 — no double-trigger on a recovered bar.
  4. Re-entry from another state (RANGE_BULL) into TREND_UP resets
     ``trend_up_stage`` to ``"full"`` (a fresh trend gets the full
     two-stage stop again).
  5. Boundary: ``Close == MA20`` does NOT trigger the full → halved
     transition (the rule uses strict ``<``).

Test fixtures mirror ``tests/test_adx_bb_regime_02.py`` /
``tests/test_adx_bb_regime_03.py`` (``FakeBarsResult`` / ``FakeAdapter``)
and the ``_swap_indicator`` pattern.

A note on the cascade vs. the HalvedStage rule
----------------------------------------------
The D3 cascade classifies a bar as ``TREND_UP`` only when
``Close > MA20`` (strict). The HalvedStage rule fires on
``Close < MA20`` (strict). These are mutually exclusive on the same
bar — which means the full → halved transition can never fire under a
real cascade. To exercise the rule, tests #1, #2, #3, and #5 monkey-
patch ``AdxBbRegimeStrategy._classify`` to always return ``"TREND_UP"``
and seed ``ctx.state["current_state"] = "TREND_UP"`` so the
HalvedStage block is reached. Test #4 uses a real rising series so the
cascade produces ``TREND_UP`` naturally (no monkey-patch needed).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

import framework.indicators as _indicators_mod
from framework.indicators import register as indicators_register
from framework.strategy.context import Context


# ---------------------------------------------------------------------------
# Fakes — same pattern as tests/test_adx_bb_regime_02.py / _03.py
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
# Synthetic OHLCV builders
# ---------------------------------------------------------------------------


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


def _rising_then_last_below_ma20(n: int = 200, *, below_by: float = 0.01) -> pd.DataFrame:
    """Rising closes with the last bar dropped to ``MA20 - below_by`` (so
    ``Close < MA20`` strictly holds). The drop is small enough that
    ``Close > MA10`` still holds because ``MA10 < MA20`` on the rising
    tail — this is the "shallow dip" the spec describes for stage
    ``full`` → ``halved``."""
    close = [10.0 + 0.1 * i for i in range(n - 1)]
    ma20_prev = sum(close[n - 21 : n - 1]) / 20.0
    close.append(ma20_prev - below_by)
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


def _rising_then_last_below_ma10(n: int = 200, *, below_by: float = 0.01) -> pd.DataFrame:
    """Rising closes with the last bar dropped to ``MA10 - below_by`` so
    ``Close < MA10`` strictly holds. This is the "deep dip" the spec
    describes for stage ``halved`` → ``cleared``."""
    close = [10.0 + 0.1 * i for i in range(n - 1)]
    ma10_prev = sum(close[n - 11 : n - 1]) / 10.0
    close.append(ma10_prev - below_by)
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


def _constant_bars(n: int = 200, *, level: float = 10.0) -> pd.DataFrame:
    """Constant close at ``level`` — after warmup ``MA10 == MA20 ==
    MA60 == level``, so ``Close == MA20`` holds exactly on the last bar
    (this is the boundary case)."""
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [level] * n,
            "high": [level + 0.01] * n,
            "low": [level - 0.01] * n,
            "close": [level] * n,
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
    ``Close > BB mid`` check always passes. Required for tests where
    the cascade has been forced to ``TREND_UP`` but the synthetic
    close is below MA20 (≈ BB mid) on the last bar.
    """
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
# _classify monkey-patch helper — saves and restores the original static
# method so tests don't permanently change the strategy.
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
        # Capture the staticmethod descriptor from the class ``__dict__``
        # directly. Reading ``cls._classify`` invokes the descriptor
        # protocol and would unwrap the staticmethod, leaving us with
        # a bare function — restoring it would then bind ``self`` on
        # every call and silently shift the argument count.
        self._original = adx_bb_regime.AdxBbRegimeStrategy.__dict__[
            "_classify"
        ]

    def __enter__(self):
        # Wrap ``fn`` in staticmethod so the descriptor protocol treats
        # the replacement identically to the original ``@staticmethod``-
        # decorated function — without this, ``self._classify(args)``
        # would forward ``self`` as the first argument.
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
    """Build a ``ctx.state`` dict pre-populated for a TREND_UP-in-progress
    test. Anything not provided falls back to the D5 defaults — matches
    what the strategy would have written after a warm-up run."""
    state = {
        "current_state": "TREND_UP",
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


def test_full_to_halved_on_close_below_ma20():
    """Stage ``full`` + ``Close < MA20``  →  stage ``halved``, weight 0.5.

    Seeds ``current_state = "TREND_UP"`` and ``trend_up_stage = "full"``
    so the strategy is "5 up bars in, now dipping"; forces the cascade
    to enter TREND_UP via a ``_classify`` monkey-patch (the cascade's
    strict ``Close > MA20`` check would otherwise reject the dip bar)
    and pins the Bollinger mid at 0 so the Hysteresis Gate's
    ``Close > BB mid`` check still passes.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_then_last_below_ma20()
    ctx = _make_ctx(df, state=_seed_state(trend_up_stage="full"))

    with _swap_classify(lambda *a, **k: "TREND_UP"), _swap_indicator(
        "bollinger", _fake_bollinger_low_mid
    ):
        out = AdxBbRegimeStrategy().generate(ctx)

    assert ctx.state["current_state"] == "TREND_UP"
    assert ctx.state["trend_up_stage"] == "halved"
    assert out == {"510300": 0.5}


def test_halved_to_cleared_on_close_below_ma10():
    """Stage ``halved`` + ``Close < MA10``  →  stage ``cleared``, weight 0.0.

    Seeds ``trend_up_stage = "halved"`` (the previous bar already saw
    ``Close < MA20``); now the dip deepens below MA10 → fully out.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_then_last_below_ma10()
    ctx = _make_ctx(df, state=_seed_state(trend_up_stage="halved"))

    with _swap_classify(lambda *a, **k: "TREND_UP"), _swap_indicator(
        "bollinger", _fake_bollinger_low_mid
    ):
        out = AdxBbRegimeStrategy().generate(ctx)

    assert ctx.state["current_state"] == "TREND_UP"
    assert ctx.state["trend_up_stage"] == "cleared"
    assert out == {"510300": 0.0}


def test_halved_stays_halved_on_recovered_bar():
    """Stage ``halved`` + ``Close > MA20`` (no breach)  →  stage stays
    ``halved``, weight stays 0.5. The HalvedStage rule must NOT
    double-trigger on a bar that recovers above MA20 but stays above
    MA10 — once we are halved, only ``Close < MA10`` can advance us.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_bars()  # last close > MA20 > MA10
    ctx = _make_ctx(df, state=_seed_state(trend_up_stage="halved"))

    with _swap_classify(lambda *a, **k: "TREND_UP"), _swap_indicator(
        "bollinger", _fake_bollinger_low_mid
    ):
        out = AdxBbRegimeStrategy().generate(ctx)

    assert ctx.state["current_state"] == "TREND_UP"
    assert ctx.state["trend_up_stage"] == "halved"
    assert out == {"510300": 0.5}


def test_reentry_from_range_bull_resets_stage_to_full():
    """Re-entering TREND_UP from another state (``RANGE_BULL`` here)
    resets ``trend_up_stage`` to ``"full"`` so a fresh trend gets the
    full two-stage stop again — even if a stale ``halved`` / ``cleared``
    stage was sitting in ``ctx.state``.

    Uses a real rising series so the cascade + Hysteresis Gate produce
    TREND_UP naturally — no monkey-patch needed.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _rising_bars()
    ctx = _make_ctx(
        df,
        state=_seed_state(current_state="RANGE_BULL", trend_up_stage="halved"),
    )

    out = AdxBbRegimeStrategy().generate(ctx)

    assert ctx.state["current_state"] == "TREND_UP"
    # Stale "halved" from the previous (now-departed) trend must be
    # reset to "full" on re-entry.
    assert ctx.state["trend_up_stage"] == "full"
    assert out == {"510300": 1.0}


def test_boundary_close_equal_to_ma20_no_transition():
    """Boundary case: ``Close == MA20`` does NOT trigger the
    ``full → halved`` transition. The HalvedStage rule uses strict
    ``<``, so equality is "no breach".

    On the constant-close series, ``Close == MA20 == 10.0`` exactly.
    The cascade's strict ``Close > MA20`` check would otherwise reject
    the bar (returning RANGE_BEAR), so we monkey-patch ``_classify`` to
    force the strategy into the TREND_UP branch where the HalvedStage
    rule evaluates. The Bollinger mid is pinned at 0 so the Hysteresis
    Gate's ``Close > BB mid`` check still passes.
    """
    from strategies.adx_bb_regime import AdxBbRegimeStrategy

    df = _constant_bars()
    ctx = _make_ctx(df, state=_seed_state(trend_up_stage="full"))

    with _swap_classify(lambda *a, **k: "TREND_UP"), _swap_indicator(
        "bollinger", _fake_bollinger_low_mid
    ):
        out = AdxBbRegimeStrategy().generate(ctx)

    # Close == MA20 → strict "<" is False → no transition.
    assert ctx.state["current_state"] == "TREND_UP"
    assert ctx.state["trend_up_stage"] == "full"
    assert out == {"510300": 1.0}
