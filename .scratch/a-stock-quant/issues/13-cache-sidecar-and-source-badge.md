# 13 — 缓存 sidecar `.meta.json` + UI 数据来源徽标

**What to build:**
每次 `write_cache` 顺手写一份 `<file>.parquet.meta.json`,记录 `{"source": "...", "fetched_at": iso8601}`。新增 `read_cache_meta(path) -> (source, fetched_at) | (None, None)`,供 `BarsResult` 在 cache 命中时携带真实 source。UI 侧(K 线页面)在每个 bar 块右上角加徽标显示"数据来源:X / 缓存命中 / 延迟 Y 天",让用户一眼看清数据新鲜度。

**Blocked by:** 09-baostock-backend-implementation, 11-fallback-chain-and-barsresult-source

**Status:** ready-for-agent

- [ ] `framework.data.cache.write_cache` 同时写 `<file>.parquet` 和 `<file>.parquet.meta.json`(原子写:先写 `.meta.json.tmp` 再 rename;parquet 本身的原子写保持不变)
- [ ] 新增 `read_cache_meta(path: Path) -> tuple[str | None, datetime | None]`:sidecar 不存在返回 `(None, None)`;存在但损坏返回 `(None, None)` 并 warning
- [ ] 兼容存量无 sidecar 的文件:读时视作 legacy AKShare 数据(`source="akshare"`,`fetched_at` 用文件 mtime)
- [ ] `DataAdapter.get_bars` 缓存命中分支返回的 `BarsResult.source` 改为**真实的 sidecar source**(可能是 `"akshare"` 也可能是 `"baostock"`,而不是固定标 primary)
- [ ] UI 改动:`pages/1_股票详情.py`(K 线页面)在 K 线图右上加一个 `st.caption` 显示"数据来源: {source} / 缓存: {是/否} / 延迟: {stale_seconds 友好格式}"
- [ ] 同样在 `pages/4_回测.py` 回测结果页面加同样的徽标
- [ ] `app.py` 仪表盘的"数据延迟"提示信息合并 source 字段:`{source} 缓存 / 延迟 X 天`
- [ ] 测试:write_cache 写两份文件、read_cache_meta 各种边界、UI 渲染含 source 字段的 BarsResult
- [ ] 现有缓存兜底测试(`tests/test_data_layer.py` 里相关用例)不破坏