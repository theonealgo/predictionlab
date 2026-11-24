#!/usr/bin/env bash
set -e

# ── Copy / repair database on persistent disk ────────────────────────────────
# A crash mid-write can leave /data/...db present but not SQLite ("file is not a
# database"). That crash-loops every deploy and blocks Render Shell. Detect the
# header; quarantine the bad file; re-seed from the image copy so gunicorn can
# bind. Quarantined copy kept for manual restore of users/predictions later.
_DB=/data/sports_predictions_original.db
_SEED="${RENDER_DB_SEED:-sports_predictions_original.db}"
_db_header_ok() {
    python3 - "$1" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
sys.exit(0 if p.is_file() and p.read_bytes()[:16] == b"SQLite format 3\x00" else 1)
PY
}

if [ ! -f "$_DB" ]; then
    echo "[render_start] Initializing database on persistent disk..."
    cp "$_SEED" "$_DB"
elif ! _db_header_ok "$_DB"; then
    _quarantine="/data/sports_predictions_original.db.corrupt.$(date +%s)"
    echo "[render_start] ERROR: $_DB is not a SQLite database — quarantining to $_quarantine"
    mv "$_DB" "$_quarantine"
    # Drop orphan WAL/SHM that can keep a bad connection state.
    rm -f /data/sports_predictions_original.db-wal /data/sports_predictions_original.db-shm
    cp "$_SEED" "$_DB"
    echo "[render_start] Re-seeded $_DB from $_SEED (login/subscription rows may need restore from quarantine)."
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

# Warm NCAAW / UFC React card feeds on boot. A cold /data volume + PL_WEB_WARM=0
# left those pages on "Loading games…" (202) until a multi-minute SSR finished;
# a past-only UFC feed also filtered to zero cards under span=0/2.
python3 - <<'PY'
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import gzip
import json

seed = Path("/opt/render/project/src/data/web_cards_seed")
dest = Path("/data/.cache/web_cards")
if not seed.is_dir():
    raise SystemExit(0)
dest.mkdir(parents=True, exist_ok=True)
today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()

def has_upcoming(path: Path) -> bool:
    try:
        raw = json.loads(gzip.decompress(path.read_bytes()))
    except Exception:
        return False
    for card in raw.get("cards") or []:
        day = str((card or {}).get("date") or "")[:10]
        if day >= today and (card or {}).get("isFinal") is not True:
            return True
    return False

wrote = 0
for src in sorted(seed.glob("*.v2.json.gz")):
    out = dest / src.name
    if out.is_file() and has_upcoming(out):
        continue
    out.write_bytes(src.read_bytes())
    wrote += 1
    print(f"[render_start] seeded web_cards {src.name}", flush=True)
print(f"[render_start] web_cards seed done (wrote {wrote})", flush=True)
PY

# Drop wipe-poisoned NHL/NBA results HTML (0-game Last Night + stale Last 7)
# and the NHL last-good file that was re-saving that poison. Autorefresh reseeds.
# Run after the process is listening. A scan here used to finish before gunicorn
# bound $PORT, so Render had nothing to route to and returned 502.
python3 - >/tmp/render_purge.log 2>&1 <<'PY' &
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
    low = (text or "").lower()
    # Cold-disk "NHL results are refreshing…" stub (often with grafted Totals scraps).
    if "results are refreshing" in low or "open again in a minute" in low:
        return True
    if "refresh results page" in low and "last night's" not in low:
        return True
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
for path in (
    list(root.glob("served_NHL*.html"))
    + list(root.glob("served_NBA*.html"))
    + [root / "nhl_results_page.html"]
):
    if not path.is_file():
        continue
    try:
        if poisoned(path.read_text(encoding="utf-8", errors="ignore")):
            path.unlink(missing_ok=True)
            removed += 1
            print(f"[render_start] removed poisoned {path.name}")
    except Exception as exc:
        print(f"[render_start] skip {path.name}: {exc}")
# KEEP nhl_results_page.html when it is usable. Deleting it every deploy forced
# cold NHL rebuilds on the only worker. If the fast-path is missing/poisoned but
# last_good is fine, seed the fast-path from last_good so visitors never wait.
nhl_fast = root / "nhl_results_page.html"
if nhl_fast.is_file():
    try:
        if poisoned(nhl_fast.read_text(encoding="utf-8", errors="ignore")):
            nhl_fast.unlink(missing_ok=True)
            removed += 1
            print("[render_start] removed poisoned nhl_results_page.html")
        else:
            print("[render_start] kept nhl_results_page.html")
    except Exception as exc:
        print(f"[render_start] skip nhl_results_page: {exc}")
if last_good.is_file():
    try:
        if poisoned(last_good.read_text(encoding="utf-8", errors="ignore")):
            last_good.unlink(missing_ok=True)
            removed += 1
            print("[render_start] removed poisoned nhl_results_last_good.html")
        else:
            print("[render_start] kept nhl_results_last_good.html as serve fallback")
            if not nhl_fast.is_file():
                import shutil
                shutil.copy2(last_good, nhl_fast)
                print("[render_start] seeded nhl_results_page.html from last_good")
    except Exception as exc:
        print(f"[render_start] skip last_good: {exc}")
print(f"[render_start] poisoned-page purge done ({removed} removed)")
PY
disown || true

# ── Memory: 1 CPU / 2 GB instance (see gunicorn.conf.py) ────────────────────
export MALLOC_ARENA_MAX=2
export MALLOC_MMAP_THRESHOLD_=131072
export MALLOC_TRIM_THRESHOLD_=131072
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
# One background page rebuild at a time on Render (each can be hundreds of MB).
export PL_BUILD_SLOTS="${PL_BUILD_SLOTS:-1}"
# Short boot grace — long grace + blocked card SSR left React on "Loading games…".
export PL_BOOT_GRACE_S="${PL_BOOT_GRACE_S:-45}"
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
