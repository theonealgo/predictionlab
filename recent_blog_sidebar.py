"""Blog rail on classic Models pages when React shell is not wrapping the response.

Sports News + Latest from PredictionLab. Never Soro. React BlogFeedBox is the
primary UI; this is the fallback for classic HTML.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)

BLOG_RAIL_PATHS = frozenset(
    {
        "/performance",
        "/ladder",
        "/ladder-picks",
        "/daily-report",
        "/our-model-vs-sportsbooks",
        "/our-model-vs-market",
        "/tutorial",
        "/ai-sports-betting-picks-today",
        "/ai-sports-predictions-today",
        "/ai-sports-projections-today",
    }
)

_CSS = """
<style id="pl-blog-rail-css">
.pl-with-blog-rail{display:flex;justify-content:flex-start;align-items:flex-start;gap:40px;max-width:1400px;margin:0 auto;padding:0 16px 28px;box-sizing:border-box}
.pl-with-blog-rail > .pl-blog-rail-main{flex:3 1 640px;min-width:0}
.pl-blog-rail.pl-right{flex:2 1 360px;min-width:280px;max-width:420px;position:sticky;top:72px;align-self:start;padding:16px;border:1px solid #e2e8f0;border-radius:16px;background:#fff;box-sizing:border-box}
.pl-blog-rail .pl-right-title{margin:0 0 12px;font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#64748b}
.pl-blog-rail .pl-rail-stack{display:flex;flex-direction:column;gap:14px}
.pl-blog-rail .pl-rail-window{border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;background:#fff}
.pl-blog-rail .pl-rail-window h2{margin:0;padding:12px 14px;font-size:15px;border-bottom:1px solid #e5e7eb;background:#f8fafc}
.pl-blog-rail .pl-news-scroll{max-height:min(320px,36vh);overflow-y:auto;overscroll-behavior-y:contain;padding:0 12px}
.pl-blog-rail .pl-news-item{display:grid;grid-template-columns:72px minmax(0,1fr);gap:10px;padding:12px 0;border-bottom:1px solid #e2e8f0;text-decoration:none;color:inherit}
.pl-blog-rail .pl-news-item:last-of-type{border-bottom:none}
.pl-blog-rail .pl-news-item img{width:72px;height:54px;object-fit:cover;border-radius:8px;background:#f1f5f9;display:block}
.pl-blog-rail .pl-news-item strong{display:block;font-size:14px;line-height:1.3;font-weight:700;color:#00529B}
.pl-blog-rail .pl-news-item:hover strong{text-decoration:underline}
.pl-blog-rail .pl-news-item em{display:block;margin-top:4px;font-style:normal;font-size:12px;color:#64748b}
.pl-blog-rail .pl-news-all{display:inline-block;margin-top:12px;font-size:13px;font-weight:700;color:#00529B;text-decoration:none}
.pl-blog-rail .pl-news-all:hover{text-decoration:underline}
@media(max-width:980px){
  .pl-with-blog-rail{flex-direction:column;gap:18px;padding:0 12px 24px}
  .pl-blog-rail.pl-right{position:static;max-width:none;width:100%}
}
</style>
"""


def path_wants_rail(path: str) -> bool:
    p = (path or "").rstrip("/") or "/"
    return p in BLOG_RAIL_PATHS


def _posts_by_kind() -> tuple[list[dict], list[dict]]:
    """(sports_news, latest_predictionlab) — never Soro."""
    try:
        from NHL77FINAL import _blog_template_posts

        posts = _blog_template_posts()
    except Exception as exc:
        logger.debug("recent blog rail: template posts unavailable: %s", exc)
        posts = []
    news, latest = [], []
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or "").strip()
        if not title:
            continue
        kind = str(p.get("kind") or "").strip().lower()
        if kind == "sports_news":
            news.append(p)
        else:
            latest.append(p)
    return news, latest


def _items_html(posts: Iterable[dict]) -> str:
    items = []
    for p in posts:
        title = html_lib.escape(str(p.get("title") or "").strip())
        sport = html_lib.escape(str(p.get("sport_tag") or "").strip())
        date = html_lib.escape(str(p.get("display_date") or p.get("date") or "").strip())
        meta = " · ".join(x for x in (sport, date) if x)
        href = html_lib.escape(str(p.get("article_url") or "/blog"))
        img = html_lib.escape(str(p.get("image") or "/static/pl-logo.svg"))
        items.append(
            f'<a class="pl-news-item" href="{href}">'
            f'<img src="{img}" alt="" loading="lazy" width="72" height="54">'
            f"<span><strong>{title}</strong>"
            f'{f"<em>{meta}</em>" if meta else ""}</span></a>'
        )
    return "".join(items)


def _rail_html() -> str:
    news, latest = _posts_by_kind()
    windows = []
    for title, rows in (("Sports News", news), ("Latest from PredictionLab", latest)):
        body = _items_html(rows[:40])
        if not body:
            body = '<p style="padding:12px;color:#64748b;font-size:14px">No articles available right now.</p>'
        windows.append(
            f'<section class="pl-rail-window"><h2>{html_lib.escape(title)}</h2>'
            f'<div class="pl-news-scroll">{body}</div></section>'
        )
    if not news and not latest:
        return ""
    return (
        '<aside class="pl-blog-rail pl-right" aria-label="PredictionLab blog">'
        '<div class="pl-right-title">Blogs</div>'
        f'<div class="pl-rail-stack">{"".join(windows)}</div>'
        '<a class="pl-news-all" href="/blog">View all articles →</a>'
        "</aside>"
    )


def inject_recent_blog_rail(page_html: str, path: str = "") -> str:
    """Put the Blogs box beside the page main content. Idempotent."""
    if not page_html or "pl-blog-rail" in page_html or "pl-blog-feed-box" in page_html:
        return page_html
    if path and not path_wants_rail(path):
        return page_html
    rail = _rail_html()
    if not rail:
        return page_html

    html = page_html
    if 'id="pl-blog-rail-css"' not in html:
        if "</head>" in html.lower():
            html = re.sub(r"</head\s*>", _CSS + "</head>", html, count=1, flags=re.I)
        else:
            html = _CSS + html

    wrappers = (
        r'(<div class="perf-wrap\b[^"]*"[^>]*>)',
        r'(<div class="ld-wrap\b[^"]*"[^>]*>)',
        r'(<div class="tutorial-wrap\b[^"]*"[^>]*>)',
        r'(<article class="legal-card\b[^"]*"[^>]*>)',
        r'(<div class="rpt-wrap\b[^"]*"[^>]*>)',
        r'(<main\b[^>]*>)',
        r'(<div class="content-wrap\b[^"]*"[^>]*>)',
    )
    for pat in wrappers:
        m = re.search(pat, html, flags=re.I)
        if not m:
            continue
        open_tag = m.group(1)
        start = m.start()
        tag = re.match(r"<(div|article|main)\b", open_tag, flags=re.I)
        if not tag:
            continue
        name = tag.group(1).lower()
        depth = 0
        end = None
        for t in re.finditer(rf"<{name}\b|</{name}\s*>", html[m.start() :], flags=re.I):
            token = t.group(0)
            if token.lower().startswith(f"</{name}"):
                depth -= 1
                if depth == 0:
                    end = m.start() + t.end()
                    break
            else:
                depth += 1
        if end is None:
            continue
        block = html[start:end]
        wrapped = (
            '<div class="pl-with-blog-rail">'
            f'<div class="pl-blog-rail-main">{block}</div>'
            f"{rail}"
            "</div>"
        )
        return html[:start] + wrapped + html[end:]

    footer = re.search(r"<footer\b", html, flags=re.I)
    if footer:
        insert = (
            '<div class="pl-with-blog-rail" style="padding-top:12px">'
            '<div class="pl-blog-rail-main"></div>'
            f"{rail}</div>"
        )
        return html[: footer.start()] + insert + html[footer.start() :]
    return html
