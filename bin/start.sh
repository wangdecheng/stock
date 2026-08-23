#!/bin/bash
# bin/start.sh — daemon control for Streamlit UI + 15:00 scheduler
#
# Subcommands:
#   start    Launch both processes in the background (idempotent).
#   stop     SIGTERM both, SIGKILL after 5s if still alive.
#   restart  stop + start.
#   status   Print whether each process is alive and its PID.
#   logs     Tail both logs (Ctrl+C to stop tailing).
#
# Process model: scheduler and streamlit are SEPARATE background processes
# (CAP-7 — runner must not be imported inside the Streamlit process). Each
# gets its own PID file under logs/. Log output goes to logs/scheduler.log
# and logs/streamlit.log respectively.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Load project-level env (gitignored). Must come before venv activation
# so AKSHARE_DIRECT etc. propagate to daemonized children.
[ -f "$ROOT/.env" ] && source "$ROOT/.env"

VENV="$ROOT/.venv/bin/activate"
LOG_DIR="$ROOT/logs"
SCHED_PID_FILE="$LOG_DIR/scheduler.pid"
STREAMLIT_PID_FILE="$LOG_DIR/streamlit.pid"
SCHED_LOG="$LOG_DIR/scheduler.log"
STREAMLIT_LOG="$LOG_DIR/streamlit.log"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"
STOP_TIMEOUT=5

die() { echo "ERROR: $*" >&2; exit 1; }

# --- Pre-flight -------------------------------------------------------------

require_venv() {
    if [ ! -f "$VENV" ]; then
        die ".venv not found. Run:
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt"
    fi
}

# status / logs / stop only need the log dir; no venv required.
# Unknown subcommands intentionally fall through to the dispatch case
# below so they get a clean "Usage:" line instead of a venv error.
case "${1:-start}" in
    status|logs|stop) ;;
    start|restart) require_venv ;;
esac

mkdir -p "$LOG_DIR"

# --- Helpers ----------------------------------------------------------------

is_alive() {
    local pidfile=$1
    [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

read_pid() {
    local pidfile=$1
    [ -f "$pidfile" ] && cat "$pidfile" || echo "-"
}

kill_pidfile() {
    local pidfile=$1 label=$2
    if [ ! -f "$pidfile" ]; then return 0; fi
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
        echo "stopping $label (pid $pid)..."
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 1 "$STOP_TIMEOUT"); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "$label did not exit after ${STOP_TIMEOUT}s; sending SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
    rm -f "$pidfile"
}

# --- Subcommands ------------------------------------------------------------

cmd_start() {
    # shellcheck disable=SC1090
    source "$VENV"

    local PYTHON_BIN
    # $VENV is .../.venv/bin/activate, so dirname is .../.venv/bin;
    # append /python (NOT /bin/python — that would give .../.venv/bin/bin/python).
    PYTHON_BIN="$(cd "$(dirname "$VENV")" && pwd)/python"
    local started=0

    if is_alive "$SCHED_PID_FILE"; then
        echo "scheduler already running (pid $(read_pid "$SCHED_PID_FILE"))"
    else
        rm -f "$SCHED_PID_FILE"
        # FileHandler owns $SCHED_LOG; stdout goes to /dev/null to avoid duplicates.
        nohup "$PYTHON_BIN" -m framework.runner.scheduler \
            --log-dir "$LOG_DIR" \
            --log-file "$SCHED_LOG" \
            > /dev/null 2>&1 &
        echo $! > "$SCHED_PID_FILE"
        echo "scheduler started (pid $(read_pid "$SCHED_PID_FILE"))"
        started=1
    fi

    if is_alive "$STREAMLIT_PID_FILE"; then
        echo "streamlit already running (pid $(read_pid "$STREAMLIT_PID_FILE"))"
    else
        rm -f "$STREAMLIT_PID_FILE"
        nohup streamlit run app.py \
            --server.port "$STREAMLIT_PORT" \
            --server.headless true \
            >> "$STREAMLIT_LOG" 2>&1 &
        echo $! > "$STREAMLIT_PID_FILE"
        echo "streamlit started (pid $(read_pid "$STREAMLIT_PID_FILE")) on port $STREAMLIT_PORT"
        started=1
    fi

    if [ "$started" -eq 1 ]; then
        echo ""
        echo "UI:    http://localhost:$STREAMLIT_PORT"
        echo "logs:  $LOG_DIR/{scheduler,streamlit}.log"
    fi
}

cmd_stop() {
    kill_pidfile "$STREAMLIT_PID_FILE" streamlit
    kill_pidfile "$SCHED_PID_FILE" scheduler
    echo "stopped."
}

cmd_status() {
    if is_alive "$SCHED_PID_FILE"; then
        echo "scheduler: running (pid $(read_pid "$SCHED_PID_FILE"))"
    else
        echo "scheduler: not running"
    fi
    if is_alive "$STREAMLIT_PID_FILE"; then
        echo "streamlit: running (pid $(read_pid "$STREAMLIT_PID_FILE"))"
    else
        echo "streamlit: not running"
    fi
}

cmd_logs() {
    if [ ! -f "$SCHED_LOG" ] && [ ! -f "$STREAMLIT_LOG" ]; then
        echo "no log files yet"
        return 0
    fi
    tail -F "$SCHED_LOG" "$STREAMLIT_LOG"
}

# --- Entrypoint -------------------------------------------------------------

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; cmd_start ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}" >&2
        exit 2
        ;;
esac
