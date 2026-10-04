# Tennis — Sports Sandbox

Independent module under `/Users/nimamesghali/Sports Sandbox/independent_sports/tennis/`.

Not live PredictionLab. No push / no production deploy.

## Markets
Winner, projected sets/games, straight-sets prob, O/U games. Surface Elo included.

## Book odds
**No book odds UI** unless ESPN provides them (not assumed for ATP/WTA scoreboard).

## Retrain
`training/retrain.py` after match.


## Layout
- `backend/` prediction interface
- `api/` JSON endpoints (mounted via hub)
- `ml/` model wrappers
- `database/` SQLite schema + seed
- `results/` grader + tracker
- `training/` train / retrain / backtest stubs
- `templates/` + `static/` pages
- `tests/` smoke tests
