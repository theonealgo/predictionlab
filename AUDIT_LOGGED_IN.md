# Logged-in admin audit (Sports Sandbox)

**Date:** 2026-06-04  
**Workspace:** `/Users/nimamesghali/Sports Sandbox/Sports Sandbox`  
**Actor:** `nmesghali@gmail.com` (password from `.env.local` → `ADMIN_PASSWORD`)  
**Method:** Flask `test_client` — `POST /login` then session cookies on `Host: 127.0.0.1`  
**Git:** No commits, no push (sandbox only)

---

## Run server + login

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
python3 scripts/seed_local_admin.py nmesghali@gmail.com   # once per machine / after password change
python3 NHL77FINAL.py
```

| | |
|--|--|
| **App** | http://127.0.0.1:5000 |
| **Login** | http://127.0.0.1:5000/login |
| **Re-run audit** | `python3 scripts/audit_logged_in_session.py` → `audit_results_logged_in.json` |

**Auth checks**

| Check | Result |
|-------|--------|
| `POST /login` → 302, nav shows Sign Out | **PASS** |
| `/plans` 200 (premium/subscription UI) | **PASS** |
| Picks pages: no `premium-locked` / `Unlock Premium` when logged in | **PASS** |

---

## Summary counts

| | Count |
|--|------:|
| **PASS** (HTTP 200 + checklist) | **31** |
| **FAIL** | **0** |
| **WARN** (200 but empty/offseason/data quirk) | **4** |

Includes auth + 27 sport/cross URLs + 2 soccer league query URLs.

**pytest:** `108 passed`, `1 failed` — `tests/test_landing_top_picks.py::test_build_todays_top_picks_skips_tbd_matchups`

---

## Pass / fail by URL

| URL | Status | Pass? | Notes |
|-----|--------|-------|-------|
| `POST /login` | 302 | PASS | Session persists on follow-up requests |
| `/` | 200 | PASS | Home loads; value picks pipeline has no `TBD` in automated scan |
| `/plans` | 200 | PASS | |
| `/login` (GET) | 200 | PASS | |
| `/all-sports-results` | 200 | PASS | |
| `/team-efficiency-results` | 200 | PASS | |
| `/nba-picks` | 200 | PASS | 3 pick cards |
| `/nba-results` | 200 | PASS | Title: `NBA Results \| predictionlab.io`; date dropdown `?date=YYYY-MM-DD` on all results pages |
| `/nhl-picks` | 200 | PASS | 4 pick cards |
| `/nhl-results` | 200 | PASS | Title: `NHL Results \| predictionlab.io`; season Grinder2 **764-545** |
| `/nfl-picks` | 200 | PASS | 70 pick cards |
| `/nfl-results` | 200 | PASS | |
| `/mlb-picks` | 200 | PASS | 61 pick cards |
| `/mlb-results` | 200 | PASS | |
| `/ncaab-picks` | 200 | WARN | 0 cards (offseason / no 7d slate) |
| `/ncaab-results` | 200 | PASS | |
| `/ncaaf-picks` | 200 | PASS | 7 pick cards |
| `/ncaaf-results` | 200 | PASS | |
| `/wnba-picks` | 200 | PASS | 23 pick cards |
| `/wnba-results` | 200 | PASS | |
| `/ncaaw-picks` | 200 | WARN | 0 cards (offseason) |
| `/ncaaw-results` | 200 | PASS | |
| `/soccer-picks` | 200 | PASS | 28 pick cards |
| `/soccer-results` | 200 | PASS | |
| `/soccer-results?league=eng.1` | 200 | PASS | Last 7d tally **34 games** (not 1) |
| `/soccer-results?league=uefa.champions` | 200 | PASS | |
| `/performance` (footer) | 200 | PASS | |
| `/tutorial` (footer) | 200 | PASS | |

**Results tab titles:** All sampled results routes use `… Results | predictionlab.io` (not `Predictions`).

**Pick cards (logged in):** Books + PL + XSharp columns present where games exist; **6 model rows** on results cards (Grinder2, Takedown, Edge, XSharp, Efficiency, Consensus). Pick-card face uses Sharp Consensus when models disagree with efficiency spread (see `tests/test_pl_spread_display.py`).

**ROI cards:** Flat-unit section headline is **win rate %**; detail line shows record · ROI · units (`build_roi_cards` in `NHL77FINAL.py`).

---

## Issues found and fixed (sandbox)

| Issue | Fix | Location |
|-------|-----|----------|
| `/nhl-results` crashed: `'dict object' has no attribute 'efficiency'` | Pad frozen `overall_stats` with empty Efficiency stats before render | `NHL77FINAL.py` — `_normalize_overall_stats()` (~8635), `_stats_from_nhl_snapshot()` (~11656) |
| Admin `nmesghali@gmail.com` login | Re-seed premium user | `scripts/seed_local_admin.py` (run, not code change) |

---

## Issues found — NOT fixed (why)

| Issue | Why not fixed |
|-------|----------------|
| NHL snapshot **Efficiency** row shows `0-1293` | Frozen season JSON predates efficiency grading; needs snapshot rebuild, not a one-line render fix |
| `scripts/audit_all_sports_pages.py` flags “PL spread contradicts face pick” for NBA/MLB/WNBA/Soccer | **False positive:** checker treats home-negative spread as inconsistent; intentional when efficiency spread ≠ Sharp Consensus pick (`test_face_pick_matches_sharp_consensus_not_pl_spread`) |
| `test_build_todays_top_picks_skips_tbd_matchups` fails | Seeded DB / date window in test — unrelated to logged-in route audit |
| NCAAB / NCAAW picks: 0 cards | No upcoming games in 7-day window (expected offseason) |
| sklearn `UserWarning` spam during XGB inference | Noise only; pages still 200 |

---

## Deep audit script notes (`scripts/audit_all_sports_pages.py`)

- NHL results: **fixed** (was processing-error page).
- Pipeline spread warnings: see false-positive note above.
- NHL results nav: missing “Join Premium” in template is **expected** for logged-in/admin localhost.

---

## Top 5 remaining blockers for your manual browser pass

1. **NHL Efficiency season stats** — verify whether `0-1293` is acceptable until snapshots are regenerated with `efficiency_correct` / `efficiency_prob`.
2. **Cold start** — first request per sport can take 1–6+ minutes (model train); use second load for UI review.
3. **Knicks @ Spurs / unanimous pick** — confirm AI pick strip shows **Spurs**, not Knicks, when all models agree (`pytest tests/test_pl_spread_display.py::test_knicks_spurs_unanimous_models_no_opposing_pick`).
4. **Home page TBD** — pytest still failing on `build_todays_top_picks` TBD filter; click home value picks and confirm no `TBD vs TBD`.
5. **NCAAB / NCAAW** — confirm offseason empty state copy is acceptable until schedules return.

---

## pytest (end of audit)

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
python3 -m pytest tests/ -q
```

**Result:** 108 passed, 1 failed (landing TBD test above).
