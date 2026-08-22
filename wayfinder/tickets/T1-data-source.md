---
name: T1-data-source
title: 数据源选定
type: grilling
status: closed
claimed-by: wayfinder-session-1
blocked-by: [T4-research ✅]
closed-date: 2026-08-22
---

## Resolution

**决策：AKShare 单源（完全免费）**

用户拍板接受两个 trade-off：
- 不取北向资金（AKShare 的 `stock_hsgt_hist_em` 自 2024-08-19 起失效，需要 Tushare Pro 才能补）
- 复权接受 10%+ 累积误差（ST/新股/重组股）

约束实现：
1. 封装 `DataAdapter` 层，对策略只暴露 `get_bars(symbol, start, end, adj) / get_fundamentals(symbol) / get_calendar()` 三个方法
2. 底层全部走 AKShare `stock_zh_a_hist` / `stock_zh_a_hist_min_em` / `stock_financial_report_sina` 等接口
3. 限频硬限 ≤ 20 req/min/IP（东财铁律），用 `ratelimit` 装饰器 + 全局令牌桶兜底
4. 失败 fallback：缓存层兜底（本地 parquet），网络挂了用最近一日数据 + UI 上显示「数据延迟」徽标

跟决策相关的 trade-off 在未来需要北向 / 复权精度时再开新 ticket。

---

## Question

A 股免费数据源选哪个？候选：

- **AKShare**：开源、免费、无 token、Python 生态好
- **Tushare Pro**：有免费额度（限频），需要 token，覆盖全
- **BaoStock**：免费、无 token，但只到日频、不维护中
- **新浪财经/东方财富直连**：免费但非官方、易失效

决策需要回答：
1. 覆盖度（K 线日/分钟、财务、复权、龙虎榜、北向、停牌、分红送股）
2. 稳定性（限频策略、是否会被封 IP）
3. 维护活跃度（最近一次 commit、最近一年数据准确性投诉）
4. 与 Python 框架的对接成本

预期在 T4-research 的 findings 出来后再 grill 一次，让用户拍板。
