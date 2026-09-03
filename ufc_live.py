"""UFC picks/results — graft the locked :5081 hub UFC page into live chrome.

Source of truth: Sports Sandbox/independent_sports/hub (signed-off UFC).
Fallback copy: _sandbox_hub_run/hub (synced from staging before merge).

Keeps live site header/footer. Does not invent a second UFC UI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

UFC_ISO = Path.home() / "Documents/Personal/ufc"
_STAGING_HUB = Path("/Users/nimamesghali/Sports Sandbox/independent_sports/hub")
_LOCAL_HUB = Path(__file__).resolve().parent / "_sandbox_hub_run" / "hub"


def _hub_dir() -> Path | None:
    if _STAGING_HUB.is_dir() and (_STAGING_HUB / "ufc_page.py").is_file():
        return _STAGING_HUB
    if _LOCAL_HUB.is_dir() and (_LOCAL_HUB / "ufc_page.py").is_file():
        return _LOCAL_HUB
    return None


def _load_hub_modules():
    hub = _hub_dir()
    if not hub:
        raise RuntimeError("UFC hub not found (staging or _sandbox_hub_run)")
    hub_s = str(hub.resolve())
    # Drop stale hub imports so we always load the locked staging copy.
    for k in list(sys.modules):
        if k in {
            "ufc_page",
            "sandbox_fixup",
            "mlb_page_template",
            "share_chrome",
            "shared_chrome",
            "team_tabbed_results",
            "mlb_three_way_consensus",
            "total_edge",
        } or k.startswith("ufc_iso_"):
            sys.modules.pop(k, None)
    if hub_s in sys.path:
        sys.path.remove(hub_s)
    sys.path.insert(0, hub_s)
    import ufc_page  # noqa: WPS433

    return ufc_page


def _replace_container(html: str, body: str) -> str:
    start = re.search(r'<div class="container\b[^"]*"[^>]*>', html, flags=re.I)
    if start:
        i = start.end()
        depth = 1
        j = i
        while j < len(html) and depth > 0:
            next_open = html.find("<div", j)
            next_close = html.find("</div>", j)
            if next_close < 0:
                break
            if next_open >= 0 and next_open < next_close:
                depth += 1
                j = next_open + 4
            else:
                depth -= 1
                if depth == 0:
                    return html[:i] + "\n" + body + "\n" + html[next_close:]
                j = next_close + 6
    m = re.search(r"(<main\b[^>]*>)([\s\S]*?)(</main>)", html, flags=re.I)
    if m:
        return html[: m.start(2)] + f'<div class="container">\n{body}\n</div>' + html[m.end(2) :]
    return html + f'<div class="container">{body}</div>'


def _strip_site_directory_footers(html: str) -> str:
    """Remove site-directory footers only — never ``footer.card-footer`` on cards."""
    if not html:
        return html
    html = re.sub(
        r'<footer\b[^>]*\bclass=["\'][^"\']*\bsite-directory-footer\b[^"\']*["\'][^>]*>[\s\S]*?</footer>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div\b[^>]*(?:id|class)=["\'][^"\']*\bsite-directory-footer\b[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    return html


def _extract_hub_main(html: str) -> str:
    """Hub main content for graft — sport UI only, no hub header/footer.

    Critical: do NOT strip the first ``<footer>`` — pick cards use
    ``<footer class="card-footer">``. Stripping that left the hub
    ``site-directory-footer`` in the graft and duplicated the live footer.
    """
    if not html:
        return ""
    body_m = re.search(r"<body\b[^>]*>([\s\S]*)</body>", html, flags=re.I)
    content = body_m.group(1) if body_m else html
    content = re.sub(r"<header\b[^>]*>[\s\S]*?</header>", "", content, flags=re.I)
    content = _strip_site_directory_footers(content)
    # Drop leftover header account/burger scripts that sit after </header>
    content = re.sub(
        r"<script\b[^>]*>[\s\S]*?pl2-account[\s\S]*?</script>",
        "",
        content,
        flags=re.I,
    )
    content = re.sub(
        r"<script\b[^>]*>[\s\S]*?pl2-burger[\s\S]*?</script>",
        "",
        content,
        flags=re.I,
    )
    # Prefer sport content from the Cards|Chart toggle onward (drops empty chrome)
    start = re.search(
        r'(?:<div class="container\b[^"]*"[^>]*>\s*)?(<div class="pl-view-toggle"[\s\S]*)',
        content,
        flags=re.I,
    )
    if start:
        content = start.group(1)
    # Unwrap a single outer container wrapper if present (live page already has one)
    wrap = re.match(
        r'^<div class="container\b[^"]*"[^>]*>([\s\S]*)</div>\s*$',
        content.strip(),
        flags=re.I,
    )
    if wrap and "pl-view-toggle" in wrap.group(1):
        content = wrap.group(1)
    return content.strip()


def _strip_live_ufc_chrome_dupes(html: str) -> str:
    """Remove live-template view/date chrome that fights the grafted hub UI."""
    if not html:
        return html
    # Live Cards|Chart button pair (hub brings pl-view-toggle)
    html = re.sub(
        r'<div class="picks-view-toggle"[^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<button\b[^>]*\bid=["\']pvCardsBtn["\'][^>]*>[\s\S]*?</button>\s*'
        r'<button\b[^>]*\bid=["\']pvChartBtn["\'][^>]*>[\s\S]*?</button>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<button\b[^>]*\bid=["\']pv(?:Cards|Chart)Btn["\'][^>]*>[\s\S]*?</button>',
        "",
        html,
        flags=re.I,
    )
    # Orphan "Today" / pc-slate date bubbles from live scaffold
    html = re.sub(
        r'<button\b[^>]*>\s*📅\s*Today\s*</button>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="date-nav"[^>]*>[\s\S]*?pc-slate[\s\S]*?</div>',
        "",
        html,
        count=1,
        flags=re.I,
    )
    return html


def _dedupe_site_directory_footers(html: str) -> str:
    """Keep a single live site-directory footer (last one)."""
    if not html or html.count("site-directory-footer") < 2:
        return html
    matches = list(
        re.finditer(
            r'<footer\b[^>]*\bclass=["\'][^"\']*\bsite-directory-footer\b[^"\']*["\'][^>]*>[\s\S]*?</footer>',
            html,
            flags=re.I,
        )
    )
    if len(matches) < 2:
        return html
    parts: list[str] = []
    last = 0
    for m in matches[:-1]:
        parts.append(html[last : m.start()])
        parts.append("<!-- ufc-deduped-site-footer -->")
        last = m.end()
    parts.append(html[last:])
    return "".join(parts)


def _dedupe_share_blocks(html: str) -> str:
    """Hub graft already includes share image + social strip — drop extras."""
    if not html:
        return html
    # Multiple "Share on X" clusters → keep first (hub), drop later full share-strips
    if html.count("Share on X") < 2:
        return html
    matches = list(
        re.finditer(
            r'<div class="share-strip"[^>]*>[\s\S]*?</div>\s*(?:<div class="share-strip"[^>]*>[\s\S]*?</div>)?',
            html,
            flags=re.I,
        )
    )
    # Broader: share-social sections
    matches = list(
        re.finditer(
            r'<div\b[^>]*class=["\'][^"\']*\bshare-(?:strip|social|bar|row)\b[^"\']*["\'][^>]*>[\s\S]*?</div>',
            html,
            flags=re.I,
        )
    )
    if len(matches) < 2:
        return html
    # Keep first, remove subsequent that contain Share on X
    keep_idxs = set()
    seen = 0
    parts: list[str] = []
    last = 0
    for m in matches:
        chunk = m.group(0)
        if "Share on X" not in chunk and "Share on Facebook" not in chunk:
            continue
        seen += 1
        if seen == 1:
            continue
        parts.append(html[last : m.start()])
        parts.append("<!-- ufc-deduped-share -->")
        last = m.end()
    if not parts:
        return html
    parts.append(html[last:])
    return "".join(parts)


def _graft_hub_into_live(live_html: str, hub_body: str) -> str:
    """Replace live main content between header and site footer with hub body.

    Falls back to container replace if chrome markers are missing.
    """
    if not live_html or not hub_body:
        return live_html
    # Prefer: after </header> … before site-directory-footer
    header_end = re.search(r"</header\s*>", live_html, flags=re.I)
    footer_m = re.search(
        r'<footer\b[^>]*\bclass=["\'][^"\']*\bsite-directory-footer\b',
        live_html,
        flags=re.I,
    )
    if header_end and footer_m and header_end.end() < footer_m.start():
        mid = f'\n<div class="container">\n{hub_body}\n</div>\n'
        return live_html[: header_end.end()] + mid + live_html[footer_m.start() :]
    return _replace_container(live_html, hub_body)

def _merge_hub_head_assets(live_html: str, hub_html: str) -> str:
    """Pull picks-chart / share / unlock CSS+JS from locked hub page into live <head>."""
    if not live_html or not hub_html:
        return live_html
    chunks: list[str] = []
    for pat in (
        r"<style[^>]*id=\"[^\"]*(?:share-social|sandbox-hide-books|mlb-tpl|picks-chart|unlock|ufc)[^\"]*\"[^>]*>[\s\S]*?</style>",
        r"<style[^>]*id=\"sandbox-unlock-premium\"[^>]*>[\s\S]*?</style>",
        r"<link[^>]+picks-chart\.css[^>]*>",
        r"<script>\s*window\.PICKS_CHART\s*=[\s\S]*?</script>",
        r"<script[^>]+picks-chart\.js[^>]*>\s*</script>",
    ):
        for m in re.finditer(pat, hub_html, flags=re.I):
            chunk = m.group(0)
            if chunk and chunk not in live_html and chunk not in chunks:
                chunks.append(chunk)
    if not chunks:
        return live_html
    inject = "\n".join(chunks)
    if re.search(r"</head\s*>", live_html, flags=re.I):
        return re.sub(r"</head\s*>", inject + "\n</head>", live_html, count=1, flags=re.I)
    return inject + live_html


def _rewrite_live_routes(html: str) -> str:
    """Hub uses /ufc/ + /ufc/results; live product uses /ufc-picks + /ufc-results.

    Keep /ufc/share.jpg (live route registered to match locked hub).
    """
    if not html:
        return html
    html = html.replace('href="/ufc/results"', 'href="/ufc-results"')
    html = html.replace("href='/ufc/results'", "href='/ufc-results'")
    html = html.replace('href="/ufc/"', 'href="/ufc-picks"')
    html = html.replace("href='/ufc/'", "href='/ufc-picks'")
    # Share social intent URLs that baked hub port
    html = re.sub(
        r"http%3A%2F%2F127\.0\.0\.1%3A\d+%2Fufc%2F",
        "http%3A%2F%2F127.0.0.1%3A5052%2Fufc-picks",
        html,
    )
    html = re.sub(
        r"http://127\.0\.0\.1:\d+/ufc/",
        "http://127.0.0.1:5052/ufc-picks",
        html,
    )
    return html


def _strip_show_date_scripts(html: str) -> str:
    """Drop inline showDate/dateBubbles scripts.

    Live UFC templates leave a ~40k chart script outside ``.container``. After
    graft the hub body brings its own copy — two ``showDate`` runners race.
    The live one rebuilds bubbles from event dates (e.g. Sep 5) while cards sit
    under ``date-YYYY-MM-DD`` for the slate day (e.g. Sep 2), clears ``.visible``,
    and the fight cards never appear.
    """
    if not html or "function showDate" not in html:
        return html

    def _drop(m: re.Match[str]) -> str:
        body = m.group(1) or ""
        if "function showDate" in body and (
            "dateBubbles" in body or "date-bubble" in body or "date-section" in body
        ):
            return "<!-- ufc-stripped-showDate -->"
        return m.group(0)

    return re.sub(
        r"<script\b[^>]*>([\s\S]*?)</script>",
        _drop,
        html,
        flags=re.I,
    )


def _dedupe_show_date_scripts(html: str) -> str:
    """Keep a single showDate script (last wins — hub graft)."""
    if not html or html.count("function showDate") < 2:
        return html
    matches = list(
        re.finditer(r"<script\b[^>]*>[\s\S]*?function showDate[\s\S]*?</script>", html, flags=re.I)
    )
    if len(matches) < 2:
        return html
    parts: list[str] = []
    last = 0
    for m in matches[:-1]:
        parts.append(html[last : m.start()])
        parts.append("<!-- ufc-deduped-showDate -->")
        last = m.end()
    parts.append(html[last:])
    return "".join(parts)


def _ensure_ufc_date_sections_visible(html: str) -> str:
    """Force slate sections that hold cards to stay visible after date-nav races."""
    if not html or "date-section" not in html:
        return html
    html = re.sub(
        r'<div\b([^>]*\bclass=["\'][^"\']*\bdate-section\b[^"\']*["\'][^>]*)>',
        lambda m: (
            m.group(0)
            if "visible" in m.group(1)
            else m.group(0).replace("date-section", "date-section visible", 1)
        ),
        html,
        flags=re.I,
    )
    if 'id="ufc-force-date-visible"' in html or "id='ufc-force-date-visible'" in html:
        return html
    script = """
<script id="ufc-force-date-visible">
(function(){
  function fix(){
    var secs=[].slice.call(document.querySelectorAll('.date-section'));
    var withCards=secs.filter(function(s){
      return s.querySelector('[data-pick-card], .game-card-stack, .pick-card');
    });
    if(!withCards.length) return;
    secs.forEach(function(s){ s.classList.remove('visible'); });
    withCards.forEach(function(s){ s.classList.add('visible'); });
  }
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded', fix);
  } else { fix(); }
  setTimeout(fix, 0);
  setTimeout(fix, 50);
  setTimeout(fix, 250);
  setTimeout(fix, 800);
})();
</script>
"""
    if re.search(r"</body\s*>", html, flags=re.I):
        return re.sub(r"</body\s*>", script + "</body>", html, count=1, flags=re.I)
    return html + script


def _strip_live_chart_scaffold(html: str) -> str:
    """Remove live picks-chart placeholders so they cannot become fake date bubbles."""
    if not html:
        return html
    # Drop prior PICKS_CHART assets — hub page brings the locked ones.
    html = re.sub(r"<script>\s*window\.PICKS_CHART\s*=[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r'<link[^>]+picks-chart\.css[^>]*>', "", html, flags=re.I)
    html = re.sub(r'<script[^>]+picks-chart\.js[^>]*>\s*</script>', "", html, flags=re.I)
    html = re.sub(
        r'<style[^>]*id=["\']picks-chart-scaffold["\'][^>]*>[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    # Empty/scaffold date-sections used by live chart inject (id=pc-slate etc.)
    html = re.sub(
        r'<div\b[^>]*\bid=["\']pc-(?:slate|chart)["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="date-section[^"]*"\s+id="pc-[^"]+"[^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(r"<!--\s*pc-slate\s*-->", "", html, flags=re.I)
    return html


def _strip_pc_slate_date_nav(html: str) -> str:
    """If a bad date-nav lists pc-slate, remove it so hub date-nav can stand."""
    if not html or "pc-slate" not in html:
        return html
    html = re.sub(
        r'<div class="date-nav"[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style id="mlb-tpl-date-nav">[\s\S]*?</style>)?',
        "",
        html,
        count=1,
        flags=re.I,
    )
    # Nuke any remaining pc-* date-sections (non-greedy balanced-ish via chart wrap)
    html = re.sub(
        r'<div class="date-section[^"]*"\s+id="pc-[^"]+"[^>]*>'
        r'(?:(?!</div>).)*'
        r'<div class="chart-table-wrap"[^>]*>[\s\S]*?</div>\s*'
        r'</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="date-section[^"]*"\s+id="pc-[^"]+"[^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = html.replace(">pc-slate<", ">📅 Today<")
    html = re.sub(r"<!--\s*pc-slate\s*-->", "", html, flags=re.I)
    return html


def _stamp_sandbox_sport(html: str) -> str:
    if not html:
        return html
    if re.search(r'data-sandbox-sport=["\']ufc["\']', html, re.I):
        return html
    if re.search(r"\bdata-sandbox-sport=", html, re.I):
        return re.sub(
            r'\bdata-sandbox-sport=["\'][^"\']*["\']',
            'data-sandbox-sport="ufc"',
            html,
            count=1,
            flags=re.I,
        )
    return re.sub(
        r"<body\b([^>]*)>",
        r'<body\1 data-sandbox-sport="ufc">',
        html,
        count=1,
        flags=re.I,
    )


def build_ufc_share_jpeg_bytes() -> bytes | None:
    """Same JPEG as locked hub /ufc/share.jpg."""
    try:
        _load_hub_modules()
        from share_chrome import build_ufc_share_jpeg  # type: ignore

        return build_ufc_share_jpeg()
    except Exception as e:
        print(f"[ufc_live] share jpeg failed: {e}", flush=True)
        return None


def _fetch_locked_hub_html(which: str) -> str:
    """Byte-faithful signed-off UFC HTML from the running :5081 hub."""
    import urllib.error
    import urllib.request

    path = "/ufc/results" if which == "results" else "/ufc/"
    url = f"http://127.0.0.1:5081{path}"
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[ufc_live] fetch locked hub {url} failed: {e}", flush=True)
        return ""


def apply_ufc_isolation_html(html: str, *, which: str = "picks") -> str:
    """Graft locked :5081 UFC page into live chrome (header/footer kept)."""
    if not html or not isinstance(html, str):
        return html

    hub_page = _fetch_locked_hub_html(which)
    if not hub_page or len(hub_page) < 500:
        # Fallback: render from staging hub modules if :5081 is down.
        try:
            ufc_page = _load_hub_modules()
            hub_page, _meta = ufc_page.render_ufc_with_chrome("", which=which)
        except Exception as e:
            print(f"[ufc_live] locked hub render failed: {e}", flush=True)
            return html
    if not hub_page or len(hub_page) < 500:
        return html

    body = _extract_hub_main(hub_page)
    if not body or len(body) < 200:
        return html

    # Live pages often already have picks-chart scaffold (id=pc-slate). Strip it
    # before grafting or it becomes a fake date bubble and wraps the cards.
    # Also strip live showDate BEFORE graft so hub's single copy is the only one.
    live = _strip_live_chart_scaffold(html)
    live = _strip_show_date_scripts(live)
    live = _strip_live_ufc_chrome_dupes(live)
    out = _merge_hub_head_assets(live, hub_page)
    out = _stamp_sandbox_sport(out)
    out = _graft_hub_into_live(out, body)
    out = _rewrite_live_routes(out)
    out = _strip_pc_slate_date_nav(out)
    out = _strip_live_chart_scaffold(out)
    out = _strip_live_ufc_chrome_dupes(out)
    out = _dedupe_show_date_scripts(out)
    out = _dedupe_site_directory_footers(out)
    out = _dedupe_share_blocks(out)
    # Hub extract must never leave a second site footer
    # (belt: if extract still had one, live had one → dedupe kept last)

    # Belt-and-suspenders: locked hub already unlocks; re-run if graft lost it.
    try:
        _load_hub_modules()
        from sandbox_fixup import unlock_premium_card_details  # type: ignore

        out = unlock_premium_card_details(out)
    except Exception as e:
        print(f"[ufc_live] unlock_premium failed: {e}", flush=True)

    # Hub app injects consensus with payload finals after render — do the same
    # when the fetched page somehow lacks it.
    if which == "results" and "pl-consensus-records" not in out:
        try:
            from team_tabbed_results import (  # type: ignore
                build_ufc_payload,
                inject_consensus_records_html,
            )

            payload = build_ufc_payload()
            finals = list(payload.get("finals") or []) if isinstance(payload, dict) else []
            ln_key = None
            if isinstance(payload, dict):
                ln_key = ((payload.get("tallies") or {}).get("last_night") or {}).get("date")
            out = inject_consensus_records_html(
                out, sport="ufc", finals=finals, last_night_key=ln_key
            )
        except Exception as e:
            print(f"[ufc_live] consensus inject failed: {e}", flush=True)

    # Last: keep fight cards visible if any leftover date-nav still races.
    out = _ensure_ufc_date_sections_visible(out)

    if "<!-- ufc-isolation-overlay -->" not in out:
        if re.search(r"</body\s*>", out, flags=re.I):
            out = re.sub(
                r"</body\s*>",
                "<!-- ufc-isolation-overlay -->\n</body>",
                out,
                count=1,
                flags=re.I,
            )
        else:
            out = out + "<!-- ufc-isolation-overlay -->"
    return out
