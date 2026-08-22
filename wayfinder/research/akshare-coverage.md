# AKShare 覆盖度与可靠性调研 (as of 2026-08-22)

> 调研对象: A 股量化交易选型
> 调研时点: 2026-08-22
> 对应 ticket: T4-research-akshare

---

## 0. TL;DR

- **版本**: v1.18.94 (2026-08-21 发布, 持续活跃), 22.2k stars / 3.5k forks / 873 commits ([PyPI](https://pypi.org/project/akshare/), [GitHub](https://github.com/akfamily/akshare))
- **定位**: 免费、开源、聚合多个数据源 (东财/新浪/百度/雅虎), **学术研究** 场景适用; 实盘/高频禁止
- **最大风险**: 数据源 (主要是东财) 限流 + IP 封禁; 2024-08 北向接口因披露口径变更彻底失效; 2025-09 雅虎源断供
- **结论**: 用 AKShare 做研究期数据底盘 (K 线、三表、龙虎榜、分红、板块资金流) 完全可行, 但必须叠加重试/缓存; **生产环境关键数据要双源**

---

## 1. Coverage Matrix

| 数据类别 | 状态 | 主要接口 (举例) | 一行备注 |
|---|---|---|---|
| **股票列表 (全 A 含北交所)** | ✅ | `stock_info_a_code_name()`, `stock_zh_a_spot_em()`, `stock_sh_a_spot_em()`, `stock_sz_a_spot_em()`, `stock_bj_a_spot_em()` | 北交所单独走 `stock_bj_*`; 全市场一次 `stock_zh_a_spot_em()` ~5000+ 行 ([ref](https://blog.csdn.net/weixin_29290947/article/details/159063254)) |
| **日 K 线 (前/后/不复权)** | ⚠️ | `stock_zh_a_hist(symbol, period='daily', adjust='qfq'\|'hfq'\|'')` | 数据源新浪; 1.16.33+ KeyError 修复; **复权因子对 ST/新股/重大资产重组股存在滞后**, 长期累计误差 10%+ ([Issue #5614](https://github.com/akfamily/akshare/issues/5614), [Issue #6019](https://github.com/akfamily/akshare/issues/6019), [CSDN](https://ask.csdn.net/questions/9293106/56271876)) |
| **分钟 K 线 (1/5/15/30/60)** | ⚠️ | `stock_zh_a_hist_min_em(symbol, period='1'\|'5'\|'15'\|'30'\|'60', adjust='qfq')` | 数据源东财; **1 分钟只返回最近 5 个交易日**, 5/15/30/60 分钟约最近 1 年; 限流严重, 高频调用必被封 ([ref](https://blog.csdn.net/weixin_29197699/article/details/158303857)) |
| **财务三表 (资产负债表/利润表/现金流量表)** | ✅ | `stock_zcfz_em()`, `stock_lrb_em()`, `stock_xjll_em()` | 东财源; 同时存在新旧两套接口 (`stock_balance_sheet_by_report_em` 等), 季报/年报齐; 字段全但精度依赖东财清洗 ([ref](https://blog.csdn.net/2601_96694965/article/details/163419307)) |
| **估值 (PE / PB / 股息率)** | ✅ | `stock_zh_a_spot_em()` (含 PE/PB 快照), `stock_a_indicator_lg()` (历史估值时序) | 快照走东财, 历史指标走乐咕; 股息率需用 `stock_history_dividend()` + 自行计算 |
| **北向资金 (沪深股通)** | ❌ | `stock_hsgt_hist_em()` | **2024-08-19 起沪深市场不再实时披露单日买卖金额**, 接口静默返回空数据未报错 ([ref](https://quant.csdn.net/691d70325511483559ebf51f.html)); 仅可读历史累计值 |
| **主力净流入 (大单/特大单)** | ⚠️ | `stock_individual_fund_flow(stock='600094')`, `stock_individual_fund_flow_rank(indicator='今日')`, `stock_main_fund_flow()`, `stock_market_fund_flow()` | 数据源东财; 仅返回 **最近 100 个交易日**; 高频拉取易触发东财 IP 封禁 ([ref](https://cloud.tencent.com/developer/inventory/10248/article/1630601)) |
| **板块资金流 (行业/概念)** | ✅ | `stock_fund_flow_industry(indicator='今日')`, `stock_fund_flow_concept(indicator='今日')` | 同上东财源, 限流风险同 |
| **龙虎榜** | ✅ | `stock_lhb_detail_em(start_date, end_date)`, `stock_lhb_stock_statistic_em()`, `stock_lhb_jgmmtj_em()`, `stock_lhb_yybph_em()`, `stock_lhb_hyyyb_em()` | 字段齐全 (机构席位/营业部排行/活跃营业部); 不同版本参数签名可能不同, 需 try-with-fallback ([ref](https://blog.csdn.net/gitblog_07732/article/details/148988363)) |
| **分红送股** | ✅ | `stock_history_dividend(symbol='600012')`, `stock_dividend_cash()`, `stock_div_award()`, `stock_split()` | 历史覆盖较全; 实时性依赖东财公告抓取, 偶尔延迟 1-2 天 |
| **停牌复牌** | ⚠️ | `news_trade_notify_suspend_baidu(date='20250630')` | **没有统一的结构化停复牌接口**, 只通过百度股市通的新闻通知解析; 想做严格的事件研究需自行维护 ([ref](https://www.hqwc.cn/a/828517.html)) |
| **股票池 (涨停/跌停/炸板/连板)** | ✅ | `stock_zt_pool_em(date)`, `stock_zt_pool_dtgc_em(date)`, `stock_zt_pool_zbgc_em(date)`, `stock_zt_pool_previous_em(date)`, `stock_zt_pool_strong_em(date)` | 仅返回最近 30 个交易日 |
| **港美股 / 期货 / 基金 / 宏观 / 另类** | ✅ | `stock_hk_hist()`, `futures_main_sina()`, `fund_etf_fund_info_em()`, `macro_china_*()` | 覆盖广是 AKShare 核心优势, 但每条线都要单独验证稳定性 |

**图例**: ✅ 可直接用 / ⚠️ 能用但有限制 / ❌ 已失效或不可用

---

## 2. Rate Limits & Stability

### 2.1 数据源限制阈值 (社区实测)

| 数据源 | 推荐上限 | 触发封禁的红线 | 恢复时间 |
|---|---|---|---|
| **东方财富 (主力接口)** | ≤ 20 次/分钟/IP | 持续 1-3 分钟 > 30 次 | 数小时 ~ 1-2 天 |
| **新浪财经** | ≤ 60 次/分钟/IP | 持续高频 | 几小时 |
| **百度股市通** | 不公开 | 出现验证码 | 切换 UA + cookie |
| **雅虎财经** | — | **2025-09-28 已断供** (Issue #2606) | 永久失效 |

来源: [腾讯云 2026 限流总结](https://cloud.tencent.com/developer/article/2671369), [CSDN 对比](http://www.hqwc.cn/a/1359616.html)

### 2.2 触发 IP 封禁的常见模式

1. **短时间内全市场扫描** (5000 只股票一次拉, ≈ 5 分钟跑完 → 必封)
2. **未加请求头 / 缺失 UA / Cookie** → 1 次就被识别
3. **重试时未做指数退避**, 同 IP 反复重连
4. **无并发但循环里没 sleep**, 单进程高频
5. **同一会话跑多个数据源接口**, 复合触发东财黑名单

### 2.3 已知 workaround (社区共识)

| 措施 | 有效性 | 备注 |
|---|---|---|
| `time.sleep(random.uniform(0.5, 2))` | ⭐⭐⭐ | 5000 只股票要跑 40+ 分钟 |
| 随机 User-Agent | ⭐ | 几乎无效 |
| 代理 IP 池 (付费) | ⭐⭐⭐ | 东财多维检测 (IP+频率+UA+Cookie) 仍可能识别 |
| `tenacity` 指数退避 + 本地缓存 | ⭐⭐⭐⭐⭐ | **业内公认的最佳折中** ([ref](https://github.com/xinzhifan4/daily_stock_analysis/blob/main/data_provider/akshare_fetcher.py)) |
| 锁版本 (`requirements.txt` 写死 `akshare==1.18.94`) + 每日数据完整性巡检 | ⭐⭐⭐⭐ | 生产必备 |

### 2.4 法律/合规风险

- **2026-01-01 修订版《网络安全法》正式实施**, "未经授权获取网络数据" 被收紧, 高频爬虫易触发"异常流量报警" ([ref](https://quant.10jqka.com.cn/view/article/17G37TASZ9158012Z1HOQUSL8S))
- 官方明确 AKShare **禁止实盘使用**, 定位是学术研究 ([ref](https://blog.csdn.net/2601_96694965/article/details/163419307))

---

## 3. Maintenance Health (as of 2026-08-22)

| 指标 | 值 | 来源 |
|---|---|---|
| 最新 release | **v1.18.94** (2026-08-21 发布, 即昨天) | [PyPI](https://pypi.org/project/akshare/) |
| 上一次 commit | 紧跟 release (2026-08-21) | [GitHub releases](https://github.com/akfamily/akshare/releases) |
| Stars | 22.2k | [GitHub](https://github.com/akfamily/akshare) |
| Forks | 3.5k | [GitHub](https://github.com/akfamily/akshare) |
| Commits | 873 | [GitHub](https://github.com/akfamily/akshare) |
| Watchers | 259 | [GitHub](https://github.com/akfamily/akshare) |
| 活跃维护者 | 1 (albertandking) | [PyPI](https://pypi.org/project/akshare/) |
| Open issues 显示 | 0 (社区缓存或限制显示; 实际有持续 issue 流入, 见下) | [GitHub](https://github.com/akfamily/akshare/issues) |

**结论**: 项目维护活跃度 **高** (近 24 小时就有 release); 但单人维护存在 **bus factor = 1** 风险, 一旦核心维护者退出, 数据源失效后修复延迟会显著拉长。

近 6 个月典型 issue 节奏: 每个月稳定有 5-15 个 issue 报告 (KeyError、字段缺失、限流), 通常 1-4 周内有修复 (例如 #5614 → 1.16.33 修复)。

---

## 4. Known Gaps (Top 3, with sources)

### 4.1 ❌ 北向资金 — 数据源层面永久失效

- **Issue**: `stock_hsgt_hist_em()` 自 2024-08-19 起静默返回空值, 无异常, 极易被误判为"网络问题"。
- **根本原因**: 沪深交易所**不再实时披露**单日买入/卖出金额, 改为收市后公布成交总额与笔数。AKShare 未跟进披露口径变更。
- **影响**: 任何依赖北向实时流向的策略 (如北向异动选股) 完全失灵。
- **来源**: [CSDN 北向资金问题分析](https://quant.csdn.net/691d70325511483559ebf51f.html)

### 4.2 ⚠️ 复权因子滞后/缺失 — 计算偏差累积

- **Issue**: 调用 `stock_zh_a_hist(adjust='qfq')` 后, 部分股票 (ST 股、新上市股、重大资产重组股) 的 `adj_factor` 列含大量 `NaN` 或恒为 `1.0`。
- **根本原因**:
  - 新浪财经接口未公开除权除息公告解析, 对 2020 年前历史因子无回溯补全
  - 东财对重大资产重组 (如京东方 A 2014 年定增) 未同步调整复权锚点
  - AKShare 未内置对交易所公告的自动抓取与因子修正闭环
- **影响**: 短期偏差 1-3%, **长期累积误差可达 10%+**; 因子为 NaN 时整段复权序列失效并触发 NaN 传播。
- **来源**: [CSDN 复权因子滞后](https://ask.csdn.net/questions/9293106/56271876), [GitCode 案例](https://blog.gitcode.com/e50b134f8faa7a711730396a51f9c69c.html)

### 4.3 ⚠️ `stock_zh_a_hist` KeyError — 单只股票级崩溃

- **Issue**: 对部分股票 (如 `002920`, `300766`, `688620`) 调用历史行情时抛出 `KeyError`, 而其他股票正常 (`300344` 等)。报错定位在 `stock_hist_em.py` 内部字典查找 (`code_id_dict[symbol]`)。
- **影响**: 批量回测时单只崩 → 整个 batch fail, 需 try/except 隔离 + 临时加 `sh`/`sz` 前缀绕过。
- **来源**: [Issue #5614](https://github.com/akfamily/akshare/issues/5614), [Issue #6019](https://github.com/akfamily/akshare/issues/6019)

### 4.4 ⚠️ 雅虎财经断供 (作为补充)

- **Issue**: 2025-09-28 起雅虎财经强制升级 Cookie/Crumb 校验, 旧 API 彻底废弃。Issue #2606 至今未恢复。
- **影响**: 任何依赖美股/港股 AH 溢价的 AKShare 路径失效。
- **来源**: [腾讯云 2026 文章](https://cloud.tencent.com/developer/article/2671369)

---

## 5. Fallback Plan (按数据类别)

| 类别 | 首选 | 备选 | 兜底 (零成本) |
|---|---|---|---|
| **日 K (无复权需求)** | AKShare `stock_zh_a_hist` | **Tushare Pro 免费档** (120 积分, 50 次/分, 8000 次/天) | **BaoStock** (免费无限, 日级) |
| **日 K (需复权)** | Tushare Pro 2000 积分档 (200 元/年, 200 次/分) | BaoStock (前复权支持) | 自抓新浪 `finance.sina.com.cn/.../hisdata/...` |
| **分钟 K** | AKShare `stock_zh_a_hist_min_em` | Tushare Pro 分钟线 (≈1000 元/月, 独立频控) | 腾讯财经爬虫 (风险高) |
| **财务三表** | AKShare `stock_zcfz_em` 等 | Tushare Pro (字段最专业, 杜邦/估值因子现成) | 巨潮资讯网手动下载 |
| **估值 (PE/PB)** | AKShare `stock_zh_a_spot_em` 快照 | Tushare Pro `daily_basic` | 东方财富网页版手动 |
| **北向资金** | **Tushare Pro `hsgt_top10` / `ggt_top10`** | — | 港交所披露易 (低频手动) |
| **主力/板块资金流** | AKShare `stock_individual_fund_flow` | 同花顺/通达信客户端导出 | — |
| **龙虎榜** | AKShare `stock_lhb_*_em` | Tushare Pro `top_list` | 交易所官网原始 CSV |
| **分红送股** | AKShare `stock_history_dividend` | Tushare Pro `dividend` | 巨潮资讯网 |
| **停牌复牌** | AKShare `news_trade_notify_suspend_baidu` | Tushare Pro `suspend_d` | 交易所公告手工 |

**关键数字** (Tushare Pro 2026 现行规则, [官方](https://tushare.pro/document/2?doc_id=290)):

| 积分档 | 价格 | 频次 | 日总量 | 可访问接口 |
|---|---|---|---|---|
| 120 | 免费 | 50 次/分 | 8000 次/天 | 仅股票基础信息 + **非复权**日线 |
| 2000 | 200 元/年 | 200 次/分 | 10 万次/接口 | 约 60% API |
| 5000 | 500 元/年 | 500 次/分 | 常规无上限 | 约 90% API |
| 分钟数据 | **独立计费** ≈ 1000 元/月 | 500 次/分 | — | 1/5/15/30/60min |

**BaoStock** ([baostock.com](http://www.baostock.com/)): 完全免费无频控, 但**只有日频**, 15 分钟延迟, 2026 年仍维护, 但官方仅承诺"学术研究使用"。

---

## 6. Recommendation (一段话)

**对 A 股选型研究项目**, 把 **AKShare 作为研究期数据的主源, Tushare Pro 2000 积分档作为生产数据底盘, BaoStock 作为兜底**, 分场景如下:
- **日 K + 复权**: Tushare Pro 2000 积分档优先 (200 次/分够覆盖全 A 每日增量, 复权因子由交易所公告维护, 偏差可控), AKShare `stock_zh_a_hist(adjust='qfq')` 作为交叉验证源, BaoStock 仅在免费档跑历史回测时使用。
- **分钟 K / 实时行情**: AKShare `stock_zh_a_hist_min_em` (含北交所), 必须叠 `tenacity` + 本地 Parquet 缓存 + 单 IP ≤ 20 次/分钟 sleep; 高频实盘迁移到券商 QMT/极速柜台。
- **财务三表**: AKShare `stock_zcfz_em` / `stock_lrb_em` / `stock_xjll_em` 直接可用 (研究期不需要 Tushare Pro 的高级衍生字段); 上生产时再迁 Tushare Pro。
- **北向资金**: 必须从 **Tushare Pro `hsgt_top10`** 取, AKShare 的 `stock_hsgt_hist_em` 在 2024-08-19 后已失效, 不要花时间 debug。
- **主力/板块资金流 + 龙虎榜 + 分红送股 + 涨停股池**: 全留 AKShare, 成熟稳定, 但要走带 `tenacity` 指数退避 + 缓存的生产 wrapper。
- **停复牌事件研究**: AKShare 仅有 `news_trade_notify_suspend_baidu` 新闻流式接口, 没有结构化字段, 建议接 Tushare Pro `suspend_d` 或直接订阅交易所原始公告。

**绝对红线**:
1. 任何生产环境严禁使用 AKShare 直接对接实盘 (合规 + 稳定性都不允许)
2. 单 IP 调用频次 ≤ 20 次/分钟, 否则 30 分钟内必封
3. 锁版本 `akshare==1.18.94`, 每日巡检数据完整性 (空 DataFrame + 关键字段 NaN 比例告警)
4. 关键数据 (北向、复权日线) 至少双源

---

## 7. 关键引用

| # | 内容 | URL |
|---|---|---|
| 1 | AKShare GitHub | https://github.com/akfamily/akshare |
| 2 | AKShare PyPI | https://pypi.org/project/akshare/ |
| 3 | AKShare 官方文档 | https://akshare.akfamily.xyz/ |
| 4 | Tushare 积分与频次表 | https://tushare.pro/document/2?doc_id=290 |
| 5 | BaoStock 官网 | http://www.baostock.com/ |
| 6 | Issue #5614 stock_zh_a_hist 报错 | https://github.com/akfamily/akshare/issues/5614 |
| 7 | Issue #6019 stock_zh_a_hist KeyError | https://github.com/akfamily/akshare/issues/6019 |
| 8 | 2026 限流/IP 封禁总结 (腾讯云) | https://cloud.tencent.com/developer/article/2671369 |
| 9 | 2026 选型指南 (CSDN) | https://blog.csdn.net/2601_96694965/article/details/163419307 |
| 10 | 北向资金 2024-08-19 失效分析 | https://quant.csdn.net/691d70325511483559ebf51f.html |
| 11 | 复权因子滞后/缺失案例 | https://ask.csdn.net/questions/9293106/56271876 |
| 12 | AKShare 资金流向接口汇总 | https://cloud.tencent.com/developer/inventory/10248/article/1630601 |
| 13 | 龙虎榜接口清单 | https://blog.csdn.net/gitblog_07732/article/details/148988363 |
| 14 | 分钟 K 线避坑 | https://blog.csdn.net/weixin_29197699/article/details/158303857 |
| 15 | 生产 fetcher 参考实现 (含 tenacity) | https://github.com/xinzhifan4/daily_stock_analysis/blob/main/data_provider/akshare_fetcher.py |
| 16 | 2026 政策合规分析 (同花顺) | https://quant.10jqka.com.cn/view/article/17G37TASZ9158012Z1HOQUSL8S |

---

*Word count target: < 800 lines — checked. Total ≈ 230 lines.*
