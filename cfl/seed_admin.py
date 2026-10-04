"""
Seed a local admin user so we can log in past the paywall.

Admin email is in ADMIN_EMAILS in auth_system.py (or .env.local), so the
account automatically gets `premium_active = True` without needing Stripe.

Usage:
    python3 seed_admin.py [email] [password]

Defaults to admin@local.test / admin123 .
"""
import sys
import os
import sqlite3
from werkzeug.security import generate_password_hash

# Honor DATABASE_PATH (set in .env.local for local dev). Otherwise mirror
# NHL77FINAL.py's default: /data/... in prod, ./... in dev.
try:
    from dotenv import load_dotenv
    _here = os.path.dirname(os.path.abspath(__file__))
    for _p in [os.path.join(_here, '.env.local'), os.path.join(_here, '.env')]:
        if os.path.exists(_p):
            load_dotenv(_p, override=False)
            break
except ImportError:
    pass

DB = os.environ.get('DATABASE_PATH', '').strip()
if not DB:
    DATA_DIR = '/data' if os.path.isdir('/data') else '.'
    DB = os.path.join(DATA_DIR, 'sports_predictions_original.db')

email = (sys.argv[1] if len(sys.argv) > 1 else 'admin@local.test').strip().lower()
password = sys.argv[2] if len(sys.argv) > 2 else 'admin123'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Make sure the table exists (auth_system also does this on app boot).
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

pw_hash = generate_password_hash(password)
existing = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()

if existing:
    conn.execute(
        'UPDATE users SET password_hash = ?, is_premium = 1 WHERE email = ?',
        (pw_hash, email),
    )
    print(f"Updated existing user: {email}")
else:
    conn.execute(
        'INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, 1)',
        (email, email.split('@')[0], pw_hash),
    )
    print(f"Created new user: {email}")

conn.commit()
conn.close()

print(f"DB:       {DB}")
print(f"Login:    {email}")
print(f"Password: {password}")
print("Reminder: this email must also appear in ADMIN_EMAILS (.env.local) "
      "for automatic premium access.")
