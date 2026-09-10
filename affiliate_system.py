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

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template,
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

def _record_click(conn, affiliate_id, visitor_id, suspicious=False):
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
            '(affiliate_id, visitor_id, landing_path, referrer, ip_hash, user_agent, is_suspicious) '
            'VALUES (?,?,?,?,?,?,?)',
            (
                affiliate_id, visitor_id,
                (request.path or '/')[:300],
                (request.referrer or '')[:300] or None,
                _hash_ip(request.remote_addr),
                (request.headers.get('User-Agent') or '')[:300] or None,
                1 if suspicious else 0,
            ),
        )
    except Exception as e:
        logger.warning('[affiliate] click record failed: %s', e)


def _upsert_visitor_attribution(conn, affiliate_id, visitor_id):
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
            '(affiliate_id, visitor_id, status, attributed_at) VALUES (?,?,?,?)',
            (affiliate_id, visitor_id, _A_PENDING, now),
        )
        return
    if row['locked'] or row['status'] == 'converted':
        return  # converted attribution is sticky
    if row['affiliate_id'] != affiliate_id or not row['user_id']:
        conn.execute(
            'UPDATE affiliate_attributions SET affiliate_id = ?, attributed_at = ? WHERE id = ?',
            (affiliate_id, now, row['id']),
        )


def _capture_ref():
    """before_request: capture ?ref=CODE, validate, record click + attribution."""
    try:
        code = request.args.get('ref')
        if not code:
            return
        aff = _affiliate_by_code(code)
        if not aff or aff['status'] != _A_APPROVED:
            return  # only approved affiliates earn attribution
        # Ensure a first-party visitor id (opaque; attribution stored server-side).
        visitor_id = request.cookies.get(_VISITOR_COOKIE)
        if not visitor_id:
            visitor_id = uuid.uuid4().hex
            g._aff_set_visitor = visitor_id  # after_request sets the cookie
        # Self-referral guard: affiliate clicking their own link while logged in.
        suspicious = False
        try:
            if (current_user.is_authenticated and aff['user_id']
                    and current_user.id == aff['user_id']):
                suspicious = True
        except Exception:
            pass
        conn = _get_db()
        try:
            _record_click(conn, aff['id'], visitor_id, suspicious=suspicious)
            if not suspicious:
                _upsert_visitor_attribution(conn, aff['id'], visitor_id)
            conn.commit()
        finally:
            conn.close()
        if suspicious:
            _audit(aff['id'], 'self_click', f'user_id={getattr(current_user, "id", None)}')
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
            # Self-referral: don't let an affiliate attribute their own account.
            if aff['user_id'] and aff['user_id'] == current_user.id:
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
        # Self-referral guard.
        if user_id and aff['user_id'] and aff['user_id'] == user_id:
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
            # Self-referral guard at conversion time too.
            if aff['user_id'] and user_id and aff['user_id'] == user_id:
                _audit(aff['id'], 'self_referral_commission_blocked', f'invoice={invoice_id}')
                return

            rate_bps = int(aff['commission_bps'] or AFFILIATE_COMMISSION_BPS)
            commission_cents = (amount_paid * rate_bps) // 10000  # integer math only

            conn.execute(
                'INSERT INTO affiliate_commissions '
                '(affiliate_id, attribution_id, user_id, stripe_customer_id, '
                ' stripe_subscription_id, stripe_invoice_id, stripe_payment_intent_id, '
                ' stripe_charge_id, source_event_id, amount_gross_cents, currency, '
                ' commission_bps, commission_cents, status, billing_reason, eligible_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    aff['id'], (attr['id'] if attr else None), user_id, customer_id,
                    subscription_id, invoice_id, payment_intent_id, charge_id,
                    event_id, amount_paid, currency, rate_bps, commission_cents,
                    _C_PENDING, billing_reason, _iso_plus_days(AFFILIATE_HOLD_DAYS),
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
        try:
            now = _now_iso()
            if affiliate_id:
                conn.execute(
                    'UPDATE affiliate_commissions SET status = ? WHERE status = ? '
                    'AND eligible_at IS NOT NULL AND eligible_at <= ? AND affiliate_id = ?',
                    (_C_PAYABLE, _C_PENDING, now, affiliate_id),
                )
            else:
                conn.execute(
                    'UPDATE affiliate_commissions SET status = ? WHERE status = ? '
                    'AND eligible_at IS NOT NULL AND eligible_at <= ?',
                    (_C_PAYABLE, _C_PENDING, now),
                )
            conn.commit()
        finally:
            conn.close()
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
        form = {
            'name': (request.form.get('name') or '').strip()[:120],
            'code': _normalize_code(request.form.get('code'))[:32],
            'country': (request.form.get('country') or '').strip()[:80],
            'website_url': (request.form.get('website_url') or '').strip()[:300],
            'promo_method': (request.form.get('promo_method') or '').strip()[:120],
            'application_note': (request.form.get('application_note') or '').strip()[:2000],
        }
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
                        'country, website_url, promo_method, application_note, commission_bps, agreed_terms_at) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (
                            current_user.id, form['code'], _A_PENDING, form['name'],
                            current_user.email, form['country'], form['website_url'],
                            form['promo_method'], form['application_note'],
                            AFFILIATE_COMMISSION_BPS, _now_iso(),
                        ),
                    )
                    conn.commit()
                    aff = _affiliate_by_user(current_user.id, conn=conn)
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
    finally:
        conn.close()
    return render_template(
        'affiliate/admin_detail.html', aff=aff, stats=stats, commissions=commissions,
        payouts=payouts, clicks=clicks, referrals=referrals, money=_money,
        link=referral_link(aff['affiliate_code']), csrf_token=_csrf_token(),
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
# App wiring
# ══════════════════════════════════════════════════════════════════════════════

def init_affiliate(app, db_path):
    """Initialize the affiliate subsystem. Call once after init_auth()."""
    global _DB_PATH
    _DB_PATH = db_path
    _ensure_affiliate_tables()

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
