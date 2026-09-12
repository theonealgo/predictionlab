#!/usr/bin/env python3
"""Soccer page checker — fail on chrome, league picker, load, and chart gaps.

Run:
  .venv/bin/python qa/soccer_checker.py --url http://127.0.0.1:5081
  .venv/bin/python qa/site_checker.py --ship --url http://127.0.0.1:5081
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

_QA = Path(__file__).resolve().parent
_ROOT = _QA.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_QA) not in sys.path:
    sys.path.insert(0, str(_QA))

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

LOCKED_PICKS = (
    "/nba-picks",
    "/nfl-picks",
    "/mlb-picks",
    "/nhl-picks",
    "/soccer-picks",
    "/ncaab-picks",
    "/ncaaf-picks",
    "/ncaaw-picks",
    "/wnba-picks",
    "/cfl-picks",
    "/tennis-picks",
    "/ufc-picks",
    "/golf-picks",
)

from chart_shape import (  # noqa: E402
    ML_CHART,
    PL_VS_BOOKS,
    has_three_cards_per_row,
    missing_signed_off_charts,
    soccer_spread_books_missing,
)

RL_CHART = PL_VS_BOOKS
OU_CHART = "Prediction Lab · XSharp — Totals"


class SoccerChecker:
    NAME = "soccer"

    def __init__(self, session, base: str, report, CheckResult):
        self.s = session
        self.base = base.rstrip("/")
        self.r = report
        self.CheckResult = CheckResult
        self.timeout = int(os.environ.get("AUDIT_SOCCER_TIMEOUT", "90"))

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
        try:
            resp = self.s.get(url, timeout=self.timeout, allow_redirects=True)
            return resp.status_code, resp.text or "", url
        except Exception as exc:
            return 0, "", url + f" ({exc})"

    def _header(self, html: str) -> str:
        m = re.search(
            r'<div id="site-chrome-header">[\s\S]*?</div>\s*<script>|'
            r'<header\b[^>]*class="[^"]*pl2-header[^"]*"[\s\S]{0,8000}',
            html,
            flags=re.I,
        )
        return m.group(0) if m else html[:12000]

    def check_locked_chrome(self, html: str, url: str, which: str) -> None:
        hdr = self._header(html)
        missing = [p for p in LOCKED_PICKS if p not in hdr and p.replace("-picks", "/") not in hdr]
        # Hub may rewrite /soccer-picks → /soccer/
        if "/soccer-picks" in missing and ("/soccer/" in hdr or 'href="/soccer"' in hdr):
            missing = [p for p in missing if p != "/soccer-picks"]
        if missing:
            self.add(
                f"soccer {which} header sports",
                FAIL,
                "Locked header is missing sports: " + ", ".join(missing),
                url=url,
            )
        else:
            self.add(
                f"soccer {which} header sports",
                PASS,
                "Locked Sports menu lists all sports",
                url=url,
            )

        if "pl2-nav-trigger" not in hdr and "Sports" not in hdr:
            self.add(
                f"soccer {which} header Sports menu",
                FAIL,
                "Header is not the locked Sports/Models/Results chrome",
                url=url,
            )
        greens = len(re.findall(r'class="[^"]*\bin-season\b', hdr, flags=re.I))
        soccer_green = bool(
            re.search(
                r'href="[^"]*soccer[^"]*"[^>]*class="[^"]*\bin-season\b|'
                r'class="[^"]*\bin-season\b[^"]*"[^>]*>\s*Soccer',
                hdr,
                flags=re.I,
            )
        )
        if greens < 4 or not soccer_green:
            self.add(
                f"soccer {which} in-season green",
                FAIL,
                f"Picks menu does not green-highlight in-season sports "
                f"(in-season links={greens}, soccer_green={soccer_green})",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} in-season green",
                PASS,
                f"{greens} in-season sports highlighted, including Soccer",
                url=url,
            )

        if 'href="/blog"' not in hdr or "/plans" not in hdr:
            self.add(
                f"soccer {which} header Blog/Pricing",
                FAIL,
                "Locked header must include Blog and Pricing",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} header Blog/Pricing",
                PASS,
                "Blog and Pricing present",
                url=url,
            )

        try:
            from site_chrome import chrome_gaps

            gaps = chrome_gaps(html)
        except Exception:
            gaps = []
        if gaps:
            self.add(
                f"soccer {which} header/footer match",
                FAIL,
                "Header/footer do not match the site chrome: " + "; ".join(gaps),
                url=url,
            )
        else:
            self.add(
                f"soccer {which} header/footer match",
                PASS,
                "Sports/Models/Results header and directory footer match the site",
                url=url,
            )
        if "site-directory-footer" not in html:
            self.add(
                f"soccer {which} footer",
                FAIL,
                "Missing locked site-directory-footer",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} footer",
                PASS,
                "Locked directory footer present",
                url=url,
            )

    def check_league_picker(self, html: str, url: str, which: str) -> None:
        if 'id="league-controls"' not in html or 'id="league"' not in html:
            self.add(
                f"soccer {which} league picker",
                FAIL,
                "League dropdown is missing",
                url=url,
            )
            return
        load_ok = "soccer-league-load" in html
        if not load_ok:
            self.add(
                f"soccer {which} load button",
                FAIL,
                "Load / Load results button is missing",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} load button",
                PASS,
                "Load button present",
                url=url,
            )
        if re.search(
            r"\.soccer-league-native\s*\{[^}]*pointer-events\s*:\s*none",
            html,
            flags=re.I,
        ) or re.search(
            r'<select[^>]*\bid=["\']league["\'][^>]*soccer-league-native',
            html,
            flags=re.I,
        ):
            self.add(
                f"soccer {which} league dropdown usable",
                FAIL,
                "League <select> is hidden (custom overlay / pointer-events:none)",
                url=url,
            )
        elif "filterLeagues" not in html:
            self.add(
                f"soccer {which} league dropdown usable",
                FAIL,
                "Continent change does not filter the league list in place",
                url=url,
            )
        elif "go(true)" in html:
            self.add(
                f"soccer {which} league dropdown usable",
                FAIL,
                "Continent change reloads the page instead of filtering the league list",
                url=url,
            )
        elif "addEventListener('change'" not in html and 'addEventListener("change"' not in html:
            self.add(
                f"soccer {which} league dropdown usable",
                FAIL,
                "Continent/League dropdowns have no change handler — selecting does nothing",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} league dropdown usable",
                PASS,
                "Continent filters the league list; Load/league fetches the page",
                url=url,
            )
        league_sel = re.search(
            r'<select[^>]*\bid=["\']league["\'][^>]*>([\s\S]*?)</select>',
            html,
            flags=re.I,
        )
        league_opts = (
            len(re.findall(r"<option\b", league_sel.group(1), flags=re.I))
            if league_sel
            else 0
        )
        try:
            from soccer_league_catalog import (
                ESPN_BROWSE_HEADINGS,
                ESPN_BROWSE_LEAGUES,
                missing_espn_browse_headings,
                missing_espn_browse_leagues,
            )
        except Exception:
            ESPN_BROWSE_HEADINGS = ()
            ESPN_BROWSE_LEAGUES = ()
            missing_espn_browse_headings = None
            missing_espn_browse_leagues = None
        missing_h = (
            missing_espn_browse_headings(html) if missing_espn_browse_headings else []
        )
        if missing_h:
            self.add(
                f"soccer {which} league headings",
                FAIL,
                "Continent dropdown is missing ESPN headings: "
                + ", ".join(missing_h[:8])
                + (f" (+{len(missing_h) - 8} more)" if len(missing_h) > 8 else ""),
                url=url,
            )
        elif ESPN_BROWSE_HEADINGS:
            self.add(
                f"soccer {which} league headings",
                PASS,
                f"{len(ESPN_BROWSE_HEADINGS)} ESPN headings present",
                url=url,
            )
        missing = (
            missing_espn_browse_leagues(html) if missing_espn_browse_leagues else []
        )
        if missing:
            self.add(
                f"soccer {which} league catalog",
                FAIL,
                f"{len(missing)} ESPN league(s) missing from the dropdown: "
                + ", ".join(missing),
                url=url,
                detail="\n".join(missing),
            )
        elif which == "picks" and league_opts < 40:
            self.add(
                f"soccer {which} league catalog",
                FAIL,
                f"League dropdown only has {league_opts} option(s) — full catalog is missing",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} league catalog",
                PASS,
                f"{league_opts} league option(s); all {len(ESPN_BROWSE_LEAGUES)} "
                "ESPN browse leagues present",
                url=url,
            )
        in_season = len(re.findall(r'data-in-season="1"', html))
        live = len(re.findall(r'data-live="1"', html))
        green_css = (
            ".soccer-dd-opt.in-season" in html
            or ".soccer-dd-opt[data-in-season" in html
        ) and "#059669" in html
        if 'data-region=' not in html and which == "picks":
            self.add(
                f"soccer {which} continent filter",
                FAIL,
                "League options have no data-region — Africa cannot filter the second list",
                url=url,
            )
        elif 'data-region="africa"' not in html and which == "picks":
            self.add(
                f"soccer {which} continent filter",
                FAIL,
                "Africa leagues are missing data-region=africa",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} continent filter",
                PASS,
                "League options carry continent keys for in-place filtering",
                url=url,
            )
        unpainted = []
        try:
            from soccer_league_catalog import missing_in_season_green_leagues

            unpainted = missing_in_season_green_leagues(html)
        except Exception:
            unpainted = []
        if in_season < 1 and live < 1 and which == "picks":
            self.add(
                f"soccer {which} in-season green",
                FAIL,
                "No in-season leagues marked (data-in-season=1) — nothing to highlight green",
                url=url,
            )
        elif not green_css:
            self.add(
                f"soccer {which} in-season green",
                FAIL,
                "In-season leagues are not painted green on the custom menu "
                "(native <option> color is ignored on Mac)",
                url=url,
            )
        elif unpainted:
            self.add(
                f"soccer {which} in-season green",
                FAIL,
                f"{len(unpainted)} in-season league(s) are not green: "
                + ", ".join(unpainted),
                url=url,
                detail="\n".join(unpainted),
            )
        else:
            self.add(
                f"soccer {which} in-season green",
                PASS,
                f"{in_season or live} in-season league(s) painted green in the menu",
                url=url,
            )
        if 'id="soccer-week-nav"' not in html:
            self.add(
                f"soccer {which} week nav",
                FAIL,
                "Week nav is missing — picks and results must be one week per page",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} week nav",
                PASS,
                "Week nav (prev / this week / next) is on the page",
                url=url,
            )
        dates = sorted(set(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html)))
        if dates:
            try:
                from datetime import date as _date

                first = _date.fromisoformat(dates[0])
                last = _date.fromisoformat(dates[-1])
                span = (last - first).days
            except Exception:
                span = 99
            if span > 6:
                self.add(
                    f"soccer {which} week pages",
                    FAIL,
                    f"Card dates span {span} days ({dates[0]}–{dates[-1]}) — weeks must stay separate",
                    url=url,
                )
            else:
                self.add(
                    f"soccer {which} week pages",
                    PASS,
                    f"{len(dates)} date(s) in one week ({dates[0]}–{dates[-1]})",
                    url=url,
                )
        elif which == "picks":
            self.add(
                "soccer picks date nav",
                FAIL,
                "Picks page has no date sections for this week",
                url=url,
            )

    def check_load_works(self) -> None:
        st_all, all_html, url_all = self.fetch("/soccer/")
        st_epl, epl_html, url_epl = self.fetch("/soccer/?league=english-premier-league")
        if st_all != 200 or st_epl != 200:
            self.add(
                "soccer load filter",
                FAIL,
                f"Load URLs failed (all={st_all}, epl={st_epl})",
                url=url_epl,
            )
            return
        epl_n = epl_html.count('data-league="English Premier League"')
        all_n = all_html.count("data-pick-card")
        other = epl_html.count('data-league="Dutch Eredivisie"')
        if epl_n < 3:
            self.add(
                "soccer load filter",
                FAIL,
                f"Load ?league=english-premier-league did not keep EPL cards (epl={epl_n})",
                url=url_epl,
            )
        elif other > epl_n:
            self.add(
                "soccer load filter",
                FAIL,
                f"Load still shows other leagues as the majority (EPL={epl_n}, Eredivisie={other})",
                url=url_epl,
            )
        else:
            self.add(
                "soccer load filter",
                PASS,
                f"League load filters cards (EPL={epl_n}, all-page cards={all_n})",
                url=url_epl,
            )

        st_r, rhtml, url_r = self.fetch("/soccer/results?league=english-premier-league")
        if st_r != 200 or "league-controls" not in rhtml:
            self.add(
                "soccer results load",
                FAIL,
                "Load results URL did not return a league picker page",
                url=url_r,
            )
        else:
            self.add(
                "soccer results load",
                PASS,
                "Results Load URL returns 200 with league picker",
                url=url_r,
            )

    def check_every_espn_league_page(self) -> None:
        """Each ESPN browse league must have its own picks page and results page."""
        try:
            from soccer_league_catalog import (
                espn_browse_league_pages,
                league_page_selection_issues,
            )
        except Exception as exc:
            self.add(
                "soccer ESPN league pages",
                FAIL,
                f"Could not load ESPN browse league list: {exc}",
            )
            return
        pages = espn_browse_league_pages()
        missing_map = [p["espn"] for p in pages if not p.get("slug")]
        if missing_map:
            self.add(
                "soccer ESPN league slugs",
                FAIL,
                "ESPN leagues have no site slug / catalog row: "
                + ", ".join(missing_map),
                detail="\n".join(missing_map),
            )
        else:
            self.add(
                "soccer ESPN league slugs",
                PASS,
                f"{len(pages)} ESPN browse leagues map to a site slug",
            )
        miss_picks: list[str] = []
        miss_results: list[str] = []
        for spec in pages:
            espn = spec["espn"]
            slug = spec.get("slug") or ""
            catalog = spec.get("catalog") or ""
            if not slug:
                miss_picks.append(f"{espn}: no slug")
                miss_results.append(f"{espn}: no slug")
                continue
            for which, path, bucket in (
                ("picks", spec["picks"], miss_picks),
                ("results", spec["results"], miss_results),
            ):
                st, html, url = self.fetch(path)
                if st != 200 or len(html) < 800:
                    bucket.append(f"{espn}: {which} HTTP {st or 'timeout'} ({url})")
                    continue
                issues = league_page_selection_issues(
                    html, slug=slug, espn_name=espn, catalog_name=catalog
                )
                bucket.extend(issues)

        if miss_picks:
            self.add(
                "soccer ESPN league picks pages",
                FAIL,
                f"{len(miss_picks)} ESPN league(s) missing their own prediction page: "
                + ", ".join(miss_picks),
                detail="\n".join(miss_picks),
            )
        else:
            self.add(
                "soccer ESPN league picks pages",
                PASS,
                f"{len(pages)} ESPN leagues have their own /soccer-picks?league= page",
            )
        if miss_results:
            self.add(
                "soccer ESPN league results pages",
                FAIL,
                f"{len(miss_results)} ESPN league(s) missing their own results page: "
                + ", ".join(miss_results),
                detail="\n".join(miss_results),
            )
        else:
            self.add(
                "soccer ESPN league results pages",
                PASS,
                f"{len(pages)} ESPN leagues have their own /soccer-results?league= page",
            )

    def check_three_card_row(self, html: str, url: str, which: str) -> None:
        cards = html.count("data-pick-card") or html.count("pick-card")
        if cards < 3:
            return
        if has_three_cards_per_row(html):
            self.add(
                f"soccer {which} 3 cards per row",
                PASS,
                "Desktop games-grid is 3 columns",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} 3 cards per row",
                FAIL,
                "Pick/results cards are not 3 per row (missing games-grid 3-column CSS)",
                url=url,
            )

    def check_spread_books(self, html: str, url: str, which: str) -> None:
        missing = soccer_spread_books_missing(html)
        if missing:
            self.add(
                f"soccer {which} spread books",
                FAIL,
                f"{missing} Spread row(s) have no sportsbook number",
                url=url,
            )
        else:
            self.add(
                f"soccer {which} spread books",
                PASS,
                "Spread Books cells have a number",
                url=url,
            )

    def check_charts(self, html: str, url: str) -> None:
        missing = missing_signed_off_charts(
            html,
            require_pl_vs_books=True,
            require_market_tabs=True,
            require_totals=True,
        )
        if missing:
            self.add(
                "soccer results charts",
                FAIL,
                "Missing chart(s): " + ", ".join(missing),
                url=url,
            )
        else:
            self.add(
                "soccer results charts",
                PASS,
                "All three consensus charts present",
                url=url,
            )

        # Week nav hides .date-section:not(.visible) — charts inside Aug 23 are invisible.
        pre_date, _sep, _rest = re.split(
            r'(<div id="date-\d{4}-\d{2}-\d{2}")', html, maxsplit=1
        ) if re.search(r'<div id="date-\d{4}-\d{2}-\d{2}"', html) else (html, "", "")
        if "id=\"pl-results-markets\"" not in pre_date and "pl-soccer-charts-stack" not in pre_date:
            self.add(
                "soccer charts visible (not in hidden date-section)",
                FAIL,
                "Books/PL/XSharp and Totals charts are inside a date-section the week nav hides",
                url=url,
            )
        else:
            self.add(
                "soccer charts visible (not in hidden date-section)",
                PASS,
                "Charts sit above date-sections so the week nav cannot hide them",
                url=url,
            )

        if "soccer-chart-source" not in html:
            self.add(
                "soccer chart source games",
                FAIL,
                "Results page has no league-results source block",
                url=url,
            )
        elif "NCAA Men's Soccer" in html[html.find("soccer-chart-source") : html.find("soccer-chart-source") + 2500] and "league=" not in (url or ""):
            self.add(
                "soccer chart source games",
                FAIL,
                "All-leagues results dumps NCAA/cup games instead of asking to load a league",
                url=url,
            )
        else:
            self.add(
                "soccer chart source games",
                PASS,
                "Chart source is league-scoped (no mixed NCAA dump on All)",
                url=url,
            )

        ln = re.search(r"Last Night'?s Soccer Results — (\d{4}-\d{2}-\d{2})", html)
        chart_dates = re.findall(r"Last night \((\d{4}-\d{2}-\d{2})\)", html)
        if ln and chart_dates and ln.group(1) not in chart_dates:
            self.add(
                "soccer chart date vs last night",
                FAIL,
                f"Last Night heading is {ln.group(1)} but charts use {sorted(set(chart_dates))}",
                url=url,
            )
        elif ln:
            self.add(
                "soccer chart date vs last night",
                PASS,
                f"Chart last-night date matches {ln.group(1)}",
                url=url,
            )

        dash_rows = len(re.findall(r"<td>—</td>", html))
        if dash_rows > 18:
            self.add(
                "soccer chart missing data",
                FAIL,
                f"Charts are mostly empty dashes ({dash_rows} — cells)",
                url=url,
            )

    def run(self):
        print("  Soccer checker: locked chrome, live leagues, Load, charts + source games")
        picks_paths = ["/soccer-picks?region=all", "/soccer/", "/soccer-picks"]
        results_paths = ["/soccer-results?region=all", "/soccer/results", "/soccer-results"]

        pst, phtml, purl = 0, "", ""
        for p in picks_paths:
            pst, phtml, purl = self.fetch(p)
            if pst == 200 and len(phtml) > 2000:
                break
        if pst != 200:
            self.add("soccer picks page", FAIL, f"Could not load picks ({pst})", url=purl)
        else:
            self.check_locked_chrome(phtml, purl, "picks")
            self.check_league_picker(phtml, purl, "picks")
            self.check_three_card_row(phtml, purl, "picks")
            self.check_spread_books(phtml, purl, "picks")
            plxg = phtml.count("PL Expected Goals")
            h2h = len(re.findall(r"H2H Last 10", phtml))
            if plxg and phtml.count("data-pick-card") and plxg > phtml.count("data-pick-card") + 2:
                self.add(
                    "soccer picks duplicate PL xG",
                    FAIL,
                    f"PL Expected Goals appears {plxg} times — cards are duplicating the chip",
                    url=purl,
                )
            if phtml.count("data-pick-card") >= 3 and h2h < 3:
                self.add(
                    "soccer picks H2H Last 10",
                    FAIL,
                    f"Soccer cards are missing H2H Last 10 (found {h2h})",
                    url=purl,
                )
            else:
                self.add(
                    "soccer picks H2H Last 10",
                    PASS,
                    f"H2H Last 10 present ({h2h})",
                    url=purl,
                )
            cards_n = phtml.count("data-pick-card")
            empty_h2h = len(re.findall(r'data-h2h=""', phtml))
            if cards_n >= 3 and empty_h2h >= cards_n:
                self.add(
                    "soccer picks data-h2h",
                    FAIL,
                    f"Every pick card has empty data-h2h ({empty_h2h}/{cards_n})",
                    url=purl,
                )
            face_first = len(
                re.findall(
                    r'line-chip-label">\s*H2H Last 10\s*</div>\s*'
                    r'<div class="line-chip-val">\s*First meeting',
                    phtml,
                    flags=re.I,
                )
            )
            detail_dash = len(
                re.findall(
                    r'<span class="sf-label">\s*H2H Last 10\s*</span>\s*'
                    r'<span class="sf-val">\s*—',
                    phtml,
                    flags=re.I,
                )
            )
            if face_first >= 3 and detail_dash >= face_first:
                self.add(
                    "soccer picks H2H details",
                    FAIL,
                    f"View Details H2H is a dash on first-meeting cards "
                    f"(face First meeting={face_first}, details —={detail_dash})",
                    url=purl,
                )

        rst, rhtml, rurl = 0, "", ""
        for p in results_paths:
            rst, rhtml, rurl = self.fetch(p)
            if rst == 200 and len(rhtml) > 2000:
                break
        if rst != 200:
            self.add("soccer results page", FAIL, f"Could not load results ({rst})", url=rurl)
        else:
            self.check_locked_chrome(rhtml, rurl, "results")
            self.check_league_picker(rhtml, rurl, "results")
            self.check_three_card_row(rhtml, rurl, "results")
            self.check_spread_books(rhtml, rurl, "results")
            self.check_charts(rhtml, rurl)
            rcards = rhtml.count("data-pick-card")
            rh2h = len(re.findall(r"H2H Last 10", rhtml))
            rempty = len(re.findall(r'data-h2h=""', rhtml))
            if rcards >= 3 and rh2h < 3:
                self.add(
                    "soccer results H2H Last 10",
                    FAIL,
                    f"Soccer results cards are missing H2H Last 10 (found {rh2h})",
                    url=rurl,
                )
            elif rcards >= 3 and rempty >= rcards:
                self.add(
                    "soccer results data-h2h",
                    FAIL,
                    f"Every results card has empty data-h2h ({rempty}/{rcards})",
                    url=rurl,
                )

        if pst == 200 and phtml.count("data-pick-card") >= 8:
            g2 = len(re.findall(r'class="pc-name">\s*Grinder2\s*<', phtml, flags=re.I))
            td = len(re.findall(r'class="pc-name">\s*Takedown\s*<', phtml, flags=re.I))
            cards = phtml.count("data-pick-card")
            if g2 < cards * 0.9 or td < cards * 0.9:
                self.add(
                    "soccer picks Grinder2 Takedown",
                    FAIL,
                    f"Grinder2/Takedown missing on cards (G2={g2}, TD={td}, cards={cards})",
                    url=purl,
                )
            else:
                self.add(
                    "soccer picks Grinder2 Takedown",
                    PASS,
                    f"Grinder2/Takedown present on {g2}/{cards} cards",
                    url=purl,
                )

        st_off, off_html, off_url = self.fetch("/soccer-results?league=fifa-world-cup")
        if st_off == 200:
            if off_html.count("data-pick-card") < 1 and not re.search(
                r"is in the off-season", off_html, flags=re.I
            ):
                self.add(
                    "soccer results out of season",
                    FAIL,
                    "FIFA World Cup results has no cards and does not say out of season",
                    url=off_url,
                )
            else:
                self.add(
                    "soccer results out of season",
                    PASS,
                    "Empty league results says out of season (or has cards)",
                    url=off_url,
                )

        if pst == 200:
            self.check_load_works()
        self.check_every_espn_league_page()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Soccer UI / chrome / chart checker")
    parser.add_argument("--url", default=os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5081"))
    args = parser.parse_args(argv)

    class _R:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)
            mark = {"PASS": "OK", "FAIL": "FAIL", "WARN": "WARN"}.get(row.status, row.status)
            print(f"  [{mark}] {row.label}: {row.message}")

    class _C:
        def __init__(self, label, status, message, detail="", url="", auditor="soccer"):
            self.label = label
            self.status = status
            self.message = message
            self.detail = detail
            self.url = url
            self.auditor = auditor

    session = requests.Session()
    session.headers["User-Agent"] = "predictionlab-soccer-checker"
    report = _R()
    SoccerChecker(session, args.url, report, _C).run()
    fails = sum(1 for r in report.rows if r.status == FAIL)
    print(f"\nSoccer checker: {len(report.rows) - fails} pass / {fails} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
