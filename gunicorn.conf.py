"""Gunicorn config — loaded automatically when Render runs `gunicorn NHL77FINAL:app`.

Dashboard start commands that omit `--worker-class` still pick up gthread here.
Prefer `bash render_start.sh` on Render so /data DB seeding and 8 threads apply.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
# preload_app MUST stay False.
# With preload_app=True gunicorn imports the whole app in the MASTER *before*
# binding $PORT, so the port only opens after every top-level import finishes —
# Render (which has no healthCheckPath, so it uses TCP port detection) can time
# out waiting for the port and mark the deploy dead. It also means the daemon
# warm-up threads (ESPN odds pre-warmer, props/soccer backfills) start in the
# master, and fork() does NOT copy them into the worker that serves traffic —
# so the worker's caches stay cold and the first request fires ~31 synchronous
# ESPN calls and times out. With preload_app=False the master binds $PORT first,
# then the worker imports the app and runs those threads where they belong.
preload_app = False
accesslog = "-"
errorlog = "-"
