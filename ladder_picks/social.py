"""Optional social posts for the ladder. Off unless LADDER_SOCIAL_ENABLED=1.

Posts never name the pick. A "Day N is live" post goes out only after that day's
pick is saved; a result post goes out only after the ladder has been settled.
Each (ladder, day, kind, platform) is posted at most once; a send that dies
mid-request is marked needs_review instead of being retried blind.

Platforms and environment variables
- x:        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
- facebook: FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN
- bluesky:  BLUESKY_HANDLE, BLUESKY_APP_PASSWORD
- discord:  LADDER_DISCORD_WEBHOOK_URL
Choose platforms with LADDER_SOCIAL_PLATFORMS (comma list, default all four).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import money, store

logger = logging.getLogger("ladder_picks")

SITE = "PredictionLab.io"
MAX_ATTEMPTS = 5
_STALE_SENDING = timedelta(minutes=30)

_REQUIRED_ENV = {
    "x": ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"),
    "facebook": ("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN"),
    "bluesky": ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"),
    "discord": ("LADDER_DISCORD_WEBHOOK_URL",),
}


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enabled_platforms() -> list[str]:
    if (os.environ.get("LADDER_SOCIAL_ENABLED") or "").strip() not in ("1", "true", "yes"):
        return []
    wanted = os.environ.get("LADDER_SOCIAL_PLATFORMS") or ",".join(_REQUIRED_ENV)
    out = []
    for name in (p.strip().lower() for p in wanted.split(",")):
        keys = _REQUIRED_ENV.get(name)
        if keys and all((os.environ.get(k) or "").strip() for k in keys):
            out.append(name)
    return out


def missing_config() -> dict[str, list[str]]:
    return {p: [k for k in keys if not (os.environ.get(k) or "").strip()] for p, keys in _REQUIRED_ENV.items()}


def day_live_text(day_number: int) -> str:
    return (f"PredictionLab Ladder — Day {day_number} is live. "
            f"Today's pick is available to premium members at {SITE}.")


def result_text(ladder) -> str | None:
    if ladder["status"] == "completed":
        return (f"PredictionLab Ladder #{ladder['id']} completed all 10 days: "
                f"{money.fmt_money(ladder['start_amount_cents'])} to "
                f"{money.fmt_money(ladder['current_amount_cents'])}. Full history at {SITE}.")
    if ladder["status"] == "failed":
        return (f"PredictionLab Ladder #{ladder['id']} ended on Day {ladder['final_day']}. "
                f"A new ladder starts with the next qualifying pick at {SITE}.")
    return None


# ── Queue ────────────────────────────────────────────────────────────────────
def _queue(conn, ladder_id: int, day_number: int, kind: str, text: str, platforms: list[str]) -> int:
    added = 0
    now = _now_iso()
    with store.write_txn(conn):
        for platform in platforms:
            cur = conn.execute(
                """INSERT OR IGNORE INTO ladder_social_posts
                       (ladder_id, day_number, kind, platform, status, text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (ladder_id, day_number, kind, platform, text, now, now),
            )
            added += cur.rowcount
    return added


def queue_due_posts(conn, platforms: list[str] | None = None) -> int:
    """Queue posts for saved picks and settled ladders. Safe to call any number of times."""
    platforms = enabled_platforms() if platforms is None else platforms
    if not platforms:
        return 0
    added = 0
    # Verified pick: the row is committed, has a game, and has not been settled yet.
    for day in conn.execute(
        """SELECT d.ladder_id, d.day_number FROM ladder_days d
           JOIN ladder_runs r ON r.id = d.ladder_id
           WHERE d.result = 'pending' AND d.game_id IS NOT NULL AND d.american_odds IS NOT NULL"""
    ).fetchall():
        added += _queue(conn, day["ladder_id"], day["day_number"], "day_live",
                        day_live_text(day["day_number"]), platforms)
    for ladder in conn.execute(
        "SELECT * FROM ladder_runs WHERE status IN ('completed', 'failed') AND finished_at IS NOT NULL"
    ).fetchall():
        text = result_text(ladder)
        if text:
            added += _queue(conn, ladder["id"], 0, "ladder_result", text, platforms)
    return added


# ── Senders ──────────────────────────────────────────────────────────────────
def _oauth1_header(method: str, url: str, key: str, secret: str, token: str, token_secret: str) -> str:
    params = {
        "oauth_consumer_key": key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    q = urllib.parse.quote
    base_params = "&".join(f"{q(k, safe='')}={q(v, safe='')}" for k, v in sorted(params.items()))
    base = "&".join((method.upper(), q(url, safe=""), q(base_params, safe="")))
    signing_key = f"{q(secret, safe='')}&{q(token_secret, safe='')}"
    sig = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{q(k, safe="")}="{q(v, safe="")}"' for k, v in sorted(params.items()))


def _send_x(text: str) -> str:
    import requests

    url = "https://api.x.com/2/tweets"
    auth = _oauth1_header("POST", url, os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                          os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_TOKEN_SECRET"])
    resp = requests.post(url, json={"text": text}, headers={"Authorization": auth}, timeout=15)
    resp.raise_for_status()
    return str((resp.json().get("data") or {}).get("id") or "")


def _send_facebook(text: str) -> str:
    import requests

    resp = requests.post(
        f"https://graph.facebook.com/v19.0/{os.environ['FB_PAGE_ID']}/feed",
        data={"message": text, "access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]},
        timeout=15,
    )
    resp.raise_for_status()
    return str(resp.json().get("id") or "")


def _send_bluesky(text: str) -> str:
    import requests

    sess = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": os.environ["BLUESKY_HANDLE"], "password": os.environ["BLUESKY_APP_PASSWORD"]},
        timeout=15,
    )
    sess.raise_for_status()
    data = sess.json()
    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {data['accessJwt']}"},
        json={
            "repo": data["did"],
            "collection": "app.bsky.feed.post",
            "record": {"$type": "app.bsky.feed.post", "text": text,
                       "createdAt": _now_iso().replace("Z", ".000Z")},
        },
        timeout=15,
    )
    resp.raise_for_status()
    return str(resp.json().get("uri") or "")


def _send_discord(text: str) -> str:
    import requests

    resp = requests.post(os.environ["LADDER_DISCORD_WEBHOOK_URL"], json={"content": text}, timeout=15)
    resp.raise_for_status()
    return "ok"


SENDERS: dict[str, Callable[[str], str]] = {
    "x": _send_x,
    "facebook": _send_facebook,
    "bluesky": _send_bluesky,
    "discord": _send_discord,
}


def process_queue(conn, senders: dict[str, Callable[[str], str]] | None = None,
                  now: datetime | None = None) -> dict:
    senders = senders or SENDERS
    now = now or datetime.now(timezone.utc)
    stats = {"posted": 0, "failed": 0, "needs_review": 0, "skipped": 0}
    with store.write_txn(conn):
        cur = conn.execute(
            "UPDATE ladder_social_posts SET status = 'needs_review', updated_at = ? "
            "WHERE status = 'sending' AND updated_at < ?",
            (_now_iso(now), _now_iso(now - _STALE_SENDING)),
        )
        stats["needs_review"] = cur.rowcount
    rows = conn.execute(
        "SELECT * FROM ladder_social_posts WHERE status IN ('pending', 'failed') AND attempts < ? ORDER BY id",
        (MAX_ATTEMPTS,),
    ).fetchall()
    for row in rows:
        if row["kind"] == "day_live":
            day = conn.execute(
                "SELECT result FROM ladder_days WHERE ladder_id = ? AND day_number = ?",
                (row["ladder_id"], row["day_number"]),
            ).fetchone()
            if not day or day["result"] != "pending":
                with store.write_txn(conn):
                    conn.execute(
                        "UPDATE ladder_social_posts SET status = 'skipped', updated_at = ? WHERE id = ? "
                        "AND status IN ('pending', 'failed')",
                        (_now_iso(now), row["id"]),
                    )
                stats["skipped"] += 1
                continue
        with store.write_txn(conn):
            claimed = conn.execute(
                "UPDATE ladder_social_posts SET status = 'sending', attempts = attempts + 1, updated_at = ? "
                "WHERE id = ? AND status IN ('pending', 'failed') AND attempts < ?",
                (_now_iso(now), row["id"], MAX_ATTEMPTS),
            ).rowcount
        if not claimed:
            continue
        sender = senders.get(row["platform"])
        try:
            if sender is None:
                raise RuntimeError("no sender for platform")
            external = sender(row["text"])
        except Exception as exc:
            msg = str(exc)[:300]
            with store.write_txn(conn):
                conn.execute(
                    "UPDATE ladder_social_posts SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
                    (msg, _now_iso(now), row["id"]),
                )
            logger.warning("ladder: %s post %s failed: %s", row["platform"], row["id"], msg)
            stats["failed"] += 1
            continue
        with store.write_txn(conn):
            conn.execute(
                "UPDATE ladder_social_posts SET status = 'posted', external_id = ?, last_error = NULL, "
                "updated_at = ? WHERE id = ?",
                (external or None, _now_iso(now), row["id"]),
            )
        stats["posted"] += 1
    return stats


def manual_post_text(conn) -> str | None:
    """Copyable post for the current ladder day (for manual publishing)."""
    row = conn.execute(
        """SELECT d.day_number FROM ladder_days d JOIN ladder_runs r ON r.id = d.ladder_id
           WHERE r.status = 'active' AND d.result = 'pending' ORDER BY d.id DESC LIMIT 1"""
    ).fetchone()
    return day_live_text(row["day_number"]) if row else None
