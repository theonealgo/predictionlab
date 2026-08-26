#!/usr/bin/env python3
"""Sandbox-only HTML fixups for proxied live-parity pages.

Never imported by production. Hub / isolation only.

MLB UI SIGNED OFF — do not change without owner request.
Locked 2026-08-10. MLB picks/results/chart fixups below are frozen unless
the owner unlocks (see notes/MLB_LOCKED.md / qa/MLB_SIGNED_OFF.txt).
Other sports (soccer, wnba, etc.) may still be edited.
"""
from __future__ import annotations

import ast
import html as html_lib
import json
import os
import re
import sys
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# MLB spread display invert (publish layer only). Default OFF — leftover
# invert hid Home −1.5. Set MLB_FLIP_SPREAD=1 only as a diagnostic.
MLB_FLIP_SPREAD = os.environ.get("MLB_FLIP_SPREAD", "0").strip().lower() in (
    "1",
    "true",
    "on",
    "yes",
)

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
    # Forbidden isolation event names (AGENTS rule 9) — never show to users
    html = re.sub(r"\bSandbox Open\b", "ATP Tour", html, flags=re.I)
    html = re.sub(r"\bSandbox\s+Open\b", "ATP Tour", html, flags=re.I)
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
    # Common live gate copy that blocks View Details on sandbox
    html = re.sub(
        r"(?is)<[^>]+>(?:\s|🔒)*no access to premium[\s\S]{0,200}?</[^>]+>",
        "",
        html,
    )
    html = re.sub(
        r"(?is)(?:Join|Upgrade)\s+(?:to\s+)?Premium[\s\S]{0,120}?View Details",
        "View Details",
        html,
    )
    if "sandbox-unlock-details" not in html:
        css = (
            "<style id=\"sandbox-unlock-details\">"
            ".odds-pricing-locked,.premium-lock,.locked-details,"
            ".join-premium-bar,.premium-upsell-strip,"
            ".premium-protected-gate,.paywall-overlay{display:none!important;}"
            ".card-details[hidden]{display:none!important;}"
            ".card-details:not([hidden]){display:block!important;}"
            ".pick-card.is-expanded .card-details:not([hidden]),"
            ".game-card.is-expanded .card-details:not([hidden]),"
            ".game-card-stack.is-expanded .card-details:not([hidden]){display:block!important;}"
            ".view-details-btn{cursor:pointer;pointer-events:auto;}"
            "</style>"
        )
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
        else:
            html = css + html
    html = ensure_toggle_pick_details_js(html)
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
    paths = {
        "ufc": ["https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"],
        "tennis": [
            "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
            "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard",
        ],
    }[sport]
    name_to_img: dict[str, str] = {}
    try:
        import ssl

        ctx = ssl._create_unverified_context()
        for path in paths:
            try:
                req = Request(path, headers={"User-Agent": "sports-sandbox-hub"})
                with urlopen(req, timeout=8, context=ctx) as resp:
                    data = json.loads(resp.read().decode())
            except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
                continue
            for ev in data.get("events") or []:
                for comp in ev.get("competitions") or []:
                    for c in comp.get("competitors") or []:
                        ath = c.get("athlete") or {}
                        name = (ath.get("displayName") or c.get("displayName") or "").strip()
                        aid = ath.get("id") or c.get("id")
                        if name and aid:
                            if sport == "ufc":
                                href = f"https://a.espncdn.com/i/headshots/mma/players/full/{aid}.png"
                            else:
                                href = f"https://a.espncdn.com/i/headshots/tennis/players/full/{aid}.png"
                            name_to_img[name.lower()] = href
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


def clamp_soccer_absurd_pl(html: str) -> str:
    """Rescale baseball-like PL soccer totals/spreads (e.g. Total 11, −9.5) to goal scale.

    Cup/small-sample H2H averages invent ~10-goal projections. Books stay untouched.
    When PL total > 5.5 or |spread| > 3, fall back to books (else 2.5 / −0.5).
    """
    if not html or "data-pick-card" not in html:
        return html

    def _round_half(x: float) -> float:
        return round(float(x) * 2.0) / 2.0

    def _num(text: str) -> float | None:
        m = re.search(r"([+\-−–]?\d+(?:\.\d+)?)", re.sub(r"<[^>]+>", " ", text or ""))
        if not m:
            return None
        s = m.group(1).replace("−", "-").replace("–", "-")
        try:
            return float(s)
        except ValueError:
            return None

    def _fav_spread_text(home: str, away: str, home_spread: float) -> str:
        # home_spread home-centric: negative => home favorite
        if home_spread <= 0:
            return f"{home} {home_spread:g}"
        return f"{away} {-home_spread:g}"

    def _home_spread_from_books(books_text: str, home: str, away: str) -> float | None:
        t = (books_text or "").strip()
        n = _num(t)
        if n is None:
            return None
        # "Columbus Crew -0.5" => that team is favorite at -0.5
        low = t.lower()
        if home and home.lower() in low:
            return n if n < 0 else -abs(n)
        if away and away.lower() in low:
            # away favorite -0.5 => home-centric +0.5
            return abs(n) if n < 0 else -abs(n)
        return n

    def _fix_card(card: str) -> str:
        home_m = re.search(r'data-home="([^"]*)"', card)
        away_m = re.search(r'data-away="([^"]*)"', card)
        home = home_m.group(1) if home_m else "Home"
        away = away_m.group(1) if away_m else "Away"
        pick_m = re.search(r'data-pick="([^"]*)"', card)
        pick = (pick_m.group(1) if pick_m else "") or ""

        pl_vals = list(
            re.finditer(r'(<[a-zA-Z][^>]*\bclass="[^"]*\bval-pl\b[^"]*"[^>]*>)([^<]*)(</)', card, re.I)
        )
        if len(pl_vals) < 2:
            return card

        spread_raw = pl_vals[0].group(2)
        total_raw = pl_vals[1].group(2)
        spread_v = _num(spread_raw)
        total_v = _num(total_raw)
        if spread_v is None or total_v is None:
            return card
        # Displayed PL spread is favorite-centric text ("Crew -9"); convert to home-centric.
        if home.lower() in spread_raw.lower():
            home_spread = spread_v if spread_v < 0 else -abs(spread_v)
        elif away.lower() in spread_raw.lower():
            home_spread = abs(spread_v) if spread_v < 0 else -abs(spread_v)
        else:
            home_spread = spread_v

        absurd = abs(home_spread) > 3.0 or total_v > 5.5 or total_v < 1.2
        if not absurd:
            return card

        books_total = None
        m_bt = re.search(r'data-books-total="([^"]+)"', card)
        if m_bt:
            books_total = _num(m_bt.group(1))
        books_spread_home = None
        m_bs = re.search(r'data-books-spread="([^"]+)"', card)
        if m_bs:
            books_spread_home = _home_spread_from_books(m_bs.group(1), home, away)

        new_total = books_total if books_total and 1.5 <= books_total <= 5.5 else 2.5
        new_total = _round_half(new_total)
        if books_spread_home is not None and abs(books_spread_home) <= 3.0:
            new_spread = _round_half(books_spread_home)
        else:
            # Mild AH toward ML pick
            if pick.lower() == away.lower():
                new_spread = 0.5
            else:
                new_spread = -0.5
            new_spread = _round_half(new_spread)

        ph = _round_half((new_total - new_spread) / 2.0)
        pa = _round_half(new_total - ph)
        spread_disp = _fav_spread_text(home, away, new_spread)
        total_disp = f"{new_total:g}"

        out = card
        for idx, m in reversed(list(enumerate(pl_vals[:2]))):
            repl = spread_disp if idx == 0 else total_disp
            out = out[: m.start(2)] + repl + out[m.end(2) :]

        proj = f"{away} {pa:g} – {home} {ph:g}"
        out = re.sub(
            r'(<[a-zA-Z][^>]*\bclass="[^"]*\bproj-val\b[^"]*"[^>]*>)([^<]*)(</)',
            rf"\g<1>{proj}\g<3>",
            out,
            count=1,
            flags=re.I,
        )
        out = re.sub(
            r'data-pl-proj="[^"]*"',
            f'data-pl-proj="{ph:g}–{pa:g}"',
            out,
            count=1,
            flags=re.I,
        )
        return out

    parts: list[str] = []
    last = 0
    for m in re.finditer(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", html, re.I):
        start = m.start()
        tag_end = html.find(">", start)
        if tag_end < 0:
            continue
        j = tag_end + 1
        depth = 1
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
            continue
        parts.append(html[last:start])
        parts.append(_fix_card(html[start:end]))
        last = end
    parts.append(html[last:])
    return "".join(parts)


def _balanced_div_at(html: str, start: int) -> tuple[str, int]:
    tag_end = html.find(">", start)
    if tag_end < 0:
        return "", -1
    j = tag_end + 1
    depth = 1
    while j < len(html) and depth > 0:
        no, nc = html.find("<div", j), html.find("</div>", j)
        if nc < 0:
            return "", -1
        if no >= 0 and no < nc:
            depth += 1
            j = no + 4
        else:
            depth -= 1
            if depth == 0:
                return html[start : nc + 6], nc + 6
            j = nc + 6
    return "", -1


def hide_unavailable_model_boxes(html: str) -> str:
    """Drop any pick-confidence pc-box rendered with N/A / null / empty values."""
    if not html or "pc-box" not in html:
        return html
    empty_re = re.compile(
        r"^\s*(N/?A|null|undefined|NaN|—|–|-|\.|none)?\s*$",
        re.I,
    )
    parts: list[str] = []
    pos = 0
    while True:
        m = re.search(r'<div class="pc-box[^"]*">', html[pos:], re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        box, end = _balanced_div_at(html, abs_start)
        if end < 0:
            parts.append(html[pos:])
            break
        parts.append(html[pos:abs_start])
        val_m = re.search(r'<div class="pc-val[^"]*"[^>]*>\s*([^<]*?)\s*</div>', box, re.I)
        side_m = re.search(r'<div class="pc-side[^"]*"[^>]*>\s*([^<]*?)\s*</div>', box, re.I)
        val = (val_m.group(1) if val_m else "").strip()
        side = (side_m.group(1) if side_m else "").strip()
        has_pct = bool(re.search(r"\d", val))
        emptyish = (
            empty_re.match(val or "") is not None
            or empty_re.match(side or "") is not None
            or val.lower() in ("n/a", "null", "undefined", "nan")
            or side.lower() in ("n/a", "null", "undefined", "nan")
            or (not val and not side)
        )
        if not (emptyish and not has_pct):
            parts.append(box)
        pos = end
    out = "".join(parts)
    # Results JS templates that inject N/A model cells — skip unavailable models
    out = re.sub(
        r"if\s*\(\s*!i\.valid\s*\)\s*\{\s*r\s*\+=\s*'[^']*N/A[^']*'\s*;\s*return;\s*\}",
        "if(!i.valid){ return; }",
        out,
        flags=re.I,
    )
    return out


# Back-compat alias
hide_wnba_unavailable_model_boxes = hide_unavailable_model_boxes


def dedupe_game_card_stacks(html: str) -> str:
    """Keep first game-card-stack per away+home+kickoff; drop identical duplicates."""
    if not html or "game-card-stack" not in html:
        return html
    parts: list[str] = []
    pos = 0
    seen: set[str] = set()
    while True:
        m = re.search(r'<div class="game-card-stack\b', html[pos:], flags=re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        box, end = _balanced_div_at(html, abs_start)
        if end < 0:
            parts.append(html[pos:])
            break
        parts.append(html[pos:abs_start])
        head = box[:1200]

        def _attr(k: str) -> str:
            am = re.search(rf'data-{k}=["\']([^"\']*)["\']', head, re.I)
            return (am.group(1) if am else "").strip().lower()

        key = f"{_attr('away')}|{_attr('home')}|{_attr('time')}"
        if key == "||" or key not in seen:
            if key != "||":
                seen.add(key)
            parts.append(box)
        pos = end
    return "".join(parts)


_HEADSHOT_OK_CACHE: dict[str, bool] = {}


def scrub_broken_espn_headshots(html: str) -> str:
    """Replace ESPN headshot URLs that 404 with a local placeholder (checker + UX)."""
    if not html or "espncdn.com" not in html:
        return html
    import ssl
    from concurrent.futures import ThreadPoolExecutor, as_completed

    placeholder = "/static/img/athlete-placeholder.svg"
    urls = sorted(
        set(
            re.findall(
                r"https://a\.espncdn\.com/i/headshots/(?:mma|tennis|golf)/players/full/\d+\.png",
                html,
            )
        )
    )
    if not urls:
        return html

    ctx = ssl._create_unverified_context()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36",
        "Accept": "image/*,*/*;q=0.8",
    }

    def _ok(url: str) -> bool:
        if url in _HEADSHOT_OK_CACHE:
            return _HEADSHOT_OK_CACHE[url]
        try:
            req = Request(url, method="HEAD", headers=headers)
            with urlopen(req, timeout=8, context=ctx) as resp:
                ok = int(getattr(resp, "status", 200) or 200) < 400
        except Exception as e:
            code = int(getattr(e, "code", 0) or 0)
            if code in (403, 405):
                try:
                    req = Request(url, headers={**headers, "Range": "bytes=0-32"})
                    with urlopen(req, timeout=8, context=ctx) as resp:
                        ok = int(getattr(resp, "status", 200) or 200) < 400
                except Exception as e2:
                    code2 = int(getattr(e2, "code", 0) or 0)
                    ok = 200 <= code2 < 400
            else:
                ok = False
        _HEADSHOT_OK_CACHE[url] = ok
        return ok

    bad: set[str] = set()
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_ok, u): u for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            try:
                if not fut.result():
                    bad.add(u)
            except Exception:
                bad.add(u)
    for u in bad:
        html = html.replace(u, placeholder)
    return html


def fix_mlb_results_display(html: str) -> str:
    """Sandbox-only: fill blank Season Efficiency + inject results analytics."""
    if not html or "Moneyline Accuracy by Model" not in html:
        return html
    try:
        from team_tabbed_results import (
            enrich_mlb_tally_units_html,
            inject_mlb_results_analytics_html,
            markets_from_live_html,
            patch_mlb_season_efficiency_html,
        )

        html = patch_mlb_season_efficiency_html(html)
        html = enrich_mlb_tally_units_html(html)
        payload = markets_from_live_html(html, "mlb")
        analytics = payload.get("analytics") or {}
        if analytics:
            html = inject_mlb_results_analytics_html(html, analytics, sport="mlb")
    except Exception as e:
        print(f"[sandbox_fixup] mlb results analytics: {e}", flush=True)
    return html


def ensure_toggle_pick_details_js(html: str) -> str:
    """Sandbox: View Details must work without live premium JS / login gate.

    Always inject last so this copy overrides a live/paywalled togglePickDetails.
    """
    if not html or "view-details-btn" not in html:
        return html
    if 'id="sandbox-toggle-pick-details"' in html or "id='sandbox-toggle-pick-details'" in html:
        return html
    script = """
<script id="sandbox-toggle-pick-details">
function _sandboxDetailsEl(btn){
  if(!btn) return null;
  var id=btn.getAttribute('aria-controls');
  var el=id?document.getElementById(id):null;
  if(el) return el;
  var stack=btn.closest('.game-card-stack,.game-card,.pick-card');
  el=stack?stack.querySelector('.card-details'):null;
  if(el) return el;
  var foot=btn.closest('.card-footer');
  return foot&&foot.parentNode?foot.parentNode.querySelector('.card-details'):null;
}
function _sandboxForcePickDetails(btn, open){
  var el=_sandboxDetailsEl(btn);
  if(!el) return;
  var card=btn.closest('.pick-card,.game-card,.game-card-stack');
  if(open){
    el.removeAttribute('hidden'); el.hidden=false;
    el.style.removeProperty('display');
    btn.setAttribute('aria-expanded','true');
    if(card) card.classList.add('is-expanded');
  } else {
    el.setAttribute('hidden',''); el.hidden=true;
    btn.setAttribute('aria-expanded','false');
    if(card) card.classList.remove('is-expanded');
  }
}
function togglePickDetails(btn){
  if(!btn) return;
  var el=_sandboxDetailsEl(btn);
  if(!el) return;
  var isOpen=!(el.hasAttribute('hidden') || el.hidden===true);
  _sandboxForcePickDetails(btn, !isOpen);
}
document.addEventListener('pointerdown', function(e){
  var t=e.target;
  if(!t || !t.closest) return;
  var btn=t.closest('.view-details-btn');
  if(!btn) return;
  btn.setAttribute('data-want-open', btn.getAttribute('aria-expanded')==='true' ? '0' : '1');
}, true);
document.addEventListener('click', function(e){
  var t=e.target;
  if(!t || !t.closest) return;
  var btn=t.closest('.view-details-btn');
  if(!btn) return;
  e.preventDefault();
  e.stopPropagation();
  if(e.stopImmediatePropagation) e.stopImmediatePropagation();
  var want=btn.getAttribute('data-want-open')!=='0';
  setTimeout(function(){ _sandboxForcePickDetails(btn, want); }, 0);
}, true);
</script>
"""
    if re.search(r"</body\s*>", html, re.I):
        return re.sub(r"</body\s*>", script + "</body>", html, count=1, flags=re.I)
    return html + script


def enrich_mlb_chart_data_attrs(html: str) -> str:
    """Backfill chart data-* attrs from View Details / face chips.

    Fills data-pl-spread / data-xs-spread / data-xs-proj when Odds & Lines or
    Projected Score rows are present, and data-total-ev from the card Total EV
    chip (Totals chart must not reuse moneyline data-edge).
    """
    if not html or "data-pick-card" not in html:
        return html

    def _cell(tr: str, cls: str) -> str:
        m = re.search(
            rf'<td\b[^>]*\bclass="[^"]*\b{cls}\b[^"]*"[^>]*>([^<]*)</td>',
            tr,
            flags=re.I,
        )
        return (m.group(1) if m else "").strip()

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]

        # Run Line row from Odds & Lines
        rl_tr = re.search(
            r"<tr>\s*<td\b[^>]*\bclass=\"[^\"]*\bmarket-k\b[^\"]*\"[^>]*>"
            r"\s*(?:Run Line|Spread|Puck Line)\s*</td>"
            r"([\s\S]*?)</tr>",
            rest,
            flags=re.I,
        )
        pl_rl = xs_rl = ""
        if rl_tr:
            pl_rl = _cell(rl_tr.group(0), "val-pl")
            xs_rl = _cell(rl_tr.group(0), "val-xs")

        # Projected score lines (View Details → Projected Score)
        # Keep labeled scoreline ("Away 4 – Home 5") when present.
        pl_proj = ""
        xs_proj = ""
        for m in re.finditer(
            r'<div\b[^>]*\bclass="[^"]*\bproj-row\b[^"]*"[^>]*>'
            r"([\s\S]*?)</div>",
            rest,
            flags=re.I,
        ):
            block = m.group(1)
            model_m = re.search(
                r'<span\b[^>]*\bclass="([^"]*\bproj-model\b[^"]*)"[^>]*>([^<]*)</span>',
                block,
                flags=re.I,
            )
            if not model_m:
                continue
            cls = (model_m.group(1) or "").lower()
            label = (model_m.group(2) or "").lower()
            val_m = re.search(
                r'<span\b[^>]*\bclass="[^"]*\bproj-val\b[^"]*"[^>]*>([^<]+)</span>',
                block,
                flags=re.I,
            )
            if not val_m:
                continue
            val = (val_m.group(1) or "").strip()
            if not val:
                continue
            nums = re.findall(r"(\d+(?:\.\d+)?)", val)
            if len(nums) < 2:
                continue
            is_pl = "prediction lab" in label or re.search(r"\bpl\b", cls)
            is_xs = "xsharp" in label or re.search(r"\bxs\b", cls)
            if is_pl and not pl_proj:
                pl_proj = val
            if is_xs and not xs_proj:
                xs_proj = val

        # Card face Total EV (e.g. -1.7%) — Totals chart column; not ML Edge
        total_ev = ""
        tev = re.search(
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*Total\s*EV\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            rest,
            flags=re.I,
        )
        if tev:
            raw = (tev.group(1) or "").strip()
            raw = re.sub(r"[%\s]+$", "", raw).strip()
            if raw and raw not in ("—", "-", "N/A", "n/a"):
                total_ev = raw

        # H2H Last 10 face chip → data-h2h (Totals chart H2H L10 column)
        h2h = ""
        h2h_m = re.search(
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*H2H\s*Last\s*10\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            rest,
            flags=re.I,
        )
        if not h2h_m:
            # Tolerate extra wrappers between label and value
            h2h_m = re.search(
                r'>\s*H2H\s*Last\s*10\s*<[\s\S]{0,180}?'
                r'class="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
                rest,
                flags=re.I,
            )
        if h2h_m:
            cand = (h2h_m.group(1) or "").strip()
            if cand and cand not in ("—", "-", "N/A", "n/a"):
                h2h = cand
        if not h2h:
            am = re.search(r'\bdata-h2h="([^"]+)"', open_tag, flags=re.I)
            if am and (am.group(1) or "").strip() not in ("—", "-", "N/A", "n/a"):
                h2h = am.group(1).strip()

        # Odds Total row (for xsproj3 when XSharp scoreline missing)
        tot_tr = re.search(
            r"<tr>\s*<td\b[^>]*\bclass=\"[^\"]*\bmarket-k\b[^\"]*\"[^>]*>"
            r"\s*Total\s*</td>"
            r"([\s\S]*?)</tr>",
            rest,
            flags=re.I,
        )
        xs_tot = pl_tot = books_tot = ""
        if tot_tr:
            books_tot = _cell(tot_tr.group(0), "val-books")
            pl_tot = _cell(tot_tr.group(0), "val-pl")
            xs_tot = _cell(tot_tr.group(0), "val-xs")
        if not books_tot:
            bm = re.search(r'\bdata-books-total="([^"]*)"', open_tag, flags=re.I)
            books_tot = (bm.group(1) if bm else "").strip()

        def _parse_total(raw: str) -> float | None:
            m = re.search(r"(\d+(?:\.\d+)?)", raw or "")
            if not m:
                return None
            try:
                n = float(m.group(1))
            except ValueError:
                return None
            return n if n > 0 else None

        def _round_half(n: float) -> float:
            return round(n * 2) / 2.0

        def _fmt_half(n: float) -> str:
            r = _round_half(n)
            return str(int(r)) if r == int(r) else str(r)

        # Prefer existing attr scorelines when enrich runs on already-filled tags
        if not pl_proj:
            am = re.search(r'\bdata-pl-proj="([^"]*)"', open_tag, flags=re.I)
            if am and (am.group(1) or "").strip():
                pl_proj = am.group(1).strip()
        if not xs_proj:
            am = re.search(r'\bdata-xs-proj="([^"]*)"', open_tag, flags=re.I)
            if am and (am.group(1) or "").strip():
                cand = am.group(1).strip()
                if len(re.findall(r"(\d+(?:\.\d+)?)", cand)) >= 2:
                    xs_proj = cand

        away_m = re.search(r'\bdata-away="([^"]*)"', open_tag, flags=re.I)
        home_m = re.search(r'\bdata-home="([^"]*)"', open_tag, flags=re.I)
        away_n = (away_m.group(1) if away_m else "").strip()
        home_n = (home_m.group(1) if home_m else "").strip()

        def _home_spread_from_label(text: str) -> float | None:
            raw = (text or "").strip()
            if not raw or raw in ("—", "-", "N/A"):
                return None
            # Soccer/MLB pick'em — even split (home spread 0).
            if re.fullmatch(r"(?i)pk|pick(?:\s*['’]?em)?|even|level", raw):
                return 0.0
            t = (
                raw.replace("\u2212", "-")
                .replace("\u2013", "-")
                .replace("\u2014", "-")
            )
            m = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*$", t)
            if not m:
                return None
            try:
                n = float(m.group(1))
            except ValueError:
                return None
            prefix = t[: m.start()].strip().lower()
            if home_n and home_n.lower() in prefix:
                return n
            if away_n and away_n.lower() in prefix:
                return -n
            return n  # bare / unknown: treat as home-centric

        def _labeled(a: float, h: float) -> str:
            if away_n and home_n:
                return f"{away_n} {_fmt_half(a)} – {home_n} {_fmt_half(h)}"
            return f"{_fmt_half(a)}–{_fmt_half(h)}"

        def _ensure_labeled(scoreline: str) -> str:
            """Prefer Away N – Home M so Totals chart never shows bare 5–4."""
            raw = (scoreline or "").strip()
            if not raw:
                return ""
            if re.search(r"[A-Za-z]", raw) and len(re.findall(r"(\d+(?:\.\d+)?)", raw)) >= 2:
                return raw
            nums = re.findall(r"(\d+(?:\.\d+)?)", raw)
            if len(nums) >= 2 and away_n and home_n:
                try:
                    return _labeled(float(nums[-2]), float(nums[-1]))
                except ValueError:
                    return raw
            return raw

        # Derive PL proj from Odds PL run line + PL total when Projected Score omitted.
        # PK / missing spread → even split from PL total (soccer cards often show PK).
        if not pl_proj:
            pl_spread_src = pl_rl
            if not pl_spread_src:
                am = re.search(r'\bdata-pl-spread="([^"]*)"', open_tag, flags=re.I)
                pl_spread_src = (am.group(1) if am else "").strip()
            hs = _home_spread_from_label(pl_spread_src)
            pt = _parse_total(pl_tot)
            if pt is not None and hs is None:
                hs = 0.0  # total alone → 50/50 scoreline for chart
            if hs is not None and pt is not None:
                home = _round_half((pt + hs) / 2.0)
                away = _round_half(pt - home)
                pl_proj = _labeled(away, home)

        # xsproj3: scale PL split to XSharp (or books) total — never bare — when total exists
        if not xs_proj:
            T = _parse_total(xs_tot) or _parse_total(books_tot)
            nums = re.findall(r"(\d+(?:\.\d+)?)", pl_proj or "")
            if T is not None and len(nums) >= 2:
                try:
                    pl_a = float(nums[-2])
                    pl_h = float(nums[-1])
                except ValueError:
                    pl_a = pl_h = 0.0
                if pl_a + pl_h > 0:
                    away = _round_half((pl_a / (pl_a + pl_h)) * T)
                    home = _round_half(T - away)
                    xs_proj = _labeled(away, home)
            elif T is not None:
                # No PL split — even XSharp total so Totals chart is not blank
                home = _round_half(T / 2.0)
                away = _round_half(T - home)
                xs_proj = _labeled(away, home)

        def _set_attr(tag: str, name: str, value: str, *, overwrite_empty: bool = True) -> str:
            if not value or value in ("—", "-", "N/A"):
                return tag
            esc = (
                value.replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
            )
            m_ex = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
            if m_ex:
                existing = (m_ex.group(1) or "").strip()
                # Prefer richer labeled scoreline over compact "4–5" / empty
                if existing and not overwrite_empty:
                    return tag
                if (
                    name in ("data-xs-proj", "data-pl-proj")
                    and existing
                    and re.search(r"[A-Za-z]", existing)
                    and not re.search(r"[A-Za-z]", value)
                ):
                    return tag
                return re.sub(
                    rf'\b{name}="[^"]*"',
                    f'{name}="{esc}"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            return tag[:-1] + f' {name}="{esc}">'

        pl_proj = _ensure_labeled(pl_proj)
        xs_proj = _ensure_labeled(xs_proj)

        # View Details: inject XSharp projected-score row when Odds/attrs have
        # XSharp total/scoreline but live omitted the row (pick-direction guard).
        rest2 = rest
        has_xs_row = bool(
            re.search(
                r'<span\b[^>]*\bclass="[^"]*\bproj-model\b[^"]*\bxs\b[^"]*"[^>]*>'
                r"\s*XSharp\s*</span>"
                r'|<span\b[^>]*\bclass="[^"]*\bproj-model\b[^"]*"[^>]*>\s*XSharp\s*</span>',
                rest2,
                flags=re.I,
            )
        )
        if xs_proj and not has_xs_row:
            xs_row_html = (
                '<div class="proj-row">'
                '<span class="proj-model xs">XSharp</span> '
                f'<span class="proj-val">{html_lib.escape(xs_proj)}</span>'
                "</div>"
            )
            # After last existing PL proj-row inside Projected Score box
            pl_row_m = re.search(
                r'(<div\b[^>]*\bclass="[^"]*\bproj-row\b[^"]*"[^>]*>'
                r'[\s\S]*?<span\b[^>]*\bclass="[^"]*\bproj-model\b[^"]*\bpl\b[^"]*"[^>]*>'
                r"[\s\S]*?</div>)",
                rest2,
                flags=re.I,
            )
            if not pl_row_m:
                # Tight fallback: same proj-row only (do not span past </div>)
                pl_row_m = re.search(
                    r'(<div\b[^>]*\bclass="[^"]*\bproj-row\b[^"]*"[^>]*>'
                    r'(?:(?!</div>).)*?Prediction Lab(?:(?!</div>).)*?</div>)',
                    rest2,
                    flags=re.I,
                )
            if pl_row_m:
                rest2 = (
                    rest2[: pl_row_m.end()]
                    + "\n            "
                    + xs_row_html
                    + rest2[pl_row_m.end() :]
                )
            else:
                title_m = re.search(
                    r'(<div\b[^>]*\bclass="[^"]*\bproj-score-title\b[^"]*"[^>]*>'
                    r"\s*Projected Score\s*</div>)",
                    rest2,
                    flags=re.I,
                )
                if title_m:
                    rest2 = (
                        rest2[: title_m.end()]
                        + "\n            "
                        + xs_row_html
                        + rest2[title_m.end() :]
                    )
                elif pl_proj or xs_proj:
                    # No Projected Score box — insert a minimal one before Pick Confidence
                    box = (
                        '<div class="proj-score-box">'
                        '<div class="proj-score-title">Projected Score</div>'
                    )
                    if pl_proj:
                        box += (
                            '<div class="proj-row">'
                            '<span class="proj-model pl">Prediction Lab</span> '
                            f'<span class="proj-val">{html_lib.escape(pl_proj)}</span>'
                            "</div>"
                        )
                    box += xs_row_html + "</div>"
                    conf_m = re.search(
                        r'<div\b[^>]*\bclass="[^"]*\bpick-conf-bar\b[^"]*"',
                        rest2,
                        flags=re.I,
                    )
                    if conf_m:
                        rest2 = rest2[: conf_m.start()] + box + rest2[conf_m.start() :]

        open2 = open_tag
        open2 = _set_attr(open2, "data-pl-spread", pl_rl)
        open2 = _set_attr(open2, "data-xs-spread", xs_rl)
        open2 = _set_attr(open2, "data-pl-proj", pl_proj)
        open2 = _set_attr(open2, "data-xs-proj", xs_proj)
        open2 = _set_attr(open2, "data-pl-total", pl_tot)
        open2 = _set_attr(open2, "data-xs-total", xs_tot)
        open2 = _set_attr(open2, "data-books-total", books_tot)
        open2 = _set_attr(open2, "data-total-ev", total_ev)
        open2 = _set_attr(open2, "data-h2h", h2h)
        if open2 == open_tag and rest2 == rest:
            return stack
        return open2 + rest2

    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


def enrich_soccer_h2h_from_db(html: str) -> str:
    """Fill data-h2h + View Details H2H chip from sandbox finals DB when history exists.

    Live attach uses min_games=2, so 1-meeting cups stay blank even though a prior
    total exists. Prefer sandbox DB (includes bra.1 catch-up) with min 1 meeting.
    Always keep the View Details H2H row: real average when history exists, else
    N/A (never hide the row; never leave an em-dash). Cards match Chart.
    Display-only — does not touch PL/XSharp / Efficiency math.

    Also patches results ``game-card`` footers (no data-pick-card) via team-name cols.
    """
    if not html:
        return html
    has_pick_cards = "data-pick-card" in html
    has_results_cards = bool(
        re.search(r'class="[^"]*\bgame-card\b', html, flags=re.I)
    )
    if not has_pick_cards and not has_results_cards:
        return html
    try:
        import sqlite3
        from pathlib import Path
    except Exception:
        return html

    db_candidates = [
        Path.home() / "Documents/Personal/soccer/data/sandbox_results.db",
        Path.home() / "Documents/Personal/predictionlabfix_work/sports_predictions_original.db",
    ]
    db_path = next((p for p in db_candidates if p.is_file()), None)
    if db_path is None:
        return html

    soccer_root = Path.home() / "Documents/Personal/soccer"
    soccer_s = str(soccer_root.resolve())
    if soccer_s not in sys.path:
        sys.path.insert(0, soccer_s)
    try:
        from engine.h2h_lookup import format_h2h_last10
    except Exception:
        format_h2h_last10 = None  # type: ignore[assignment]

    _bad = ("", "—", "-", "–", "‒", "N/A", "n/a")

    def _good(val: str) -> bool:
        return bool(val) and val.strip() not in _bad

    conn = sqlite3.connect(str(db_path))
    _h2h_cache: dict[tuple[str, str], str] = {}

    def _lookup(home: str, away: str) -> str:
        if not home or not away:
            return ""
        key = (home, away)
        if key in _h2h_cache:
            return _h2h_cache[key]
        val = ""
        if format_h2h_last10 is not None:
            try:
                val = format_h2h_last10(conn, home, away, n=10, min_games=1) or ""
            except Exception:
                val = ""
        _h2h_cache[key] = val
        _h2h_cache[(away, home)] = val
        return val

    def _set_attr(tag: str, name: str, value: str) -> str:
        if not value:
            return tag
        esc = (
            value.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
        )
        if re.search(rf'\b{name}="[^"]*"', tag, flags=re.I):
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{esc}"',
                tag,
                count=1,
                flags=re.I,
            )
        return tag[:-1] + f' {name}="{esc}">'

    def _chip_html(h2h: str) -> str:
        return (
            '<div class="sf-item">'
            '<span class="sf-label">H2H Last 10</span> '
            f'<span class="sf-val">{html_lib.escape(h2h)}</span>'
            "</div>"
        )

    def _h2h_display(h2h: str) -> str:
        """User-facing H2H Last 10: average string, or N/A when teams never met."""
        return h2h if _good(h2h) else "N/A"

    def _ensure_chip(rest: str, h2h: str) -> str:
        """Insert or replace View Details H2H chip so every card shows the row.

        Real averages when history exists; N/A when true zero-history so Cards
        match Chart (column always present) and live-style footers never omit the label.
        """
        display = _h2h_display(h2h)
        chip_re = re.compile(
            r'<div\b[^>]*\bclass="[^"]*\bsf-item\b[^"]*"[^>]*>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*H2H\s*Last\s*10\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]*?)\s*</span>\s*'
            r"</div>",
            flags=re.I,
        )
        m = chip_re.search(rest)
        if m:
            cur = (m.group(1) or "").strip()
            if cur == display:
                return rest
            return rest[: m.start()] + _chip_html(display) + rest[m.end() :]
        # No chip yet — inject into odds-extras-footer (or synthesize one)
        foot_m = re.search(
            r'(<div\b[^>]*\bclass="[^"]*\bodds-extras-footer\b[^"]*"[^>]*>)',
            rest,
            flags=re.I,
        )
        if foot_m:
            return (
                rest[: foot_m.end()]
                + "\n        "
                + _chip_html(display)
                + rest[foot_m.end() :]
            )
        conf_m = re.search(
            r'<div\b[^>]*\bclass="[^"]*\bpick-conf-bar\b[^"]*"',
            rest,
            flags=re.I,
        )
        if conf_m:
            return (
                rest[: conf_m.start()]
                + '<div class="odds-extras-footer">'
                + _chip_html(display)
                + "</div>"
                + rest[conf_m.start() :]
            )
        return rest

    def _chip_value(rest: str) -> str:
        chip_m = re.search(
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*H2H\s*Last\s*10\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            rest,
            flags=re.I,
        )
        if not chip_m:
            return ""
        return (chip_m.group(1) or "").strip()

    def _resolve_h2h(home: str, away: str, existing: str, chip_val: str) -> str:
        # Prefer sandbox DB so 1-game meetings (live min_games=2) still display.
        db_h2h = _lookup(home, away) if home and away else ""
        if _good(db_h2h):
            return db_h2h
        if _good(existing):
            return existing
        if _good(chip_val):
            return chip_val
        return ""

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        existing = ""
        am = re.search(r'\bdata-h2h="([^"]*)"', open_tag, flags=re.I)
        if am:
            existing = html_lib.unescape((am.group(1) or "").strip())
        chip_val = _chip_value(rest)
        away_m = re.search(r'\bdata-away="([^"]*)"', open_tag, flags=re.I)
        home_m = re.search(r'\bdata-home="([^"]*)"', open_tag, flags=re.I)
        away = html_lib.unescape(away_m.group(1) if away_m else "").strip()
        home = html_lib.unescape(home_m.group(1) if home_m else "").strip()
        h2h = _resolve_h2h(home, away, existing, chip_val)

        # Always stamp data-h2h so Chart Totals H2H L10 is a number or N/A (never —).
        open2 = _set_attr(open_tag, "data-h2h", _h2h_display(h2h))
        rest2 = _ensure_chip(rest, h2h)
        if open2 == open_tag and rest2 == rest:
            return stack
        return open2 + rest2

    def _patch_results_card(card: str) -> str:
        """Results ``game-card`` (no data-pick-card): fill H2H footer from team names."""
        open_m = re.match(
            r'(<div\b[^>]*\bclass="[^"]*\bgame-card\b[^"]*"[^>]*>)',
            card,
            flags=re.I,
        )
        if not open_m:
            return card
        open_tag = open_m.group(1)
        # Inner pick-card under a stack — already handled by _patch_stack.
        if re.search(r"\bpick-card\b", open_tag, flags=re.I):
            return card
        rest = card[open_m.end() :]
        names = re.findall(
            r'<div\b[^>]*\bclass="[^"]*\bteam-name\b[^"]*"[^>]*>\s*([^<]+?)\s*</div>',
            rest,
            flags=re.I,
        )
        if len(names) < 2:
            return card
        away = html_lib.unescape(names[0]).strip()
        home = html_lib.unescape(names[1]).strip()
        chip_val = _chip_value(rest)
        h2h = _resolve_h2h(home, away, "", chip_val)
        rest2 = _ensure_chip(rest, h2h)
        if rest2 == rest:
            return card
        return open_tag + rest2

    out = html
    try:
        if has_pick_cards:
            parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', out, flags=re.I)
            if len(parts) > 1:
                out = parts[0] + "".join(_patch_stack(p) for p in parts[1:])

        if has_results_cards:
            parts_r = re.split(
                r'(?=<div\b[^>]*\bclass="[^"]*\bgame-card\b[^"]*"[^>]*>)',
                out,
                flags=re.I,
            )
            if len(parts_r) > 1:
                out = parts_r[0] + "".join(_patch_results_card(p) for p in parts_r[1:])
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return out


def _flip_spread_side_text(text: str, home: str, away: str) -> str:
    """Invert favorite↔dog spread display. 'Orioles -1.5' → 'Angels -1.5'.

    Home-centric: negate the implied home spread, then reformat as fav −line.
    Leaves bare empties / N/A alone. ML and totals are never passed here.
    """
    raw = (text or "").strip()
    if not raw or raw in ("—", "–", "-", "‒", "N/A", "n/a"):
        return text
    home = (home or "").strip()
    away = (away or "").strip()
    t = (
        raw.replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("&minus;", "-")
    )
    m = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*$", t)
    if not m:
        return text
    try:
        n = float(m.group(1))
    except ValueError:
        return text
    mag_s = m.group(1).lstrip("+-")
    prefix = t[: m.start()].strip()
    # Home-centric spread from favorite-centric label
    if home and home.lower() in prefix.lower():
        home_spread = n  # "Home -1.5" => -1.5; "Home +1.5" => +1.5
    elif away and away.lower() in prefix.lower():
        home_spread = -n  # "Away -1.5" => home +1.5
    elif prefix:
        # Unknown team token — flip sign on the same label
        flipped = f"+{mag_s}" if n < 0 else f"-{mag_s}"
        return f"{prefix} {flipped}".strip()
    else:
        # Bare number: negate
        return f"+{mag_s}" if n < 0 else f"-{mag_s}"

    flipped_home = -home_spread
    # Favorite-centric label (negative number on the favored side)
    if flipped_home <= 0:
        side = home or prefix
        return f"{side} -{mag_s}".replace("--", "-") if side else f"-{mag_s}"
    side = away or prefix
    return f"{side} -{mag_s}".replace("--", "-") if side else f"-{mag_s}"


def flip_mlb_model_spread_display(html: str) -> str:
    """After cards are built: invert displayed PL/XSharp run-line sides only.

    Books run line / data-books-spread stay market. ML + totals unchanged.
    Display/publish layer for current slate — does not rewrite graded results DB.
    Controlled by MLB_FLIP_SPREAD (default OFF).
    """
    if not MLB_FLIP_SPREAD or not html or "data-pick-card" not in html:
        return html

    def _cell_repl(tr: str, cls: str, home: str, away: str) -> str:
        def _sub(m: re.Match[str]) -> str:
            inner = m.group(2)
            flipped = _flip_spread_side_text(inner, home, away)
            return f"{m.group(1)}{flipped}{m.group(3)}"

        return re.sub(
            rf'(<td\b[^>]*\bclass="[^"]*\b{cls}\b[^"]*"[^>]*>)([^<]*)(</td>)',
            _sub,
            tr,
            count=1,
            flags=re.I,
        )

    def _patch_stack(stack: str) -> str:
        home_m = re.search(r'\bdata-home="([^"]*)"', stack, re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', stack, re.I)
        home = (home_m.group(1) if home_m else "").strip()
        away = (away_m.group(1) if away_m else "").strip()
        if not home or not away:
            names = re.findall(
                r'<div class="team-name">\s*([^<]+?)\s*</div>', stack, flags=re.I
            )
            if len(names) >= 2:
                away = away or names[0].strip()
                home = home or names[1].strip()
        if not home or not away:
            return stack

        def _flip_rl_row(tr: str) -> str:
            # Keep Books; flip model PL + XSharp only
            tr2 = _cell_repl(tr, "val-pl", home, away)
            tr2 = _cell_repl(tr2, "val-xs", home, away)
            return tr2

        def _rl_row_sub(m: re.Match[str]) -> str:
            return _flip_rl_row(m.group(0))

        stack2 = re.sub(
            r"<tr>\s*<td\b[^>]*\bclass=\"[^\"]*\bmarket-k\b[^\"]*\"[^>]*>"
            r"\s*(?:Run Line|Spread|Puck Line)\s*</td>"
            r"[\s\S]*?</tr>",
            _rl_row_sub,
            stack,
            flags=re.I,
        )

        def _attr_flip(tag: str, name: str) -> str:
            m = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
            if not m:
                return tag
            flipped = _flip_spread_side_text(m.group(1), home, away)
            if flipped == m.group(1):
                return tag
            esc = (
                flipped.replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
            )
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{esc}"',
                tag,
                count=1,
                flags=re.I,
            )

        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack2, flags=re.I)
        if open_m:
            open_tag = open_m.group(1)
            open2 = _attr_flip(open_tag, "data-pl-spread")
            open2 = _attr_flip(open2, "data-xs-spread")
            # Never flip books
            if open2 != open_tag:
                stack2 = open2 + stack2[open_m.end() :]
        return stack2

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


def enrich_sparse_pick_card_attrs(html: str) -> str:
    """Backfill data-home/away/pick/conf/time on sparse stacks (e.g. CFL)."""
    if not html or "data-pick-card" not in html:
        return html

    def _set_attr(tag: str, name: str, value: str) -> str:
        if not value:
            return tag
        esc = (
            value.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
        )
        m_ex = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
        if m_ex:
            existing = (m_ex.group(1) or "").strip()
            if existing:
                return tag
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{esc}"',
                tag,
                count=1,
                flags=re.I,
            )
        return tag[:-1] + f' {name}="{esc}">'

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        names = re.findall(
            r'<div class="team-name">\s*([^<]+?)\s*</div>', rest, flags=re.I
        )
        away = names[0].strip() if len(names) >= 1 else ""
        home = names[1].strip() if len(names) >= 2 else ""
        pick = ""
        conf = ""
        fav = re.search(
            r'<div class="team-slot[^"]*\bfavored\b[^"]*"[^>]*>[\s\S]*?'
            r'<div class="team-name">\s*([^<]+?)\s*</div>',
            rest,
            flags=re.I,
        )
        if fav:
            pick = fav.group(1).strip()
        if not pick and names:
            best_pct = -1.0
            for slot in re.finditer(
                r'<div class="team-slot[^"]*"[^>]*>([\s\S]*?)</div>\s*'
                r'(?=<div class="team-slot|</div>\s*<div class="matchup-at|</div>\s*<div class="lines-strip|</div>\s*<footer)',
                rest,
                flags=re.I,
            ):
                block = slot.group(1)
                nm = re.search(r'<div class="team-name">\s*([^<]+)', block, re.I)
                wp = re.search(
                    r'<div class="win-pct">\s*([0-9.]+)', block, re.I
                )
                if not nm or not wp:
                    continue
                try:
                    pct = float(wp.group(1))
                except ValueError:
                    continue
                if pct > best_pct:
                    best_pct = pct
                    pick = nm.group(1).strip()
                    conf = wp.group(1)
        if pick and not conf:
            for nm, wp in zip(
                re.findall(r'<div class="team-name">\s*([^<]+)', rest, re.I),
                re.findall(r'<div class="win-pct">\s*([0-9.]+)', rest, re.I),
            ):
                if nm.strip().lower() == pick.lower():
                    conf = wp
                    break
        time_m = re.search(
            r'<span class="game-time">\s*([^<]+?)\s*</span>', rest, flags=re.I
        )
        time_s = (time_m.group(1).strip() if time_m else "")
        open2 = open_tag
        open2 = _set_attr(open2, "data-away", away)
        open2 = _set_attr(open2, "data-home", home)
        open2 = _set_attr(open2, "data-pick", pick)
        open2 = _set_attr(open2, "data-conf", conf)
        open2 = _set_attr(open2, "data-time", time_s)
        # Model spread/total chips → chart attrs when odds Books column absent
        ms = re.search(
            r'line-chip-label">\s*Model spread\s*</div>\s*'
            r'<div class="line-chip-val[^"]*">([^<]+)</div>',
            rest,
            re.I,
        )
        mt = re.search(
            r'line-chip-label">\s*Model total\s*</div>\s*'
            r'<div class="line-chip-val[^"]*">([^<]+)</div>',
            rest,
            re.I,
        )
        if ms:
            open2 = _set_attr(open2, "data-pl-spread", ms.group(1).strip())
            open2 = _set_attr(open2, "data-xs-spread", ms.group(1).strip())
        # MLB chart model series from Pick Confidence (home-win %). Do not
        # invent values when the card has no pc-box / locked pick.
        name_attr = {
            "grinder2": "data-m-grinder2",
            "takedown": "data-m-takedown",
            "edge": "data-m-edge",
            "xsharp": "data-m-xsharp",
            "efficiency": "data-m-efficiency",
            "sharp consensus": "data-m-consensus",
            "sharp cons.": "data-m-consensus",
        }
        home_l = home.lower()
        away_l = away.lower()
        for box in re.finditer(
            r'<div class="pc-box[^"]*">\s*'
            r'<div class="pc-name">\s*([^<]+?)\s*</div>\s*'
            r'<div class="pc-val">\s*([0-9.]+)\s*%?\s*</div>\s*'
            r'<div class="pc-side[^"]*">\s*([^<]+?)\s*</div>',
            rest,
            flags=re.I,
        ):
            attr = name_attr.get((box.group(1) or "").strip().lower())
            if not attr:
                continue
            try:
                fav_pct = float(box.group(2))
            except ValueError:
                continue
            side = (box.group(3) or "").strip().lower()
            if home_l and side == home_l:
                home_pct = fav_pct
            elif away_l and side == away_l:
                home_pct = 100.0 - fav_pct
            else:
                continue
            open2 = _set_attr(open2, attr, f"{home_pct:.1f}")
        if open2 == open_tag:
            return stack
        return open2 + rest

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


_PICKS_CHART_CONTROLS_RE = re.compile(
    r'(?:<style id="picks-chart-scaffold">[\s\S]*?</style>\s*)?'
    r'<div class="picks-view-controls">\s*'
    r'<div class="pv-toggle"[^>]*>[\s\S]*?</div>\s*</div>\s*',
    flags=re.I,
)


def _insert_picks_chart_controls(html: str, chunk: str) -> str:
    """Place Cards|Chart toggle below site header (MLB pattern), never above it."""
    if not html or not chunk:
        return html
    hm = _PL2_HEADER_RE.search(html)
    header_end = hm.end() if hm else 0
    for sel, after_open in (
        (r'(<div class="section-tabs"[\s\S]*?</div>)', True),
        (r'(<div class="sport-picks-writeup"[^>]*>[\s\S]*?</div>)', True),
        (r'(<div class="header">\s*<h1[^>]*>[\s\S]*?</h1>\s*</div>)', True),
        (r'(<h1\b[^>]*(?:pageHeading|page-title)[^>]*>[\s\S]*?</h1>)', True),
        (r'(<div class="(?:container|golf-wrap)"\b[^>]*>)', True),
        (r'(<main\b[^>]*>)', True),
    ):
        m = re.search(sel, html, flags=re.I)
        if m and m.start() >= header_end:
            pos = m.end() if after_open else m.start()
            return html[:pos] + chunk + html[pos:]
    if hm:
        return html[:header_end] + chunk + html[header_end:]
    if re.search(r"<body\b[^>]*>", html, flags=re.I):
        return re.sub(r"(<body\b[^>]*>)", r"\1" + chunk, html, count=1, flags=re.I)
    return chunk + html


def _relocate_picks_chart_controls_below_header(html: str) -> str:
    """Move Cards|Chart toggle below pl2-header when it was injected above chrome."""
    hm = _PL2_HEADER_RE.search(html)
    if not hm:
        return html
    before = html[: hm.start()]
    cm = _PICKS_CHART_CONTROLS_RE.search(before)
    if not cm:
        return html
    chunk = cm.group(0)
    html_wo = before[: cm.start()] + before[cm.end() :] + html[hm.start() :]
    return _insert_picks_chart_controls(html_wo, chunk)


def ensure_picks_chart_scaffold(html: str) -> str:
    """Inject Cards|Chart toggle + date-section/chart-table-wrap when missing.

    Live-parity MLB/Soccer/WNBA/Tennis pages already have this. CFL/UFC isolation
    fragments often do not — add a minimal scaffold so picks-chart.js can run.
    """
    if not html or "data-pick-card" not in html:
        return html
    if "setPicksView" in html and "chart-table-wrap" in html:
        return _relocate_picks_chart_controls_below_header(html)

    css = """
<style id="picks-chart-scaffold">
.picks-view-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;max-width:1200px;margin:0 auto 12px;padding:0 4px;}
.pv-toggle{display:inline-flex;border:1px solid #cbd5e1;border-radius:999px;overflow:hidden;background:#fff;}
.pv-btn{border:0;background:transparent;color:#475569;font-size:0.8em;font-weight:700;padding:6px 14px;cursor:pointer;}
.pv-btn.active{background:#0c1e3a;color:#fff;}
.chart-table-wrap{display:none;margin-top:4px;-webkit-overflow-scrolling:touch;}
.date-section.chart-mode .games-grid{display:none !important;}
.date-section.chart-mode .game-card-stack{display:none !important;}
.date-section.chart-mode .chart-table-wrap{display:block;max-height:78vh;overflow:auto;border:1px solid rgba(15,23,42,0.12);border-radius:10px;}
.picks-chart-table{width:100%;border-collapse:separate;border-spacing:0;font-size:0.8em;background:#fff;}
.picks-chart-table th,.picks-chart-table td{padding:8px 10px;border-bottom:1px solid #e2e8f0;text-align:left;}
.picks-chart-table th{background:#f8fafc;font-weight:800;color:#0c1e3a;}
.picks-chart-table .mu-away{display:block;font-weight:700;}
.picks-chart-table .mu-home{display:block;color:#64748b;font-size:0.92em;}
.picks-chart-table .pct-league-row td{background:#f1f5f9;font-weight:800;letter-spacing:0.03em;}
</style>
"""
    controls = (
        '<div class="picks-view-controls">'
        '<div class="pv-toggle" role="group" aria-label="Picks view">'
        '<button type="button" class="pv-btn active" id="pvCardsBtn" '
        "onclick=\"setPicksView('cards')\">Cards</button>"
        '<button type="button" class="pv-btn" id="pvChartBtn" '
        "onclick=\"setPicksView('chart')\">Chart</button>"
        "</div></div>"
    )
    boot = "<script>var picksViewMode='cards';</script>"

    # Prefer wrapping an existing games-grid; else wrap all pick stacks in container.
    if re.search(r'class="[^"]*\bgames-grid\b', html, flags=re.I):
        html = re.sub(
            r'(<div class="[^"]*\bgames-grid\b[^"]*"[^>]*>)',
            r'<div class="date-section visible" id="pc-slate">'
            r'<div class="chart-table-wrap" id="pc-chart"></div>\1',
            html,
            count=1,
            flags=re.I,
        )
        # Close date-section after games-grid (balanced-ish: after first games-grid's close is hard;
        # append chart already before grid; close section before </main>/container end).
        if 'id="pc-slate"' in html and "</div><!--pc-slate-->" not in html:
            # Close slate only before site chrome / body end — never before a card
            # <footer class="card-footer"> (that mangled CFL isolation cards).
            html = re.sub(
                r'(</div>\s*)(</div>\s*</body>|</main>|'
                r'</div>\s*<footer\b[^>]*\bsite-directory-footer)',
                r"\1</div><!--pc-slate-->\2",
                html,
                count=1,
                flags=re.I,
            )
            if "</div><!--pc-slate-->" not in html:
                html = re.sub(
                    r"</body\s*>",
                    "</div><!--pc-slate--></body>",
                    html,
                    count=1,
                    flags=re.I,
                )
    else:
        # Insert section open before first pick card; close before body end
        html = re.sub(
            r'(?=<div\b[^>]*\bdata-pick-card\b)',
            '<div class="date-section visible" id="pc-slate">'
            '<div class="chart-table-wrap" id="pc-chart"></div>',
            html,
            count=1,
            flags=re.I,
        )
        if re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(
                r"</body\s*>",
                "</div><!--pc-slate--></body>",
                html,
                count=1,
                flags=re.I,
            )

    # Controls after site header / section-tabs — never above pl2-header.
    html = _insert_picks_chart_controls(html, css + controls)

    if "picksViewMode" not in html:
        if re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(r"</body\s*>", boot + "</body>", html, count=1, flags=re.I)
        else:
            html = html + boot

    # Stub setPicksView so onclick works before picks-chart.js overrides it
    if "function setPicksView" not in html and "setPicksView=" not in html:
        stub = (
            "<script>function setPicksView(mode){picksViewMode="
            "(mode==='chart')?'chart':'cards';}</script>"
        )
        if re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(r"</body\s*>", stub + "</body>", html, count=1, flags=re.I)
        else:
            html = html + stub
    return html


def inject_picks_chart_tabs(
    html: str,
    *,
    sport: str,
    markets: list[str] | None = None,
    is_premium: bool | None = None,
) -> str:
    """Cards|Chart + market tabs via generic picks-chart.js (sandbox hub).

    Overrides dense all-column chart builders. Values from card data-* / face /
    View Details. MLB Cards/Chart UI is signed off — only load for other sports
    or when MLB still needs the shared script.
    """
    if not html:
        return html
    sport_l = (sport or "").lower()
    markets = markets or ["moneyline", "spread", "totals"]
    premium = True if is_premium is None else bool(is_premium)
    # Live template already has Moneyline|Spread|Totals (setPicksChartMarket) — do not
    # inject a second picks-chart.js (WNBA/MLB signed-off inline chart).
    if "setPicksChartMarket" in html:
        return _relocate_picks_chart_controls_below_header(html)
    if "picks-chart.js" in html and "PICKS_CHART" in html:
        prem_js = "true" if premium else "false"
        html = re.sub(
            r"<script>window\.PICKS_CHART=\{.*?\};</script>",
            lambda m: m.group(0)
            if '"isPremium"' in m.group(0)
            else m.group(0).replace("};</script>", f',"isPremium":{prem_js}}};</script>'),
            html,
            count=1,
            flags=re.S,
        )
        return _relocate_picks_chart_controls_below_header(html)
    html = ensure_picks_chart_scaffold(html)
    # Need chart scaffold (live-parity pages already have it; CFL/UFC get one injected)
    if "chart-table-wrap" not in html:
        return html

    # Tennis: no totals EV. CFL: show Total EV only when a real number is on
    # the card (current-week CFL.ca O/U vs model). Anonymous pages strip EV
    # after this inject — keep the column off so it never renders as a dash.
    if sport_l == "tennis":
        has_total_ev = False
    elif sport_l == "cfl":
        has_total_ev = bool(re.search(r'\bdata-total-ev="[^"]*[0-9]', html))
    else:
        has_total_ev = True
    cfg = {
        "sport": sport_l,
        "markets": markets,
        "showRunLineConfidence": False,
        # CFL/tennis cards omit Books lines — chart must not show Books columns/dashes.
        "showBooks": sport_l not in ("cfl", "tennis"),
        "isPremium": premium,
        "hasTotalEv": has_total_ev,
    }
    cfg_js = json.dumps(cfg, separators=(",", ":"))
    # mlb-picks-chart.js: signed-off note + MLB config (optional); picks-chart.js = impl
    note = (
        "<!-- MLB picks Cards/Chart UI signed off — no more MLB UI churn unless broken -->"
        if sport_l == "mlb"
        else ""
    )
    assets_sync = (
        f"{note}"
        f"<script>window.PICKS_CHART={cfg_js};</script>"
        '<link rel="stylesheet" href="/static/css/picks-chart.css?v=pc17" />'
        '<script src="/static/js/picks-chart.js?v=pc17"></script>'
    )
    assets_head = (
        f"{note}"
        f"<script>window.PICKS_CHART={cfg_js};</script>"
        '<link rel="stylesheet" href="/static/css/picks-chart.css?v=pc17" />'
        '<script src="/static/js/picks-chart.js?v=pc17" defer></script>'
    )
    if re.search(r"</body\s*>", html, flags=re.I):
        return re.sub(r"</body\s*>", assets_sync + "</body>", html, count=1, flags=re.I)
    if re.search(r"</head\s*>", html, flags=re.I):
        return re.sub(r"</head\s*>", assets_head + "</head>", html, count=1, flags=re.I)
    return html + assets_sync


def inject_mlb_picks_chart_tabs(html: str) -> str:
    """MLB: Moneyline | Spread | Totals chart (signed-off UI)."""
    return inject_picks_chart_tabs(
        html, sport="mlb", markets=["moneyline", "spread", "totals"]
    )


def inject_mlb_run_line_confidence(html: str) -> str:
    """Add user-facing Run Line Confidence chips on MLB pick cards (sandbox only).

    Uses card data-* attrs + Books run line / Edge chips only.
    Never invents SP/bullpen/weather stats. No methodology dump in HTML.
    Loads run_line_v2 by file path (avoids mlb.optimization package init / pandas).
    Skipped for anonymous/paywalled pages so chart tabs cannot scrape RL confidence.
    """
    if not html or ("game-card-stack" not in html and "game-card" not in html):
        return html
    # Live paywall: anon pages lock View Details — do not inject RL confidence teaser.
    if "odds-pricing-locked" in html and 'data-m-consensus="' not in html:
        return html
    if html.count("Run Line Confidence") >= 3:
        return html
    try:
        import importlib.util
        from pathlib import Path

        rl_path = Path(__file__).resolve().parent / "mlb_run_line_v2.py"
        spec = importlib.util.spec_from_file_location("mlb_run_line_v2_sandbox", rl_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {rl_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        projected_run_margin = mod.projected_run_margin
        run_line_confidence = mod.run_line_confidence
    except Exception as e:
        print(f"[mlb_ui_fixup] run_line_v2 import: {e}", flush=True)
        return html

    def _f(raw: str | None) -> float | None:
        if raw is None:
            return None
        try:
            return float(str(raw).strip().replace("%", ""))
        except ValueError:
            return None

    def _patch_stack(stack: str) -> str:
        if "Run Line Confidence" in stack:
            return stack
        # Prefer structured data attrs on game-card-stack
        home = re.search(r'\bdata-home="([^"]*)"', stack, re.I)
        away = re.search(r'\bdata-away="([^"]*)"', stack, re.I)
        conf_raw = re.search(r'\bdata-conf="([^"]*)"', stack, re.I)
        edge_raw = re.search(r'\bdata-edge="([^"]*)"', stack, re.I)
        books_spread = re.search(r'\bdata-books-spread="([^"]*)"', stack, re.I)
        pick = re.search(r'\bdata-pick="([^"]*)"', stack, re.I)

        home_name = (home.group(1) if home else "").strip()
        away_name = (away.group(1) if away else "").strip()
        conf_pct = _f(conf_raw.group(1) if conf_raw else None)
        market_edge = _f(edge_raw.group(1) if edge_raw else None)
        if market_edge is not None and abs(market_edge) > 1.5:
            market_edge = market_edge / 100.0

        home_win = None
        if conf_pct is not None:
            p = conf_pct / 100.0 if conf_pct > 1.5 else conf_pct
            pick_name = (pick.group(1) if pick else "").strip().lower()
            if home_name and pick_name and pick_name in home_name.lower():
                home_win = p
            elif away_name and pick_name and pick_name in away_name.lower():
                home_win = 1.0 - p
            else:
                home_win = p  # assume pick-side conf maps to home if unknown

        book_spread = None
        spread_txt = books_spread.group(1) if books_spread else ""
        if not spread_txt:
            rl_probe = re.search(
                r'line-chip-label">\s*Books run line\s*</div>\s*<div class="line-chip-val[^"]*">([^<]+)</div>',
                stack,
                re.I,
            )
            if rl_probe:
                spread_txt = rl_probe.group(1) or ""
        sm = re.search(r"([+\-]?\d+(?:\.\d+)?)", spread_txt)
        if sm:
            try:
                mag = abs(float(sm.group(1)))
                # Home-centric: negative if home is favorite on the run line
                if home_name and home_name.lower() in spread_txt.lower():
                    book_spread = -mag
                elif away_name and away_name.lower() in spread_txt.lower():
                    book_spread = mag
                else:
                    book_spread = -mag
            except ValueError:
                book_spread = None

        # Model agreement from pc-boxes when present
        agree_n = None
        models = list(
            re.finditer(
                r'<div class="pc-name">([^<]+)</div>\s*'
                r'<div class="pc-val">([^<]*)</div>\s*'
                r'<div class="pc-side[^"]*"',
                stack,
                re.I,
            )
        )
        if models and home_win is not None:
            agree_n = 0
            pick_home = home_win >= 0.5
            for m in re.finditer(
                r'<div class="pc-side\s+(home|away)[^"]*"[^>]*>',
                stack,
                re.I,
            ):
                lean_home = m.group(1).lower() == "home"
                if lean_home == pick_home:
                    agree_n += 1

        margin = projected_run_margin(home_win_prob=home_win, book_spread=book_spread)
        conf = run_line_confidence(
            margin,
            models_agree_n=agree_n,
            market_edge=market_edge,
        )
        _rl_tip = (
            "How strongly our run-line model favors its side (0–100). "
            "One score for that run-line lean — not a second PL or XSharp pick."
        )
        chip = (
            '<div class="line-chip rl-confidence-chip">'
            '<div class="line-chip-label">Run Line Confidence '
            f'<button type="button" class="pct-info-btn rl-conf-info" title="{_rl_tip}" '
            f'aria-label="{_rl_tip}">ⓘ</button></div>'
            f'<div class="line-chip-val">{conf:.0f}</div>'
            "</div>"
        )
        rl = re.search(
            r'(<div class="line-chip">\s*'
            r'<div class="line-chip-label">\s*Books run line\s*</div>\s*'
            r'<div class="line-chip-val[^"]*">[^<]*</div>\s*</div>)',
            stack,
            re.I,
        )
        if rl:
            return stack.replace(rl.group(1), rl.group(1) + "\n    " + chip, 1)
        # Fallback: end of lines-strip
        ls = re.search(r'(<div class="lines-strip">[\s\S]*?)(</div>\s*(?:<footer|<!--|</div>\s*</div>))', stack, re.I)
        if ls:
            return stack.replace(ls.group(1), ls.group(1) + "\n    " + chip + "\n", 1)
        return stack

    parts = re.split(r'(?=<div class="game-card-stack\b)', html)
    if len(parts) <= 1:
        parts = re.split(r'(?=<div class="game-card\b)', html)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])



def _inject_consensus_if_results(html: str, sport: str, which: str) -> str:
    if (which or "").lower() != "results" or not html:
        return html
    try:
        from team_tabbed_results import inject_consensus_records_html

        return inject_consensus_records_html(html, sport=sport)
    except Exception as e:
        print(f"[sandbox_fixup] consensus inject ({sport}): {e}", flush=True)
        return html


def apply_sport_fixups(
    html: str,
    sport: str,
    which: str = "picks",
    *,
    unlock_paywall: bool = True,
    is_premium: bool | None = None,
) -> str:
    """Apply all sandbox fixups for a proxied sport page."""
    if not html:
        return html
    sport = (sport or "").lower()
    # Always: unlock paywall stubs, strip isolation banners / duplicate Picks|Results bars
    if unlock_paywall:
        html = unlock_premium_card_details(html)
    html = strip_sandbox_dev_notes(html)
    html = strip_sport_subnav(html)

    # Live-parity team sports (MLB/Soccer/WNBA): content fixups only — chrome frozen later.
    # Do NOT run hide_empty_books / hide_books_chrome / strip_fake_pl — those
    # strip Books run line / Books total / Edge when values look empty.
    if sport in ("mlb", "soccer", "wnba"):
        if sport == "soccer" and which == "picks":
            html = clamp_soccer_absurd_pl(html)
        if sport == "wnba" and which == "picks":
            html = hide_unavailable_model_boxes(html)
        if sport in ("mlb", "wnba") and which == "picks":
            html = dedupe_game_card_stacks(html)
        if sport == "mlb" and which == "picks":
            # Flip model run-line sides first (display only), then chart attrs + chart JS.
            # MLB picks Cards/Chart UI is signed off — no more MLB UI churn unless broken.
            html = flip_mlb_model_spread_display(html)
            html = enrich_mlb_chart_data_attrs(html)
            html = inject_mlb_run_line_confidence(html)
            html = inject_mlb_picks_chart_tabs(html)
        if sport in ("soccer", "wnba") and which == "picks":
            html = enrich_mlb_chart_data_attrs(html)
            if sport == "soccer":
                html = enrich_soccer_h2h_from_db(html)
            html = inject_picks_chart_tabs(
                html,
                sport=sport,
                markets=["moneyline", "spread", "totals"],
            )
        if sport == "soccer" and which == "picks":
            html = ensure_sport_picks_writeup(html, "soccer")
            html = strip_duplicate_soccer_predictions_heading(html)
            # League pills → results-style dropdown + currently-live note.
            html = replace_soccer_league_pills_with_dropdown(html)
            # Restore Pick Confidence before grid inject (also runs inside inject).
            html = restore_pick_confidence_css(html)
            # Live soccer HTML can leave cards left-stacked; force shared multi-col grid.
            html = inject_soccer_card_grid(html)
        if sport == "soccer" and which == "results":
            # Live cards results still ship league pills — convert to dropdown (no picks-grid CSS).
            html = replace_soccer_league_pills_with_dropdown(html)
            # Cards league list must match Chart (All + audit leagues), not curated pills-only.
            html = align_soccer_cards_league_dropdown(html)
            html = gate_soccer_flat_unit_tracking(html)
            html = label_soccer_results_league_context(html)
            # Season Moneyline Efficiency is blank because overall_stats is built
            # before Efficiency grading — backfill from graded game cards (display only).
            html = fix_soccer_cards_efficiency_season(html)
            # Results must never carry picks FAQ / methodology chrome.
            html = strip_picks_methodology_from_results(html)
            # Keep projected scores + odds cells from mid-word clipping on narrow cards.
            html = fix_soccer_results_projected_text(html)
            # Same H2H backfill as picks when prior meetings exist in sandbox DB.
            html = enrich_soccer_h2h_from_db(html)
        # Do NOT apply picks card-grid CSS on soccer results — it shrinks/breaks chart layout.
        if sport == "mlb" and which == "results":
            html = fix_mlb_results_display(html)
        html = strip_sandbox_dev_notes(html)
        html = strip_sport_subnav(html)
        html = fix_share_social_assets(html)
        html = _inject_consensus_if_results(html, sport, which)
        from shared_chrome import ensure_canonical_chrome

        return ensure_canonical_chrome(html, sport, which=which)

    if sport == "golf":
        try:
            from golf_page import render_golf_with_chrome

            html, _meta = render_golf_with_chrome(html, which=which)
        except Exception as e:
            print(f"[sandbox_fixup] golf chrome inject: {e}", flush=True)
            if which == "picks":
                html = rebuild_golf_picks_table(html)
        if which == "picks":
            html = ensure_sport_picks_writeup(html, "golf")
        html = strip_sandbox_dev_notes(html)
        html = strip_sport_subnav(html)
        html = fix_share_social_assets(html)
        from shared_chrome import ensure_canonical_chrome

        return ensure_canonical_chrome(html, sport, which=which)

    # UFC: isolation cards keep real probs; legacy coin-flip gets odds fill / honesty.
    if sport == "ufc" and which == "picks":
        html = fixup_ufc_picks_honesty(html)
        html = hide_unavailable_model_boxes(html)
    # Tennis: never invent Books N/A; hide missing book chrome instead.
    # CFL uses the MLB card face with Books as — when no line exists.
    if sport != "cfl":
        html = hide_empty_books_slots(html)
    if sport == "tennis":
        html = hide_books_chrome(html)
    elif sport == "ufc":
        html = _hide_books_except_injected(html)

    # Picks Chart: CFL (ML/spread/totals when card data exists); UFC/Tennis ML-only.
    # Golf skipped (not card-based the same way).
    if which == "picks" and sport == "cfl":
        html = enrich_sparse_pick_card_attrs(html)
        html = enrich_mlb_chart_data_attrs(html)
        html = inject_picks_chart_tabs(
            html,
            sport="cfl",
            markets=["moneyline", "spread", "totals"],
            is_premium=True if is_premium is None else bool(is_premium),
        )
        html = ensure_sport_picks_writeup(html, "cfl")
        html = strip_duplicate_cfl_predictions_heading(html)
    if which == "picks" and sport in ("ufc", "tennis"):
        html = inject_picks_chart_tabs(
            html, sport=sport, markets=["moneyline"]
        )
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
        # After ML-only chip scrub, restore MLB lines-strip footprint.
        html = ensure_mlb_lines_strip_on_pick_cards(html)

    if sport == "tennis":
        html = hide_unavailable_model_boxes(html)
        if which == "picks":
            html = ensure_tennis_picks_writeup(html)
            html = apply_tennis_known_player_logos(html)
            # Sidecar tennis ships extra </div>s — repair before grid/chart CSS.
            html = repair_tennis_picks_date_sections(html)
        # Sidecar tennis often left-aligns a single column; force MLB-style card grid.
        html = inject_tennis_card_grid(html)
        if which == "results":
            html = fix_tennis_results_empty_state(html)
        if "tennis-chrome-isolate" not in html:
            html = html.replace(
                "</head>",
                "<style id=\"tennis-chrome-isolate\">"
                "header.pl2-header, header.pl2-header a, header.pl2-header a:hover{"
                "color:inherit}"
                "</style></head>",
                1,
            )

    if sport == "ufc":
        html = scrub_broken_espn_headshots(html)
        if which == "picks":
            html = ensure_mlb_lines_strip_on_pick_cards(html)

    html = strip_sandbox_dev_notes(html)
    html = strip_sport_subnav(html)
    html = fix_share_social_assets(html)
    html = _inject_consensus_if_results(html, sport, which)
    from shared_chrome import ensure_canonical_chrome

    return ensure_canonical_chrome(html, sport, which=which)


_SPORT_WRITEUP_BLURBS: dict[str, tuple[str, str]] = {
    "tennis": (
        "Tennis",
        "player form, surface performance, serve and return metrics, recent match "
        "results, and head-to-head matchups. By evaluating ranking trends, tournament "
        "context, and key performance indicators, our model identifies high-value "
        "opportunities across Tennis moneyline and match outcome predictions.",
    ),
    "soccer": (
        "Soccer",
        "team form, league strength, home and away splits, recent results, and "
        "head-to-head matchups. By evaluating goals trends, schedule context, and key "
        "performance indicators, our model identifies high-value opportunities across "
        "Soccer moneyline, spread, and totals predictions.",
    ),
    "cfl": (
        "CFL",
        "team form, rest and travel factors, recent results, and matchup history. By "
        "evaluating scoring trends, situational context, and key performance indicators, "
        "our model identifies high-value opportunities across CFL moneyline, spread, "
        "and totals predictions.",
    ),
    "golf": (
        "Golf",
        "player form, course history, recent finishes, and field strength. By evaluating "
        "strokes-gained trends, event context, and key performance indicators, our model "
        "identifies high-value opportunities across Golf winner and placement predictions.",
    ),
}


def sport_picks_writeup_html(sport: str) -> str:
    """MLB/UFC-style SEO intro (title + paragraph). No second Predictions H2."""
    sport_l = (sport or "").lower()
    label, body = _SPORT_WRITEUP_BLURBS.get(sport_l, (sport_l.upper(), "matchup context and form."))
    emoji = {"tennis": "🎾", "soccer": "⚽", "cfl": "🏈", "golf": "⛳"}.get(sport_l, "📊")
    heading_h2 = ""
    # Soccer/golf/cfl: AI Picks H1 + intro is the product title. A second
    # "CFL Predictions" H2 duplicates it.
    if sport_l not in ("soccer", "golf", "cfl"):
        heading_h2 = (
            f'<h2 class="sport-predictions-heading" style="color:#0f172a;font-size:1.2rem;'
            f'margin:0 0 12px;">{emoji} {label} Predictions</h2>\n'
        )
    return (
        '<div class="header">'
        f'<h1 id="pageHeading">{emoji} {label} AI Picks, Predictions and Model Probabilities</h1>'
        "</div>\n"
        "<!-- SEO text block -->\n"
        '<div class="sport-picks-writeup" style="margin-bottom:16px;padding:14px 16px;'
        "background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);"
        'border-radius:10px;font-size:0.85em;color:#475569;line-height:1.7;">\n'
        f"Our {label} picks today are generated using a specialized AI prediction system that "
        f"analyzes {body}\n"
        "</div>\n"
        + heading_h2
    )


_SOCCER_DUP_PREDICTIONS_H2 = re.compile(
    r'<h2\b[^>]*\bclass="[^"]*\bsport-predictions-heading\b[^"]*"[^>]*>\s*'
    r"(?:⚽\s*)?Soccer\s+Predictions\s*</h2>\s*",
    flags=re.I,
)

_GOLF_DUP_PREDICTIONS_H2 = re.compile(
    r'<h2\b[^>]*\bclass="[^"]*\bsport-predictions-heading\b[^"]*"[^>]*>\s*'
    r"(?:⛳\s*)?Golf\s+Predictions\s*</h2>\s*",
    flags=re.I,
)

_CFL_DUP_PREDICTIONS_H2 = re.compile(
    r'<h2\b[^>]*\bclass="[^"]*\bsport-predictions-heading\b[^"]*"[^>]*>\s*'
    r"(?:🏈\s*)?CFL\s+Predictions\s*</h2>\s*",
    flags=re.I,
)

_CFL_DUP_PAGE_TITLE_H1 = re.compile(
    r'<h1\b[^>]*\bclass="[^"]*\bpage-title\b[^"]*"[^>]*>\s*'
    r"(?:🏈\s*)?CFL AI Picks and Model Probabilities\s*</h1>\s*",
    flags=re.I,
)

_PL2_HEADER_RE = re.compile(
    r'<header\b[^>]*class="[^"]*pl2-header[^"]*"[^>]*>[\s\S]*?</header>',
    flags=re.I,
)

_WRITEUP_CHUNK_RE = re.compile(
    r'<div class="header">\s*<h1[^>]*>[\s\S]*?</h1>\s*</div>\s*'
    r'(?:<!--\s*SEO text block\s*-->\s*)?'
    r'(?:<div class="sport-picks-writeup"[^>]*>[\s\S]*?</div>\s*)?'
    r'(?:<h2\b[^>]*\bclass="[^"]*\bsport-predictions-heading\b[^"]*"[^>]*>[\s\S]*?</h2>\s*)?',
    flags=re.I,
)


def strip_duplicate_soccer_predictions_heading(html: str) -> str:
    """Remove the extra ⚽ Soccer Predictions H2 under the AI Picks title + intro."""
    if not html:
        return html
    return _SOCCER_DUP_PREDICTIONS_H2.sub("", html, count=1)


def strip_duplicate_golf_predictions_heading(html: str) -> str:
    """Remove the extra ⛳ Golf Predictions H2 under the AI Picks title + intro."""
    if not html:
        return html
    return _GOLF_DUP_PREDICTIONS_H2.sub("", html, count=1)


def strip_duplicate_cfl_predictions_heading(html: str) -> str:
    """Remove extra CFL Predictions H2 and fragment page-title under the SEO H1."""
    if not html:
        return html
    html = _CFL_DUP_PREDICTIONS_H2.sub("", html, count=1)
    return _CFL_DUP_PAGE_TITLE_H1.sub("", html, count=1)


def _insert_writeup_after_site_header(html: str, writeup: str) -> str:
    """Place SEO writeup after pl2-header (never above site chrome)."""
    hm = _PL2_HEADER_RE.search(html)
    if not hm:
        return ""
    after = html[hm.end() :]
    gw = re.match(r'\s*<div class="golf-wrap"[^>]*>', after, flags=re.I)
    if gw:
        pos = hm.end() + gw.end()
        return html[:pos] + writeup + html[pos:]
    return html[: hm.end()] + "\n" + writeup + html[hm.end() :]


def _relocate_writeup_below_pl2_header(html: str) -> str:
    """If the SEO block was injected above pl2-header, move it below."""
    hm = _PL2_HEADER_RE.search(html)
    if not hm:
        return html
    before = html[: hm.start()]
    wm = _WRITEUP_CHUNK_RE.search(before)
    if not wm:
        return html
    chunk = wm.group(0)
    html_wo = before[: wm.start()] + before[wm.end() :] + html[hm.start() :]
    moved = _insert_writeup_after_site_header(html_wo, chunk)
    return moved or html_wo


def ensure_sport_picks_writeup(html: str, sport: str) -> str:
    """Inject MLB-style AI picks write-up when missing (soccer/cfl/golf/tennis).

    Always below pl2-header. Never prepend to <body> (that parks copy above
    the site header — golf layout bug).
    """
    if not html:
        return html
    sport_l = (sport or "").lower()
    if sport_l == "golf":
        html = strip_duplicate_golf_predictions_heading(html)
        html = _relocate_writeup_below_pl2_header(html)
    if sport_l == "soccer":
        html = strip_duplicate_soccer_predictions_heading(html)
    if sport_l == "cfl":
        html = strip_duplicate_cfl_predictions_heading(html)
    if sport_l == "tennis":
        try:
            from tennis_page import tennis_picks_writeup_html

            writeup = tennis_picks_writeup_html()
        except Exception:
            writeup = sport_picks_writeup_html("tennis")
    else:
        writeup = sport_picks_writeup_html(sport_l)
    header_m = _PL2_HEADER_RE.search(html)
    header_end = header_m.end() if header_m else 0
    # Already substantial — but never leave it above the site header.
    if re.search(
        r"Our\s+\w+\s+picks|specialized\s+(?:AI\s+)?(?:prediction\s+)?system",
        html,
        re.I,
    ) and re.search(r"<h1[^>]*>[^<]*(?:AI\s+Picks|Predictions)", html, re.I):
        if "sport-picks-writeup" in html or "SEO text block" in html:
            if header_m:
                h1 = re.search(r'<h1[^>]*id="pageHeading"', html, flags=re.I)
                if h1 and h1.start() >= header_end:
                    return html
                html = _relocate_writeup_below_pl2_header(html)
                return html
            return html
    pat = re.compile(
        r'<div class="header">\s*<h1[^>]*>[\s\S]*?</h1>\s*</div>\s*'
        r'(?:<!--\s*SEO text block\s*-->\s*)?<div\b[^>]*>[\s\S]*?</div>',
        re.I,
    )
    if pat.search(html) and sport_l != "golf":
        html = pat.sub(writeup.rstrip(), html, count=1)
        if header_m:
            html = _relocate_writeup_below_pl2_header(html)
        return html
    if "sport-picks-writeup" in html and re.search(r"<h1[^>]*>[^<]*AI\s+Picks", html, re.I):
        if header_m:
            html = _relocate_writeup_below_pl2_header(html)
        return html
    placed = _insert_writeup_after_site_header(html, writeup)
    if placed:
        return placed
    # Insert near top of main/container — never before an existing pl2-header
    for sel, after_open in (
        (r'(<div class="golf-wrap"\b[^>]*>)', True),
        (r'(<div class="section-tabs"\b)', False),
        (r'(<div class="(?:container|date-nav|golf-board|board-wrap)"\b)', False),
        (r'(<main\b[^>]*>)', False),
    ):
        m = re.search(sel, html, re.I)
        if m and m.start() >= header_end:
            pos = m.end() if after_open else m.start()
            return html[:pos] + writeup + html[pos:]
    # Last resort: after <body> only when there is no site header yet
    if not header_m and re.search(r"<body\b[^>]*>", html, re.I):
        return re.sub(r"(<body\b[^>]*>)", r"\1" + writeup, html, count=1, flags=re.I)
    return html if header_m else writeup + html


def ensure_tennis_picks_writeup(html: str) -> str:
    """Replace thin generic SEO blurb with MLB/UFC-style Tennis AI picks write-up."""
    return ensure_sport_picks_writeup(html, "tennis")


def apply_tennis_known_player_logos(html: str) -> str:
    """Swap placeholder team-logo srcs for ATP/WTA headshots on tennis pick cards."""
    if not html:
        return html
    try:
        from tennis_page import tennis_player_logo, _TENNIS_HEADSHOT_IDS
    except Exception:
        return html

    # Prefetch a small unique-name budget (scoreboard pass usually covers the slate).
    names = []
    for m in re.finditer(
        r'<div class="team-name">\s*([^<]+?)\s*</div>',
        html,
        flags=re.I,
    ):
        n = m.group(1).strip()
        if n and n not in names:
            names.append(n)
    if names:
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import time as _time

            deadline = _time.monotonic() + 3.0
            with ThreadPoolExecutor(max_workers=6) as pool:
                futs = [pool.submit(tennis_player_logo, n) for n in names[:16]]
                for fut in as_completed(futs):
                    if _time.monotonic() > deadline:
                        break
                    try:
                        fut.result(timeout=0.1)
                    except Exception:
                        pass
        except Exception:
            pass

    # Sidecar invents many ESPN athlete ids that 404 — keep only verified ids.
    _known_ids = {str(v) for v in (_TENNIS_HEADSHOT_IDS or {}).values()}

    def _safe_headshot(m: re.Match) -> str:
        aid = m.group(1)
        if aid in _known_ids:
            return m.group(0)
        return "/static/pl-logo.svg"

    html = re.sub(
        r"https://a\.espncdn\.com/i/headshots/tennis/players/full/(\d+)\.png",
        _safe_headshot,
        html,
        flags=re.I,
    )

    def _fix_slot(m: re.Match) -> str:
        block = m.group(0)
        nm = re.search(r'class="[^"]*\bteam-name\b[^"]*"[^>]*>\s*([^<]+)', block, re.I)
        if not nm:
            return block
        name = nm.group(1).strip()
        src = tennis_player_logo(name)
        if not src:
            return block
        # Avoid re-introducing unverified ESPN ids
        mid = re.search(r"/full/(\d+)\.png", src)
        if mid and mid.group(1) not in _known_ids and "espncdn.com" in src:
            src = "/static/pl-logo.svg"
        alt = name.replace('"', "")
        block = re.sub(
            r'(<img\b[^>]*\bteam-logo\b[^>]*\bsrc=")[^"]*(")',
            rf"\1{src}\2",
            block,
            count=1,
            flags=re.I,
        )
        block = re.sub(
            r'(<img\b[^>]*\bteam-logo\b[^>]*\balt=")[^"]*(")',
            rf"\1{alt}\2",
            block,
            count=1,
            flags=re.I,
        )
        return block

    return re.sub(
        r'<div class="team-slot[^"]*">[\s\S]*?<div class="team-name">[\s\S]*?</div>',
        _fix_slot,
        html,
        flags=re.I,
    )


def fix_tennis_results_empty_state(html: str) -> str:
    """Clear empty-state copy when tennis has no graded finals yet."""
    if not html:
        return html
    if re.search(r"Not enough data to calculate performance", html, re.I):
        html = re.sub(
            r"Not enough data to calculate performance for Tennis",
            "No completed games yet",
            html,
            flags=re.I,
        )
    return html


def repair_tennis_picks_date_sections(html: str) -> str:
    """Rebuild date-section > header + games-grid + chart-table-wrap.

    Live tennis sidecar HTML often has extra </div> closers that close the
    date-section early in the browser, orphaning pick stacks and chart wraps
    onto <body>. That breaks Cards|Chart (wrap not found) and equal-height grid.
    """
    if not html or "date-section" not in html:
        return html

    sec_re = re.compile(
        r'<div\b([^>]*\bclass=["\'][^"\']*\bdate-section\b[^"\']*["\'][^>]*)>',
        re.I,
    )
    matches = list(sec_re.finditer(html))
    if not matches:
        return html

    out: list[str] = []
    cursor = 0
    for idx, m in enumerate(matches):
        start = m.start()
        open_tag = m.group(0)
        attrs = m.group(1) or ""
        id_m = re.search(r'\bid=["\']([^"\']+)["\']', attrs, flags=re.I)
        sec_id = id_m.group(1) if id_m else f"date-sec-{idx}"
        date_key = sec_id[5:] if sec_id.startswith("date-") else sec_id
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(html)
        # Prefer cutting before a following main/container close if this is last
        if idx + 1 >= len(matches):
            stop_m = re.search(
                r"</main\b|</div>\s*<footer\b|</body\b",
                html[start:end],
                flags=re.I,
            )
            if stop_m:
                end = start + stop_m.start()
        block = html[start:end]
        out.append(html[cursor:start])

        header_m = re.search(
            r'<div\b[^>]*\bclass=["\'][^"\']*\bdate-header\b[^"\']*["\'][^>]*>[\s\S]*?</div>',
            block,
            flags=re.I,
        )
        header_html = header_m.group(0) if header_m else ""

        stacks: list[str] = []
        for s, e, open_s, inner in _walk_game_card_stacks(block):
            stacks.append(open_s + inner + "</div>")

        wrap_m = re.search(
            r'<div\b[^>]*\bclass=["\'][^"\']*\bchart-table-wrap\b[^"\']*["\'][^>]*>\s*</div>',
            block,
            flags=re.I,
        )
        if wrap_m:
            wrap_html = wrap_m.group(0)
            if f'id="chart-{date_key}"' not in wrap_html and f"id='chart-{date_key}'" not in wrap_html:
                wrap_html = re.sub(
                    r'\bid=["\'][^"\']*["\']',
                    f'id="chart-{date_key}"',
                    wrap_html,
                    count=1,
                    flags=re.I,
                )
                if "id=" not in wrap_html:
                    wrap_html = wrap_html.replace(
                        "<div",
                        f'<div id="chart-{date_key}"',
                        1,
                    )
        else:
            wrap_html = f'<div class="chart-table-wrap" id="chart-{date_key}"></div>'

        rebuilt = (
            f"{open_tag}\n"
            f"  {header_html}\n"
            f'  <div class="games-grid">\n'
            f'    {"".join(stacks)}\n'
            f"  </div>\n"
            f"  {wrap_html}\n"
            f"</div>\n"
        )
        out.append(rebuilt)
        cursor = end
    out.append(html[cursor:])
    return "".join(out)


def inject_tennis_card_grid(html: str) -> str:
    """Force responsive card grid so tennis picks/results aren't a left-aligned stack."""
    if not html:
        return html
    # Always (re)assert multi-column grid after structure repair.
    css = """
<style id="tennis-mlb-grid-fix">
/* Tennis: multi-column grid, content-height cards (no equal-height stretch). */
body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) > .games-grid,
body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) .games-grid {
  display: grid !important;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)) !important;
  gap: 16px !important;
  align-items: start !important;
  justify-content: center !important;
  width: 100% !important;
}
body[data-sandbox-sport="tennis"] .date-section.chart-mode > .games-grid,
body[data-sandbox-sport="tennis"] .date-section.chart-mode .games-grid {
  display: none !important;
}
body[data-sandbox-sport="tennis"] .date-section.chart-mode > .chart-table-wrap,
body[data-sandbox-sport="tennis"] .date-section.chart-mode .chart-table-wrap {
  display: block !important;
}
body[data-sandbox-sport="tennis"] .date-section .games-grid > .game-card-stack,
body[data-sandbox-sport="tennis"] .games-grid > .game-card-stack {
  width: 100% !important;
  max-width: 420px !important;
  min-height: 0 !important;
  height: auto !important;
  display: flex !important;
  flex-direction: column !important;
  justify-self: stretch !important;
  margin: 0 !important;
}
body[data-sandbox-sport="tennis"] .game-card-stack > .game-card,
body[data-sandbox-sport="tennis"] .game-card-stack > .pick-card {
  flex: 0 0 auto !important;
  height: auto !important;
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}
</style>
"""
    html = re.sub(
        r'<style\b[^>]*\bid=["\']tennis-mlb-grid-fix["\'][^>]*>[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    if re.search(r"</head>", html, flags=re.I):
        return re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
    return css + html


def _soccer_league_pill_label(inner: str, *, is_live: bool) -> tuple[str, str]:
    """Return (display_name, option_label) from a league-pill inner HTML.

    Pills use ``Name<span class="league-pill-count">237</span>`` — stripping tags
    alone yields ``Name237``. Always format counts as ``Name (237)``.
    """
    raw = inner or ""
    count_m = re.search(
        r'<span[^>]*\bleague-pill-count\b[^>]*>(.*?)</span>',
        raw,
        flags=re.I | re.S,
    )
    count_txt = ""
    if count_m:
        count_txt = re.sub(r"<[^>]+>", "", count_m.group(1) or "")
        count_txt = re.sub(r"\s+", " ", count_txt).strip()
    wo_count = re.sub(
        r'<span[^>]*\bleague-pill-count\b[^>]*>.*?</span>',
        " ",
        raw,
        flags=re.I | re.S,
    )
    # Drop live-dot / decorative spans before text extract.
    wo_count = re.sub(
        r'<span[^>]*\blive-dot\b[^>]*>.*?</span>',
        " ",
        wo_count,
        flags=re.I | re.S,
    )
    name = re.sub(r"<[^>]+>", " ", wo_count)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "", ""
    if not count_txt:
        glued = re.match(r"^(.*?)(\d{1,4})$", name)
        if glued and glued.group(1).strip() and not glued.group(1).strip()[-1:].isdigit():
            name = glued.group(1).strip()
            count_txt = glued.group(2)
    label = f"{name} ({count_txt})" if count_txt else name
    if is_live and name.lower() not in ("all leagues", "all"):
        label = f"{label} · Live"
    return name, label


def gate_soccer_flat_unit_tracking(html: str, *, min_n: int = 40) -> str:
    """Hide deceptive soccer Flat Unit ROI when the graded sample is tiny.

    Causes we paper over (sandbox display only):
    - Incomplete FIFA World Cup ingest (~30 of ~104 matches in local audit DB)
    - Moneyline unit math that can show 1-18 / -89% on that tiny slate
    - Any market window with decided W+L below ``min_n``

    Product rule: show "Insufficient sample" instead of flashing garbage W-L/ROI.
    """
    if not html or "Flat Unit Tracking" not in html:
        return html

    selected_wc = bool(
        re.search(r"Season Performance[^<]*—\s*FIFA World Cup", html, flags=re.I)
        or re.search(
            r'<option[^>]*selected[^>]*>\s*FIFA World Cup\b',
            html,
            flags=re.I,
        )
        or re.search(
            r'rel="canonical"[^>]+league=fifa-world-cup',
            html,
            flags=re.I,
        )
    )
    wc_n = None
    m_wc = re.search(
        r'<option[^>]*selected[^>]*>\s*FIFA World Cup\s*\(\s*([0-9]{1,3})\s*\)',
        html,
        flags=re.I,
    )
    if m_wc:
        try:
            wc_n = int(m_wc.group(1))
        except Exception:
            wc_n = None
    # Expanded WC ≈ 104 matches; local audit often has ~30 — hide the whole unit strip.
    force_gate_all = bool(selected_wc and (wc_n is None or wc_n < 80))

    insuff = (
        '<div><div style="opacity:0.8;">{label}</div>'
        '<div style="font-weight:700;color:#64748b;">—</div>'
        '<div style="opacity:0.85;font-size:0.9em;">Insufficient sample</div></div>'
    )

    def _rewrite_unit_cells(block: str, *, force: bool) -> str:
        def _cell(m: re.Match) -> str:
            label = m.group(1)
            detail = m.group(3)
            wm = re.match(r"^\s*(\d+)-(\d+)-(\d+)", detail or "")
            if not wm:
                if force and "Insufficient sample" not in (detail or ""):
                    return insuff.format(label=label)
                return m.group(0)
            n = int(wm.group(1)) + int(wm.group(2))
            if force or n < min_n:
                return insuff.format(label=label)
            return m.group(0)

        return re.sub(
            r"<div>\s*<div style=\"opacity:0\.8;\">(Season|7 Days|Daily)</div>\s*"
            r"<div style=\"font-weight:700;color:[^\"]+;\">([^<]*)</div>\s*"
            r"<div style=\"opacity:0\.85;font-size:0\.9em;\">([^<]*)</div>\s*</div>",
            _cell,
            block,
            flags=re.I,
        )

    def _gate_moneyline_column(block: str) -> str:
        """Always hide soccer moneyline unit cells — tracker is 2-way, soccer is 3-way."""
        ml = re.search(r">Moneyline</div>", block, flags=re.I)
        if not ml:
            return block
        # Slice from Moneyline header through the start of the next market card.
        rest = block[ml.end() :]
        nxt = re.search(r">(?:Spread|Total|O/U)</div>", rest, flags=re.I)
        end = ml.end() + (nxt.start() if nxt else len(rest))
        ml_chunk = block[ml.start() : end]
        fixed = _rewrite_unit_cells(ml_chunk, force=True)
        return block[: ml.start()] + fixed + block[end:]

    # Flat Unit section only (do not touch Season Performance accuracy cards).
    start = html.lower().find("flat unit tracking")
    if start < 0:
        return html
    # Walk back to the surrounding <h2> if present.
    h2 = html.rfind("<h2", max(0, start - 200), start)
    if h2 >= 0:
        start = h2
    end = html.lower().find("season performance", start)
    if end < 0:
        end = min(len(html), start + 6000)
    # Include the Season Performance h2 marker boundary only as cut point.
    chunk = html[start:end]

    if force_gate_all:
        notice = (
            '<h2 style="text-align:center;margin:0 0 4px 0;font-size:1.3em;color:#0f172a;">'
            "Model Performance (Flat Unit Tracking)</h2>"
            '<p style="text-align:center;margin:0 0 14px;font-size:0.9em;color:#64748b;">'
            "Insufficient sample for unit tracking on this league.</p>"
        )
        return html[:start] + notice + html[end:]

    # Moneyline: always insufficient (broken 2-way soccer units).
    # Spread / Total: keep only when decided n >= min_n.
    chunk = _gate_moneyline_column(chunk)
    chunk = _rewrite_unit_cells(chunk, force=False)
    return html[:start] + chunk + html[end:]


def _soccer_chart_leagues() -> list[dict[str, Any]]:
    """Same league catalog Chart uses (Personal/soccer audit ``_leagues()``)."""
    try:
        from pathlib import Path
        import sys

        iso = Path.home() / "Documents/Personal/soccer"
        iso_s = str(iso.resolve())
        if iso.is_dir() and iso_s not in sys.path:
            sys.path.insert(0, iso_s)
        import app_audit_legacy as mod  # type: ignore

        rows = mod._leagues() or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if isinstance(r, dict):
                name = (r.get("name") or "").strip()
                if not name:
                    continue
                out.append({"name": name, "n": r.get("n")})
            elif isinstance(r, str) and r.strip():
                out.append({"name": r.strip(), "n": None})
        return out
    except Exception as e:
        print(f"[sandbox_fixup] soccer chart leagues: {e}", flush=True)
        return []


def _soccer_league_slug_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def align_soccer_cards_league_dropdown(html: str) -> str:
    """Rebuild Cards ``#league`` options to match Chart (All + audit league list).

    Live results pills omit All Leagues and only list curated ``SOCCER_LEAGUE_ORDER``
    rows with counts — Chart uses audit ``_leagues()`` (full DB). Display-only.
    """
    if not html or not re.search(r'<select[^>]*\bid=["\']league["\']', html, flags=re.I):
        return html

    sel_m = re.search(
        r'(<select[^>]*\bid=["\']league["\'][^>]*>)([\s\S]*?)(</select>)',
        html,
        flags=re.I,
    )
    if not sel_m:
        return html

    open_tag, body, close_tag = sel_m.group(1), sel_m.group(2), sel_m.group(3)
    live_names: set[str] = set()
    selected_slug = ""
    selected_name = ""
    for om in re.finditer(r"<option\b([^>]*)>(.*?)</option>", body, flags=re.I | re.S):
        attrs, inner = om.group(1) or "", om.group(2) or ""
        label = re.sub(r"<[^>]+>", "", inner)
        label = re.sub(r"\s+", " ", label).strip()
        name = re.sub(r"\s*\(\d+\)\s*$", "", label)
        name = re.sub(r"\s*·\s*Live\s*$", "", name, flags=re.I).strip()
        val_m = re.search(r'\bvalue=["\']([^"\']*)["\']', attrs, flags=re.I)
        val = (val_m.group(1) if val_m else "").strip()
        is_live = (
            re.search(r'\bdata-live=["\']1["\']', attrs, flags=re.I) is not None
            or "· Live" in label
        )
        if is_live and name.lower() not in ("all leagues", "all"):
            live_names.add(name)
        if re.search(r"\bselected\b", attrs, flags=re.I):
            selected_slug = val
            selected_name = name

    ctx = _soccer_selected_league_name(html)
    if ctx:
        selected_name = ctx
        if not selected_slug or selected_slug.upper() in ("", "ALL"):
            selected_slug = _soccer_league_slug_name(ctx)

    selected_is_all = (selected_slug or "").upper() in ("", "ALL") and (
        not selected_name or selected_name.lower() in ("all leagues", "all")
    )
    if selected_name and selected_name.lower() not in ("all leagues", "all"):
        selected_is_all = False

    leagues = _soccer_chart_leagues()
    # Fallback: keep prior option names (still inject All) if audit DB unavailable.
    if not leagues:
        names: list[str] = []
        for om in re.finditer(r"<option\b([^>]*)>(.*?)</option>", body, flags=re.I | re.S):
            label = re.sub(r"<[^>]+>", "", om.group(2) or "")
            label = re.sub(r"\s+", " ", label).strip()
            name = re.sub(r"\s*\(\d+\)\s*$", "", label)
            name = re.sub(r"\s*·\s*Live\s*$", "", name, flags=re.I).strip()
            if name and name.lower() not in ("all leagues", "all"):
                names.append(name)
        leagues = [{"name": n, "n": None} for n in names]

    # Live leagues first (Chart populateLeagues order).
    if live_names:
        live_first = [L for L in leagues if L["name"] in live_names]
        rest = [L for L in leagues if L["name"] not in live_names]
        leagues = live_first + rest

    options: list[str] = [
        (
            f'<option value="ALL" data-href="/soccer/results?league=ALL" data-live="0"'
            f'{" selected" if selected_is_all else ""}>All Leagues</option>'
        )
    ]
    seen: set[str] = set()
    for L in leagues:
        name = str(L.get("name") or "").strip()
        # Exact-name dedupe only — Chart keeps DB spelling variants
        # (e.g. "Spanish LaLiga" vs "Spanish LALIGA").
        if not name or name in seen or name.lower() in ("all leagues", "all"):
            continue
        seen.add(name)
        slug = _soccer_league_slug_name(name)
        n = L.get("n")
        is_live = name in live_names
        sel = ""
        if not selected_is_all:
            if selected_slug and selected_slug == slug:
                sel = " selected"
            elif selected_name and selected_name == name:
                sel = " selected"
        label = f"{name} ({n})" if n not in (None, "") else name
        if is_live:
            label = f"{label} · Live"
        href = f"/soccer/results?league={slug}"
        options.append(
            f'<option value="{_html_attr(slug)}" data-href="{_html_attr(href)}" '
            f'data-live="{1 if is_live else 0}"{sel}>{_html_text(label)}</option>'
        )

    # If a selected league somehow missing from catalog, keep it selectable.
    if (
        not selected_is_all
        and selected_name
        and selected_name not in seen
        and selected_name.lower() not in ("all leagues", "all")
    ):
        slug = selected_slug or _soccer_league_slug_name(selected_name)
        href = f"/soccer/results?league={slug}" if slug else "/soccer/results"
        options.insert(
            1,
            f'<option value="{_html_attr(slug)}" data-href="{_html_attr(href)}" '
            f'data-live="0" selected>{_html_text(selected_name)}</option>',
        )

    new_inner = "\n      " + "\n      ".join(options) + "\n    "
    new_sel = open_tag + new_inner + close_tag
    html = html[: sel_m.start()] + new_sel + html[sel_m.end() :]

    # Refresh "Currently live" note from preserved live names when present.
    if live_names and 'id="soccer-live-leagues"' in html:
        shown = sorted(live_names)[:12]
        more = len(live_names) - len(shown)
        live_txt = ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")
        html = re.sub(
            r'(<p class="soccer-live-leagues"[^>]*id="soccer-live-leagues"[^>]*>)'
            r"[\s\S]*?(</p>)",
            lambda m: (
                f'{m.group(1)}<strong>Currently live:</strong> {_html_text(live_txt)}{m.group(2)}'
            ),
            html,
            count=1,
            flags=re.I,
        )
    return html


def fix_soccer_cards_efficiency_season(html: str) -> str:
    """Fill blank Season Moneyline Efficiency cells from graded Efficiency game boxes.

    Root cause (live SSR): ``overall_stats`` is computed before Efficiency ML grading
    runs in ``_finalize_daily_result_cards``, so Season shows ``—`` while Last Night /
    Last 7 (built after grading) populate. Display/mapping fix only — no model changes.
    """
    if not html or "Moneyline Accuracy by Model" not in html:
        return html
    try:
        from team_tabbed_results import patch_mlb_season_efficiency_html

        return patch_mlb_season_efficiency_html(html)
    except Exception as e:
        print(f"[sandbox_fixup] soccer efficiency season: {e}", flush=True)
        return html


def replace_soccer_league_pills_with_dropdown(html: str) -> str:
    """Replace soccer league-pill slider with a results-style <select> + live note.

    Uses a balanced </div> walk — the slider contains nested ``.league-badges`` divs;
    a non-greedy regex left a stray ``</div>`` that closed ``.container`` early and
    created a huge empty gap (research-theme min-height: 58vh on the orphaned box).
    """
    if not html or ("league-slider" not in html and "league-pill" not in html):
        return html

    pills = list(
        re.finditer(
            r'<a\b([^>]*\bclass="[^"]*\bleague-pill\b[^"]*"[^>]*)>(.*?)</a>',
            html,
            flags=re.I | re.S,
        )
    )
    if not pills:
        return html

    options = []
    live_names = []
    for m in pills:
        attrs, inner = m.group(1), m.group(2)
        href_m = re.search(r'href="([^"]*)"', attrs, flags=re.I)
        href = href_m.group(1) if href_m else "/soccer/"
        is_live = bool(
            re.search(r"\blive-league\b", attrs, flags=re.I)
            or re.search(r'class="[^"]*\blive-dot\b', inner or "", flags=re.I)
            or 'class="live-dot"' in (inner or "")
            or "live-dot" in (inner or "")
        )
        name, label = _soccer_league_pill_label(inner, is_live=is_live)
        if not name:
            continue
        is_active = bool(re.search(r"\bactive\b", attrs, flags=re.I))
        slug_m = re.search(r"[?&]league=([^&\"'#]+)", href)
        slug = slug_m.group(1) if slug_m else ""
        if name.lower() in ("all leagues", "all"):
            slug = ""
        if is_live and name.lower() not in ("all leagues", "all"):
            live_names.append(name)
        sel = " selected" if is_active else ""
        live_flag = "1" if is_live else "0"
        # Results pills point at /soccer-results — keep sandbox paths in sync.
        href = re.sub(r"/soccer-results\b", "/soccer/results", href)
        href = re.sub(r"/soccer-picks\b", "/soccer/", href)
        options.append(
            f'<option value="{_html_attr(slug)}" data-href="{_html_attr(href)}" '
            f'data-live="{live_flag}"{sel}>{_html_text(label)}</option>'
        )

    if not options:
        return html

    live_line = ""
    if live_names:
        # Cap list for readability; full list remains in the dropdown (· Live).
        shown = live_names[:12]
        more = len(live_names) - len(shown)
        live_txt = ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")
        live_line = (
            f'<p class="soccer-live-leagues" id="soccer-live-leagues">'
            f"<strong>Currently live:</strong> {_html_text(live_txt)}</p>"
        )
    # If builder didn't mark live pills (common on results HTML), omit the note —
    # chart/CSR pages fill #soccer-live-leagues from upcoming via JS instead.

    controls = f"""
<section class="controls soccer-league-controls" id="league-controls" aria-label="League filter">
  <label>
    League
    <select id="league" name="league" aria-label="Select league">
      {"".join(options)}
    </select>
  </label>
  {live_line}
</section>
<style id="soccer-league-dropdown-css">
.soccer-league-controls{{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 18px;
  max-width:1100px;margin:8px auto 14px;padding:0 16px;}}
.soccer-league-controls label{{display:flex;flex-direction:column;gap:6px;
  font-size:0.78rem;font-weight:700;color:#475569;letter-spacing:0.02em;}}
.soccer-league-controls select{{min-width:min(100%,320px);max-width:420px;padding:8px 12px;
  border:1px solid #cbd5e1;border-radius:8px;background:#fff;color:#0f172a;
  font-size:0.95rem;font-weight:600;}}
.soccer-live-leagues{{margin:0;flex:1 1 220px;font-size:0.86rem;line-height:1.35;color:#0f172a;}}
.soccer-live-leagues strong{{color:#059669;}}
.soccer-live-leagues.muted{{color:#64748b;}}
/* Hide legacy pill slider when dropdown is present */
.league-slider{{display:none!important;}}
/* Avoid empty 58vh box if markup ever orphans content outside .container */
body.research-site[data-sandbox-sport="soccer"] > .container{{min-height:0;}}
</style>
<script id="soccer-league-dropdown-js">
(function(){{
  var sel=document.getElementById('league');
  if(!sel||sel.dataset.soccerDropdownBound==='1') return;
  sel.dataset.soccerDropdownBound='1';
  sel.addEventListener('change', function(){{
    var opt=sel.options[sel.selectedIndex];
    var href=(opt && opt.getAttribute('data-href')) || '/soccer/';
    if(href) window.location.href=href;
  }});
}})();
</script>
"""

    # Balanced replace — nested .league-badges must not truncate the slider early.
    replaced = False
    if "league-slider" in html:
        html, replaced = _replace_balanced_div(
            html,
            r'<div class="league-slider\b[^"]*"[^>]*>',
            controls,
        )
    if not replaced:
        # Fallback: hide pills via CSS already in controls; prepend controls near picks heading.
        html2, n = re.subn(
            r'(<h2 class="sport-predictions-heading"[^>]*>[\s\S]*?</h2>)',
            r"\1" + controls,
            html,
            count=1,
            flags=re.I,
        )
        if n:
            html = html2
            replaced = True
        else:
            html2, n = re.subn(
                r'(<div class="sport-picks-writeup"[^>]*>[\s\S]*?</div>)',
                r"\1" + controls,
                html,
                count=1,
                flags=re.I,
            )
            if n:
                html = html2
                replaced = True
        if not replaced:
            # Results pages: insert after section-tabs / page-title.
            html2, n = re.subn(
                r'(<div class="section-tabs"[\s\S]*?</div>)',
                r"\1" + controls,
                html,
                count=1,
                flags=re.I,
            )
            if n:
                html = html2
                replaced = True
    # Strip any leftover slider if a prior broken replace left fragments.
    if 'id="league-controls"' in html and "league-slider" in html:
        html, _ = _replace_balanced_div(
            html,
            r'<div class="league-slider\b[^"]*"[^>]*>',
            "",
        )
    return html


def _html_attr(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _html_text(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _soccer_selected_league_name(html: str) -> str:
    """Best-effort selected league label from dropdown / season header."""
    if not html:
        return ""
    m = re.search(
        r'<select[^>]*\bid="league"[^>]*>[\s\S]*?'
        r'<option[^>]*\bselected\b[^>]*>(.*?)</option>',
        html,
        flags=re.I,
    )
    if m:
        name = re.sub(r"<[^>]+>", "", m.group(1) or "")
        name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
        name = re.sub(r"\s*·\s*Live\s*$", "", name, flags=re.I).strip()
        if name and name.lower() not in ("all leagues", "all", "soccer"):
            return name
    m = re.search(
        r"Season Performance\s*—\s*([^<]+)",
        html,
        flags=re.I,
    )
    if m:
        name = (m.group(1) or "").strip()
        if name and name.lower() not in ("all leagues", "all", "soccer"):
            return name
    return ""


def strip_picks_methodology_from_results(html: str) -> str:
    """Remove picks-only FAQ blocks if they leaked onto results HTML.

    Results must not show “How These AI Picks Are Generated” / pick-education
    chrome — that belongs on /soccer/ only.
    """
    if not html:
        return html
    if "How These AI Picks Are Generated" not in html and "What to Expect From These Picks" not in html:
        return html
    # Drop the methodology section through the next major footer/directory marker.
    html = re.sub(
        r"<h2\b[^>]*>\s*How These AI Picks Are Generated\s*</h2>"
        r"[\s\S]*?(?="
        r'<footer\b|class="site-directory-footer"|class="directory-shell"|'
        r'id="social-export"|</main\s*>|</body\s*>)',
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"<h2\b[^>]*>\s*What to Expect From These Picks\s*</h2>"
        r"[\s\S]*?(?=<h2\b|<footer\b|class=\"site-directory-footer\"|</main\s*>)",
        "",
        html,
        count=1,
        flags=re.I,
    )
    return html


def fix_soccer_results_projected_text(html: str) -> str:
    """Keep XSharp / PL projected scores readable on narrow results cards.

    Live results cards use overflow:hidden + tight columns; long scorelines
    (Houston Dynamo FC 1 – New England Revolution 1.5) were clipping mid-name.
    """
    if not html or "proj-val" not in html:
        return html
    css = """
<style id="soccer-results-proj-fix">
/* Results only: never clip projected scorelines or mid-break long team names. */
html body[data-sandbox-sport="soccer"] .proj-row,
body.research-site .proj-row {
  display: flex !important;
  flex-wrap: wrap !important;
  align-items: flex-start !important;
  gap: 4px 10px !important;
}
html body[data-sandbox-sport="soccer"] .proj-model,
body.research-site .proj-model {
  flex: 0 0 auto !important;
}
html body[data-sandbox-sport="soccer"] .proj-val,
body.research-site .proj-val {
  flex: 1 1 12rem !important;
  min-width: 0 !important;
  max-width: 100% !important;
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  overflow-wrap: break-word !important;
  word-break: normal !important;
  hyphens: manual !important;
  text-align: left !important;
}
html body[data-sandbox-sport="soccer"] .odds-pricing-table td,
html body[data-sandbox-sport="soccer"] .odds-pricing-table .val-books,
html body[data-sandbox-sport="soccer"] .odds-pricing-table .val-pl,
html body[data-sandbox-sport="soccer"] .odds-pricing-table .val-xs,
body.research-site .odds-pricing-table td {
  white-space: normal !important;
  overflow-wrap: break-word !important;
  word-break: normal !important;
  hyphens: manual !important;
}
html body[data-sandbox-sport="soccer"] .games-card .proj-score-box,
body.research-site .game-card .proj-score-box {
  overflow: visible !important;
}
html body[data-sandbox-sport="soccer"] .games-grid,
html body[data-sandbox-sport="soccer"] .results-grid {
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)) !important;
}
</style>
"""
    html = re.sub(
        r'<style\b[^>]*\bid=["\']soccer-results-proj-fix["\'][^>]*>[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", css + "</body>", html, count=1, flags=re.I)
    if re.search(r"</head>", html, flags=re.I):
        return re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
    return html + css


def label_soccer_results_league_context(html: str, *, stale_days: int = 14) -> str:
    """Name the selected league on Last Night / Last 7 / banner; hide months-old 'last night'.

    Live templates hardcode sport_info.name ("Soccer") in window headers while the
    Season block already says the league — easy to misread with many leagues.
    When the Last Night date is older than ``stale_days``, replace the recent
    windows with an honest empty state instead of a March slate in August.
    """
    if not html or "Last Night" not in html:
        return html

    league = _soccer_selected_league_name(html)
    if not league:
        return html

    league_esc = _html_text(league)

    # Sticky context under the league control (once).
    if 'id="soccer-results-league-context"' not in html:
        banner = (
            f'<p class="soccer-results-league-context" id="soccer-results-league-context" '
            f'style="margin:0 0 10px;padding:0 16px;max-width:1100px;margin-left:auto;'
            f'margin-right:auto;font-size:0.92rem;font-weight:700;color:#0f172a;">'
            f"These results are for: {league_esc}</p>"
        )
        html2, n = re.subn(
            r'(id="league-controls"[^>]*>[\s\S]*?</(?:section|div)>)',
            r"\1" + banner,
            html,
            count=1,
            flags=re.I,
        )
        if n:
            html = html2
        else:
            html2, n = re.subn(
                r'(</select>\s*</label>)',
                r"\1" + banner,
                html,
                count=1,
                flags=re.I,
            )
            if n:
                html = html2

    # Rewrite window titles: "Soccer" → selected league name.
    html = re.sub(
        r"Last Night's Soccer Results",
        f"Last Night's {league_esc} Results",
        html,
    )
    html = re.sub(
        r"Last 7 Days Soccer Results",
        f"Last 7 Days {league_esc} Results",
        html,
    )
    # Share button aria / JS seed strings that still say Soccer for this page.
    html = re.sub(
        r'(aria-label="Share Last Night\'s )Soccer( Results")',
        rf"\1{league_esc}\2",
        html,
    )

    # Stale "Last Night" gate — do not present months-old finals as last night.
    m_date = re.search(
        rf"Last Night's {re.escape(league_esc)} Results\s*—\s*(\d{{4}}-\d{{2}}-\d{{2}})",
        html,
    )
    if not m_date:
        m_date = re.search(
            r"Last Night's [^<]*?Results\s*—\s*(\d{4}-\d{2}-\d{2})",
            html,
        )
    if m_date:
        from datetime import datetime, timedelta

        try:
            night = datetime.strptime(m_date.group(1), "%Y-%m-%d").date()
            today = datetime.now().date()
            stale = night < (today - timedelta(days=max(1, int(stale_days))))
        except Exception:
            stale = False
        if stale:
            notice = (
                f'<div class="daily-tally" style="padding:18px 16px;margin:12px auto 18px;'
                f'max-width:1100px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;">'
                f'<h2 style="margin:0 0 8px;font-size:1.15rem;color:#0f172a;">'
                f"No recent finals for {league_esc}</h2>"
                f'<p style="margin:0;color:#475569;font-size:0.95rem;line-height:1.45;">'
                f"Last Night and Last 7 Days stay empty when this league has no graded finals "
                f"in the last {int(stale_days)} days "
                f"(most recent stored slate: {m_date.group(1)}). "
                f"Season Performance below still covers the full {league_esc} sample."
                f"</p></div>"
            )
            # Drop Daily Tally + Last 7 Days blocks; keep Flat Unit / Season.
            html2, n = re.subn(
                r"<!--\s*──\s*Daily Tally\s*──\s*-->[\s\S]*?"
                r"(?=(?:<!--\s*──\s*(?:Combined Stats Banner|Season)|"
                r"<h2[^>]*>\s*(?:🏆\s*)?Season Performance|"
                r"<h2[^>]*>[^<]*Flat Unit))",
                notice,
                html,
                count=1,
                flags=re.I,
            )
            if n:
                html = html2
            else:
                # Fallback: rewrite headings only already done; prepend notice once.
                if "No recent finals for" not in html:
                    html = html.replace(
                        "<!-- ── Daily Tally ── -->",
                        "<!-- ── Daily Tally ── -->" + notice,
                        1,
                    )

    # Flat unit strip — after stale-window surgery so the heading stays league-named.
    needle = "Model Performance (Flat Unit Tracking)"
    labeled = f"Model Performance (Flat Unit Tracking) — {league_esc}"
    if labeled not in html and needle in html:
        html = html.replace(needle, labeled, 1)

    return html


def restore_pick_confidence_css(html: str) -> str:
    """Rewrite live-embedded Pick Confidence CSS that letter-shreds long names.

    Source HTML ships overflow-wrap:anywhere + fixed grid-template-rows on .pc-box.
    Replace those rules in-place so names wrap at words / ellipsize cleanly.
    """
    if not html or "pick-conf" not in html:
        return html
    # Kill the shredder property everywhere in the document CSS/markup attrs.
    html = re.sub(
        r"overflow-wrap\s*:\s*anywhere\s*;?",
        "overflow-wrap:normal;",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"word-break\s*:\s*break-word\s*;?",
        "word-break:normal;",
        html,
        flags=re.I,
    )
    # Fixed 3-row grid was crushing / overflowing into EV footer.
    html = re.sub(
        r"\.pc-box\s*\{([^}]*)\}",
        lambda m: (
            ".pc-box{"
            + re.sub(
                r"display\s*:\s*grid\s*;?",
                "display:flex;flex-direction:column;",
                m.group(1),
                flags=re.I,
            )
            + "}"
        ),
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"grid-template-rows\s*:\s*30px\s+26px\s+32px\s*;?",
        "grid-template-rows:none;",
        html,
        flags=re.I,
    )
    return html


def _strip_soccer_in_grid_league_headers(html: str) -> str:
    """Drop full-width league labels that sit inside ``.games-grid``.

    Live soccer HTML inserts ``grid-column:1/-1`` headings between cards.
    Those restart the CSS grid, so a 1-game league becomes a ragged single-card
    row instead of filling the next column. League stays on the card badge
    (see ``_relabel_soccer_league_badges``) and the league dropdown.
    """
    if not html:
        return html
    return re.sub(
        r'<div style="grid-column:1/-1;font-size:0\.88em;font-weight:700;color:#92400e;[^"]*">[^<]*</div>\s*',
        "",
        html,
        flags=re.I,
    )


def _relabel_soccer_league_badges(html: str) -> str:
    """Replace generic ``⚽ Soccer`` badges with the card's ``data-league`` name."""
    if not html or "league-badge" not in html:
        return html
    parts = re.split(r'(?=<div class="game-card-stack\b)', html)
    if len(parts) <= 1:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        lm = re.search(r'\bdata-league="([^"]*)"', part)
        league = (lm.group(1) if lm else "").strip()
        if league:
            part, _n = re.subn(
                r'(<span class="league-badge">)(?:⚽\s*)?Soccer(</span>)',
                rf"\1{league}\2",
                part,
                count=1,
                flags=re.I,
            )
        out.append(part)
    return "".join(out)


def inject_soccer_card_grid(html: str) -> str:
    """Multi-column soccer picks grid + restore Pick Confidence (no letter-stack).

    Auto-fill columns (320px min → 2 or 3 cols at hub width). Cards are
    content-height — do not stretch collapsed cards to a neighbor's height.
    Pick Confidence stays 2×3 so all six models remain on-screen.
    End-of-body CSS beats live-embedded overflow-wrap:anywhere that shreds names.
    """
    if not html:
        return html
    # Picks only — never shrink results/chart pages.
    if 'data-ssr-chart="1"' in html or 'id="ssr-finals"' in html:
        return html
    if "team-results.js" in html and "game-card-stack" not in html:
        return html
    html = _strip_soccer_in_grid_league_headers(html)
    html = _relabel_soccer_league_badges(html)
    html = restore_pick_confidence_css(html)
    css = """
<style id="soccer-mlb-grid-fix">
/* Soccer picks: multi-column grid, content-height (no equal-height empty gap). */
html body.research-site[data-sandbox-sport="soccer"] .date-section:not(.chart-mode) > .games-grid,
html body.research-site[data-sandbox-sport="soccer"] .date-section:not(.chart-mode) .games-grid,
html body[data-sandbox-sport="soccer"] .date-section:not(.chart-mode) .games-grid,
html body[data-sandbox-sport="soccer"] .games-grid {
  display: grid !important;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)) !important;
  gap: 16px !important;
  align-items: start !important;
  justify-content: center !important;
  justify-items: stretch !important;
  width: 100% !important;
  max-width: 1100px !important;
  margin-left: auto !important;
  margin-right: auto !important;
}
html body[data-sandbox-sport="soccer"] .date-section.chart-mode .games-grid {
  display: none !important;
}
/* In-grid league labels restart rows — keep them out of the card grid. */
html body[data-sandbox-sport="soccer"] .games-grid > div[style*="grid-column:1/-1"],
html body[data-sandbox-sport="soccer"] .games-grid > .slate-league-heading {
  display: none !important;
}
html body[data-sandbox-sport="soccer"] .date-section .games-grid > .game-card-stack,
html body[data-sandbox-sport="soccer"] .games-grid > .game-card-stack {
  width: 100% !important;
  max-width: none !important;
  min-width: 0 !important;
  min-height: 0 !important;
  height: auto !important;
  display: flex !important;
  flex-direction: column !important;
  justify-self: stretch !important;
  margin: 0 !important;
}
html body[data-sandbox-sport="soccer"] .game-card-stack > .game-card,
html body[data-sandbox-sport="soccer"] .game-card-stack > .pick-card {
  flex: 0 0 auto !important;
  height: auto !important;
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}
html body[data-sandbox-sport="soccer"] .league-badge {
  max-width: 72%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* Pick Confidence: always 2×3 on soccer cards (~420–520px).
   Prior min-width:762px + 6×120px cols clipped Efficiency + Sharp Consensus
   off-screen with a non-obvious horizontal scroll. */
html body[data-sandbox-sport="soccer"] .pick-conf-bar {
  overflow-x: visible !important;
  overflow-y: visible !important;
  max-width: 100% !important;
}
html body[data-sandbox-sport="soccer"] .pick-conf-grid {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
  gap: 6px !important;
  align-items: stretch !important;
  min-width: 0 !important;
  width: 100% !important;
  max-width: 100% !important;
}
html body[data-sandbox-sport="soccer"] .pc-box {
  display: flex !important;
  flex-direction: column !important;
  justify-content: space-between !important;
  align-items: center !important;
  gap: 3px !important;
  grid-template-rows: none !important;
  min-width: 0 !important;
  width: 100% !important;
  min-height: 88px !important;
  height: auto !important;
  overflow: hidden !important;
  box-sizing: border-box !important;
}
html body[data-sandbox-sport="soccer"] .pc-name {
  display: block !important;
  white-space: nowrap !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  word-break: normal !important;
  overflow-wrap: normal !important;
  hyphens: none !important;
  max-width: 100% !important;
  width: 100% !important;
  text-align: center !important;
  font-size: 0.62em !important;
  letter-spacing: 0.15px !important;
  line-height: 1.15 !important;
}
html body[data-sandbox-sport="soccer"] .pc-side {
  display: -webkit-box !important;
  -webkit-box-orient: vertical !important;
  -webkit-line-clamp: 2 !important;
  line-clamp: 2 !important;
  white-space: normal !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  word-break: normal !important;
  overflow-wrap: normal !important;
  hyphens: none !important;
  max-width: 100% !important;
  width: 100% !important;
  text-align: center !important;
  font-size: 0.56em !important;
  letter-spacing: 0.05px !important;
  padding: 3px 4px !important;
  line-height: 1.2 !important;
  min-height: 28px !important;
  max-height: 2.5em !important;
  box-sizing: border-box !important;
}
html body[data-sandbox-sport="soccer"] .pc-val {
  flex: 0 0 auto !important;
  min-height: 24px !important;
}
@media (max-width: 640px) {
  html body[data-sandbox-sport="soccer"] .date-section:not(.chart-mode) .games-grid,
  html body[data-sandbox-sport="soccer"] .games-grid {
    grid-template-columns: 1fr !important;
  }
  html body[data-sandbox-sport="soccer"] .games-grid > .game-card-stack {
    max-width: 100% !important;
    min-height: 0 !important;
  }
}
</style>
"""
    html = re.sub(
        r'<style\b[^>]*\bid=["\']soccer-mlb-grid-fix["\'][^>]*>[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    # Inject at end of body so it wins over sports-chrome / mlb-pick-cards link order.
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", css + "</body>", html, count=1, flags=re.I)
    if re.search(r"</head>", html, flags=re.I):
        return re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
    return html + css


def ensure_mlb_lines_strip_on_pick_cards(html: str) -> str:
    """MLB cards always have a lines-strip under the matchup row.

    UFC/Tennis moneyline cleanup can empty the strip; reinject Model pick /
    Confidence chips so structure matches MLB (required by UI parity checker).
    """
    if not html or "matchup-row" not in html:
        return html

    def _chip_block(stack: str) -> str:
        pick = ""
        conf = ""
        m = re.search(r'data-pick=["\']([^"\']*)["\']', stack, flags=re.I)
        if m:
            pick = m.group(1).strip()
        m = re.search(r'data-conf=["\']([^"\']*)["\']', stack, flags=re.I)
        if m:
            conf = m.group(1).strip()
        if not pick:
            m = re.search(
                r'class=["\'][^"\']*\bteam-slot[^"\']*\bfavored\b[^"\']*["\'][\s\S]{0,800}?'
                r'class=["\'][^"\']*\bteam-name\b[^"\']*["\'][^>]*>\s*([^<]+)',
                stack,
                flags=re.I,
            )
            if m:
                pick = m.group(1).strip()
        if not conf:
            m = re.search(
                r'class=["\'][^"\']*\bteam-slot[^"\']*\bfavored\b[^"\']*["\'][\s\S]{0,1200}?'
                r'class=["\'][^"\']*\bwin-pct\b[^"\']*["\'][^>]*>\s*([0-9.]+)',
                stack,
                flags=re.I,
            )
            if m:
                conf = m.group(1).strip()
        pick = pick or "—"
        conf = conf or "—"
        conf_disp = conf if conf.endswith("%") or conf == "—" else f"{conf}%"
        return (
            '<div class="lines-strip">'
            '<div class="line-chip"><div class="line-chip-label">Model pick</div>'
            f'<div class="line-chip-val">{pick}</div></div>'
            '<div class="line-chip"><div class="line-chip-label">Confidence</div>'
            f'<div class="line-chip-val">{conf_disp}</div></div>'
            "</div>"
        )

    out: list[str] = []
    cursor = 0
    for start, end, open_tag, inner in _walk_game_card_stacks(html):
        out.append(html[cursor:start])
        stack = open_tag + inner + "</div>"
        if "matchup-row" in stack and "lines-strip" not in stack:
            chip = _chip_block(stack)
            # Insert after matchup-row block (first closing of that section is hard;
            # place before card-footer / view-details / card-details).
            if re.search(r'<footer\b[^>]*\bcard-footer', stack, flags=re.I):
                stack = re.sub(
                    r'(<footer\b[^>]*\bcard-footer)',
                    chip + r"\1",
                    stack,
                    count=1,
                    flags=re.I,
                )
            elif "view-details-btn" in stack:
                stack = re.sub(
                    r'(<button\b[^>]*\bview-details-btn)',
                    chip + r"\1",
                    stack,
                    count=1,
                    flags=re.I,
                )
            else:
                stack = stack.replace("</div></div>", chip + "</div></div>", 1)
        out.append(stack)
        cursor = end
    out.append(html[cursor:])
    return "".join(out)


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
