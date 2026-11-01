"""Build data/live_patch/<name>.json.gz from the local database (read-only).

Usage: .venv/bin/python tools/export_live_patch.py NAME [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--games] [--lines]

Copies prediction lock and model columns saved no later than game day for
games from --since up to (not including) --until, so published upcoming picks
are never rewritten. --lines adds stored sportsbook lines; --games adds
every stored finished game (live inserts only the ones it is missing).
live_data_patch.py applies the file once on the live database.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from live_data_patch import LINE_COLS, LOCK_COLS, MODEL_COLS, PATCH_DIR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--since", default="2026-10-15")
    ap.add_argument("--until", default=date.today().isoformat(),
                    help="predictions only for games before this date (published picks stay)")
    ap.add_argument("--games", action="store_true")
    ap.add_argument("--lines", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "sports_predictions_original.db"))
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = (*LOCK_COLS, *MODEL_COLS)
    preds = []
    for r in conn.execute(
        f"SELECT sport, game_id, game_date, home_team_id, created_at, {', '.join(cols)} "
        "FROM predictions WHERE game_date >= ? AND date(game_date) < date(?) "
        "AND sport NOT IN ('TENNIS', 'UFC', 'GOLF')",
        (args.since, args.until),
    ):
        if all(r[c] is None for c in cols):
            continue
        if str(r["created_at"] or "")[:10] > str(r["game_date"] or "")[:10]:
            continue
        preds.append({k: r[k] for k in ("sport", "game_id", "game_date", "home_team_id", *cols)})
    lines = []
    for r in conn.execute(
        f"SELECT sport, game_id, game_date, home_team, away_team, {', '.join(LINE_COLS)}, source, fetched_at "
        "FROM betting_lines WHERE game_date >= ? AND sport IS NOT NULL AND game_id IS NOT NULL "
        "ORDER BY fetched_at",
        (args.since,),
    ) if args.lines else ():
        lines.append(dict(r))
    games = []
    if args.games:
        for r in conn.execute(
            "SELECT * FROM games WHERE home_score IS NOT NULL AND away_score IS NOT NULL "
            "AND sport IS NOT NULL AND game_id IS NOT NULL ORDER BY date(game_date), game_id"
        ):
            games.append({k: r[k] for k in r.keys() if k != "id"})
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    out = PATCH_DIR / f"{args.name}.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump({"since": args.since, "predictions": preds, "betting_lines": lines, "games": games}, fh)
    print(f"{out}: {len(games)} games, {len(preds)} predictions, {len(lines)} book lines, "
          f"{out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
