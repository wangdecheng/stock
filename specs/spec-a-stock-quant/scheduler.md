# Companion — Scheduler

This companion is part of `SPEC-a-stock-quant` (CAP-7). Locked in T5 ("apscheduler 或系统 cron 触发 Streamlit 外部写 DB").

## Hard rule

The 15:30 trigger **must not** run inside the Streamlit process. Reasons:

- Streamlit's run loop is a single asyncio worker; a long-running backtest or strategy run inside it freezes the UI.
- Restarting the Streamlit process to deploy a new strategy must not lose a scheduled trigger.

Implementation in MVP:

- A separate `python -m runner.scheduled_run` script (or systemd service / cron entry).
- The runner reads "active strategy" from a single-row config table, calls `DataAdapter.get_calendar` to confirm today's a trading day, instantiates the strategy with persisted `params.json`, runs `generate()`, then writes one `strategy_suggestions` row per (symbol, action) pair.
- Concurrency with the UI is fine: SQLite WAL mode allows the runner to write while Streamlit reads.

## Suggested file layout

```
runner/
  __init__.py
  scheduled_run.py     ← entry point; argparse {--strategy NAME}
  __main__.py          ← optional: `python -m runner`
```

Cron entry (sample, left as the operator's responsibility):

```
30 15 * * 1-5 cd /opt/app && /opt/app/.venv/bin/python -m runner.scheduled_run >> /var/log/runner.log 2>&1
```

(`1-5` = Mon–Fri; operator may swap to a China-trading-calendar-aware wrapper later.)

## Configuration: which strategy is "active"

Add a one-row config table:

```sql
CREATE TABLE IF NOT EXISTS config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO config (key, value) VALUES ('active_strategy_id', '');
```

- UI on the Strategy page: "set as active" button → writes this row.
- The runner reads it on each fire.
- Empty `active_strategy_id` → runner no-ops and logs "no active strategy".

## Failure-mode contract (CAP-7 success)

Failure modes the runner must **not** crash on:

| Failure | Behavior |
|---|---|
| No active strategy | Log info; exit 0 |
| AKShare fetch fails | Retry with exponential backoff (3 attempts, 30s/120s/300s); if all fail, write a single sentinel row to `strategy_suggestions(reason='data_unavailable')` and exit non-zero |
| Strategy raises in `generate()` | Catch; write one sentinel row (`reason='strategy_exception', error=...`); exit non-zero |
| DB write fails | Log + exit non-zero (do **not** swallow) |

CAP-7's success is "new rows appear within 5 minutes" — see SPEC. The "data delay" badge mechanism (data-adapter.md) applies here too: a sentinel row triggers the badge.

## Trading-day awareness

The runner calls `DataAdapter.get_calendar(today, today)`. If today is not in the returned list (weekend / holiday), exit 0 with an info log. This is preferable to `1-5` cron days because A-share holidays do not match Western weekends.

## What the scheduler does NOT do (out of scope)

- Auto-apply suggestions to virtual books. That is a UI action.
- Equity / market-value recompute after suggestion application. UI does that on render.
- Send notifications outside the app (no email / webhook in MVP — see Open Question #6 in SPEC.md).
- Health-check loop / watchdog. systemd or the operator's monitoring owns that.
