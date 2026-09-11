#!/usr/bin/env python3
"""Open every header/footer destination. Fail if a link 404s, 500s, or hangs.

This is the check the owner has been doing by hand:
  Sports menu complete + green in-season, Blog, Pricing, Affiliate,
  and every one of those URLs actually loads.

Also fail when a page's header/footer is not the same Sports / Models /
  Results + site-directory-footer chrome as the homepage (thin Picks|Results
  bars and hub isolation navs). Locked MLB / tennis / UFC-picks chrome is
  not rewritten; every other HTML page is.

Also fail any sport when the team-sports template is incomplete:
  H2H Last 10 values on picks (not label-only / all — / all 0),
  Consensus Based Betting Records (unanimous / all-but rows + bars),
  PL vs Sportsbook (Books favorite / PL favorite / disagree / agree + bars),
  Moneyline | Spread | Totals tabs, Cards|Chart / view=chart,
  XSharp values when an XSharp consensus row is present, Prediction Lab
  W-L when XSharp already has a record (Books and Totals), scored game-log
  rows that are still ungraded, Copy All on in-season team picks
  (every loaded game + ML/spread/totals; MLB button only), coin-flip /
  blank-Books / stuck-50% pick slates, missing H2H Last 10 on some cards,
  and results charts whose 2/4 or Past 7 cells ignore the cards.
  Also fail kickoff times stuck on Upcoming, missing team logos
  (empty src / site fallback), H2H Last 10 gaps, NFL/CFL/NCAAF 4/4
  consensus, Last Night G2/Takedown/Efficiency dashes when other
  moneyline models have a record, last-night consensus or PL vs
  Sportsbook all 0-0 on a graded slate, and Last Night dates that
  disagree across moneyline / spread / totals.

  Tennis / UFC / Golf: moneyline consensus + chart view (no H2H / PL-vs-books
  required unless the page already shows those).

Run:
  .venv/bin/python qa/chrome_checker.py --url https://predictionlab.io
  .venv/bin/python qa/site_checker.py --chrome --url https://predictionlab.io
  .venv/bin/python qa/site_checker.py --ship --url https://predictionlab.io
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

_ROOT = Path(__file__).resolve().parents[1]
_QA = Path(__file__).resolve().parent
for _p in (_QA, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chart_shape import (  # noqa: E402
    ML_CHART,
    PL_VS_BOOKS,
    card_blank_market_line_issues,
    card_missing_model_value_issues,
    h2h_gap_issues,
    missing_signed_off_charts,
    nba_last_season_gap_issues,
    ncaaf_results_date_nav_issues,
    mlb_xsharp_totals_issues,
    nfl_chart_api_issues,
    nfl_chart_not_mlb_issues,
    nfl_chart_same_as_cards_issues,
    nfl_chart_window_tally_issues,
    nfl_missing_efficiency_issues,
    nfl_spread_result_card_issues,
    nfl_stale_season_perf_issues,
    nfl_stale_season_week_issues,
    nhl_chart_view_missing_issues,
    picks_clock_issues,
    picks_logo_issues,
    picks_placeholder_issues,
    results_math_issues,
    six_model_chart_issues,
    team_chart_template_issues,
    team_picks_template_issues,
    team_results_template_issues,
    tennis_chart_same_as_cards_issues,
)

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

# Pages whose chrome is locked and is not rewritten to match homepage.
CHROME_LOCK_SKIP = frozenset(
    {
        "/mlb-picks",
        "/tennis-picks",
        "/tennis-results",
        "/ufc-picks",
    }
)

# Locked Sports menu — same list as templates/partials/research_header.html
LOCKED_SPORTS = (
    ("NBA", "/nba-picks"),
    ("NFL", "/nfl-picks"),
    ("MLB", "/mlb-picks"),
    ("NHL", "/nhl-picks"),
    ("Soccer", "/soccer-picks"),
    ("NCAAB", "/ncaab-picks"),
    ("NCAAF", "/ncaaf-picks"),
    ("NCAAW", "/ncaaw-picks"),
    ("WNBA", "/wnba-picks"),
    ("CFL", "/cfl-picks"),
    ("Tennis", "/tennis-picks"),
    ("UFC", "/ufc-picks"),
    ("Golf", "/golf-picks"),
)

LOCKED_RESULTS = (
    "/all-sports-results",
    "/nba-results",
    "/nfl-results",
    "/mlb-results",
    "/nhl-results",
    "/soccer-results",
    "/ncaab-results",
    "/ncaaf-results",
    "/ncaaw-results",
    "/wnba-results",
    "/cfl-results",
    "/tennis-results",
    "/ufc-results",
    "/golf-results",
    "/daily-report",
    "/results/downloads",
)

LOCKED_MODELS = (
    "/performance",
    "/our-model-vs-sportsbooks",
    "/ai-sports-betting-picks-today",
    "/tutorial",
)

# September-safe: these Sports rows must be class="in-season" (green).
IN_SEASON_MUST = ("NFL", "MLB", "Soccer", "NCAAF", "CFL", "Tennis", "UFC", "Golf", "WNBA")

# Empty picks pages are OK only for these (true offseason in September).
# WNBA is still live / playoffs — a blank /wnba-picks is a FAIL.
OFFSEASON_OK_EMPTY = frozenset({"NBA", "NHL", "NCAAB", "NCAAW"})

# 302 is OK (login / plans / auth-gated tools). Everything else must 200.
ALLOW_302 = {
    "/plans",
    "/login",
    "/signup",
    "/performance",
    "/player-props",
    "/logout",
    "/account",
    "/search",
}

RULE9 = (
    "theoddsapi",
    "the odds api",
    "sandbox only",
    "not live",
    "isolation —",
    "prob source",
    "elo + market blend",
)

# Team-sports results template (NFL-style). Chrome used to only GET the URL.
H2H_LAST10 = "H2H Last 10"
RL_CHART = PL_VS_BOOKS  # started= heuristic for pages that already have charts

# Every team sport — same H2H + both chart families + Cards|Chart checks.
TEAM_TEMPLATE_SPORTS = (
    ("NBA", "/nba-picks", "/nba-results"),
    ("NFL", "/nfl-picks", "/nfl-results"),
    ("MLB", "/mlb-picks", "/mlb-results"),
    ("NHL", "/nhl-picks", "/nhl-results"),
    ("Soccer", "/soccer-picks", "/soccer-results"),
    ("NCAAB", "/ncaab-picks", "/ncaab-results"),
    ("NCAAF", "/ncaaf-picks", "/ncaaf-results"),
    ("NCAAW", "/ncaaw-picks", "/ncaaw-results"),
    ("WNBA", "/wnba-picks", "/wnba-results"),
    ("CFL", "/cfl-picks", "/cfl-results"),
)

# ML consensus + chart view only (no H2H / PL-vs-books unless already on page).
ML_ONLY_SPORTS = (
    ("Tennis", "/tennis-picks", "/tennis-results"),
    ("UFC", "/ufc-picks", "/ufc-results"),
    ("Golf", "/golf-picks", "/golf-results"),
)


def _has_chart_view_control(html: str) -> bool:
    """Cards|Chart control — href, query, or the JS picks-view toggle."""
    html = html or ""
    if "view=chart" in html:
        return True
    if re.search(r"setPicksView\(\s*['\"]chart['\"]\s*\)", html):
        return True
    if re.search(r'id=["\']pvChartBtn["\']', html):
        return True
    if re.search(r'class="[^"]*pl-view-toggle', html) and re.search(
        r">\s*Chart\s*<", html
    ):
        return True
    return False


def _missing_results_charts(html: str) -> list[str]:
    return missing_signed_off_charts(
        html,
        require_pl_vs_books=True,
        require_market_tabs=True,
        require_totals=False,
    )


def _h2h_value_is_real(raw: str) -> bool:
    """True for a real last-10 total. 0 / 0 (0 games) / — do not count."""
    plain = re.sub(r"<[^>]+>", "", raw or "")
    plain = re.sub(r"&mdash;|&ndash;|—|–", "", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if re.fullmatch(r"first meeting", plain, flags=re.I):
        return True
    if not re.search(r"\d", plain):
        return False
    if re.fullmatch(r"0+(?:\.0+)?", plain):
        return False
    if re.fullmatch(r"0+(?:\.0+)?\s*\(\s*0\s*games?\)", plain, flags=re.I):
        return False
    return True


def _h2h_on_card_face(html: str) -> bool:
    """True when H2H is on the card face, not only buried in View Details."""
    html = html or ""
    if "h2h-face-chip" in html:
        return True
    return bool(
        re.search(r'line-chip-label">\s*H2H Last 10', html, flags=re.I)
    )


def _h2h_has_values(html: str) -> bool:
    """True when H2H Last 10 chips have a real number, not only — or 0."""
    html = html or ""
    vals = re.findall(
        r"H2H Last 10</span>\s*<span class=\"sf-val\">([\s\S]*?)</span>",
        html,
        flags=re.I,
    )
    data = re.findall(r'data-h2h="([^"]*)"', html)
    return any(_h2h_value_is_real(raw) for raw in vals + data)


def _face_pl_ml_has_values(html: str) -> bool:
    """True when at least one pick-card face shows a real Prediction Lab moneyline."""
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
    nums = re.findall(
        r'class="ml-src pl">\s*Prediction Lab\s*</span>\s*'
        r'<span class="ml-num[^"]*">\s*([+]?\d+|[\u2212\-]\d+)\s*</span>',
        html,
        flags=re.I,
    )
    return len(nums) >= 2


def _has_pick_cards(html: str) -> bool:
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
    low = html.lower()
    return bool(
        H2H_LAST10 in html
        or "data-pick-card" in low
        or "pl2-pick-card" in low
        or "data-game-card" in low
        or 'class="pick-card"' in low
        or "class='pick-card'" in low
    )


def _xsharp_rows_present(html: str) -> bool:
    return bool(
        re.search(r'<td class="bucket">\s*XSharp\s*</td>', html or "", flags=re.I)
    )


def _wl_has_a_result(text: str) -> bool:
    """True when a W-L (or non-zero %) is real. All 0-0 / 0% do not count."""
    text = text or ""
    for m in re.finditer(r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?", text):
        if int(m.group(1)) > 0 or int(m.group(2)) > 0 or int(m.group(3) or 0) > 0:
            return True
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        try:
            if float(m.group(1)) > 0:
                return True
        except ValueError:
            continue
    return False


def _consensus_xsharp_has_values(html: str) -> bool:
    """True when a consensus XSharp row has a real W-L or percent, not — / 0-0."""
    return _consensus_bucket_has_values(html, "XSharp")


def _consensus_bucket_has_values(html: str, bucket: str) -> bool:
    rows = re.findall(
        rf'<td class="bucket">\s*{re.escape(bucket)}\s*</td>([\s\S]*?)</tr>',
        html or "",
        flags=re.I,
    )
    return any(_wl_has_a_result(row) for row in rows)


def _pl_records_expected(html: str) -> bool:
    """True when this HTML already has PL W-L or graded Spread / Totals tiles."""
    if _consensus_bucket_has_values(html, "Prediction Lab"):
        return True
    for m in re.finditer(
        r'class="[^"]*(?:daily-model|model-label)[^"]*"[^>]*>\s*'
        r"(?:[⭐🎯📊🤖🏆⚡📈🎲]\s*)?"
        r"(Spread|Over/Under)\s*</div>"
        r"[\s\S]{0,360}?class=\"[^\"]*(?:daily-rec|model-rec)[^\"]*\"[^>]*>\s*"
        r"([^<]{1,40})</div>",
        html or "",
        flags=re.I,
    ):
        if _wl_has_a_result(m.group(2)):
            return True
    return False


def _pl_consensus_missing_where_xsharp_exists(
    html: str, expected_html: str | None = None
) -> bool:
    """True when Books/Totals should show PL (cards have it) but this page does not."""
    if not _pl_records_expected(expected_html if expected_html is not None else html):
        return False
    for table in re.findall(r"<table[\s\S]*?</table>", html or "", flags=re.I):
        if not re.search(r'class="bucket">\s*Prediction Lab\s*<', table, flags=re.I):
            continue
        if not re.search(r'class="bucket">\s*XSharp\s*<', table, flags=re.I):
            continue
        if _consensus_bucket_has_values(table, "XSharp") and not _consensus_bucket_has_values(
            table, "Prediction Lab"
        ):
            return True
    return False


def _scored_gamelog_ungraded_count(html: str) -> int:
    """Scored moneyline results-table rows whose pick and Result are both blank."""
    empty = {"", "—", "–", "-", "n/a", "N/A"}
    count = 0
    tables = re.findall(
        r'<table\b[^>]*class="[^"]*results-table[^"]*"[\s\S]*?</table>',
        html or "",
        flags=re.I,
    )
    for table in tables:
        if not re.search(r"<th>[^<]*pick</th>", table, flags=re.I):
            continue
        if not re.search(r"<th>\s*Result\s*</th>", table, flags=re.I):
            continue
        for row in re.findall(r"<tr>([\s\S]*?)</tr>", table, flags=re.I):
            cells = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", row, flags=re.I)
            if len(cells) < 5:
                continue
            plain = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in cells]
            score_i = next(
                (i for i, c in enumerate(plain) if re.search(r"\d+\s*[–-]\s*\d+", c)),
                None,
            )
            if score_i is None or score_i + 3 >= len(plain):
                continue
            pick = plain[score_i + 1]
            result = plain[score_i + 3]
            if pick in empty and result in empty:
                count += 1
    return count


def _copy_all_present(html: str) -> bool:
    html = html or ""
    return bool(
        re.search(r'id=["\']pvCopyBtn["\']', html)
        or re.search(r">\s*(?:📋\s*)?Copy All\s*<", html)
    )


def _copy_all_copies_slate(html: str) -> bool:
    """Non-MLB Copy All must walk every date section (or the slate override)."""
    html = html or ""
    if 'id="pl-copy-all-slate"' in html or "pl-copy-all-slate" in html:
        return True
    return bool(
        re.search(
            r"copyVisiblePicks[\s\S]{0,1200}querySelectorAll\(\s*['\"]\\.date-section",
            html,
        )
    )


FOOTER_MUST = (
    "/affiliate",
    "/blog",
    "/plans",
    "/faq",
    "/contact",
    "/privacy",
    "/terms",
    "/ncaaf-picks",
    "/nfl-picks",
    "/soccer-picks",
    "/mlb-picks",
    "/ufc-picks",
    "/tennis-picks",
)


def _header_block(html: str) -> str:
    m = re.search(
        r'(?:<div id="site-chrome-header">|<header\b[^>]*class="[^"]*pl2-header)[\s\S]{0,14000}',
        html or "",
        flags=re.I,
    )
    return m.group(0) if m else (html or "")[:12000]


class ChromeChecker:
    NAME = "chrome"

    def __init__(self, session, base: str, report, CheckResult):
        self.s = session
        self.base = base.rstrip("/")
        self.r = report
        self.CheckResult = CheckResult
        self.timeout = int(os.environ.get("AUDIT_CHROME_TIMEOUT", "45"))

    def add(self, label, status, message, url="", detail=""):
        self.r.add(
            self.CheckResult(
                label=label,
                status=status,
                message=message,
                detail=detail,
                url=url,
                auditor=self.NAME,
            )
        )

    def fetch(self, path: str):
        url = urljoin(self.base + "/", path.lstrip("/"))
        wait = self.timeout
        p = (path or "").split("?")[0]
        if p.endswith("-results") or p.endswith("/results"):
            wait = max(wait, 70)
        if p.rstrip("/").endswith("ncaaf-picks"):
            wait = max(wait, 180)
        try:
            resp = self.s.get(url, timeout=wait, allow_redirects=True)
            return resp.status_code, resp.text or "", url
        except Exception as exc:
            return 0, str(exc), url

    def _ok_status(self, path: str, status: int) -> bool:
        if status == 200:
            return True
        if status in (301, 302) and path.split("?")[0] in ALLOW_302:
            return True
        return False

    def check_homepage_chrome(self, html: str, url: str) -> None:
        hdr = _header_block(html)
        if "pl2-nav-trigger" not in hdr or "Sports" not in hdr:
            self.add(
                "homepage Sports menu",
                FAIL,
                "Header is not the locked Sports / Models / Results chrome",
                url=url,
            )
        else:
            self.add("homepage Sports menu", PASS, "Sports / Models / Results present", url=url)

        missing = []
        for name, href in LOCKED_SPORTS:
            if href not in hdr and not (
                name == "Soccer" and ("/soccer/" in hdr or 'href="/soccer"' in hdr)
            ):
                missing.append(f"{name} ({href})")
        if missing:
            self.add(
                "homepage Sports list",
                FAIL,
                "Sports menu missing: " + ", ".join(missing),
                url=url,
            )
        else:
            self.add(
                "homepage Sports list",
                PASS,
                f"All {len(LOCKED_SPORTS)} sports listed (including UFC)",
                url=url,
            )

        greens = []
        for name, href in LOCKED_SPORTS:
            if name not in IN_SEASON_MUST:
                continue
            pat = re.compile(
                rf'href="{re.escape(href)}"[^>]*class="[^"]*\bin-season\b|'
                rf'class="[^"]*\bin-season\b[^"]*"[^>]*>\s*{re.escape(name)}',
                flags=re.I,
            )
            if pat.search(hdr):
                greens.append(name)
        need = list(IN_SEASON_MUST)
        if set(greens) < set(need):
            self.add(
                "homepage in-season green",
                FAIL,
                "Not green: " + ", ".join(n for n in need if n not in greens),
                url=url,
            )
        else:
            self.add(
                "homepage in-season green",
                PASS,
                "In-season sports highlighted: " + ", ".join(greens),
                url=url,
            )

        if 'href="/blog"' not in hdr or "/plans" not in hdr:
            self.add(
                "homepage Blog/Pricing",
                FAIL,
                "Header must include Blog and Pricing",
                url=url,
            )
        else:
            self.add("homepage Blog/Pricing", PASS, "Blog and Pricing present", url=url)

        missing_f = [p for p in FOOTER_MUST if p not in html]
        if "site-directory-footer" not in html:
            self.add("homepage footer", FAIL, "Missing locked site-directory-footer", url=url)
        elif missing_f:
            self.add(
                "homepage footer links",
                FAIL,
                "Footer missing: " + ", ".join(missing_f),
                url=url,
            )
        else:
            self.add("homepage footer links", PASS, "Affiliate + sport footer links present", url=url)

        low = html.lower()
        hits = [s for s in RULE9 if s in low]
        if hits:
            self.add(
                "homepage Rule 9",
                FAIL,
                "Internal/sandbox copy on the public homepage: " + ", ".join(hits),
                url=url,
            )

    def _discovered_paths(self, html: str) -> list[str]:
        """Every same-site header/footer href, plus the locked list."""
        skip_exact = {
            "/create-portal-session",
            "/logout",
        }
        found = set()
        block = html or ""
        for href in re.findall(r'href="(/[^"#?]+)"', block):
            if href.startswith("//"):
                continue
            if href in skip_exact:
                continue
            if any(href.startswith(p) for p in ("/static/", "/assets/", "/favicon")):
                continue
            found.add(href)
        return sorted(found)

    def check_destinations(self, homepage_html: str = "") -> None:
        """GET every locked header/footer URL. Sequential — live is one worker."""
        seen: set[str] = set()
        paths: list[str] = ["/"]
        paths += [h for _, h in LOCKED_SPORTS]
        paths += list(LOCKED_RESULTS)
        paths += list(LOCKED_MODELS)
        paths += [
            "/blog",
            "/plans",
            "/affiliate",
            "/faq",
            "/contact",
            "/privacy",
            "/terms",
            "/refund-policy",
            "/responsible-gaming",
            "/what-are-ai-sports-betting-picks",
            "/login",
            "/signup",
        ]
        paths += self._discovered_paths(homepage_html)
        unique = []
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            unique.append(path)
        for _sport, _picks, results in TEAM_TEMPLATE_SPORTS + ML_ONLY_SPORTS:
            chart = results + "?view=chart"
            if chart not in seen:
                seen.add(chart)
                unique.append(chart)
        print(f"  Chrome checker: opening {len(unique)} header/footer destinations (one at a time)")
        fails = []
        chrome_fails = []
        for path in unique:
            status, body, url = self.fetch(path)
            label = f"open {path}"
            if status == 0:
                self.add(
                    label,
                    FAIL,
                    f"Won't open — timed out or no response ({self.timeout}s)",
                    url=url,
                    detail=str(body)[:200],
                )
                fails.append(f"{path} timeout")
                continue
            if not self._ok_status(path, status):
                self.add(
                    label,
                    FAIL,
                    f"Won't open — HTTP {status}",
                    url=url,
                )
                fails.append(f"{path} {status}")
                continue
            if status == 200 and (
                (len(body) < 80 and "Page not found" in body)
                or body.strip() == "Page not found"
            ):
                self.add(
                    label,
                    FAIL,
                    "Won't open — catch-all 404 body",
                    url=url,
                )
                fails.append(f"{path} empty-404")
                continue
            if status == 200 and "Internal Server Error" in body and len(body) < 2000:
                self.add(label, FAIL, "Won't open — HTTP 500 error page", url=url)
                fails.append(f"{path} 500-page")
                continue
            self.add(label, PASS, f"HTTP {status} ({len(body):,} bytes)", url=url)
            if status == 200 and self._check_shared_chrome(path, body, url) is False:
                chrome_fails.append(path)

        if chrome_fails:
            self.add(
                "DIGEST mismatched header/footer",
                FAIL,
                "Header/footer do not match the site chrome: " + ", ".join(chrome_fails),
            )
        else:
            self.add(
                "DIGEST mismatched header/footer",
                PASS,
                "Checked pages use the same Sports/Models/Results header and directory footer",
            )

        if fails:
            self.add(
                "DIGEST dead header/footer links",
                FAIL,
                f"{len(fails)} destination(s) do not open: " + ", ".join(fails),
            )
        else:
            self.add(
                "DIGEST dead header/footer links",
                PASS,
                f"All {len(seen)} destinations opened",
            )

    def _check_shared_chrome(self, path: str, html: str, url: str) -> bool | None:
        """Fail when this page's header/footer is not the homepage chrome.

        Returns False on FAIL, True on PASS, None when the page is skipped.
        """
        raw = path or ""
        base = raw.split("?")[0].rstrip("/") or "/"
        is_chart = "view=chart" in raw
        if base in CHROME_LOCK_SKIP and not is_chart:
            return None
        if not html or "<html" not in html.lower():
            return None
        if "pl2-header" not in html and "site-directory-footer" not in html:
            return None
        try:
            from site_chrome import chrome_gaps
        except Exception:
            return None
        gaps = chrome_gaps(html)
        label = f"{path} site chrome"
        if gaps:
            self.add(label, FAIL, "; ".join(gaps), url=url)
            return False
        self.add(label, PASS, "Header/footer match site Sports/Models/Results chrome", url=url)
        return True

    def _check_picks_slate(self, sport: str, picks: str) -> None:
        """Fail a live sport whose predictions page has no cards."""
        if sport in ("Tennis", "UFC", "Golf"):
            return
        st, body, url = self.fetch(picks)
        if (st != 200 or not _has_pick_cards(body)) and sport not in OFFSEASON_OK_EMPTY:
            st, body, url = self.fetch(picks)
        if st != 200:
            self.add(
                f"{sport} picks slate",
                FAIL,
                f"{picks} won't open — cannot confirm picks",
                url=url,
            )
            return
        if _has_pick_cards(body):
            self.add(
                f"{sport} picks slate",
                PASS,
                "Predictions page has pick cards",
                url=url,
            )
            if sport != "MLB" and not _face_pl_ml_has_values(body):
                self.add(
                    f"{sport} Prediction Lab moneyline",
                    FAIL,
                    "Pick cards are missing Prediction Lab moneylines (all —)",
                    url=url,
                )
            elif sport != "MLB":
                self.add(
                    f"{sport} Prediction Lab moneyline",
                    PASS,
                    "Pick cards show Prediction Lab moneylines",
                    url=url,
                )
            if sport not in ("MLB", "Tennis", "UFC"):
                junk = picks_placeholder_issues(body)
                if junk:
                    self.add(
                        f"{sport} placeholder picks",
                        FAIL,
                        "; ".join(junk),
                        url=url,
                    )
                else:
                    self.add(
                        f"{sport} placeholder picks",
                        PASS,
                        "Face win% / Pick Confidence / Books ML are not placeholders",
                        url=url,
                    )
            self._check_picks_clock_and_logos(sport, body, url)
            return
        if sport in OFFSEASON_OK_EMPTY or "is in the off-season" in (body or ""):
            return
        self.add(
            f"{sport} picks slate",
            FAIL,
            "Predictions page has no pick cards (blank slate on a live sport)",
            url=url,
        )

    def _check_picks_clock_and_logos(self, sport: str, body: str, url: str) -> None:
        clock = picks_clock_issues(body)
        if clock:
            self.add(f"{sport} kickoff time", FAIL, "; ".join(clock), url=url)
        else:
            self.add(
                f"{sport} kickoff time",
                PASS,
                "Pick cards show a real kickoff time",
                url=url,
            )
        if sport in ("MLB", "UFC"):
            return
        logos = picks_logo_issues(body)
        if logos:
            self.add(f"{sport} team logos", FAIL, "; ".join(logos), url=url)
        else:
            self.add(
                f"{sport} team logos",
                PASS,
                "Pick cards have team logos (not the site fallback)",
                url=url,
            )

    def _check_h2h(self, sport: str, picks: str, *, required: bool) -> None:
        st, body, url = self.fetch(picks)
        if st != 200:
            if required:
                self.add(
                    f"{sport} H2H Last 10",
                    FAIL,
                    f"{picks} won't open — cannot confirm H2H Last 10",
                    url=url,
                )
            return
        has_label = H2H_LAST10 in (body or "")
        if not required and not has_label:
            return
        if not _has_pick_cards(body) and not has_label:
            return
        gaps = h2h_gap_issues(body) if required and sport != "MLB" else []
        if not has_label or not _h2h_has_values(body):
            self.add(
                f"{sport} H2H Last 10",
                FAIL,
                "Picks cards are missing H2H Last 10 values (label only, all —, or all 0)",
                url=url,
            )
        elif gaps:
            self.add(
                f"{sport} H2H Last 10",
                FAIL,
                "; ".join(gaps),
                url=url,
            )
        elif required and sport != "MLB" and not _h2h_on_card_face(body):
            self.add(
                f"{sport} H2H Last 10",
                FAIL,
                "H2H Last 10 exists in page HTML but is not on the card face (only inside details)",
                url=url,
            )
        else:
            self.add(
                f"{sport} H2H Last 10",
                PASS,
                "H2H Last 10 present on picks cards",
                url=url,
            )

    def _check_xsharp_values(self, sport: str, html: str, url: str, label: str) -> None:
        if not _xsharp_rows_present(html):
            return
        if _consensus_xsharp_has_values(html):
            self.add(label, PASS, "Consensus chart includes XSharp values", url=url)
        else:
            self.add(
                label,
                FAIL,
                "XSharp consensus row is present but values are only — or 0-0",
                url=url,
            )

    def _check_pl_consensus(
        self,
        sport: str,
        html: str,
        url: str,
        label: str,
        *,
        expected_html: str | None = None,
    ) -> None:
        if not re.search(r'class="bucket">\s*Prediction Lab\s*<', html or "", flags=re.I):
            return
        if not re.search(r'class="bucket">\s*XSharp\s*<', html or "", flags=re.I):
            return
        if _pl_consensus_missing_where_xsharp_exists(html, expected_html):
            self.add(
                label,
                FAIL,
                "Prediction Lab consensus is — / 0-0 while spread/totals records exist "
                "(Books or Totals table)",
                url=url,
            )
        else:
            self.add(
                label,
                PASS,
                "Prediction Lab consensus has values wherever records exist",
                url=url,
            )

    def _check_ungraded_gamelog(self, sport: str, html: str, url: str, label: str) -> None:
        if "results-table" not in (html or ""):
            return
        n = _scored_gamelog_ungraded_count(html)
        if n:
            self.add(
                label,
                FAIL,
                f"{n} scored game-log row(s) have no pick / result (ungraded finals)",
                url=url,
            )
        else:
            self.add(label, PASS, "Scored game-log rows have a pick and result", url=url)

    def _check_results_template(
        self,
        sport: str,
        results: str,
        *,
        require_both_charts: bool,
        require_chart_view: bool,
    ) -> None:
        rst, rhtml, rurl = self.fetch(results)
        if sport in OFFSEASON_OK_EMPTY:
            if rst != 200:
                self.add(
                    f"{sport} results page",
                    FAIL,
                    f"{results} won't open",
                    url=rurl,
                )
            else:
                self.add(
                    f"{sport} results charts",
                    PASS,
                    "Offseason — in-season consensus math is not required",
                    url=rurl,
                )
            if sport == "NHL":
                cst, chtml, curl = self.fetch(f"{results}?view=chart")
                issues = nhl_chart_view_missing_issues(
                    chtml if cst == 200 else "", rhtml
                )
                if issues:
                    self.add(
                        "NHL results chart view",
                        FAIL,
                        "; ".join(issues),
                        url=curl,
                    )
                else:
                    self.add(
                        "NHL results chart view",
                        PASS,
                        "NHL chart view exists and is not the cards page",
                        url=curl,
                    )
            if sport == "NBA" and rst == 200:
                gaps = nba_last_season_gap_issues(rhtml)
                if gaps:
                    self.add(
                        "NBA last-season results",
                        FAIL,
                        "; ".join(gaps),
                        url=rurl,
                    )
                else:
                    self.add(
                        "NBA last-season results",
                        PASS,
                        "NBA last-season boards have records",
                        url=rurl,
                    )
            return
        if rst != 200:
            self.add(
                f"{sport} results charts",
                FAIL,
                f"{results} won't open — cannot confirm consensus / chart view",
                url=rurl,
            )
            return
        if sport != "Soccer":
            tpl = team_results_template_issues(rhtml, sport)
            if tpl:
                self.add(
                    f"{sport} results template",
                    FAIL,
                    "; ".join(tpl),
                    url=rurl,
                )
            else:
                self.add(
                    f"{sport} results template",
                    PASS,
                    f"{sport} results uses the shared daily team-sports template",
                    url=rurl,
                )
        if sport == "NFL":
            stale = nfl_stale_season_week_issues(rhtml)
            if stale:
                self.add(
                    "NFL results current week",
                    FAIL,
                    "; ".join(stale),
                    url=rurl,
                )
            else:
                self.add(
                    "NFL results current week",
                    PASS,
                    "NFL weekly board is not stuck on last season's playoffs",
                    url=rurl,
                )
            stale_perf = nfl_stale_season_perf_issues(rhtml)
            if stale_perf:
                self.add(
                    "NFL Season Performance this season",
                    FAIL,
                    "; ".join(stale_perf),
                    url=rurl,
                )
            else:
                self.add(
                    "NFL Season Performance this season",
                    PASS,
                    "NFL Season Performance is this season, not last year's snapshot",
                    url=rurl,
                )
            missing_eff = nfl_missing_efficiency_issues(rhtml)
            if missing_eff:
                self.add(
                    "NFL results Efficiency",
                    FAIL,
                    "; ".join(missing_eff),
                    url=rurl,
                )
            else:
                self.add(
                    "NFL results Efficiency",
                    PASS,
                    "NFL results include Efficiency with the other moneyline models",
                    url=rurl,
                )
            spread_cards = nfl_spread_result_card_issues(rhtml)
            if spread_cards:
                self.add(
                    "NFL spread result cards",
                    FAIL,
                    "; ".join(spread_cards),
                    url=rurl,
                )
            else:
                self.add(
                    "NFL spread result cards",
                    PASS,
                    "NFL Spread tab has result cards",
                    url=rurl,
                )
        if sport == "NCAAF":
            date_issues = ncaaf_results_date_nav_issues(rhtml)
            if date_issues:
                self.add(
                    "NCAAF results date nav",
                    FAIL,
                    "; ".join(date_issues),
                    url=rurl,
                )
            else:
                self.add(
                    "NCAAF results date nav",
                    PASS,
                    "Date strip is at the top with multiple days",
                    url=rurl,
                )
        chart_href = f"{results}?view=chart"
        started = (
            require_chart_view
            or "view=chart" in rhtml
            or ML_CHART in rhtml
            or RL_CHART in rhtml
        )
        if require_chart_view or started:
            if not _has_chart_view_control(rhtml):
                self.add(
                    f"{sport} results chart view",
                    FAIL,
                    "Results page has no Cards|Chart / view=chart control",
                    url=rurl,
                )
        if require_both_charts:
            missing = _missing_results_charts(rhtml)
            if missing:
                self.add(
                    f"{sport} results charts",
                    FAIL,
                    "Missing: " + ", ".join(missing),
                    url=rurl,
                )
            else:
                self.add(
                    f"{sport} results charts",
                    PASS,
                    "Consensus + PL vs sportsbook charts present",
                    url=rurl,
                )
        elif require_chart_view or ML_CHART in rhtml:
            if ML_CHART not in rhtml:
                self.add(
                    f"{sport} results charts",
                    FAIL,
                    "Missing Consensus Based Betting Records",
                    url=rurl,
                )
            else:
                self.add(
                    f"{sport} results charts",
                    PASS,
                    "Consensus Based Betting Records present",
                    url=rurl,
                )
        self._check_xsharp_values(sport, rhtml, rurl, f"{sport} consensus XSharp")
        if sport != "MLB":
            self._check_pl_consensus(
                sport, rhtml, rurl, f"{sport} consensus Prediction Lab", expected_html=rhtml
            )
            self._check_ungraded_gamelog(sport, rhtml, rurl, f"{sport} results game log")
            panel_issues = six_model_chart_issues(rhtml, sport)
            if panel_issues:
                self.add(
                    f"{sport} results chart panel",
                    FAIL,
                    "; ".join(panel_issues),
                    url=rurl,
                )
            math_issues = results_math_issues(rhtml)
            if math_issues:
                self.add(
                    f"{sport} results math",
                    FAIL,
                    "; ".join(math_issues),
                    url=rurl,
                )
            elif "Consensus Based Betting Records" in rhtml:
                self.add(
                    f"{sport} results math",
                    PASS,
                    "Consensus / Past 7 match the cards on this page",
                    url=rurl,
                )

        if not (require_chart_view or started):
            return
        cst, chtml, curl = self.fetch(chart_href)
        if cst != 200:
            self.add(
                f"{sport} results chart view",
                FAIL,
                f"{chart_href} won't open",
                url=curl,
            )
            return
        if require_both_charts:
            missing_v = _missing_results_charts(chtml)
            if missing_v:
                self.add(
                    f"{sport} results chart view",
                    FAIL,
                    "Chart view is missing: " + ", ".join(missing_v),
                    url=curl,
                )
            else:
                self.add(
                    f"{sport} results chart view",
                    PASS,
                    "Chart view has consensus + PL vs sportsbook",
                    url=curl,
                )
        elif ML_CHART not in chtml:
            self.add(
                f"{sport} results chart view",
                FAIL,
                "Chart view is missing Consensus Based Betting Records",
                url=curl,
            )
        else:
            self.add(
                f"{sport} results chart view",
                PASS,
                "Chart view has Consensus Based Betting Records",
                url=curl,
            )
        self._check_xsharp_values(
            sport, chtml, curl, f"{sport} chart-view consensus XSharp"
        )
        if sport == "MLB":
            xs = mlb_xsharp_totals_issues(rhtml, chtml)
            if xs:
                self.add(
                    "MLB XSharp totals",
                    FAIL,
                    "; ".join(xs),
                    url=curl,
                )
            else:
                self.add(
                    "MLB XSharp totals",
                    PASS,
                    "MLB cards + chart have Prediction Lab · XSharp — Totals",
                    url=curl,
                )
        if sport != "MLB":
            self._check_pl_consensus(
                sport,
                chtml,
                curl,
                f"{sport} chart-view consensus Prediction Lab",
                expected_html=rhtml,
            )
            self._check_ungraded_gamelog(
                sport, chtml, curl, f"{sport} chart-view game log"
            )
            chart_panel = six_model_chart_issues(chtml, sport)
            if chart_panel:
                self.add(
                    f"{sport} chart-view panel",
                    FAIL,
                    "; ".join(chart_panel),
                    url=curl,
                )
            chart_math = results_math_issues(chtml, cards_html=rhtml)
            if chart_math:
                self.add(
                    f"{sport} chart-view results math",
                    FAIL,
                    "; ".join(chart_math),
                    url=curl,
                )
        if sport != "Soccer":
            chart_tpl = team_chart_template_issues(chtml, sport)
            if chart_tpl:
                self.add(
                    f"{sport} chart template",
                    FAIL,
                    "; ".join(chart_tpl),
                    url=curl,
                )
            else:
                self.add(
                    f"{sport} chart template",
                    PASS,
                    f"{sport} chart is the shared consensus table",
                    url=curl,
                )
        if sport == "NFL":
            mlb_shape = nfl_chart_not_mlb_issues(chtml)
            if mlb_shape:
                self.add(
                    "NFL chart vs MLB template",
                    FAIL,
                    "; ".join(mlb_shape),
                    url=curl,
                )
            else:
                self.add(
                    "NFL chart vs MLB template",
                    PASS,
                    "NFL chart is the MLB 6/6 dissent table",
                    url=curl,
                )
            same = nfl_chart_same_as_cards_issues(rhtml, chtml)
            if same:
                self.add(
                    "NFL cards vs chart",
                    FAIL,
                    "; ".join(same),
                    url=curl,
                )
            else:
                self.add(
                    "NFL cards vs chart",
                    PASS,
                    "NFL chart view is not the same as the week board",
                    url=curl,
                )
            windows = nfl_chart_window_tally_issues(chtml)
            if windows:
                self.add(
                    "NFL chart Last Night / Last 7",
                    FAIL,
                    "; ".join(windows),
                    url=curl,
                )
            else:
                self.add(
                    "NFL chart Last Night / Last 7",
                    PASS,
                    "NFL chart has Last Night / Last 7 / Season model cards",
                    url=curl,
                )
            ast, atext, aurl = self.fetch("/nfl/api/picks")
            api_body = None
            if ast == 200 and atext:
                try:
                    import json as _json
                    api_body = _json.loads(atext)
                except Exception:
                    api_body = None
            api = nfl_chart_api_issues(api_body)
            if ast != 200:
                api = [f"NFL chart API HTTP {ast}"] + api
            if api:
                self.add(
                    "NFL chart API",
                    FAIL,
                    "; ".join(api),
                    url=aurl,
                )
            else:
                self.add(
                    "NFL chart API",
                    PASS,
                    "/nfl/api/picks hydrates Moneyline | Spread | Totals",
                    url=aurl,
                )

    def _check_copy_all(self, sport: str, picks: str) -> None:
        """In-season team picks with cards must have Copy All.

        MLB: button only (locked copy JS). Tennis/UFC: do not require.
        Other team sports: button plus all-dates / all-markets slate copy.
        """
        if sport in ("Tennis", "UFC", "Golf"):
            return
        st, body, url = self.fetch(picks)
        if st != 200 or not _has_pick_cards(body):
            return
        if not _copy_all_present(body):
            self.add(
                f"{sport} Copy All",
                FAIL,
                "Predictions page has cards but no Copy All button",
                url=url,
            )
            return
        if sport == "MLB":
            self.add(
                f"{sport} Copy All",
                PASS,
                "Copy All button present",
                url=url,
            )
            return
        if not _copy_all_copies_slate(body):
            self.add(
                f"{sport} Copy All",
                FAIL,
                "Copy All is present but only copies the visible date / moneyline, not the full slate",
                url=url,
            )
            return
        self.add(
            f"{sport} Copy All",
            PASS,
            "Copy All copies every loaded game (moneyline + spread + totals)",
            url=url,
        )

    def check_all_sport_template_gaps(self) -> None:
        """Every sport: H2H values, consensus, PL vs sportsbook, chart view."""
        print(
            "  Chrome checker: all-sports H2H + consensus + PL vs sportsbook + chart view + Copy All"
        )
        for sport, picks, results in TEAM_TEMPLATE_SPORTS:
            self._check_picks_slate(sport, picks)
            # Soccer cards use PL-xG, not H2H Last 10.
            self._check_h2h(sport, picks, required=sport != "Soccer")
            self._check_copy_all(sport, picks)
            if sport != "Soccer":
                pst, phtml, purl = self.fetch(picks)
                if pst == 200:
                    tpl = team_picks_template_issues(phtml, sport)
                    if tpl:
                        self.add(
                            f"{sport} picks template",
                            FAIL,
                            "; ".join(tpl),
                            url=purl,
                        )
                    else:
                        self.add(
                            f"{sport} picks template",
                            PASS,
                            f"{sport} picks uses the shared team-sports cards",
                            url=purl,
                        )
            if sport in ("NCAAF", "NFL", "CFL", "Soccer", "MLB"):
                pst, phtml, purl = self.fetch(picks)
                if pst == 200 and _has_pick_cards(phtml):
                    miss = card_missing_model_value_issues(phtml, sport)
                    miss.extend(card_blank_market_line_issues(phtml, sport))
                    if miss:
                        self.add(
                            f"{sport} missing models/values",
                            FAIL,
                            "; ".join(miss),
                            url=purl,
                        )
                    else:
                        self.add(
                            f"{sport} missing models/values",
                            PASS,
                            f"{sport} cards have model boxes with values",
                            url=purl,
                        )
            if sport == "WNBA":
                rst, rhtml, rurl = self.fetch(results)
                if rst == 200 and (
                    rhtml.count("data-pick-card") >= 3 or "H2H Last 10" in rhtml
                ):
                    gaps = h2h_gap_issues(rhtml)
                    if gaps or rhtml.count("H2H Last 10") < 3:
                        self.add(
                            "WNBA results H2H Last 10",
                            FAIL,
                            "; ".join(gaps)
                            if gaps
                            else "WNBA results cards are missing H2H Last 10",
                            url=rurl,
                        )
                    else:
                        self.add(
                            "WNBA results H2H Last 10",
                            PASS,
                            "WNBA results cards have H2H Last 10",
                            url=rurl,
                        )
            self._check_results_template(
                sport, results, require_both_charts=True, require_chart_view=True
            )
        for sport, picks, results in ML_ONLY_SPORTS:
            st, body, url = self.fetch(picks)
            if st == 200 and _has_pick_cards(body):
                self._check_picks_clock_and_logos(sport, body, url)
            if sport == "Golf" and st == 200:
                title = re.search(r"<title>([^<]+)", body or "", flags=re.I)
                h1 = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", body or "", flags=re.I)
                blob = (
                    (title.group(1) if title else "")
                    + " "
                    + re.sub(r"<[^>]+>", "", h1.group(1) if h1 else "")
                )
                if re.search(r"\bATP Tour\b", blob):
                    self.add(
                        "Golf picks title",
                        FAIL,
                        "Golf picks is titled ATP Tour (tennis name leaked onto golf)",
                        url=url,
                    )
            self._check_h2h(sport, picks, required=False)
            self._check_results_template(
                sport,
                results,
                require_both_charts=False,
                require_chart_view=sport in ("Tennis", "UFC"),
            )
            if sport == "Tennis":
                rst, rhtml, rurl = self.fetch(results)
                cst, chtml, curl = self.fetch(f"{results}?view=chart")
                issues = tennis_chart_same_as_cards_issues(
                    rhtml if rst == 200 else "",
                    chtml if cst == 200 else "",
                )
                if issues:
                    self.add(
                        "Tennis results chart vs cards",
                        FAIL,
                        "; ".join(issues),
                        url=curl or rurl,
                    )
                else:
                    self.add(
                        "Tennis results chart vs cards",
                        PASS,
                        "Tennis chart view is a real chart, not the cards page",
                        url=curl,
                    )

    def run(self):
        print("  Chrome checker: locked header/footer + actually open every link")
        status, html, url = self.fetch("/")
        if status != 200 or len(html) < 800:
            self.add("homepage", FAIL, f"Homepage failed ({status})", url=url)
            return
        self.check_homepage_chrome(html, url)
        self.check_destinations(html)
        self.check_all_sport_template_gaps()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Open every Sports / Results / Blog / Affiliate URL and fail if dead"
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("AUDIT_BASE_URL", "https://predictionlab.io"),
    )
    args = parser.parse_args(argv)

    class _R:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)
            mark = {"PASS": "OK", "FAIL": "FAIL", "WARN": "WARN"}.get(row.status, row.status)
            print(f"  [{mark}] {row.label}: {row.message}")

    class _C:
        def __init__(self, label, status, message, detail="", url="", auditor="chrome"):
            self.label = label
            self.status = status
            self.message = message
            self.detail = detail
            self.url = url
            self.auditor = auditor

    session = requests.Session()
    session.headers["User-Agent"] = "predictionlab-chrome-checker"
    report = _R()
    ChromeChecker(session, args.url, report, _C).run()
    fails = sum(1 for r in report.rows if r.status == FAIL)
    print(f"\nChrome checker: {len(report.rows) - fails} pass / {fails} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
