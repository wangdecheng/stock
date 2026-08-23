"""framework/runner/scheduler.py

Long-running daemon that invokes ``framework.runner.scheduled_run`` once per
trading day at 15:00 (A-share close). Stands in for cron / systemd for users
who don't want to configure the system scheduler.

Hard rule: launches the runner as a subprocess (never imports it). Mirrors
``framework.runner.__init__``'s empty-imports policy — importing the runner
would drag ``AKShareAdapter`` (and the requests/pandas dependency chain)
into a process that should stay lightweight.

Public surface
--------------
* :class:`SchedulerConfig` — frozen dataclass for the few knobs.
* :func:`is_trading_day` — MVP Mon-Fri heuristic. Holiday calendar is out of
  scope per the spec; replace with an akshare-backed calendar when one
  ships.
* :func:`has_run_today` / :func:`mark_ran_today` — idempotency state.
* :func:`run_forever` — main loop. Blocks until SIGTERM/SIGINT.
* :func:`main` — argparse CLI; ``python -m framework.runner.scheduler``.

Idempotency
-----------
A single ``<log_dir>/.scheduler_last_run`` file holds the ISO date of the
last fire. Re-running on the same calendar day is a no-op. This makes the
daemon safe to restart at 15:01 — it will not double-fire.

Process model
-------------
The daemon writes its PID to ``<log_dir>/scheduler.pid`` on startup and
deletes it on shutdown. ``bin/start.sh`` uses this file for ``start/stop/
status``. SIGTERM triggers a clean exit; the loop checks the flag between
sleeps, so the worst-case shutdown latency is ``--interval`` seconds.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path

TARGET_TIME = dtime(15, 0)
DEFAULT_INTERVAL_SECONDS = 30
PID_FILENAME = "scheduler.pid"
STATE_FILENAME = ".scheduler_last_run"

_log = logging.getLogger("framework.runner.scheduler")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchedulerConfig:
    log_dir: Path
    project_root: Path
    target_time: dtime = TARGET_TIME
    check_interval: int = DEFAULT_INTERVAL_SECONDS

    @property
    def state_file(self) -> Path:
        return self.log_dir / STATE_FILENAME

    @property
    def pid_file(self) -> Path:
        return self.log_dir / PID_FILENAME

    @property
    def log_file(self) -> Path:
        return self.log_dir / "scheduler.log"


# ---------------------------------------------------------------------------
# Trading-day heuristic
# ---------------------------------------------------------------------------


def is_trading_day(today: date) -> bool:
    """Mon-Fri only for MVP. ``weekday()`` is 0=Mon ... 6=Sun."""
    return today.weekday() < 5


# ---------------------------------------------------------------------------
# Idempotency state
# ---------------------------------------------------------------------------


def has_run_today(state_file: Path) -> bool:
    if not state_file.exists():
        return False
    raw = state_file.read_text().strip()
    try:
        return date.fromisoformat(raw) == date.today()
    except ValueError:
        # Corrupt state — treat as "has not run" so the next fire still
        # happens rather than silently skipping.
        return False


def mark_ran_today(state_file: Path) -> None:
    state_file.write_text(date.today().isoformat() + "\n")


# ---------------------------------------------------------------------------
# Subprocess dispatch
# ---------------------------------------------------------------------------


def run_runner(project_root: Path) -> int:
    """Invoke ``python -m framework.runner`` and stream output. Returns exit code."""
    _log.info("triggering runner at %s", datetime.now().isoformat(timespec="seconds"))
    result = subprocess.run(
        [sys.executable, "-m", "framework.runner"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if result.stdout:
        _log.info("runner stdout:\n%s", result.stdout.rstrip())
    if result.stderr:
        _log.warning("runner stderr:\n%s", result.stderr.rstrip())
    _log.info("runner exited with code %s", result.returncode)
    return result.returncode


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_forever(config: SchedulerConfig) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.pid_file.write_text(str(os.getpid()))

    # Use threading.Event for sleep so SIGTERM exits within milliseconds
    # instead of waiting out the full check_interval (default 30s).
    # time.sleep() does NOT get interrupted by Python signal handlers.
    stop_event = threading.Event()

    def _request_stop(signum: int, _frame) -> None:
        _log.info("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    _log.info("scheduler started; pid=%s target=%s interval=%ss log_dir=%s",
              os.getpid(), config.target_time, config.check_interval, config.log_dir)

    try:
        while not stop_event.is_set():
            now = datetime.now()
            if (is_trading_day(now.date())
                    and now.time() >= config.target_time
                    and not has_run_today(config.state_file)):
                run_runner(config.project_root)
                mark_ran_today(config.state_file)
            # event.wait is interruptible by stop_event.set() from the
            # signal handler. Returns True if the event was set during
            # the wait, False on timeout — either way we re-check the
            # loop condition.
            stop_event.wait(config.check_interval)
    finally:
        try:
            config.pid_file.unlink()
        except FileNotFoundError:
            pass
        _log.info("scheduler exited")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _configure_logging(log_file: Path | None) -> None:
    """Configure root logger.

    When ``log_file`` is given, write only to that file. When it is None,
    write only to stdout. Mixing both (e.g. FileHandler + stdout tee'd to
    the same file) duplicates every line, so we pick exactly one sink.
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    if log_file is None:
        logger.addHandler(logging.StreamHandler(sys.stdout))
        return
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.addHandler(logging.FileHandler(log_file))


def _parse_hhmm(raw: str) -> dtime:
    h, m = raw.split(":")
    return dtime(int(h), int(m))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A-share 15:00 scheduler daemon (trading-day MVP, no holiday calendar).",
    )
    parser.add_argument("--log-dir", type=Path, default=Path("logs"),
                        help="Directory for log, state, and pid files (default: ./logs).")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="If set, also write logs to this file in addition to stdout.")
    parser.add_argument("--target-time", default="15:00",
                        help="Fire time in HH:MM (default: 15:00).")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                        help=f"Wake interval in seconds (default: {DEFAULT_INTERVAL_SECONDS}).")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    config = SchedulerConfig(
        log_dir=args.log_dir.resolve(),
        project_root=project_root,
        target_time=_parse_hhmm(args.target_time),
        check_interval=args.interval,
    )
    _configure_logging(args.log_file or config.log_file)
    run_forever(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
