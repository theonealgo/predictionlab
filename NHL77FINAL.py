#!/usr/bin/env python3
"""Fast production entrypoint for predictionlab.io.

Render imports ``NHL77FINAL:app`` directly. The full legacy module is large and
can block the single production worker during cold starts, so this wrapper keeps
the public site responsive and lazy-loads the full application only for routes
that need it.
"""

import importlib
import os
import threading

for _v in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "XGBOOST_NTHREAD",
):
    os.environ.setdefault(_v, "1")

from flask import Flask, redirect, request
from werkzeug.wrappers import Response as WsgiResponse
from werkzeug.middleware.proxy_fix import ProxyFix


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

_CANONICAL_HOST = "predictionlab.io"
_PRIMARY_HOSTS = {"predictionlab.io", "www.predictionlab.io"}
_full_module = None
_full_app = None
_full_lock = threading.Lock()


def _full_app_module():
    global _full_module, _full_app
    if _full_module is None:
        with _full_lock:
            if _full_module is None:
                _full_module = importlib.import_module("NHL77FINAL_full")
                _full_app = _full_module.app
    return _full_module


def _full_wsgi_app():
    if _full_app is None:
        _full_app_module()
    return _full_app


def __getattr__(name):
    """Delegate legacy ``import NHL77FINAL as main`` attributes to the full app."""
    return getattr(_full_app_module(), name)


def _canonical_redirect():
    host = (request.host or "").split(":")[0].lower()
    if not host or host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
        return None
    if not (host.endswith("underdogs.bet") or host.endswith("predictionlab.io")):
        return None
    target_host = host if host in _PRIMARY_HOSTS else _CANONICAL_HOST
    is_https = request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    if host != target_host or not is_https:
        full_path = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        return redirect(f"https://{target_host}{full_path}", code=301)
    return None


@app.before_request
def _fast_canonical():
    return _canonical_redirect()


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/", methods=["GET", "HEAD"])
def landing_page():
    if request.method == "HEAD":
        return "", 200
    return WsgiResponse.from_app(_full_wsgi_app().wsgi_app, request.environ)


@app.route("/mlb-picks")
@app.route("/nhl-picks")
@app.route("/nba-picks")
@app.route("/nfl-picks")
@app.route("/soccer-picks")
@app.route("/ncaab-picks")
@app.route("/ncaaf-picks")
@app.route("/ncaaw-picks")
@app.route("/wnba-picks")
@app.route("/tennis-picks")
@app.route("/ufc-picks")
@app.route("/golf-picks")
def _lazy_pick_pages():
    return WsgiResponse.from_app(_full_wsgi_app().wsgi_app, request.environ)


@app.route("/<path:_path>", methods=["GET", "POST", "HEAD", "OPTIONS"])
def _lazy_full_app(_path):
    return WsgiResponse.from_app(_full_wsgi_app().wsgi_app, request.environ)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
