#!/bin/bash
# bin/start-dev.sh — foreground dev launcher: scheduler (bg) + Streamlit (fg)
#
# Use this for local development when you want both processes in one terminal.
# For production / "leave it running" use bin/start.sh (daemon mode).
#
# Layout:
#   - scheduler runs in the background, logs to logs/scheduler.log
#   - streamlit runs in the foreground, you see its output directly
#   - Ctrl+C kills streamlit; the trap also kills the scheduler so neither
#     process is left orphaned

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Load project-level env (gitignored). Must come before venv activation
# so AKSHARE_DIRECT etc. propagate to both processes.
[ -f "$ROOT/.env" ] && source "$ROOT/.env"

VENV="$ROOT/.venv/bin/activate"
LOG_DIR="$ROOT/logs"
SCHED_LOG="$LOG_DIR/scheduler.log"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

if [ ! -f "$VENV" ]; then
    echo "ERROR: .venv not found. Run:" >&2
    echo "    python3.11 -m venv .venv" >&2
    echo "    source .venv/bin/activate" >&2
    echo "    pip install -r requirements.txt" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"

# shellcheck disable=SC1090
source "$VENV"

PYTHON_BIN="$(cd "$(dirname "$VENV")" && pwd)/bin/python"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Launch scheduler in the background. FileHandler writes to $SCHED_LOG
# directly, so we drop stdout to /dev/null (no `>> $SCHED_LOG` here — that
# would duplicate every line).
"$PYTHON_BIN" -m framework.runner.scheduler \
    --log-dir "$LOG_DIR" \
    --log-file "$SCHED_LOG" \
    > /dev/null 2>&1 &
SCHED_PID=$!

cleanup() {
    echo ""
    echo "shutting down..."
    if kill -0 "$SCHED_PID" 2>/dev/null; then
        kill "$SCHED_PID" 2>/dev/null || true
        # Give it up to 5s to exit cleanly.
        for _ in 1 2 3 4 5; do
            kill -0 "$SCHED_PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$SCHED_PID" 2>/dev/null || true
    fi
}
trap cleanup INT TERM EXIT

echo "scheduler started (pid $SCHED_PID, log: $SCHED_LOG)"
echo "starting streamlit on port $STREAMLIT_PORT (Ctrl+C to stop both)..."
echo ""

# Foreground: streamlit blocks here. The trap above runs on exit.
exec streamlit run app.py \
    --server.port "$STREAMLIT_PORT" \
    --server.headless false
