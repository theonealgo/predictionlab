"""Public blog-index cards only; articles and editorial storage stay unchanged."""
from html import escape
import posixpath
from urllib.parse import unquote, urlsplit

PAGE_SIZE = 30
MAX_PAGE_SIZE = 60


def _article_url(value):
    try:
        url = urlsplit(str(value or ""))
    except ValueError:
        return None
    path = url.path
    if url.scheme or url.netloc or not path.startswith("/blog/") or "\\" in path:
        return None
    decoded = unquote(path)
    if posixpath.normpath(decoded) != decoded or "\\" in decoded:
        return None
    return path


def _image_url(value):
    value = str(value or "")
    try:
        url = urlsplit(value)
    except ValueError:
        return "/static/pl-logo.svg"
    if (value.startswith("/") and not value.startswith("//")) or url.scheme in ("https", "http"):
        return value
    return "/static/pl-logo.svg"


def public_cards(posts):
    news, latest, seen = [], [], set()
    for post in posts:
        if not isinstance(post, dict) or str(post.get("status") or "published").lower() != "published":
            continue
        url = _article_url(post.get("article_url") or post.get("canonical_path"))
        if not url or url in seen:
            continue
        seen.add(url)
        card = {
            "url": url, "title": str(post.get("title") or "PredictionLab article"),
            "excerpt": str(post.get("card_excerpt") or post.get("excerpt") or "")[:300],
            "image": _image_url(post.get("image")),
            "imageIsLogo": bool(post.get("image_is_logo")),
            "sport": str(post.get("sport_tag") or ""),
            "date": str(post.get("date_published") or post.get("date") or ""),
            "displayDate": str(post.get("display_date") or ""),
        }
        (news if post.get("kind") == "sports_news" else latest).append(card)
    return {"news": news, "latest": latest}


def feed(posts, section=None, offset=0, limit=PAGE_SIZE):
    if section not in (None, "news", "latest") or offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("Invalid blog page")
    groups = public_cards(posts)
    result = {}
    for key in ([section] if section else ("news", "latest")):
        rows = groups[key]
        items = rows[offset:offset+limit]
        result[key] = {"items": items, "total": len(rows),
                       "nextOffset": offset+len(items) if offset+len(items) < len(rows) else None}
    return result


def initial_html(payload):
    """Visible article links before React loads, including when JavaScript is off."""
    sections = []
    for key, title in (("news", "Sports News"), ("latest", "Latest from PredictionLab")):
        cards = []
        for row in (payload.get(key) or {}).get("items") or []:
            url = _article_url(row.get("url"))
            if not url:
                continue
            cards.append(
                '<article class="pl-blog-card"><a href="' + escape(url, quote=True) + '">'
                '<h3>' + escape(str(row.get("title") or "")) + '</h3><p>'
                + escape(str(row.get("excerpt") or "")) + '</p></a></article>'
            )
        sections.append('<section class="pl-blog-window" aria-label="' + title + '"><h2>'
                        + title + '</h2><div class="pl-blog-scroll">'
                        + ("".join(cards) or "<p>No articles available right now.</p>") + "</div></section>")
    return ('<main class="pl-blog-page"><h1>PredictionLab Blog</h1>'
            '<p><a href="/classic/blog#soro-blog">Trending in Sports</a></p>'
            '<div class="pl-blog-columns">' + "".join(sections) + '</div>'
            '<p><a href="/classic/blog">Browse the complete article archive</a></p></main>')
