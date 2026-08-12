"""Lifecycle emails: payment failed, cancel, renewal, expired + webhook wiring."""
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


class LifecycleEmailHelpersTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        auth._DB_PATH = self._tmp.name
        auth._ensure_users_table()
        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, stripe_customer_id) VALUES (?, ?, ?)",
            ('life@example.com', 'Ada Lovelace', 'cus_life_1'),
        )
        conn.commit()
        self.user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('life@example.com',)
        ).fetchone()['id']
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_html_builders_branded_no_card_leak(self):
        for builder, kwargs in (
            (auth._build_payment_failed_email_content, {'first_name': 'Ada'}),
            (auth._build_cancel_confirm_email_content, {
                'first_name': 'Ada', 'access_until': 'Sep 01, 2026',
            }),
            (auth._build_renewal_notice_email_content, {
                'first_name': 'Ada', 'renew_on': 'Sep 01, 2026', 'plan_label': 'Monthly',
            }),
            (auth._build_expired_winback_email_content, {'first_name': 'Ada'}),
        ):
            subject, text, html = builder(**kwargs)
            self.assertTrue(subject)
            self.assertIn('Ada', text)
            self.assertIn('PredictionLab', html)
            self.assertIn('#00529B', html)
            self.assertIn('/plans', html)
            self.assertNotRegex(html, r'\b\d{12,19}\b')
            self.assertNotIn('visa', html.lower())
            nasty_kwargs = dict(kwargs)
            nasty_kwargs['first_name'] = '<script>x</script>'
            _, _, nasty_html = builder(**nasty_kwargs)
            self.assertNotIn('<script>', nasty_html)
            self.assertIn('&lt;script&gt;', nasty_html)

    def test_idempotent_lifecycle_send(self):
        subject, text, html = auth._build_payment_failed_email_content(first_name='Ada')
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            ok1 = auth._maybe_send_lifecycle_email(
                kind=auth._LIFECYCLE_KIND_PAYMENT_FAILED,
                idem_key='payment_failed:in_1',
                to_email='life@example.com',
                first_name='Ada',
                subject=subject,
                text_body=text,
                html_body=html,
                user_id=self.user_id,
                event_id='evt_1',
            )
            ok2 = auth._maybe_send_lifecycle_email(
                kind=auth._LIFECYCLE_KIND_PAYMENT_FAILED,
                idem_key='payment_failed:in_1',
                to_email='life@example.com',
                first_name='Ada',
                subject=subject,
                text_body=text,
                html_body=html,
                user_id=self.user_id,
                event_id='evt_1_retry',
            )
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertEqual(send.call_count, 1)

    def test_send_failure_releases_slot(self):
        subject, text, html = auth._build_expired_winback_email_content(first_name='Ada')
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=False):
            self.assertFalse(auth._maybe_send_lifecycle_email(
                kind=auth._LIFECYCLE_KIND_EXPIRED_WINBACK,
                idem_key='expired_winback:sub_x',
                to_email='life@example.com',
                first_name='Ada',
                subject=subject,
                text_body=text,
                html_body=html,
                user_id=self.user_id,
            ))
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            self.assertTrue(auth._maybe_send_lifecycle_email(
                kind=auth._LIFECYCLE_KIND_EXPIRED_WINBACK,
                idem_key='expired_winback:sub_x',
                to_email='life@example.com',
                first_name='Ada',
                subject=subject,
                text_body=text,
                html_body=html,
                user_id=self.user_id,
            ))
            self.assertEqual(send.call_count, 1)

    def test_cancel_confirm_skips_when_not_canceling(self):
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            ok = auth._maybe_send_cancel_confirm_from_subscription({
                'id': 'sub_active',
                'customer': 'cus_life_1',
                'cancel_at_period_end': False,
                'status': 'active',
            }, event_id='evt_no_cancel')
        self.assertTrue(ok)
        self.assertEqual(send.call_count, 0)


class LifecycleWebhookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.whsec = 'whsec_test_lifecycle'
        self._prev_secret = auth.STRIPE_SECRET_KEY
        self._prev_wh = auth.STRIPE_WEBHOOK_SECRET
        auth.STRIPE_SECRET_KEY = 'sk_test_lifecycle'
        auth.STRIPE_WEBHOOK_SECRET = self.whsec
        auth._DB_PATH = self.db_path

        from flask import Flask
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-life-secret'
        self.app.config['TESTING'] = True
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, stripe_customer_id, is_premium) "
            "VALUES (?, ?, ?, 1)",
            ('life@example.com', 'Ada Lovelace', 'cus_life_1'),
        )
        conn.commit()
        self.user_id = conn.execute(
            "SELECT id FROM users WHERE email = ?", ('life@example.com',)
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

    def test_payment_failed_webhook_sends_once(self):
        event = {
            'id': 'evt_pay_fail_1',
            'object': 'event',
            'type': 'invoice.payment_failed',
            'data': {
                'object': {
                    'id': 'in_fail_1',
                    'customer': 'cus_life_1',
                    'customer_email': 'life@example.com',
                    'attempt_count': 1,
                }
            },
        }
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            r1 = self._post_event(event)
            r2 = self._post_event(event)
            r3 = self._post_event({**event, 'id': 'evt_pay_fail_2'})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 200)
        # Same invoice id → one send even across event ids
        self.assertEqual(send.call_count, 1)
        subject = (send.call_args.kwargs or {}).get('subject') or ''
        self.assertIn('unsuccessful', subject.lower())

    def test_invoice_upcoming_renewal_notice(self):
        period_end = int((datetime.now() + timedelta(days=3)).timestamp())
        event = {
            'id': 'evt_upcoming_1',
            'object': 'event',
            'type': 'invoice.upcoming',
            'data': {
                'object': {
                    'id': 'in_upcoming_1',
                    'customer': 'cus_life_1',
                    'customer_email': 'life@example.com',
                    'period_end': period_end,
                    'next_payment_attempt': period_end,
                }
            },
        }
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            resp = self._post_event(event)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(send.call_count, 1)
        subject = (send.call_args.kwargs or {}).get('subject') or ''
        self.assertIn('renewal', subject.lower())

    def test_subscription_updated_cancel_at_period_end(self):
        period_end = int((datetime.now() + timedelta(days=20)).timestamp())
        event = {
            'id': 'evt_cancel_sched_1',
            'object': 'event',
            'type': 'customer.subscription.updated',
            'data': {
                'object': {
                    'id': 'sub_cancel_1',
                    'object': 'subscription',
                    'customer': 'cus_life_1',
                    'status': 'active',
                    'cancel_at_period_end': True,
                    'current_period_end': period_end,
                    'items': {'data': [{'price': {'id': 'price_monthly_test'}}]},
                }
            },
        }
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            r1 = self._post_event(event)
            r2 = self._post_event({**event, 'id': 'evt_cancel_sched_2'})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(send.call_count, 1)
        subject = (send.call_args.kwargs or {}).get('subject') or ''
        self.assertIn('cancellation', subject.lower())

    def test_subscription_deleted_expired_winback(self):
        event = {
            'id': 'evt_deleted_1',
            'object': 'event',
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {
                    'id': 'sub_gone_1',
                    'object': 'subscription',
                    'customer': 'cus_life_1',
                    'status': 'canceled',
                }
            },
        }

        class _SubList:
            @staticmethod
            def list(**kwargs):
                return {'data': []}

        with mock.patch('stripe.Subscription', _SubList), \
             mock.patch.object(auth, '_smtp_send_html_email', return_value=True) as send:
            resp = self._post_event(event)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(send.call_count, 1)
        user = auth._load_user_by_id(self.user_id)
        self.assertFalse(user.premium_active)
        subject = (send.call_args.kwargs or {}).get('subject') or ''
        self.assertIn('ended', subject.lower())

    def test_email_smtp_failure_still_200(self):
        event = {
            'id': 'evt_fail_smtp',
            'object': 'event',
            'type': 'invoice.payment_failed',
            'data': {
                'object': {
                    'id': 'in_smtp_fail',
                    'customer': 'cus_life_1',
                    'customer_email': 'life@example.com',
                }
            },
        }
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=False):
            resp = self._post_event(event)
        self.assertEqual(resp.status_code, 200)


class ChangePasswordAccountTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        from flask import Flask
        from werkzeug.security import generate_password_hash

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'test-account-secret'
        self.app.config['TESTING'] = True
        self.app.template_folder = os.path.join(root, 'templates')
        auth._DB_PATH = self.db_path
        auth.init_auth(self.app, db_path=self.db_path)
        self.client = self.app.test_client()

        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            ('acct@example.com', 'Acct', generate_password_hash('oldpass99')),
        )
        conn.execute(
            "INSERT INTO users (email, name, google_id) VALUES (?, ?, ?)",
            ('gonly@example.com', 'GOnly', 'google-sub-acct'),
        )
        conn.commit()
        self.pw_uid = conn.execute(
            "SELECT id FROM users WHERE email='acct@example.com'"
        ).fetchone()[0]
        self.g_uid = conn.execute(
            "SELECT id FROM users WHERE email='gonly@example.com'"
        ).fetchone()[0]
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _login(self, email, password):
        return self.client.post(
            '/login',
            data={'email': email, 'password': password},
            follow_redirects=False,
        )

    def _force_login(self, user_id):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def test_routes_registered(self):
        rules = {str(r) for r in self.app.url_map.iter_rules()}
        self.assertIn('/account', rules)
        self.assertIn('/change-password', rules)

    def test_account_requires_login(self):
        resp = self.client.get('/account')
        self.assertIn(resp.status_code, (302, 401))

    def test_change_password_success(self):
        from werkzeug.security import check_password_hash

        self._login('acct@example.com', 'oldpass99')
        resp = self.client.post(
            '/account',
            data={
                'action': 'change_password',
                'current_password': 'oldpass99',
                'password': 'newpass88',
                'confirm': 'newpass88',
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('updated', html.lower())
        conn = auth._get_db()
        row = conn.execute(
            'SELECT password_hash FROM users WHERE id = ?', (self.pw_uid,)
        ).fetchone()
        conn.close()
        self.assertTrue(check_password_hash(row['password_hash'], 'newpass88'))
        self.client.get('/logout')
        login = self._login('acct@example.com', 'newpass88')
        self.assertIn(login.status_code, (302, 200, 303))

    def test_change_password_wrong_current(self):
        self._login('acct@example.com', 'oldpass99')
        resp = self.client.post(
            '/account',
            data={
                'action': 'change_password',
                'current_password': 'wrong',
                'password': 'newpass88',
                'confirm': 'newpass88',
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('incorrect', resp.get_data(as_text=True).lower())

    def test_google_only_can_set_password(self):
        from werkzeug.security import check_password_hash

        self._force_login(self.g_uid)
        resp = self.client.post(
            '/account',
            data={
                'action': 'change_password',
                'password': 'googlepass1',
                'confirm': 'googlepass1',
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('password', html.lower())
        conn = auth._get_db()
        row = conn.execute(
            'SELECT password_hash FROM users WHERE id = ?', (self.g_uid,)
        ).fetchone()
        conn.close()
        self.assertTrue(row and row['password_hash'])
        self.assertTrue(check_password_hash(row['password_hash'], 'googlepass1'))
        self.assertFalse(auth._user_is_google_only(self.g_uid))


if __name__ == '__main__':
    unittest.main()
