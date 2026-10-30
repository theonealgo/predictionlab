"""Cap how many background page rebuilds run at the same time.

Saved-page refreshes replay a page through the app on their own thread
(``app.test_client()`` or a localhost request marked ``X-PL-Results-Rebuild``).
Each full page build can take a few hundred MB, and a crawl over every sport
and view used to start dozens at once on a 2 GB, 1-CPU instance. Visitor
requests are never held; background rebuilds take turns, and visitors keep
getting the saved page meanwhile.
"""
from __future__ import annotations

import os
import re
import threading

_SLOTS = max(1, int(os.environ.get("PL_BUILD_SLOTS", "2") or 2))
_WAIT_SECONDS = float(os.environ.get("PL_BUILD_WAIT", "300") or 300)
_gate = threading.BoundedSemaphore(_SLOTS)
_held = threading.local()
stats = {"gated": 0, "waited": 0, "timed_out": 0}

# Thread names of server request workers: gunicorn gthread, werkzeug dev
# server, and single-threaded servers.
_REQUEST_THREAD_MARKS = ("ThreadPoolExecutor", "process_request_thread", "MainThread")


def _is_background(environ) -> bool:
    if environ.get("HTTP_X_PL_RESULTS_REBUILD"):
        return True
    name = threading.current_thread().name or ""
    return not any(mark in name for mark in _REQUEST_THREAD_MARKS)


# Visitors opening the same unbuilt page at once (right after a deploy or a
# restart) each used to build it; on one CPU that made every copy slow. The
# first visitor builds it, the rest wait for that build and then run their own
# request, which finds the page cached. Nobody is handed another visitor's page.
_SAME_PAGE_WAIT = float(os.environ.get("PL_SAME_PAGE_WAIT", "20") or 20)
_PAGE_PATH = re.compile(
    r"^/(?:[a-z]+-(?:picks|results)(?:-[a-z0-9-]+)?|all-sports-[a-z-]+|daily-report|historic-results(?:/[0-9-]+)?)?/?$"
)
_DATED_PICKS = re.compile(r"^/([a-z]+-picks)-[a-z]+-\d{1,2}-\d{4}/?$")
_building: dict = {}
_building_lock = threading.Lock()


def _page_key(environ):
    if environ.get("REQUEST_METHOD") != "GET":
        return None
    path = environ.get("PATH_INFO") or ""
    if not _PAGE_PATH.match(path):
        return None
    dated = _DATED_PICKS.match(path)
    if dated:
        # Every dated picks page of a sport builds from the same slate.
        return (environ.get("HTTP_HOST") or "", dated.group(1) + "-dated", "")
    return (environ.get("HTTP_HOST") or "", path, environ.get("QUERY_STRING") or "")


class BuildGate:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if getattr(_held, "depth", 0) or not _is_background(environ):
            if getattr(_held, "depth", 0):
                _held.depth += 1
                try:
                    return self._run(environ, start_response)
                finally:
                    _held.depth -= 1
            return self._visitor(environ, start_response)
        stats["gated"] += 1
        got = _gate.acquire(blocking=False)
        if not got:
            stats["waited"] += 1
            got = _gate.acquire(timeout=_WAIT_SECONDS)
            if not got:
                stats["timed_out"] += 1
        _held.depth = 1
        try:
            return self._run(environ, start_response)
        finally:
            _held.depth = 0
            if got:
                _gate.release()

    def _visitor(self, environ, start_response):
        try:
            key = _page_key(environ)
        except Exception:
            key = None
        if key is None:
            return self.app(environ, start_response)
        with _building_lock:
            done = _building.get(key)
            leader = done is None
            if leader:
                done = _building[key] = threading.Event()
        if not leader:
            stats["same_page_waits"] = stats.get("same_page_waits", 0) + 1
            done.wait(_SAME_PAGE_WAIT)
            return self.app(environ, start_response)
        try:
            return self._run(environ, start_response)
        finally:
            with _building_lock:
                if _building.get(key) is done:
                    del _building[key]
            done.set()

    def _run(self, environ, start_response):
        # Read the whole body while the slot is held so the build finishes
        # inside it (test clients and local requests consume it right away).
        out = self.app(environ, start_response)
        try:
            body = b"".join(out)
        finally:
            close = getattr(out, "close", None)
            if close:
                close()
        return [body]
