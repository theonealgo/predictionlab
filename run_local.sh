#!/bin/bash
# Local test server: stops any old copy, starts a fresh one with the current code.
# Usage: ./run_local.sh      then open http://127.0.0.1:5122
# Stop it with Ctrl+C.
cd "$(dirname "$0")" || exit 1
PORT=5122

old=$(lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null)
pkill -f NHL77FINAL.py 2>/dev/null
[ -n "$old" ] && kill $old 2>/dev/null
for _ in $(seq 1 20); do
  lsof -tiTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -tiTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  kill -9 $(lsof -tiTCP:$PORT -sTCP:LISTEN) 2>/dev/null
  sleep 1
fi

echo "Starting fresh local server ($(date '+%a %b %-d %-I:%M %p')) ..."
PORT=$PORT .venv/bin/python NHL77FINAL.py > /tmp/pl_local.log 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; echo; echo "Server stopped."; exit 0' INT TERM

for _ in $(seq 1 180); do
  if ! kill -0 $pid 2>/dev/null; then
    echo "Server failed to start. Last lines of the log:"
    tail -20 /tmp/pl_local.log
    exit 1
  fi
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/robots.txt"; then
    echo
    echo "READY: http://127.0.0.1:$PORT   (started $(date '+%-I:%M %p'), latest code)"
    echo "Leave this window open. Press Ctrl+C to stop. Log: /tmp/pl_local.log"
    wait $pid
    exit 0
  fi
  sleep 1
done
echo "Server is still loading after 3 minutes. Log: /tmp/pl_local.log"
wait $pid
