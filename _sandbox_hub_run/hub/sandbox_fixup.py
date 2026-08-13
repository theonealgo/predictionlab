#!/usr/bin/env python3
"""Sandbox-only HTML fixups for proxied live-parity pages.

Never imported by production. Hub / isolation only.
"""
from __future__ import annotations

import ast
import html as html_lib
import json
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# Missing book / line cells: live often shows em-dash.
_BOOK_EMDASH_PATTERNS = [
    (re.compile(r"(Books(?:\s+Est\.?)?)\s*—", re.I), r"\1 N/A"),
    (re.compile(r"(Books\s+run\s+line)\s*—", re.I), r"\1 N/A"),
    (re.compile(r"(Books\s+spread)\s*—", re.I), r"\1 N/A"),
    (re.compile(r"(Books\s+total)\s*—", re.I), r"\1 N/A"),
    (re.compile(r"(run\s+line)\s*—", re.I), r"\1 N/A"),
    (re.compile(r"(total(?:\s+O/U)?)\s*—", re.I), r"\1 N/A"),
]

# Edge 0% next to missing books looks broken — show N/A.
_EDGE_ZERO_RE = re.compile(
    r"(Books(?:\s+(?:Est\.?|run\s+line|spread|total))?[^<\n]{0,80}?N/A[^<\n]{0,120}?)Edge\s+0(?:\.0)?%",
    re.I,
)


def fix_missing_odds_labels(html: str) -> str:
    if not html:
        return html
    for pat, repl in _BOOK_EMDASH_PATTERNS:
        html = pat.sub(repl, html)
    # Also HTML entity emdash
    html = html.replace("Books &mdash;", "Books N/A")
    html = html.replace("Books &#8212;", "Books N/A")
    html = html.replace("Books &#x2014;", "Books N/A")
    # Live card markup: Books and the emdash live in separate spans
    # <span class="ml-src books">Books</span><span class="ml-num ">—</span>
    # Also: Books <span>Est.</span></span> …
    html = re.sub(
        r'(<span class="ml-src books">Books(?:\s*<span[^>]*>Est\.?</span>)?</span>\s*'
        r'<span class="ml-num[^"]*">)\s*[—–\-]\s*(</span>)',
        r"\1N/A\2",
        html,
        flags=re.I,
    )
    # line-chip: <div class="line-chip-label">Books …</div>…— / empty
    html = re.sub(
        r'(<div class="line-chip-label">Books[^<]*</div>\s*'
        r'<div class="line-chip-value[^"]*">)\s*(?:[—–\-]|&mdash;|&ndash;)\s*(</div>)',
        r"\1N/A\2",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<div class="line-chip-label">Edge</div>\s*'
        r'<div class="line-chip-value[^"]*">)\s*0(?:\.0)?%\s*(</div>)',
        r"\1N/A\2",
        html,
        flags=re.I,
    )
    # Edge 0.0% when books are N/A in nearby text (plain + tags stripped lightly)
    html = _EDGE_ZERO_RE.sub(r"\1Edge N/A", html)
    # Common card footer pattern after our N/A rewrite
    html = re.sub(
        r"(Books\s+total\s+N/A)\s*Edge\s+0(?:\.0)?%",
        r"\1 Edge N/A",
        html,
        flags=re.I,
    )
    # Span cards: Edge 0% near Books N/A
    html = re.sub(
        r'(Books</span>\s*<span class="ml-num[^"]*">\s*N/A\s*</span>'
        r'[\s\S]{0,500}?)Edge\s+0(?:\.0)?%',
        r"\1Edge N/A",
        html,
        flags=re.I,
    )
    return html


def strip_fake_pl_minus_108(html: str) -> str:
    """Remove coin-flip Prediction Lab −108/+108 (50/50 ensemble), not real book lines.

    Keep Books −108 when that is an actual sportsbook quote. Replace PL face odds
    and PL table cells that are only ±108 with an em dash — win % stays honest.
    """
    if not html:
        return html
    # Face moneyline: Prediction Lab ±108 only (not Books)
    html = re.sub(
        r'(<div class="ml-line face-pl-ml">[\s\S]*?<span class="ml-num[^"]*">)\s*[+−\-]?108\s*(</span>)',
        r"\1—\2",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"(Prediction\s+Lab)</span>\s*<span class=\"ml-num[^\"]*\">\s*[+−\-]?108\s*</span>",
        r'Prediction Lab</span><span class="ml-num">—</span>',
        html,
        flags=re.I,
    )
    # Odds & Lines PL column cells that are only ±108
    html = re.sub(
        r'(<td\b[^>]*\bclass="[^"]*\bval-pl\b[^"]*"[^>]*>)\s*[+−\-]?108\s*(</td>)',
        r"\1—\2",
        html,
        flags=re.I,
    )
    return html


def _strip_balanced_div(html: str, open_match_start: int) -> str:
    """Remove one balanced <div>...</div> starting at open_match_start."""
    tag_end = html.find(">", open_match_start)
    if tag_end < 0:
        return html
    i = tag_end + 1
    depth = 1
    j = i
    end = -1
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
                end = next_close + 6
                break
            j = next_close + 6
    if end < 0:
        return html
    return html[:open_match_start] + html[end:]


def strip_sport_subnav(html: str) -> str:
    """Remove sticky Picks/Results sport subnav above pl2-header (duplicate of site nav)."""
    if not html:
        return html
    for pat in (
        r'<div\b[^>]*\bid=["\'][^"\']*(?:sandbox-badge|isolation-banner|sport-subnav|fan-badge)[^"\']*["\']',
        r'<div\b[^>]*\bclass=["\'][^"\']*\bhub-sport-subnav\b',
        r'<div\b[^>]*style=["\'][^"\']*background:\s*#f0b429',
    ):
        while True:
            m = re.search(pat, html, flags=re.I)
            if not m:
                break
            html = _strip_balanced_div(html, m.start())
    return html


def strip_social_export_block(html: str) -> str:
    """Deprecated no-op — keep share card + icons (hub proxies /share/ + /static/icons)."""
    return html or ""


def fix_share_social_assets(html: str) -> str:
    """Point share JPEG + social icons at hub routes (not dead isolation ports)."""
    if not html:
        return html
    # Absolute isolation / sidecar static icons → hub-relative
    html = re.sub(
        r"""(?P<attr>src|href)=(?P<q>['"])https?://127\.0\.0\.1:\d+/static/icons/social/""",
        r"\g<attr>=\g<q>/static/icons/social/",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"""(?P<attr>src|href)=(?P<q>['"])https?://localhost:\d+/static/icons/social/""",
        r"\g<attr>=\g<q>/static/icons/social/",
        html,
        flags=re.I,
    )
    # Absolute share URLs → hub-relative (hub proxies to sidecar / production)
    html = re.sub(
        r"""(?P<attr>src|href)=(?P<q>['"])https?://127\.0\.0\.1:\d+/share/""",
        r"\g<attr>=\g<q>/share/",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"""(?P<attr>src|href)=(?P<q>['"])https?://localhost:\d+/share/""",
        r"\g<attr>=\g<q>/share/",
        html,
        flags=re.I,
    )
    return html


def strip_sandbox_dev_notes(html: str) -> str:
    """Remove visible sandbox / isolation / hub-dev chrome from product pages."""
    if not html:
        return html
    # Yellow sticky Sandbox · banners (any sport)
    html = re.sub(
        r'<div\b[^>]*\bid=["\'][^"\']*(?:sandbox-badge|isolation-banner|fan-badge)[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    # Any sticky / top bar painted #f0b429 (even without known id)
    html = re.sub(
        r'<div\b[^>]*style=["\'][^"\']*background:\s*#f0b429[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    # Isolation / hub dark strip navs that say Sandbox
    html = re.sub(
        r'<p\b[^>]*>\s*Sandbox\s*·[\s\S]*?</p>',
        "",
        html,
        flags=re.I,
    )
    html = strip_sport_subnav(html)
    # Soccer lab badge etc.
    html = re.sub(
        r'<span\b[^>]*\bclass=["\'][^"\']*\bpl-badge\b[^"\']*["\'][^>]*>\s*Sandbox\s*</span>',
        "",
        html,
        flags=re.I,
    )
    # Visible “Sandbox only / not live / isolation” lead copy
    html = re.sub(
        r"\s*Sandbox only\s*[—\-]\s*not live PredictionLab\.?",
        "",
        html,
        flags=re.I,
    )
    html = re.sub(r"\s*·\s*Sandbox\b", "", html, flags=re.I)
    html = re.sub(
        r"(<title>[^<]*?)\s*·\s*Sandbox(\s*</title>)",
        r"\1 | Prediction Lab\2",
        html,
        flags=re.I,
    )
    # Isolation / training-data lead notes (UFC etc.) — never show to users
    html = re.sub(
        r'<p\b[^>]*\bclass=["\'][^"\']*\bufc-note\b[^"\']*["\'][^>]*>[\s\S]*?</p>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<p\b[^>]*>\s*(?:UFC\s+)?isolation\s*[—\-].*?</p>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<p\b[^>]*>[\s\S]*?Elo trained on\s+\d+\s+ESPN[\s\S]*?</p>',
        "",
        html,
        flags=re.I,
    )
    # Card detail IP: Prob source / TheOddsAPI / market blend labels
    html = re.sub(
        r'<div\b[^>]*\bclass=["\'][^"\']*\bsf-item\b[^"\']*["\'][^>]*>\s*'
        r'<span\b[^>]*\bclass=["\'][^"\']*\bsf-label\b[^"\']*["\'][^>]*>\s*'
        r'(?:Prob\s*source|Books)\s*</span>\s*'
        r'<span\b[^>]*\bclass=["\'][^"\']*\bsf-val\b[^"\']*["\'][^>]*>'
        r'[\s\S]*?(?:TheOddsAPI|Odds\s*API|market\s*blend|Elo\s*\+\s*market|'
        r'Odds-implied|Fighter\s*Elo|Elo\s*seed|Record\s*form)[\s\S]*?</span>\s*</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(r'\sdata-books-src=["\'][^"\']*["\']', "", html, flags=re.I)
    html = re.sub(r'\sdata-prob-source=["\'][^"\']*["\']', "", html, flags=re.I)
    html = re.sub(r"TheOddsAPI\s*\(\d+\)", "—", html, flags=re.I)
    html = re.sub(r"\bTheOddsAPI\b", "", html, flags=re.I)
    html = re.sub(r"\bThe Odds API\b", "", html, flags=re.I)
    html = re.sub(r"Elo\s*\+\s*market\s*blend", "", html, flags=re.I)
    html = re.sub(r"Prob\s*source", "", html, flags=re.I)
    # Analysis panels that dump source=/market=/elo= internals
    html = re.sub(
        r'<div\b[^>]*\bid=["\']analysis-ufc-\d+["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div\b[^>]*\bclass=["\'][^"\']*\banalysis-toggle\b[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    # HTML comments left by auth stripping
    html = re.sub(r"<!--\s*sandbox:[^>]*-->", "", html, flags=re.I)
    return html


def inject_sport_subnav(html: str, sport: str, *, which: str = "picks") -> str:
    """No-op inject — live pl2-header already has PICKS/MODELS/RESULTS.

    Kept as a stable API for callers; only strips duplicate sticky bars / isolation banners.
    """
    _ = sport, which
    if not html:
        return html
    html = strip_sandbox_dev_notes(html)
    html = strip_sport_subnav(html)
    return html


def strip_spread_total_for_ml_sports(html: str) -> str:
    """UFC/Tennis are moneyline-only — remove Spread/Total chrome and projected scores."""
    if not html:
        return html
    # Mid-bar chips: Books * / Spread / Total (any wording)
    html = re.sub(
        r'<div class="line-chip(?:\s+edge-chip)?[^"]*">\s*'
        r'<div class="line-chip-label">\s*'
        r'(?:Books[^<]*|Spread|Total|Puck\s*line|Run\s*line|Model\s+spread|Model\s+total|Edge)\s*'
        r'</div>\s*'
        r'<div class="line-chip-val[^"]*">[\s\S]*?</div>\s*'
        r'</div>',
        "",
        html,
        flags=re.I,
    )
    # Odds & Lines table rows for Spread / Total / PK
    html = re.sub(
        r'<tr>\s*<td class="market-k">\s*(?:Spread|Total|Puck\s*Line|Run\s*Line|PK(?:/100)?)\s*</td>'
        r'[\s\S]*?</tr>',
        "",
        html,
        flags=re.I,
    )
    # Projected score boxes (PK/100 junk for combat/tennis)
    html = re.sub(
        r'<div class="proj-score-box">[\s\S]*?</div>\s*(?=<div class="(?:pick-conf|odds-extras)|</div>)',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(r"\bPK\s*/\s*100\b", "", html, flags=re.I)
    html = re.sub(r">\s*PK\s*<", ">—<", html, flags=re.I)
    # Empty lines-strip shells
    html = re.sub(r'<div class="lines-strip">\s*</div>', "", html, flags=re.I)
    return html


_UFC_ODDS_CACHE: list[dict[str, Any]] | None = None


def _norm_fighter(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return re.sub(r"\s+", " ", s)


def _fighter_match(a: str, b: str) -> bool:
    na, nb = _norm_fighter(a), _norm_fighter(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    ta, tb = na.split(), nb.split()
    if ta and tb and ta[-1] == tb[-1] and (ta[0][:1] == tb[0][:1] or ta[0] in tb or tb[0] in ta):
        return True
    return False


def fetch_ufc_the_odds_api_events() -> list[dict[str, Any]]:
    """Offline: The Odds API mma_mixed_martial_arts (h2h). Cached per process."""
    global _UFC_ODDS_CACHE
    if _UFC_ODDS_CACHE is not None:
        return _UFC_ODDS_CACHE
    events: list[dict[str, Any]] = []
    try:
        import os
        from pathlib import Path

        try:
            from dotenv import load_dotenv

            env_path = Path(__file__).resolve().parents[2] / ".env"
            if env_path.is_file():
                load_dotenv(env_path)
        except Exception:
            pass
        key = os.environ.get("ODDS_API_KEY") or ""
        if not key:
            _UFC_ODDS_CACHE = []
            return _UFC_ODDS_CACHE
        import json
        import urllib.parse

        qs = urllib.parse.urlencode(
            {
                "apiKey": key,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            }
        )
        url = f"https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds?{qs}"
        req = Request(url, headers={"User-Agent": "sports-sandbox-hub"})
        with urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if isinstance(data, list):
            for g in data:
                home = str(g.get("home_team") or "")
                away = str(g.get("away_team") or "")
                home_ml = away_ml = None
                for book in g.get("bookmakers") or []:
                    for market in book.get("markets") or []:
                        if market.get("key") != "h2h":
                            continue
                        for outcome in market.get("outcomes") or []:
                            nm = str(outcome.get("name") or "")
                            price = outcome.get("price")
                            if _fighter_match(nm, home):
                                home_ml = price
                            elif _fighter_match(nm, away):
                                away_ml = price
                    if home_ml is not None and away_ml is not None:
                        break
                if home_ml is None and away_ml is None:
                    continue
                events.append(
                    {
                        "home": home,
                        "away": away,
                        "home_ml": int(home_ml) if home_ml is not None else None,
                        "away_ml": int(away_ml) if away_ml is not None else None,
                        "source": "TheOddsAPI",
                    }
                )
    except Exception as e:
        print(f"[sandbox_fixup] UFC Odds API fetch failed: {e}", flush=True)
        events = []
    _UFC_ODDS_CACHE = events
    print(f"[sandbox_fixup] UFC TheOddsAPI events with ML: {len(events)}", flush=True)
    return _UFC_ODDS_CACHE


def _lookup_ufc_odds(home: str, away: str) -> dict[str, Any] | None:
    for ev in fetch_ufc_the_odds_api_events():
        eh, ea = ev.get("home") or "", ev.get("away") or ""
        if (_fighter_match(home, eh) and _fighter_match(away, ea)) or (
            _fighter_match(home, ea) and _fighter_match(away, eh)
        ):
            # Orient to card home/away
            if _fighter_match(home, eh):
                return {
                    "home_ml": ev.get("home_ml"),
                    "away_ml": ev.get("away_ml"),
                    "source": ev.get("source"),
                }
            return {
                "home_ml": ev.get("away_ml"),
                "away_ml": ev.get("home_ml"),
                "source": ev.get("source"),
            }
    return None


def _walk_game_card_stacks(html: str):
    """Yield (start, end, open_tag, inner) for each game-card-stack."""
    pos = 0
    while True:
        m = re.search(r'<div class="game-card-stack\b[^"]*"[^>]*>', html[pos:], flags=re.I)
        if not m:
            break
        start = pos + m.start()
        open_tag = m.group(0)
        i = start + len(open_tag)
        depth = 1
        j = i
        end = -1
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
                    end = next_close + 6
                    break
                j = next_close + 6
        if end < 0:
            break
        yield start, end, open_tag, html[i:end - 6]
        pos = end


def _is_coin_flip_ufc_card(open_tag: str, inner: str) -> bool:
    conf = re.search(r'data-conf="([^"]*)"', open_tag, flags=re.I)
    conf_v = (conf.group(1) if conf else "").strip()
    if conf_v in ("50", "50.0", "50.00"):
        return True
    # Face win% all 50.0
    win_pcts = re.findall(
        r'class="[^"]*win-pct[^"]*"[^>]*>\s*([\d.]+)\s*%?',
        inner,
        flags=re.I,
    )
    if win_pcts and all(p in ("50", "50.0", "50.00") for p in win_pcts):
        return True
    # All pick-conf models N/A or 50%
    vals = re.findall(r'<div class="pc-val[^"]*">\s*([^<]*?)\s*</div>', inner, flags=re.I)
    if vals and all(
        v.strip() in ("N/A", "—", "–", "-", "50.0%", "50%", "50.0", "50") for v in vals
    ):
        return True
    return False


def _replace_balanced_div(html: str, open_re: str, replacement: str) -> tuple[str, bool]:
    m = re.search(open_re, html, flags=re.I)
    if not m:
        return html, False
    start = m.start()
    tag_end = html.find(">", start) + 1
    depth = 1
    j = tag_end
    end = -1
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
                end = next_close + 6
                break
            j = next_close + 6
    if end < 0:
        return html, False
    return html[:start] + replacement + html[end:], True


def _collapse_pick_confidence(inner: str, *, coin_flip: bool) -> str:
    """Replace useless 6-model N/A/50% grid with one honest line (or keep real models)."""
    if not coin_flip:
        # Remove N/A-only pc-boxes (balanced).
        while True:
            m = re.search(r'<div class="pc-box[^"]*">', inner, flags=re.I)
            if not m:
                break
            start = m.start()
            tag_end = inner.find(">", start) + 1
            depth = 1
            j = tag_end
            end = -1
            while j < len(inner) and depth > 0:
                next_open = inner.find("<div", j)
                next_close = inner.find("</div>", j)
                if next_close < 0:
                    break
                if next_open >= 0 and next_open < next_close:
                    depth += 1
                    j = next_open + 4
                else:
                    depth -= 1
                    if depth == 0:
                        end = next_close + 6
                        break
                    j = next_close + 6
            if end < 0:
                break
            box = inner[start:end]
            val = re.search(r'<div class="pc-val[^"]*">\s*([^<]*?)\s*</div>', box, flags=re.I)
            side_na = re.search(
                r'<div class="pc-side"[^>]*title="[^"]*not available[^"]*"[^>]*>\s*N/A\s*</div>',
                box,
                flags=re.I,
            )
            v = (val.group(1) if val else "").strip()
            if side_na or v in ("N/A", "—", "–", "-", ""):
                inner = inner[:start] + inner[end:]
                continue
            # Keep this box; skip past it
            # Mark with a temporary attribute to avoid re-finding
            inner = inner[: start + 4] + ' data-keep="1"' + inner[start + 4 :]
            # advance by rewriting search to skip kept boxes
            # simpler: break loop by renaming class temporarily
            inner = re.sub(
                r'<div data-keep="1" class="pc-box',
                '<div class="pc-box-kept',
                inner,
                count=1,
                flags=re.I,
            )
        inner = inner.replace('class="pc-box-kept', 'class="pc-box')
        inner = inner.replace("class='pc-box-kept", "class='pc-box")
        return inner

    msg = (
        '<div class="pick-conf-bar">'
        '<div class="pick-conf-title">Pick Confidence</div>'
        '<div class="pick-conf-grid" style="display:block;padding:10px 12px;">'
        '<p style="margin:0;color:#656762;font-size:.9rem;line-height:1.4;">'
        "Models unavailable for this fight — fighter ratings are not trained "
        "(defaults collapse to a coin-flip). No pick shown.</p>"
        "</div></div>"
    )
    inner2, ok = _replace_balanced_div(inner, r'<div class="pick-conf-bar\b[^"]*"[^>]*>', msg)
    return inner2 if ok else inner


def _mark_ufc_face_unavailable(inner: str) -> str:
    """Replace fake 50% / ±108 PL face chrome with honest dashes."""
    # Face win percent number
    inner = re.sub(
        r'(class="[^"]*win-pct[^"]*"[^>]*>)\s*50(?:\.0+)?\s*(%?)',
        r"\1—\2",
        inner,
        flags=re.I,
    )
    # Some templates split number / %
    inner = re.sub(
        r'(class="[^"]*win-pct[^"]*"[^>]*>)\s*50(?:\.0+)?\s*</span>\s*<span[^>]*>\s*%',
        r"\1—</span><span class=\"win-pct-suffix\">",
        inner,
        flags=re.I,
    )
    # Prediction Lab ±108
    inner = re.sub(
        r'(<div class="ml-line face-pl-ml">[\s\S]*?<span class="ml-num[^"]*">)\s*[+−\-]?108\s*(</span>)',
        r"\1—\2",
        inner,
        flags=re.I,
    )
    # Sharp Consensus label near 50 — leave label, already dashed win%
    return inner


def _inject_books_ml_into_card(inner: str, home_ml: int | None, away_ml: int | None) -> str:
    """Fill face Books ML rows when The Odds API has a match (pick-only sport)."""
    if home_ml is None and away_ml is None:
        return inner

    def _fmt(n: int | None) -> str:
        if n is None:
            return "—"
        return f"+{n}" if n > 0 else str(n)

    # Prefer updating existing face-books-ml nums in order away then home (card order)
    nums = list(
        re.finditer(
            r'(<div class="ml-line face-books-ml">[\s\S]*?<span class="ml-num[^"]*">)\s*([^<]*?)\s*(</span>)',
            inner,
            flags=re.I,
        )
    )
    if len(nums) >= 2:
        # Card shows away slot first, then home
        repls = [_fmt(away_ml), _fmt(home_ml)]
        out = inner
        # replace from the end so offsets stay valid
        for idx in (1, 0):
            m = nums[idx]
            out = out[: m.start(1)] + m.group(1) + repls[idx] + m.group(3) + out[m.end(3) :]
        return out
    # If books chrome was stripped, re-add a compact Books line under each face PL block
    if "face-books-ml" not in inner and (home_ml is not None or away_ml is not None):
        # Insert after each face-pl-ml
        parts = []
        last = 0
        mls = [_fmt(away_ml), _fmt(home_ml)]
        i = 0
        for m in re.finditer(
            r'<div class="ml-line face-pl-ml">[\s\S]*?</div>',
            inner,
            flags=re.I,
        ):
            parts.append(inner[last : m.end()])
            if i < 2:
                parts.append(
                    f'<div class="ml-line face-books-ml">'
                    f'<span class="ml-src books">Books</span>'
                    f'<span class="ml-num">{mls[i]}</span></div>'
                )
            i += 1
            last = m.end()
        parts.append(inner[last:])
        return "".join(parts)
    return inner


def _hide_empty_odds_pricing(inner: str) -> str:
    """Drop Odds & Lines block when every cell is empty / em dash / PK junk."""
    m = re.search(r'<div class="odds-pricing-section\b[^"]*"[^>]*>', inner, flags=re.I)
    if not m:
        return inner
    # Walk this section only
    start = m.start()
    tag_end = inner.find(">", start) + 1
    depth = 1
    j = tag_end
    end = -1
    while j < len(inner) and depth > 0:
        next_open = inner.find("<div", j)
        next_close = inner.find("</div>", j)
        if next_close < 0:
            break
        if next_open >= 0 and next_open < next_close:
            depth += 1
            j = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                end = next_close + 6
                break
            j = next_close + 6
    if end < 0:
        return inner
    section = inner[start:end]
    # Keep if any real American odds digit sequence (not just 50 / 100 PK)
    useful = re.findall(r"[+−\-]?\d{3,}", section)
    useful = [u for u in useful if u not in ("100", "+100", "-100", "108", "-108", "+108", "−108")]
    if useful:
        return inner
    return inner[:start] + inner[end:]


def fixup_ufc_picks_honesty(html: str) -> str:
    """UFC pick-only honesty pass.

    Isolation cards (ufc-mlb-grid-fix / locked pick stacks) already carry real
    probs — never blank those. Legacy live coin-flip cards still get books
    inject or an honest unavailable note.
    """
    if not html or "game-card-stack" not in html:
        return html

    # Isolation page already filled — leave real win% / picks alone.
    if (
        'id="ufc-mlb-grid-fix"' in html
        or "id='ufc-mlb-grid-fix'" in html
        or 'data-pick-card' in html
    ):
        print("[sandbox_fixup] UFC honesty: skip (locked UFC pick cards present)", flush=True)
        return html

    # Warm odds cache once
    try:
        fetch_ufc_the_odds_api_events()
    except Exception:
        pass

    pieces: list[str] = []
    cursor = 0
    injected = 0
    collapsed = 0
    filled = 0
    for start, end, open_tag, inner in _walk_game_card_stacks(html):
        pieces.append(html[cursor:start])
        home_m = re.search(r'data-home="([^"]*)"', open_tag, flags=re.I)
        away_m = re.search(r'data-away="([^"]*)"', open_tag, flags=re.I)
        home = home_m.group(1) if home_m else ""
        away = away_m.group(1) if away_m else ""
        odds = _lookup_ufc_odds(home, away) if home and away else None
        if odds:
            inner = _inject_books_ml_into_card(inner, odds.get("home_ml"), odds.get("away_ml"))
            injected += 1
            open_tag = re.sub(
                r'\sdata-books-ml="[^"]*"',
                "",
                open_tag,
                flags=re.I,
            )
            open_tag = open_tag[:-1] + ' data-books-ml="1">'

        coin = _is_coin_flip_ufc_card(open_tag, inner)
        if coin and odds and odds.get("home_ml") is not None and odds.get("away_ml") is not None:
            # Prefer market-implied win% over blanking the card.
            try:
                from pathlib import Path
                import importlib.util

                pred_path = Path.home() / "Documents/Personal/ufc/engine/predict.py"
                spec = importlib.util.spec_from_file_location("ufc_pred_fixup", pred_path)
                pred = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(pred)
                hp, ap = pred.devig_two_way(int(odds["home_ml"]), int(odds["away_ml"]))
                pl_h = pred.american_from_prob(hp)
                pl_a = pred.american_from_prob(ap)
                pick = home if hp >= ap else away
                conf = f"{(hp if hp >= ap else ap) * 100:.1f}"
                # Replace face win% in order away then home (matchup row order).
                win_iter = list(
                    re.finditer(
                        r'(class="[^"]*win-pct[^"]*"[^>]*>)\s*[^<]*',
                        inner,
                        flags=re.I,
                    )
                )
                if len(win_iter) >= 2:
                    # replace from end
                    for idx, pct in ((1, f"{hp * 100:.1f}"), (0, f"{ap * 100:.1f}")):
                        m = win_iter[idx]
                        inner = inner[: m.start(1)] + m.group(1) + pct + inner[m.end() :]
                # PL moneylines
                pl_nums = list(
                    re.finditer(
                        r'(<div class="ml-line face-pl-ml">[\s\S]*?<span class="ml-num[^"]*">)\s*([^<]*?)\s*(</span>)',
                        inner,
                        flags=re.I,
                    )
                )
                if len(pl_nums) >= 2:
                    repls = [f"{pl_a:+d}" if pl_a > 0 else str(pl_a), f"{pl_h:+d}" if pl_h > 0 else str(pl_h)]
                    for idx in (1, 0):
                        m = pl_nums[idx]
                        inner = inner[: m.start(1)] + m.group(1) + repls[idx] + m.group(3) + inner[m.end(3) :]
                open_tag = re.sub(r'data-conf="[^"]*"', f'data-conf="{conf}"', open_tag, flags=re.I)
                open_tag = re.sub(r'data-pick="[^"]*"', f'data-pick="{pick}"', open_tag, flags=re.I)
                if 'data-prob-source="' not in open_tag:
                    open_tag = open_tag[:-1] + ' data-prob-source="odds">'
                inner = _collapse_pick_confidence(inner, coin_flip=False)
                filled += 1
                coin = False
            except Exception as e:
                print(f"[sandbox_fixup] UFC odds-fill failed: {e}", flush=True)

        if coin:
            inner = _mark_ufc_face_unavailable(inner)
            inner = _collapse_pick_confidence(inner, coin_flip=True)
            open_tag = re.sub(r'data-conf="[^"]*"', 'data-conf=""', open_tag, flags=re.I)
            open_tag = re.sub(r'data-pick="[^"]*"', 'data-pick=""', open_tag, flags=re.I)
            collapsed += 1
        else:
            inner = _collapse_pick_confidence(inner, coin_flip=False)

        inner = _hide_empty_odds_pricing(inner)
        pieces.append(open_tag + inner + "</div>")
        cursor = end
    pieces.append(html[cursor:])
    out = "".join(pieces)
    print(
        f"[sandbox_fixup] UFC honesty: collapsed={collapsed} books={injected} odds_filled={filled}",
        flush=True,
    )
    if "sandbox-ufc-honesty" not in out:
        css = (
            "<style id=\"sandbox-ufc-honesty\">"
            ".game-card-stack[data-conf=\"\"] .win-pct{opacity:.85;}"
            "</style>"
        )
        if re.search(r"</head>", out, re.I):
            out = re.sub(r"</head>", css + "</head>", out, count=1, flags=re.I)
        else:
            out = css + out
    return out


def unlock_premium_card_details(html: str) -> str:
    """Sandbox: remove locked paywall stubs so cards are not behind login."""
    if not html:
        return html
    html = re.sub(
        r'<div\b[^>]*class=["\'][^"\']*odds-pricing-locked[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"🔒\s*Lines\s*&amp;\s*projections\s*locked\.[\s\S]{0,200}?</div>",
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"Lines\s*&(?:amp;)?\s*projections\s*locked\.[\s\S]{0,160}?(?:premium|Log in)[\s\S]{0,80}",
        "",
        html,
        flags=re.I,
    )
    if "sandbox-unlock-details" not in html:
        css = (
            "<style id=\"sandbox-unlock-details\">"
            ".odds-pricing-locked,.premium-lock,.locked-details,"
            ".join-premium-bar,.premium-upsell-strip{display:none!important;}"
            "</style>"
        )
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
        else:
            html = css + html
    return html


def _fmt_pct(v: Any) -> str:
    try:
        f = float(v)
        return f"{f:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_odds(v: Any, *, has_books: bool) -> str:
    if v is None or v == "" or v == "—":
        return "N/A"
    if not has_books and str(v) in ("-108", "−108", 108, -108):
        return "N/A"
    try:
        n = int(float(v))
        return f"{n:+d}".replace("+", "+") if n != 0 else "N/A"
    except (TypeError, ValueError):
        return str(v)


def _american(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _golf_headshot(athlete_id: str | None) -> str:
    if not athlete_id:
        return ""
    return f"https://a.espncdn.com/i/headshots/golf/players/full/{athlete_id}.png"


def rebuild_golf_picks_table(html: str) -> str:
    """Replace live golf matchup/books dump with ranked tournament win-% board."""
    try:
        from golf_page import render_golf_with_chrome

        page, meta = render_golf_with_chrome(html or "", which="picks")
        if meta.get("ok") is not False:
            return page
    except Exception as e:
        print(f"[sandbox_fixup] golf board fallback: {e}", flush=True)

    if not html or ("Raw Prediction Data" not in html and "<pre" not in html):
        return html
    # Legacy path: aggregate pairwise into ranks
    scores: dict[str, float] = {}
    meta_p: dict[str, dict[str, Any]] = {}
    for pre in re.findall(r"<pre[^>]*>([\s\S]*?)</pre>", html):
        raw = html_lib.unescape(pre).strip()
        try:
            d = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            try:
                d = json.loads(raw.replace("'", '"'))
            except Exception:
                continue
        if not isinstance(d, dict):
            continue
        home = str(d.get("home_team_id") or "?")
        away = str(d.get("away_team_id") or "?")
        home_prob = d.get("face_home_prob")
        if home_prob is None:
            home_prob = d.get("ensemble_prob")
        try:
            hp = float(home_prob) / (100.0 if float(home_prob) > 1.5 else 1.0)
        except (TypeError, ValueError):
            hp = 0.5
        ap = 1.0 - hp
        scores[home] = scores.get(home, 0.0) + hp
        scores[away] = scores.get(away, 0.0) + ap
        meta_p[home] = {"athlete_id": d.get("home_athlete_id")}
        meta_p[away] = {"athlete_id": d.get("away_athlete_id")}
    if not scores:
        html = re.sub(r"<pre[^>]*>[\s\S]*?</pre>", "", html, flags=re.I)
        return html
    # Softmax normalize matchup tallies → win %
    import math

    names = list(scores.keys())
    vals = [scores[n] for n in names]
    m = max(vals)
    exps = [math.exp((v - m) * 2.0) for v in vals]
    z = sum(exps) or 1.0
    ranked = sorted(
        ((n, exps[i] / z) for i, n in enumerate(names)),
        key=lambda t: -t[1],
    )
    rows_html = []
    for i, (name, pr) in enumerate(ranked, 1):
        aid = (meta_p.get(name) or {}).get("athlete_id")
        img = _golf_headshot(str(aid) if aid else None)
        img_tag = (
            f'<img src="{img}" alt="" width="40" height="40" style="border-radius:50%;object-fit:cover;margin-right:8px" onerror="this.style.display=\'none\'"/>'
            if img
            else ""
        )
        rows_html.append(
            "<tr>"
            f"<td style='padding:10px;border-bottom:1px solid #eee;font-weight:800;color:#666'>#{i}</td>"
            f"<td style='padding:10px;border-bottom:1px solid #eee'>{img_tag}<strong>{html_lib.escape(name)}</strong></td>"
            f"<td style='padding:10px;border-bottom:1px solid #eee;font-weight:900'>{pr*100:.1f}%</td>"
            "</tr>"
        )
    table = (
        '<div class="container" style="max-width:900px;margin:30px auto;padding:20px;">'
        "<h1 style='margin-bottom:8px;'>Tournament Win Probability</h1>"
        "<p style='color:#666;margin-bottom:20px;'>Players ranked by model win probability. Not head-to-head matchups.</p>"
        '<table style="width:100%;border-collapse:collapse">'
        "<thead><tr><th style='text-align:left;padding:10px'>Rank</th>"
        "<th style='text-align:left;padding:10px'>Player</th>"
        "<th style='text-align:left;padding:10px'>Win %</th></tr></thead><tbody>"
        + "\n".join(rows_html)
        + "</tbody></table></div>"
    )
    html = re.sub(r"<pre[^>]*>[\s\S]*?</pre>", "", html, flags=re.I)
    html = html.replace("Raw Prediction Data", "")
    if re.search(r"<main\b", html, re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + table, html, count=1, flags=re.I)
    if re.search(r"</body>", html, re.I):
        return re.sub(r"</body>", table + "</body>", html, count=1, flags=re.I)
    return table


def try_fix_athlete_images(html: str, sport: str) -> str:
    """Best-effort: map data-home/data-away names to ESPN headshots for UFC/Tennis."""
    if not html or sport not in ("ufc", "tennis"):
        return html
    path = {
        "ufc": "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard",
        "tennis": "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
    }[sport]
    name_to_img: dict[str, str] = {}
    try:
        req = Request(path, headers={"User-Agent": "sports-sandbox-hub"})
        with urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        for ev in data.get("events") or []:
            for comp in ev.get("competitions") or []:
                for c in comp.get("competitors") or []:
                    ath = c.get("athlete") or {}
                    name = (ath.get("displayName") or c.get("displayName") or "").strip()
                    href = ""
                    for link in ath.get("links") or []:
                        pass
                    # headshot from athlete id
                    aid = ath.get("id") or c.get("id")
                    if name and aid:
                        if sport == "ufc":
                            href = f"https://a.espncdn.com/i/headshots/mma/players/full/{aid}.png"
                        else:
                            href = f"https://a.espncdn.com/i/headshots/tennis/players/full/{aid}.png"
                        name_to_img[name.lower()] = href
                        # last name key
                        parts = name.lower().split()
                        if parts:
                            name_to_img[parts[-1]] = href
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return html
    if not name_to_img:
        return html

    def _replace_slot(m: re.Match) -> str:
        block = m.group(0)
        # find nearby name text
        names = re.findall(r"<[^>]*class=\"[^\"]*team-name[^\"]*\"[^>]*>([^<]+)", block)
        if not names:
            names = re.findall(r">([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+)+)<", block)
        img = None
        for n in names:
            img = name_to_img.get(n.strip().lower())
            if img:
                break
            parts = n.strip().lower().split()
            if parts:
                img = name_to_img.get(parts[-1])
            if img:
                break
        if not img:
            return block
        return re.sub(
            r'(<img class="team-logo"[^>]*src=")[^"]*(")',
            rf"\1{img}\2",
            block,
            count=1,
            flags=re.I,
        )

    html = re.sub(
        r'<div class="team-slot[^"]*"[\s\S]{0,600}?</div>',
        _replace_slot,
        html,
        flags=re.I,
    )
    return html


def hide_books_chrome(html: str) -> str:
    """Same MLB card template — omit Books UI when sport has no book odds.

    Removes Books moneyline rows, Books run/puck/spread/total chips, and Books table columns.
    Keeps the same card shell; does not invent a different design.
    """
    if not html:
        return html

    # Face moneyline: any class list containing face-books-ml
    html = re.sub(
        r'<div\b[^>]*\bclass="[^"]*\bface-books-ml\b[^"]*"[^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    # Lines strip chips whose label starts with Books (run line / total / spread / puck)
    html = re.sub(
        r'<div class="line-chip">\s*'
        r'<div class="line-chip-label">\s*Books[^<]*</div>\s*'
        r'<div class="line-chip-val[^"]*">[\s\S]*?</div>\s*'
        r'</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(r'<th\b[^>]*\bcol-books\b[^>]*>\s*Books\s*</th>', "", html, flags=re.I)
    html = re.sub(r'<td\b[^>]*\bval-books\b[^>]*>[\s\S]*?</td>', "", html, flags=re.I)
    # Kill-switch CSS for anything left (stylesheet selectors still mention the class)
    if "sandbox-hide-books" not in html:
        css = (
            "<style id=\"sandbox-hide-books\">"
            ".face-books-ml,.ml-line.face-books-ml,"
            ".ml-stack .ml-line:has(.ml-src.books){display:none!important;}"
            "th.col-books,td.val-books{display:none!important;}"
            "</style>"
        )
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
        else:
            html = css + html
    return html


def apply_sport_fixups(html: str, sport: str, which: str = "picks") -> str:
    """Apply all sandbox fixups for a proxied sport page."""
    if not html:
        return html
    sport = (sport or "").lower()
    # Always: unlock paywall stubs, strip isolation banners / duplicate Picks|Results bars
    html = unlock_premium_card_details(html)
    html = strip_sandbox_dev_notes(html)
    html = strip_sport_subnav(html)

    # Live-parity team sports (MLB/Soccer/WNBA): proxy chrome untouched.
    # Do NOT run hide_empty_books / hide_books_chrome / strip_fake_pl — those
    # strip Books run line / Books total / Edge when values look empty.
    if sport in ("mlb", "soccer", "wnba"):
        html = strip_sandbox_dev_notes(html)
        html = strip_sport_subnav(html)
        html = fix_share_social_assets(html)
        return html

    if sport == "golf":
        try:
            from golf_page import render_golf_with_chrome

            html, _meta = render_golf_with_chrome(html, which=which)
        except Exception as e:
            print(f"[sandbox_fixup] golf chrome inject: {e}", flush=True)
            if which == "picks":
                html = rebuild_golf_picks_table(html)
        html = strip_sandbox_dev_notes(html)
        html = strip_sport_subnav(html)
        html = fix_share_social_assets(html)
        return html  # Golf is exempt from betting-card template

    # UFC: isolation cards keep real probs; legacy coin-flip gets odds fill / honesty.
    if sport == "ufc" and which == "picks":
        html = fixup_ufc_picks_honesty(html)
    # Tennis/CFL only — never invent Books N/A; hide missing book chrome instead
    html = hide_empty_books_slots(html)
    if sport in ("tennis", "cfl"):
        html = hide_books_chrome(html)
    elif sport == "ufc":
        html = _hide_books_except_injected(html)
    # Coin-flip PL ±108 → em dash — but never strip locked UFC cards with real probs
    if sport == "ufc" and (
        'id="ufc-mlb-grid-fix"' in html
        or "id='ufc-mlb-grid-fix'" in html
        or 'data-pick-card' in html
    ):
        pass  # UFC PL moneylines are real (may legitimately be −108)
    else:
        html = strip_fake_pl_minus_108(html)
    if sport in ("ufc", "tennis"):
        html = strip_spread_total_for_ml_sports(html)
        if sport == "tennis":
            html = hide_books_chrome(html)
        else:
            html = _hide_books_except_injected(html)
    if sport in ("ufc", "tennis") and which == "picks":
        # Locked UFC cards already have ESPN headshots
        if 'id="ufc-mlb-grid-fix"' not in html and 'data-pick-card' not in html:
            html = try_fix_athlete_images(html, sport)

    html = strip_sandbox_dev_notes(html)
    html = strip_sport_subnav(html)
    html = fix_share_social_assets(html)
    return html


def _hide_books_except_injected(html: str) -> str:
    """Hide empty Books chrome, but keep face Books ML when TheOddsAPI filled a card."""
    if not html:
        return html
    pieces: list[str] = []
    cursor = 0
    for start, end, open_tag, inner in _walk_game_card_stacks(html):
        pieces.append(html[cursor:start])
        stack = open_tag + inner + "</div>"
        if re.search(r'data-books-ml="1"', open_tag, flags=re.I):
            # Still drop spread/total Books chips / empty table cols via hide_empty only
            stack = hide_empty_books_slots(stack)
        else:
            stack = hide_books_chrome(stack)
        pieces.append(stack)
        cursor = end
    pieces.append(html[cursor:])
    out = "".join(pieces)
    # Ensure hide-books CSS still present for residual selectors
    if "sandbox-hide-books" not in out and "face-books-ml" in out:
        # Partial hide: only empty books columns in tables
        out = hide_empty_books_slots(out)
    elif "sandbox-hide-books" not in out:
        out = hide_books_chrome(out)
    return out


def hide_empty_books_slots(html: str) -> str:
    """When a Books value is missing (em dash / empty), remove that Books chrome — no N/A."""
    if not html:
        return html
    # Face Books ML with em dash / empty
    html = re.sub(
        r'<div\b[^>]*\bclass="[^"]*\bface-books-ml\b[^"]*"[^>]*>\s*'
        r'<span class="ml-src books">Books(?:\s*<span[^>]*>Est\.?</span>)?</span>\s*'
        r'<span class="ml-num[^"]*">\s*(?:[—–\-‒]|&mdash;|&ndash;|&nbsp;|\s*)\s*</span>\s*'
        r'</div>',
        "",
        html,
        flags=re.I,
    )
    # Lines-strip Books chips with missing values
    html = re.sub(
        r'<div class="line-chip">\s*'
        r'<div class="line-chip-label">\s*Books[^<]*</div>\s*'
        r'<div class="line-chip-val[^"]*">\s*(?:[—–\-‒]|&mdash;|&ndash;|\s*)\s*</div>\s*'
        r'</div>',
        "",
        html,
        flags=re.I,
    )
    # Edge 0% next to missing books looks fake — drop orphan Edge 0 chips only when
    # no books chip remains nearby (handled loosely via empty lines-strip cleanup)
    html = re.sub(r'<div class="lines-strip">\s*</div>', "", html, flags=re.I)
    return html
