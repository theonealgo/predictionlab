"""Open the site as a signed-in paid customer for the checker.

    with paid_view("http://192.168.2.47:5122") as base:
        ...  # every request to `base` reaches the site signed in as a paid account

A throwaway paid account is made (qa/test_accounts.py), signed in through
/login, and a small local relay adds that account's cookies to every request
before passing it to the site with the site's own Host header. The account is
deleted when the block ends. Chrome and plain HTTP checks both use `base`, so
they see exactly what a paying customer sees.
"""
from __future__ import annotations

import contextlib
import http.client
import importlib.util
import re
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 300
_HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding",
        "proxy-connection", "te", "trailer", "upgrade", "set-cookie"}


def _test_accounts():
    spec = importlib.util.spec_from_file_location("pl_test_accounts", ROOT / "qa" / "test_accounts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cookies_from(resp: http.client.HTTPResponse, jar: dict[str, str]) -> None:
    for raw in resp.headers.get_all("Set-Cookie") or []:
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except Exception:
            continue
        for name, morsel in parsed.items():
            if morsel.value == "" or morsel["max-age"] == "0":
                jar.pop(name, None)
            else:
                jar[name] = morsel.value


def _request(target, method: str, path: str, headers: dict[str, str], body: bytes | None = None):
    conn_cls = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(target.hostname, target.port, timeout=TIMEOUT_S)
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def _sign_in(target, email: str, password: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    host = target.netloc

    def hdrs(extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Host": host, "User-Agent": "pl-checker-paid", "Accept-Encoding": "identity"}
        if jar:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        h.update(extra or {})
        return h

    resp, page = _request(target, "GET", "/login", hdrs())
    _cookies_from(resp, jar)
    tok = re.search(rb'name="csrf_token" value="([^"]+)"', page)
    from urllib.parse import urlencode

    form = urlencode({"email": email, "password": password,
                      "csrf_token": tok.group(1).decode() if tok else ""}).encode()
    resp, _ = _request(target, "POST", "/login", hdrs({"Content-Type": "application/x-www-form-urlencoded"}), form)
    _cookies_from(resp, jar)
    resp, page = _request(target, "GET", "/account", hdrs())
    _cookies_from(resp, jar)
    if b'href="/logout"' not in page:
        raise RuntimeError("the paid checker account could not sign in (no Sign out on /account)")
    return jar


def _relay(target, jar: dict[str, str]):
    target_origin = f"{target.scheme}://{target.netloc}"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def _forward(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in _HOP and k.lower() not in ("host", "cookie", "accept-encoding")}
            headers["Host"] = target.netloc
            headers["Accept-Encoding"] = "identity"
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
            try:
                resp, data = _request(target, self.command, self.path, headers, body)
            except Exception as exc:
                msg = f"checker relay could not reach {target_origin}: {exc}".encode()
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
            own = f"http://{self.headers.get('Host') or ''}"
            ctype = resp.headers.get("Content-Type") or ""
            if any(t in ctype for t in ("text/", "json", "javascript", "xml")):
                data = data.replace(target_origin.encode(), own.encode())
            self.send_response(resp.status, resp.reason)
            for k, v in resp.headers.items():
                if k.lower() in _HOP:
                    continue
                if k.lower() == "location":
                    v = v.replace(target_origin, own)
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = _forward

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # Chrome drops connections it no longer needs; that is not a page error.
            import sys
            if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            super().handle_error(request, client_address)

    server = Server(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@contextlib.contextmanager
def paid_view(url: str, app_dir: Path | str = ROOT):
    """Yields a local base URL that reaches `url` signed in as a paid account."""
    target = urlsplit(url.rstrip("/"))
    with _test_accounts().accounts(app_dir) as made:
        email, password = made["paid"]
        jar = _sign_in(target, email, password)
        server = _relay(target, jar)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            resp, page = _request(urlsplit(base), "GET", "/nfl-picks", {"Host": base[7:]})
            if b"const IS_PREMIUM = false" in page:
                raise RuntimeError("signed in as the paid checker account but /nfl-picks is still locked")
            yield base
        finally:
            server.shutdown()
            server.server_close()
            time.sleep(0.1)
