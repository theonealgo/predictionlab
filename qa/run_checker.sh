#!/bin/bash
# PredictionLab Site Checker — daily runner
# Run manually:   bash qa/run_checker.sh
# Runs automatically via launchd (see setup below)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
LOG="$SCRIPT_DIR/checker_cron.log"
# Audit targets port 5003 (where the app normally runs). If your own server is
# already up on 5003, the checker just uses it; otherwise we start one.
PORT_NUM=5003
export AUDIT_BASE_URL="${AUDIT_BASE_URL:-http://127.0.0.1:${PORT_NUM}}"

echo "=== Site Checker run: $(date) ===" >> "$LOG"
cd "$PROJECT_DIR"

# Wake/launch Mail.app first so it's warm before we try to send (avoids the
# 8am cold-start timeout that silently dropped a scheduled email).
open -a Mail 2>/dev/null
sleep 8

# Ensure the Flask app is up. NHL77FINAL loads ~6 ML model bundles on boot,
# which can take well over a minute — so wait up to ~3 minutes.
STARTED_BY_US=0
if ! curl -s -o /dev/null --max-time 8 "$AUDIT_BASE_URL/healthz"; then
    echo "Server not responding on ${PORT_NUM} — starting it…" >> "$LOG"
    PORT=$PORT_NUM "$PYTHON" NHL77FINAL.py >> "$LOG" 2>&1 &
    STARTED_PID=$!
    STARTED_BY_US=1
    UP=0
    for i in $(seq 1 60); do          # 60 × 3s = up to 180s
        sleep 3
        if curl -s -o /dev/null --max-time 5 "$AUDIT_BASE_URL/healthz"; then
            UP=1; echo "Server came up after $((i*3))s" >> "$LOG"; break
        fi
    done
    if [ "$UP" != "1" ]; then
        echo "Server FAILED to come up within 180s — audit will report preflight fail" >> "$LOG"
    fi
fi

"$PYTHON" qa/site_checker.py --full --email >> "$LOG" 2>&1
EXIT=$?

# If we started the server just for the audit, shut it down to leave a clean state.
if [ "$STARTED_BY_US" = "1" ] && [ -n "$STARTED_PID" ]; then
    kill "$STARTED_PID" 2>/dev/null
fi
echo "=== Done: exit $EXIT ===" >> "$LOG"
