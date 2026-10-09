# UFC isolation (sandbox only)

Offline UFC picks engine — **not** live PredictionLab. Do not push / deploy.

## Probabilities

For each fight (prefer in order):

1. **Blend** — trained fighter Elo (from ESPN completed fights) + Odds API implied win% when both exist
2. **Odds-implied** — market consensus from The Odds API `mma_mixed_martial_arts` h2h (devigged)
3. **Elo** — when no books, use Elo (seeded from career record if fighter is new)
4. Never force 50% / −108 for every card

## Data

- ESPN UFC scoreboard (upcoming + historical window)
- The Odds API (key from `predictionlabfix_work/.env` → `ODDS_API_KEY`)

## Refresh

```bash
python3 ~/Documents/Personal/ufc/scripts/sync_and_predict.py
```

## Hub

```bash
cd ~/Documents/Personal/predictionlabfix_work/_sandbox_hub_run/hub
PORT=5081 ../../.venv/bin/python -u app.py
# → http://127.0.0.1:5081/ufc/
```

Pick-only cards (no spread/total). Books shown when Odds API has a match.
