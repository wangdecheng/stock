"""framework/charts — T10 chart wrappers (Pyecharts v2.0.9).

Centralizes the A-share K-line + volume Grid pattern so the Streamlit
pages don't reinvent the boilerplate on every render. Visual style and
interaction are kept identical to the inline version that previously
lived in ``pages/1_股票详情.py`` — this module is a refactor seam, not a
redesign.

Public surface:
  * ``A_SHARE_UP_COLOR`` / ``A_SHARE_DOWN_COLOR`` / ``A_SHARE_HOLD_COLOR``
    — convention pins (also exported for tests).
  * ``TREND_STATE_COLORS`` / ``HALVED_STAGE_COLORS`` / ``INDICATOR_LINE_COLORS``
    — palette pins for the stock-detail indicator overlay (T09 / ADR-0006).
  * ``mark_point_data`` — turn (buy, sell, hold) signal lists into
    Pyecharts ``MarkPointItem`` objects with the right colors, glyphs,
    and Chinese labels (买 / 卖 / 持).
  * ``build_kline`` — A-share candlestick chart with optional MarkPoints.
  * ``build_volume`` — volume sub-chart colored by per-bar direction.
    (Deprecated for stock-detail; retained for backtest reuse — see
    ADR-0008.)
  * ``build_kline_volume_grid`` — the K-line + volume Grid multi-chart.
    (Kept for backtest page; stock detail now uses ``build_indicator_grid``.)
  * ``build_indicator_grid`` — the stock-detail 3-panel Grid: K-line +
    MA + BB / ADX / RSI, with TrendState + HalvedStage color bands
    (T09 / ADR-0006).

Pages import from here and hand the result straight to
``streamlit_echarts.st_pyecharts``. We do NOT call ``chart.render()``
here — the Streamlit component renders into the page directly.
"""

from __future__ import annotations

from typing import Iterable, TypedDict

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline, Line

# A-share convention: red up, green down. Hex codes pinned so a future
# tweak can't silently drift away from the T10 decision without a test
# failing (see ``tests/test_charts.py::test_a_share_color_constants_*``).
A_SHARE_UP_COLOR = "#ec0000"
A_SHARE_DOWN_COLOR = "#14b143"
A_SHARE_HOLD_COLOR = "#888888"

# ---------------------------------------------------------------------------
# Indicator-overlay palette (T09 / ADR-0006)
# ---------------------------------------------------------------------------
#
# Pinned at module scope so a future palette tweak can't silently drift
# away from the agreed convention without a test failing
# (``tests/test_charts.py::test_*_colors_*``). The colors are deliberately
# muted so they sit underneath candlesticks / line overlays without
# fighting them for attention. Hex codes use 6-digit form (no alpha) —
# Pyecharts' ``areastyle`` accepts ``opacity`` separately, which keeps
# these constants reusable across area / line / marker contexts.

# TrendState background bands on the main K-line panel.
# Pastel so the candlesticks dominate; the band is read at a glance.
TREND_STATE_COLORS: dict[str, str] = {
    "TREND_UP":    "#fde0e0",  # pale red — matches A-share "up" sentiment
    "TREND_DOWN":  "#dff5e0",  # pale green — matches A-share "down" sentiment
    "RANGE_BULL":  "#fff5cc",  # pale yellow — neutral-positive
    "RANGE_BEAR":  "#eeeeee",  # pale grey — neutral-negative
}

# HalvedStage bands under the ADX sub-panel. Three distinct pastels;
# ``cleared`` is the most muted since it means "fully out".
HALVED_STAGE_COLORS: dict[str, str] = {
    "full":    "#cce5ff",  # pale blue — full trend position
    "halved":  "#ffe4b3",  # pale orange — half position (post MA20 break)
    "cleared": "#d9d9d9",  # pale grey — fully out (post MA10 break)
}

# Per-indicator line colors. Picked to be distinct from each other AND
# from the candlestick (which uses A_SHARE_UP_COLOR / A_SHARE_DOWN_COLOR).
# BB upper and lower are visually distinguished by line style (upper
# solid, lower dashed) — same orange hue is intentional so the channel
# reads as a single visual band. BB mid is olive to stand out as the
# channel centerline.
INDICATOR_LINE_COLORS: dict[str, str] = {
    "MA20":     "#1f77b4",  # blue
    "MA60":     "#9467bd",  # purple
    "BB_upper": "#ff7f0e",  # orange (solid)
    "BB_mid":   "#bcbd22",  # olive (channel centerline)
    "BB_lower": "#d62728",  # brick red (dashed; distinguishes from upper)
    "ADX":      "#2ca02c",  # green
    "plus_di":  "#17becf",  # teal
    "minus_di": "#e377c2",  # pink
    "RSI":      "#8c564b",  # brown
}


class MarkPoint(TypedDict, total=False):
    """Schema for one buy/sell/hold overlay point.

    ``coord`` is the ``[date_str, price]`` pair — Pyecharts requires the
    x value to be a category-axis string already in the chart's x-axis.
    ``value`` is the label shown next to the marker (defaults to the
    Chinese verb when omitted, see ``mark_point_data``).
    """

    coord: list
    value: str


# Bucket configuration for ``mark_point_data``. Centralizing the per-bucket
# (label, color, symbol, rotate) tuple here kills the three near-identical
# loops the first version of this module had and makes a future fourth
# signal type (e.g. "reduce") a one-line addition.
_BUCKET_SPECS: list[tuple[str, str, str, int, str]] = [
    # (bucket_key, item_name, color, symbol_rotate, default_chinese_label)
    ("buy", "buy", A_SHARE_UP_COLOR, 0, "买"),
    ("sell", "sell", A_SHARE_DOWN_COLOR, 180, "卖"),
    ("hold", "hold", A_SHARE_HOLD_COLOR, 0, "持"),
]


__all__ = [
    "A_SHARE_UP_COLOR",
    "A_SHARE_DOWN_COLOR",
    "A_SHARE_HOLD_COLOR",
    "TREND_STATE_COLORS",
    "HALVED_STAGE_COLORS",
    "INDICATOR_LINE_COLORS",
    "MarkPoint",
    "mark_point_data",
    "build_kline",
    "build_volume",
    "build_kline_volume_grid",
    "build_indicator_grid",
]


# ---------------------------------------------------------------------------
# MarkPoints
# ---------------------------------------------------------------------------


def mark_point_data(
    buy: Iterable[MarkPoint] | None = None,
    sell: Iterable[MarkPoint] | None = None,
    hold: Iterable[MarkPoint] | None = None,
) -> list[opts.MarkPointItem]:
    """Combine buy/sell/hold signal lists into one flat list of
    ``MarkPointItem`` objects with A-share colors.

    Buy uses an upward triangle in red, sell uses a downward-rotated
    triangle in green (rotated 180°), hold uses a small grey pin — so
    the chart still reads at a glance even when the active strategy
    emits a mix.

    The default label for each marker is the Chinese verb (买 / 卖 / 持).
    Callers can override by setting ``MarkPoint.value`` explicitly.
    """
    buckets: dict[str, Iterable[MarkPoint]] = {
        "buy": buy or [],
        "sell": sell or [],
        "hold": hold or [],
    }
    items: list[opts.MarkPointItem] = []
    for bucket_key, item_name, color, rotate, default_label in _BUCKET_SPECS:
        for pt in buckets[bucket_key]:
            items.append(
                opts.MarkPointItem(
                    name=item_name,
                    coord=list(pt["coord"]),
                    value=pt.get("value", default_label),
                    symbol="triangle",
                    symbol_rotate=rotate,
                    itemstyle_opts=opts.ItemStyleOpts(color=color),
                )
            )
    return items


# ---------------------------------------------------------------------------
# K-line
# ---------------------------------------------------------------------------


def build_kline(
    df: pd.DataFrame,
    *,
    symbol: str,
    adj_label: str,
    buy_pts: Iterable[MarkPoint] | None = None,
    sell_pts: Iterable[MarkPoint] | None = None,
    hold_pts: Iterable[MarkPoint] | None = None,
    height: str = "420px",
) -> Kline:
    """A-share candlestick chart with optional MarkPoint overlays.

    ``df`` must have columns ``date, open, close, high, low, volume``.
    Dates are rendered as ISO strings so they line up with the volume
    sub-chart's category axis.
    """
    dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]]
    # Pyecharts Kline data order is [open, close, low, high] — NOT the
    # usual OHLC. Pinning this in one place keeps callers from getting it
    # wrong (gotcha #1 in T10 research).
    ohlc = df[["open", "close", "low", "high"]].values.tolist()

    items = mark_point_data(buy=buy_pts, sell=sell_pts, hold=hold_pts)

    return (
        Kline(init_opts=opts.InitOpts(width="100%", height=height))
        .add_xaxis(dates)
        .add_yaxis(
            series_name=f"{symbol} · {adj_label} · 日 K",
            y_axis=ohlc,
            itemstyle_opts=opts.ItemStyleOpts(
                color=A_SHARE_UP_COLOR,
                color0=A_SHARE_DOWN_COLOR,
                border_color="#8A0000",
                border_color0="#065726",
            ),
            markpoint_opts=opts.MarkPointOpts(
                data=items,
                symbol_size=42,
                label_opts=opts.LabelOpts(position="top", color="#fff", font_size=10),
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title=f"{symbol}", pos_left="center"),
            xaxis_opts=opts.AxisOpts(type_="category", is_scale=True, grid_index=0),
            yaxis_opts=opts.AxisOpts(is_scale=True, grid_index=0),
            legend_opts=opts.LegendOpts(pos_top="3%"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1])],
            axispointer_opts=opts.AxisPointerOpts(
                is_show=True,
                link=[{"xAxisIndex": "all"}],
                label=opts.LabelOpts(background_color="#777"),
            ),
        )
    )


# ---------------------------------------------------------------------------
# Volume sub-chart
# ---------------------------------------------------------------------------


def build_volume(df: pd.DataFrame, *, height: str = "180px") -> Bar:
    """Deprecated: use ``build_indicator_grid`` for the stock-detail page
    (T09 / ADR-0008).

    Currently unused by ``pages/1_股票详情.py`` — that page now uses
    ``build_indicator_grid`` (K-line + MA + BB / ADX / RSI). Kept in the
    public surface for the backtest page, which may want a volume
    bottom panel under the equity curve. If the backtest page does not
    pick this up within 12 months, retire the function entirely.

    Original behavior — volume sub-chart colored by per-bar direction
    (A-share convention):

        Implemented as two stacked Bar series — red for up-days, green
        for down-days — because Pyecharts does not honor a per-bar
        color array on a single series the way Plotly does. The visual
        result matches the legacy inlined version.
    """
    dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]]
    volumes = df["volume"].tolist()
    opens = df["open"].tolist()
    closes = df["close"].tolist()
    red_vols = [v if c >= o else 0 for v, o, c in zip(volumes, opens, closes)]
    green_vols = [v if c < o else 0 for v, o, c in zip(volumes, opens, closes)]

    return (
        Bar(init_opts=opts.InitOpts(width="100%", height=height))
        .add_xaxis(dates)
        .add_yaxis(
            "↑",
            red_vols,
            stack="总量",
            itemstyle_opts=opts.ItemStyleOpts(color=A_SHARE_UP_COLOR),
        )
        .add_yaxis(
            "↓",
            green_vols,
            stack="总量",
            itemstyle_opts=opts.ItemStyleOpts(color=A_SHARE_DOWN_COLOR),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=1),
            yaxis_opts=opts.AxisOpts(grid_index=1),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            legend_opts=opts.LegendOpts(pos_top="3%"),
        )
    )


# ---------------------------------------------------------------------------
# K-line + volume Grid (the standard stock-detail view)
# ---------------------------------------------------------------------------


def build_kline_volume_grid(
    df: pd.DataFrame,
    *,
    symbol: str,
    adj_label: str,
    buy_pts: Iterable[MarkPoint] | None = None,
    sell_pts: Iterable[MarkPoint] | None = None,
    hold_pts: Iterable[MarkPoint] | None = None,
    kline_height: str = "420px",
    volume_height: str = "180px",
    total_height: str = "600px",
) -> Grid:
    """Stack K-line (top) + volume (bottom) into a single Grid with
    shared x-axis and crosshair linkage.

    This is the canonical "stock detail" view from ``pages/1_股票详情.py``.
    ``total_height`` is the Grid container's overall height — it should
    be at least ``kline_height + volume_height`` plus room for the top
    axis titles and the datazoom slider.
    """
    kline = build_kline(
        df,
        symbol=symbol,
        adj_label=adj_label,
        buy_pts=buy_pts,
        sell_pts=sell_pts,
        hold_pts=hold_pts,
        height=kline_height,
    )
    volume = build_volume(df, height=volume_height)

    return (
        Grid(init_opts=opts.InitOpts(width="100%", height=total_height))
        .add(
            kline,
            grid_opts=opts.GridOpts(
                pos_left="8%", pos_right="4%", pos_top="8%", height="60%",
            ),
        )
        .add(
            volume,
            grid_opts=opts.GridOpts(
                pos_left="8%", pos_right="4%", pos_top="72%", height="20%",
            ),
        )
    )


# ---------------------------------------------------------------------------
# build_indicator_grid (T09 — stock-detail 3-panel Grid, ADR-0006)
# ---------------------------------------------------------------------------


def build_indicator_grid(
    df: pd.DataFrame,
    *,
    symbol: str,
    adj_label: str,
    strategy_instance: object | None = None,
    buy_pts: Iterable[MarkPoint] | None = None,
    sell_pts: Iterable[MarkPoint] | None = None,
    hold_pts: Iterable[MarkPoint] | None = None,
    kline_height: str = "420px",
    adx_height: str = "180px",
    rsi_height: str = "160px",
    total_height: str = "820px",
) -> Grid:
    """3-panel Grid for the stock-detail page (T09 / ADR-0006).

    Single entry point per ADR-0007 Consequences #3: the page passes
    the active strategy instance and the bar DataFrame; this function
    owns all indicator computation and state-replay. Pages do **not**
    import indicators or strategies directly.

    Panel layout (top to bottom):

      0  K-line + MA20 + MA60 + BB (upper / mid / lower) +
         strategy-supplied buy / sell / hold MarkPoints +
         Bollinger-band-touch MarkPoints (when ``strategy_instance``
         is ADX+BB) + TrendState background band on the price axis.
      1  ADX sub-panel: +DI / -DI / ADX three lines + HalvedStage
         background band + reference lines at ADX=25 (trend
         threshold) and ADX=20 (range threshold).
      2  RSI sub-panel: single RSI line + reference line at RSI=30
         (Signal Modulator floor).

    Indicator parameter source
    --------------------------
    When ``strategy_instance`` is an ``AdxBbRegimeStrategy`` instance
    (or any duck-type with ``adx_len``, ``bb_len``, ``bb_std``,
    ``rsi_len`` attributes), the chart pulls those lengths from the
    instance so the chart never drifts from what the strategy emits.

    When ``strategy_instance`` is ``None`` or doesn't expose the
    parameters, the chart falls back to the ADX+BB defaults
    (14 / 20 / 2.0 / 14) and skips the TrendState / HalvedStage
    bands (they're ADX+BB-specific).

    The chart module imports ``framework.indicators`` and
    ``strategies.adx_bb_regime`` lazily inside this function — it does
    NOT pull them at module scope, so tests / pages that don't use the
    indicator grid don't pay the import cost.
    """
    # Lazy imports — keep the module cheap to load.
    from framework.indicators import adx as adx_ind, bollinger as bb_ind
    from framework.indicators import ma as ma_ind, rsi as rsi_ind

    # Parameter extraction (with duck-typing fallback).
    adx_len = getattr(strategy_instance, "adx_len", 14)
    bb_len = getattr(strategy_instance, "bb_len", 20)
    bb_std = getattr(strategy_instance, "bb_std", 2.0)
    rsi_len = getattr(strategy_instance, "rsi_len", 14)
    hysteresis_days = getattr(strategy_instance, "hysteresis_days", 2)

    # Compute indicators.
    adx_df = adx_ind(df, length=adx_len)
    ma20 = ma_ind(df, length=20)
    ma60 = ma_ind(df, length=60)
    ma10 = ma_ind(df, length=10)
    bb_df = bb_ind(df, length=bb_len, std=bb_std)
    rsi_series = rsi_ind(df, length=rsi_len)

    # State replay — only meaningful for ADX+BB. Skip when the strategy
    # isn't ADX+BB or doesn't expose the replay functions.
    state_series: pd.Series = pd.Series(["RANGE_BEAR"] * len(df), index=df.index)
    stage_series: pd.Series = pd.Series(["full"] * len(df), index=df.index)
    is_adx_bb = (
        strategy_instance is not None
        and getattr(strategy_instance, "name", None) == "adx_bb_regime"
    )
    if is_adx_bb:
        from strategies.adx_bb_regime import (
            _new_state_series,
            _run_halved_stage_series,
        )
        state_series = _new_state_series(
            df,
            adx_df=adx_df,
            ma20=ma20,
            ma60=ma60,
            bb_mid=bb_df["mid"],
            hysteresis_days=hysteresis_days,
        )
        stage_series = _run_halved_stage_series(
            df["close"].reset_index(drop=True),
            ma10.reset_index(drop=True),
            ma20.reset_index(drop=True),
            initial_stage="full",
        )
        # Re-anchor onto ``df.index`` so downstream Bar/Line series
        # align on the original index. ``_run_halved_stage_series``
        # returns a Series indexed by a default RangeIndex; we replace
        # it via ``pd.Series(..., index=...)`` rather than ``.set_index``
        # (which is a DataFrame method, not a Series method).
        stage_series = pd.Series(
            stage_series.values, index=df.index, name="trend_up_stage"
        )

    # Bollinger-band-touch MarkPoints — per ADR-0006 ("触线当日在 K 线上打
    # MarkPoint"). RANGE_BULL 之外的裸 BB 触线不打点(策略不认)。
    bb_touch_pts = _build_bb_touch_points(df, bb_df)

    dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]]
    ohlc = df[["open", "close", "low", "high"]].values.tolist()

    # ----- Panel 0: K-line with MA + BB + MarkPoints + TrendState band ----

    kline_items = mark_point_data(buy=buy_pts, sell=sell_pts, hold=hold_pts)
    # Bollinger-band touches are informational — render at smaller size
    # than strategy signals so the eye still separates them.
    for touch in bb_touch_pts:
        kline_items.append(
            opts.MarkPointItem(
                name="bb_touch",
                coord=list(touch["coord"]),
                value=touch.get("value", "触"),
                symbol="circle",
                symbol_size=14,
                itemstyle_opts=opts.ItemStyleOpts(color=A_SHARE_HOLD_COLOR),
                label_opts=opts.LabelOpts(is_show=False),
            )
        )

    kline = (
        Kline(init_opts=opts.InitOpts(width="100%", height=kline_height))
        .add_xaxis(dates)
        .add_yaxis(
            series_name=f"{symbol} · {adj_label} · 日 K",
            y_axis=ohlc,
            itemstyle_opts=opts.ItemStyleOpts(
                color=A_SHARE_UP_COLOR,
                color0=A_SHARE_DOWN_COLOR,
                border_color="#8A0000",
                border_color0="#065726",
            ),
            markpoint_opts=opts.MarkPointOpts(
                data=kline_items,
                symbol_size=42,
                label_opts=opts.LabelOpts(position="top", color="#fff", font_size=10),
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title=f"{symbol}", pos_left="center"),
            xaxis_opts=opts.AxisOpts(type_="category", is_scale=True, grid_index=0),
            yaxis_opts=opts.AxisOpts(is_scale=True, grid_index=0),
            legend_opts=opts.LegendOpts(pos_top="3%"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1, 2])],
            axispointer_opts=opts.AxisPointerOpts(
                is_show=True,
                link=[{"xAxisIndex": "all"}],
                label=opts.LabelOpts(background_color="#777"),
            ),
        )
    )

    # MA20 + MA60 line series — overlaid on the K-line axis (grid_index=0).
    ma20_line = (
        Line()
        .add_xaxis(dates)
        .add_yaxis(
            "MA20",
            ma20.tolist(),
            xaxis_index=0,
            yaxis_index=0,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["MA20"], width=1
            ),
        )
    )
    ma60_line = (
        Line()
        .add_xaxis(dates)
        .add_yaxis(
            "MA60",
            ma60.tolist(),
            xaxis_index=0,
            yaxis_index=0,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["MA60"], width=1
            ),
        )
    )
    bb_upper_line = (
        Line()
        .add_xaxis(dates)
        .add_yaxis(
            "BB_upper",
            bb_df["upper"].tolist(),
            xaxis_index=0,
            yaxis_index=0,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["BB_upper"], width=1, type_="dashed"
            ),
        )
    )
    bb_mid_line = (
        Line()
        .add_xaxis(dates)
        .add_yaxis(
            "BB_mid",
            bb_df["mid"].tolist(),
            xaxis_index=0,
            yaxis_index=0,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["BB_mid"], width=1
            ),
        )
    )
    bb_lower_line = (
        Line()
        .add_xaxis(dates)
        .add_yaxis(
            "BB_lower",
            bb_df["lower"].tolist(),
            xaxis_index=0,
            yaxis_index=0,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["BB_lower"], width=1, type_="dashed"
            ),
        )
    )

    # TrendState background band — one Bar series per state, stacked
    # at a fixed height; only the relevant state has the volume value
    # at each bar, others are 0. Pyecharts Bar stack renders this as
    # a thin colored strip behind the K-line.
    state_to_color = TREND_STATE_COLORS
    state_strips: list[Bar] = []
    for state_name, color in state_to_color.items():
        heights = [
            1.0 if s == state_name else 0.0 for s in state_series.tolist()
        ]
        strip = (
            Bar()
            .add_xaxis(dates)
            .add_yaxis(
                state_name,
                heights,
                xaxis_index=0,
                yaxis_index=0,
                stack="trend_state_band",
                itemstyle_opts=opts.ItemStyleOpts(color=color, opacity=0.35),
                label_opts=opts.LabelOpts(is_show=False),
            )
            .set_global_opts(
                xaxis_opts=opts.AxisOpts(
                    type_="category", grid_index=0, is_show=False
                ),
                yaxis_opts=opts.AxisOpts(grid_index=0, is_show=False),
            )
        )
        state_strips.append(strip)

    # ----- Panel 1: ADX (+DI / -DI / ADX) + HalvedStage band -------------

    adx_chart = (
        Line(init_opts=opts.InitOpts(width="100%", height=adx_height))
        .add_xaxis(dates)
        .add_yaxis(
            "ADX",
            adx_df["adx"].tolist(),
            xaxis_index=1,
            yaxis_index=1,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["ADX"], width=1
            ),
            markline_opts=opts.MarkLineOpts(
                data=[
                    opts.MarkLineItem(y=25, name="ADX 25"),
                    opts.MarkLineItem(y=20, name="ADX 20"),
                ],
                label_opts=opts.LabelOpts(position="insideEndTop"),
                linestyle_opts=opts.LineStyleOpts(type_="dashed", color="#888"),
            ),
        )
        .add_yaxis(
            "+DI",
            adx_df["plus_di"].tolist(),
            xaxis_index=1,
            yaxis_index=1,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["plus_di"], width=1
            ),
        )
        .add_yaxis(
            "-DI",
            adx_df["minus_di"].tolist(),
            xaxis_index=1,
            yaxis_index=1,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["minus_di"], width=1
            ),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=1),
            yaxis_opts=opts.AxisOpts(
                grid_index=1, min_=0, max_=100, name="ADX",
            ),
            legend_opts=opts.LegendOpts(pos_top="55%"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1, 2])],
        )
    )

    # HalvedStage band — same stack pattern as TrendState, on the ADX axis.
    stage_strips: list[Bar] = []
    for stage_name, color in HALVED_STAGE_COLORS.items():
        heights = [
            1.0 if s == stage_name else 0.0 for s in stage_series.tolist()
        ]
        strip = (
            Bar()
            .add_xaxis(dates)
            .add_yaxis(
                stage_name,
                heights,
                xaxis_index=1,
                yaxis_index=1,
                stack="halved_stage_band",
                itemstyle_opts=opts.ItemStyleOpts(color=color, opacity=0.45),
                label_opts=opts.LabelOpts(is_show=False),
            )
            .set_global_opts(
                xaxis_opts=opts.AxisOpts(
                    type_="category", grid_index=1, is_show=False
                ),
                yaxis_opts=opts.AxisOpts(grid_index=1, is_show=False),
            )
        )
        stage_strips.append(strip)

    # ----- Panel 2: RSI ----------------------------------------------------

    rsi_chart = (
        Line(init_opts=opts.InitOpts(width="100%", height=rsi_height))
        .add_xaxis(dates)
        .add_yaxis(
            "RSI",
            rsi_series.tolist(),
            xaxis_index=2,
            yaxis_index=2,
            is_symbol_show=False,
            linestyle_opts=opts.LineStyleOpts(
                color=INDICATOR_LINE_COLORS["RSI"], width=1
            ),
            markline_opts=opts.MarkLineOpts(
                data=[opts.MarkLineItem(y=30, name="RSI 30")],
                label_opts=opts.LabelOpts(position="insideEndTop"),
                linestyle_opts=opts.LineStyleOpts(type_="dashed", color="#888"),
            ),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(type_="category", grid_index=2),
            yaxis_opts=opts.AxisOpts(
                grid_index=2, min_=0, max_=100, name="RSI",
            ),
            legend_opts=opts.LegendOpts(pos_top="80%"),
            tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
            datazoom_opts=[opts.DataZoomOpts(type_="inside", xaxis_index=[0, 1, 2])],
        )
    )

    # ----- Grid assembly ---------------------------------------------------

    grid = Grid(init_opts=opts.InitOpts(width="100%", height=total_height))
    # Main K-line panel (top).
    grid.add(
        kline,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
        ),
    )
    # Overlay MA + BB lines on the same panel — Pyecharts lets multiple
    # chart objects share a grid via ``grid_index``.
    grid.add(
        ma20_line,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
        ),
    )
    grid.add(
        ma60_line,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
        ),
    )
    grid.add(
        bb_upper_line,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
        ),
    )
    grid.add(
        bb_mid_line,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
        ),
    )
    grid.add(
        bb_lower_line,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
        ),
    )
    for strip in state_strips:
        grid.add(
            strip,
            grid_opts=opts.GridOpts(
                pos_left="8%", pos_right="4%", pos_top="8%", height="50%",
            ),
        )

    # ADX panel (middle).
    grid.add(
        adx_chart,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="62%", height="20%",
        ),
    )
    for strip in stage_strips:
        grid.add(
            strip,
            grid_opts=opts.GridOpts(
                pos_left="8%", pos_right="4%", pos_top="62%", height="20%",
            ),
        )

    # RSI panel (bottom).
    grid.add(
        rsi_chart,
        grid_opts=opts.GridOpts(
            pos_left="8%", pos_right="4%", pos_top="84%", height="14%",
        ),
    )

    return grid


# ---------------------------------------------------------------------------
# Helper — Bollinger band-touch MarkPoints (T09 / ADR-0006)
# ---------------------------------------------------------------------------


def _build_bb_touch_points(
    df: pd.DataFrame, bb_df: pd.DataFrame
) -> list[MarkPoint]:
    """Return one MarkPoint per bar where ``High >= BB_upper`` or
    ``Low <= BB_lower`` — the Adaptive Trigger's contact events.

    Per ADR-0006 the chart renders these as small grey dots on the
    K-line at the touch y-value. They are informational only — the
    strategy only acts on BB touches when the state is RANGE_BULL,
    but the chart shows every touch so the user can see where the
    band pressure concentrated.

    Each MarkPoint carries a ``value`` of ``"上轨"`` or ``"下轨"``
    so the legend / hover reads in Chinese.
    """
    dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]]
    high = df["high"].tolist()
    low = df["low"].tolist()
    upper = bb_df["upper"].tolist()
    lower = bb_df["lower"].tolist()

    points: list[MarkPoint] = []
    for i, (d, h, l, u, lo) in enumerate(zip(dates, high, low, upper, lower)):
        if h >= u:
            points.append({"coord": [d, float(u)], "value": "上轨"})
        if l <= lo:
            points.append({"coord": [d, float(lo)], "value": "下轨"})
    return points
