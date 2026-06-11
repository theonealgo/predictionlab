# Folder guide — plain English

**For non-coders.** This map explains what each part of the project does, whether you can edit it, and what is safe to delete. **Do not rename Python folders** (`sports`, `templates`, `src`, etc.) — spaces or friendly names break imports and the live site.

---

## 1. Start here

| Path | What it is | Edit? |
|------|------------|-------|
| **`NHL77FINAL.py`** | The main app — Flask routes, login, database, pick cards, results pages, grading. ~800 KB; most logic still lives here. | Dev only |
| **`app.py`** | Tiny launcher: `from NHL77FINAL import app`. Used by Render/gunicorn. | Do not edit |
| **`render_start.sh`** | Production startup script (copies DB to disk, runs gunicorn). Referenced by Render deploy. | Do not move/rename |
| **`sports/`** | One Python file per league (NBA, NHL, NFL, …). Sport-specific picks, results, and shortcuts like `/nba` → `/nba-picks`. | Yes — see `sports/README.md` |
| **`StartSportsApp.command`** | Double-click Mac shortcut to run the app locally. | Optional |
| **`requirements.txt`** | Python packages the app needs. | Dev only |
| **`gunicorn.conf.py`** | Production web server settings. | Dev only |

**Quick start (local):**

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
pip install -r requirements.txt
python3 NHL77FINAL.py
```

Open http://127.0.0.1:5000 — picks at `/nba-picks`, results at `/nba-results`.

---

## 2. What users see (website look & feel)

| Path | What it is | Edit? |
|------|------------|-------|
| **`templates/`** | HTML pages Jinja renders — pick cards, results tables, login, landing pages. Subfolder `includes/` has shared fragments (nav, card body). | Yes — layout/copy |
| **`static/`** | CSS, JavaScript, logos (`pl-logo.svg`, `PLLOGO.PNG`), background images. | Yes — styling/assets |
| **`espn_predictions_template.html`** (root) | Legacy duplicate of picks template; live app uses `templates/espn_predictions_template.html`. | Ignore root copy |

Changing text on a pick card → `templates/includes/game_card_body.html`. Changing colors/logo → `static/css/` and `static/`.

---

## 3. Data & models (predictions, scores, ML)

| Path | What it is | Edit? |
|------|------------|-------|
| **`data/`** | JSON config and cached stats — team ESPN IDs, season snapshot files (`data/season_snapshots/*.json`). Snapshots freeze end-of-season banner stats so results pages load fast. | Snapshots: dev rebuild only; IDs: careful |
| **`sports_predictions_original.db`** | SQLite database — games, scores, predictions, users. **The app's memory.** | Do not delete; back up before experiments |
| **`models/`** | Trained ML files (`.pkl`, `NBA_v2/`, `NHL_v2/`, etc.) — XSharp / ensemble models. | Do not delete; replace only when retraining |
| **`prediction_system_v2/`** | Code for the v2 ML pipeline (features, ensemble, calibration). | Dev only |
| **`team_efficiency.py`** | **Prediction Lab spread/total engine** — ORtg/DRtg/pace from ESPN box scores. Also exposed as its own **⚡ Efficiency** model row on pick cards and **`/team-efficiency-results`**. | Dev only |
| **`weighted_total_predictor.py`** | Fallback totals when efficiency data is missing; shares ESPN cache with `team_efficiency.py`. | Dev only |
| **`odds_engine_espn.py`** | ESPN team stats helper used by main app for book-style reference lines. | Dev only |
| **`src/`** | Newer utilities — season snapshot builder (`season_snapshots.py`), experimental book odds (`src/odds/book_odds_engine.py`). | Dev only |

### Three prediction layers (do not mix them up)

1. **Book lines** — what DraftKings/ESPN post (`betting_lines`, live market fetch).
2. **Prediction Lab (PL)** — `our_spread`, `our_total`, `ensemble_prob` from efficiency + models. **Team efficiency is layer #2 for NBA/WNBA spread.**
3. **XSharp** — `xgb_spread`, `xgb_total`, `xgb_prob` from ML pickles in `models/`.

On pick cards: **PL column = layer 2**, **XSharp column = layer 3**.

### PL spread on results & all-sports stats

There is **no separate "efficiency results" page**. Efficiency grades as:

- **`pl_spread_graded` / `pl_spread_covered` / `pl_spread_pct`** on each sport's results page (label: **Prediction Lab**).
- Same keys inside **`data/season_snapshots/*_regular.json`** for frozen season banners.

### Sandbox note — PL spread display

**Production** shows the efficiency-derived spread on pick cards when models disagree. **This sandbox** may flip or hide PL spread on cards so the featured pick aligns with Sharp Consensus (ensemble ML). Grading and results pages still track **`pl_spread_*`** from efficiency/`our_spread`. If efficiency numbers look wrong here, check `sports/NBA.py` and `_set_card_pl_spread` in `NHL77FINAL.py` — not a missing model.

---

## 4. Safe to delete (cleanup candidates)

These do **not** affect the running app if removed. Back up first if unsure.

| Path | Why it's safe |
|------|----------------|
| **`TO_DELETE/`** | Already marked for removal — old experiments. |
| **`backups/`**, **`database_backups/`** | Old DB/code copies. Keep one recent backup elsewhere, then delete. |
| **`__pycache__/`**, **`.pytest_cache/`** | Auto-generated Python cache — recreated on run. |
| **`catboost_info/`** | CatBoost training logs — not needed at runtime. |
| **`standalone-player-props copy/`** | Duplicate of player-props side project. |
| **`odds_engine/`** | Legacy folder; live app uses root `odds_engine_espn.py` and `src/odds/` instead. Not imported by `NHL77FINAL.py`. |
| **Root `*.md` guides** (50+ files) | Developer notes — e.g. `ATS_SYSTEM_README.md`, `QUICK_START_ML.md`, `WEIGHTED_TOTAL_SYSTEM.md`. **Keep** `README.md`, `LOCAL_TESTING.md`, this file, and `sports/README.md`. |
| **Debug HTML** | `MOCKUP_card_preview.html`, `samples/new_pick_card_preview.html`, root `espn_predictions_template.html` duplicate. |
| **CSV / sample dumps in `samples/`** | Design previews and charts — not loaded by app. |
| **`Archive.zip`** | Manual archive — not used by app. |
| **`.props_backfill_*`** (root dotfiles) | One-line markers from prop backfill jobs. |
| **`NHL77FINAL.py.bak`**, **`NHL77FINAL_SAFE_BACKUP_*.py`**, **`ats_app_*BACKUP*.py`** | Old code backups. |
| **`ats_app.py`** | Separate ATS app — not the main Prediction Lab site. |
| **`TelegramExporter/`**, **`social-media-app/`**, **`streamly.blog/`** | Side projects, not the picks site. |
| **`repl_nix_workspace.egg-info/`** | Replit packaging artifact. |
| **Log files** | `app.log`, `ats_app.log`, `logs/` — safe to clear. |
| **`.cache/`**, **`.cache.sqlite`** | Local HTTP/share-image cache — rebuilds automatically. |

**Do NOT delete:** `NHL77FINAL.py`, `app.py`, `render_start.sh`, `sports/`, `templates/`, `static/`, `models/`, `data/season_snapshots/` (if you use frozen seasons), `sports_predictions_original.db`, `requirements.txt`.

---

## 5. Scripts you might run

| Path | What it does |
|------|----------------|
| **`scripts/build_season_snapshot.py`** | Freeze end-of-season results stats → JSON in `data/season_snapshots/`. |
| **`scripts/compute_all_sport_season_stats.py`** | Recompute all-sports performance summaries. |
| **`scripts/seed_local_admin.py`** | Create a local admin login for testing. |
| **`scripts/audit_*.py`**, **`verify_*.py`** | QA checks on pick cards, book odds, page gaps. |
| **`scripts/analyze_sport_fade_candidates.py`** | Analytics on model fade patterns. |

Run from project root: `python3 scripts/seed_local_admin.py`

---

## 6. Tests

| Path | What it is |
|------|------------|
| **`tests/`** | Automated checks — PL spread display, NBA results performance, all-sports snapshots, etc. |

```bash
pytest tests/ -q
```

Keeps efficiency/PL spread behavior from regressing. Dev-only; not deployed to users.

---

## 7. Do not touch unless you are a developer

| Path | Why |
|------|-----|
| **`.git/`** | Version history — breaking it loses rollback. |
| **`.env.local`** | Secrets (API keys, DB paths). Never commit. Copy from `.env.local.example`. |
| **`.gitignore`**, **`Procfile`** | Deploy & git config. |

---

## 8. Other folders (reference)

| Path | Plain English |
|------|----------------|
| **`docs/`** | Short dev docs (season snapshots, Datadog). Folder name stays `docs/` — read files inside for details. |
| **`standalone-player-props/`** | Separate player-props product (own backend). Not required for main picks site. |
| **`datadog/`** | Monitoring config for production tracing. |
| **Root sport `*_feature_engineering.py`, `soccer_models.py`, etc.** | ML feature code imported by `NHL77FINAL.py` — leave names as-is. |

---

## 9. Key root Python files (names stay as-is)

| File | Role |
|------|------|
| `team_efficiency.py` | PL spread/total for NBA/WNBA (efficiency model) |
| `weighted_total_predictor.py` | Total/spread fallback from recent scores |
| `odds_engine_espn.py` | ESPN stats for odds warm-up |
| `pl_book_odds_api.py` | Book odds comparison helpers |
| `prop_odds_engine.py` | Player props (standalone app) |
| `seed_local_admin.py` | Also at root in some setups; canonical copy in `scripts/` |

---

## 10. Where to change things (cheat sheet)

| I want to… | Open… |
|------------|--------|
| Change NBA spread/total logic | `sports/NBA.py` + `team_efficiency.py` |
| Change NHL results page | `sports/NHL.py` |
| Change pick card layout | `templates/includes/game_card_body.html` |
| Change site logo/colors | `static/css/`, `static/pl-logo.svg` |
| Change login or premium | `NHL77FINAL.py` (search routes) |
| Retrain ML models | `prediction_system_v2/`, then save to `models/` |
| Fix wrong season banner stats | Re-run `scripts/build_season_snapshot.py`, commit new JSON |

---

*Last updated for Sports Sandbox — folder names intentionally kept technical so Python imports keep working.*
