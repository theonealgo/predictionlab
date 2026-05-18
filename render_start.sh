#!/usr/bin/env bash
set -e

echo "[render_start] deploy $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

# ── Refresh database on persistent disk EVERY deploy ──────────────────────────
# The repo DB is the source of truth (updated with completed games + team stats).
# Always overwrite the persistent copy so the spread/total/efficiency models on
# production have the same recent data as local. A timestamped backup of the
# previous /data copy is kept so nothing is permanently lost.
if [ -f /data/sports_predictions_original.db ]; then
    cp /data/sports_predictions_original.db \
       "/data/sports_predictions_original.db.bak.$(date +%Y%m%d%H%M%S)" || true
    # keep only the 3 most recent backups
    ls -1t /data/sports_predictions_original.db.bak.* 2>/dev/null \
       | tail -n +4 | xargs -r rm -f || true
fi
echo "[render_start] Refreshing database on persistent disk from repo..."
cp sports_predictions_original.db /data/sports_predictions_original.db
echo "[render_start] Database refreshed ($(du -h sports_predictions_original.db | cut -f1))."

# ── Launch Flask app via gunicorn ─────────────────────────────────────────────
exec gunicorn NHL77FINAL:app \
    --bind "0.0.0.0:${PORT:-10000}" \
    --workers 1 \
    --threads 4 \
    --timeout 120 \
    --preload \
    --access-logfile - \
    --error-logfile -
