---
name: T12-tech-indicators
title: 技术指标库选型
type: grilling
status: closed
blocked-by: []
claimed-by: wayfinder-session-1
closed-date: 2026-08-22
---

## Resolution

**决策：pandas-ta**

用户确认：技术指标（ADX、MACD、MA、布林线等）由 pandas-ta 提供，和 empyrical（组合指标）并列。

实现：
- `indicators/` 模块封装常用指标，避免策略里到处 `ta.xxx()`
- 至少提供：`ma(n)` / `ema(n)` / `macd()` / `bollinger(n, k)` / `rsi(n)` / `atr(n)` / `adx(n)` / `kdj()` / `obv()`
- 内部统一用 pandas-ta 实现，外部暴露简单函数签名
- DataFrame 是 AKShare 直接输出，零格式转换

为什么不是 TA-Lib：
- TA-Lib C 库在 Apple Silicon / Windows / 某些 Linux 发行版 pip 装不上，需要 conda 或自编译
- 我们目标是单云服务器部署，pandas-ta 零环境成本更稳
- A 股常用指标 pandas-ta 全覆盖

跟 T2 关系：
- T2（回测引擎）用 empyrical 算 Sharpe/回撤
- T12（技术指标库）用 pandas-ta 算 ADX/MACD/MA 等
- 策略代码里两者都会用，通过 `indicators.xxx()` 和 `metrics.xxx()` 区分

---

## Question

技术指标库选哪个？策略代码里需要 ADX、MACD、MA、布林线等 A 股常用指标。

候选：
- **pandas-ta**：纯 Python、130+ 指标、DataFrame-native、pip 直接装
- **TA-Lib**：C 库、最快，但跨平台安装坑多
- **stockstats**：纯 Python、30+ 指标、风格最简
- **finta**：纯 Python、80+ 指标
