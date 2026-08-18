"""Shared sport blog/preview article builder (SEO + content).

Read-only against stored Prediction Lab picks. Does not compute or change models.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

SITE_DOMAIN = "https://predictionlab.io"
OG_IMAGE_PATH = "/static/apple-touch-icon.png"
DEFAULT_LOGO_PATH = "/static/pl-logo.svg"
MLB_PICKS_PATH = "/mlb-picks"
MLB_RESULTS_PATH = "/mlb-results"
BLOG_INDEX_PATH = "/blog"
PUBLISHER_NAME = "GoodsandMore Inc."
AUTHOR_NAME = "Prediction Lab"
MLB_KEEP_DAYS = 14
PREVIEW_KEEP_DAYS = 14
HUB_MAX_LINKS = 12
RELATED_MAX = 6

SPORT_PICKS_PATHS = {
    "NHL": "/nhl-picks",
    "NBA": "/nba-picks",
    "NFL": "/nfl-picks",
    "MLB": "/mlb-picks",
    "NCAAB": "/ncaab-picks",
    "NCAAW": "/ncaaw-picks",
    "NCAAF": "/ncaaf-picks",
    "WNBA": "/wnba-picks",
    "SOCCER": "/soccer-picks",
    "TENNIS": "/tennis-picks",
    "GOLF": "/golf-picks",
    "CFL": "/cfl-picks",
}
SPORT_RESULTS_PATHS = {
    key: path.replace("-picks", "-results") for key, path in SPORT_PICKS_PATHS.items()
}
HUB_SPORTS = tuple(SPORT_PICKS_PATHS.keys())
HUB_SPORT_PATHS = {key.lower() for key in HUB_SPORTS}

SPORT_DISPLAY = {
    "NHL": "NHL",
    "NBA": "NBA",
    "NFL": "NFL",
    "MLB": "MLB",
    "NCAAB": "NCAAB",
    "NCAAW": "NCAAW",
    "NCAAF": "NCAAF",
    "WNBA": "WNBA",
    "SOCCER": "Soccer",
    "TENNIS": "Tennis",
    "GOLF": "Golf",
    "CFL": "CFL",
}

_FULL_NAME_SLUG_SPORTS = {"NCAAB", "NCAAF", "NCAAW", "SOCCER", "TENNIS", "UFC", "GOLF"}
_VS_SPORTS = {"UFC", "TENNIS", "GOLF"}
_MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}
_TIME_LABEL = {
    "MLB": "First pitch",
    "NBA": "Tip",
    "WNBA": "Tip",
    "NCAAB": "Tip",
    "NCAAW": "Tip",
    "NFL": "Kickoff",
    "NCAAF": "Kickoff",
    "CFL": "Kickoff",
    "NHL": "Puck drop",
    "SOCCER": "Kickoff",
    "TENNIS": "Start",
    "UFC": "Start",
    "GOLF": "Tee time",
}
_VISIT_VERB = {
    "MLB": "visit",
    "NBA": "visit",
    "WNBA": "visit",
    "NCAAB": "visit",
    "NCAAW": "visit",
    "NFL": "visit",
    "NCAAF": "visit",
    "CFL": "visit",
    "NHL": "visit",
    "SOCCER": "visit",
    "TENNIS": "face",
    "UFC": "face",
    "GOLF": "tee off at",
}

# Two-word MLB nicknames that would otherwise collapse to "sox" / "jays".
_NICKNAME_OVERRIDES = {
    "chicago white sox": "white-sox",
    "boston red sox": "red-sox",
    "toronto blue jays": "blue-jays",
}

_MODEL_KEYS = (
    ("Grinder2", ("glicko2_prob", "glicko_home_prob", "glicko2_home_prob")),
    ("Takedown", ("trueskill_prob", "trueskill_home_prob", "logistic_home_prob")),
    ("Edge", ("elo_prob", "elo_home_prob", "stored_elo_prob")),
    ("XSharp", ("xgb_prob", "xgboost_home_prob", "stored_xgb_prob")),
    ("Efficiency", ("disp_ml_prob", "efficiency_prob")),
    ("Sharp Consensus", ("ensemble_prob", "ens_prob", "win_probability", "stored_ensemble_prob", "meta_home_prob")),
)

_PITCHER_RE = re.compile(
    r"^\s*(?P<name>.+?)\s*(?:\((?P<stats>[^)]+)\))?\s*$"
)
_ERA_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")
_WL_RE = re.compile(r"\b(\d+\s*-\s*\d+)\b")


def _slug_token(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def mlb_nickname_slug(team_name: str) -> str:
    raw = re.sub(r"\s+", " ", (team_name or "").strip().lower())
    if raw in _NICKNAME_OVERRIDES:
        return _NICKNAME_OVERRIDES[raw]
    if not raw:
        return "mlb"
    parts = raw.split()
    if len(parts) >= 3 and parts[-2] in ("white", "red", "blue"):
        return _slug_token(f"{parts[-2]} {parts[-1]}")
    return _slug_token(parts[-1])


def mlb_article_slug(away: str, home: str, date_str: str) -> str:
    return f"{mlb_nickname_slug(away)}-vs-{mlb_nickname_slug(home)}-prediction-{date_str}"


def mlb_canonical_path(away: str, home: str, date_str: str) -> str:
    return f"/blog/mlb/{mlb_article_slug(away, home, date_str)}"


def sport_display_name(sport: str) -> str:
    key = str(sport or "").upper()
    return SPORT_DISPLAY.get(key, key or "Sports")


def sport_picks_path(sport: str) -> str:
    key = str(sport or "").upper()
    return SPORT_PICKS_PATHS.get(key) or "/"


def sport_results_path(sport: str) -> str:
    key = str(sport or "").upper()
    return SPORT_RESULTS_PATHS.get(key) or "/"


def sport_nickname_slug(sport: str, team_name: str) -> str:
    sport_u = str(sport or "").upper()
    if sport_u == "MLB":
        return mlb_nickname_slug(team_name)
    if sport_u in _FULL_NAME_SLUG_SPORTS:
        return _slug_token(team_name) or sport_u.lower()
    raw = re.sub(r"\s+", " ", (team_name or "").strip().lower())
    if not raw:
        return sport_u.lower() or "sport"
    return _slug_token(raw.split()[-1]) or sport_u.lower()


def sport_article_slug(sport: str, away: str, home: str, date_str: str) -> str:
    sport_u = str(sport or "").upper()
    if sport_u == "MLB":
        return mlb_article_slug(away, home, date_str)
    return (
        f"{sport_nickname_slug(sport_u, away)}-vs-"
        f"{sport_nickname_slug(sport_u, home)}-prediction-{date_str}"
    )


def sport_canonical_path(sport: str, away: str, home: str, date_str: str) -> str:
    sport_u = str(sport or "").upper()
    slug = sport_article_slug(sport_u, away, home, date_str)
    return f"/blog/{sport_u.lower()}/{slug}"


def is_hub_sport(sport: str) -> bool:
    return str(sport or "").upper() in HUB_SPORTS


def dated_picks_path(sport: str, date_str: str) -> str:
    picks = sport_picks_path(sport)
    raw = str(date_str or "")[:10]
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except Exception:
        return picks
    month = _MONTH_NAMES.get(dt.month, "january")
    return f"{picks}-{month}-{dt.day}-{dt.year}"


def empty_sections() -> dict:
    return {
        "intro": [],
        "game_info": {},
        "pitchers": {"away": {}, "home": {}},
        "models": [],
        "consensus": None,
        "markets": {},
        "why": [],
        "takeaway": "",
        "disclaimer": (
            "Prediction Lab model outputs are statistical estimates from the stored board. "
            "They are not guarantees, not betting advice, and can miss."
        ),
    }


def coerce_sections(post: Optional[dict]) -> dict:
    """Always return a mapping so templates never see sections: null."""
    if not isinstance(post, dict):
        return empty_sections()
    sections = post.get("sections")
    out = empty_sections()
    if isinstance(sections, dict):
        intro = sections.get("intro")
        if isinstance(intro, list):
            out["intro"] = [str(p) for p in intro if str(p).strip()]
        game_info = sections.get("game_info")
        out["game_info"] = dict(game_info) if isinstance(game_info, dict) else {}
        pitchers = sections.get("pitchers")
        if isinstance(pitchers, dict):
            away_p = pitchers.get("away") if isinstance(pitchers.get("away"), dict) else {}
            home_p = pitchers.get("home") if isinstance(pitchers.get("home"), dict) else {}
            out["pitchers"] = {"away": dict(away_p), "home": dict(home_p)}
        models = sections.get("models")
        out["models"] = list(models) if isinstance(models, list) else []
        consensus = sections.get("consensus")
        out["consensus"] = dict(consensus) if isinstance(consensus, dict) else None
        markets = sections.get("markets")
        out["markets"] = dict(markets) if isinstance(markets, dict) else {}
        why = sections.get("why")
        out["why"] = [str(p) for p in why if str(p).strip()] if isinstance(why, list) else []
        if sections.get("takeaway"):
            out["takeaway"] = str(sections.get("takeaway"))
        if sections.get("disclaimer"):
            out["disclaimer"] = str(sections.get("disclaimer"))
    if not out["intro"]:
        body = post.get("body") or []
        if isinstance(body, list):
            out["intro"] = [str(p).strip() for p in body if str(p).strip()]
        elif str(body).strip():
            out["intro"] = [str(body).strip()]
    post["sections"] = out
    return out


def legacy_preview_slug(sport: str, away: str, home: str, date_str: str, *, is_mma: bool = False) -> str:
    core = f"{away}-vs-{home}" if is_mma else f"{away}-at-{home}"
    return _slug_token(f"{sport.lower()}-{core}-preview-{date_str}")


def is_mlb_prediction_slug(slug: str) -> bool:
    return bool(re.search(r"-prediction-\d{4}-\d{2}-\d{2}$", str(slug or "").strip().lower()))


def is_game_preview_slug(slug: str) -> bool:
    s = str(slug or "").strip().lower()
    return bool(re.search(r"-preview-\d{4}-\d{2}-\d{2}$", s) or is_mlb_prediction_slug(s))


def short_display_date(date_str: str, display_date: str = "") -> str:
    raw = str(date_str or "")[:10]
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.strftime("%b. %-d, %Y") if hasattr(dt, "strftime") else dt.strftime("%b. %d, %Y").replace(" 0", " ")
    except Exception:
        return (display_date or raw).replace("August", "Aug.")


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def names_match(a: str, b: str) -> bool:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    sa, sb = set(na.split()), set(nb.split())
    if sa & sb and (mlb_nickname_slug(a) == mlb_nickname_slug(b)):
        return True
    return mlb_nickname_slug(a) == mlb_nickname_slug(b) and len(mlb_nickname_slug(a)) > 2


def parse_pitcher(raw: str) -> dict:
    text = str(raw or "").strip()
    if not text or text.upper() == "TBD":
        return {}
    m = _PITCHER_RE.match(text)
    name = (m.group("name") if m else text).strip()
    stats = (m.group("stats") if m else "") or ""
    out: dict[str, Any] = {"name": name, "raw": text}
    wl = _WL_RE.search(stats)
    if wl:
        out["record"] = re.sub(r"\s+", "", wl.group(1))
    era_m = None
    if "," in stats:
        era_m = _ERA_RE.search(stats.split(",", 1)[-1])
    elif "era" in stats.lower():
        era_m = _ERA_RE.search(stats)
    elif stats and not wl:
        era_m = _ERA_RE.search(stats)
    elif stats and wl and "," in stats:
        era_m = _ERA_RE.search(stats.split(",", 1)[-1])
    if era_m:
        try:
            out["era"] = float(era_m.group(1))
        except (TypeError, ValueError):
            pass
    return out


def _as_fraction(value) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 1.5:
        n = n / 100.0
    if n <= 0 or n >= 1:
        return None
    return n


def _as_pct(value) -> Optional[float]:
    frac = _as_fraction(value)
    if frac is None:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return None
        if 1.5 < n <= 100:
            return round(n, 1)
        return None
    return round(frac * 100.0, 1)


def _fmt_american(odds) -> Optional[str]:
    try:
        n = float(odds)
    except (TypeError, ValueError):
        return None
    n = int(round(n))
    if n == 0:
        return None
    return f"+{n}" if n > 0 else str(n)


def _prob_to_american(frac: Optional[float]) -> Optional[str]:
    if frac is None or frac <= 0 or frac >= 1:
        return None
    if frac >= 0.5:
        return _fmt_american(-100.0 * frac / (1.0 - frac))
    return _fmt_american(100.0 * (1.0 - frac) / frac)


def _fmt_line(value) -> Optional[str]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if abs(n - round(n)) < 1e-9:
        n = int(round(n))
        return f"{n:+d}" if n != 0 else "0"
    return f"{n:+.1f}"


def _fmt_total(value) -> Optional[str]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.1f}"


def _fmt_score(away_pts, home_pts) -> Optional[str]:
    try:
        a, h = float(away_pts), float(home_pts)
    except (TypeError, ValueError):
        return None
    def _one(n):
        return str(int(round(n))) if abs(n - round(n)) < 1e-9 else f"{n:.1f}"
    return f"{_one(a)}–{_one(h)}"


def extract_model_rows(pick: Optional[dict], away: str, home: str) -> list[dict]:
    if not isinstance(pick, dict):
        return []
    rows = []
    for label, keys in _MODEL_KEYS:
        home_pct = None
        for key in keys:
            home_pct = _as_pct(pick.get(key))
            if home_pct is not None:
                break
        if home_pct is None:
            continue
        home_fav = home_pct >= 50.0
        side = home if home_fav else away
        side_pct = home_pct if home_fav else round(100.0 - home_pct, 1)
        rows.append({
            "name": label,
            "home_pct": home_pct,
            "away_pct": round(100.0 - home_pct, 1),
            "side": side,
            "side_pct": side_pct,
        })
    return rows


def _first_pick_value(pick: dict, keys: tuple) -> Any:
    for key in keys:
        val = pick.get(key)
        if val is not None and val != "":
            return val
    return None


def extract_markets(pick: Optional[dict]) -> dict:
    if not isinstance(pick, dict):
        return {}
    out: dict[str, Any] = {}
    book_home = _first_pick_value(pick, ("book_home_moneyline", "home_moneyline", "b_home_ml"))
    book_away = _first_pick_value(pick, ("book_away_moneyline", "away_moneyline", "b_away_ml"))
    if _fmt_american(book_home):
        out["book_home_ml"] = _fmt_american(book_home)
    if _fmt_american(book_away):
        out["book_away_ml"] = _fmt_american(book_away)
    pl_home = _first_pick_value(pick, ("pl_model_home_ml",))
    pl_away = _first_pick_value(pick, ("pl_model_away_ml",))
    if _fmt_american(pl_home):
        out["pl_home_ml"] = _fmt_american(pl_home)
    if _fmt_american(pl_away):
        out["pl_away_ml"] = _fmt_american(pl_away)
    book_rl = _first_pick_value(pick, ("disp_book_spread", "book_spread", "spread", "market_spread"))
    pl_rl = _first_pick_value(pick, ("disp_pl_spread", "our_spread"))
    xs_rl = _first_pick_value(pick, ("disp_xs_spread", "xsharp_spread", "xgb_spread"))
    if _fmt_line(book_rl):
        out["book_run_line"] = _fmt_line(book_rl)
    if _fmt_line(pl_rl):
        out["pl_run_line"] = _fmt_line(pl_rl)
    if _fmt_line(xs_rl):
        out["xs_run_line"] = _fmt_line(xs_rl)
    book_tot = _first_pick_value(pick, ("disp_book_total", "book_total", "market_total", "total"))
    pl_tot = _first_pick_value(pick, ("disp_pl_total", "our_total", "predicted_total"))
    xs_tot = _first_pick_value(pick, ("disp_xs_total", "xsharp_total", "xgb_total"))
    if _fmt_total(book_tot):
        out["book_total"] = _fmt_total(book_tot)
    if _fmt_total(pl_tot):
        out["pl_total"] = _fmt_total(pl_tot)
    if _fmt_total(xs_tot):
        out["xs_total"] = _fmt_total(xs_tot)
    pl_proj = _fmt_score(
        _first_pick_value(pick, ("pl_proj_away_pts", "our_away_pts", "naive_away_score")),
        _first_pick_value(pick, ("pl_proj_home_pts", "our_home_pts", "naive_home_score")),
    )
    xs_proj = _fmt_score(
        _first_pick_value(pick, ("xs_proj_away_pts", "xsharp_away_score", "xgb_away_score")),
        _first_pick_value(pick, ("xs_proj_home_pts", "xsharp_home_score", "xgb_home_score")),
    )
    if pl_proj:
        out["pl_proj"] = pl_proj
    if xs_proj:
        out["xs_proj"] = xs_proj
    h2h = _first_pick_value(pick, ("h2h_last10_meetings", "h2h_last10_games"))
    if h2h:
        out["h2h"] = str(h2h).strip()
    h2h_total = _first_pick_value(pick, ("h2h_last10_total", "our_total"))
    h2h_games = _first_pick_value(pick, ("our_total_games", "h2h_last10_games"))
    if _fmt_total(h2h_total) and h2h_games:
        out["h2h_total"] = f"{_fmt_total(h2h_total)} combined runs over {h2h_games} meetings"
    elif out.get("h2h"):
        pass
    return out


def lookup_sport_pick_from_db(
    db_path: str, sport: str, away: str, home: str, date_str: str
) -> Optional[dict]:
    if not db_path:
        return None
    sport_u = str(sport or "").upper() or "MLB"
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT p.game_id, p.game_date, p.home_team_id, p.away_team_id,
                   p.win_probability, p.predicted_total, p.predicted_winner,
                   p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob,
                   p.meta_home_prob, p.glicko_home_prob, p.trueskill_home_prob,
                   p.key_factors,
                   e.home_moneyline AS e_home_ml, e.away_moneyline AS e_away_ml,
                   e.spread AS e_spread, e.total AS e_total,
                   b.home_moneyline AS b_home_ml, b.away_moneyline AS b_away_ml,
                   b.spread AS b_spread, b.total AS b_total
            FROM predictions p
            LEFT JOIN engine_odds e
              ON e.game_id = p.game_id AND COALESCE(e.sport, ?) = ?
            LEFT JOIN betting_odds b ON b.game_id = p.game_id
            WHERE p.sport = ? AND date(p.game_date) = date(?)
            """,
            (sport_u, sport_u, sport_u, date_str),
        ).fetchall()
        conn.close()
    except Exception:
        return None
    for row in rows:
        d = dict(row)
        if names_match(d.get("away_team_id") or "", away) and names_match(d.get("home_team_id") or "", home):
            pick = {
                "home_team_id": d.get("home_team_id"),
                "away_team_id": d.get("away_team_id"),
                "game_date": d.get("game_date"),
                "win_probability": d.get("win_probability"),
                "elo_home_prob": d.get("elo_home_prob"),
                "xgboost_home_prob": d.get("xgboost_home_prob"),
                "logistic_home_prob": d.get("logistic_home_prob"),
                "meta_home_prob": d.get("meta_home_prob"),
                "glicko_home_prob": d.get("glicko_home_prob"),
                "trueskill_home_prob": d.get("trueskill_home_prob"),
                "predicted_total": d.get("predicted_total"),
                "predicted_winner": d.get("predicted_winner"),
                "key_factors": d.get("key_factors"),
                "home_moneyline": d.get("e_home_ml") or d.get("b_home_ml"),
                "away_moneyline": d.get("e_away_ml") or d.get("b_away_ml"),
                "book_spread": d.get("e_spread") or d.get("b_spread"),
                "book_total": d.get("e_total") or d.get("b_total"),
            }
            return pick
    return None


def lookup_mlb_pick_from_db(db_path: str, away: str, home: str, date_str: str) -> Optional[dict]:
    return lookup_sport_pick_from_db(db_path, "MLB", away, home, date_str)


def find_pick_in_slate(slate: Optional[list], away: str, home: str, date_str: str) -> Optional[dict]:
    if not slate:
        return None
    target = str(date_str or "")[:10]
    for pred in slate:
        if not isinstance(pred, dict):
            continue
        gd = str(pred.get("game_date") or pred.get("date") or "")[:10]
        if target and gd and gd != target:
            continue
        if names_match(pred.get("away_team_id") or pred.get("away") or "", away) and names_match(
            pred.get("home_team_id") or pred.get("home") or "", home
        ):
            return pred
    return None


def _clip_meta(text: str, lo: int = 150, hi: int = 160, sport: str = "MLB") -> str:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    label = sport_display_name(sport)
    extras = (
        f" See the live {label} picks board.",
        f" See today's {label} picks.",
        f" See {label} picks.",
        " See picks.",
    )
    if len(clean) < lo:
        for extra in extras:
            if lo <= len(clean + extra) <= hi:
                clean = clean + extra
                break
        else:
            room = hi - len(clean)
            if room >= 3:
                clean = (clean + " See picks.")[:hi].rstrip()
    if lo <= len(clean) <= hi:
        return clean
    if len(clean) <= hi:
        return clean
    cut = clean[: hi - 1].rsplit(" ", 1)[0].rstrip(".,;:")
    return (cut or clean[: hi - 1]).strip() + "…"


def _iso_datetime(date_str: str, hour: int = 12) -> str:
    raw = str(date_str or "")[:10]
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return f"{raw}T{hour:02d}:00:00-04:00"
    except Exception:
        return f"{raw}T12:00:00-04:00"


def _fingerprint(parts: dict) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _pitcher_clause(team: str, parsed: dict) -> str:
    if not parsed.get("name"):
        return ""
    bits = [parsed["name"]]
    extra = []
    if parsed.get("record"):
        extra.append(parsed["record"])
    if parsed.get("era") is not None:
        extra.append(f"{parsed['era']:.2f} ERA")
    if extra:
        bits.append(f"({', '.join(extra)})")
    return f"{team} { ' '.join(bits) }"


def build_mlb_article(
    game: dict,
    display_date: str,
    pick: Optional[dict] = None,
    *,
    related: Optional[list] = None,
    existing: Optional[dict] = None,
    site_domain: str = SITE_DOMAIN,
) -> dict:
    away = str(game.get("away") or "Away").strip()
    home = str(game.get("home") or "Home").strip()
    date_str = str(game.get("date") or "")[:10]
    display = display_date or short_display_date(date_str)
    short_date = short_display_date(date_str, display)
    slug = mlb_article_slug(away, home, date_str)
    canonical_path = mlb_canonical_path(away, home, date_str)
    legacy = legacy_preview_slug("MLB", away, home, date_str)
    time_bit = str(game.get("local_time") or "").strip()
    venue = str(game.get("venue") or "").strip()
    city = str(game.get("city") or "").strip()
    venue_bit = venue
    if city and city.lower() not in venue.lower():
        venue_bit = f"{venue} ({city})" if venue else city
    away_rec = str(game.get("away_record") or "").strip()
    home_rec = str(game.get("home_record") or "").strip()
    status = str(game.get("status") or "").strip()
    away_p = parse_pitcher(game.get("away_pitcher") or "")
    home_p = parse_pitcher(game.get("home_pitcher") or "")
    models = extract_model_rows(pick, away, home)
    markets = extract_markets(pick)
    consensus = next((m for m in models if m["name"] == "Sharp Consensus"), None)
    if consensus is None and models:
        consensus = models[-1]

    h1 = f"{away} vs {home} Prediction, Odds & Preview — {short_date}"
    seo_title = h1

    meta_bits = [f"{away} at {home} on {short_date}."]
    if consensus:
        meta_bits.append(
            f"Prediction Lab Sharp Consensus leans {consensus['side']} at {consensus['side_pct']}%."
        )
    elif away_p.get("name") or home_p.get("name"):
        starters = []
        if away_p.get("name"):
            starters.append(away_p["name"])
        if home_p.get("name"):
            starters.append(home_p["name"])
        meta_bits.append("Probable starters: " + " vs ".join(starters) + ".")
    meta_bits.append("Model outputs, not guarantees.")
    meta_description = _clip_meta(" ".join(meta_bits))

    intro = []
    lead = f"{away} visit the {home} on {display}"
    if time_bit:
        lead += f", with first pitch listed for {time_bit}"
    if venue_bit:
        lead += f" at {venue_bit}"
    lead += "."
    if away_rec or home_rec:
        rec_bits = []
        if away_rec:
            rec_bits.append(f"{away} enter at {away_rec}")
        if home_rec:
            rec_bits.append(f"{home} sit at {home_rec}")
        lead += " " + " and ".join(rec_bits) + "."
    intro.append(lead)

    if away_p.get("name") or home_p.get("name"):
        p_bits = []
        ac = _pitcher_clause(away, away_p)
        hc = _pitcher_clause(home, home_p)
        if ac:
            p_bits.append(ac)
        if hc:
            p_bits.append(hc)
        intro.append(
            "Probable starters from the day's board: "
            + "; ".join(p_bits)
            + ". Starter listings can change; this page updates when the stored board changes."
        )

    if consensus:
        intro.append(
            f"Prediction Lab's stored Sharp Consensus currently gives {consensus['side']} "
            f"a {consensus['side_pct']}% win probability. That figure is a statistical model output "
            f"for this matchup — not a guarantee, and not betting advice."
        )
    else:
        intro.append(
            f"Open the live {away}–{home} card on Prediction Lab's MLB picks board for the current "
            f"model probabilities. Predictions are model outputs, not guarantees."
        )

    why = []
    if models:
        sides: dict[str, list[str]] = {}
        for row in models:
            sides.setdefault(row["side"], []).append(f"{row['name']} ({row['side_pct']}%)")
        if len(sides) == 1:
            side, names = next(iter(sides.items()))
            why.append(
                f"{len(names)} of {len(models)} models with a stored probability lean {side}: "
                + ", ".join(names)
                + "."
            )
        else:
            chunks = [f"{side}: {', '.join(names)}" for side, names in sides.items()]
            why.append(
                f"The stored models split on this moneyline. "
                + " ".join(chunks)
                + "."
            )
        if consensus and any(m["side"] != consensus["side"] for m in models if m["name"] != "Sharp Consensus"):
            dissenters = [m["name"] for m in models if m["name"] != "Sharp Consensus" and m["side"] != consensus["side"]]
            if dissenters:
                why.append(
                    f"Sharp Consensus leans {consensus['side']}, while "
                    + ", ".join(dissenters)
                    + " lean the other way on the stored probabilities."
                )
        if markets.get("book_home_ml") or markets.get("book_away_ml"):
            ml_bits = []
            if markets.get("book_away_ml"):
                ml_bits.append(f"{away} {markets['book_away_ml']}")
            if markets.get("book_home_ml"):
                ml_bits.append(f"{home} {markets['book_home_ml']}")
            why.append(
                "Available sportsbook moneyline on the stored board: "
                + ", ".join(ml_bits)
                + ". Compare that price with the model probability before treating either as a pick."
            )
    kf = ""
    if isinstance(pick, dict):
        kf = str(pick.get("key_factors") or "").strip()
        if kf and kf.lower() not in ("none", "null", "{}"):
            why.append(f"Stored model notes for this game: {kf}")

    takeaway_parts = [
        f"This preview is built from Prediction Lab's stored {away} at {home} board for {display}."
    ]
    if consensus:
        takeaway_parts.append(
            f"The current Sharp Consensus lean is {consensus['side']} at {consensus['side_pct']}%."
        )
    takeaway_parts.append(
        "Use the MLB picks page for the live card, then check graded results after the game. "
        "Model probabilities can be wrong; they are not locks."
    )

    body = list(intro)
    if why:
        body.extend(why)
    body.append(" ".join(takeaway_parts))
    excerpt = intro[0]
    if len(intro) > 1:
        excerpt = intro[0] + " " + intro[1]

    fp = _fingerprint({
        "away_pitcher": game.get("away_pitcher"),
        "home_pitcher": game.get("home_pitcher"),
        "away_record": away_rec,
        "home_record": home_rec,
        "status": status,
        "time": time_bit,
        "models": [(m["name"], m["home_pct"]) for m in models],
        "markets": markets,
    })
    published = None
    modified = None
    if isinstance(existing, dict):
        published = existing.get("date_published") or existing.get("date")
        if existing.get("content_fingerprint") == fp:
            modified = existing.get("date_modified") or published
    if not published:
        published = date_str
    if not modified:
        modified = datetime.now().strftime("%Y-%m-%d") if isinstance(existing, dict) and existing.get("content_fingerprint") != fp else published

    related_links = list(related or [])
    news_items = [{
        "sport": "MLB",
        "topic": "MLB picks — live model board",
        "summary_hint": "Prediction Lab MLB predictions",
        "source": "Prediction Lab",
        "url": MLB_PICKS_PATH,
    }]

    sections = {
        "intro": intro,
        "game_info": {
            "date": display,
            "time": time_bit,
            "time_label": "First pitch",
            "venue": venue_bit,
            "away": away,
            "home": home,
            "away_record": away_rec,
            "home_record": home_rec,
            "status": status,
        },
        "pitchers": {
            "away": away_p,
            "home": home_p,
        },
        "models": models,
        "consensus": consensus,
        "markets": markets,
        "why": why,
        "takeaway": " ".join(takeaway_parts),
        "disclaimer": (
            "Prediction Lab model outputs are statistical estimates from the stored board. "
            "They are not guarantees, not betting advice, and can miss."
        ),
    }

    canonical_url = f"{site_domain.rstrip('/')}{canonical_path}"
    return {
        "title": h1,
        "seo_title": seo_title,
        "slug": slug,
        "canonical_path": canonical_path,
        "canonical_url": canonical_url,
        "legacy_slug": legacy,
        "article_url": canonical_path,
        "date": date_str,
        "date_published": str(published)[:10],
        "date_modified": str(modified)[:10],
        "status": "published",
        "sport_tag": "MLB",
        "excerpt": excerpt,
        "meta_description": meta_description,
        "body": body,
        "sections": sections,
        "news_items": news_items,
        "related": related_links,
        "content_fingerprint": fp,
        "image": OG_IMAGE_PATH,
        "away": away,
        "home": home,
        "hub_label": f"{away} vs {home}",
    }


def build_sport_article(
    game: dict,
    display_date: str,
    pick: Optional[dict] = None,
    *,
    related: Optional[list] = None,
    existing: Optional[dict] = None,
    site_domain: str = SITE_DOMAIN,
) -> dict:
    sport = str((game or {}).get("sport") or "").upper()
    if sport == "MLB":
        return build_mlb_article(
            game,
            display_date,
            pick=pick,
            related=related,
            existing=existing,
            site_domain=site_domain,
        )
    if not sport:
        sport = "WNBA"
    away = str(game.get("away") or "Away").strip()
    home = str(game.get("home") or "Home").strip()
    date_str = str(game.get("date") or "")[:10]
    display = display_date or short_display_date(date_str)
    short_date = short_display_date(date_str, display)
    is_mma = bool(game.get("is_mma")) or sport in _VS_SPORTS
    slug = sport_article_slug(sport, away, home, date_str)
    canonical_path = sport_canonical_path(sport, away, home, date_str)
    legacy = legacy_preview_slug(sport, away, home, date_str, is_mma=is_mma)
    time_bit = str(game.get("local_time") or "").strip()
    venue = str(game.get("venue") or "").strip()
    city = str(game.get("city") or "").strip()
    venue_bit = venue
    if city and city.lower() not in venue.lower():
        venue_bit = f"{venue} ({city})" if venue else city
    away_rec = str(game.get("away_record") or "").strip()
    home_rec = str(game.get("home_record") or "").strip()
    status = str(game.get("status") or "").strip()
    models = extract_model_rows(pick, away, home)
    markets = extract_markets(pick)
    consensus = next((m for m in models if m["name"] == "Sharp Consensus"), None)
    if consensus is None and models:
        consensus = models[-1]
    display_sport = sport_display_name(sport)
    picks_path = sport_picks_path(sport)
    results_path = sport_results_path(sport)
    matchup = f"{away} vs {home}" if is_mma else f"{away} at {home}"
    h1 = f"{away} vs {home} Prediction, Odds & Preview — {short_date}"
    seo_title = h1

    meta_bits = [f"{matchup} on {short_date}."]
    if consensus:
        meta_bits.append(
            f"Prediction Lab Sharp Consensus leans {consensus['side']} at {consensus['side_pct']}%."
        )
    elif time_bit:
        meta_bits.append(f"Listed at {time_bit}.")
    meta_bits.append("Model outputs, not guarantees.")
    meta_description = _clip_meta(" ".join(meta_bits), sport=sport)

    visit = _VISIT_VERB.get(sport, "visit")
    intro = []
    if is_mma:
        lead = f"{away} vs {home} is on the {display_sport} board for {display}"
    else:
        lead = f"{away} {visit} the {home} on {display}"
    if time_bit:
        lead += f", listed for {time_bit}"
    if venue_bit:
        lead += f" at {venue_bit}"
    lead += "."
    if away_rec or home_rec:
        rec_bits = []
        if away_rec:
            rec_bits.append(f"{away} enter at {away_rec}")
        if home_rec:
            rec_bits.append(f"{home} sit at {home_rec}")
        lead += " " + " and ".join(rec_bits) + "."
    intro.append(lead)

    if consensus:
        intro.append(
            f"Prediction Lab's stored Sharp Consensus currently gives {consensus['side']} "
            f"a {consensus['side_pct']}% win probability. That figure is a statistical model output "
            f"for this matchup — not a guarantee, and not betting advice."
        )
    else:
        intro.append(
            f"Open the live {away}–{home} card on Prediction Lab's {display_sport} picks board for the current "
            f"model probabilities. Predictions are model outputs, not guarantees."
        )

    why = []
    if models:
        sides: dict[str, list[str]] = {}
        for row in models:
            sides.setdefault(row["side"], []).append(f"{row['name']} ({row['side_pct']}%)")
        if len(sides) == 1:
            side, names = next(iter(sides.items()))
            why.append(
                f"{len(names)} of {len(models)} models with a stored probability lean {side}: "
                + ", ".join(names)
                + "."
            )
        else:
            chunks = [f"{side}: {', '.join(names)}" for side, names in sides.items()]
            why.append(
                "The stored models split on this moneyline. "
                + " ".join(chunks)
                + "."
            )
        if consensus and any(m["side"] != consensus["side"] for m in models if m["name"] != "Sharp Consensus"):
            dissenters = [m["name"] for m in models if m["name"] != "Sharp Consensus" and m["side"] != consensus["side"]]
            if dissenters:
                why.append(
                    f"Sharp Consensus leans {consensus['side']}, while "
                    + ", ".join(dissenters)
                    + " lean the other way on the stored probabilities."
                )
        if markets.get("book_home_ml") or markets.get("book_away_ml"):
            ml_bits = []
            if markets.get("book_away_ml"):
                ml_bits.append(f"{away} {markets['book_away_ml']}")
            if markets.get("book_home_ml"):
                ml_bits.append(f"{home} {markets['book_home_ml']}")
            why.append(
                "Available sportsbook moneyline on the stored board: "
                + ", ".join(ml_bits)
                + ". Compare that price with the model probability before treating either as a pick."
            )
    kf = ""
    if isinstance(pick, dict):
        kf = str(pick.get("key_factors") or "").strip()
        if kf and kf.lower() not in ("none", "null", "{}"):
            why.append(f"Stored model notes for this game: {kf}")

    takeaway_parts = [
        f"This preview is built from Prediction Lab's stored {away} vs {home} board for {display}."
    ]
    if consensus:
        takeaway_parts.append(
            f"The current Sharp Consensus lean is {consensus['side']} at {consensus['side_pct']}%."
        )
    takeaway_parts.append(
        f"Use the {display_sport} picks page for the live card, then check graded results after the game. "
        "Model probabilities can be wrong; they are not locks."
    )

    body = list(intro)
    if why:
        body.extend(why)
    body.append(" ".join(takeaway_parts))
    excerpt = intro[0]
    if len(intro) > 1:
        excerpt = intro[0] + " " + intro[1]

    fp = _fingerprint({
        "sport": sport,
        "away_record": away_rec,
        "home_record": home_rec,
        "status": status,
        "time": time_bit,
        "models": [(m["name"], m["home_pct"]) for m in models],
        "markets": markets,
    })
    published = None
    modified = None
    if isinstance(existing, dict):
        published = existing.get("date_published") or existing.get("date")
        if existing.get("content_fingerprint") == fp:
            modified = existing.get("date_modified") or published
    if not published:
        published = date_str
    if not modified:
        modified = datetime.now().strftime("%Y-%m-%d") if isinstance(existing, dict) and existing.get("content_fingerprint") != fp else published

    related_links = list(related or [])
    news_items = [{
        "sport": sport,
        "topic": f"{display_sport} picks — live model board",
        "summary_hint": f"Prediction Lab {display_sport} predictions",
        "source": "Prediction Lab",
        "url": picks_path,
    }]
    time_label = _TIME_LABEL.get(sport, "Time")
    sections = empty_sections()
    sections.update({
        "intro": intro,
        "game_info": {
            "date": display,
            "time": time_bit,
            "time_label": time_label,
            "venue": venue_bit,
            "away": away,
            "home": home,
            "away_record": away_rec,
            "home_record": home_rec,
            "status": status,
        },
        "models": models,
        "consensus": consensus,
        "markets": markets,
        "why": why,
        "takeaway": " ".join(takeaway_parts),
    })
    canonical_url = f"{site_domain.rstrip('/')}{canonical_path}"
    return {
        "title": h1,
        "seo_title": seo_title,
        "slug": slug,
        "canonical_path": canonical_path,
        "canonical_url": canonical_url,
        "legacy_slug": legacy,
        "article_url": canonical_path,
        "date": date_str,
        "date_published": str(published)[:10],
        "date_modified": str(modified)[:10],
        "status": "published",
        "sport_tag": sport,
        "excerpt": excerpt,
        "meta_description": meta_description,
        "body": body,
        "sections": sections,
        "news_items": news_items,
        "related": related_links,
        "content_fingerprint": fp,
        "image": OG_IMAGE_PATH,
        "away": away,
        "home": home,
        "hub_label": f"{away} vs {home}",
    }


def attach_related_mlb(posts: list[dict]) -> None:
    attach_related_posts(posts, sport="MLB")


def attach_related_posts(posts: list[dict], sport: str = "") -> None:
    want = str(sport or "").upper()
    grouped: dict[str, list] = {}
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        tag = str(p.get("sport_tag") or "").upper()
        if want and tag != want:
            continue
        if not tag:
            continue
        grouped.setdefault(tag, []).append(p)
    for tag, sport_posts in grouped.items():
        by_date: dict[str, list] = {}
        for p in sport_posts:
            by_date.setdefault(str(p.get("date") or "")[:10], []).append(p)
        display = sport_display_name(tag)
        picks = sport_picks_path(tag)
        results = sport_results_path(tag)
        for p in sport_posts:
            date_key = str(p.get("date") or "")[:10]
            related = []
            for other in by_date.get(date_key, []):
                if other.get("slug") == p.get("slug"):
                    continue
                url = other.get("article_url") or other.get("canonical_path")
                title = other.get("hub_label") or other.get("title")
                if url and title:
                    related.append({"title": title, "url": url})
                if len(related) >= RELATED_MAX:
                    break
            related.append({"title": f"{display} picks today", "url": picks})
            related.append({"title": f"{display} results", "url": results})
            p["related"] = related


def _recent_hub_preview_date(posts: list[dict], sport: str, today: str) -> str:
    """Most recent published preview date for this sport within PREVIEW_KEEP_DAYS."""
    sport_u = str(sport or "").upper()
    try:
        keep_dt = datetime.strptime(str(today or "")[:10], "%Y-%m-%d")
    except Exception:
        return ""
    best = ""
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("sport_tag") or "").upper() != sport_u:
            continue
        if str(p.get("status") or "published").lower() != "published":
            continue
        raw = str(p.get("date") or "")[:10]
        if not raw:
            continue
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d")
        except Exception:
            continue
        if dt > keep_dt or dt < (keep_dt - timedelta(days=PREVIEW_KEEP_DAYS)):
            continue
        if raw > best:
            best = raw
    return best


def hub_items_from_posts(
    posts: list[dict],
    *,
    date_str: str = "",
    limit: int = HUB_MAX_LINKS,
    sport: str = "MLB",
) -> list[dict]:
    items = []
    target = str(date_str or "")[:10]
    sport_u = str(sport or "MLB").upper()
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("sport_tag") or "").upper() != sport_u:
            continue
        if str(p.get("status") or "published").lower() != "published":
            continue
        if target and str(p.get("date") or "")[:10] != target:
            continue
        url = p.get("article_url") or p.get("canonical_path")
        if not url and p.get("slug"):
            url = "/blog/" + str(p.get("slug")).lstrip("/")
        label = p.get("hub_label") or p.get("title")
        if url and label:
            items.append({"title": label, "url": url})
        if len(items) >= limit:
            break
    return items


def hub_bundle_from_posts(
    posts: list[dict],
    sport: str,
    *,
    today: str,
    tomorrow: str = "",
    next_date: str = "",
    limit: int = HUB_MAX_LINKS,
) -> Optional[dict]:
    sport_u = str(sport or "").upper()
    if sport_u not in HUB_SPORTS:
        return None
    display = sport_display_name(sport_u)
    picks_path = sport_picks_path(sport_u)
    today_items = hub_items_from_posts(posts, sport=sport_u, date_str=today, limit=limit)
    tom_items = (
        hub_items_from_posts(posts, sport=sport_u, date_str=tomorrow, limit=limit)
        if tomorrow
        else []
    )
    next_items = []
    if next_date and next_date not in (today, tomorrow):
        next_items = hub_items_from_posts(posts, sport=sport_u, date_str=next_date, limit=limit)
    dates = []
    if today_items:
        dates.append({"label": "Today", "date": today, "path": picks_path, "items": today_items})
    if tom_items:
        dates.append({
            "label": "Tomorrow",
            "date": tomorrow,
            "path": dated_picks_path(sport_u, tomorrow),
            "items": tom_items,
        })
    if next_items:
        dates.append({
            "label": short_display_date(next_date),
            "date": next_date,
            "path": dated_picks_path(sport_u, next_date),
            "items": next_items,
        })
    if not dates:
        recent = _recent_hub_preview_date(posts, sport_u, today)
        if recent:
            recent_items = hub_items_from_posts(
                posts, sport=sport_u, date_str=recent, limit=limit,
            )
            if recent_items:
                dates.append({
                    "label": short_display_date(recent),
                    "date": recent,
                    "path": dated_picks_path(sport_u, recent),
                    "items": recent_items,
                })
    if not dates:
        return None
    first = dates[0]["label"]
    if first == "Today":
        heading = f"Today's {display} previews"
    elif first == "Tomorrow":
        heading = f"Tomorrow's {display} previews"
    else:
        heading = f"{first} {display} previews"
    return {
        "sport": sport_u,
        "display": display,
        "heading": heading,
        "picks_path": picks_path,
        "dates": dates,
    }


def _hub_list_html(items: list[dict]) -> str:
    lis = "".join(
        f'<li><a href="{html_lib.escape(str(item["url"]), quote=True)}">'
        f'{html_lib.escape(str(item["title"]))}</a></li>'
        for item in items
        if item.get("url") and item.get("title")
    )
    return f"<ul>{lis}</ul>" if lis else ""


def render_hub_html(items: list[dict], sport: str = "MLB") -> str:
    bundle = None
    if items and isinstance(items, dict) and items.get("dates"):
        bundle = items
    elif items:
        sport_u = str(sport or "MLB").upper()
        display = sport_display_name(sport_u)
        bundle = {
            "sport": sport_u,
            "display": display,
            "heading": f"Today's {display} previews",
            "picks_path": sport_picks_path(sport_u),
            "dates": [{"label": "Today", "date": "", "path": sport_picks_path(sport_u), "items": items}],
        }
    if not bundle:
        return ""
    display = bundle["display"]
    picks_path = bundle["picks_path"]
    dates = bundle["dates"]
    date_nav = ""
    if len(dates) > 1:
        links = []
        for i, d in enumerate(dates):
            href = html_lib.escape(str(d.get("path") or picks_path), quote=True)
            label = html_lib.escape(str(d.get("label") or ""))
            current = ' aria-current="page"' if i == 0 else ""
            links.append(f'<a href="{href}"{current}>{label}</a>')
        date_nav = '<p class="sport-preview-hub-dates">' + " · ".join(links) + "</p>"
    lists = []
    for i, d in enumerate(dates):
        block = _hub_list_html(d.get("items") or [])
        if not block:
            continue
        if i > 0:
            lists.append(f'<h3>{html_lib.escape(str(d.get("label") or ""))}</h3>')
        lists.append(block)
    if not lists:
        return ""
    heading = html_lib.escape(str(bundle.get("heading") or f"Today's {display} previews"))
    return (
        '<!-- sport-preview-hub -->'
        f'<nav class="mlb-preview-hub sport-preview-hub" aria-label="{html_lib.escape(display)} previews">'
        f"<h2>{heading}</h2>"
        f"{date_nav}"
        f"{''.join(lists)}"
        f'<p><a href="{BLOG_INDEX_PATH}">Prediction Lab blog</a>'
        f' · <a href="{html_lib.escape(picks_path, quote=True)}">{html_lib.escape(display)} picks today</a></p>'
        "</nav>"
    )


_HUB_STYLE = (
    "<style>"
    ".mlb-preview-hub,.sport-preview-hub{max-width:1100px;margin:12px auto 18px;padding:14px 16px;"
    "border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;}"
    ".mlb-preview-hub h2,.sport-preview-hub h2{font-size:1.05rem;margin:0 0 8px;color:#0f172a;}"
    ".mlb-preview-hub h3,.sport-preview-hub h3{font-size:0.95rem;margin:10px 0 6px;color:#334155;}"
    ".mlb-preview-hub ul,.sport-preview-hub ul{margin:0;padding-left:18px;columns:2;gap:12px;}"
    ".mlb-preview-hub li,.sport-preview-hub li{margin:0 0 6px;break-inside:avoid;}"
    ".mlb-preview-hub a,.sport-preview-hub a{color:#00529B;font-weight:700;text-decoration:none;}"
    ".mlb-preview-hub a:hover,.sport-preview-hub a:hover{text-decoration:underline;}"
    ".mlb-preview-hub p,.sport-preview-hub p{margin:10px 0 0;font-size:0.9rem;}"
    ".sport-preview-hub-dates{margin:0 0 8px !important;font-size:0.88rem;}"
    "@media (max-width:700px){.mlb-preview-hub ul,.sport-preview-hub ul{columns:1;}}"
    "</style>"
)


def _insert_hub_after_cards(html: str, block: str) -> Optional[str]:
    """Place the preview hub below the matchup cards (MLB / WNBA / soccer)."""
    slot = "<!-- mlb-preview-hub-slot -->"
    if slot in html:
        return html.replace(slot, block + "\n" + slot, 1)
    if re.search(r"<!-- Internal links to recent dated pages", html):
        return re.sub(
            r"(<!-- Internal links to recent dated pages)",
            block + "\n" + r"\1",
            html,
            count=1,
        )
    if re.search(r'<div class="seo-picks-footer"', html):
        return re.sub(
            r'(<div class="seo-picks-footer")',
            block + "\n" + r"\1",
            html,
            count=1,
        )
    return None


def _insert_hub_block(html: str, block: str, sport: str = "") -> str:
    html = re.sub(
        r"<!-- (?:mlb|sport)-preview-hub -->.*?</nav>",
        "",
        html,
        count=2,
        flags=re.S | re.I,
    )
    html = re.sub(
        r"<style>\s*\.mlb-preview-hub[^{]*\{.*?</style>",
        "",
        html,
        count=2,
        flags=re.S,
    )
    if str(sport or "").upper() in ("MLB", "WNBA", "SOCCER"):
        placed = _insert_hub_after_cards(html, block)
        if placed is not None:
            return placed
    if re.search(r'<div class="premium-upsell-strip">.*?</div>', html, flags=re.S):
        return re.sub(
            r'(<div class="premium-upsell-strip">.*?</div>)',
            r"\1\n" + block,
            html,
            count=1,
            flags=re.S,
        )
    if re.search(r'<div class="section-tabs">[\s\S]*?</div>', html):
        return re.sub(
            r'(<div class="section-tabs">[\s\S]*?</div>)',
            r"\1\n" + block,
            html,
            count=1,
        )
    if re.search(r'<div class="container">', html):
        return re.sub(
            r'(<div class="container">)',
            r"\1\n" + block,
            html,
            count=1,
        )
    if re.search(r"</header>", html, flags=re.I):
        return re.sub(r"</header>", "</header>\n" + block, html, count=1, flags=re.I)
    return html


def inject_sport_preview_hub(html: str, bundle_or_items, sport: str = "MLB") -> str:
    if not html or not isinstance(html, str):
        return html
    if isinstance(bundle_or_items, dict) and bundle_or_items.get("dates"):
        nav = render_hub_html(bundle_or_items, sport=sport)
    else:
        nav = render_hub_html(bundle_or_items or [], sport=sport)
    if not nav:
        return html
    return _insert_hub_block(html, _HUB_STYLE + nav, sport=sport)


def inject_mlb_preview_hub(html: str, items: list[dict]) -> str:
    return inject_sport_preview_hub(html, items, sport="MLB")


def keep_preview_post(slug: str, sport_tag: str, slug_date: Optional[str], keep_date: str) -> bool:
    sport_u = str(sport_tag or "").upper()
    slug_l = str(slug or "").lower()
    is_preview = is_game_preview_slug(slug) or any(
        slug_l.startswith(s.lower() + "-") for s in HUB_SPORTS
    )
    if sport_u not in HUB_SPORTS and not is_preview:
        return False
    if not slug_date:
        return False
    try:
        keep_dt = datetime.strptime(keep_date[:10], "%Y-%m-%d")
        post_dt = datetime.strptime(slug_date[:10], "%Y-%m-%d")
    except Exception:
        return slug_date == keep_date
    return post_dt >= (keep_dt - timedelta(days=PREVIEW_KEEP_DAYS))


def keep_mlb_post(slug: str, sport_tag: str, slug_date: Optional[str], keep_date: str) -> bool:
    return keep_preview_post(slug, sport_tag, slug_date, keep_date)


def sitemap_article_entries(posts: list[dict], site_domain: str = SITE_DOMAIN) -> list[tuple]:
    """(loc, lastmod, changefreq, priority) for published articles only."""
    out = []
    seen = set()
    domain = site_domain.rstrip("/")
    for p in posts:
        if not isinstance(p, dict):
            continue
        if str(p.get("status") or "published").lower() != "published":
            continue
        path = p.get("canonical_path") or p.get("article_url")
        if not path:
            slug = p.get("slug")
            if not slug:
                continue
            sport = str(p.get("sport_tag") or "").upper()
            if sport in HUB_SPORTS:
                path = f"/blog/{sport.lower()}/{slug}" if "/" not in str(slug) else f"/blog/{slug}"
            else:
                path = f"/blog/{slug}"
        if not str(path).startswith("/"):
            continue
        loc = domain + path
        if loc in seen:
            continue
        seen.add(loc)
        lastmod = str(p.get("date_modified") or p.get("date") or "")[:10]
        out.append((loc, lastmod, "daily", "0.65"))
    return out


def article_json_ld(post: dict, site_domain: str = SITE_DOMAIN) -> list[dict]:
    domain = site_domain.rstrip("/")
    canonical = post.get("canonical_url") or (domain + (post.get("canonical_path") or post.get("article_url") or ""))
    image = post.get("image") or OG_IMAGE_PATH
    if not isinstance(image, str) or not image:
        image = OG_IMAGE_PATH
    if image.startswith("/"):
        image = domain + image
    published = _iso_datetime(post.get("date_published") or post.get("date") or "")
    modified = _iso_datetime(post.get("date_modified") or post.get("date_published") or post.get("date") or "")
    headline = post.get("seo_title") or post.get("title") or ""
    description = post.get("meta_description") or post.get("excerpt") or ""
    article = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "description": description,
        "datePublished": published,
        "dateModified": modified,
        "author": {"@type": "Organization", "name": AUTHOR_NAME, "url": domain},
        "publisher": {
            "@type": "Organization",
            "name": PUBLISHER_NAME,
            "url": domain,
            "logo": {"@type": "ImageObject", "url": domain + OG_IMAGE_PATH},
        },
        "image": image,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
    }
    sport = str(post.get("sport_tag") or "Blog").upper()
    sport_path = SPORT_PICKS_PATHS.get(sport)
    crumb_items = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": domain + "/"},
        {"@type": "ListItem", "position": 2, "name": "Blog", "item": domain + BLOG_INDEX_PATH},
    ]
    if sport_path:
        crumb_items.append({
            "@type": "ListItem",
            "position": 3,
            "name": sport_display_name(sport),
            "item": domain + sport_path,
        })
        crumb_items.append({"@type": "ListItem", "position": 4, "name": headline, "item": canonical})
    else:
        crumb_items.append({"@type": "ListItem", "position": 3, "name": headline, "item": canonical})
    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": crumb_items,
    }
    return [article, crumbs]


def is_indexable_post(post: Optional[dict]) -> bool:
    if not isinstance(post, dict):
        return False
    status = str(post.get("status") or "published").lower()
    if status in ("draft", "unpublished", "deleted"):
        return False
    return True
