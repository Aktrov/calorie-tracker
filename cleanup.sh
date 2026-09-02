#!/usr/bin/env bash
# Tears down calorie-tracker: kills the tmux session, kills every PID run.sh
# recorded, and as a final fallback kills whatever is still bound to the port.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_NAME="calorie-tracker"
PIDFILE="$PROJECT_DIR/.calorie-tracker.pid"
PORT=5200

did_something=0

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Killing tmux session '$SESSION_NAME'..."
    tmux kill-session -t "$SESSION_NAME"
    did_something=1
fi

if [ -f "$PIDFILE" ]; then
    echo "Killing PIDs from $PIDFILE..."
    while read -r pid; do
        [ -z "$pid" ] && continue
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
            did_something=1
        fi
    done < "$PIDFILE"

    sleep 1

    while read -r pid; do
        [ -z "$pid" ] && continue
        if kill -0 "$pid" 2>/dev/null; then
            echo "  PID $pid still alive, sending SIGKILL"
            kill -KILL "$pid" 2>/dev/null
        fi
    done < "$PIDFILE"

    rm -f "$PIDFILE"
fi

port_pids="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
if [ -n "$port_pids" ]; then
    echo "Killing stragglers still bound to port $PORT: $port_pids"
    kill -KILL $port_pids 2>/dev/null || true
    did_something=1
fi

if [ "$did_something" -eq 0 ]; then
    echo "Nothing to clean up: no tmux session, no tracked PIDs, port $PORT is free."
else
    echo "Cleanup complete."
fi
