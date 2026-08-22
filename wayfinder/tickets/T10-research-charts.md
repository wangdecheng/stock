---
name: T10-research-charts
title: K 线 + 买卖点叠加 图表方案对比
type: research
status: closed
blocked-by: []
---

## Resolution

Findings: [`wayfinder/research/chart-libraries.md`](../research/chart-libraries.md)
**Gist**: 主选 Pyecharts v2.0.9（ECharts 5 稳定线）+ `streamlit-echarts[pyecharts]` 桥接 — A 股 MarkPoint 买卖点一行调用、Grid 多图联动、340 KB gzip 包体积匹配 Plotly；备选 Plotly 仅在需要 rangeselector 时启用。

## Question

为 UI 的"股票图表 + 买卖点标注"做技术选型准备。

候选：
- **Pyecharts**：中文文档完善、K 线 + markpoint/markline 一行调用、Web 渲染
- **Plotly**：交互体验最好（缩放/拖拽/框选）、Python + JS 双栈
- **mplfinance + mpld3**：传统方案、静态为主
- **ECharts 原生 JS**：最灵活，但要前端集成

维度（2026 年现状）：
1. K 线 OHLC + 复权处理
2. 在图上叠加买卖点（不同颜色 + 文字标签）
3. 缩放/拖拽/十字光标体验
4. 多图联动（K 线 + 成交量 + 指标）
5. 在 Streamlit / Dash 中的集成方式
6. 包体积、首次加载时间

输出：`wayfinder/research/chart-libraries.md` —— 表格 + 推荐 + 代码骨架示例。

完成后 → 贴 pointer → close → 加 MAP.md Decisions so far。
