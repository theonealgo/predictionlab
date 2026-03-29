# Odds Engine Stack

This folder contains the full sportsbook-style odds engine stack (Fastify + Python + Postgres + Redis).

## Quick start

```bash
docker compose up --build
```

Services:
- Fastify API: http://localhost:7000
- Python model service: http://localhost:7001
- Postgres: localhost:5433 (db: `odds_engine`)
- Redis: localhost:6380

## API endpoints

- `GET /health`
- `GET /games`
- `GET /odds?gameId=...` or `GET /odds?sport=...&home=...&away=...`
- `GET /model?gameId=...`
- `POST /bet` (body: `{ gameId, market, side, stake }`)
- `GET /risk?gameId=...`

## Sample data

Sample games and team stats are seeded via `db/init.sql`.

## Local integration (Flask app)

Set the Flask app to read odds from this service:

```bash
export ODDS_ENGINE_URL=http://localhost:7000
```
