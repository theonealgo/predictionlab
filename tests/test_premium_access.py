"""Focused tests for premium activate + claim token + weekly checkout route."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_system as auth


class PremiumAccessHelpersTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        auth._DB_PATH = self._tmp.name
        auth._ensure_users_table()
        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, stripe_customer_id) VALUES (?, ?, ?)",
            ('payer@example.com', 'payer', 'cus_test_123'),
        )
        conn.commit()
        self.user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('payer@example.com',)
        ).fetchone()['id']
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_valid_plans_include_weekly(self):
        self.assertEqual(auth.VALID_CHECKOUT_PLANS, frozenset({'monthly', 'yearly', 'weekly'}))

    def test_activate_premium_weekly(self):
        before = datetime.now()
        auth._activate_premium(self.user_id, plan='weekly', stripe_customer_id='cus_test_123')
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        exp = datetime.fromisoformat(row['premium_expires'])
        self.assertLess(exp, before + timedelta(days=8))
        self.assertGreater(exp, before + timedelta(days=6))

    def test_activate_premium_uses_explicit_expires(self):
        period_end = (datetime.now() + timedelta(days=14)).isoformat()
        auth._activate_premium(
            self.user_id, plan='monthly',
            stripe_customer_id='cus_test_123',
            premium_expires=period_end,
        )
        conn = auth._get_db()
        row = conn.execute('SELECT premium_expires, is_premium FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['premium_expires'], period_end)
        self.assertEqual(row['is_premium'], 1)

    def test_claim_token_issued_when_no_password(self):
        self.assertFalse(auth._user_has_password(self.user_id))
        raw = auth._ensure_claim_token_for_user(self.user_id, force_new=True)
        self.assertTrue(raw)
        uid, email = auth._lookup_set_password_token(raw)
        self.assertEqual(uid, self.user_id)
        self.assertEqual(email, 'payer@example.com')
        # Second ensure without force_new does not clobber
        again = auth._ensure_claim_token_for_user(self.user_id, force_new=False)
        self.assertIsNone(again)
        uid2, _ = auth._lookup_set_password_token(raw)
        self.assertEqual(uid2, self.user_id)

    def test_consume_claim_token_sets_password(self):
        raw = auth._issue_set_password_token(self.user_id)
        user = auth._consume_set_password_token(raw, 'secret99')
        self.assertIsNotNone(user)
        self.assertTrue(auth._user_has_password(self.user_id))
        # Token is one-time
        self.assertIsNone(auth._consume_set_password_token(raw, 'otherpass'))

    def test_sync_premium_from_subscription_extends(self):
        ts = int((datetime.now() + timedelta(days=14)).timestamp())
        sub = {
            'id': 'sub_sync',
            'customer': 'cus_test_123',
            'status': 'active',
            'current_period_end': ts,
        }
        auth._sync_premium_from_subscription(sub)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_weekly_checkout_route_registered(self):
        from flask import Flask
        app = Flask(__name__)
        app.config['SECRET_KEY'] = 'test'
        app.config['TESTING'] = True
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            db_path = tmp.name
        try:
            auth.init_auth(app, db_path=db_path)
            rules = {str(r) for r in app.url_map.iter_rules()}
            self.assertIn('/checkout/<plan>', rules)
            self.assertIn('/set-password', rules)
            self.assertIn('/claim-account', rules)
            client = app.test_client()
            # Without Stripe key, weekly should still accept the plan slug
            # (500 "not configured" is ok; 400 invalid plan is not)
            with mock.patch.object(auth, 'STRIPE_SECRET_KEY', ''):
                resp = client.get('/checkout/weekly')
                self.assertEqual(resp.status_code, 500)
                self.assertIn(b'Stripe not configured', resp.data)
            with mock.patch.object(auth, 'STRIPE_SECRET_KEY', 'sk_test'):
                with mock.patch.object(auth, 'STRIPE_PRICE_WEEKLY', ''):
                    with mock.patch.object(auth, 'STRIPE_WEEKLY_URL', 'https://buy.stripe.com/test_weekly'):
                        resp = client.get('/checkout/weekly', follow_redirects=False)
                        self.assertEqual(resp.status_code, 302)
                        self.assertIn('buy.stripe.com/test_weekly', resp.headers.get('Location', ''))
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


if __name__ == '__main__':
    unittest.main()
