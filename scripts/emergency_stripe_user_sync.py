#!/usr/bin/env python3
"""EMERGENCY: rebuild premium users from Stripe into /data DB only.

Render Shell (one line):
  python scripts/emergency_stripe_user_sync.py
"""
import os
import sys
import sqlite3
import secrets
from datetime import datetime, timezone, timedelta

DB_PATH = "/data/sports_predictions_original.db"
ALLOWED = os.path.realpath(DB_PATH)
HOCK_EMAIL = "hockmichael186@gmail.com"


def main():
    if not os.path.isdir("/data"):
        sys.exit("REFUSE: /data not mounted")
    if os.path.realpath(DB_PATH) != ALLOWED or not DB_PATH.startswith("/data/"):
        sys.exit(f"REFUSE: refusing path {DB_PATH!r}")
    if not os.path.isfile(DB_PATH):
        sys.exit(f"REFUSE: missing {DB_PATH}")

    print(f"OK path={ALLOWED} size={os.path.getsize(DB_PATH)} bytes")

    key = os.environ.get("STRIPE_SECRET_KEY") or ""
    if not key.startswith("sk_"):
        sys.exit("REFUSE: STRIPE_SECRET_KEY missing/invalid in env")

    import stripe
    from werkzeug.security import generate_password_hash

    stripe.api_key = key

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    need = {
        "email",
        "is_premium",
        "premium_expires",
        "stripe_customer_id",
        "stripe_subscription_id",
        "password_hash",
        "name",
    }
    missing = need - cols
    if missing:
        sys.exit(f"REFUSE: users table missing columns: {sorted(missing)}")

    print(f"users before: {conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]}")

    def iso_from_unix(ts):
        if not ts:
            return None
        try:
            return (
                datetime.fromtimestamp(int(ts), tz=timezone.utc)
                .replace(tzinfo=None)
                .isoformat()
            )
        except Exception:
            return None

    def period_end(sub):
        pe = getattr(sub, "current_period_end", None)
        if pe:
            return iso_from_unix(pe)
        try:
            for item in sub["items"]["data"] or []:
                pe = (
                    item.get("current_period_end")
                    if isinstance(item, dict)
                    else getattr(item, "current_period_end", None)
                )
                if pe:
                    return iso_from_unix(pe)
        except Exception:
            pass
        return None

    _cust_cache = {}

    def cust_email(customer_id):
        if not customer_id:
            return None
        if customer_id in _cust_cache:
            return _cust_cache[customer_id][0]
        try:
            c = stripe.Customer.retrieve(customer_id)
            email = (getattr(c, "email", None) or "").strip().lower() or None
            name = getattr(c, "name", None) or None
        except Exception as e:
            print(f"  WARN customer retrieve {customer_id}: {e}")
            email, name = None, None
        _cust_cache[customer_id] = (email, name)
        return email

    def cust_name(customer_id):
        if customer_id in _cust_cache:
            return _cust_cache[customer_id][1]
        cust_email(customer_id)
        return _cust_cache.get(customer_id, (None, None))[1]

    def upsert(email, name, customer_id, sub_id, expires, password_hash=None):
        email = (email or "").strip().lower()
        if not email:
            return "skip_no_email"
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not row and customer_id:
            row = conn.execute(
                "SELECT id FROM users WHERE stripe_customer_id = ?",
                (customer_id,),
            ).fetchone()
        if row:
            uid = row["id"]
            conn.execute(
                """UPDATE users SET
                       is_premium = 1,
                       premium_expires = COALESCE(?, premium_expires),
                       stripe_customer_id = COALESCE(?, stripe_customer_id),
                       stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                       name = COALESCE(name, ?)
                   WHERE id = ?""",
                (expires, customer_id, sub_id, name, uid),
            )
            if password_hash:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (password_hash, uid),
                )
            return f"updated:{uid}"
        conn.execute(
            """INSERT INTO users
                   (email, name, password_hash, is_premium, premium_expires,
                    stripe_customer_id, stripe_subscription_id)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (
                email,
                name or email.split("@")[0],
                password_hash,
                expires,
                customer_id,
                sub_id,
            ),
        )
        uid = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()["id"]
        return f"inserted:{uid}"

    statuses = ("active", "trialing", "past_due")
    seen_subs = set()
    by_email = {}
    stats = {"ok": 0, "no_email": 0, "errors": 0}

    for status in statuses:
        starting_after = None
        while True:
            kwargs = {"status": status, "limit": 100}
            if starting_after:
                kwargs["starting_after"] = starting_after
            page = stripe.Subscription.list(**kwargs)
            data = list(page.data)
            if not data:
                break
            for sub in data:
                if sub.id in seen_subs:
                    continue
                seen_subs.add(sub.id)
                cid = (
                    sub.customer
                    if isinstance(sub.customer, str)
                    else getattr(sub.customer, "id", None)
                )
                email = cust_email(cid)
                name = cust_name(cid)
                expires = period_end(sub)
                if not email:
                    stats["no_email"] += 1
                    print(
                        f"SKIP no email sub={sub.id} customer={cid} status={status}"
                    )
                    continue
                by_email[email] = (name, cid, sub.id, expires, status)
            if not page.has_more:
                break
            starting_after = data[-1].id

    print(f"Stripe subs: {len(seen_subs)}; unique emails: {len(by_email)}")

    for email, (name, cid, sid, expires, status) in sorted(by_email.items()):
        try:
            result = upsert(email, name, cid, sid, expires, password_hash=None)
            stats["ok"] += 1
            print(
                f"{result} {email} status={status} expires={expires} "
                f"cust={cid} sub={sid}"
            )
        except Exception as e:
            stats["errors"] += 1
            print(f"ERR {email}: {e}")

    # --- hockmichael: premium + printed temp password ---
    temp_pw = secrets.token_urlsafe(16)
    pw_hash = generate_password_hash(temp_pw)
    hock = by_email.get(HOCK_EMAIL)
    if hock:
        name, cid, sid, expires, status = hock
    else:
        name, cid, sid, expires, status = (
            "hockmichael",
            None,
            None,
            None,
            "manual",
        )
        expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
        print(
            "WARN: hockmichael not in Stripe active/trialing/past_due — "
            "granting 7-day emergency premium"
        )

    result = upsert(HOCK_EMAIL, name, cid, sid, expires, password_hash=pw_hash)
    conn.commit()

    row = conn.execute(
        """SELECT id, email, is_premium, premium_expires, stripe_customer_id,
                  stripe_subscription_id
           FROM users WHERE email = ?""",
        (HOCK_EMAIL,),
    ).fetchone()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    premium = conn.execute(
        "SELECT COUNT(*) FROM users WHERE is_premium = 1"
    ).fetchone()[0]
    conn.close()

    print("\n========== HOCKMICHAEL TEMP PASSWORD (copy now) ==========")
    print(f"email:    {HOCK_EMAIL}")
    print(f"password: {temp_pw}")
    print(f"row:      {dict(row) if row else None}")
    print(f"upsert:   {result}")
    print("=========================================================")
    print(f"DONE stats={stats} users_total={total} premium={premium}")


if __name__ == "__main__":
    main()
