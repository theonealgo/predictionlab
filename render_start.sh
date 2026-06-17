#!/usr/bin/env bash
set -e

# Force single-threaded native math (XGBoost/OpenMP/BLAS). XGBoost's OpenMP
# thread pool segfaulted gunicorn's threaded workers → site-wide 502 on
# prediction pages. Pinning to 1 thread avoids the crash.
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export XGBOOST_NTHREAD=1
PY_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
    PY_BIN="python3"
fi

# ── Copy database to persistent disk on first deploy ──────────────────────────
DATA_DIR="/data"
if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR" 2>/dev/null || DATA_DIR="."
fi

if [ "$DATA_DIR" = "/data" ]; then
    if [ ! -f "$DATA_DIR/sports_predictions_original.db" ]; then
        echo "[render_start] Initializing database on persistent disk..."
        cp sports_predictions_original.db "$DATA_DIR/sports_predictions_original.db"
    else
        echo "[render_start] Database already on persistent disk."
    fi
else
    echo "[render_start] /data unavailable; using repository database."
fi

# ── Launch Flask app via gunicorn (optional Datadog APM via ddtrace-run) ───────
if [ -n "${DD_API_KEY:-}" ] || [ "${DD_TRACE_ENABLED:-}" = "true" ]; then
    echo "[render_start] Datadog tracing enabled (DD_SERVICE=${DD_SERVICE:-predictionlab})"
    exec ddtrace-run "$PY_BIN" -m gunicorn -c gunicorn.conf.py app:app
fi
exec "$PY_BIN" -m gunicorn -c gunicorn.conf.py app:app
