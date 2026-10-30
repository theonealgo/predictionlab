"""Build data/historic_results/<season>.json (season records per model).

Run offline: .venv/bin/python tools/build_historic_results.py [2025-26]
The /historic-results pages only read the JSON this writes.

Only games whose prediction was saved before the game counts: the day before,
or before 12:00 UTC on game day (earlier than any game starts). Predictions
filled in after a game would grade the models on results they already had.
Spread, total and Efficiency records are kept only when that season's
pre-game predictions saved their lines.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUT_DIR = ROOT / "data" / "historic_results"

SEASONS = {
    "2025-26": (
        ("MLB", "2025", "2025-03-27", "2025-11-02"),
        ("NHL", "2025-26", "2025-10-07", "2026-06-30"),
        ("NBA", "2025-26", "2025-10-21", "2026-06-30"),
        ("NFL", "2025", "2025-09-04", "2026-02-10"),
        ("NCAAF", "2025", "2025-08-23", "2026-01-20"),
        ("NCAAB", "2025-26", "2025-11-03", "2026-04-07"),
        ("NCAAW", "2025-26", "2025-11-03", "2026-04-07"),
        ("WNBA", "2025", "2025-05-16", "2025-10-15"),
        ("CFL", "2025", "2025-06-05", "2025-11-17"),
        ("SOCCER", "2025-26", "2025-08-01", "2026-06-30"),
        ("TENNIS", "2025", "2025-01-01", "2025-12-31"),
        ("UFC", "2025", "2025-01-01", "2025-12-31"),
        ("GOLF", "2025", "2025-01-01", "2025-12-31"),
    ),
}

MODELS = (
    ("glicko2", "Grinder2"),
    ("trueskill", "Takedown"),
    ("elo", "Edge"),
    ("xgboost", "XSharp"),
    ("ensemble", "Sharp Consensus"),
    ("efficiency", "Efficiency"),
)
SPREAD_ROWS = (
    ("pl_spread_covered", "pl_spread_graded", "Prediction Lab"),
    ("spread_covered", "spread_graded", "XSharp"),
)
TOTAL_ROWS = (
    ("pl_total_correct", "pl_total_graded", "Prediction Lab"),
    ("total_correct", "total_graded", "XSharp"),
)
EMPTY = {"games": 0, "moneyline": {}, "spread": {}, "total": {}}


def _variant(sport: str):
    path = ROOT / "sport_variants" / sport.lower() / "picks_recent_results.py"
    if not path.is_file():
        path = ROOT / "picks_recent_results.py"
    spec = importlib.util.spec_from_file_location(f"_hr_prr_{sport.lower()}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wl(correct, total):
    total = int(total or 0)
    correct = int(correct or 0)
    if total <= 0:
        return None
    return {"w": correct, "l": total - correct}


def _norm(name) -> str:
    return " ".join(str(name or "").lower().split())


def _pregame_keys(sport: str, start: str, end: str) -> tuple[set[str], set[tuple]]:
    """(game ids, (date, home, away)) of games whose prediction was saved before
    the game. Results rows may carry a different game id format, so they are
    also matched on date and teams."""
    from ladder_picks.sources import main_db_path

    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT game_id, date(game_date), home_team_id, away_team_id "
            "FROM predictions WHERE sport = ? AND date(game_date) BETWEEN ? AND ? "
            "AND replace(created_at, 'T', ' ') < date(game_date) || ' 12:00:00'",
            (sport, start, end),
        ).fetchall()
    finally:
        con.close()
    return ({str(r[0]) for r in rows},
            {(str(r[1]), _norm(r[2]), _norm(r[3])) for r in rows})


def _pregame_lines_saved(sport: str, start: str, end: str) -> bool:
    """True when the season's pre-game predictions saved their spread / total.
    Without them the spread, total and Efficiency (built from the PL spread)
    grades were worked out after the games, so they are left off."""
    from ladder_picks.sources import main_db_path

    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM predictions WHERE sport = ? AND date(game_date) BETWEEN ? AND ? "
            "AND replace(created_at, 'T', ' ') < date(game_date) || ' 12:00:00' "
            "AND (lock_pl_spread IS NOT NULL OR json_extract(lock_card_json, '$.xgb_spread') IS NOT NULL)",
            (sport, start, end),
        ).fetchone()[0]
    finally:
        con.close()
    return n > 0


def _is_pregame(g: dict, day: str, ids: set[str], keys: set[tuple]) -> bool:
    if str(g.get("game_id")) in ids:
        return True
    d = str(g.get("date") or day)[:10]
    home = _norm(g.get("home") or g.get("home_team"))
    away = _norm(g.get("away") or g.get("away_team"))
    return (d, home, away) in keys


def _team_sport(N, sport, start, end):
    ids, keys = _pregame_keys(sport, start, end)
    if not ids:
        return dict(EMPTY)
    mod = _variant(sport)
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d").replace(hour=23, minute=59)
    daily = N._banner_daily_results_for_range(sport, s, e, prefer_weekly=False) or {}
    # Some seasons store dates as DD/MM/YYYY, which the SQL date filter skips.
    every = N._banner_daily_results_for_range(sport, None, None, prefer_weekly=False) or {}
    seen = {
        str(g.get("game_id"))
        for b in daily.values() for g in (b.get("games") or []) if isinstance(g, dict)
    }
    for dk, bucket in every.items():
        if not N._date_in_range(dk, s, e):
            continue
        for g in bucket.get("games") or []:
            if isinstance(g, dict) and str(g.get("game_id")) not in seen:
                seen.add(str(g.get("game_id")))
                daily.setdefault(dk, {"games": []}).setdefault("games", []).append(g)
    for dk in list(daily):
        games = [g for g in (daily[dk].get("games") or [])
                 if isinstance(g, dict) and _is_pregame(g, dk, ids, keys)]
        if games:
            daily[dk]["games"] = games
        else:
            del daily[dk]
    if not daily:
        return dict(EMPTY)
    if hasattr(mod, "_fill_efficiency_and_grades"):
        mod._fill_efficiency_and_grades(sport, daily)
    if hasattr(N, "_compute_spread_total_for_daily") and sport not in ("UFC", "GOLF", "TENNIS"):
        try:
            N._compute_spread_total_for_daily(sport, daily, skip_efficiency=True)
        except Exception as exc:
            print(f"  {sport} spread/total skipped: {exc}")
    tally = N.compute_model_tally_for_range(daily, s, e, sport=sport) or {}
    st = {}
    if sport not in ("UFC", "GOLF", "TENNIS") and hasattr(N, "_recount_spread_total_stats"):
        try:
            st = N._recount_spread_total_stats(daily) or {}
        except Exception as exc:
            print(f"  {sport} spread/total recount skipped: {exc}")
    ml = {}
    for key, label in MODELS:
        m = tally.get(key) or {}
        rec = _wl(m.get("correct"), m.get("total"))
        if rec:
            ml[label] = rec
    spread = {}
    for wk, nk, label in SPREAD_ROWS:
        rec = _wl(st.get(wk), st.get(nk))
        if rec:
            spread[label] = rec
    total = {}
    for wk, nk, label in TOTAL_ROWS:
        rec = _wl(st.get(wk), st.get(nk))
        if rec:
            total[label] = rec
    if not _pregame_lines_saved(sport, start, end):
        ml.pop("Efficiency", None)
        spread, total = {}, {}
    return {
        "games": int(tally.get("games") or 0),
        "moneyline": ml,
        "spread": spread,
        "total": total,
    }


def _archive(sport: str, season: str):
    """Moneyline records from an older site's pre-game predictions
    (data/historic_results/archive/, written by import_archive_predictions.py)."""
    path = OUT_DIR / "archive" / f"{sport.lower()}_{season}.json"
    if not path.is_file():
        return None
    games = json.loads(path.read_text()).get("games") or []
    ml, graded = {}, 0
    for g in games:
        if g["home_score"] == g["away_score"]:
            continue
        graded += 1
        home_won = g["home_score"] > g["away_score"]
        for _, label in MODELS:
            p = (g.get("home_prob") or {}).get(label)
            if p is None or p == 0.5:
                continue
            rec = ml.setdefault(label, {"w": 0, "l": 0})
            rec["w" if (p > 0.5) == home_won else "l"] += 1
    if not graded:
        return None
    return {"games": graded, "moneyline": ml, "spread": {}, "total": {}}


def _tennis(start, end):
    from tennis.database.paths import DB_PATH

    if not DB_PATH.is_file():
        return dict(EMPTY)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT g.winner_correct FROM matches m "
            "JOIN predictions p ON p.match_id = m.match_id "
            "JOIN grades g ON g.pred_id = p.pred_id "
            "WHERE m.status = 'final' AND g.winner_correct IS NOT NULL "
            "AND replace(p.created_at, 'T', ' ') < replace(m.match_date, 'T', ' ') "
            "AND substr(m.match_date, 1, 10) BETWEEN ? AND ?",
            (start, end),
        ).fetchall()
    finally:
        con.close()
    flags = [int(r[0]) for r in rows]
    ml = {}
    if flags:
        ml["Sharp Consensus"] = {"w": sum(flags), "l": len(flags) - sum(flags)}
    return {"games": len(flags), "moneyline": ml, "spread": {}, "total": {}}


def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
    if season not in SEASONS:
        raise SystemExit(f"unknown season {season}; known: {', '.join(SEASONS)}")
    os.environ["PL_SNAPSHOT_BUILD"] = "1"
    import NHL77FINAL as N

    out = {"season": season, "built": datetime.now().strftime("%Y-%m-%d %H:%M"), "sports": []}
    for sport, label, start, end in SEASONS[season]:
        t0 = time.time()
        try:
            if sport == "TENNIS":
                rec = _tennis(start, end)
            elif sport in ("UFC", "GOLF"):
                rec = dict(EMPTY)
            else:
                with N.app.test_request_context(f"/{sport.lower()}-results"):
                    rec = _team_sport(N, sport, start, end)
            if not rec.get("games"):
                rec = _archive(sport, season) or rec
        except Exception as exc:
            print(f"{sport}: failed ({exc})")
            rec = dict(EMPTY)
        rec.update({"sport": sport, "season": label, "start": start, "end": end})
        out["sports"].append(rec)
        print(f"{sport} {label}: {rec['games']} games, ML {rec['moneyline']}, "
              f"spread {rec['spread']}, total {rec['total']} ({time.time() - t0:.0f}s)")
        sys.stdout.flush()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{season}.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"wrote {path}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
    os._exit(0)
