#!/usr/bin/env python3
"""Local PageSpeed-equivalent checker (Google Lighthouse on your Mac).

PageSpeed Insights (pagespeed.web.dev) runs Lighthouse in Google's cloud.
This script runs the same Lighthouse engine in Chrome on THIS computer against
your local site (:5052). Nothing is embedded in the website.

Examples:
  .venv/bin/python qa/lighthouse_local.py
  .venv/bin/python qa/lighthouse_local.py --url http://127.0.0.1:5052/mlb-picks
  .venv/bin/python qa/lighthouse_local.py --paths /mlb-picks /nfl-picks --form-factor both
  .venv/bin/python qa/lighthouse_local.py --no-open   # skip opening the HTML report
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
REPORT_DIR = QA_DIR / "lighthouse_reports"
DEFAULT_BASE = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5052")
DEFAULT_PATHS = ["/mlb-picks"]
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _chrome_path() -> str:
    env = (os.environ.get("CHROME_PATH") or "").strip()
    if env and Path(env).exists():
        return env
    if Path(CHROME_MAC).exists():
        return CHROME_MAC
    which = shutil.which("google-chrome") or shutil.which("chromium")
    if which:
        return which
    raise SystemExit(
        "Google Chrome not found. Install Chrome or set CHROME_PATH."
    )


def _npx() -> str:
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("npx not found — install Node.js (nvm/node) first.")
    return npx


def _run_lighthouse(
    page_url: str,
    *,
    form_factor: str,
    out_stem: Path,
    chrome: str,
    view: bool,
    headed: bool,
    psi_throttle: bool,
) -> dict:
    """Run Lighthouse once; return parsed categories + top failing audits."""
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_stem.with_suffix(".html")
    json_path = out_stem.with_suffix(".json")

    # Headed (real Chrome window) matches "opens on my Mac" and avoids NO_FCP
    # flakes that headless hits on cold localhost pages.
    chrome_flags = (
        "--no-sandbox --disable-dev-shm-usage --window-size=1280,900"
        if headed
        else "--headless=new --no-sandbox --disable-dev-shm-usage"
    )

    # Local Flask can take >30s cold; extend FCP/load waits past Lighthouse defaults.
    # --psi-throttle (below) adds mobile Slow 4G; default is unthrottled so local
    # :5052 can finish painting and still report LCP/CLS/a11y audits.
    cmd = [
        _npx(),
        "--yes",
        "lighthouse",
        page_url,
        f"--chrome-path={chrome}",
        f"--form-factor={form_factor}",
        "--screenEmulation.mobile" if form_factor == "mobile" else "--screenEmulation.mobile=false",
        "--only-categories=performance,accessibility,best-practices,seo",
        "--output=html",
        "--output=json",
        f"--output-path={out_stem}",
        "--quiet",
        "--max-wait-for-fcp=180000",
        "--max-wait-for-load=180000",
        f"--chrome-flags={chrome_flags}",
    ]
    if not psi_throttle:
        cmd.extend(
            [
                "--throttling-method=provided",
                "--throttling.cpuSlowdownMultiplier=1",
                "--throttling.rttMs=0",
                "--throttling.throughputKbps=0",
                "--throttling.requestLatencyMs=0",
                "--throttling.downloadThroughputKbps=0",
                "--throttling.uploadThroughputKbps=0",
            ]
        )
    # Lighthouse's --output-path without extension writes name.report.html + .json
    # when multiple formats are requested in some versions; normalize below.

    print(f"  ▶ Lighthouse {form_factor}: {page_url}")
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout or "lighthouse failed\n")
        raise SystemExit(f"Lighthouse failed ({form_factor}) in {elapsed:.1f}s")

    # Resolve where Lighthouse wrote files (version-dependent names).
    candidates = [
        json_path,
        Path(str(out_stem) + ".report.json"),
        out_stem.parent / f"{out_stem.name}.report.json",
    ]
    html_candidates = [
        html_path,
        Path(str(out_stem) + ".report.html"),
        out_stem.parent / f"{out_stem.name}.report.html",
    ]
    found_json = next((p for p in candidates if p.exists()), None)
    found_html = next((p for p in html_candidates if p.exists()), None)
    if not found_json:
        raise SystemExit(f"No Lighthouse JSON written under {out_stem.parent}")

    data = json.loads(found_json.read_text())
    cats = {
        k: round(100 * (v.get("score") or 0))
        for k, v in (data.get("categories") or {}).items()
    }
    audits = data.get("audits") or {}
    failed = []
    for aid, a in audits.items():
        score = a.get("score")
        if score is None:
            continue
        if score < 1 and a.get("scoreDisplayMode") in {"binary", "numeric", "metricSavings"}:
            title = a.get("title") or aid
            display = a.get("displayValue") or ""
            failed.append(f"{title}" + (f" — {display}" if display else ""))

    summary = {
        "url": page_url,
        "form_factor": form_factor,
        "elapsed_s": round(elapsed, 1),
        "categories": cats,
        "html": str(found_html) if found_html else None,
        "json": str(found_json),
        "failed_audits": failed[:25],
    }
    print(
        f"    scores  perf={cats.get('performance', '?')}  "
        f"a11y={cats.get('accessibility', '?')}  "
        f"bp={cats.get('best-practices', '?')}  "
        f"seo={cats.get('seo', '?')}  ({elapsed:.1f}s)"
    )
    if failed:
        print(f"    top misses ({min(8, len(failed))}):")
        for line in failed[:8]:
            print(f"      • {line}")

    if view and found_html and found_html.exists():
        subprocess.run(["open", str(found_html)], check=False)
        print(f"    opened {found_html}")
    elif found_html:
        print(f"    report  {found_html}")

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run Google Lighthouse locally (same engine as PageSpeed Insights)."
    )
    ap.add_argument("--base", default=DEFAULT_BASE, help="Site origin (default :5052)")
    ap.add_argument("--url", action="append", default=[], help="Full URL(s) to audit")
    ap.add_argument(
        "--paths",
        nargs="+",
        default=None,
        help="Paths under --base (default: /mlb-picks)",
    )
    ap.add_argument(
        "--form-factor",
        choices=("mobile", "desktop", "both"),
        default="both",
        help="Match PSI mobile / desktop (default both)",
    )
    ap.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the HTML report in your browser",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome headless (default: visible Chrome window on this Mac)",
    )
    ap.add_argument(
        "--psi-throttle",
        action="store_true",
        help="Apply PSI-like Slow 4G + CPU throttle (default off — local :5052 is already slow)",
    )
    ap.add_argument(
        "--out-dir",
        default=str(REPORT_DIR),
        help="Where to write HTML/JSON reports",
    )
    args = ap.parse_args()

    urls: list[str] = list(args.url)
    if not urls:
        paths = args.paths or DEFAULT_PATHS
        base = args.base.rstrip("/")
        urls = [base + (p if p.startswith("/") else "/" + p) for p in paths]

    chrome = _chrome_path()
    _npx()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    factors = (
        ["mobile", "desktop"] if args.form_factor == "both" else [args.form_factor]
    )

    # Warm pages so first paint isn't a cold-cache miss (NO_FCP).
    try:
        import urllib.request

        for u in urls:
            try:
                urllib.request.urlopen(u, timeout=60).read(64)
                print(f"  warmed {u}")
            except Exception as exc:
                print(f"  warm failed {u}: {exc}")
    except Exception:
        pass

    print("=" * 60)
    print("  Local Lighthouse (= PageSpeed Insights engine)")
    print(f"  Chrome: {chrome}")
    print(f"  Mode:   {'headless' if args.headless else 'headed (visible window)'}")
    print(f"  Reports: {out_dir}")
    print("  Runs on THIS Mac only — not on predictionlab.io")
    print("=" * 60)

    summaries = []
    for url in urls:
        slug = url.rstrip("/").split("/")[-1] or "home"
        slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)
        for ff in factors:
            stem = out_dir / f"{stamp}_{slug}_{ff}"
            summaries.append(
                _run_lighthouse(
                    url,
                    form_factor=ff,
                    out_stem=stem,
                    chrome=chrome,
                    view=not args.no_open,
                    headed=not args.headless,
                    psi_throttle=args.psi_throttle,
                )
            )

    index = out_dir / f"{stamp}_summary.json"
    index.write_text(json.dumps(summaries, indent=2))
    print(f"\n  Summary JSON: {index}")
    # Soft gate: performance < 50 or a11y < 90 → exit 1 (tune later).
    bad = [
        s
        for s in summaries
        if (s["categories"].get("performance") or 0) < 50
        or (s["categories"].get("accessibility") or 0) < 90
    ]
    if bad:
        print(f"\n  Soft fail: {len(bad)} run(s) below perf<50 or a11y<90")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
