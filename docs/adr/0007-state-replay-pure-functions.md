# 历史状态回放走纯函数,不复用 `generate()`

股票详情页要在主图下方贴 TrendState 4 色带、ADX 副图下方贴 HalvedStage 3 色带(见 ADR-0006)。这两个色带需要**每根历史 bar** 的 `current_state` 和 `trend_up_stage`。

## Considered Options

- **方案 A 复用 `generate()`**(曾考虑过):每根 bar 构造一个 `Context`,调 `AdxBbRegimeStrategy.generate()`。问题:① `generate()` 写 `ctx.state`(`setdefault` / `exit_in_progress` 改写),需为图表构造一个 fake `Context`,且要保证副作用不污染;② Phased Exit 的中间态(`exit_in_progress={"target":..., "days_left":1}`)会出现在色带上,但色带只关心 `current_state` —— 中间态会误导用户以为状态在变化。
- **方案 B 纯函数回放**(本方案):新增两个纯函数,接受 `pd.DataFrame` 数组,返回 `pd.Series[str]`:
  - `_new_state_series(df, adx_df, ma20, ma60, bb_mid, hysteresis_days=2) -> pd.Series[str]` —— 逐 bar 跑 `_classify` + Hysteresis Gate,产出 **post-gate `new_state`**(即 `generate()` 写入 `ctx.state["current_state"]` 的值)。色带要贴的就是这个 —— 策略真正落库的状态,不是 cascade 的原始分类。
  - `_run_halved_stage_series(close, ma10, ma20, initial_stage="full") -> pd.Series[str]` —— 把 T04 的三阶段机改成按时间序列推进。

  **Phased Exit 不画**(它是过渡用的中间态,见 ADR-0006 决策)。
- **方案 C 不画历史**:只贴最右一根 bar 的当前状态,色带基本不可见,违背「图-策略对齐」的初衷。

## Decision

采用 **方案 B**,两个纯函数都放在 `strategies/adx_bb_regime.py`,与 `_classify` 同级。

理由:

1. **零副作用**:纯函数吃 df → 出 Series,方便测试;色带渲染只需要这两个 Series,不需要构造 `Context`。
2. **零分歧**:`_new_state_series` 内部直接调 `_classify`(静态方法)+ Hysteresis Gate 的逐 bar 实现(等价于 `_count_consecutive` + `bb_mid` 比对),数学上不可能跟 `generate()` 里写入 `current_state` 的值不一致 —— Phased Exit 不参与,严格等于 post-gate `new_state`。
3. **可配 parity test**:写一个 `test_new_state_series_matches_generate_step_by_step` —— 用一段足够长的 df(≥60 bar)逐 bar 走完 `generate()`,记录每根 bar 写入的 `ctx.state["current_state"]`,然后跟 `_new_state_series(...).iloc[i]` 逐元素比对。**Hysteresis Gate 跨 bar 的 2-day 计数是关键** —— parity 必须覆盖至少一次 gate 触发 + 至少一次 gate 不触发,才能证明回放正确。这条测试是图-策略对齐的最终保险。
4. **HalvedStage 的初值**:用策略里的默认 `"full"`(`ctx.state.setdefault("trend_up_stage", "full")`),跟 `generate()` 的冷启动假设一致;色带从最左的 `full` 开始。

## Consequences

- `strategies/adx_bb_regime.py` 新增 ~40 行纯函数 + docstring,跟既有 `_classify` / `_count_consecutive` 风格一致。
- 新增测试 `tests/test_adx_bb_regime_state_replay.py`(或归入已有 `tests/test_adx_bb_regime_*.py` 套件),至少覆盖:
  - `test_classify_series_matches_generate_step_by_step` —— 至少 60 根真实 bar,逐 bar 比对。
  - `test_halved_stage_series_transitions_full_to_halved_to_cleared` —— 手工构造 close/ma10/ma20 序列,验证三阶段切换。
  - `test_classify_series_handles_warmup_nan` —— 前 `length-1` 根 bar 应该输出 `"RANGE_BEAR"`(策略 `_classify` 在 NaN 上 `>` `<` 都为 False,落到 else 分支),而不是 NaN。
- `pages/1_股票详情.py` 不直接调这两个函数 —— 通过 `framework.charts` 暴露的 `build_indicator_grid(..., df, strategy_instance)` 间接调用,这样图表层成为唯一入口,未来换页面也只改 charts 层。
- 如果未来有**第二种**策略也想在图上重放状态,要考虑把这两个纯函数提到 `framework.strategy` 作为公共 API(`state_replay(strategy_class, df) -> pd.DataFrame`)。**本 ADR 不预先做这一步**,等真有第二个用例再动。
