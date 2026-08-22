# 06 — 15:30 scheduler, failure handling, Run-now button, active-strategy switcher

**What to build:** A separate-process runner fetches today, runs the active strategy, and writes `strategy_suggestions` rows. On AKShare failure it retries with exponential backoff and writes a sentinel row; on strategy exception it writes a sentinel row with the error. The dashboard has a "Run now" button that invokes the runner and an "Activate" toggle on the Strategy page that writes `config.active_strategy_id` for the runner to pick up.

**Blocked by:** 05 — needs the dashboard for the Run-now / Activate / Apply-all buttons to have UX semantics.

**Status:** ready-for-agent

- [ ] `runner/scheduled_run.py` runs as `python -m runner.scheduled_run` from a separate OS process (NOT inside the Streamlit process); reads `config.active_strategy_id`; exit 0 if empty.
- [ ] Calls `DataAdapter.get_calendar(today, today)`; exits 0 if today is not a trading day.
- [ ] On AKShare exception, retries with backoff `30 s / 120 s / 300 s` (3 attempts); if all fail, writes one sentinel row to `strategy_suggestions` (action='hold', `reason='data_unavailable'`, `applied=0`) and exits non-zero.
- [ ] On `strategy.generate(ctx)` raising, catches the exception, writes one sentinel row (`reason='strategy_exception'`, error captured in `note`), and exits non-zero.
- [ ] Successful run writes one row per (symbol, action) emitted by the strategy; idempotency: a second run for the same calendar date overwrites prior suggestions for that strategy (or appends + a separate dedupe pass — pick one and document it in `scheduler.md`).
- [ ] Dashboard "Run now" button shells out to the runner (subprocess); refreshes page after completion; new rows visible in `strategy_suggestions` within 5 minutes for fixtures on a fast laptop.
- [ ] "Activate" toggle on `pages/2_策略管理.py` writes `config.active_strategy_id`; the runner picks it up on the next fire.
- [ ] The current `data/app.db` reflects `active_strategy_id` and one complete (synthetic) run on the test fixture: `python -m runner.scheduled_run --once --now 2026-08-22T15:30:00` produces a known set of rows.
- [ ] Activation guard (per `strategy-interface.md`) blocks the toggle if no backtest exists for the strategy in the last 30 days; the badge "需要 30 天内有一次回测" is shown when blocked.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-7 + Constraint 13 (scheduler outside Streamlit process)
- `specs/spec-a-stock-quant/scheduler.md` — full runner contract + failure-mode table + crontab sample
- `specs/spec-a-stock-quant/strategy-interface.md` § Activation guard — 30-day backtest prerequisite
- `specs/spec-a-stock-quant/ui-pages.md` § Page 1 / Page 3 — Run-now button + Activate toggle
- `specs/spec-a-stock-quant/data-adapter.md` § Failure modes — empty/error/cache behaviors the scheduler relies on
