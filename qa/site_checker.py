#!/usr/bin/env python3
"""
PredictionLab Site Checker
===========================
Automated QA: checks every route, navigation link, prediction card,
model outputs, results math, CSV exports, and stale content — then
emails a detailed report.

Usage:
  python qa/site_checker.py                  # full check, no email
  python qa/site_checker.py --quick          # routes + content + nav only (~45s)
  python qa/site_checker.py --full           # every auditor
  python qa/site_checker.py --email          # full check + email report
  python qa/site_checker.py --screenshots    # include Playwright screenshots
  python qa/site_checker.py --quick --email  # quick check + email
  python qa/site_checker.py --preflight      # server reachability only (~1s)

EMAIL SETUP (one-time):
  Edit qa/checker_email.py and fill in your Gmail address and App Password.
  Get an App Password at: https://myaccount.google.com/apppasswords
  (Requires Gmail 2FA to be enabled)

Environment variable overrides:
  AUDIT_BASE_URL       default http://127.0.0.1:5001
  AUDIT_EMAIL_TO       default nmesghali@gmail.com
  AUDIT_EMAIL_FROM     sender address
  AUDIT_EMAIL_PASSWORD app password for SMTP
  AUDIT_SMTP_HOST      default smtp.gmail.com
  AUDIT_SMTP_PORT      default 587
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── paths ──────────────────────────────────────────────────────────────────
QA_DIR = Path(__file__).parent
sys.path.insert(0, str(QA_DIR))

from audit_config import (
    BASE_URL, CARD_REQUIRED_CLASSES, CLUSTER_RANGE_HIGH, CLUSTER_RANGE_LOW,
    CLUSTER_WARN_PCT, EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO,
    EXPECTED_DASHBOARD_SPORTS, FORBIDDEN_CONTENT,
    FULL_MODE_AUDITORS, HISTORY_DIR, MODEL_DISPLAY_NAMES, MODEL_KEYS,
    PREFLIGHT_TIMEOUT, QUICK_MODE_AUDITORS, REQUEST_TIMEOUT, SCREENSHOTS_DIR,
    SMTP_HOST, SMTP_PORT, SPORT_PICKS_SLUGS, SPORT_RESULTS_SLUGS, STALE_STRINGS,
    USER_AGENT,
)

# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

PASS    = "PASS"
WARN    = "WARN"
FAIL    = "FAIL"
INFO    = "INFO"


@dataclass
class CheckResult:
    label:    str
    status:   str          # PASS | WARN | FAIL | INFO
    message:  str
    detail:   str = ""
    auditor:  str = ""
    url:      str = ""


@dataclass
class AuditReport:
    run_id:    str = ""
    timestamp: str = ""
    base_url:  str = ""
    duration:  float = 0.0
    checks:    list[CheckResult] = field(default_factory=list)
    overall:   str = PASS

    def add(self, result: CheckResult):
        self.checks.append(result)
        if result.status == FAIL:
            self.overall = FAIL
        elif result.status == WARN and self.overall == PASS:
            self.overall = WARN

    @property
    def passes(self):  return [c for c in self.checks if c.status == PASS]
    @property
    def warnings(self): return [c for c in self.checks if c.status == WARN]
    @property
    def failures(self): return [c for c in self.checks if c.status == FAIL]
    @property
    def infos(self):   return [c for c in self.checks if c.status == INFO]

    def summary(self) -> str:
        return (f"{self.overall} — "
                f"{len(self.passes)} passed, "
                f"{len(self.warnings)} warnings, "
                f"{len(self.failures)} failures")


# ═══════════════════════════════════════════════════════════════════════════
# HTTP SESSION
# ═══════════════════════════════════════════════════════════════════════════

def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://",  adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": USER_AGENT})
    return s


HTTP_DEPENDENT_AUDITORS = frozenset({
    "routes", "content", "navigation", "cards", "models", "results", "csv",
    "screenshots",
})


def classify_request_error(detail: str) -> str:
    """Classify HTTP client failures: connection_refused, timeout, or other."""
    d = detail.lower()
    if ("connection refused" in d
            or "econnrefused" in d
            or ("failed to establish" in d and "refused" in d)):
        return "connection_refused"
    if "timed out" in d or "timeout" in d or "read timed out" in d:
        return "timeout"
    return "other"


def _server_down_result(base: str, detail: str = "") -> CheckResult:
    return CheckResult(
        label="Server preflight",
        status=FAIL,
        message=f"Server not running on {base} — start Flask before audit",
        detail=detail[:200] if detail else "",
        auditor="preflight",
        url=base,
    )


def preflight_server(session: requests.Session, base: str,
                     timeout: int | None = None) -> tuple[bool, CheckResult | None]:
    """Hit /healthz or / before route audit. Fail fast if nothing is listening."""
    timeout = timeout if timeout is not None else PREFLIGHT_TIMEOUT
    last_detail = ""

    for path in ("/healthz", "/"):
        url = urljoin(base, path)
        try:
            resp = session.get(url, timeout=timeout, allow_redirects=True,
                               stream=True)
            resp.close()
            if resp.status_code < 500:
                return True, None
            last_detail = f"HTTP {resp.status_code} from {path}"
        except requests.exceptions.ConnectionError as exc:
            detail = str(exc)
            last_detail = detail
            if classify_request_error(detail) == "connection_refused":
                return False, _server_down_result(base, detail)
        except requests.exceptions.Timeout:
            # TCP connected but response slow — server is up.
            return True, None
        except Exception as exc:
            detail = str(exc)
            last_detail = detail
            if classify_request_error(detail) == "connection_refused":
                return False, _server_down_result(base, detail)

    if last_detail and classify_request_error(last_detail) == "connection_refused":
        return False, _server_down_result(base, last_detail)
    return True, None


def _request_error_message(kind: str) -> str:
    if kind == "timeout":
        return "Page exists but is slow to respond — check for heavy queries"
    if kind == "connection_refused":
        return "Connection refused — server not running"
    return "Request failed"


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class RouteAuditor:
    NAME = "routes"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _get(self, path: str) -> tuple[int, str]:
        """Return (status_code, final_url). Follows redirects once."""
        url = urljoin(self.base, path)
        try:
            resp = self.s.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=False)
            return resp.status_code, resp.url
        except Exception as exc:
            return -1, str(exc)

    def _head(self, path: str) -> int:
        """Quick HEAD check — no redirect following."""
        url = urljoin(self.base, path)
        try:
            resp = self.s.get(url, timeout=REQUEST_TIMEOUT,
                              allow_redirects=False)
            return resp.status_code
        except Exception:
            return -1

    def _check_route(self, route: dict) -> CheckResult:
        path    = route["path"]
        allowed = route["expected_status"]
        kind    = route.get("kind", "public")

        if kind == "protected":
            code = self._head(path)
            if code in allowed:
                return CheckResult(label=route["label"], status=PASS,
                                   message=f"Correctly redirects (HTTP {code})",
                                   url=path, auditor=self.NAME)
            return CheckResult(label=route["label"], status=WARN,
                               message=f"Expected {allowed}, got {code}",
                               url=path, auditor=self.NAME)

        code, detail = self._get(path)
        if code in allowed:
            return CheckResult(label=route["label"], status=PASS,
                               message=f"HTTP {code}", url=path,
                               auditor=self.NAME)
        if code == -1:
            err_kind = classify_request_error(detail)
            # Slow page on local dev = WARN; server down = FAIL (preflight should
            # catch that first, but classify consistently if it happens mid-audit).
            if err_kind == "timeout" and kind == "public":
                sev = WARN
            else:
                sev = FAIL
            return CheckResult(label=route["label"], status=sev,
                               message=_request_error_message(err_kind),
                               detail=detail[:200],
                               url=path, auditor=self.NAME)
        sev = WARN if kind == "should_404" else FAIL
        return CheckResult(label=route["label"], status=sev,
                           message=f"Expected {allowed}, got {code}",
                           url=path, auditor=self.NAME)

    def run(self):
        cfg_path = QA_DIR / "expected_routes.json"
        cfg = json.loads(cfg_path.read_text())

        # Build work list — deduplicate sport slugs already covered by public list
        already_covered = {r["path"] for r in cfg.get("public", [])}
        tasks = []
        for r in cfg.get("public", []):
            tasks.append({**r, "kind": "public"})
        for r in cfg.get("protected", []):
            tasks.append({**r, "kind": "protected"})
        for r in cfg.get("should_404", []):
            tasks.append({**r, "kind": "should_404"})
        # Only add sport slug checks for slugs NOT already in the public list
        for slug in SPORT_PICKS_SLUGS:
            path = f"/{slug}"
            if path not in already_covered:
                tasks.append({"path": path, "label": f"Sport page {path}",
                              "expected_status": [200, 301, 302], "kind": "sport"})

        # Warm key routes sequentially so cold-cache pages don't false-timeout in parallel audit.
        for _warm_path in ('/all-sports-results', '/nba-picks', '/daily-report'):
            try:
                self.s.get(urljoin(self.base, _warm_path), timeout=max(REQUEST_TIMEOUT, 45),
                           allow_redirects=True)
            except Exception:
                pass

        # Run route checks with limited parallelism (Flask dev server is single-threaded).
        results: list[tuple[int, CheckResult]] = []
        # Flask dev server is single-threaded — parallel route hits queue and false-timeout.
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_map = {pool.submit(self._check_route, t): i
                          for i, t in enumerate(tasks)}
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    results.append((idx, future.result()))
                except Exception as exc:
                    results.append((idx, CheckResult(
                        label=tasks[idx].get("label", "unknown"),
                        status=FAIL, message=str(exc),
                        auditor=self.NAME)))

        # Tally timeout warnings — collapse many slow-page warnings into one summary
        timeout_warns = []
        for _, cr in sorted(results, key=lambda x: x[0]):
            if cr.status == WARN and "slow to respond" in cr.message:
                timeout_warns.append(cr.url or cr.label)
            else:
                self.r.add(cr)

        if timeout_warns:
            self.r.add(CheckResult(
                label="Slow pages on local server",
                status=WARN,
                message=f"{len(timeout_warns)} page(s) exceeded the {REQUEST_TIMEOUT}s timeout "
                        f"— normal on local dev, will be fast in production",
                detail=", ".join(timeout_warns[:8]),
                auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# CONTENT AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class ContentAuditor:
    NAME = "content"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _fetch(self, path: str) -> str:
        try:
            resp = self.s.get(urljoin(self.base, path),
                              timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return resp.text
        except Exception:
            return ""

    def run(self):
        # Pages to scan for stale/forbidden content
        pages_to_scan = ["/", "/nba-picks", "/nhl-picks", "/plans",
                         "/privacy", "/terms", "/faq", "/daily-report",
                         "/all-sports-results"]

        # Fetch all pages in parallel
        page_html: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._fetch, p): p for p in pages_to_scan}
            for fut in as_completed(futures):
                page_html[futures[fut]] = fut.result()

        found: dict[str, list[str]] = {}

        for path in pages_to_scan:
            html = page_html.get(path, "")
            if not html:
                continue
            for term in STALE_STRINGS + FORBIDDEN_CONTENT:
                # Case-insensitive search, avoid false positives in script URLs
                # (e.g., underdogs.bet in a CSP header string)
                stripped = re.sub(r'<script[^>]*>.*?</script>', '',
                                  html, flags=re.DOTALL | re.IGNORECASE)
                stripped = re.sub(r'<style[^>]*>.*?</style>', '',
                                  stripped, flags=re.DOTALL | re.IGNORECASE)
                if term.lower() in stripped.lower():
                    found.setdefault(term, []).append(path)

        for term, paths in found.items():
            sev = FAIL if term in FORBIDDEN_CONTENT else WARN
            self.r.add(CheckResult(
                label=f"Stale content: '{term}'", status=sev,
                message=f"Found on {len(paths)} page(s): {', '.join(paths)}",
                auditor=self.NAME))

        if not found:
            self.r.add(CheckResult(
                label="Stale content scan", status=PASS,
                message="No stale/forbidden strings found in page content",
                auditor=self.NAME))

        # Check for debug-mode indicators
        homepage = self._fetch("/")
        if "Debug mode: on" in homepage or "Werkzeug" in homepage:
            self.r.add(CheckResult(
                label="Debug mode", status=WARN,
                message="Flask debug mode appears to be ON — disable before production",
                auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Debug mode", status=PASS,
                message="No debug mode indicators found",
                auditor=self.NAME))

        # Check page sizes (warn if suspiciously small = likely error page)
        for path in ["/nba-picks", "/nhl-picks"]:
            html = self._fetch(path)
            size = len(html)
            if size < 5000:
                self.r.add(CheckResult(
                    label=f"Page size: {path}", status=WARN,
                    message=f"Page is only {size} bytes — possible error or empty page",
                    url=path, auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"Page size: {path}", status=PASS,
                    message=f"{size:,} bytes", url=path, auditor=self.NAME))

        # Check ALL sport picks pages for fatal error strings or missing prediction data
        FATAL_BODY_STRINGS = [
            "Sport not found",
            "Page not found",
            "Internal Server Error",
            "500 Internal Server Error",
            "Application Error",
        ]
        for slug in SPORT_PICKS_SLUGS:
            path = f"/{slug}"
            try:
                resp = self.s.get(urljoin(self.base, path),
                                  timeout=8, allow_redirects=True)
                if resp.status_code == 404:
                    self.r.add(CheckResult(
                        label=f"Sport page broken: {slug}", status=FAIL,
                        message=f"Returns 404 — sport not registered in app",
                        url=path, auditor=self.NAME))
                elif resp.status_code == 200:
                    for bad in FATAL_BODY_STRINGS:
                        if bad.lower() in resp.text.lower():
                            self.r.add(CheckResult(
                                label=f"Sport page error: {slug}", status=FAIL,
                                message=f'Page body contains "{bad}" — sport not wired up correctly',
                                url=path, auditor=self.NAME))
                            break
            except Exception:
                pass  # timeout handled by route auditor

        # Model blank-out check — catch pages where win-prob shows "—" for
        # all cards (means model returned None for all games, like soccer WC above).
        for slug in ["nba-picks", "nhl-picks", "mlb-picks", "soccer-picks",
                     "tennis-picks", "ufc-picks", "golf-picks"]:
            path = f"/{slug}"
            try:
                resp = self.s.get(urljoin(self.base, path),
                                  timeout=10, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                html = resp.text
                # Count pick-cards vs cards with actual win-pct numbers
                total_cards = html.count('class="game-card pick-card"')
                # win-pct spans contain a digit when populated; dashes when blank
                cards_with_prob = len(re.findall(
                    r'class="win-pct"[^>]*>\d', html))
                if total_cards >= 3 and cards_with_prob == 0:
                    self.r.add(CheckResult(
                        label=f"Missing win probabilities: {slug}",
                        status=FAIL,
                        message=(f"All {total_cards} prediction cards show '—' for win "
                                 f"probability — model returned None for all games"),
                        url=path, auditor=self.NAME))
                elif total_cards >= 3 and cards_with_prob < total_cards * 0.5:
                    self.r.add(CheckResult(
                        label=f"Partial win probabilities: {slug}",
                        status=WARN,
                        message=(f"{cards_with_prob}/{total_cards} cards have win "
                                 f"probability — some games missing model data"),
                        url=path, auditor=self.NAME))
            except Exception:
                pass

        # Dashboard completeness — every expected sport must appear on the
        # homepage "Today's Picks by Sport" grid AND the all-sports-results page.
        home_html = page_html.get("/", "")
        grid_m = re.search(r'Today.s Picks by Sport.*?</div>\s*</div>\s*</div>',
                           home_html, re.DOTALL)
        grid_html = grid_m.group(0) if grid_m else home_html
        results_html = page_html.get("/all-sports-results", "")
        if not results_html:
            results_html = self._fetch("/all-sports-results")

        for sport in EXPECTED_DASHBOARD_SPORTS:
            on_home = bool(re.search(rf'sport-name">\s*{re.escape(sport)}\b', grid_html)) \
                      or f'>{sport}<' in grid_html
            if on_home:
                self.r.add(CheckResult(
                    label=f"Homepage grid: {sport}", status=PASS,
                    message="Listed in Today's Picks by Sport", auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"Homepage grid: {sport} MISSING", status=FAIL,
                    message=f"{sport} is not shown in the homepage 'Today's Picks by Sport' grid",
                    url="/", auditor=self.NAME))

            if results_html:
                on_results = sport.lower() in results_html.lower()
                if on_results:
                    self.r.add(CheckResult(
                        label=f"Results dashboard: {sport}", status=PASS,
                        message="Listed on all-sports-results", auditor=self.NAME))
                else:
                    self.r.add(CheckResult(
                        label=f"Results dashboard: {sport} MISSING", status=FAIL,
                        message=f"{sport} is not shown on the All Sports Results page",
                        url="/all-sports-results", auditor=self.NAME))

        if results_html:
            raw_dash_cells = len(re.findall(r'<td[^>]*>\s*[—-]\s*</td>', results_html))
            pct_cells = len(re.findall(r'class="asr-pct"[^>]*>\s*\d+(?:\.\d+)?%', results_html))
            explicit_status = len(re.findall(r'class="asr-status"', results_html))
            if raw_dash_cells:
                self.r.add(CheckResult(
                    label="All Sports Results missing cells",
                    status=FAIL,
                    message=f"{raw_dash_cells} dashboard cell(s) render as a bare dash",
                    detail="Use real stats, Not tracked, or No games yet so blanks cannot hide broken data.",
                    url="/all-sports-results", auditor=self.NAME))
            elif pct_cells >= 10 and explicit_status >= 1:
                self.r.add(CheckResult(
                    label="All Sports Results cell coverage",
                    status=PASS,
                    message=f"{pct_cells} stat cell(s), {explicit_status} explicit non-stat status cell(s)",
                    url="/all-sports-results", auditor=self.NAME))
            elif pct_cells >= 10:
                self.r.add(CheckResult(
                    label="All Sports Results cell coverage",
                    status=PASS,
                    message=f"{pct_cells} stat cell(s) populated",
                    url="/all-sports-results", auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label="All Sports Results stat coverage",
                    status=FAIL,
                    message=f"Only {pct_cells} populated percentage cell(s) found",
                    detail="Dashboard may have loaded but season snapshot stats are missing.",
                    url="/all-sports-results", auditor=self.NAME))

        # ── Search functionality check ───────────────────────────────────
        # (a) The /api/search endpoint must return real results for a known team.
        try:
            resp = self.s.get(urljoin(self.base, "/api/search?query=Detroit"),
                              timeout=10, allow_redirects=True)
            data = resp.json() if resp.status_code == 200 else {}
            hits = (data.get("team_results") or []) + (data.get("espn_results") or [])
            if hits:
                self.r.add(CheckResult(
                    label="Search API", status=PASS,
                    message=f"/api/search?query=Detroit returned {len(hits)} result(s)",
                    auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label="Search API", status=FAIL,
                    message="/api/search returned no results for 'Detroit' "
                            "(a known team) — search backend is broken",
                    url="/api/search?query=Detroit", auditor=self.NAME))
        except Exception as exc:
            self.r.add(CheckResult(
                label="Search API", status=FAIL,
                message=f"/api/search request failed: {exc}",
                auditor=self.NAME))

        # (b) The navbar search OVERLAY must actually call /api/search.
        # (Bug class: renderSrchItems only filtering a static link list.)
        home = page_html.get("/", "") or self._fetch("/")
        m = re.search(r"function renderSrchItems\([^)]*\)\{", home)
        # Grab a wide window after the opening brace (the function is multi-line)
        srch_fn = home[m.end():m.end() + 900] if m else ""
        if srch_fn and "/api/search" in srch_fn:
            self.r.add(CheckResult(
                label="Search overlay wiring", status=PASS,
                message="Navbar search overlay calls /api/search",
                auditor=self.NAME))
        elif srch_fn:
            self.r.add(CheckResult(
                label="Search overlay wiring", status=FAIL,
                message="Navbar search overlay does NOT call /api/search — "
                        "it only filters static links, so team searches return nothing",
                url="/", auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# NAVIGATION AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class NavigationAuditor:
    NAME = "navigation"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _fetch(self, path: str) -> str:
        try:
            resp = self.s.get(urljoin(self.base, path),
                              timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return resp.text
        except Exception:
            return ""

    def _parse_menu_sections(self, html: str) -> dict:
        """Parse TV_MENUS JS object into {section_key: [(label, href), ...]}."""
        sections: dict[str, list] = {}
        m = re.search(r"var TV_MENUS=(\{.*?\}\});", html)
        if not m:
            return sections
        menu = m.group(1)
        for sec in re.finditer(
                r"(\w+):\{title:'([^']*)',items:\[(.*?)\]\}", menu):
            key = sec.group(1)
            items = re.findall(r"\{l:'([^']+)',h:'([^']+)'", sec.group(3))
            sections[key] = items
        return sections

    def run(self):
        cfg = json.loads((QA_DIR / "expected_navigation.json").read_text())
        html = self._fetch("/")
        if not html:
            self.r.add(CheckResult(
                label="Navigation fetch", status=FAIL,
                message="Could not load homepage for nav audit",
                auditor=self.NAME))
            return

        all_hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
        internal_hrefs = [h for h in all_hrefs
                          if h.startswith("/") and not h.startswith("//")]

        # ── Parse the actual menu sections from TV_MENUS ──────────────────
        sections = self._parse_menu_sections(html)
        section_cfg = cfg.get("menu_sections", {})

        # 1. Per-section required + forbidden link checks
        href_to_sections: dict[str, list] = {}
        for sec_key, spec in section_cfg.items():
            items = sections.get(sec_key, [])
            hrefs_in_sec = [h for _, h in items]
            title = spec.get("title", sec_key)

            for req in spec.get("required", []):
                if req in hrefs_in_sec:
                    self.r.add(CheckResult(
                        label=f"{title}: has {req}", status=PASS,
                        message="Present in correct section", auditor=self.NAME))
                else:
                    self.r.add(CheckResult(
                        label=f"{title}: missing {req}", status=FAIL,
                        message=f"Required link {req} is NOT in the {title} menu",
                        auditor=self.NAME))

            for bad in spec.get("forbidden", []):
                if bad in hrefs_in_sec:
                    self.r.add(CheckResult(
                        label=f"{title}: wrong-section link", status=FAIL,
                        message=f"{bad} should NOT be in {title} — "
                                f"it belongs in a different section",
                        auditor=self.NAME))

            for h in hrefs_in_sec:
                base_h = h.split("?")[0]
                href_to_sections.setdefault(base_h, [])
                if sec_key not in href_to_sections[base_h]:
                    href_to_sections[base_h].append(sec_key)

        # 2. Cross-section duplicate detection — same page in 2+ menu sections
        allowed = set(cfg.get("cross_section_allowed", []))
        cross_dupes = {h: secs for h, secs in href_to_sections.items()
                       if len(secs) > 1 and h not in allowed}
        if cross_dupes:
            for h, secs in cross_dupes.items():
                self.r.add(CheckResult(
                    label=f"Duplicate across menu sections: {h}",
                    status=FAIL,
                    message=f"{h} appears in multiple menu sections "
                            f"({', '.join(secs)}) — a page should live in one section",
                    auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Menu section placement", status=PASS,
                message="No link appears in more than one menu section",
                auditor=self.NAME))

        # 2b. Search-overlay section tags — a results page must NOT be tagged
        # 's:props' (it would show under the Props search filter).
        RESULTS_PAGES = {"/performance", "/edge-performance", "/all-sports-results",
                         "/daily-report", "/results/downloads", "/picks/export.csv"}
        srch_m = re.search(r"var _srchDefaults=\[(.*?)\];", html)
        if srch_m:
            mis = []
            for lbl, href, tag in re.findall(
                    r"\{l:'([^']+)',h:'([^']+)',s:'([^']+)'\}", srch_m.group(1)):
                if href in RESULTS_PAGES and tag == "props":
                    mis.append(f"{lbl} ({href})")
            if mis:
                self.r.add(CheckResult(
                    label="Search: results page tagged 'props'", status=FAIL,
                    message=f"{len(mis)} results page(s) mis-tagged into the Props "
                            f"search filter — wrong section",
                    detail=", ".join(mis), auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label="Search section tags", status=PASS,
                    message="No results page mis-tagged in the search overlay",
                    auditor=self.NAME))

        # 3. Footer links
        footer_m = re.search(
            r'<footer[^>]*>(.*?)</footer>', html,
            flags=re.DOTALL | re.IGNORECASE)
        footer_html = footer_m.group(1) if footer_m else ""
        footer_hrefs = re.findall(r'href=["\']([^"\']+)["\']', footer_html)

        for req in cfg.get("footer_required", []):
            if req in footer_hrefs:
                self.r.add(CheckResult(
                    label=f"Footer link: {req}", status=PASS,
                    message="Present in footer", auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"Footer link: {req}", status=WARN,
                    message="Not found in footer — verify manually",
                    auditor=self.NAME))

        # 4. Forbidden strings in nav HTML
        nav_m = re.search(r'<nav[^>]*>(.*?)</nav>', html,
                          flags=re.DOTALL | re.IGNORECASE)
        nav_html = nav_m.group(1) if nav_m else html[:10000]
        for bad in cfg.get("forbidden_in_nav", []):
            if bad.lower() in nav_html.lower():
                self.r.add(CheckResult(
                    label=f"Forbidden in nav: '{bad}'", status=FAIL,
                    message=f"'{bad}' found in navigation HTML",
                    auditor=self.NAME))

        # 5. Broken internal links (spot-check — key pages only)
        key_links = [
            "/nba-picks", "/nhl-picks", "/mlb-picks", "/plans",
            "/privacy", "/terms", "/faq", "/contact",
            "/daily-report", "/all-sports-results",
        ]
        broken = []
        checked = 0
        for href in key_links:
            try:
                resp = self.s.get(urljoin(self.base, href),
                                  timeout=4, allow_redirects=True)
                if resp.status_code == 404:
                    broken.append(href)
                checked += 1
            except Exception:
                pass

        if broken:
            self.r.add(CheckResult(
                label="Broken internal links", status=FAIL,
                message=f"{len(broken)} link(s) return 404",
                detail=", ".join(broken), auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Internal link spot-check", status=PASS,
                message=f"Checked {checked} key links — none returned 404",
                auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# PREDICTION CARD AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class CardAuditor:
    NAME = "cards"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _fetch(self, path: str) -> str:
        try:
            resp = self.s.get(urljoin(self.base, path),
                              timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return resp.text
        except Exception:
            return ""

    # Impossible line thresholds per prop type
    _MAX_BATTER_K  = 3.5   # a batter can't strike out more than ~3-4 times per game
    _MLB_BATTER_PROPS = {"hits", "runs", "rbis", "home_runs"}
    _MLB_PITCHER_PROPS = {"strikeouts"}

    def _audit_page(self, slug: str):
        html = self._fetch(f"/{slug}")
        if not html or len(html) < 5000:
            self.r.add(CheckResult(
                label=f"Card audit: {slug}", status=WARN,
                message="Page too small or empty — no cards audited",
                url=f"/{slug}", auditor=self.NAME))
            return

        sport = slug.replace("-picks", "").upper()

        # Sport line sanity check via props API
        SPORT_LINE_CAPS = {
            "NHL":    {"points": 3.0,  "assists": 2.0, "goals": 1.5, "shots_on_goal": 6.0},
            "SOCCER": {"goals": 2.0,   "assists": 2.0, "shots": 8.0},
            "MLB":    {"strikeouts": 15.0, "hits": 5.0, "home_runs": 2.0},
        }
        league_for_api = sport  # e.g. "NHL", "MLB"
        if league_for_api in SPORT_LINE_CAPS:
            try:
                api_url = urljoin(self.base, f"/player-props-api/props?league={league_for_api}")
                resp = self.s.get(api_url, timeout=8, allow_redirects=False)
                if resp.status_code == 200:
                    items = resp.json().get("items") or []
                    bad_lines = []
                    for row in items:
                        pt = row.get("prop_type", "")
                        cap = SPORT_LINE_CAPS[league_for_api].get(pt)
                        if cap is None:
                            continue
                        line = float(row.get("_calc_line") or row.get("line") or 0)
                        if line > cap * 1.5:   # 50% over cap = definitely wrong
                            bad_lines.append(
                                f"{row.get('player_name','?')} {pt}={line}")
                    if bad_lines:
                        self.r.add(CheckResult(
                            label=f"Impossible prop lines: {league_for_api}",
                            status=FAIL,
                            message=f"{len(bad_lines)} prop(s) have unrealistic lines",
                            detail=", ".join(bad_lines[:4]),
                            url=f"/player-props-api/props?league={league_for_api}",
                            auditor=self.NAME))
                    else:
                        self.r.add(CheckResult(
                            label=f"Prop lines sane: {league_for_api}", status=PASS,
                            message=f"All {len(items)} {league_for_api} lines within realistic range",
                            auditor=self.NAME))
            except Exception:
                pass

        # MLB-specific: detect K props with pitcher-scale lines on batters
        # The player props API returns JSON we can check
        if sport == "MLB":
            try:
                api_url = urljoin(self.base, "/player-props-api/props?league=MLB")
                resp = self.s.get(api_url, timeout=8, allow_redirects=False)
                if resp.status_code == 200:
                    data = resp.json()
                    k_props = [r for r in (data.get("items") or [])
                               if r.get("prop_type") == "strikeouts"]
                    bad_k = [r for r in k_props
                             if float(r.get("_calc_line") or r.get("line") or 0) > self._MAX_BATTER_K
                             and r.get("position_type", "pitcher") != "pitcher"]
                    if bad_k:
                        names = [r.get("player_name","?") for r in bad_k[:3]]
                        self.r.add(CheckResult(
                            label="MLB K props: pitcher lines on non-pitchers",
                            status=FAIL,
                            message=f"{len(bad_k)} batter(s) have pitcher-scale K lines: "
                                    f"{', '.join(names)}",
                            url="/player-props-api/props?league=MLB",
                            auditor=self.NAME))
                    else:
                        self.r.add(CheckResult(
                            label="MLB K props: position check", status=PASS,
                            message=f"All {len(k_props)} K props have appropriate lines",
                            auditor=self.NAME))
            except Exception:
                pass

        # OVER-only milestone props must never show an UNDER pick.
        # NHL: goals/points/assists.  MLB: hits/runs/rbis/home_runs.
        OVER_ONLY = {
            "NHL": {"goals", "points", "assists"},
            "MLB": {"hits", "runs", "rbis", "home_runs"},
            "SOCCER": {"goals", "assists"},
        }
        if sport in OVER_ONLY:
            try:
                api_url = urljoin(self.base, f"/player-props-api/props?league={sport}")
                resp = self.s.get(api_url, timeout=8, allow_redirects=False)
                if resp.status_code == 200:
                    items = resp.json().get("items") or []
                    bad_under = [
                        f"{r.get('player_name','?')} {r.get('prop_type')}"
                        for r in items
                        if r.get("prop_type") in OVER_ONLY[sport]
                        and str(r.get("picked_side", "")).upper() == "UNDER"
                    ]
                    if bad_under:
                        self.r.add(CheckResult(
                            label=f"{sport} milestone props: UNDER pick found",
                            status=FAIL,
                            message=f"{len(bad_under)} milestone prop(s) show UNDER — "
                                    f"these are anytime/over-only markets",
                            detail=", ".join(bad_under[:5]),
                            url=f"/player-props-api/props?league={sport}",
                            auditor=self.NAME))
                    else:
                        self.r.add(CheckResult(
                            label=f"{sport} milestone props: over-only", status=PASS,
                            message="No UNDER picks on anytime/milestone props",
                            auditor=self.NAME))
            except Exception:
                pass

        # Count cards
        card_count = html.lower().count('class="game-card pick-card"')
        if card_count == 0:
            card_count = html.lower().count('pick-card')

        if card_count == 0:
            self.r.add(CheckResult(
                label=f"Card count: {sport}", status=WARN,
                message="No prediction cards found on page — off-season or error?",
                url=f"/{slug}", auditor=self.NAME))
            return

        self.r.add(CheckResult(
            label=f"Card count: {sport}", status=INFO,
            message=f"{card_count} prediction card(s) found",
            url=f"/{slug}", auditor=self.NAME))

        # Required CSS classes
        cfg = json.loads((QA_DIR / "expected_card_fields.json").read_text())
        missing_classes = []
        for cls in cfg["required_css_classes"]:
            if f'class="{cls}"' not in html and f'"{cls}"' not in html:
                # fuzzy check
                if cls not in html:
                    missing_classes.append(cls)

        if missing_classes:
            self.r.add(CheckResult(
                label=f"Card fields: {sport}", status=WARN,
                message=f"Missing expected CSS classes: {missing_classes}",
                url=f"/{slug}", auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label=f"Card fields: {sport}", status=PASS,
                message="All required card field classes present",
                url=f"/{slug}", auditor=self.NAME))

        # Probability values — extract and validate range
        probs = re.findall(r'class="win-pct"[^>]*>(\d+\.?\d*)', html)
        if not probs:
            probs = re.findall(r'(\d{1,2}\.\d)\s*<span class="unit">%', html)

        invalid_probs = [p for p in probs if not (0 <= float(p) <= 100)]
        if invalid_probs:
            self.r.add(CheckResult(
                label=f"Probability range: {sport}", status=FAIL,
                message=f"Out-of-range probabilities: {invalid_probs[:5]}",
                url=f"/{slug}", auditor=self.NAME))
        elif probs:
            self.r.add(CheckResult(
                label=f"Probability range: {sport}", status=PASS,
                message=f"All {len(probs)} probability values in range [0,100]",
                url=f"/{slug}", auditor=self.NAME))

        # Clustering detection
        if len(probs) >= 5:
            clustered = [p for p in probs
                         if CLUSTER_RANGE_LOW <= float(p) <= CLUSTER_RANGE_HIGH]
            pct_clustered = len(clustered) / len(probs) * 100
            if pct_clustered >= CLUSTER_WARN_PCT:
                self.r.add(CheckResult(
                    label=f"Model clustering: {sport}", status=WARN,
                    message=(f"{pct_clustered:.0f}% of probabilities are between "
                             f"{CLUSTER_RANGE_LOW}% and {CLUSTER_RANGE_HIGH}% — "
                             "possible fallback model detected"),
                    url=f"/{slug}", auditor=self.NAME))

        # Confidence values
        conf_vals = re.findall(
            r'<strong>(\d+\.?\d*)%</strong>\s*</div>\s*</div>', html)
        if not conf_vals:
            conf_vals = re.findall(
                r'Confidence\s*<strong>(\d+\.?\d*)%</strong>', html)
        if conf_vals:
            self.r.add(CheckResult(
                label=f"Confidence values: {sport}", status=INFO,
                message=f"{len(conf_vals)} confidence value(s), "
                        f"range: {min(float(c) for c in conf_vals):.1f}%–"
                        f"{max(float(c) for c in conf_vals):.1f}%",
                url=f"/{slug}", auditor=self.NAME))

    def run(self):
        active = ["nba-picks", "nhl-picks", "mlb-picks", "wnba-picks",
                  "soccer-picks"]
        for slug in active:
            self._audit_page(slug)


# ═══════════════════════════════════════════════════════════════════════════
# MODEL OUTPUT AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class ModelAuditor:
    NAME = "models"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _fetch(self, path: str) -> str:
        try:
            resp = self.s.get(urljoin(self.base, path),
                              timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return resp.text
        except Exception:
            return ""

    def run(self):
        cfg = json.loads((QA_DIR / "expected_models.json").read_text())

        for slug in ["nba-picks", "nhl-picks", "mlb-picks"]:
            sport = slug.replace("-picks", "").upper()
            html = self._fetch(f"/{slug}")
            if not html or len(html) < 5000:
                continue

            # Expected model display labels
            missing_models = []
            for label in cfg["expected_page_labels"]:
                if label.lower() not in html.lower():
                    missing_models.append(label)

            if missing_models:
                self.r.add(CheckResult(
                    label=f"Model labels: {sport}", status=WARN,
                    message=f"Missing model label(s): {missing_models}",
                    url=f"/{slug}", auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"Model labels: {sport}", status=PASS,
                    message="All expected model labels found",
                    url=f"/{slug}", auditor=self.NAME))

            # Stale model names
            for stale in cfg.get("stale_model_names", []):
                if stale.lower() in html.lower():
                    self.r.add(CheckResult(
                        label=f"Stale model name: {sport}", status=WARN,
                        message=f"Found stale model reference: '{stale}'",
                        url=f"/{slug}", auditor=self.NAME))

            # Check "via" labels (should mention model names)
            via_labels = re.findall(r'<span class="via">via ([^<]+)</span>', html)
            if via_labels:
                self.r.add(CheckResult(
                    label=f"Pick attribution: {sport}", status=INFO,
                    message=f"Pick attributed to: "
                            f"{', '.join(set(via_labels[:5]))}",
                    url=f"/{slug}", auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"Pick attribution: {sport}", status=WARN,
                    message="No 'via [model]' attribution found on cards",
                    url=f"/{slug}", auditor=self.NAME))

        # Check performance page shows all models
        perf_html = self._fetch("/performance")
        if perf_html and len(perf_html) > 5000:
            for model in ["Grinder2", "Takedown", "Edge", "XSharp", "Consensus"]:
                if model.lower() in perf_html.lower():
                    self.r.add(CheckResult(
                        label=f"Performance page: {model}", status=PASS,
                        message="Model appears on performance page",
                        auditor=self.NAME))
                else:
                    self.r.add(CheckResult(
                        label=f"Performance page: {model}", status=WARN,
                        message="Model NOT found on performance page",
                        auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# RESULTS MATH AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class ResultsAuditor:
    NAME = "results"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _fetch(self, path: str) -> str:
        try:
            resp = self.s.get(urljoin(self.base, path),
                              timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return resp.text
        except Exception:
            return ""

    def run(self):
        html = self._fetch("/all-sports-results")
        if not html or len(html) < 5000:
            self.r.add(CheckResult(
                label="Results page load", status=WARN,
                message="Could not load /all-sports-results",
                auditor=self.NAME))
            return

        self.r.add(CheckResult(
            label="Results page load", status=PASS,
            message=f"Page loaded ({len(html):,} bytes)",
            auditor=self.NAME))

        # Parse each percentage TOGETHER with its adjacent W-L record from the
        # same cell (markup is "<pct>%\n<w>-<l>"). Pairing two independent regex
        # lists by index misaligns them and produces false mismatches.
        pairs = re.findall(r'(\d{1,3}(?:\.\d)?)\s*%\s*<[^>]*>\s*(\d+)\s*-\s*(\d+)', html)
        if not pairs:
            # Fallback: tolerant whitespace/markup between pct and record
            pairs = re.findall(r'(\d{1,3}(?:\.\d)?)\s*%[^0-9]{0,40}?(\d+)\s*-\s*(\d+)', html)

        if not pairs:
            self.r.add(CheckResult(
                label="Results math", status=INFO,
                message="No paired pct+record cells found to validate "
                        "(layout may have changed)",
                auditor=self.NAME))
            return

        mismatches = []
        checked = 0
        for pct_str, w_str, l_str in pairs:
            w, l = int(w_str), int(l_str)
            total = w + l
            if total < 5:          # skip tiny samples (rounding noise dominates)
                continue
            displayed = float(pct_str)
            calc_pct = round(w / total * 100, 1)
            checked += 1
            # Allow 1pt tolerance for rounding / push handling
            if abs(calc_pct - displayed) > 1.0:
                mismatches.append(
                    f"{w}-{l}: shows {displayed}% but {w}/{total} = {calc_pct}%")

        if mismatches:
            self.r.add(CheckResult(
                label="Results math validation", status=WARN,
                message=f"{len(mismatches)} possible pct mismatches",
                detail="; ".join(mismatches[:5]),
                auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Results math validation", status=PASS,
                message=f"Validated {checked} pct+record cells — "
                        f"displayed percentages match the math",
                auditor=self.NAME))

        # Model anomaly: a model going 0% or 100% over a MEANINGFUL sample
        records = re.findall(r'(\d+)\s*-\s*(\d+)', html)
        # (n >= 15) is a red flag. Small samples (a 3-game slate) are just
        # variance and are intentionally ignored to avoid false alarms.
        ANOMALY_MIN_N = 15
        PERFECT_MIN_N = 25     # X-0 / 0-X over 25+ is mathematically impossible
        anomalies, perfect = [], []
        for (w_str, l_str) in records:
            w, l = int(w_str), int(l_str)
            n = w + l
            if (w == 0 or l == 0) and n >= PERFECT_MIN_N:
                perfect.append(f"{w}-{l}")
            elif n >= ANOMALY_MIN_N and (w == 0 or l == 0):
                anomalies.append(f"{w}-{l}")
        if perfect:
            # A perfect record over a big sample is impossible in real betting —
            # it means grading is fabricated (e.g. grading the pick against its
            # own projection instead of the real game outcome).
            self.r.add(CheckResult(
                label="FABRICATED RESULTS (perfect record)", status=FAIL,
                message=f"{len(perfect)} record(s) show a PERFECT {PERFECT_MIN_N}+ "
                        f"game result (e.g. {perfect[0]}) — impossible in real "
                        f"betting; grading is not using real outcomes",
                detail=", ".join(perfect[:8]), auditor=self.NAME))
        elif anomalies:
            self.r.add(CheckResult(
                label="Model anomaly (0% / 100%)", status=WARN,
                message=f"{len(anomalies)} model record(s) at 0% or 100% over "
                        f"{ANOMALY_MIN_N}+ games — likely a grading/model bug",
                detail=", ".join(anomalies[:6]), auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Model anomaly check", status=PASS,
                message=f"No fabricated/anomalous records over the sample threshold",
                auditor=self.NAME))

        # Check daily report
        daily_html = self._fetch("/daily-report")
        if daily_html and len(daily_html) > 5000:
            self.r.add(CheckResult(
                label="Daily report loads", status=PASS,
                message=f"Daily report page: {len(daily_html):,} bytes",
                auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Daily report loads", status=WARN,
                message="Daily report page appears empty",
                auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# CSV EXPORT AUDITOR
# ═══════════════════════════════════════════════════════════════════════════

class CsvAuditor:
    NAME = "csv"

    def __init__(self, session: requests.Session, base: str, report: AuditReport):
        self.s = session
        self.base = base
        self.r = report

    def _fetch(self, path: str) -> tuple[int, str]:
        try:
            resp = self.s.get(urljoin(self.base, path),
                              timeout=REQUEST_TIMEOUT, allow_redirects=False)
            return resp.status_code, resp.text
        except Exception as exc:
            return -1, str(exc)

    def run(self):
        exports = [
            ("/picks/export.csv",       "Picks export CSV"),
            ("/results/export.csv",     "Results export CSV"),
            ("/performance/audit.csv",  "Performance audit CSV"),
        ]
        for path, label in exports:
            code, body = self._fetch(path)
            if code == 302:
                self.r.add(CheckResult(
                    label=label, status=PASS,
                    message="Correctly requires auth (302 redirect)",
                    url=path, auditor=self.NAME))
            elif code == 200:
                # Should not be accessible without auth
                lines = body.strip().splitlines()
                if len(lines) < 2:
                    self.r.add(CheckResult(
                        label=label, status=WARN,
                        message="CSV accessible without auth but appears empty",
                        url=path, auditor=self.NAME))
                else:
                    # Validate CSV structure
                    header = lines[0].lower()
                    has_pick    = "pick" in header
                    has_pct     = any(x in header for x in
                                      ["pct", "prob", "confidence", "percent"])
                    issues = []
                    if not has_pick:  issues.append("no 'pick' column")
                    if not has_pct:   issues.append("no probability column")
                    if issues:
                        self.r.add(CheckResult(
                            label=label, status=WARN,
                            message=f"CSV accessible without auth. Column issues: "
                                    f"{', '.join(issues)}",
                            url=path, auditor=self.NAME))
                    else:
                        self.r.add(CheckResult(
                            label=label, status=INFO,
                            message=f"CSV accessible without auth "
                                    f"({len(lines)-1} rows, columns OK)",
                            url=path, auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=label, status=WARN,
                    message=f"Unexpected HTTP {code}",
                    url=path, auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# SCREENSHOT AUDITOR (Playwright — optional)
# ═══════════════════════════════════════════════════════════════════════════

class PropsAuditor:
    """Audits the player-props engine directly (the API is auth-gated, so we
    import the engine). Catches lies, impossible lines, mis-placed values, and
    fabricated diagnostics."""
    NAME = "props"

    # Max sane line per prop type — a line above this is nonsense.
    LINE_CAPS = {
        "points": 45, "rebounds": 22, "assists": 18, "threes": 7,
        "shots_on_goal": 7, "goals": 1, "hits": 4, "runs": 3, "rbis": 3,
        "home_runs": 1, "strikeouts": 14,
        # MLB extended props (real gamelog-based lines)
        "singles": 5, "doubles": 4, "total_bases": 8, "stolen_bases": 4,
        "earned_runs": 10, "hits_allowed": 15, "walks": 8, "outs": 27,
        "h_r_rbi": 9,
        # NBA combined ("parlay") props
        "pts_reb": 60, "pts_ast": 55, "reb_ast": 35, "pts_reb_ast": 75,
        "passing_yards": 400, "rushing_yards": 200, "receiving_yards": 220,
        "receptions": 16, "shots": 8, "shots_on_target": 6,
        "aces": 30, "double_faults": 12, "significant_strikes": 220,
        "takedowns": 10, "birdies": 12, "bogeys": 10,
    }
    OVER_ONLY = {
        "NHL": {"goals", "points", "assists"},
        "SOCCER": {"goals", "assists"},
    }

    def __init__(self, report: AuditReport):
        self.r = report

    def _load_engine(self):
        import sys as _sys
        backend = str((QA_DIR.parent / "standalone-player-props" / "backend"))
        if backend not in _sys.path:
            _sys.path.insert(0, backend)
        from app import engine  # type: ignore
        return engine

    def run(self):
        try:
            engine = self._load_engine()
        except Exception as exc:
            self.r.add(CheckResult(
                label="Props engine import", status=WARN,
                message=f"Could not import props engine: {exc}", auditor=self.NAME))
            return

        # In-season sports worth auditing live
        sports = ["NBA", "WNBA", "NHL", "MLB"]
        for sport in sports:
            try:
                engine._CACHE.pop(sport, None)
                data = engine.get_league_data(sport)
                props = data.get("props") or []
            except Exception as exc:
                self.r.add(CheckResult(
                    label=f"Props load: {sport}", status=WARN,
                    message=f"get_league_data failed: {exc}", auditor=self.NAME))
                continue
            if not props:
                continue

            # 1. Nonsensical lines OR projections (the 3PT bug was a 6.68
            #    *projection* from reading 3PT attempts instead of makes).
            bad_lines = []
            for p in props:
                pt = p.get("prop_type", "")
                cap = self.LINE_CAPS.get(pt)
                if cap is None:
                    continue
                line = float(p.get("_calc_line") or p.get("line") or 0)
                proj = float(p.get("projection") or 0)
                if line > cap:
                    bad_lines.append(f"{p.get('player_name','?')} {pt} line={line}")
                elif proj > cap:
                    bad_lines.append(f"{p.get('player_name','?')} {pt} proj={proj}")
            if bad_lines:
                self.r.add(CheckResult(
                    label=f"{sport} impossible lines", status=FAIL,
                    message=f"{len(bad_lines)} prop(s) have nonsensical lines",
                    detail=", ".join(bad_lines[:5]), auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"{sport} line sanity", status=PASS,
                    message=f"All {len(props)} lines within realistic bounds",
                    auditor=self.NAME))

            # 2. Missing/blank required fields (values in wrong/empty cells)
            missing = [p.get("player_name", "?") for p in props
                       if p.get("projection") is None or p.get("picked_side") in (None, "")
                       or p.get("confidence_score") is None]
            if missing:
                self.r.add(CheckResult(
                    label=f"{sport} missing prop values", status=FAIL,
                    message=f"{len(missing)} prop(s) missing projection/pick/confidence",
                    detail=", ".join(missing[:5]), auditor=self.NAME))

            # 3. Over-only props must not show UNDER
            oo = self.OVER_ONLY.get(sport, set())
            bad_under = [f"{p.get('player_name','?')} {p.get('prop_type')}"
                         for p in props if p.get("prop_type") in oo
                         and str(p.get("picked_side", "")).upper() == "UNDER"]
            if bad_under:
                self.r.add(CheckResult(
                    label=f"{sport} milestone UNDER pick", status=FAIL,
                    message=f"{len(bad_under)} over-only prop(s) show UNDER",
                    detail=", ".join(bad_under[:5]), auditor=self.NAME))

            # 4. Diagnostics integrity
            try:
                diag = engine.get_diagnostics(sport)
                self._audit_diagnostics(sport, diag)
            except Exception:
                pass

    def _audit_diagnostics(self, sport: str, diag: dict):
        if not diag or diag.get("error"):
            return
        # Feature collapse: a single feature carrying everything = broken pipeline
        fi = diag.get("feature_importance") or {}
        nonzero = {k: v for k, v in fi.items() if abs(float(v or 0)) > 0.001}
        if fi and len(nonzero) <= 1:
            self.r.add(CheckResult(
                label=f"{sport} diagnostics: feature collapse", status=WARN,
                message=f"Only {len(nonzero)} feature(s) carry signal "
                        f"({', '.join(nonzero) or 'none'}) — feature importance is degenerate",
                auditor=self.NAME))
        # Impossible "100% positive EV across every prop type"
        bpt = diag.get("by_prop_type") or {}
        all_100 = bpt and all(s.get("positive_ev_pct") == 100 for s in bpt.values())
        if all_100 and len(bpt) >= 3:
            self.r.add(CheckResult(
                label=f"{sport} diagnostics: 100% +EV everywhere", status=WARN,
                message="Every prop type shows 100% positive EV — EV is derived "
                        "from model odds, not a real market, so this isn't a true edge",
                auditor=self.NAME))
        # Degenerate probability distribution (everything at 50%)
        stab = (diag.get("distribution_stability") or {}).get("label")
        if stab == "degenerate":
            self.r.add(CheckResult(
                label=f"{sport} diagnostics: degenerate distribution", status=WARN,
                message="All pick probabilities clustered near 50% — possible fallback model",
                auditor=self.NAME))


class ScreenshotAuditor:
    NAME = "screenshots"

    def __init__(self, base: str, report: AuditReport):
        self.base = base
        self.r = report

    def run(self):
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            self.r.add(CheckResult(
                label="Screenshots", status=WARN,
                message="Playwright not installed. "
                        "Run: pip install playwright && playwright install chromium",
                auditor=self.NAME))
            return

        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        from audit_config import SCREENSHOT_PAGES

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})

                for pg in SCREENSHOT_PAGES:
                    url = urljoin(self.base, pg["path"])
                    name = pg["name"]
                    out_path = SCREENSHOTS_DIR / f"{name}.png"
                    prev_path = SCREENSHOTS_DIR / f"{name}_prev.png"

                    try:
                        page.goto(url, wait_until="networkidle", timeout=20000)
                        page.wait_for_timeout(1000)

                        # Rotate: current → prev, new → current
                        if out_path.exists():
                            out_path.rename(prev_path)

                        page.screenshot(path=str(out_path), full_page=True)

                        self.r.add(CheckResult(
                            label=f"Screenshot: {name}", status=PASS,
                            message=f"Saved to {out_path.name}",
                            url=pg["path"], auditor=self.NAME))

                    except Exception as exc:
                        self.r.add(CheckResult(
                            label=f"Screenshot: {name}", status=WARN,
                            message=f"Failed: {exc}",
                            url=pg["path"], auditor=self.NAME))

                browser.close()

        except Exception as exc:
            self.r.add(CheckResult(
                label="Screenshot audit", status=WARN,
                message=f"Playwright run failed: {exc}",
                auditor=self.NAME))


# ═══════════════════════════════════════════════════════════════════════════
# REPORT GENERATORS
# ═══════════════════════════════════════════════════════════════════════════

STATUS_ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", INFO: "ℹ️"}
STATUS_COLOR = {PASS: "#22c55e", WARN: "#f59e0b", FAIL: "#ef4444", INFO: "#3b82f6"}
STATUS_BG    = {PASS: "#f0fdf4", WARN: "#fffbeb", FAIL: "#fef2f2", INFO: "#eff6ff"}


def generate_txt_report(report: AuditReport) -> str:
    lines = [
        "=" * 70,
        f"  PredictionLab Site Audit — {report.timestamp}",
        f"  Target: {report.base_url}",
        f"  Run ID: {report.run_id}",
        f"  Duration: {report.duration:.1f}s",
        "=" * 70,
        f"  OVERALL: {report.overall}",
        f"  ✅ {len(report.passes)} passed | "
        f"⚠️  {len(report.warnings)} warnings | "
        f"❌ {len(report.failures)} failures | "
        f"ℹ️  {len(report.infos)} info",
        "=" * 70,
        "",
    ]

    # Group by auditor
    auditors: dict[str, list[CheckResult]] = {}
    for c in report.checks:
        auditors.setdefault(c.auditor or "general", []).append(c)

    for auditor_name, checks in auditors.items():
        lines.append(f"── {auditor_name.upper()} AUDIT ──────────────────────────────")
        for c in checks:
            icon = STATUS_ICON.get(c.status, " ")
            lines.append(f"  {icon} [{c.status}] {c.label}")
            lines.append(f"       {c.message}")
            if c.detail:
                lines.append(f"       Detail: {c.detail}")
            if c.url:
                lines.append(f"       URL: {c.url}")
        lines.append("")

    if report.failures:
        lines += ["", "═" * 70, "  FAILURES SUMMARY", "═" * 70]
        for c in report.failures:
            lines.append(f"  ❌ {c.label}: {c.message}")

    if report.warnings:
        lines += ["", "═" * 70, "  WARNINGS SUMMARY", "═" * 70]
        for c in report.warnings:
            lines.append(f"  ⚠️  {c.label}: {c.message}")

    return "\n".join(lines)


def generate_html_report(report: AuditReport) -> str:
    def row(c: CheckResult) -> str:
        bg    = STATUS_BG.get(c.status, "#fff")
        color = STATUS_COLOR.get(c.status, "#000")
        icon  = STATUS_ICON.get(c.status, " ")
        detail_html = (f'<div style="color:#64748b;font-size:0.8em;'
                       f'margin-top:3px;">{c.detail}</div>') if c.detail else ""
        url_html = (f'<a href="{c.url}" style="color:#2563eb;font-size:0.8em;"'
                    f'>{c.url}</a>') if c.url else ""
        return (
            f'<tr style="background:{bg};">'
            f'<td style="padding:8px 12px;font-size:1.1em;">{icon}</td>'
            f'<td style="padding:8px 12px;font-weight:700;color:{color};">'
            f'{c.status}</td>'
            f'<td style="padding:8px 12px;font-weight:600;">{c.label}</td>'
            f'<td style="padding:8px 12px;color:#475569;">{c.message}'
            f'{detail_html}{url_html}</td>'
            f'</tr>'
        )

    overall_color = STATUS_COLOR.get(report.overall, "#000")
    overall_bg    = STATUS_BG.get(report.overall, "#fff")

    # Group by auditor
    sections = ""
    auditors: dict[str, list[CheckResult]] = {}
    for c in report.checks:
        auditors.setdefault(c.auditor or "general", []).append(c)

    for auditor_name, checks in auditors.items():
        passes  = sum(1 for c in checks if c.status == PASS)
        warns   = sum(1 for c in checks if c.status == WARN)
        fails   = sum(1 for c in checks if c.status == FAIL)
        rows_html = "\n".join(row(c) for c in checks)
        sections += f"""
        <div style="margin-bottom:32px;">
          <h2 style="font-size:1rem;font-weight:800;text-transform:uppercase;
                     letter-spacing:0.5px;color:#0f172a;border-bottom:2px solid #e2e8f0;
                     padding-bottom:8px;margin-bottom:12px;">
            {auditor_name.replace('_',' ').title()} Audit
            <span style="font-size:0.8em;font-weight:600;color:#64748b;margin-left:8px;">
              ✅{passes} ⚠️{warns} ❌{fails}
            </span>
          </h2>
          <table style="width:100%;border-collapse:collapse;font-size:0.88rem;">
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PredictionLab QA Report — {report.timestamp}</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          margin:0;padding:0;background:#f8fafc;color:#0f172a;}}
    .wrap{{max-width:1100px;margin:0 auto;padding:32px 24px;}}
    .header{{background:#0f172a;color:#fff;border-radius:14px;padding:28px 32px;margin-bottom:28px;}}
    .header h1{{margin:0 0 6px;font-size:1.5rem;}}
    .header p{{margin:0;color:#94a3b8;font-size:0.88rem;}}
    .summary{{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap;}}
    .stat-card{{flex:1;min-width:120px;background:#fff;border:1px solid #e2e8f0;
                border-radius:12px;padding:16px 20px;text-align:center;}}
    .stat-num{{font-size:2rem;font-weight:900;line-height:1;}}
    .stat-lbl{{font-size:0.72rem;font-weight:700;text-transform:uppercase;
               letter-spacing:0.5px;color:#64748b;margin-top:4px;}}
    .overall{{padding:16px 24px;border-radius:12px;margin-bottom:28px;
              font-weight:800;font-size:1.1rem;}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>🔍 PredictionLab Automated QA Report</h1>
    <p>Run: {report.run_id} &nbsp;|&nbsp; {report.timestamp} &nbsp;|&nbsp;
       Target: {report.base_url} &nbsp;|&nbsp; Duration: {report.duration:.1f}s</p>
  </div>

  <div class="overall" style="background:{overall_bg};color:{overall_color};
                               border:2px solid {overall_color};">
    Overall Result: {report.overall} — {len(report.passes)} passed,
    {len(report.warnings)} warnings, {len(report.failures)} failures
  </div>

  <div class="summary">
    <div class="stat-card">
      <div class="stat-num" style="color:#22c55e;">{len(report.passes)}</div>
      <div class="stat-lbl">Passed</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:#f59e0b;">{len(report.warnings)}</div>
      <div class="stat-lbl">Warnings</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:#ef4444;">{len(report.failures)}</div>
      <div class="stat-lbl">Failures</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:#3b82f6;">{len(report.infos)}</div>
      <div class="stat-lbl">Info</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{len(report.checks)}</div>
      <div class="stat-lbl">Total Checks</div>
    </div>
  </div>

  {sections}
</div>
</body>
</html>"""


def generate_fix_prompt(report: AuditReport) -> str:
    failures = report.failures
    warnings = report.warnings

    if not failures and not warnings:
        return ("The automated QA audit found no issues.\n"
                "All checks passed. No fixes required.\n")

    lines = [
        "The automated QA audit for PredictionLab detected the following issues:",
        f"Audit run: {report.run_id}  |  {report.timestamp}",
        f"Target: {report.base_url}",
        "",
    ]

    if failures:
        lines.append("FAILURES (must fix):")
        for c in failures:
            lines.append(f"  - [{c.auditor}] {c.label}: {c.message}")
            if c.detail:
                lines.append(f"    Detail: {c.detail}")
            if c.url:
                lines.append(f"    Page: {c.url}")

    if warnings:
        lines.append("")
        lines.append("WARNINGS (should investigate):")
        for c in warnings:
            lines.append(f"  - [{c.auditor}] {c.label}: {c.message}")
            if c.detail:
                lines.append(f"    Detail: {c.detail}")
            if c.url:
                lines.append(f"    Page: {c.url}")

    lines += [
        "",
        "=" * 60,
        "REQUIREMENTS FOR CLAUDE:",
        "=" * 60,
        "  - Fix ONLY the issues listed above.",
        "  - Do NOT refactor unrelated code.",
        "  - Do NOT modify model architecture or prediction logic.",
        "  - Do NOT touch backup folders or archived files.",
        "  - Do NOT change working frontend layouts.",
        "  - Preserve all existing APIs and route signatures.",
        "  - After applying fixes, re-run: python qa/site_audit.py",
        "  - Verify the fixed checks now show PASS.",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL SENDER
# ═══════════════════════════════════════════════════════════════════════════

def _send_via_mailapp(to: str, subject: str, body: str,
                      html_path: Path, txt_path: Path, prompt_path: Path) -> bool:
    """Send through macOS Mail.app using AppleScript — no passwords needed.
    Writes body to a temp file to avoid AppleScript string-escaping issues
    with Unicode characters (emojis, special chars in report summaries).
    """
    import subprocess, tempfile, re

    # Strip emoji/unicode that AppleScript can't handle in string literals;
    # the full detail is in the attached HTML/TXT files anyway.
    clean_body = re.sub(r'[^\x00-\x7F]+', '', body)
    # Safe-escape for AppleScript double-quoted string
    safe_subject = subject.replace('\\', '\\\\').replace('"', '\\"')
    safe_body    = clean_body.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    safe_to      = to.replace('"', '\\"')

    # Build attachment lines
    attach_lines = []
    for p in [html_path, txt_path, prompt_path]:
        if p and p.exists():
            safe_p = str(p).replace('\\', '\\\\').replace('"', '\\"')
            attach_lines.append(
                f'make new attachment with properties {{file name:POSIX file "{safe_p}"}} '
                f'at after the last paragraph of content'
            )
    attach_block = "\n        ".join(attach_lines)

    script = f'''tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{safe_subject}", content:"{safe_body}", visible:false}}
    tell newMsg
        make new to recipient at end of to recipients with properties {{address:"{safe_to}"}}
        {attach_block}
    end tell
    send newMsg
end tell'''

    # Ensure Mail.app is running first (cold-start under launchd was dropping
    # the 8am email). Launch it, then retry the send up to 2 times.
    try:
        subprocess.run(["open", "-a", "Mail"], capture_output=True, timeout=15)
    except Exception:
        pass

    last_err = ""
    for attempt in range(2):
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=90)
            if result.returncode == 0:
                return True
            last_err = (result.stderr or "").strip()
        except Exception as exc:
            last_err = str(exc)
        import time as _t
        _t.sleep(5)

    if last_err:
        print(f"  Mail.app error: {last_err[:200]}")
    return False


def send_email_report(report: AuditReport,
                      html_path: Path, txt_path: Path, prompt_path: Path):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    subject = (f"[PredictionLab Checker] {report.overall} — "
               f"{len(report.failures)} failures, "
               f"{len(report.warnings)} warnings — {report.timestamp}")

    body_text = (
        f"PredictionLab Site Checker Report\n"
        f"Run ID: {report.run_id}\n"
        f"Time:   {report.timestamp}\n"
        f"Target: {report.base_url}\n\n"
        f"Overall: {report.overall}\n"
        f"  ✅ {len(report.passes)} passed\n"
        f"  ⚠️  {len(report.warnings)} warnings\n"
        f"  ❌ {len(report.failures)} failures\n\n"
    )
    if report.failures:
        body_text += "FAILURES:\n"
        for c in report.failures:
            body_text += f"  • {c.label}: {c.message}\n"
        body_text += "\n"
    if report.warnings:
        body_text += "WARNINGS:\n"
        for c in report.warnings[:10]:
            body_text += f"  • {c.label}: {c.message}\n"

    # ── Try macOS Mail.app first (uses already-configured account, no passwords) ──
    print(f"  ✉️  Sending via Mail.app to {EMAIL_TO}…")
    if _send_via_mailapp(EMAIL_TO, subject, body_text, html_path, txt_path, prompt_path):
        print(f"  ✅ Email sent to {EMAIL_TO}")
        return

    print("  Mail.app unavailable — trying SMTP…")

    # ── SMTP fallback ──────────────────────────────────────────────────────
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print("  ⚠️  SMTP skipped: open qa/checker_email.py and add credentials")
        return

    try:
        msg = MIMEMultipart("mixed")
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain"))
        for fpath in [html_path, txt_path, prompt_path]:
            if fpath and fpath.exists():
                with open(fpath, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition",
                                f"attachment; filename={fpath.name}")
                msg.attach(part)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"  ✅ Email sent to {EMAIL_TO} via SMTP")
    except Exception as exc:
        print(f"  ❌ Email failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_audit(args, base_url: str | None = None) -> AuditReport:
    import time
    start = time.time()
    base = base_url or BASE_URL

    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    report = AuditReport(run_id=run_id, timestamp=ts, base_url=base)

    print(f"\n{'='*60}")
    print(f"  PredictionLab QA Audit")
    print(f"  Target: {base}")
    print(f"  Mode: {'quick' if args.quick else 'full'}")
    print(f"  Run ID: {run_id}")
    print(f"{'='*60}\n")

    session = _make_session()

    # Determine which auditors to run
    auditors_to_run = QUICK_MODE_AUDITORS if args.quick else FULL_MODE_AUDITORS

    print("  ▶ Preflight server check…")
    reachable, preflight_fail = preflight_server(session, base)
    if not reachable:
        report.add(preflight_fail)
        print(f"  ❌ {preflight_fail.message}")
        print("  ⏭  Skipping HTTP auditors — server unreachable")
        report.duration = time.time() - start
        return report
    print("  ✅ Server reachable")

    def run_auditor(name: str, fn):
        if name not in auditors_to_run and not args.full:
            return
        if name in HTTP_DEPENDENT_AUDITORS and not reachable:
            return
        print(f"  ▶ Running {name} audit…")
        try:
            fn()
        except Exception as exc:
            report.add(CheckResult(
                label=f"{name} auditor crash", status=FAIL,
                message=str(exc),
                detail=traceback.format_exc()[-400:],
                auditor=name))

    run_auditor("routes",     lambda: RouteAuditor(session, base, report).run())
    run_auditor("content",    lambda: ContentAuditor(session, base, report).run())
    run_auditor("navigation", lambda: NavigationAuditor(session, base, report).run())
    run_auditor("cards",      lambda: CardAuditor(session, base, report).run())
    run_auditor("models",     lambda: ModelAuditor(session, base, report).run())
    run_auditor("results",    lambda: ResultsAuditor(session, base, report).run())
    run_auditor("csv",        lambda: CsvAuditor(session, base, report).run())
    # Props auditor imports the engine directly (props API is auth-gated) —
    # not HTTP-dependent, so it runs even if the site is unreachable.
    run_auditor("props",      lambda: PropsAuditor(report).run())

    if args.screenshots:
        print("  ▶ Running screenshot audit…")
        try:
            ScreenshotAuditor(base, report).run()
        except Exception as exc:
            report.add(CheckResult(
                label="screenshot auditor crash", status=WARN,
                message=str(exc), auditor="screenshots"))

    report.duration = time.time() - start
    return report


def save_reports(report: AuditReport) -> tuple[Path, Path, Path, Path]:
    # TXT
    txt = generate_txt_report(report)
    txt_path = QA_DIR / "latest_report.txt"
    txt_path.write_text(txt, encoding="utf-8")

    # JSON
    json_data = {
        "run_id":    report.run_id,
        "timestamp": report.timestamp,
        "base_url":  report.base_url,
        "duration":  report.duration,
        "overall":   report.overall,
        "summary": {
            "passes":   len(report.passes),
            "warnings": len(report.warnings),
            "failures": len(report.failures),
            "infos":    len(report.infos),
        },
        "checks": [
            {"label": c.label, "status": c.status,
             "message": c.message, "detail": c.detail,
             "auditor": c.auditor, "url": c.url}
            for c in report.checks
        ],
    }
    json_path = QA_DIR / "latest_report.json"
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

    # HTML
    html = generate_html_report(report)
    html_path = QA_DIR / "latest_report.html"
    html_path.write_text(html, encoding="utf-8")

    # Claude fix prompt
    prompt = generate_fix_prompt(report)
    prompt_path = QA_DIR / "qa_fix_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    # Archive to history
    archive_dir = HISTORY_DIR / report.run_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "report.txt").write_text(txt, encoding="utf-8")
    (archive_dir / "report.json").write_text(
        json.dumps(json_data, indent=2), encoding="utf-8")
    (archive_dir / "report.html").write_text(html, encoding="utf-8")
    (archive_dir / "fix_prompt.txt").write_text(prompt, encoding="utf-8")

    return html_path, json_path, txt_path, prompt_path


def print_summary(report: AuditReport):
    print(f"\n{'='*60}")
    print(f"  AUDIT COMPLETE — {report.overall}")
    print(f"  Duration: {report.duration:.1f}s")
    print(f"  ✅ {len(report.passes)} passed | "
          f"⚠️  {len(report.warnings)} warnings | "
          f"❌ {len(report.failures)} failures | "
          f"ℹ️  {len(report.infos)} info")
    print(f"{'='*60}")

    if report.failures:
        print("\n  ❌ FAILURES:")
        for c in report.failures:
            print(f"    • [{c.auditor}] {c.label}")
            print(f"      {c.message}")

    if report.warnings:
        print("\n  ⚠️  WARNINGS:")
        for c in report.warnings:
            print(f"    • [{c.auditor}] {c.label}")
            print(f"      {c.message}")

    print(f"\n  Reports saved to: {QA_DIR}/")
    print(f"    latest_report.html")
    print(f"    latest_report.txt")
    print(f"    latest_report.json")
    print(f"    qa_fix_prompt.txt")
    print(f"    audit_history/{report.run_id}/")


def main():
    parser = argparse.ArgumentParser(
        description="PredictionLab automated QA and site audit")
    parser.add_argument("--email",       action="store_true",
                        help="Email the report after running")
    parser.add_argument("--screenshots", action="store_true",
                        help="Include Playwright screenshot audit")
    parser.add_argument("--full",        action="store_true",
                        help="Run all auditors (default)")
    parser.add_argument("--quick",       action="store_true",
                        help="Quick mode: routes + content + nav only")
    parser.add_argument("--url",         type=str, default=None,
                        help=f"Override base URL (default: {BASE_URL})")
    parser.add_argument("--preflight",   action="store_true",
                        help="Only run server preflight check (no full audit)")
    args = parser.parse_args()

    # Allow --url override
    target_url = args.url or BASE_URL
    if args.url:
        import audit_config
        audit_config.BASE_URL = args.url

    if args.preflight:
        session = _make_session()
        ok, fail = preflight_server(session, target_url)
        if ok:
            print(f"✅ Server reachable at {target_url}")
            sys.exit(0)
        print(f"❌ {fail.message}")
        if fail.detail:
            print(f"   {fail.detail[:200]}")
        sys.exit(1)

    # If neither quick nor full, default to full
    if not args.quick:
        args.full = True

    report = run_audit(args, base_url=target_url)
    html_path, json_path, txt_path, prompt_path = save_reports(report)
    print_summary(report)

    if args.email:
        send_email_report(report, html_path, txt_path, prompt_path)

    # Exit code: 0=pass/warn, 1=failures
    sys.exit(1 if report.failures else 0)


if __name__ == "__main__":
    main()
