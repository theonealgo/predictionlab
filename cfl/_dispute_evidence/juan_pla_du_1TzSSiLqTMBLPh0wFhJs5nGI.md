# Stripe Dispute Evidence Brief — Juan Pla

**Dispute:** `du_1TzSSiLqTMBLPh0wFhJs5nGI`  
**Reason:** Product not received  
**Amount:** $19.99 USD (Predictionlab.io Monthly Subscription)  
**Customer:** Juan Pla `<juan_pla@hotmail.com>`  
**Charge:** `ch_3Tx4FTLqTMBLPh0w2ztBGd6R`  
**PaymentIntent:** `pi_3Tx4FTLqTMBLPh0w2DflH5tP`  
**Subscription:** `sub_1Tx4FVLqTMBLPh0w5yo10srQ` (created Jul 25, 2026)  
**Invoice:** `IM6JTHB3-0001`  
**Payment method:** Mastercard •••• 3215 (Citibank); CVC + ZIP passed  
**Payment succeeded:** Jul 25, 2026 ~8:00 AM  
**Dispute opened:** Jul 31, 2026 ~10:23 PM  
**Evidence due:** Sep 9, 2026  
**Stripe account:** `acct_1TCgvoLqTMBLPh0w`  
**Stripe note:** “No receipts sent”

**Related (lower priority):** `gfore123@gmail.com` — $4.99, Product unacceptable, disputed ~Jul 28, due ~Sep 6. Not researched in depth here unless owner asks.

---

## Local search result (NOT production proof)

| Source | Result |
|--------|--------|
| Codebase / text search for `juan_pla@hotmail.com` / `Juan Pla` | **Not found** |
| Local `sports_predictions_original.db` `users` | **Not present** (DB only has 4 local/admin test users) |
| Local `TO_DELETE/purepicks_users.db` | **Not present** |
| Local `.env` | Has `ODDS_API_KEY` only — **no `STRIPE_SECRET_KEY`** |
| Stripe CLI (`~/.config/stripe/config.toml`) | Logged into **different** account `acct_1RHT6EGM80oGs0bF` (not `acct_1TCgvo…`); live restricted key **expired 2025-08-12**. Cannot pull this dispute via CLI. |

**Verdict from local alone:** We cannot prove or disprove that Juan’s premium was activated. Production DB + Render logs + Dashboard objects are required before submitting a firm counter.

---

## What should have happened on Jul 25 (monthly $19.99 path)

Source of truth in local code: `auth_system.py`.

### Checkout path (monthly uses app Checkout, not weekly Payment Link)

1. Customer visits `/plans` → clicks **Get Monthly Access** → `GET /checkout/monthly`.
2. App creates a Stripe Checkout Session (`mode=subscription`, metadata `plan=monthly`), redirects to Stripe-hosted checkout.
3. Stripe collects email + card. Payment succeeds → customer lands on  
   `/checkout/success?session_id={CHECKOUT_SESSION_ID}`.
4. **Success handler** (same request path):
   - Retrieves Checkout Session; if `payment_status == 'paid'`, reads email from `customer_details.email`.
   - Finds or **auto-creates** `users` row: `INSERT INTO users (email, name)` — **no `password_hash`**.
   - Calls `_activate_premium(user_id, plan='monthly', stripe_customer_id=…)`:
     - `is_premium = 1`
     - `premium_expires = now + 31 days` (≈ **Aug 25, 2026** if activated Jul 25)
     - stores `stripe_customer_id`
   - `login_user(..., remember=True)` + session token — browser gets a logged-in cookie **if they complete this redirect**.
5. **Webhook** `POST /stripe/webhook` on `checkout.session.completed` does the same find-or-create + `_activate_premium` (redundant backup if success page never loads).
6. Success page copy: “Welcome to Premium!” / access to Spreads, Totals, Score Predictions → CTA to `/`.

### Product delivery model (digital / login-gated)

- Premium is **not** emailed as a file. Access is unlocking locked pick fields when `is_authenticated` and `premium_active`.
- Anonymous visitors see locked UI (normal paywall) — evidence screenshots already in `_dispute_evidence/` (`mlb-picks-anonymous.*`, `nba-picks-anonymous.*`, `plans-anonymous.*`).
- **Guest checkout risk:** account may exist with **no password**. Password login fails (`password_hash` empty → “Invalid email or password”). Hotmail is unlikely to use Google OAuth for the same address. If the customer closed the success tab / cleared cookies / used another device, they can sincerely believe they “never received” access even if `is_premium=1` in DB.

### Receipts

- Checkout session creation does **not** set receipt/invoice email options in code.
- Stripe Dashboard note **“No receipts sent”** is consistent with config/settings, not proof the product wasn’t delivered. Still weakens “customer was notified” narrative — attach Stripe invoice PDF + any support emails if they exist.

### Renewal lag (Jul 29 revert)

- This charge is the **first** payment (sub created Jul 25). Renewal-sync lag is **not** the primary theory for “never received,” unless activation itself failed and they never got initial premium.

---

## Ready-to-paste Stripe Dashboard evidence outline

Use on dispute `du_1TzSSiLqTMBLPh0wFhJs5nGI` → Submit evidence. Fill `[PROD: …]` after owner pulls production facts.

### 1. Product description

> predictionlab.io sells a **digital subscription** to AI sports betting analytics. The **Monthly plan ($19.99/month)** unlocks premium content on predictionlab.io while logged in, including: every spread pick, every total pick, projected scores, full odds engine (moneyline / spread / total), player props picks & projections, model performance calculator, and coverage across supported sports.  
> Delivery is **immediate and login-gated** on the website after successful Stripe Checkout — there is no physical shipment and no separate download package.  
> Product page: https://predictionlab.io/plans  
> Terms: https://predictionlab.io/terms

### 2. Customer communication / access (login instructions)

> The customer paid via Stripe Checkout for the Monthly Subscription using email **juan_pla@hotmail.com** on **July 25, 2026**. After payment, Stripe redirects the customer to our success URL (`/checkout/success`), which activates premium on that email and logs the browser into the account.  
> Access instructions for digital delivery:  
> 1) Go to https://predictionlab.io/login  
> 2) Sign in with the **same email used at Checkout** (or use “Continue with Google” if that Google account email matches)  
> 3) Open any sport picks page — premium fields unlock for active subscribers  
> Anonymous browsing shows locked premiums by design; that is not a failed delivery.  
> [PROD: paste any emails/support tickets/chat with this address. If none: “No customer support request was received from this email before the dispute.”]  
> Note: Stripe indicates no automatic receipts were sent for this charge; we are attaching the Stripe invoice/receipt PDF from the Dashboard as proof of purchase and digital service description.

### 3. Access activity / service provided

**Have locally (generic, not customer-specific):**

- Anonymous locked-paywall screenshots/HTML under `_dispute_evidence/` showing the product is live and gated (not a blank/broken site).
- Code path proving paid monthly checkout activates premium for ~31 days.

**Need from production (customer-specific):**

| Check | Why it matters |
|-------|----------------|
| `users` row for `juan_pla@hotmail.com` | Proves account creation |
| `is_premium`, `premium_expires`, `stripe_customer_id`, `created_at`, whether `password_hash` is null, `session_token`, `google_id` | Proves activation + explains login friction |
| Render logs Jul 25 for `[checkout/success] Activated premium for juan_pla@hotmail.com` and/or `[stripe webhook] Activated premium for juan_pla@hotmail.com` | Proves delivery event |
| Stripe Checkout Session / Customer / Subscription status for this charge | Proves paid subscription object |
| Any login/session activity after Jul 25 | Usage vs never returned |

**If prod shows activated premium:**

> On July 25, 2026, following successful payment `ch_3Tx4FTLqTMBLPh0w2ztBGd6R`, our systems created/updated the account for juan_pla@hotmail.com and set `is_premium=1` with `premium_expires` approximately 31 days out (expected ~August 25, 2026), linked to Stripe customer `[PROD: cus_…]`. Webhook and/or checkout success logs confirm activation. The product was therefore available online. A “product not received” claim is inconsistent with digital delivery and account activation; locked pages when logged out are expected behavior.

**If prod cannot prove activation:**

> We cannot currently demonstrate account activation for this email in production logs/DB. We should **not** overclaim delivery. Prefer refund/accept or Soft Dispute resolution after fixing access, rather than asserting service was provided.

### 4. Refund policy / cancellation

> **Terms of Service** (https://predictionlab.io/terms §6): subscriptions renew automatically; users may cancel via account dashboard; **purchases are stated as final and non-refundable** (no partial/prorated refunds).  
> **FAQ** (https://predictionlab.io/faq): states monthly plans have a **10-day return window** (and yearly 30-day). Dispute opened **~6 days** after purchase — inside that FAQ window.  
> Owner note: Terms vs FAQ conflict. For evidence, prefer quoting Terms for non-refundable digital goods **and** document whether any refund request was received. For business decision, FAQ 10-day window + access friction (guest checkout without password) may favor **goodwill refund** over a hard fight if activation/login help was never offered.

### 5. Why “product not received” is weak vs honest gap

| Scenario (from prod) | Recommended posture |
|----------------------|---------------------|
| User exists, `is_premium=1`, expires ~+31d, logs show activate Jul 25 | **Counter** — digital goods delivered; customer did not use login / expected email attachment. Attach DB export (redact hashes), logs, invoice, plans screenshot, access instructions. |
| User exists, premium on, but `password_hash` NULL and no Google link / no later session | **Mixed** — service was provisioned, but access UX is weak. Counter only if you also show activation + clear access instructions offered; consider cancel sub + refund as goodwill if you want win-rate / reputation. |
| User missing OR `is_premium=0` and no activate logs | **Do not claim delivery.** Accept dispute or refund; fix webhook if broken. |
| No local Stripe API access to `acct_1TCgvo…` | Owner must pull Dashboard objects (below). |

---

## Owner 5-step action list (TODAY)

1. **Stripe Dashboard screenshots / PDFs** (account `acct_1TCgvoLqTMBLPh0w`)  
   - Payments → Charge `ch_3Tx4FTLqTMBLPh0w2ztBGd6R` (status, receipt, billing details, Radar/CVC/ZIP)  
   - Customers → customer for `juan_pla@hotmail.com` (id, email, created)  
   - Subscriptions → `sub_1Tx4FVLqTMBLPh0w5yo10srQ` (status, current period, cancel state)  
   - Invoices → `IM6JTHB3-0001` (download PDF)  
   - Checkout Sessions filtered by customer/email Jul 25 (session id, `payment_status`, metadata.plan)  
   - Dispute page `du_1TzSSiLqTMBLPh0wFhJs5nGI` (reason, due date, “No receipts sent”)  
   - Settings → Customer emails / receipts (confirm receipts off — explains Stripe note)

2. **Production SQL** (Render disk DB, typically `/data` — confirm path on host):

```sql
SELECT id, email, name, is_premium, premium_expires, stripe_customer_id,
       created_at, google_id, session_token,
       CASE WHEN password_hash IS NULL OR password_hash = '' THEN 'NO_PASSWORD' ELSE 'HAS_PASSWORD' END AS pw
FROM users
WHERE lower(email) = 'juan_pla@hotmail.com';
```

   Optional: search stripe customer id from Dashboard once known:
   `WHERE stripe_customer_id = 'cus_…';`

3. **Render logs Jul 25, 2026** search strings:
   - `juan_pla@hotmail.com`
   - `[checkout/success] Activated premium for juan_pla`
   - `[stripe webhook] Activated premium for juan_pla`
   - `[checkout/success] Auto-created account for juan_pla`
   - `[stripe webhook] Auto-created account for juan_pla`
   - Any Stripe/webhook errors around ~8:00 AM (timezone: confirm UTC vs ET in logs)

4. **Smart Disputes / counter vs accept**  
   - If prod proves activation: submit evidence (product description + invoice + access activity + terms). Smart Disputes / Stripe-assisted evidence can help assemble docs — still attach **customer-specific** activate proof.  
   - If activation unproven or guest locked out with no support contact: **prefer refund / accept** (FAQ 10-day window; $19.99; reduces loss + fee risk) rather than a thin “product not received” fight.  
   - Do **not** invent login emails that were never sent.

5. **Goodwill premium grant vs counter**  
   - If you will **counter**: do not silently change history; document current state; optionally email access help (set password / magic login) **and** keep subscription active as proof of ongoing service.  
   - If you will **refund/accept**: cancel `sub_1Tx4FV…` in Stripe, refund or accept dispute, optionally leave a short note that digital access was available at checkout email — then improve guest-checkout password setup later (separate engineering; not for this brief).  
   - Manually setting `is_premium=1` **now** without prior activate proof does **not** create Jul 25 delivery evidence; it only helps customer experience if you choose goodwill retain.

---

## Exact Dashboard fields to copy if API unavailable

Because local env has **no** `STRIPE_SECRET_KEY` for `acct_1TCgvoLqTMBLPh0w`, owner should copy:

| Object | URL / location | Fields to capture |
|--------|----------------|-------------------|
| Dispute | Dashboard → Payments → Disputes → `du_1TzSSi…` | reason, status, evidence due, network reason code, amount |
| Charge | `ch_3Tx4FT…` | created, paid, outcome, receipt_url, billing_details.email/name/address, payment_method_details |
| PaymentIntent | `pi_3Tx4FT…` | status, customer, charges |
| Subscription | `sub_1Tx4FV…` | status, items/price nickname, current_period_start/end, cancel_at_period_end |
| Invoice | `IM6JTHB3-0001` | hosted_invoice_url, invoice_pdf, customer_email, lines |
| Customer | Customers search `juan_pla@hotmail.com` | `cus_…`, email, created, default payment method |
| Checkout Session | Payments → Checkout sessions (Jul 25) | id, customer_email, payment_status, metadata.plan, success_url |

CLI note: re-login Stripe CLI to **`acct_1TCgvoLqTMBLPh0w`** with a non-expired key if you want API pulls later. Current machine CLI config points at a different expired account.

---

## Suggested decision tree (after prod checks)

```
Prod user + is_premium=1 + activate log Jul 25?
  YES → Counter with evidence; optionally email access help (hotmail + no password risk).
  NO  → Accept/refund; do not claim delivery.
User premium but NO_PASSWORD and no logins?
  → Prefer goodwill refund OR counter + documented access remediation email same day.
```

---

## Files / folders

- Written: `predictionlabfix_work/_dispute_evidence/juan_pla_du_1TzSSiLqTMBLPh0wFhJs5nGI.md`
- Existing locked-UI evidence (reuse in upload): `predictionlabfix_work/_dispute_evidence/*-anonymous.*`
- Code traced: `predictionlabfix_work/auth_system.py` (checkout, success, webhook, `_activate_premium`)
- Policy refs: `templates/terms.html`, `templates/faq.html`

**No production changes. No git push. No deploy.**
