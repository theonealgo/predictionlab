"""Gzip text responses (HTML, CSS, JS, JSON, SVG) for clients that accept it."""
from __future__ import annotations

import gzip

_COMPRESSIBLE = (
    "text/html",
    "text/css",
    "text/plain",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
)
_MIN_BYTES = 1024


class GzipMiddleware:
    def __init__(self, app, level: int = 6):
        self.app = app
        self.level = level

    def __call__(self, environ, start_response):
        if "gzip" not in (environ.get("HTTP_ACCEPT_ENCODING") or "").lower():
            return self.app(environ, start_response)
        if environ.get("REQUEST_METHOD", "GET").upper() not in ("GET", "POST"):
            return self.app(environ, start_response)

        captured: dict = {}

        def _capture(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            captured["exc_info"] = exc_info
            return lambda data: captured.setdefault("written", []).append(data)

        app_iter = self.app(environ, _capture)
        status = captured.get("status", "500 INTERNAL SERVER ERROR")
        headers = list(captured.get("headers") or [])
        lower = {k.lower(): v for k, v in headers}
        ctype = (lower.get("content-type") or "").split(";")[0].strip().lower()
        if (
            not status.startswith("200")
            or ctype not in _COMPRESSIBLE
            or "content-encoding" in lower
            or "no-transform" in (lower.get("cache-control") or "").lower()
        ):
            start_response(status, headers, captured.get("exc_info"))
            return _chain(captured.get("written"), app_iter)

        try:
            body = b"".join(captured.get("written") or []) + b"".join(app_iter)
        finally:
            close = getattr(app_iter, "close", None)
            if close:
                close()
        if len(body) < _MIN_BYTES:
            out = body
            headers = [(k, v) for k, v in headers if k.lower() != "content-length"]
            headers.append(("Content-Length", str(len(out))))
            start_response(status, headers, captured.get("exc_info"))
            return [out]
        out = gzip.compress(body, compresslevel=self.level)
        headers = [(k, v) for k, v in headers if k.lower() not in ("content-length", "content-encoding")]
        vary = [v for k, v in headers if k.lower() == "vary"]
        headers = [(k, v) for k, v in headers if k.lower() != "vary"]
        vary_vals = [s.strip() for v in vary for s in v.split(",") if s.strip()]
        if "accept-encoding" not in [s.lower() for s in vary_vals]:
            vary_vals.append("Accept-Encoding")
        headers.append(("Vary", ", ".join(vary_vals)))
        headers.append(("Content-Encoding", "gzip"))
        headers.append(("Content-Length", str(len(out))))
        start_response(status, headers, captured.get("exc_info"))
        return [out]


def _chain(written, app_iter):
    if not written:
        return app_iter
    return _ChainedIter(written, app_iter)


class _ChainedIter:
    def __init__(self, first, rest):
        self._first = first
        self._rest = rest

    def __iter__(self):
        yield from self._first
        yield from self._rest

    def close(self):
        close = getattr(self._rest, "close", None)
        if close:
            close()
