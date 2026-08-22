"""tests/test_charts_equity.py

TDD cover for the dashboard / backtest equity-curve builder. The chart is
a thin Pyecharts wrapper; what we're really guarding is the wiring:

      * length validation (mismatched dates / portfolio / benchmark)
      * benchmark-optional path
      * the chart carries the right series names + colors so the legend
        and tooltip text stay correct when the dashboard surfaces it.

We don't snapshot the rendered HTML — pyecharts' output drifts on minor
versions. We DO assert on the option-tree shape, which is the
load-bearing contract.
"""

from __future__ import annotations

import pytest
from pyecharts.charts import Line

from framework.charts.equity import (
    BENCHMARK_COLOR,
    PORTFOLIO_COLOR,
    build_equity_curve,
    build_equity_curve_from_df,
)


def _dates(n: int = 5) -> list[str]:
    return [f"2024-01-{i + 1:02d}" for i in range(n)]


def _series(start: float = 100.0, n: int = 5) -> list[float]:
    return [start + i for i in range(n)]


# ---------------------------------------------------------------------------
# length validation
# ---------------------------------------------------------------------------


def test_raises_on_length_mismatch():
    with pytest.raises(ValueError):
        build_equity_curve(dates=["a", "b"], portfolio=[1.0, 2.0, 3.0])


def test_raises_on_benchmark_length_mismatch():
    with pytest.raises(ValueError):
        build_equity_curve(
            dates=_dates(),
            portfolio=_series(),
            benchmark=[100.0, 101.0],  # 2 vs 5
        )


# ---------------------------------------------------------------------------
# shape — with and without benchmark
# ---------------------------------------------------------------------------


def _series_dict(line: Line) -> list[dict]:
    """Pyecharts stashes each ``.add_yaxis()`` call under ``options['series']``.
    That's the only stable surface to introspect series shape post-build."""
    return list(line.options.get("series") or [])


def test_single_series_when_benchmark_is_none():
    line = build_equity_curve(_dates(), _series())
    assert isinstance(line, Line)
    series = _series_dict(line)
    assert len(series) == 1
    assert series[0]["name"] == "策略净值"
    legend = line.options.get("legend")
    assert legend is not None
    legend_opts = legend[0] if isinstance(legend, list) else legend
    assert "策略净值" in (legend_opts.get("data") or [])


def test_two_series_when_benchmark_provided():
    line = build_equity_curve(
        _dates(), _series(), _series(start=200.0),
        benchmark_label="基准 000300",
    )
    series = _series_dict(line)
    assert len(series) == 2
    names = [s["name"] for s in series]
    assert "策略净值" in names
    assert "基准 000300" in names


def _line_color(series_dict: dict) -> str | None:
    """Pyecharts surfaces ``LineStyleOpts`` as a ``LineStyleOpts`` instance;
    its ``.opts`` dict carries the rendered color."""
    ls = series_dict.get("lineStyle")
    if ls is None:
        return None
    opts_dict = getattr(ls, "opts", None) or {}
    return opts_dict.get("color")


def test_benchmark_color_matches_constant():
    line = build_equity_curve(_dates(), _series(), _series(start=200.0))
    bench = next(s for s in _series_dict(line) if s["name"].startswith("基准"))
    assert _line_color(bench) == BENCHMARK_COLOR


def test_portfolio_color_matches_constant():
    line = build_equity_curve(_dates(), _series(), _series(start=200.0))
    port = next(s for s in _series_dict(line) if s["name"] == "策略净值")
    assert _line_color(port) == PORTFOLIO_COLOR


# ---------------------------------------------------------------------------
# DataFrame wrapper
# ---------------------------------------------------------------------------


def test_build_from_df_uses_standard_columns():
    import pandas as pd

    df = pd.DataFrame({
        "date": _dates(),
        "portfolio_value": _series(),
        "benchmark_value": _series(start=200.0),
    })
    line = build_equity_curve_from_df(df, benchmark_label="基准")
    assert len(_series_dict(line)) == 2


def test_build_from_df_skips_missing_benchmark():
    import pandas as pd

    df = pd.DataFrame({
        "date": _dates(),
        "portfolio_value": _series(),
        # no benchmark_value column
    })
    line = build_equity_curve_from_df(df)
    assert len(_series_dict(line)) == 1


def test_build_from_df_raises_on_missing_portfolio_column():
    import pandas as pd

    df = pd.DataFrame({"date": _dates(), "junk": _series()})
    with pytest.raises(KeyError):
        build_equity_curve_from_df(df)


def test_build_from_df_raises_on_missing_date_column():
    import pandas as pd

    df = pd.DataFrame({"portfolio_value": _series()})
    with pytest.raises(KeyError):
        build_equity_curve_from_df(df)