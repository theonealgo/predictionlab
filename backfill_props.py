#!/usr/bin/env python3
"""
backfill_props.py — Backfill NBA player prop results for this season.

Uses ESPN's free game log API to get every player's actual stats for every
game this season, then grades them against the lines our engine generates
today.  Results go into player_prop_results in the main SQLite DB.

Usage:
    python backfill_props.py             # backfill NBA
    python backfill_props.py --dry-run   # preview without writing
"""

import sys, os, sqlite3, time, argparse, requests
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
PROPS_BACKEND = os.path.join(BASE_DIR, "standalone-player-props", "backend")
DATABASE   = os.path.join(BASE_DIR, "sports_predictions_original.db")

# Remove conflicting app.py from import path and import the props engine package
for _k in list(sys.modules.keys()):
    if _k == "app" or _k.startswith("app."):
        del sys.modules[_k]
# Put backend first; remove base dir to avoid finding app.py there
sys.path = [PROPS_BACKEND] + [p for p in sys.path if os.path.abspath(p) != BASE_DIR]

from app.engine import get_league_data  # noqa: E402

# Restore base dir at end of path (for other imports if needed)
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# ── Config ─────────────────────────────────────────────────────────────────
LEAGUE      = "NBA"
REQUEST_DELAY = 0.35   # seconds between ESPN calls — be polite
TODAY       = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Map engine prop_type keys to gamelog label names
STAT_LABEL = {
    "points":    "PTS",
    "rebounds":  "REB",
    "assists":   "AST",
    "threes":    "3PT",
    "steals":    "STL",
}


# ── Helpers ────────────────────────────────────────────────────────────────
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_made(v):
    """Parse "made-attempted" or plain float → made count."""
    s = str(v)
    if "-" in s:
        try:
            return float(s.split("-")[0])
        except ValueError:
            return 0.0
    return _num(v)


def fetch_gamelog(player_id: str, player_name: str) -> list[dict]:
    """Return a list of game dicts: {date, points, rebounds, assists, threes, steals}."""
    url = (
        f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
        f"/athletes/{player_id}/gamelog"
    )
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        body = r.json()
    except Exception as exc:
        print(f"  ESPN error for {player_name}: {exc}")
        return []

    labels     = body.get("labels") or []
    idx        = {name: i for i, name in enumerate(labels)}
    events_meta = body.get("events") or {}

    seasons = body.get("seasonTypes") or []
    # Prefer regular season; fall back to first available
    regular = next(
        (s for s in seasons if "regular" in (s.get("displayName") or "").lower()),
        seasons[0] if seasons else None,
    )
    if regular is None:
        return []

    games = []
    for cat in (regular.get("categories") or []):
        for ev in (cat.get("events") or []):
            stats = ev.get("stats") or []
            if not stats:
                continue

            ev_id = str(ev.get("eventId") or ev.get("id") or "")
            meta  = events_meta.get(ev_id) or {}
            raw_date = meta.get("gameDate") or ""
            if not raw_date:
                continue

            game_date = raw_date[:10]   # "YYYY-MM-DD"
            if game_date >= TODAY:       # skip today / future
                continue

            mins = _num(stats[idx["MIN"]]) if "MIN" in idx else 0.0
            if mins < 1:                 # DNP — skip
                continue

            def _stat(key):
                if key not in idx:
                    return None
                raw = stats[idx[key]] if idx[key] < len(stats) else None
                if raw is None:
                    return None
                if key in ("3PT",):
                    return _parse_made(raw)
                return _num(raw)

            games.append({
                "date":     game_date,
                "points":   _stat("PTS"),
                "rebounds": _stat("REB"),
                "assists":  _stat("AST"),
                "threes":   _stat("3PT"),
                "steals":   _stat("STL"),
            })

    return games


def run(dry_run=False):
    print(f"Loading {LEAGUE} props from engine (this may take ~30 s)…")
    data  = get_league_data(LEAGUE)
    props = data.get("props") or []

    if not props:
        print("ERROR: No props returned. Make sure the props backend dependencies are installed.")
        sys.exit(1)

    # Group by player_id → {player_name, team, lines: {prop_type: line}}
    player_map: dict[str, dict] = {}
    for p in props:
        pid = p.get("player_id") or p.get("player_name")
        if not pid:
            continue
        if pid not in player_map:
            player_map[pid] = {
                "player_name": p["player_name"],
                "team":        p.get("team", ""),
                "lines":       {},
                "picks":       {},
            }
        pt = p.get("prop_type")
        if pt:
            player_map[pid]["lines"][pt] = float(p.get("line") or p.get("_calc_line") or 0.0)
            player_map[pid]["picks"][pt] = p.get("picked_side") or "OVER"

    print(f"Found {len(props)} props across {len(player_map)} players.\n")

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    inserted = 0
    skipped  = 0
    no_games = 0

    for i, (pid, player) in enumerate(player_map.items(), 1):
        name = player["player_name"]
        print(f"[{i}/{len(player_map)}] {name}", end="", flush=True)

        games = fetch_gamelog(pid, name)
        if not games:
            print(" — no game log")
            no_games += 1
            time.sleep(REQUEST_DELAY)
            continue

        print(f" — {len(games)} games", flush=True)

        for game in games:
            for prop_type, line in player["lines"].items():
                if line == 0.0:
                    continue
                actual = game.get(prop_type)
                if actual is None:
                    continue

                pick = player["picks"].get(prop_type, "OVER")
                hit  = (actual > line and pick == "OVER") or (actual < line and pick == "UNDER")
                result = "HIT" if hit else "MISS"

                if dry_run:
                    inserted += 1
                    continue

                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO player_prop_results
                           (league, result_date, player_name, team,
                            prop_type, pick, line, projection, actual, result)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            LEAGUE, game["date"],
                            name, player["team"],
                            prop_type, pick, line,
                            None, actual, result,
                        ),
                    )
                    inserted += 1
                except Exception as exc:
                    print(f"  DB error: {exc}")
                    skipped += 1

        if not dry_run:
            conn.commit()

        time.sleep(REQUEST_DELAY)

    conn.close()

    mode = "DRY RUN — " if dry_run else ""
    print(f"\n{mode}Done.")
    print(f"  Players processed : {len(player_map) - no_games}")
    print(f"  Players with no log: {no_games}")
    print(f"  Rows {'would insert' if dry_run else 'inserted'}: {inserted}")
    if skipped:
        print(f"  Rows skipped (errors): {skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Count rows without writing to DB")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
