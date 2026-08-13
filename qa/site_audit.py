#!/usr/bin/env python3
"""
PredictionLab Automated QA & Site Audit
========================================
Usage:
  python qa/site_audit.py                  # full audit, no email
  python qa/site_audit.py --quick          # routes + content + nav only
  python qa/site_audit.py --full           # every auditor
  python qa/site_audit.py --email          # full + email report
  python qa/site_audit.py --screenshots    # include Playwright screenshots
  python qa/site_audit.py --quick --email  # quick + email

Environment variables:
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
    CLUSTER_WARN_PCT, EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO, FORBIDDEN_CONTENT,
    FULL_MODE_AUDITORS, HISTORY_DIR, MODEL_DISPLAY_NAMES, MODEL_KEYS,
    QUICK_MODE_AUDITORS, REQUEST_TIMEOUT, SCREENSHOTS_DIR, SMTP_HOST,
    SMTP_PORT, SPORT_PICKS_SLUGS, SPORT_RESULTS_SLUGS, STALE_STRINGS,
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
            resp = self.s.get(url, timeout=6, allow_redirects=True, stream=False)
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
            return CheckResult(label=route["label"], status=FAIL,
                               message="Connection error", detail=detail,
                               url=path, auditor=self.NAME)
        sev = WARN if kind == "should_404" else FAIL
        return CheckResult(label=route["label"], status=sev,
                           message=f"Expected {allowed}, got {code}",
                           url=path, auditor=self.NAME)

    def run(self):
        cfg_path = QA_DIR / "expected_routes.json"
        cfg = json.loads(cfg_path.read_text())

        # Build work list
        tasks = []
        for r in cfg.get("public", []):
            tasks.append({**r, "kind": "public"})
        for r in cfg.get("protected", []):
            tasks.append({**r, "kind": "protected"})
        for r in cfg.get("should_404", []):
            tasks.append({**r, "kind": "should_404"})
        for slug in SPORT_PICKS_SLUGS:
            tasks.append({"path": f"/{slug}", "label": f"Sport page /{slug}",
                          "expected_status": [200, 301, 302], "kind": "sport"})

        # Run all checks in parallel
        results: list[tuple[int, CheckResult]] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
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

        # Preserve original order
        for _, cr in sorted(results, key=lambda x: x[0]):
            # Downgrade sport-page FAIL → WARN (off-season is normal)
            if cr.auditor == self.NAME and cr.url and cr.url.endswith("-picks"):
                if cr.status == FAIL:
                    cr.status = WARN
                    cr.message = f"HTTP error on picks page — {cr.message}"
            self.r.add(cr)


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
                         "/privacy", "/terms", "/refund-policy", "/faq", "/daily-report",
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

    def run(self):
        cfg = json.loads((QA_DIR / "expected_navigation.json").read_text())
        html = self._fetch("/")
        if not html:
            self.r.add(CheckResult(
                label="Navigation fetch", status=FAIL,
                message="Could not load homepage for nav audit",
                auditor=self.NAME))
            return

        # Extract all hrefs
        all_hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
        internal_hrefs = [h for h in all_hrefs
                          if h.startswith("/") and not h.startswith("//")]

        # 1. Required links
        for required in cfg.get("picks_menu_required", []):
            if required in internal_hrefs:
                self.r.add(CheckResult(
                    label=f"Nav link: {required}", status=PASS,
                    message="Found in navigation", auditor=self.NAME))
            else:
                self.r.add(CheckResult(
                    label=f"Nav link: {required}", status=FAIL,
                    message="Missing from navigation HTML",
                    auditor=self.NAME))

        # 2. Footer links
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

        # 3. Forbidden strings in nav HTML
        # Extract nav HTML only
        nav_m = re.search(r'<nav[^>]*>(.*?)</nav>', html,
                          flags=re.DOTALL | re.IGNORECASE)
        nav_html = nav_m.group(1) if nav_m else html[:10000]
        for bad in cfg.get("forbidden_in_nav", []):
            if bad.lower() in nav_html.lower():
                self.r.add(CheckResult(
                    label=f"Forbidden in nav: '{bad}'", status=FAIL,
                    message=f"'{bad}' found in navigation HTML",
                    auditor=self.NAME))

        # 4. Duplicate link detection
        href_counts: dict[str, int] = {}
        for h in internal_hrefs:
            href_counts[h] = href_counts.get(h, 0) + 1

        allowed_dupes = cfg.get("known_duplicates_allowed", [])
        for href, count in href_counts.items():
            # Only warn if a link appears more than 6x (nav + footer + hero + mobile
            # menu is normal; >6 suggests an actual bug like a loop)
            if count > 6 and href not in allowed_dupes:
                self.r.add(CheckResult(
                    label=f"Duplicate nav link: {href}", status=WARN,
                    message=f"Appears {count}x in page HTML "
                            f"(may be intentional if in both nav + footer)",
                    auditor=self.NAME))

        # 5. Props menu is only for player props; model/tutorial links live in Tools & Models.
        # Look for TV_MENUS props section
        props_m = re.search(
            r"props:\{title:'Props[^}]+items:\[([^\]]+)\]", html)
        if props_m:
            props_items = props_m.group(1)
            for forbidden in cfg.get("props_menu_forbidden", []):
                if forbidden in props_items:
                    self.r.add(CheckResult(
                        label="Props menu: forbidden link",
                        status=FAIL,
                        message=f"'{forbidden}' is in Props menu — "
                                f"should live in Tools & Models or Results & Tracking",
                        auditor=self.NAME))
                else:
                    self.r.add(CheckResult(
                        label="Props menu: no forbidden links",
                        status=PASS,
                        message="Props menu is clean",
                        auditor=self.NAME))

        # 6. Broken internal links (spot-check — key pages only)
        key_links = [
            "/nba-picks", "/nhl-picks", "/mlb-picks", "/plans",
            "/privacy", "/terms", "/refund-policy", "/faq", "/contact",
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

        # Extract all W-L records and displayed percentages
        records = re.findall(r'(\d+)-(\d+)', html)
        pcts    = re.findall(r'(\d{2,3}\.\d)%', html)

        if not records:
            self.r.add(CheckResult(
                label="Results math", status=WARN,
                message="No W-L records found to validate",
                auditor=self.NAME))
            return

        mismatches = []
        for i, (w_str, l_str) in enumerate(records[:30]):
            w, l = int(w_str), int(l_str)
            total = w + l
            if total == 0:
                continue
            calc_pct = round(w / total * 100, 1)
            # Try to match against nearby displayed pct
            if i < len(pcts):
                displayed = float(pcts[i])
                diff = abs(calc_pct - displayed)
                if diff > 1.0:
                    mismatches.append(
                        f"{w}-{l} → calc {calc_pct}% vs displayed {displayed}%")

        if mismatches:
            self.r.add(CheckResult(
                label="Results math validation", status=WARN,
                message=f"{len(mismatches)} possible pct mismatches",
                detail="; ".join(mismatches[:5]),
                auditor=self.NAME))
        else:
            self.r.add(CheckResult(
                label="Results math validation", status=PASS,
                message=f"Spot-checked {min(len(records),30)} records — "
                        f"no significant pct discrepancies",
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

def send_email_report(report: AuditReport,
                      html_path: Path, txt_path: Path, prompt_path: Path):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print("⚠️  Email skipped: set AUDIT_EMAIL_FROM and AUDIT_EMAIL_PASSWORD env vars")
        return

    subject = (f"[PredictionLab QA] {report.overall} — "
               f"{len(report.failures)} failures, "
               f"{len(report.warnings)} warnings — {report.timestamp}")

    msg = MIMEMultipart("mixed")
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg["Subject"] = subject

    # Body
    body_text = (
        f"PredictionLab Automated QA Report\n"
        f"Run ID: {report.run_id}\n"
        f"Time: {report.timestamp}\n"
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

    msg.attach(MIMEText(body_text, "plain"))

    # Attach files
    for fpath in [html_path, txt_path, prompt_path]:
        if fpath.exists():
            with open(fpath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition",
                            f"attachment; filename={fpath.name}")
            msg.attach(part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"✉️  Report emailed to {EMAIL_TO}")
    except Exception as exc:
        print(f"⚠️  Email failed: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_audit(args) -> AuditReport:
    import time
    start = time.time()

    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    report = AuditReport(run_id=run_id, timestamp=ts, base_url=BASE_URL)

    print(f"\n{'='*60}")
    print(f"  PredictionLab QA Audit")
    print(f"  Target: {BASE_URL}")
    print(f"  Mode: {'quick' if args.quick else 'full'}")
    print(f"  Run ID: {run_id}")
    print(f"{'='*60}\n")

    session = _make_session()

    # Determine which auditors to run
    auditors_to_run = QUICK_MODE_AUDITORS if args.quick else FULL_MODE_AUDITORS

    def run_auditor(name: str, fn):
        if name not in auditors_to_run and not args.full:
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

    run_auditor("routes",     lambda: RouteAuditor(session, BASE_URL, report).run())
    run_auditor("content",    lambda: ContentAuditor(session, BASE_URL, report).run())
    run_auditor("navigation", lambda: NavigationAuditor(session, BASE_URL, report).run())
    run_auditor("cards",      lambda: CardAuditor(session, BASE_URL, report).run())
    run_auditor("models",     lambda: ModelAuditor(session, BASE_URL, report).run())
    run_auditor("results",    lambda: ResultsAuditor(session, BASE_URL, report).run())
    run_auditor("csv",        lambda: CsvAuditor(session, BASE_URL, report).run())

    if args.screenshots:
        print("  ▶ Running screenshot audit…")
        try:
            ScreenshotAuditor(BASE_URL, report).run()
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
    args = parser.parse_args()

    # Allow --url override
    if args.url:
        import audit_config
        audit_config.BASE_URL = args.url
        from importlib import reload
        import audit_config as ac
        ac.BASE_URL = args.url

    # If neither quick nor full, default to full
    if not args.quick:
        args.full = True

    report = run_audit(args)
    html_path, json_path, txt_path, prompt_path = save_reports(report)
    print_summary(report)

    if args.email:
        send_email_report(report, html_path, txt_path, prompt_path)

    # Exit code: 0=pass/warn, 1=failures
    sys.exit(1 if report.failures else 0)


if __name__ == "__main__":
    main()
