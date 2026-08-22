"""tools/compare_to_baseline.py — strategy-vs-baseline comparison report.

T08 — implements ticket
``.scratch/adx-bb-regime/issues/08-buy-and-hold-baseline-and-comparison.md``.

Usage
-----
::

    python tools/compare_to_baseline.py
    python tools/compare_to_baseline.py --symbols 510300 159915
    python tools/compare_to_baseline.py --strategies adx_bb_regime buy_and_hold
    python tools/compare_to_baseline.py --format csv
    python tools/compare_to_baseline.py --out comparison.md

What it does
------------
For each (symbol, strategy) pair in the cross product of ``--symbols``
(default: curated 6-ETF basket) and ``--strategies`` (default:
``["adx_bb_regime", "buy_and_hold"]``):

1. Loads ``<reports-dir>/<strategy>_<symbol>.json``. Missing files are
   skipped with a warning to stderr so partial runs still produce a
   usable report.
2. Rehydrates an :class:`framework.backtest.Equity` from the JSON (dates
   are ISO strings → ``datetime.date``, ``Fill`` → ``Fill(**dict)``).
3. Computes the four-card row via
   ``framework.backtest.metrics.compute(equity)`` and renders it.

Output
------
* Markdown (default) — fixed header per the ticket's style.
* CSV (``--format csv``) — same columns, one row per pair.
* Stdout by default; ``--out PATH`` writes the same content to a file.
* A final ``Wrote N rows to <stdout|file>`` summary line.

Dependencies
------------
Pure stdlib + :mod:`framework.backtest.metrics`. No network / data
access — this tool only reads the JSON files that ``tools/run_batch_backtest.py``
produces.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


# Curated 6-ETF basket per spec D6 — the comparison report's default.
DEFAULT_SYMBOLS: list[str] = [
    "510300", "510500", "159915", "512760", "512000", "511010",
]

# Default strategy names — the regime strategy + the buy-and-hold
# baseline from T08 itself. Both files are produced by
# ``tools/run_batch_backtest.py`` for each universe.
DEFAULT_STRATEGIES: list[str] = ["adx_bb_regime", "buy_and_hold"]

DEFAULT_REPORTS_DIR = "reports"

# Output column order — pinned by the ticket's markdown example; metric
# names match ``framework.backtest.metrics.compute`` keys so the same
# four appear in every row.
COLUMNS: list[str] = [
    "Symbol", "Strategy", "Total Return", "Annualized", "Sharpe", "Max DD",
]

# Metric keys surfaced by ``framework.backtest.metrics.compute``. Used to
# build a row dict once and render both formats from a single source.
_METRIC_KEYS: list[tuple[str, str]] = [
    ("Symbol", None),
    ("Strategy", None),
    ("Total Return", "total_return"),
    ("Annualized", "annualized"),
    ("Sharpe", "sharpe"),
    ("Max DD", "max_drawdown"),
]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point.

    Returns the POSIX exit code. The body is split into ``_parse_args``,
    ``_build_rows``, and ``_format_rows`` so the same logic is reachable
    from tests via ``build_rows()`` and ``format_rows()`` without
    spawning a subprocess.
    """
    args = _parse_args(argv)
    rows = build_rows(
        reports_dir=Path(args.reports_dir),
        symbols=list(args.symbols),
        strategies=list(args.strategies),
    )
    output = format_rows(rows, fmt=args.format)
    return write_output(
        text=output, n_rows=len(rows), fmt=args.format, out_path=args.out
    )


def build_rows(
    *,
    reports_dir: Path,
    symbols: Iterable[str],
    strategies: Iterable[str],
) -> list[dict[str, Any]]:
    """Read each (strategy, symbol) JSON and return one dict per row.

    Each row carries all ``COLUMNS`` keys — the renderers (markdown/CSV)
    can use the row dict directly. Missing files are skipped with a
    warning to stderr (per the ticket) and produce no row.
    """
    # We import inside the function so the module-load path stays
    # light: ``framework.backtest.engine`` pulls in ``Context`` plus
    # the indicator registry, which we don't want as a hard cost for
    # ``python tools/compare_to_baseline.py --help``.
    from framework.backtest.metrics import compute

    rows: list[dict[str, Any]] = []
    for strategy in strategies:
        for symbol in symbols:
            path = reports_dir / f"{strategy}_{symbol}.json"
            if not path.is_file():
                print(
                    f"[compare_to_baseline] missing {path} — skipping "
                    f"(symbol={symbol}, strategy={strategy})",
                    file=sys.stderr,
                )
                continue
            try:
                equity = _load_equity(path)
                metrics = compute(equity)
            except Exception as exc:
                # A malformed JSON / metrics failure on one pair should
                # not poison the whole report — log and continue.
                print(
                    f"[compare_to_baseline] {path}: failed to compute "
                    f"metrics ({type(exc).__name__}: {exc}) — skipping",
                    file=sys.stderr,
                )
                continue
            rows.append(
                {
                    "Symbol": symbol,
                    "Strategy": strategy,
                    "Total Return": metrics["total_return"],
                    "Annualized": metrics["annualized"],
                    "Sharpe": metrics["sharpe"],
                    "Max DD": metrics["max_drawdown"],
                }
            )
    return rows


def format_rows(rows: list[dict[str, Any]], *, fmt: str) -> str:
    """Render the rows as a string in the requested format.

    ``fmt`` is either ``"md"`` (default) or ``"csv"``. The renderer
    is format-only — it doesn't touch the filesystem.
    """
    if fmt == "csv":
        return _format_csv(rows)
    if fmt == "md":
        return _format_markdown(rows)
    raise ValueError(f"unknown format: {fmt!r} (expected 'md' or 'csv')")


def write_output(
    text: str,
    *,
    n_rows: int,
    fmt: str,
    out_path: Optional[str],
) -> int:
    """Print ``text`` to stdout and (optionally) also write to ``out_path``.

    Always prints a trailing summary line so the operator (and tests) can
    see exactly how many data rows the report contains and where it was
    written. Exit code is always 0 — partial reports are signaled via
    warnings, not via exit status (the ticket spec doesn't pin an
    exit code and the runner's ``default 6-ETF basket`` routinely
    produces missing-file warnings in the wild).
    """
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    if out_path is not None:
        Path(out_path).write_text(text, encoding="utf-8")
    destination = out_path if out_path is not None else "stdout"
    print(f"Wrote {n_rows} rows to {destination}")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python tools/compare_to_baseline.py",
        description=(
            "Compare backtest reports for one or more strategies across "
            "a basket of symbols. Reads <reports-dir>/<strategy>_<symbol>.json "
            "files (produced by tools/run_batch_backtest.py) and emits a "
            "markdown or CSV table with Total Return / Annualized / Sharpe "
            "/ Max Drawdown for each (symbol, strategy) pair."
        ),
    )
    p.add_argument(
        "--reports-dir",
        default=DEFAULT_REPORTS_DIR,
        help=(
            "Directory containing <strategy>_<symbol>.json files. "
            "Default: %(default)s."
        ),
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        metavar="SYM",
        help=(
            "Symbols to include in the report. Default: the curated 6-ETF "
            "basket from spec D6."
        ),
    )
    p.add_argument(
        "--strategies",
        nargs="+",
        default=DEFAULT_STRATEGIES,
        metavar="NAME",
        help=(
            "Strategy names to compare. Default: %(default)s."
        ),
    )
    p.add_argument(
        "--format",
        choices=("md", "csv"),
        default="md",
        help=(
            "Output format — 'md' for the markdown table (default), "
            "'csv' for a header-row CSV."
        ),
    )
    p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help=(
            "Write the rendered report to PATH in addition to stdout. "
            "When omitted, only stdout is written."
        ),
    )
    return p


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    return _build_arg_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Rehydration: JSON → Equity
# ---------------------------------------------------------------------------


def _load_equity(path: Path) -> Any:
    """Read a JSON file and rebuild an :class:`framework.backtest.Equity`.

    The JSON shape mirrors ``tools/run_batch_backtest._serialize_equity``:
    ``dates`` and ``fill.date`` are ISO strings; ``Fill`` entries are
    dicts with the dataclass' field names; everything else is already
    in the right Python type.

    We rebuild the dataclass instead of computing metrics directly from
    ``portfolio_value`` / ``dates`` because (a) the dataclass IS the
    public contract — keeping the tool's tight coupling to it means the
    tool will survive any future re-shaping of ``compute(...)`` — and
    (b) the ``Fill`` list is needed for ``win_rate`` and
    ``profit_loss_ratio`` even if the comparison table does not yet
    surface them.
    """
    from framework.backtest.engine import Equity, Fill

    payload = json.loads(path.read_text(encoding="utf-8"))

    # Dates and fill dates → ``datetime.date``. ``Fill.date`` is a
    # required ISO-8601 string; we accept the legacy ``pd.Timestamp``
    # form too (some AKShare adapters historically serialize that way)
    # but fall back to ``str(d)`` rather than silently swallowing.
    dates = [_parse_date(d) for d in payload["dates"]]
    raw_fills = payload.get("fills") or []
    fills: list[Fill] = []
    for raw in raw_fills:
        fill_dict = dict(raw)
        fill_dict["date"] = _parse_date(fill_dict["date"])
        fills.append(Fill(**fill_dict))

    return Equity(
        dates=dates,
        portfolio_value=list(payload["portfolio_value"]),
        benchmark_value=list(payload["benchmark_value"]),
        cash=list(payload["cash"]),
        fills=fills,
        strategy_name=payload["strategy_name"],
        start=_parse_date(payload["start"]),
        end=_parse_date(payload["end"]),
        initial_cash=float(payload["initial_cash"]),
        final_value=float(payload["final_value"]),
    )


def _parse_date(value: Any) -> date:
    """Best-effort coercion of an ISO-ish value into ``datetime.date``.

    Accepts strings (``"YYYY-MM-DD"``), ``datetime.date``,
    ``datetime.datetime``, and ``pandas.Timestamp`` (which exposes a
    ``.date()`` method). Anything else raises ``ValueError`` — a
    malformed JSON deserves a loud failure rather than a hidden
    fallback.
    """
    if isinstance(value, date) and not isinstance(value, type(date)):
        return value
    if hasattr(value, "date") and callable(getattr(value, "date")):
        try:
            return value.date()
        except Exception:
            pass
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(
        f"cannot coerce date-like value of type {type(value).__name__!r}: {value!r}"
    )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _format_markdown(rows: list[dict[str, Any]]) -> str:
    """Render ``rows`` as the ticket's markdown table.

    Columns are explicitly ordered (``COLUMNS``); numeric values are
    formatted with a fixed precision so two strategies on the same
    symbol line up under their column header. Strategy names are
    left-padded so the markdown table lines up with the existing
    examples in the ticket.
    """
    header = (
        "| Symbol | Strategy | Total Return | Annualized | Sharpe | Max DD |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    )
    if not rows:
        # No data rows → render just the header. The trailing newline
        # is mandatory for ``write_output``'s "ends with newline" check
        # so the summary line is on its own line.
        return header

    # Pre-compute column widths so the strategy column lines up with
    # the ticket's example (longer names like "adx_bb_regime" are the
    # binding case; we pad shorter names to the same width).
    sym_w = max(len(str(r["Symbol"])) for r in rows)
    strat_w = max(len(str(r["Strategy"])) for r in rows)
    body_lines: list[str] = []
    for r in rows:
        body_lines.append(
            "| "
            + f"{r['Symbol']:<{sym_w}}"
            + " | "
            + f"{r['Strategy']:<{strat_w}}"
            + " | "
            + _fmt_pct(r["Total Return"])
            + " | "
            + _fmt_pct(r["Annualized"])
            + " | "
            + _fmt_float(r["Sharpe"])
            + " | "
            + _fmt_pct(r["Max DD"])
            + " |"
        )
    return header + "\n".join(body_lines) + "\n"


def _format_csv(rows: list[dict[str, Any]]) -> str:
    """Render ``rows`` as a CSV string with the canonical header row.

    The string ends with a newline so the ``write_output`` summary line
    starts on its own line in the captured stdout.
    """
    # ``csv.writer`` is stdlib and well-tested. We use ``io.StringIO``
    # so the test suite can introspect the output without filesystem
    # round-trips.
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([r[col] for col in COLUMNS])
    return buf.getvalue() + "\n"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_pct(value: float) -> str:
    """Format a 0..1 (or negative) ratio as a percentage string.

    Total Return, Annualized, and Max DD are unit-less ratios in
    empyrical / the canonical schema; surface them as percentages so
    the user can read the table at a glance. Rounded to 4 decimal
    places — finer precision would surface the empyrical noise floor
    without adding signal.
    """
    return f"{value * 100:.4f}%"


def _fmt_float(value: float) -> str:
    """Format the Sharpe ratio with 3 decimals — finer would be noise."""
    return f"{value:.3f}"


# ---------------------------------------------------------------------------
# Script entry
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(main())
