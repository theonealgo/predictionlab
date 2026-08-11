"""Forgot / reset password flow tests (local, isolated temp DB)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

import auth_system as auth


def _make_app(db_path):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['TESTING'] = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    # Point Flask at project templates/
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.template_folder = os.path.join(root, 'templates')
    auth.init_auth(app, db_path=db_path)
    return app


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.app = _make_app(self.db_path)
        self.client = self.app.test_client()
        # Seed users
        conn = auth._get_db()
        conn.execute(
            'INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)',
            ('pwuser@example.com', 'Pw', generate_password_hash('oldpass1')),
        )
        conn.execute(
            'INSERT INTO users (email, name, google_id) VALUES (?, ?, ?)',
            ('googleonly@example.com', 'G', 'google-sub-123'),
        )
        conn.execute(
            'INSERT INTO users (email, name) VALUES (?, ?)',
            ('guest@example.com', 'Guest'),
        )
        conn.commit()
        self.pw_uid = conn.execute(
            "SELECT id FROM users WHERE email='pwuser@example.com'"
        ).fetchone()[0]
        self.google_uid = conn.execute(
            "SELECT id FROM users WHERE email='googleonly@example.com'"
        ).fetchone()[0]
        self.guest_uid = conn.execute(
            "SELECT id FROM users WHERE email='guest@example.com'"
        ).fetchone()[0]
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    # 1. Routes registered
    def test_routes_registered(self):
        rules = {str(r) for r in self.app.url_map.iter_rules()}
        self.assertIn('/forgot-password', rules)
        self.assertIn('/reset-password', rules)
        self.assertIn('/login', rules)
        self.assertIn('/auth/google', rules)

    # 2. Login page shows Forgot password link
    def test_login_has_forgot_link(self):
        resp = self.client.get('/login')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Forgot password?', html)
        self.assertIn('/forgot-password', html)
        # No Google-only messaging next to forgot link
        self.assertNotIn('Google-only', html)
        self.assertNotIn('use Google Sign-In', html.lower())

    # 3. Forgot-password GET renders form
    def test_forgot_password_get(self):
        resp = self.client.get('/forgot-password')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('Send Reset Link', html)
        self.assertIn('name="email"', html)

    # 4. Unknown email → generic success (no enumeration)
    def test_forgot_unknown_email_generic_success(self):
        with mock.patch.object(auth, '_send_password_reset_email', return_value=True) as send_reset:
            with mock.patch.object(auth, '_send_google_only_signin_email', return_value=True) as send_g:
                resp = self.client.post(
                    '/forgot-password',
                    data={'email': 'nosuch@example.com'},
                    follow_redirects=True,
                )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('If an account exists', html)
        send_reset.assert_not_called()
        send_g.assert_not_called()

    # 5. Password user → token issued + email attempted; hash at rest
    def test_forgot_password_user_issues_hashed_token(self):
        with mock.patch.object(auth, '_send_password_reset_email', return_value=True) as send_reset:
            resp = self.client.post(
                '/forgot-password',
                data={'email': 'pwuser@example.com'},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('If an account exists', resp.get_data(as_text=True))
        send_reset.assert_called_once()
        raw = send_reset.call_args[0][1]
        self.assertTrue(raw)
        # Raw token must not be stored
        conn = auth._get_db()
        rows = conn.execute(
            'SELECT token_hash, user_id, used_at, expires_at FROM password_reset_tokens '
            'WHERE user_id = ? AND used_at IS NULL',
            (self.pw_uid,),
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['token_hash'], auth._hash_reset_token(raw))
        self.assertNotEqual(rows[0]['token_hash'], raw)
        exp = datetime.fromisoformat(rows[0]['expires_at'])
        self.assertLess(exp, datetime.now() + timedelta(hours=2))

    # 6. Google-only → no password token; google email; generic page
    def test_forgot_google_only_no_password_created(self):
        with mock.patch.object(auth, '_send_password_reset_email', return_value=True) as send_reset:
            with mock.patch.object(auth, '_send_google_only_signin_email', return_value=True) as send_g:
                resp = self.client.post(
                    '/forgot-password',
                    data={'email': 'googleonly@example.com'},
                )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('If an account exists', resp.get_data(as_text=True))
        send_reset.assert_not_called()
        send_g.assert_called_once()
        self.assertFalse(auth._user_has_password(self.google_uid))
        conn = auth._get_db()
        n = conn.execute(
            'SELECT COUNT(*) AS c FROM password_reset_tokens WHERE user_id = ?',
            (self.google_uid,),
        ).fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    # 7. Reset with valid token updates password; single-use
    def test_reset_valid_token_sets_password_once(self):
        raw = auth._issue_set_password_token(self.pw_uid, ttl_hours=1)
        resp = self.client.get(f'/reset-password?token={raw}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('New password', resp.get_data(as_text=True))

        resp = self.client.post(
            '/reset-password',
            data={'token': raw, 'password': 'newpass9', 'confirm': 'newpass9'},
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('password has been reset', html)
        self.assertIn('/login', html)
        self.assertTrue(auth._user_has_password(self.pw_uid))
        conn = auth._get_db()
        row = conn.execute(
            'SELECT password_hash FROM users WHERE id = ?', (self.pw_uid,)
        ).fetchone()
        conn.close()
        self.assertTrue(check_password_hash(row['password_hash'], 'newpass9'))
        self.assertFalse(check_password_hash(row['password_hash'], 'oldpass1'))

        # Reuse rejected
        resp2 = self.client.post(
            '/reset-password',
            data={'token': raw, 'password': 'another1', 'confirm': 'another1'},
        )
        self.assertIn('invalid or has expired', resp2.get_data(as_text=True))

    # 8. Expired token rejected
    def test_reset_expired_token_rejected(self):
        raw = auth._issue_set_password_token(self.pw_uid, ttl_hours=1)
        th = auth._hash_reset_token(raw)
        past = (datetime.now() - timedelta(hours=2)).isoformat()
        conn = auth._get_db()
        conn.execute(
            'UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?',
            (past, th),
        )
        conn.commit()
        conn.close()
        resp = self.client.get(f'/reset-password?token={raw}')
        self.assertIn('invalid or has expired', resp.get_data(as_text=True))
        resp = self.client.post(
            '/reset-password',
            data={'token': raw, 'password': 'newpass9', 'confirm': 'newpass9'},
        )
        self.assertIn('invalid or has expired', resp.get_data(as_text=True))
        conn = auth._get_db()
        row = conn.execute(
            'SELECT password_hash FROM users WHERE id = ?', (self.pw_uid,)
        ).fetchone()
        conn.close()
        self.assertTrue(check_password_hash(row['password_hash'], 'oldpass1'))

    # 9. Password rules: mismatch + short
    def test_reset_password_validation(self):
        raw = auth._issue_set_password_token(self.pw_uid, ttl_hours=1)
        resp = self.client.post(
            '/reset-password',
            data={'token': raw, 'password': 'abcdef', 'confirm': 'abcdefg'},
        )
        self.assertIn('do not match', resp.get_data(as_text=True))
        resp = self.client.post(
            '/reset-password',
            data={'token': raw, 'password': 'ab', 'confirm': 'ab'},
        )
        self.assertIn('at least 6', resp.get_data(as_text=True))
        # Token still usable
        uid, _ = auth._lookup_set_password_token(raw)
        self.assertEqual(uid, self.pw_uid)

    # 10. Cannot reset another user (token bound to user_id)
    def test_token_cannot_reset_other_user(self):
        raw = auth._issue_set_password_token(self.pw_uid, ttl_hours=1)
        user = auth._consume_set_password_token(raw, 'boundpass')
        self.assertEqual(user.id, self.pw_uid)
        self.assertFalse(auth._user_has_password(self.guest_uid))

    # 11. Google-only login without password does not issue claim token
    def test_google_only_login_no_claim(self):
        with mock.patch.object(auth, '_maybe_send_claim_email') as claim_mail:
            resp = self.client.post(
                '/login',
                data={'email': 'googleonly@example.com', 'password': 'anything'},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('error=invalid', resp.headers.get('Location', ''))
        claim_mail.assert_not_called()
        self.assertFalse(auth._user_has_password(self.google_uid))

    # 12. Rate limit trips after max per email
    def test_forgot_rate_limit(self):
        with mock.patch.object(auth, '_send_password_reset_email', return_value=True):
            with mock.patch.object(auth, 'FORGOT_PASSWORD_MAX_PER_EMAIL', 2):
                with mock.patch.object(auth, 'FORGOT_PASSWORD_MAX_PER_IP', 100):
                    self.client.post('/forgot-password', data={'email': 'pwuser@example.com'})
                    self.client.post('/forgot-password', data={'email': 'pwuser@example.com'})
                    resp = self.client.post(
                        '/forgot-password', data={'email': 'pwuser@example.com'}
                    )
        self.assertIn('Too many reset requests', resp.get_data(as_text=True))

    # 13. Invalid email validation
    def test_forgot_invalid_email(self):
        resp = self.client.post('/forgot-password', data={'email': 'not-an-email'})
        self.assertIn('valid email', resp.get_data(as_text=True).lower())


if __name__ == '__main__':
    unittest.main()
