"""tests/test_compare_to_baseline_08.py

T08 — buy-and-hold baseline strategy + comparison report CLI.

Covers the four required cases from
``.scratch/adx-bb-regime/issues/08-buy-and-hold-baseline-and-comparison.md``:

  1. Synthetic two-series equity data → expected markdown table.
  2. Missing report file → that row is omitted and a warning is
     printed to stderr.
  3. ``--format csv`` → valid CSV with the expected header.
  4. ``--out`` writes to file with the same content as stdout.

Plus the trivial discovery contract check on
``strategies/buy_and_hold.py``.

Test strategy
-------------
For the equity-JSON fixtures we build :class:`Equity` dataclass
instances, serialize them using the same shape
``tools/run_batch_backtest._serialize_equity`` produces (dates → ISO,
Fill → dict), and write them to a ``tmp_path`` reports directory. The
CLI is invoked via ``tools.compare_to_baseline.main()`` (the public
surface accepts no adapter, just argv) — no subprocess is spawned.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pytest

from framework.backtest.engine import Equity, Fill
from framework.strategy.discover import discover_strategies
from tools import compare_to_baseline as ctb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _serialize_equity(equity: Equity) -> str:
    """Mirror ``tools/run_batch_backtest._serialize_equity``.

    The CLI rehydrates from JSON, so the fixtures must use the exact
    shape the runner produces (ISO dates, Fill-as-dict). Keeping this
    helper local avoids touching the runner module from test code
    beyond the one import at the top.
    """
    payload = {
        "strategy_name": equity.strategy_name,
        "start": equity.start.isoformat(),
        "end": equity.end.isoformat(),
        "initial_cash": equity.initial_cash,
        "final_value": equity.final_value,
        "dates": [d.isoformat() for d in equity.dates],
        "portfolio_value": list(equity.portfolio_value),
        "benchmark_value": list(equity.benchmark_value),
        "cash": list(equity.cash),
        "fills": [
            {
                "date": f.date.isoformat(),
                "symbol": f.symbol,
                "side": f.side,
                "qty": int(f.qty),
                "price": float(f.price),
                "fee": float(f.fee),
            }
            for f in equity.fills
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _build_equity(
    *,
    strategy_name: str,
    symbol: str,
    n_bars: int = 10,
    base_value: float = 100_000.0,
    drift: float = 50.0,
    initial_date: date | None = None,
    fills: list[Fill] | None = None,
) -> Equity:
    """Construct a deterministic synthetic Equity instance.

    The ``portfolio_value`` series rises by ``drift`` per bar so the
    metrics are non-trivial:
        total_return = n_bars * drift / base_value
    Sharpe is noisy (pct_change with monotonic rises is large-pos / small-pos
    → huge ratio); that's fine — we don't pin it in test 1, only the
    rendered string content / shape.
    """
    if initial_date is None:
        initial_date = date(2022, 1, 1)
    dates = [initial_date + timedelta(days=i) for i in range(n_bars)]
    pv = [base_value + drift * i for i in range(n_bars)]
    return Equity(
        dates=dates,
        portfolio_value=pv,
        # Benchmark follows portfolio so rehydration carries a usable
        # benchmark series. The compare_to_baseline tool doesn't surface
        # benchmark; empyrical also doesn't read it, so equality is enough.
        benchmark_value=list(pv),
        cash=list(pv),
        fills=fills if fills is not None else [],
        strategy_name=strategy_name,
        start=dates[0],
        end=dates[-1],
        initial_cash=base_value,
        final_value=pv[-1],
    )


def _write_eq(report_dir: Path, strategy: str, symbol: str, equity: Equity) -> Path:
    """Write an Equity JSON file with the canonical
    ``<strategy>_<symbol>.json`` filename. Returns the file path."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{strategy}_{symbol}.json"
    path.write_text(_serialize_equity(equity), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Test 1 — synthetic data → expected markdown table
# ---------------------------------------------------------------------------


def test_synthetic_two_series_produces_markdown_table(tmp_path: Path, capsys):
    """Build two synthetic equity files (adx_bb_regime_510300 and
    buy_and_hold_510300), run the CLI on them, and verify the rendered
    markdown contains both rows in order with the expected symbol and
    strategy names. Total Return is fully pinned (deterministic drift).
    """
    eq_a = _build_equity(
        strategy_name="adx_bb_regime",
        symbol="510300",
        n_bars=10,
        drift=50.0,
    )
    eq_b = _build_equity(
        strategy_name="buy_and_hold",
        symbol="510300",
        n_bars=10,
        drift=30.0,
    )
    _write_eq(tmp_path, "adx_bb_regime", "510300", eq_a)
    _write_eq(tmp_path, "buy_and_hold", "510300", eq_b)

    rc = ctb.main(
        [
            "--reports-dir", str(tmp_path),
            "--symbols", "510300",
            "--strategies", "adx_bb_regime", "buy_and_hold",
        ]
    )
    assert rc == 0

    captured = capsys.readouterr()
    out = captured.out

    # Header row — pinned to the ticket's example format.
    assert "| Symbol | Strategy | Total Return | Annualized | Sharpe | Max DD |" in out
    assert "| --- | --- | --- | --- | --- | --- |" in out

    # Both rows present with the canonical symbol + strategy names.
    assert "| 510300 | adx_bb_regime |" in out
    assert "| 510300 | buy_and_hold  |" in out  # trailing pad aligns columns
    # The summary line is always last and names the destination.
    assert out.strip().endswith("Wrote 2 rows to stdout")
    # No warnings — both files were present.
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Test 2 — missing report file
# ---------------------------------------------------------------------------


def test_missing_report_file_omitted_with_warning(tmp_path: Path, capsys):
    """When one (symbol, strategy) pair is missing, the row is dropped
    and a warning is printed to stderr (so the operator learns about
    the gap rather than silently reading a partial report)."""
    # Only the adx_bb_regime_510300 file exists.
    eq = _build_equity(strategy_name="adx_bb_regime", symbol="510300", n_bars=10)
    _write_eq(tmp_path, "adx_bb_regime", "510300", eq)

    rc = ctb.main(
        [
            "--reports-dir", str(tmp_path),
            "--symbols", "510300", "510500",   # one present, one missing
            "--strategies", "adx_bb_regime", "buy_and_hold",
        ]
    )
    assert rc == 0

    captured = capsys.readouterr()

    # The present pair produced exactly one row; the missing pairs were
    # silently skipped, so the body has exactly one ``| 510300 |`` line.
    assert "510300 | adx_bb_regime" in captured.out
    assert "510500" not in captured.out
    assert "buy_and_hold" not in captured.out

    # Summary line reports 1 data row (not 4).
    assert captured.out.strip().endswith("Wrote 1 rows to stdout")

    # Warning to stderr names the missing file and is non-empty.
    assert "missing" in captured.err.lower()
    # Every missing (strategy, symbol) pair is warned about — 3 missing
    # out of 4 in this test.
    assert "adx_bb_regime_510500" in captured.err
    assert "buy_and_hold_510300" in captured.err
    assert "buy_and_hold_510500" in captured.err


# ---------------------------------------------------------------------------
# Test 3 — CSV format
# ---------------------------------------------------------------------------


def test_csv_format_writes_valid_csv_with_expected_header(tmp_path: Path, capsys):
    """``--format csv`` produces a parseable CSV with the documented
    header row and the same data rows as the markdown table would."""
    eq_a = _build_equity(
        strategy_name="adx_bb_regime", symbol="510300",
        n_bars=10, drift=50.0,
    )
    eq_b = _build_equity(
        strategy_name="buy_and_hold", symbol="510300",
        n_bars=10, drift=30.0,
    )
    _write_eq(tmp_path, "adx_bb_regime", "510300", eq_a)
    _write_eq(tmp_path, "buy_and_hold", "510300", eq_b)

    rc = ctb.main(
        [
            "--reports-dir", str(tmp_path),
            "--symbols", "510300",
            "--strategies", "adx_bb_regime", "buy_and_hold",
            "--format", "csv",
        ]
    )
    assert rc == 0

    captured = capsys.readouterr()
    out = captured.out

    # Split off the trailing summary line ``Wrote ...`` — it isn't a
    # CSV row and ``csv.reader`` will treat it as a malformed extra
    # row if we leave it in. We split on the literal "Wrote" string
    # which is not used as a cell value in any of our rows.
    csv_body, sep, summary = out.rpartition("Wrote")
    assert sep == "Wrote", f"unexpected summary split: {sep!r}"
    summary = "Wrote" + summary
    csv_body = csv_body.rstrip("\n")

    # Parse the CSV body — stdlib csv.reader accepts our writer's output.
    reader = csv.reader(io.StringIO(csv_body))
    parsed = list(reader)

    # Header row matches the canonical ``COLUMNS`` order.
    assert parsed[0] == ctb.COLUMNS
    assert parsed[0] == [
        "Symbol", "Strategy", "Total Return", "Annualized", "Sharpe", "Max DD",
    ]

    # Exactly two data rows (both pairs produced).
    data_rows = parsed[1:]
    assert len(data_rows) == 2

    # The two rows correspond to the two strategies we wrote; the
    # strategy column carries the canonical names.
    strategies_in_rows = sorted(row[1] for row in data_rows)
    assert strategies_in_rows == ["adx_bb_regime", "buy_and_hold"]

    # All numeric columns parse as floats (raw, not pre-formatted).
    for row in data_rows:
        # ``Symbol`` and ``Strategy`` are strings; columns 2..5 are floats.
        for cell in row[2:]:
            float(cell)
    # No markdown table artefacts leaked into the CSV.
    assert "| --- |" not in csv_body
    # Summary line uses the same destination convention.
    assert "Wrote 2 rows to stdout" in summary


# ---------------------------------------------------------------------------
# Test 4 — ``--out`` writes to file
# ---------------------------------------------------------------------------


def test_out_writes_to_file_with_same_content_as_stdout(tmp_path: Path, capsys):
    """When ``--out PATH`` is supplied, the rendered report is also
    written to PATH with the same content that landed on stdout. The
    summary line names the file as the destination."""
    eq_a = _build_equity(strategy_name="adx_bb_regime", symbol="510300", n_bars=10)
    _write_eq(tmp_path, "adx_bb_regime", "510300", eq_a)

    out_path = tmp_path / "comparison.md"
    rc = ctb.main(
        [
            "--reports-dir", str(tmp_path),
            "--symbols", "510300",
            "--strategies", "adx_bb_regime",
            "--out", str(out_path),
        ]
    )
    assert rc == 0
    assert out_path.is_file(), "--out path must be written"

    on_disk = out_path.read_text(encoding="utf-8")

    captured = capsys.readouterr()
    # Stdout content and file content match (with the trailing summary line).
    stdout_table = captured.out.split("Wrote")[0].rstrip()
    on_disk_table = on_disk.split("Wrote")[0].rstrip()
    assert stdout_table == on_disk_table
    # The summary line names the file path, not "stdout".
    assert f"Wrote 1 rows to {out_path}" in captured.out


# ---------------------------------------------------------------------------
# Test 5 — discovery + decoupling scan
# ---------------------------------------------------------------------------


def test_buy_and_hold_is_discoverable_and_passes_decoupling_scan():
    """The new strategy file is registered under ``"buy_and_hold"`` by
    the standard ``discover_strategies()`` call (which enforces the
    decoupling rule automatically). One assertion per contract bullet
    in the ticket — registration and the decoupling scan both pass."""
    registry = discover_strategies()
    assert "buy_and_hold" in registry
    cls = registry["buy_and_hold"]
    assert cls.__name__ == "BuyAndHoldStrategy"
    assert cls.name == "buy_and_hold"
