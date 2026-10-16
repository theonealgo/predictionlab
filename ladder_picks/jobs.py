"""Scheduled ladder pass: settle, expire, select, then social posts.

Runs in a background thread inside the web app (every LADDER_INTERVAL_MINUTES,
default 20; set LADDER_SCHEDULER=0 to turn it off) and can be run by hand:

    python -m ladder_picks.jobs            # one pass
    python -m ladder_picks.jobs --status   # print the current ladder
    python -m ladder_picks.jobs --dry-run  # show today's best pick without saving it
    python -m ladder_picks.jobs --types    # pick types whose graded record qualifies

Every step is safe to repeat: a second pass finds the work already done.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import social, store
from .engine import ET, LadderEngine

logger = logging.getLogger("ladder_picks")

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def run_once(now: datetime | None = None, engine: LadderEngine | None = None) -> dict:
    """One full pass, serialized across threads and processes by a lock file."""
    lock_path = Path(store.db_path()).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"skipped": "another ladder pass is running"}
        try:
            engine = engine or LadderEngine()
            out = engine.run_cycle(now)
            try:
                out["social_queued"] = social.queue_due_posts(engine.conn)
                out["social"] = social.process_queue(engine.conn)
            except Exception:
                logger.exception("ladder: social posting pass failed")
            return out
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _loop(interval_s: int) -> None:
    import time

    time.sleep(30)
    while True:
        try:
            out = run_once()
            sel = (out.get("selection") or {}).get("action")
            if out.get("settled") or sel in ("published", "no_pick") or out.get("expired"):
                logger.info("ladder pass: %s", json.dumps(out, default=str))
        except Exception:
            logger.exception("ladder: scheduled pass failed")
        time.sleep(interval_s)


def start_background() -> bool:
    global _thread
    if (os.environ.get("LADDER_SCHEDULER") or "1").strip() in ("0", "false", "no"):
        return False
    try:
        minutes = max(5, int(os.environ.get("LADDER_INTERVAL_MINUTES") or 20))
    except ValueError:
        minutes = 20
    with _thread_lock:
        if _thread and _thread.is_alive():
            return False
        _thread = threading.Thread(target=_loop, args=(minutes * 60,), daemon=True, name="ladder-picks")
        _thread.start()
    return True


def _status() -> dict:
    conn = store.connect()
    ladder = conn.execute("SELECT * FROM ladder_runs ORDER BY id DESC LIMIT 1").fetchone()
    days = []
    if ladder:
        days = [dict(r) for r in conn.execute(
            "SELECT day_number, pick_date, result, sport, event_label, pick_team, american_odds, "
            "stake_cents, profit_cents, amount_after_cents FROM ladder_days WHERE ladder_id = ? "
            "ORDER BY day_number", (ladder["id"],))]
    return {"db": store.db_path(), "ladder": dict(ladder) if ladder else None, "days": days}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--status" in sys.argv:
        print(json.dumps(_status(), indent=2, default=str))
    elif "--types" in sys.argv:
        today = datetime.now(timezone.utc).astimezone(ET).date()
        print(json.dumps(LadderEngine().qualifying_types(today), indent=2, default=str))
    elif "--dry-run" in sys.argv:
        print(json.dumps(LadderEngine().find_pick(datetime.now(timezone.utc)), indent=2, default=str))
    else:
        print(json.dumps(run_once(datetime.now(timezone.utc)), indent=2, default=str))
