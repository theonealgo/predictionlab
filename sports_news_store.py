"""Persistent news drafts and approvals; never opens the predictions database."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from zoneinfo import ZoneInfo

BUNDLED = Path(__file__).resolve().parent / "data/sports_news_posts.json"


def store_path():
    from flask import current_app, has_app_context
    if has_app_context() and current_app.config.get("SPORTS_NEWS_STORE"):
        return Path(current_app.config["SPORTS_NEWS_STORE"])
    if Path("/data").is_dir():
        return Path("/data/sports-news/posts.json")
    return BUNDLED


def load(path=None):
    path = Path(path or store_path())
    source = path if path.exists() else BUNDLED if path == Path("/data/sports-news/posts.json") else None
    if source is None:
        return {"posts": [], "runs": []}
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("posts"), list):
        raise ValueError("The news store is invalid; it was not overwritten.")
    document.setdefault("runs", [])
    return document

def revision(path=None):
    path = Path(path or store_path())
    try:
        return (str(path), path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        return (str(path), 0, 0)


@contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("News storage is busy; retry the action.")
                time.sleep(.05)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def update(change, path=None):
    path = Path(path or store_path())
    with _locked(path):
        document = load(path)
        before = deepcopy(document)
        result = change(document)
        if document == before and path.exists():
            return result
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".news-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return result


def content_version(post):
    content = {key: post.get(key) for key in
               ("title", "body", "canonical_path", "provenance", "news_items",
                "status", "review", "date_published")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def review(slug, version, publish, reviewer, path=None):
    from sports_news import validate_article

    def change(document):
        post = next((p for p in document["posts"] if p.get("slug") == slug), None)
        if post is None:
            raise LookupError("Article not found.")
        if not version or content_version(post) != version:
            raise ValueError("The article changed. Reload and review the current version.")
        validate_article(post)
        timestamp = datetime.now(timezone.utc).isoformat()
        post["date_modified"] = timestamp
        post.setdefault("created_date", post["date"])
        if publish and post.get("status") != "published" and not post.get("first_published_at"):
            post["date_published"] = timestamp
            post["date"] = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        post["status"] = "published" if publish else "draft"
        post["review"] = {"by": str(reviewer), "at": timestamp,
                          "version": version, "decision": post["status"]}
        if publish and not post.get("first_published_at"):
            post["first_published_at"] = timestamp
        post.setdefault("review_history", []).append(deepcopy(post["review"]))
        post["review_history"] = post["review_history"][-30:]
        # An article approval is not authorization to send anything to Meta.
        post["social_drafts"]["ready_for_delivery"] = False
        return deepcopy(post)

    return update(change, path)
