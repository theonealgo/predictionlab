"""Gunicorn config — loaded automatically when Render runs `gunicorn NHL77FINAL:app`.

Dashboard start commands that omit `--worker-class` still pick up gthread here.
Prefer `bash render_start.sh` on Render so /data DB seeding and 8 threads apply.
"""
import os
import sys

# macOS + gunicorn: ObjC can initialize on a background thread before fork(); the
# child then SIGKILLs itself (prepush gate mis-labels that as OOM). Linux/Render
# is unaffected. Set before workers fork.
if sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

# Local only: the project folder (it holds .pushgit) serves just the 5122 site via
# ./run_local.sh. Test servers run from a copy (qa/test_copy.py) and never take 5122.
if os.path.isdir(os.path.join(os.path.realpath(os.getcwd()), ".pushgit")):
    raise SystemExit("gunicorn refused: start test servers from a copy (qa/test_copy.py), not the project folder")
if os.environ.get("PORT") == "5122":
    raise SystemExit("gunicorn refused: port 5122 is the local site (./run_local.sh)")

# Render gives this service 1 CPU and 2 GB, but the host shows every core.
# numpy / OpenBLAS / OpenMP start one thread (and buffer) per visible core, and
# glibc opens up to 8 malloc arenas per core; together they took a 1-worker app
# past 2 GB under a full-site crawl. Set in the master so the forked worker
# inherits them; render_start.sh exports the same values.
for _k, _v in (("OMP_NUM_THREADS", "1"), ("OPENBLAS_NUM_THREADS", "1"), ("MKL_NUM_THREADS", "1")):
    os.environ.setdefault(_k, _v)
try:
    import ctypes as _ctypes

    _libc = _ctypes.CDLL("libc.so.6")
    _libc.mallopt(-8, 2)  # M_ARENA_MAX
    _libc.mallopt(-3, 131072)  # M_MMAP_THRESHOLD
    _libc.mallopt(-1, 131072)  # M_TRIM_THRESHOLD
except Exception:
    pass

# Stamp master start once. Worker recycles (max_requests) must NOT re-apply
# PL_BOOT_GRACE_S — that left card builds blocked while the homepage hammered.
try:
    import time as _time_boot
    from pathlib import Path as _Path_boot
    _boot_stamp = _Path_boot(os.environ.get("PL_BOOT_STAMP", "/data/.cache/gunicorn_boot_at"))
    _boot_stamp.parent.mkdir(parents=True, exist_ok=True)
    _boot_stamp.write_text(str(_time_boot.time()))
except Exception:
    pass

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
worker_class = "gthread"
# 8 gthreads on a 1-CPU / 2 GB box inflate RSS and fight the GIL during XGB/ESPN
# storms after deploy. Cap on Render-sized hosts; override with GUNICORN_THREADS.
_default_threads = "4" if (
    os.path.isdir("/data") or os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID")
) else "8"
threads = max(1, int(os.environ.get("GUNICORN_THREADS", _default_threads) or _default_threads))
# 120s was killing the only worker mid rebuild (WORKER TIMEOUT) and forcing a
# full cold boot under traffic. Allow one slow rebuild without recycling.
timeout = max(60, int(os.environ.get("GUNICORN_TIMEOUT", "180") or 180))
# Recycle the worker before RAM creep OOMs the 2–4 GB Render box.
# Jitter spreads restarts so a traffic spike does not coincide with recycle.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "400") or 400)
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "50") or 50)
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
#
# Boot contract (NHL77FINAL): disk-seed prediction slates at import; defer
# preds/odds/props/soccer heavy work ≥60–120s so /healthz + homepage stay
# responsive on this single worker. Never force-rebuild seeded slates on boot.
# Served picks/results HTML lives on /data/.cache (served_cache.py) so a
# redeploy does not wipe every page and stampede this one worker.
preload_app = False
accesslog = "-"
errorlog = "-"
