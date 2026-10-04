# Sports modules

Production app not modified until sandbox validated.

**Non-coder?** See **`FOLDER_GUIDE.md`** at the project root for a full map of every folder.

Each file under `sports/` is one league. Plain-English purpose:

| File | What it does |
|------|----------------|
| `NBA.py` | **Open this to change basketball** — NBA picks (efficiency spread/total = Prediction Lab), weekly grading, full `/nba-results` page. WNBA reuses the same efficiency hooks. |
| `NHL.py` | NHL route shortcut + `/nhl-results` (season snapshots, playoffs) |
| `NFL.py` | NFL route shortcut + `/nfl-results` (weekly + DB fallback) |
| `MLB.py` | MLB route shortcut + daily results page |
| `NCAAB.py` | Men's college basketball shortcut + results |
| `NCAAF.py` | College football shortcut + results |
| `WNBA.py` | Women's pro basketball shortcut + results |
| `NCAAW.py` | Women's college basketball shortcut + results |
| `SOCCER.py` | Soccer shortcut + league-filtered results |
| `TENNIS.py` | Tennis (ATP/WTA) shortcut + ESPN draw fetch + results |
| `UFC.py` | UFC/MMA shortcut + ESPN fight-card fetch + results |
| `GOLF.py` | PGA Tour shortcut + ESPN tournament fetch + results |

## What stays in `NHL77FINAL.py`

- Flask app, login, premium checks, navigation
- Database (`get_db_connection`, predictions/games tables)
- Book odds (`betting_lines`, ESPN Core, DraftKings compare)
- Shared grading (`_model_probs_for_grading`, spread/total tally, ROI)
- Pick card templates and `_prepare_pred_card_display`
- SEO routes (`/nba-picks`, `/nhl-results`, etc.) — sport modules only add short aliases (`/nba` → `/nba-picks`)

## Wiring

After `app = Flask(...)`, main imports each sport module and:

1. Calls `register_routes(app)` for shortcuts
2. Registers `render_sport_results_page` in `_SPORT_RESULTS_RENDERERS`
3. Delegates `sport_results(sport)` to the matching module

NBA also hooks `get_upcoming_predictions` for efficiency-based `our_spread` / `our_total` (from `team_efficiency.py` at project root — the **Prediction Lab** layer, not XSharp ML).

### Changing NBA / WNBA spreads or totals

1. **`sports/NBA.py`** — `attach_nba_prediction_projections()` (live picks) and `attach_nba_efficiency_to_daily_results()` (completed games on results).
2. **`team_efficiency.py`** — the math (ORtg, DRtg, pace, home court, projected spread/total).
3. **`NHL77FINAL.py`** — card display (`disp_pl_spread`, `efficiency_prob` row, `/team-efficiency-results`). PL spread stays honest when efficiency disagrees with Sharp Consensus.

Modules use lazy `import NHL77FINAL as main` to avoid circular imports.

## Run locally

```bash
cd "/Users/nimamesghali/Sports Sandbox/Sports Sandbox"
pip install -r requirements.txt
python3 app.py
# or: gunicorn -c gunicorn.conf.py app:app
```

Sample URLs:

- Picks: http://127.0.0.1:5000/nba-picks , `/nhl-picks`, `/mlb-picks`
- Results: http://127.0.0.1:5000/nba-results , `/nhl-results`
- Shortcuts: `/nba`, `/nhl`, `/mlb` (301 to picks)

## Tests

```bash
pytest tests/test_pl_spread_display.py tests/test_nba_results_perf_guards.py tests/test_nba_model_stats_parity.py -q
```
