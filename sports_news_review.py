"""Owner-only review and manual social handoff using the existing login."""
from io import BytesIO
import secrets

from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, session
from flask_login import current_user

from sports_news import published_posts, validate_article
from sports_news_media import artwork, package, social_copy
from sports_news_store import content_version, load, review

news = Blueprint("sports_news_review", __name__)


def _csrf():
    if not session.get("_news_csrf"):
        session["_news_csrf"] = secrets.token_urlsafe(32)
    return session["_news_csrf"]


@news.before_request
def protect_review():
    if not request.path.startswith("/admin/sports-news"):
        return
    if not current_user.is_authenticated:
        abort(401)
    if not getattr(current_user, "is_admin", False):
        abort(403)
    if request.method == "POST":
        token = session.get("_news_csrf") or ""
        if not token or not secrets.compare_digest(token, request.form.get("csrf_token") or ""):
            abort(400, "The review form expired. Reload the page.")


@news.after_request
def private_review(response):
    if request.path.startswith("/admin/sports-news"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "same-origin"
        response.vary.add("Cookie")
    return response


def _post(slug):
    post = next((p for p in load()["posts"] if p.get("slug") == slug), None)
    if not post:
        abort(404)
    try:
        validate_article(post)
    except ValueError:
        abort(422, "Article validation failed. Publication is blocked.")
    return post


@news.route("/admin/sports-news")
def dashboard():
    document = load()
    posts = []
    for post in sorted(document["posts"], key=lambda p: p.get("date_published", ""), reverse=True):
        if post.get("kind") != "sports_news":
            continue
        try:
            validate_article(post)
            error = ""
        except ValueError as exc:
            error = str(exc)
        posts.append({**post, "version": content_version(post), "validation_error": error,
                      "social": social_copy(post)})
    return render_template("sports_news_review.html", posts=posts, csrf_token=_csrf(),
                           runs=document["runs"][-8:][::-1],
                           message={"published": "Article published to the blog.",
                                    "draft": "Article returned to drafts.",
                                    "queued": "Draft preparation requested. Refresh to see its progress."}.get(
                                        request.args.get("result"), ""))


@news.route("/admin/sports-news/generate", methods=["POST"])
def prepare():
    from sports_news_jobs import queue_batch
    queue_batch(current_app._get_current_object())
    return redirect("/admin/sports-news?result=queued", code=303)


@news.route("/admin/sports-news/<slug>/review", methods=["POST"])
def decide(slug):
    action = request.form.get("action")
    if action not in ("publish", "draft"):
        abort(400)
    try:
        review(slug, request.form.get("version"), action == "publish", current_user.get_id())
    except LookupError:
        abort(404)
    except ValueError as exc:
        abort(409, str(exc))
    invalidate = current_app.extensions.get("sports_news_invalidate")
    if invalidate:
        invalidate()
    return redirect(f"/admin/sports-news?result={'published' if action == 'publish' else 'draft'}", code=303)


@news.route("/admin/sports-news/<slug>/package.zip")
def social_package(slug):
    return send_file(package(_post(slug)), mimetype="application/zip",
                     as_attachment=True, download_name=f"{slug}-social.zip")


@news.route("/admin/sports-news/<slug>/<platform>.png")
def preview_image(slug, platform):
    if platform not in ("facebook", "instagram"):
        abort(404)
    return send_file(BytesIO(artwork(_post(slug), platform)), mimetype="image/png")


@news.route("/blog/news-assets/<slug>/<platform>.png")
def published_image(slug, platform):
    if platform not in ("facebook", "instagram"):
        abort(404)
    post = next((p for p in published_posts() if p["slug"] == slug), None)
    if post is None:
        abort(404)
    response = send_file(BytesIO(artwork(post, platform)), mimetype="image/png")
    # Check current publication status on every request, including after withdrawal.
    response.headers["Cache-Control"] = "no-store"
    return response


def init_news(app, invalidate=None):
    app.register_blueprint(news)
    app.extensions["sports_news_invalidate"] = invalidate
    def publication_headers(response):
        if request.path == "/blog" or request.path.startswith("/blog/"):
            response.headers["Cache-Control"] = "no-store"
            response.vary.add("Cookie")
        return response
    # Final response headers must also cover the existing article routes and their 404s.
    app.after_request_funcs.setdefault(None, []).insert(0, publication_headers)
    from sports_news_jobs import start_worker
    start_worker(app)
