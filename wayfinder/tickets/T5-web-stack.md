---
name: T5-web-stack
title: Web 框架 / Tech stack 选定
type: grilling
status: closed
claimed-by: wayfinder-session-1
blocked-by: [T7 ✅ out-of-scope, T10 ✅]
closed-date: 2026-08-22
---

## Resolution

**决策：Streamlit**

用户拍板。理由：
- 和 T10 决策（Pyecharts + streamlit-echarts）天然契合，单进程零集成成本
- 单云服务器部署一句话：`streamlit run app.py --server.port 8501 --server.address 0.0.0.0`
- 多页架构：`pages/` 目录即路由，Streamlit 1.30+ 的原生 multipage
- 浏览器端访问：Streamlit 自带 WebSocket + Rerun 机制，刷新自动拉数据

页面结构（5 页）：
1. `app.py` — 仪表盘首页（持仓概览 + 今日操作计划）
2. `pages/1_股票详情.py` — K 线 + 买卖点 + 财务摘要
3. `pages/2_策略管理.py` — 列出 / 激活 / 跑批
4. `pages/3_持仓管理.py` — 真实持仓录入、查询、虚拟账本切换
5. `pages/4_回测.py` — 选策略 + 时间窗口 + 看 PnL

约束：
- 不引入 FastAPI 等其他后端（除非明确需要）
- 用 `st.session_state` 管理当前选中策略、当前持仓视图
- 每天 15:30 调度：用 `apscheduler` 或系统 cron 触发 Streamlit 内部写 DB（不在 Streamlit 进程里跑 cron，避免阻塞 UI）

---

## Question

云端部署 + 浏览器访问的 Web 框架选哪个？

候选：

- **Streamlit**：纯 Python、内置组件丰富、Plotly/ECharts 集成方便；单进程部署；缺点是复杂交互要 hack
- **FastAPI + React (Vite)**：前后端分离、API 清晰、UI 灵活；缺点是工程量翻倍、要管两个进程
- **Dash**：Python 写回调、对金融图表友好；缺点是社区在收缩
- **Gradio**：ML demo 风、和量化场景不太契合

决策需要结合：
- T7-research：云服务器带宽/内存约束
- T10-research：图表方案的实现成本

约束：
- 单一仓库、单进程部署优先
- 不要为了"灵活"过度工程化
- 浏览器端访问要顺（图表不能卡）

期望：T7/T10 出来后用户拍板。
