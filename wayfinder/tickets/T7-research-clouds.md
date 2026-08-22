---
name: T7-research-clouds
title: 国内云服务商对比
type: research
status: closed
blocked-by: []
---

## Resolution

**Out of scope** — 用户已有云服务器，本 ticket 不再需要。
移至 MAP.md 的 Out of scope 区。

---

## Question (存档)

为云端部署选型做事实准备。

调研对象：
- 阿里云轻量应用服务器
- 腾讯云轻量应用服务器
- 华为云 Flexus 应用服务器
- 百度智能云

维度：
1. **价格**：2C2G / 2C4G 规格的月费、年费、新用户折扣、续费价
2. **网络**：国内访问速度（到 AKShare 数据源）、公网带宽上限、流量计费
3. **性能**：CPU 是否真分核、磁盘 IO、网络延迟稳定性
4. **合规**：A 股数据访问是否需要备案、是否影响策略运行
5. **运维**：防火墙规则、SSH、Web 端口暴露难度
6. **数据安全**：快照、备份、是否会被回收（90 天不登录）
7. **国际支付**：支付宝/微信 vs 信用卡

输出：`wayfinder/research/domestic-clouds.md` —— 表格 + 推荐方案 + 注意事项。

Findings 完成后贴 context pointer → 关闭 → 写进 MAP.md Decisions so far。
