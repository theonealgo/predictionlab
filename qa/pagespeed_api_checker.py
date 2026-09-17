#!/usr/bin/env python3
"""Google PageSpeed Insights API — shared by site_checker + optional CLI.

  https://www.googleapis.com/pagespeedonline/v5/runPagespeed

Wired into qa/site_checker.py as auditor ``pagespeed`` (full/ship modes).
This file remains runnable alone for one-off PSI runs.

Key (first match wins):
  1. --key / cli
  2. PAGESPEED_API_KEY env
  3. qa/.pagespeed_api_key (one line)

Google fetches the URL from their cloud → localhost/:5052 will NOT work.
When site_checker targets :5052, set PAGESPEED_BASE_URL=https://predictionlab.io
so PSI still audits the public site in the same checker run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable

QA_DIR = Path(__file__).resolve().parent
REPORT_DIR = QA_DIR / "pagespeed_reports"
API_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
DEFAULT_BASE = os.environ.get("PAGESPEED_BASE_URL", "https://predictionlab.io")
DEFAULT_PATHS = [
    p.strip()
    for p in (os.environ.get("PAGESPEED_PATHS") or "/mlb-picks").split(",")
    if p.strip()
]


def is_transient_psi_error(exc: BaseException | str) -> bool:
    """Google Lighthouse sometimes can't load the public URL (timeout / flake)."""
    msg = str(exc)
    needles = (
        "FAILED_DOCUMENT_REQUEST",
        "ERR_TIMED_OUT",
        "unable to reliably load the page",
        "NO_FCP",
        "ERRORED_DOCUMENT_REQUEST",
    )
    return any(n in msg for n in needles)


DEFAULT_CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]
PERF_FLOOR = int(os.environ.get("PAGESPEED_PERF_FLOOR", "50"))
A11Y_FLOOR = int(os.environ.get("PAGESPEED_A11Y_FLOOR", "90"))


def try_load_api_key(cli_key: str | None = None) -> str | None:
    if cli_key and cli_key.strip():
        return cli_key.strip()
    env = (os.environ.get("PAGESPEED_API_KEY") or "").strip()
    if env:
        return env
    key_file = QA_DIR / ".pagespeed_api_key"
    if key_file.exists():
        for line in key_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("REPLACE_WITH_"):
                continue
            return line
    return None


def load_api_key(cli_key: str | None = None) -> str:
    key = try_load_api_key(cli_key)
    if key:
        return key
    raise SystemExit(
        "No PageSpeed API key found.\n"
        "  export PAGESPEED_API_KEY='yourAPIKey'\n"
        "  or put the key in qa/.pagespeed_api_key\n"
        "Get a key: https://developers.google.com/speed/docs/insights/v5/get-started"
    )


def is_local_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"} or host.endswith(".local")


def reject_local(url: str) -> None:
    if is_local_url(url):
        raise SystemExit(
            f"PageSpeed Insights API cannot fetch {url}\n"
            "Use a public URL, e.g. --url https://predictionlab.io/mlb-picks\n"
            "Or set PAGESPEED_BASE_URL=https://predictionlab.io when auditing :5052."
        )


def public_psi_base(audit_base: str) -> str | None:
    """Origin Google can fetch. Prefer PAGESPEED_BASE_URL when audit target is local."""
    override = (os.environ.get("PAGESPEED_BASE_URL") or "").strip().rstrip("/")
    if override:
        return None if is_local_url(override) else override
    base = (audit_base or "").strip().rstrip("/")
    if not base or is_local_url(base):
        return None
    return base


def run_pagespeed(
    url: str,
    *,
    strategy: str,
    api_key: str,
    categories: Iterable[str] | None = None,
    save_raw_path: Path | None = None,
    quiet: bool = False,
) -> dict:
    """Call PSI API once. Raises RuntimeError on HTTP/network failure."""
    if is_local_url(url):
        raise RuntimeError(
            f"PageSpeed Insights API cannot fetch local URL {url}. "
            "Set PAGESPEED_BASE_URL to a public origin."
        )
    cats = list(categories or DEFAULT_CATEGORIES)
    params: list[tuple[str, str]] = [
        ("url", url),
        ("key", api_key),
        ("strategy", strategy),
    ]
    for cat in cats:
        params.append(("category", cat))
    full = API_ENDPOINT + "?" + urllib.parse.urlencode(params)
    safe = API_ENDPOINT + "?" + urllib.parse.urlencode(
        [(k, "***" if k == "key" else v) for k, v in params]
    )
    if not quiet:
        print(f"  ▶ PSI {strategy}: {url}")
        print(f"    GET {safe}")

    started = time.perf_counter()
    req = urllib.request.Request(
        full, headers={"User-Agent": "predictionlab-pagespeed-checker/1.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"PSI HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"PSI network error: {exc}") from exc
    elapsed = time.perf_counter() - started

    if save_raw_path is not None:
        save_raw_path.parent.mkdir(parents=True, exist_ok=True)
        save_raw_path.write_text(json.dumps(data, indent=2))

    lh = data.get("lighthouseResult") or {}
    scores = {
        k: round(100 * (v.get("score") or 0))
        for k, v in (lh.get("categories") or {}).items()
    }
    audits = lh.get("audits") or {}
    metric_ids = [
        "first-contentful-paint",
        "largest-contentful-paint",
        "total-blocking-time",
        "cumulative-layout-shift",
        "speed-index",
        "interactive",
        "server-response-time",
    ]
    metrics = {
        mid: (audits.get(mid) or {}).get("displayValue")
        for mid in metric_ids
        if mid in audits
    }
    failed: list[str] = []
    for aid, a in audits.items():
        score = a.get("score")
        if score is None:
            continue
        mode = a.get("scoreDisplayMode")
        if mode in {"binary", "numeric", "metricSavings"} and score < 1:
            title = a.get("title") or aid
            display = a.get("displayValue") or ""
            failed.append(title + (f" — {display}" if display else ""))

    crux = {
        mid: m.get("category")
        for mid, m in ((data.get("loadingExperience") or {}).get("metrics") or {}).items()
    }

    if not quiet:
        print(
            f"    scores  perf={scores.get('performance', '?')}  "
            f"a11y={scores.get('accessibility', '?')}  "
            f"bp={scores.get('best-practices', '?')}  "
            f"seo={scores.get('seo', '?')}  ({elapsed:.1f}s)"
        )
        if metrics:
            bits = [f"{k}={v}" for k, v in metrics.items() if v]
            print("    lab     " + " | ".join(bits[:4]))
        if failed:
            print(f"    top misses ({min(8, len(failed))}):")
            for line in failed[:8]:
                print(f"      • {line}")

    return {
        "url": url,
        "strategy": strategy,
        "elapsed_s": round(elapsed, 1),
        "categories": scores,
        "metrics": metrics,
        "crux_field": crux,
        "failed_audits": failed[:25],
        "fetch_time": lh.get("fetchTime"),
        "lighthouse_version": lh.get("lighthouseVersion"),
        "raw": str(save_raw_path) if save_raw_path else None,
    }


def soft_fail_reasons(summary: dict, *, perf_floor: int | None = None,
                      a11y_floor: int | None = None) -> list[str]:
    cats = summary.get("categories") or {}
    pf = PERF_FLOOR if perf_floor is None else perf_floor
    af = A11Y_FLOOR if a11y_floor is None else a11y_floor
    bad: list[str] = []
    if (cats.get("performance") or 0) < pf:
        bad.append(f"perf={cats.get('performance')} (floor {pf})")
    if "accessibility" in cats and (cats.get("accessibility") or 0) < af:
        bad.append(f"a11y={cats.get('accessibility')} (floor {af})")
    return bad


class PagespeedAuditor:
    """Google PageSpeed Insights — one auditor inside site_checker.

    Soft score floors → WARN. API/network failures → FAIL.
    Missing key / no public base → WARN skip (do not fail the whole audit).
    """

    def __init__(
        self,
        base: str,
        report,
        CheckResult,
        *,
        paths: list[str] | None = None,
        strategy: str | None = None,
        perf_floor: int | None = None,
        a11y_floor: int | None = None,
        save_raw: bool = False,
    ):
        self.base = (base or "").rstrip("/")
        self.r = report
        self.CheckResult = CheckResult
        self.paths = list(paths) if paths is not None else list(DEFAULT_PATHS)
        self.strategy = (strategy or os.environ.get("PAGESPEED_STRATEGY") or "mobile").strip().lower()
        self.perf_floor = PERF_FLOOR if perf_floor is None else perf_floor
        self.a11y_floor = A11Y_FLOOR if a11y_floor is None else a11y_floor
        self.save_raw = save_raw

    def run(self) -> None:
        CR = self.CheckResult
        api_key = try_load_api_key()
        if not api_key:
            self.r.add(CR(
                label="pagespeed api key",
                status="WARN",
                message=(
                    "PageSpeed skipped — no API key. "
                    "Set PAGESPEED_API_KEY or qa/.pagespeed_api_key "
                    "(see qa/.pagespeed_api_key.example)."
                ),
                auditor="pagespeed",
            ))
            return

        public_base = public_psi_base(self.base)
        if not public_base:
            self.r.add(CR(
                label="pagespeed public base",
                status="WARN",
                message=(
                    "PageSpeed skipped — Google cannot fetch this local base. "
                    "Set PAGESPEED_BASE_URL=https://predictionlab.io "
                    f"(audit target was {self.base or '(empty)'})."
                ),
                auditor="pagespeed",
                url=self.base,
            ))
            return

        strategies = (
            ["mobile", "desktop"] if self.strategy == "both" else [self.strategy]
        )
        if strategies[0] not in {"mobile", "desktop"}:
            strategies = ["mobile"]

        urls: list[str] = []
        for p in self.paths:
            path = p if p.startswith("/") else f"/{p}"
            urls.append(f"{public_base}{path}")

        out_dir = REPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        ran = 0

        for url in urls:
            slug = url.rstrip("/").split("/")[-1] or "home"
            slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)
            for strat in strategies:
                label = f"PSI {strat} {urllib.parse.urlparse(url).path or '/'}"
                raw_path = (
                    out_dir / f"{stamp}_{slug}_{strat}_raw.json" if self.save_raw else None
                )
                try:
                    summary = run_pagespeed(
                        url,
                        strategy=strat,
                        api_key=api_key,
                        save_raw_path=raw_path,
                        quiet=False,
                    )
                except Exception as exc:
                    # Production timeouts from Google's crawler are flake, not a
                    # local code FAIL — keep them as WARN so one bad fetch doesn't
                    # tank the whole checker run.
                    status = "WARN" if is_transient_psi_error(exc) else "FAIL"
                    self.r.add(CR(
                        label=label,
                        status=status,
                        message=f"PageSpeed API error: {exc}",
                        auditor="pagespeed",
                        url=url,
                    ))
                    continue

                ran += 1
                cats = summary.get("categories") or {}
                msg = (
                    f"perf={cats.get('performance', '?')} "
                    f"a11y={cats.get('accessibility', '?')} "
                    f"bp={cats.get('best-practices', '?')} "
                    f"seo={cats.get('seo', '?')} "
                    f"({summary.get('elapsed_s', '?')}s)"
                )
                metrics = summary.get("metrics") or {}
                detail_bits = [f"{k}={v}" for k, v in metrics.items() if v]
                detail = " | ".join(detail_bits[:6])
                misses = summary.get("failed_audits") or []
                if misses:
                    detail = (detail + "\n" if detail else "") + "; ".join(misses[:5])

                reasons = soft_fail_reasons(
                    summary,
                    perf_floor=self.perf_floor,
                    a11y_floor=self.a11y_floor,
                )
                if reasons:
                    self.r.add(CR(
                        label=label,
                        status="WARN",
                        message=f"{msg} — below floor: {', '.join(reasons)}",
                        detail=detail,
                        auditor="pagespeed",
                        url=url,
                    ))
                else:
                    self.r.add(CR(
                        label=label,
                        status="PASS",
                        message=msg,
                        detail=detail,
                        auditor="pagespeed",
                        url=url,
                    ))

        if ran:
            self.r.add(CR(
                label="pagespeed summary",
                status="PASS",
                message=(
                    f"Ran {ran} PageSpeed check(s) against {public_base} "
                    f"(paths={self.paths}, strategy={','.join(strategies)})"
                ),
                auditor="pagespeed",
                url=public_base,
            ))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Google PageSpeed Insights API (also runs inside site_checker --full/--ship)."
    )
    ap.add_argument("--key", default=None, help="API key (prefer env / key file)")
    ap.add_argument("--base", default=DEFAULT_BASE, help="Public site origin")
    ap.add_argument("--url", action="append", default=[], help="Full public URL(s)")
    ap.add_argument("--paths", nargs="+", default=None, help="Paths under --base")
    ap.add_argument(
        "--strategy",
        choices=("mobile", "desktop", "both"),
        default=os.environ.get("PAGESPEED_STRATEGY", "both"),
        help="PSI strategy (default both, or PAGESPEED_STRATEGY)",
    )
    ap.add_argument(
        "--categories",
        nargs="+",
        default=DEFAULT_CATEGORIES,
        help="PSI categories to request",
    )
    ap.add_argument("--out-dir", default=str(REPORT_DIR), help="JSON report directory")
    ap.add_argument(
        "--save-raw",
        action="store_true",
        help="Also save full API JSON per URL/strategy (large)",
    )
    args = ap.parse_args()

    urls: list[str] = list(args.url)
    if not urls:
        paths = args.paths or DEFAULT_PATHS
        base = args.base.rstrip("/")
        urls = [base + (p if p.startswith("/") else "/" + p) for p in paths]
    for u in urls:
        reject_local(u)
    api_key = load_api_key(args.key)

    strategies = ["mobile", "desktop"] if args.strategy == "both" else [args.strategy]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("  PageSpeed Insights API")
    print(f"  URLs:     {urls}")
    print(f"  Strategy: {', '.join(strategies)}")
    print(f"  Reports:  {out_dir}")
    print("=" * 60)

    summaries: list[dict] = []
    for url in urls:
        slug = url.rstrip("/").split("/")[-1] or "home"
        slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)
        for strat in strategies:
            raw_path = (
                out_dir / f"{stamp}_{slug}_{strat}_raw.json" if args.save_raw else None
            )
            summaries.append(
                run_pagespeed(
                    url,
                    strategy=strat,
                    api_key=api_key,
                    categories=args.categories,
                    save_raw_path=raw_path,
                )
            )

    index = out_dir / f"{stamp}_summary.json"
    index.write_text(json.dumps(summaries, indent=2))
    print(f"\n  Summary JSON: {index}")

    bad: list[str] = []
    for s in summaries:
        reasons = soft_fail_reasons(s)
        if reasons:
            bad.append(f"{s.get('url')} {s.get('strategy')}: " + ", ".join(reasons))
    if bad:
        print(f"\n  Soft fail (perf<{PERF_FLOOR} or a11y<{A11Y_FLOOR}):")
        for line in bad:
            print(f"    • {line}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
