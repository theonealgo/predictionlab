"""NCAAB / NCAAW team ratings: the Prediction Lab line while teams have few games.

Early in a season the efficiency projection (each team's last games) has
nothing to work from. These ratings are fit on Division I finals by ESPN team
id, starting from last season's ratings (data/college_ratings_prior_<SPORT>.json,
built offline) so a team's own games take over as they are played:

    home_pts = mu + off[home] - def[away] + hca / 2   (no hca on neutral courts)
    away_pts = mu + off[away] - def[home] - hca / 2

refresh(sport) refits from this season's ESPN scoreboards (one request per
day, finished days kept on disk) and saves data/college_ratings_<SPORT>.json.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DAYS = ROOT / ".cache" / "college_days"
LEAGUE = {"NCAAB": "mens-college-basketball", "NCAAW": "womens-college-basketball"}
SEASON_START = {"NCAAB": date(2026, 11, 1), "NCAAW": date(2026, 11, 1)}
FEW_GAMES = 8        # below this a team's own games are too few for the efficiency line
NEW_TEAM_GAMES = 3   # a school with no rating last season needs this many games first
PRIOR_GAMES = 6.0    # a team's prior counts like this many games
RIDGE = 2.0

_LOCK = threading.Lock()
_RATINGS: dict[str, tuple[float, dict]] = {}


def _norm(name: str) -> str:
    txt = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", txt.lower().replace("&", "and"))


def _path(sport: str) -> Path:
    return DATA / f"college_ratings_{sport}.json"


def ratings(sport: str) -> dict | None:
    sport = (sport or "").upper()
    if sport not in LEAGUE:
        return None
    path = _path(sport)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _LOCK:
        hit = _RATINGS.get(sport)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        _RATINGS[sport] = (mtime, data)
        return data


def team_id(r: dict, name: str) -> str | None:
    return (r.get("names") or {}).get(_norm(name))


def project(sport: str, home: str, away: str, neutral: bool = False, *, only_if_few: bool = True):
    """(home_pts, away_pts) from the ratings, or None.

    only_if_few: None when both teams already have FEW_GAMES games this season
    (their own efficiency line takes over).
    """
    r = ratings(sport)
    if not r:
        return None
    h, a = team_id(r, home), team_id(r, away)
    off, de = r.get("off") or {}, r.get("def") or {}
    if not h or not a or h not in off or a not in off:
        return None
    gp = r.get("gp") or {}
    new = set(r.get("new") or ())
    if any(t in new and gp.get(t, 0) < NEW_TEAM_GAMES for t in (h, a)):
        return None
    if only_if_few and gp.get(h, 0) >= FEW_GAMES and gp.get(a, 0) >= FEW_GAMES:
        return None
    half = 0.0 if neutral else float(r["hca"]) / 2
    hp = float(r["mu"]) + off[h] - de[a] + half
    ap = float(r["mu"]) + off[a] - de[h] - half
    return hp, ap


# ── refit from ESPN finals ───────────────────────────────────────────────────

def _fetch_day(sport: str, day: date) -> list[dict]:
    path = DAYS / sport / f"{day.isoformat()}.json"
    done_day = day < date.today() - timedelta(days=1)
    if done_day and path.is_file():
        return json.loads(path.read_text())
    import requests

    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/basketball/{LEAGUE[sport]}/scoreboard"
        f"?dates={day:%Y%m%d}&groups=50&limit=500"
    )
    data = requests.get(url, timeout=20).json()
    games = []
    for ev in data.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        if not comp.get("status", {}).get("type", {}).get("completed"):
            continue
        teams = comp.get("competitors") or []
        home = next((t for t in teams if t.get("homeAway") == "home"), None)
        away = next((t for t in teams if t.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        try:
            hs, as_ = int(home.get("score")), int(away.get("score"))
        except (TypeError, ValueError):
            continue
        games.append({
            "date": day.isoformat(),
            "neutral": bool(comp.get("neutralSite")),
            "home": home["team"].get("displayName"), "away": away["team"].get("displayName"),
            "home_id": home["team"].get("id"), "away_id": away["team"].get("id"),
            "home_score": hs, "away_score": as_,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games))
    return games


def _fit(games: list[dict], prior: dict) -> dict:
    import numpy as np

    ids = sorted({g["home_id"] for g in games} | {g["away_id"] for g in games} | set(prior["off"]))
    idx = {t: i for i, t in enumerate(ids)}
    n = len(ids)
    cols = 2 * n + 2
    X = np.zeros((2 * len(games), cols))
    y = np.zeros(2 * len(games))
    for k, g in enumerate(games):
        h, a = idx[g["home_id"]], idx[g["away_id"]]
        site = 0.0 if g.get("neutral") else 1.0
        X[2 * k, [h, n + a, 2 * n, 2 * n + 1]] = [1, -1, 1, site]
        y[2 * k] = g["home_score"]
        X[2 * k + 1, [a, n + h, 2 * n, 2 * n + 1]] = [1, -1, 1, -site]
        y[2 * k + 1] = g["away_score"]
    reg = np.zeros(cols)
    target = np.zeros(cols)
    for t, i in idx.items():
        known = t in prior["off"]
        reg[i] = reg[n + i] = PRIOR_GAMES if known else RIDGE
        target[i] = prior["off"].get(t, 0.0)
        target[n + i] = prior["def"].get(t, 0.0)
    reg[2 * n] = reg[2 * n + 1] = 50.0
    target[2 * n] = prior["mu"]
    target[2 * n + 1] = prior["hca"] / 2
    beta = np.linalg.solve(X.T @ X + np.diag(reg), X.T @ y + reg * target)
    return {
        "mu": float(beta[2 * n]), "hca": float(2 * beta[2 * n + 1]),
        "off": {t: float(beta[i]) for t, i in idx.items()},
        "def": {t: float(beta[n + i]) for t, i in idx.items()},
    }


def refresh(sport: str) -> bool:
    """Refit this season's ratings from ESPN finals. False when nothing was saved."""
    sport = (sport or "").upper()
    prior_path = DATA / f"college_ratings_prior_{sport}.json"
    if sport not in LEAGUE or not prior_path.is_file():
        return False
    prior = json.loads(prior_path.read_text())
    games: list[dict] = []
    day = SEASON_START[sport]
    today = date.today()
    while day <= today:
        try:
            games.extend(_fetch_day(sport, day))
        except Exception as exc:
            logger.warning(f"[college_ratings] {sport} {day} fetch failed: {exc}")
            return False
        day += timedelta(days=1)
    fit = _fit(games, prior)
    names = dict(prior.get("names") or {})
    gp: dict[str, int] = {}
    for g in games:
        for side in ("home", "away"):
            names[_norm(g[side])] = g[f"{side}_id"]
            gp[g[f"{side}_id"]] = gp.get(g[f"{side}_id"], 0) + 1
    fit.update({"names": names, "gp": gp, "new": sorted(set(fit["off"]) - set(prior["off"])), "games": len(games),
                "built": datetime.now().isoformat(timespec="seconds")})
    tmp = _path(sport).with_suffix(".tmp")
    tmp.write_text(json.dumps(fit))
    tmp.replace(_path(sport))
    logger.info(f"[college_ratings] {sport}: {len(games)} games this season, {len(fit['off'])} teams")
    return True
