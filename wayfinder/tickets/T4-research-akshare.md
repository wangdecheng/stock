---
name: T4-research-akshare
title: AKShare 数据覆盖度调研
type: research
status: closed
blocked-by: []
---

## Resolution

调研完成 (2026-08-22)。Findings 写入 `wayfinder/research/akshare-coverage.md`。

**Gist**: AKShare v1.18.94 (2026-08-21 发布) 覆盖研究期所需 10 类数据中日 K、分钟 K、财务三表、估值、龙虎榜、分红送股、涨停股池全可用; **北向资金 `stock_hsgt_hist_em` 自 2024-08-19 起已失效 (交易所披露口径变更)**, 必须切到 Tushare Pro; 复权因子对 ST/新股/重组股有滞后 (累积误差 10%+); 东财限流 ≤ 20 次/分钟/IP 是铁律。**建议**: AKShare (研究主源) + Tushare Pro 2000 积分档 (生产, 复权 + 北向) + BaoStock (兜底) 三层组合。

---

## Question

为 A 股选型做事实准备。AKShare 在以下维度的真实表现如何？

1. **覆盖度**（2026 年现状）：
   - 日/分钟 K 线（沪深两市，含北交所）
   - 前复权/后复权/不复权切换
   - 财务数据（资产负债表、利润表、现金流量表、季报/年报）
   - 估值（PE、PB、股息率）
   - 资金流（北向、主力、龙虎榜）
   - 分红送股、停牌复牌
2. **稳定性**：
   - 限频阈值（被封 IP 的红线）
   - 单次请求返回数据量上限
   - 近 6 个月 GitHub issue 中"数据不准/缺失"的占比
3. **维护活跃度**：最近一次 commit、最近一次 release、贡献者活跃度
4. **替代品对位**：Tushare Pro 免费额度、BaoStock 仍可用的部分

输出：`wayfinder/research/akshare-coverage.md` —— 包含一份「AKShare 字段清单 + 已知坑 + 备选 fallback」文档。

Findings 写完会在本 ticket 上贴一条 context pointer，然后关闭（resolution comment + close），并加进 MAP.md 的 Decisions so far。
