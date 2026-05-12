# AI Player Props Prediction Engine (Standalone)

This is a completely isolated app for player props projections.

## What this includes

- Separate backend (FastAPI) in `backend/`
- Separate frontend (React + Vite) in `frontend/`
- No imports from the existing underdogs.bet codebase
- League support for:
  - MLB, NBA, NHL, NFL, Soccer, NCAAB, WNBA, NCAAF, NCAAW
- Top 50 player filtering per league (dynamic score from usage/minutes + prop frequency)
- Model stack (reimplemented simplified versions):
  - XGBoost-style regressor (mean projection proxy)
  - XSharp-style matchup/pace adjustment
  - TrueSkill/Glicko-style form rating
  - Ensemble + Sharp Consensus probability
  - Edge / EV vs odds
- API routes:
  - `GET /players`
  - `GET /props`
  - `GET /projections`

## Backend setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8101
```

Optional env vars:

- `ODDS_API_KEY` (optional, otherwise synthetic odds fallback is used)
- `ODDS_API_BASE` (default: `https://api.the-odds-api.com/v4`)
- `CACHE_TTL_SECONDS` (default: `600`)

## Frontend setup

```bash
cd frontend
npm install
npm run dev
```

Vite is fixed to **port 5179** (`strictPort: true`). It serves at **http://localhost:5179/** (or http://127.0.0.1:5179/). You must keep this process running while you use the UI; if nothing is listening, the browser shows `ERR_CONNECTION_REFUSED`. If dev start fails with “port already in use”, stop the other process using 5179.

Scripts invoke Vite via `node` so `npm run dev` works even when the `vite` CLI is not on your shell `PATH`.

In **`npm run dev`**, the browser calls **`/api/...`** on the Vite server, which **proxies** to `http://127.0.0.1:8101` (avoids CORS and localhost vs 127.0.0.1 mismatches). Set **`VITE_PROPS_API_BASE`** in `frontend/.env` only if you want to talk to the API directly (proxy is skipped).

## Link from the main underdogs.bet site

The main Flask app menu item **Player Props** goes to **`/player-props`**. With no env set, that page explains how to run this stack. Set **`PLAYER_PROPS_URL`** on Flask (e.g. `https://props.example.com`) to redirect straight to your hosted props app.

## Notes

- This module is standalone by design and not wired into the existing site.
- Data retrieval uses ESPN endpoints where available and fills with robust fallbacks for local/dev reliability.
