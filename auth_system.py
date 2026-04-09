"""
Auth & Premium System for underdogs.bet
=========================================
Google OAuth + Email/Password login + Stripe subscriptions.

Usage:
    from auth_system import init_auth, login_required, premium_required, is_premium_user
    init_auth(app)  # call once after Flask app is created

Env vars needed:
    GOOGLE_CLIENT_ID        - from console.cloud.google.com
    GOOGLE_CLIENT_SECRET    - from console.cloud.google.com
    STRIPE_SECRET_KEY       - from dashboard.stripe.com/apikeys
    STRIPE_WEBHOOK_SECRET   - from Stripe webhook settings
    STRIPE_PRICE_MONTHLY    - Stripe Price ID for $9.99/mo
    STRIPE_PRICE_YEARLY     - Stripe Price ID for $99/yr
    SECRET_KEY              - Flask session secret (auto-generated if missing)
"""

import os
import sqlite3
import logging
import secrets
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
    render_template_string, jsonify, flash, g
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
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '').strip()
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '').strip()
STRIPE_PRICE_MONTHLY = os.environ.get('STRIPE_PRICE_MONTHLY', '').strip()
STRIPE_PRICE_YEARLY = os.environ.get('STRIPE_PRICE_YEARLY', '').strip()

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
            created_at TEXT DEFAULT (datetime('now'))
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
    """Load user from database by email."""
    try:
        conn = _get_db()
        row = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
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


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_auth(app, db_path=None):
    """Initialize auth system on the Flask app."""
    global _DB_PATH
    _DB_PATH = db_path or app.config.get('DATABASE', 'sports_predictions_original.db')

    # Secret key for sessions
    app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

    # Flask-Login setup
    _login_manager.init_app(app)
    _login_manager.login_view = 'auth.login_page'

    @_login_manager.user_loader
    def load_user(user_id):
        return _load_user_by_id(user_id)

    # Create users table
    _ensure_users_table()

    # Register auth blueprint
    app.register_blueprint(auth_bp)

    # Google OAuth setup (if credentials available)
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        _setup_google_oauth(app)

    # Inject is_premium into all templates
    @app.context_processor
    def inject_auth():
        return {
            'user': current_user,
            'is_premium': current_user.premium_active if current_user.is_authenticated else False,
            'is_logged_in': current_user.is_authenticated,
        }

    logger.info("[auth] Auth system initialized")


# ─── Google OAuth ─────────────────────────────────────────────────────────────

_oauth = None

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
    redirect_uri = url_for('auth.google_callback', _external=True)
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
        name = userinfo.get('name', email.split('@')[0])
        google_id = userinfo.get('sub')

        if not email:
            return redirect(url_for('auth.login_page', error='no_email'))

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
        return redirect(request.args.get('next', '/'))

    except Exception as e:
        logger.error(f"Google OAuth error: {e}")
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
    }.get(error, '')

    return render_template_string(LOGIN_TEMPLATE,
                                  error_msg=error_msg,
                                  google_enabled=bool(GOOGLE_CLIENT_ID),
                                  page='login')


@auth_bp.route('/login', methods=['POST'])
def login_submit():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')

    if not email or not password:
        return redirect(url_for('auth.login_page', error='invalid'))

    user = _load_user_by_email(email)
    if not user:
        return redirect(url_for('auth.login_page', error='invalid'))

    # Check password
    conn = _get_db()
    row = conn.execute('SELECT password_hash FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()

    if not row or not row['password_hash']:
        return redirect(url_for('auth.login_page', error='invalid'))

    if not check_password_hash(row['password_hash'], password):
        return redirect(url_for('auth.login_page', error='invalid'))

    login_user(user, remember=True)
    return redirect(request.args.get('next', '/'))


@auth_bp.route('/signup', methods=['GET'])
def signup_page():
    return render_template_string(SIGNUP_TEMPLATE,
                                  google_enabled=bool(GOOGLE_CLIENT_ID),
                                  page='signup')


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

    return redirect('/')


@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect('/')


# ─── Stripe Payments ──────────────────────────────────────────────────────────

@auth_bp.route('/checkout/<plan>')
def checkout(plan):
    """Create Stripe Checkout session.
    
    No login required. Stripe collects the email during checkout.
    The webhook auto-creates the user account and activates premium.
    If the user is already logged in, we pre-fill their email.
    """
    if not STRIPE_SECRET_KEY:
        return "Stripe not configured. Set STRIPE_SECRET_KEY.", 500

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    price_id = STRIPE_PRICE_MONTHLY if plan == 'monthly' else STRIPE_PRICE_YEARLY
    if not price_id:
        return "Stripe price not configured.", 500

    try:
        session_kwargs = {
            'payment_method_types': ['card'],
            'line_items': [{'price': price_id, 'quantity': 1}],
            'mode': 'subscription',
            'success_url': request.url_root.rstrip('/') + '/checkout/success?session_id={CHECKOUT_SESSION_ID}',
            'cancel_url': request.url_root.rstrip('/') + '/plans',
            'metadata': {'plan': plan},
        }
        # Pre-fill email if logged in
        if current_user.is_authenticated:
            session_kwargs['customer_email'] = current_user.email
            session_kwargs['metadata']['user_id'] = str(current_user.id)

        checkout_session = stripe.checkout.Session.create(**session_kwargs)
        return redirect(checkout_session.url)
    except Exception as e:
        logger.error(f"Stripe checkout error: {e}")
        return f"Payment error: {e}", 500


@auth_bp.route('/checkout/success')
def checkout_success():
    """Handle successful Stripe checkout.
    
    Verifies the Stripe session, auto-creates account if needed,
    logs the user in, and activates premium.
    """
    session_id = request.args.get('session_id')
    if not session_id:
        return redirect('/plans')

    if STRIPE_SECRET_KEY and session_id:
        try:
            import stripe
            stripe.api_key = STRIPE_SECRET_KEY
            cs = stripe.checkout.Session.retrieve(session_id)
            if cs.payment_status == 'paid':
                email = (cs.customer_details or {}).get('email') or cs.get('customer_email', '')
                email = email.strip().lower() if email else ''
                plan = (cs.metadata or {}).get('plan', 'monthly')
                customer_id = cs.get('customer')

                if email:
                    # Find or create user from payment email
                    user = _load_user_by_email(email)
                    if not user:
                        conn = _get_db()
                        conn.execute(
                            'INSERT INTO users (email, name) VALUES (?, ?)',
                            (email, email.split('@')[0])
                        )
                        conn.commit()
                        conn.close()
                        user = _load_user_by_email(email)
                        logger.info(f"[checkout/success] Auto-created account for {email}")

                    if user:
                        _activate_premium(user.id, plan=plan, stripe_customer_id=customer_id)
                        login_user(user, remember=True)
                        logger.info(f"[checkout/success] Activated premium for {email}")
        except Exception as e:
            logger.warning(f"[checkout/success] Stripe verification failed: {e}")

    return render_template_string(SUCCESS_TEMPLATE, page='success')


@auth_bp.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return '', 400

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature', '')

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        return '', 400

    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        plan = session_data.get('metadata', {}).get('plan', 'monthly')
        customer_id = session_data.get('customer')
        # Get email from Stripe session (works for both logged-in and guest checkouts)
        email = (session_data.get('customer_details') or {}).get('email') or session_data.get('customer_email', '')
        email = email.strip().lower() if email else ''

        if email:
            user = _load_user_by_email(email)
            if not user:
                # Auto-create account from payment email
                conn = _get_db()
                conn.execute(
                    'INSERT INTO users (email, name) VALUES (?, ?)',
                    (email, email.split('@')[0])
                )
                conn.commit()
                conn.close()
                user = _load_user_by_email(email)
                logger.info(f"[stripe webhook] Auto-created account for {email}")
            if user:
                _activate_premium(user.id, plan=plan, stripe_customer_id=customer_id)
                logger.info(f"[stripe webhook] Activated premium for {email} ({plan})")
        else:
            # Fallback: try user_id from metadata (legacy)
            user_id = session_data.get('metadata', {}).get('user_id')
            if user_id:
                _activate_premium(int(user_id), plan=plan, stripe_customer_id=customer_id)
                logger.info(f"[stripe webhook] Activated premium for user_id {user_id} ({plan})")

    elif event['type'] == 'customer.subscription.deleted':
        customer_id = event['data']['object'].get('customer')
        if customer_id:
            _deactivate_premium_by_customer(customer_id)
            logger.info(f"[stripe] Deactivated premium for customer {customer_id}")

    return '', 200


def _activate_premium(user_id, plan='monthly', stripe_customer_id=None):
    """Activate premium for a user."""
    if plan == 'yearly':
        expires = (datetime.now() + timedelta(days=365)).isoformat()
    else:
        expires = (datetime.now() + timedelta(days=31)).isoformat()

    conn = _get_db()
    if stripe_customer_id:
        conn.execute(
            'UPDATE users SET is_premium = 1, premium_expires = ?, stripe_customer_id = ? WHERE id = ?',
            (expires, stripe_customer_id, user_id)
        )
    else:
        conn.execute(
            'UPDATE users SET is_premium = 1, premium_expires = ? WHERE id = ?',
            (expires, user_id)
        )
    conn.commit()
    conn.close()


def _deactivate_premium_by_customer(stripe_customer_id):
    """Deactivate premium when subscription is cancelled."""
    conn = _get_db()
    conn.execute(
        'UPDATE users SET is_premium = 0 WHERE stripe_customer_id = ?',
        (stripe_customer_id,)
    )
    conn.commit()
    conn.close()


# ─── Helper: check premium in views ──────────────────────────────────────────

def is_premium_user():
    """Check if current request is from a premium user."""
    if not current_user.is_authenticated:
        return False
    return current_user.premium_active


# ─── Templates ────────────────────────────────────────────────────────────────

_AUTH_STYLES = """
<style>
    .auth-container { max-width: 420px; margin: 60px auto; padding: 40px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.12); border-radius: 16px; }
    .auth-title { font-size: 1.8em; text-align: center; margin-bottom: 24px; color: #fbbf24; }
    .auth-form input { width: 100%; padding: 12px 16px; margin-bottom: 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.08); color: white; font-size: 1em; box-sizing: border-box; }
    .auth-form input::placeholder { color: #94a3b8; }
    .auth-form input:focus { outline: none; border-color: #fbbf24; }
    .auth-btn { width: 100%; padding: 14px; border: none; border-radius: 8px; font-size: 1.05em; font-weight: 700; cursor: pointer; transition: all 0.2s; }
    .auth-btn-primary { background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #000; }
    .auth-btn-primary:hover { opacity: 0.9; }
    .auth-btn-google { background: white; color: #333; margin-bottom: 14px; display: flex; align-items: center; justify-content: center; gap: 10px; }
    .auth-btn-google:hover { background: #f3f4f6; }
    .auth-divider { text-align: center; margin: 18px 0; color: #94a3b8; font-size: 0.85em; position: relative; }
    .auth-divider::before, .auth-divider::after { content: ''; position: absolute; top: 50%; width: 40%; height: 1px; background: rgba(255,255,255,0.15); }
    .auth-divider::before { left: 0; }
    .auth-divider::after { right: 0; }
    .auth-link { text-align: center; margin-top: 18px; color: #94a3b8; font-size: 0.9em; }
    .auth-link a { color: #fbbf24; text-decoration: none; }
    .auth-error { background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.4); color: #fca5a5; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; font-size: 0.9em; }
</style>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login — underdogs.bet</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:url('/static/baseball.jpg') center/cover no-repeat fixed;color:white;min-height:100vh;}body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.82);z-index:0;}body>*{position:relative;z-index:1;}</style>
""" + _AUTH_STYLES + """
</head><body>
<div class="auth-container">
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
<title>Sign Up — underdogs.bet</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:url('/static/baseball.jpg') center/cover no-repeat fixed;color:white;min-height:100vh;}body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.82);z-index:0;}body>*{position:relative;z-index:1;}</style>
""" + _AUTH_STYLES + """
</head><body>
<div class="auth-container">
    <div class="auth-title">Create Account</div>
    <p style="text-align:center;color:#94a3b8;font-size:0.85em;margin-bottom:18px;">Sign up to access free picks. Upgrade anytime for Spreads, Totals &amp; Score Predictions.</p>
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

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Welcome to Premium — underdogs.bet</title>
<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);color:white;min-height:100vh;display:flex;align-items:center;justify-content:center;}</style>
</head><body>
<div style="text-align:center;padding:40px;">
    <div style="font-size:4em;margin-bottom:20px;">🎉</div>
    <h1 style="color:#fbbf24;margin-bottom:12px;">Welcome to Premium!</h1>
    <p style="opacity:0.8;margin-bottom:30px;">You now have full access to Spreads, Totals, and Score Predictions.</p>
    <a href="/" style="background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;">View Today's Picks →</a>
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
            @keyframes pulse-bg{0%,100%{transform:scale(1);opacity:0.5;}50%{transform:scale(1.1);opacity:1;}}
            .plans-page-bg{position:fixed;inset:0;background:url('/static/baseball.jpg') center/cover no-repeat;z-index:-2;}
            .plans-page-bg::after{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.82);z-index:-1;}
            .plans-hero{background:rgba(255,255,255,0.03);border:2px solid rgba(255,255,255,0.15);border-radius:16px;padding:40px 24px 32px;text-align:center;position:relative;overflow:hidden;margin-bottom:30px;}
            .plans-hero::after{content:'';position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:300px;height:300px;background:url('/static/IMG_3179.PNG') center/contain no-repeat;opacity:0.06;z-index:0;}
            .plans-hero::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(255,255,255,0.04) 0%,transparent 60%);animation:pulse-bg 6s ease-in-out infinite;}
            .plans-hero-logo{font-size:2em;font-weight:900;color:#ffffff;position:relative;z-index:1;letter-spacing:0.5px;line-height:1.3;}
            .plans-hero-sub{font-size:1.05em;color:#94a3b8;margin-top:12px;position:relative;z-index:1;max-width:650px;margin-left:auto;margin-right:auto;line-height:1.7;}
            .plans-hero-stats{display:flex;justify-content:center;gap:16px;margin-top:20px;position:relative;z-index:1;flex-wrap:wrap;}
            .stat-pill{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.18);border-radius:24px;padding:7px 18px;font-size:0.82em;font-weight:600;color:#cbd5e1;}
            .stat-pill span{color:#fff;}
            .competitor-bar{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.12);border-radius:10px;max-width:560px;margin:20px auto 0;padding:10px 18px;font-size:0.85em;position:relative;z-index:1;color:#94a3b8;}
            .competitor-bar strong{color:#fff;}
            .plans-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;}
            .plan-card{background:#ffffff;color:#1e293b;border-radius:18px;padding:36px 28px;text-align:center;transition:all 0.3s;box-shadow:0 4px 24px rgba(0,0,0,0.3);position:relative;}
            .plan-card:hover{transform:translateY(-6px);box-shadow:0 12px 40px rgba(0,0,0,0.4);}
            .plan-card.popular{border:3px solid #e2e8f0;}
            .plan-badge{position:absolute;top:-14px;left:50%;transform:translateX(-50%);background:#fff;color:#0f172a;padding:5px 20px;border-radius:20px;font-size:0.78em;font-weight:800;}
            .plan-name{font-size:1.4em;font-weight:800;margin-bottom:6px;color:#0f172a;}
            .plan-old-price{font-size:1.1em;color:#94a3b8;text-decoration:line-through;margin-bottom:2px;}
            .plan-price{font-size:3.2em;font-weight:900;color:#0f172a;margin-bottom:2px;}
            .plan-price span{font-size:0.3em;opacity:0.5;font-weight:500;}
            .plan-save{color:#059669;font-size:0.88em;margin-bottom:20px;font-weight:700;}
            .plan-features{text-align:left;margin-bottom:28px;padding:0;}
            .plan-features li{padding:7px 0;font-size:0.9em;list-style:none;color:#334155;border-bottom:1px solid #f1f5f9;}
            .plan-features li:last-child{border-bottom:none;}
            .plan-features li::before{content:none;}
            .plan-btn{display:block;width:100%;padding:16px;border:none;border-radius:10px;font-size:1.1em;font-weight:800;cursor:pointer;text-decoration:none;text-align:center;transition:all 0.2s;}
            .plan-btn-primary{background:#0f172a;color:white;box-shadow:0 4px 16px rgba(0,0,0,0.3);}
            .plan-btn-primary:hover{transform:translateY(-2px);background:#1e293b;box-shadow:0 8px 24px rgba(0,0,0,0.4);}
            .plan-btn-secondary{background:#0f172a;color:white;}
            .plan-btn-secondary:hover{background:#1e293b;}
            .free-section{margin-top:40px;text-align:center;padding:28px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:14px;}
            .free-features{display:flex;justify-content:center;gap:20px;margin-top:12px;flex-wrap:wrap;}
            .free-pill{background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);border-radius:20px;padding:6px 16px;font-size:0.82em;}
            @media(max-width:640px){.plans-grid{grid-template-columns:1fr;}.plans-hero-stats{flex-direction:column;align-items:center;gap:10px;}}
            """
        ).replace('{% block content %}{% endblock %}', """
            <div class="plans-page-bg"></div>
            <div class="plans-hero">
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
            <p style="text-align:center;font-size:1.15em;color:#e2e8f0;margin-bottom:28px;font-weight:600;">Free gets you the winners. Premium gets you the edge.</p>
            <div class="plans-grid">
                <div class="plan-card">
                    <div class="plan-name">Monthly</div>
                    <div class="plan-price">$19.99<span>/month</span></div>
                    <div class="plan-save">Flexible access. Cancel anytime.</div>
                    <ul class="plan-features">
                        <li>Every Spread Pick (No Guessing)</li>
                        <li>Every Total Pick (Our Strongest Edge)</li>
                        <li>Projected Scores for Every Game</li>
                        <li>Full Odds Engine (ML, Spread, Total)</li>
                        <li>All Sports Covered</li>
                        <li>Priority Support</li>
                        <li>Cancel Anytime</li>
                    </ul>
                    <a href="/checkout/monthly" class="plan-btn plan-btn-secondary">Get Monthly Access</a>
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
                        <li>All Sports Covered</li>
                        <li>Priority Support</li>
                        <li>Cancel Anytime</li>
                    </ul>
                    <a href="/checkout/yearly" class="plan-btn plan-btn-primary">Get Yearly Access</a>
                </div>
            </div>
            <p style="text-align:center;font-size:0.85em;color:#64748b;margin-top:20px;">Tracked results updated daily.</p>
            <div class="free-section">
                <p style="font-size:1.2em;margin-bottom:6px;font-weight:700;">Start Free</p>
                <p style="opacity:0.7;margin-bottom:12px;">Start free. Upgrade when you're ready for the full edge.</p>
                <div class="free-features">
                    <div class="free-pill">Moneyline Picks</div>
                    <div class="free-pill">5-Model Win %</div>
                    <div class="free-pill">Full Results</div>
                    <div class="free-pill">All Sports Covered</div>
                </div>
            </div>
        """)
        return render_template_string(plans_content, page='plans')
    except Exception as e:
        logger.error(f"Plans page error: {e}")
        return redirect('/')
