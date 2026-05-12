import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import SUPPORTED_LEAGUES
from .engine import filter_props, get_diagnostics, get_league_data, get_league_results
from .models import PlayersResponse, PropsResponse


app = FastAPI(title="AI Player Props Prediction Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "service": "standalone-player-props"}


@app.get("/")
def root():
    ui_url = (os.getenv("PLAYER_PROPS_UI_URL") or "http://localhost:5179/").strip()
    # If users land on the API root in browser, send them to the React UI.
    if ui_url.startswith(("http://", "https://")):
        return RedirectResponse(url=ui_url, status_code=307)
    return HTMLResponse(
        content=(
            "<h2>Standalone Player Props API</h2>"
            "<p>UI is available at <code>http://localhost:5179/</code>.</p>"
            "<p>Health endpoint: <code>/health</code></p>"
        ),
        status_code=200,
    )


@app.get("/leagues")
def leagues():
    return {"leagues": SUPPORTED_LEAGUES}


@app.get("/players", response_model=PlayersResponse)
def players(league: str = Query(..., description="League code, e.g. NBA")):
    league = league.upper()
    if league not in SUPPORTED_LEAGUES:
        raise HTTPException(status_code=400, detail=f"Unsupported league: {league}")
    data = get_league_data(league)
    resp = {"league": league, "count": len(data["players"]), "items": data["players"]}
    if "excluded_players" in data:
        resp["excluded_players"] = data["excluded_players"]
    return resp


@app.get("/props", response_model=PropsResponse)
def props(
    league: str = Query(...),
    prop_type: str | None = Query(None),
    side: str | None = Query(None),
    min_ev: float | None = Query(None),
):
    league = league.upper()
    if league not in SUPPORTED_LEAGUES:
        raise HTTPException(status_code=400, detail=f"Unsupported league: {league}")
    data = get_league_data(league)
    rows = filter_props(data["props"], prop_type=prop_type, side=side, min_ev=min_ev)
    resp = {"league": league, "count": len(rows), "items": rows}
    if "excluded_players" in data:
        resp["excluded_players"] = data["excluded_players"]
    if "model_variance" in data:
        resp["model_variance"] = data["model_variance"]
    if "sanity_flags" in data:
        resp["sanity_flags"] = data["sanity_flags"]
    return resp


@app.get("/projections", response_model=PropsResponse)
def projections(
    league: str = Query(...),
    prop_type: str | None = Query(None),
    side: str | None = Query(None),
    min_ev: float | None = Query(None),
):
    return props(league=league, prop_type=prop_type, side=side, min_ev=min_ev)


@app.get("/results")
def results(league: str = Query(...)):
    league = league.upper()
    if league not in SUPPORTED_LEAGUES:
        raise HTTPException(status_code=400, detail=f"Unsupported league: {league}")
    return get_league_results(league)


@app.get("/diagnostics")
def diagnostics(league: str = Query("NBA")):
    league = league.upper()
    if league not in SUPPORTED_LEAGUES:
        raise HTTPException(status_code=400, detail=f"Unsupported league: {league}")
    return get_diagnostics(league)
