import ast
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from flask import Flask, render_template
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

from blog_presentation import public_blog_post
from blog_urls import article_aliases, article_description, public_article_path, publication_dates, slug_words
from sport_blog_previews import article_json_ld, sitemap_article_entries
from sports_news_media import social_copy

ROOT = Path(__file__).resolve().parents[1]


def records():
    return (json.loads((ROOT / "data/sports_news_posts.json").read_text())["posts"] +
            json.loads((ROOT / "data/blog_posts.json").read_text())["posts"])


def test_requested_college_football_url_describes_the_story():
    post = next(p for p in records() if p.get("kind") == "sports_news" and p["sport_tag"] == "NCAAF")
    assert public_article_path(post) == (
        "/blog/ncaaf/western-michigan-broncos-28-point-win-college-football-recap-2026-11-10"
    )
    assert post["slug"] in article_aliases(post)
    assert public_article_path(post).rsplit("/", 1)[-1] in article_aliases(post)


def test_news_url_stays_stable_when_headline_changes():
    post = next(p for p in records() if p.get("kind") == "sports_news")
    old = public_article_path(post)
    changed = {**post, "title": "An edited headline"}
    assert public_article_path(changed) == old


def test_future_game_date_is_not_a_publication_timestamp():
    post = {"date": "2026-11-14", "date_published": "2026-11-14",
            "date_modified": "2026-11-10"}
    published, modified = publication_dates(post, datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert published is None
    assert modified == "2026-11-10T00:00:00-05:00"


def test_unicode_full_matchup_names_and_date_in_preview_url():
    assert slug_words("Atlético-MG vs São Paulo") == "atletico-mg-vs-sao-paulo"
    post = {"sport_tag": "SOCCER", "away": "Atlético-MG", "home": "São Paulo",
            "slug": "old-short-name-2026-11-14", "canonical_path": "/blog/soccer/old-short-name-2026-11-14",
            "date": "2026-11-12"}
    assert public_article_path(post) == "/blog/soccer/atletico-mg-vs-sao-paulo-game-preview-2026-11-14"
    assert post["slug"] in article_aliases(post)


def test_archive_has_no_descriptive_url_collisions_between_distinct_games():
    seen = {}
    for post in records():
        path = public_article_path(post)
        identity = (post.get("sport_tag"), post.get("game_date") or post.get("date"),
                    post.get("away"), post.get("home"), post.get("kind"))
        assert path not in seen or seen[path] == identity, path
        seen[path] = identity
        assert re.fullmatch(r"/blog/[a-z]+/[a-z0-9-]+", path), path


def test_existing_and_descriptive_slugs_resolve_to_same_stored_identity():
    tree = ast.parse((ROOT / "NHL77FINAL.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_match_blog_post")
    namespace = {"_slugify_blog": lambda value: re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "article-route-fixture", "exec"), namespace)
    for post in records()[0:10]:
        old, new = post["slug"], public_article_path(post).rsplit("/", 1)[-1]
        a = namespace["_match_blog_post"]([post], old)
        b = namespace["_match_blog_post"]([post], new)
        assert a["slug"] == b["slug"] == post["slug"]
        assert a["canonical_path"] == b["canonical_path"] == public_article_path(post)


def test_urls_and_metadata_do_not_change_original_records():
    posts = records()
    frozen = deepcopy(posts)
    for post in posts:
        shown = public_blog_post(post)
        description = article_description(shown)
        assert description and len(description) <= 160
        assert not re.search(r"\b(?:odds|betting|sportsbook|moneyline)\b", description, re.I)
        assert shown["standfirst"] == shown["meta_description"] == description
        assert article_json_ld(shown)[0]["mainEntityOfPage"]["@id"].endswith(public_article_path(post))
    assert posts == frozen


def test_sitemap_and_social_copy_use_descriptive_canonical_urls():
    posts = records()
    sitemap = {row[0] for row in sitemap_article_entries(posts)}
    for post in posts:
        if post.get("status", "published") == "published":
            assert "https://predictionlab.io" + public_article_path(post) in sitemap
        if post.get("kind") == "sports_news":
            assert social_copy(post)["article_url"] == "https://predictionlab.io" + public_article_path(post)


def test_article_description_and_sharing_metadata_are_visible_and_consistent():
    app = Flask(__name__)
    app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": "<html><head>{% block head_meta %}{% endblock %}</head>"
                                "<body>{% block content %}{% endblock %}</body></html>"}),
        FileSystemLoader(ROOT / "templates"),
    ])
    for source in (records()[0], records()[-1]):
        post = public_blog_post(source)
        with app.test_request_context():
            html = render_template("blog_article.html", post=post, page_title=post["title"],
                                   page_description=post["meta_description"],
                                   canonical_url="https://predictionlab.io"+post["canonical_path"],
                                   og_image="https://predictionlab.io/static/pl-logo.svg",
                                   json_ld_blocks=article_json_ld(post))
        for name in ("description", "twitter:title", "twitter:description", "twitter:image"):
            assert f'name="{name}"' in html
        assert 'property="og:type" content="article"' in html
        assert 'class="standfirst"' in html
        assert post["title"] in html
