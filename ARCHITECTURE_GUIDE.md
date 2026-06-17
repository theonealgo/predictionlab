# Prediction Lab architecture guide

This is the practical map of the live Sports Sandbox app.

## What starts the website

`app.py` is the canonical launcher.

- Local: `python3 app.py`
- Render/Gunicorn: `gunicorn -c gunicorn.conf.py app:app`
- Mac background service: `com.predictionlab.local.plist` starts `app.py`
- Double-click/local scripts: `start_app.sh` and `StartSportsApp.command` start `app.py`

`app.py` imports the Flask object from `NHL77FINAL.py`. That large file is now
the **legacy shared core**, not an NHL-only module.

## Why the core became large again

The sport split was only partially completed:

- `sports/NBA.py` contains real extracted NBA behavior (about 321 lines).
- `sports/TENNIS.py`, `sports/UFC.py`, and `sports/GOLF.py` own their ESPN loaders.
- Most other sport files are thin wrappers (about 20-30 lines) that call back
  into `NHL77FINAL.py`.
- Commit `8786d7c` on May 29, 2026 added about 4,327 lines to the shared core
  while creating the sport wrappers. The core grew from 17,502 to 20,168 lines.
- Later fixes continued changing the shared implementations because the thin
  sport modules did not yet own those implementations.

Nothing secretly switched to another app. The modular extraction stopped
halfway, while every startup path still launched the shared core directly.

## Ownership rules

Use these rules for all new work:

| Change | Correct owner |
|---|---|
| Start/serve the website | `app.py` |
| Login, signup, logout, Stripe, premium status | `auth_system.py` |
| Shared header/footer | `templates/partials/` and `static/css/research-theme.css` |
| Pick card visibility/paywall | `templates/includes/game_card_body.html` |
| Pro table visibility/paywall | `templates/includes/mlb_pro_table.html` |
| NBA-only calculations/results | `sports/NBA.py` |
| Soccer model/fallback behavior | `sports/SOCCER.py` and `soccer_models.py` |
| Tennis/UFC/Golf loading | matching file in `sports/` |
| Player props | `standalone-player-props/backend/app/` |
| Site checker | `qa/site_checker.py` |
| Shared database, odds, grading, route assembly | legacy core until extracted |

Do not add new sport-specific algorithms to the shared core unless a shared
interface is genuinely required.

## Code labeling standard

New and changed code must be readable by someone who did not build the feature.

- Major file areas use numbered section banners that say what the area owns.
- Public helpers and non-obvious functions use docstrings describing purpose,
  inputs, output, and important fallback behavior.
- Complex template and JavaScript blocks use short comments naming the user
  feature they control.
- Comments explain why a rule exists. They do not repeat obvious syntax.
- Sport-specific behavior belongs in `sports/<SPORT>.py`; the legacy core should
  contain only the small wiring call and a comment pointing to that owner.

## Legacy core section map

Line numbers move as code changes, so search for the named heading/function.

| Approx. area | Search for | What it does |
|---|---|---|
| Top of file | `1. IMPORTS` | Imports, model loading, logging, caches |
| 150-700 | `_stale_page_cache_get` | Page cache, share images, props module loader |
| 700-1,200 | `_cached_get`, `_compute_odds` | ESPN HTTP cache and moneyline math |
| 1,200-1,730 | `_attach_h2h`, `_apply_model_fades` | H2H projections and model adjustments |
| 1,730-2,430 | `_refresh_books`, `_prepare_result_card_display` | Book odds and result-card fields |
| 2,430-3,140 | `team_logo_url`, `_prepare_pred_card_display` | Logos and pick-card data preparation |
| 3,140-4,320 | `_mlb_`, `_model_probs_for_grading` | MLB context, injuries, grading, EV |
| 4,320-4,520 | `app = Flask`, `inject_globals` | Flask creation and global template values |
| 4,520-5,230 | `_fetch_soccer_`, `_get_soccer_model_bundle` | Soccer leagues, feeds, model training |
| 5,230-6,330 | `update_`, `init_db` | Score updates, tables, startup jobs, date helpers |
| 6,330-8,040 | `V2 PREDICTION SYSTEM`, `get_upcoming_predictions` | Model loading and prediction assembly |
| 8,040-9,650 | `calculate_`, `_compute_spread_total` | Results calculations and performance summaries |
| 9,650-12,170 | `BASE TEMPLATE` through `RESULTS TEMPLATE` | Legacy embedded HTML templates |
| 12,170 onward | `ROUTES` | Homepage, auth-adjacent pages, picks/results, APIs |
| Near file end | `API ENDPOINTS FOR FRONTEND INTEGRATION` | JSON/API endpoints and local server start |

## Sport modules

| Module | Current state |
|---|---|
| `sports/NBA.py` | Substantial extraction; owns NBA results and efficiency hooks |
| `sports/NHL.py` | Thin wrapper; shared core still owns NHL implementation |
| `sports/NFL.py` | Thin wrapper; shared core still owns NFL implementation |
| `sports/MLB.py` | Thin wrapper; MLB calibration is separately extracted |
| `sports/NCAAB.py` | Thin wrapper |
| `sports/NCAAF.py` | Thin wrapper |
| `sports/NCAAW.py` | Thin wrapper |
| `sports/WNBA.py` | Thin wrapper |
| `sports/SOCCER.py` | Thin wrapper; next priority for extraction |
| `sports/TENNIS.py` | Owns ESPN loader and delegates shared results |
| `sports/UFC.py` | Owns ESPN loader and delegates shared results |
| `sports/GOLF.py` | Owns ESPN loader and delegates shared results |

## Safe extraction order

1. Move soccer feed/model fallback functions into `sports/SOCCER.py`.
2. Move each thin sport's score updater and results renderer into its module.
3. Move shared odds/card preparation into focused modules under `src/`.
4. Move route groups into Flask blueprints.
5. Rename the legacy core only after imports and tests no longer depend on it.

This order keeps the live site working while steadily reducing the shared core.
