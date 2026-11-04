"""Watch published cards the way a customer does: screenshot early, compare later.

Usage:
  .venv/bin/python tools/card_watch.py snap    [--url https://predictionlab.io]
  .venv/bin/python tools/card_watch.py compare [--url https://predictionlab.io]

snap     saves every picks card's visible Prediction Lab / XSharp lines and model
         percents to .cache/card_watch/<host>/<time>.json.
compare  re-reads the picks pages and the results pages and lists every game whose
         lines or model percents differ from the FIRST snapshot that saw it.
         Books lines may move before a game and are not compared.

On the live site it signs in with the paid checker account (qa/test_accounts.py)
so the lines behind the paywall are visible. Read-only: it only GETs pages.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qa"))

SPORTS = ("nba", "nhl", "nfl", "ncaaf", "ncaab", "ncaaw", "mlb", "wnba", "cfl", "soccer")
OUT = ROOT / ".cache" / "card_watch"


def _session(base: str):
    target = urlparse(base)
    headers = {"Host": target.netloc, "User-Agent": "pl-card-watch", "Accept-Encoding": "identity"}
    import paid_viewer as pv

    if target.hostname not in ("127.0.0.1", "localhost"):
        email, password = pv._test_accounts()._live_accounts()["paid"]
        jar = pv._sign_in(target, email, password)
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())

    def get(path: str) -> str:
        resp, body = pv._request(target, "GET", path, headers)
        return body.decode("utf-8", "replace") if resp.status == 200 else ""

    return get


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _cards(html: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    starts = [m for m in re.finditer(r'<div\b[^>]*\bdata-pick-card\b[^>]*>', html or "")]
    for i, m in enumerate(starts):
        gid = re.search(r'data-game-id="([^"]+)"', m.group(0))
        if not gid:
            continue
        card = html[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(html)]
        rec: dict[str, str] = {}
        for row in re.finditer(r'<tr>\s*<td class="market-k">([^<]+)</td>([\s\S]*?)</tr>', card):
            market = _clean(row.group(1))
            for who, cls in (("PL", "val-pl"), ("XSharp", "val-xs")):
                cell = re.search(rf'class="{cls}"[^>]*>([\s\S]*?)</td>', row.group(2))
                if cell:
                    rec[f"{who} {market}"] = _clean(cell.group(1))
        for name, val in re.findall(
            r'class="pc-name">\s*([^<]+?)\s*</div>\s*(?:<[^>]+>\s*)*?<div class="pc-val"[^>]*>([^<]*)<', card
        ):
            rec[f"{_clean(name)} %"] = _clean(val)
        if rec:
            out.setdefault(gid.group(1), rec)
    return out


def _host_dir(base: str) -> Path:
    d = OUT / (urlparse(base).netloc.replace(":", "_") or "local")
    d.mkdir(parents=True, exist_ok=True)
    return d


def snap(base: str) -> Path:
    get = _session(base)
    data = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "sports": {}}
    for sport in SPORTS:
        data["sports"][sport] = _cards(get(f"/{sport}-picks"))
    path = _host_dir(base) / (time.strftime("%Y%m%d_%H%M%S") + ".json")
    path.write_text(json.dumps(data, indent=1))
    n = sum(len(v) for v in data["sports"].values())
    print(f"saved {n} cards -> {path}")
    return path


def compare(base: str) -> int:
    snaps = sorted(_host_dir(base).glob("*.json"))
    if not snaps:
        print("no snapshot yet: run snap first")
        return 1
    first: dict[tuple[str, str], tuple[str, dict]] = {}
    for path in snaps:
        data = json.loads(path.read_text())
        for sport, cards in data["sports"].items():
            for gid, rec in cards.items():
                first.setdefault((sport, gid), (data["at"], rec))
    get = _session(base)
    changes = 0
    checked = 0
    for sport in SPORTS:
        now: dict[str, tuple[str, dict]] = {}
        for page in (f"/{sport}-picks", f"/{sport}-results"):
            for gid, rec in _cards(get(page)).items():
                now.setdefault(gid, (page, rec))
        for gid, (page, rec) in now.items():
            seen = first.get((sport, gid))
            if not seen:
                continue
            at, old = seen
            checked += 1
            diffs = [
                f"{key}: {old[key]} -> {rec[key]}"
                for key in old
                if key in rec and old[key] and rec[key] and old[key] != rec[key]
            ]
            if diffs:
                changes += 1
                print(f"CHANGED {sport.upper()} {gid} ({page}, first seen {at}): " + "; ".join(diffs))
    print(f"compared {checked} cards against their first snapshot: {changes} changed")
    return 1 if changes else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("snap", "compare"))
    ap.add_argument("--url", default="https://predictionlab.io")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    return snap(base) and 0 if args.action == "snap" else compare(base)


if __name__ == "__main__":
    sys.exit(main())
