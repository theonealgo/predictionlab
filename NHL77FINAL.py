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

from flask import Flask, Response, redirect, request
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


def _home_html():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prediction Lab | AI Sports Picks</title>
  <meta name="description" content="Daily AI sports picks, model projections, and betting market analysis.">
  <link rel="canonical" href="https://predictionlab.io/">
  <style>
    body{margin:0;background:#05070b;color:#f7f9fc;font-family:Arial,Helvetica,sans-serif}
    main{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:32px}
    section{max-width:980px;width:100%}
    .brand{font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:#93a4b8;margin-bottom:18px}
    h1{font-size:clamp(40px,7vw,86px);line-height:.95;margin:0 0 22px;font-weight:900}
    p{font-size:20px;line-height:1.5;color:#cdd7e4;max-width:760px}
    nav{display:flex;flex-wrap:wrap;gap:10px;margin-top:30px}
    a{color:#071018;background:#f7c948;text-decoration:none;font-weight:800;padding:12px 16px;border-radius:6px}
    a.secondary{background:#182230;color:#f7f9fc;border:1px solid #2d3a4b}
  </style>
</head>
<body>
  <main>
    <section>
      <div class="brand">predictionlab.io</div>
      <h1>AI Sports Picks</h1>
      <p>Daily model-driven picks, betting market signals, and performance tracking across major sports.</p>
      <nav>
        <a href="/mlb-picks">MLB Picks</a>
        <a href="/nhl-picks" class="secondary">NHL Picks</a>
        <a href="/nba-picks" class="secondary">NBA Picks</a>
        <a href="/soccer-picks" class="secondary">Soccer Picks</a>
        <a href="/blog" class="secondary">Blog</a>
      </nav>
    </section>
  </main>
</body>
</html>"""


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
    return Response(_home_html(), mimetype="text/html")


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
