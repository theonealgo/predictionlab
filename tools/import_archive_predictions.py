"""Copy pre-game predictions from older site databases into
data/historic_results/archive/<sport>_<season>.json.

Run once: .venv/bin/python tools/import_archive_predictions.py <old db> [<old db> ...]

Every database is opened read-only. A prediction counts only when it was saved
at least 5 hours before kickoff (old timestamps carry no time zone; game times
are UTC). When a game was saved more than once before kickoff, across all the
databases given, the last of those saves is kept (what the site showed at game
time). Final scores may come from any of the databases.
"""
from __future__ import annotations

import json
import sqlite3
import struct
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "historic_results" / "archive"

IMPORTS = (
    ("NFL", "2025-26", "2025-09-04", "2026-02-10"),
)
MODEL_COLS = (
    ("elo_home_prob", "Edge"),
    ("xgboost_home_prob", "XSharp"),
)
SAFETY = timedelta(hours=5)


def _dt(s):
    s = str(s or "").replace("T", " ")[:26]
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
              "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def _prob(v):
    if isinstance(v, bytes):
        return struct.unpack("<f", v[:4])[0] if len(v) >= 4 else None
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _import(cons, sport, season, start, end):
    lo = datetime.strptime(start, "%Y-%m-%d")
    hi = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    finals, latest = {}, {}
    for con in cons:
        cols = {r[1] for r in con.execute("PRAGMA table_info(predictions)")}
        for g in con.execute(
            "SELECT game_date, home_team_id, away_team_id, home_score, away_score "
            "FROM games WHERE sport = ? AND home_score IS NOT NULL AND away_score IS NOT NULL",
            (sport,),
        ):
            d = _dt(g["game_date"])
            if d and (g["home_score"] or g["away_score"]):
                finals[(d.date(), g["home_team_id"], g["away_team_id"])] = (
                    int(g["home_score"]), int(g["away_score"]))
        for p in con.execute("SELECT * FROM predictions WHERE sport = ?", (sport,)):
            kick, saved = _dt(p["game_date"]), _dt(p["created_at"])
            if not kick or not saved or not (lo <= kick < hi):
                continue
            if not (kick.hour or kick.minute):
                kick = datetime.combine(kick.date(), time(12))
            if saved + SAFETY >= kick:
                continue
            key = (kick.date(), p["home_team_id"], p["away_team_id"])
            if key in latest and latest[key][0] >= saved:
                continue
            probs = {}
            for col, label in MODEL_COLS:
                v = _prob(p[col]) if col in cols else None
                if v is not None:
                    probs[label] = round(v / 100 if v > 1 else v, 4)
            latest[key] = (saved, probs)
    games = []
    for key, (_, probs) in latest.items():
        day, home, away = key
        score = finals.get(key) or finals.get((day - timedelta(days=1), home, away))
        if not score:
            continue
        games.append({
            "date": day.isoformat(),
            "home": home,
            "away": away,
            "home_score": score[0],
            "away_score": score[1],
            "home_prob": probs,
        })
    games.sort(key=lambda r: (r["date"], r["home"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{sport.lower()}_{season}.json"
    path.write_text(json.dumps({"sport": sport, "season": season, "games": games}, indent=1))
    print(f"{sport} {season}: {len(games)} pre-game games -> {path}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: import_archive_predictions.py <old db> [<old db> ...]")
    cons = []
    for path in sys.argv[1:]:
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        cons.append(con)
    try:
        for args in IMPORTS:
            _import(cons, *args)
    finally:
        for con in cons:
            con.close()


if __name__ == "__main__":
    main()
