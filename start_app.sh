#!/bin/bash
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.predictionlab.pid"
LOG_FILE="$APP_DIR/app.log"
PORT="${PORT:-5002}"

cd "$APP_DIR" || exit 1

is_running() {
    [ -f "$PID_FILE" ] || return 1
    PID="$(cat "$PID_FILE" 2>/dev/null)"
    [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null
}

if is_running; then
    echo "Prediction Lab is already running (PID $(cat "$PID_FILE"))."
    echo "Open http://127.0.0.1:$PORT/"
    exit 0
fi

rm -f "$PID_FILE"
touch "$LOG_FILE"

PORT="$PORT" nohup python3 app.py >>"$LOG_FILE" 2>&1 </dev/null &
APP_PID=$!
echo "$APP_PID" >"$PID_FILE"

for _ in $(seq 1 90); do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        echo "Prediction Lab stopped during startup. See $LOG_FILE"
        tail -40 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
    if curl --silent --fail --max-time 2 "http://127.0.0.1:$PORT/" >/dev/null; then
        echo "Prediction Lab is running (PID $APP_PID)."
        echo "Open http://127.0.0.1:$PORT/"
        echo "Logs: $LOG_FILE"
        exit 0
    fi
    sleep 1
done

echo "Prediction Lab did not become ready in 90 seconds. See $LOG_FILE"
exit 1
