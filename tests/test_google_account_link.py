"""Google OAuth must land on the premium/email account (guest checkout link/merge)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from flask import Flask

import auth_system as auth


def _make_app(db_path):
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['TESTING'] = True
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.template_folder = os.path.join(root, 'templates')
    auth.init_auth(app, db_path=db_path)
    return app


class GoogleAccountLinkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.app = _make_app(self.db_path)
        self.client = self.app.test_client()
        auth._oauth = mock.MagicMock()

    def tearDown(self):
        auth._oauth = None
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _seed_premium_guest(self, email='hockmichael186@gmail.com'):
        expires = (datetime.now() + timedelta(days=20)).isoformat()
        conn = auth._get_db()
        conn.execute(
            "INSERT INTO users (email, name, is_premium, premium_expires, stripe_customer_id) "
            "VALUES (?, ?, 1, ?, ?)",
            (email, 'Hock', expires, 'cus_test_hock'),
        )
        conn.commit()
        uid = conn.execute(
            'SELECT id FROM users WHERE lower(email) = ?', (email,)
        ).fetchone()[0]
        conn.close()
        return uid, expires

    def _fake_google(self, email, sub, verified=True):
        token = {
            'userinfo': {
                'email': email,
                'email_verified': verified,
                'name': 'Hock Michael',
                'sub': sub,
            }
        }
        auth._oauth.google.authorize_access_token.return_value = token

    def test_links_google_to_existing_premium_email(self):
        uid, expires = self._seed_premium_guest()
        self._fake_google('HockMichael186@gmail.com', 'google-sub-hock-1')

        with self.app.test_request_context('/auth/google/callback'):
            resp = auth.google_callback()

        self.assertEqual(resp.status_code, 302)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
        conn.close()
        self.assertEqual(row['google_id'], 'google-sub-hock-1')
        self.assertEqual(row['email'], 'hockmichael186@gmail.com')
        self.assertEqual(row['is_premium'], 1)
        self.assertEqual(row['stripe_customer_id'], 'cus_test_hock')
        self.assertEqual(row['premium_expires'], expires)

    def test_merges_google_only_row_into_premium_email_row(self):
        uid_prem, expires = self._seed_premium_guest()
        conn = auth._get_db()
        # Simulate older bug: Google created a second empty row with same logical identity
        # but different email casing stored before normalize — use distinct email then
        # pretend google row has a temp email that later matches via sub+email path.
        conn.execute(
            "INSERT INTO users (email, name, google_id, is_premium) VALUES (?, ?, ?, 0)",
            ('hock.michael.google@gmail.com', 'G', 'google-sub-hock-2'),
        )
        conn.commit()
        gid_uid = conn.execute(
            "SELECT id FROM users WHERE google_id = 'google-sub-hock-2'"
        ).fetchone()[0]
        conn.close()

        # Google now returns the checkout email + existing sub → two rows must merge.
        # First attach: update google row email isn't automatic; callback sees by_email
        # (premium) and by_google (empty). Merge into premium.
        self._fake_google('hockmichael186@gmail.com', 'google-sub-hock-2')

        with self.app.test_request_context('/auth/google/callback'):
            resp = auth.google_callback()

        self.assertEqual(resp.status_code, 302)
        conn = auth._get_db()
        prem = conn.execute('SELECT * FROM users WHERE id = ?', (uid_prem,)).fetchone()
        donor = conn.execute('SELECT * FROM users WHERE id = ?', (gid_uid,)).fetchone()
        conn.close()
        self.assertEqual(prem['google_id'], 'google-sub-hock-2')
        self.assertEqual(prem['is_premium'], 1)
        self.assertEqual(prem['stripe_customer_id'], 'cus_test_hock')
        self.assertEqual(prem['premium_expires'], expires)
        self.assertEqual(prem['email'], 'hockmichael186@gmail.com')
        self.assertTrue(str(donor['email']).startswith('merged+'))
        self.assertIsNone(donor['google_id'])
        self.assertEqual(donor['is_premium'], 0)

    def test_google_login_session_is_premium(self):
        self._seed_premium_guest()
        self._fake_google('hockmichael186@gmail.com', 'google-sub-hock-3')

        with self.client as c:
            with mock.patch.object(auth, '_oauth', auth._oauth):
                # Hit callback through test client
                auth._oauth.google.authorize_access_token.return_value = {
                    'userinfo': {
                        'email': 'hockmichael186@gmail.com',
                        'email_verified': True,
                        'name': 'Hock',
                        'sub': 'google-sub-hock-3',
                    }
                }
                resp = c.get('/auth/google/callback', follow_redirects=False)
                self.assertIn(resp.status_code, (302, 200))
                # premium_active via loaded user
                user = auth._load_user_by_email('hockmichael186@gmail.com')
                self.assertTrue(user.premium_active)
                self.assertEqual(user.google_id, 'google-sub-hock-3')


if __name__ == '__main__':
    unittest.main()
