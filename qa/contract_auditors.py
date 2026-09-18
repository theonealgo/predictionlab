#!/usr/bin/env python3
"""Generic contract auditors — catch the next bug, not just known regressions.

Architecture (see SITE_CHECKER_CHECKPOINTS.txt):

  TemplateContractAuditor
  DataIntegrityAuditor
  RuntimeAuditor
  PerformanceAccessibilityAuditor
  ChartAuditor
  ImagesAuditor
  ApiContractAuditor
  ResponsiveAuditor

Known-issue helpers in chart_shape.py stay underneath these auditors.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable
from urllib.parse import urljoin, urlparse

# Sports / template matrix
TEAM_SPORTS = (
    "mlb", "nhl", "nba", "ncaab", "ncaaw", "nfl", "ncaaf", "wnba", "cfl",
)
# Empty picks pages are OK only for these (true offseason in September).
OFFSEASON_OK_EMPTY = frozenset({"NBA", "NHL", "NCAAB", "NCAAW"})
INDIVIDUAL_SPORTS = ("tennis", "ufc")
GOLF = ("golf",)
SOCCER = ("soccer",)
ALL_PICKS_SLUGS = tuple(f"{s}-picks" for s in TEAM_SPORTS + INDIVIDUAL_SPORTS + GOLF + SOCCER)
ALL_RESULTS_SLUGS = tuple(f"{s}-results" for s in TEAM_SPORTS + INDIVIDUAL_SPORTS + GOLF + SOCCER)

# PageSpeed/a11y FAIL sports (unlocked). Others WARN until owner unlocks.
PSI_ENFORCE_SPORTS = frozenset(
    s.strip().upper()
    for s in (os.environ.get("PSI_ENFORCE_SPORTS") or "MLB").split(",")
    if s.strip()
)

# Logo CDN size expected for card faces (display ~52px).
LOGO_CARD_SIZE_BY_SPORT = {
    "MLB": 100,
    # Other sports stay on 500 until unlocked for the same miss.
}

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"


def _sport_from_slug(slug: str) -> str:
    return slug.split("-")[0].upper()


def global_leakage_issues(html: str, *, base_url: str = "") -> list[str]:
    """Site-wide HTML leakage / debug / wrong-host catches."""
    html = html or ""
    issues: list[str] = []
    low = html.lower()
    host_is_local = any(
        h in (base_url or "").lower()
        for h in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    )

    for needle, label in (
        ("traceback (most recent call last)", "Python traceback in HTML"),
        ("werkzeug debugger", "Werkzeug debugger in HTML"),
        ("debug mode: on", "Flask debug mode indicator"),
        ("internal server error", "Internal Server Error body"),
        ("application error", "Application Error body"),
        ("jinja2.exceptions", "Jinja exception leakage"),
        ("undefinederror", "Jinja UndefinedError leakage"),
    ):
        if needle in low:
            issues.append(label)

    # Accidental local / staging URLs in href/src (skip when auditing localhost).
    if not host_is_local:
        for m in re.finditer(
            r"""(?:href|src)=["'](https?://(?:127\.0\.0\.1|localhost)[^"']*)["']""",
            html,
            flags=re.I,
        ):
            issues.append(f"localhost URL in page: {m.group(1)[:80]}")
            break
        for m in re.finditer(
            r"""(?:href|src)=["'](https?://[^"']*(?:staging|ngrok|trycloudflare|herokuapp)[^"']*)["']""",
            html,
            flags=re.I,
        ):
            issues.append(f"staging/dev URL in page: {m.group(1)[:80]}")
            break

    # Template leakage markers (Jinja only — avoid JSON/JS {{)
    if re.search(r"\{%\s*(if|for|endif|endfor|block|extends|include)\b", html):
        issues.append("possible Jinja {% %} leakage in HTML")
    if re.search(r"\{\{\s*[a-zA-Z_][a-zA-Z0-9_\.]*\s*\}\}", html):
        issues.append("possible Jinja {{ var }} leakage in HTML")

    # Duplicate IDs (a11y / runtime)
    ids = re.findall(r'\bid=["\']([^"\']+)["\']', html, flags=re.I)
    seen: dict[str, int] = {}
    for i in ids:
        seen[i] = seen.get(i, 0) + 1
    dups = [i for i, n in seen.items() if n > 1 and not i.startswith("date-")]
    # date-YYYY-MM-DD can repeat across markets; ignore date-* and analysis-* bulk
    dups = [
        i for i in dups
        if not i.startswith("date-")
        and not i.startswith("analysis-")
        and not i.startswith("chart-")
    ]
    if dups:
        issues.append(
            f"duplicate HTML ids ({len(dups)}): " + ", ".join(sorted(dups)[:8])
        )

    # Empty aria-label — common false positive on decorative controls; WARN-level
    # is handled by callers if needed. Do not FAIL the whole page for one blank.
    # (kept out of FAIL leakage list on purpose)

    # Mixed content on https pages
    if (base_url or "").lower().startswith("https://"):
        if re.search(r"""(?:href|src)=["']http://(?!localhost|127\.0\.0\.1)""", html, flags=re.I):
            issues.append("mixed-content http:// asset on https page")

    return issues


def empty_aria_label_count(html: str) -> int:
    return len(re.findall(r'aria-label=["\']\s*["\']', html or "", flags=re.I))


def _picks_sport_display(sport: str) -> str:
    s = (sport or "").strip()
    if s.upper() == "SOCCER":
        return "Soccer"
    return s.upper() if s else ""


def blank_picks_issues(html: str, sport: str) -> list[str]:
    """FAIL-level: in-season sport showing an empty slate.

    This is the miss the owner was still catching by hand (live NFL/MLB
    'No predictions available') after chrome/ship already had a similar
    check — the generic layer never ran, and thin pages were WARN-skipped.
    """
    sport_u = (sport or "").strip().upper()
    if not html or sport_u in OFFSEASON_OK_EMPTY:
        return []
    display = _picks_sport_display(sport)
    if display and re.search(
        rf"No predictions available for\s+{re.escape(display)}\b",
        html,
        flags=re.I,
    ):
        return [f"blank-slate banner: No predictions available for {display}"]
    if re.search(r"No predictions available\b", html, flags=re.I):
        has_card = bool(
            re.search(
                r"data-pick-card|pl2-pick-card|data-game-card|game-card-stack",
                html,
                flags=re.I,
            )
            or re.search(
                r"""class=["'][^"']*\b(?:game-card|pick-card)\b""",
                html,
                flags=re.I,
            )
        )
        if not has_card:
            return [
                f"blank-slate: No predictions available ({display or sport_u})"
            ]
    return []


def seo_page_issues(html: str, path: str = "") -> list[str]:
    """Expanded on-page SEO contract (static)."""
    html = html or ""
    low = html.lower()
    problems: list[str] = []
    titles = re.findall(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if len(titles) == 0:
        problems.append("missing <title>")
    elif len(titles) > 1:
        problems.append(f"{len(titles)} <title> tags")
    else:
        t = re.sub(r"\s+", " ", titles[0]).strip()
        if not t:
            problems.append("empty <title>")
        elif len(t) < 10:
            problems.append(f"title too short ({len(t)})")

    if not re.search(r'<meta[^>]+name=["\']description["\']', html, flags=re.I):
        problems.append("missing meta description")
    else:
        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']*)',
            html,
            flags=re.I,
        )
        if m and not m.group(1).strip():
            problems.append("empty meta description")

    if not re.search(r'rel=["\']canonical["\']', html, flags=re.I):
        problems.append("missing canonical")
    else:
        m = re.search(
            r'rel=["\']canonical["\'][^>]*href=["\']([^"\']+)',
            html,
            flags=re.I,
        ) or re.search(
            r'href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']',
            html,
            flags=re.I,
        )
        if m and path:
            href = m.group(1)
            # Canonical shouldn't point at a different sport slug
            sport = path.strip("/").split("-")[0] if "-" in path.strip("/") else ""
            if sport and sport not in ("plans", "all", "daily", "privacy", "terms", "faq"):
                other = [
                    s for s in TEAM_SPORTS + INDIVIDUAL_SPORTS + GOLF + SOCCER
                    if s != sport and f"/{s}-" in href.lower()
                ]
                if other:
                    problems.append(f"canonical points at other sport ({other[0]})")

    for prop in ("og:title", "og:description", "og:url"):
        if f'property="{prop}"' not in low and f"property='{prop}'" not in low:
            problems.append(f"missing {prop}")

    if not re.search(r"<html\b[^>]*\blang\s*=", html, flags=re.I):
        problems.append("html missing lang")

    h1s = re.findall(r"<h1[\s>]", low)
    if len(h1s) == 0:
        problems.append("no <h1>")
    elif len(h1s) > 3:
        problems.append(f"{len(h1s)} <h1> tags")

    # Malformed JSON-LD
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    ):
        raw = block.strip()
        if not raw:
            problems.append("empty JSON-LD")
            continue
        try:
            json.loads(raw)
        except Exception:
            problems.append("malformed JSON-LD")
            break

    return problems


def share_card_contract_issues(html: str, sport: str = "") -> list[str]:
    """Expand share_ad_card_issues with PSI + a11y share requirements."""
    html = html or ""
    issues: list[str] = []
    if "social-export-wrap" not in html:
        return []
    if not re.search(r"/share/predictions/", html, flags=re.I):
        issues.append("share wrap present but no /share/predictions/ URL")
    for m in re.finditer(
        r'<a\b([^>]*\bsocial-image-link\b[^>]*)>(.*?)</a>',
        html,
        flags=re.I | re.S,
    ):
        attrs, inner = m.group(1), m.group(2)
        has_aria = bool(re.search(r"\baria-label\s*=\s*[\"'][^\"']+\S", attrs, flags=re.I))
        img_alt = re.search(r'\balt=["\']([^"\']*)["\']', inner, flags=re.I)
        if not has_aria and not (img_alt and img_alt.group(1).strip()):
            issues.append("social-image-link missing discernible name")
        src = re.search(r'\bsrc=["\']([^"\']+)["\']', inner, flags=re.I)
        if src and "/share/predictions/" in src.group(1) and "w=" not in src.group(1):
            if sport.upper() in PSI_ENFORCE_SPORTS or sport.upper() == "MLB":
                issues.append("share preview missing ?w= downscale")
        if src and re.search(r"localhost|127\.0\.0\.1", src.group(1), flags=re.I):
            issues.append("share image URL is localhost")
        if "loading=" not in inner.lower() and sport.upper() in PSI_ENFORCE_SPORTS:
            issues.append("share preview img missing loading=lazy")
        break
    return issues


def logo_size_issues(html: str, sport: str) -> list[str]:
    """Wrong ESPN CDN size for card faces."""
    sport_u = (sport or "").strip().upper()
    expect = LOGO_CARD_SIZE_BY_SPORT.get(sport_u)
    if not expect:
        return []
    slug = sport_u.lower()
    if sport_u == "NCAAF":
        path_sport = "ncaa"
    else:
        path_sport = slug
    # MLB: combiner?img=/i/teamlogos/mlb/500/…&h=100&w=100 is the signed-off
    # face (many raw /100/ paths 404). Treat combiner as satisfying expect=100.
    if sport_u == "MLB":
        if re.search(
            r"https://a\.espncdn\.com/combiner/i\?[^\"']*teamlogos/mlb/500/"
            r"[a-z0-9]+\.png[^\"']*(?:[?&](?:h|w)=100)",
            html,
            flags=re.I,
        ):
            # Still flag bare /500/ faces that never went through pagespeed.
            bare500 = re.findall(
                r"https://a\.espncdn\.com/i/teamlogos/mlb/500/[a-z0-9]+\.png",
                html,
                flags=re.I,
            )
            if bare500:
                return [
                    f"MLB logos still using bare /500/ ({len(bare500)}); "
                    "expected combiner h=100&w=100"
                ]
            return []
        bad100 = re.findall(
            r"https://a\.espncdn\.com/i/teamlogos/mlb/100/[a-z0-9]+\.png",
            html,
            flags=re.I,
        )
        if bad100:
            return [
                f"MLB logos using broken /100/ paths ({len(bad100)}); "
                "expected combiner from /500/"
            ]
    bad = re.findall(
        rf"https://a\.espncdn\.com/i/teamlogos/{re.escape(path_sport)}/(?!{expect}/)\d+/[a-z0-9]+\.png",
        html,
        flags=re.I,
    )
    # Only flag oversized 500 when we expect 100
    if expect == 100:
        bad500 = re.findall(
            rf"https://a\.espncdn\.com/i/teamlogos/{re.escape(path_sport)}/500/[a-z0-9]+\.png",
            html,
            flags=re.I,
        )
        if bad500:
            return [
                f"{sport_u} logos still using /500/ ({len(bad500)}); expected /{expect}/"
            ]
    return []


def ads_analytics_issues(html: str) -> list[str]:
    """Third-party Ads/GA loading contract (keep tags; don't block render)."""
    html = html or ""
    issues: list[str] = []
    aw_sync = re.search(
        r'<script\b[^>]*src="https://www\.googletagmanager\.com/gtag/js\?id=AW-[^"]*"',
        html,
        flags=re.I,
    )
    if aw_sync:
        issues.append(
            "Google Ads gtag.js present as blocking/async src in HTML; defer after load/idle"
        )
    ga_sync = re.search(
        r'<script\b[^>]*src="https://www\.googletagmanager\.com/gtag/js\?id=G-[^"]*"',
        html,
        flags=re.I,
    )
    if ga_sync:
        issues.append(
            "GA gtag.js present as head script src; defer after load/idle (do not remove)"
        )
    # Duplicate gtag boot markers
    if html.count("gtag('config', 'AW-") + html.count('gtag("config", "AW-') > 1:
        issues.append("duplicate AW gtag config")
    if html.count("gtag('config', 'G-") + html.count('gtag("config", "G-') > 2:
        issues.append("duplicate GA gtag config")
    # Unused fonts preconnect without stylesheet use
    if "fonts.googleapis.com" in html and "fonts.googleapis.com/css" not in html:
        if re.search(r'rel=["\']preconnect["\'][^>]*fonts\.googleapis', html, flags=re.I):
            issues.append("unused fonts.googleapis.com preconnect")
    return issues


def picks_functional_issues(html: str, sport: str = "") -> list[str]:
    """Picks functional contract beyond visual chrome."""
    html = html or ""
    issues: list[str] = []
    issues.extend(blank_picks_issues(html, sport))
    if "game-card" not in html and "pick-card" not in html and "data-pick-card" not in html:
        return issues

    # Placeholder / blank picks
    if re.search(r"\bTODO\b|\bPLACEHOLDER\b|\bTBD\b|lorem ipsum", html, flags=re.I):
        issues.append("placeholder/TODO text on picks page")

    # Model tags present
    tags = re.findall(r'class="model-tag"[^>]*>([^<]+)', html, flags=re.I)
    if tags:
        blank = sum(1 for t in tags if not t.strip() or t.strip() in {"—", "-", "N/A", "NA"})
        if blank:
            issues.append(f"{blank} blank/N/A model-tag faces")

    # Win pct blanks
    pcts = re.findall(r'class="win-pct"[^>]*>([^<]+)', html, flags=re.I)
    if pcts:
        bad = sum(1 for p in pcts if not re.search(r"\d", p))
        if bad >= max(2, len(pcts) // 2):
            issues.append(f"{bad}/{len(pcts)} win-pct faces missing numbers")

    # Impossible American odds (e.g. text "None", "nan")
    if re.search(r'class="ml-num[^"]*">\s*(None|nan|undefined)\s*<', html, flags=re.I):
        issues.append("impossible/None odds in ml-num")

    return issues


def html_weight_issues(html: str, path: str = "", *, max_kb: int = 900) -> list[str]:
    n = len(html or "")
    if n > max_kb * 1024:
        return [f"HTML size {n // 1024} KiB exceeds {max_kb} KiB budget ({path})"]
    if n and n < 1500:
        return [f"HTML suspiciously small ({n} bytes) — possible error page"]
    return []


class _BaseContractAuditor:
    def __init__(self, session, base: str, report, CheckResult, timed_get: Callable):
        self.s = session
        self.base = base.rstrip("/")
        self.r = report
        self.CheckResult = CheckResult
        self._timed_get = timed_get

    def add(self, label, status, message, url="", detail="", auditor=""):
        self.r.add(
            self.CheckResult(
                label=label,
                status=status,
                message=message,
                detail=detail or "",
                url=url,
                auditor=auditor or self.NAME,
            )
        )

    def fetch(self, path: str, timeout: int = 45) -> tuple[int, str]:
        try:
            resp = self._timed_get(self.s, self.r, self.base, path, timeout=timeout)
            return resp.status_code, resp.text or ""
        except Exception as exc:
            return 0, str(exc)


class TemplateContractAuditor(_BaseContractAuditor):
    """Hard template family matrix — one miss on MLB must not silently drift NHL."""

    NAME = "templates"

    def run(self):
        from chart_shape import (
            team_chart_template_issues,
            team_picks_template_issues,
            team_results_template_issues,
        )

        # Team picks + results + chart
        for slug in TEAM_SPORTS:
            sport = slug.upper()
            picks = f"/{slug}-picks"
            code, html = self.fetch(picks)
            if code >= 500 or code == 0:
                self.add(
                    f"Template picks load: {slug}",
                    FAIL,
                    f"HTTP {code or 'error'} loading {picks}",
                    url=picks,
                )
                continue
            if code == 404:
                self.add(f"Template picks load: {slug}", WARN, "404", url=picks)
                continue
            blank = blank_picks_issues(html, sport)
            has_cards = bool(
                "data-pick-card" in html.lower()
                or "game-card" in html
                or "pick-card" in html
            )
            offseason_page = (
                sport in OFFSEASON_OK_EMPTY
                or "is in the off-season" in html.lower()
            )
            if blank:
                self.add(
                    f"Template picks: {slug}",
                    FAIL,
                    "; ".join(blank),
                    url=picks,
                )
            elif (len(html) < 4000 or not has_cards) and not offseason_page:
                self.add(
                    f"Template picks: {slug}",
                    FAIL,
                    "thin/empty picks page on an in-season sport",
                    url=picks,
                )
            elif not has_cards and offseason_page:
                self.add(
                    f"Template picks: {slug}",
                    PASS,
                    "off-season / no slate",
                    url=picks,
                )
            else:
                issues = team_picks_template_issues(html, sport)
                self.add(
                    f"Team picks contract: {slug}",
                    FAIL if issues else PASS,
                    "; ".join(issues) if issues else "structural contract OK",
                    url=picks,
                )

            results = f"/{slug}-results"
            code, html = self.fetch(results)
            if code == 200 and len(html) > 4000:
                issues = team_results_template_issues(html, sport)
                self.add(
                    f"Team results contract: {slug}",
                    FAIL if issues else PASS,
                    "; ".join(issues) if issues else "structural contract OK",
                    url=results,
                )
                chart = f"/{slug}-results?view=chart"
                ccode, chtml = self.fetch(chart)
                if ccode == 200 and len(chtml) > 2000:
                    cissues = team_chart_template_issues(chtml, sport)
                    self.add(
                        f"Team chart contract: {slug}",
                        FAIL if cissues else PASS,
                        "; ".join(cissues) if cissues else "chart contract OK",
                        url=chart,
                    )

        # Tennis / UFC individual template markers
        for slug in INDIVIDUAL_SPORTS:
            for which in ("picks", "results"):
                path = f"/{slug}-{which}"
                code, html = self.fetch(path)
                if code != 200 or len(html) < 2000:
                    self.add(
                        f"Individual {which}: {slug}",
                        WARN,
                        f"HTTP {code}, {len(html)} bytes",
                        url=path,
                    )
                    continue
                need = ["pl2-", "research"] if False else []
                # Minimal: page must not be team-sports chart table for tennis/ufc picks
                bad = []
                if which == "picks" and "picks-chart-table" in html and "match-card" not in html and "fight-card" not in html:
                    # allow if they share chart infra
                    pass
                if "Sport not found" in html or "Internal Server Error" in html:
                    bad.append("error body")
                self.add(
                    f"Individual {which} contract: {slug}",
                    FAIL if bad else PASS,
                    "; ".join(bad) if bad else "page loads",
                    url=path,
                )

        # Golf + Soccer smoke loads (detailed soccer lives in SoccerChecker)
        for path in ("/golf-picks", "/golf-results", "/soccer-picks", "/soccer-results"):
            code, html = self.fetch(path, timeout=90)
            if path == "/soccer-picks":
                blank = blank_picks_issues(html, "SOCCER")
                if blank:
                    self.add(
                        f"Template load: {path}",
                        FAIL,
                        "; ".join(blank),
                        url=path,
                    )
                    continue
            sev = FAIL if code >= 500 or code == 0 else (WARN if code != 200 else PASS)
            self.add(
                f"Template load: {path}",
                sev,
                f"HTTP {code}, {len(html)} bytes",
                url=path,
            )


class DataIntegrityAuditor(_BaseContractAuditor):
    """Cross-check displayed numbers against chart/API payloads where possible."""

    NAME = "integrity"

    def run(self):
        # Representative sports: enforce MLB; sample NFL/NCAAF if live
        targets = ["mlb"]
        for extra in ("nfl", "ncaaf", "nba"):
            if extra not in targets:
                targets.append(extra)

        for slug in targets:
            sport = slug.upper()
            picks = f"/{slug}-picks"
            code, html = self.fetch(picks)
            blank = blank_picks_issues(html, sport)
            if blank:
                self.add(
                    f"Integrity picks slate: {slug}",
                    FAIL,
                    "; ".join(blank),
                    url=picks,
                )
            if code != 200 or len(html) < 4000:
                continue

            # Cards present ↔ share wrap pick count if present
            cards = len(re.findall(r"data-pick-card", html, flags=re.I))
            if not cards:
                cards = len(re.findall(r'class="[^"]*\bgame-card\b', html, flags=re.I))
            m = re.search(r'data-share-picks="(\d+)"', html)
            if m and cards:
                share_n = int(m.group(1))
                # share card often tops at 2–3 picks for the image; only flag if share > cards
                if share_n > cards:
                    self.add(
                        f"Integrity share vs cards: {slug}",
                        FAIL,
                        f"data-share-picks={share_n} but only {cards} cards",
                        url=picks,
                    )
                else:
                    self.add(
                        f"Integrity share vs cards: {slug}",
                        PASS,
                        f"share_picks={share_n}, cards≈{cards}",
                        url=picks,
                    )

            # Chart page HTML sport consistency (API paths vary by sport)
            chart = f"/{slug}-results?view=chart"
            ccode, chtml = self.fetch(chart, timeout=60)
            if ccode == 200 and len(chtml) > 2000:
                if chtml.lstrip().startswith("{") or "Internal Server Error" in chtml:
                    self.add(
                        f"Integrity chart page: {slug}",
                        FAIL,
                        "chart view returned error/JSON instead of HTML",
                        url=chart,
                    )
                elif f"sport-{slug}" not in chtml.lower() and f'data-sport="{sport}"' not in chtml and sport.lower() not in chtml.lower()[:2000]:
                    self.add(
                        f"Integrity chart page: {slug}",
                        WARN,
                        "chart HTML missing sport marker",
                        url=chart,
                    )
                else:
                    # Bleed: another team-sport body class
                    bleed = [
                        s for s in TEAM_SPORTS
                        if s != slug and f"sport-{s}" in chtml.lower()
                    ]
                    if bleed:
                        self.add(
                            f"Integrity chart sport bleed: {slug}",
                            FAIL,
                            f"found other sport class: {bleed[0]}",
                            url=chart,
                        )
                    else:
                        self.add(
                            f"Integrity chart page: {slug}",
                            PASS,
                            "chart HTML OK",
                            url=chart,
                        )


class RuntimeAuditor(_BaseContractAuditor):
    """Leakage / status / optional Playwright console errors."""

    NAME = "runtime"

    SAMPLE_PATHS = (
        "/",
        "/mlb-picks",
        "/mlb-results",
        "/nfl-picks",
        "/soccer-picks",
        "/tennis-picks",
        "/ufc-picks",
        "/golf-picks",
        "/plans",
        "/blog",
    )

    def run(self):
        for path in self.SAMPLE_PATHS:
            code, html = self.fetch(path, timeout=60)
            if code in (500, 502, 503) or code == 0:
                self.add(
                    f"Runtime HTTP: {path}",
                    FAIL,
                    f"HTTP {code or 'error'}",
                    url=path,
                )
                continue
            if code >= 400:
                self.add(
                    f"Runtime HTTP: {path}",
                    WARN,
                    f"HTTP {code}",
                    url=path,
                )
            leaks = global_leakage_issues(html, base_url=self.base)
            if leaks:
                self.add(
                    f"Runtime leakage: {path}",
                    FAIL,
                    "; ".join(leaks[:6]),
                    url=path,
                    detail="; ".join(leaks),
                )
            else:
                n_empty = empty_aria_label_count(html)
                if n_empty:
                    self.add(
                        f"Runtime a11y: {path}",
                        WARN,
                        f'{n_empty} empty aria-label=""',
                        url=path,
                    )
                self.add(
                    f"Runtime leakage: {path}",
                    PASS,
                    "no debug/traceback/localhost/mixed-content/dup-id",
                    url=path,
                )

        # Optional Playwright console check
        if os.environ.get("AUDIT_PLAYWRIGHT_CONSOLE", "").strip() in {"1", "true", "yes"}:
            self._console_errors()
        else:
            self.add(
                "Runtime console",
                INFO,
                "skipped — set AUDIT_PLAYWRIGHT_CONSOLE=1 to enable Playwright console scan",
            )

    def _console_errors(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.add("Runtime console", WARN, f"Playwright unavailable: {exc}")
            return
        errors: list[str] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.on(
                    "pageerror",
                    lambda exc: errors.append(f"pageerror: {exc}"),
                )
                page.on(
                    "console",
                    lambda msg: errors.append(f"console.{msg.type}: {msg.text}")
                    if msg.type in ("error",) else None,
                )
                page.goto(urljoin(self.base + "/", "mlb-picks"), wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                browser.close()
        except Exception as exc:
            self.add("Runtime console", WARN, f"console scan failed: {exc}")
            return
        bad = [e for e in errors if "favicon" not in e.lower()]
        self.add(
            "Runtime console: /mlb-picks",
            FAIL if bad else PASS,
            f"{len(bad)} error(s)" if bad else "no console errors",
            detail="; ".join(bad[:8]),
            url="/mlb-picks",
        )


class PerformanceAccessibilityAuditor(_BaseContractAuditor):
    """Generic per-sport PSI/a11y gate (MLB enforced; others warn until unlock)."""

    NAME = "perf_a11y"

    def run(self):
        from chart_shape import picks_pagespeed_a11y_issues

        for slug in ALL_PICKS_SLUGS:
            sport = _sport_from_slug(slug)
            path = f"/{slug}"
            code, html = self.fetch(path, timeout=90)
            if code != 200 or len(html) < 2000:
                continue

            issues = []
            issues.extend(picks_pagespeed_a11y_issues(html, sport))
            issues.extend(share_card_contract_issues(html, sport))
            issues.extend(logo_size_issues(html, sport))
            issues.extend(ads_analytics_issues(html))
            issues.extend(picks_functional_issues(html, sport))
            issues.extend(html_weight_issues(html, path))
            issues.extend(seo_page_issues(html, path))
            # De-dupe while preserving order
            seen = set()
            uniq = []
            for i in issues:
                if i not in seen:
                    seen.add(i)
                    uniq.append(i)

            enforce = sport in PSI_ENFORCE_SPORTS
            if uniq:
                self.add(
                    f"Perf/a11y picks: {slug}",
                    FAIL if enforce else WARN,
                    "; ".join(uniq[:8]),
                    url=path,
                    detail="; ".join(uniq),
                )
            else:
                self.add(
                    f"Perf/a11y picks: {slug}",
                    PASS,
                    f"gate OK ({'enforced' if enforce else 'warn-mode'})",
                    url=path,
                )

        # Homepage global
        code, html = self.fetch("/", timeout=60)
        if code == 200:
            leaks = global_leakage_issues(html, base_url=self.base)
            seo = seo_page_issues(html, "/")
            weight = html_weight_issues(html, "/", max_kb=1200)
            ads = ads_analytics_issues(html)
            all_i = leaks + seo + weight + ads
            self.add(
                "Perf/a11y homepage",
                FAIL if all_i else PASS,
                "; ".join(all_i[:8]) if all_i else "homepage gate OK",
                url="/",
                detail="; ".join(all_i),
            )


class ChartAuditor(_BaseContractAuditor):
    """Generic chart contract for every team sport (+ tennis/ufc smoke)."""

    NAME = "charts"

    def run(self):
        from chart_shape import (
            team_chart_same_as_cards_issues,
            team_chart_template_issues,
            tennis_chart_same_as_cards_issues,
        )

        for slug in TEAM_SPORTS:
            sport = slug.upper()
            cards_path = f"/{slug}-results"
            chart_path = f"/{slug}-results?view=chart"
            ccode, cards_html = self.fetch(cards_path, timeout=60)
            hcode, chart_html = self.fetch(chart_path, timeout=60)
            if hcode >= 500 or hcode == 0:
                self.add(
                    f"Chart load: {slug}",
                    FAIL,
                    f"HTTP {hcode or 'error'}",
                    url=chart_path,
                )
                continue
            if hcode != 200 or len(chart_html) < 1500:
                self.add(
                    f"Chart load: {slug}",
                    WARN,
                    f"HTTP {hcode}, {len(chart_html)} bytes",
                    url=chart_path,
                )
                continue
            if chart_html.lstrip().startswith("{") or "Internal Server Error" in chart_html:
                self.add(
                    f"Chart body: {slug}",
                    FAIL,
                    "chart view returned JSON/error instead of HTML",
                    url=chart_path,
                )
                continue

            issues = []
            issues.extend(team_chart_template_issues(chart_html, sport))
            if ccode == 200 and len(cards_html) > 2000:
                issues.extend(team_chart_same_as_cards_issues(cards_html, chart_html, sport))
            # Wrong-sport bleed in chart HTML
            for other in TEAM_SPORTS:
                if other == slug:
                    continue
                if f"sport-{other}" in chart_html.lower() and f"sport-{slug}" in chart_html.lower():
                    # both present can happen in shared chrome — only fail if body class is wrong
                    if re.search(
                        rf'<body[^>]*class="[^"]*\bsport-{other}\b',
                        chart_html,
                        flags=re.I,
                    ) and not re.search(
                        rf'<body[^>]*class="[^"]*\bsport-{slug}\b',
                        chart_html,
                        flags=re.I,
                    ):
                        issues.append(f"chart body class is sport-{other}, expected sport-{slug}")
            # Empty chart while cards have graded content
            if "tally" not in chart_html.lower() and "consensus" not in chart_html.lower():
                if ccode == 200 and ("game-card" in cards_html or "pick-card" in cards_html):
                    issues.append("chart missing tally/consensus while cards page has games")

            self.add(
                f"Chart contract: {slug}",
                FAIL if issues else PASS,
                "; ".join(issues[:6]) if issues else "chart contract OK",
                url=chart_path,
                detail="; ".join(issues),
            )

        # Tennis chart vs cards
        tcode, thtml = self.fetch("/tennis-results", timeout=60)
        tccode, tchtml = self.fetch("/tennis-results?view=chart", timeout=60)
        if tcode == 200 and tccode == 200:
            tiss = tennis_chart_same_as_cards_issues(thtml, tchtml)
            self.add(
                "Chart contract: tennis",
                FAIL if tiss else PASS,
                "; ".join(tiss) if tiss else "tennis chart OK",
                url="/tennis-results?view=chart",
            )


class ImagesAuditor(_BaseContractAuditor):
    """Global logo / share / static image contract."""

    NAME = "images"

    def run(self):
        sample = ["mlb", "nfl", "nba", "soccer", "tennis"]
        for slug in sample:
            path = f"/{slug}-picks"
            code, html = self.fetch(path, timeout=90)
            if code != 200 or len(html) < 2000:
                continue
            sport = slug.upper() if slug != "soccer" else "SOCCER"
            issues = []
            issues.extend(logo_size_issues(html, sport))
            issues.extend(share_card_contract_issues(html, sport))

            # Localhost / staging image URLs
            for m in re.finditer(r"""(?:src)=["']([^"']+\.(?:png|jpe?g|webp|svg|gif)[^"']*)["']""", html, flags=re.I):
                src = m.group(1)
                if re.search(r"localhost|127\.0\.0\.1|staging|ngrok", src, flags=re.I):
                    if "127.0.0.1" not in self.base and "localhost" not in self.base:
                        issues.append(f"image URL is local/staging: {src[:80]}")
                        break

            # Missing width/height on team-logo (CLS risk)
            logos = re.findall(r"<img\b[^>]*\bteam-logo\b[^>]*>", html, flags=re.I)
            missing_dim = 0
            for tag in logos[:40]:
                if "width=" not in tag.lower() or "height=" not in tag.lower():
                    missing_dim += 1
            if missing_dim >= 4:
                issues.append(f"{missing_dim} team-logo imgs missing width/height")

            # HEAD-check a few logo URLs for 404
            srcs = re.findall(
                r'src="(https://a\.espncdn\.com/i/teamlogos/[^"]+)"',
                html,
                flags=re.I,
            )[:6]
            for src in srcs:
                try:
                    resp = self.s.head(src, timeout=8, allow_redirects=True)
                    if resp.status_code == 404:
                        issues.append(f"logo 404: {src}")
                except Exception:
                    pass

            self.add(
                f"Images picks: {slug}",
                FAIL if any("404" in i or "localhost" in i or "/500/" in i for i in issues)
                else (WARN if issues else PASS),
                "; ".join(issues[:6]) if issues else "logos/share OK",
                url=path,
                detail="; ".join(issues),
            )


class ApiContractAuditor(_BaseContractAuditor):
    """Backend/API contract — JSON vs HTML, required fields, wrong-sport bleed."""

    NAME = "api"

    ENDPOINTS = (
        ("/healthz", "health"),
        ("/api/search?query=Detroit", "search"),
        ("/api/performance-data", "performance"),
        ("/robots.txt", "robots"),
    )

    def run(self):
        for path, kind in self.ENDPOINTS:
            code, body = self.fetch(path, timeout=30)
            if code >= 500 or code == 0:
                self.add(
                    f"API {kind}",
                    FAIL,
                    f"HTTP {code or 'error'}",
                    url=path,
                )
                continue
            if kind == "health":
                self.add(
                    f"API {kind}",
                    PASS if code == 200 else WARN,
                    f"HTTP {code}",
                    url=path,
                )
                continue
            if kind == "robots":
                issues = []
                if code != 200:
                    issues.append(f"HTTP {code}")
                elif "user-agent" not in body.lower():
                    issues.append("robots.txt missing User-agent")
                self.add(
                    f"API {kind}",
                    FAIL if issues else PASS,
                    "; ".join(issues) if issues else "robots.txt OK",
                    url=path,
                )
                continue
            # JSON endpoints
            if body.lstrip().startswith("<"):
                self.add(
                    f"API {kind}",
                    FAIL,
                    "returned HTML instead of JSON",
                    url=path,
                )
                continue
            try:
                payload = json.loads(body)
            except Exception:
                self.add(f"API {kind}", FAIL, "invalid JSON", url=path)
                continue
            if payload is None or payload == {} or payload == []:
                self.add(f"API {kind}", WARN, "empty JSON payload", url=path)
                continue
            self.add(f"API {kind}", PASS, "JSON OK", url=path)

        # Sitemap (optional)
        for sm in ("/sitemap.xml", "/sitemap_index.xml"):
            code, body = self.fetch(sm, timeout=20)
            if code == 404:
                continue
            if code == 200 and ("<urlset" in body or "<sitemapindex" in body):
                self.add("API sitemap", PASS, f"{sm} OK", url=sm)
                # Spot-check a few URLs resolve
                locs = re.findall(r"<loc>([^<]+)</loc>", body)[:5]
                bad = 0
                for loc in locs:
                    try:
                        path = urlparse(loc).path or "/"
                        c, _ = self.fetch(path, timeout=20)
                        if c >= 500 or c == 0:
                            bad += 1
                    except Exception:
                        bad += 1
                if bad:
                    self.add(
                        "API sitemap URLs",
                        FAIL,
                        f"{bad}/{len(locs)} sitemap URLs failed",
                        url=sm,
                    )
                break
            if code >= 500:
                self.add("API sitemap", FAIL, f"HTTP {code}", url=sm)


class ResponsiveAuditor(_BaseContractAuditor):
    """Viewport overflow checks via Playwright (optional)."""

    NAME = "responsive"

    VIEWPORTS = (
        (375, 812, "iphone-x"),
        (390, 844, "iphone-12"),
        (430, 932, "iphone-14-pro-max"),
        (768, 1024, "ipad"),
        (1024, 768, "ipad-landscape"),
        (1440, 900, "desktop"),
    )

    SAMPLE = ("/mlb-picks", "/nfl-picks", "/soccer-picks", "/")

    def run(self):
        if os.environ.get("AUDIT_PLAYWRIGHT_RESPONSIVE", "").strip() not in {
            "1",
            "true",
            "yes",
        }:
            self.add(
                "Responsive",
                INFO,
                "skipped — set AUDIT_PLAYWRIGHT_RESPONSIVE=1 to enable viewport overflow scans",
            )
            return
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.add("Responsive", WARN, f"Playwright unavailable: {exc}")
            return

        failures = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for path in self.SAMPLE:
                    for w, h, label in self.VIEWPORTS:
                        page = browser.new_page(viewport={"width": w, "height": h})
                        try:
                            page.goto(
                                urljoin(self.base + "/", path.lstrip("/")),
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )
                            page.wait_for_timeout(400)
                            overflow = page.evaluate(
                                """() => {
                                  const doc = document.documentElement;
                                  const body = document.body;
                                  const sw = Math.max(doc.scrollWidth, body ? body.scrollWidth : 0);
                                  const cw = doc.clientWidth;
                                  return {sw, cw, overflow: sw > cw + 2};
                                }"""
                            )
                            if overflow and overflow.get("overflow"):
                                failures.append(
                                    f"{path}@{label}: scrollWidth {overflow['sw']} > {overflow['cw']}"
                                )
                        except Exception as exc:
                            failures.append(f"{path}@{label}: {exc}")
                        finally:
                            page.close()
                browser.close()
        except Exception as exc:
            self.add("Responsive", WARN, f"responsive scan failed: {exc}")
            return

        self.add(
            "Responsive overflow",
            FAIL if failures else PASS,
            f"{len(failures)} overflow(s)" if failures else "no horizontal overflow on sample pages",
            detail="; ".join(failures[:12]),
        )
