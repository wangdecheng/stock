# 14 — 依赖 / CI grep 墙 / 文档收尾

**What to build:**
把临时夹带的依赖、CI 墙、文档补齐。这是新架构上线的最后一公里 —— 没有它,前面 6 个 ticket 完成的功能对生产环境来说只是"代码里能跑"。

**Blocked by:** 12-ui-config-editor-page, 13-cache-sidecar-and-source-badge

**Status:** ready-for-agent

- [ ] `requirements.txt` 增补 `baostock>=0.1.0`(若 ticket 09 已加则核对版本号)
- [ ] `tests/test_data_layer.py` 的 CI grep 墙扩展:
 - 允许集:`framework/data/adapter.py`、`framework/data/backends/*.py`
 - 违规检测:`import akshare` 或 `import baostock` 在允许集外出现即失败
 - 测试本身放在允许集外(避免误判)
- [ ] `specs/spec-a-stock-quant/data-adapter.md` 更新:
 - 删除"AKShare 单一上游"段(改为"默认 AKShare,可切换")
 - 新增"Multi-source"段,指向 ADR-0001..0005
 - "AKShare interface mapping" 表保留(仍是 cheat sheet),但加一段"BaoStock 等价映射"
- [ ] `README.md` 更新:
 - 启动部分增加"数据源切换"小节,指向 `pages/5_数据源.py`
 - 部署部分增加 `STOCK_DATA_SOURCE_*` 环境变量说明
- [ ] `.scratch/a-stock-quant/spec-multi-source-data-adapter.md` 标 `Status: shipped`
- [ ] 全量回归:`pytest tests/` 全绿
- [ ] 手动 smoke test:停掉 Streamlit,启 `streamlit run app.py`,访问 `pages/5_数据源.py`,改 fallback,保存,验证 K 线页面生效