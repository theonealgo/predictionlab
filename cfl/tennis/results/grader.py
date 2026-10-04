"""Tennis grader — winner + O/U games."""
from __future__ import annotations

from shared.db import connect
from shared.roi import roi_from_bets
from tennis.database.paths import DB_PATH
from tennis.database.init_db import seed


def performance() -> dict:
    if not DB_PATH.exists():
        seed()
    with connect(DB_PATH) as conn:
        grades = [dict(r) for r in conn.execute("SELECT * FROM grades")]
        runs = [dict(r) for r in conn.execute("SELECT * FROM model_runs")]
    bets = [{"result": g.get("result") or "PUSH", "odds": -110, "stake": 1.0} for g in grades]
    summary = roi_from_bets(bets)
    summary["model_runs"] = runs
    return summary


def grade_pending() -> int:
    return 0
