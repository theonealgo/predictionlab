#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.predictionlab.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Prediction Lab is not running."
    exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null)"
if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 20); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.25
    done
fi

rm -f "$PID_FILE"
echo "Prediction Lab stopped."
