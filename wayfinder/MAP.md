# Wayfinder Map — A 股量化 Web 框架

> 本地图使用 local-markdown tracker。Tickets 在 `tickets/`，research findings 在 `research/`。
> 用 `/mattpocock-skills:wayfinder <ticket-name or empty>` 推进。

## Destination

部署在云端服务器的 A 股量化 Web 应用 MVP：
- 后端 Python，云端常驻，每个交易日 15:30 自动跑所选策略生成操作计划
- 浏览器访问：可看持仓、股票 K 线（含买卖点标注）、历史回测、操作计划
- 多策略可热插拔，策略代码与框架解耦
- 数据源免费（A 股）
- 持仓手动录入，不实盘

## Notes

- **域**：个人 A 股量化（非机构、非高频）
- **部署**：单云服务器（轻量级实例）
- **数据源**：必须免费、无需 token 优先
- **不实盘**：每日输出操作计划，人工决策
- **Skills this effort should consult**：`/domain-modeling`（策略接口、持仓模型、信号格式）/ `/prototype`（UI 框架验证）/ `/grilling`（关键决策）
- **用户偏好**：单机能跑通的最简方案优先，不要过度工程化

## Decisions so far

<!-- one line per closed ticket — gist + link -->

- [T4-research-akshare](tickets/T4-research-akshare.md) — AKShare 作研究源 + Tushare Pro 2000 积分档（200 元/年）作生产源（复权 + 北向）+ BaoStock 兜底；东财硬限 ≤ 20 req/min/IP
- [T10-research-charts](tickets/T10-research-charts.md) — 主选 Pyecharts v2.0.9（ECharts 5 稳定线）+ `streamlit-echarts[pyecharts]` 桥接，A 股 MarkPoint 买卖点 + 颜色 + 文字一行调用，Grid 多图联动；备选 Plotly。→ [`research/chart-libraries.md`](research/chart-libraries.md)
- [T1-data-source](tickets/T1-data-source.md) — **AKShare 单源**（完全免费），DataAdapter 层封装；暂不取北向、复权接受 10%+ 误差
- [T2-backtest-engine](tickets/T2-backtest-engine.md) — **自写最小引擎**（事件循环 + PnL 记录）+ **empyrical 算指标**（Sharpe / 最大回撤 / Calmar）；总代码量 ~400 行
- [T12-tech-indicators](tickets/T12-tech-indicators.md) — **pandas-ta**（纯 Python、130+ 指标、DataFrame-native），统一封装在 `indicators/` 模块；TA-Lib 因跨平台安装坑被排除
- [T5-web-stack](tickets/T5-web-stack.md) — **Streamlit** 单进程 + 5 页 multipage（仪表盘 / 股票详情 / 策略管理 / 持仓管理 / 回测）；和 Pyecharts 桥接；不引入 FastAPI
- [T6-position-model](tickets/T6-position-model.md) — **双轨**：真实持仓（手动录入）+ 每策略虚拟账本（10 万初始资金、平均成本法）；4 张 SQLite 表 schema 已锁
- [T3-strategy-interface](tickets/T3-strategy-interface.md) — **target_position** 信号格式 + Context 对象（含 positions/cash/trades/state/bars/price）+ 自动扫描 strategies/ 加载 + `__init__` 默认参数 + UI 自动表单 + state.json 可选
- [T11-ui-info-arch](tickets/T11-ui-info-arch.md) — 5 页 MVP 一起上（仪表盘 / 股票详情 / 策略管理 / 持仓管理 / 回测）；桌面端为主；Streamlit 裸跑 + Nginx 反代 basic auth

## Not yet specified

<!-- in-scope fog that can't be ticketized yet — graduates as the frontier advances -->

- 是否需要登录/鉴权（单用户场景下可否裸跑 + 内网限制？）
- 多股票池管理（自选股分组、策略绑定到股池？）
- 回测的默认时间窗口（近 1 年？近 3 年？可配置？）
- 复权方式（前复权 vs 不复权，统一默认？）
- 买入资金分配（等权？固定金额？按信号强度？）
- 策略参数调优 UI（手动改 JSON/CSV vs Web 表单）
- 失败/异常告警（数据拉取失败、策略抛错怎么通知？）

## Out of scope

<!-- ruled beyond the destination — closed tickets here don't appear in Decisions so far -->

- [T7-research-clouds](tickets/T7-research-clouds.md) — 用户已有云服务器，无需选型调研
- [T8-cloud-choice](tickets/T8-cloud-choice.md) — 同上
- [T9-provision-server](tickets/T9-provision-server.md) — 用户自行运维，开通与初始化不在本 effort

---

## Frontier (open, unblocked, takeable)

_(empty — 所有 ticket 都已关闭，前方路线清晰)_
