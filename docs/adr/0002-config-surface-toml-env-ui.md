# 配置面:`config.toml` + 环境变量 override + UI 编辑页

数据源选择需要一个**进程间共享**的真相源 —— `framework.runner.scheduled_run` 作为子进程在 15:30 自动跑,Streamlit UI 进程只是它的一个消费方,任何"只在 UI 里生效"的开关都注定是 half-feature。

因此:

- 默认配置写入仓库根目录的 **`config.toml`**(git 可追溯)
- 环境变量 `STOCK_DATA_SOURCE_BARS` / `STOCK_DATA_SOURCE_FUNDAMENTALS` 在子进程启动时 override 文件值(12-factor 部署友好)
- 一个**专门的 Streamlit 页面**(`pages/5_数据源.py`)作为该文件的 web 编辑器:显示当前生效值(含 env override)、表单编辑、保存回写。保存后 Streamlit 进程自动 reload;子进程要等下次启动(`立即运行当前策略` 按钮即触发新子进程)才生效

侧栏运行时切换 widget **不做** —— 它管不了子进程,且会让 `stale_seconds` / `cache_hit` / `source` 三个 UI 字段的语义跳变,投入产出比低。