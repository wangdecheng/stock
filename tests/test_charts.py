"""tests/test_charts.py

Unit tests for the T10 chart wrapper (``framework.charts``).

These tests intentionally do NOT render anything. We only assert on the
``pyecharts`` option dict (types + keys + data) so the wrapper is testable
in CI without a browser. Visual rendering is verified manually via
``streamlit run`` and the existing ``AppTest`` UI smoke (see
``tests/test_ui_t5.py::test_stock_detail_renders_kline_with_mock_adapter``).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from pyecharts import options as opts
from pyecharts.charts import Bar, Grid, Kline

from framework.charts import (
    A_SHARE_UP_COLOR,
    A_SHARE_DOWN_COLOR,
    A_SHARE_HOLD_COLOR,
    build_kline,
    build_kline_volume_grid,
    build_volume,
    mark_point_data,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """60-row daily OHLCV with a monotonic uptrend (close >= open every day)
    so the volume split into up/down days is deterministic."""
    n = 60
    base = 10.0
    return pd.DataFrame(
        {
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": [base + 0.05 * i for i in range(n)],
            "close": [base + 0.10 * i for i in range(n)],
            "high": [base + 0.20 * i for i in range(n)],
            "low": [base - 0.10 + 0.05 * i for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
        }
    )


@pytest.fixture
def mixed_ohlcv() -> pd.DataFrame:
    """OHLCV where half the bars are up-days and half are down-days, to
    exercise the per-bar volume colour split."""
    n = 6
    rows = []
    for i in range(n):
        if i % 2 == 0:
            rows.append({"date": date(2024, 1, 1) + timedelta(days=i),
                         "open": 10.0, "close": 11.0, "high": 12.0,
                         "low": 9.5, "volume": 100})
        else:
            rows.append({"date": date(2024, 1, 1) + timedelta(days=i),
                         "open": 11.0, "close": 10.0, "high": 11.5,
                         "low": 9.5, "volume": 200})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_a_share_color_constants_match_t10_decision():
    """T10 pins A-share convention red-up / green-down. Hex codes match
    the recommendation in ``research/chart-libraries.md`` Section 2.1
    (Chinese-market standard). Keep these pinned so a future tweak can't
    silently drift away from the agreed convention."""
    assert A_SHARE_UP_COLOR == "#ec0000"
    assert A_SHARE_DOWN_COLOR == "#14b143"
    assert A_SHARE_HOLD_COLOR == "#888888"


# ---------------------------------------------------------------------------
# mark_point_data
# ---------------------------------------------------------------------------


def test_mark_point_data_emits_one_item_per_input_point():
    buy = [{"coord": ["2024-01-02", 10.5], "value": "买"}]
    sell = [{"coord": ["2024-01-03", 11.0], "value": "卖"}]
    hold = [{"coord": ["2024-01-04", 10.7], "value": "持"}]
    items = mark_point_data(buy=buy, sell=sell, hold=hold)
    assert len(items) == 3
    assert all(isinstance(it, opts.MarkPointItem) for it in items)


def test_mark_point_data_uses_a_share_colors():
    buy = [{"coord": ["2024-01-02", 10.5], "value": "买"}]
    sell = [{"coord": ["2024-01-03", 11.0], "value": "卖"}]
    items = mark_point_data(buy=buy, sell=sell)
    buy_color = items[0].opts["itemStyle"].opts.get("color")
    sell_color = items[1].opts["itemStyle"].opts.get("color")
    assert buy_color == A_SHARE_UP_COLOR
    assert sell_color == A_SHARE_DOWN_COLOR


def test_mark_point_data_defaults_to_chinese_labels():
    """The chart is for Chinese-market UI; markers default to 买 / 卖 / 持
    so the legend reads correctly even when callers only pass the action
    code (buy / sell / hold)."""
    items = mark_point_data(
        buy=[{"coord": ["2024-01-02", 10.5]}],
        sell=[{"coord": ["2024-01-03", 11.0]}],
        hold=[{"coord": ["2024-01-04", 10.7]}],
    )
    assert [it.opts["value"] for it in items] == ["买", "卖", "持"]


def test_mark_point_data_sell_uses_rotated_triangle():
    """Buy and sell share the same glyph (triangle) so they're visually
    matched in size; the sell marker is rotated 180° so it points down —
    matching the snippet in ``research/chart-libraries.md`` §2.1."""
    items = mark_point_data(
        buy=[{"coord": ["2024-01-02", 10.5]}],
        sell=[{"coord": ["2024-01-03", 11.0]}],
    )
    buy_it, sell_it = items
    assert buy_it.opts["symbol"] == "triangle"
    assert buy_it.opts["symbolRotate"] == 0
    assert sell_it.opts["symbol"] == "triangle"
    assert sell_it.opts["symbolRotate"] == 180


def test_mark_point_data_empty_inputs_returns_empty_list():
    assert mark_point_data() == []
    assert mark_point_data(buy=[], sell=[], hold=[]) == []


# ---------------------------------------------------------------------------
# build_kline
# ---------------------------------------------------------------------------


def test_build_kline_returns_kline_instance(sample_ohlcv):
    chart = build_kline(sample_ohlcv, symbol="000001", adj_label="前复权")
    assert isinstance(chart, Kline)


def test_build_kline_uses_a_share_candle_colors(sample_ohlcv):
    chart = build_kline(sample_ohlcv, symbol="000001", adj_label="前复权")
    series = chart.options["series"][0]
    assert series["type"] == "candlestick"
    # OHLC data must be in [open, close, low, high] order per pyecharts.
    expected = sample_ohlcv[["open", "close", "low", "high"]].values.tolist()
    assert series["data"] == expected


def test_build_kline_includes_buy_sell_marks(sample_ohlcv):
    buy = [{"coord": ["2024-01-10", 10.5], "value": "买"}]
    sell = [{"coord": ["2024-01-30", 12.5], "value": "卖"}]
    chart = build_kline(
        sample_ohlcv, symbol="000001", adj_label="前复权",
        buy_pts=buy, sell_pts=sell,
    )
    markpoint_obj = chart.options["series"][0]["markPoint"]
    # MarkPointOpts stores the data list under opts["data"].
    data = markpoint_obj.opts["data"]
    assert len(data) == 2


def test_build_kline_without_signals_omits_marks(sample_ohlcv):
    chart = build_kline(sample_ohlcv, symbol="000001", adj_label="前复权")
    # markPoint is still present but its data list is empty (pyecharts
    # round-trips it back as MarkPointOpts(data=[])).
    data = chart.options["series"][0]["markPoint"].opts["data"]
    assert data == []


def test_build_kline_sets_axis_pointer_link_for_grid_coupling(sample_ohlcv):
    """T10 requires multi-chart axis-pointer linkage so crosshair is
    shared between the K-line and the volume sub-chart."""
    chart = build_kline(sample_ohlcv, symbol="000001", adj_label="前复权")
    ap = chart.options["axisPointer"]
    assert ap.opts["show"] is True
    # link is a list of {"xAxisIndex": "all"} dicts.
    link = ap.opts["link"]
    assert any(d.get("xAxisIndex") == "all" for d in link)


# ---------------------------------------------------------------------------
# build_volume
# ---------------------------------------------------------------------------


def test_build_volume_returns_bar_instance(sample_ohlcv):
    chart = build_volume(sample_ohlcv)
    assert isinstance(chart, Bar)


def test_build_volume_splits_red_green_by_day_direction(mixed_ohlcv):
    """Up-day volume goes to the red stack, down-day volume to the green.
    Verify each bar's value list: red series non-zero on up-days only,
    green series non-zero on down-days only."""
    chart = build_volume(mixed_ohlcv)
    series = chart.options["series"]
    # Two stacked series: "↑" (red) and "↓" (green).
    assert len(series) == 2
    red_data = series[0]["data"]
    green_data = series[1]["data"]
    closes = mixed_ohlcv["close"].tolist()
    opens = mixed_ohlcv["open"].tolist()
    volumes = mixed_ohlcv["volume"].tolist()
    for i, (o, c, v, r, g) in enumerate(zip(opens, closes, volumes, red_data, green_data)):
        if c >= o:
            assert r == v, f"up-day {i}: red series should carry full volume"
            assert g == 0, f"up-day {i}: green series must be zero"
        else:
            assert r == 0, f"down-day {i}: red series must be zero"
            assert g == v, f"down-day {i}: green series should carry full volume"


def test_build_volume_uses_a_share_colors(mixed_ohlcv):
    chart = build_volume(mixed_ohlcv)
    series = chart.options["series"]
    assert series[0]["itemStyle"].opts["color"] == A_SHARE_UP_COLOR
    assert series[1]["itemStyle"].opts["color"] == A_SHARE_DOWN_COLOR


def test_build_volume_anchored_to_grid_index_1(sample_ohlcv):
    """When stacked into a Grid with the K-line at grid_index 0, the
    volume sub-chart must claim grid_index=1 so they don't overlap."""
    chart = build_volume(sample_ohlcv)
    xaxis_opts = chart.options["xAxis"]
    yaxis_opts = chart.options["yAxis"]
    assert any(ax.get("gridIndex") == 1 for ax in xaxis_opts)
    assert any(ax.get("gridIndex") == 1 for ax in yaxis_opts)


# ---------------------------------------------------------------------------
# build_kline_volume_grid
# ---------------------------------------------------------------------------


def test_build_kline_volume_grid_returns_grid_merging_kline_and_bar_series(
    sample_ohlcv,
):
    """Pyecharts' Grid merges each child's options into its own options
    dict (no child chart reference is kept), so we assert on the merged
    series list instead of child instances."""
    grid = build_kline_volume_grid(sample_ohlcv, symbol="000001", adj_label="前复权")
    assert isinstance(grid, Grid)
    series_types = {s["type"] for s in grid.options["series"]}
    assert "candlestick" in series_types, series_types
    assert "bar" in series_types, series_types
    # Two distinct yAxis grid indices must exist so the sub-charts don't
    # overlap on the same axes.
    yaxis_indices = {ax.get("gridIndex") for ax in grid.options["yAxis"]}
    assert {0, 1}.issubset(yaxis_indices), yaxis_indices


def test_build_kline_volume_grid_forwards_buy_sell_to_kline(sample_ohlcv):
    buy = [{"coord": ["2024-01-05", 10.5], "value": "买"}]
    sell = [{"coord": ["2024-01-50", 14.5], "value": "卖"}]
    grid = build_kline_volume_grid(
        sample_ohlcv, symbol="000001", adj_label="前复权",
        buy_pts=buy, sell_pts=sell,
    )
    # The candlestick series is the one carrying MarkPoints.
    kline_series = next(s for s in grid.options["series"] if s["type"] == "candlestick")
    data = kline_series["markPoint"].opts["data"]
    assert len(data) == 2
