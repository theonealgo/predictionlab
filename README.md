# Sports Sandbox — NBA module pilot

Minimal extraction of NBA logic from `NHL77FINAL.py` into `sports/NBA.py`. Other sports remain in the monolith.

## Run locally

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
pip install -r requirements.txt
python3 -c "from NHL77FINAL import app; print('ok', app.name)"
python3 NHL77FINAL.py   # or: gunicorn -c gunicorn.conf.py NHL77FINAL:app
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
