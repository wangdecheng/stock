"""framework/charts/equity.py

Equity / PnL line-chart helper (T11). Kept in its own module so the
concurrent T12 work in ``framework/charts/__init__.py`` (K-line / volume)
doesn't have to know about it — and so the dashboard and backtest pages
can share one chart builder instead of inlining the same Pyecharts
``Line`` + ``AreaStyleOpts`` + ``DataZoomOpts`` boilerplate.

Visual contract:

* x-axis: ISO date strings (category) — matches the K-line pages.
* two named series, ``portfolio`` + ``benchmark``, both ``is_smooth``.
* portfolio gets an ``AreaStyle`` at 18% opacity so it reads as the
  primary line; benchmark is a flat stroke only.
* Y-axis is scale (not stacked) so portfolio-vs-benchmark comparison
  stays honest when the magnitudes differ.
* Inline datazoom + tooltip-on-axis match the K-line viewer's feel.

Callers feed it a 2-col ``DataFrame`` (``date`` + value columns) or two
parallel lists. The page is responsible for the real/virtual/both
toggle — this module is the render-only seam.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Line


# Color pins — kept module-local so this file can stand on its own and
# a future tweak to the dashboard palette doesn't drift away silently.
PORTFOLIO_COLOR = "#ec0000"   # A-share red (UP_COLOR convention)
BENCHMARK_COLOR = "#888888"


def build_equity_curve(
    dates: Sequence[str],
    portfolio: Sequence[float],
    benchmark: Sequence[float] | None = None,
    *,
    title: str = "PnL 曲线",
    benchmark_label: str = "基准",
    height: str = "380px",
) -> Line:
    """Build a Pyecharts Line chart with optional benchmark overlay.

    Parameters
    ----------
    dates
        ISO date strings (or any category-axis labels) aligned with the
        value series. Must be the same length as ``portfolio``.
    portfolio
        Portfolio NAV time series.
    benchmark
        Optional benchmark NAV series. ``None`` → single-series chart.
    benchmark_label
        Legend text for the benchmark series (e.g. "基准 000300").
    """
    if len(dates) != len(portfolio):
        raise ValueError(
            f"dates ({len(dates)}) and portfolio ({len(portfolio)}) "
            "must be the same length"
        )
    if benchmark is not None and len(benchmark) != len(dates):
        raise ValueError(
            f"benchmark ({len(benchmark)}) and dates ({len(dates)}) "
            "must be the same length"
        )

    line = Line(init_opts=opts.InitOpts(width="100%", height=height))
    line.add_xaxis(list(dates))
    line.add_yaxis(
        series_name="策略净值",
        y_axis=[float(v) for v in portfolio],
        is_smooth=True,
        label_opts=opts.LabelOpts(is_show=False),
        areastyle_opts=opts.AreaStyleOpts(opacity=0.18),
        linestyle_opts=opts.LineStyleOpts(color=PORTFOLIO_COLOR, width=2),
        symbol="none",
    )
    if benchmark is not None:
        line.add_yaxis(
            series_name=benchmark_label,
            y_axis=[float(v) for v in benchmark],
            is_smooth=True,
            label_opts=opts.LabelOpts(is_show=False),
            linestyle_opts=opts.LineStyleOpts(color=BENCHMARK_COLOR, width=1),
            symbol="none",
        )

    line.set_global_opts(
        title_opts=opts.TitleOpts(title=title, pos_left="center"),
        xaxis_opts=opts.AxisOpts(type_="category"),
        yaxis_opts=opts.AxisOpts(is_scale=True),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
        legend_opts=opts.LegendOpts(pos_top="3%"),
        datazoom_opts=[opts.DataZoomOpts(type_="inside")],
    )
    return line


def build_equity_curve_from_df(
    df: pd.DataFrame,
    *,
    date_col: str = "date",
    portfolio_col: str = "portfolio_value",
    benchmark_col: str | None = "benchmark_value",
    title: str = "PnL 曲线",
    benchmark_label: str = "基准",
    height: str = "380px",
) -> Line:
    """Convenience wrapper: accept a DataFrame with the standard equity
    columns and build the chart. ``benchmark_col=None`` skips benchmark."""
    if date_col not in df.columns:
        raise KeyError(f"missing column {date_col!r} in equity frame")
    if portfolio_col not in df.columns:
        raise KeyError(f"missing column {portfolio_col!r} in equity frame")
    dates = df[date_col].astype(str).tolist()
    portfolio = df[portfolio_col].astype(float).tolist()
    bench = df[benchmark_col].astype(float).tolist() if benchmark_col and benchmark_col in df.columns else None
    return build_equity_curve(
        dates,
        portfolio,
        bench,
        title=title,
        benchmark_label=benchmark_label,
        height=height,
    )


__all__ = ["build_equity_curve", "build_equity_curve_from_df"]