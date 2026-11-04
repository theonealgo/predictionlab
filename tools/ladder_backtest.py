"""Chronological ladder backtest (read-only).

Replays every past date: the pick-type records use only games before that date,
the day's candidates are that date's published (locked) picks, and the best
qualifying one is graded. Prints per-rule results, a held-out split, the ladder
completion rate, and the per-type table.

Spread / total prices before price history began are not stored; those picks are
priced at -110, except NHL puck line and MLB run line, whose real prices run far
from -110, so they are left out (no price = not eligible).

    .venv/bin/python tools/ladder_backtest.py [--since 2025-10-01] [--holdout 2026-09-19]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ladder_picks import money, sources  # noqa: E402
from ladder_picks.engine import _wilson_lower  # noqa: E402

UNPRICED_SPREAD = {"NHL", "MLB"}
EXCLUDED = {("NHL", "spread", "dog"), ("SOCCER", "spread", "dog"), ("SOCCER", "spread", "fav")}


def _locked_ids(path: str) -> set[tuple[str, str]]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        return {(s, g) for s, g in c.execute(
            "SELECT sport, game_id FROM predictions WHERE lock_card_json IS NOT NULL AND COALESCE(locked,0)=1")}


def _price(rec) -> int | None:
    if rec.get("unpriced"):
        return None
    if rec["market"] == "moneyline":
        return money.parse_american(rec["price"])
    if rec["sport"] in UNPRICED_SPREAD and rec["market"] == "spread":
        return None
    return -110


def _be(price: int) -> float:
    return 1.0 / float(money.american_to_decimal(price))


def _profit(price: int, win: bool) -> float:
    return float(money.american_to_decimal(price)) - 1 if win else -1.0


def load(since: str, until: str) -> list[dict]:
    src = sources.PredictionSource()
    locked = _locked_ids(src.path)
    out = []
    for r in src.graded_history(since, until):
        if r["sport"] not in ("TENNIS", "UFC") and (r["sport"], r["game_id"]) not in locked:
            continue
        r["american"] = _price(r)
        r["key"] = (r["sport"], r["market"], r["model"], r["side_class"])
        out.append(r)
    return out


class Rule:
    def __init__(self, name, window_days, min_n, min_rate, z, margin, lo, hi, allow=None, need_roi=False):
        self.name, self.window, self.min_n, self.min_rate = name, window_days, min_n, min_rate
        self.z, self.margin, self.lo, self.hi, self.allow, self.need_roi = z, margin, lo, hi, allow, need_roi

    def price_ok(self, p):
        if p is None:
            return False
        d = float(money.american_to_decimal(p))
        return float(money.american_to_decimal(self.lo)) - 1e-9 <= d <= float(money.american_to_decimal(self.hi)) + 1e-9

    def allowed(self, key):
        if (key[0], key[1], key[3]) in EXCLUDED and self.allow is None:
            return False
        return self.allow is None or key in self.allow


def stats_for(history: list[dict], rule: Rule):
    g = defaultdict(list)
    for r in history:
        if r["american"] is None or not rule.price_ok(r["american"]):
            continue
        g[r["key"]].append(r)
    out = {}
    for k, rs in g.items():
        n = len(rs)
        w = sum(r["win"] for r in rs)
        out[k] = {"n": n, "w": w, "rate": w / n, "lower": _wilson_lower(w, n, rule.z),
                  "roi": sum(_profit(r["american"], r["win"]) for r in rs) / n}
    return out


def replay(recs: list[dict], rule: Rule, start: str, end: str):
    by_day = defaultdict(list)
    for r in recs:
        by_day[r["date"]].append(r)
    picks, skips = [], 0
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while d <= stop:
        ds = d.isoformat()
        if by_day.get(ds):
            lo = (d - timedelta(days=rule.window)).isoformat() if rule.window else "0000"
            hist = [r for r in recs if lo <= r["date"] < ds]
            st = stats_for(hist, rule)
            best = None
            for c in by_day[ds]:
                s = st.get(c["key"])
                if not s or not rule.allowed(c["key"]) or not rule.price_ok(c["american"]):
                    continue
                if s["n"] < rule.min_n or s["rate"] < rule.min_rate:
                    continue
                if rule.need_roi and s["roi"] <= 0:
                    continue
                edge = s["lower"] - _be(c["american"]) - rule.margin
                if edge <= 0:
                    continue
                score = (edge, s["n"])
                if best is None or score > best[0]:
                    best = (score, c, s)
            if best:
                picks.append({**best[1], "type_n": best[2]["n"], "type_rate": best[2]["rate"]})
            else:
                skips += 1
        d += timedelta(days=1)
    return picks, skips


def summarize(picks, skips, label):
    n = len(picks)
    if not n:
        return f"{label:<34} picks 0  skipped days {skips}"
    w = sum(p["win"] for p in picks)
    roi = sum(_profit(p["american"], p["win"]) for p in picks) / n
    streak = cur = 0
    for p in picks:
        cur = 0 if p["win"] else cur + 1
        streak = max(streak, cur)
    avg = sum(float(money.american_to_decimal(p["american"])) for p in picks) / n
    lad = ladders(picks)
    return (f"{label:<34} picks {n:>4}  {w}-{n - w}  hit {w / n:6.1%}  low95 {_wilson_lower(w, n, 1.96):6.1%}  "
            f"avg dec {avg:4.2f}  ROI {roi:+6.1%}  worst losing run {streak}  skipped days {skips}  "
            f"ladders {lad[0]} done {lad[1]} (best day reached {lad[2]})")


def ladders(picks, days=10):
    started = done = best = day = 0
    for p in picks:
        if day == 0:
            started += 1
        if p["win"]:
            day += 1
            best = max(best, day)
            if day == days:
                done += 1
                day = 0
        else:
            day = 0
    return started, done, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-07-01")
    ap.add_argument("--start", default="2025-11-01", help="first replayed date")
    ap.add_argument("--holdout", default="2026-09-19")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()
    today = date.today().isoformat()
    recs = load(a.since, today)
    print(f"graded published picks: {len(recs)}  ({a.since} .. {today})")
    claude = {("NFL", "total", "XSharp", "OVER"), ("NFL", "total", "XSharp", "UNDER"),
              ("NHL", "spread", "XSharp", "dog"), ("NBA", "moneyline", "Consensus", "all")}
    rules = [
        Rule("current (21d, n20, 60%, z80)", 21, 20, 0.60, 0.8416, 0.0, -150, 150),
        Rule("proposed (all prior, n100, z95+3)", 0, 100, 0.0, 1.96, 0.03, -150, 150, need_roi=True),
        Rule("middle (all prior, n50, z90+2)", 0, 50, 0.0, 1.645, 0.02, -150, 150, need_roi=True),
        Rule("claude list (-200..+100 band)", 0, 30, 0.0, 1.96, 0.0, -200, 100, allow=claude),
    ]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for rule in rules:
        picks, skips = replay(recs, rule, a.start, yesterday)
        train = [p for p in picks if p["date"] < a.holdout]
        test = [p for p in picks if p["date"] >= a.holdout]
        print(summarize(picks, skips, rule.name))
        print(summarize(train, 0, "   before " + a.holdout))
        print(summarize(test, 0, "   held out from " + a.holdout))
    if a.table:
        rule = Rule("table", 0, 0, 0, 1.96, 0, -200, 150)
        st = stats_for(recs, rule)
        print("\nper type (all published history, priced -200..+150):")
        for k, s in sorted(st.items(), key=lambda kv: -kv[1]["lower"]):
            if s["n"] >= 30:
                print(f"  {' '.join(k):<36} n {s['n']:>4}  hit {s['rate']:6.1%}  low95 {s['lower']:6.1%}  ROI {s['roi']:+6.1%}")


if __name__ == "__main__":
    main()
