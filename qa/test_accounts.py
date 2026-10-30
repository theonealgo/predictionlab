"""Permanent paid and free logins for the checker.

Local server: two accounts in the app database, made once and kept (never
deleted). The database is never pushed, so they never reach the live site.

Live site: the owner's own paid and free accounts on predictionlab.io, saved
once with:

    python qa/test_accounts.py --set-live     # asks for the live emails/passwords

Logins are kept in .cache/checker_accounts.json (git-ignored, owner-only).

    python qa/test_accounts.py --cleanup      # delete old throwaway accounts only
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "@checker.local.test"            # old throwaway accounts
SAVED_DOMAIN = "@checker-saved.local.test"  # permanent local accounts
DB_NAME = "sports_predictions_original.db"
SAVED_FILE = ROOT / ".cache" / "checker_accounts.json"


def _hash(password: str) -> str:
    """Same format werkzeug's check_password_hash reads (pbkdf2:sha256)."""
    salt = secrets.token_hex(8)
    iters = 260000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iters).hex()
    return f"pbkdf2:sha256:{iters}${salt}${digest}"


def _connect(app_dir: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(str(Path(app_dir) / DB_NAME), timeout=30)


def is_live(base: str | None) -> bool:
    """True for the public site; False for this Mac or the home network."""
    host = (urlsplit(base).hostname or "") if base else ""
    if not host or host in ("localhost", "127.0.0.1", "0.0.0.0") or host.endswith(".local"):
        return False
    return not host.startswith(("192.168.", "10.", "172."))


def _load() -> dict:
    try:
        return json.loads(SAVED_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    SAVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAVED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(SAVED_FILE)


def cleanup(app_dir: Path | str = ROOT, stale_only: bool = False) -> int:
    """Delete old throwaway accounts. The permanent accounts are never touched."""
    con = _connect(app_dir)
    try:
        with con:
            sql = "DELETE FROM users WHERE email LIKE ?"
            if stale_only:
                sql += " AND created_at < datetime('now', '-3 hours')"
            return con.execute(sql, ("%" + DOMAIN,)).rowcount
    finally:
        con.close()


def _local_accounts(app_dir: Path | str) -> dict[str, tuple[str, str]]:
    """The two permanent local accounts; made (or repaired) in the app database."""
    data = _load()
    local = data.get("local") or {}
    con = _connect(app_dir)
    try:
        with con:
            for kind, premium in (("paid", 1), ("free", 0)):
                email, password = local.get(kind) or (f"checker-{kind}{SAVED_DOMAIN}", secrets.token_urlsafe(18))
                row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                if row:
                    con.execute("UPDATE users SET password_hash = ?, is_premium = ? WHERE email = ?",
                                (_hash(password), premium, email))
                else:
                    con.execute(
                        "INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, ?)",
                        (email, "Checker", _hash(password), premium),
                    )
                local[kind] = [email, password]
    finally:
        con.close()
    if data.get("local") != local:
        data["local"] = local
        _save(data)
    return {k: tuple(v) for k, v in local.items()}


def _live_accounts() -> dict[str, tuple[str, str]]:
    live = _load().get("live") or {}
    if not live.get("paid"):
        raise RuntimeError("no live checker accounts saved; run: python qa/test_accounts.py --set-live")
    return {k: tuple(v) for k, v in live.items() if v}


@contextlib.contextmanager
def accounts(app_dir: Path | str = ROOT, base: str | None = None):
    """Yields {'paid': (email, password), 'free': (email, password)}. Nothing is deleted."""
    if is_live(base):
        yield _live_accounts()
        return
    cleanup(app_dir, stale_only=True)
    yield _local_accounts(app_dir)


def _set_live() -> None:
    import getpass
    data = _load()
    live = {}
    for kind in ("paid", "free"):
        email = input(f"live {kind} account email: ").strip()
        password = getpass.getpass(f"live {kind} account password: ")
        live[kind] = [email, password]
    data["live"] = live
    _save(data)
    print(f"saved to {SAVED_FILE}")


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        print(f"removed {cleanup()} old throwaway checker account(s)")
    elif "--set-live" in sys.argv:
        _set_live()
