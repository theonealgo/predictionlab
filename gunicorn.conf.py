"""Gunicorn config — loaded automatically when Render runs `gunicorn app:app`.

Dashboard start commands that omit `--worker-class` still pick up gthread here.
Prefer `bash render_start.sh` on Render so /data DB seeding and 8 threads apply.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
# preload_app must stay False: the v2 predictors load native XGBoost (OpenMP)
# models at import. With preload the app is imported in the master and then
# forked — XGBoost/OpenMP state is not fork-safe, so the worker SIGSEGV'd on the
# first prediction request (site-wide 502). Loading models inside the worker
# (no fork-after-load) avoids the segfault.
preload_app = False
accesslog = "-"
errorlog = "-"
