#!/usr/bin/env python3
"""Create or update a local admin user with premium access.

Reads DATABASE from NHL77FINAL (or DATABASE_PATH env). Password from
ADMIN_PASSWORD env, or the sandbox default documented in LOCAL_TESTING.md.

Usage:
    python3 scripts/seed_local_admin.py
    ADMIN_PASSWORD='my-secret' python3 scripts/seed_local_admin.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _p in (os.path.join(_root, '.env.local'), os.path.join(_root, '.env')):
        if os.path.exists(_p):
            load_dotenv(_p, override=True)
            break
except ImportError:
    pass

from werkzeug.security import generate_password_hash

# Documented in LOCAL_TESTING.md — sandbox-only default
DEFAULT_ADMIN_EMAIL = 'admin@predictionlab.local'
DEFAULT_ADMIN_PASSWORD = 'sandbox-admin-2026'


def _db_path() -> str:
    explicit = (os.environ.get('DATABASE_PATH') or '').strip()
    if explicit:
        return explicit
    data_dir = '/data' if os.path.isdir('/data') else '.'
    return os.path.join(data_dir, 'sports_predictions_original.db')


def seed_admin(
    email: str = DEFAULT_ADMIN_EMAIL,
    password: str | None = None,
    db_path: str | None = None,
) -> None:
    password = (password or os.environ.get('ADMIN_PASSWORD') or DEFAULT_ADMIN_PASSWORD).strip()
    if not password:
        print('Set ADMIN_PASSWORD or pass a password.', file=sys.stderr)
        sys.exit(1)

    db_path = db_path or _db_path()
    if not os.path.exists(db_path):
        print(f'Database not found: {db_path}', file=sys.stderr)
        sys.exit(1)

    pw_hash = generate_password_hash(password)
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            password_hash TEXT,
            google_id TEXT,
            is_premium INTEGER DEFAULT 0,
            premium_expires TEXT,
            stripe_customer_id TEXT,
            session_token TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    if existing:
        conn.execute(
            'UPDATE users SET password_hash = ?, is_premium = 1, name = ? WHERE email = ?',
            (pw_hash, email.split('@')[0], email),
        )
        action = 'updated'
    else:
        conn.execute(
            'INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, 1)',
            (email, email.split('@')[0], pw_hash),
        )
        action = 'created'
    conn.commit()
    conn.close()
    print(f'Admin {action}: {email} (premium=1) in {db_path}')


if __name__ == '__main__':
    email = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ADMIN_EMAIL).strip().lower()
    seed_admin(email=email)
