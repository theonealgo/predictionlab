"""Daily draft preparation only. No blog approval or social publishing runs here."""
from datetime import datetime, timedelta, timezone
import logging
import os
import threading
import uuid

from sports_news import ET, MAX_DAILY, generate, save_articles
from sports_news_store import store_path, update


def run_daily(path=None, now=None, force=False, get=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ET)
    if local.hour < 9 and not force:
        return {"status": "not_due"}
    path = path or store_path()
    today = local.date().isoformat()
    day = (local.date() - timedelta(days=1)).isoformat()
    token = uuid.uuid4().hex

    def claim(document):
        runs = document["runs"]
        recent = [r for r in runs if r.get("date") == today]
        if recent:
            last = recent[-1]
            age = (now - datetime.fromisoformat(last["started_at"])).total_seconds()
            if last["status"] == "running" and age < 300:
                return False
            if not force and (last["status"] == "complete" or age < 1800 or len(recent) >= 4):
                return False
        if not force and sum(p.get("created_date", p.get("date")) == today
                             for p in document["posts"]) >= MAX_DAILY:
            return False
        runs.append({"id": token, "date": today, "game_date": day,
                     "started_at": now.isoformat(), "status": "running"})
        document["runs"] = runs[-40:]
        return True

    if not update(claim, path):
        return {"status": "already_checked"}
    report = []
    try:
        articles = generate(day, get=get, now=now, report=report)
        save_articles(path, articles)  # Always drafts, even if an old env flag is set.
        status = "complete" if len(articles) == MAX_DAILY or not any(
            r["status"] in ("unavailable", "pending") for r in report) else "retry"
        result = {"status": status, "sports": report, "slugs": [p["slug"] for p in articles]}
    except Exception:
        logging.getLogger(__name__).exception("Sports-news draft preparation failed")
        result = {"status": "retry", "sports": report,
                  "reason": "Draft preparation failed. Existing articles were preserved."}

    def finish(document):
        run = next(r for r in document["runs"] if r["id"] == token)
        run.update(result, finished_at=datetime.now(timezone.utc).isoformat())
    update(finish, path)
    return result


def queue_batch(app):
    def run():
        with app.app_context():
            try:
                run_daily(force=True)
            except Exception:
                app.logger.exception("News review job unavailable")
    threading.Thread(target=run, name="sports-news-manual-review", daemon=True).start()


def start_worker(app):
    if app.testing or os.getenv("PL_NEWS_WORKER", "1") == "0":
        return
    if app.extensions.get("sports_news_worker"):
        return
    stop = threading.Event()
    app.extensions["sports_news_worker"] = stop

    def run():
        # A small startup delay keeps news work out of application boot.
        while not stop.wait(60):
            with app.app_context():
                try:
                    run_daily()
                except Exception:
                    app.logger.exception("Daily news preparation unavailable")
    threading.Thread(target=run, name="sports-news-daily-drafts", daemon=True).start()
