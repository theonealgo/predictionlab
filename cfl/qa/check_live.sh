#!/bin/bash
# Audit the LIVE site only. Does not start a local server.
# Usage: bash qa/check_live.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export AUDIT_BASE_URL="https://predictionlab.io"
echo "Auditing LIVE site: $AUDIT_BASE_URL"
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi
# Chrome first: actually GET every Sports / Results / Blog / Affiliate URL.
# Then ship + soccer. Sequential so the live single worker is not hammered in parallel.
exec "$PY" qa/site_checker.py --ship --url https://predictionlab.io
