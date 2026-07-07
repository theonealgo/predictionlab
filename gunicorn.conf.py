"""Gunicorn config — loaded automatically when Render runs `gunicorn NHL77FINAL:app`.

Dashboard start commands that omit `--worker-class` still pick up gthread here.
Prefer `bash render_start.sh` on Render so /data DB seeding and 8 threads apply.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
# Scale out via the WEB_CONCURRENCY env var (Render dashboard) now that the app
# is on a paid instance. Defaults to 1 to preserve prior behavior/memory use.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = 8
timeout = 120
preload_app = True
accesslog = "-"
errorlog = "-"
