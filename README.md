# Prediction Lab Sports Sandbox

`app.py` is the canonical application launcher. Sport extraction is in progress:
NBA and the individual-sport loaders are substantially separated, while several
sport modules still delegate to the legacy shared core.

Read `ARCHITECTURE_GUIDE.md` before changing application structure.

## Run locally

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
pip install -r requirements.txt
python3 -c "from app import app; print('ok', app.name)"
python3 app.py   # or: gunicorn -c gunicorn.conf.py app:app
```

Routes (SEO slugs still registered in main):

- Picks: `/nba-picks` (alias `/nba` → 301)
- Results: `/nba-results`

## What moved to `sports/NBA.py`

- `calculate_nba_weekly_performance` — season grading / weekly ML tally
- `nba_model_probs_for_grading` — NBA wrapper for shared `_model_probs_for_grading`
- `update_nba_scores` — ESPN score sync
- `attach_nba_efficiency_to_daily_results` — PL spread/total on results cards (NBA + WNBA)
- `attach_nba_prediction_projections` — efficiency-based `our_spread` / `our_total` on picks page
- `render_sport_results_page` — full `/nba-results` pipeline
- `register_routes` — `/nba` shortcut

## Main wiring

After `app = Flask(...)`, `NHL77FINAL` imports `sports.NBA`, re-exports aliases for tests, and calls `register_routes(app)`. `sport_predictions` / `sport_results` delegate when `sport == 'NBA'`.

Odds layers unchanged: book lines in main; PL (`our_*`) in NBA module; XSharp (`xgb_*`) still from shared predictors in main.

## Tests

```bash
pytest tests/test_nba_results_perf_guards.py tests/test_nba_model_stats_parity.py -q
```
