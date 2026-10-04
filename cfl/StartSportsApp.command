#!/bin/bash
# Underdogs.bet local launcher.
# Double-click this file to run the app. It will:
#   1. Kill any existing NHL77FINAL.py instances (no zombies, no port bumping)
#   2. Wipe the .pyc cache so old code can't sneak back in
#   3. Start the server on port 5050 in this folder
#   4. Open the home page in your default browser
#
# Folder is locked to the (sandbox 04:18:2026) copy directory — the one
# Cowork is editing. If you rename the folder, update the cd path below.

cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (sandbox 04:18:2026) copy" || {
    echo "❌ Could not cd into the project folder. Was it renamed or moved?"
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
}

echo "📂 Folder: $(pwd)"

# 1. Kill anything still running
echo "🧹 Stopping old NHL77FINAL processes..."
pkill -9 -f NHL77FINAL.py >/dev/null 2>&1
sleep 1
lsof -ti:5050 | xargs kill -9 >/dev/null 2>&1

# 2. Clear stale .pyc cache
echo "🧹 Clearing Python cache..."
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null

# 3. Start server
echo "🚀 Starting on port 5050..."
PORT=5050 nohup python3 NHL77FINAL.py > app.log 2>&1 &
SERVER_PID=$!
echo "   Server PID: $SERVER_PID"

# 4. Wait for boot, then open browser
echo "⏳ Waiting for server to come up..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null http://127.0.0.1:5050/ ; then
        echo "✅ Server is up after ${i}s"
        break
    fi
    sleep 1
done

echo ""
echo "🌐 Opening http://127.0.0.1:5050/ in your browser..."
open "http://127.0.0.1:5050/"

echo ""
echo "─────────────────────────────────────────────────────────"
echo "  Server is running. Logs streaming to: app.log"
echo "  To stop the server:  pkill -9 -f NHL77FINAL.py"
echo "  Tail logs in real time:  tail -f app.log"
echo "─────────────────────────────────────────────────────────"
