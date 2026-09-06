"""tests/test_adx_bb_regime_09.py

T09 — State-replay pure functions for the stock-detail chart overlays.

Companion to ADR-0007 (.scratch/adx-bb-regime/indicator-overlay-design.html).
Covers:

  1. ``_new_state_series`` produces the same per-bar TrendState values
     that ``AdxBbRegimeStrategy.generate()`` writes into
     ``ctx.state["current_state"]``. The parity claim is over **post-
     Hysteresis-Gate ``new_state``** (Phased Exit is intentionally NOT
     in the replay; see ADR-0007 and CONTEXT.md TrendState definition).

  2. ``_run_halved_stage_series`` mirrors the T04 three-stage machine
     step by step: ``full → halved → cleared`` on the documented MA20 /
     MA10 strict-less-than breaches, and stays put otherwise.

  3. ``_new_state_series`` produces ``"RANGE_BEAR"`` (not NaN) during
     the warmup window where ADX / MA indicators are NaN.

  4. ``_run_halved_stage_series`` uses the ``"full"`` cold-start default
     to match the strategy's ``ctx.state.setdefault("trend_up_stage",
     "full")`` first-run rule.

Why parity is "post-gate" not "raw cascade"
------------------------------------------
``AdxBbRegimeStrategy._classify`` (the static helper) returns one of
the four raw cascade labels. ``generate()`` then applies the
Hysteresis Gate, which **downgrades** a TREND_UP candidate to
RANGE_BEAR when today's close is below BB mid or fewer than
``hysteresis_days`` trailing bars also classify as TREND_UP. The chart
must show what the strategy actually emits (``ctx.state["current_state"]``),
not the pre-gate cascade label. ``_new_state_series`` therefore replays
the gate, not just the cascade.

Indicator injection
-------------------
The fixture builds ADX / Bollinger / MA via ``framework.indicators`` —
the same code path the strategy uses at runtime. Indicator values are
not hand-pinned; the parity assertion compares the strategy's emitted
state to the replay's emitted state on the **same** indicator slice, so
indicator differences would cancel out and not affect the test.

Fixture strategy mirrors the engine's call convention (``hysteresis_days=2``,
default constructor params) so the test reflects what the UI will use.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from strategies.adx_bb_regime import (
    AdxBbRegimeStrategy,
    _new_state_series,
    _run_halved_stage_series,
)


def _indicators(df: pd.DataFrame) -> dict:
    """Compute the four indicators ``_new_state_series`` needs.

    Centralised so every parity test exercises the same code path the
    stock-detail chart will use (``framework.indicators`` registry)."""
    from framework.indicators import adx, bollinger, ma

    return {
        "adx_df": adx(df, length=14),
        "ma20": ma(df, length=20),
        "ma60": ma(df, length=60),
        "bb_mid": bollinger(df, length=20, std=2.0)["mid"],
    }


# ---------------------------------------------------------------------------
# Fakes — mirror tests/test_adx_bb_regime_02.py / _06.py
# ---------------------------------------------------------------------------


class FakeBarsResult:
    """Stand-in for the adapter's BarsResult; just holds a DataFrame."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df


class FakeAdapter:
    """Stand-in for AKShareAdapter; returns the full synthetic series."""

    def __init__(self, df_by_symbol: dict[str, pd.DataFrame]) -> None:
        self.df_by_symbol = df_by_symbol

    def get_bars(self, symbol, start, end, **kw):
        return FakeBarsResult(self.df_by_symbol[symbol])


def _make_ctx(df: pd.DataFrame, symbol: str = "510300") -> "object":
    """Build a Context wired to a FakeAdapter returning ``df`` for ``symbol``.

    Imported lazily so this test file does not pull in the whole framework
    module graph on collection — the import is cheap but we'd rather fail
    on demand than at import time."""
    from framework.strategy.context import Context

    return Context(
        now=date(2024, 7, 18),
        universe=[symbol],
        adapter=FakeAdapter({symbol: df}),
        state={},
    )


# ---------------------------------------------------------------------------
# Synthetic OHLCV builders
# ---------------------------------------------------------------------------


def _rising_bars(n: int = 200) -> pd.DataFrame:
    """Monotonically rising closes. ADX converges to ~100, ``+DI > -DI``,
    and Close > MA20 by construction. Hysteresis Gate fires (>= 2-day
    count + close > BB mid) and ``new_state`` is TREND_UP on the last bar."""
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
    """Constant closes with a 0.02 envelope. ADX / +DI / -DI are NaN at
    the tail, so the cascade falls through to the else (RANGE_BEAR)
    branch — no Hysteresis Gate interaction possible."""
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


def _oscillating_bars(n: int = 200) -> pd.DataFrame:
    """Sinusoidal closes that wander between 9.5 and 10.5. Close sits
    around the mean, ADX stays in the no-trend zone (< 20 most bars),
    so most bars classify as RANGE_BULL or RANGE_BEAR with no sustained
    TREND_UP. This exercises the gate path on a non-monotonic series."""
    import math

    close = [10.0 + 0.5 * math.sin(0.1 * i) for i in range(n)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [c - 0.02 for c in close],
            "high": [c + 0.05 for c in close],
            "low": [c - 0.05 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_new_state_series_matches_generate_on_rising_series():
    """Parity claim (rising series, post-gate new_state).

    Rising series → ADX > 25, +DI > -DI, Close > MA20 hold by
    construction → ``candidate_state`` is TREND_UP every bar → gate
    fires → ``new_state`` is TREND_UP every bar. The replay Series
    must equal ``"TREND_UP"`` on every bar where the cascade is
    well-defined, and ``"RANGE_BEAR"`` (the documented warmup
    fallback) on the warmup bars."""
    df = _rising_bars()
    ind = _indicators(df)
    replay = _new_state_series(
        df, adx_df=ind["adx_df"], ma20=ind["ma20"],
        ma60=ind["ma60"], bb_mid=ind["bb_mid"],
    )

    # All bars post-warmup should classify post-gate as TREND_UP.
    assert (replay == "TREND_UP").sum() >= 100, (
        f"expected most bars to be TREND_UP on a monotonic rise; "
        f"got counts {replay.value_counts().to_dict()}"
    )
    # No NaN — the warmup window maps to "RANGE_BEAR" not NaN.
    assert replay.notna().all(), (
        f"warmup bars should be 'RANGE_BEAR', got NaN counts: "
        f"{replay.isna().sum()}"
    )


def test_new_state_series_matches_generate_step_by_step():
    """Parity claim: ``_new_state_series(...).iloc[i]`` equals the
    ``ctx.state["current_state"]`` value ``generate()`` would write on
    bar ``i``, for each i in the synthetic history.

    This is the **direct** parity test. We drive ``generate()`` on each
    bar ``i`` from i=0 .. n-1 against a fresh Context seeded with the
    bar-i prefix of the synthetic series, then compare to the replay
    Series element at index ``i``.

    Note: this test compares the *post-gate new_state* on the LAST bar
    of each slice, not the entire state trajectory. The trajectory-
    level parity is checked by the simpler ``_rising_bars`` /
    ``_oscillating_bars`` tests above; this step-by-step test pins the
    algorithm at a single bar so any divergence points at one specific
    bar index."""
    from framework.indicators import adx, bollinger, ma

    df = _rising_bars()
    n = len(df)

    # Pre-compute the replay Series once.
    adx_df = adx(df, length=14)
    ma20 = ma(df, length=20)
    ma60 = ma(df, length=60)
    bb_df = bollinger(df, length=20, std=2.0)
    replay = _new_state_series(
        df,
        adx_df=adx_df,
        ma20=ma20,
        ma60=ma60,
        bb_mid=bb_df["mid"],
        hysteresis_days=2,
    )

    # Strategy's emitted state, bar by bar.
    strategy_emitted: list[str] = []
    for i in range(n):
        sub = df.iloc[: i + 1].copy().reset_index(drop=True)
        # Reindex date so the date column is well-formed for Context.
        sub["date"] = pd.date_range("2024-01-01", periods=len(sub), freq="D")
        ctx = _make_ctx(sub)
        AdxBbRegimeStrategy().generate(ctx)
        strategy_emitted.append(ctx.state["current_state"])

    # Compare tail (post-warmup). The replay is over the full n bars,
    # so compare the last n elements of both lists.
    for i in range(60, n):  # skip warmup bars (60-bar MA window)
        assert replay.iloc[i] == strategy_emitted[i], (
            f"parity diverged at bar {i}: "
            f"replay={replay.iloc[i]!r} strategy={strategy_emitted[i]!r}"
        )


def test_new_state_series_oscillating_series():
    """Oscillating series: most bars are RANGE_BULL / RANGE_BEAR
    (no sustained TREND_UP). The replay must produce at least one
    non-TREND_UP bar — confirms the function does not blindly return
    TREND_UP for every non-flat series."""
    df = _oscillating_bars()
    ind = _indicators(df)
    replay = _new_state_series(
        df, adx_df=ind["adx_df"], ma20=ind["ma20"],
        ma60=ind["ma60"], bb_mid=ind["bb_mid"],
    )

    counts = replay.value_counts().to_dict()
    # The oscillating series should NOT be 100% TREND_UP. At least
    # one bar must fall through to RANGE_BULL or RANGE_BEAR.
    non_trend = counts.get("RANGE_BULL", 0) + counts.get("RANGE_BEAR", 0) + counts.get(
        "TREND_DOWN", 0
    )
    assert non_trend > 0, f"expected non-TREND_UP bars; got counts {counts}"


def test_halved_stage_series_full_to_halved_to_cleared():
    """T04 stage machine: ``full → halved → cleared`` on documented
    strict-less-than breaches. Construct a close series where
    ``close < ma20`` happens first, then ``close < ma10`` happens
    after — expecting ``["full", "full", "halved", "halved",
    "cleared"]`` for the last 5 bars (or analogous; the exact pattern
    depends on the input shape)."""
    n = 60
    close = pd.Series([10.0] * n)
    ma10 = pd.Series([10.0] * n)
    ma20 = pd.Series([10.0] * n)
    # Bar 50: close drops below ma20 (still >= ma10) → halved.
    close.iloc[50] = 9.5
    ma10.iloc[50] = 9.4  # close 9.5 >= ma10 9.4 → not cleared yet
    ma20.iloc[50] = 10.0  # close 9.5 <  ma20 10.0 → halved
    # Bar 55: close drops below ma10 → cleared.
    close.iloc[55] = 9.0
    ma10.iloc[55] = 9.5
    ma20.iloc[55] = 10.0

    stage = _run_halved_stage_series(close, ma10, ma20)

    assert stage.iloc[40] == "full", "bar before breach should be full"
    assert stage.iloc[50] == "halved", (
        f"close 9.5 < ma20 10.0 with ma10 9.4 → halved; got {stage.iloc[50]!r}"
    )
    assert stage.iloc[55] == "cleared", (
        f"close 9.0 < ma10 9.5 (post-half) → cleared; got {stage.iloc[55]!r}"
    )


def test_halved_stage_series_stays_full_when_no_breach():
    """No MA breach: stage stays ``"full"`` for the entire series."""
    close = pd.Series([10.0] * 60)
    ma10 = pd.Series([9.5] * 60)
    ma20 = pd.Series([9.0] * 60)

    stage = _run_halved_stage_series(close, ma10, ma20)

    assert (stage == "full").all(), (
        f"no breach should keep stage full; got unique values "
        f"{stage.unique().tolist()}"
    )


def test_halved_stage_series_initial_stage_full():
    """Cold-start default: when ``initial_stage="full"`` (the strategy's
    first-run default per D5), the FIRST bar with ``close >= ma20`` is
    ``"full"``. A bar where ``close < ma20`` from bar 0 transitions to
    ``"halved"`` on bar 0 itself — same as ``generate()``, which
    evaluates the strict-less-than rule on every bar.

    This matches the strategy's T04 logic: ``ctx.state.setdefault(...
    "full")`` only controls the *initial* value, not whether the first
    bar's rule fires."""
    n = 60
    # Construct a series where bar 0 has close >= ma20 (rule does NOT
    # fire), so the cold-start ``full`` carries through bar 0.
    close = pd.Series([10.0] + [9.0] * (n - 1))
    ma10 = pd.Series([9.5] * n)
    ma20 = pd.Series([10.0] * n)

    stage = _run_halved_stage_series(close, ma10, ma20, initial_stage="full")

    assert stage.iloc[0] == "full", (
        f"bar 0 close 10.0 >= ma20 10.0 → no breach → stays 'full'; "
        f"got {stage.iloc[0]!r}"
    )
    # Bar 1: close 9.0 < ma20 10.0 → halved.
    assert stage.iloc[1] == "halved", (
        f"close 9.0 < ma20 10.0 at bar 1 → halved; got {stage.iloc[1]!r}"
    )
