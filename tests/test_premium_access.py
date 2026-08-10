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


# Plan interval fixtures for parameterized activation coverage.
_PLAN_INTERVALS = (
    # plan, price_id, recurring.interval, days_for_period_end
    ('weekly', 'price_weekly_test', 'week', 7),
    ('monthly', 'price_monthly_test', 'month', 31),
    ('yearly', 'price_yearly_test', 'year', 365),
)


class StripeWebhookPremiumTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.whsec = 'whsec_test_premium_access'
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_wh = auth.STRIPE_WEBHOOK_SECRET
        self._prev_weekly = auth.STRIPE_PRICE_WEEKLY
        self._prev_monthly = auth.STRIPE_PRICE_MONTHLY
        self._prev_yearly = auth.STRIPE_PRICE_YEARLY
        auth.STRIPE_SECRET_KEY = 'sk_test_premium'
        auth.STRIPE_WEBHOOK_SECRET = self.whsec
        auth.STRIPE_PRICE_WEEKLY = 'price_weekly_test'
        auth.STRIPE_PRICE_MONTHLY = 'price_monthly_test'
        auth.STRIPE_PRICE_YEARLY = 'price_yearly_test'
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
        auth.STRIPE_PRICE_MONTHLY = self._prev_monthly
        auth.STRIPE_PRICE_YEARLY = self._prev_yearly
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
        # Activation events may re-run on Resend (safe / idempotent activate).
        self.assertGreaterEqual(_SubAPI.calls, 1)
        conn = auth._get_db()
        row = conn.execute(
            'SELECT is_premium FROM users WHERE id = ?', (self.user_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)

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

    def test_zero_dollar_invoice_active_weekly_grants_premium(self):
        """100% coupon: amount_paid=0 + active weekly sub → durable premium."""
        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        # Existing account with password, NOT yet linked to Stripe customer
        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, 0)",
            ('theonealgo@gmail.com', 'Nima', 'pbkdf2:sha256:fakefor tests',),
        )
        conn.commit()
        uid = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('theonealgo@gmail.com',)
        ).fetchone()['id']
        conn.close()

        fake_sub = {
            'id': 'sub_1U14N4LqTMBLPh0wgk2Ds0FA',
            'customer': 'cus_coupon_new',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': 'price_weekly_test',
                                          'recurring': {'interval': 'week'}}}]},
        }

        class _API:
            @staticmethod
            def retrieve_sub(sub_id):
                return fake_sub

            @staticmethod
            def retrieve_cust(cid):
                return {'id': cid, 'email': 'theonealgo@gmail.com'}

        with mock.patch('stripe.Subscription') as Sub, \
             mock.patch('stripe.Customer') as Cust:
            Sub.retrieve = _API.retrieve_sub
            Cust.retrieve = _API.retrieve_cust
            resp = self._post_event({
                'id': 'evt_zero_dollar_inv',
                'object': 'event',
                'type': 'invoice.payment_succeeded',
                'data': {
                    'object': {
                        'id': 'in_zero',
                        'object': 'invoice',
                        'customer': 'cus_coupon_new',
                        # No customer_email on invoice (Buy Button edge)
                        'amount_paid': 0,
                        'amount_due': 0,
                        'status': 'paid',
                        'period_end': ts,
                        'parent': {
                            'subscription_details': {
                                'subscription': 'sub_1U14N4LqTMBLPh0wgk2Ds0FA',
                            }
                        },
                        'lines': {
                            'data': [{
                                'pricing': {
                                    'price_details': {'price': 'price_weekly_test'}
                                }
                            }]
                        },
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_customer_id'], 'cus_coupon_new')
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))
        user = auth._load_user_by_id(uid)
        self.assertTrue(user.premium_active)

    def test_checkout_completed_no_client_reference_customer_email(self):
        """Buy Button / Payment Link: empty client_reference_id, email on session."""
        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        fake_sub = {
            'id': 'sub_buy_btn',
            'customer': 'cus_buy_btn',
            'status': 'active',
            'current_period_end': ts,
            'metadata': {},
            'items': {'data': [{'price': {
                'id': 'price_weekly_test',
                'recurring': {'interval': 'week'},
            }}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_buy_btn_checkout',
                'object': 'event',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_buy_btn',
                        'object': 'checkout.session',
                        'customer': 'cus_buy_btn',
                        'subscription': 'sub_buy_btn',
                        'client_reference_id': None,
                        'payment_status': 'no_payment_required',
                        'customer_details': {'email': 'BuyButton.User@Example.com'},
                        'customer_email': None,
                        'metadata': {},
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        user = auth._load_user_by_email('buybutton.user@example.com')
        self.assertIsNotNone(user)
        self.assertTrue(user.premium_active)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user.id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_customer_id'], 'cus_buy_btn')

    def test_checkout_completed_fetches_customer_email_when_missing(self):
        """Session has customer id but no email fields — fetch Customer.email."""
        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        fake_sub = {
            'id': 'sub_cust_email',
            'customer': 'cus_fetch_email',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': 'price_weekly_test',
                                          'recurring': {'interval': 'week'}}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        class _CustAPI:
            @staticmethod
            def retrieve(cid):
                return {'id': cid, 'email': 'fetched@example.com'}

        with mock.patch('stripe.Subscription', _SubAPI), \
             mock.patch('stripe.Customer', _CustAPI):
            resp = self._post_event({
                'id': 'evt_fetch_cust_email',
                'object': 'event',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_no_email',
                        'customer': 'cus_fetch_email',
                        'subscription': 'sub_cust_email',
                        'client_reference_id': '',
                        'payment_status': 'paid',
                        'customer_details': {},
                        'metadata': {},
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        user = auth._load_user_by_email('fetched@example.com')
        self.assertIsNotNone(user)
        self.assertTrue(user.premium_active)

    def test_login_after_activation_still_premium(self):
        """Premium written by webhook survives logout/login (DB-backed)."""
        from werkzeug.security import generate_password_hash

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium) VALUES (?, ?, ?, 0)",
            ('loginprem@example.com', 'LP', generate_password_hash('secret99')),
        )
        conn.commit()
        uid = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('loginprem@example.com',)
        ).fetchone()['id']
        conn.close()

        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        fake_sub = {
            'id': 'sub_login_prem',
            'customer': 'cus_login_prem',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': 'price_weekly_test'}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_login_prem',
                'object': 'event',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_login_prem',
                        'customer': 'cus_login_prem',
                        'subscription': 'sub_login_prem',
                        'payment_status': 'no_payment_required',
                        'customer_details': {'email': 'loginprem@example.com'},
                        'metadata': {},
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)

        # Fresh load as login does — not session cookie state
        user = auth._load_user_by_email('loginprem@example.com')
        self.assertEqual(user.id, uid)
        self.assertTrue(user.premium_active)
        self.assertEqual(user.is_premium, True)

        # Simulate logout then login_submit path
        with self.client.session_transaction() as sess:
            sess.clear()
        login_resp = self.client.post(
            '/login',
            data={'email': 'loginprem@example.com', 'password': 'secret99'},
            follow_redirects=False,
        )
        self.assertIn(login_resp.status_code, (302, 303))
        user2 = auth._load_user_by_id(uid)
        self.assertTrue(user2.premium_active)

    def test_checkout_payment_ok_accepts_no_payment_required(self):
        self.assertTrue(auth._checkout_payment_ok('paid'))
        self.assertTrue(auth._checkout_payment_ok('no_payment_required'))
        self.assertFalse(auth._checkout_payment_ok('unpaid'))
        self.assertFalse(auth._checkout_payment_ok(''))

    def test_noop_activation_not_marked_idempotent(self):
        """Failed resolve must not mark event_id — Dashboard Resend can retry."""
        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return {
                    'id': sub_id,
                    'customer': 'cus_unknown_zzz',
                    'status': 'active',
                    'current_period_end': int(
                        (datetime.now() + timedelta(days=7)).timestamp()
                    ),
                }

        class _CustAPI:
            @staticmethod
            def retrieve(cid):
                return {'id': cid, 'email': None}

        with mock.patch('stripe.Subscription', _SubAPI), \
             mock.patch('stripe.Customer', _CustAPI):
            resp = self._post_event({
                'id': 'evt_noop_unmarked',
                'object': 'event',
                'type': 'customer.subscription.created',
                'data': {
                    'object': {
                        'id': 'sub_unknown_zzz',
                        'customer': 'cus_unknown_zzz',
                        'status': 'active',
                        'current_period_end': int(
                            (datetime.now() + timedelta(days=7)).timestamp()
                        ),
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(auth._webhook_event_already_processed('evt_noop_unmarked'))

    def test_resend_of_previously_marked_activation_still_applies(self):
        """Older builds marked no-ops; Resend after fix must still grant premium."""
        ts = int((datetime.now() + timedelta(days=7)).timestamp())
        auth._mark_webhook_event_processed('evt_old_noop', 'checkout.session.completed')
        self.assertTrue(auth._webhook_event_already_processed('evt_old_noop'))

        fake_sub = {
            'id': 'sub_resend',
            'customer': 'cus_resend',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': 'price_weekly_test',
                                          'recurring': {'interval': 'week'}}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        with mock.patch('stripe.Subscription', _SubAPI):
            resp = self._post_event({
                'id': 'evt_old_noop',
                'object': 'event',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_resend',
                        'customer': 'cus_resend',
                        'subscription': 'sub_resend',
                        'payment_status': 'no_payment_required',
                        'customer_details': {'email': 'resend@example.com'},
                        'metadata': {},
                    }
                },
            })
        self.assertEqual(resp.status_code, 200)
        user = auth._load_user_by_email('resend@example.com')
        self.assertIsNotNone(user)
        self.assertTrue(user.premium_active)

    def test_plan_from_price_id_all_intervals(self):
        """Weekly/monthly/yearly env price ids resolve identically."""
        self.assertEqual(auth._plan_from_price_id('price_weekly_test'), 'weekly')
        self.assertEqual(auth._plan_from_price_id('price_monthly_test'), 'monthly')
        self.assertEqual(auth._plan_from_price_id('price_yearly_test'), 'yearly')
        self.assertIsNone(auth._plan_from_price_id('price_unknown_zzz'))
        self.assertIsNone(auth._plan_from_price_id('prod_not_a_price'))

    def test_checkout_all_intervals_no_client_reference_customer_email(self):
        """Payment Link / Buy Button: each interval activates without client_reference_id."""
        for plan, price_id, interval, days in _PLAN_INTERVALS:
            with self.subTest(plan=plan):
                ts = int((datetime.now() + timedelta(days=days)).timestamp())
                email = f'{plan}.buyer@example.com'
                cus = f'cus_pl_{plan}'
                sub_id = f'sub_pl_{plan}'
                fake_sub = {
                    'id': sub_id,
                    'customer': cus,
                    'status': 'active',
                    'current_period_end': ts,
                    'metadata': {},
                    'items': {'data': [{'price': {
                        'id': price_id,
                        'recurring': {'interval': interval},
                    }}]},
                }

                class _SubAPI:
                    @staticmethod
                    def retrieve(sid):
                        return fake_sub

                with mock.patch('stripe.Subscription', _SubAPI):
                    resp = self._post_event({
                        'id': f'evt_checkout_{plan}_noref',
                        'object': 'event',
                        'type': 'checkout.session.completed',
                        'data': {
                            'object': {
                                'id': f'cs_{plan}_noref',
                                'customer': cus,
                                'subscription': sub_id,
                                'client_reference_id': None,
                                'payment_status': 'paid',
                                'customer_details': {'email': email},
                                'metadata': {},
                            }
                        },
                    })
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(
                    auth._webhook_event_already_processed(f'evt_checkout_{plan}_noref')
                )
                user = auth._load_user_by_email(email)
                self.assertIsNotNone(user)
                self.assertTrue(user.premium_active)
                conn = auth._get_db()
                row = conn.execute(
                    'SELECT is_premium, stripe_customer_id, premium_expires '
                    'FROM users WHERE id = ?',
                    (user.id,),
                ).fetchone()
                conn.close()
                self.assertEqual(row['is_premium'], 1)
                self.assertEqual(row['stripe_customer_id'], cus)
                self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))
                self.assertEqual(
                    auth._plan_from_checkout_or_subscription({}, fake_sub),
                    plan,
                )

    def test_checkout_all_intervals_no_payment_required(self):
        """$0 / coupon: no_payment_required grants premium for every interval."""
        for plan, price_id, interval, days in _PLAN_INTERVALS:
            with self.subTest(plan=plan):
                ts = int((datetime.now() + timedelta(days=days)).timestamp())
                email = f'{plan}.zero@example.com'
                cus = f'cus_zero_{plan}'
                sub_id = f'sub_zero_{plan}'
                fake_sub = {
                    'id': sub_id,
                    'customer': cus,
                    'status': 'active',
                    'current_period_end': ts,
                    'items': {'data': [{'price': {
                        'id': price_id,
                        'recurring': {'interval': interval},
                    }}]},
                }

                class _SubAPI:
                    @staticmethod
                    def retrieve(sid):
                        return fake_sub

                with mock.patch('stripe.Subscription', _SubAPI):
                    resp = self._post_event({
                        'id': f'evt_zero_checkout_{plan}',
                        'object': 'event',
                        'type': 'checkout.session.completed',
                        'data': {
                            'object': {
                                'id': f'cs_zero_{plan}',
                                'customer': cus,
                                'subscription': sub_id,
                                'client_reference_id': '',
                                'payment_status': 'no_payment_required',
                                'customer_details': {'email': email},
                                'metadata': {},
                            }
                        },
                    })
                self.assertEqual(resp.status_code, 200)
                user = auth._load_user_by_email(email)
                self.assertIsNotNone(user)
                self.assertTrue(user.premium_active)
                self.assertEqual(
                    auth._load_user_by_id(user.id).stripe_customer_id, cus,
                )

    def test_subscription_created_all_intervals_customer_retrieve_email(self):
        """customer.subscription.created: only customer id; email from Customer.retrieve."""
        for plan, price_id, interval, days in _PLAN_INTERVALS:
            with self.subTest(plan=plan):
                ts = int((datetime.now() + timedelta(days=days)).timestamp())
                email = f'{plan}.subcreate@example.com'
                cus = f'cus_subc_{plan}'
                sub_id = f'sub_subc_{plan}'

                class _CustAPI:
                    @staticmethod
                    def retrieve(cid):
                        self.assertEqual(cid, cus)
                        return {'id': cid, 'email': email}

                with mock.patch('stripe.Customer', _CustAPI):
                    resp = self._post_event({
                        'id': f'evt_sub_created_{plan}',
                        'object': 'event',
                        'type': 'customer.subscription.created',
                        'data': {
                            'object': {
                                'id': sub_id,
                                'customer': cus,
                                'status': 'active',
                                'current_period_end': ts,
                                'metadata': {},
                                'items': {'data': [{'price': {
                                    'id': price_id,
                                    'recurring': {'interval': interval},
                                }}]},
                            }
                        },
                    })
                self.assertEqual(resp.status_code, 200)
                user = auth._load_user_by_email(email)
                self.assertIsNotNone(user)
                self.assertTrue(user.premium_active)
                conn = auth._get_db()
                row = conn.execute(
                    'SELECT is_premium, stripe_customer_id, premium_expires '
                    'FROM users WHERE id = ?',
                    (user.id,),
                ).fetchone()
                conn.close()
                self.assertEqual(row['is_premium'], 1)
                self.assertEqual(row['stripe_customer_id'], cus)
                self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_invoice_paid_and_payment_succeeded_activate(self):
        """Both invoice.paid and invoice.payment_succeeded grant premium."""
        for etype, suffix in (
            ('invoice.paid', 'paid'),
            ('invoice.payment_succeeded', 'succeeded'),
        ):
            with self.subTest(etype=etype):
                ts = int((datetime.now() + timedelta(days=31)).timestamp())
                email = f'inv.{suffix}@example.com'
                cus = f'cus_inv_{suffix}'
                sub_id = f'sub_inv_{suffix}'
                fake_sub = {
                    'id': sub_id,
                    'customer': cus,
                    'status': 'active',
                    'current_period_end': ts,
                    'items': {'data': [{'price': {
                        'id': 'price_monthly_test',
                        'recurring': {'interval': 'month'},
                    }}]},
                }

                class _SubAPI:
                    @staticmethod
                    def retrieve(sid):
                        return fake_sub

                class _CustAPI:
                    @staticmethod
                    def retrieve(cid):
                        return {'id': cid, 'email': email}

                with mock.patch('stripe.Subscription', _SubAPI), \
                     mock.patch('stripe.Customer', _CustAPI):
                    resp = self._post_event({
                        'id': f'evt_inv_{suffix}',
                        'object': 'event',
                        'type': etype,
                        'data': {
                            'object': {
                                'id': f'in_{suffix}',
                                'customer': cus,
                                'amount_paid': 1999 if suffix == 'succeeded' else 0,
                                'status': 'paid',
                                'period_end': ts,
                                'parent': {
                                    'subscription_details': {
                                        'subscription': sub_id,
                                    }
                                },
                                'lines': {'data': [{'pricing': {
                                    'price_details': {'price': 'price_monthly_test'},
                                }}]},
                            }
                        },
                    })
                self.assertEqual(resp.status_code, 200)
                user = auth._load_user_by_email(email)
                self.assertIsNotNone(user)
                self.assertTrue(user.premium_active)
                self.assertTrue(auth._webhook_event_already_processed(f'evt_inv_{suffix}'))

    def test_zero_dollar_invoice_all_intervals(self):
        """amount_paid=0 + active sub → premium for weekly/monthly/yearly."""
        for plan, price_id, interval, days in _PLAN_INTERVALS:
            with self.subTest(plan=plan):
                ts = int((datetime.now() + timedelta(days=days)).timestamp())
                email = f'{plan}.invzero@example.com'
                cus = f'cus_invz_{plan}'
                sub_id = f'sub_invz_{plan}'
                fake_sub = {
                    'id': sub_id,
                    'customer': cus,
                    'status': 'active',
                    'current_period_end': ts,
                    'items': {'data': [{'price': {
                        'id': price_id,
                        'recurring': {'interval': interval},
                    }}]},
                }

                class _SubAPI:
                    @staticmethod
                    def retrieve(sid):
                        return fake_sub

                class _CustAPI:
                    @staticmethod
                    def retrieve(cid):
                        return {'id': cid, 'email': email}

                with mock.patch('stripe.Subscription', _SubAPI), \
                     mock.patch('stripe.Customer', _CustAPI):
                    resp = self._post_event({
                        'id': f'evt_inv_zero_{plan}',
                        'object': 'event',
                        'type': 'invoice.paid',
                        'data': {
                            'object': {
                                'id': f'in_zero_{plan}',
                                'customer': cus,
                                'amount_paid': 0,
                                'amount_due': 0,
                                'status': 'paid',
                                'period_end': ts,
                                'parent': {
                                    'subscription_details': {'subscription': sub_id},
                                },
                                'lines': {'data': [{'pricing': {
                                    'price_details': {'price': price_id},
                                }}]},
                            }
                        },
                    })
                self.assertEqual(resp.status_code, 200)
                user = auth._load_user_by_email(email)
                self.assertIsNotNone(user)
                self.assertTrue(user.premium_active)

    def test_unknown_price_active_sub_still_grants_premium(self):
        """Mismatched/unknown price must not crash; active sub still unlocks premium."""
        ts = int((datetime.now() + timedelta(days=30)).timestamp())
        fake_sub = {
            'id': 'sub_unknown_price',
            'customer': 'cus_renew_1',
            'status': 'active',
            'current_period_end': ts,
            'items': {'data': [{'price': {
                'id': 'price_totally_unmapped',
                'recurring': {'interval': 'month'},
            }}]},
        }
        resp = self._post_event({
            'id': 'evt_unknown_price',
            'object': 'event',
            'type': 'customer.subscription.created',
            'data': {'object': fake_sub},
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
        # Interval inference still yields monthly for expires semantics
        self.assertEqual(
            auth._plan_from_checkout_or_subscription({}, fake_sub),
            'monthly',
        )

    def test_login_after_monthly_and_yearly_activation(self):
        """Premium from monthly/yearly webhooks survives logout/login."""
        from werkzeug.security import generate_password_hash

        for plan, price_id, interval, days in (
            ('monthly', 'price_monthly_test', 'month', 31),
            ('yearly', 'price_yearly_test', 'year', 365),
        ):
            with self.subTest(plan=plan):
                email = f'login.{plan}@example.com'
                conn = auth._get_db()
                conn.execute(
                    "INSERT INTO users (email, name, password_hash, is_premium) "
                    "VALUES (?, ?, ?, 0)",
                    (email, plan, generate_password_hash('secret99')),
                )
                conn.commit()
                uid = conn.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()['id']
                conn.close()

                ts = int((datetime.now() + timedelta(days=days)).timestamp())
                fake_sub = {
                    'id': f'sub_login_{plan}',
                    'customer': f'cus_login_{plan}',
                    'status': 'active',
                    'current_period_end': ts,
                    'items': {'data': [{'price': {
                        'id': price_id,
                        'recurring': {'interval': interval},
                    }}]},
                }

                class _SubAPI:
                    @staticmethod
                    def retrieve(sid):
                        return fake_sub

                with mock.patch('stripe.Subscription', _SubAPI):
                    resp = self._post_event({
                        'id': f'evt_login_{plan}',
                        'object': 'event',
                        'type': 'checkout.session.completed',
                        'data': {
                            'object': {
                                'id': f'cs_login_{plan}',
                                'customer': f'cus_login_{plan}',
                                'subscription': f'sub_login_{plan}',
                                'payment_status': 'paid',
                                'customer_details': {'email': email},
                                'metadata': {},
                            }
                        },
                    })
                self.assertEqual(resp.status_code, 200)

                with self.client.session_transaction() as sess:
                    sess.clear()
                login_resp = self.client.post(
                    '/login',
                    data={'email': email, 'password': 'secret99'},
                    follow_redirects=False,
                )
                self.assertIn(login_resp.status_code, (302, 303))
                user = auth._load_user_by_id(uid)
                self.assertTrue(user.premium_active)
                self.assertEqual(user.stripe_customer_id, f'cus_login_{plan}')

    def test_grant_premium_helper_used_for_all_plans(self):
        """Shared helper writes durable premium for each plan slug."""
        for plan, _price, _interval, days in _PLAN_INTERVALS:
            with self.subTest(plan=plan):
                email = f'grant.{plan}@example.com'
                ok = auth._grant_premium_to_payer(
                    customer_id=f'cus_grant_{plan}',
                    email=email,
                    plan=plan,
                    subscription_id=f'sub_grant_{plan}',
                    context='test_grant',
                )
                self.assertTrue(ok)
                user = auth._load_user_by_email(email)
                self.assertTrue(user.premium_active)
                before = datetime.now()
                exp = datetime.fromisoformat(user.premium_expires)
                self.assertGreater(exp, before + timedelta(days=days - 1))
                self.assertLess(exp, before + timedelta(days=days + 2))

    def test_unpaid_checkout_marked_processed_not_activation_miss(self):
        """Unpaid checkout is intentional skip — mark processed (not Resend forever)."""
        resp = self._post_event({
            'id': 'evt_unpaid_skip',
            'object': 'event',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_unpaid',
                    'customer': 'cus_unpaid',
                    'payment_status': 'unpaid',
                    'customer_details': {'email': 'unpaid@example.com'},
                    'metadata': {},
                }
            },
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(auth._webhook_event_already_processed('evt_unpaid_skip'))
        self.assertIsNone(auth._load_user_by_email('unpaid@example.com'))



class PremiumWelcomeEmailTest(unittest.TestCase):
    """Welcome email: first sub only, idempotent on retries, new sub after cancel OK."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.whsec = 'whsec_test_welcome'
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_wh = auth.STRIPE_WEBHOOK_SECRET
        self._prev_monthly = auth.STRIPE_PRICE_MONTHLY
        auth.STRIPE_SECRET_KEY = 'sk_test_welcome'
        auth.STRIPE_WEBHOOK_SECRET = self.whsec
        auth.STRIPE_PRICE_MONTHLY = 'price_monthly_test'
        auth._DB_PATH = self.db_path

        from flask import Flask
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-welcome-secret'
        self.app.config['TESTING'] = True
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, stripe_customer_id) VALUES (?, ?, ?)",
            ('welcome@example.com', 'Ada Lovelace', 'cus_welcome_1'),
        )
        conn.commit()
        self.user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('welcome@example.com',)
        ).fetchone()['id']
        conn.close()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY = self._prev_secret
        auth.STRIPE_WEBHOOK_SECRET = self._prev_wh
        auth.STRIPE_PRICE_MONTHLY = self._prev_monthly
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

    def test_helper_idempotent_same_subscription(self):
        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=True) as send:
            ok1 = auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                name='Ada Lovelace',
                plan='monthly',
                subscription_id='sub_welcome_A',
                event_id='evt_w1',
                is_initial_subscribe=True,
            )
            ok2 = auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                name='Ada Lovelace',
                plan='monthly',
                subscription_id='sub_welcome_A',
                event_id='evt_w1_retry',
                is_initial_subscribe=True,
            )
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(send.call_count, 1)
        conn = auth._get_db()
        row = conn.execute(
            'SELECT welcome_email_subscription_id FROM users WHERE id = ?',
            (self.user_id,),
        ).fetchone()
        n = conn.execute(
            'SELECT COUNT(*) AS c FROM premium_welcome_emails WHERE subscription_id = ?',
            ('sub_welcome_A',),
        ).fetchone()['c']
        conn.close()
        self.assertEqual(row['welcome_email_subscription_id'], 'sub_welcome_A')
        self.assertEqual(n, 1)

    def test_helper_skips_renewals_flag(self):
        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=True) as send:
            ok = auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                plan='monthly',
                subscription_id='sub_renew_skip',
                event_id='evt_renew',
                is_initial_subscribe=False,
            )
        self.assertFalse(ok)
        self.assertEqual(send.call_count, 0)

    def test_new_subscription_after_cancel_gets_new_welcome(self):
        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=True) as send:
            self.assertTrue(auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                plan='weekly',
                subscription_id='sub_old',
                event_id='evt_old',
                is_initial_subscribe=True,
            ))
            self.assertTrue(auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                plan='monthly',
                subscription_id='sub_new',
                event_id='evt_new',
                is_initial_subscribe=True,
            ))
        self.assertEqual(send.call_count, 2)

    def test_send_failure_releases_slot_for_retry(self):
        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=False):
            self.assertFalse(auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                plan='monthly',
                subscription_id='sub_fail',
                event_id='evt_fail1',
                is_initial_subscribe=True,
            ))
        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=True) as send:
            self.assertTrue(auth._maybe_send_premium_welcome_email(
                user_id=self.user_id,
                email='welcome@example.com',
                plan='monthly',
                subscription_id='sub_fail',
                event_id='evt_fail2',
                is_initial_subscribe=True,
            ))
            self.assertEqual(send.call_count, 1)

    def test_checkout_webhook_sends_once_on_duplicate_replay(self):
        fake_sub = {
            'id': 'sub_wh_welcome',
            'object': 'subscription',
            'customer': 'cus_welcome_1',
            'status': 'active',
            'current_period_end': int((datetime.now() + timedelta(days=30)).timestamp()),
            'items': {'data': [{'price': {'id': 'price_monthly_test'}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        event = {
            'id': 'evt_wh_welcome_1',
            'object': 'event',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'id': 'cs_wh_welcome',
                    'customer': 'cus_welcome_1',
                    'subscription': 'sub_wh_welcome',
                    'payment_status': 'paid',
                    'customer_details': {
                        'email': 'welcome@example.com',
                        'name': 'Ada Lovelace',
                    },
                    'metadata': {'plan': 'monthly'},
                }
            },
        }

        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=True) as send:
            with mock.patch('stripe.Subscription', _SubAPI):
                r1 = self._post_event(event)
                # Duplicate Stripe delivery (same event id)
                r2 = self._post_event(event)
                # Sibling initial event for same subscription
                r3 = self._post_event({
                    **event,
                    'id': 'evt_wh_welcome_invoice',
                    'type': 'invoice.payment_succeeded',
                    'data': {
                        'object': {
                            'id': 'in_wh_welcome',
                            'customer': 'cus_welcome_1',
                            'customer_email': 'welcome@example.com',
                            'subscription': 'sub_wh_welcome',
                            'billing_reason': 'subscription_create',
                            'amount_paid': 1999,
                            'period_end': fake_sub['current_period_end'],
                        }
                    },
                })
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(send.call_count, 1)
        user = auth._load_user_by_id(self.user_id)
        self.assertTrue(user.premium_active)

    def test_renewal_invoice_does_not_welcome(self):
        auth._activate_premium(
            self.user_id, plan='monthly',
            stripe_customer_id='cus_welcome_1',
            stripe_subscription_id='sub_cycle',
        )
        fake_sub = {
            'id': 'sub_cycle',
            'object': 'subscription',
            'customer': 'cus_welcome_1',
            'status': 'active',
            'current_period_end': int((datetime.now() + timedelta(days=30)).timestamp()),
            'items': {'data': [{'price': {'id': 'price_monthly_test'}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=True) as send:
            with mock.patch('stripe.Subscription', _SubAPI):
                resp = self._post_event({
                    'id': 'evt_cycle_no_welcome',
                    'object': 'event',
                    'type': 'invoice.payment_succeeded',
                    'data': {
                        'object': {
                            'id': 'in_cycle',
                            'customer': 'cus_welcome_1',
                            'customer_email': 'welcome@example.com',
                            'subscription': 'sub_cycle',
                            'billing_reason': 'subscription_cycle',
                            'amount_paid': 1999,
                            'period_end': fake_sub['current_period_end'],
                        }
                    },
                })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(send.call_count, 0)

    def test_email_failure_still_returns_200_and_keeps_premium(self):
        fake_sub = {
            'id': 'sub_fail_mail',
            'object': 'subscription',
            'customer': 'cus_welcome_1',
            'status': 'active',
            'current_period_end': int((datetime.now() + timedelta(days=30)).timestamp()),
            'items': {'data': [{'price': {'id': 'price_monthly_test'}}]},
        }

        class _SubAPI:
            @staticmethod
            def retrieve(sub_id):
                return fake_sub

        with mock.patch.object(auth, '_send_premium_welcome_email_smtp', return_value=False):
            with mock.patch('stripe.Subscription', _SubAPI):
                resp = self._post_event({
                    'id': 'evt_fail_mail',
                    'object': 'event',
                    'type': 'checkout.session.completed',
                    'data': {
                        'object': {
                            'id': 'cs_fail_mail',
                            'customer': 'cus_welcome_1',
                            'subscription': 'sub_fail_mail',
                            'payment_status': 'paid',
                            'customer_details': {'email': 'welcome@example.com'},
                            'metadata': {},
                        }
                    },
                })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(auth._load_user_by_id(self.user_id).premium_active)

    def test_html_contains_branding_and_no_card_numbers(self):
        html = auth._build_premium_welcome_email_html(
            first_name='Ada', plan_label='Monthly',
        )
        self.assertIn('PredictionLab', html)
        self.assertIn('Monthly', html)
        self.assertIn('predictionlab.io', html)
        self.assertIn('/plans', html)
        self.assertIn('/login', html)
        self.assertIn('manage or cancel', html.lower())
        # No PAN-like digit runs / card brand leakage
        self.assertNotRegex(html, r'\b\d{12,19}\b')
        self.assertNotIn('visa', html.lower())
        self.assertNotIn('mastercard', html.lower())


class StripeCustomerPortalTest(unittest.TestCase):
    """Billing portal session: auth + DB customer id + mocked Stripe redirect."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_return = auth.STRIPE_PORTAL_RETURN_URL
        auth.STRIPE_SECRET_KEY = 'sk_test_portal'
        auth.STRIPE_PORTAL_RETURN_URL = ''
        auth._DB_PATH = self.db_path

        from flask import Flask
        from werkzeug.security import generate_password_hash

        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-portal-secret'
        self.app.config['TESTING'] = True
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium, stripe_customer_id) "
            "VALUES (?, ?, ?, 1, ?)",
            (
                'portal@example.com',
                'Portal User',
                generate_password_hash('portal-pass-123'),
                'cus_portal_abc',
            ),
        )
        conn.execute(
            "INSERT INTO users (email, name, password_hash, is_premium) "
            "VALUES (?, ?, ?, 1)",
            (
                'nocustomer@example.com',
                'No Customer',
                generate_password_hash('portal-pass-123'),
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY = self._prev_secret
        auth.STRIPE_PORTAL_RETURN_URL = self._prev_return
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _login(self, email='portal@example.com'):
        resp = self.client.post(
            '/login',
            data={'email': email, 'password': 'portal-pass-123'},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))

    def test_portal_routes_registered(self):
        rules = {str(r) for r in self.app.url_map.iter_rules()}
        self.assertIn('/create-portal-session', rules)
        self.assertIn('/billing/portal', rules)

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.post('/create-portal-session', follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303, 401))
        loc = resp.headers.get('Location', '')
        self.assertTrue('/login' in loc or resp.status_code == 401)

    def test_missing_customer_id_clear_error(self):
        self._login('nocustomer@example.com')
        resp = self.client.post('/create-portal-session', follow_redirects=False)
        self.assertEqual(resp.status_code, 400)
        body = resp.get_data(as_text=True).lower()
        self.assertIn('no stripe customer', body)
        self.assertNotIn('sk_test', body)

    def test_portal_session_redirects_with_db_customer(self):
        self._login('portal@example.com')
        created = {}

        class _PortalAPI:
            @staticmethod
            def create(**kwargs):
                # Customer must come from server/DB — never invent or accept browser ids.
                created.update(kwargs)
                return {'url': 'https://billing.stripe.com/p/session/test_portal'}

        with mock.patch('stripe.billing_portal.Session', _PortalAPI):
            resp = self.client.post('/create-portal-session', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.headers.get('Location'),
            'https://billing.stripe.com/p/session/test_portal',
        )
        self.assertEqual(created.get('customer'), 'cus_portal_abc')
        # test_client host is localhost → helper may use request host; always a URL.
        self.assertTrue(str(created.get('return_url') or '').startswith('http'))
        self.assertNotIn('sk_test', resp.get_data(as_text=True))

    def test_billing_portal_alias_same_behavior(self):
        self._login('portal@example.com')

        class _PortalAPI:
            @staticmethod
            def create(**kwargs):
                return type('S', (), {'url': 'https://billing.stripe.com/p/session/alias'})()

        with mock.patch('stripe.billing_portal.Session', _PortalAPI):
            resp = self.client.get('/billing/portal', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('billing.stripe.com', resp.headers.get('Location', ''))

    def test_portal_return_url_env_override(self):
        auth.STRIPE_PORTAL_RETURN_URL = 'https://predictionlab.io/plans'
        with self.app.test_request_context('/'):
            self.assertEqual(auth._portal_return_url(), 'https://predictionlab.io/plans')

    def test_portal_return_url_local_uses_request_host(self):
        auth.STRIPE_PORTAL_RETURN_URL = ''
        with self.app.test_request_context('http://127.0.0.1:5050/'):
            self.assertEqual(auth._portal_return_url(), 'http://127.0.0.1:5050/')

    def test_portal_return_url_production_default(self):
        auth.STRIPE_PORTAL_RETURN_URL = ''
        with self.app.test_request_context(
            '/',
            base_url='https://predictionlab.io/',
        ):
            self.assertEqual(
                auth._portal_return_url(),
                auth.DEFAULT_STRIPE_PORTAL_RETURN_URL,
            )


if __name__ == '__main__':
    unittest.main()
