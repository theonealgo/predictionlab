from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import zipfile

from flask import Flask
from flask_login import LoginManager, UserMixin
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from PIL import Image
import pytest

import sports_news as news
from sports_news_jobs import run_daily
from sports_news_media import artwork, package
from sports_news_review import init_news
from sports_news_store import content_version, load, review, update

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 11, 15, tzinfo=timezone.utc)
DAY = "2026-11-10"


def board(state="STATUS_FINAL", score="117", event_id="123"):
    return {"events": [{"id": event_id, "date": "2026-11-11T00:00Z",
                       "status": {"type": {"name": state, "completed": state == "STATUS_FINAL"}},
                       "competitions": [{"competitors": [
                           {"homeAway": "home", "score": score, "team": {"displayName": "New York Knicks"}},
                           {"homeAway": "away", "score": "85", "team": {"displayName": "Milwaukee Bucks"}},
                       ]}]}]}


def article():
    return news.build_article("NBA", news.final_facts("NBA", board(), DAY, NOW), NOW)


def only_nba(monkeypatch):
    monkeypatch.setattr(news, "SPORTS", {"NBA": news.SPORTS["NBA"]})


def test_individual_provider_failure_does_not_abort_batch(monkeypatch):
    monkeypatch.setattr(news, "SPORTS", {k: news.SPORTS[k] for k in ("NBA", "NHL")})
    def get(url):
        if "/nba/" in url:
            raise TimeoutError
        return board()
    report = []
    posts = news.generate(DAY, get=get, now=NOW, report=report)
    assert len(posts) == 1 and posts[0]["sport_tag"] == "NHL"
    assert report[0]["status"] == "unavailable"


@pytest.mark.parametrize("state,score", [("STATUS_IN_PROGRESS", "80"), ("STATUS_FINAL", None),
                                        ("STATUS_FINAL", "NaN"), ("STATUS_FINAL", "-1")])
def test_partial_or_invalid_final_slate_is_withheld(monkeypatch, state, score):
    only_nba(monkeypatch)
    payload = board()
    payload["events"].extend(board(state, score, event_id="456")["events"])
    report = []
    assert news.generate(DAY, get=lambda url: payload, now=NOW, report=report) == []
    assert report[0]["status"] in ("pending", "unavailable")


def test_all_games_have_citations_beyond_eight(monkeypatch):
    only_nba(monkeypatch)
    payload = {"events": []}
    for n in range(10):
        payload["events"].extend(board(event_id=str(100+n))["events"])
    post = news.generate(DAY, get=lambda url: payload, now=NOW)[0]
    assert len(post["provenance"]["facts"]) == len(post["news_items"]) == 10
    assert news.validate_article(post)


def test_current_and_future_days_rejected_before_network():
    for day in ("2026-11-11", "2026-11-12"):
        with pytest.raises(ValueError):
            news.generate(day, get=lambda url: pytest.fail("Unexpected network"), now=NOW)


def test_atomic_cross_thread_storage_and_daily_cap(tmp_path):
    path = tmp_path / "posts.json"
    posts = []
    for n in range(8):
        post = article()
        post["slug"] += f"-{n}"
        post["canonical_path"] = f"/blog/nba/{post['slug']}"
        posts.append(post)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda p: news.save_articles(path, [p]), posts))
    assert len(load(path)["posts"]) == 3
    assert not list(tmp_path.glob(".news-*.tmp"))


def test_corrupt_store_never_overwritten(tmp_path):
    path = tmp_path / "posts.json"
    path.write_text("corrupt")
    with pytest.raises(ValueError):
        news.save_articles(path, [article()])
    assert path.read_text() == "corrupt"


def test_noop_job_check_does_not_invalidate_public_archive(tmp_path):
    path = tmp_path / "posts.json"
    news.save_articles(path, [article()])
    before = path.stat().st_mtime_ns
    assert update(lambda document: False, path) is False
    assert path.stat().st_mtime_ns == before


def test_fact_citation_and_canonical_path_tampering_rejected():
    for field in ("citation", "canonical"):
        post = article()
        if field == "citation":
            post["news_items"] = []
        else:
            post["canonical_path"] = "/blog/nba/../../admin"
        with pytest.raises(ValueError):
            news.validate_article(post)


def test_daily_job_never_publishes_even_with_legacy_flag(tmp_path, monkeypatch):
    only_nba(monkeypatch)
    monkeypatch.setenv("PL_NEWS_AUTOPUBLISH", "1")
    path = tmp_path / "posts.json"
    assert run_daily(path, now=NOW.replace(hour=13), get=lambda url: board())["status"] == "not_due"
    assert run_daily(path, now=NOW, get=lambda url: board())["status"] == "complete"
    assert load(path)["posts"][0]["status"] == "draft"
    assert run_daily(path, now=NOW+timedelta(minutes=1),
                     get=lambda url: pytest.fail("Duplicate job"))["status"] == "already_checked"
    assert len(load(path)["runs"]) == 1


def test_daily_failure_retries_are_bounded(tmp_path, monkeypatch):
    only_nba(monkeypatch)
    path = tmp_path / "posts.json"
    def offline(url):
        raise TimeoutError
    for n in range(4):
        assert run_daily(path, now=NOW+timedelta(minutes=31*n), get=offline)["status"] == "retry"
    assert run_daily(path, now=NOW+timedelta(hours=3), get=offline)["status"] == "already_checked"
    assert len(load(path)["runs"]) == 4


def test_content_version_prevents_stale_approval_and_replay(tmp_path):
    path = tmp_path / "posts.json"
    post = article()
    news.save_articles(path, [post])
    version = content_version(load(path)["posts"][0])
    with pytest.raises(ValueError):
        review(post["slug"], "outdated", True, "owner", path)
    published = review(post["slug"], version, True, "owner", path)
    assert published["status"] == "published"
    assert not published["social_drafts"]["ready_for_delivery"]
    assert published["provenance"] == post["provenance"]
    with pytest.raises(ValueError):
        review(post["slug"], version, False, "owner", path)
    assert review(post["slug"], content_version(published), False, "owner", path)["status"] == "draft"


def test_approved_articles_not_overwritten_on_regeneration(tmp_path):
    path = tmp_path / "posts.json"
    post = article()
    news.save_articles(path, [post], publish_local=True)
    before = deepcopy(load(path)["posts"][0])
    replacement = deepcopy(post)
    replacement["title"] = "A revised draft"
    news.save_articles(path, [replacement])
    assert load(path)["posts"][0] == before


@pytest.mark.parametrize("platform,size", [("facebook", (1200, 630)), ("instagram", (1080, 1080))])
def test_social_images_are_original_and_correct_dimensions(platform, size):
    assert Image.open(BytesIO(artwork(article(), platform))).size == size


def test_package_contains_assets_captions_and_honest_delivery_status():
    with zipfile.ZipFile(package(article())) as archive:
        assert set(archive.namelist()) == {
            "facebook.png", "instagram.png", "facebook-caption.txt", "instagram-caption.txt",
            "article-link.txt", "package.json",
        }
        manifest = json.loads(archive.read("package.json"))
        assert not manifest["delivered"] and manifest["article_status"] == "draft"
        assert b"https://predictionlab.io/blog/nba/" in archive.read("facebook-caption.txt")


class FixtureUser(UserMixin):
    def __init__(self, role):
        self.id = role
        self.is_admin = role == "admin"


@pytest.fixture
def review_app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="fixture-only",
                      SPORTS_NEWS_STORE=str(tmp_path / "posts.json"))
    app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": "<html><head>{% block head_meta %}{% endblock %}</head>"
                                "<body>{% block content %}{% endblock %}</body></html>"}),
        FileSystemLoader(ROOT / "templates"),
    ])
    login = LoginManager(app)
    login.user_loader(lambda role: FixtureUser(role))
    app.extensions["invalidated"] = []
    init_news(app, invalidate=lambda: app.extensions["invalidated"].append(True))
    with app.app_context():
        news.save_articles(None, [article()])
    return app


def client_for(app, role=None):
    client = app.test_client()
    if role:
        with client.session_transaction() as session:
            session["_user_id"] = role
            session["_fresh"] = True
    return client


@pytest.mark.parametrize("role,status", [(None, 401), ("paid", 403), ("admin", 200)])
def test_review_is_admin_only_not_merely_premium(review_app, role, status):
    response = client_for(review_app, role).get("/admin/sports-news")
    assert response.status_code == status
    assert "no-store" in response.headers["Cache-Control"]
    assert "noindex" in response.headers["X-Robots-Tag"]


def test_csrf_publish_withdraw_and_public_draft_images(review_app):
    client = client_for(review_app, "admin")
    post = article()
    slug = post["slug"]
    public_url = f"/blog/news-assets/{slug}/facebook.png"
    assert client.get(public_url).status_code == 404
    assert "no-store" in client.get(public_url).headers["Cache-Control"]
    endpoint = f"/admin/sports-news/{slug}/review"
    assert client.post(endpoint, data={"action": "publish"}).status_code == 400
    assert client.get("/admin/sports-news").status_code == 200
    with client.session_transaction() as session:
        token = session["_news_csrf"]
    with review_app.app_context():
        version = content_version(load()["posts"][0])
    response = client.post(endpoint, data={"action": "publish", "csrf_token": token, "version": version})
    assert response.status_code == 303
    assert client.get(public_url).status_code == 200
    assert review_app.extensions["invalidated"] == [True]
    assert client.post(endpoint, data={"action": "draft", "csrf_token": token,
                                       "version": version}).status_code == 409
    with review_app.app_context():
        current = load()["posts"][0]
        version = content_version(current)
    assert client.post(endpoint, data={"action": "draft", "csrf_token": token,
                                       "version": version}).status_code == 303
    assert client.get(public_url).status_code == 404


def test_package_and_private_images_require_admin(review_app):
    slug = article()["slug"]
    for role in (None, "paid"):
        client = client_for(review_app, role)
        for suffix in ("package.zip", "facebook.png", "instagram.png"):
            assert client.get(f"/admin/sports-news/{slug}/{suffix}").status_code in (401, 403)
    response = client_for(review_app, "admin").get(f"/admin/sports-news/{slug}/package.zip")
    assert response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
    with zipfile.ZipFile(BytesIO(response.data)) as archive:
        assert json.loads(archive.read("package.json"))["delivered"] is False
