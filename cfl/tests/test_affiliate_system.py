"""Affiliate platform: attribution, commissions, refunds, payouts, authorization.

Drives the real webhook endpoint (signature verify + dispatch in
auth_system.stripe_webhook) and the affiliate request lifecycle
(before/after_request) via a minimal Flask app that wires init_auth +
init_affiliate against a temp SQLite DB (same DB, like production).
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


class AffiliateBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self._tmp.close()
        self.db = self._tmp.name
        self.whsec = 'whsec_aff_test'
        self._pk, self._wh = auth.STRIPE_SECRET_KEY, auth.STRIPE_WEBHOOK_SECRET
        auth.STRIPE_SECRET_KEY = 'sk_test_aff'
        auth.STRIPE_WEBHOOK_SECRET = self.whsec
        auth._DB_PATH = self.db

        from flask import Flask
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY='aff-test', TESTING=True)
        auth.init_auth(self.app, db_path=self.db)
        aff.init_affiliate(self.app, db_path=self.db)

        @self.app.route('/')
        def _index():
            return 'ok'

        self.client = self.app.test_client()

    def tearDown(self):
        auth.STRIPE_SECRET_KEY, auth.STRIPE_WEBHOOK_SECRET = self._pk, self._wh
        try:
            os.unlink(self.db)
        except OSError:
            pass

    # helpers -----------------------------------------------------------------
    def _mkuser(self, email, is_premium=0, customer=None, sub=None):
        conn = auth._get_db()
        conn.execute(
            'INSERT INTO users (email, name, is_premium, stripe_customer_id, stripe_subscription_id) '
            'VALUES (?,?,?,?,?)',
            (email, email.split('@')[0], is_premium, customer, sub),
        )
        conn.commit()
        uid = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()['id']
        conn.close()
        return uid

    def _mkaffiliate(self, code, user_id=None, status='approved', bps=1000):
        conn = auth._get_db()
        conn.execute(
            'INSERT INTO affiliates (user_id, affiliate_code, status, name, email, commission_bps) '
            'VALUES (?,?,?,?,?,?)',
            (user_id, code, status, code.title(), f'{code.lower()}@aff.com', bps),
        )
        conn.commit()
        aid = conn.execute('SELECT id FROM affiliates WHERE affiliate_code=?', (code,)).fetchone()['id']
        conn.close()
        return aid

    def _login(self, uid):
        with self.client.session_transaction() as s:
            s['_user_id'] = str(uid)
            s['_fresh'] = True

    def _logout(self):
        with self.client.session_transaction() as s:
            s.clear()

    def _post_event(self, event):
        payload = json.dumps(event).encode()
        return self.client.post('/stripe/webhook', data=payload, headers={
            'Stripe-Signature': _sign(payload, self.whsec),
            'Content-Type': 'application/json',
        })

    def _invoice_event(self, etype, invoice_id, *, amount=1999, code=None,
                       customer='cus_x', sub='sub_x', charge='ch_x', pi='pi_x',
                       reason='subscription_cycle', event_id=None):
        obj = {
            'id': invoice_id, 'object': 'invoice', 'customer': customer,
            'subscription': sub, 'amount_paid': amount, 'currency': 'usd',
            'billing_reason': reason, 'charge': charge, 'payment_intent': pi,
        }
        if code:
            obj['metadata'] = {'affiliate_code': code}
        return {
            'id': event_id or f'evt_{invoice_id}', 'object': 'event',
            'type': etype, 'data': {'object': obj},
        }


class AttributionTest(AffiliateBase):
    def test_click_records_attribution(self):  # TEST 1
        self._mkaffiliate('TESTAFF')
        r = self.client.get('/?ref=TESTAFF')
        self.assertEqual(r.status_code, 200)
        conn = auth._get_db()
        clicks = conn.execute('SELECT COUNT(*) c FROM affiliate_clicks').fetchone()['c']
        attr = conn.execute('SELECT * FROM affiliate_attributions').fetchone()
        conn.close()
        self.assertEqual(clicks, 1)
        self.assertIsNotNone(attr)
        self.assertEqual(attr['status'], 'pending')

    def test_unknown_or_unapproved_code_no_attribution(self):
        self._mkaffiliate('PENDINGAFF', status='pending')
        self.client.get('/?ref=PENDINGAFF')
        self.client.get('/?ref=NOEXIST')
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_attributions').fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    def test_signup_association_follows_user(self):  # TEST 2
        self._mkaffiliate('TESTAFF')
        uid = self._mkuser('cust@example.com')
        self.client.get('/?ref=TESTAFF')       # sets visitor cookie + attribution
        self._login(uid)
        self.client.get('/')                    # association runs
        conn = auth._get_db()
        attr = conn.execute('SELECT * FROM affiliate_attributions WHERE user_id=?', (uid,)).fetchone()
        conn.close()
        self.assertIsNotNone(attr)
        self.assertEqual(attr['status'], 'signed_up')

    def test_existing_customer_not_retro_attributed(self):
        self._mkaffiliate('TESTAFF')
        uid = self._mkuser('old@example.com', is_premium=1, sub='sub_old')
        self.client.get('/?ref=TESTAFF')
        self._login(uid)
        self.client.get('/')
        conn = auth._get_db()
        attr = conn.execute('SELECT * FROM affiliate_attributions WHERE user_id=?', (uid,)).fetchone()
        conn.close()
        self.assertIsNone(attr)  # protected

    def test_self_referral_blocked(self):  # TEST 9
        auid = self._mkuser('affuser@example.com')
        self._mkaffiliate('OWN', user_id=auid)
        self._login(auid)
        self.client.get('/?ref=OWN')
        conn = auth._get_db()
        attr = conn.execute('SELECT * FROM affiliate_attributions WHERE user_id=?', (auid,)).fetchone()
        audit = conn.execute("SELECT * FROM affiliate_audit WHERE kind='self_click'").fetchone()
        conn.close()
        self.assertIsNone(attr)
        self.assertIsNotNone(audit)


class CommissionTest(AffiliateBase):
    def test_commission_created_via_metadata(self):  # TEST 3
        self._mkaffiliate('TESTAFF')
        r = self._post_event(self._invoice_event(
            'invoice.paid', 'in_1', amount=1999, code='TESTAFF', reason='subscription_create'))
        self.assertEqual(r.status_code, 200)
        conn = auth._get_db()
        c = conn.execute('SELECT * FROM affiliate_commissions').fetchone()
        conn.close()
        self.assertIsNotNone(c)
        self.assertEqual(c['amount_gross_cents'], 1999)
        self.assertEqual(c['commission_cents'], 199)  # 10% integer math
        self.assertEqual(c['status'], 'pending')

    def test_duplicate_webhook_idempotent(self):  # TEST 4
        self._mkaffiliate('TESTAFF')
        e = self._invoice_event('invoice.paid', 'in_dup', code='TESTAFF')
        self._post_event(e)
        self._post_event(e)                                  # same event id
        self._post_event({**e, 'id': 'evt_dup_2'})           # different event, same invoice
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_commissions WHERE stripe_invoice_id=?', ('in_dup',)).fetchone()['c']
        conn.close()
        self.assertEqual(n, 1)

    def test_recurring_creates_second_commission(self):  # TEST 5
        self._mkaffiliate('TESTAFF')
        self._post_event(self._invoice_event('invoice.paid', 'in_a', code='TESTAFF', reason='subscription_create'))
        self._post_event(self._invoice_event('invoice.paid', 'in_b', code='TESTAFF', reason='subscription_cycle'))
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_commissions').fetchone()['c']
        conn.close()
        self.assertEqual(n, 2)

    def test_failed_payment_no_commission(self):  # TEST 6
        self._mkaffiliate('TESTAFF')
        with mock.patch.object(auth, '_smtp_send_html_email', return_value=True):
            self._post_event(self._invoice_event('invoice.payment_failed', 'in_f', code='TESTAFF'))
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_commissions').fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    def test_zero_amount_no_commission(self):
        self._mkaffiliate('TESTAFF')
        self._post_event(self._invoice_event('invoice.paid', 'in_zero', amount=0, code='TESTAFF'))
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_commissions').fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    def test_refund_reverses_commission(self):  # TEST 7
        self._mkaffiliate('TESTAFF')
        self._post_event(self._invoice_event('invoice.paid', 'in_r', amount=1999, code='TESTAFF',
                                             charge='ch_r', pi='pi_r'))
        refund = {
            'id': 'evt_refund', 'object': 'event', 'type': 'charge.refunded',
            'data': {'object': {'id': 'ch_r', 'object': 'charge', 'payment_intent': 'pi_r',
                                'invoice': 'in_r', 'amount_refunded': 1999}},
        }
        self._post_event(refund)
        conn = auth._get_db()
        c = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_invoice_id=?', ('in_r',)).fetchone()
        conn.close()
        self.assertEqual(c['status'], 'reversed')

    def test_partial_refund_adjusts_commission(self):
        self._mkaffiliate('TESTAFF')
        self._post_event(self._invoice_event('invoice.paid', 'in_p', amount=2000, code='TESTAFF',
                                             charge='ch_p', pi='pi_p'))
        refund = {
            'id': 'evt_refund_p', 'object': 'event', 'type': 'charge.refunded',
            'data': {'object': {'id': 'ch_p', 'payment_intent': 'pi_p', 'invoice': 'in_p',
                                'amount_refunded': 1000}},
        }
        self._post_event(refund)
        conn = auth._get_db()
        c = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_invoice_id=?', ('in_p',)).fetchone()
        conn.close()
        self.assertEqual(c['status'], 'pending')       # not fully reversed
        self.assertEqual(c['commission_cents'], 100)   # 10% of remaining 1000

    def test_dispute_reverses_commission(self):
        self._mkaffiliate('TESTAFF')
        self._post_event(self._invoice_event('invoice.paid', 'in_d', code='TESTAFF', charge='ch_d', pi='pi_d'))
        dispute = {
            'id': 'evt_dispute', 'object': 'event', 'type': 'charge.dispute.created',
            'data': {'object': {'id': 'dp_1', 'charge': 'ch_d', 'payment_intent': 'pi_d'}},
        }
        self._post_event(dispute)
        conn = auth._get_db()
        c = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_invoice_id=?', ('in_d',)).fetchone()
        conn.close()
        self.assertEqual(c['status'], 'reversed')

    def test_suspended_affiliate_no_new_commission(self):  # TEST 13
        self._mkaffiliate('SUSP', status='suspended')
        self._post_event(self._invoice_event('invoice.paid', 'in_s', code='SUSP'))
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_commissions').fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    def test_commission_via_customer_attribution(self):
        aid = self._mkaffiliate('TESTAFF')
        uid = self._mkuser('buyer@example.com', customer='cus_buyer')
        # Pre-existing attribution linked to the customer.
        conn = auth._get_db()
        conn.execute('INSERT INTO affiliate_attributions (affiliate_id, user_id, stripe_customer_id, status) '
                     'VALUES (?,?,?,?)', (aid, uid, 'cus_buyer', 'signed_up'))
        conn.commit()
        conn.close()
        self._post_event(self._invoice_event('invoice.paid', 'in_cust', customer='cus_buyer', code=None))
        conn = auth._get_db()
        c = conn.execute('SELECT * FROM affiliate_commissions WHERE stripe_invoice_id=?', ('in_cust',)).fetchone()
        attr = conn.execute('SELECT * FROM affiliate_attributions WHERE id=?', (c['attribution_id'],)).fetchone()
        conn.close()
        self.assertIsNotNone(c)
        self.assertEqual(attr['status'], 'converted')
        self.assertEqual(attr['locked'], 1)


class CouponTest(AffiliateBase):
    def test_valid_code_applies_attribution(self):
        aid = self._mkaffiliate('JOSH')
        uid = self._mkuser('c1@example.com')
        ok, reason = aff.apply_code('JOSH', user_id=uid)
        self.assertTrue(ok)
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM affiliate_attributions WHERE user_id=?', (uid,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row['affiliate_id'], aid)
        self.assertEqual(row['status'], 'signed_up')

    def test_invalid_code_rejected(self):
        uid = self._mkuser('c2@example.com')
        ok, reason = aff.apply_code('NOPE', user_id=uid)
        self.assertFalse(ok)
        self.assertEqual(reason, 'invalid')
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_attributions').fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    def test_unapproved_code_rejected(self):
        self._mkaffiliate('PEND', status='pending')
        uid = self._mkuser('c2b@example.com')
        ok, reason = aff.apply_code('PEND', user_id=uid)
        self.assertFalse(ok)
        self.assertEqual(reason, 'invalid')

    def test_code_attribution_new_customer_via_visitor(self):
        aid = self._mkaffiliate('JOSH')
        ok, reason = aff.apply_code('JOSH', visitor_id='visX')
        self.assertTrue(ok)
        conn = auth._get_db()
        row = conn.execute("SELECT * FROM affiliate_attributions WHERE visitor_id='visX'").fetchone()
        conn.close()
        self.assertEqual(row['affiliate_id'], aid)
        self.assertEqual(row['status'], 'pending')

    def test_code_does_not_overwrite_existing_referral(self):
        a1 = self._mkaffiliate('AONE')
        a2 = self._mkaffiliate('ATWO')
        uid = self._mkuser('c3@example.com')
        # Existing referral-link attribution to AONE.
        conn = auth._get_db()
        conn.execute('INSERT INTO affiliate_attributions (affiliate_id, user_id, status) VALUES (?,?,?)',
                     (a1, uid, 'signed_up'))
        conn.commit()
        conn.close()
        ok, reason = aff.apply_code('ATWO', user_id=uid)
        self.assertFalse(ok)
        self.assertEqual(reason, 'already_attributed')
        conn = auth._get_db()
        row = conn.execute('SELECT * FROM affiliate_attributions WHERE user_id=?', (uid,)).fetchone()
        conn.close()
        self.assertEqual(row['affiliate_id'], a1)  # unchanged — no hijack

    def test_code_no_retro_attribution_existing_customer(self):
        self._mkaffiliate('JOSH')
        uid = self._mkuser('paying@example.com', is_premium=1, sub='sub_pre')
        ok, reason = aff.apply_code('JOSH', user_id=uid)
        self.assertFalse(ok)
        self.assertEqual(reason, 'existing_customer')

    def test_self_referral_code_blocked(self):
        auid = self._mkuser('affself@example.com')
        self._mkaffiliate('OWN', user_id=auid)
        ok, reason = aff.apply_code('OWN', user_id=auid)
        self.assertFalse(ok)
        self.assertEqual(reason, 'self')

    def test_code_attributed_subscription_generates_commission(self):
        self._mkaffiliate('JOSH')
        uid = self._mkuser('buyer2@example.com', customer='cus_j2')
        ok, _ = aff.apply_code('JOSH', user_id=uid)
        self.assertTrue(ok)
        # Link the attribution to the customer so the webhook resolves it.
        conn = auth._get_db()
        conn.execute('UPDATE affiliate_attributions SET stripe_customer_id=? WHERE user_id=?', ('cus_j2', uid))
        conn.commit()
        conn.close()
        self._post_event(self._invoice_event('invoice.paid', 'in_code1', customer='cus_j2', code=None))
        conn = auth._get_db()
        c = conn.execute("SELECT * FROM affiliate_commissions WHERE stripe_invoice_id='in_code1'").fetchone()
        conn.close()
        self.assertIsNotNone(c)
        self.assertEqual(c['commission_cents'], 199)

    def test_code_commission_duplicate_webhook_idempotent(self):
        self._mkaffiliate('JOSH')
        e = self._invoice_event('invoice.paid', 'in_code_dup', code='JOSH')
        self._post_event(e)
        self._post_event({**e, 'id': 'evt_code_dup2'})
        conn = auth._get_db()
        n = conn.execute("SELECT COUNT(*) c FROM affiliate_commissions WHERE stripe_invoice_id='in_code_dup'").fetchone()['c']
        conn.close()
        self.assertEqual(n, 1)

    def test_refund_reverses_code_attributed_commission(self):
        self._mkaffiliate('JOSH')
        self._post_event(self._invoice_event('invoice.paid', 'in_code_r', code='JOSH', charge='ch_cr', pi='pi_cr'))
        refund = {'id': 'evt_cr', 'object': 'event', 'type': 'charge.refunded',
                  'data': {'object': {'id': 'ch_cr', 'payment_intent': 'pi_cr', 'invoice': 'in_code_r',
                                      'amount_refunded': 1999}}}
        self._post_event(refund)
        conn = auth._get_db()
        c = conn.execute("SELECT * FROM affiliate_commissions WHERE stripe_invoice_id='in_code_r'").fetchone()
        conn.close()
        self.assertEqual(c['status'], 'reversed')

    def test_dispute_funds_withdrawn_reverses_commission(self):
        self._mkaffiliate('JOSH')
        self._post_event(self._invoice_event('invoice.paid', 'in_fw', code='JOSH', charge='ch_fw', pi='pi_fw'))
        ev = {'id': 'evt_fw', 'object': 'event', 'type': 'charge.dispute.funds_withdrawn',
              'data': {'object': {'id': 'dp_fw', 'charge': 'ch_fw', 'payment_intent': 'pi_fw'}}}
        self._post_event(ev)
        conn = auth._get_db()
        c = conn.execute("SELECT * FROM affiliate_commissions WHERE stripe_invoice_id='in_fw'").fetchone()
        conn.close()
        self.assertEqual(c['status'], 'reversed')


class PayoutTest(AffiliateBase):
    def _add_payable(self, affiliate_id, cents):
        conn = auth._get_db()
        conn.execute(
            'INSERT INTO affiliate_commissions (affiliate_id, amount_gross_cents, currency, '
            'commission_bps, commission_cents, status, stripe_invoice_id) VALUES (?,?,?,?,?,?,?)',
            (affiliate_id, cents * 10, 'usd', 1000, cents, 'payable', f'in_{cents}_{time.time_ns()}'),
        )
        conn.commit()
        conn.close()

    def _csrf(self):
        with self.client.session_transaction() as s:
            s['_aff_csrf'] = 'tok'
        return 'tok'

    def test_payout_blocked_below_threshold(self):  # TEST 10
        auid = self._mkuser('a99@example.com')
        aid = self._mkaffiliate('AFF99', user_id=auid)
        self._add_payable(aid, 9900)  # $99
        self._login(auid)
        tok = self._csrf()
        r = self.client.post('/affiliate/payouts/request', data={'csrf_token': tok})
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_payouts').fetchone()['c']
        conn.close()
        self.assertEqual(n, 0)

    def test_payout_available_at_threshold(self):  # TEST 11
        auid = self._mkuser('a100@example.com')
        aid = self._mkaffiliate('AFF100', user_id=auid)
        self._add_payable(aid, 10000)  # $100
        self._login(auid)
        tok = self._csrf()
        r = self.client.post('/affiliate/payouts/request', data={'csrf_token': tok},
                             follow_redirects=False)
        conn = auth._get_db()
        p = conn.execute('SELECT * FROM affiliate_payouts').fetchone()
        linked = conn.execute('SELECT COUNT(*) c FROM affiliate_commissions WHERE payout_id IS NOT NULL').fetchone()['c']
        conn.close()
        self.assertIsNotNone(p)
        self.assertEqual(p['amount_cents'], 10000)
        self.assertEqual(p['status'], 'requested')
        self.assertEqual(linked, 1)

    def test_payout_csrf_required(self):
        auid = self._mkuser('acsrf@example.com')
        aid = self._mkaffiliate('AFFC', user_id=auid)
        self._add_payable(aid, 20000)
        self._login(auid)
        with self.client.session_transaction() as s:
            s['_aff_csrf'] = 'real'
        r = self.client.post('/affiliate/payouts/request', data={'csrf_token': 'wrong'})
        self.assertEqual(r.status_code, 400)

    def test_no_duplicate_open_payout(self):
        auid = self._mkuser('adup@example.com')
        aid = self._mkaffiliate('AFFD', user_id=auid)
        self._add_payable(aid, 15000)
        self._login(auid)
        tok = self._csrf()
        self.client.post('/affiliate/payouts/request', data={'csrf_token': tok})
        self._add_payable(aid, 15000)
        self.client.post('/affiliate/payouts/request', data={'csrf_token': tok})
        conn = auth._get_db()
        n = conn.execute('SELECT COUNT(*) c FROM affiliate_payouts').fetchone()['c']
        conn.close()
        self.assertEqual(n, 1)  # second blocked while first open


class AdminAuthTest(AffiliateBase):
    def test_admin_route_forbidden_for_non_admin(self):  # TEST 8-style authz
        uid = self._mkuser('plainuser@example.com')
        self._login(uid)
        r = self.client.get('/admin/affiliates')
        self.assertEqual(r.status_code, 403)

    def test_admin_status_change_and_maturation(self):  # TEST 12/13
        admin_email = sorted(auth.ADMIN_EMAILS)[0]
        admin_uid = self._mkuser(admin_email)
        aid = self._mkaffiliate('NEWAFF', status='pending')
        self._login(admin_uid)
        with self.client.session_transaction() as s:
            s['_aff_csrf'] = 'tok'
        with mock.patch.object(auth, '_smtp_send_text_email', return_value=True):
            r = self.client.post(f'/admin/affiliates/{aid}/status',
                                 data={'csrf_token': 'tok', 'action': 'approve'})
        conn = auth._get_db()
        a = conn.execute('SELECT * FROM affiliates WHERE id=?', (aid,)).fetchone()
        conn.close()
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(a['status'], 'approved')
        self.assertIsNotNone(a['approved_at'])

    def test_maturation_flips_pending_to_payable(self):
        aid = self._mkaffiliate('MAT')
        conn = auth._get_db()
        conn.execute(
            'INSERT INTO affiliate_commissions (affiliate_id, amount_gross_cents, currency, '
            'commission_bps, commission_cents, status, eligible_at, stripe_invoice_id) '
            "VALUES (?,?,?,?,?,?,datetime('now','-1 day'),?)",
            (aid, 1999, 'usd', 1000, 199, 'pending', 'in_mat'),
        )
        conn.commit()
        conn.close()
        aff._mature_commissions(aid)
        conn = auth._get_db()
        c = conn.execute('SELECT status FROM affiliate_commissions WHERE stripe_invoice_id=?', ('in_mat',)).fetchone()
        conn.close()
        self.assertEqual(c['status'], 'payable')


if __name__ == '__main__':
    unittest.main()
