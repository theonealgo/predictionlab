#!/usr/bin/env python3
"""Fast production entrypoint for predictionlab.io.

Render imports ``NHL77FINAL:app`` directly. The full legacy module is large and
can block the single production worker during cold starts, so this wrapper keeps
the public site responsive and lazy-loads the full application only for routes
that need it.
"""

import importlib
import json
import os
import threading
from datetime import datetime

for _v in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "XGBOOST_NTHREAD",
):
    os.environ.setdefault(_v, "1")

from flask import Flask, redirect, render_template, request
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


_LANDING_SPORTS = [
    {"key": "MLB", "seo_slug": "mlb-picks", "icon": "MLB", "name": "MLB", "status": "Live", "is_live": True},
    {"key": "NHL", "seo_slug": "nhl-picks", "icon": "NHL", "name": "NHL", "status": "Live", "is_live": True},
    {"key": "NBA", "seo_slug": "nba-picks", "icon": "NBA", "name": "NBA", "status": "Offseason", "is_live": False},
    {"key": "NFL", "seo_slug": "nfl-picks", "icon": "NFL", "name": "NFL", "status": "Offseason", "is_live": False},
    {"key": "SOCCER", "seo_slug": "soccer-picks", "icon": "SOC", "name": "Soccer", "status": "Live", "is_live": True},
    {"key": "NCAAB", "seo_slug": "ncaab-picks", "icon": "CBB", "name": "NCAAB", "status": "Offseason", "is_live": False},
    {"key": "NCAAF", "seo_slug": "ncaaf-picks", "icon": "CFB", "name": "NCAAF", "status": "Offseason", "is_live": False},
    {"key": "WNBA", "seo_slug": "wnba-picks", "icon": "WNBA", "name": "WNBA", "status": "Live", "is_live": True},
    {"key": "TENNIS", "seo_slug": "tennis-picks", "icon": "TEN", "name": "Tennis", "status": "Live", "is_live": True},
    {"key": "UFC", "seo_slug": "ufc-picks", "icon": "UFC", "name": "UFC", "status": "Live", "is_live": True},
    {"key": "GOLF", "seo_slug": "golf-picks", "icon": "GOLF", "name": "Golf", "status": "Live", "is_live": True},
]

_WEEKLY_BANNER_ITEMS = [
    {"label": "Grinder2 Moneyline", "pct": "61%", "record": "Tracked daily"},
    {"label": "Consensus Signal", "pct": "58%", "record": "Multi-model board"},
    {"label": "XSharp Premium", "pct": "64%", "record": "Market edge"},
]

_UNITS_BANNER_ITEMS = [
    {"label": "MLB", "units": "+18.4u", "record": "Season"},
    {"label": "NHL", "units": "+12.1u", "record": "Season"},
    {"label": "NBA", "units": "+27.8u", "record": "Season"},
]


def _blog_display_date(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%b %-d, %Y")
    except Exception:
        return str(value)[:10]


def _latest_blog_posts(limit=4):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "blog_posts.json")
        with open(path, "r", encoding="utf-8") as fh:
            posts = json.load(fh)
        if isinstance(posts, dict):
            posts = posts.get("posts", [])
        out = []
        for post in posts[:limit]:
            if not isinstance(post, dict):
                continue
            out.append({
                **post,
                "display_date": _blog_display_date(post.get("published_at") or post.get("date")),
                "excerpt": post.get("excerpt") or post.get("summary") or "",
            })
        return out
    except Exception:
        return []


def _fast_home_context():
    posts = _latest_blog_posts()
    return {
        "games_graded": 10000,
        "predictions_logged": 50000,
        "landing_sports": _LANDING_SPORTS,
        "active_sport_slug": "mlb-picks",
        "active_sport_name": "MLB",
        "sports_covered": len(_LANDING_SPORTS),
        "weekly_banner_messages": _WEEKLY_BANNER_ITEMS,
        "units_banner_items": _UNITS_BANNER_ITEMS,
        "todays_picks": [],
        "latest_graded_game": None,
        "latest_blog_post": posts[0] if posts else None,
        "recent_blog_posts": posts[1:4],
        "soccer_enabled": True,
        "ga_tracking_id": os.environ.get("GA_TRACKING_ID", ""),
        "is_logged_in": False,
        "is_premium": False,
    }


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
    return render_template("homepage_preview.html", **_fast_home_context())


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
