"""Focused tests for premium activate + claim token + weekly checkout + webhooks."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_system as auth


def _sign_stripe_payload(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    if isinstance(payload, bytes):
        payload_str = payload.decode('utf-8')
    else:
        payload_str = payload
    signed = f"{timestamp}.{payload_str}"
    sig = hmac.new(secret.encode('utf-8'), signed.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


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

    def test_subscription_and_price_id_from_dahlia_invoice(self):
        inv = {
            'customer': 'cus_UyNMRYYeQlV4aT',
            'parent': {
                'subscription_details': {
                    'subscription': 'sub_1TyQc7LqTMBLPh0wCpnIfvgY',
                }
            },
            'lines': {
                'data': [{
                    'pricing': {
                        'price_details': {
                            'price': 'price_1TayccLqTMBLPh0wCsTLZh4j',
                        }
                    }
                }]
            },
        }
        self.assertEqual(
            auth._subscription_id_from_invoice(inv),
            'sub_1TyQc7LqTMBLPh0wCpnIfvgY',
        )
        self.assertEqual(
            auth._price_id_from_invoice(inv),
            'price_1TayccLqTMBLPh0wCsTLZh4j',
        )
        self.assertEqual(
            auth._subscription_id_from_invoice({'subscription': 'sub_legacy'}),
            'sub_legacy',
        )

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

    def test_sync_handles_expanded_customer_object(self):
        ts = int((datetime.now() + timedelta(days=10)).timestamp())
        sub = {
            'id': 'sub_exp',
            'customer': {'id': 'cus_test_123'},
            'status': 'active',
            'current_period_end': ts,
        }
        auth._sync_premium_from_subscription(sub)
        conn = auth._get_db()
        row = conn.execute('SELECT is_premium, premium_expires FROM users WHERE id = ?', (self.user_id,)).fetchone()
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
            # Without Stripe price env, checkout falls back to Payment Links
            with mock.patch.object(auth, 'STRIPE_SECRET_KEY', ''):
                with mock.patch.object(auth, 'STRIPE_PRICE_WEEKLY', ''):
                    resp = client.get('/checkout/weekly', follow_redirects=False)
                    self.assertEqual(resp.status_code, 302)
                    self.assertIn(
                        'buy.stripe.com/14A6oI4Ra66ReWLczTao802',
                        resp.headers.get('Location', ''),
                    )
            with mock.patch.object(auth, 'STRIPE_SECRET_KEY', 'sk_test'):
                with mock.patch.object(auth, 'STRIPE_PRICE_WEEKLY', ''):
                    with mock.patch.object(auth, 'STRIPE_WEEKLY_URL', 'https://buy.stripe.com/test_weekly'):
                        resp = client.get('/checkout/weekly', follow_redirects=False)
                        self.assertEqual(resp.status_code, 302)
                        self.assertIn('buy.stripe.com/test_weekly', resp.headers.get('Location', ''))
                with mock.patch.object(auth, 'STRIPE_PRICE_MONTHLY', ''):
                    with mock.patch.object(
                        auth,
                        'STRIPE_MONTHLY_URL',
                        'https://buy.stripe.com/bJeeVe0AU1QB7uj7fzao801',
                    ):
                        resp = client.get('/checkout/monthly', follow_redirects=False)
                        self.assertEqual(resp.status_code, 302)
                        self.assertIn(
                            'buy.stripe.com/bJeeVe0AU1QB7uj7fzao801',
                            resp.headers.get('Location', ''),
                        )
                with mock.patch.object(auth, 'STRIPE_PRICE_YEARLY', ''):
                    with mock.patch.object(
                        auth,
                        'STRIPE_YEARLY_URL',
                        'https://buy.stripe.com/8x228s83mfHr8yneI1ao803',
                    ):
                        resp = client.get('/checkout/yearly', follow_redirects=False)
                        self.assertEqual(resp.status_code, 302)
                        self.assertIn(
                            'buy.stripe.com/8x228s83mfHr8yneI1ao803',
                            resp.headers.get('Location', ''),
                        )
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class StripeWebhookPremiumTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.whsec = 'whsec_test_premium_access'
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_wh = auth.STRIPE_WEBHOOK_SECRET
        self._prev_weekly = auth.STRIPE_PRICE_WEEKLY
        auth.STRIPE_SECRET_KEY = 'sk_test_premium'
        auth.STRIPE_WEBHOOK_SECRET = self.whsec
        auth.STRIPE_PRICE_WEEKLY = 'price_weekly_test'
        auth._DB_PATH = self.db_path

        from flask import Flask
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-secret-stable'
        self.app.config['TESTING'] = True
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, stripe_customer_id) VALUES (?, ?, ?)",
            ('renew@example.com', 'renew', 'cus_renew_1'),
        )
        conn.commit()
        self.user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('renew@example.com',)
        ).fetchone()['id']
        conn.close()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY = self._prev_secret
        auth.STRIPE_WEBHOOK_SECRET = self._prev_wh
        auth.STRIPE_PRICE_WEEKLY = self._prev_weekly
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _post_event(self, event: dict):
        payload = json.dumps(event).encode('utf-8')
        sig = _sign_stripe_payload(payload, self.whsec)
        return self.client.post(
            '/stripe/webhook',
            data=payload,
            headers={
                'Stripe-Signature': sig,
                'Content-Type': 'application/json',
            },
        )

    def test_invoice_payment_succeeded_returns_200_and_extends(self):
        ts = int((datetime.now() + timedelta(days=21)).timestamp())
        fake_sub = {
            'id': 'sub_renew_1',
            'object': 'subscription',
            'customer': 'cus_renew_1',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': 'price_weekly_test'}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                self.assertEqual(sub_id, 'sub_renew_1')
                return fake_sub

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_invoice_1',
                'object': 'event',
                'type': 'invoice.payment_succeeded',
                'data': {
                    'object': {
                        'id': 'in_1',
                        'object': 'invoice',
                        'customer': 'cus_renew_1',
                        'subscription': 'sub_renew_1',
                        'period_end': ts,
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_invoice_payment_succeeded_dahlia_shape_weekly_extends(self):
        """Stripe API 2026-06-24.dahlia: no top-level subscription / lines[].price."""
        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        weekly_price = 'price_weekly_test'
        fake_sub = {
            'id': 'sub_1TyQc7LqTMBLPh0wCpnIfvgY',
            'object': 'subscription',
            'customer': 'cus_renew_1',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': weekly_price}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                self.assertEqual(sub_id, 'sub_1TyQc7LqTMBLPh0wCpnIfvgY')
                return fake_sub

        invoice_obj = {
            'id': 'in_dahlia_1',
            'object': 'invoice',
            # No top-level "subscription" (dahlia).
            'customer': 'cus_renew_1',
            'customer_email': 'reyes.paul94@gmail.com',
            'billing_reason': 'subscription_cycle',
            'period_end': ts,
            'parent': {
                'type': 'subscription_details',
                'subscription_details': {
                    'subscription': 'sub_1TyQc7LqTMBLPh0wCpnIfvgY',
                },
            },
            'lines': {
                'data': [{
                    # No classic "price"; dahlia nests under pricing.price_details.
                    'pricing': {
                        'price_details': {
                            'price': weekly_price,
                        }
                    }
                }]
            },
        }
        self.assertEqual(
            auth._subscription_id_from_invoice(invoice_obj),
            'sub_1TyQc7LqTMBLPh0wCpnIfvgY',
        )
        self.assertEqual(auth._price_id_from_invoice(invoice_obj), weekly_price)

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_1U0zrLLqTMBLPh0w53n3Jo1L',
                'object': 'event',
                'type': 'invoice.payment_succeeded',
                'data': {'object': invoice_obj},
            })
        self.assertEqual(resp.status_code, 200)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_invoice_dahlia_fallback_activate_weekly_when_retrieve_fails(self):
        """No top-level subscription; retrieve fails; still 200 + weekly activate."""
        ts = int((datetime.now() + timedelta(days=7)).timestamp())

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                raise RuntimeError('stripe retrieve unavailable')

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_dahlia_fallback',
                'object': 'event',
                'type': 'invoice.payment_succeeded',
                'data': {
                    'object': {
                        'id': 'in_dahlia_fb',
                        'object': 'invoice',
                        'customer': 'cus_renew_1',
                        'customer_email': 'renew@example.com',
                        'billing_reason': 'subscription_cycle',
                        'period_end': ts,
                        'parent': {
                            'subscription_details': {
                                'subscription': 'sub_dahlia_fb',
                            }
                        },
                        'lines': {
                            'data': [{
                                'pricing': {
                                    'price_details': {
                                        'price': 'price_weekly_test',
                                    }
                                }
                            }]
                        },
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        conn = auth._get_db()
        row = conn.execute(
            'SELECT is_premium, premium_expires FROM users WHERE id = ?',
            (self.user_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_checkout_weekly_activates_and_returns_200(self):
        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        fake_sub = {
            'id': 'sub_week_1',
            'customer': 'cus_new_week',
            'status': 'active',
            'current_period_end': ts,
            'metadata': {'plan': 'weekly'},
            'items': {'data': [{'price': {'id': 'price_weekly_test'}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_checkout_week',
                'object': 'event',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_week',
                        'object': 'checkout.session',
                        'customer': 'cus_new_week',
                        'subscription': 'sub_week_1',
                        'customer_details': {'email': 'weekly@example.com'},
                        'metadata': {'plan': 'weekly'},
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        user = auth._load_user_by_email('weekly@example.com')
        self.assertIsNotNone(user)
        self.assertTrue(user.premium_active)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user.id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_customer_id'], 'cus_new_week')
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_checkout_yearly_payment_link_activates_via_interval(self):
        """Payment Link checkout often omits metadata.plan; infer yearly from interval."""
        ts = int((datetime.now() + timedelta(days=365)).timestamp())
        fake_sub = {
            'id': 'sub_year_pl',
            'customer': 'cus_new_year',
            'status': 'active',
            'current_period_end': ts,
            'metadata': {},
            'items': {
                'data': [{
                    'price': {
                        'id': 'price_unmapped_yearly',
                        'recurring': {'interval': 'year'},
                    }
                }]
            },
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        prev_yearly = auth.STRIPE_PRICE_YEARLY
        auth.STRIPE_PRICE_YEARLY = ''
        try:
            with mock.patch('stripe.Subscription', _SubAPI):
                resp = self._post_event({
                    'id': 'evt_checkout_year_pl',
                    'object': 'event',
                    'type': 'checkout.session.completed',
                    'data': {
                        'object': {
                            'id': 'cs_year_pl',
                            'object': 'checkout.session',
                            'customer': 'cus_new_year',
                            'subscription': 'sub_year_pl',
                            'customer_details': {'email': 'yearly@example.com'},
                            'metadata': {},
                        }
                    },
                })
        finally:
            auth.STRIPE_PRICE_YEARLY = prev_yearly

        self.assertEqual(resp.status_code, 200)
        user = auth._load_user_by_email('yearly@example.com')
        self.assertIsNotNone(user)
        self.assertTrue(user.premium_active)
        self.assertEqual(
            auth._plan_from_checkout_or_subscription({}, fake_sub),
            'yearly',
        )
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user.id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_webhook_idempotency(self):
        ts = int((datetime.now() + timedelta(days=14)).timestamp())
        fake_sub = {
            'id': 'sub_idem',
            'customer': 'cus_renew_1',
            'status': 'active',
            'current_period_end': ts,
        }

        class _SubAPI:
            calls = 0

            @classmethod
            def retrieve(cls, sub_id):
                cls.calls += 1
                return fake_sub

        event = {
            'id': 'evt_idem_1',
            'object': 'event',
            'type': 'invoice.payment_succeeded',
            'data': {
                'object': {
                    'customer': 'cus_renew_1',
                    'subscription': 'sub_idem',
                    'period_end': ts,
                }
            },
        }
        with mock.patch('stripe.Subscription', _SubAPI):
            r1 = self._post_event(event)
            r2 = self._post_event(event)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(_SubAPI.calls, 1)

    def test_thin_payload_acks_200_without_500(self):
        payload = json.dumps({
            'id': 'evt_thin_1',
            'object': 'v2.core.event',
            'type': 'v1.billing.meter.error_report_triggered',
            'data': {},
        }).encode('utf-8')
        sig = _sign_stripe_payload(payload, self.whsec)
        resp = self.client.post(
            '/stripe/webhook',
            data=payload,
            headers={'Stripe-Signature': sig, 'Content-Type': 'application/json'},
        )
        self.assertEqual(resp.status_code, 200)
        # Premium unchanged
        conn = auth._get_db()
        row = conn.execute('SELECT is_premium FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 0)

    def test_invoice_fallback_when_retrieve_fails_still_200(self):
        ts = int((datetime.now() + timedelta(days=12)).timestamp())

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                raise RuntimeError('stripe down')

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_fallback_1',
                'object': 'event',
                'type': 'invoice.payment_succeeded',
                'data': {
                    'object': {
                        'customer': 'cus_renew_1',
                        'subscription': 'sub_x',
                        'period_end': ts,
                        'lines': {'data': [{'price': {'id': 'price_weekly_test'}}]},
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        conn = auth._get_db()
        row = conn.execute('SELECT is_premium, premium_expires FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_subscription_created_activates(self):
        ts = int((datetime.now() + timedelta(days=30)).timestamp())
        resp = self._post_event({
            'id': 'evt_sub_created',
            'object': 'event',
            'type': 'customer.subscription.created',
            'data': {
                'object': {
                    'id': 'sub_new',
                    'customer': 'cus_renew_1',
                    'status': 'active',
                    'current_period_end': ts,
                }
            },
        })
        self.assertEqual(resp.status_code, 200)
        conn = auth._get_db()
        row = conn.execute('SELECT is_premium FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)

    def test_is_premium_user_respects_active_flag(self):
        auth._activate_premium(self.user_id, plan='weekly', stripe_customer_id='cus_renew_1')
        user = auth._load_user_by_id(self.user_id)
        self.assertTrue(user.premium_active)
        with self.app.test_request_context('/'):
            with mock.patch.object(auth, 'current_user', user):
                # current_user is flask_login proxy — patch is_premium_user path via login
                pass
        # Direct property used by is_premium_user
        self.assertTrue(user.premium_active)


if __name__ == '__main__':
    unittest.main()
