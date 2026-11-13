import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager
import pytest

from blog_feed import feed, initial_html, public_cards
from react_shell import ReactShell, _page_meta
import paywall
from web_api import api


def post(index=1, **overrides):
    return {"title": f"Article {index}", "article_url": f"/blog/nba/article-{index}",
            "card_excerpt": "Published excerpt.", "status": "published",
            "image": "/static/pl-logo.svg", "sport_tag": "NBA",
            "display_date": "November 12, 2026", "date": "2026-11-12",
            "provenance": {"private": "do not publish"}, "social_drafts": {"package": "private"},
            **overrides}


def test_published_groups_use_only_public_fields_without_mutating_articles():
    rows = [post(kind="sports_news"), post(2), post(3, status="draft"), post(4, status="review")]
    before = copy.deepcopy(rows)
    payload = feed(rows)
    assert [row["title"] for row in payload["news"]["items"]] == ["Article 1"]
    assert [row["title"] for row in payload["latest"]["items"]] == ["Article 2"]
    assert "private" not in json.dumps(payload)
    assert set(payload["news"]["items"][0]) == {
        "url", "title", "excerpt", "image", "imageIsLogo", "sport", "date", "displayDate"}
    assert rows == before


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "//outside.test/blog/a", "https://outside.test/blog/a",
    "/admin/sports-news", "/blog/../admin", "/blog/%2e%2e/admin", "/blog/\\outside",
])
def test_nonarticle_destinations_are_not_exposed(url):
    assert public_cards([post(article_url=url)]) == {"news": [], "latest": []}


def test_bad_images_use_the_public_logo_and_duplicate_urls_do_not_duplicate_cards():
    row = post(image="javascript:alert(1)")
    result = feed([row, row])
    assert result["latest"]["total"] == 1
    assert result["latest"]["items"][0]["image"] == "/static/pl-logo.svg"


def test_pagination_preserves_archive_order_and_bounds_response_size():
    rows = [post(index) for index in range(75)]
    result = feed(rows)
    assert len(result["latest"]["items"]) == 30
    assert result["latest"]["nextOffset"] == 30
    more = feed(rows, "latest", 60, 30)
    assert list(more) == ["latest"]
    assert len(more["latest"]["items"]) == 15
    assert more["latest"]["nextOffset"] is None
    assert more["latest"]["items"][0]["title"] == "Article 60"


@pytest.mark.parametrize("section,offset,limit", [("drafts", 0, 30), (None, -1, 30), (None, 0, 0), (None, 0, 1000)])
def test_invalid_pagination_rejected(section, offset, limit):
    with pytest.raises(ValueError):
        feed([], section, offset, limit)


def test_initial_html_keeps_escaped_public_article_links():
    payload = feed([post(title='<img src=x onerror="alert(1)">')])
    html = initial_html(payload)
    assert 'href="/blog/nba/article-1"' in html
    assert "&lt;img" in html and "<img" not in html
    assert "Sports News" in html and "Latest from PredictionLab" in html
    assert "/classic/blog" in html


def app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="blog-test-only")
    LoginManager(app).user_loader(lambda identity: None)
    app.register_blueprint(api)
    paywall.install_access_policy(app)
    return app


@pytest.mark.parametrize("premium", [False, True])
def test_feed_is_public_but_internal_fields_and_other_private_routes_stay_protected(premium):
    application = app()
    with patch.dict("sys.modules", {
        "NHL77FINAL": SimpleNamespace(_blog_template_posts=lambda: [post(kind="sports_news"), post(2)]),
        "auth_system": SimpleNamespace(is_premium_user=lambda: premium),
    }):
        response = application.test_client().get("/api/web/blog")
        assert response.status_code == 200
        assert response.json["news"]["items"][0]["url"] == "/blog/nba/article-1"
        assert "provenance" not in response.get_data(as_text=True)
        if not premium:
            assert application.test_client().get("/api/web/props-hub").status_code == 401


def test_api_invalid_query_and_source_failure_are_not_successful_empty_feeds():
    application = app()
    site = SimpleNamespace(_blog_template_posts=lambda: [])
    with patch.dict("sys.modules", {"NHL77FINAL": site, "auth_system": SimpleNamespace(is_premium_user=lambda: False)}):
        client = application.test_client()
        assert client.get("/api/web/blog?offset=bad").status_code == 400
        assert client.get("/api/web/blog?section=drafts").status_code == 400
        def broken():
            raise RuntimeError("fixture")
        site._blog_template_posts = broken
        assert client.get("/api/web/blog").status_code == 503


def test_shell_bootstrap_retains_seo_links_tags_and_classic_article_paths():
    payload = feed([post(title="News </script> safe", kind="sports_news"), post(2)])
    def wrapped(environ, start_response):
        start_response("200 OK", [("Content-Type", "application/json")])
        if environ["PATH_INFO"] == "/api/web/blog":
            return [json.dumps(payload).encode()]
        return [b'{"header":"","footer":""}']
    shell = ReactShell(wrapped, add_site_tags=lambda html: html.replace("</head>", '<script src="/tracking-fixture.js"></script></head>'))
    markup = '<html><head><title>Fixture</title></head><body><div id="root"></div></body></html>'
    environ = {"PATH_INFO": "/blog", "REQUEST_METHOD": "GET", "HTTP_HOST": "127.0.0.1",
               "werkzeug.socket": object(), "QUERY_STRING": ""}
    with patch.object(shell, "_shell_html", return_value=markup):
        status = []
        html = b"".join(shell(environ, lambda s, h: status.append(s))).decode()
    assert status == ["200 OK"]
    assert 'href="https://predictionlab.io/blog"' in html
    assert 'href="/blog/nba/article-1"' in html
    assert 'window.__PL_BLOG__=' in html
    assert 'News <\\/script> safe' in html
    assert '/tracking-fixture.js' in html
    assert 'property="og:type" content="website"' in html
    assert 'name="robots" content="index,follow"' in html
    assert _page_meta("/blog/nba/article-1") is None
    assert _page_meta("/classic/blog") is None


@pytest.mark.parametrize("body", [b'{"error":"unavailable"}', b'not-json', b'{}'])
def test_blog_shell_keeps_classic_fallback_when_feed_fails(body):
    def wrapped(environ, start_response):
        start_response("503 Unavailable", [("Content-Type", "application/json")])
        return [body]
    shell = ReactShell(wrapped)
    html = shell._blog_index({"PATH_INFO": "/blog", "QUERY_STRING": ""}, '<div id="root"></div>')
    assert 'href="/classic/blog"' in html
