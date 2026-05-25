#!/usr/bin/env bash
set -e

# ── Copy database to persistent disk on first deploy ──────────────────────────
if [ ! -f /data/sports_predictions_original.db ]; then
    echo "[render_start] Initializing database on persistent disk..."
    cp sports_predictions_original.db /data/sports_predictions_original.db
else
    echo "[render_start] Database already on persistent disk."
fi

# ── Launch Flask app via gunicorn ─────────────────────────────────────────────
exec gunicorn -c gunicorn.conf.py NHL77FINAL:app
