import ast
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re
from types import SimpleNamespace

from flask import Flask, render_template_string
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
import pytest

from blog_presentation import public_blog_post
from blog_urls import article_aliases, public_article_path
from sport_blog_previews import article_json_ld, sitemap_article_entries

ROOT = Path(__file__).resolve().parents[1]
COMMERCIAL = re.compile(r"\b(?:gambling|betting|sportsbooks?|odds|moneyline|wager|parlay)\b", re.I)


@lru_cache
def archive_template():
    tree = ast.parse((ROOT / "NHL77FINAL.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == "BLOG_ARCHIVE_TEMPLATE" for t in n.targets))
    return ast.literal_eval(node.value)


def preview():
    return json.loads((ROOT / "data/blog_posts.json").read_text())["posts"][0]


def news():
    return json.loads((ROOT / "data/sports_news_posts.json").read_text())["posts"][0]


def test_preview_copy_preserves_game_facts_and_original_archive():
    source = preview()
    frozen = deepcopy(source)
    post = public_blog_post(source)
    assert source == frozen
    for key in ("slug", "date", "away", "home"):
        assert post[key] == source[key]
    assert post["canonical_path"] == public_article_path(source)
    assert source["slug"] in article_aliases(source)
    assert post["sections"]["models"] == source["sections"]["models"]
    assert post["sections"]["game_info"] == source["sections"]["game_info"]
    assert set(post["sections"]["markets"]) <= {"pl_proj", "xs_proj"}
    text = " ".join([post["title"], post["seo_title"], post["excerpt"], post["meta_description"],
                     *post["body"], *post["sections"]["intro"], *post["sections"]["why"],
                     post["sections"]["takeaway"], post["sections"]["disclaimer"]])
    assert not COMMERCIAL.search(text)
    assert "Game Preview" in post["title"]


def test_every_archived_preview_is_kept_with_clean_public_text():
    originals = json.loads((ROOT / "data/blog_posts.json").read_text())["posts"]
    for original in originals:
        post = public_blog_post(original)
        for key in ("title", "seo_title", "excerpt", "meta_description"):
            assert not COMMERCIAL.search(post.get(key, "")), (original["slug"], key)
        assert post["canonical_path"] == public_article_path(original)


def test_news_keeps_factual_article_and_has_no_model_disclaimer():
    source = news()
    post = public_blog_post(source)
    assert post["provenance"] == source["provenance"]
    assert post["body"] == source["body"]
    assert post["sections"]["disclaimer"] == ""
    assert article_json_ld(post)[0]["headline"] == post["title"]
    assert sitemap_article_entries([post])[0][0].endswith(post["canonical_path"])


@pytest.mark.parametrize("admin", [False, True])
def test_public_blog_orders_soro_news_previews_and_has_only_article_links(admin):
    app = Flask(__name__)
    app.jinja_loader = ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}"}),
        FileSystemLoader(ROOT / "templates"),
    ])
    new, old = public_blog_post(news()), public_blog_post(preview())
    for post in (new, old):
        post.update(display_date=post["date"], card_excerpt=post["excerpt"],
                    article_url=post["canonical_path"], image_is_logo=False)
    with app.test_request_context("/blog"):
        html = render_template_string(archive_template(), news_posts=[new], preview_posts=[old],
                                      current_user=SimpleNamespace(is_authenticated=admin, is_admin=admin))
    assert html.index('id="soro-blog"') < html.index('id="sports-news"') < html.index("Latest from Prediction Lab")
    assert f'href="{new["canonical_path"]}"' in html
    assert f'href="{old["canonical_path"]}"' in html
    assert html.count(f'id="{new["slug"]}"') == 1
    assert "<details" not in html
    for forbidden in ("sports-news/review", "/admin/", ".zip", "Download social", "Prepare yesterday", "Return to draft"):
        assert forbidden not in html
    assert not COMMERCIAL.search(re.sub("<[^>]+>", "", html))


def test_soro_embed_unchanged():
    template = archive_template()
    start = template.index('<div id="soro-blog"')
    stop = template.index("</script>", start) + len("</script>")
    expected = (
        '<div id="soro-blog"></div>\n'
        '        <script src="https://app.trysoro.com/api/embed/7713f25e-b95b-4eb2-a414-ec63a136d16f" defer></script>'
    )
    assert template[start:stop] == expected
