"""
Auth & Premium System for predictionlab.io
=========================================
Google OAuth + Email/Password login + Stripe subscriptions.

Usage:
    from auth_system import init_auth, login_required, premium_required, is_premium_user
    init_auth(app)  # call once after Flask app is created

Env vars needed:
    GOOGLE_CLIENT_ID        - from console.cloud.google.com (OAuth 2.0 *Web application*)
    GOOGLE_CLIENT_SECRET    - from console.cloud.google.com
    GOOGLE_REDIRECT_URI     - optional; if unset, Flask builds it from the request host.
                              Must match Google Console *exactly* (scheme, host, path, no stray slash).

    Google Cloud Console (APIs & Services → Credentials → your OAuth client):
    - *Authorized JavaScript origins*: your public site origin, e.g. ``https://predictionlab.io``
      (add ``https://www.predictionlab.io`` only if you serve that host).
    - *Authorized redirect URIs*: must match **byte-for-byte** what the app sends
      (``Error 400: redirect_uri_mismatch`` means this list is wrong or missing an entry).
      After deploy, click “Continue with Google” once and check Render logs for
      ``[auth] Google OAuth redirect_uri=...`` — paste that **exact** URL into Google Console.
    - Typical values: ``https://predictionlab.io/auth/google/callback`` and/or
      ``https://<your-service>.onrender.com/auth/google/callback`` if users hit Render URL.
    - If you set ``GOOGLE_REDIRECT_URI`` on Render, it must equal the same string you add
      in Google Console (or remove the env var and rely on auto-generated URLs).
    STRIPE_SECRET_KEY       - from dashboard.stripe.com/apikeys
    STRIPE_WEBHOOK_SECRET   - from Stripe webhook settings
    STRIPE_PRICE_MONTHLY    - Stripe Price ID for monthly plan (falls back to Payment Link)
    STRIPE_PRICE_YEARLY     - Stripe Price ID for yearly plan (falls back to Payment Link)
    STRIPE_PRICE_WEEKLY     - Stripe Price ID for weekly plan (falls back to Payment Link)
    STRIPE_PORTAL_RETURN_URL - optional; Customer Portal return URL
                              (default https://predictionlab.io/; local uses request host)
    SECRET_KEY              - Flask session secret (auto-generated if missing)

    SMTP (welcome + claim + contact — same Gmail/App-Password pattern on Render):
    SMTP_PASSWORD or CONTACT_SMTP_PASSWORD  - required to send mail
    SMTP_HOST               - default smtp.gmail.com
    SMTP_PORT               - default 587
    SMTP_USER               - default SUPPORT_EMAIL
    SUPPORT_EMAIL           - From address fallback (default underdogsbetemail@gmail.com)
"""

import os
import html
import sqlite3
import logging
import secrets
import hashlib
from datetime import datetime, timedelta
from functools import wraps

# Load .env.local for local dev + Render's /etc/secrets/ path
try:
    from dotenv import load_dotenv
    _auth_dir = os.path.dirname(os.path.abspath(__file__))
    for _p in [
        os.path.join(_auth_dir, '.env.local'),
        os.path.join(_auth_dir, '.env'),
        '/etc/secrets/.env.local',
        '/etc/secrets/.env',
    ]:
        if os.path.exists(_p):
            load_dotenv(_p, override=True)
            break
except ImportError:
    pass

from flask import (
    Blueprint, request, redirect, url_for, session,
    render_template, render_template_string, jsonify, flash, g
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    current_user, login_required as _flask_login_required
)
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

# ─── Blueprint ────────────────────────────────────────────────────────────────

auth_bp = Blueprint('auth', __name__)

# ─── Config ───────────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
# Show Google button only when both are set (matches _setup_google_oauth gate).
GOOGLE_OAUTH_READY = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '').strip()
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '').strip()
STRIPE_PRICE_MONTHLY = os.environ.get('STRIPE_PRICE_MONTHLY', '').strip()
STRIPE_PRICE_YEARLY = os.environ.get('STRIPE_PRICE_YEARLY', '').strip()
STRIPE_PRICE_WEEKLY = os.environ.get('STRIPE_PRICE_WEEKLY', '').strip()
STRIPE_WEEKLY_URL = 'https://buy.stripe.com/14A6oI4Ra66ReWLczTao802'
STRIPE_MONTHLY_URL = 'https://buy.stripe.com/bJeeVe0AU1QB7uj7fzao801'
STRIPE_YEARLY_URL = 'https://buy.stripe.com/8x228s83mfHr8yneI1ao803'
# Customer Portal return URL — production default; override with env if needed.
STRIPE_PORTAL_RETURN_URL = os.environ.get('STRIPE_PORTAL_RETURN_URL', '').strip()
DEFAULT_STRIPE_PORTAL_RETURN_URL = 'https://predictionlab.io/'
VALID_CHECKOUT_PLANS = frozenset({'monthly', 'yearly', 'weekly'})
SET_PASSWORD_TOKEN_HOURS = 48

# Admin emails get automatic premium — no payment needed
ADMIN_EMAILS = {
    e.strip().lower() for e in
    os.environ.get('ADMIN_EMAILS', 'underdogsbetemail@gmail.com,nmesghali@gmail.com').split(',')
    if e.strip()
}

_DB_PATH = None  # set by init_auth()
_login_manager = LoginManager()
_login_manager.remember_cookie_duration = timedelta(days=90)


# ─── User Model ───────────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, id, email, name=None, google_id=None,
                 is_premium=False, premium_expires=None, stripe_customer_id=None):
        self.id = id
        self.email = email
        self.name = name or email.split('@')[0]
        self.google_id = google_id
        self.is_premium = bool(is_premium)
        self.premium_expires = premium_expires
        self.stripe_customer_id = stripe_customer_id

    @property
    def is_admin(self):
        """Check if user is an admin."""
        return self.email and self.email.lower() in ADMIN_EMAILS

    @property
    def premium_active(self):
        """Check if premium is currently active (not expired). Admins always have premium."""
        if self.is_admin:
            return True
        if not self.is_premium:
            return False
        if not self.premium_expires:
            return True  # lifetime or no expiry set
        try:
            exp = datetime.fromisoformat(self.premium_expires)
            return datetime.now() < exp
        except Exception:
            return True


def _get_db():
    """Get database connection."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_users_table():
    """Create users table if it doesn't exist."""
    conn = _get_db()
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
            stripe_subscription_id TEXT,
            session_token TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    # Add columns if missing (existing DBs)
    for col_sql in (
        'ALTER TABLE users ADD COLUMN session_token TEXT',
        'ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT',
        'ALTER TABLE users ADD COLUMN welcome_email_sent_at TEXT',
        'ALTER TABLE users ADD COLUMN welcome_email_subscription_id TEXT',
    ):
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    # One-time set-password / claim-account tokens (post-checkout for guests)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    # Stripe webhook idempotency (event.id)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stripe_webhook_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT,
            processed_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    # Premium welcome email: one send per Stripe subscription_id (retries + renewals safe)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS premium_welcome_emails (
            subscription_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            event_id TEXT,
            plan TEXT,
            sent_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    conn.close()


def _load_user_by_id(user_id):
    """Load user from database by ID."""
    try:
        conn = _get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if row:
            return User(
                id=row['id'], email=row['email'], name=row['name'],
                google_id=row['google_id'], is_premium=row['is_premium'],
                premium_expires=row['premium_expires'],
                stripe_customer_id=row['stripe_customer_id']
            )
    except Exception as e:
        logger.error(f"Error loading user {user_id}: {e}")
    return None


def _load_user_by_email(email):
    """Load user from database by email (case-insensitive)."""
    if not email:
        return None
    try:
        email_norm = email.strip().lower()
        conn = _get_db()
        row = conn.execute(
            'SELECT * FROM users WHERE lower(email) = ?', (email_norm,)
        ).fetchone()
        conn.close()
        if row:
            return User(
                id=row['id'], email=row['email'], name=row['name'],
                google_id=row['google_id'], is_premium=row['is_premium'],
                premium_expires=row['premium_expires'],
                stripe_customer_id=row['stripe_customer_id']
            )
    except Exception:
        pass
    return None


def _set_session_token(user_id):
    """Store a session marker in the cookie (does not force-logout other devices)."""
    token = secrets.token_hex(32)
    try:
        conn = _get_db()
        conn.execute('UPDATE users SET session_token = ? WHERE id = ?', (token, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to set session token for user {user_id}: {e}")
    session['_session_token'] = token


# ─── Init ─────────────────────────────────────────────────────────────────────────────

def _running_on_render():
    """Best-effort detection of the Render (production HTTPS) environment."""
    return bool(
        os.environ.get('RENDER')
        or os.environ.get('RENDER_EXTERNAL_URL')
        or os.path.isdir('/data')
    )


def _resolve_secret_key():
    """Return a STABLE Flask session secret.

    A changing secret invalidates every session + remember-me cookie on restart.
    Order: SECRET_KEY env → persisted disk key → process-local random fallback.
    """
    env_key = (os.environ.get('SECRET_KEY') or '').strip()
    if env_key:
        return env_key

    data_dir = '/data' if os.path.isdir('/data') else os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(data_dir, '.flask_secret_key')
    try:
        if os.path.exists(key_path):
            with open(key_path, 'r') as fh:
                disk_key = fh.read().strip()
            if disk_key:
                logger.warning(
                    "[auth] SECRET_KEY env var not set — using persisted key at %s. "
                    "Set SECRET_KEY in Render to silence this.", key_path)
                return disk_key
        new_key = secrets.token_hex(32)
        tmp_path = key_path + '.tmp'
        with open(tmp_path, 'w') as fh:
            fh.write(new_key)
        os.replace(tmp_path, key_path)
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
        logger.warning(
            "[auth] SECRET_KEY env var not set — generated and persisted a new key at %s. "
            "Set SECRET_KEY in Render to control this.", key_path)
        return new_key
    except Exception as e:
        logger.error(
            "[auth] Could not persist a session key (%s) — using a process-local "
            "random key; users may be logged out on restart.", e)
        return secrets.token_hex(32)


def init_auth(app, db_path=None):
    """Initialize auth system on the Flask app."""
    global _DB_PATH
    _DB_PATH = db_path or app.config.get('DATABASE', 'sports_predictions_original.db')

    # Secret key for sessions — MUST stay stable across restarts.
    app.secret_key = _resolve_secret_key()

    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(days=90))
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
    app.config.setdefault('REMEMBER_COOKIE_HTTPONLY', True)
    app.config.setdefault('REMEMBER_COOKIE_DURATION', timedelta(days=90))
    if _running_on_render():
        app.config.setdefault('SESSION_COOKIE_SAMESITE', 'None')
        app.config.setdefault('SESSION_COOKIE_SECURE', True)
        app.config.setdefault('REMEMBER_COOKIE_SAMESITE', 'None')
        app.config.setdefault('REMEMBER_COOKIE_SECURE', True)
    else:
        app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
        app.config.setdefault('SESSION_COOKIE_SECURE', False)
        app.config.setdefault('REMEMBER_COOKIE_SAMESITE', 'Lax')
        app.config.setdefault('REMEMBER_COOKIE_SECURE', False)

    @app.before_request
    def _make_session_permanent():
        session.permanent = True

    # Flask-Login setup
    _login_manager.init_app(app)
    _login_manager.login_view = 'auth.login_page'

    @_login_manager.user_loader
    def load_user(user_id):
        return _load_user_by_id(user_id)

    # Create users table
    _ensure_users_table()

    # Auto-seed admin password from env var (set ADMIN_PASSWORD in Render)
    _admin_pw = os.environ.get('ADMIN_PASSWORD', '').strip()
    if _admin_pw:
        try:
            _conn = _get_db()
            _pw_hash = generate_password_hash(_admin_pw)
            for _adm_email in ADMIN_EMAILS:
                _existing = _conn.execute('SELECT id FROM users WHERE email = ?', (_adm_email,)).fetchone()
                if _existing:
                    _conn.execute('UPDATE users SET password_hash = ?, is_premium = 1 WHERE email = ?', (_pw_hash, _adm_email))
                else:
                    _conn.execute('INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, 1)', (_adm_email, _adm_email.split('@')[0], _pw_hash))
            _conn.commit()
            _conn.close()
        except Exception as _e:
            logger.warning(f"Auto-seed admin failed: {_e}")

    # Register auth blueprint
    app.register_blueprint(auth_bp)

    # Google OAuth setup (if credentials available)
    if GOOGLE_OAUTH_READY:
        _setup_google_oauth(app)

    # Inject is_premium into all templates
    @app.context_processor
    def inject_auth():
        is_prem = current_user.premium_active if current_user.is_authenticated else False
        # Local dev: full premium preview on localhost so picks pages are testable without Stripe login
        if not is_prem:
            try:
                host = (request.host or '').split(':')[0].lower()
                if host in ('127.0.0.1', 'localhost'):
                    is_prem = True
            except Exception:
                pass
        return {
            'user': current_user,
            'is_premium': is_prem,
            'is_logged_in': current_user.is_authenticated,
        }

    logger.info("[auth] Auth system initialized")


# ─── Google OAuth ─────────────────────────────────────────────────────────────

_oauth = None


def _google_redirect_uri():
    """
    Callback URL sent to Google on authorize and token exchange.
    Must match an entry under *Authorized redirect URIs* in Google Cloud Console exactly.
    """
    explicit = (os.environ.get('GOOGLE_REDIRECT_URI') or '').strip()
    if explicit:
        uri = explicit
    else:
        uri = url_for('auth.google_callback', _external=True)
    uri = (uri or '').strip()

    # Behind Render / other proxies, scheme can be http while the public URL is https;
    # Google only allows https for production hosts → mismatch if we send http://...
    host = (request.host or '').split(':')[0].lower()
    local = host in {'localhost', '127.0.0.1'} or host.endswith('.local')
    if uri.startswith('http://') and not local:
        xfp = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
        if xfp == 'https':
            uri = 'https://' + uri[len('http://') :]
        elif host.endswith('onrender.com') or host.endswith('predictionlab.io') or host.endswith('underdogs.bet'):
            uri = 'https://' + uri[len('http://') :]
    return uri


def _setup_google_oauth(app):
    global _oauth
    from authlib.integrations.flask_client import OAuth
    _oauth = OAuth(app)
    _oauth.register(
        name='google',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )


@auth_bp.route('/auth/google')
def google_login():
    if not _oauth:
        return "Google login not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.", 500
    redirect_uri = _google_redirect_uri()
    logger.info(f"[auth] Google OAuth redirect_uri={redirect_uri}")
    return _oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/auth/google/callback')
def google_callback():
    if not _oauth:
        return "Google login not configured.", 500
    try:
        token = _oauth.google.authorize_access_token()
        userinfo = token.get('userinfo')
        if not userinfo:
            userinfo = _oauth.google.get('https://openidconnect.googleapis.com/v1/userinfo').json()

        email = userinfo.get('email')
        if not email:
            return redirect(url_for('auth.login_page', error='no_email'))
        name = userinfo.get('name') or email.split('@')[0]
        google_id = userinfo.get('sub')

        # Find or create user
        user = _load_user_by_email(email)
        if not user:
            conn = _get_db()
            conn.execute(
                'INSERT INTO users (email, name, google_id) VALUES (?, ?, ?)',
                (email, name, google_id)
            )
            conn.commit()
            conn.close()
            user = _load_user_by_email(email)
        elif not user.google_id:
            # Link Google to existing email account
            conn = _get_db()
            conn.execute('UPDATE users SET google_id = ?, name = ? WHERE id = ?',
                         (google_id, name, user.id))
            conn.commit()
            conn.close()
            user = _load_user_by_id(user.id)

        login_user(user, remember=True)
        _set_session_token(user.id)
        return redirect(request.args.get('next', '/'))

    except Exception:
        logger.exception("Google OAuth callback failed")
        return redirect(url_for('auth.login_page', error='oauth_failed'))


# ─── Email/Password Auth ──────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET'])
def login_page():
    error = request.args.get('error', '')
    error_msg = {
        'invalid': 'Invalid email or password.',
        'exists': 'An account with that email already exists.',
        'no_email': 'Could not get email from Google.',
        'oauth_failed': 'Google login failed. Please try again.',
        'mismatch': 'Passwords do not match.',
        'session_expired': 'Your session expired. Please log in again.',
        'set_password': (
            'This account was created at checkout and still needs a password. '
            'Check your email for a set-password link. '
            'If you just paid, open /set-password from that email.'
        ),
        'checkout_failed': (
            'We could not confirm your payment yet. '
            'If you were charged, try logging in — premium may already be active. '
            'Otherwise contact support.'
        ),
    }.get(error, '')

    return render_template(
        'login.html',
        error_msg=error_msg,
        google_enabled=GOOGLE_OAUTH_READY,
        page='login',
    )


@auth_bp.route('/login', methods=['POST'])
def login_submit():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')

    if not email or not password:
        return redirect(url_for('auth.login_page', error='invalid'))

    user = _load_user_by_email(email)
    if not user:
        return redirect(url_for('auth.login_page', error='invalid'))

    # Check password (case-insensitive email match)
    conn = _get_db()
    row = conn.execute(
        'SELECT password_hash FROM users WHERE lower(email) = ?', (email,)
    ).fetchone()
    conn.close()

    if not row or not row['password_hash']:
        # Guest checkout: premium may be active but no password yet — re-issue claim link
        try:
            claim_token = _ensure_claim_token_for_user(user.id, force_new=True)
            if claim_token:
                _maybe_send_claim_email(
                    email, claim_token,
                    base_url=request.url_root.rstrip('/'),
                )
                logger.info(
                    "[auth] Passwordless login for user_id=%s email=%s premium=%s — claim link issued",
                    user.id, email, bool(user.premium_active),
                )
        except Exception as e:
            logger.warning("[auth] Claim re-issue on login failed: %s", e)
        return redirect(url_for('auth.login_page', error='set_password'))

    if not check_password_hash(row['password_hash'], password):
        return redirect(url_for('auth.login_page', error='invalid'))

    login_user(user, remember=True)
    _set_session_token(user.id)
    logger.info(
        "[auth] Login ok user_id=%s email=%s premium=%s expires=%s",
        user.id, email, bool(user.premium_active), user.premium_expires,
    )
    return redirect(request.args.get('next', '/'))


@auth_bp.route('/signup', methods=['GET'])
def signup_page():
    error = request.args.get('error', '')
    error_msg = {
        'invalid': 'Please enter a valid email and password.',
        'mismatch': 'Passwords do not match.',
    }.get(error, '')
    return render_template(
        'signup.html',
        error_msg=error_msg,
        google_enabled=GOOGLE_OAUTH_READY,
        page='signup',
    )


@auth_bp.route('/signup', methods=['POST'])
def signup_submit():
    email = request.form.get('email', '').strip().lower()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    confirm = request.form.get('confirm', '')

    if not email or not password:
        return redirect(url_for('auth.signup_page', error='invalid'))
    if password != confirm:
        return redirect(url_for('auth.signup_page', error='mismatch'))

    # Check if user exists
    existing = _load_user_by_email(email)
    if existing:
        return redirect(url_for('auth.login_page', error='exists'))

    # Create user
    pw_hash = generate_password_hash(password)
    conn = _get_db()
    conn.execute(
        'INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)',
        (email, name or email.split('@')[0], pw_hash)
    )
    conn.commit()
    conn.close()

    user = _load_user_by_email(email)
    if user:
        login_user(user, remember=True)
        _set_session_token(user.id)

    return redirect('/')


@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect('/')


@auth_bp.route('/admin-reset')
def admin_reset():
    """One-time admin password reset. Visit /admin-reset?token=YOUR_ADMIN_PASSWORD"""
    token = request.args.get('token', '').strip()
    expected = os.environ.get('ADMIN_PASSWORD', '').strip()
    if not token or not expected or token != expected:
        return 'Unauthorized', 403
    try:
        pw_hash = generate_password_hash(token)
        conn = _get_db()
        for adm_email in ADMIN_EMAILS:
            existing = conn.execute('SELECT id FROM users WHERE email = ?', (adm_email,)).fetchone()
            if existing:
                conn.execute('UPDATE users SET password_hash = ?, is_premium = 1 WHERE email = ?', (pw_hash, adm_email))
            else:
                conn.execute('INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, 1)',
                             (adm_email, adm_email.split('@')[0], pw_hash))
        conn.commit()
        conn.close()
        return f'<h2>Done.</h2><p>Admin accounts updated. <a href="/login">Login now</a> with your ADMIN_PASSWORD.</p>'
    except Exception as e:
        return f'Error: {e}', 500


# ─── Set-password / claim-account (post-pay for guest checkouts) ──────────────

def _hash_reset_token(raw_token):
    return hashlib.sha256((raw_token or '').encode('utf-8')).hexdigest()


def _user_has_password(user_id):
    """True if the user has a non-empty password_hash."""
    if not user_id:
        return False
    try:
        conn = _get_db()
        row = conn.execute(
            'SELECT password_hash FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        conn.close()
        return bool(row and row['password_hash'])
    except Exception as e:
        logger.error(f"_user_has_password failed: {e}")
        return False


def _issue_set_password_token(user_id, ttl_hours=None):
    """Create a one-time set-password token. Returns raw token (show once)."""
    if not user_id:
        return None
    ttl_hours = SET_PASSWORD_TOKEN_HOURS if ttl_hours is None else ttl_hours
    raw = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(raw)
    expires_at = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
    try:
        conn = _get_db()
        # Invalidate unused prior tokens for this user
        conn.execute(
            'UPDATE password_reset_tokens SET used_at = datetime(\'now\') '
            'WHERE user_id = ? AND used_at IS NULL',
            (user_id,)
        )
        conn.execute(
            'INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) '
            'VALUES (?, ?, ?)',
            (user_id, token_hash, expires_at)
        )
        conn.commit()
        conn.close()
        return raw
    except Exception as e:
        logger.error(f"_issue_set_password_token failed: {e}")
        return None


def _lookup_set_password_token(raw_token):
    """Return (user_id, email) for a valid unused token, else (None, None)."""
    if not raw_token:
        return None, None
    token_hash = _hash_reset_token(raw_token)
    try:
        conn = _get_db()
        row = conn.execute(
            '''SELECT t.user_id, t.expires_at, t.used_at, u.email
               FROM password_reset_tokens t
               JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ?''',
            (token_hash,)
        ).fetchone()
        conn.close()
        if not row or row['used_at']:
            return None, None
        try:
            if datetime.fromisoformat(row['expires_at']) < datetime.now():
                return None, None
        except Exception:
            return None, None
        return row['user_id'], row['email']
    except Exception as e:
        logger.error(f"_lookup_set_password_token failed: {e}")
        return None, None


def _consume_set_password_token(raw_token, new_password):
    """Set password for the token's user and mark token used. Returns User or None."""
    user_id, email = _lookup_set_password_token(raw_token)
    if not user_id or not new_password or len(new_password) < 6:
        return None
    pw_hash = generate_password_hash(new_password)
    token_hash = _hash_reset_token(raw_token)
    try:
        conn = _get_db()
        conn.execute(
            'UPDATE users SET password_hash = ? WHERE id = ?',
            (pw_hash, user_id)
        )
        conn.execute(
            'UPDATE password_reset_tokens SET used_at = datetime(\'now\') '
            'WHERE token_hash = ?',
            (token_hash,)
        )
        conn.commit()
        conn.close()
        return _load_user_by_id(user_id)
    except Exception as e:
        logger.error(f"_consume_set_password_token failed: {e}")
        return None


def _has_unused_set_password_token(user_id):
    """True if user already has an unused, unexpired claim token."""
    if not user_id:
        return False
    try:
        conn = _get_db()
        row = conn.execute(
            '''SELECT expires_at FROM password_reset_tokens
               WHERE user_id = ? AND used_at IS NULL
               ORDER BY id DESC LIMIT 1''',
            (user_id,)
        ).fetchone()
        conn.close()
        if not row:
            return False
        return datetime.fromisoformat(row['expires_at']) >= datetime.now()
    except Exception:
        return False


def _ensure_claim_token_for_user(user_id, force_new=False):
    """If user has no password, issue a claim token; else return None.

    When force_new is False and an unused token already exists, return None
    (raw token cannot be recovered — used by webhook to avoid clobbering).
    """
    if _user_has_password(user_id):
        return None
    if not force_new and _has_unused_set_password_token(user_id):
        return None
    return _issue_set_password_token(user_id)


def _maybe_send_claim_email(email, raw_token, base_url=None):
    """Best-effort claim email via SMTP. Never raises; success page works without it."""
    if not email or not raw_token:
        return False
    smtp_password = (
        os.environ.get('SMTP_PASSWORD')
        or os.environ.get('CONTACT_SMTP_PASSWORD')
        or ''
    ).strip()
    if not smtp_password:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        smtp_host = (os.environ.get('SMTP_HOST') or 'smtp.gmail.com').strip()
        smtp_port = int(os.environ.get('SMTP_PORT') or '587')
        support = (os.environ.get('SUPPORT_EMAIL') or 'underdogsbetemail@gmail.com').strip()
        smtp_user = (os.environ.get('SMTP_USER') or support).strip()
        root = (base_url or '').rstrip('/') or 'https://predictionlab.io'
        link = f'{root}/set-password?token={raw_token}'
        body = (
            f'Your Prediction Lab Premium access is active for {email}.\n\n'
            f'Set your password so you can log in anytime:\n{link}\n\n'
            f'This link expires in {SET_PASSWORD_TOKEN_HOURS} hours.\n'
        )
        msg = MIMEText(body)
        msg['Subject'] = 'Set your Prediction Lab password'
        msg['From'] = smtp_user
        msg['To'] = email
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.sendmail(smtp_user, [email], msg.as_string())
        logger.info(f"[claim] Sent set-password email to {email}")
        return True
    except Exception as e:
        logger.warning(f"[claim] Email send failed (non-fatal): {e}")
        return False


# ─── Premium welcome email (first-time subscribe only) ─────────────────────────

_PLAN_DISPLAY_NAMES = {
    'weekly': 'Weekly',
    'monthly': 'Monthly',
    'yearly': 'Yearly',
}


def _plan_display_name(plan):
    """Human plan label for email copy (Weekly / Monthly / Yearly)."""
    key = _normalize_plan(plan) if plan else 'monthly'
    return _PLAN_DISPLAY_NAMES.get(key, 'Premium')


def _first_name_for_email(name=None, email=None):
    """Best-effort first name for greeting; never raises."""
    try:
        if name:
            part = str(name).strip().split()[0]
            if part and '@' not in part and len(part) < 40:
                return part
        if email:
            local = str(email).split('@')[0].strip()
            token = local.replace('.', ' ').replace('_', ' ').replace('-', ' ').split()[0]
            if token and token.isalpha():
                return token.capitalize()
    except Exception:
        pass
    return 'there'


def _smtp_credentials():
    """Return (host, port, user, password, support) or None if password missing."""
    smtp_password = (
        os.environ.get('SMTP_PASSWORD')
        or os.environ.get('CONTACT_SMTP_PASSWORD')
        or ''
    ).strip()
    if not smtp_password:
        return None
    smtp_host = (os.environ.get('SMTP_HOST') or 'smtp.gmail.com').strip()
    smtp_port = int(os.environ.get('SMTP_PORT') or '587')
    support = (os.environ.get('SUPPORT_EMAIL') or 'underdogsbetemail@gmail.com').strip()
    smtp_user = (os.environ.get('SMTP_USER') or support).strip()
    return smtp_host, smtp_port, smtp_user, smtp_password, support


def _welcome_subscription_key(subscription_id=None, event_id=None):
    """Idempotency key: prefer Stripe subscription id; else event id once."""
    sub = _stripe_id(subscription_id) if subscription_id else None
    if sub:
        return sub
    evt = _stripe_id(event_id) if event_id else None
    if evt:
        return f'evt:{evt}'
    return None


def _claim_welcome_email_slot(subscription_key, user_id, event_id=None, plan=None):
    """Atomically claim the welcome send for this subscription. True = caller owns send."""
    if not subscription_key or not user_id:
        return False
    try:
        conn = _get_db()
        cur = conn.execute(
            '''INSERT OR IGNORE INTO premium_welcome_emails
               (subscription_id, user_id, event_id, plan)
               VALUES (?, ?, ?, ?)''',
            (subscription_key, user_id, event_id or '', plan or ''),
        )
        conn.commit()
        claimed = (cur.rowcount or 0) > 0
        conn.close()
        return claimed
    except Exception as e:
        logger.warning("[welcome] claim slot failed (non-fatal): %s", e)
        return False


def _release_welcome_email_slot(subscription_key):
    """Release claim after send failure so a later initial event can retry."""
    if not subscription_key:
        return
    try:
        conn = _get_db()
        conn.execute(
            'DELETE FROM premium_welcome_emails WHERE subscription_id = ?',
            (subscription_key,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("[welcome] release slot failed (non-fatal): %s", e)


def _finalize_welcome_email_sent(user_id, subscription_key):
    """Mirror send onto users.welcome_email_* for ops visibility."""
    if not user_id or not subscription_key:
        return
    sent_at = datetime.now().isoformat()
    try:
        conn = _get_db()
        try:
            conn.execute(
                '''UPDATE users SET
                       welcome_email_sent_at = ?,
                       welcome_email_subscription_id = ?
                   WHERE id = ?''',
                (sent_at, subscription_key, user_id),
            )
        except sqlite3.OperationalError:
            # Columns missing on very old DBs — table row is enough for idempotency
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("[welcome] finalize user columns failed (non-fatal): %s", e)


def _build_premium_welcome_email_html(*, first_name, plan_label, site_url='https://predictionlab.io'):
    """Responsive HTML welcome body. No payment / card details."""
    site = (site_url or 'https://predictionlab.io').rstrip('/')
    login_url = f'{site}/login'
    plans_url = f'{site}/plans'
    safe_name = first_name or 'there'
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Welcome to PredictionLab Premium</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#e2e8f0;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0f172a;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#1e293b;border:1px solid #334155;border-radius:16px;overflow:hidden;">
        <tr><td style="padding:28px 28px 12px;text-align:center;">
          <div style="font-size:22px;font-weight:800;letter-spacing:0.3px;color:#fbbf24;">PredictionLab</div>
          <div style="margin-top:6px;font-size:13px;color:#94a3b8;">AI sports predictions</div>
        </td></tr>
        <tr><td style="padding:8px 28px 8px;">
          <h1 style="margin:0 0 12px;font-size:22px;line-height:1.35;color:#f8fafc;">Welcome to Premium, {safe_name}</h1>
          <p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#cbd5e1;">
            Your <strong style="color:#fbbf24;">{plan_label}</strong> PredictionLab Premium subscription is active.
            After you log in, spreads, totals, projected scores, and full model edge unlock across supported sports.
          </p>
          <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:#cbd5e1;">
            Open PredictionLab, sign in with the email you used at checkout, and you are ready to go.
          </p>
          <table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto 18px;">
            <tr><td style="border-radius:10px;background:linear-gradient(135deg,#fbbf24,#f59e0b);">
              <a href="{site}/" style="display:inline-block;padding:14px 28px;font-size:15px;font-weight:800;color:#0f172a;text-decoration:none;">
                Go to PredictionLab.io
              </a>
            </td></tr>
          </table>
          <p style="margin:0 0 10px;font-size:14px;line-height:1.55;color:#94a3b8;">
            <a href="{login_url}" style="color:#93c5fd;font-weight:600;">Log in</a>
            &nbsp;·&nbsp;
            <a href="{plans_url}" style="color:#93c5fd;font-weight:600;">Account / subscription (Plans)</a>
          </p>
          <p style="margin:16px 0 0;font-size:13px;line-height:1.55;color:#64748b;">
            You can manage or cancel your subscription anytime from the Plans page on PredictionLab
            or through the billing email Stripe sends you. No payment details are included in this message.
          </p>
        </td></tr>
        <tr><td style="padding:18px 28px 26px;text-align:center;border-top:1px solid #334155;">
          <p style="margin:0;font-size:12px;color:#64748b;">
            PredictionLab · <a href="{site}/" style="color:#94a3b8;text-decoration:none;">{site.replace('https://', '')}</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>'''


def _build_premium_welcome_email_text(*, first_name, plan_label, site_url='https://predictionlab.io'):
    site = (site_url or 'https://predictionlab.io').rstrip('/')
    return (
        f'Welcome to PredictionLab Premium, {first_name or "there"}!\n\n'
        f'Your {plan_label} Premium subscription is active.\n'
        f'Log in at {site}/login — Premium unlocks after login.\n'
        f'Manage or cancel anytime: {site}/plans\n\n'
        f'Open the site: {site}/\n'
        f'(This email does not include payment details.)\n'
    )


def _send_premium_welcome_email_smtp(*, to_email, first_name, plan_label):
    """Send welcome HTML via existing SMTP env. Never raises. Returns True on success."""
    creds = _smtp_credentials()
    if not creds:
        logger.warning("[welcome] failed: SMTP password not configured (non-fatal)")
        return False
    smtp_host, smtp_port, smtp_user, smtp_password, _support = creds
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        site = 'https://predictionlab.io'
        text_body = _build_premium_welcome_email_text(
            first_name=first_name, plan_label=plan_label, site_url=site,
        )
        html_body = _build_premium_welcome_email_html(
            first_name=first_name, plan_label=plan_label, site_url=site,
        )
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Welcome to PredictionLab Premium'
        msg['From'] = smtp_user
        msg['To'] = to_email
        msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.sendmail(smtp_user, [to_email], msg.as_string())
        return True
    except Exception as e:
        # Do not log credentials or message bodies
        logger.warning("[welcome] SMTP send failed (non-fatal): %s", e)
        return False


def _maybe_send_premium_welcome_email(
    *,
    user_id=None,
    email=None,
    name=None,
    plan=None,
    subscription_id=None,
    event_id=None,
    is_initial_subscribe=True,
):
    """Send one Premium welcome per Stripe subscription_id.

    Server-side only; never raises; never blocks Premium activation.
    Logging outcomes: attempted / sent / skipped already_sent / skipped not_initial /
    skipped missing / failed.
    """
    subscription_key = None
    try:
        if not is_initial_subscribe:
            logger.info(
                "[welcome] skipped not_initial user_id=%s sub=%s event_id=%s",
                user_id, subscription_id, event_id,
            )
            return False

        email_norm = _normalize_email(email)
        subscription_key = _welcome_subscription_key(subscription_id, event_id)
        if not user_id or not email_norm or not subscription_key:
            logger.info(
                "[welcome] skipped missing user_id=%s email=%s sub_key=%s event_id=%s",
                user_id, bool(email_norm), subscription_key, event_id,
            )
            return False

        # Fast path: same subscription already welcomed on the user row
        try:
            conn = _get_db()
            row = conn.execute(
                'SELECT welcome_email_subscription_id FROM users WHERE id = ?',
                (user_id,),
            ).fetchone()
            conn.close()
            if row is not None:
                try:
                    prior = row['welcome_email_subscription_id']
                except (KeyError, IndexError, TypeError):
                    prior = None
                if prior and prior == subscription_key:
                    logger.info(
                        "[welcome] skipped already_sent user_id=%s sub=%s",
                        user_id, subscription_key,
                    )
                    return False
        except sqlite3.OperationalError:
            pass
        except Exception as e:
            logger.warning("[welcome] prior-sent lookup failed (continuing): %s", e)

        plan_key = _normalize_plan(plan)
        plan_label = _plan_display_name(plan_key)
        first_name = _first_name_for_email(name, email_norm)

        logger.info(
            "[welcome] attempted user_id=%s sub=%s plan=%s event_id=%s",
            user_id, subscription_key, plan_key, event_id,
        )

        if not _claim_welcome_email_slot(
            subscription_key, user_id, event_id=event_id, plan=plan_key,
        ):
            logger.info(
                "[welcome] skipped already_sent user_id=%s sub=%s",
                user_id, subscription_key,
            )
            return False

        ok = _send_premium_welcome_email_smtp(
            to_email=email_norm,
            first_name=first_name,
            plan_label=plan_label,
        )
        if ok:
            _finalize_welcome_email_sent(user_id, subscription_key)
            logger.info(
                "[welcome] sent user_id=%s sub=%s plan=%s",
                user_id, subscription_key, plan_key,
            )
            return True

        _release_welcome_email_slot(subscription_key)
        logger.warning(
            "[welcome] failed user_id=%s sub=%s (premium still active)",
            user_id, subscription_key,
        )
        return False
    except Exception as e:
        logger.warning("[welcome] failed unexpected (non-fatal): %s", e)
        try:
            if subscription_key:
                _release_welcome_email_slot(subscription_key)
        except Exception:
            pass
        return False


def _welcome_after_premium_grant(
    *,
    user_id=None,
    email=None,
    name=None,
    plan=None,
    subscription_id=None,
    event_id=None,
    is_initial_subscribe=True,
):
    """Best-effort welcome after premium was already granted. Never raises."""
    try:
        if not user_id and email:
            user = _load_user_by_email(email)
            if user:
                user_id = user.id
                name = name or user.name
        if user_id and not email:
            user = _load_user_by_id(user_id)
            if user:
                email = user.email
                name = name or user.name
        _maybe_send_premium_welcome_email(
            user_id=user_id,
            email=email,
            name=name,
            plan=plan,
            subscription_id=subscription_id,
            event_id=event_id,
            is_initial_subscribe=is_initial_subscribe,
        )
    except Exception as e:
        logger.warning("[welcome] post-grant hook failed (non-fatal): %s", e)


# ─── Stripe Payments ──────────────────────────────────────────────────────────

def _stripe_price_for_plan(plan):
    """Return Stripe Price ID for a validated plan name."""
    return {
        'monthly': STRIPE_PRICE_MONTHLY,
        'yearly': STRIPE_PRICE_YEARLY,
        'weekly': STRIPE_PRICE_WEEKLY,
    }.get(plan, '')


def _plan_from_price_id(price_id):
    """Map a Stripe Price ID back to monthly|yearly|weekly when metadata is missing.

    Env vars must be Price IDs (price_…), not Product IDs (prod_…). Unmapped
    prices still grant premium via active-sub sync; interval inference sets plan.
    """
    if not price_id:
        return None
    if price_id == STRIPE_PRICE_WEEKLY:
        return 'weekly'
    if price_id == STRIPE_PRICE_MONTHLY:
        return 'monthly'
    if price_id == STRIPE_PRICE_YEARLY:
        return 'yearly'
    return None


def _plan_from_recurring_interval(interval):
    """Map Stripe recurring.interval (week|month|year) to plan slug."""
    if not interval:
        return None
    key = str(interval).strip().lower()
    if key in ('year', 'yearly', 'annual', 'annually'):
        return 'yearly'
    if key in ('week', 'weekly'):
        return 'weekly'
    if key in ('month', 'monthly'):
        return 'monthly'
    return None


def _recurring_interval_from_price(price):
    """Best-effort recurring.interval from a price object (not a bare id string)."""
    if not price or isinstance(price, str):
        return None
    recurring = _obj_get(price, 'recurring') or {}
    return _obj_get(recurring, 'interval')


def _plan_from_subscription_interval(subscription):
    """Infer plan from subscription item price.recurring.interval (Payment Link path)."""
    if not subscription:
        return None
    try:
        items = _obj_get(subscription, 'items') or {}
        data = _obj_get(items, 'data') if not isinstance(items, list) else items
        for item in data or []:
            price = _obj_get(item, 'price') or {}
            plan = _plan_from_recurring_interval(_recurring_interval_from_price(price))
            if plan:
                return plan
    except Exception:
        pass
    return None


def _plan_from_invoice_interval(invoice):
    """Infer plan from invoice line price.recurring.interval when price id is unmapped."""
    if not invoice:
        return None
    try:
        lines = _obj_get(invoice, 'lines') or {}
        data = _obj_get(lines, 'data') or []
        for line in data or []:
            price = _obj_get(line, 'price')
            plan = _plan_from_recurring_interval(_recurring_interval_from_price(price))
            if plan:
                return plan
    except Exception:
        pass
    return None


def _iso_from_unix(ts):
    """Convert a Stripe unix timestamp to ISO datetime string."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).isoformat()
    except Exception:
        return None


def _obj_get(obj, key, default=None):
    """Safe get for dict or StripeObject."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj.get(key, default)
    except Exception:
        pass
    return getattr(obj, key, default)


def _stripe_id(value):
    """Coerce expanded Stripe objects / dicts down to an id string."""
    if not value:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _stripe_id(value.get('id'))
    return _stripe_id(getattr(value, 'id', None))


def _subscription_id_from_invoice(invoice):
    """Extract subscription id from invoice across classic and newer Stripe shapes.

    Classic: invoice.subscription
    API 2026-06-24.dahlia+: invoice.parent.subscription_details.subscription
    Also: lines.data[].subscription / line.parent.subscription_item_details
    Never raises.
    """
    if not invoice:
        return None
    try:
        sub_id = _stripe_id(_obj_get(invoice, 'subscription'))
        if sub_id:
            return sub_id
        parent = _obj_get(invoice, 'parent') or {}
        details = _obj_get(parent, 'subscription_details') or {}
        sub_id = _stripe_id(_obj_get(details, 'subscription'))
        if sub_id:
            return sub_id
        # Some payloads only nest the subscription on invoice lines.
        lines = _obj_get(invoice, 'lines') or {}
        data = _obj_get(lines, 'data') or []
        for line in data or []:
            sub_id = _stripe_id(_obj_get(line, 'subscription'))
            if sub_id:
                return sub_id
            line_parent = _obj_get(line, 'parent') or {}
            item_details = (
                _obj_get(line_parent, 'subscription_item_details')
                or _obj_get(line_parent, 'subscription_details')
                or {}
            )
            sub_id = _stripe_id(_obj_get(item_details, 'subscription'))
            if sub_id:
                return sub_id
    except Exception as e:
        logger.warning("[stripe] _subscription_id_from_invoice failed: %s", e)
    return None


def _price_id_from_invoice(invoice):
    """Best-effort price id from invoice lines (classic price + dahlia pricing)."""
    if not invoice:
        return None
    try:
        lines = _obj_get(invoice, 'lines') or {}
        data = _obj_get(lines, 'data') or []
        for line in data or []:
            price = _obj_get(line, 'price')
            pid = _stripe_id(price)
            if pid:
                return pid
            pricing = _obj_get(line, 'pricing') or {}
            details = _obj_get(pricing, 'price_details') or {}
            pid = _stripe_id(_obj_get(details, 'price'))
            if pid:
                return pid
    except Exception as e:
        logger.warning("[stripe] _price_id_from_invoice failed: %s", e)
    return None


def _price_id_from_subscription(subscription):
    """Best-effort price id from subscription items."""
    if not subscription:
        return None
    try:
        items = _obj_get(subscription, 'items') or {}
        data = _obj_get(items, 'data') if not isinstance(items, list) else items
        if not data:
            return None
        first = data[0]
        price = _obj_get(first, 'price') or {}
        return _stripe_id(price) or _obj_get(price, 'id')
    except Exception:
        return None


def _period_end_from_subscription(subscription):
    """current_period_end from subscription root or first item (newer API shapes)."""
    if not subscription:
        return None
    pe = _obj_get(subscription, 'current_period_end')
    if pe:
        return _iso_from_unix(pe)
    try:
        items = _obj_get(subscription, 'items') or {}
        data = _obj_get(items, 'data') if not isinstance(items, list) else items
        for item in data or []:
            pe = _obj_get(item, 'current_period_end')
            if pe:
                return _iso_from_unix(pe)
    except Exception:
        pass
    return None


def _plan_from_checkout_or_subscription(session_data, subscription=None):
    """Resolve plan from checkout metadata, price id, or recurring interval.

    Payment Links often omit metadata.plan; interval inference keeps yearly/weekly
    from activating as monthly when STRIPE_PRICE_* env is unset.
    """
    plan = None
    if session_data:
        meta = _obj_get(session_data, 'metadata') or {}
        plan = _obj_get(meta, 'plan') if not isinstance(meta, dict) else meta.get('plan')
        if isinstance(plan, str):
            plan = plan.strip().lower()
    if plan in VALID_CHECKOUT_PLANS:
        return plan
    if subscription:
        try:
            meta = _obj_get(subscription, 'metadata') or {}
            sub_plan = meta.get('plan') if isinstance(meta, dict) else _obj_get(meta, 'plan')
            if isinstance(sub_plan, str) and sub_plan.strip().lower() in VALID_CHECKOUT_PLANS:
                return sub_plan.strip().lower()
            inferred = _plan_from_price_id(_price_id_from_subscription(subscription))
            if inferred:
                return inferred
            inferred = _plan_from_subscription_interval(subscription)
            if inferred:
                return inferred
        except Exception:
            pass
    return plan if plan in VALID_CHECKOUT_PLANS else 'monthly'


def _find_user_id_by_customer(stripe_customer_id):
    """Return users.id for a Stripe customer id, or None."""
    customer_id = _stripe_id(stripe_customer_id)
    if not customer_id:
        return None
    try:
        conn = _get_db()
        row = conn.execute(
            'SELECT id FROM users WHERE stripe_customer_id = ?',
            (customer_id,)
        ).fetchone()
        conn.close()
        return row['id'] if row else None
    except Exception as e:
        logger.error(f"Lookup by stripe_customer_id failed: {e}")
        return None


def _normalize_email(email):
    """Strip + lowercase email; empty → None."""
    if not email:
        return None
    email = str(email).strip().lower()
    return email or None


def _checkout_payment_ok(payment_status, session_status=None):
    """True when Checkout Session should grant premium.

    100% coupons / trials often use payment_status=no_payment_required
    (not 'paid') while the session is still complete and the sub is active.
    """
    ps = (payment_status or '').strip().lower()
    if ps in ('paid', 'no_payment_required'):
        return True
    # Belt-and-suspenders: complete session with unknown status still ok
    # only when explicitly paid-like — avoid activating unpaid opens.
    return False


def _fetch_stripe_customer_email(customer_id, stripe_mod):
    """Best-effort Customer.email lookup. Never raises."""
    customer_id = _stripe_id(customer_id)
    if not customer_id or stripe_mod is None:
        return None
    try:
        cust = stripe_mod.Customer.retrieve(customer_id)
        return _normalize_email(_obj_get(cust, 'email'))
    except Exception as e:
        logger.warning(
            "[stripe] Customer.retrieve email failed customer=%s: %s",
            customer_id, e,
        )
        return None


def _user_id_from_reference(client_reference_id=None, metadata=None):
    """Parse users.id from Checkout client_reference_id or metadata.user_id."""
    candidates = []
    if client_reference_id is not None and str(client_reference_id).strip():
        candidates.append(str(client_reference_id).strip())
    if metadata is not None:
        if isinstance(metadata, dict):
            uid = metadata.get('user_id')
        else:
            uid = _obj_get(metadata, 'user_id')
        if uid is not None and str(uid).strip():
            candidates.append(str(uid).strip())
    for raw in candidates:
        if raw.isdigit():
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            if _load_user_by_id(uid):
                return uid
    return None


def _resolve_user_for_stripe_payer(
    *,
    customer_id=None,
    email=None,
    name=None,
    client_reference_id=None,
    metadata=None,
    stripe_mod=None,
):
    """Resolve (or create) the local user for a Stripe payer.

    Order: stripe_customer_id → client_reference_id / metadata.user_id →
    email → Customer.retrieve(email). Buy Buttons / Payment Links often omit
    client_reference_id and metadata; email may only live on the Customer.
    Returns (user_id or None, email_norm or None).
    """
    customer_id = _stripe_id(customer_id)
    email = _normalize_email(email)

    user_id = _find_user_id_by_customer(customer_id) if customer_id else None
    if user_id:
        return user_id, email

    ref_uid = _user_id_from_reference(client_reference_id, metadata)
    if ref_uid:
        return ref_uid, email

    if not email and customer_id:
        email = _fetch_stripe_customer_email(customer_id, stripe_mod)

    if email:
        user = _find_or_create_user_by_email(email, name=name)
        if user:
            return user.id, email

    return None, email


def _find_or_create_user_by_email(email, name=None):
    """Find user by email or create a passwordless account. Returns User or None."""
    email = _normalize_email(email)
    if not email:
        return None
    user = _load_user_by_email(email)
    if user:
        return user
    try:
        conn = _get_db()
        conn.execute(
            'INSERT INTO users (email, name) VALUES (?, ?)',
            (email, name or email.split('@')[0])
        )
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        # Race: another webhook/success path created the row
        pass
    except Exception as e:
        logger.error("[stripe] create user failed for %s: %s", email, e)
        return _load_user_by_email(email)
    user = _load_user_by_email(email)
    if user:
        logger.info("[stripe] Auto-created account for %s user_id=%s", email, user.id)
    return user


def _webhook_event_already_processed(event_id):
    if not event_id:
        return False
    try:
        conn = _get_db()
        row = conn.execute(
            'SELECT 1 FROM stripe_webhook_events WHERE event_id = ?', (event_id,)
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception as e:
        logger.warning("[stripe] idempotency lookup failed: %s", e)
        return False


def _mark_webhook_event_processed(event_id, event_type=None):
    if not event_id:
        return
    try:
        conn = _get_db()
        conn.execute(
            'INSERT OR IGNORE INTO stripe_webhook_events (event_id, event_type) VALUES (?, ?)',
            (event_id, event_type or '')
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("[stripe] idempotency store failed: %s", e)


def _log_premium_decision(context, *, user_id=None, email=None, customer_id=None,
                          subscription_id=None, status=None, plan=None,
                          is_premium=None, expires=None):
    """Server-only premium decision log (never render in HTML)."""
    logger.info(
        "[stripe] %s user_id=%s email=%s customer=%s sub=%s status=%s plan=%s "
        "premium=%s expires=%s",
        context, user_id, email, customer_id, subscription_id, status, plan,
        is_premium, expires,
    )


def _log_activation_failure(
    reason,
    *,
    event_id=None,
    event_type=None,
    customer_id=None,
    email=None,
    subscription_id=None,
    user_id=None,
    payment_status=None,
    sub_status=None,
):
    """ERROR when paid/active activation cannot write durable premium.

    Leave the webhook event unmarked so Stripe Dashboard Resend can retry.
    """
    logger.error(
        "[stripe] ACTIVATION_FAILED reason=%s event_id=%s type=%s "
        "customer=%s email=%s sub=%s user_id=%s payment_status=%s sub_status=%s",
        reason, event_id, event_type, customer_id, email, subscription_id,
        user_id, payment_status, sub_status,
    )


def _normalize_plan(plan):
    """Coerce plan slug to weekly|monthly|yearly; unknown → monthly."""
    if isinstance(plan, str):
        key = plan.strip().lower()
        if key in VALID_CHECKOUT_PLANS:
            return key
    return 'monthly'


def _grant_premium_to_payer(
    *,
    customer_id=None,
    email=None,
    name=None,
    client_reference_id=None,
    metadata=None,
    stripe_mod=None,
    plan=None,
    subscription_id=None,
    premium_expires=None,
    context='grant_premium',
    event_id=None,
    event_type=None,
    payment_status=None,
    send_claim_email=False,
):
    """Single shared activation path for Checkout / invoice / Payment Link / Buy Button.

    Resolve payer (customer id → client_reference_id/metadata → email →
    Customer.retrieve) then write is_premium=1 + stripe ids + plan expires.
    Weekly, monthly, and yearly all use this helper so handlers cannot diverge.

    Returns True only when a local user row was updated with premium.
    """
    user_id, resolved_email = _resolve_user_for_stripe_payer(
        customer_id=customer_id,
        email=email,
        name=name,
        client_reference_id=client_reference_id,
        metadata=metadata,
        stripe_mod=stripe_mod,
    )
    if not user_id:
        _log_activation_failure(
            'no_resolvable_user',
            event_id=event_id, event_type=event_type,
            customer_id=customer_id, email=resolved_email or email,
            subscription_id=subscription_id, payment_status=payment_status,
        )
        return False

    resolved_plan = _normalize_plan(plan)
    ok = _activate_premium(
        user_id,
        plan=resolved_plan,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        premium_expires=premium_expires,
    )
    if not ok:
        _log_activation_failure(
            'db_activate_failed',
            event_id=event_id, event_type=event_type,
            customer_id=customer_id, email=resolved_email,
            subscription_id=subscription_id, user_id=user_id,
            payment_status=payment_status,
        )
        return False

    if send_claim_email and resolved_email:
        claim_token = _ensure_claim_token_for_user(user_id, force_new=False)
        if claim_token:
            _maybe_send_claim_email(resolved_email, claim_token)

    _log_premium_decision(
        context,
        user_id=user_id, email=resolved_email, customer_id=customer_id,
        subscription_id=subscription_id, status=payment_status or 'paid',
        plan=resolved_plan, is_premium=1,
        expires=premium_expires or 'plan_calendar',
    )
    return True


def _sync_premium_from_subscription(
    subscription, plan=None, stripe_mod=None, event_id=None, event_type=None,
    fallback_email=None,
):
    """
    Sync local premium access from a Stripe Subscription (renewals + first create).
    ONLY updates is_premium / premium_expires / stripe_customer_id [/subscription id].
    Never raises — logs and returns False on incomplete payloads.
    Returns True when a local user row was updated.

    Product intent: any active/trialing/past_due subscription grants premium
    regardless of whether the price id matches STRIPE_PRICE_* env (unknown /
    remapped prices still unlock; plan slug only affects calendar fallback length).
    """
    if not subscription:
        return False

    customer_id = _stripe_id(_obj_get(subscription, 'customer'))
    subscription_id = _stripe_id(_obj_get(subscription, 'id'))
    status = (_obj_get(subscription, 'status') or '').strip()
    period_end_iso = _period_end_from_subscription(subscription)
    resolved_plan = _normalize_plan(
        plan or _plan_from_checkout_or_subscription(None, subscription)
    )

    # Subscription objects rarely include email; Buy Button / Payment Link first
    # events often arrive before stripe_customer_id is linked locally.
    meta = _obj_get(subscription, 'metadata') or {}
    meta_email = None
    if isinstance(meta, dict):
        meta_email = meta.get('email') or meta.get('user_email')
    else:
        meta_email = _obj_get(meta, 'email') or _obj_get(meta, 'user_email')

    user_id, resolved_email = _resolve_user_for_stripe_payer(
        customer_id=customer_id,
        email=meta_email or fallback_email,
        metadata=meta,
        stripe_mod=stripe_mod,
    )
    should_have_premium = status in ('active', 'trialing', 'past_due') or (
        not status and bool(period_end_iso)
    )
    if not user_id:
        if should_have_premium:
            _log_activation_failure(
                'no_resolvable_user',
                event_id=event_id, event_type=event_type or 'subscription_sync',
                customer_id=customer_id, email=resolved_email or meta_email,
                subscription_id=subscription_id, sub_status=status,
            )
        else:
            logger.info(
                "[stripe] No user for customer=%s sub=%s status=%s on sync (non-active)",
                customer_id, subscription_id, status,
            )
        return False

    now = datetime.now()
    expires_dt = None
    if period_end_iso:
        try:
            expires_dt = datetime.fromisoformat(period_end_iso)
        except Exception:
            expires_dt = None

    if status in ('active', 'trialing', 'past_due'):
        is_premium = 1
    elif status == 'canceled' and expires_dt and expires_dt > now:
        is_premium = 1
    elif not status and period_end_iso:
        # Incomplete thin/partial object — still extend if period_end present
        is_premium = 1
    else:
        is_premium = 0

    if not period_end_iso and is_premium:
        ok = _activate_premium(
            user_id,
            plan=resolved_plan,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
        )
        if not ok:
            _log_activation_failure(
                'db_activate_failed',
                event_id=event_id, event_type=event_type or 'subscription_sync',
                customer_id=customer_id, email=resolved_email,
                subscription_id=subscription_id, user_id=user_id,
                sub_status=status,
            )
            return False
        _log_premium_decision(
            'sync_activate_fallback',
            user_id=user_id, email=resolved_email, customer_id=customer_id,
            subscription_id=subscription_id, status=status,
            plan=resolved_plan, is_premium=1, expires='plan_calendar',
        )
        return True

    try:
        conn = _get_db()
        # stripe_subscription_id column is optional on very old DBs — try full update first
        try:
            conn.execute(
                '''UPDATE users SET
                       is_premium = ?,
                       premium_expires = COALESCE(?, premium_expires),
                       stripe_customer_id = COALESCE(?, stripe_customer_id),
                       stripe_subscription_id = COALESCE(?, stripe_subscription_id)
                   WHERE id = ?''',
                (is_premium, period_end_iso, customer_id, subscription_id, user_id)
            )
        except sqlite3.OperationalError:
            conn.execute(
                '''UPDATE users SET
                       is_premium = ?,
                       premium_expires = COALESCE(?, premium_expires),
                       stripe_customer_id = COALESCE(?, stripe_customer_id)
                   WHERE id = ?''',
                (is_premium, period_end_iso, customer_id, user_id)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        _log_activation_failure(
            'db_sync_failed',
            event_id=event_id, event_type=event_type or 'subscription_sync',
            customer_id=customer_id, email=resolved_email,
            subscription_id=subscription_id, user_id=user_id,
            sub_status=status,
        )
        logger.error("[stripe] sync DB update failed user_id=%s: %s", user_id, e)
        return False

    _log_premium_decision(
        'sync_subscription',
        user_id=user_id, email=resolved_email, customer_id=customer_id,
        subscription_id=subscription_id, status=status,
        plan=resolved_plan, is_premium=is_premium, expires=period_end_iso,
    )
    return True


def _payment_link_for_plan(plan):
    """Hardcoded Stripe Payment Link for weekly/monthly/yearly."""
    return {
        'weekly': STRIPE_WEEKLY_URL,
        'monthly': STRIPE_MONTHLY_URL,
        'yearly': STRIPE_YEARLY_URL,
    }.get(plan, '')


def _portal_return_url():
    """Return URL after Stripe Customer Portal (cancel / payment methods / invoices).

    Order: STRIPE_PORTAL_RETURN_URL env → local request host → production default
    ``https://predictionlab.io/``. Never invent customer IDs; this only picks a URL.
    """
    explicit = (STRIPE_PORTAL_RETURN_URL or '').strip()
    if explicit:
        return explicit
    try:
        host = (request.host or '').split(':')[0].lower()
    except Exception:
        host = ''
    local = host in {'localhost', '127.0.0.1'} or host.endswith('.local')
    if local:
        try:
            root = (request.url_root or '').rstrip('/')
            if root:
                return root + '/'
        except Exception:
            pass
    return DEFAULT_STRIPE_PORTAL_RETURN_URL


def _stripe_customer_id_from_db(user_id):
    """Load stripe_customer_id from the users table — never from the browser."""
    if not user_id:
        return None
    try:
        conn = _get_db()
        row = conn.execute(
            'SELECT stripe_customer_id FROM users WHERE id = ?',
            (user_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return _stripe_id(row['stripe_customer_id'])
    except Exception as e:
        logger.error("[billing/portal] DB lookup failed user_id=%s: %s", user_id, e)
        return None


def _billing_support_email():
    """Public support address for billing/portal error pages."""
    return (os.environ.get('SUPPORT_EMAIL') or 'support.predictionlab@gmail.com').strip()


def _billing_error_page(title, message, status_code=400):
    """Always return visible HTML for billing/portal failures (never a blank page)."""
    safe_title = html.escape(str(title or 'Billing'))
    safe_msg = html.escape(str(message or 'Something went wrong.'))
    support = _billing_support_email()
    safe_mailto = html.escape(support, quote=True)
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} — PredictionLab</title>
  <style>
    body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background:#f8fafc; color:#0f172a; }}
    .wrap {{ max-width:560px; margin:12vh auto; padding:24px; }}
    .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px;
             padding:28px 24px; box-shadow:0 10px 30px rgba(15,23,42,.06); }}
    h1 {{ margin:0 0 12px; font-size:1.35rem; }}
    p {{ margin:0 0 18px; line-height:1.55; color:#334155; }}
    a {{ color:#00529B; font-weight:600; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .actions {{ display:flex; gap:14px; flex-wrap:wrap; }}
  </style>
</head>
<body>
  <div class="wrap"><div class="card">
    <h1>{safe_title}</h1>
    <p>{safe_msg}</p>
    <div class="actions">
      <a href="/plans">Back to Plans</a>
      <a href="/">Home</a>
      <a href="mailto:{safe_mailto}">Contact support</a>
    </div>
  </div></div>
</body>
</html>"""
    return body, status_code, {'Content-Type': 'text/html; charset=utf-8'}


@auth_bp.route('/create-portal-session', methods=['POST', 'GET'])
@auth_bp.route('/billing/portal', methods=['POST', 'GET'])
@_flask_login_required
def create_portal_session():
    """Create a Stripe Billing Portal session and redirect the logged-in user.

    Customer id is always read from the database. Secret key stays server-side.
    Portal configuration (cancel, payment methods, invoices) is managed in Stripe
    Dashboard — this only opens a session for the stored customer.
    """
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))

    customer_id = _stripe_customer_id_from_db(current_user.id)
    if not customer_id:
        logger.warning(
            "[billing/portal] no stripe_customer_id for user_id=%s email=%s",
            getattr(current_user, 'id', None),
            getattr(current_user, 'email', None),
        )
        support = _billing_support_email()
        return _billing_error_page(
            'Manage Subscription unavailable',
            'No Stripe customer is linked to this account. '
            'If you subscribe through PredictionLab, contact support at '
            f'{support} so we can link your billing profile.',
            400,
        )

    if not STRIPE_SECRET_KEY:
        logger.error("[billing/portal] STRIPE_SECRET_KEY missing")
        return _billing_error_page(
            'Billing not configured',
            'Stripe is not configured on this server (missing STRIPE_SECRET_KEY). '
            'Add the key to the local environment and restart, then try again.',
            500,
        )

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=_portal_return_url(),
        )
        portal_url = _obj_get(portal_session, 'url')
        if not portal_url:
            logger.error("[billing/portal] Session.create returned no url")
            return _billing_error_page(
                'Could not open billing portal',
                'Stripe did not return a portal URL. Please try again in a moment.',
                502,
            )
        return redirect(portal_url)
    except Exception as e:
        logger.error(
            "[billing/portal] Session.create failed user_id=%s customer=%s: %s",
            getattr(current_user, 'id', None),
            customer_id,
            e,
        )
        support = _billing_support_email()
        return _billing_error_page(
            'Could not open billing portal',
            'Could not open the billing portal. Please try again in a moment, '
            f'or contact support at {support}. '
            '(Common local causes: test/live Stripe key mismatch, or Customer '
            'Portal not enabled in that Stripe mode.)',
            502,
        )


@auth_bp.route('/checkout/<plan>')
def checkout(plan):
    """Create Stripe Checkout session.
    
    No login required. Stripe collects the email during checkout.
    The webhook auto-creates the user account and activates premium.
    If the user is already logged in, we pre-fill their email.
    Uses Checkout Sessions when STRIPE_PRICE_* is set; otherwise (or on
    Session create failure) falls back to the plan Payment Link.
    """
    plan = (plan or '').strip().lower()
    if plan not in VALID_CHECKOUT_PLANS:
        return "Invalid plan. Use monthly, yearly, or weekly.", 400

    payment_link = _payment_link_for_plan(plan)
    price_id = _stripe_price_for_plan(plan)
    if not price_id:
        if payment_link:
            logger.warning(
                "[checkout] STRIPE_PRICE_%s unset; falling back to Payment Link",
                plan.upper(),
            )
            return redirect(payment_link)
        if not STRIPE_SECRET_KEY:
            return "Stripe not configured. Set STRIPE_SECRET_KEY.", 500
        return f"Stripe price not configured for {plan}.", 500

    if not STRIPE_SECRET_KEY:
        if payment_link:
            return redirect(payment_link)
        return "Stripe not configured. Set STRIPE_SECRET_KEY.", 500

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    try:
        session_kwargs = {
            'payment_method_types': ['card'],
            'line_items': [{'price': price_id, 'quantity': 1}],
            'mode': 'subscription',
            'success_url': request.url_root.rstrip('/') + '/checkout/success?session_id={CHECKOUT_SESSION_ID}',
            'cancel_url': request.url_root.rstrip('/') + '/plans',
            'metadata': {'plan': plan},
            'subscription_data': {'metadata': {'plan': plan}},
        }
        # Pre-fill email if logged in
        if current_user.is_authenticated:
            session_kwargs['customer_email'] = current_user.email
            session_kwargs['metadata']['user_id'] = str(current_user.id)

        checkout_session = stripe.checkout.Session.create(**session_kwargs)
        return redirect(checkout_session.url)
    except Exception as e:
        logger.error(f"Stripe checkout error: {e}")
        if payment_link:
            logger.warning(
                "[checkout] %s Checkout Session failed; falling back to Payment Link",
                plan.capitalize(),
            )
            return redirect(payment_link)
        return f"Payment error: {e}", 500


def _is_real_checkout_session_id(session_id: str | None) -> bool:
    """True only for a real Stripe Checkout Session id (cs_...).

    Stripe substitutes `{CHECKOUT_SESSION_ID}` in success_url at redirect time.
    Never call Session.retrieve with the literal placeholder or other junk —
    that becomes GET /v1/checkout/sessions/%7BCHECKOUT_SESSION_ID%7D and
    resource_missing in Stripe logs.
    """
    if not session_id:
        return False
    from urllib.parse import unquote

    sid = unquote(str(session_id)).strip()
    if not sid or "CHECKOUT_SESSION_ID" in sid:
        return False
    # Live/test Checkout Session ids always start with cs_
    return sid.startswith("cs_") and len(sid) >= 10


@auth_bp.route('/checkout/success')
def checkout_success():
    """Handle successful Stripe checkout.

    Verifies the Stripe session, auto-creates account if needed,
    logs the user in, and activates premium — then redirects to the homepage.
    Guests without a password are sent to /set-password with a one-time token.
    Stripe success_url must keep pointing here with session_id.
    """
    session_id = (request.args.get('session_id') or '').strip()
    if not _is_real_checkout_session_id(session_id):
        logger.warning(
            "[checkout/success] missing/invalid session_id=%r — skipping Stripe retrieve",
            session_id or None,
        )
        return redirect('/plans')

    if not STRIPE_SECRET_KEY:
        logger.warning("[checkout/success] STRIPE_SECRET_KEY missing")
        return redirect(url_for('auth.login_page', error='checkout_failed'))

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        cs = stripe.checkout.Session.retrieve(session_id)
        payment_status = _obj_get(cs, 'payment_status')
        if not _checkout_payment_ok(payment_status, _obj_get(cs, 'status')):
            logger.warning(
                "[checkout/success] session %s not paid (status=%s payment_status=%s)",
                session_id, _obj_get(cs, 'status'), payment_status,
            )
            return redirect('/plans')

        details = _obj_get(cs, 'customer_details') or {}
        email = _normalize_email(
            (_obj_get(details, 'email') if details else None)
            or _obj_get(cs, 'customer_email')
        )
        plan = _plan_from_checkout_or_subscription(cs)
        customer_id = _stripe_id(_obj_get(cs, 'customer'))
        subscription_id = _stripe_id(_obj_get(cs, 'subscription'))
        period_end = None
        if subscription_id:
            try:
                sub_obj = stripe.Subscription.retrieve(subscription_id)
                period_end = _period_end_from_subscription(sub_obj)
                plan = _plan_from_checkout_or_subscription(cs, sub_obj)
            except Exception as se:
                logger.warning("[checkout/success] sub retrieve failed: %s", se)

        meta = _obj_get(cs, 'metadata') or {}
        granted = _grant_premium_to_payer(
            customer_id=customer_id,
            email=email,
            client_reference_id=_obj_get(cs, 'client_reference_id'),
            metadata=meta,
            stripe_mod=stripe,
            plan=plan,
            subscription_id=subscription_id,
            premium_expires=period_end,
            context='checkout/success',
            event_type='checkout/success',
            payment_status=payment_status,
            send_claim_email=False,
        )
        if not granted:
            logger.error(
                "[checkout/success] activation failed session=%s customer=%s email=%s",
                session_id, customer_id, email,
            )
            return redirect(url_for('auth.login_page', error='checkout_failed'))

        user_id, email = _resolve_user_for_stripe_payer(
            customer_id=customer_id,
            email=email,
            client_reference_id=_obj_get(cs, 'client_reference_id'),
            metadata=meta,
            stripe_mod=stripe,
        )
        # Reload so premium_active reflects DB
        user = _load_user_by_id(user_id) if user_id else None
        if not user:
            return redirect(url_for('auth.login_page', error='checkout_failed'))
        login_user(user, remember=True)
        _set_session_token(user.id)

        if not _user_has_password(user.id):
            claim_token = _ensure_claim_token_for_user(user.id, force_new=True)
            if claim_token:
                _maybe_send_claim_email(
                    email or user.email, claim_token,
                    base_url=request.url_root.rstrip('/'),
                )
                return redirect(
                    url_for('auth.set_password_page', token=claim_token)
                )
            return redirect(url_for('auth.set_password_page'))

        return redirect('/')
    except Exception as e:
        logger.warning(f"[checkout/success] Stripe verification failed: {e}")
        return redirect(url_for('auth.login_page', error='checkout_failed'))


@auth_bp.route('/set-password', methods=['GET', 'POST'])
@auth_bp.route('/claim-account', methods=['GET', 'POST'])
def set_password_page():
    """One-time set-password / claim-account after guest checkout.

    Accepts a token query/form param, or a logged-in user with no password_hash.
    """
    token = (request.values.get('token') or '').strip()
    error_msg = ''
    email = ''
    user_id = None

    if token:
        user_id, email = _lookup_set_password_token(token)
    elif current_user.is_authenticated and not _user_has_password(current_user.id):
        user_id = current_user.id
        email = current_user.email

    can_set = bool(user_id)

    if request.method == 'GET':
        if not can_set:
            error_msg = (
                'This set-password link is invalid or has expired. '
                'If you just paid, check your email for the set-password link or contact support.'
            )
        return render_template_string(
            SET_PASSWORD_TEMPLATE,
            page='set_password',
            token=token,
            email=email or '',
            error_msg=error_msg,
            premium_active=can_set,
        )

    # POST
    password = request.form.get('password', '')
    confirm = request.form.get('confirm', '')
    if not can_set:
        error_msg = 'This set-password link is invalid or has expired.'
    elif not password or len(password) < 6:
        error_msg = 'Password must be at least 6 characters.'
    elif password != confirm:
        error_msg = 'Passwords do not match.'
    else:
        user = None
        if token:
            user = _consume_set_password_token(token, password)
        elif user_id:
            # Session-authenticated claim (no token): set password directly
            try:
                pw_hash = generate_password_hash(password)
                conn = _get_db()
                conn.execute(
                    'UPDATE users SET password_hash = ? WHERE id = ?',
                    (pw_hash, user_id)
                )
                conn.commit()
                conn.close()
                user = _load_user_by_id(user_id)
            except Exception as e:
                logger.error(f"Session claim set-password failed: {e}")
                user = None
        if user:
            login_user(user, remember=True)
            _set_session_token(user.id)
            return redirect('/')
        error_msg = 'Could not set password. The link may have already been used.'

    return render_template_string(
        SET_PASSWORD_TEMPLATE,
        page='set_password',
        token=token,
        email=email or '',
        error_msg=error_msg,
        premium_active=can_set,
    )


def _parse_stripe_webhook_event(payload, sig, webhook_secret):
    """Verify signature and parse Snapshot webhook events.

    Thin (v2.core.event) payloads are detected after signature verify and returned
    as (None, 'thin', data) so the route can ack 200 without crashing.
    Returns (event_or_none, kind, raw_data) where kind is 'snapshot'|'thin'|'error'.
    """
    import json
    import stripe

    raw = payload.decode('utf-8') if hasattr(payload, 'decode') else payload
    try:
        stripe.WebhookSignature.verify_header(raw, sig, webhook_secret)
    except Exception as e:
        return None, 'error', str(e)

    try:
        data = json.loads(raw)
    except Exception as e:
        return None, 'error', f'invalid json: {e}'

    obj_type = data.get('object') if isinstance(data, dict) else None
    if obj_type == 'v2.core.event':
        return None, 'thin', data

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        return event, 'snapshot', data
    except ValueError as e:
        # construct_event raises ValueError for thin after verify — belt and suspenders
        if 'thin event' in str(e).lower():
            return None, 'thin', data
        return None, 'error', str(e)
    except Exception as e:
        return None, 'error', str(e)


def _handle_checkout_session_completed(
    session_data, stripe_mod=None, event_id=None,
):
    """Activate premium from checkout.session.completed.

    Returns True when handled successfully (premium applied OR intentional
    unpaid skip). Returns False only for activation misses that need Resend.
    """
    customer_id = _stripe_id(_obj_get(session_data, 'customer'))
    subscription_id = _stripe_id(_obj_get(session_data, 'subscription'))
    details = _obj_get(session_data, 'customer_details') or {}
    email = _normalize_email(
        (_obj_get(details, 'email') if details else None)
        or _obj_get(session_data, 'customer_email')
    )
    meta = _obj_get(session_data, 'metadata') or {}
    client_ref = _obj_get(session_data, 'client_reference_id')

    # $0 / coupon sessions: payment_status may be no_payment_required — still grant.
    payment_status = _obj_get(session_data, 'payment_status')
    if payment_status and not _checkout_payment_ok(payment_status):
        # Intentional skip (not paid) — mark processed so Stripe does not Resend forever.
        logger.info(
            "[stripe] checkout.session.completed intentional skip unpaid "
            "status=%s customer=%s event_id=%s",
            payment_status, customer_id, event_id,
        )
        return True

    period_end = None
    sub_obj = None
    if subscription_id and stripe_mod is not None:
        try:
            sub_obj = stripe_mod.Subscription.retrieve(subscription_id)
            period_end = _period_end_from_subscription(sub_obj)
        except Exception as se:
            logger.warning("[stripe] Could not retrieve subscription %s: %s", subscription_id, se)

    plan = _plan_from_checkout_or_subscription(session_data, sub_obj)

    # Shared grant path — session email / client_reference_id matter for Buy Button
    # and Payment Links (weekly/monthly/yearly) that omit linked stripe_customer_id.
    detail_name = _obj_get(details, 'name') if details else None
    granted = _grant_premium_to_payer(
        customer_id=customer_id,
        email=email,
        name=detail_name,
        client_reference_id=client_ref,
        metadata=meta,
        stripe_mod=stripe_mod,
        plan=plan,
        subscription_id=subscription_id,
        premium_expires=period_end,
        context='checkout.session.completed',
        event_id=event_id,
        event_type='checkout.session.completed',
        payment_status=payment_status or 'paid',
        send_claim_email=True,
    )
    if granted:
        # Initial checkout only — renewals never emit checkout.session.completed.
        # Email failure must not affect granted / webhook HTTP status.
        try:
            user_id, resolved_email = _resolve_user_for_stripe_payer(
                customer_id=customer_id,
                email=email,
                name=detail_name,
                client_reference_id=client_ref,
                metadata=meta,
                stripe_mod=stripe_mod,
            )
            _welcome_after_premium_grant(
                user_id=user_id,
                email=resolved_email or email,
                name=detail_name,
                plan=plan,
                subscription_id=subscription_id,
                event_id=event_id,
                is_initial_subscribe=True,
            )
        except Exception as we:
            logger.warning(
                "[welcome] checkout.session.completed hook failed (non-fatal): %s", we
            )
    return granted


def _handle_invoice_payment_succeeded(
    invoice, stripe_mod=None, event_id=None, event_type=None,
):
    """Extend premium on successful invoice (initial + renewals). Never raises.

    Tolerates Stripe API 2026-06-24.dahlia invoices that omit top-level
    ``subscription`` / ``lines[].price`` in favor of parent.subscription_details
    and lines[].pricing.price_details.price.

    ``amount_paid=0`` (100% coupon) is still a successful paid invoice — do not
    skip. Handles both ``invoice.paid`` and ``invoice.payment_succeeded``.
    Returns True when premium was applied/synced for a local user.
    """
    etype = event_type or 'invoice.payment_succeeded'
    try:
        customer_id = _stripe_id(_obj_get(invoice, 'customer'))
        subscription_id = _subscription_id_from_invoice(invoice)
        period_end = _iso_from_unix(_obj_get(invoice, 'period_end'))
        customer_email = _normalize_email(_obj_get(invoice, 'customer_email'))
        billing_reason = (_obj_get(invoice, 'billing_reason') or '').strip()
        # amount_paid may be 0 for 100% off — still activate (do not gate on it)
        amount_paid = _obj_get(invoice, 'amount_paid')
        logger.info(
            "[stripe] invoice payment event_id=%s type=%s customer=%s email=%s "
            "sub=%s amount_paid=%s billing_reason=%s",
            event_id, etype, customer_id, customer_email, subscription_id,
            amount_paid, billing_reason or None,
        )

        # Welcome only on the first invoice of a subscription — never renewals.
        # Missing billing_reason: do not welcome here (checkout.session.completed /
        # customer.subscription.created cover first-time; empty reason on cycle
        # invoices must not re-welcome).
        is_initial_invoice = billing_reason == 'subscription_create'

        applied = False
        plan = (
            _plan_from_price_id(_price_id_from_invoice(invoice))
            or _plan_from_invoice_interval(invoice)
            or 'monthly'
        )

        if subscription_id and stripe_mod is not None:
            try:
                sub_obj = stripe_mod.Subscription.retrieve(subscription_id)
                plan = _normalize_plan(
                    _plan_from_checkout_or_subscription(None, sub_obj) or plan
                )
                if _sync_premium_from_subscription(
                    sub_obj, stripe_mod=stripe_mod,
                    event_id=event_id, event_type=etype,
                    fallback_email=customer_email,
                ):
                    applied = True
            except Exception as e:
                logger.error(
                    "[stripe] %s retrieve/sync failed sub=%s event_id=%s: %s",
                    etype, subscription_id, event_id, e,
                )

        if not applied:
            applied = bool(_grant_premium_to_payer(
                customer_id=customer_id,
                email=customer_email,
                stripe_mod=stripe_mod,
                plan=plan,
                subscription_id=subscription_id,
                premium_expires=period_end,
                context=f'{etype}_fallback',
                event_id=event_id,
                event_type=etype,
                payment_status='paid',
                send_claim_email=False,
            ))

        if applied and is_initial_invoice:
            try:
                user_id, resolved_email = _resolve_user_for_stripe_payer(
                    customer_id=customer_id,
                    email=customer_email,
                    stripe_mod=stripe_mod,
                )
                _welcome_after_premium_grant(
                    user_id=user_id,
                    email=resolved_email or customer_email,
                    plan=plan,
                    subscription_id=subscription_id,
                    event_id=event_id,
                    is_initial_subscribe=True,
                )
            except Exception as we:
                logger.warning(
                    "[welcome] %s hook failed (non-fatal): %s", etype, we
                )
        elif applied and not is_initial_invoice:
            logger.info(
                "[welcome] skipped not_initial invoice billing_reason=%s sub=%s",
                billing_reason or None, subscription_id,
            )

        return applied
    except Exception as e:
        logger.exception(
            "[stripe] %s handler error event_id=%s (non-fatal): %s",
            etype, event_id, e,
        )
        return False


@auth_bp.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events (Snapshot payloads).

    HTTP policy (intentional):
    - Invalid signature / missing secrets → non-200 (400) so Stripe retries auth.
    - After a valid signature: always return 200 for business-logic outcomes
      (including activation misses) so renewals do not stick in retry loops.
    - Activation no-ops are NOT marked processed → Dashboard Resend can recover
      after a code/config fix without relying on Stripe's automatic retries.
    - Thin (v2.core.event) payloads ack 200 without applying; Dashboard must use
      Snapshot / full payloads for premium sync.
    """
    try:
        if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
            logger.error(
                "[stripe] webhook missing STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRET"
            )
            return '', 400

        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        payload = request.get_data()
        sig = request.headers.get('Stripe-Signature', '')

        event, kind, extra = _parse_stripe_webhook_event(
            payload, sig, STRIPE_WEBHOOK_SECRET
        )
        if kind == 'error':
            logger.error("[stripe] webhook signature/parse error: %s", extra)
            return '', 400

        if kind == 'thin':
            thin_id = (extra or {}).get('id') if isinstance(extra, dict) else None
            thin_type = (extra or {}).get('type') if isinstance(extra, dict) else None
            logger.error(
                "[stripe] Thin webhook payload received (id=%s type=%s object=v2.core.event). "
                "Premium sync requires Snapshot (full) event payloads — set the Stripe "
                "Dashboard webhook to Snapshot. Acknowledging 200 without applying premium.",
                thin_id, thin_type,
            )
            return '', 200

        try:
            etype = (
                event['type'] if hasattr(event, '__getitem__')
                else _obj_get(event, 'type')
            )
            event_id = _stripe_id(_obj_get(event, 'id')) or (
                event.get('id') if isinstance(event, dict) else None
            )
            data_obj = (
                event['data']['object'] if hasattr(event, '__getitem__')
                else _obj_get(_obj_get(event, 'data'), 'object')
            )

            # Activation events: only mark idempotent when we actually applied
            # premium (or deactivated / intentional unpaid skip). Silent no-ops
            # stay unmarked so Stripe Dashboard "Resend" can retry after a fix.
            # Also: allow Resend of previously-marked activation events —
            # older builds marked no-ops as processed, which blocked recovery.
            _ACTIVATION_TYPES = frozenset({
                'checkout.session.completed',
                'invoice.payment_succeeded',
                'invoice.paid',
                'customer.subscription.created',
                'customer.subscription.updated',
                'customer.subscription.deleted',
            })

            if event_id and _webhook_event_already_processed(event_id):
                if etype not in _ACTIVATION_TYPES:
                    logger.info(
                        "[stripe] Ignoring duplicate event_id=%s type=%s",
                        event_id, etype,
                    )
                    return '', 200
                logger.info(
                    "[stripe] Re-processing marked activation event_id=%s type=%s",
                    event_id, etype,
                )

            logger.info("[stripe] webhook event_id=%s type=%s", event_id, etype)

            applied = True  # non-activation types: mark processed

            if etype == 'checkout.session.completed':
                applied = bool(
                    _handle_checkout_session_completed(
                        data_obj, stripe_mod=stripe, event_id=event_id,
                    )
                )

            elif etype in ('invoice.payment_succeeded', 'invoice.paid'):
                applied = bool(
                    _handle_invoice_payment_succeeded(
                        data_obj, stripe_mod=stripe,
                        event_id=event_id, event_type=etype,
                    )
                )

            elif etype in (
                'customer.subscription.created',
                'customer.subscription.updated',
            ):
                applied = bool(
                    _sync_premium_from_subscription(
                        data_obj, stripe_mod=stripe,
                        event_id=event_id, event_type=etype,
                    )
                )
                # Welcome on brand-new subscription only (not updates / renewals).
                # Idempotent by subscription_id if checkout/invoice already welcomed.
                if applied and etype == 'customer.subscription.created':
                    try:
                        sub_id = _stripe_id(_obj_get(data_obj, 'id'))
                        cust_id = _stripe_id(_obj_get(data_obj, 'customer'))
                        plan = _plan_from_checkout_or_subscription(None, data_obj)
                        user_id, resolved_email = _resolve_user_for_stripe_payer(
                            customer_id=cust_id,
                            metadata=_obj_get(data_obj, 'metadata'),
                            stripe_mod=stripe,
                        )
                        _welcome_after_premium_grant(
                            user_id=user_id,
                            email=resolved_email,
                            plan=plan,
                            subscription_id=sub_id,
                            event_id=event_id,
                            is_initial_subscribe=True,
                        )
                    except Exception as we:
                        logger.warning(
                            "[welcome] subscription.created hook failed (non-fatal): %s",
                            we,
                        )
                elif etype == 'customer.subscription.updated':
                    logger.info(
                        "[welcome] skipped not_initial subscription.updated sub=%s",
                        _stripe_id(_obj_get(data_obj, 'id')),
                    )

            elif etype == 'customer.subscription.deleted':
                customer_id = _stripe_id(_obj_get(data_obj, 'customer'))
                if customer_id:
                    _deactivate_premium_by_customer(customer_id)
                    _log_premium_decision(
                        'customer.subscription.deleted',
                        customer_id=customer_id, is_premium=0,
                        subscription_id=_stripe_id(_obj_get(data_obj, 'id')),
                        status='canceled',
                    )
                    applied = True
                else:
                    applied = False

            else:
                logger.info("[stripe] Unhandled event type=%s (acked)", etype)
                applied = True

            if applied or etype not in _ACTIVATION_TYPES:
                _mark_webhook_event_processed(event_id, etype)
            else:
                logger.error(
                    "[stripe] event_id=%s type=%s acked 200 but premium not "
                    "applied — leaving unmarked for Dashboard Resend",
                    event_id, etype,
                )
        except Exception as e:
            # Do not 500 — log and ack so Stripe renewals do not loop on permanent bugs.
            # Operator can Resend after fix; idempotency skips already-marked successes.
            logger.exception(
                "[stripe] webhook handler error (returning 200): %s", e
            )
            return '', 200

        return '', 200
    except Exception as e:
        # Absolute last resort: never 500 the Stripe endpoint.
        logger.exception("[stripe] webhook outer error (returning 200): %s", e)
        return '', 200


def _activate_premium(user_id, plan='monthly', stripe_customer_id=None,
                      stripe_subscription_id=None, premium_expires=None):
    """Activate premium for a user. Returns True if DB write succeeded.

    Prefer explicit ``premium_expires`` (e.g. Stripe current_period_end).
    Otherwise: weekly +7, yearly +365, monthly +31.
    """
    if not user_id:
        return False
    plan = _normalize_plan(plan)

    customer_id = _stripe_id(stripe_customer_id)
    subscription_id = _stripe_id(stripe_subscription_id)

    if not premium_expires:
        if plan == 'yearly':
            premium_expires = (datetime.now() + timedelta(days=365)).isoformat()
        elif plan == 'weekly':
            premium_expires = (datetime.now() + timedelta(days=7)).isoformat()
        else:
            premium_expires = (datetime.now() + timedelta(days=31)).isoformat()

    try:
        conn = _get_db()
        try:
            conn.execute(
                '''UPDATE users SET
                       is_premium = 1,
                       premium_expires = ?,
                       stripe_customer_id = COALESCE(?, stripe_customer_id),
                       stripe_subscription_id = COALESCE(?, stripe_subscription_id)
                   WHERE id = ?''',
                (premium_expires, customer_id, subscription_id, user_id)
            )
        except sqlite3.OperationalError:
            if customer_id:
                conn.execute(
                    'UPDATE users SET is_premium = 1, premium_expires = ?, stripe_customer_id = ? WHERE id = ?',
                    (premium_expires, customer_id, user_id)
                )
            else:
                conn.execute(
                    'UPDATE users SET is_premium = 1, premium_expires = ? WHERE id = ?',
                    (premium_expires, user_id)
                )
        conn.commit()
        conn.close()
        _log_premium_decision(
            'activate_premium',
            user_id=user_id, customer_id=customer_id,
            subscription_id=subscription_id, plan=plan,
            is_premium=1, expires=premium_expires,
        )
        return True
    except Exception as e:
        logger.error("[stripe] _activate_premium failed user_id=%s: %s", user_id, e)
        return False


def _deactivate_premium_by_customer(stripe_customer_id):
    """Deactivate premium when subscription is cancelled."""
    customer_id = _stripe_id(stripe_customer_id)
    if not customer_id:
        return
    try:
        conn = _get_db()
        conn.execute(
            'UPDATE users SET is_premium = 0 WHERE stripe_customer_id = ?',
            (customer_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("[stripe] deactivate failed customer=%s: %s", customer_id, e)


# ─── Helper: check premium in views ──────────────────────────────────────────

def is_premium_user():
    """Check if current request is from a premium user."""
    if not current_user.is_authenticated:
        return False
    active = current_user.premium_active
    try:
        logger.debug(
            "[auth] is_premium_user user_id=%s email=%s decision=%s expires=%s",
            getattr(current_user, 'id', None),
            getattr(current_user, 'email', None),
            active,
            getattr(current_user, 'premium_expires', None),
        )
    except Exception:
        pass
    return active


# ─── Templates ────────────────────────────────────────────────────────────────

_AUTH_STYLES = """
<style>
    .auth-container { max-width: 420px; margin: 60px auto; padding: 40px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12); border-radius: 16px; }
    .auth-title { font-size: 1.8em; text-align: center; margin-bottom: 24px; color: #fbbf24; }
    .auth-form input { width: 100%; padding: 12px 16px; margin-bottom: 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.08); color: white; font-size: 1em; box-sizing: border-box; }
    .auth-form input::placeholder { color: #cbd5e1; }
    .auth-form input:focus { outline: none; border-color: #fbbf24; }
    .auth-btn { width: 100%; padding: 14px; border: none; border-radius: 8px; font-size: 1.05em; font-weight: 700; cursor: pointer; transition: all 0.2s; }
    .auth-btn-primary { background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #000; }
    .auth-btn-primary:hover { opacity: 0.9; }
    .auth-btn-google { background: white; color: #333; margin-bottom: 14px; display: flex; align-items: center; justify-content: center; gap: 10px; }
    .auth-btn-google:hover { background: #f3f4f6; }
    .auth-divider { text-align: center; margin: 18px 0; color: #cbd5e1; font-size: 0.85em; position: relative; }
    .auth-divider::before, .auth-divider::after { content: ''; position: absolute; top: 50%; width: 40%; height: 1px; background: rgba(255,255,255,0.15); }
    .auth-divider::before { left: 0; }
    .auth-divider::after { right: 0; }
    .auth-link { text-align: center; margin-top: 18px; color: #cbd5e1; font-size: 0.9em; }
    .auth-link a { color: #93c5fd; text-decoration: none; font-weight: 600; }
    .auth-link a:hover { text-decoration: underline; }
    .auth-error { background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.4); color: #fca5a5; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; font-size: 0.9em; }
</style>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login — predictionlab.io</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-R4XM0WKTGG"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","G-R4XM0WKTGG");</script>
<meta name="description" content="Log in to your predictionlab.io account to access AI-powered sports picks, spreads, and totals.">
<meta property="og:title" content="Login — predictionlab.io">
<meta property="og:description" content="Log in to access AI-powered sports predictions and forecasts.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://predictionlab.io/login">
<link rel="canonical" href="https://predictionlab.io/login">
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:white;min-height:100vh;}body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.82);z-index:0;}body>*{position:relative;z-index:1;}</style>
""" + _AUTH_STYLES + """
</head><body>
<div class="auth-container">
    <h1 style="position:absolute;left:-9999px;">Log in to predictionlab.io AI sports picks platform</h1>
    <div class="auth-title">🔐 Login</div>
    {% if error_msg %}<div class="auth-error">{{ error_msg }}</div>{% endif %}
    {% if google_enabled %}
    <a href="/auth/google" class="auth-btn auth-btn-google" style="text-decoration:none;">
        <svg width="20" height="20" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
        Continue with Google
    </a>
    <div class="auth-divider">or</div>
    {% endif %}
    <form class="auth-form" method="POST" action="/login">
        <input type="email" name="email" placeholder="Email address" required>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit" class="auth-btn auth-btn-primary">Log In</button>
    </form>
    <div class="auth-link">Don't have an account? <a href="/signup">Sign up</a></div>
    <div class="auth-link" style="margin-top:10px;"><a href="/">← Back to Home</a></div>
</div>
</body></html>
"""

SIGNUP_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sign Up — predictionlab.io</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-R4XM0WKTGG"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","G-R4XM0WKTGG");</script>
<meta name="description" content="Create a free predictionlab.io account to access AI-powered sports picks. Upgrade for spreads, totals, and score predictions.">
<meta property="og:title" content="Sign Up — predictionlab.io">
<meta property="og:description" content="Create a free account for AI-powered sports predictions and forecasts.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://predictionlab.io/signup">
<link rel="canonical" href="https://predictionlab.io/signup">
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:white;min-height:100vh;}body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.82);z-index:0;}body>*{position:relative;z-index:1;}</style>
""" + _AUTH_STYLES + """
</head><body>
<div class="auth-container">
    <h1 style="position:absolute;left:-9999px;">Sign up for predictionlab.io AI sports picks access</h1>
    <div class="auth-title">Create Account</div>
    <p style="text-align:center;color:#cbd5e1;font-size:0.85em;margin-bottom:18px;">Sign up to access free picks. Upgrade anytime for Spreads, Totals &amp; Score Predictions.</p>
    {% if google_enabled %}
    <a href="/auth/google" class="auth-btn auth-btn-google" style="text-decoration:none;">
        <svg width="20" height="20" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
        Sign up with Google
    </a>
    <div class="auth-divider">or</div>
    {% endif %}
    <form class="auth-form" method="POST" action="/signup">
        <input type="email" name="email" placeholder="Email address" required>
        <input type="password" name="password" placeholder="Password" required>
        <input type="password" name="confirm" placeholder="Confirm password" required>
        <button type="submit" class="auth-btn auth-btn-primary">Create Account</button>
    </form>
    <div class="auth-link">Already have an account? <a href="/login">Log in</a></div>
    <div class="auth-link" style="margin-top:10px;"><a href="/">← Back to Home</a></div>
</div>
</body></html>
"""

SET_PASSWORD_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Set Password — predictionlab.io</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-R4XM0WKTGG"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","G-R4XM0WKTGG");</script>
<meta name="robots" content="noindex, nofollow">
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:white;min-height:100vh;}body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.82);z-index:0;}body>*{position:relative;z-index:1;}</style>
""" + _AUTH_STYLES + """
</head><body>
<div class="auth-container">
    <div class="auth-title">Set your password</div>
    {% if premium_active %}
    <p style="text-align:center;color:#86efac;font-size:0.9em;margin-bottom:14px;font-weight:600;">✓ Premium is active{% if email %} for {{ email }}{% endif %}</p>
    {% endif %}
    <p style="text-align:center;color:#cbd5e1;font-size:0.88em;margin-bottom:18px;line-height:1.5;">Choose a password so you can log in anytime. This link works once.</p>
    {% if error_msg %}<div class="auth-error">{{ error_msg }}</div>{% endif %}
    {% if premium_active %}
    <form class="auth-form" method="POST" action="/set-password">
        {% if token %}<input type="hidden" name="token" value="{{ token }}">{% endif %}
        {% if email %}<input type="email" value="{{ email }}" disabled style="opacity:0.7;">{% endif %}
        <input type="password" name="password" placeholder="New password (min 6 characters)" required minlength="6" autocomplete="new-password">
        <input type="password" name="confirm" placeholder="Confirm password" required minlength="6" autocomplete="new-password">
        <button type="submit" class="auth-btn auth-btn-primary">Save password &amp; continue</button>
    </form>
    {% else %}
    <div class="auth-link" style="margin-top:8px;"><a href="/login">Go to login</a> · <a href="/plans">Plans</a></div>
    {% endif %}
    <div class="auth-link" style="margin-top:14px;"><a href="/">← Back to Home</a></div>
</div>
</body></html>
"""


# ─── Plans Page ───────────────────────────────────────────────────────────────

# Plans page uses render_template so it gets the base template navbar
PLANS_USES_BASE_TEMPLATE = True


@auth_bp.route('/plans')
def plans_page():
    # Import BASE_TEMPLATE from main app to get consistent navbar
    try:
        from NHL77FINAL import BASE_TEMPLATE
        plans_content = BASE_TEMPLATE.replace(
            '{% block extra_styles %}{% endblock %}',
            """
            .plans-wrap{max-width:1080px;margin:0 auto;padding:4px 0 40px;}
            .plans-hero{background:#ffffff;border:1px solid #E0E4E8;border-radius:16px;padding:36px 22px 28px;text-align:center;box-shadow:0 4px 20px rgba(15,23,42,0.06);margin-bottom:24px;}
            .plans-hero-logo{font-size:1.65em;font-weight:900;color:#0f172a;letter-spacing:0.3px;line-height:1.35;margin-top:10px;}
            .plans-hero-sub{font-size:1.05em;color:#475569;margin-top:12px;max-width:650px;margin-left:auto;margin-right:auto;line-height:1.75;}
            .plans-hero-stats{display:flex;justify-content:center;gap:12px;margin-top:18px;flex-wrap:wrap;}
            .stat-pill{background:#F4F7F9;border:1px solid #E0E4E8;border-radius:24px;padding:8px 16px;font-size:0.82em;font-weight:600;color:#334155;}
            .competitor-bar{background:#f8fafc;border:1px solid #E0E4E8;border-radius:10px;max-width:560px;margin:18px auto 0;padding:12px 18px;font-size:0.88em;color:#475569;}
            .competitor-bar strong{color:#0f172a;}
            .plans-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:22px;}
            .plan-card{background:#ffffff;color:#1e293b;border-radius:18px;padding:34px 26px;text-align:center;transition:all 0.25s;box-shadow:0 4px 18px rgba(15,23,42,0.08);border:1px solid #E0E4E8;position:relative;}
            .plan-card:hover{transform:translateY(-4px);box-shadow:0 10px 28px rgba(15,23,42,0.12);}
            .plan-card.popular{border:2px solid #00529B;}
            #weekly{scroll-margin-top:88px;}
            .plan-badge{position:absolute;top:-14px;left:50%;transform:translateX(-50%);background:#00529B;color:#fff;padding:5px 20px;border-radius:20px;font-size:0.78em;font-weight:800;}
            .plan-name{font-size:1.35em;font-weight:800;margin-bottom:6px;color:#0f172a;}
            .plan-old-price{font-size:1.05em;color:#475569;text-decoration:line-through;margin-bottom:2px;}
            .plan-price{font-size:3em;font-weight:900;color:#0f172a;margin-bottom:2px;}
            .plan-price span{font-size:0.3em;opacity:0.55;font-weight:500;}
            .plan-save{color:#059669;font-size:0.88em;margin-bottom:20px;font-weight:700;}
            .plan-features{text-align:left;margin-bottom:26px;padding:0;}
            .plan-features li{padding:7px 0;font-size:0.9em;list-style:none;color:#334155;border-bottom:1px solid #f1f5f9;}
            .plan-features li:last-child{border-bottom:none;}
            .plan-features li::before{content:none;}
            .plan-btn{display:block;width:100%;padding:15px;border:none;border-radius:10px;font-size:1.05em;font-weight:800;cursor:pointer;text-decoration:none;text-align:center;transition:all 0.2s;}
            .plan-btn-primary{background:#00529B;color:#fff;box-shadow:0 4px 14px rgba(0,82,155,0.25);}
            .plan-btn-primary:hover{transform:translateY(-2px);background:#003d73;}
            .plan-btn-secondary{background:#0f172a;color:#fff;}
            .plan-btn-secondary:hover{background:#1e293b;}
            .free-section{margin-top:32px;text-align:center;padding:26px 20px;background:#ffffff;border:1px solid #E0E4E8;border-radius:14px;}
            .free-section .free-head{font-size:1.2em;margin-bottom:8px;font-weight:800;color:#0f172a;}
            .free-section .free-copy{margin-bottom:14px;color:#475569;font-size:0.95em;line-height:1.55;}
            .free-features{display:flex;justify-content:center;gap:12px;margin-top:12px;flex-wrap:wrap;}
            .free-pill{background:#F4F7F9;border:1px solid #E0E4E8;border-radius:20px;padding:7px 16px;font-size:0.82em;color:#334155;font-weight:600;}
            .plans-why-premium{max-width:920px;margin:28px auto 0;padding:28px 24px;background:#ffffff;border:1px solid #E0E4E8;border-radius:14px;}
            .plans-why-title{font-size:1.35em;font-weight:900;color:#0f172a;margin:0 0 12px;text-align:center;}
            .plans-why-lead{color:#475569;font-size:1em;line-height:1.75;margin:0 auto 16px;text-align:center;max-width:720px;}
            .plans-why-list{margin:0 auto 18px;padding-left:22px;max-width:640px;color:#334155;line-height:1.7;font-size:0.95em;}
            .plans-why-list li{margin-bottom:8px;}
            .plans-why-foot{margin:0 auto;text-align:center;color:#475569;font-size:0.92em;line-height:1.65;max-width:680px;}
            .plans-why-foot a{color:#00529B;font-weight:700;text-decoration:none;}
            .plans-why-foot a:hover{text-decoration:underline;}
            @media(max-width:900px){.plans-grid{grid-template-columns:1fr 1fr;}}
            @media(max-width:640px){.plans-grid{grid-template-columns:1fr;}.plans-hero-stats{flex-direction:column;align-items:center;gap:10px;}}
            """
        ).replace('{% block content %}{% endblock %}', """
            <div class="plans-wrap">
            <div class="plans-hero">
                <h1 style="font-size:2em;font-weight:900;color:#0f172a;line-height:1.25;margin-bottom:8px;">AI Sports Betting Pricing Plans for Spreads, Totals and Score Predictions</h1>
                <div class="plans-hero-logo">Built to Beat the Public &mdash; Not Follow It.</div>
                <div class="plans-hero-sub">Data-driven spreads, totals, and score projections &mdash; tracked, transparent, and built for real edges.</div>
                <div class="plans-hero-stats">
                    <div class="stat-pill">Full Spread &amp; Total Coverage</div>
                    <div class="stat-pill">Projected Scores for Every Game</div>
                    <div class="stat-pill">Find Value the Public Misses</div>
                </div>
                <div class="plans-hero-stats" style="margin-top:10px;">
                    <div class="stat-pill">Consistently Updated Models</div>
                    <div class="stat-pill">Transparent Results &mdash; Always</div>
                </div>
                <div class="competitor-bar">
                    Every pick is tracked. No deletes. No edits. Full transparency.
                </div>
            </div>
            <p style="text-align:center;font-size:1.12em;color:#334155;margin-bottom:26px;font-weight:700;">Free gets you the winners. Premium gets you the edge.</p>
            <div class="plans-grid">
                <div class="plan-card" id="weekly">
                    <div class="plan-name">Weekly</div>
                    <div class="plan-price">$4.99<span>/week</span></div>
                    <div class="plan-save">Try the edge. No commitment.</div>
                    <ul class="plan-features">
                        <li>Every Spread Pick (No Guessing)</li>
                        <li>Every Total Pick (Our Strongest Edge)</li>
                        <li>Projected Scores for Every Game</li>
                        <li>Full Odds Engine (ML, Spread, Total)</li>
                        <li>Player Props Picks &amp; Projections</li>
                        <li>Model Performance Calculator Access</li>
                        <li>All Sports Covered</li>
                        <li>Cancel Anytime</li>
                    </ul>
                    <a href="https://buy.stripe.com/14A6oI4Ra66ReWLczTao802" class="plan-btn plan-btn-secondary">Try This Week</a>
                </div>
                <div class="plan-card">
                    <div class="plan-name">Monthly</div>
                    <div class="plan-price">$19.99<span>/month</span></div>
                    <div class="plan-save">Flexible access. Cancel anytime.</div>
                    <ul class="plan-features">
                        <li>Every Spread Pick (No Guessing)</li>
                        <li>Every Total Pick (Our Strongest Edge)</li>
                        <li>Projected Scores for Every Game</li>
                        <li>Full Odds Engine (ML, Spread, Total)</li>
                        <li>Player Props Picks &amp; Projections</li>
                        <li>Model Performance Calculator Access</li>
                        <li>All Sports Covered</li>
                        <li>Priority Support</li>
                        <li>Cancel Anytime</li>
                    </ul>
                    <a href="https://buy.stripe.com/bJeeVe0AU1QB7uj7fzao801" class="plan-btn plan-btn-secondary">Get Monthly Access</a>
                </div>
                <div class="plan-card popular">
                    <div class="plan-badge">BEST VALUE</div>
                    <div class="plan-name">Yearly</div>
                    <div class="plan-price">$149.99<span>/year</span></div>
                    <div class="plan-save">Only $12.50/month &mdash; lock in the edge all year</div>
                    <ul class="plan-features">
                        <li>Every Spread Pick (No Guessing)</li>
                        <li>Every Total Pick (Our Strongest Edge)</li>
                        <li>Projected Scores for Every Game</li>
                        <li>Full Odds Engine (ML, Spread, Total)</li>
                        <li>Player Props Picks &amp; Projections</li>
                        <li>Model Performance Calculator Access</li>
                        <li>All Sports Covered</li>
                        <li>Priority Support</li>
                        <li>Cancel Anytime</li>
                    </ul>
                    <a href="https://buy.stripe.com/8x228s83mfHr8yneI1ao803" class="plan-btn plan-btn-primary">Get Yearly Access</a>
                </div>
            </div>
            <p style="text-align:center;font-size:0.88em;color:#475569;margin-top:18px;">Tracked results updated daily. Cancel any plan anytime. <a href="/refund-policy" style="color:#00529B;font-weight:600;text-decoration:none;">Refund policy</a></p>
            <div class="free-section">
                <p class="free-head">Start Free</p>
                <p class="free-copy">Start free. Upgrade when you're ready for the full edge.</p>
                <div class="free-features">
                    <div class="free-pill">Moneyline Picks</div>
                    <div class="free-pill">5-Model Win %</div>
                    <div class="free-pill">Full Results</div>
                    <div class="free-pill">All Sports Covered</div>
                </div>
            </div>
            <div class="plans-why-premium">
                <h2 class="plans-why-title">Why upgrade to Premium?</h2>
                <p class="plans-why-lead">Free picks already show which side our models favor. Premium is for bettors who want the <strong style="color:#0f172a;">full picture</strong>—spreads, totals, and projected scores—so you are not reverse-engineering an edge from a moneyline alone.</p>
                <ul class="plans-why-list">
                    <li><strong>Save time:</strong> projected scores and lines in one place for every slate you follow.</li>
                    <li><strong>See model agreement:</strong> where the stack lines up (or splits) before you put capital at risk.</li>
                    <li><strong>Same transparency:</strong> the same public grading you trust on free results, applied to every premium market we publish.</li>
                </ul>
                <p class="plans-why-foot">Still deciding? Read the <a href="/#faq">homepage FAQ</a>. Ready to try the edge—pick monthly or yearly above. Prefer to look around first? <a href="/signup">Create a free account</a>, then upgrade when you want spreads and totals unlocked.</p>
            </div>
            </div>
        """)
        return render_template_string(plans_content, page='plans')
    except Exception as e:
        logger.error(f"Plans page error: {e}")
        return redirect('/')
