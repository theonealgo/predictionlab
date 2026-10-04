"""Ship-parity auditor for the sports that were actually working on :5052.

:5052 was not a complete site. Only these were signed off there:
  MLB, UFC, tennis, and soccer results charts / values.
Do not treat NBA / NFL / NHL / college / WNBA / golf / CFL as :5052 gold.

Also checks live-site chrome that was part of the ship: homepage, Affiliate
footer, and the paid Soro blog embed.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import requests

_QA = Path(__file__).resolve().parent
if str(_QA) not in sys.path:
    sys.path.insert(0, str(_QA))

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"

# Both consensus chart families — signed-off :5052 plus every team sport.
BOTH_CHARTS = {"MLB", "SOCCER"}
# Moneyline consensus table only (signed-off :5052 tennis/UFC).
ML_CHART_ONLY = {"TENNIS", "UFC"}
# Every team sport — H2H values, both chart families, Cards|Chart, XSharp values.
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
ML_ONLY_SPORTS = (
    ("Tennis", "/tennis-picks", "/tennis-results"),
    ("UFC", "/ufc-picks", "/ufc-results"),
    ("Golf", "/golf-picks", "/golf-results"),
)
H2H_LAST10 = "H2H Last 10"

# Only the sports that were actually working on :5052.
SHIP_SPORTS = (
    ("MLB", "/mlb-picks", "/mlb-results"),
    ("SOCCER", "/soccer-picks", "/soccer-results"),
    ("TENNIS", "/tennis-picks", "/tennis-results"),
    ("UFC", "/ufc-picks", "/ufc-results"),
)

EMPTY_SLATE = (
    "no predictions available",
    "we are refreshing this page",
)

WEIRD_UI = (
    "sandbox only",
    "not live",
    "isolation —",
    "prob source",
    "theoddsapi",
    "elo + market blend",
)

from chart_shape import (  # noqa: E402
    ML_CHART,
    OU_CHART_DOT as OU_CHART,
    PL_VS_BOOKS,
    missing_signed_off_charts,
)

RL_CHART = PL_VS_BOOKS

SLOW_PATHS = {
    "/", "/soccer-results", "/soccer-picks", "/mlb-results", "/blog",
    "/ncaaf-results", "/cfl-results",
}


class ShipParityAuditor:
    NAME = "ship"

    def __init__(self, session: requests.Session, base: str, report, CheckResult):
        self.s = session
        self.base = base.rstrip("/")
        self.r = report
        self.CheckResult = CheckResult
        self.timeout = int(os.environ.get("AUDIT_SHIP_TIMEOUT", "45"))
        self.slow_timeout = int(os.environ.get("AUDIT_SHIP_SLOW_TIMEOUT", "75"))
        self.gold = (os.environ.get("AUDIT_GOLD_URL") or "http://127.0.0.1:5052").rstrip("/")
        self.against_gold = self.base.rstrip("/").endswith(":5052")
        self._lock = threading.Lock()
        self._missing_cards: list[str] = []
        self._missing_charts: list[str] = []
        self._missing_chrome: list[str] = []
        self._missing_share: list[str] = []
        self._weird_ui: list[str] = []
        self._missing_values: list[str] = []

    def add(self, label, status, message, url="", detail=""):
        with self._lock:
            self.r.add(self.CheckResult(
                label=label, status=status, message=message,
                detail=detail, url=url, auditor=self.NAME,
            ))

    def _note(self, bucket: list[str], sport: str):
        if sport not in bucket:
            bucket.append(sport)

    def fetch(self, path: str, base: str | None = None, timeout: int | None = None):
        url = urljoin((base or self.base) + "/", path.lstrip("/"))
        if timeout is None:
            timeout = self.slow_timeout if path.split("?")[0] in SLOW_PATHS else self.timeout
        t0 = time.time()
        try:
            resp = self.s.get(url, timeout=timeout, allow_redirects=True)
            return resp.status_code, resp.text or "", time.time() - t0, resp
        except Exception as exc:
            return 0, "", time.time() - t0, exc

    def _card_count(self, html: str) -> int:
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
        stacks = html.count("game-card-stack")
        picks = len(re.findall(r'class="[^"]*pick-card[^"]*"', html, flags=re.I))
        data = html.count("data-pick-card")
        return max(stacks, picks, data)

    def _blank_values(self, html: str) -> int:
        return len(re.findall(
            r'class="(?:win-pct|pc-val)[^"]*">\s*(?:—|&mdash;|N/?A)\s*<',
            html, flags=re.I,
        ))

    def _has_header(self, html: str) -> bool:
        return "pl2-nav" in html

    def _has_footer(self, html: str) -> bool:
        return ("/affiliate" in html and "Affiliate Program" in html)

    def _share_src(self, html: str) -> str:
        m = re.search(
            r'(?:src|href)="((?:/share/predictions/[^"]+\.jpg)|(?:/[^"]+/share\.jpg))"',
            html, flags=re.I,
        )
        if m:
            return m.group(1)
        if "Download image" in html:
            return "download"
        return ""

    def _empty(self, html: str) -> bool:
        low = html.lower()
        return any(s in low for s in EMPTY_SLATE)

    def _weird(self, html: str) -> list[str]:
        low = html.lower()
        hits = [w for w in WEIRD_UI if w in low]
        if not self.against_gold and ("127.0.0.1" in html or "localhost" in low):
            hits.append("localhost URLs on a public page")
        if "data-sandbox-sport" in html and "pl2-nav" not in html:
            hits.append("sandbox-sport without product header")
        return hits

    def _gold_cards(self, path: str) -> int | None:
        if self.against_gold:
            return None
        status, html, _, _ = self.fetch(path, base=self.gold, timeout=12)
        if status != 200 or len(html) < 2000:
            return None
        return self._card_count(html)

    def run(self):
        names = ", ".join(s[0] for s in SHIP_SPORTS)
        print(f"  5052 parity sports only: {names} (+ homepage / blog / affiliate)")
        print("  Also fail if header/footer destinations won't open (NCAAF, NFL, Affiliate, Blog)")
        self._check_homepage()
        self._check_blog()
        self._check_affiliate_page()
        self._check_must_open()
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = [
                pool.submit(self._check_sport, sport, picks, results)
                for sport, picks, results in SHIP_SPORTS
            ]
            for fut in as_completed(futs):
                fut.result()
        _off = frozenset({"NBA", "NHL", "NCAAB", "NCAAW"})
        for sport, picks, results in TEAM_TEMPLATE_SPORTS:
            if sport in _off:
                continue
            self._check_team_chart_sport(sport, picks, results, both_charts=True)
        for sport, picks, results in ML_ONLY_SPORTS:
            if sport.upper() in BOTH_CHARTS or sport.upper() in ML_CHART_ONLY:
                continue
            self._check_team_chart_sport(sport, picks, results, both_charts=False)
        self._digest()

    def _digest(self):
        def line(title: str, items: list[str]):
            if items:
                self.add(title, FAIL, ", ".join(sorted(items)))
            else:
                self.add(title, PASS, "none")

        line("DIGEST missing prediction cards", self._missing_cards)
        line("DIGEST missing results charts", self._missing_charts)
        line("DIGEST missing header/footer", self._missing_chrome)
        line("DIGEST missing bottom share image", self._missing_share)
        line("DIGEST missing card values", self._missing_values)
        line("DIGEST weird UI", self._weird_ui)

    def _check_homepage(self):
        status, html, elapsed, err = self.fetch("/")
        if status != 200 or len(html) < 2000:
            why = getattr(err, "args", [err])[0] if status == 0 else f"HTTP {status}"
            self.add("Homepage load", FAIL,
                     f"Homepage failed ({why}, {len(html)} bytes, {elapsed:.1f}s)",
                     url="/")
            return
        if elapsed >= 15:
            self.add("Homepage speed", FAIL,
                     f"Homepage took {elapsed:.1f}s — paying users will bounce",
                     url="/")
        elif elapsed >= 5:
            self.add("Homepage speed", WARN,
                     f"Homepage took {elapsed:.1f}s",
                     url="/")
        else:
            self.add("Homepage speed", PASS,
                     f"Homepage loaded in {elapsed:.1f}s",
                     url="/")

        previews = html.count("pl2-pick-card")
        if previews == 0:
            self.add("Today's previews", FAIL,
                     "Homepage has no today's pick preview cards (pl2-pick-card)",
                     url="/")
        else:
            self.add("Today's previews", PASS,
                     f"Homepage today's board has {previews} preview card(s)",
                     url="/")
        if not self._has_header(html):
            self.add("Homepage header", FAIL, "Homepage is missing pl2-nav", url="/")
            self._note(self._missing_chrome, "HOMEPAGE")
        else:
            self.add("Homepage header", PASS, "Product header present", url="/")
        if not self._has_footer(html):
            self.add("Homepage footer", FAIL, "Homepage missing Affiliate Program footer link", url="/")
            self._note(self._missing_chrome, "HOMEPAGE")
        else:
            self.add("Homepage footer", PASS, "Affiliate Program footer present", url="/")

    def _check_affiliate_page(self):
        """Footer 'Affiliate Program' must open a real page — not 404 / catch-all."""
        status, html, elapsed, err = self.fetch("/affiliate", timeout=15)
        if status == 0:
            self.add(
                "Affiliate page",
                FAIL,
                f"/affiliate won't open ({err or 'timeout'})",
                url="/affiliate",
            )
            return
        if status != 200:
            self.add(
                "Affiliate page",
                FAIL,
                f"/affiliate returned {status} — footer link is a dead page",
                url="/affiliate",
            )
            return
        if "Page not found" in html and len(html) < 200:
            self.add(
                "Affiliate page",
                FAIL,
                "/affiliate is the catch-all 404 (link shipped without a route)",
                url="/affiliate",
            )
            return
        if "Affiliate Program" not in html and "Creator Partnership" not in html:
            self.add(
                "Affiliate page",
                FAIL,
                "/affiliate loaded but is not the affiliate program page",
                url="/affiliate",
            )
            return
        self.add(
            "Affiliate page",
            PASS,
            f"/affiliate opened ({len(html):,} bytes, {elapsed:.1f}s)",
            url="/affiliate",
        )

    def _check_must_open(self):
        """In-season header links that currently hang on live and look like a dead site."""
        for path, label in (
            ("/ncaaf-picks", "NCAAF picks"),
            ("/nfl-picks", "NFL picks"),
        ):
            status, html, elapsed, err = self.fetch(path, timeout=15)
            if status == 0:
                self.add(
                    label,
                    FAIL,
                    f"{path} won't open — timed out ({err or 'no bytes'})",
                    url=path,
                )
            elif status >= 400:
                self.add(
                    label,
                    FAIL,
                    f"{path} returned {status}",
                    url=path,
                )
            elif len(html) < 400:
                self.add(
                    label,
                    FAIL,
                    f"{path} opened but is empty ({len(html)} bytes)",
                    url=path,
                )
            else:
                self.add(
                    label,
                    PASS,
                    f"{path} opened ({len(html):,} bytes, {elapsed:.1f}s)",
                    url=path,
                )

    def _check_blog(self):
        status, html, elapsed, err = self.fetch("/blog")
        if status == 0:
            self.add("Blog", FAIL, f"/blog won't open ({err or 'timeout'})", url="/blog")
            return
        if status != 200:
            self.add("Blog", FAIL, f"/blog returned {status} in {elapsed:.1f}s", url="/blog")
            return
        if len(html) < 1500:
            self.add("Blog", FAIL, f"/blog is empty ({len(html)} bytes)", url="/blog")
            return
        has_soro = 'id="soro-blog"' in html or "id='soro-blog'" in html
        has_script = "app.trysoro.com" in html
        if not has_soro or not has_script:
            self.add("Blog Soro embed", FAIL,
                     "Paid Soro blog embed is missing (need #soro-blog and app.trysoro.com)",
                     url="/blog")
        else:
            self.add("Blog Soro embed", PASS, "Soro embed markup is present", url="/blog")
        if "Internal Server Error" in html or "Traceback" in html:
            self.add("Blog", FAIL, "/blog rendered an error page", url="/blog")
        elif elapsed >= 15:
            self.add("Blog speed", FAIL, f"/blog took {elapsed:.1f}s", url="/blog")
        else:
            self.add("Blog", PASS, f"/blog loaded ({len(html):,} bytes, {elapsed:.1f}s)", url="/blog")

    def _missing_team_charts(self, html: str) -> list[str]:
        return missing_signed_off_charts(
            html,
            require_pl_vs_books=True,
            require_market_tabs=True,
            require_totals=False,
        )

    def _h2h_value_is_real(self, raw: str) -> bool:
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

    def _h2h_has_values(self, html: str) -> bool:
        html = html or ""
        vals = re.findall(
            r"H2H Last 10</span>\s*<span class=\"sf-val\">([\s\S]*?)</span>",
            html,
            flags=re.I,
        )
        data = re.findall(r'data-h2h="([^"]*)"', html)
        return any(self._h2h_value_is_real(raw) for raw in vals + data)

    def _wl_has_a_result(self, text: str) -> bool:
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

    def _consensus_xsharp_has_values(self, html: str) -> bool:
        return self._consensus_bucket_has_values(html, "XSharp")

    def _consensus_bucket_has_values(self, html: str, bucket: str) -> bool:
        rows = re.findall(
            rf'<td class="bucket">\s*{re.escape(bucket)}\s*</td>([\s\S]*?)</tr>',
            html or "",
            flags=re.I,
        )
        return any(self._wl_has_a_result(row) for row in rows)

    def _pl_records_expected(self, html: str) -> bool:
        if self._consensus_bucket_has_values(html, "Prediction Lab"):
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
            if self._wl_has_a_result(m.group(2)):
                return True
        return False

    def _pl_consensus_missing_where_xsharp_exists(
        self, html: str, expected_html: str | None = None
    ) -> bool:
        if not self._pl_records_expected(expected_html if expected_html is not None else html):
            return False
        for table in re.findall(r"<table[\s\S]*?</table>", html or "", flags=re.I):
            if not re.search(r'class="bucket">\s*Prediction Lab\s*<', table, flags=re.I):
                continue
            if not re.search(r'class="bucket">\s*XSharp\s*<', table, flags=re.I):
                continue
            if self._consensus_bucket_has_values(
                table, "XSharp"
            ) and not self._consensus_bucket_has_values(table, "Prediction Lab"):
                return True
        return False

    def _scored_gamelog_ungraded_count(self, html: str) -> int:
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
                plain = [
                    re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in cells
                ]
                score_i = next(
                    (i for i, c in enumerate(plain) if re.search(r"\d+\s*[–-]\s*\d+", c)),
                    None,
                )
                if score_i is None or score_i + 3 >= len(plain):
                    continue
                if plain[score_i + 1] in empty and plain[score_i + 3] in empty:
                    count += 1
        return count

    def _copy_all_present(self, html: str) -> bool:
        html = html or ""
        return bool(
            re.search(r'id=["\']pvCopyBtn["\']', html)
            or re.search(r">\s*(?:📋\s*)?Copy All\s*<", html)
        )

    def _copy_all_copies_slate(self, html: str) -> bool:
        html = html or ""
        if "pl-copy-all-slate" in html:
            return True
        return bool(
            re.search(
                r"copyVisiblePicks[\s\S]{0,1200}querySelectorAll\(\s*['\"]\\.date-section",
                html,
            )
        )

    def _has_chart_view_control(self, html: str) -> bool:
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

    def _has_pick_cards(self, html: str) -> bool:
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
        low = html.lower()
        # Real card nodes only — leftover H2H copy must not hide a blank slate.
        return bool(
            "data-pick-card" in low
            or "pl2-pick-card" in low
            or "data-game-card" in low
            or 'class="pick-card"' in low
            or "class='pick-card'" in low
            or "game-card-stack" in low
        )

    @staticmethod
    def _picks_no_predictions_banner(html: str, sport: str) -> bool:
        if not html:
            return False
        name = re.escape(sport or "")
        return bool(
            re.search(
                rf'class="no-data"[^>]*>\s*No predictions available for\s+{name}\s*<',
                html,
                flags=re.I,
            )
            or re.search(
                rf"No predictions available for\s+{name}\b",
                html,
                flags=re.I,
            )
        )

    @staticmethod
    def _et_today_str() -> str:
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime

            return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            from datetime import datetime

            return datetime.now().strftime("%Y-%m-%d")

    def _xsharp_rows_present(self, html: str) -> bool:
        return bool(
            re.search(r'<td class="bucket">\s*XSharp\s*</td>', html or "", flags=re.I)
        )

    def _note_xsharp(self, sport: str, html: str, url: str, label: str) -> None:
        if not self._xsharp_rows_present(html):
            return
        if self._consensus_xsharp_has_values(html):
            self.add(label, PASS, "Consensus chart includes XSharp values", url=url)
        else:
            self.add(label, FAIL,
                     "XSharp consensus row is present but values are only — or 0-0",
                     url=url)
            self._note(self._missing_values, sport)

    def _note_pl_consensus(
        self, sport: str, html: str, url: str, label: str, *, expected_html: str | None = None
    ) -> None:
        if sport == "MLB":
            return
        if not re.search(r'class="bucket">\s*Prediction Lab\s*<', html or "", flags=re.I):
            return
        if not re.search(r'class="bucket">\s*XSharp\s*<', html or "", flags=re.I):
            return
        if self._pl_consensus_missing_where_xsharp_exists(html, expected_html):
            self.add(
                label,
                FAIL,
                "Prediction Lab consensus is — / 0-0 while XSharp has a W-L "
                "(Books or Totals table)",
                url=url,
            )
            self._note(self._missing_values, sport)
        else:
            self.add(
                label,
                PASS,
                "Prediction Lab consensus has values wherever XSharp does",
                url=url,
            )

    def _note_ungraded(self, sport: str, html: str, url: str, label: str) -> None:
        if sport == "MLB" or "results-table" not in (html or ""):
            return
        n = self._scored_gamelog_ungraded_count(html)
        if n:
            self.add(
                label,
                FAIL,
                f"{n} scored game-log row(s) have no pick / result (ungraded finals)",
                url=url,
            )
            self._note(self._missing_values, sport)
        else:
            self.add(label, PASS, "Scored game-log rows have a pick and result", url=url)

    def _check_team_chart_sport(
        self, sport: str, picks: str, results: str, *, both_charts: bool = True
    ):
        """All sports: H2H Last 10, consensus, PL vs sportsbook, chart view."""
        st, html, _, _ = self.fetch(picks)
        require_h2h = both_charts and sport != "Soccer"
        has_label = H2H_LAST10 in (html or "")
        if st != 200:
            if require_h2h:
                self.add(f"{sport} H2H Last 10", FAIL,
                         f"{picks} failed ({st}) — cannot confirm H2H Last 10",
                         url=picks)
        elif require_h2h or has_label:
            if self._has_pick_cards(html) or has_label:
                if not has_label or not self._h2h_has_values(html):
                    self.add(f"{sport} H2H Last 10", FAIL,
                             "Picks cards are missing H2H Last 10 values (label only, all —, or all 0)",
                             url=picks)
                    self._note(self._missing_values, sport)
                else:
                    self.add(f"{sport} H2H Last 10", PASS,
                             "H2H Last 10 present on picks cards", url=picks)

        if st == 200 and sport not in ("Tennis", "UFC", "Golf", "NBA", "NHL", "NCAAB", "NCAAW"):
            today = self._et_today_str()
            banner = self._picks_no_predictions_banner(html, sport)
            has_cards = self._has_pick_cards(html)
            has_today = f'id="date-{today}"' in (html or "")
            if banner:
                self.add(
                    f"{sport} picks slate",
                    FAIL,
                    f'Page shows "No predictions available for {sport}" '
                    f"(blank slate on a live sport; today={today})",
                    url=picks,
                )
                self._note(self._missing_values, sport)
            elif not has_cards and "is in the off-season" not in (html or ""):
                self.add(f"{sport} picks slate", FAIL,
                         "Predictions page has no pick cards (blank slate on a live sport)",
                         url=picks)
                self._note(self._missing_values, sport)
            # Daily sport only — weekly sports often have no games today.
            elif sport == "MLB" and has_cards and not has_today:
                self.add(
                    f"{sport} picks today",
                    FAIL,
                    f"Pick cards exist but none for today ({today}) — "
                    "stale slate / missing today's games",
                    url=picks,
                )
                self._note(self._missing_values, sport)
            elif sport == "MLB" and has_cards:
                self.add(
                    f"{sport} picks today",
                    PASS,
                    f"Today's date section present ({today})",
                    url=picks,
                )

        if st == 200 and sport not in ("Tennis", "UFC", "Golf") and self._has_pick_cards(html):
            if not self._copy_all_present(html):
                self.add(f"{sport} Copy All", FAIL,
                         "Predictions page has cards but no Copy All button",
                         url=picks)
            elif sport != "MLB" and not self._copy_all_copies_slate(html):
                self.add(f"{sport} Copy All", FAIL,
                         "Copy All is present but only copies the visible date / moneyline, not the full slate",
                         url=picks)
            else:
                self.add(f"{sport} Copy All", PASS,
                         "Copy All present" + (
                             "" if sport == "MLB"
                             else " (every loaded game + moneyline/spread/totals)"
                         ),
                         url=picks)

        rst, rhtml, _, _ = self.fetch(results)
        if rst != 200 or len(rhtml) < 800:
            self.add(f"{sport} results charts", FAIL,
                     f"{results} failed ({rst})", url=results)
            self._note(self._missing_charts, sport)
            return
        started = (
            both_charts
            or "view=chart" in rhtml
            or ML_CHART in rhtml
            or RL_CHART in rhtml
        )
        if started and not self._has_chart_view_control(rhtml):
            self.add(f"{sport} results chart view", FAIL,
                     "Results page has no Cards|Chart / view=chart control",
                     url=results)
            self._note(self._missing_charts, sport)
        if both_charts:
            missing = self._missing_team_charts(rhtml)
            if missing:
                self.add(f"{sport} results charts", FAIL,
                         "Missing: " + ", ".join(missing), url=results)
                self._note(self._missing_charts, sport)
            else:
                self.add(f"{sport} results charts", PASS,
                         "Consensus + PL vs sportsbook charts present", url=results)
        elif started:
            if ML_CHART not in rhtml:
                self.add(f"{sport} results charts", FAIL,
                         "Missing Consensus Based Betting Records", url=results)
                self._note(self._missing_charts, sport)
            else:
                self.add(f"{sport} results charts", PASS,
                         "Consensus Based Betting Records present", url=results)
        self._note_xsharp(sport, rhtml, results, f"{sport} consensus XSharp")
        self._note_pl_consensus(
            sport, rhtml, results, f"{sport} consensus Prediction Lab", expected_html=rhtml
        )
        self._note_ungraded(sport, rhtml, results, f"{sport} results game log")

        if not started:
            return
        cst, chtml, _, _ = self.fetch(results + "?view=chart")
        if cst != 200:
            self.add(f"{sport} results chart view", FAIL,
                     f"{results}?view=chart won't open ({cst})",
                     url=f"{results}?view=chart")
            self._note(self._missing_charts, sport)
            return
        if both_charts:
            missing_v = self._missing_team_charts(chtml)
            if missing_v:
                self.add(f"{sport} results chart view", FAIL,
                         "Chart view is missing: " + ", ".join(missing_v),
                         url=f"{results}?view=chart")
                self._note(self._missing_charts, sport)
            else:
                self.add(f"{sport} results chart view", PASS,
                         "Chart view has consensus + PL vs sportsbook",
                         url=f"{results}?view=chart")
        elif ML_CHART not in chtml:
            self.add(f"{sport} results chart view", FAIL,
                     "Chart view is missing Consensus Based Betting Records",
                     url=f"{results}?view=chart")
            self._note(self._missing_charts, sport)
        else:
            self.add(f"{sport} results chart view", PASS,
                     "Chart view has Consensus Based Betting Records",
                     url=f"{results}?view=chart")
        self._note_xsharp(
            sport, chtml, f"{results}?view=chart",
            f"{sport} chart-view consensus XSharp",
        )
        self._note_pl_consensus(
            sport, chtml, f"{results}?view=chart",
            f"{sport} chart-view consensus Prediction Lab",
            expected_html=rhtml,
        )
        self._note_ungraded(
            sport, chtml, f"{results}?view=chart",
            f"{sport} chart-view game log",
        )

    def _check_sport(self, sport: str, picks: str, results: str):
        st, html, elapsed, _ = self.fetch(picks)
        if st != 200 or len(html) < 800:
            self.add(f"{sport} picks page", FAIL,
                     f"{picks} failed ({st}, {len(html)} bytes, {elapsed:.1f}s)",
                     url=picks)
            self._note(self._missing_cards, sport)
            html = html or ""
            cards = 0
        else:
            cards = self._card_count(html)
            empty = self._empty(html)
            blanks = self._blank_values(html)
            gold = self._gold_cards(picks)
            if cards == 0 or empty:
                self.add(f"{sport} prediction cards", FAIL,
                         f"No prediction cards on {picks}"
                         + (" — empty slate copy present" if empty else ""),
                         url=picks,
                         detail=f"gold :5052 cards={gold}" if gold is not None else "")
                self._note(self._missing_cards, sport)
            else:
                msg = f"{cards} card(s) on {picks}"
                if gold is not None and gold > 0 and cards < max(1, gold // 4):
                    self.add(f"{sport} prediction cards", WARN,
                             f"{msg} — :5052 has {gold}", url=picks)
                else:
                    self.add(f"{sport} prediction cards", PASS, msg, url=picks)
            if blanks >= 6:
                self.add(f"{sport} missing values", FAIL,
                         f"{blanks} blank/dash model values on picks cards",
                         url=picks)
                self._note(self._missing_values, sport)
            elif blanks:
                self.add(f"{sport} missing values", WARN,
                         f"{blanks} blank/dash model values on picks cards",
                         url=picks)
                self._note(self._missing_values, sport)

            if not self._has_header(html) or not self._has_footer(html):
                self.add(f"{sport} picks chrome", FAIL,
                         "Missing product header and/or Affiliate Program footer",
                         url=picks)
                self._note(self._missing_chrome, sport)
            else:
                self.add(f"{sport} picks chrome", PASS,
                         "Header + Affiliate footer present", url=picks)

            share = self._share_src(html)
            if cards > 0 and not share:
                self.add(f"{sport} bottom share image", FAIL,
                         "Picks page has cards but no /share/predictions image or /share.jpg",
                         url=picks)
                self._note(self._missing_share, sport)
            elif share and share != "download":
                img_url = urljoin(self.base + "/", share.lstrip("/"))
                try:
                    img = self.s.get(img_url, timeout=15, allow_redirects=True)
                    if img.status_code != 200 or len(img.content) < 800:
                        self.add(f"{sport} bottom share image", FAIL,
                                 f"Share image missing or tiny ({img.status_code}, {len(img.content)} bytes)",
                                 url=img_url)
                        self._note(self._missing_share, sport)
                    else:
                        self.add(f"{sport} bottom share image", PASS,
                                 f"Share image ok ({len(img.content):,} bytes)",
                                 url=img_url)
                except Exception as exc:
                    self.add(f"{sport} bottom share image", FAIL,
                             f"Share image request failed: {exc}", url=img_url)
                    self._note(self._missing_share, sport)
            elif share == "download":
                self.add(f"{sport} bottom share image", PASS,
                         "Download image control present", url=picks)

            weird = self._weird(html)
            if weird:
                self.add(f"{sport} weird UI", FAIL,
                         f"Picks page has leftover/broken UI: {', '.join(weird)}",
                         url=picks)
                self._note(self._weird_ui, sport)

        rst, rhtml, _, err = self.fetch(results)
        if rst != 200 or len(rhtml) < 800:
            self.add(f"{sport} results page", FAIL,
                     f"{results} failed ({rst}, {len(rhtml)} bytes)",
                     url=results)
            if sport in BOTH_CHARTS or sport in ML_CHART_ONLY:
                self._note(self._missing_charts, sport)
            return

        if sport in BOTH_CHARTS:
            missing = missing_signed_off_charts(
                rhtml,
                require_pl_vs_books=True,
                require_market_tabs=True,
                require_totals=sport == "SOCCER",
            )
            if not missing:
                self.add(f"{sport} results charts", PASS,
                         "Signed-off consensus + PL vs Sportsbook charts present",
                         url=results)
            else:
                self.add(f"{sport} results charts", FAIL,
                         "Missing the new results consensus charts: " + ", ".join(missing),
                         url=results)
                self._note(self._missing_charts, sport)
        elif sport in ML_CHART_ONLY:
            if ML_CHART in rhtml:
                self.add(f"{sport} results charts", PASS,
                         "Consensus Based Betting Records present",
                         url=results)
            else:
                self.add(f"{sport} results charts", FAIL,
                         "Missing Consensus Based Betting Records",
                         url=results)
                self._note(self._missing_charts, sport)
        else:
            self.add(f"{sport} results charts", INFO,
                     "Not a :5052 consensus-chart sport",
                     url=results)

        if sport in BOTH_CHARTS or sport in ML_CHART_ONLY:
            cst, chtml, _, _ = self.fetch(results + "?view=chart")
            if cst != 200 or ML_CHART not in chtml:
                self.add(f"{sport} results chart view", FAIL,
                         "Cards|Chart view is missing Consensus Based Betting Records",
                         url=f"{results}?view=chart")
                self._note(self._missing_charts, sport)

        if not self._has_header(rhtml) or not self._has_footer(rhtml):
            self.add(f"{sport} results chrome", FAIL,
                     "Missing product header and/or Affiliate Program footer",
                     url=results)
            self._note(self._missing_chrome, sport)
        weird_r = self._weird(rhtml)
        if weird_r:
            self.add(f"{sport} results weird UI", FAIL,
                     f"Results page has leftover/broken UI: {', '.join(weird_r)}",
                     url=results)
            self._note(self._weird_ui, sport)
