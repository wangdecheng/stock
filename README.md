# A 股量化 Web 框架 (MVP)

Self-hosted Web app for an individual A-share investor: write strategies → backtest → daily 15:30 plan → view in browser.

Single-user, free data (AKShare), manual positions, no live order routing. Production-grade accuracy is **not** a goal — closing the strategy→plan loop is.

> 当前状态:T1 (DataAdapter + hello K-line) 开发中。T2-T7 待办。
>
> 完整能力定义见 [`specs/spec-a-stock-quant/SPEC.md`](specs/spec-a-stock-quant/SPEC.md)。

## 本地启动

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

打开 http://localhost:8501,在表单里输入股票代码(如 `000001`)和日期区间,即可看到 Pyecharts K 线。

## 部署契约

Effort 内只交付可部署的代码 + 本 README。**不**包含云服务器开通、Nginx、TLS、cron 配线 —— 这些由用户自行运维。

最小部署步骤(参考,不在本 effort 内):

1. 把本目录 scp 到云服务器 `/opt/app/`
2. `pip install -r requirements.txt`
3. `streamlit run app.py --server.port 8501 --server.address 0.0.0.0` (systemd / supervisor / nohup)
4. Nginx/Caddy 反代 `localhost:8501` + basic auth + HTTPS
5. 15:30 cron(独立进程):`python -m framework.runner.scheduled_run`(T6 实现,目前为空)

## 目录结构

```
.
├── app.py                      # Page 1 (T1 暂为 hello K-line,T5 改为仪表盘)
├── pages/                      # 多页(T5 填充)
├── framework/                  # 框架内部,strategies/ 不允许 import 这里
│   ├── data/                   # CAP-1:DataAdapter + 限频 + 缓存
│   ├── persistence/            # T4:SQLite schema + db
│   ├── strategy/               # T2:Strategy 基类 + Context + discover
│   ├── backtest/               # T3:回测引擎
│   ├── indicators/             # T2:pandas-ta 包装
│   ├── metrics/                # T3:empyrical 包装
│   └── runner/                 # T6:15:30 调度入口
├── strategies/                 # 用户编辑的策略 .py(framework 不允许 import)
├── data/cache/                 # parquet 兜底缓存(gitignored)
├── tests/
├── requirements.txt
└── specs/                      # 设计文档
```

## 开发约定

- **AKShare 单源**:整个代码库只有 `framework/data/adapter.py` 可以 `import akshare`,CI 用 grep 卡死。
- **限频硬墙**:20 req/min/IP,任何 DataAdapter 公开方法必须经 `ratelimit` 装饰器。
- **缓存兜底**:网络挂时 `get_bars` 读最近 parquet overlap 并返回 `stale_seconds > 0`,UI 渲染「数据延迟」徽标。
- **策略代码解耦**:`strategies/*.py` 只能 `import` `Strategy` 基类 + `Context`,不能 `import framework.*` 任何其他模块。