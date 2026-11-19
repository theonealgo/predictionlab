#!/usr/bin/env bash
set -e

# ── Copy database to persistent disk on first deploy ──────────────────────────
if [ ! -f /data/sports_predictions_original.db ]; then
    echo "[render_start] Initializing database on persistent disk..."
    cp sports_predictions_original.db /data/sports_predictions_original.db
else
    echo "[render_start] Database already on persistent disk."
fi

# Served picks/results HTML must live here — the app directory is wiped on
# every deploy. Prediction slates already use /data/.cache/predictions.
mkdir -p /data/.cache
# Master boot clock for PL_BOOT_GRACE_S (gunicorn.conf.py also writes this).
date +%s > /data/.cache/gunicorn_boot_at
_saved=$(ls /data/.cache/served_*.html 2>/dev/null | wc -l | tr -d ' ')
echo "[render_start] served-page cache /data/.cache (${_saved} saved page(s))"

# Drop wipe-poisoned NHL/NBA results HTML (0-game Last Night + stale Last 7)
# and the NHL last-good file that was re-saving that poison. Autorefresh reseeds.
python3 - <<'PY' || true
from pathlib import Path
import re
root = Path("/data/.cache")
last_good = Path("/data/nhl_results_last_good.html")
pat_ln = re.compile(
    r"Last Night's \w+ Results\s*[—–-]\s*(\d{4}-\d{2}-\d{2})\s*\((\d+)\s+games?\)",
    re.I,
)
pat_l7 = re.compile(
    r"Last 7 Days \w+ Results\s*[—–-]\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
    re.I,
)

def poisoned(text: str) -> bool:
    ln, l7 = pat_ln.search(text or ""), pat_l7.search(text or "")
    if not ln or int(ln.group(2) or 0) != 0:
        return False
    if not l7:
        return True
    try:
        from datetime import datetime as dt
        return dt.strptime(l7.group(2), "%Y-%m-%d") < dt.strptime(ln.group(1), "%Y-%m-%d")
    except Exception:
        return True

removed = 0
for path in list(root.glob("served_NHL*.html")) + list(root.glob("served_NBA*.html")):
    try:
        if poisoned(path.read_text(encoding="utf-8", errors="ignore")):
            path.unlink(missing_ok=True)
            removed += 1
            print(f"[render_start] removed poisoned {path.name}")
    except Exception as exc:
        print(f"[render_start] skip {path.name}: {exc}")
if last_good.is_file():
    try:
        if poisoned(last_good.read_text(encoding="utf-8", errors="ignore")):
            last_good.unlink(missing_ok=True)
            removed += 1
            print("[render_start] removed poisoned nhl_results_last_good.html")
    except Exception as exc:
        print(f"[render_start] skip last_good: {exc}")
print(f"[render_start] poisoned-page purge done ({removed} removed)")
PY

# ── Memory: 1 CPU / 2 GB instance (see gunicorn.conf.py) ────────────────────
export MALLOC_ARENA_MAX=2
export MALLOC_MMAP_THRESHOLD_=131072
export MALLOC_TRIM_THRESHOLD_=131072
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
# One background page rebuild at a time on Render (each can be hundreds of MB).
export PL_BUILD_SLOTS="${PL_BUILD_SLOTS:-1}"
# Defer score sync / tennis ESPN for a few minutes so / and cards answer first.
export PL_BOOT_GRACE_S="${PL_BOOT_GRACE_S:-180}"
export PL_SCORE_SYNC_SLOTS="${PL_SCORE_SYNC_SLOTS:-1}"
export PL_TENNIS_SYNC_S="${PL_TENNIS_SYNC_S:-1800}"
export GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
export GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-180}"
# Worker recycle — see gunicorn.conf.py max_requests (override via env if needed).
export GUNICORN_MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-400}"
export GUNICORN_MAX_REQUESTS_JITTER="${GUNICORN_MAX_REQUESTS_JITTER:-50}"
# Props forks eat the only worker (WORKER TIMEOUT in logs). Off until stable;
# set PL_PROP_PUBLICATION=1 in the Render dashboard when ready to turn back on.
export PL_PROP_PUBLICATION="${PL_PROP_PUBLICATION:-0}"
export PL_PROPS_START_DELAY="${PL_PROPS_START_DELAY:-1800}"
export PL_PROPS_JOB_TIMEOUT="${PL_PROPS_JOB_TIMEOUT:-90}"

# ── Launch Flask app via gunicorn (optional Datadog APM via ddtrace-run) ───────
if [ -n "${DD_API_KEY:-}" ] || [ "${DD_TRACE_ENABLED:-}" = "true" ]; then
    echo "[render_start] Datadog tracing enabled (DD_SERVICE=${DD_SERVICE:-predictionlab})"
    exec ddtrace-run gunicorn -c gunicorn.conf.py NHL77FINAL:app
fi
exec gunicorn -c gunicorn.conf.py NHL77FINAL:app
