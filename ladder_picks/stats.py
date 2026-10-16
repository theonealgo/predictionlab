"""Historical ladder statistics with honest denominators."""
from __future__ import annotations


def ladder_stats(conn, days: int = 10) -> dict:
    runs = conn.execute("SELECT status, COUNT(*) AS n FROM ladder_runs GROUP BY status").fetchall()
    by_status = {r["status"]: r["n"] for r in runs}
    total = sum(by_status.values())
    finished = total - by_status.get("active", 0)
    completed = by_status.get("completed", 0)

    per_day = []
    rows = conn.execute(
        """SELECT day_number, result, COUNT(*) AS n FROM ladder_days
           WHERE result != 'no_pick' GROUP BY day_number, result"""
    ).fetchall()
    counts: dict[int, dict[str, int]] = {}
    for r in rows:
        counts.setdefault(r["day_number"], {})[r["result"]] = r["n"]
    for d in range(1, days + 1):
        c = counts.get(d, {})
        wins, losses = c.get("win", 0), c.get("loss", 0)
        pushes = c.get("push", 0) + c.get("void", 0)
        pending = c.get("pending", 0)
        settled = wins + losses + pushes
        per_day.append({
            "day": d,
            "reached": settled + pending,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "pending": pending,
            "settled": settled,
            # Survived = not a loss. Pending picks are not in the denominator.
            "success_rate": (wins + pushes) / settled if settled else None,
        })
    return {
        "total": total,
        "active": by_status.get("active", 0),
        "finished": finished,
        "completed": completed,
        "failed": by_status.get("failed", 0),
        "no_pick": by_status.get("no_pick", 0),
        "expired": by_status.get("expired", 0),
        # Only ladders that have ended count toward the completion rate.
        "completion_rate": completed / finished if finished else None,
        "per_day": per_day,
    }
