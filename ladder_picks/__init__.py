"""10-Day Ladder Picks: one published pick a day, compounding from $10."""
from __future__ import annotations

import logging

logger = logging.getLogger("ladder_picks")


def init_app(app) -> None:
    from . import jobs, store
    from .views import bp

    app.register_blueprint(bp)
    try:
        store.connect().close()
    except Exception:
        logger.exception("ladder: database setup failed")
        return
    if jobs.start_background():
        logger.info("ladder: background checker started (db %s)", store.db_path())
