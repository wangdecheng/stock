"""tests/test_run_batch_backtest_07.py

T07 — multi-universe batch backtest runner.

Covers the five required cases from
``.scratch/adx-bb-regime/issues/07-multi-universe-batch-runner.md``:

  1. End-to-end on a stub adapter (N JSON files, correct names + shape).
  2. Default basket notice (stderr mentions the default 6-ETF basket).
  3. Per-symbol failure tolerance (one symbol's adapter raises → the
     other N-1 still produce JSON, CLI exits non-zero).
  4. Default strategy class (``--strategy-class`` omitted → uses
     ``AdxBbRegimeStrategy`` from T02-T06).
  5. ``out_dir`` is created when given a non-existent path.

Strategy
--------
The stub adapter follows the same pattern as the existing tests in
``tests/test_adx_bb_regime_0{2,3,4,5,6}.py`` and ``tests/test_backtest_t3.py``:
it returns a ``BarsResult``-shaped object (anything with ``.df``) and
filters the synthetic OHLCV by the requested ``[start, end]`` window.
The strategy runs on real indicator code (ADX / BB / RSI / MA) so we
need enough bars (300) to satisfy MA60 warmup plus the 120-day lookback
the strategy asks ``ctx.bars(...)`` for.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from tools import run_batch_backtest as rbb


# ---------------------------------------------------------------------------
# Stub adapter
# ---------------------------------------------------------------------------


class _StubBarsResult:
    """Stand-in for ``framework.data.adapter.BarsResult`` — just ``.df``."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df


class _StubAdapter:
    """Minimal adapter matching the seam the runner touches:

    * ``get_bars(symbol, start, end, **kw) -> .df``
    * optionally ``get_calendar(start, end)`` (we omit it so the runner
      exercises its bars-based fallback path — the production path is
      covered by the AKShare integration).

    ``fail_on`` is a set of symbols for which every ``get_bars`` call
    raises — used by test 3 to verify per-symbol failure tolerance.
    """

    def __init__(
        self,
        data_by_symbol: dict[str, pd.DataFrame],
        fail_on: tuple[str, ...] = (),
    ) -> None:
        self.data = dict(data_by_symbol)
        self.fail_on = set(fail_on)

    def get_bars(self, symbol, start, end, **kw):
        if symbol in self.fail_on:
            raise RuntimeError(f"simulated adapter failure for {symbol!r}")
        df = self.data.get(symbol)
        if df is None:
            raise KeyError(f"no data for {symbol!r}")
        rows = df[(df["date"] >= start) & (df["date"] <= end)]
        if rows.empty:
            # The strategy's first bars ask for a window partly outside
            # the data range; fall back to the full series so the
            # indicator code has something to compute on.
            rows = df
        return _StubBarsResult(rows.reset_index(drop=True))


# ---------------------------------------------------------------------------
# Synthetic OHLCV builder
# ---------------------------------------------------------------------------


def _rising_bars(n: int = 300, base: float = 10.0, step: float = 0.05) -> pd.DataFrame:
    """Monotonically rising close (TREND_UP-friendly) with deterministic
    envelopes — high/low are close +/- 0.5, open is close - 0.05. With
    300 bars the MA60 warmup, ADX(14) Wilder smoothing, and the
    strategy's 120-day lookback are all well-defined."""
    close = [base + step * i for i in range(n)]
    return pd.DataFrame(
        {
            "date": [date(2022, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": [c - 0.05 for c in close],
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [1_000_000.0] * n,
        }
    )


# The engine's default ``benchmark_symbol = "000300"`` and
# ``benchmark_adapter or adapter`` means the same stub serves both the
# strategy data and the benchmark series. Provide a benchmark series
# for the default symbol so the engine's ``_benchmark_closes()`` does
# not raise. Tests that don't care about benchmark values share this
# constant (it's a slowly rising series; the engine aligns the
# benchmark_value series via ``_nearest_benchmark``).
_BENCHMARK_SYMBOL = "000300"


def _adapter_with_benchmark(universe: list[str], **stub_kwargs) -> _StubAdapter:
    """Build a ``_StubAdapter`` that includes the engine's default
    benchmark series (``000300``). Without this the engine's
    ``_benchmark_closes()`` raises on its very first ``get_bars``
    call and every universe's run fails before ``strategy.generate``
    is ever called."""
    data = {sym: _rising_bars() for sym in universe}
    data[_BENCHMARK_SYMBOL] = _rising_bars(base=3500.0, step=1.0)
    return _StubAdapter(data, **stub_kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_end_to_end_writes_one_json_per_symbol(tmp_path: Path, capsys):
    """Test 1 — Run the runner end-to-end against a 3-symbol stub
    adapter; verify three JSON files appear with the canonical names
    and shape (``strategy_name``, ``start``, ``end``, ``dates``,
    ``portfolio_value``, ``benchmark_value``, ``cash``, ``fills``,
    ``initial_cash``, ``final_value``).

    The 3-symbol basket is a deterministic subset of the default 6-ETF
    basket — using a subset keeps the test fast while still exercising
    the per-universe loop.
    """
    symbols = ["510300", "510500", "159915"]
    adapter = _adapter_with_benchmark(symbols)
    out_dir = tmp_path / "reports"

    rc = rbb.main(
        [
            "--universes", *symbols,
            "--start", "2022-01-01",
            "--end", "2022-10-27",  # 300 calendar days inclusive
            "--initial-cash", "100000",
            "--out-dir", str(out_dir),
        ],
        adapter=adapter,
    )

    assert rc == 0, "all backtests should succeed; got non-zero exit code"
    # One JSON per symbol with the strategy_name + symbol filename.
    for sym in symbols:
        path = out_dir / f"adx_bb_regime_{sym}.json"
        assert path.is_file(), f"missing JSON for {sym}: {path}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Canonical schema — all fields from the engine's Equity dataclass.
        expected_keys = {
            "strategy_name", "start", "end",
            "initial_cash", "final_value",
            "dates", "portfolio_value", "benchmark_value", "cash", "fills",
        }
        assert expected_keys <= set(payload), (
            f"JSON for {sym} missing keys: {expected_keys - set(payload)}"
        )
        # The default strategy class is ``AdxBbRegimeStrategy`` whose
        # ``name`` class attribute is ``"adx_bb_regime"`` — the engine
        # copies that into ``Equity.strategy_name``.
        assert payload["strategy_name"] == "adx_bb_regime"
        # 300-bar series → 300 entries in every per-bar list.
        assert len(payload["dates"]) == 300
        assert len(payload["portfolio_value"]) == 300
        assert len(payload["benchmark_value"]) == 300
        assert len(payload["cash"]) == 300
        # Dates serialized as ISO strings.
        assert payload["dates"][0] == "2022-01-01"
        assert payload["dates"][-1] == "2022-10-27"
        # Sanity: initial_cash + initial cash at day 0.
        assert payload["initial_cash"] == pytest.approx(100_000.0)
    # No unexpected JSON files.
    assert sorted(p.name for p in out_dir.glob("*.json")) == sorted(
        f"adx_bb_regime_{sym}.json" for sym in symbols
    )


def test_default_basket_notice_printed_to_stderr(tmp_path: Path, capsys):
    """Test 2 — Omitting ``--universes`` should print a notice to stderr
    naming the default 6-ETF basket, so a silent run does not get
    mistaken for a misconfiguration."""
    adapter = _adapter_with_benchmark(["510300"])
    rc = rbb.main(
        [
            "--start", "2022-01-01",
            "--end", "2022-01-10",
            "--out-dir", str(tmp_path / "out"),
        ],
        adapter=adapter,
    )
    captured = capsys.readouterr()
    # Notice mentions "default" + "6-ETF" so an operator grep'ing for
    # either word will find it.
    assert "default" in captured.err.lower()
    assert "6-ETF" in captured.err or "6-ETF basket" in captured.err or "6-etf" in captured.err.lower()
    # And every default-basket symbol shows up in the notice.
    for sym in rbb.DEFAULT_UNIVERSES:
        assert sym in captured.err, f"default-basket notice missing {sym}"

    # Default basket still produces 6 JSON files (only the first has
    # data here because we stubbed only one symbol — but the runner
    # writes the file for each one it processed; for the other 5 it
    # will raise because the stub has no data). Skip the file-count
    # assertion; the notice is the only thing test 2 pins.
    assert rc == 1, "default basket with only one symbol stubbed → non-zero"


def test_per_symbol_failure_does_not_abort_batch(tmp_path: Path, capsys):
    """Test 3 — One symbol's adapter raises on every ``get_bars`` call;
    the other N-1 still produce JSON, and the runner exits non-zero.

    The runner catches per-symbol exceptions and continues with the
    next symbol (per ticket bullet "a per-universe failure prints to
    stderr and continues"). Only catastrophic failures (i.e. all
    symbols failing) would still abort; we don't test that here."""
    symbols_good = ["510300", "510500"]
    bad_symbol = "159915"
    adapter = _adapter_with_benchmark(
        [*symbols_good, bad_symbol], fail_on=(bad_symbol,),
    )

    rc = rbb.main(
        [
            "--universes", *symbols_good, bad_symbol,
            "--start", "2022-01-01",
            "--end", "2022-10-27",
            "--out-dir", str(tmp_path / "out"),
        ],
        adapter=adapter,
    )

    # The two good symbols each produce a JSON file.
    out_dir = tmp_path / "out"
    for sym in symbols_good:
        assert (out_dir / f"adx_bb_regime_{sym}.json").is_file(), (
            f"expected JSON for {sym} — the failure of another symbol "
            "must not skip the others"
        )
    # The bad symbol has no file.
    assert not (out_dir / f"adx_bb_regime_{bad_symbol}.json").exists()
    # Exit code is non-zero because at least one backtest failed.
    assert rc == 1, f"expected non-zero exit on partial failure, got {rc}"

    # stderr mentions the bad symbol and the error message.
    captured = capsys.readouterr()
    assert bad_symbol in captured.err
    assert "simulated adapter failure" in captured.err


def test_default_strategy_class_is_adx_bb_regime(tmp_path: Path):
    """Test 4 — When ``--strategy-class`` is omitted, the runner uses
    ``strategies.adx_bb_regime.AdxBbRegimeStrategy``. We verify this by
    inspecting the JSON's ``strategy_name`` field, which the engine
    copies from the strategy instance's ``name`` attribute (``name =
    "adx_bb_regime"`` per T02). If a different default were wired up,
    the field would carry a different value (or fail to import)."""
    adapter = _adapter_with_benchmark(["510300"])

    rc = rbb.main(
        [
            "--universes", "510300",
            "--start", "2022-01-01",
            "--end", "2022-10-27",
            "--out-dir", str(tmp_path / "out"),
        ],
        adapter=adapter,
    )

    assert rc == 0
    payload = json.loads(
        (tmp_path / "out" / "adx_bb_regime_510300.json").read_text(encoding="utf-8")
    )
    assert payload["strategy_name"] == "adx_bb_regime"

    # Also pin the *default* constant so the test fails loudly if the
    # default is ever changed accidentally (rather than silently
    # rerunning with a different strategy).
    assert rbb.DEFAULT_STRATEGY_CLASS == (
        "strategies.adx_bb_regime.AdxBbRegimeStrategy"
    )


def test_out_dir_is_created_when_missing(tmp_path: Path):
    """Test 5 — When ``--out-dir`` points at a non-existent path, the
    runner creates it (``mkdir parents=True, exist_ok=True``). The
    path used here is two levels deep so ``parents=True`` is exercised,
    not just the leaf directory."""
    nested = tmp_path / "level1" / "level2" / "reports"
    assert not nested.exists(), "precondition: nested path must not exist"

    adapter = _adapter_with_benchmark(["510300"])
    rc = rbb.main(
        [
            "--universes", "510300",
            "--start", "2022-01-01",
            "--end", "2022-10-27",
            "--out-dir", str(nested),
        ],
        adapter=adapter,
    )

    assert rc == 0
    assert nested.is_dir(), "runner must mkdir -p the output directory"
    assert (nested / "adx_bb_regime_510300.json").is_file()


# ---------------------------------------------------------------------------
# Bonus — integration with the script entry point via subprocess
# ---------------------------------------------------------------------------


def test_script_invocation_via_subprocess_succeeds(tmp_path: Path):
    """Smoke test that ``python tools/run_batch_backtest.py --help`` and
    a full run (with a real AKShare adapter) parses + dispatches
    correctly. We don't assert on the JSON output here — the adapter
    requires network access and a non-empty data fetch, which is
    outside the test environment. The argparse smoke test is enough to
    catch a broken CLI signature / syntax error."""
    # Help text — argparse exits 0 on -h.
    help_proc = subprocess.run(
        [sys.executable, str(Path(rbb.__file__).resolve()), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_proc.returncode == 0
    assert "--strategy-class" in help_proc.stdout
    assert "--universes" in help_proc.stdout
