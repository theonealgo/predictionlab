#!/usr/bin/env python3
"""One-shot: resend Premium welcome emails to subscribers who never got one.

Eligibility (default):
  - users.is_premium = 1
  - users.welcome_email_sent_at IS NULL
  - users.stripe_subscription_id is set
  - no row yet in premium_welcome_emails for that subscription_id

Uses the same idempotent path as the Stripe webhook
(`_maybe_send_premium_welcome_email`), so a second run will not double-send.

Examples (run from repo root, with SMTP_* env loaded the same way as the app):

  # Preview who would be emailed
  python scripts/resend_missed_welcome_emails.py --dry-run

  # Send to at most 20 recent premium users missing welcome
  python scripts/resend_missed_welcome_emails.py --limit 20

  # Optional DB path override
  python scripts/resend_missed_welcome_emails.py --db sports_predictions_original.db --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--db',
        default=os.environ.get('AUTH_DB_PATH') or 'sports_predictions_original.db',
        help='SQLite DB path (default: AUTH_DB_PATH or sports_predictions_original.db)',
    )
    parser.add_argument('--dry-run', action='store_true', help='List eligible users only')
    parser.add_argument('--limit', type=int, default=50, help='Max sends (default 50)')
    parser.add_argument(
        '--email',
        default='',
        help='Only this email (optional; still must be eligible)',
    )
    args = parser.parse_args(argv)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import auth_system as auth

    db_path = args.db
    if not os.path.isabs(db_path):
        db_path = os.path.join(repo_root, db_path)
    if not os.path.isfile(db_path):
        print(f'ERROR: DB not found: {db_path}', file=sys.stderr)
        return 2

    auth._DB_PATH = db_path
    auth._ensure_users_table()

    conn = auth._get_db()
    sql = '''
        SELECT u.id, u.email, u.name, u.stripe_subscription_id, u.created_at
        FROM users u
        WHERE u.is_premium = 1
          AND (u.welcome_email_sent_at IS NULL OR TRIM(u.welcome_email_sent_at) = '')
          AND u.stripe_subscription_id IS NOT NULL
          AND TRIM(u.stripe_subscription_id) != ''
          AND NOT EXISTS (
              SELECT 1 FROM premium_welcome_emails p
              WHERE p.subscription_id = u.stripe_subscription_id
          )
    '''
    params = []
    if args.email.strip():
        sql += ' AND lower(u.email) = ?'
        params.append(args.email.strip().lower())
    sql += ' ORDER BY u.id DESC LIMIT ?'
    params.append(max(1, int(args.limit)))

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    print(f'DB: {db_path}')
    print(f'Eligible: {len(rows)} (limit={args.limit}, dry_run={args.dry_run})')
    if not rows:
        return 0

    sent = 0
    failed = 0
    for row in rows:
        user_id = row['id']
        email = row['email']
        sub_id = row['stripe_subscription_id']
        print(f'- user_id={user_id} email={email} sub={sub_id} created={row["created_at"]}')
        if args.dry_run:
            continue
        ok = auth._maybe_send_premium_welcome_email(
            user_id=user_id,
            email=email,
            name=row['name'],
            plan='monthly',
            subscription_id=sub_id,
            event_id=f'manual_resend:{sub_id}',
            is_initial_subscribe=True,
        )
        if ok:
            sent += 1
            print('  sent')
        else:
            failed += 1
            print('  not_sent (see [welcome] logs; may already be claimed)')

    if args.dry_run:
        print('Dry run only — no emails sent.')
    else:
        print(f'Done. sent={sent} not_sent={failed}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
