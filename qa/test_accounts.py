"""Throwaway paid and free logins for one local checker run.

Both accounts are written to the app database when a check starts and deleted
when it ends (and any left behind by a crashed run are deleted after 3 hours),
so they never ship in a pushed database.

    python qa/test_accounts.py --cleanup   # delete every checker account now
"""
from __future__ import annotations

import contextlib
import hashlib
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "@checker.local.test"
DB_NAME = "sports_predictions_original.db"


def _hash(password: str) -> str:
    """Same format werkzeug's check_password_hash reads (pbkdf2:sha256)."""
    salt = secrets.token_hex(8)
    iters = 260000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iters).hex()
    return f"pbkdf2:sha256:{iters}${salt}${digest}"


def _connect(app_dir: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(str(Path(app_dir) / DB_NAME), timeout=30)


def cleanup(app_dir: Path | str = ROOT, stale_only: bool = False) -> int:
    con = _connect(app_dir)
    try:
        with con:
            sql = "DELETE FROM users WHERE email LIKE ?"
            if stale_only:
                sql += " AND created_at < datetime('now', '-3 hours')"
            return con.execute(sql, ("%" + DOMAIN,)).rowcount
    finally:
        con.close()


@contextlib.contextmanager
def accounts(app_dir: Path | str = ROOT):
    """Yields {'paid': (email, password), 'free': (email, password)}; both removed on exit."""
    cleanup(app_dir, stale_only=True)
    tag = secrets.token_hex(4)
    made: dict[str, tuple[str, str]] = {}
    con = _connect(app_dir)
    try:
        with con:
            for kind, premium in (("paid", 1), ("free", 0)):
                email = f"checker-{kind}-{tag}{DOMAIN}"
                password = secrets.token_urlsafe(18)
                con.execute(
                    "INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, ?)",
                    (email, "Checker", _hash(password), premium),
                )
                made[kind] = (email, password)
    finally:
        con.close()
    try:
        yield made
    finally:
        con = _connect(app_dir)
        try:
            with con:
                con.executemany("DELETE FROM users WHERE email = ?", [(e,) for e, _ in made.values()])
        finally:
            con.close()


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        print(f"removed {cleanup()} checker account(s)")
