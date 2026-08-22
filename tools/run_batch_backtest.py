"""tools/run_batch_backtest.py — multi-universe batch backtest runner.

T07 — implements ticket
``.scratch/adx-bb-regime/issues/07-multi-universe-batch-runner.md``.

Usage
-----
::

    python tools/run_batch_backtest.py
    python tools/run_batch_backtest.py --universes 510300 159915
    python tools/run_batch_backtest.py --strategy-class strategies.etf_rebalance.ETFRebalanceStrategy

What it does
------------
For each symbol in the (default or supplied) universe list:

1. Instantiates the strategy class with default ``__init__`` kwargs.
2. Builds a *per-symbol* trading calendar over ``[start, end]``. The
   preferred source is ``adapter.get_calendar(start, end)``; when that
   method is unavailable (test stubs) we fall back to the unique dates
   of ``adapter.get_bars(symbol, start, end)``.
3. Runs :class:`framework.backtest.Engine` once with that universe +
   calendar and serializes the resulting :class:`Equity` to
   ``<out_dir>/<strategy_name>_<symbol>.json``.

The 6-ETF default basket — ``[510300, 510500, 159915, 512760, 512000,
511010]`` — is the curated basket from ``spec.md`` D6. When invoked
without ``--universes`` we print a notice to stderr so a silent run does
not get mistaken for a misconfiguration.

Exit code is ``0`` iff every backtest succeeded; a per-symbol failure
prints to stderr and the runner proceeds to the next symbol. Existing
files for the same ``(strategy, symbol)`` pair are overwritten.

Dependencies
------------
Production callers use ``framework.data.adapter.AKShareAdapter``. Tests
inject a fake by calling ``main(argv, adapter=fake)`` directly — the
public surface accepts an injected adapter so we never need to monkey-
patch module-level globals.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# Defaults from spec.md D6 (the curated 6-ETF basket). Strings, not ints,
# so the JSON output and CLI argparse agree on type.
DEFAULT_UNIVERSES: list[str] = ["510300", "510500", "159915", "512760", "512000", "511010"]

# Default strategy class — the regime strategy from T02-T06. Importing
# the dotted path lazily (inside ``_import_strategy_class``) keeps
# module load fast and lets tests point at any strategy without
# touching this module.
DEFAULT_STRATEGY_CLASS = "strategies.adx_bb_regime.AdxBbRegimeStrategy"

DEFAULT_START = "2022-01-01"  # matches the user's spec context
DEFAULT_INITIAL_CASH = 100_000
DEFAULT_OUT_DIR = "reports"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    """Summary of one batch run — useful for callers (tests) that want to
    introspect outcomes without re-parsing stderr.

    ``succeeded`` and ``failed`` are the lists of symbols for which
    JSON was written vs. where an exception was caught. ``exit_code``
    is the same int :func:`main` returns.
    """

    succeeded: list[str]
    failed: list[tuple[str, str]]   # (symbol, error message)
    exit_code: int


def main(argv: Optional[Sequence[str]] = None, *, adapter: Any = None) -> int:
    """CLI entry point.

    Parameters
    ----------
    argv
        Argument list. ``None`` reads ``sys.argv[1:]`` so
        ``python tools/run_batch_backtest.py`` works as a standalone
        script.
    adapter
        Optional pre-built data adapter. Production callers leave this
        ``None`` to get a real ``AKShareAdapter``; tests inject a fake
        here so the runner never touches ``akshare``.

    Returns
    -------
    int
        POSIX exit code: ``0`` iff every universe backtest succeeded;
        ``1`` if any per-symbol backtest raised.
    """
    args = _parse_args(argv)

    # Ensure the repo root is on ``sys.path`` so
    # ``importlib.import_module("strategies.adx_bb_regime")`` resolves
    # when the user runs the script from anywhere. ``__file__`` here
    # is ``<repo>/tools/run_batch_backtest.py``; the parent is ``tools/``,
    # the parent of that is the repo root.
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    strategy_cls = _import_strategy_class(args.strategy_class)

    if args.universes is None:
        print(
            "[run_batch_backtest] no --universes supplied; using default "
            f"6-ETF basket: {DEFAULT_UNIVERSES}",
            file=sys.stderr,
        )
        universes = list(DEFAULT_UNIVERSES)
    else:
        universes = list(args.universes)

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    adapter = _resolve_adapter(adapter)

    result = _run_batch(
        strategy_cls=strategy_cls,
        adapter=adapter,
        universes=universes,
        start=start_date,
        end=end_date,
        initial_cash=args.initial_cash,
        out_dir=out_dir,
    )
    return result.exit_code


# Backwards-compatible alias for tests that call ``run_batch`` directly.
run_batch = main


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python tools/run_batch_backtest.py",
        description=(
            "Run a strategy against a list of universes (one backtest per "
            "symbol) and write one equity JSON per universe to <out-dir>/. "
            "Default basket is the curated 6-ETF set from the ADX+BB regime "
            "spec."
        ),
    )
    p.add_argument(
        "--strategy-class",
        default=DEFAULT_STRATEGY_CLASS,
        help=(
            "Dotted path to the strategy class, e.g. "
            "'strategies.adx_bb_regime.AdxBbRegimeStrategy'. "
            "Default: %(default)s."
        ),
    )
    p.add_argument(
        "--universes",
        nargs="+",
        default=None,
        metavar="SYM",
        help=(
            "One or more symbols to backtest. If omitted, the default "
            "6-ETF basket is used and a notice is printed to stderr."
        ),
    )
    p.add_argument(
        "--start",
        default=DEFAULT_START,
        help="Start date (YYYY-MM-DD). Default: %(default)s.",
    )
    p.add_argument(
        "--end",
        default=date.today().isoformat(),
        help="End date (YYYY-MM-DD). Default: today.",
    )
    p.add_argument(
        "--initial-cash",
        type=float,
        default=DEFAULT_INITIAL_CASH,
        help="Initial cash per backtest. Default: %(default)s.",
    )
    p.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help="Directory to write per-symbol JSON files. Default: %(default)s.",
    )
    return p


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    return _build_arg_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Adapter / strategy resolution
# ---------------------------------------------------------------------------


def _resolve_adapter(adapter: Optional[Any]) -> Any:
    """Lazy-import AKShareAdapter when no adapter is injected.

    Mirrors the same lazy-import pattern as
    ``framework.runner.scheduled_run._resolve_adapter``: keep the bare
    ``import akshare`` off the module-load path so tests never pay for
    it. Production callers leave ``adapter=None`` to get the real one.
    """
    if adapter is not None:
        return adapter
    from framework.data.adapter import AKShareAdapter

    return AKShareAdapter()


def _import_strategy_class(dotted: str) -> type:
    """Resolve a ``dotted.path.Class`` string to the class object.

    Imports happen lazily inside ``main`` so the runner file loads
    cleanly even when ``strategies`` is not on ``sys.path`` at import
    time. The ``getattr`` step then fetches the class attribute;
    ``inspect.isclass`` ensures the result is a class — duck-typed
    strategies with a class-level ``name`` attr are valid, but bare
    functions or modules are not.
    """
    import inspect

    if "." not in dotted:
        raise ValueError(
            f"--strategy-class must be a dotted path (got {dotted!r}); "
            "expected e.g. 'strategies.adx_bb_regime.AdxBbRegimeStrategy'"
        )
    module_name, _, attr = dotted.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise ImportError(
            f"could not import module {module_name!r} for "
            f"--strategy-class={dotted!r}: {type(exc).__name__}: {exc}"
        ) from exc
    cls = getattr(module, attr, None)
    if cls is None:
        raise ImportError(
            f"module {module_name!r} has no attribute {attr!r} "
            f"(check --strategy-class={dotted!r})"
        )
    if not inspect.isclass(cls):
        raise TypeError(
            f"--strategy-class={dotted!r} resolved to {type(cls).__name__}, "
            "expected a class"
        )
    return cls


# ---------------------------------------------------------------------------
# Calendar + backtest execution
# ---------------------------------------------------------------------------


def _build_calendar(adapter: Any, symbol: str, start: date, end: date) -> list[date]:
    """Return a sorted, deduplicated list of trading days in ``[start, end]``.

    Two paths, in priority order:

    1. ``adapter.get_calendar(start, end)`` — the production AKShare
       adapter exposes this and returns the canonical A-share calendar
       over the window. Use it when available; it is the cheapest and
       matches the project's existing usage in
       ``framework.runner.scheduled_run``.
    2. ``adapter.get_bars(symbol, start, end)`` — fallback for stubs
       that ship only ``get_bars``. We use the unique ``date`` column
       of the returned DataFrame.

    Both paths are guarded against adapter exceptions — a transient
    upstream failure on the calendar lookup must not abort the entire
    batch; the caller (``_run_one``) catches and reports per-symbol
    failures uniformly.
    """
    cal_method = getattr(adapter, "get_calendar", None)
    if callable(cal_method):
        try:
            cal = cal_method(start, end)
            if cal:
                # Defensive: ensure dates are ``datetime.date`` (some
                # AKShare wrappers return ``pd.Timestamp`` instances).
                out: list[date] = []
                for d in cal:
                    out.append(d.date() if hasattr(d, "date") else d)
                return sorted(set(out))
        except Exception:
            # Fall through to bars-based derivation below.
            pass

    result = adapter.get_bars(
        symbol, start=start, end=end, adj="qfq", frequency="daily"
    )
    df = result.df
    if df is None or df.empty:
        return []
    dates_raw = df["date"].tolist()
    out_dates: set[date] = set()
    for d in dates_raw:
        if hasattr(d, "date"):
            d = d.date()
        if start <= d <= end:
            out_dates.add(d)
    return sorted(out_dates)


def _run_batch(
    *,
    strategy_cls: type,
    adapter: Any,
    universes: Sequence[str],
    start: date,
    end: date,
    initial_cash: float,
    out_dir: Path,
) -> BatchResult:
    """Run the strategy once per universe; write one JSON per success.

    Imports :class:`framework.backtest.Engine` inside the function so
    the module-load path stays free of heavyweight framework imports
    (akshare / engine wiring only on demand).
    """
    from framework.backtest.engine import Engine

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []

    for symbol in universes:
        try:
            equity = _run_one(
                strategy_cls=strategy_cls,
                adapter=adapter,
                symbol=symbol,
                start=start,
                end=end,
                initial_cash=initial_cash,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(
                f"[run_batch_backtest] {symbol}: backtest failed — {msg}",
                file=sys.stderr,
            )
            failed.append((symbol, msg))
            continue

        out_path = out_dir / f"{equity.strategy_name}_{symbol}.json"
        out_path.write_text(_serialize_equity(equity), encoding="utf-8")
        succeeded.append(symbol)
        print(
            f"[run_batch_backtest] {symbol}: wrote {out_path}",
            file=sys.stderr,
        )

    exit_code = 0 if not failed else 1
    return BatchResult(succeeded=succeeded, failed=failed, exit_code=exit_code)


def _run_one(
    *,
    strategy_cls: type,
    adapter: Any,
    symbol: str,
    start: date,
    end: date,
    initial_cash: float,
) -> Any:
    """Run a single-symbol backtest end-to-end and return the Equity.

    Failure modes propagated to the caller:

    * ``adapter.get_bars`` raises on the calendar build → propagates.
    * ``Engine.run`` raises (signal validation, etc.) → propagates.

    The runner relies on the engine's own per-bar guards (skip when
    today's close/open is missing) to handle data gaps inside the
    window — we only catch *catastrophic* failures here.
    """
    from framework.backtest.engine import Engine

    # Per-symbol calendar — required by the Engine (validated non-empty
    # inside its ``__init__``). Empty calendar for a symbol is treated
    # as a per-symbol failure so the other universes still complete.
    calendar = _build_calendar(adapter, symbol, start, end)
    if not calendar:
        raise ValueError(
            f"no trading days in [{start.isoformat()}, {end.isoformat()}] "
            f"for symbol {symbol!r} — check adapter and date range"
        )

    strategy_instance = strategy_cls()

    engine = Engine(
        strategy=strategy_instance,
        universe=[symbol],
        adapter=adapter,
        calendar=calendar,
    )
    return engine.run(initial_cash=initial_cash)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _serialize_equity(equity: Any) -> str:
    """Serialize an :class:`Equity` to a pretty-printed JSON string.

    The shape mirrors the engine's dataclass so consumers can
    reconstruct an Equity by name:

        {
          "strategy_name": str,
          "start": "YYYY-MM-DD",
          "end":   "YYYY-MM-DD",
          "initial_cash": float,
          "final_value":  float,
          "dates":          ["YYYY-MM-DD", ...],
          "portfolio_value":[float, ...],
          "benchmark_value":[float, ...],
          "cash":          [float, ...],
          "fills": [
            {"date": "YYYY-MM-DD", "symbol": str, "side": str,
             "qty": int, "price": float, "fee": float},
            ...
          ]
        }

    ``date`` objects are converted via ``isoformat()``; ``Fill`` is
    expanded to a dict with the same field names.
    """
    payload = {
        "strategy_name": equity.strategy_name,
        "start": equity.start.isoformat() if hasattr(equity.start, "isoformat") else equity.start,
        "end": equity.end.isoformat() if hasattr(equity.end, "isoformat") else equity.end,
        "initial_cash": equity.initial_cash,
        "final_value": equity.final_value,
        "dates": [_iso(d) for d in equity.dates],
        "portfolio_value": list(equity.portfolio_value),
        "benchmark_value": list(equity.benchmark_value),
        "cash": list(equity.cash),
        "fills": [_fill_to_dict(f) for f in equity.fills],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _iso(d: Any) -> str:
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def _fill_to_dict(fill: Any) -> dict:
    return {
        "date": _iso(fill.date),
        "symbol": fill.symbol,
        "side": fill.side,
        "qty": int(fill.qty),
        "price": float(fill.price),
        "fee": float(fill.fee),
    }


# ---------------------------------------------------------------------------
# Script entry
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(main())
