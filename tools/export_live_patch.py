"""Build data/live_patch/<name>.json.gz from the local database (read-only).

Usage: .venv/bin/python tools/export_live_patch.py NAME [--since YYYY-MM-DD]

Copies prediction lock columns saved no later than game day, and stored
sportsbook lines, for games on or after --since. live_data_patch.py applies
the file once on the live database.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from live_data_patch import LINE_COLS, LOCK_COLS, PATCH_DIR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--since", default="2026-10-15")
    ap.add_argument("--db", default=str(ROOT / "sports_predictions_original.db"))
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    preds = []
    for r in conn.execute(
        f"SELECT sport, game_id, game_date, home_team_id, created_at, {', '.join(LOCK_COLS)} "
        "FROM predictions WHERE game_date >= ? AND sport NOT IN ('TENNIS', 'UFC', 'GOLF')",
        (args.since,),
    ):
        if all(r[c] is None for c in LOCK_COLS):
            continue
        if str(r["created_at"] or "")[:10] > str(r["game_date"] or "")[:10]:
            continue
        preds.append({k: r[k] for k in ("sport", "game_id", "game_date", "home_team_id", *LOCK_COLS)})
    lines = []
    for r in conn.execute(
        f"SELECT sport, game_id, game_date, home_team, away_team, {', '.join(LINE_COLS)}, source, fetched_at "
        "FROM betting_lines WHERE game_date >= ? AND sport IS NOT NULL AND game_id IS NOT NULL "
        "ORDER BY fetched_at",
        (args.since,),
    ):
        lines.append(dict(r))
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    out = PATCH_DIR / f"{args.name}.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump({"since": args.since, "predictions": preds, "betting_lines": lines}, fh)
    print(f"{out}: {len(preds)} predictions, {len(lines)} book lines, {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
