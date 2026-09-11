"""Tests for the expanded affiliate platform (Starter-tier feature set):
leads CRM, recommendations, quality scoring, campaigns + attribution, assets,
academy, contracts/terms, profiles, ledger, security/authorization.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth_system as auth
import affiliate_system as aff


def _sign(payload: bytes, secret: str) -> str:
    ts = int(time.time())
    body = payload.decode() if isinstance(payload, bytes) else payload
    sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


class ProBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        self.whsec = 'whsec_pro'
        self._pk, self._wh = auth.STRIPE_SECRET_KEY, auth.STRIPE_WEBHOOK_SECRET
        auth.STRIPE_SECRET_KEY, auth.STRIPE_WEBHOOK_SECRET = 'sk_test_pro', self.whsec
        auth._DB_PATH = self.db

        from flask import Flask
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(__name__, template_folder=os.path.join(root, 'templates'))
        self.app.config.update(SECRET_KEY='pro', TESTING=True)
        auth.init_auth(self.app, db_path=self.db)
        aff.init_affiliate(self.app, db_path=self.db)

        @self.app.context_processor
        def _ctx():
            return {'is_premium': True, 'is_logged_in': True,
                    'soccer_enabled': False, 'wnba_enabled': False}

        @self.app.route('/')
        def _i():
            return 'ok'

        @self.app.route('/plans')
        def _p():
            return 'plans'

        self.client = self.app.test_client()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY, auth.STRIPE_WEBHOOK_SECRET = self._pk, self._wh
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def _mkuser(self, email, is_premium=0):
        conn = auth._get_db()
        conn.execute('INSERT INTO users (email, name, is_premium) VALUES (?,?,?)',
                     (email, email.split('@')[0], is_premium))
        conn.commit()
        uid = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()['id']
        conn.close()
        return uid

    def _mkaff(self, code, user_id=None, status='approved', **cols):
        conn = auth._get_db()
        conn.execute('INSERT INTO affiliates (user_id, affiliate_code, status, name, email, commission_bps) '
                     'VALUES (?,?,?,?,?,1000)',
                     (user_id, code, status, code.title(), f'{code.lower()}@aff.com'))
        aid = conn.execute('SELECT id FROM affiliates WHERE affiliate_code=?', (code,)).fetchone()['id']
        for k, v in cols.items():
            conn.execute(f'UPDATE affiliates SET {k}=? WHERE id=?', (v, aid))
        conn.commit()
        conn.close()
        return aid

    def _login(self, uid):
        with self.client.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True

    def _csrf(self, tok='tok'):
        with self.client.session_transaction() as s:
            s['_aff_csrf'] = tok
        return tok

    def _admin_login(self):
        email = sorted(auth.ADMIN_EMAILS)[0]
        uid = self._mkuser(email)
        self._login(uid)
        return uid

    def _add_converted_customer(self, affiliate_id, cust, cents=1999, reason='subscription_create', invoice=None):
        conn = auth._get_db()
        cur = conn.execute(
            "INSERT INTO affiliate_attributions (affiliate_id, stripe_customer_id, status, converted_at, locked) "
            "VALUES (?,?, 'converted', datetime('now'), 1)", (affiliate_id, cust))
        attr_id = cur.lastrowid
        conn.execute(
            'INSERT INTO affiliate_commissions (affiliate_id, attribution_id, stripe_customer_id, '
            'stripe_invoice_id, amount_gross_cents, currency, commission_bps, commission_cents, status, '
            "billing_reason, eligible_at) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','-1 day'))",
            (affiliate_id, attr_id, cust, invoice or f'in_{cust}_{time.time_ns()}', cents, 'usd',
             1000, cents // 10, 'payable', reason))
        conn.commit()
        conn.close()

    def _add_clicks(self, affiliate_id, n, campaign=None):
        conn = auth._get_db()
        for i in range(n):
            conn.execute('INSERT INTO affiliate_clicks (affiliate_id, visitor_id, campaign) VALUES (?,?,?)',
                         (affiliate_id, f'v_{affiliate_id}_{i}_{time.time_ns()}', campaign))
        conn.commit()
        conn.close()


class LeadsTest(ProBase):
    def test_create_update_convert_lead(self):
        self._admin_login()
        tok = self._csrf()
        r = self.client.post('/admin/affiliates/leads', data={
            'csrf_token': tok, 'name': 'Big Creator', 'email': 'creator@x.com',
            'platform': 'YouTube', 'country': 'US', 'sports': 'NFL', 'status': 'prospect'})
        self.assertIn(r.status_code, (302, 303))
        conn = auth._get_db()
        lead = conn.execute("SELECT * FROM affiliate_leads WHERE email='creator@x.com'").fetchone()
        conn.close()
        self.assertIsNotNone(lead)
        lid = lead['id']
        r = self.client.post(f'/admin/affiliates/leads/{lid}', data={
            'csrf_token': tok, 'status': 'contacted', 'assigned_admin': 'me'})
        conn = auth._get_db()
        lead = conn.execute('SELECT * FROM affiliate_leads WHERE id=?', (lid,)).fetchone()
        conn.close()
        self.assertEqual(lead['status'], 'contacted')
        # Convert: link to an existing affiliate with the same email.
        self._mkaff('CREATOR', user_id=self._mkuser('creator@x.com2'))
        conn = auth._get_db()
        conn.execute("UPDATE affiliates SET email='creator@x.com' WHERE affiliate_code='CREATOR'")
        conn.commit()
        conn.close()
        self.client.post(f'/admin/affiliates/leads/{lid}/convert', data={'csrf_token': tok})
        conn = auth._get_db()
        lead = conn.execute('SELECT * FROM affiliate_leads WHERE id=?', (lid,)).fetchone()
        conn.close()
        self.assertEqual(lead['status'], 'converted')
        self.assertIsNotNone(lead['converted_affiliate_id'])

    def test_leads_require_admin(self):
        uid = self._mkuser('plain@x.com')
        self._login(uid)
        self.assertEqual(self.client.get('/admin/affiliates/leads').status_code, 403)


class RecommendationTest(ProBase):
    def test_quality_high_ranks_above_low_and_clicks_do_not_dominate(self):
        # Low quality: 5000 clicks, zero paying customers.
        low = self._mkaff('LOWQ')
        self._add_clicks(low, 300)  # lots of clicks
        # High quality: few clicks, several paying customers + sports profile.
        high = self._mkaff('HIGHQ', sports='NFL, NBA', primary_platform='YouTube',
                           existing_sports_audience=1)
        self._add_clicks(high, 30)
        for i in range(12):
            self._add_converted_customer(high, f'cus_h_{i}', cents=1999,
                                         reason='subscription_cycle' if i % 2 else 'subscription_create')
        s_low = aff._quality_score(low)[0]
        s_high = aff._quality_score(high)[0]
        self.assertGreater(s_high, s_low)
        self.assertLess(s_low, 40)          # clicks alone stay LOW
        self.assertGreaterEqual(s_high, 40)  # conversions lift the score

    def test_recommendation_priority(self):
        high = self._mkaff('RH', sports='NFL', existing_sports_audience=1,
                           audience_size=200000, primary_countries='US, Canada',
                           primary_platform='YouTube')
        low = self._mkaff('RL')
        conn = auth._get_db()
        try:
            ph = aff._recommendation(aff._affiliate_by_id(high, conn=conn), conn=conn)[0]
            pl = aff._recommendation(aff._affiliate_by_id(low, conn=conn), conn=conn)[0]
        finally:
            conn.close()
        order = {'high': 0, 'medium': 1, 'low': 2}
        self.assertLessEqual(order[ph], order[pl])
        self.assertEqual(ph, 'high')

    def test_reco_page_admin_only(self):
        uid = self._mkuser('u@x.com')
        self._login(uid)
        self.assertEqual(self.client.get('/admin/affiliates/recommendations').status_code, 403)


class CampaignTest(ProBase):
    def test_campaign_attribution_preserves_affiliate(self):
        aid = self._mkaff('JOSH')
        r = self.client.get('/?ref=JOSH&utm_campaign=MLB Opening Week')
        self.assertEqual(r.status_code, 200)
        conn = auth._get_db()
        attr = conn.execute('SELECT * FROM affiliate_attributions WHERE affiliate_id=?', (aid,)).fetchone()
        click = conn.execute('SELECT * FROM affiliate_clicks WHERE affiliate_id=?', (aid,)).fetchone()
        conn.close()
        self.assertIsNotNone(attr)
        self.assertEqual(attr['affiliate_id'], aid)          # correct affiliate credited
        self.assertEqual(attr['campaign'], 'mlb-opening-week')  # campaign tagged
        self.assertEqual(click['campaign'], 'mlb-opening-week')

    def test_commission_carries_campaign(self):
        aid = self._mkaff('JOSH')
        self.client.get('/?ref=JOSH&utm_campaign=yt')
        # Now a webhook invoice paid for that visitor's customer via metadata code.
        payload = json.dumps({
            'id': 'evt_c', 'object': 'event', 'type': 'invoice.paid',
            'data': {'object': {'id': 'in_camp', 'customer': 'cus_z', 'subscription': 'sub_z',
                                'amount_paid': 1999, 'currency': 'usd', 'billing_reason': 'subscription_create',
                                'metadata': {'affiliate_code': 'JOSH', 'affiliate_visitor_id':
                                             self.client.get('/').request.cookies.get('pl_aff_vid') if False else ''}}},
        }).encode()
        # Simpler: attribute the customer directly then pay.
        conn = auth._get_db()
        conn.execute("UPDATE affiliate_attributions SET stripe_customer_id='cus_z' WHERE affiliate_id=?", (aid,))
        conn.commit()
        conn.close()
        self.client.post('/stripe/webhook', data=payload, headers={
            'Stripe-Signature': _sign(payload, self.whsec), 'Content-Type': 'application/json'})
        conn = auth._get_db()
        c = conn.execute("SELECT * FROM affiliate_commissions WHERE stripe_invoice_id='in_camp'").fetchone()
        conn.close()
        self.assertIsNotNone(c)
        self.assertEqual(c['campaign'], 'yt')

    def test_affiliate_cannot_edit_admin_campaign_brief(self):
        uid = self._mkuser('aff@x.com')
        self._mkaff('AF', user_id=uid)
        self._login(uid)
        tok = self._csrf()
        r = self.client.post('/admin/affiliates/briefs', data={'csrf_token': tok, 'title': 'Sneaky'})
        self.assertEqual(r.status_code, 403)

    def test_affiliate_sees_active_brief(self):
        self._admin_login()
        tok = self._csrf()
        self.client.post('/admin/affiliates/briefs', data={'csrf_token': tok, 'title': 'MLB Season',
                                                            'description': 'Promote MLB'})
        # Switch to an affiliate and load campaigns page.
        uid = self._mkuser('viewer@x.com')
        self._mkaff('VIEW', user_id=uid)
        self._login(uid)
        r = self.client.get('/affiliate/campaigns')
        self.assertEqual(r.status_code, 200)
        self.assertIn('MLB Season', r.get_data(as_text=True))


class AssetTest(ProBase):
    def test_admin_create_asset_affiliate_sees_only_active(self):
        self._admin_login()
        tok = self._csrf()
        self.client.post('/admin/affiliates/assets', data={'csrf_token': tok, 'title': 'Live Banner',
                                                           'body': 'Use PredictionLab', 'asset_type': 'banner'})
        self.client.post('/admin/affiliates/assets', data={'csrf_token': tok, 'title': 'Hidden Banner',
                                                           'body': 'secret', 'asset_type': 'banner'})
        conn = auth._get_db()
        hid = conn.execute("SELECT id FROM affiliate_assets WHERE title='Hidden Banner'").fetchone()['id']
        conn.close()
        self.client.post(f'/admin/affiliates/assets/{hid}/toggle', data={'csrf_token': tok})  # hide
        uid = self._mkuser('a@x.com')
        self._mkaff('AA', user_id=uid)
        self._login(uid)
        html = self.client.get('/affiliate/assets').get_data(as_text=True)
        self.assertIn('Live Banner', html)
        self.assertNotIn('Hidden Banner', html)

    def test_asset_admin_only(self):
        uid = self._mkuser('a2@x.com')
        self._login(uid)
        self.assertEqual(self.client.get('/admin/affiliates/assets').status_code, 403)


class TermsTest(ProBase):
    def test_apply_records_terms_acceptance(self):
        uid = self._mkuser('t@x.com')
        self._login(uid)
        tok = self._csrf()
        r = self.client.post('/affiliate/apply', data={
            'csrf_token': tok, 'name': 'Tester', 'code': 'TESTER', 'agree_terms': 'on'})
        self.assertIn(r.status_code, (302, 303))
        conn = auth._get_db()
        a = conn.execute("SELECT id FROM affiliates WHERE affiliate_code='TESTER'").fetchone()
        acc = conn.execute('SELECT * FROM affiliate_terms_acceptances WHERE affiliate_id=?', (a['id'],)).fetchone()
        conn.close()
        self.assertIsNotNone(acc)
        self.assertEqual(acc['version'], aff.AFFILIATE_TERMS_VERSION)

    def test_updated_terms_require_reacceptance(self):
        aid = self._mkaff('TV', user_id=self._mkuser('tv@x.com'))
        conn = auth._get_db()
        conn.execute('INSERT INTO affiliate_terms_acceptances (affiliate_id, version) VALUES (?,?)',
                     (aid, aff.AFFILIATE_TERMS_VERSION))
        conn.commit()
        conn.close()
        old = aff.AFFILIATE_TERMS_VERSION
        try:
            aff.AFFILIATE_TERMS_VERSION = 'v-new'
            conn = auth._get_db()
            conn.execute('INSERT INTO affiliate_terms_versions (version, requires_reaccept) VALUES (?,1)', ('v-new',))
            conn.commit()
            conn.close()
            self.assertTrue(aff._affiliate_needs_terms(aid))
            conn = auth._get_db()
            aff._record_terms_acceptance(aid, None, conn)
            conn.commit()
            conn.close()
            self.assertFalse(aff._affiliate_needs_terms(aid))
        finally:
            aff.AFFILIATE_TERMS_VERSION = old


class ProfileTest(ProBase):
    def test_edit_allowed_fields_only(self):
        uid = self._mkuser('p@x.com')
        aid = self._mkaff('PF', user_id=uid, commission_bps=1000, status='approved')
        self._login(uid)
        tok = self._csrf()
        r = self.client.post('/affiliate/profile', data={
            'csrf_token': tok, 'name': 'New Name', 'sports': 'NHL',
            'commission_bps': '9999', 'status': 'suspended', 'affiliate_code': 'HACK'})
        self.assertIn(r.status_code, (302, 303))
        conn = auth._get_db()
        a = conn.execute('SELECT * FROM affiliates WHERE id=?', (aid,)).fetchone()
        conn.close()
        self.assertEqual(a['name'], 'New Name')
        self.assertEqual(a['sports'], 'NHL')
        self.assertEqual(a['commission_bps'], 1000)   # unchanged
        self.assertEqual(a['status'], 'approved')      # unchanged
        self.assertEqual(a['affiliate_code'], 'PF')    # unchanged

    def test_profile_csrf_required(self):
        uid = self._mkuser('p2@x.com')
        self._mkaff('PF2', user_id=uid)
        self._login(uid)
        with self.client.session_transaction() as s:
            s['_aff_csrf'] = 'real'
        r = self.client.post('/affiliate/profile', data={'csrf_token': 'bad', 'name': 'x'})
        self.assertEqual(r.status_code, 400)


class LedgerTest(ProBase):
    def test_ledger_reconciles_and_paid_reduces_payable(self):
        uid = self._mkuser('l@x.com')
        aid = self._mkaff('LG', user_id=uid)
        for i in range(6):
            self._add_converted_customer(aid, f'cus_l_{i}', cents=20000)  # 2000 commission each
        led = aff._ledger(aid)
        self.assertEqual(led['payable'], 12000)        # 6 * 2000 = $120 (>= $100 threshold)
        self.assertEqual(led['current'], led['pending'] + led['payable'])
        # Request payout, admin marks paid.
        self._login(uid)
        tok = self._csrf()
        self.client.post('/affiliate/payouts/request', data={'csrf_token': tok})
        conn = auth._get_db()
        pid = conn.execute('SELECT id FROM affiliate_payouts WHERE affiliate_id=?', (aid,)).fetchone()['id']
        conn.close()
        self._admin_login()
        self._csrf(tok)
        self.client.post(f'/admin/affiliates/payouts/{pid}/status', data={'csrf_token': tok, 'status': 'paid'})
        led = aff._ledger(aid)
        self.assertEqual(led['payable'], 0)
        self.assertEqual(led['paid'], 12000)

    def test_reversal_reduces_balance(self):
        aid = self._mkaff('RV')
        self._add_converted_customer(aid, 'cus_rv', cents=2000, invoice='in_rv')
        conn = auth._get_db()
        conn.execute("UPDATE affiliate_commissions SET stripe_charge_id='ch_rv', stripe_payment_intent_id='pi_rv' "
                     "WHERE stripe_invoice_id='in_rv'")
        conn.commit()
        conn.close()
        before = aff._ledger(aid)['payable']
        refund = json.dumps({'id': 'evt_rv', 'object': 'event', 'type': 'charge.refunded',
                             'data': {'object': {'id': 'ch_rv', 'payment_intent': 'pi_rv',
                                                 'invoice': 'in_rv', 'amount_refunded': 2000}}}).encode()
        self.client.post('/stripe/webhook', data=refund, headers={
            'Stripe-Signature': _sign(refund, self.whsec), 'Content-Type': 'application/json'})
        led = aff._ledger(aid)
        self.assertEqual(before, 200)
        self.assertEqual(led['payable'], 0)
        self.assertEqual(led['reversed'], 200)


class SecurityTest(ProBase):
    def test_non_admin_blocked_from_admin_partner_data(self):
        uid = self._mkuser('s@x.com')
        self._mkaff('SEC', user_id=uid)
        self._login(uid)
        for path in ('/admin/affiliates', '/admin/affiliates/leads', '/admin/affiliates/reports',
                     '/admin/affiliates/recommendations', '/admin/affiliates/assets',
                     '/admin/affiliates/briefs', '/admin/affiliates/academy', '/admin/affiliates/terms'):
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_internal_notes_not_visible_to_affiliate(self):
        # Admin adds an internal note to an affiliate.
        auid = self._mkuser('owner@x.com')
        aid = self._mkaff('NOTE', user_id=auid)
        self._admin_login()
        tok = self._csrf()
        self.client.post(f'/admin/affiliates/{aid}/note',
                         data={'csrf_token': tok, 'body': 'SECRET-INTERNAL-NOTE'})
        # The affiliate views their own dashboard/profile — note must not appear.
        self._login(auid)
        for path in ('/affiliate/dashboard', '/affiliate/profile', '/affiliate/commissions'):
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn('SECRET-INTERNAL-NOTE', html)
        # And an affiliate cannot post notes.
        self._csrf(tok)
        self.assertEqual(
            self.client.post(f'/admin/affiliates/{aid}/note', data={'csrf_token': tok, 'body': 'x'}).status_code,
            403)

    def test_affiliate_cannot_change_status_via_admin_route(self):
        uid = self._mkuser('e@x.com')
        aid = self._mkaff('ESC', user_id=uid, status='approved')
        self._login(uid)
        tok = self._csrf()
        r = self.client.post(f'/admin/affiliates/{aid}/status',
                             data={'csrf_token': tok, 'action': 'suspend'})
        self.assertEqual(r.status_code, 403)
        conn = auth._get_db()
        a = conn.execute('SELECT status FROM affiliates WHERE id=?', (aid,)).fetchone()
        conn.close()
        self.assertEqual(a['status'], 'approved')


if __name__ == '__main__':
    unittest.main()
