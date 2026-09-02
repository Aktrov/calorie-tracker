#!/usr/bin/env bash
# Launches calorie-tracker inside a detached tmux session and records the PIDs
# of everything it starts so cleanup.sh can tear it all down.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="calorie-tracker"
PIDFILE="$PROJECT_DIR/.calorie-tracker.pid"
PORT=5200

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Session '$SESSION_NAME' is already running. Attach with:"
    echo "  tmux attach -t $SESSION_NAME"
    exit 1
fi

if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
    echo "venv not found at $PROJECT_DIR/venv. Create it first with:"
    echo "  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

tmux new-session -d -s "$SESSION_NAME" -c "$PROJECT_DIR" \
    "SCRIPT_NAME=/calorie-tracker $PROJECT_DIR/venv/bin/python $PROJECT_DIR/app.py"

# Give the process a moment to spawn before we snapshot PIDs.
sleep 1

collect_pids() {
    local root="$1"
    echo "$root"
    for child in $(pgrep -P "$root" 2>/dev/null || true); do
        collect_pids "$child"
    done
}

PANE_PID="$(tmux list-panes -t "$SESSION_NAME" -F '#{pane_pid}')"
collect_pids "$PANE_PID" > "$PIDFILE"

echo "Started '$SESSION_NAME' in tmux, serving on http://127.0.0.1:$PORT"
echo "PIDs tracked in $PIDFILE:"
cat "$PIDFILE"
echo
echo "Attach with:  tmux attach -t $SESSION_NAME"
echo "Detach with:  Ctrl-b then d"
echo "Stop with:    ./cleanup.sh"
