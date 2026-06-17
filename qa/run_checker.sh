#!/bin/bash
# PredictionLab Site Checker — daily runner
# Run manually:   bash qa/run_checker.sh
# Runs automatically via launchd (see setup below)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
LOG="$SCRIPT_DIR/checker_cron.log"
# Audit the LIVE production site by default. Override with AUDIT_BASE_URL to point
# at a local dev server, e.g. AUDIT_BASE_URL=http://127.0.0.1:5003 bash qa/run_checker.sh
export AUDIT_BASE_URL="${AUDIT_BASE_URL:-https://predictionlab.io}"

echo "=== Site Checker run: $(date) — target ${AUDIT_BASE_URL} ===" >> "$LOG"
cd "$PROJECT_DIR"

# Wake/launch Mail.app first so it is ready before the nightly report is sent.
open -a Mail 2>/dev/null
sleep 8

# Only spin up a local Flask server when the target IS localhost. Against the
# production URL we just audit the live site directly (no local server needed).
STARTED_BY_US=0
STARTED_PID=""
case "$AUDIT_BASE_URL" in
    *127.0.0.1*|*localhost*)
        PORT_NUM="$(echo "$AUDIT_BASE_URL" | sed -n 's/.*:\([0-9][0-9]*\).*/\1/p')"
        PORT_NUM="${PORT_NUM:-5003}"
        if ! curl -s -o /dev/null --max-time 8 "$AUDIT_BASE_URL/healthz"; then
            echo "Local server not up on ${PORT_NUM} — starting it…" >> "$LOG"
            PORT=$PORT_NUM "$PYTHON" NHL77FINAL.py >> "$LOG" 2>&1 &
            STARTED_PID=$!
            STARTED_BY_US=1
            for i in $(seq 1 60); do          # up to 180s for model bundles to load
                sleep 3
                curl -s -o /dev/null --max-time 5 "$AUDIT_BASE_URL/healthz" && { echo "Server up after $((i*3))s" >> "$LOG"; break; }
            done
        fi
        ;;
    *)
        echo "Auditing remote target (no local server started)." >> "$LOG"
        ;;
esac

"$PYTHON" qa/site_checker.py --full --email >> "$LOG" 2>&1
EXIT=$?

# If we started a local server just for the audit, shut it down again.
if [ "$STARTED_BY_US" = "1" ] && [ -n "$STARTED_PID" ]; then
    kill "$STARTED_PID" 2>/dev/null
fi
echo "=== Done: exit $EXIT ===" >> "$LOG"
