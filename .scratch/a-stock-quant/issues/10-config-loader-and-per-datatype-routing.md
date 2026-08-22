# 10 — Config 加载 + 环境变量 override + 按数据类型路由

**What to build:**
用户能在 `config.toml` 里按数据类型分别指定 primary backend,环境变量能 override 文件值。`DataAdapter` 启动时按 config 装配每个 datatype 映射到对应 backend,运行时按请求的 datatype 选择 backend。**本 ticket 不实现 fallback 链**(那是 ticket 11),只做单 backend 路由。

**Blocked by:** 08-extract-backend-protocol-and-adapter-facade, 09-baostock-backend-implementation

**Status:** ready-for-agent

- [ ] 仓库根新增 `config.toml`,默认内容:`[data_source.bars] backend = "akshare"` + `[data_source.fundamentals] backend = "akshare"` + `[data_source.calendar] backend = "akshare"`(全 AKShare,行为与当前一致)
- [ ] `config.toml` 加入 `.gitignore`(`config.toml` 个人配置不入版本库,但提供 `config.toml.example` 入库)
- [ ] 新增 `framework.data.config` 模块,`load_config(path: Path) -> DataAdapterConfig`
- [ ] `DataAdapterConfig` 是 frozen dataclass,持有 `default_backend: str` + `routes: dict[DataType, str]`
- [ ] 环境变量 override 优先级:`STOCK_DATA_SOURCE_BARS` / `STOCK_DATA_SOURCE_FUNDAMENTALS` / `STOCK_DATA_SOURCE_CALENDAR` 优先于文件值;不存在则用文件值
- [ ] 缺字段报错 `InvalidConfig`(消息明确指出哪个 datatype 没配);backend 名不在注册表里报错 `UnknownBackend`
- [ ] `DataAdapter` 启动时调 `load_config()` 构造 backend 映射,缓存到 `st.cache_resource`(`Streamlit` 上下文)/ 普通单例(脚本上下文);`get_bars` 内部按 datatype 选 backend
- [ ] 工厂函数 `make_backend(name: str, cache_dir: Path) -> Backend` 注册表放在 `framework/data/backends/__init__.py`
- [ ] 测试:env override 优先级、缺字段抛错、未知 backend 名抛错、路由选对了 backend(用 fake backend 验证调用了哪个)
- [ ] 现有所有调用方测试全绿(行为不变,只是底层多了一层配置读取)