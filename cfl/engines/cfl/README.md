# CFL isolation (sandbox only)

Separate CFL prediction engine — **not** NFL tables/models, **not** live PredictionLab.

## Models

- **Current (baseline):** `engine/predict.py` — `cfl_elo_form_v1`
- **Updated (accepted):** `engine/models_v2.py` — `cfl_v3_cal_blend`
  - Separate ML / margin-spread / totals
  - Platt calibration + early-season shrink (reduces overconfidence)
  - Proxies documented for missing EPA / injury / weather / books

`engine/pipeline.py` writes sandbox predictions with the accepted model.
Set `CFL_USE_V1=1` to force the baseline path (debug only).

## DB

`database/cfl_sandbox.db` — tables `cfl_teams`, `cfl_games`, `cfl_team_stats`, `cfl_player_stats`, `cfl_predictions`.

## Refresh data + predictions

```bash
python3 ~/Documents/Personal/cfl/scripts/sync_and_predict.py
```

## Backtest gates

```bash
python3 ~/Documents/Personal/cfl/scripts/run_backtest.py
# → notes/cfl_backtest_YYYYMMDD.md + notes/cfl_backtest_latest.md
```

## Hub (sandbox)

```bash
cd "/Users/nimamesghali/Sports Sandbox/independent_sports"
SPORTS_SANDBOX_SKIP_LIVE_VENV=1 .venv/bin/python hub/app.py
# → http://127.0.0.1:5081/cfl/
```

Routes:

- `/cfl/` — picks
- `/cfl/results` — finals + Best Performing (shared MLB analytics chrome)
- `/cfl/results?view=chart` — tabbed markets
- `/cfl/model-health` — Model Health (ML/Spread/Totals) using shared `pl-analytics-*` classes
- `/cfl/total-edge` → 404 (removed)

Do not invent `cfl-special.css`. UI chrome is shared with MLB via hub `shared_chrome`.

Do not push / deploy / merge to live.
