#!/usr/bin/env python3
"""Audit canonical public navigation links against the running local site."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen


PATHS = (
    "/",
    "/ai-sports-betting-picks-today",
    "/all-sports-results",
    "/blog",
    "/contact",
    "/daily-report",
    "/edge-performance",
    "/faq",
    "/login",
    "/mlb-picks",
    "/nba-picks",
    "/ncaab-picks",
    "/ncaaf-picks",
    "/ncaaw-picks",
    "/nfl-picks",
    "/nhl-picks",
    "/our-model-vs-sportsbooks",
    "/performance",
    "/plans",
    "/player-props",
    "/privacy",
    "/responsible-gaming",
    "/results/downloads",
    "/search",
    "/signup",
    "/soccer-picks",
    "/terms",
    "/tutorial",
    "/what-are-ai-sports-betting-picks",
    "/wnba-picks",
)


def audit(base_url: str, path: str) -> tuple[str, int, int, int, bool]:
    request = Request(base_url + path, headers={"User-Agent": "PredictionLabLinkAudit/1.0"})
    with urlopen(request, timeout=60) as response:
        html = response.read().decode("utf-8", errors="replace")
        return (
            path,
            response.status,
            html.count('class="pl2-header"'),
            html.count('class="site-directory-footer"'),
            (
                'refreshing this page right now' in html.lower()
                or 'upstream data/model dependency failed' in html.lower()
                or 'failed to render' in html.lower()
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5002",
        help="Running local site to audit (default: %(default)s)",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    rows = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(audit, base_url, path): path for path in PATHS}
        for future in as_completed(futures):
            path = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append((path, 0, 0, 0, True))
                print(f"{path:<40} ERROR {exc}")

    failed = False
    for path, status, headers, footers, fallback in sorted(rows):
        ok = status == 200 and headers == 1 and footers == 1 and not fallback
        failed = failed or not ok
        print(
            f"{path:<40} status={status:<3} "
            f"headers={headers} footers={footers} fallback={int(fallback)} "
            f"{'OK' if ok else 'FAIL'}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
