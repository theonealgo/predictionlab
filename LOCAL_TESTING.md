# Local testing checklist (Sports Sandbox)

Use this guide to confirm the sandbox app works on your Mac **before** renaming the repo or pushing to GitHub. The production app in `Documents/2025sports/predictionmay` is **not** changed during sandbox work.

**Sandbox folder:** `/Users/nimamesghali/Sports Sandbox/Sports Sandbox`

---

## 1. Start the server

Open Terminal, then:

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
python3 NHL77FINAL.py
```

**Quick check (no server):** app loads without crashing:

```bash
python3 -c "from NHL77FINAL import app; print('ok')"
```

You should see `ok` (and some model-load messages). That means Python can import the app.

**With server running:** leave that Terminal window open. In your browser use:

`http://127.0.0.1:5000`

(If the app prints a different port, use that instead.)

**First visit can be slow** (about 1–6 minutes on a cold start) while spread/total models train. Later page loads are faster.

---

## 2. Pages to click — predictions (picks)

| Sport | URL |
|-------|-----|
| NBA | http://127.0.0.1:5000/nba-picks |
| NHL | http://127.0.0.1:5000/nhl-picks |
| NFL | http://127.0.0.1:5000/nfl-picks |
| MLB | http://127.0.0.1:5000/mlb-picks |
| NCAAB | http://127.0.0.1:5000/ncaab-picks |
| NCAAF | http://127.0.0.1:5000/ncaaf-picks |
| WNBA | http://127.0.0.1:5000/wnba-picks |
| Soccer | http://127.0.0.1:5000/soccer-picks |

---

## 3. Pages to click — results

| Sport | URL |
|-------|-----|
| NBA | http://127.0.0.1:5000/nba-results |
| NHL | http://127.0.0.1:5000/nhl-results |
| NFL | http://127.0.0.1:5000/nfl-results |
| MLB | http://127.0.0.1:5000/mlb-results |
| NCAAB | http://127.0.0.1:5000/ncaab-results |
| NCAAF | http://127.0.0.1:5000/ncaaf-results |
| WNBA | http://127.0.0.1:5000/wnba-results |
| Soccer | http://127.0.0.1:5000/soccer-results |

**All sports summary:** http://127.0.0.1:5000/all-sports-results  

**Home:** http://127.0.0.1:5000/

---

## 4. What “pass” looks like

### In the browser

- Page loads (no blank screen, no “500 Internal Server Error”).
- You see the normal layout: nav, game cards or results tables (some days may have zero games — that is OK).
- Pick cards show team names, spreads/totals, and model columns (PL vs XSharp) without obvious broken HTML.

### Automated checks (optional — ask a developer or run in Terminal)

**Site audit** (`python3 qa/site_checker.py`) expects Flask running at `AUDIT_BASE_URL` (default `http://127.0.0.1:5001`); start with `PORT=5001 python3 NHL77FINAL.py`.

From the sandbox folder:

```bash
# All routes return HTTP 200 (may take several minutes first time)
python3 -c "from NHL77FINAL import app; c=app.test_client(); ..."

# Pick card logic
python3 -m pytest tests/test_pl_spread_display.py -q

# Full test suite
python3 -m pytest tests/ -q
```

**Pass:** pytest ends with `N passed` and no `FAILED` lines.

---

## 5. Last automated run (2026-06-03)

| Check | Result |
|-------|--------|
| `from NHL77FINAL import app` | **PASS** |
| Flask routes (17 URLs, status 200) | **PASS** (all) |
| `tests/test_pl_spread_display.py` | **PASS** — 22 passed (includes Knicks @ Spurs unanimous pick) |
| `tests/` full suite | **96 passed, 1 failed** |

### Route smoke (Flask test client)

| Route | Status | Pass? |
|-------|--------|-------|
| /nba-picks | 200 | PASS |
| /nhl-picks | 200 | PASS |
| /nfl-picks | 200 | PASS |
| /mlb-picks | 200 | PASS |
| /ncaab-picks | 200 | PASS |
| /ncaaf-picks | 200 | PASS |
| /wnba-picks | 200 | PASS |
| /soccer-picks | 200 | PASS |
| /nba-results | 200 | PASS |
| /nhl-results | 200 | PASS |
| /nfl-results | 200 | PASS |
| /mlb-results | 200 | PASS |
| /ncaab-results | 200 | PASS |
| /ncaaf-results | 200 | PASS |
| /wnba-results | 200 | PASS |
| /soccer-results | 200 | PASS |
| /all-sports-results | 200 | PASS |
| / | 200 | PASS |

### Known failure from this run

- **`tests/test_results_date_tally.py::test_nba_results_uses_stale_tally_bundle`** — expected daily tally date `2026-05-25` from mocked weekly data, but render used `2026-05-30` (likely local DB / tally logic). Fix or confirm behavior before treating the suite as fully green.

### Non-blocking warnings

- Many sklearn `UserWarning: X does not have valid feature names` during XGB/LGBM inference — noisy logs, pages still load.
- `pytest --timeout=120` is **not** installed in this environment; use `pytest tests/ -q` without `--timeout`.

---

## 6. Before git rename / push

Only when you are happy with browser checks **and** pytest is all green:

1. Confirm production folder `predictionmay` was not edited for this feature.
2. Rename / repoint the sandbox remote as you prefer.
3. Push when ready.

Until the stale tally test is fixed or accepted, treat **one pytest failure** as a blocker for “all green.”

---

## 7. Pick card sanity (Knicks @ Spurs)

Automated test confirms: when all models pick **Spurs**, the card must **not** show Knicks as the displayed pick. Run:

```bash
python3 -m pytest tests/test_pl_spread_display.py::test_knicks_spurs_unanimous_models_no_opposing_pick -q
```

Expected: `1 passed`.

---

## 8. Admin login (see everything locally)

The sandbox seeds an admin account from `.env.local` on startup. You can also run:

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
python3 scripts/seed_local_admin.py
```

| Field | Value |
|-------|--------|
| **Login URL** | http://127.0.0.1:5000/login |
| **Email** | `admin@predictionlab.local` |
| **Password** | `sandbox-admin-2026` (from `.env.local` → `ADMIN_PASSWORD`) |

That email is in `ADMIN_EMAILS` — admin + premium access. On **localhost**, picks pages also treat you as premium even when logged out (dev preview).

**Change password:** set `ADMIN_PASSWORD=your-secret` in `.env.local`, restart the app, or re-run `seed_local_admin.py`.
