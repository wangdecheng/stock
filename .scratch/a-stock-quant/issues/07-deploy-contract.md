# 07 — Deploy contract: .gitignore, README, reverse-proxy + crontab snippets, smoke test

**What to build:** A repo that another operator can bring up with one Streamlit command + paste-in config snippets, following the README. No actual deployment is performed in this ticket — the operator runs the snippets on their own cloud server, per the effort boundary.

**Blocked by:** 06 — all preceding artifacts need to exist before a sensible deploy contract can be documented.

**Status:** ready-for-agent

- [ ] `.gitignore` at the repo root covers: `strategies/*.state.json`, `strategies/*.params.json`, `data/app.db`, `data/cache/`, `data/backtests/`, `.env`, `__pycache__/`, `.venv/`, `.mypy_cache/`, `.pytest_cache/`.
- [ ] `requirements.txt` (or `pyproject.toml`) pins (or sets lower bounds): `akshare`, `pandas-ta`, `empyrical`, `pyecharts==2.0.9`, `streamlit-echarts[pyecharts]`, `streamlit>=1.30`, plus transitive deps that surfaced in tickets 01-06 (empyrical replaced where appropriate, etc.).
- [ ] `README.md` documents:
  - one-line start command: `streamlit run app.py --server.port 8501 --server.address 0.0.0.0`
  - directory layout (matches `architecture-diagrams.md`)
  - sample Nginx basic-auth + reverse-proxy snippet that fronts Streamlit on 8501 with HTTPS termination
  - sample Caddy equivalent
  - sample crontab entry for the 15:30 trigger (per `scheduler.md`)
  - "故障排查" quick-ref pointing at common exit codes + the "数据延迟" badge + the sentinel-row query `SELECT * FROM strategy_suggestions WHERE reason LIKE 'data_%' OR reason LIKE 'strategy_%' ORDER BY generated_at DESC LIMIT 10;`
- [ ] `scripts/smoke.py` boots the Streamlit app in a subprocess, visits each of the 5 pages via the multipage URLs, asserts no unhandled exception, and verifies the deploy README's directory layout matches reality (every README-named path exists, nothing README-named is missing).
- [ ] Smoke test exits 0 against a fixture that uses a stubbed `DataAdapter` returning canned bars + a known strategy in `strategies/`.
- [ ] No ticket in this effort deploys anything to a real server; the smoke test is the artifact that proves deploy-readiness.

## References (read alongside this ticket)

- `specs/spec-a-stock-quant/SPEC.md` — CAP-8 + Constraints 14-15 (single-process deploy + reverse-proxy auth)
- `specs/spec-a-stock-quant/architecture-diagrams.md` § File layout — canonical paths the README must match
- `specs/spec-a-stock-quant/scheduler.md` — crontab sample the README quotes
- `specs/spec-a-stock-quant/data-adapter.md` — rate-limit and cache behaviors referenced in "故障排查"
- Handoff boundary: cloud server provisioning + Nginx + TLS + crontab wiring are the operator's responsibility, NOT in this effort (T7/T8/T9 wayfinder tickets out-of-scope)
