"""Read-only local link audit. No account creation, forms, billing, or external requests."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urljoin, urlsplit
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".checkpoints/navigation-audit"
BASE = "http://127.0.0.1:5122"
SPORTS = ("nba", "nhl", "nfl", "ncaaf", "ncaab", "ncaaw", "mlb", "wnba", "cfl",
          "soccer", "tennis", "ufc", "golf")
SEEDS = ["/", "/daily-report", "/all-sports-results", "/blog", "/plans", "/login", "/signup",
         "/forgot-password", "/account", "/contact", "/tutorial", "/privacy", "/terms",
         "/refund-policy", "/responsible-gaming", "/affiliate", "/affiliate/terms",
         "/performance", "/ladder", "/sports-props", "/player-props", "/results/downloads",
         "/historic-results"] + [f"/{sport}-{kind}" for sport in SPORTS for kind in ("predictions", "results")]
SKIP = re.compile(
    r"^/(?:admin|api|player-props-api|checkout|billing|stripe|logout|create[-_]portal|"
    r"create[-_]checkout|reset-password|set-password|claim-account|change-password|smtp|"
    r"send[-_/]|delete[-_/]|cancel[-_/]|refresh[-_/]|run[-_/]|auth/.*/callback|auth/google/callback)"
    r"|^/static/|^/sport-static/", re.I,
)
SAFE_QUERY = {"view", "market", "sport", "league", "region", "week", "date", "team", "player",
              "model", "last_n", "season", "type", "period", "tab", "q", "page", "limit",
              "offset", "game", "format", "window", "n", "sort", "order", "days", "conference",
              "division", "as_of", "next", "month", "year", "span", "range"}


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href") or "")


def local_path(href, source):
    href = str(href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None, "non-navigation"
    u = urlsplit(urljoin(BASE + source, href))
    if u.scheme not in ("http", "https"):
        return None, "non-http"
    if u.hostname not in ("127.0.0.1", "localhost", "predictionlab.io", "www.predictionlab.io"):
        return None, "external"
    if u.hostname in ("127.0.0.1", "localhost") and u.port not in (None, 5122):
        return None, "foreign-local-port"
    path = u.path or "/"
    if SKIP.search(path) or any(k not in SAFE_QUERY for k, _ in parse_qsl(u.query)):
        return None, "unsafe-or-account-action"
    return path + ("?" + u.query if u.query else ""), None


def fetch(path):
    start = time.monotonic()
    current = path
    hops = []
    try:
        for _ in range(7):
            response = requests.get(BASE + current, timeout=(3, 20), allow_redirects=False,
                                    headers={"User-Agent": "PredictionLab-local-navigation-audit"})
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                next_path, reason = local_path(response.headers["Location"], current)
                if not next_path:
                    return {"path": path, "final": current, "status": response.status_code,
                            "kind": "handoff", "reason": reason, "links": [], "hops": hops,
                            "seconds": round(time.monotonic()-start, 3)}
                hops.append(next_path)
                current = next_path
                continue
            content_type = response.headers.get("Content-Type", "")
            html = response.text if "html" in content_type else ""
            parser = Links()
            if html:
                parser.feed(html)
            visible = re.sub(r"<(script|style)\b[\s\S]*?</\1>", "", html, flags=re.I)
            broken = bool(re.search(r"Traceback \(most recent call last\)|Internal Server Error|"
                                    r"jinja2\.exceptions|UndefinedError|\{%\s*(?:block|extends|for)\b", visible))
            code = response.status_code
            kind = ("protected" if code in (401, 403) else "broken" if code >= 400 or broken
                    else "ok")
            return {"path": path, "final": current, "status": code, "kind": kind,
                    "links": parser.hrefs, "hops": hops,
                    "seconds": round(time.monotonic()-start, 3)}
        return {"path": path, "final": current, "kind": "broken", "status": 0,
                "reason": "redirect-loop", "links": [], "hops": hops}
    except requests.RequestException as exc:
        return {"path": path, "final": current, "kind": "unverified", "status": None,
                "reason": type(exc).__name__, "links": [],
                "seconds": round(time.monotonic()-start, 3)}


def run():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    queued = {p: 0 for p in SEEDS}
    sources = defaultdict(set)
    skipped = defaultdict(set)
    try:
        response = requests.get(BASE + "/sitemap.xml", timeout=30)
        response.raise_for_status()
        for node in ET.fromstring(response.content).findall("{*}url/{*}loc"):
            path, reason = local_path(node.text, "/sitemap.xml")
            if path:
                queued[path] = 0
                sources[path].add("/sitemap.xml")
            elif reason:
                skipped[reason].add(node.text)
    except Exception as exc:
        print("Sitemap not inventoried:", type(exc).__name__, flush=True)
    pending = dict(queued)
    results = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        while pending:
            batch = sorted(pending.items(), key=lambda pair: (pair[1], pair[0]))
            pending.clear()
            for row, (path, depth) in zip(pool.map(fetch, [p for p, _ in batch]), batch):
                results.append(row)
                for href in row.pop("links"):
                    target, reason = local_path(href, row.get("final") or path)
                    if target:
                        sources[target].add(path)
                        if target not in queued:
                            if depth < 1 and len(queued) < 3500:
                                queued[target] = depth + 1
                                pending[target] = depth + 1
                            else:
                                skipped["deeper-or-limit"].add(target)
                    elif reason:
                        skipped[reason].add(href)
                if len(results) % 25 == 0 or row["kind"] in ("broken", "unverified"):
                    print(f"{len(results)}/{len(queued)} {row['kind']} {path}", flush=True)
                if len(results) % 25 == 0:
                    write_report(results, sources, skipped, start, False)
    write_report(results, sources, skipped, start, True)
    counts = {kind: sum(r["kind"] == kind for r in results)
              for kind in ("ok", "protected", "handoff", "broken", "unverified")}
    print(json.dumps({"checked": len(results), "seconds": round(time.monotonic()-start), **counts}), flush=True)


def write_report(results, sources, skipped, start, complete):
    report = {
        "complete": complete, "base": BASE, "seconds": round(time.monotonic()-start, 1),
        "scope": "Guest same-site GETs from public page families, sitemap and their direct links; "
                 "two workers; no forms, account changes, external calls or unlimited query crawling.",
        "results": results, "found_on": {k: sorted(v) for k, v in sources.items()},
        "skipped": {k: sorted(v) for k, v in skipped.items()},
    }
    temporary = OUTPUT / "link-crawl.tmp"
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(OUTPUT / "link-crawl.json")


if __name__ == "__main__":
    run()
