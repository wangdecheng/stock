# 12 — UI 配置编辑页 `pages/5_数据源.py`

**What to build:**
用户打开 `pages/5_数据源.py`,看到当前生效配置(环境变量 override 高亮显示),用表单修改 per-datatype 的 primary + fallback,保存即写回 `config.toml` 并触发 `DataAdapter` 重建。"测试连接"按钮调用当前表单里选的 backend 的 `ping()`,返回连通性 + 延迟。

**Blocked by:** 11-fallback-chain-and-barsresult-source

**Status:** ready-for-agent

- [ ] 新增 `pages/5_数据源.py`,使用 Streamlit 表单(`st.form`)防止 partial submit
- [ ] 顶部信息块:显示当前 `config.toml` 路径 + 最近修改时间 + 哪些字段被环境变量 override(用 ⚠️ 图标 + tooltip 说明)
- [ ] 表单字段:per-datatype(目前三个:bars / fundamentals / calendar)各两个下拉框(primary + fallback,fallback 可空)
- [ ] "测试连接"按钮:对每个 backend 调 `ping()`,结果显示绿色 ✓ + 延迟 ms 或红色 ✗ + 错误消息
- [ ] 保存按钮:写回 `config.toml`(保持原顺序与注释风格;用 `toml` 库写回而不是手拼字符串),触发 `st.cache_resource.clear()`(让 DataAdapter 重建)+ `st.rerun()`
- [ ] 保存前 schema 校验(走 `framework.data.config.load_config` 的同一套验证,避免写出不可解析的 toml)
- [ ] 保存失败给出明确错误(哪个字段、为什么)
- [ ] 顶部提示:"子进程(15:30 调度)需要下次启动生效;当前 Streamlit 进程自动生效"
- [ ] 测试:用 `AppTest` 模拟"打开页面 → 看到当前值 → 改表单 → 保存 → reload 后读到新值";覆盖 env override 高亮逻辑
- [ ] 所有现有 UI 测试 (`test_ui_t5.py`, `test_ui_t11.py`) 全绿