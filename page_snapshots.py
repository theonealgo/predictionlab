"""Logged-out visitors never wait for a slow page build.

Sport picks / results pages (every market / week / league view), the
performance page and the public CSV downloads are kept as the finished
response. A logged-out visitor gets the saved copy at once; a copy older than
FRESH_S is still served and rebuilt in the background for the next visitor.

Logged-in visitors always get a fresh build (their pages differ by tier), except
the CSV downloads, which are the same for everyone. A saved copy is never one
that sets cookies for someone, carries a form token, or is flagged premium.
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict

FRESH_S = 300.0
MAX_AGE_S = 24 * 3600.0
MAX_BYTES = 64 * 1024 * 1024
WARM_DELAY_S = 90.0
WARM_GAP_S = 2.0

_PAGE_RE = re.compile(
    r"^/(?:(?:mlb|nba|nfl|nhl|ncaab|ncaaf|ncaaw|wnba|cfl|soccer|tennis|ufc|golf)-(?:picks|results)"
    r"|performance|all-sports-results)/?$"
)
_PUBLIC_DOWNLOADS = ("/results/export.csv", "/performance/audit.csv")
_LOCAL_HOSTS = ("127.0.0.1", "localhost")
_TRACKING_RE = re.compile(r"(?:^|&)(?:ref|utm_[a-z]+|gclid|gbraid|wbraid|fbclid|msclkid)=", re.I)
_UNSAFE = (b'name="csrf_token"', b"const IS_PREMIUM = true")
_KEEP_HEADERS = ("content-type", "content-disposition", "cache-control", "vary", "content-language")


class PageSnapshots:
    def __init__(self, app, inner, on_hit=None):
        self.app = app
        self.inner = inner
        self.on_hit = on_hit
        self._lock = threading.Lock()
        self._pages: "OrderedDict[tuple, tuple[float, str, list, bytes]]" = OrderedDict()
        self._bytes = 0
        self._building: set = set()

    # ---- who may get a saved copy -------------------------------------------------
    def _logged_in(self, environ) -> bool:
        from werkzeug.wrappers import Request
        req = Request(environ)
        cookies = req.cookies
        if cookies.get(self.app.config.get("REMEMBER_COOKIE_NAME", "remember_token")):
            return True
        raw = cookies.get(self.app.config.get("SESSION_COOKIE_NAME", "session"))
        if not raw:
            return False
        try:
            data = self.app.session_interface.get_signing_serializer(self.app).loads(raw)
        except Exception:
            return True  # unreadable session: treat as logged in, build fresh
        return bool(data.get("_user_id"))

    def _key(self, environ):
        if environ.get("REQUEST_METHOD") != "GET":
            return None
        path = environ.get("PATH_INFO") or "/"
        public = path in _PUBLIC_DOWNLOADS
        if not public and not _PAGE_RE.match(path):
            return None
        query = environ.get("QUERY_STRING") or ""
        if _TRACKING_RE.search(query):
            return None  # affiliate / ad clicks must reach the app so the visit is credited
        host = (environ.get("HTTP_HOST") or "").split(":")[0].lower()
        if host in _LOCAL_HOSTS:
            return None  # local addresses are shown everything; never save those
        if not public and self._logged_in(environ):
            return None
        return (environ.get("wsgi.url_scheme"), environ.get("HTTP_HOST"), path,
                environ.get("QUERY_STRING") or "")

    # ---- saved copies -------------------------------------------------------------
    def _remember(self, key, status, headers, body):
        if not status.startswith("200") or not body:
            return
        if any(mark in body for mark in _UNSAFE):
            return
        kept = [(k, v) for k, v in headers if k.lower() in _KEEP_HEADERS]
        ctype = next((v for k, v in kept if k.lower() == "content-type"), "")
        if "text/html" not in ctype and "text/csv" not in ctype:
            return
        with self._lock:
            old = self._pages.pop(key, None)
            if old:
                self._bytes -= len(old[3])
            self._pages[key] = (time.time(), status, kept, body)
            self._bytes += len(body)
            while self._bytes > MAX_BYTES and self._pages:
                _, dropped = self._pages.popitem(last=False)
                self._bytes -= len(dropped[3])

    def _build(self, environ):
        """Run the app for a logged-out copy of this request; (status, headers, body)."""
        env = dict(environ)
        env.pop("HTTP_COOKIE", None)
        env["wsgi.input"] = __import__("io").BytesIO(b"")
        got = {}

        def _start(status, headers, exc_info=None):
            got["status"], got["headers"] = status, headers
            return lambda _data: None

        result = self.inner(env, _start)
        try:
            body = b"".join(result)
        finally:
            if hasattr(result, "close"):
                result.close()
        return got.get("status", "500"), got.get("headers", []), body

    def _refresh_later(self, key, environ):
        with self._lock:
            if key in self._building:
                return
            self._building.add(key)

        def _run():
            try:
                status, headers, body = self._build(environ)
                self._remember(key, status, headers, body)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._building.discard(key)

        threading.Thread(target=_run, daemon=True, name="page-snapshot-refresh").start()

    # ---- WSGI -----------------------------------------------------------------------
    def __call__(self, environ, start_response):
        key = None
        try:
            key = self._key(environ)
        except Exception:
            key = None
        if key is None:
            return self.inner(environ, start_response)
        with self._lock:
            saved = self._pages.get(key)
            if saved:
                self._pages.move_to_end(key)
        if saved and time.time() - saved[0] < MAX_AGE_S:
            ts, status, headers, body = saved
            if time.time() - ts >= FRESH_S:
                self._refresh_later(key, environ)
            if self.on_hit:
                try:
                    self.on_hit(environ)
                except Exception:
                    pass
            start_response(status, list(headers) + [("Content-Length", str(len(body)))])
            return [body]

        captured = {}

        def _capture(status, headers, exc_info=None):
            captured["status"], captured["headers"] = status, headers
            return start_response(status, headers, exc_info) if exc_info else start_response(status, headers)

        result = self.inner(environ, _capture)
        try:
            body = b"".join(result)
        finally:
            if hasattr(result, "close"):
                result.close()
        try:
            self._remember(key, captured.get("status", ""), captured.get("headers", []), body)
        except Exception:
            pass
        return [body]

    # ---- after a restart: build the slow views once, one at a time -------------------
    def warm(self, host: str, paths: list[str]):
        def _run():
            time.sleep(WARM_DELAY_S)
            seen = set()
            queue = list(paths)
            starts = set(paths)
            while queue:
                target = queue.pop(0)
                if target in seen:
                    continue
                seen.add(target)
                path, _, query = target.partition("?")
                env = _environ(host, path, query)
                try:
                    status, headers, body = self._build(env)
                    key = ("https", host, path, query)
                    self._remember(key, status, headers, body)
                    if target in starts:
                        for variant in _same_page_variants(body, path):
                            if variant not in seen:
                                queue.append(variant)
                except Exception:
                    pass
                time.sleep(WARM_GAP_S)

        threading.Thread(target=_run, daemon=True, name="page-snapshot-warm").start()


def _environ(host, path, query):
    from werkzeug.test import EnvironBuilder
    env = EnvironBuilder(path=path, query_string=query, base_url=f"https://{host}",
                         headers={"X-Forwarded-Proto": "https", "User-Agent": "pl-page-warm"}).get_environ()
    env["wsgi.url_scheme"] = "https"
    return env


def _same_page_variants(body: bytes, path: str) -> list[str]:
    """Market / week / league links on a page that point back to the same page."""
    html = body.decode("utf-8", "replace")
    out = []
    for href in re.findall(r"<a\b[^>]*?\bhref=[\"'](%s\?[^\"'#]+)[\"']" % re.escape(path), html):
        href = href.replace("&amp;", "&")
        if "{" not in href and "+" not in href:
            out.append(href)
    for href in _PUBLIC_DOWNLOADS_RE.findall(html):
        out.append(href.replace("&amp;", "&"))
    return out


_PUBLIC_DOWNLOADS_RE = re.compile(
    r"href=[\"']((?:/results/export\.csv|/performance/audit\.csv)(?:\?[^\"'#]*)?)[\"']"
)


def install(app, on_hit=None, warm_host="predictionlab.io"):
    """Put saved pages inside ProxyFix so the host / scheme are the visitor's."""
    target = app.wsgi_app
    if hasattr(target, "app"):
        snap = PageSnapshots(app, target.app, on_hit)
        target.app = snap
    else:
        snap = PageSnapshots(app, target, on_hit)
        app.wsgi_app = snap
    sports = ("mlb", "nba", "nfl", "nhl", "ncaab", "ncaaf", "ncaaw", "wnba", "cfl", "soccer", "tennis", "ufc", "golf")
    paths = ["/performance", "/all-sports-results", "/soccer-picks?region=all"] + [
        f"/{s}-{k}" for s in sports for k in ("results", "picks") if f"/{s}-{k}" != "/soccer-picks"
    ]
    snap.warm(warm_host, paths)
    return snap
