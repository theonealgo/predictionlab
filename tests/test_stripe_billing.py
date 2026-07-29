"""Unit tests for Stripe billing helpers (P0/P1) — no live Stripe calls."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_system as auth


class StripeBillingHelpersTest(unittest.TestCase):
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

    def test_valid_plans_constant(self):
        self.assertEqual(auth.VALID_CHECKOUT_PLANS, frozenset({'monthly', 'yearly', 'weekly'}))

    def test_activate_premium_weekly_fallback(self):
        before = datetime.now()
        auth._activate_premium(self.user_id, plan='weekly', stripe_customer_id='cus_test_123')
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        exp = datetime.fromisoformat(row['premium_expires'])
        self.assertLess(exp, before + timedelta(days=8))
        self.assertGreater(exp, before + timedelta(days=6))

    def test_activate_premium_uses_stripe_period_end(self):
        period_end = (datetime.now() + timedelta(days=12)).isoformat()
        auth._activate_premium(
            self.user_id,
            plan='monthly',
            stripe_customer_id='cus_test_123',
            stripe_subscription_id='sub_abc',
            premium_expires=period_end,
        )
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['premium_expires'], period_end)
        self.assertEqual(row['stripe_subscription_id'], 'sub_abc')
        self.assertEqual(row['stripe_status'], 'active')

    def test_payment_failed_does_not_extend(self):
        period_end = (datetime.now() + timedelta(days=20)).isoformat()
        auth._activate_premium(
            self.user_id, plan='monthly',
            stripe_customer_id='cus_test_123',
            premium_expires=period_end,
        )
        auth._record_payment_failure('cus_test_123', {'id': 'in_fail'})
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['premium_expires'], period_end)
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_status'], 'past_due')
        self.assertTrue(row['last_payment_failed_at'])

    def test_sync_from_subscription_period_end(self):
        ts = int((datetime.now() + timedelta(days=14)).timestamp())
        sub = {
            'id': 'sub_sync',
            'customer': 'cus_test_123',
            'status': 'active',
            'cancel_at_period_end': False,
            'current_period_end': ts,
            'items': {'data': [{'price': {'id': 'price_x'}}]},
        }
        auth._sync_premium_from_subscription(sub)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_subscription_id'], 'sub_sync')
        self.assertEqual(row['premium_expires'], auth._iso_from_unix(ts))

    def test_sync_cancel_at_period_end_status(self):
        ts = int((datetime.now() + timedelta(days=5)).timestamp())
        sub = {
            'id': 'sub_cancel',
            'customer': 'cus_test_123',
            'status': 'active',
            'cancel_at_period_end': True,
            'current_period_end': ts,
            'items': {'data': []},
        }
        auth._sync_premium_from_subscription(sub)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (self.user_id,)).fetchone()
        conn.close()
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_status'], 'cancel_at_period_end')

    def test_plan_from_price_id(self):
        with mock.patch.object(auth, 'STRIPE_PRICE_WEEKLY', 'price_week'), \
             mock.patch.object(auth, 'STRIPE_PRICE_MONTHLY', 'price_month'), \
             mock.patch.object(auth, 'STRIPE_PRICE_YEARLY', 'price_year'):
            self.assertEqual(auth._plan_from_price_id('price_week'), 'weekly')
            self.assertEqual(auth._plan_from_price_id('price_month'), 'monthly')
            self.assertEqual(auth._plan_from_price_id('price_year'), 'yearly')
            self.assertIsNone(auth._plan_from_price_id('price_other'))

    def test_plan_from_checkout_metadata_weekly(self):
        plan = auth._plan_from_checkout_or_subscription(
            {'metadata': {'plan': 'weekly'}}, None
        )
        self.assertEqual(plan, 'weekly')


if __name__ == '__main__':
    unittest.main()
