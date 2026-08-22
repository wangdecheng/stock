"""framework/charts — T10 chart wrappers (Pyecharts v2.0.9).

Centralizes the A-share K-line + volume Grid pattern so the Streamlit
pages don't reinvent the boilerplate on every render. Visual style and
interaction are kept identical to the inline version that previously
lived in ``pages/1_股票详情.py`` — this module is a refactor seam, not a
redesign.

Public surface:
  * ``A_SHARE_UP_COLOR`` / ``A_SHARE_DOWN_COLOR`` — convention pins.
  * ``mark_point_data`` — turn (buy, sell, hold) signal lists into
    Pyecharts ``MarkPointItem`` objects with the right colors.
  * ``build_kline`` — A-share candlestick chart with optional MarkPoints.
  * ``build_volume`` — volume sub-chart colored by per-bar direction.
  * ``build_kline_volume_grid`` — the K-line + volume Grid multi-chart.

Pages import from here and hand the result straight to
``streamlit_echarts.st_pyecharts``. We do NOT call ``chart.render()``
here — the Streamlit component renders into the page directly.
"""

from __future__ import annotations

from typing import Iterable, TypedDict

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline

# A-share convention: red up, green down. Pinned so a future tweak can't
# silently drift away from the T10 decision without a test failing.
A_SHARE_UP_COLOR = "#ec0000"
A_SHARE_DOWN_COLOR = "#00da3c"


class MarkPoint(TypedDict, total=False):
    """Schema for one buy/sell/hold overlay point.

    ``coord`` is the [date_str, price] pair — Pyecharts requires the
    x value to be a category-axis string already in the chart's x-axis.
    ``value`` is the label shown next to the marker.
    """

    coord: list
    value: str


__all__ = [
    "A_SHARE_UP_COLOR",
    "A_SHARE_DOWN_COLOR",
    "MarkPoint",
    "mark_point_data",
    "build_kline",
    "build_volume",
    "build_kline_volume_grid",
]


# ---------------------------------------------------------------------------
# MarkPoints
# ---------------------------------------------------------------------------


def _build_mark_item(
    name: str,
    color: str,
    symbol: str,
    pt: MarkPoint,
) -> opts.MarkPointItem:
    return opts.MarkPointItem(
        name=name,
        coord=list(pt["coord"]),
        value=pt.get("value", name),
        symbol=symbol,
        itemstyle_opts=opts.ItemStyleOpts(color=color),
    )


def mark_point_data(
    buy: Iterable[MarkPoint] | None = None,
    sell: Iterable[MarkPoint] | None = None,
    hold: Iterable[MarkPoint] | None = None,
) -> list[opts.MarkPointItem]:
    """Combine buy/sell/hold signal lists into one flat list of
    ``MarkPointItem`` objects with A-share colors.

    Buy uses an upward triangle in red, sell uses a downward triangle in
    green, hold uses a small grey dot — so the chart still reads at a
    glance even when the active strategy emits a mix.
    """
    items: list[opts.MarkPointItem] = []
    for pt in buy or []:
        items.append(
            _build_mark_item(
                name="buy",
                color=A_SHARE_UP_COLOR,
                symbol="triangle",
                pt=pt,
            )
        )
    for pt in sell or []:
        items.append(
            _build_mark_item(
                name="sell",
                color=A_SHARE_DOWN_COLOR,
                symbol="triangle",
                pt=pt,
            )
        )
    for pt in hold or []:
        items.append(
            _build_mark_item(
                name="hold",
                color="#888888",
                symbol="pin",
                pt=pt,
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
                border_color0="#008F28",
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
    """Volume sub-chart colored by per-bar direction (A-share convention).

    Implemented as two stacked Bar series — red for up-days, green for
    down-days — because Pyecharts does not honor a per-bar color array
    on a single series the way Plotly does. The visual result matches
    the legacy inlined version.
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
) -> Grid:
    """Stack K-line (top) + volume (bottom) into a single Grid with
    shared x-axis and crosshair linkage.

    This is the canonical "stock detail" view from ``pages/1_股票详情.py``.
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

    total_h = 600
    return (
        Grid(init_opts=opts.InitOpts(width="100%", height=f"{total_h}px"))
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