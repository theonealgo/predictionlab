"""Server-rendered daily blog SEO section and routes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _fake_picks():
    return [
        {
            "sport": "MLB",
            "away": "Seattle Mariners",
            "home": "Baltimore Orioles",
            "pick": "Baltimore Orioles",
            "prob": 51.3,
            "slug": "mlb-picks",
        }
    ]


def _fake_news():
    return [
        {
            "sport": "MLB",
            "topic": "Baltimore updates its rotation before a divisional matchup",
            "summary_hint": "Rotation news can change the market.",
            "source": "ESPN",
            "url": "https://www.espn.com/mlb/",
        }
    ]


def test_homepage_renders_daily_blog_section_below_results(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, "build_todays_top_picks", _fake_picks)
    monkeypatch.setattr(N, "_fetch_espn_news_items", lambda limit=5: _fake_news()[:limit])
    N._BLOG_CACHE.update({"ts": 0, "posts": []})

    with N.app.test_client() as client:
        resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Daily Betting Results Report" in html
    assert "Yesterday's performance across all sports and models" in html
    assert "Prediction Lab Blog" in html
    assert "Latest Daily Article:" in html
    assert "View All Articles" in html
    assert 'href="/blog"' in html
    assert 'href="/blog/prediction-lab-blog-' in html
    assert html.index("Daily Betting Results Report") < html.index("Prediction Lab Blog")


def test_blog_archive_and_latest_daily_article_routes_render(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, "build_todays_top_picks", _fake_picks)
    monkeypatch.setattr(N, "_fetch_espn_news_items", lambda limit=5: _fake_news()[:limit])
    N._BLOG_CACHE.update({"ts": 0, "posts": []})
    latest = N._get_latest_blog_post(todays_picks=_fake_picks())

    with N.app.test_client() as client:
        archive = client.get("/blog")
        post = client.get(f"/blog/{latest['slug']}")

    archive_html = archive.get_data(as_text=True)
    post_html = post.get_data(as_text=True)

    assert archive.status_code == 200
    assert post.status_code == 200
    assert "<section" in archive_html
    assert "<article" in archive_html
    escaped_title = latest["title"].replace("&", "&amp;")
    assert escaped_title in archive_html
    assert escaped_title in post_html
    assert "Sports News Watch" in post_html
    assert "Baltimore updates its rotation" in post_html
    assert "Source" in post_html
    assert "Seattle Mariners" in post_html
    assert "Baltimore Orioles" in post_html
    assert "/daily-report" in post_html


def test_latest_blog_prefers_newer_json_post(monkeypatch):
    import NHL77FINAL as N

    json_post = {
        "title": "NBA Market Breakdown",
        "slug": "nba-market-breakdown",
        "date": "2099-01-01",
        "sport_tag": "NBA",
        "excerpt": "A newer manually created article should win.",
        "body": ["A newer manually created article should win."],
    }
    monkeypatch.setattr(N, "_load_blog_posts_from_json", lambda: [json_post])
    monkeypatch.setattr(N, "_fetch_espn_news_items", lambda limit=5: _fake_news()[:limit])

    latest = N._get_latest_blog_post(todays_picks=_fake_picks())

    assert latest["slug"] == "nba-market-breakdown"
    assert latest["sport_tag"] == "NBA"


def test_sitemap_includes_blog_archive_and_latest_article(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, "build_todays_top_picks", _fake_picks)
    monkeypatch.setattr(N, "_fetch_espn_news_items", lambda limit=5: _fake_news()[:limit])
    N._BLOG_CACHE.update({"ts": 0, "posts": []})
    latest = N._get_latest_blog_post(todays_picks=_fake_picks())

    with N.app.test_client() as client:
        resp = client.get("/sitemap.xml")
    xml = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "https://predictionlab.io/blog" in xml
    assert f"https://predictionlab.io/blog/{latest['slug']}" in xml
