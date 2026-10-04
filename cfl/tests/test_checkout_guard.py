"""Checkout duplicate-subscribe guard: portal redirect + reuse customer id."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_system as auth


class CheckoutDuplicateSubscribeGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_weekly = auth.STRIPE_PRICE_WEEKLY
        self._prev_monthly = auth.STRIPE_PRICE_MONTHLY
        self._prev_yearly = auth.STRIPE_PRICE_YEARLY
        auth.STRIPE_SECRET_KEY = 'sk_test_checkout_guard'
        auth.STRIPE_PRICE_WEEKLY = 'price_weekly_guard'
        auth.STRIPE_PRICE_MONTHLY = 'price_monthly_guard'
        auth.STRIPE_PRICE_YEARLY = 'price_yearly_guard'
        auth._DB_PATH = self.db_path

        from flask import Flask
        from werkzeug.security import generate_password_hash

        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-checkout-guard'
        self.app.config['TESTING'] = True
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        expires = (datetime.now() + timedelta(days=20)).isoformat()
        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium, "
            "premium_expires, stripe_customer_id) VALUES (?, ?, ?, 1, ?, ?)",
            (
                'subbed@example.com',
                'Subbed User',
                generate_password_hash('guard-pass-123'),
                expires,
                'cus_guard_active',
            ),
        )
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium, "
            "stripe_customer_id) VALUES (?, ?, ?, 0, ?)",
            (
                'returning@example.com',
                'Returning User',
                generate_password_hash('guard-pass-123'),
                'cus_guard_returning',
            ),
        )
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium) "
            "VALUES (?, ?, ?, 0)",
            (
                'newuser@example.com',
                'New User',
                generate_password_hash('guard-pass-123'),
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY = self._prev_secret
        auth.STRIPE_PRICE_WEEKLY = self._prev_weekly
        auth.STRIPE_PRICE_MONTHLY = self._prev_monthly
        auth.STRIPE_PRICE_YEARLY = self._prev_yearly
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _login(self, email):
        resp = self.client.post(
            '/login',
            data={'email': email, 'password': 'guard-pass-123'},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))

    def test_active_subscriber_redirects_to_portal_not_new_checkout(self):
        self._login('subbed@example.com')
        created = {}

        class _SubList:
            @staticmethod
            def list(**kwargs):
                return {
                    'data': [
                        {'id': 'sub_already', 'status': 'active', 'customer': 'cus_guard_active'},
                    ],
                }

        class _CheckoutAPI:
            @staticmethod
            def create(**kwargs):
                created['called'] = True
                return {'url': 'https://checkout.stripe.com/should-not-happen'}

        class _PortalAPI:
            @staticmethod
            def create(**kwargs):
                created['portal'] = kwargs
                return {'url': 'https://billing.stripe.com/p/session/guard_portal'}

        with mock.patch('stripe.Subscription', _SubList), \
             mock.patch('stripe.checkout.Session', _CheckoutAPI), \
             mock.patch('stripe.billing_portal.Session', _PortalAPI):
            resp = self.client.get('/checkout/monthly', follow_redirects=False)

        self.assertNotIn('called', created)
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers.get('Location', '')
        # First hop is internal portal route; follow once to Stripe portal URL.
        self.assertTrue(
            '/create-portal-session' in loc
            or '/billing/portal' in loc
            or 'billing.stripe.com' in loc,
            loc,
        )
        if 'billing.stripe.com' not in loc:
            with mock.patch('stripe.billing_portal.Session', _PortalAPI):
                resp2 = self.client.get(loc, follow_redirects=False)
            self.assertEqual(resp2.status_code, 302)
            self.assertIn('billing.stripe.com', resp2.headers.get('Location', ''))

    def test_checkout_reuses_stripe_customer_id(self):
        self._login('returning@example.com')
        created = {}

        class _SubList:
            @staticmethod
            def list(**kwargs):
                return {'data': []}

        class _Session:
            def __init__(self, **kwargs):
                created.update(kwargs)
                self.url = 'https://checkout.stripe.com/c/pay/cs_test_reuse'

        class _CheckoutAPI:
            @staticmethod
            def create(**kwargs):
                return _Session(**kwargs)

        with mock.patch('stripe.Subscription', _SubList), \
             mock.patch('stripe.checkout.Session', _CheckoutAPI):
            resp = self.client.get('/checkout/weekly', follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertIn('checkout.stripe.com', resp.headers.get('Location', ''))
        self.assertEqual(created.get('customer'), 'cus_guard_returning')
        self.assertNotIn('customer_email', created)

    def test_logged_in_without_customer_uses_email_only(self):
        self._login('newuser@example.com')
        created = {}

        class _Session:
            def __init__(self, **kwargs):
                created.update(kwargs)
                self.url = 'https://checkout.stripe.com/c/pay/cs_test_email'

        class _CheckoutAPI:
            @staticmethod
            def create(**kwargs):
                return _Session(**kwargs)

        with mock.patch('stripe.checkout.Session', _CheckoutAPI):
            resp = self.client.get('/checkout/yearly', follow_redirects=False)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(created.get('customer_email'), 'newuser@example.com')
        self.assertNotIn('customer', created)

    def test_plans_page_ctas_use_checkout_routes(self):
        from pathlib import Path
        src = Path(auth.__file__).read_text(encoding='utf-8')
        # plans_page HTML is embedded in auth_system — assert CTAs route through /checkout/
        start = src.index("@auth_bp.route('/plans')")
        chunk = src[start:start + 12000]
        self.assertIn('href="/checkout/weekly"', chunk)
        self.assertIn('href="/checkout/monthly"', chunk)
        self.assertIn('href="/checkout/yearly"', chunk)
        self.assertNotIn('buy.stripe.com/14A6oI4Ra66ReWLczTao802', chunk)
        self.assertNotIn('buy.stripe.com/bJeeVe0AU1QB7uj7fzao801', chunk)
        self.assertNotIn('buy.stripe.com/8x228s83mfHr8yneI1ao803', chunk)


class SubscriptionDeletedKeepPremiumTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.whsec = 'whsec_test_deleted_keep'
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_wh = auth.STRIPE_WEBHOOK_SECRET
        auth.STRIPE_SECRET_KEY = 'sk_test_deleted_keep'
        auth.STRIPE_WEBHOOK_SECRET = self.whsec
        auth._DB_PATH = self.db_path

        from flask import Flask
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-deleted-keep'
        self.app.config['TESTING'] = True
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, stripe_customer_id, is_premium) "
            "VALUES (?, ?, ?, 1)",
            ('keep@example.com', 'Keep User', 'cus_keep_1'),
        )
        conn.commit()
        self.user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('keep@example.com',)
        ).fetchone()['id']
        conn.close()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY = self._prev_secret
        auth.STRIPE_WEBHOOK_SECRET = self._prev_wh
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _post_event(self, event: dict):
        import hashlib
        import hmac
        import json
        import time

        payload = json.dumps(event).encode('utf-8')
        timestamp = int(time.time())
        signed = f"{timestamp}.{payload.decode('utf-8')}"
        sig = hmac.new(
            self.whsec.encode('utf-8'), signed.encode('utf-8'), hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            '/stripe/webhook',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Stripe-Signature': f't={timestamp},v1={sig}',
            },
        )

    def test_deleted_keeps_premium_when_other_active_sub(self):
        event = {
            'id': 'evt_deleted_keep',
            'object': 'event',
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {
                    'id': 'sub_gone_dup',
                    'object': 'subscription',
                    'customer': 'cus_keep_1',
                    'status': 'canceled',
                }
            },
        }

        class _SubList:
            @staticmethod
            def list(**kwargs):
                return {
                    'data': [
                        {'id': 'sub_gone_dup', 'status': 'canceled'},
                        {'id': 'sub_still_active', 'status': 'active'},
                    ],
                }

        with mock.patch('stripe.Subscription', _SubList), \
             mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            resp = self._post_event(event)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(send.call_count, 0)
        user = auth._load_user_by_id(self.user_id)
        self.assertTrue(user.is_premium)

    def test_deleted_deactivates_when_no_other_active_sub(self):
        event = {
            'id': 'evt_deleted_only',
            'object': 'event',
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {
                    'id': 'sub_only',
                    'object': 'subscription',
                    'customer': 'cus_keep_1',
                    'status': 'canceled',
                }
            },
        }

        class _SubList:
            @staticmethod
            def list(**kwargs):
                return {'data': [{'id': 'sub_only', 'status': 'canceled'}]}

        with mock.patch('stripe.Subscription', _SubList), \
             mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            resp = self._post_event(event)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(send.call_count, 1)
        user = auth._load_user_by_id(self.user_id)
        self.assertFalse(user.is_premium)


if __name__ == '__main__':
    unittest.main()
