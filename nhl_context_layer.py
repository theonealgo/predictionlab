"""
NHL Context Layer
=================
Additive signal enrichment for NHL moneyline and totals predictions.

Provides four independent adjustment modules:
  1. Rest & fatigue   — back-to-back, 3-games-in-4-nights
  2. Goalie quality   — save% differential (from game_goalies table or GAA proxy)
  3. Special teams    — goals-for deviation as PP/PK proxy
  4. Playoff context  — series score, elimination game pressure

Contract for get_nhl_context_adjustments():
  {
    'moneyline_boost': float,   # delta on home win prob [0,1]; capped ±0.08
    'total_adj':       float,   # goals delta on xgb_total; capped ±0.50
    'diagnostics': {
        'rest':          { home_b2b, away_b2b, home_3in4, away_3in4, ml_boost, total_adj },
        'goalie':        { home_save_pct, away_save_pct, home_gaa, away_gaa,
                           ml_boost, total_adj, source },
        'special_teams': { home_gpg_dev, away_gpg_dev, total_adj },
        'playoff':       { is_playoff, series_home, series_away,
                           is_elimination, team_facing_elimination, ml_boost },
    }
  }

Rules:
  - Never fabricates data; returns zero adjustment when data unavailable.
  - All DB queries are read-only.
  - Full result cached 15 minutes per (home, away, date) triple.
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────────
_CTX_CACHE: dict = {}
_CTX_TTL = 900  # 15 minutes


def _cache_key(home: str, away: str, game_date: str) -> str:
    return f"nhl_ctx|{home}|{away}|{game_date}"


def _cached_get(key: str):
    entry = _CTX_CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CTX_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data: dict) -> None:
    _CTX_CACHE[key] = {"ts": time.time(), "data": data}
    if len(_CTX_CACHE) > 600:
        oldest = sorted(_CTX_CACHE, key=lambda k: _CTX_CACHE[k]["ts"])
        for k in oldest[:60]:
            _CTX_CACHE.pop(k, None)


# ── DB helper ────────────────────────────────────────────────────────────────

def _open_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


# ── Module constants ─────────────────────────────────────────────────────────

# Rest/fatigue magnitudes
# B2B total impact is incremental on top of the existing _B2B_PENALTY (0.15 goals).
# Net desired = -0.30 goals, existing = -0.15, so incremental = -0.15 here.
_B2B_TOTAL_INCR  = -0.15   # incremental goals on top of existing penalty
_B2B_ML_IMPACT   = -0.035  # home win prob penalty when home team is on B2B
_3IN4_TOTAL_ADJ  = -0.12   # goals adjustment for 3-in-4 fatigue
_3IN4_ML_IMPACT  = -0.018  # ML penalty for 3-in-4

# Goalie quality
_LEAGUE_AVG_SAVE_PCT = 0.910
_SHOTS_PER_GAME      = 29.5
_GOALIE_ML_SCALE     = 1.5   # save_pct_diff × scale → moneyline boost
_GOALIE_TOTAL_SCALE  = 0.50  # save_pct deviation × shots_per_game × scale → total adj
# At scale 0.50: ±1% save_pct deviation per team → ±0.15 goals on total

# Special teams
_LEAGUE_GPG    = 3.09        # NHL avg goals-for per team per game (≈ 6.18 / 2)
_ST_TOTAL_SCALE = 0.15       # fraction of GPG deviation applied to total

# Playoff context
_PLAYOFF_SERIES_ML     =  0.015  # each series-win lead adds small boost
_PLAYOFF_ELIM_LEAD_ML  =  0.030  # home team closes out series (elimination game)
_PLAYOFF_ELIM_TRAIL_ML = -0.040  # home team faces elimination (historically poor)

# Hard caps
_MAX_ML_BOOST  = 0.08
_MAX_TOTAL_ADJ = 0.50


# ── 1. Rest & fatigue ────────────────────────────────────────────────────────

def _rest_context(home: str, away: str, game_date: str, conn: sqlite3.Connection) -> dict:
    """Detect back-to-back and 3-in-4-nights from the games table."""
    result = {
        "home_b2b": False, "away_b2b": False,
        "home_3in4": False, "away_3in4": False,
        "ml_boost": 0.0, "total_adj": 0.0,
    }
    try:
        gd = datetime.strptime(game_date[:10], "%Y-%m-%d")
    except Exception:
        return result

    yesterday     = (gd - timedelta(days=1)).strftime("%Y-%m-%d")
    three_ago     = (gd - timedelta(days=3)).strftime("%Y-%m-%d")

    for side, team in (("home", home), ("away", away)):
        try:
            # Back-to-back: played yesterday
            row = conn.execute(
                """SELECT COUNT(*) AS cnt FROM games
                   WHERE sport='NHL' AND date(game_date)=date(?)
                   AND (home_team_id=? OR away_team_id=?)""",
                (yesterday, team, team),
            ).fetchone()
            b2b = bool(row and row["cnt"] > 0)
            result[f"{side}_b2b"] = b2b

            # 3-in-4: 2+ completed games in the 3 days before today
            if not b2b:
                row2 = conn.execute(
                    """SELECT COUNT(*) AS cnt FROM games
                       WHERE sport='NHL'
                       AND date(game_date) BETWEEN date(?) AND date(?)
                       AND (home_team_id=? OR away_team_id=?)""",
                    (three_ago, yesterday, team, team),
                ).fetchone()
                result[f"{side}_3in4"] = bool(row2 and row2["cnt"] >= 2)
        except Exception:
            pass

    # Total adjustments (both teams being tired → fewer goals)
    b2b_count  = result["home_b2b"]  + result["away_b2b"]
    in4_count  = result["home_3in4"] + result["away_3in4"]
    result["total_adj"] = b2b_count * _B2B_TOTAL_INCR + in4_count * _3IN4_TOTAL_ADJ

    # ML boost: positive = home advantage, negative = home disadvantaged
    # home B2B hurts home → negative boost; away B2B helps home → positive boost
    result["ml_boost"] = (
        (result["away_b2b"]  - result["home_b2b"])  * abs(_B2B_ML_IMPACT)
        + (result["away_3in4"] - result["home_3in4"]) * abs(_3IN4_ML_IMPACT)
    )
    return result


# ── 2. Goalie quality ────────────────────────────────────────────────────────

def _goalie_context(home: str, away: str, game_date: str, conn: sqlite3.Connection) -> dict:
    """
    Compute goalie quality delta.

    Priority:
      1. game_goalies table — actual per-game save% (when populated).
      2. GAA proxy — goals-against rate from recent games → approximate save%.

    Returns zero adjustments when neither source has sufficient data.
    """
    result = {
        "home_save_pct": None, "away_save_pct": None,
        "home_gaa": None,      "away_gaa": None,
        "ml_boost": 0.0,       "total_adj": 0.0,
        "source": "none",
    }
    try:
        # ── Priority 1: game_goalies table ─────────────────────────────────
        def _from_goalies_table(team: str, side: str):
            col_sp  = f"{side}_goalie_save_pct"
            col_gaa = f"{side}_goalie_gaa"
            col_tm  = f"{side}_team_id"
            rows = conn.execute(
                f"""SELECT gg.{col_sp} AS sp, gg.{col_gaa} AS gaa
                    FROM game_goalies gg
                    JOIN games g ON g.id = gg.game_id
                    WHERE g.sport='NHL'
                    AND g.{col_tm}=?
                    AND date(g.game_date) < date(?)
                    AND gg.{col_sp} IS NOT NULL
                    ORDER BY g.game_date DESC
                    LIMIT 10""",
                (team, game_date),
            ).fetchall()
            if not rows:
                return None, None
            sps  = [float(r["sp"])  for r in rows if r["sp"]  is not None]
            gaas = [float(r["gaa"]) for r in rows if r["gaa"] is not None]
            return (sum(sps) / len(sps) if sps else None,
                    sum(gaas) / len(gaas) if gaas else None)

        h_sp, h_gaa = _from_goalies_table(home, "home")
        a_sp, a_gaa = _from_goalies_table(away, "away")

        if h_sp is not None or a_sp is not None:
            result.update({"home_save_pct": h_sp, "away_save_pct": a_sp,
                           "home_gaa": h_gaa, "away_gaa": a_gaa,
                           "source": "game_goalies"})
        else:
            # ── Priority 2: GAA proxy from goal history ─────────────────────
            def _gaa_proxy(team: str):
                rows = conn.execute(
                    """SELECT home_team_id, home_score, away_score
                       FROM games
                       WHERE sport='NHL'
                       AND date(game_date) < date(?)
                       AND (home_team_id=? OR away_team_id=?)
                       AND home_score IS NOT NULL
                       ORDER BY game_date DESC
                       LIMIT 20""",
                    (game_date, team, team),
                ).fetchall()
                if len(rows) < 5:
                    return None
                ga = sum(
                    float(r["away_score"]) if r["home_team_id"] == team
                    else float(r["home_score"])
                    for r in rows
                ) / len(rows)
                return ga

            h_ga = _gaa_proxy(home)
            a_ga = _gaa_proxy(away)

            if h_ga is not None or a_ga is not None:
                result["source"] = "gaa_proxy"
                # save_pct ≈ 1 - GAA / shots_per_game; clamped to plausible range
                if h_ga is not None:
                    result["home_save_pct"] = max(0.860, min(0.945,
                        1.0 - h_ga / _SHOTS_PER_GAME))
                    result["home_gaa"] = round(h_ga, 3)
                if a_ga is not None:
                    result["away_save_pct"] = max(0.860, min(0.945,
                        1.0 - a_ga / _SHOTS_PER_GAME))
                    result["away_gaa"] = round(a_ga, 3)

        # ── Compute adjustments ────────────────────────────────────────────
        h_sp_eff = result["home_save_pct"] or _LEAGUE_AVG_SAVE_PCT
        a_sp_eff = result["away_save_pct"] or _LEAGUE_AVG_SAVE_PCT
        sp_diff  = h_sp_eff - a_sp_eff  # positive = home goalie better

        # ML: home goalie advantage → more home wins
        result["ml_boost"] = round(sp_diff * _GOALIE_ML_SCALE, 4)

        # Total: combined deviation from league avg → fewer or more goals expected
        h_dev = h_sp_eff - _LEAGUE_AVG_SAVE_PCT
        a_dev = a_sp_eff - _LEAGUE_AVG_SAVE_PCT
        # Better-than-avg goalies → fewer goals allowed → lower total
        result["total_adj"] = round(
            -((h_dev + a_dev) * _SHOTS_PER_GAME * _GOALIE_TOTAL_SCALE), 3
        )

    except Exception as exc:
        logger.debug("[nhl_ctx] goalie_context: %s", exc)

    return result


# ── 3. Special teams proxy ────────────────────────────────────────────────────

def _special_teams_context(home: str, away: str, game_date: str, conn: sqlite3.Connection) -> dict:
    """
    Approximate PP/PK quality from recent goals-for deviation vs league average.
    High-scoring teams tend to have better power plays; this is a proxy,
    not a direct measurement. Contribution is deliberately small.
    """
    result = {"home_gpg_dev": 0.0, "away_gpg_dev": 0.0, "total_adj": 0.0}
    try:
        def _gpg(team: str) -> float:
            rows = conn.execute(
                """SELECT home_team_id, home_score, away_score
                   FROM games
                   WHERE sport='NHL'
                   AND date(game_date) < date(?)
                   AND (home_team_id=? OR away_team_id=?)
                   AND home_score IS NOT NULL
                   ORDER BY game_date DESC
                   LIMIT 20""",
                (game_date, team, team),
            ).fetchall()
            if len(rows) < 5:
                return _LEAGUE_GPG
            gf = sum(
                float(r["home_score"]) if r["home_team_id"] == team
                else float(r["away_score"])
                for r in rows
            ) / len(rows)
            return gf

        h_gpg = _gpg(home)
        a_gpg = _gpg(away)
        h_dev = h_gpg - _LEAGUE_GPG
        a_dev = a_gpg - _LEAGUE_GPG
        result["home_gpg_dev"] = round(h_dev, 3)
        result["away_gpg_dev"] = round(a_dev, 3)
        # Combined offensive tendency shifts expected total
        result["total_adj"] = round((h_dev + a_dev) * _ST_TOTAL_SCALE, 3)
    except Exception as exc:
        logger.debug("[nhl_ctx] special_teams_context: %s", exc)

    return result


# ── 4. Playoff context ────────────────────────────────────────────────────────

_PLAYOFF_MONTHS = {4, 5, 6}  # April – June


def _playoff_context(home: str, away: str, game_date: str, conn: sqlite3.Connection) -> dict:
    """
    Detect playoff series from repeated H2H matchups and adjust accordingly.

    Series detection: 2+ completed games between the same two teams in the
    60 days before this game.  Series score is counted from those games.
    Adjustments are bounded at ±0.05 to prevent playoff context from
    overriding the base rating signal.
    """
    result = {
        "is_playoff": False,
        "series_home": 0, "series_away": 0,
        "is_elimination": False,
        "team_facing_elimination": None,
        "ml_boost": 0.0,
    }
    try:
        gd = datetime.strptime(game_date[:10], "%Y-%m-%d")
    except Exception:
        return result

    if gd.month not in _PLAYOFF_MONTHS:
        return result

    sixty_ago = (gd - timedelta(days=60)).strftime("%Y-%m-%d")
    try:
        games = conn.execute(
            """SELECT home_team_id, away_team_id, home_score, away_score
               FROM games
               WHERE sport='NHL'
               AND date(game_date) BETWEEN date(?) AND date(?)
               AND (
                   (home_team_id=? AND away_team_id=?)
                   OR (home_team_id=? AND away_team_id=?)
               )
               AND home_score IS NOT NULL
               ORDER BY game_date""",
            (sixty_ago, game_date, home, away, away, home),
        ).fetchall()

        if len(games) < 2:
            return result  # Not a confirmed series

        result["is_playoff"] = True

        home_wins = away_wins = 0
        for g in games:
            hs, as_ = float(g["home_score"]), float(g["away_score"])
            if hs == as_:
                continue  # Shouldn't happen in NHL (OT resolves all games)
            home_won = hs > as_
            if g["home_team_id"] == home:
                if home_won:
                    home_wins += 1
                else:
                    away_wins += 1
            else:
                if home_won:
                    away_wins += 1
                else:
                    home_wins += 1

        result["series_home"] = home_wins
        result["series_away"] = away_wins

        # Series-lead momentum: small edge for the team in the lead
        lead = home_wins - away_wins
        result["ml_boost"] += lead * _PLAYOFF_SERIES_ML

        # Elimination game: one team needs one more win for the series
        if home_wins == 3 or away_wins == 3:
            result["is_elimination"] = True
            if home_wins == 3:
                # Home team closes out: historically slight extra home edge
                result["team_facing_elimination"] = away
                result["ml_boost"] += _PLAYOFF_ELIM_LEAD_ML
            else:
                # Home team must win or go home: historically underperforms (~38%)
                result["team_facing_elimination"] = home
                result["ml_boost"] += _PLAYOFF_ELIM_TRAIL_ML

        result["ml_boost"] = max(-0.05, min(0.05, result["ml_boost"]))

    except Exception as exc:
        logger.debug("[nhl_ctx] playoff_context: %s", exc)

    return result


# ── 5. Goalie boxscore fetch (optional, for populating game_goalies table) ────

def fetch_and_store_goalie_boxscores(db_path: str, lookback_days: int = 7) -> int:
    """
    Fetch goalie stats from the NHL boxscore API for recent completed games
    and store them in the game_goalies table.

    Call this from update_nhl_scores() to keep goalie data current.
    Returns the number of new rows inserted.

    NOTE: Requires outbound network access to api-web.nhle.com.
    Fails silently when the API is unavailable.
    """
    try:
        import requests
        from datetime import datetime, timedelta
    except ImportError:
        return 0

    inserted = 0
    try:
        conn = _open_conn(db_path)
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        # Find recent NHL games that don't have goalie data yet
        games = conn.execute(
            """SELECT g.id, g.game_id, g.game_date, g.home_team_id, g.away_team_id
               FROM games g
               LEFT JOIN game_goalies gg ON gg.game_id = g.id
               WHERE g.sport='NHL'
               AND g.home_score IS NOT NULL
               AND date(g.game_date) >= date(?)
               AND gg.id IS NULL
               ORDER BY g.game_date DESC""",
            (cutoff,),
        ).fetchall()

        for row in games:
            raw_id = str(row["game_id"]).replace("NHL_", "")
            # NHL API boxscore uses numeric game IDs only
            if not raw_id.isdigit():
                continue
            url = f"https://api-web.nhle.com/v1/gamecenter/{raw_id}/boxscore"
            try:
                resp = requests.get(url, timeout=4)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                home_gs = data.get("homeTeam", {}).get("goalies", [])
                away_gs = data.get("awayTeam", {}).get("goalies", [])

                def _goalie_stats(goalies):
                    # Pick the starter (most saves/shots)
                    best = max(goalies, key=lambda g: g.get("shotsAgainst", 0),
                               default=None)
                    if not best:
                        return None, None, None
                    name   = (best.get("firstName", {}).get("default", "") + " "
                               + best.get("lastName", {}).get("default", "")).strip()
                    sa     = best.get("shotsAgainst", 0)
                    sv     = best.get("saves", 0)
                    sp     = sv / sa if sa > 0 else None
                    goals  = sa - sv
                    toi_s  = best.get("toi", "00:00")
                    try:
                        m, s = map(int, toi_s.split(":"))
                        toi_h = (m * 60 + s) / 3600.0
                    except Exception:
                        toi_h = 1.0
                    gaa    = (goals / toi_h * 60 / 60) if toi_h > 0 else None
                    return name, round(sp, 4) if sp else None, round(gaa, 3) if gaa else None

                h_name, h_sp, h_gaa = _goalie_stats(home_gs)
                a_name, a_sp, a_gaa = _goalie_stats(away_gs)

                conn.execute(
                    """INSERT OR IGNORE INTO game_goalies
                       (game_id, home_goalie, away_goalie,
                        home_goalie_save_pct, away_goalie_save_pct,
                        home_goalie_gaa, away_goalie_gaa)
                       VALUES (?,?,?,?,?,?,?)""",
                    (row["id"], h_name, a_name, h_sp, a_sp, h_gaa, a_gaa),
                )
                conn.commit()
                inserted += 1
            except Exception:
                continue

        conn.close()
    except Exception as exc:
        logger.debug("[nhl_ctx] fetch_and_store_goalie_boxscores: %s", exc)

    if inserted:
        logger.info("[nhl_ctx] Stored goalie stats for %d NHL games", inserted)
    return inserted


# ── 6. Aggregator ─────────────────────────────────────────────────────────────

def get_nhl_context_adjustments(
    home_team: str,
    away_team: str,
    game_date: str,
    db_path: str = "sports_predictions_original.db",
) -> dict:
    """
    Compute all NHL context adjustments for a single game.

    Returns a dict with 'moneyline_boost', 'total_adj', and 'diagnostics'.
    Always returns a valid dict — never raises.
    """
    _zero = {"moneyline_boost": 0.0, "total_adj": 0.0, "diagnostics": {}}
    if not home_team or not away_team or not game_date:
        return _zero

    key = _cache_key(home_team, away_team, game_date)
    cached = _cached_get(key)
    if cached is not None:
        return cached

    try:
        conn = _open_conn(db_path)
        try:
            rest    = _rest_context(home_team, away_team, game_date, conn)
            goalie  = _goalie_context(home_team, away_team, game_date, conn)
            st      = _special_teams_context(home_team, away_team, game_date, conn)
            playoff = _playoff_context(home_team, away_team, game_date, conn)
        finally:
            conn.close()

        ml_boost = (
            rest["ml_boost"]
            + goalie["ml_boost"]
            + playoff["ml_boost"]
            # special teams contributes only to totals
        )
        total_adj = (
            rest["total_adj"]
            + goalie["total_adj"]
            + st["total_adj"]
        )

        ml_boost  = max(-_MAX_ML_BOOST,  min(_MAX_ML_BOOST,  ml_boost))
        total_adj = max(-_MAX_TOTAL_ADJ, min(_MAX_TOTAL_ADJ, total_adj))

        logger.debug(
            "[nhl_ctx] %s@%s %s | ml=%+.4f (rest=%+.4f goalie=%+.4f playoff=%+.4f) "
            "| total_adj=%+.3f (rest=%+.3f goalie=%+.3f st=%+.3f)",
            away_team, home_team, game_date,
            ml_boost, rest["ml_boost"], goalie["ml_boost"], playoff["ml_boost"],
            total_adj, rest["total_adj"], goalie["total_adj"], st["total_adj"],
        )

        result = {
            "moneyline_boost": round(ml_boost,  4),
            "total_adj":       round(total_adj, 3),
            "diagnostics": {
                "rest":          rest,
                "goalie":        goalie,
                "special_teams": st,
                "playoff":       playoff,
            },
        }
        _cache_set(key, result)
        return result

    except Exception as exc:
        logger.warning("[nhl_ctx] get_nhl_context_adjustments error: %s", exc)
        return _zero
