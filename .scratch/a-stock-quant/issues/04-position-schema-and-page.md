# 04 — Position schema, DB module, positions page, cost-basis algorithm, apply batch

**What to build:** The Positions page lets the user insert / edit / delete real trades via `st.data_editor`; entering two `510300` buys at different prices produces a real-position aggregate with weighted-average cost shown. Switching to a virtual book lets the user click "Apply all to virtual book" on a list of `strategy_suggestions` and watch an atomic batch update positions + cash without half-applied state on failure.

**Blocked by:** 03 — needs the SQLite infrastructure + `backtests` table built up by the backtest ticket.

**Status:** ready-for-agent

- [ ] Schema (`framework/persistence/schema.sql`) defines the four tables per `specs/spec-a-stock-quant/position-schema.md`: `real_trades`, `virtual_books`, `virtual_positions`, `strategy_suggestions` (plus their indexes + CHECK constraints + FK `ON DELETE CASCADE`).
- [ ] `config` key-value table also added with `INSERT OR IGNORE (active_strategy_id, '')` so the runner + UI can read/write active strategy without a separate ticket.
- [ ] `framework/persistence/db.py` exposes a transaction context manager; `pages/3_持仓管理.py` and the dashboard buttons in `app.py` use it for any multi-row write.
- [ ] `pages/3_持仓管理.py` tabs: "真实持仓" (`st.data_editor` over `real_trades` + new-trade form) and "虚拟账本" (strategy dropdown over `virtual_books`, `initial_cash` editor, positions table, suggestions audit table with applied/unapplied filter).
- [ ] Weighted-average cost algorithm matches `position-schema.md`: a multi-lot buy merges; a sell reduces qty with `avg_cost` unchanged and records realized PnL via fills (in `strategy_suggestions.applied` write-through).
- [ ] Real-position aggregate (`real_position` / `real_avg_cost`) is derived from `real_trades` per `position-schema.md`; not stored as a separate table.
- [ ] "Apply all to virtual book" runs an atomic SQLite transaction: any row failure rolls back the entire batch; a half-applied book is forbidden (verified by a fault-injection test).
- [ ] Defaults: `virtual_books.initial_cash = 100_000`; commission = `0.0003` of cash flow per fill (configurable in code; UI follows in 06).
- [ ] Two-track invariant: no code path writes `real_trades` from a strategy run; no code path reads `real_trades` to influence a virtual-book decision. (Verified by code review checklist + a single grep-based smoke test if practical.)
- [ ] `data/app.db` is gitignored.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-5 + Constraints 9/10/11 (two-track separation + weighted-avg cost + dual track guards)
- `specs/spec-a-stock-quant/position-schema.md` — full 4-table DDL + cost-basis algorithm + apply-batch atomicity contract
- `specs/spec-a-stock-quant/ui-pages.md` § Page 4 — Positions page wireframe + data_editor pattern
