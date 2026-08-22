# 显式 fallback 链,不做隐式自动回退

## Status

accepted

## Context

多源之后出现一个诱惑:**让 adapter 在 primary 失败时自动试下一家**。这看起来贴心,但违背"显式声明用哪家"的初衷 —— 用户在配置里写 `bars.backend = "akshare"` 表示"我要 AKShare 的数据",如果系统偷偷换成 BaoStock,两家在复权因子、除权除息日上的微小差异会让**回测结果不可复现**。这是 backtest 的大忌。

## Decision

fallback 必须在配置文件中被显式命名:

```toml
[[data_source.bars]]
backend = "akshare"
fallback = "baostock"   # 不写 = 不回退,挂了直接抛
```

回退触发条件**仅限 primary 抛不可恢复异常**(网络、DNS、解析错误)。**不**触发的情况:

- EmptyBarsError(backend 给出空响应 = 它对该 symbol 有结论,尊重它)
- UnknownSymbolError(任何 backend 都认不出)
- 限频阻塞(应该排队等,而不是换家)

每家 backend 维护**独立**的 `SlidingWindowLimiter`,fallback 路径只走 fallback 自己的限频曲线。`BarsResult.source` 字段标明数据真实来自 primary 还是 fallback,UI 诚实展示。

## Consequences

- 配置 schema 多了一层 `[[data_source.<datatype>]]` 数组语义 —— 但只比单值复杂一点点。
- UI 编辑页面需要渲染"主 + 备"两个下拉框,而不是一个。
- 回退行为**完全可预测**且**可复现**。同一份 config + 同一份策略代码,任何时候跑出的回测数据来自同一家,直到用户主动改 config。