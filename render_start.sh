#!/usr/bin/env bash
set -e

# ── Copy database to persistent disk on first deploy ──────────────────────────
if [ ! -f /data/sports_predictions_original.db ]; then
    echo "[render_start] Initializing database on persistent disk..."
    cp sports_predictions_original.db /data/sports_predictions_original.db
else
    echo "[render_start] Database already on persistent disk."
fi

# ── Memory: 1 CPU / 2 GB instance (see gunicorn.conf.py) ────────────────────
export MALLOC_ARENA_MAX=2
export MALLOC_MMAP_THRESHOLD_=131072
export MALLOC_TRIM_THRESHOLD_=131072
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

# ── Launch Flask app via gunicorn (optional Datadog APM via ddtrace-run) ───────
if [ -n "${DD_API_KEY:-}" ] || [ "${DD_TRACE_ENABLED:-}" = "true" ]; then
    echo "[render_start] Datadog tracing enabled (DD_SERVICE=${DD_SERVICE:-predictionlab})"
    exec ddtrace-run gunicorn -c gunicorn.conf.py NHL77FINAL:app
fi
exec gunicorn -c gunicorn.conf.py NHL77FINAL:app
