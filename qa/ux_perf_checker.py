"""Speed, page-memory, and mobile-layout audits for the React sports shell.

Three checks the owner asked for after speed/memory work regressed UX:

1. Speed — TTFB / total time for homepage, a sport page, live + cards APIs.
2. Memory — Playwright Chromium reads JS heap (and
   ``performance.measureUserAgentSpecificMemory`` when the page is
   cross-origin-isolated; see https://web.dev/articles/monitor-total-page-memory-usage).
3. Mobile layout — phone viewport: sport chips must not be giant circles, and
   the gap from the sport strip to Predictions/Results must stay tight.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urljoin

import requests

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"

# HTTP budgets (seconds). Override with UX_SPEED_* env vars.
SPEED_HOME_S = float(os.environ.get("UX_SPEED_HOME_S", "3"))
SPEED_SPORT_S = float(os.environ.get("UX_SPEED_SPORT_S", "5"))
SPEED_API_S = float(os.environ.get("UX_SPEED_API_S", "3"))

# JS heap after load (bytes). Legacy performance.memory / CDP heap.
MEMORY_HEAP_WARN = int(os.environ.get("UX_MEMORY_HEAP_WARN", str(120 * 1024 * 1024)))
MEMORY_HEAP_FAIL = int(os.environ.get("UX_MEMORY_HEAP_FAIL", str(220 * 1024 * 1024)))

# Phone layout budgets (CSS pixels).
MOBILE_CHIP_MAX_H = float(os.environ.get("UX_MOBILE_CHIP_MAX_H", "44"))
MOBILE_STRIP_GAP_MAX = float(os.environ.get("UX_MOBILE_STRIP_GAP_MAX", "48"))
MOBILE_VIEWPORT = {
    "width": int(os.environ.get("UX_MOBILE_WIDTH", "390")),
    "height": int(os.environ.get("UX_MOBILE_HEIGHT", "844")),
}

PATHS = {
    "home": "/",
    "sport": os.environ.get("UX_SPORT_PATH", "/nba-predictions"),
    "live": "/api/web/live",
    "cards": os.environ.get(
        "UX_CARDS_PATH", "/api/web/sports/nba-picks/cards?limit=50&span=0"
    ),
}


def _mb(n: int | float | None) -> str:
    if n is None:
        return "n/a"
    return f"{float(n) / (1024 * 1024):.1f} MB"


class UxPerfChecker:
    def __init__(self, session, base: str, report, CheckResult):
        self.session = session or requests.Session()
        self.base = (base or "").rstrip("/")
        self.report = report
        self.CheckResult = CheckResult

    def add(self, label, status, message, url="", detail=""):
        self.report.add(
            self.CheckResult(
                label=label,
                status=status,
                message=message,
                detail=detail or "",
                auditor="ux_perf",
                url=urljoin(self.base + "/", (url or "").lstrip("/")) if url else self.base,
            )
        )

    def run(self):
        self._check_speed()
        self._check_memory_and_mobile()

    def _timed_get(self, path: str, timeout: float):
        url = urljoin(self.base + "/", path.lstrip("/"))
        t0 = time.perf_counter()
        try:
            resp = self.session.get(url, timeout=timeout)
            elapsed = time.perf_counter() - t0
            return resp, elapsed, None
        except Exception as exc:
            return None, time.perf_counter() - t0, str(exc)

    def _check_speed(self):
        checks = (
            ("home", PATHS["home"], SPEED_HOME_S, 200),
            ("sport page", PATHS["sport"], SPEED_SPORT_S, 200),
            ("live API", PATHS["live"], SPEED_API_S, 200),
            ("cards API", PATHS["cards"], SPEED_API_S, 200),
        )
        for label, path, budget, want in checks:
            resp, elapsed, err = self._timed_get(path, max(budget + 5, 15))
            url = path.split("?")[0]
            if err:
                self.add(
                    f"Speed: {label}",
                    FAIL,
                    f"request failed in {elapsed:.1f}s: {err}",
                    url=url,
                )
                continue
            code = getattr(resp, "status_code", 0)
            size = len(getattr(resp, "content", b"") or b"")
            if code != want:
                self.add(
                    f"Speed: {label}",
                    FAIL,
                    f"HTTP {code} in {elapsed:.1f}s (wanted {want})",
                    url=url,
                )
                continue
            if size == 0 and not path.startswith("/api/"):
                self.add(
                    f"Speed: {label}",
                    FAIL,
                    f"0-byte body in {elapsed:.1f}s",
                    url=url,
                )
                continue
            status = FAIL if elapsed > budget else PASS
            self.add(
                f"Speed: {label}",
                status,
                f"{elapsed:.2f}s (budget {budget:.0f}s, {size} bytes)",
                url=url,
            )

    def _check_memory_and_mobile(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.add(
                "Memory + mobile layout",
                WARN,
                f"Playwright unavailable — install with: pip install playwright && playwright install chromium ({exc})",
            )
            return

        sport = PATHS["sport"]
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--enable-blink-features=ForceEagerMeasureMemory"],
                )
                context = browser.new_context(
                    viewport=MOBILE_VIEWPORT,
                    device_scale_factor=2,
                    is_mobile=True,
                    has_touch=True,
                    user_agent=(
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                        "Mobile/15E148 Safari/604.1 PL-UxPerf/1.0"
                    ),
                )
                page = context.new_page()
                page.goto(
                    urljoin(self.base + "/", sport.lstrip("/")),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(2500)

                memory = page.evaluate(
                    """async () => {
                      const out = {
                        crossOriginIsolated: !!window.crossOriginIsolated,
                        hasMeasure: typeof performance.measureUserAgentSpecificMemory === 'function',
                        usedJSHeapSize: (performance.memory && performance.memory.usedJSHeapSize) || null,
                        measureBytes: null,
                        measureError: null,
                      };
                      if (out.crossOriginIsolated && out.hasMeasure) {
                        try {
                          const r = await performance.measureUserAgentSpecificMemory();
                          out.measureBytes = r && r.bytes != null ? r.bytes : null;
                        } catch (e) {
                          out.measureError = String(e && e.message || e);
                        }
                      }
                      return out;
                    }"""
                )

                layout = page.evaluate(
                    """() => {
                      const chip = document.querySelector('.pl-sport-nav .pl-sport-link:not(.is-placeholder)');
                      const strip = document.querySelector('.pl-sport-nav');
                      const switchEl = document.querySelector('.pl-mobile-switch');
                      const left = document.querySelector('.pl-left');
                      if (!chip || !strip || !switchEl) {
                        return { ok: false, reason: 'missing chip, strip, or Predictions/Results switch' };
                      }
                      const c = chip.getBoundingClientRect();
                      const s = strip.getBoundingClientRect();
                      const t = switchEl.getBoundingClientRect();
                      const leftBox = left ? left.getBoundingClientRect() : null;
                      return {
                        ok: true,
                        chipW: c.width,
                        chipH: c.height,
                        gap: t.top - s.bottom,
                        leftH: leftBox ? leftBox.height : null,
                        switchVisible: getComputedStyle(switchEl).display !== 'none',
                      };
                    }"""
                )
                browser.close()
        except Exception as exc:
            self.add(
                "Memory + mobile layout",
                FAIL,
                f"Playwright run failed: {exc}",
                url=sport,
            )
            return

        self._report_memory(memory, sport)
        self._report_mobile(layout, sport)

    def _report_memory(self, memory: dict | None, path: str):
        memory = memory or {}
        heap = memory.get("usedJSHeapSize")
        measured = memory.get("measureBytes")
        isolated = bool(memory.get("crossOriginIsolated"))
        has_api = bool(memory.get("hasMeasure"))

        if measured is not None:
            status = (
                FAIL
                if measured >= MEMORY_HEAP_FAIL
                else WARN
                if measured >= MEMORY_HEAP_WARN
                else PASS
            )
            self.add(
                "Memory: measureUserAgentSpecificMemory",
                status,
                f"{_mb(measured)} (warn {_mb(MEMORY_HEAP_WARN)}, fail {_mb(MEMORY_HEAP_FAIL)})",
                url=path,
                detail="https://web.dev/articles/monitor-total-page-memory-usage",
            )
        else:
            reason = memory.get("measureError") or (
                "page is not cross-origin-isolated (needs COOP+COEP)"
                if not isolated
                else "API missing"
                if not has_api
                else "no result"
            )
            self.add(
                "Memory: measureUserAgentSpecificMemory",
                INFO,
                f"unavailable — {reason}",
                url=path,
                detail="Enable COOP+COEP for production sampling; checker still uses JS heap below.",
            )

        if heap is None:
            self.add(
                "Memory: JS heap",
                WARN,
                "performance.memory unavailable in this browser",
                url=path,
            )
            return
        status = (
            FAIL
            if heap >= MEMORY_HEAP_FAIL
            else WARN
            if heap >= MEMORY_HEAP_WARN
            else PASS
        )
        self.add(
            "Memory: JS heap",
            status,
            f"{_mb(heap)} used (warn {_mb(MEMORY_HEAP_WARN)}, fail {_mb(MEMORY_HEAP_FAIL)})",
            url=path,
            detail="Legacy performance.memory — use for local regression; not comparable across browsers.",
        )

    def _report_mobile(self, layout: dict | None, path: str):
        layout = layout or {}
        if not layout.get("ok"):
            self.add(
                "Mobile layout: sport strip",
                FAIL,
                layout.get("reason") or "could not measure layout",
                url=path,
            )
            return

        chip_h = float(layout.get("chipH") or 0)
        chip_w = float(layout.get("chipW") or 0)
        gap = float(layout.get("gap") or 0)
        left_h = layout.get("leftH")

        circle_like = chip_h > MOBILE_CHIP_MAX_H or (
            chip_w > 0 and chip_h > 0 and abs(chip_w - chip_h) < 8 and chip_h > 36
        )
        if circle_like:
            self.add(
                "Mobile layout: sport chips",
                FAIL,
                f"chip {chip_w:.0f}×{chip_h:.0f}px looks oversized/circular (max height {MOBILE_CHIP_MAX_H:.0f}px)",
                url=path,
            )
        else:
            self.add(
                "Mobile layout: sport chips",
                PASS,
                f"chip {chip_w:.0f}×{chip_h:.0f}px",
                url=path,
            )

        if gap > MOBILE_STRIP_GAP_MAX or (left_h is not None and float(left_h) > 120):
            self.add(
                "Mobile layout: strip-to-switch gap",
                FAIL,
                f"gap {gap:.0f}px (max {MOBILE_STRIP_GAP_MAX:.0f}); left rail height {left_h}",
                url=path,
            )
        else:
            self.add(
                "Mobile layout: strip-to-switch gap",
                PASS,
                f"gap {gap:.0f}px; left rail height {left_h}",
                url=path,
            )

        if not layout.get("switchVisible"):
            self.add(
                "Mobile layout: Predictions/Results switch",
                FAIL,
                "switch not visible at phone width",
                url=path,
            )
        else:
            self.add(
                "Mobile layout: Predictions/Results switch",
                PASS,
                "visible",
                url=path,
            )


def run_ux_perf_checker(session, base: str, report, CheckResult):
    UxPerfChecker(session, base, report, CheckResult).run()
