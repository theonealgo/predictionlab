"""PredictionLab first-party affiliate platform.

Self-contained affiliate subsystem that plugs into the existing Flask app
(NHL77FINAL.py) and the existing auth/Stripe infrastructure (auth_system.py).

Design principles
-----------------
* Reuses the existing SQLite database (same file as auth/users) — no second DB.
* Reuses the existing Flask-Login authentication and admin authorization
  (``current_user`` + ``auth_system.ADMIN_EMAILS``). No second login/admin system.
* Reuses the existing SMTP email helpers in ``auth_system`` for notifications.
* All money is stored and computed in integer minor units (cents). Never floats.
* Commission creation from Stripe webhooks is idempotent (one commission per
  qualifying paid invoice, enforced by a UNIQUE index on the invoice id).
* Attribution is server/DB-backed (an opaque first-party visitor cookie maps to
  a DB attribution row), not localStorage.

Public entrypoints used by the host app
---------------------------------------
* ``init_affiliate(app, db_path)``          — call once after ``init_auth``.
* ``checkout_metadata(user)``               — merge into Stripe Checkout metadata.
* ``handle_invoice_paid(invoice, ...)``     — call from invoice.paid webhook.
* ``handle_refund(charge, ...)``            — call from charge.refunded webhook.
* ``handle_dispute(dispute, ...)``          — call from charge.dispute.* webhook.
* ``handle_invoice_uncollectible(...)``     — call from invoice.voided/uncollectible.

The Stripe webhook wiring lives in ``auth_system.stripe_webhook`` and calls the
handlers above via a guarded lazy import so this module stays optional and never
breaks existing billing if it is absent.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, Response, abort, current_app, flash, g, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

affiliate_bp = Blueprint('affiliate', __name__)

_DB_PATH = None

# ─── Configurable program terms (env-overridable; no magic numbers in logic) ──
# Commission rate in basis points (1000 = 10.00%).
AFFILIATE_COMMISSION_BPS = int(os.environ.get('AFFILIATE_COMMISSION_BPS', '1000'))
# Minimum payable balance before a payout can be requested, in cents ($100.00).
AFFILIATE_MIN_PAYOUT_CENTS = int(os.environ.get('AFFILIATE_MIN_PAYOUT_CENTS', '10000'))
# Holding period (days) before a pending commission becomes payable. Protects
# against refunds/chargebacks. Configurable — not hardcoded across the code.
AFFILIATE_HOLD_DAYS = int(os.environ.get('AFFILIATE_HOLD_DAYS', '30'))
# Referral attribution window (days). Last valid click within this window wins.
AFFILIATE_COOKIE_DAYS = int(os.environ.get('AFFILIATE_COOKIE_DAYS', '30'))
# Canonical site URL used to build referral links (reuses production domain).
AFFILIATE_SITE_URL = (
    os.environ.get('AFFILIATE_SITE_URL')
    or os.environ.get('SITE_URL')
    or 'https://predictionlab.io'
).rstrip('/')

_VISITOR_COOKIE = 'pl_aff_vid'         # opaque visitor id (server-backed attribution)
_LINKED_SESSION_KEY = 'pl_aff_linked'  # session flag: user already association-checked
_CLICK_DEDUPE_MINUTES = 30             # ignore repeat clicks from same visitor+code

# Commission lifecycle statuses.
_C_PENDING = 'pending'      # earned, inside holding period
_C_PAYABLE = 'payable'      # holding period elapsed, eligible for payout
_C_PAID = 'paid'            # included in a completed payout
_C_REVERSED = 'reversed'    # refunded / charged back
_C_CANCELLED = 'cancelled'  # admin-cancelled

# Affiliate application statuses.
_A_PENDING = 'pending'
_A_APPROVED = 'approved'
_A_REJECTED = 'rejected'
_A_SUSPENDED = 'suspended'

_VALID_CODE_RE = re.compile(r'^[A-Z0-9][A-Z0-9_-]{2,31}$')
_RESERVED_CODES = {'ADMIN', 'AFFILIATE', 'PREDICTIONLAB', 'PL', 'API', 'WWW', 'NULL', 'NONE'}

# Self-referral: earning commission on your OWN subscription. Self-PROMOTION to
# your own audience is always allowed; this flag only governs whether an affiliate
# may attribute + earn on their own account. Default off (protected); flip to
# allow via env if the business decides creators may earn on their own plan.
AFFILIATE_ALLOW_SELF_REFERRAL = os.environ.get('AFFILIATE_ALLOW_SELF_REFERRAL', '0') in ('1', 'true', 'yes')

# Optional campaign bonus (architected but inactive unless configured by admin).
# Base commission stays 10%; a per-campaign-brief bonus can add on top when > 0.
AFFILIATE_BONUS_ENABLED = os.environ.get('AFFILIATE_BONUS_ENABLED', '0') in ('1', 'true', 'yes')

# Current affiliate terms version (bump to require re-acceptance).
AFFILIATE_TERMS_VERSION = os.environ.get('AFFILIATE_TERMS_VERSION', '2026-09-11')

# Partner lifecycle stages (superset compatible with the application statuses).
_LIFECYCLE_STAGES = ['prospect', 'contacted', 'interested', 'applied',
                     'approved', 'active', 'inactive', 'suspended']

# Partner-lead CRM statuses.
_LEAD_STATUSES = ['prospect', 'contacted', 'interested', 'application_sent',
                  'applied', 'approved', 'rejected', 'not_interested',
                  'follow_up', 'converted']

# Quality-score weights (configurable). Deliberately NOT click-weighted so raw
# click volume cannot inflate the score — paying customers/conversion dominate.
_QUALITY_WEIGHTS = {
    'paying_customers': 34,   # up to +34 (>= 20 paying customers)
    'conversion_rate': 26,    # up to +26 (>= 8% click->customer)
    'revenue': 20,            # up to +20 (>= $1,000 net revenue)
    'retention': 14,          # up to +14 (recurring invoices per customer)
    'refund_penalty': 24,     # up to -24 (high refund/reversal rate)
    'relevance': 12,          # up to +12 (sports audience / relevant platform)
}

# Promotion catalog (subscription plans) — reused from the site's pricing.
_PROMO_CATALOG = [
    {'plan': 'Weekly', 'price': '$4.99 / week', 'path': '/plans',
     'blurb': 'Full model edge — spreads, totals, projected scores — billed weekly.'},
    {'plan': 'Monthly', 'price': '$19.99 / month', 'path': '/plans',
     'blurb': 'Everything in Premium, billed monthly. Most popular for regular bettors.'},
    {'plan': 'Yearly', 'price': '$149.99 / year', 'path': '/plans',
     'blurb': 'Best value — full-season access to every model and sport.'},
]

# Destination pages the link builder can target.
_LINK_DESTINATIONS = [
    ('/', 'Home'),
    ('/plans', 'Plans & Pricing'),
    ('/ai-sports-betting-picks-today', 'Free Picks Today'),
    ('/mlb-picks', 'MLB Picks'), ('/nba-picks', 'NBA Picks'),
    ('/nfl-picks', 'NFL Picks'), ('/nhl-picks', 'NHL Picks'),
    ('/soccer-picks', 'Soccer Picks'), ('/performance', 'Model Performance'),
]

_CAMPAIGN_SLUG_RE = re.compile(r'[^a-z0-9]+')


# ══════════════════════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════════════════════

def _get_db():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat(sep=' ')


def _iso_plus_days(days):
    return (datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
            + timedelta(days=days)).isoformat(sep=' ')


def _ensure_affiliate_tables():
    """Create affiliate tables + indexes if missing (idempotent)."""
    conn = _get_db()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                affiliate_code TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                name TEXT,
                email TEXT,
                country TEXT,
                website_url TEXT,
                promo_method TEXT,
                application_note TEXT,
                commission_bps INTEGER NOT NULL DEFAULT 1000,
                agreed_terms_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                approved_at TEXT,
                rejected_at TEXT,
                suspended_at TEXT
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_aff_user ON affiliates(user_id) WHERE user_id IS NOT NULL')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_aff_status ON affiliates(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_aff_email ON affiliates(email)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                visitor_id TEXT,
                landing_path TEXT,
                referrer TEXT,
                ip_hash TEXT,
                user_agent TEXT,
                is_suspicious INTEGER NOT NULL DEFAULT 0,
                converted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_click_aff ON affiliate_clicks(affiliate_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_click_visitor ON affiliate_clicks(visitor_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_click_created ON affiliate_clicks(created_at)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                visitor_id TEXT,
                user_id INTEGER,
                stripe_customer_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attributed_at TEXT DEFAULT (datetime('now')),
                signed_up_at TEXT,
                converted_at TEXT,
                first_subscription_id TEXT,
                locked INTEGER NOT NULL DEFAULT 0
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_attr_user ON affiliate_attributions(user_id) WHERE user_id IS NOT NULL')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_attr_visitor ON affiliate_attributions(visitor_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_attr_customer ON affiliate_attributions(stripe_customer_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_attr_aff ON affiliate_attributions(affiliate_id)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_commissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                attribution_id INTEGER,
                user_id INTEGER,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                stripe_invoice_id TEXT,
                stripe_payment_intent_id TEXT,
                stripe_charge_id TEXT,
                source_event_id TEXT,
                amount_gross_cents INTEGER NOT NULL,
                amount_refunded_cents INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'usd',
                commission_bps INTEGER NOT NULL,
                commission_cents INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                billing_reason TEXT,
                eligible_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                paid_at TEXT,
                reversed_at TEXT,
                payout_id INTEGER,
                admin_note TEXT
            )
        ''')
        # Idempotency: one commission per paid invoice.
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_comm_invoice ON affiliate_commissions(stripe_invoice_id) WHERE stripe_invoice_id IS NOT NULL')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_comm_aff ON affiliate_commissions(affiliate_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_comm_status ON affiliate_commissions(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_comm_charge ON affiliate_commissions(stripe_charge_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_comm_pi ON affiliate_commissions(stripe_payment_intent_id)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                status TEXT NOT NULL DEFAULT 'requested',
                method TEXT,
                reference TEXT,
                note TEXT,
                requested_at TEXT DEFAULT (datetime('now')),
                processed_at TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payout_aff ON affiliate_payouts(affiliate_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payout_status ON affiliate_payouts(status)')
        # DB-level guard against duplicate open payouts (race-safe): at most one
        # requested/processing payout per affiliate.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payout_open_unique "
            "ON affiliate_payouts(affiliate_id) WHERE status IN ('requested','processing')"
        )

        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER,
                kind TEXT,
                detail TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_aff ON affiliate_audit(affiliate_id)')

        # ── Extended affiliate profile / application fields (idempotent migrations) ──
        for col_sql in (
            "ALTER TABLE affiliates ADD COLUMN primary_platform TEXT",
            "ALTER TABLE affiliates ADD COLUMN youtube_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN tiktok_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN instagram_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN x_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN facebook_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN discord_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN newsletter_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN other_url TEXT",
            "ALTER TABLE affiliates ADD COLUMN audience_size INTEGER",
            "ALTER TABLE affiliates ADD COLUMN primary_countries TEXT",
            "ALTER TABLE affiliates ADD COLUMN sports TEXT",
            "ALTER TABLE affiliates ADD COLUMN audience_description TEXT",
            "ALTER TABLE affiliates ADD COLUMN paid_ads INTEGER DEFAULT 0",
            "ALTER TABLE affiliates ADD COLUMN existing_sports_audience INTEGER DEFAULT 0",
            "ALTER TABLE affiliates ADD COLUMN why_promote TEXT",
            "ALTER TABLE affiliates ADD COLUMN lifecycle TEXT",
            "ALTER TABLE affiliates ADD COLUMN onboarding_done_at TEXT",
            "ALTER TABLE affiliates ADD COLUMN bonus_bps INTEGER DEFAULT 0",
            # campaign tag captured on the click/attribution that led to a commission
            "ALTER TABLE affiliate_clicks ADD COLUMN campaign TEXT",
            "ALTER TABLE affiliate_attributions ADD COLUMN campaign TEXT",
            "ALTER TABLE affiliate_commissions ADD COLUMN campaign TEXT",
            "ALTER TABLE affiliate_commissions ADD COLUMN bonus_bps INTEGER DEFAULT 0",
        ):
            try:
                conn.execute(col_sql)
            except Exception:
                pass  # column already exists

        # ── Partner leads / CRM ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                contact_name TEXT,
                email TEXT,
                website_url TEXT,
                social_url TEXT,
                platform TEXT,
                country TEXT,
                sports TEXT,
                audience_size INTEGER,
                audience_description TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'prospect',
                source TEXT,
                assigned_admin TEXT,
                last_contacted TEXT,
                next_follow_up TEXT,
                converted_affiliate_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_lead_status ON affiliate_leads(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_lead_email ON affiliate_leads(email)')

        # ── Affiliate-owned campaigns (link builder + campaign tracking) ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                destination_path TEXT DEFAULT '/',
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_aff_slug ON affiliate_campaigns(affiliate_id, slug)')

        # ── Admin campaign briefs (visible to affiliates) ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_campaign_briefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                key_points TEXT,
                cta TEXT,
                destination_url TEXT,
                promo_code TEXT,
                start_date TEXT,
                end_date TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_brief_active ON affiliate_campaign_briefs(active)')

        # ── Marketing assets ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                asset_type TEXT,
                body TEXT,
                image_url TEXT,
                destination_url TEXT,
                sports TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_asset_active ON affiliate_assets(active)')

        # ── Affiliate Academy modules + per-affiliate progress ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_academy_modules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE,
                title TEXT NOT NULL,
                body TEXT,
                video_url TEXT,
                sort_order INTEGER DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_academy_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                module_id INTEGER NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        ''')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_progress_uniq ON affiliate_academy_progress(affiliate_id, module_id)')

        # ── Terms versions + acceptances (lightweight contracting) ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_terms_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT UNIQUE NOT NULL,
                body TEXT,
                requires_reaccept INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_terms_acceptances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER NOT NULL,
                version TEXT NOT NULL,
                accepted_at TEXT DEFAULT (datetime('now')),
                ip_hash TEXT,
                user_id INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_accept_aff ON affiliate_terms_acceptances(affiliate_id)')

        # ── Internal admin notes (never shown to affiliates) ──
        conn.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_admin_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id INTEGER,
                lead_id INTEGER,
                author TEXT,
                body TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_note_aff ON affiliate_admin_notes(affiliate_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_note_lead ON affiliate_admin_notes(lead_id)')

        # Performance indexes for date-filtered reporting.
        conn.execute('CREATE INDEX IF NOT EXISTS idx_comm_created ON affiliate_commissions(created_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_comm_aff_created ON affiliate_commissions(affiliate_id, created_at)')

        conn.commit()
    finally:
        conn.close()


def _audit(affiliate_id, kind, detail=''):
    try:
        conn = _get_db()
        conn.execute(
            'INSERT INTO affiliate_audit (affiliate_id, kind, detail) VALUES (?,?,?)',
            (affiliate_id, kind, str(detail)[:500]),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning('[affiliate] audit failed: %s', e)


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════════════

def _is_admin_user():
    try:
        return bool(current_user.is_authenticated and getattr(current_user, 'is_admin', False))
    except Exception:
        return False


def _hash_ip(ip):
    if not ip:
        return None
    return hashlib.sha256(('plaff:' + str(ip)).encode('utf-8')).hexdigest()[:32]


def _normalize_code(raw):
    return (raw or '').strip().upper()


def _valid_code(code):
    code = _normalize_code(code)
    return bool(_VALID_CODE_RE.match(code)) and code not in _RESERVED_CODES


def _obj_get(obj, key, default=None):
    """Safe get for dict or Stripe object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _stripe_id(val):
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return _obj_get(val, 'id')


def referral_link(code):
    return f'{AFFILIATE_SITE_URL}/?ref={code}'


# ─── CSRF (lightweight, session-backed — app has no global CSRFProtect) ───────

def _csrf_token():
    tok = session.get('_aff_csrf')
    if not tok:
        tok = secrets.token_urlsafe(32)
        session['_aff_csrf'] = tok
    return tok


def _check_csrf():
    sent = request.form.get('csrf_token', '')
    good = session.get('_aff_csrf', '')
    return bool(good) and secrets.compare_digest(str(sent), str(good))


# ══════════════════════════════════════════════════════════════════════════════
# Affiliate lookups
# ══════════════════════════════════════════════════════════════════════════════

def _affiliate_by_code(code, conn=None):
    code = _normalize_code(code)
    if not code:
        return None
    own = conn is None
    conn = conn or _get_db()
    try:
        return conn.execute(
            'SELECT * FROM affiliates WHERE affiliate_code = ?', (code,)
        ).fetchone()
    finally:
        if own:
            conn.close()


def _affiliate_by_user(user_id, conn=None):
    if not user_id:
        return None
    own = conn is None
    conn = conn or _get_db()
    try:
        return conn.execute(
            'SELECT * FROM affiliates WHERE user_id = ?', (user_id,)
        ).fetchone()
    finally:
        if own:
            conn.close()


def _affiliate_by_id(affiliate_id, conn=None):
    own = conn is None
    conn = conn or _get_db()
    try:
        return conn.execute(
            'SELECT * FROM affiliates WHERE id = ?', (affiliate_id,)
        ).fetchone()
    finally:
        if own:
            conn.close()


def _user_id_for_customer(customer_id, email=None, conn=None):
    """Best-effort local users.id for a Stripe customer (reuses users table)."""
    own = conn is None
    conn = conn or _get_db()
    try:
        if customer_id:
            row = conn.execute(
                'SELECT id FROM users WHERE stripe_customer_id = ? LIMIT 1',
                (customer_id,),
            ).fetchone()
            if row:
                return row['id']
        if email:
            row = conn.execute(
                'SELECT id FROM users WHERE lower(email) = ? LIMIT 1',
                (str(email).strip().lower(),),
            ).fetchone()
            if row:
                return row['id']
    except Exception as e:
        logger.warning('[affiliate] user lookup failed: %s', e)
    finally:
        if own:
            conn.close()
    return None


def _user_is_existing_customer(user_id, conn):
    """True if the user already subscribed (protects existing customers from
    being retro-attributed to an affiliate after they click a link)."""
    if not user_id:
        return False
    try:
        row = conn.execute(
            'SELECT is_premium, stripe_subscription_id FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()
        if not row:
            return False
        return bool(row['is_premium']) or bool(row['stripe_subscription_id'])
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Referral attribution (request lifecycle)
# ══════════════════════════════════════════════════════════════════════════════

def _clean_campaign(raw):
    """Normalize a campaign tag to a short slug (utm_campaign value)."""
    if not raw:
        return None
    slug = _CAMPAIGN_SLUG_RE.sub('-', str(raw).strip().lower()).strip('-')
    return slug[:60] or None


def _record_click(conn, affiliate_id, visitor_id, suspicious=False, campaign=None):
    """Insert a click unless the same visitor+affiliate clicked very recently."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=_CLICK_DEDUPE_MINUTES))
        cutoff = cutoff.replace(microsecond=0, tzinfo=None).isoformat(sep=' ')
        recent = conn.execute(
            'SELECT 1 FROM affiliate_clicks WHERE affiliate_id = ? AND visitor_id = ? '
            'AND created_at >= ? LIMIT 1',
            (affiliate_id, visitor_id, cutoff),
        ).fetchone()
        if recent:
            return
        conn.execute(
            'INSERT INTO affiliate_clicks '
            '(affiliate_id, visitor_id, landing_path, referrer, ip_hash, user_agent, is_suspicious, campaign) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (
                affiliate_id, visitor_id,
                (request.path or '/')[:300],
                (request.referrer or '')[:300] or None,
                _hash_ip(request.remote_addr),
                (request.headers.get('User-Agent') or '')[:300] or None,
                1 if suspicious else 0, campaign,
            ),
        )
    except Exception as e:
        logger.warning('[affiliate] click record failed: %s', e)


def _upsert_visitor_attribution(conn, affiliate_id, visitor_id, campaign=None):
    """Last-click-wins within the window, but never overwrite a locked
    (already-converted) attribution for that visitor."""
    row = conn.execute(
        'SELECT * FROM affiliate_attributions WHERE visitor_id = ? '
        'ORDER BY id DESC LIMIT 1',
        (visitor_id,),
    ).fetchone()
    now = _now_iso()
    if row is None:
        conn.execute(
            'INSERT INTO affiliate_attributions '
            '(affiliate_id, visitor_id, status, attributed_at, campaign) VALUES (?,?,?,?,?)',
            (affiliate_id, visitor_id, _A_PENDING, now, campaign),
        )
        return
    if row['locked'] or row['status'] == 'converted':
        return  # converted attribution is sticky
    if row['affiliate_id'] != affiliate_id or not row['user_id']:
        conn.execute(
            'UPDATE affiliate_attributions SET affiliate_id = ?, attributed_at = ?, '
            'campaign = COALESCE(?, campaign) WHERE id = ?',
            (affiliate_id, now, campaign, row['id']),
        )


def _capture_ref():
    """before_request: capture ?ref=CODE (+ optional ?utm_campaign=), validate,
    record click + attribution. Campaign attribution never changes which affiliate
    is credited — it only tags the click/attribution for reporting."""
    try:
        code = request.args.get('ref')
        if not code:
            return
        aff = _affiliate_by_code(code)
        if not aff or aff['status'] != _A_APPROVED:
            return  # only approved affiliates earn attribution
        campaign = _clean_campaign(request.args.get('utm_campaign') or request.args.get('campaign'))
        # Ensure a first-party visitor id (opaque; attribution stored server-side).
        visitor_id = request.cookies.get(_VISITOR_COOKIE)
        if not visitor_id:
            visitor_id = uuid.uuid4().hex
            g._aff_set_visitor = visitor_id  # after_request sets the cookie
        # Self-referral guard: affiliate clicking their own link while logged in.
        # Self-promotion is always fine; this only concerns earning on your OWN plan.
        is_self = False
        try:
            if (current_user.is_authenticated and aff['user_id']
                    and current_user.id == aff['user_id']):
                is_self = True
        except Exception:
            pass
        block_self = is_self and not AFFILIATE_ALLOW_SELF_REFERRAL
        conn = _get_db()
        try:
            _record_click(conn, aff['id'], visitor_id, suspicious=block_self, campaign=campaign)
            if not block_self:
                _upsert_visitor_attribution(conn, aff['id'], visitor_id, campaign=campaign)
            conn.commit()
        finally:
            conn.close()
        if is_self:
            _audit(aff['id'], 'self_click', f'user_id={getattr(current_user, "id", None)} blocked={block_self}')
    except Exception as e:
        logger.warning('[affiliate] capture_ref failed: %s', e)


def _associate_user():
    """before_request: link a visitor's pending attribution to the logged-in user.

    Runs at most once per user per session. Enforces:
    * one attribution per user,
    * no retro-attribution of existing paying customers,
    * self-referral rejection.
    """
    try:
        if not current_user.is_authenticated:
            return
        if session.get(_LINKED_SESSION_KEY) == current_user.id:
            return
        visitor_id = getattr(g, '_aff_set_visitor', None) or request.cookies.get(_VISITOR_COOKIE)
        if not visitor_id:
            session[_LINKED_SESSION_KEY] = current_user.id
            return
        conn = _get_db()
        try:
            existing = conn.execute(
                'SELECT 1 FROM affiliate_attributions WHERE user_id = ? LIMIT 1',
                (current_user.id,),
            ).fetchone()
            if existing:
                session[_LINKED_SESSION_KEY] = current_user.id
                return
            attr = conn.execute(
                'SELECT * FROM affiliate_attributions WHERE visitor_id = ? AND user_id IS NULL '
                'ORDER BY id DESC LIMIT 1',
                (visitor_id,),
            ).fetchone()
            if not attr:
                session[_LINKED_SESSION_KEY] = current_user.id
                return
            aff = _affiliate_by_id(attr['affiliate_id'], conn=conn)
            if not aff or aff['status'] != _A_APPROVED:
                session[_LINKED_SESSION_KEY] = current_user.id
                return
            # Self-referral: don't let an affiliate attribute their own account
            # (unless explicitly allowed). Self-promotion to an audience is fine.
            if (aff['user_id'] and aff['user_id'] == current_user.id
                    and not AFFILIATE_ALLOW_SELF_REFERRAL):
                _audit(aff['id'], 'self_referral_blocked', f'user_id={current_user.id}')
                session[_LINKED_SESSION_KEY] = current_user.id
                return
            # Existing-customer protection.
            if _user_is_existing_customer(current_user.id, conn):
                _audit(aff['id'], 'existing_customer_skip', f'user_id={current_user.id}')
                session[_LINKED_SESSION_KEY] = current_user.id
                return
            conn.execute(
                'UPDATE affiliate_attributions SET user_id = ?, status = ?, signed_up_at = ? '
                'WHERE id = ?',
                (current_user.id, 'signed_up', _now_iso(), attr['id']),
            )
            conn.commit()
            _audit(aff['id'], 'signup', f'user_id={current_user.id}')
        finally:
            conn.close()
        session[_LINKED_SESSION_KEY] = current_user.id
    except Exception as e:
        logger.warning('[affiliate] associate_user failed: %s', e)


def _set_visitor_cookie(response):
    """after_request: persist a newly minted visitor id as a first-party cookie."""
    try:
        vid = getattr(g, '_aff_set_visitor', None)
        if vid:
            secure = request.is_secure or request.headers.get('X-Forwarded-Proto', '') == 'https'
            response.set_cookie(
                _VISITOR_COOKIE, vid,
                max_age=AFFILIATE_COOKIE_DAYS * 86400,
                httponly=True, samesite='Lax', secure=bool(secure),
            )
    except Exception as e:
        logger.warning('[affiliate] set cookie failed: %s', e)
    return response


def _ensure_visitor_id():
    """Return the current first-party visitor id, minting one (set via
    after_request) if the visitor has none yet."""
    vid = getattr(g, '_aff_set_visitor', None) or request.cookies.get(_VISITOR_COOKIE)
    if not vid:
        vid = uuid.uuid4().hex
        g._aff_set_visitor = vid
    return vid


def apply_code(code, *, user_id=None, visitor_id=None):
    """Attribute a customer via an affiliate's promotional code (secondary path).

    Server-authoritative. Safe attribution rules (mirrors referral-link rules but
    is strictly non-overwriting — the referral link is the primary mechanism):

    * The code must belong to an APPROVED affiliate.
    * Self-referral (affiliate applying their own code) is blocked.
    * Existing paying customers are never retro-attributed.
    * If the visitor/user already has a valid affiliate attribution (from a
      referral link or an earlier code), it is NOT overwritten — a code can
      never steal attribution from another affiliate.

    Returns (ok: bool, reason: str). Never raises.
    """
    try:
        aff = _affiliate_by_code(code)
        if not aff or aff['status'] != _A_APPROVED:
            return False, 'invalid'
        # Self-referral guard (unless explicitly allowed).
        if (user_id and aff['user_id'] and aff['user_id'] == user_id
                and not AFFILIATE_ALLOW_SELF_REFERRAL):
            _audit(aff['id'], 'self_referral_code_blocked', f'user_id={user_id}')
            return False, 'self'
        conn = _get_db()
        try:
            # Existing paying customer → no retro-attribution.
            if user_id and _user_is_existing_customer(user_id, conn):
                _audit(aff['id'], 'existing_customer_code_skip', f'user_id={user_id}')
                return False, 'existing_customer'
            # Do not overwrite an existing valid attribution (link or code).
            existing = None
            if user_id:
                existing = conn.execute(
                    'SELECT * FROM affiliate_attributions WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                    (user_id,),
                ).fetchone()
            if not existing and visitor_id:
                existing = conn.execute(
                    "SELECT * FROM affiliate_attributions WHERE visitor_id = ? "
                    "AND status != 'void' ORDER BY id DESC LIMIT 1",
                    (visitor_id,),
                ).fetchone()
            if existing:
                if existing['affiliate_id'] == aff['id']:
                    return True, 'already'          # idempotent no-op for same affiliate
                _audit(aff['id'], 'code_no_overwrite',
                       f'existing_aff={existing["affiliate_id"]} user_id={user_id}')
                return False, 'already_attributed'  # never steal another affiliate's referral

            if user_id:
                conn.execute(
                    'INSERT INTO affiliate_attributions '
                    '(affiliate_id, visitor_id, user_id, status, attributed_at, signed_up_at) '
                    'VALUES (?,?,?,?,?,?)',
                    (aff['id'], visitor_id, user_id, 'signed_up', _now_iso(), _now_iso()),
                )
            else:
                conn.execute(
                    'INSERT INTO affiliate_attributions '
                    '(affiliate_id, visitor_id, status, attributed_at) VALUES (?,?,?,?)',
                    (aff['id'], visitor_id, _A_PENDING, _now_iso()),
                )
            conn.commit()
            _audit(aff['id'], 'code_applied', f'user_id={user_id} visitor={bool(visitor_id)}')
            return True, 'ok'
        finally:
            conn.close()
    except Exception as e:
        logger.warning('[affiliate] apply_code failed (non-fatal): %s', e)
        return False, 'error'


def apply_code_request(code):
    """Apply a code in the context of the current request (mints visitor cookie,
    uses the logged-in user if present). Returns (ok, reason)."""
    uid = getattr(current_user, 'id', None) if current_user.is_authenticated else None
    vid = _ensure_visitor_id()
    return apply_code(code, user_id=uid, visitor_id=vid)


def checkout_metadata(user=None):
    """Return Stripe Checkout metadata carrying affiliate attribution.

    Called from auth_system.checkout() so attribution survives the hosted
    Checkout/subscription flow (including guest checkout) and is available to
    the webhook on the resulting invoice/subscription.
    """
    meta = {}
    try:
        # Include a visitor id minted earlier in this same request (e.g. a code
        # applied at checkout) so brand-new visitors are still attributed.
        visitor_id = getattr(g, '_aff_set_visitor', None) or request.cookies.get(_VISITOR_COOKIE)
        conn = _get_db()
        try:
            attr = None
            uid = getattr(user, 'id', None)
            if uid:
                attr = conn.execute(
                    'SELECT * FROM affiliate_attributions WHERE user_id = ? ORDER BY id DESC LIMIT 1',
                    (uid,),
                ).fetchone()
            if not attr and visitor_id:
                attr = conn.execute(
                    'SELECT * FROM affiliate_attributions WHERE visitor_id = ? ORDER BY id DESC LIMIT 1',
                    (visitor_id,),
                ).fetchone()
            if attr:
                aff = _affiliate_by_id(attr['affiliate_id'], conn=conn)
                if aff and aff['status'] == _A_APPROVED:
                    meta['affiliate_code'] = aff['affiliate_code']
                    meta['affiliate_id'] = str(aff['id'])
                    if visitor_id:
                        meta['affiliate_visitor_id'] = visitor_id
        finally:
            conn.close()
    except Exception as e:
        logger.warning('[affiliate] checkout_metadata failed: %s', e)
    return meta


# ══════════════════════════════════════════════════════════════════════════════
# Commission engine (Stripe webhook side)
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_affiliate_for_payment(conn, *, user_id, customer_id, subscription_id,
                                   metadata, stripe_mod):
    """Resolve (affiliate_row, attribution_row_or_None) for a paid invoice.

    Priority:
      1. Attribution already linked to the local user.
      2. Attribution linked to the Stripe customer.
      3. affiliate_code carried in checkout/subscription metadata.
      4. Subscription metadata (retrieved) affiliate_code.
    """
    attr = None
    if user_id:
        attr = conn.execute(
            'SELECT * FROM affiliate_attributions WHERE user_id = ? ORDER BY id DESC LIMIT 1',
            (user_id,),
        ).fetchone()
    if not attr and customer_id:
        attr = conn.execute(
            'SELECT * FROM affiliate_attributions WHERE stripe_customer_id = ? ORDER BY id DESC LIMIT 1',
            (customer_id,),
        ).fetchone()
    if attr:
        aff = _affiliate_by_id(attr['affiliate_id'], conn=conn)
        if aff:
            return aff, attr

    code = None
    if metadata:
        code = _obj_get(metadata, 'affiliate_code')
    if not code and subscription_id and stripe_mod is not None:
        try:
            sub = stripe_mod.Subscription.retrieve(subscription_id)
            code = _obj_get(_obj_get(sub, 'metadata') or {}, 'affiliate_code')
        except Exception:
            pass
    if code:
        aff = _affiliate_by_code(code, conn=conn)
        if aff:
            return aff, None
    return None, None


def handle_invoice_paid(invoice, *, event_id=None, stripe_mod=None):
    """Create an idempotent commission for a qualifying paid invoice.

    Called from the existing invoice.paid / invoice.payment_succeeded webhook.
    Never raises (billing must not be affected by affiliate bugs).
    """
    try:
        if _DB_PATH is None:
            return
        invoice_id = _stripe_id(_obj_get(invoice, 'id'))
        amount_paid = int(_obj_get(invoice, 'amount_paid') or 0)
        if amount_paid <= 0:
            return  # $0 / 100%-coupon invoices are not qualifying revenue
        customer_id = _stripe_id(_obj_get(invoice, 'customer'))
        subscription_id = _stripe_id(_obj_get(invoice, 'subscription'))
        currency = (_obj_get(invoice, 'currency') or 'usd').lower()
        billing_reason = _obj_get(invoice, 'billing_reason')
        charge_id = _stripe_id(_obj_get(invoice, 'charge'))
        payment_intent_id = _stripe_id(_obj_get(invoice, 'payment_intent'))
        customer_email = _obj_get(invoice, 'customer_email')
        metadata = _obj_get(invoice, 'metadata') or {}

        conn = _get_db()
        try:
            # Idempotency: one commission per invoice.
            if invoice_id:
                dup = conn.execute(
                    'SELECT 1 FROM affiliate_commissions WHERE stripe_invoice_id = ?',
                    (invoice_id,),
                ).fetchone()
                if dup:
                    logger.info('[affiliate] commission exists for invoice=%s (idempotent skip)', invoice_id)
                    return

            user_id = _user_id_for_customer(customer_id, customer_email, conn=conn)
            aff, attr = _resolve_affiliate_for_payment(
                conn, user_id=user_id, customer_id=customer_id,
                subscription_id=subscription_id, metadata=metadata, stripe_mod=stripe_mod,
            )
            if not aff:
                return  # not a referred payment
            # Suspended/rejected affiliates do not accrue new commissions.
            if aff['status'] != _A_APPROVED:
                _audit(aff['id'], 'commission_skipped_status', f'status={aff["status"]} invoice={invoice_id}')
                return
            # Self-referral guard at conversion time too (unless explicitly allowed).
            if (aff['user_id'] and user_id and aff['user_id'] == user_id
                    and not AFFILIATE_ALLOW_SELF_REFERRAL):
                _audit(aff['id'], 'self_referral_commission_blocked', f'invoice={invoice_id}')
                return

            rate_bps = int(aff['commission_bps'] or AFFILIATE_COMMISSION_BPS)
            # Optional additive campaign bonus — inactive unless enabled AND set.
            bonus_bps = int(aff['bonus_bps'] or 0) if AFFILIATE_BONUS_ENABLED else 0
            effective_bps = rate_bps + bonus_bps
            commission_cents = (amount_paid * effective_bps) // 10000  # integer math only
            campaign = attr['campaign'] if (attr and 'campaign' in attr.keys()) else None

            conn.execute(
                'INSERT INTO affiliate_commissions '
                '(affiliate_id, attribution_id, user_id, stripe_customer_id, '
                ' stripe_subscription_id, stripe_invoice_id, stripe_payment_intent_id, '
                ' stripe_charge_id, source_event_id, amount_gross_cents, currency, '
                ' commission_bps, bonus_bps, commission_cents, status, billing_reason, campaign, eligible_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    aff['id'], (attr['id'] if attr else None), user_id, customer_id,
                    subscription_id, invoice_id, payment_intent_id, charge_id,
                    event_id, amount_paid, currency, rate_bps, bonus_bps, commission_cents,
                    _C_PENDING, billing_reason, campaign, _iso_plus_days(AFFILIATE_HOLD_DAYS),
                ),
            )

            # Lock/convert attribution (first conversion wins; sticky thereafter).
            if attr:
                conn.execute(
                    'UPDATE affiliate_attributions SET status = ?, locked = 1, '
                    'converted_at = COALESCE(converted_at, ?), '
                    'first_subscription_id = COALESCE(first_subscription_id, ?), '
                    'stripe_customer_id = COALESCE(stripe_customer_id, ?), '
                    'user_id = COALESCE(user_id, ?) WHERE id = ?',
                    ('converted', _now_iso(), subscription_id, customer_id, user_id, attr['id']),
                )
                conn.execute(
                    'UPDATE affiliate_clicks SET converted = 1 WHERE visitor_id = '
                    '(SELECT visitor_id FROM affiliate_attributions WHERE id = ?)',
                    (attr['id'],),
                )
            elif user_id or customer_id:
                # Metadata-only attribution: persist a converted attribution row.
                conn.execute(
                    'INSERT INTO affiliate_attributions '
                    '(affiliate_id, user_id, stripe_customer_id, status, converted_at, '
                    ' first_subscription_id, locked) VALUES (?,?,?,?,?,?,1)',
                    (aff['id'], user_id, customer_id, 'converted', _now_iso(), subscription_id),
                )
            conn.commit()
            logger.info(
                '[affiliate] commission created affiliate=%s invoice=%s gross=%s comm=%s %s',
                aff['affiliate_code'], invoice_id, amount_paid, commission_cents, currency,
            )
        finally:
            conn.close()

        _notify_commission_earned(aff, commission_cents, currency)
    except Exception as e:
        logger.warning('[affiliate] handle_invoice_paid failed (non-fatal): %s', e)


def _reverse_commission(row, conn, reason, refunded_cents=None):
    """Reverse or proportionally adjust a commission. Idempotent."""
    if row['status'] in (_C_REVERSED, _C_CANCELLED):
        return
    if refunded_cents is not None and refunded_cents < row['amount_gross_cents']:
        # Partial refund → proportional adjustment on remaining amount.
        remaining = row['amount_gross_cents'] - refunded_cents
        new_comm = (remaining * row['commission_bps']) // 10000
        conn.execute(
            'UPDATE affiliate_commissions SET amount_refunded_cents = ?, commission_cents = ?, '
            'admin_note = ? WHERE id = ?',
            (refunded_cents, new_comm, f'partial refund: {reason}', row['id']),
        )
        _audit(row['affiliate_id'], 'commission_adjusted', f'id={row["id"]} refunded={refunded_cents} {reason}')
    else:
        conn.execute(
            'UPDATE affiliate_commissions SET status = ?, reversed_at = ?, '
            'amount_refunded_cents = ?, admin_note = ? WHERE id = ?',
            (_C_REVERSED, _now_iso(), row['amount_gross_cents'], reason, row['id']),
        )
        _audit(row['affiliate_id'], 'commission_reversed', f'id={row["id"]} {reason}')


def _find_commission_for_charge(conn, *, charge_id, payment_intent_id, invoice_id=None):
    if charge_id:
        row = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_charge_id = ?', (charge_id,)).fetchone()
        if row:
            return row
    if payment_intent_id:
        row = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_payment_intent_id = ?', (payment_intent_id,)).fetchone()
        if row:
            return row
    if invoice_id:
        row = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_invoice_id = ?', (invoice_id,)).fetchone()
        if row:
            return row
    return None


def handle_refund(charge, *, event_id=None, stripe_mod=None):
    """charge.refunded → reverse/adjust the related commission. Never raises."""
    try:
        if _DB_PATH is None:
            return
        charge_id = _stripe_id(_obj_get(charge, 'id'))
        payment_intent_id = _stripe_id(_obj_get(charge, 'payment_intent'))
        invoice_id = _stripe_id(_obj_get(charge, 'invoice'))
        amount_refunded = int(_obj_get(charge, 'amount_refunded') or 0)
        conn = _get_db()
        try:
            row = _find_commission_for_charge(
                conn, charge_id=charge_id, payment_intent_id=payment_intent_id, invoice_id=invoice_id,
            )
            if not row:
                return
            _reverse_commission(row, conn, f'refund event={event_id}', refunded_cents=amount_refunded)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning('[affiliate] handle_refund failed (non-fatal): %s', e)


def handle_dispute(dispute, *, event_id=None, stripe_mod=None):
    """charge.dispute.created / funds_withdrawn → reverse the commission."""
    try:
        if _DB_PATH is None:
            return
        charge_id = _stripe_id(_obj_get(dispute, 'charge'))
        payment_intent_id = _stripe_id(_obj_get(dispute, 'payment_intent'))
        conn = _get_db()
        try:
            row = _find_commission_for_charge(
                conn, charge_id=charge_id, payment_intent_id=payment_intent_id,
            )
            if not row:
                return
            _reverse_commission(row, conn, f'dispute event={event_id}')
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning('[affiliate] handle_dispute failed (non-fatal): %s', e)


def handle_invoice_uncollectible(invoice, *, event_id=None, stripe_mod=None):
    """invoice.voided / invoice.marked_uncollectible → reverse the commission."""
    try:
        if _DB_PATH is None:
            return
        invoice_id = _stripe_id(_obj_get(invoice, 'id'))
        conn = _get_db()
        try:
            row = conn.execute(
                'SELECT * FROM affiliate_commissions WHERE stripe_invoice_id = ?',
                (invoice_id,),
            ).fetchone()
            if not row:
                return
            _reverse_commission(row, conn, f'invoice uncollectible/void event={event_id}')
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning('[affiliate] handle_invoice_uncollectible failed (non-fatal): %s', e)


def _mature_commissions(affiliate_id=None):
    """Flip pending→payable once the holding period has elapsed. Opportunistic
    (called on dashboard/admin loads and before payout requests); no cron needed."""
    try:
        conn = _get_db()
        matured = []  # (affiliate_id, newly_payable_cents)
        try:
            now = _now_iso()
            base = (
                'SELECT affiliate_id, COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions '
                'WHERE status = ? AND eligible_at IS NOT NULL AND eligible_at <= ?'
            )
            if affiliate_id:
                matured = conn.execute(base + ' AND affiliate_id = ? GROUP BY affiliate_id',
                                       (_C_PENDING, now, affiliate_id)).fetchall()
                conn.execute(
                    'UPDATE affiliate_commissions SET status = ? WHERE status = ? '
                    'AND eligible_at IS NOT NULL AND eligible_at <= ? AND affiliate_id = ?',
                    (_C_PAYABLE, _C_PENDING, now, affiliate_id),
                )
            else:
                matured = conn.execute(base + ' GROUP BY affiliate_id', (_C_PENDING, now)).fetchall()
                conn.execute(
                    'UPDATE affiliate_commissions SET status = ? WHERE status = ? '
                    'AND eligible_at IS NOT NULL AND eligible_at <= ?',
                    (_C_PAYABLE, _C_PENDING, now),
                )
            conn.commit()
        finally:
            conn.close()
        # Best-effort "commission became payable" emails (one per affiliate/batch).
        for row in matured:
            if row['s'] and row['s'] > 0:
                try:
                    _notify_commission_payable(row['affiliate_id'], row['s'])
                except Exception:
                    pass
    except Exception as e:
        logger.warning('[affiliate] mature_commissions failed: %s', e)


# ══════════════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════════════

def _affiliate_stats(affiliate_id, conn=None):
    own = conn is None
    conn = conn or _get_db()
    try:
        clicks = conn.execute(
            'SELECT COUNT(*) c FROM affiliate_clicks WHERE affiliate_id = ?', (affiliate_id,)
        ).fetchone()['c']
        signups = conn.execute(
            "SELECT COUNT(*) c FROM affiliate_attributions WHERE affiliate_id = ? AND user_id IS NOT NULL",
            (affiliate_id,),
        ).fetchone()['c']
        customers = conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(user_id, stripe_customer_id)) c "
            "FROM affiliate_attributions WHERE affiliate_id = ? AND status = 'converted'",
            (affiliate_id,),
        ).fetchone()['c']
        rev = conn.execute(
            "SELECT COALESCE(SUM(amount_gross_cents - amount_refunded_cents),0) s "
            "FROM affiliate_commissions WHERE affiliate_id = ? AND status != ?",
            (affiliate_id, _C_REVERSED),
        ).fetchone()['s']
        earned = conn.execute(
            "SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions "
            "WHERE affiliate_id = ? AND status IN (?,?,?)",
            (affiliate_id, _C_PENDING, _C_PAYABLE, _C_PAID),
        ).fetchone()['s']
        pending = conn.execute(
            "SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions "
            "WHERE affiliate_id = ? AND status = ?", (affiliate_id, _C_PENDING),
        ).fetchone()['s']
        payable = conn.execute(
            "SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions "
            "WHERE affiliate_id = ? AND status = ? AND payout_id IS NULL",
            (affiliate_id, _C_PAYABLE),
        ).fetchone()['s']
        paid = conn.execute(
            "SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions "
            "WHERE affiliate_id = ? AND status = ?", (affiliate_id, _C_PAID),
        ).fetchone()['s']
        reversed_ = conn.execute(
            "SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions "
            "WHERE affiliate_id = ? AND status = ?", (affiliate_id, _C_REVERSED),
        ).fetchone()['s']
        conv_rate = (customers / clicks * 100.0) if clicks else 0.0
        return {
            'clicks': clicks, 'signups': signups, 'customers': customers,
            'revenue_cents': rev, 'earned_cents': earned, 'pending_cents': pending,
            'payable_cents': payable, 'paid_cents': paid, 'reversed_cents': reversed_,
            'conversion_rate': round(conv_rate, 1),
        }
    finally:
        if own:
            conn.close()


def _money(cents, currency='usd'):
    cents = int(cents or 0)
    sym = {'usd': '$', 'cad': 'CA$', 'eur': '€', 'gbp': '£'}.get((currency or 'usd').lower(), '$')
    return f'{sym}{cents/100:,.2f}'


# ══════════════════════════════════════════════════════════════════════════════
# Email notifications (reuse auth_system SMTP)
# ══════════════════════════════════════════════════════════════════════════════

def _send_email(to_email, subject, body):
    if not to_email:
        return
    try:
        import auth_system
        auth_system._smtp_send_text_email(
            to_email=to_email, subject=subject, body=body, purpose='affiliate',
        )
    except Exception as e:
        logger.warning('[affiliate] email send failed (non-fatal): %s', e)


def _admin_emails():
    try:
        import auth_system
        return sorted(auth_system.ADMIN_EMAILS)
    except Exception:
        return []


def _notify_application_received(aff):
    _send_email(
        aff['email'],
        'PredictionLab Affiliate — application received',
        f"Hi {aff['name'] or 'there'},\n\nThanks for applying to the PredictionLab "
        f"affiliate program. Your application is under review and we'll email you "
        f"once it's approved.\n\n— PredictionLab",
    )
    for adm in _admin_emails():
        _send_email(adm, 'New affiliate application',
                    f"New affiliate application from {aff['email']} "
                    f"(requested code {aff['affiliate_code']}). Review: {AFFILIATE_SITE_URL}/admin/affiliates")


def _notify_commission_payable(affiliate_id, cents):
    aff = _affiliate_by_id(affiliate_id)
    if not aff:
        return
    _send_email(
        aff['email'], 'Commission now payable — PredictionLab Affiliate',
        f"Hi {aff['name'] or 'there'},\n\n{_money(cents)} of your PredictionLab commission has "
        f"cleared the holding period and is now payable. Once your payable balance reaches "
        f"{_money(AFFILIATE_MIN_PAYOUT_CENTS)}, you can request a payout from your dashboard.\n\n— PredictionLab",
    )


def _notify_all_active_affiliates(subject, body):
    """Broadcast to approved affiliates (used for new campaigns/assets/terms)."""
    try:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT email, name FROM affiliates WHERE status = 'approved' AND email IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            _send_email(r['email'], subject, body)
    except Exception as e:
        logger.warning('[affiliate] broadcast failed: %s', e)


def _notify_status(aff, status):
    subj = {
        _A_APPROVED: 'You are approved — PredictionLab Affiliate',
        _A_REJECTED: 'PredictionLab Affiliate application update',
        _A_SUSPENDED: 'PredictionLab Affiliate account suspended',
    }.get(status)
    if not subj:
        return
    if status == _A_APPROVED:
        body = (f"Hi {aff['name'] or 'there'},\n\nYour PredictionLab affiliate account is approved!\n"
                f"Your code: {aff['affiliate_code']}\nYour link: {referral_link(aff['affiliate_code'])}\n\n"
                f"Sign in and open the Affiliate Dashboard to start promoting.\n\n— PredictionLab")
    elif status == _A_REJECTED:
        body = (f"Hi {aff['name'] or 'there'},\n\nThank you for your interest. We're unable to "
                f"approve your affiliate application at this time.\n\n— PredictionLab")
    else:
        body = (f"Hi {aff['name'] or 'there'},\n\nYour PredictionLab affiliate account has been "
                f"suspended. Please contact support if you believe this is an error.\n\n— PredictionLab")
    _send_email(aff['email'], subj, body)


def _notify_commission_earned(aff, commission_cents, currency):
    _send_email(
        aff['email'], 'You earned a PredictionLab commission',
        f"Hi {aff['name'] or 'there'},\n\nYou just earned {_money(commission_cents, currency)} "
        f"in commission from a referred subscription payment. It will become payable after the "
        f"{AFFILIATE_HOLD_DAYS}-day holding period.\n\nSee details in your Affiliate Dashboard.\n\n— PredictionLab",
    )


def _notify_payout(aff, payout, kind):
    if kind == 'requested':
        _send_email(aff['email'], 'Payout requested — PredictionLab Affiliate',
                    f"Hi {aff['name'] or 'there'},\n\nWe received your payout request for "
                    f"{_money(payout['amount_cents'], payout['currency'])}. We'll process it shortly.\n\n— PredictionLab")
        for adm in _admin_emails():
            _send_email(adm, 'Affiliate payout requested',
                        f"Affiliate {aff['affiliate_code']} requested a payout of "
                        f"{_money(payout['amount_cents'], payout['currency'])}.")
    elif kind == 'paid':
        _send_email(aff['email'], 'Payout processed — PredictionLab Affiliate',
                    f"Hi {aff['name'] or 'there'},\n\nYour payout of "
                    f"{_money(payout['amount_cents'], payout['currency'])} has been marked paid.\n\n— PredictionLab")


# ══════════════════════════════════════════════════════════════════════════════
# Routes — public
# ══════════════════════════════════════════════════════════════════════════════

def _current_affiliate():
    if not current_user.is_authenticated:
        return None
    return _affiliate_by_user(current_user.id)


@affiliate_bp.route('/affiliate')
def affiliate_landing():
    aff = _current_affiliate()
    return render_template(
        'affiliate/landing.html',
        commission_pct=AFFILIATE_COMMISSION_BPS / 100.0,
        min_payout=_money(AFFILIATE_MIN_PAYOUT_CENTS),
        cookie_days=AFFILIATE_COOKIE_DAYS,
        hold_days=AFFILIATE_HOLD_DAYS,
        my_affiliate=aff,
    )


@affiliate_bp.route('/affiliate/terms')
def affiliate_terms():
    return render_template(
        'affiliate/terms.html',
        commission_pct=AFFILIATE_COMMISSION_BPS / 100.0,
        min_payout=_money(AFFILIATE_MIN_PAYOUT_CENTS),
        cookie_days=AFFILIATE_COOKIE_DAYS,
        hold_days=AFFILIATE_HOLD_DAYS,
    )


@affiliate_bp.route('/affiliate/code', methods=['GET', 'POST'])
def affiliate_code_entry():
    """Secondary attribution: let a visitor enter an affiliate's promotion code
    when they did not click a referral link. Server-authoritative."""
    result = None
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        code = _normalize_code(request.form.get('code'))
        ok, reason = apply_code_request(code)
        msgs = {
            'ok': ('success', f'Code {code} applied — your next subscription will support this creator.'),
            'already': ('success', f'Code {code} is already applied.'),
            'invalid': ('error', 'That affiliate code is not valid.'),
            'self': ('error', 'You cannot use your own affiliate code.'),
            'existing_customer': ('error', 'Affiliate codes apply to new subscriptions only.'),
            'already_attributed': ('error', 'You already have a referral applied, so this code was not used.'),
            'error': ('error', 'Could not apply that code. Please try again.'),
        }
        cat, msg = msgs.get(reason, ('error', 'Could not apply that code.'))
        flash(msg, cat)
        if ok:
            return redirect('/plans')
        result = reason
    return render_template('affiliate/code.html', csrf_token=_csrf_token(), result=result)


@affiliate_bp.route('/affiliate/apply', methods=['GET', 'POST'])
def affiliate_apply():
    # Reuse existing auth — must be signed in so the affiliate ties to a user.
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next='/affiliate/apply'))

    existing = _current_affiliate()
    if existing:
        flash('You already have an affiliate account.', 'success')
        return redirect(url_for('affiliate.affiliate_dashboard'))

    error = None
    form = {}
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        try:
            _aud = (request.form.get('audience_size') or '').replace(',', '').strip()
            audience_size = int(_aud) if _aud else None
        except ValueError:
            audience_size = None
        form = {
            'name': (request.form.get('name') or '').strip()[:120],
            'code': _normalize_code(request.form.get('code'))[:32],
            'country': (request.form.get('country') or '').strip()[:80],
            'website_url': (request.form.get('website_url') or '').strip()[:300],
            'promo_method': (request.form.get('promo_method') or '').strip()[:120],
            'application_note': (request.form.get('application_note') or '').strip()[:2000],
            'primary_platform': (request.form.get('primary_platform') or '').strip()[:80],
            'youtube_url': (request.form.get('youtube_url') or '').strip()[:300],
            'tiktok_url': (request.form.get('tiktok_url') or '').strip()[:300],
            'instagram_url': (request.form.get('instagram_url') or '').strip()[:300],
            'x_url': (request.form.get('x_url') or '').strip()[:300],
            'facebook_url': (request.form.get('facebook_url') or '').strip()[:300],
            'discord_url': (request.form.get('discord_url') or '').strip()[:300],
            'newsletter_url': (request.form.get('newsletter_url') or '').strip()[:300],
            'other_url': (request.form.get('other_url') or '').strip()[:300],
            'primary_countries': (request.form.get('primary_countries') or '').strip()[:160],
            'sports': (request.form.get('sports') or '').strip()[:160],
            'audience_description': (request.form.get('audience_description') or '').strip()[:1000],
            'why_promote': (request.form.get('why_promote') or '').strip()[:1000],
        }
        paid_ads = 1 if request.form.get('paid_ads') == 'on' else 0
        existing_sports = 1 if request.form.get('existing_sports_audience') == 'on' else 0
        agree = request.form.get('agree_terms') == 'on'
        if not form['name']:
            error = 'Please enter your name.'
        elif not _valid_code(form['code']):
            error = 'Choose a code with 3–32 letters/numbers (A–Z, 0–9, - or _), not reserved.'
        elif not agree:
            error = 'You must agree to the affiliate terms.'
        else:
            conn = _get_db()
            try:
                if _affiliate_by_code(form['code'], conn=conn):
                    error = 'That affiliate code is taken — please choose another.'
                else:
                    conn.execute(
                        'INSERT INTO affiliates (user_id, affiliate_code, status, name, email, '
                        'country, website_url, promo_method, application_note, commission_bps, agreed_terms_at, '
                        'primary_platform, youtube_url, tiktok_url, instagram_url, x_url, facebook_url, '
                        'discord_url, newsletter_url, other_url, audience_size, primary_countries, sports, '
                        'audience_description, why_promote, paid_ads, existing_sports_audience, lifecycle) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (
                            current_user.id, form['code'], _A_PENDING, form['name'],
                            current_user.email, form['country'], form['website_url'],
                            form['promo_method'], form['application_note'],
                            AFFILIATE_COMMISSION_BPS, _now_iso(),
                            form['primary_platform'], form['youtube_url'], form['tiktok_url'],
                            form['instagram_url'], form['x_url'], form['facebook_url'],
                            form['discord_url'], form['newsletter_url'], form['other_url'],
                            audience_size, form['primary_countries'], form['sports'],
                            form['audience_description'], form['why_promote'], paid_ads,
                            existing_sports, 'applied',
                        ),
                    )
                    aff = _affiliate_by_user(current_user.id, conn=conn)
                    # Record acceptance of the current terms version at apply time.
                    _record_terms_acceptance(aff['id'], current_user.id, conn)
                    conn.commit()
            finally:
                conn.close()
            if not error:
                try:
                    _notify_application_received(aff)
                except Exception:
                    pass
                _audit(aff['id'], 'application', f'code={form["code"]}')
                flash('Application submitted! We will email you once it is reviewed.', 'success')
                return redirect(url_for('affiliate.affiliate_dashboard'))

    return render_template(
        'affiliate/apply.html', error=error, form=form,
        csrf_token=_csrf_token(),
        commission_pct=AFFILIATE_COMMISSION_BPS / 100.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Routes — affiliate (authenticated)
# ══════════════════════════════════════════════════════════════════════════════

def _require_affiliate():
    aff = _current_affiliate()
    if not aff:
        return None
    return aff


@affiliate_bp.route('/affiliate/dashboard')
@login_required
def affiliate_dashboard():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    _mature_commissions(aff['id'])
    stats = _affiliate_stats(aff['id'])
    link = referral_link(aff['affiliate_code'])
    can_payout = (aff['status'] == _A_APPROVED
                  and stats['payable_cents'] >= AFFILIATE_MIN_PAYOUT_CENTS)
    needs_terms = _affiliate_needs_terms(aff['id'])
    needs_onboarding = (aff['status'] == _A_APPROVED and not aff['onboarding_done_at'])
    conn = _get_db()
    try:
        open_payout = conn.execute(
            "SELECT * FROM affiliate_payouts WHERE affiliate_id = ? AND status IN ('requested','processing') "
            "ORDER BY id DESC LIMIT 1", (aff['id'],),
        ).fetchone()
    finally:
        conn.close()
    return render_template(
        'affiliate/dashboard.html',
        aff=aff, stats=stats, link=link, money=_money,
        min_payout_cents=AFFILIATE_MIN_PAYOUT_CENTS,
        min_payout=_money(AFFILIATE_MIN_PAYOUT_CENTS),
        can_payout=can_payout, open_payout=open_payout,
        commission_pct=(aff['commission_bps'] or AFFILIATE_COMMISSION_BPS) / 100.0,
        hold_days=AFFILIATE_HOLD_DAYS,
        csrf_token=_csrf_token(),
        site_url=AFFILIATE_SITE_URL,
        needs_terms=needs_terms, needs_onboarding=needs_onboarding,
    )


@affiliate_bp.route('/affiliate/referrals')
@login_required
def affiliate_referrals():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    conn = _get_db()
    try:
        rows = conn.execute(
            'SELECT a.*, '
            '(SELECT COALESCE(SUM(commission_cents),0) FROM affiliate_commissions c '
            '  WHERE c.attribution_id = a.id AND c.status != ?) AS comm_cents, '
            '(SELECT COALESCE(SUM(amount_gross_cents),0) FROM affiliate_commissions c '
            '  WHERE c.attribution_id = a.id AND c.status != ?) AS rev_cents '
            'FROM affiliate_attributions a WHERE a.affiliate_id = ? AND a.user_id IS NOT NULL '
            'ORDER BY a.id DESC LIMIT 500',
            (_C_REVERSED, _C_REVERSED, aff['id']),
        ).fetchall()
    finally:
        conn.close()
    return render_template('affiliate/referrals.html', aff=aff, rows=rows, money=_money)


@affiliate_bp.route('/affiliate/commissions')
@login_required
def affiliate_commissions():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    _mature_commissions(aff['id'])
    conn = _get_db()
    try:
        rows = conn.execute(
            'SELECT * FROM affiliate_commissions WHERE affiliate_id = ? ORDER BY id DESC LIMIT 1000',
            (aff['id'],),
        ).fetchall()
    finally:
        conn.close()
    return render_template('affiliate/commissions.html', aff=aff, rows=rows, money=_money)


@affiliate_bp.route('/affiliate/payouts')
@login_required
def affiliate_payouts():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    _mature_commissions(aff['id'])
    stats = _affiliate_stats(aff['id'])
    conn = _get_db()
    try:
        rows = conn.execute(
            'SELECT * FROM affiliate_payouts WHERE affiliate_id = ? ORDER BY id DESC LIMIT 200',
            (aff['id'],),
        ).fetchall()
        open_payout = conn.execute(
            "SELECT 1 FROM affiliate_payouts WHERE affiliate_id = ? AND status IN ('requested','processing') LIMIT 1",
            (aff['id'],),
        ).fetchone()
    finally:
        conn.close()
    can_payout = (aff['status'] == _A_APPROVED
                  and stats['payable_cents'] >= AFFILIATE_MIN_PAYOUT_CENTS
                  and not open_payout)
    return render_template(
        'affiliate/payouts.html', aff=aff, rows=rows, stats=stats, money=_money,
        min_payout=_money(AFFILIATE_MIN_PAYOUT_CENTS), can_payout=can_payout,
        min_payout_cents=AFFILIATE_MIN_PAYOUT_CENTS, csrf_token=_csrf_token(),
    )


@affiliate_bp.route('/affiliate/payouts/request', methods=['POST'])
@login_required
def affiliate_request_payout():
    aff = _current_affiliate()
    if not aff:
        abort(403)
    if not _check_csrf():
        abort(400)
    if aff['status'] != _A_APPROVED:
        flash('Your affiliate account is not active.', 'error')
        return redirect(url_for('affiliate.affiliate_payouts'))
    _mature_commissions(aff['id'])
    conn = _get_db()
    try:
        # Block duplicate/open payout requests.
        open_payout = conn.execute(
            "SELECT 1 FROM affiliate_payouts WHERE affiliate_id = ? AND status IN ('requested','processing') LIMIT 1",
            (aff['id'],),
        ).fetchone()
        if open_payout:
            flash('You already have a payout in progress.', 'error')
            return redirect(url_for('affiliate.affiliate_payouts'))
        payable_rows = conn.execute(
            'SELECT id, commission_cents, currency FROM affiliate_commissions '
            'WHERE affiliate_id = ? AND status = ? AND payout_id IS NULL',
            (aff['id'], _C_PAYABLE),
        ).fetchall()
        total = sum(r['commission_cents'] for r in payable_rows)
        if total < AFFILIATE_MIN_PAYOUT_CENTS:
            flash(f'You need at least {_money(AFFILIATE_MIN_PAYOUT_CENTS)} payable to request a payout.', 'error')
            return redirect(url_for('affiliate.affiliate_payouts'))
        currency = payable_rows[0]['currency'] if payable_rows else 'usd'
        try:
            cur = conn.execute(
                'INSERT INTO affiliate_payouts (affiliate_id, amount_cents, currency, status) VALUES (?,?,?,?)',
                (aff['id'], total, currency, 'requested'),
            )
        except sqlite3.IntegrityError:
            # Race: another concurrent request already opened a payout.
            conn.rollback()
            flash('You already have a payout in progress.', 'error')
            return redirect(url_for('affiliate.affiliate_payouts'))
        payout_id = cur.lastrowid
        conn.execute(
            'UPDATE affiliate_commissions SET payout_id = ? WHERE affiliate_id = ? AND status = ? AND payout_id IS NULL',
            (payout_id, aff['id'], _C_PAYABLE),
        )
        conn.commit()
        payout = conn.execute('SELECT * FROM affiliate_payouts WHERE id = ?', (payout_id,)).fetchone()
    finally:
        conn.close()
    _audit(aff['id'], 'payout_requested', f'payout={payout_id} amount={total}')
    try:
        _notify_payout(aff, payout, 'requested')
    except Exception:
        pass
    flash('Payout requested. We will process it shortly.', 'success')
    return redirect(url_for('affiliate.affiliate_payouts'))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — admin (reuse existing admin authorization)
# ══════════════════════════════════════════════════════════════════════════════

@affiliate_bp.route('/admin/affiliates')
@login_required
def admin_affiliates():
    if not _is_admin_user():
        abort(403)
    _mature_commissions()
    status = request.args.get('status', '').strip().lower()
    q = (request.args.get('q') or '').strip()
    conn = _get_db()
    try:
        sql = 'SELECT * FROM affiliates WHERE 1=1'
        params = []
        if status in (_A_PENDING, _A_APPROVED, _A_REJECTED, _A_SUSPENDED):
            sql += ' AND status = ?'
            params.append(status)
        if q:
            sql += ' AND (affiliate_code LIKE ? OR email LIKE ? OR name LIKE ?)'
            like = f'%{q}%'
            params += [like, like, like]
        sql += ' ORDER BY CASE status WHEN "pending" THEN 0 ELSE 1 END, id DESC LIMIT 500'
        affs = conn.execute(sql, params).fetchall()
        rows = []
        for a in affs:
            rows.append({'a': a, 's': _affiliate_stats(a['id'], conn=conn)})
        totals = {
            'affiliates': conn.execute('SELECT COUNT(*) c FROM affiliates').fetchone()['c'],
            'active': conn.execute("SELECT COUNT(*) c FROM affiliates WHERE status='approved'").fetchone()['c'],
            'pending': conn.execute("SELECT COUNT(*) c FROM affiliates WHERE status='pending'").fetchone()['c'],
            'clicks': conn.execute('SELECT COUNT(*) c FROM affiliate_clicks').fetchone()['c'],
            'customers': conn.execute("SELECT COUNT(*) c FROM affiliate_attributions WHERE status='converted'").fetchone()['c'],
            'revenue': conn.execute("SELECT COALESCE(SUM(amount_gross_cents-amount_refunded_cents),0) s FROM affiliate_commissions WHERE status!=?", (_C_REVERSED,)).fetchone()['s'],
            'commissions': conn.execute("SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions WHERE status IN (?,?,?)", (_C_PENDING,_C_PAYABLE,_C_PAID)).fetchone()['s'],
            'paid': conn.execute("SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions WHERE status=?", (_C_PAID,)).fetchone()['s'],
            'payable': conn.execute("SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions WHERE status=? AND payout_id IS NULL", (_C_PAYABLE,)).fetchone()['s'],
            'prospects': conn.execute("SELECT COUNT(*) c FROM affiliate_leads WHERE status NOT IN ('converted','rejected','not_interested')").fetchone()['c'],
        }
        open_payouts = conn.execute(
            "SELECT p.*, a.affiliate_code FROM affiliate_payouts p JOIN affiliates a ON a.id=p.affiliate_id "
            "WHERE p.status IN ('requested','processing') ORDER BY p.id DESC LIMIT 100"
        ).fetchall()
        suspicious = conn.execute(
            "SELECT au.*, a.affiliate_code FROM affiliate_audit au LEFT JOIN affiliates a ON a.id=au.affiliate_id "
            "WHERE au.kind IN ('self_click','self_referral_blocked','self_referral_commission_blocked','existing_customer_skip') "
            "ORDER BY au.id DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        'affiliate/admin_list.html', rows=rows, totals=totals, money=_money,
        status=status, q=q, open_payouts=open_payouts, suspicious=suspicious,
        csrf_token=_csrf_token(),
    )


@affiliate_bp.route('/admin/affiliates/<int:affiliate_id>')
@login_required
def admin_affiliate_detail(affiliate_id):
    if not _is_admin_user():
        abort(403)
    _mature_commissions(affiliate_id)
    conn = _get_db()
    try:
        aff = _affiliate_by_id(affiliate_id, conn=conn)
        if not aff:
            abort(404)
        stats = _affiliate_stats(affiliate_id, conn=conn)
        commissions = conn.execute(
            'SELECT * FROM affiliate_commissions WHERE affiliate_id = ? ORDER BY id DESC LIMIT 500',
            (affiliate_id,),
        ).fetchall()
        payouts = conn.execute(
            'SELECT * FROM affiliate_payouts WHERE affiliate_id = ? ORDER BY id DESC LIMIT 200',
            (affiliate_id,),
        ).fetchall()
        clicks = conn.execute(
            'SELECT * FROM affiliate_clicks WHERE affiliate_id = ? ORDER BY id DESC LIMIT 100',
            (affiliate_id,),
        ).fetchall()
        referrals = conn.execute(
            'SELECT * FROM affiliate_attributions WHERE affiliate_id = ? AND user_id IS NOT NULL ORDER BY id DESC LIMIT 200',
            (affiliate_id,),
        ).fetchall()
        notes = conn.execute(
            'SELECT * FROM affiliate_admin_notes WHERE affiliate_id = ? ORDER BY id DESC LIMIT 100',
            (affiliate_id,),
        ).fetchall()
        term_accepts = conn.execute(
            'SELECT * FROM affiliate_terms_acceptances WHERE affiliate_id = ? ORDER BY id DESC LIMIT 20',
            (affiliate_id,),
        ).fetchall()
        score, tier, reasons, _s = _quality_score(affiliate_id, conn=conn)
        flags = _fraud_flags(affiliate_id, conn=conn)
        ledger = _ledger(affiliate_id, conn=conn)
        campaigns = _campaign_stats(affiliate_id, conn=conn)
    finally:
        conn.close()
    return render_template(
        'affiliate/admin_detail.html', aff=aff, stats=stats, commissions=commissions,
        payouts=payouts, clicks=clicks, referrals=referrals, money=_money,
        link=referral_link(aff['affiliate_code']), csrf_token=_csrf_token(),
        notes=notes, term_accepts=term_accepts, score=score, tier=tier, reasons=reasons,
        flags=flags, ledger=ledger, campaigns=campaigns,
    )


def _set_affiliate_status(affiliate_id, status):
    conn = _get_db()
    try:
        aff = _affiliate_by_id(affiliate_id, conn=conn)
        if not aff:
            return None
        col = {
            _A_APPROVED: 'approved_at', _A_REJECTED: 'rejected_at', _A_SUSPENDED: 'suspended_at',
        }.get(status)
        if col:
            conn.execute(
                f'UPDATE affiliates SET status = ?, {col} = COALESCE({col}, ?) WHERE id = ?',
                (status, _now_iso(), affiliate_id),
            )
        else:
            conn.execute('UPDATE affiliates SET status = ? WHERE id = ?', (status, affiliate_id))
        conn.commit()
        aff = _affiliate_by_id(affiliate_id, conn=conn)
    finally:
        conn.close()
    return aff


@affiliate_bp.route('/admin/affiliates/<int:affiliate_id>/status', methods=['POST'])
@login_required
def admin_affiliate_status(affiliate_id):
    if not _is_admin_user():
        abort(403)
    if not _check_csrf():
        abort(400)
    action = (request.form.get('action') or '').strip().lower()
    mapping = {
        'approve': _A_APPROVED, 'reject': _A_REJECTED,
        'suspend': _A_SUSPENDED, 'reactivate': _A_APPROVED,
    }
    status = mapping.get(action)
    if not status:
        abort(400)
    aff = _set_affiliate_status(affiliate_id, status)
    if aff:
        _audit(affiliate_id, f'status_{action}', f'by_admin={current_user.email}')
        try:
            _notify_status(aff, status)
        except Exception:
            pass
        flash(f'Affiliate {aff["affiliate_code"]} → {status}.', 'success')
    return redirect(request.referrer or url_for('affiliate.admin_affiliates'))


@affiliate_bp.route('/admin/affiliates/commissions/<int:commission_id>/adjust', methods=['POST'])
@login_required
def admin_adjust_commission(commission_id):
    if not _is_admin_user():
        abort(403)
    if not _check_csrf():
        abort(400)
    action = (request.form.get('action') or '').strip().lower()
    note = (request.form.get('note') or '').strip()[:500]
    conn = _get_db()
    try:
        row = conn.execute('SELECT * FROM affiliate_commissions WHERE id = ?', (commission_id,)).fetchone()
        if not row:
            abort(404)
        if action == 'reverse':
            _reverse_commission(row, conn, f'admin reverse: {note} ({current_user.email})')
        elif action == 'cancel':
            conn.execute('UPDATE affiliate_commissions SET status = ?, admin_note = ? WHERE id = ?',
                         (_C_CANCELLED, f'admin cancel: {note}', commission_id))
        elif action == 'approve':
            conn.execute('UPDATE affiliate_commissions SET status = ?, admin_note = ? WHERE id = ?',
                         (_C_PAYABLE, f'admin approve: {note}', commission_id))
        else:
            abort(400)
        conn.commit()
    finally:
        conn.close()
    _audit(row['affiliate_id'], f'commission_{action}', f'id={commission_id} by={current_user.email}')
    flash('Commission updated.', 'success')
    return redirect(request.referrer or url_for('affiliate.admin_affiliates'))


@affiliate_bp.route('/admin/affiliates/payouts/<int:payout_id>/status', methods=['POST'])
@login_required
def admin_payout_status(payout_id):
    if not _is_admin_user():
        abort(403)
    if not _check_csrf():
        abort(400)
    status = (request.form.get('status') or '').strip().lower()
    reference = (request.form.get('reference') or '').strip()[:200]
    method = (request.form.get('method') or '').strip()[:80]
    if status not in ('pending', 'processing', 'paid', 'failed', 'cancelled'):
        abort(400)
    conn = _get_db()
    try:
        payout = conn.execute('SELECT * FROM affiliate_payouts WHERE id = ?', (payout_id,)).fetchone()
        if not payout:
            abort(404)
        processed_at = _now_iso() if status in ('paid', 'failed', 'cancelled') else None
        conn.execute(
            'UPDATE affiliate_payouts SET status = ?, reference = ?, method = ?, '
            'processed_at = COALESCE(?, processed_at) WHERE id = ?',
            (status, reference or payout['reference'], method or payout['method'], processed_at, payout_id),
        )
        if status == 'paid':
            conn.execute(
                'UPDATE affiliate_commissions SET status = ?, paid_at = ? WHERE payout_id = ? AND status = ?',
                (_C_PAID, _now_iso(), payout_id, _C_PAYABLE),
            )
        elif status in ('failed', 'cancelled'):
            # Release the commissions back to payable so they can be re-requested.
            conn.execute(
                'UPDATE affiliate_commissions SET payout_id = NULL WHERE payout_id = ? AND status = ?',
                (payout_id, _C_PAYABLE),
            )
        conn.commit()
        aff = _affiliate_by_id(payout['affiliate_id'], conn=conn)
        payout = conn.execute('SELECT * FROM affiliate_payouts WHERE id = ?', (payout_id,)).fetchone()
    finally:
        conn.close()
    _audit(payout['affiliate_id'], f'payout_{status}', f'payout={payout_id} by={current_user.email}')
    if status == 'paid' and aff:
        try:
            _notify_payout(aff, payout, 'paid')
        except Exception:
            pass
    flash(f'Payout #{payout_id} → {status}.', 'success')
    return redirect(request.referrer or url_for('affiliate.admin_affiliates'))


# ══════════════════════════════════════════════════════════════════════════════
# Reporting / analytics (date-filtered), quality scoring, ledger
# ══════════════════════════════════════════════════════════════════════════════

_DATE_RANGES = [('today', 'Today'), ('7d', '7 days'), ('30d', '30 days'),
                ('90d', '90 days'), ('ytd', 'This year'), ('all', 'All time'),
                ('custom', 'Custom')]


def _date_bounds(range_key=None, custom_from=None, custom_to=None):
    """Return (start_iso, end_iso) for a range key. None means unbounded."""
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    rk = (range_key or 'all').lower()
    start = end = None
    if rk == 'today':
        start = now.replace(hour=0, minute=0, second=0)
    elif rk == '7d':
        start = now - timedelta(days=7)
    elif rk == '30d':
        start = now - timedelta(days=30)
    elif rk == '90d':
        start = now - timedelta(days=90)
    elif rk in ('ytd', 'year'):
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0)
    elif rk == 'custom':
        try:
            start = datetime.fromisoformat(custom_from) if custom_from else None
        except Exception:
            start = None
        try:
            end = datetime.fromisoformat(custom_to) if custom_to else None
        except Exception:
            end = None
    return (start.isoformat(sep=' ') if start else None,
            end.isoformat(sep=' ') if end else None)


def _range_clause(col, start, end):
    frag, params = '', []
    if start:
        frag += f' AND {col} >= ?'
        params.append(start)
    if end:
        frag += f' AND {col} <= ?'
        params.append(end)
    return frag, params


def _affiliate_stats_ranged(affiliate_id, start=None, end=None, conn=None):
    """Extended, optionally date-filtered performance for one affiliate."""
    own = conn is None
    conn = conn or _get_db()
    try:
        cf, cp = _range_clause('created_at', start, end)
        clicks_row = conn.execute(
            'SELECT COUNT(*) c, COUNT(DISTINCT visitor_id) u FROM affiliate_clicks '
            'WHERE affiliate_id = ?' + cf, [affiliate_id] + cp,
        ).fetchone()
        clicks, unique_visitors = clicks_row['c'], clicks_row['u']
        af, ap = _range_clause('attributed_at', start, end)
        signups = conn.execute(
            'SELECT COUNT(*) c FROM affiliate_attributions WHERE affiliate_id = ? '
            'AND user_id IS NOT NULL' + af, [affiliate_id] + ap,
        ).fetchone()['c']
        customers = conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(user_id, stripe_customer_id)) c "
            "FROM affiliate_attributions WHERE affiliate_id = ? AND status = 'converted'" + af,
            [affiliate_id] + ap,
        ).fetchone()['c']

        def comm_sum(where, params):
            return conn.execute(
                'SELECT COALESCE(SUM(commission_cents),0) s, COUNT(*) n FROM affiliate_commissions '
                'WHERE affiliate_id = ?' + cf + where, [affiliate_id] + cp + params,
            ).fetchone()
        revenue = conn.execute(
            'SELECT COALESCE(SUM(amount_gross_cents - amount_refunded_cents),0) s '
            'FROM affiliate_commissions WHERE affiliate_id = ? AND status != ?' + cf,
            [affiliate_id, _C_REVERSED] + cp,
        ).fetchone()['s']
        earned = comm_sum(' AND status IN (?,?,?)', [_C_PENDING, _C_PAYABLE, _C_PAID])
        pending = comm_sum(' AND status = ?', [_C_PENDING])['s']
        payable = conn.execute(
            'SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions '
            'WHERE affiliate_id = ? AND status = ? AND payout_id IS NULL' + cf,
            [affiliate_id, _C_PAYABLE] + cp,
        ).fetchone()['s']
        paid = comm_sum(' AND status = ?', [_C_PAID])['s']
        reversed_ = comm_sum(' AND status = ?', [_C_REVERSED])
        refunds = conn.execute(
            'SELECT COALESCE(SUM(amount_refunded_cents),0) s FROM affiliate_commissions '
            'WHERE affiliate_id = ?' + cf, [affiliate_id] + cp,
        ).fetchone()['s']
        recurring = conn.execute(
            "SELECT COALESCE(SUM(amount_gross_cents - amount_refunded_cents),0) s "
            "FROM affiliate_commissions WHERE affiliate_id = ? AND billing_reason = 'subscription_cycle' "
            "AND status != ?" + cf, [affiliate_id, _C_REVERSED] + cp,
        ).fetchone()['s']
        comm_count = earned['n']
        # Retention proxy: paying invoices per paying customer (>=2 => renewals).
        retention = round(comm_count / customers, 2) if customers else 0.0
        conv = round(customers / clicks * 100.0, 1) if clicks else 0.0
        return {
            'clicks': clicks, 'unique_visitors': unique_visitors, 'signups': signups,
            'customers': customers, 'conversion_rate': conv, 'revenue_cents': revenue,
            'earned_cents': earned['s'], 'pending_cents': pending, 'payable_cents': payable,
            'paid_cents': paid, 'reversed_cents': reversed_['s'], 'refunds_cents': refunds,
            'recurring_revenue_cents': recurring, 'commission_count': comm_count,
            'retention': retention,
        }
    finally:
        if own:
            conn.close()


def _quality_score(affiliate_id, conn=None):
    """Deterministic 0–100 partner-quality score. Click volume is intentionally
    NOT a positive factor — paying customers / conversion / revenue dominate, so a
    partner with many clicks and no customers cannot outrank a converting one."""
    own = conn is None
    conn = conn or _get_db()
    try:
        s = _affiliate_stats_ranged(affiliate_id, conn=conn)
        aff = _affiliate_by_id(affiliate_id, conn=conn)
        W = _QUALITY_WEIGHTS
        score = 0.0
        score += min(s['customers'] / 20.0, 1.0) * W['paying_customers']
        score += min(s['conversion_rate'] / 8.0, 1.0) * W['conversion_rate']
        score += min(s['revenue_cents'] / 100000.0, 1.0) * W['revenue']
        renewals = max(s['retention'] - 1.0, 0.0)
        score += min(renewals / 3.0, 1.0) * W['retention']
        gross = s['revenue_cents'] + s['refunds_cents']
        refund_rate = (s['refunds_cents'] / gross) if gross else 0.0
        score -= min(refund_rate, 1.0) * W['refund_penalty']
        rel = 0.0
        sports = (aff['sports'] or '') if aff else ''
        plat = ((aff['primary_platform'] or aff['promo_method'] or '') if aff else '').lower()
        if sports.strip():
            rel += 0.6
        if any(k in plat for k in ('youtube', 'tiktok', 'instagram', 'newsletter', 'blog', 'website', 'discord', 'x ', 'twitter')):
            rel += 0.4
        score += min(rel, 1.0) * W['relevance']
        score = int(max(0, min(100, round(score))))
        tier = 'high' if score >= 70 else ('medium' if score >= 40 else 'low')
        reasons = []
        if s['customers']:
            reasons.append(f"{s['customers']} paying customer(s)")
        if s['conversion_rate'] >= 2:
            reasons.append(f"{s['conversion_rate']:.1f}% conversion")
        if s['revenue_cents'] >= 5000:
            reasons.append(f"{_money(s['revenue_cents'])} net revenue")
        if renewals >= 1:
            reasons.append('recurring renewals')
        if refund_rate >= 0.2:
            reasons.append('elevated refund rate')
        if not reasons:
            reasons.append('limited performance data yet')
        return score, tier, reasons, s
    finally:
        if own:
            conn.close()


def _recommendation(aff, conn=None):
    """Priority recommendation for an applicant/partner. Uses performance when it
    exists, else profile signals (sports relevance, audience, geography, platform)."""
    own = conn is None
    conn = conn or _get_db()
    try:
        pscore, _tier, preasons, _s = _quality_score(aff['id'], conn=conn)
        prof = 0
        why = []
        if (aff['sports'] or '').strip():
            prof += 25
            why.append('sports-relevant audience')
        if aff['existing_sports_audience']:
            prof += 20
            why.append('existing sports-analysis audience')
        aud = aff['audience_size'] or 0
        if aud >= 100000:
            prof += 25
            why.append('large audience')
        elif aud >= 10000:
            prof += 15
            why.append('solid audience')
        elif aud >= 1000:
            prof += 8
        countries = (aff['primary_countries'] or '').lower()
        if any(c in countries for c in ('us', 'usa', 'united states', 'canada', 'north america', 'uk', 'united kingdom')):
            prof += 15
            why.append('North American / UK traffic')
        plat = (aff['primary_platform'] or aff['promo_method'] or '').lower()
        if any(k in plat for k in ('youtube', 'tiktok', 'instagram', 'newsletter', 'website', 'blog')):
            prof += 10
            why.append(f'{plat} platform')
        combined = max(pscore, min(prof, 100))
        priority = 'high' if combined >= 70 else ('medium' if combined >= 40 else 'low')
        reasons = preasons if pscore >= 40 else (why or preasons)
        return priority, combined, reasons
    finally:
        if own:
            conn.close()


def _fraud_flags(affiliate_id, conn=None):
    """Return a list of 'review recommended' quality flags (never auto-ban)."""
    own = conn is None
    conn = conn or _get_db()
    try:
        s = _affiliate_stats_ranged(affiliate_id, conn=conn)
        flags = []
        if s['clicks'] >= 200 and s['customers'] == 0:
            flags.append(f"{s['clicks']} clicks with zero paying customers")
        if s['clicks'] >= 20 and s['conversion_rate'] >= 60:
            flags.append(f"Unusually high conversion rate ({s['conversion_rate']:.0f}%)")
        selfc = conn.execute(
            "SELECT COUNT(*) c FROM affiliate_audit WHERE affiliate_id = ? AND kind LIKE 'self_%'",
            (affiliate_id,),
        ).fetchone()['c']
        if selfc >= 3:
            flags.append(f'Repeated self-referral attempts ({selfc})')
        gross = s['revenue_cents'] + s['refunds_cents']
        if gross > 0 and (s['refunds_cents'] / gross) >= 0.3:
            flags.append(f"High refund/reversal rate ({s['refunds_cents']/gross*100:.0f}%)")
        rapid = conn.execute(
            'SELECT visitor_id, COUNT(*) c FROM affiliate_clicks WHERE affiliate_id = ? '
            'GROUP BY visitor_id ORDER BY c DESC LIMIT 1', (affiliate_id,),
        ).fetchone()
        if rapid and rapid['c'] >= 25:
            flags.append(f'Many repeated clicks from a single visitor ({rapid["c"]})')
        return flags
    finally:
        if own:
            conn.close()


def _ledger(affiliate_id, conn=None):
    """Auditable balance rollup for one affiliate (integer cents)."""
    own = conn is None
    conn = conn or _get_db()
    try:
        def s(status):
            return conn.execute(
                'SELECT COALESCE(SUM(commission_cents),0) s FROM affiliate_commissions '
                'WHERE affiliate_id = ? AND status = ?', (affiliate_id, status),
            ).fetchone()['s']
        pending, payable, paid = s(_C_PENDING), s(_C_PAYABLE), s(_C_PAID)
        reversed_, cancelled = s(_C_REVERSED), s(_C_CANCELLED)
        earned = pending + payable + paid
        entries = conn.execute(
            'SELECT * FROM affiliate_commissions WHERE affiliate_id = ? ORDER BY id DESC LIMIT 1000',
            (affiliate_id,),
        ).fetchall()
        return {
            'earned': earned, 'pending': pending, 'payable': payable, 'paid': paid,
            'reversed': reversed_, 'cancelled': cancelled,
            'current': pending + payable,  # owed but not yet paid out
            'entries': entries,
        }
    finally:
        if own:
            conn.close()


def _campaign_stats(affiliate_id, conn=None):
    """Per-campaign performance for one affiliate (clicks + conversions)."""
    own = conn is None
    conn = conn or _get_db()
    try:
        rows = {}
        for r in conn.execute(
            'SELECT COALESCE(campaign, "(none)") k, COUNT(*) c FROM affiliate_clicks '
            'WHERE affiliate_id = ? GROUP BY k', (affiliate_id,),
        ):
            rows.setdefault(r['k'], {'campaign': r['k'], 'clicks': 0, 'customers': 0,
                                     'revenue_cents': 0, 'commission_cents': 0})['clicks'] = r['c']
        for r in conn.execute(
            "SELECT COALESCE(campaign, '(none)') k, COUNT(*) cust, "
            "COALESCE(SUM(amount_gross_cents - amount_refunded_cents),0) rev, "
            "COALESCE(SUM(commission_cents),0) comm FROM affiliate_commissions "
            "WHERE affiliate_id = ? AND status != ? GROUP BY k",
            (affiliate_id, _C_REVERSED),
        ):
            d = rows.setdefault(r['k'], {'campaign': r['k'], 'clicks': 0, 'customers': 0,
                                         'revenue_cents': 0, 'commission_cents': 0})
            d['customers'] = r['cust']
            d['revenue_cents'] = r['rev']
            d['commission_cents'] = r['comm']
        out = list(rows.values())
        for d in out:
            d['conversion_rate'] = round(d['customers'] / d['clicks'] * 100.0, 1) if d['clicks'] else 0.0
        out.sort(key=lambda d: (d['commission_cents'], d['customers']), reverse=True)
        return out
    finally:
        if own:
            conn.close()


# ─── Terms versioning / acceptance ────────────────────────────────────────────

def _affiliate_needs_terms(affiliate_id, conn=None):
    own = conn is None
    conn = conn or _get_db()
    try:
        row = conn.execute(
            'SELECT requires_reaccept FROM affiliate_terms_versions WHERE version = ?',
            (AFFILIATE_TERMS_VERSION,),
        ).fetchone()
        if not row or not row['requires_reaccept']:
            return False
        acc = conn.execute(
            'SELECT 1 FROM affiliate_terms_acceptances WHERE affiliate_id = ? AND version = ? LIMIT 1',
            (affiliate_id, AFFILIATE_TERMS_VERSION),
        ).fetchone()
        return acc is None
    finally:
        if own:
            conn.close()


def _record_terms_acceptance(affiliate_id, user_id, conn):
    try:
        ip = request.remote_addr
    except Exception:
        ip = None
    conn.execute(
        'INSERT INTO affiliate_terms_acceptances (affiliate_id, version, ip_hash, user_id) '
        'VALUES (?,?,?,?)',
        (affiliate_id, AFFILIATE_TERMS_VERSION, _hash_ip(ip), user_id),
    )


def _seed_defaults():
    """Seed the current terms version + a starter Academy (idempotent)."""
    try:
        conn = _get_db()
        try:
            conn.execute(
                'INSERT OR IGNORE INTO affiliate_terms_versions (version, body, requires_reaccept) '
                'VALUES (?,?,0)',
                (AFFILIATE_TERMS_VERSION, 'See /affiliate/terms for the full program terms.'),
            )
            modules = [
                ('getting-started', 'Getting Started', 'Welcome to the PredictionLab affiliate program. Grab your referral link and code from your dashboard and share them with your audience.'),
                ('understanding-predictionlab', 'Understanding PredictionLab', 'PredictionLab publishes data-driven AI sports predictions with transparent, tracked results across the major leagues.'),
                ('choosing-your-audience', 'Choosing Your Audience', 'Sports bettors and sports-analysis fans convert best. Focus on audiences interested in data-driven picks.'),
                ('how-referral-links-work', 'How Referral Links Work', 'Your link /?ref=CODE sets a first-party, server-side attribution that lasts 30 days and survives checkout.'),
                ('how-promo-codes-work', 'How Promo Codes Work', 'Your code works when someone signs up without clicking your link. It never overrides an existing referral.'),
                ('effective-content', 'How to Create Effective Content', 'Show real results, explain the edge, and be transparent. Honest content converts and retains.'),
                ('promote-responsibly', 'Promoting Sports Predictions Responsibly', 'Never guarantee winnings or income. Follow local advertising and disclosure rules. Bet responsibly.'),
                ('understanding-your-dashboard', 'Understanding Your Dashboard', 'Track clicks, signups, paying customers, conversion, and your commission balances.'),
                ('understanding-commissions', 'Understanding Commissions', 'You earn 10% of qualifying paid subscription revenue, including recurring renewals, after a 30-day hold.'),
                ('how-payouts-work', 'How Payouts Work', 'Once your payable balance reaches $100 you can request a payout, which an admin processes.'),
            ]
            for i, (slug, title, body) in enumerate(modules):
                conn.execute(
                    'INSERT OR IGNORE INTO affiliate_academy_modules (slug, title, body, sort_order, active) '
                    'VALUES (?,?,?,?,1)', (slug, title, body, i),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning('[affiliate] seed defaults failed: %s', e)


def _to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def build_referral_link(code, destination='/', campaign=None):
    """Compose a referral URL with optional destination + campaign tag."""
    dest = destination or '/'
    if not dest.startswith('/'):
        dest = '/' + dest
    sep = '&' if '?' in dest else '?'
    url = f'{AFFILIATE_SITE_URL}{dest}{sep}ref={code}'
    camp = _clean_campaign(campaign)
    if camp:
        url += f'&utm_campaign={camp}'
    return url


# Fields an affiliate may edit on their own profile (never commission/status/balance).
_EDITABLE_PROFILE_FIELDS = (
    'name', 'website_url', 'primary_platform', 'youtube_url', 'tiktok_url',
    'instagram_url', 'x_url', 'facebook_url', 'discord_url', 'newsletter_url',
    'other_url', 'country', 'primary_countries', 'sports', 'audience_description',
    'promo_method', 'why_promote',
)


# ══════════════════════════════════════════════════════════════════════════════
# Affiliate-facing routes (authenticated; own data only)
# ══════════════════════════════════════════════════════════════════════════════

@affiliate_bp.route('/affiliate/profile', methods=['GET', 'POST'])
@login_required
def affiliate_profile():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    error = None
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        vals = {}
        for f in _EDITABLE_PROFILE_FIELDS:
            vals[f] = (request.form.get(f) or '').strip()[:2000]
        try:
            aud = request.form.get('audience_size', '').strip().replace(',', '')
            audience_size = int(aud) if aud else None
        except ValueError:
            audience_size = None
        sets = ', '.join(f'{f} = ?' for f in _EDITABLE_PROFILE_FIELDS)
        params = [vals[f] for f in _EDITABLE_PROFILE_FIELDS]
        conn = _get_db()
        try:
            conn.execute(
                f'UPDATE affiliates SET {sets}, audience_size = ? WHERE id = ?',
                params + [audience_size, aff['id']],
            )
            conn.commit()
        finally:
            conn.close()
        flash('Profile updated.', 'success')
        return redirect(url_for('affiliate.affiliate_profile'))
    return render_template('affiliate/profile.html', aff=aff, csrf_token=_csrf_token(),
                           link=referral_link(aff['affiliate_code']))


@affiliate_bp.route('/affiliate/onboarding')
@login_required
def affiliate_onboarding():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    return render_template('affiliate/onboarding.html', aff=aff, csrf_token=_csrf_token(),
                           link=referral_link(aff['affiliate_code']),
                           commission_pct=(aff['commission_bps'] or AFFILIATE_COMMISSION_BPS) / 100.0,
                           min_payout=_money(AFFILIATE_MIN_PAYOUT_CENTS),
                           hold_days=AFFILIATE_HOLD_DAYS, cookie_days=AFFILIATE_COOKIE_DAYS)


@affiliate_bp.route('/affiliate/onboarding/complete', methods=['POST'])
@login_required
def affiliate_onboarding_complete():
    aff = _current_affiliate()
    if not aff:
        abort(403)
    if not _check_csrf():
        abort(400)
    conn = _get_db()
    try:
        conn.execute('UPDATE affiliates SET onboarding_done_at = COALESCE(onboarding_done_at, ?) WHERE id = ?',
                     (_now_iso(), aff['id']))
        conn.commit()
    finally:
        conn.close()
    flash('Onboarding complete — welcome aboard!', 'success')
    return redirect(url_for('affiliate.affiliate_dashboard'))


@affiliate_bp.route('/affiliate/academy')
@login_required
def affiliate_academy():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    conn = _get_db()
    try:
        modules = conn.execute(
            'SELECT * FROM affiliate_academy_modules WHERE active = 1 ORDER BY sort_order, id'
        ).fetchall()
        prog = {r['module_id']: r for r in conn.execute(
            'SELECT * FROM affiliate_academy_progress WHERE affiliate_id = ?', (aff['id'],))}
    finally:
        conn.close()
    done = sum(1 for m in modules if prog.get(m['id']) and prog[m['id']]['completed_at'])
    return render_template('affiliate/academy.html', aff=aff, modules=modules, prog=prog,
                           done=done, total=len(modules))


@affiliate_bp.route('/affiliate/academy/<slug>', methods=['GET', 'POST'])
@login_required
def affiliate_academy_module(slug):
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    conn = _get_db()
    try:
        mod = conn.execute(
            'SELECT * FROM affiliate_academy_modules WHERE slug = ? AND active = 1', (slug,)
        ).fetchone()
        if not mod:
            abort(404)
        # Mark started (idempotent).
        conn.execute(
            'INSERT OR IGNORE INTO affiliate_academy_progress (affiliate_id, module_id, started_at) '
            'VALUES (?,?,?)', (aff['id'], mod['id'], _now_iso()))
        if request.method == 'POST':
            if not _check_csrf():
                abort(400)
            conn.execute(
                'UPDATE affiliate_academy_progress SET completed_at = COALESCE(completed_at, ?) '
                'WHERE affiliate_id = ? AND module_id = ?', (_now_iso(), aff['id'], mod['id']))
            conn.commit()
            flash('Module marked complete.', 'success')
            return redirect(url_for('affiliate.affiliate_academy'))
        conn.commit()
    finally:
        conn.close()
    return render_template('affiliate/academy_module.html', aff=aff, mod=mod, csrf_token=_csrf_token())


@affiliate_bp.route('/affiliate/assets')
@login_required
def affiliate_assets():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    conn = _get_db()
    try:
        assets = conn.execute(
            'SELECT * FROM affiliate_assets WHERE active = 1 ORDER BY id DESC'
        ).fetchall()
    finally:
        conn.close()
    return render_template('affiliate/assets.html', aff=aff, assets=assets,
                           link=referral_link(aff['affiliate_code']),
                           catalog=_PROMO_CATALOG, site_url=AFFILIATE_SITE_URL)


@affiliate_bp.route('/affiliate/links', methods=['GET', 'POST'])
@login_required
def affiliate_links():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    built = None
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        dest = (request.form.get('destination') or '/').strip()
        valid_dests = {d for d, _ in _LINK_DESTINATIONS}
        if dest not in valid_dests:
            dest = '/'
        campaign = _clean_campaign(request.form.get('campaign'))
        built = build_referral_link(aff['affiliate_code'], dest, campaign)
        if campaign:
            # Persist the campaign so it appears in campaign reporting (idempotent).
            conn = _get_db()
            try:
                conn.execute(
                    'INSERT OR IGNORE INTO affiliate_campaigns (affiliate_id, name, slug, destination_path) '
                    'VALUES (?,?,?,?)',
                    (aff['id'], request.form.get('campaign', campaign)[:80], campaign, dest))
                conn.commit()
            finally:
                conn.close()
    return render_template('affiliate/links.html', aff=aff, csrf_token=_csrf_token(),
                           destinations=_LINK_DESTINATIONS, built=built,
                           base_link=referral_link(aff['affiliate_code']))


@affiliate_bp.route('/affiliate/campaigns')
@login_required
def affiliate_campaigns_view():
    aff = _current_affiliate()
    if not aff:
        return redirect(url_for('affiliate.affiliate_apply'))
    stats = _campaign_stats(aff['id'])
    conn = _get_db()
    try:
        campaigns = conn.execute(
            'SELECT * FROM affiliate_campaigns WHERE affiliate_id = ? ORDER BY id DESC', (aff['id'],)
        ).fetchall()
        briefs = conn.execute(
            'SELECT * FROM affiliate_campaign_briefs WHERE active = 1 ORDER BY id DESC'
        ).fetchall()
    finally:
        conn.close()
    return render_template('affiliate/campaigns.html', aff=aff, stats=stats, campaigns=campaigns,
                           briefs=briefs, money=_money)


@affiliate_bp.route('/affiliate/accept-terms', methods=['POST'])
@login_required
def affiliate_accept_terms():
    aff = _current_affiliate()
    if not aff:
        abort(403)
    if not _check_csrf():
        abort(400)
    conn = _get_db()
    try:
        _record_terms_acceptance(aff['id'], current_user.id, conn)
        conn.commit()
    finally:
        conn.close()
    flash('Thanks — updated affiliate terms accepted.', 'success')
    return redirect(url_for('affiliate.affiliate_dashboard'))


# ══════════════════════════════════════════════════════════════════════════════
# Admin routes (existing admin authorization; internal data)
# ══════════════════════════════════════════════════════════════════════════════

def _admin_only():
    if not current_user.is_authenticated:
        abort(403)
    if not _is_admin_user():
        abort(403)


@affiliate_bp.route('/admin/affiliates/leads', methods=['GET', 'POST'])
@login_required
def admin_leads():
    _admin_only()
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        f = request.form
        try:
            aud = int((f.get('audience_size') or '').replace(',', '')) if f.get('audience_size') else None
        except ValueError:
            aud = None
        conn = _get_db()
        try:
            conn.execute(
                'INSERT INTO affiliate_leads (name, contact_name, email, website_url, social_url, '
                'platform, country, sports, audience_size, audience_description, notes, status, source, '
                'assigned_admin, next_follow_up) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    (f.get('name') or '').strip()[:160], (f.get('contact_name') or '').strip()[:160],
                    (f.get('email') or '').strip()[:200], (f.get('website_url') or '').strip()[:300],
                    (f.get('social_url') or '').strip()[:300], (f.get('platform') or '').strip()[:80],
                    (f.get('country') or '').strip()[:80], (f.get('sports') or '').strip()[:160],
                    aud, (f.get('audience_description') or '').strip()[:1000],
                    (f.get('notes') or '').strip()[:2000],
                    (f.get('status') or 'prospect').strip().lower(),
                    (f.get('source') or '').strip()[:120], current_user.email,
                    (f.get('next_follow_up') or '').strip()[:40],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        flash('Lead added.', 'success')
        return redirect(url_for('affiliate.admin_leads'))

    status = (request.args.get('status') or '').strip().lower()
    q = (request.args.get('q') or '').strip()
    conn = _get_db()
    try:
        sql = 'SELECT * FROM affiliate_leads WHERE 1=1'
        params = []
        if status in _LEAD_STATUSES:
            sql += ' AND status = ?'
            params.append(status)
        if q:
            sql += ' AND (name LIKE ? OR email LIKE ? OR website_url LIKE ? OR social_url LIKE ?)'
            like = f'%{q}%'
            params += [like, like, like, like]
        sql += ' ORDER BY id DESC LIMIT 500'
        leads = conn.execute(sql, params).fetchall()
        counts = {r['status']: r['c'] for r in conn.execute(
            'SELECT status, COUNT(*) c FROM affiliate_leads GROUP BY status')}
    finally:
        conn.close()
    return render_template('affiliate/admin_leads.html', leads=leads, counts=counts,
                           statuses=_LEAD_STATUSES, status=status, q=q, csrf_token=_csrf_token())


@affiliate_bp.route('/admin/affiliates/leads/<int:lead_id>', methods=['GET', 'POST'])
@login_required
def admin_lead_detail(lead_id):
    _admin_only()
    conn = _get_db()
    try:
        lead = conn.execute('SELECT * FROM affiliate_leads WHERE id = ?', (lead_id,)).fetchone()
        if not lead:
            abort(404)
        if request.method == 'POST':
            if not _check_csrf():
                abort(400)
            f = request.form
            conn.execute(
                'UPDATE affiliate_leads SET status = ?, assigned_admin = ?, last_contacted = ?, '
                'next_follow_up = ?, notes = ?, updated_at = ? WHERE id = ?',
                (
                    (f.get('status') or lead['status']).strip().lower(),
                    (f.get('assigned_admin') or lead['assigned_admin'] or '').strip()[:160],
                    (f.get('last_contacted') or lead['last_contacted'] or '').strip()[:40],
                    (f.get('next_follow_up') or lead['next_follow_up'] or '').strip()[:40],
                    (f.get('notes') or lead['notes'] or '').strip()[:2000],
                    _now_iso(), lead_id,
                ),
            )
            conn.commit()
            flash('Lead updated.', 'success')
            return redirect(url_for('affiliate.admin_lead_detail', lead_id=lead_id))
        notes = conn.execute(
            'SELECT * FROM affiliate_admin_notes WHERE lead_id = ? ORDER BY id DESC', (lead_id,)
        ).fetchall()
    finally:
        conn.close()
    return render_template('affiliate/admin_lead_detail.html', lead=lead, notes=notes,
                           statuses=_LEAD_STATUSES, csrf_token=_csrf_token())


@affiliate_bp.route('/admin/affiliates/leads/<int:lead_id>/convert', methods=['POST'])
@login_required
def admin_lead_convert(lead_id):
    _admin_only()
    if not _check_csrf():
        abort(400)
    conn = _get_db()
    try:
        lead = conn.execute('SELECT * FROM affiliate_leads WHERE id = ?', (lead_id,)).fetchone()
        if not lead:
            abort(404)
        linked = None
        if lead['email']:
            arow = conn.execute('SELECT id FROM affiliates WHERE lower(email) = ?',
                                (lead['email'].strip().lower(),)).fetchone()
            linked = arow['id'] if arow else None
        conn.execute(
            'UPDATE affiliate_leads SET status = ?, converted_affiliate_id = ?, updated_at = ? WHERE id = ?',
            ('converted' if linked else 'applied', linked, _now_iso(), lead_id))
        conn.commit()
    finally:
        conn.close()
    if linked:
        flash('Lead linked to existing affiliate and marked converted.', 'success')
    else:
        flash('Marked as applied. Ask them to apply at /affiliate/apply with their PredictionLab account; '
              'it will auto-link by email when approved.', 'success')
    return redirect(url_for('affiliate.admin_lead_detail', lead_id=lead_id))


@affiliate_bp.route('/admin/affiliates/recommendations')
@login_required
def admin_recommendations():
    _admin_only()
    conn = _get_db()
    try:
        affs = conn.execute('SELECT * FROM affiliates ORDER BY id DESC LIMIT 500').fetchall()
        rows = []
        for a in affs:
            priority, score, reasons = _recommendation(a, conn=conn)
            rows.append({'a': a, 'priority': priority, 'score': score, 'reasons': reasons})
        order = {'high': 0, 'medium': 1, 'low': 2}
        rows.sort(key=lambda r: (order[r['priority']], -r['score']))
    finally:
        conn.close()
    return render_template('affiliate/admin_recommendations.html', rows=rows)


@affiliate_bp.route('/admin/affiliates/reports')
@login_required
def admin_reports():
    _admin_only()
    _mature_commissions()
    rng = request.args.get('range', '30d')
    start, end = _date_bounds(rng, request.args.get('from'), request.args.get('to'))
    rank_by = request.args.get('rank', 'revenue_cents')
    valid_rank = {'revenue_cents', 'customers', 'conversion_rate', 'clicks',
                  'earned_cents', 'recurring_revenue_cents', 'retention'}
    if rank_by not in valid_rank:
        rank_by = 'revenue_cents'
    conn = _get_db()
    try:
        affs = conn.execute("SELECT * FROM affiliates ORDER BY id DESC LIMIT 500").fetchall()
        rows = []
        totals = {'clicks': 0, 'signups': 0, 'customers': 0, 'revenue_cents': 0,
                  'earned_cents': 0, 'payable_cents': 0, 'paid_cents': 0}
        for a in affs:
            s = _affiliate_stats_ranged(a['id'], start, end, conn=conn)
            score, tier, _r, _s = _quality_score(a['id'], conn=conn)
            rows.append({'a': a, 's': s, 'score': score, 'tier': tier})
            for k in totals:
                totals[k] += s.get(k, 0)
        rows.sort(key=lambda r: r['s'].get(rank_by, 0), reverse=True)
        prospects = conn.execute("SELECT COUNT(*) c FROM affiliate_leads WHERE status NOT IN ('converted','rejected','not_interested')").fetchone()['c']
        pending = conn.execute("SELECT COUNT(*) c FROM affiliates WHERE status='pending'").fetchone()['c']
        active = conn.execute("SELECT COUNT(*) c FROM affiliates WHERE status='approved'").fetchone()['c']
    finally:
        conn.close()
    return render_template('affiliate/admin_reports.html', rows=rows, totals=totals, money=_money,
                           ranges=_DATE_RANGES, rng=rng, rank_by=rank_by,
                           date_from=request.args.get('from', ''), date_to=request.args.get('to', ''),
                           prospects=prospects, pending=pending, active=active,
                           total_affiliates=len(affs))


@affiliate_bp.route('/admin/affiliates/export.csv')
@login_required
def admin_export_csv():
    _admin_only()
    _mature_commissions()
    conn = _get_db()
    try:
        affs = conn.execute('SELECT * FROM affiliates ORDER BY id DESC').fetchall()
        data = []
        for a in affs:
            s = _affiliate_stats_ranged(a['id'], conn=conn)
            data.append([
                a['name'], a['affiliate_code'], a['status'], s['clicks'], s['signups'],
                s['customers'], f"{s['revenue_cents']/100:.2f}", f"{s['earned_cents']/100:.2f}",
                f"{s['payable_cents']/100:.2f}", f"{s['paid_cents']/100:.2f}",
            ])
    finally:
        conn.close()
    csv_text = _to_csv(
        ['name', 'code', 'status', 'clicks', 'signups', 'paid_customers', 'revenue',
         'commissions_earned', 'payable', 'paid'], data)
    return Response(csv_text, mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=affiliates.csv'})


@affiliate_bp.route('/admin/affiliates/assets', methods=['GET', 'POST'])
@login_required
def admin_assets():
    _admin_only()
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        f = request.form
        conn = _get_db()
        try:
            conn.execute(
                'INSERT INTO affiliate_assets (title, description, asset_type, body, image_url, '
                'destination_url, sports, active) VALUES (?,?,?,?,?,?,?,1)',
                (
                    (f.get('title') or '').strip()[:200], (f.get('description') or '').strip()[:1000],
                    (f.get('asset_type') or 'text').strip()[:40], (f.get('body') or '').strip()[:4000],
                    (f.get('image_url') or '').strip()[:400], (f.get('destination_url') or '').strip()[:400],
                    (f.get('sports') or '').strip()[:120],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        try:
            _notify_all_active_affiliates(
                'New PredictionLab promotional asset',
                f"A new marketing asset is available: {(f.get('title') or '').strip()}. "
                f"Find it under Marketing Assets in your dashboard.\n\n— PredictionLab")
        except Exception:
            pass
        flash('Asset created.', 'success')
        return redirect(url_for('affiliate.admin_assets'))
    conn = _get_db()
    try:
        assets = conn.execute('SELECT * FROM affiliate_assets ORDER BY id DESC').fetchall()
    finally:
        conn.close()
    return render_template('affiliate/admin_assets.html', assets=assets, csrf_token=_csrf_token())


@affiliate_bp.route('/admin/affiliates/assets/<int:asset_id>/toggle', methods=['POST'])
@login_required
def admin_asset_toggle(asset_id):
    _admin_only()
    if not _check_csrf():
        abort(400)
    conn = _get_db()
    try:
        conn.execute('UPDATE affiliate_assets SET active = 1 - active WHERE id = ?', (asset_id,))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('affiliate.admin_assets'))


@affiliate_bp.route('/admin/affiliates/briefs', methods=['GET', 'POST'])
@login_required
def admin_briefs():
    _admin_only()
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        f = request.form
        conn = _get_db()
        try:
            conn.execute(
                'INSERT INTO affiliate_campaign_briefs (title, description, key_points, cta, '
                'destination_url, promo_code, start_date, end_date, active) VALUES (?,?,?,?,?,?,?,?,1)',
                (
                    (f.get('title') or '').strip()[:200], (f.get('description') or '').strip()[:2000],
                    (f.get('key_points') or '').strip()[:2000], (f.get('cta') or '').strip()[:120],
                    (f.get('destination_url') or '').strip()[:400],
                    _normalize_code(f.get('promo_code'))[:32] or None,
                    (f.get('start_date') or '').strip()[:40], (f.get('end_date') or '').strip()[:40],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        try:
            _notify_all_active_affiliates(
                'New PredictionLab campaign available',
                f"A new affiliate campaign is live: {(f.get('title') or '').strip()}. "
                f"See it in your dashboard under Campaigns.\n\n— PredictionLab")
        except Exception:
            pass
        flash('Campaign brief created.', 'success')
        return redirect(url_for('affiliate.admin_briefs'))
    conn = _get_db()
    try:
        briefs = conn.execute('SELECT * FROM affiliate_campaign_briefs ORDER BY id DESC').fetchall()
    finally:
        conn.close()
    return render_template('affiliate/admin_briefs.html', briefs=briefs, csrf_token=_csrf_token())


@affiliate_bp.route('/admin/affiliates/briefs/<int:brief_id>/toggle', methods=['POST'])
@login_required
def admin_brief_toggle(brief_id):
    _admin_only()
    if not _check_csrf():
        abort(400)
    conn = _get_db()
    try:
        conn.execute('UPDATE affiliate_campaign_briefs SET active = 1 - active WHERE id = ?', (brief_id,))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('affiliate.admin_briefs'))


@affiliate_bp.route('/admin/affiliates/academy', methods=['GET', 'POST'])
@login_required
def admin_academy():
    _admin_only()
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        f = request.form
        slug = _CAMPAIGN_SLUG_RE.sub('-', (f.get('title') or '').strip().lower()).strip('-')[:60]
        conn = _get_db()
        try:
            conn.execute(
                'INSERT OR IGNORE INTO affiliate_academy_modules (slug, title, body, video_url, sort_order, active) '
                'VALUES (?,?,?,?,?,1)',
                (slug or uuid.uuid4().hex[:8], (f.get('title') or '').strip()[:200],
                 (f.get('body') or '').strip()[:8000], (f.get('video_url') or '').strip()[:400],
                 int(f.get('sort_order') or 100)))
            conn.commit()
        finally:
            conn.close()
        flash('Academy module saved.', 'success')
        return redirect(url_for('affiliate.admin_academy'))
    conn = _get_db()
    try:
        modules = conn.execute('SELECT * FROM affiliate_academy_modules ORDER BY sort_order, id').fetchall()
    finally:
        conn.close()
    return render_template('affiliate/admin_academy.html', modules=modules, csrf_token=_csrf_token())


@affiliate_bp.route('/admin/affiliates/academy/<int:module_id>/toggle', methods=['POST'])
@login_required
def admin_academy_toggle(module_id):
    _admin_only()
    if not _check_csrf():
        abort(400)
    conn = _get_db()
    try:
        conn.execute('UPDATE affiliate_academy_modules SET active = 1 - active WHERE id = ?', (module_id,))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for('affiliate.admin_academy'))


@affiliate_bp.route('/admin/affiliates/<int:affiliate_id>/note', methods=['POST'])
@login_required
def admin_affiliate_note(affiliate_id):
    _admin_only()
    if not _check_csrf():
        abort(400)
    body = (request.form.get('body') or '').strip()[:2000]
    if body:
        conn = _get_db()
        try:
            conn.execute(
                'INSERT INTO affiliate_admin_notes (affiliate_id, author, body) VALUES (?,?,?)',
                (affiliate_id, current_user.email, body))
            conn.commit()
        finally:
            conn.close()
        flash('Internal note added.', 'success')
    return redirect(request.referrer or url_for('affiliate.admin_affiliate_detail', affiliate_id=affiliate_id))


@affiliate_bp.route('/admin/affiliates/leads/<int:lead_id>/note', methods=['POST'])
@login_required
def admin_lead_note(lead_id):
    _admin_only()
    if not _check_csrf():
        abort(400)
    body = (request.form.get('body') or '').strip()[:2000]
    if body:
        conn = _get_db()
        try:
            conn.execute(
                'INSERT INTO affiliate_admin_notes (lead_id, author, body) VALUES (?,?,?)',
                (lead_id, current_user.email, body))
            conn.commit()
        finally:
            conn.close()
        flash('Note added.', 'success')
    return redirect(url_for('affiliate.admin_lead_detail', lead_id=lead_id))


@affiliate_bp.route('/admin/affiliates/terms', methods=['GET', 'POST'])
@login_required
def admin_terms():
    _admin_only()
    if request.method == 'POST':
        if not _check_csrf():
            abort(400)
        # Flag the CURRENT terms version to require re-acceptance by all affiliates.
        conn = _get_db()
        try:
            conn.execute(
                'INSERT OR IGNORE INTO affiliate_terms_versions (version, body, requires_reaccept) VALUES (?,?,1)',
                (AFFILIATE_TERMS_VERSION, 'Updated terms'))
            conn.execute('UPDATE affiliate_terms_versions SET requires_reaccept = 1 WHERE version = ?',
                         (AFFILIATE_TERMS_VERSION,))
            conn.commit()
        finally:
            conn.close()
        try:
            _notify_all_active_affiliates(
                'Updated PredictionLab affiliate terms — action needed',
                f"Our affiliate terms were updated (version {AFFILIATE_TERMS_VERSION}). "
                f"Please sign in and accept the updated terms from your dashboard.\n\n— PredictionLab")
        except Exception:
            pass
        flash(f'Affiliates will be required to re-accept terms {AFFILIATE_TERMS_VERSION}.', 'success')
        return redirect(url_for('affiliate.admin_terms'))
    conn = _get_db()
    try:
        versions = conn.execute('SELECT * FROM affiliate_terms_versions ORDER BY id DESC').fetchall()
        accept_counts = {r['version']: r['c'] for r in conn.execute(
            'SELECT version, COUNT(*) c FROM affiliate_terms_acceptances GROUP BY version')}
    finally:
        conn.close()
    return render_template('affiliate/admin_terms.html', versions=versions, accept_counts=accept_counts,
                           current=AFFILIATE_TERMS_VERSION, csrf_token=_csrf_token())


# ══════════════════════════════════════════════════════════════════════════════
# App wiring
# ══════════════════════════════════════════════════════════════════════════════

def init_affiliate(app, db_path):
    """Initialize the affiliate subsystem. Call once after init_auth()."""
    global _DB_PATH
    _DB_PATH = db_path
    _ensure_affiliate_tables()
    _seed_defaults()

    @app.before_request
    def _affiliate_before():
        # Only touch the DB when a ref is present or a logged-in user needs linking.
        if request.args.get('ref'):
            _capture_ref()
        _associate_user()

    @app.after_request
    def _affiliate_after(response):
        return _set_visitor_cookie(response)

    app.register_blueprint(affiliate_bp)
    logger.info('[affiliate] initialized (rate=%s bps, hold=%sd, window=%sd, min_payout=%s)',
                AFFILIATE_COMMISSION_BPS, AFFILIATE_HOLD_DAYS, AFFILIATE_COOKIE_DAYS,
                _money(AFFILIATE_MIN_PAYOUT_CENTS))
