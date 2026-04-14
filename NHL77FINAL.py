#!/usr/bin/env python3
"""
underdogs.bet - Multi-Sport Prediction Platform
==================================================
Complete platform with Dashboard, Predictions, and Results pages for all sports.
5-Model System: Glicko-2, TrueSkill, Elo, XGBoost, Ensemble
"""

from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, Response
import json
from flask_cors import CORS
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import nfl_data_py as nfl
from nhlschedules import get_nhl_2025_schedule
import requests
from nba_sportsdata_api import NBASportsDataAPI
from nhl_api import NHLAPI
from value_predictor import ValuePredictor
from ats_system import ATSSystem
from soccer_models import build_soccer_model_bundle

# V2 PREDICTION SYSTEM - Upgraded architecture
import os as _os_v2
_V2_BASE = _os_v2.path.dirname(_os_v2.path.abspath(__file__))
try:
    from prediction_system_v2 import AdvancedPredictor
    V2_PREDICTORS = {}
    # Load trained models for supported sports
    for sport in ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAF', 'NCAAB']:
        try:
            _model_path = _os_v2.path.join(_V2_BASE, 'models', f'{sport}_v2')
            V2_PREDICTORS[sport] = AdvancedPredictor.load(sport, _model_path)
            print(f"✅ Loaded {sport} v2 predictor (Glicko-2 + Ensemble + Calibration)")
        except Exception as e:
            print(f"⚠️ {sport} v2 model not found at {_model_path}: {e}")
    HAS_V2_SYSTEM = len(V2_PREDICTORS) > 0
except ImportError as e:
    print(f"⚠️ V2 prediction system not available: {e}")
    V2_PREDICTORS = {}
    HAS_V2_SYSTEM = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import time as _time
import copy as _copy

# ── Module-level HTTP request cache (15-min TTL) ──────────────────────────────
_API_CACHE: dict = {}
_API_TTL = 900  # seconds
_PREDICTIONS_CACHE: dict = {}
_PREDICTIONS_TTL_BY_SPORT = {
    'NHL': 180,
    'NBA': 180,
    'NCAAB': 180,
    'NCAAW': 180,
    'MLB': 240,
    'NFL': 300,
    'NCAAF': 300,
    'WNBA': 240,
    'SOCCER': 240,
}
_SPORT_RESULTS_CACHE: dict = {}
_SPORT_RESULTS_TTL_BY_SPORT = {
    'NHL': 300,
    'NBA': 240,
    'NCAAB': 240,
    'NCAAW': 240,
    'MLB': 300,
    'NCAAF': 300,
    'NFL': 300,
    'WNBA': 300,
    'SOCCER': 300,
}
_SOCCER_MODEL_CACHE: dict = {}
_SOCCER_MODEL_TTL = 900
_LANDING_BANNER_CACHE = {'ts': 0, 'messages': []}
_LANDING_BANNER_TTL = 900
_MANUAL_BANNER_ITEMS = [
    {'label': '⭐ Grinder2', 'pct': '83.3%', 'record': '40-8'},
    {'label': '🎲 NBA O/U (XSharp)', 'pct': '82.6%', 'record': '247/299'},
    {'label': 'MLB 🎯 Moneyline (Consensus)', 'pct': '60.0%', 'record': '60-40'},
    {'label': 'NHL 📊 Edge', 'pct': '56.5%', 'record': '113-87'},
]


def _cached_get(url: str, timeout: int = 10):
    """requests.get with 15-minute in-process cache."""
    now = _time.time()
    entry = _API_CACHE.get(url)
    if entry and (now - entry['ts']) < _API_TTL:
        return entry['data']
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _API_CACHE[url] = {'data': data, 'ts': now}
        return data
    except Exception as exc:
        raise exc

CORE_API_SPORT_PATHS = {
    'NBA': ('basketball', 'nba'),
    'NHL': ('hockey', 'nhl'),
    'NFL': ('football', 'nfl'),
    'MLB': ('baseball', 'mlb'),
    'WNBA': ('basketball', 'wnba'),
    'NCAAB': ('basketball', 'mens-college-basketball'),
    'NCAAF': ('football', 'college-football'),
    'NCAAW': ('basketball', 'womens-college-basketball'),
    'SOCCER': ('soccer', 'all'),
}


def _normalize_team_key(team_name: str) -> str:
    """Normalize team names for resilient cross-source matching."""
    if not team_name:
        return ''
    import re
    import unicodedata
    txt = unicodedata.normalize('NFKD', str(team_name))
    txt = txt.encode('ascii', 'ignore').decode('ascii')
    txt = txt.lower().replace('&', 'and')
    txt = re.sub(r'[^a-z0-9]+', '', txt)
    alias_map = {
        'utahhockeyclub': 'utahmammoth',
    }
    txt = alias_map.get(txt, txt)
    return txt

_TEAM_ALIAS_BY_SPORT = {
    'NBA': {
        'atl': 'atlantahawks',
        'bos': 'bostonceltics',
        'bkn': 'brooklynnets',
        'brk': 'brooklynnets',
        'cha': 'charlottehornets',
        'cho': 'charlottehornets',
        'chi': 'chicagobulls',
        'cle': 'clevelandcavaliers',
        'dal': 'dallasmavericks',
        'den': 'denvernuggets',
        'det': 'detroitpistons',
        'gsw': 'goldenstatewarriors',
        'gs': 'goldenstatewarriors',
        'hou': 'houstonrockets',
        'ind': 'indianapacers',
        'lac': 'losangelesclippers',
        'lal': 'losangeleslakers',
        'mem': 'memphisgrizzlies',
        'mia': 'miamiheat',
        'mil': 'milwaukeebucks',
        'min': 'minnesotatimberwolves',
        'nop': 'neworleanspelicans',
        'no': 'neworleanspelicans',
        'nyk': 'newyorkknicks',
        'okc': 'oklahomacitythunder',
        'orl': 'orlandomagic',
        'phi': 'philadelphia76ers',
        'phl': 'philadelphia76ers',
        'pho': 'phoenixsuns',
        'phx': 'phoenixsuns',
        'por': 'portlandtrailblazers',
        'sac': 'sacramentokings',
        'sas': 'sanantoniospurs',
        'sa': 'sanantoniospurs',
        'tor': 'torontoraptors',
        'uta': 'utahjazz',
        'was': 'washingtonwizards',
        'wsh': 'washingtonwizards',
    }
}


def _normalize_team_key_for_sport(sport: str, team_name: str) -> str:
    key = _normalize_team_key(team_name)
    if not key or not sport:
        return key
    alias_map = _TEAM_ALIAS_BY_SPORT.get(sport, {})
    return alias_map.get(key, key)


def _resolve_espn_event_id_by_matchup(sport: str, game_date: str, home_team: str, away_team: str):
    """
    Resolve ESPN event ID by matching date + teams from scoreboard API.
    Needed for sports where local game IDs are not ESPN event IDs (notably NHL).
    """
    sport_path = CORE_API_SPORT_PATHS.get(sport)
    if not sport_path:
        return None

    parsed = parse_date(str(game_date)) if game_date else None
    if not parsed:
        return None

    home_key = _normalize_team_key(home_team)
    away_key = _normalize_team_key(away_team)
    if not home_key or not away_key:
        return None

    sport_slug, league_slug = sport_path
    day_offsets = [0, -1, 1]

    for day_offset in day_offsets:
        check_dt = parsed + timedelta(days=day_offset)
        date_str = check_dt.strftime('%Y%m%d')
        scoreboard_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/"
            f"{league_slug}/scoreboard?dates={date_str}"
        )
        if sport == 'NCAAB':
            scoreboard_url += '&groups=50&limit=357'

        try:
            data = _cached_get(scoreboard_url, timeout=8)
            events = data.get('events', []) if isinstance(data, dict) else []
        except Exception:
            continue

        for event in events:
            competition = event.get('competitions', [{}])[0]
            competitors = competition.get('competitors', [])
            if len(competitors) != 2:
                continue

            home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
            away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
            if not home or not away:
                continue

            def _team_keys(competitor):
                team = competitor.get('team', {})
                vals = {
                    team.get('displayName'),
                    team.get('shortDisplayName'),
                    team.get('name'),
                    team.get('nickname'),
                    team.get('location'),
                    team.get('abbreviation'),
                }
                keys = {_normalize_team_key(v) for v in vals if v}
                return {k for k in keys if k}

            home_keys = _team_keys(home)
            away_keys = _team_keys(away)
            if home_key in home_keys and away_key in away_keys:
                event_id = str(event.get('id') or '').strip()
                if event_id:
                    return event_id

    return None


def _american_units(odds: float):
    if odds is None:
        return None
    try:
        odds = float(odds)
    except Exception:
        return None
    return (odds / 100.0) if odds > 0 else (100.0 / abs(odds))


_ODDS_VIG = 0.04


def _prob_to_american(p):
    """Convert a win probability (0-1) to American odds."""
    if p is None or p <= 0 or p >= 1:
        return None
    if p >= 0.5:
        return -round((p / (1 - p)) * 100)
    return round(((1 - p) / p) * 100)


def _compute_odds_from_prob(home_prob_pct, vig=_ODDS_VIG):
    """Compute American moneyline odds from a home win probability percentage.

    home_prob_pct: e.g. 65.0 meaning 65% home win chance.
    Returns dict with moneyline_home, moneyline_away.
    """
    if home_prob_pct is None:
        return None
    hp = home_prob_pct / 100.0
    ap = 1.0 - hp
    if hp <= 0 or hp >= 1:
        return None
    # Apply vig (same logic as engine buildOdds)
    total = hp + ap
    ph = hp / total
    pa = ap / total
    vig_factor = 1 + vig
    ph_vig = min(ph * vig_factor, 0.99)
    pa_vig = min(pa * vig_factor, 0.99)
    return {
        'moneyline_home': _prob_to_american(ph_vig),
        'moneyline_away': _prob_to_american(pa_vig),
    }


def _fetch_engine_odds(sport, game_id, game_date=None, home_team=None, away_team=None):
    if not ODDS_ENGINE_URL:
        return None, "odds engine URL not configured"
    params = {
        'gameId': game_id,
        'sport': sport,
        'home': home_team,
        'away': away_team,
        'gameDate': game_date,
    }
    try:
        resp = requests.get(f"{ODDS_ENGINE_URL}/odds", params=params, timeout=6)
        if resp.status_code == 404 and home_team and away_team:
            params_fallback = {
                'sport': sport,
                'home': home_team,
                'away': away_team,
            }
            resp = requests.get(f"{ODDS_ENGINE_URL}/odds", params=params_fallback, timeout=6)
        if resp.status_code != 200:
            return None, f"odds engine returned {resp.status_code}"
        data = resp.json() if resp.content else {}
        odds = data.get('odds') if isinstance(data, dict) else None
        if not odds:
            return None, "odds engine returned no odds"
        return odds, None
    except Exception:
        return None, "odds engine unavailable"


def _upsert_engine_odds(
    conn,
    sport,
    game_id,
    game_date,
    home_team,
    away_team,
    odds,
):
    now_ts = datetime.now().isoformat()
    try:
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT id FROM engine_odds WHERE sport=? AND game_id=?",
            (sport, game_id)
        ).fetchone()
        if existing:
            cur.execute(
                """UPDATE engine_odds
                   SET home_moneyline=?, away_moneyline=?, spread=?, total=?,
                       spread_price_home=?, spread_price_away=?, total_over_price=?, total_under_price=?,
                       source=?, created_at=?
                   WHERE id=?""",
                (
                    odds.get('moneyline_home'),
                    odds.get('moneyline_away'),
                    odds.get('spread'),
                    odds.get('total'),
                    odds.get('spread_price_home'),
                    odds.get('spread_price_away'),
                    odds.get('total_over_price'),
                    odds.get('total_under_price'),
                    odds.get('source', 'engine'),
                    now_ts,
                    existing['id'],
                )
            )
        else:
            cur.execute(
                """INSERT INTO engine_odds
                   (sport, game_id, game_date, home_team, away_team,
                    home_moneyline, away_moneyline, spread, total,
                    spread_price_home, spread_price_away, total_over_price, total_under_price,
                    source, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sport,
                    game_id,
                    game_date,
                    home_team,
                    away_team,
                    odds.get('moneyline_home'),
                    odds.get('moneyline_away'),
                    odds.get('spread'),
                    odds.get('total'),
                    odds.get('spread_price_home'),
                    odds.get('spread_price_away'),
                    odds.get('total_over_price'),
                    odds.get('total_under_price'),
                    odds.get('source', 'engine'),
                    now_ts,
                )
            )
    except Exception as _e:
        logger.debug(f"[engine_odds] upsert failed: {_e}")


def _attach_engine_odds_to_daily_results(sport, daily_results, limit=40):
    """Attach ESPN-engine odds to completed game results for ROI calculation."""
    if not daily_results:
        return
    from odds_engine_espn import get_odds as _espn_get_odds
    for dd in daily_results.values():
        for g in dd.get('games', []):
            home = g.get('home', '')
            away = g.get('away', '')
            odds = _espn_get_odds(sport, home, away)
            if odds:
                g['home_moneyline'] = odds['moneyline_home']
                g['away_moneyline'] = odds['moneyline_away']
                g['spread_price_home'] = odds.get('spread_price_home', -110)
                g['spread_price_away'] = odds.get('spread_price_away', -110)
                g['total_over_price'] = odds.get('total_over_price', -110)
                g['total_under_price'] = odds.get('total_under_price', -110)
                if g.get('market_spread') is None:
                    g['market_spread'] = odds.get('spread_home')
                if g.get('market_total') is None:
                    g['market_total'] = odds.get('total')
                g['odds_source'] = 'espn_engine'
            else:
                # Fallback to model probability
                ens = g.get('ens_prob')
                ml = _compute_odds_from_prob(ens)
                if ml:
                    g['home_moneyline'] = ml['moneyline_home']
                    g['away_moneyline'] = ml['moneyline_away']
                g.setdefault('spread_price_home', -110)
                g.setdefault('spread_price_away', -110)
                g.setdefault('total_over_price', -110)
                g.setdefault('total_under_price', -110)
                g['odds_source'] = 'model_fallback'

def _attach_engine_odds_to_predictions(sport, predictions, limit=40):
    """Attach ESPN-engine odds to upcoming predictions."""
    if not predictions:
        return
    from odds_engine_espn import get_odds as _espn_get_odds
    for pred in predictions:
        if pred.get('home_score') is not None:
            continue
        home = pred.get('home_team_id', '')
        away = pred.get('away_team_id', '')
        odds = _espn_get_odds(sport, home, away)
        if odds:
            pred['home_moneyline'] = odds['moneyline_home']
            pred['away_moneyline'] = odds['moneyline_away']
            pred['market_spread'] = odds.get('spread_home')
            pred['market_total'] = odds.get('total')
            pred['spread_price_home'] = odds.get('spread_price_home', -110)
            pred['spread_price_away'] = odds.get('spread_price_away', -110)
            pred['total_over_price'] = odds.get('total_over_price', -110)
            pred['total_under_price'] = odds.get('total_under_price', -110)
            pred['odds_source'] = 'espn_engine'
        else:
            # Fallback to model probability
            ens = pred.get('ensemble_prob')
            ml = _compute_odds_from_prob(ens)
            if ml:
                pred['home_moneyline'] = ml['moneyline_home']
                pred['away_moneyline'] = ml['moneyline_away']
            pred.setdefault('spread_price_home', -110)
            pred.setdefault('spread_price_away', -110)
            pred.setdefault('total_over_price', -110)
            pred.setdefault('total_under_price', -110)
            pred['odds_source'] = 'model_fallback'


def _compute_model_profit(daily_results):
    model_keys = ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']
    model_map = {'glicko2': 'glicko2', 'trueskill': 'trueskill', 'elo': 'elo', 'xgboost': 'xgb', 'ensemble': 'ens'}
    profit = {m: {'units': None, 'roi': None, 'risked': 0, 'missing': 0, 'reason': None} for m in model_keys}
    for m in model_keys:
        units = 0.0
        risked = 0
        missing = 0
        for dd in daily_results.values():
            for g in dd.get('games', []):
                if g.get('skip_grading'):
                    continue
                key = model_map.get(m, m)
                prob = g.get(f"{key}_prob")
                correct = g.get(f"{key}_correct")
                if prob is None or correct is None:
                    continue
                pick_home = prob >= 50
                odds = g.get('home_moneyline') if pick_home else g.get('away_moneyline')
                if odds is None:
                    missing += 1
                    continue
                risked += 1
                if correct:
                    payout = _american_units(odds)
                    units += payout if payout is not None else 0.0
                else:
                    units -= 1.0
        if risked == 0:
            reason = "no odds available for graded games" if missing > 0 else "no graded games with odds"
            profit[m].update({'units': None, 'roi': None, 'risked': 0, 'missing': missing, 'reason': reason})
        else:
            roi = round((units / risked) * 100, 1)
            profit[m].update({'units': round(units, 2), 'roi': roi, 'risked': risked, 'missing': missing})
    return profit


def _daily_results_from_weekly(weekly_results):
    from collections import defaultdict
    daily_results = defaultdict(lambda: {'games': []})
    if not weekly_results:
        return daily_results
    for week_data in weekly_results.values():
        for game in week_data.get('games', []):
            date_key = game.get('date') or 'Unknown'
            daily_results[date_key]['games'].append(game)
    return daily_results

def _banner_daily_results_for_range(sport, start_dt, end_dt):
    if sport == 'NFL':
        weekly_results = calculate_nfl_weekly_performance()
        return _daily_results_from_weekly(weekly_results) if weekly_results else None
    if sport == 'NHL':
        weekly_results = calculate_nhl_weekly_performance()
        return _daily_results_from_weekly(weekly_results) if weekly_results else None
    if sport == 'NBA':
        weekly_results = calculate_nba_weekly_performance()
        return _daily_results_from_weekly(weekly_results) if weekly_results else None

    try:
        conn = get_db_connection()
        rows = conn.execute('''
            SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ?
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) BETWEEN ? AND ?
            ORDER BY g.game_date DESC
        ''', (
            sport,
            sport,
            start_dt.strftime('%Y-%m-%d'),
            end_dt.strftime('%Y-%m-%d'),
        )).fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    from collections import defaultdict
    daily_results = defaultdict(lambda: {'games': []})
    for game in rows:
        home_score = _to_float_safe(game['home_score'])
        away_score = _to_float_safe(game['away_score'])
        if home_score is None or away_score is None:
            continue
        home_won = home_score > away_score
        is_draw = False
        if sport == 'SOCCER' and abs(home_score - away_score) < 1e-9:
            is_draw = True
            home_won = None
        home_team = game['home_team_id']
        away_team = game['away_team_id']
        _raw_date = _to_date_str(game['game_date'])
        game_date = _raw_date[:10] if _raw_date else None
        league_name = game.get('league') if isinstance(game, dict) else game['league']
        if sport == 'SOCCER':
            league_name = _canonical_soccer_league_name(league_name) or league_name
            if not league_name or league_name not in SOCCER_LEAGUE_ORDER:
                continue

        elo_prob = _to_float_safe(game['elo_home_prob'], 0.5)
        xgb_prob = _to_float_safe(game['xgboost_home_prob'])
        if xgb_prob is None:
            xgb_prob = _to_float_safe(game['elo_home_prob'], 0.5)
        ens_prob = _to_float_safe(game['win_probability'])
        if ens_prob is None:
            ens_prob = _to_float_safe(game['elo_home_prob'], 0.5)

        v2 = get_v2_prediction(sport, home_team, away_team, game_date) if sport != 'SOCCER' else None
        glicko2_prob = v2.get('glicko2_prob') if v2 else None
        trueskill_prob = v2.get('trueskill_prob') if v2 else None
        if v2:
            xgb_prob = v2.get('xgboost_prob', xgb_prob)
            ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

        game_info = {
            'game_id':         game['game_id'],
            'date':             game_date or 'Unknown',
            'home':             home_team,
            'away':             away_team,
            'league':           league_name or sport,
            'home_score':       int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
            'away_score':       int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
            'home_win':         home_won,
            'is_draw':          is_draw,
            'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
            'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
            'elo_prob':         round(elo_prob  * 100, 1),
            'xgb_prob':         round(xgb_prob  * 100, 1),
            'ens_prob':         round(ens_prob  * 100, 1),
            'glicko2_correct':   (glicko2_prob   > 0.5) == home_won if glicko2_prob   is not None and home_won is not None else None,
            'trueskill_correct': (trueskill_prob > 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
            'elo_correct':       (elo_prob  > 0.5) == home_won if home_won is not None else None,
            'xgb_correct':       (xgb_prob  > 0.5) == home_won if home_won is not None else None,
            'ens_correct':       (ens_prob  > 0.5) == home_won if ens_prob is not None and home_won is not None else None,
            'skip_grading':      True if home_won is None else False,
        }
        daily_results[game_info['date']]['games'].append(game_info)
    return daily_results


def _upsert_betting_line(conn, sport, game_id, game_date, home_team, away_team, spread, total, source=None):
    try:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info('betting_lines')").fetchall()]
    except Exception:
        cols = []
    has_extra = any(c in cols for c in ['sport', 'game_date', 'home_team', 'away_team'])
    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.cursor()
    try:
        if has_extra:
            existing = cur.execute(
                "SELECT id, spread, total FROM betting_lines WHERE sport=? AND game_id=? ORDER BY fetched_at DESC LIMIT 1",
                (sport, game_id)
            ).fetchone()
            if existing:
                cur.execute(
                    "UPDATE betting_lines SET spread=COALESCE(spread, ?), total=COALESCE(total, ?), fetched_at=? WHERE id=?",
                    (spread, total, now_ts, existing['id'])
                )
            else:
                cur.execute(
                    "INSERT INTO betting_lines (sport, game_id, game_date, home_team, away_team, spread, total, source, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (sport, game_id, game_date, home_team, away_team, spread, total, source or 'live', now_ts)
                )
        else:
            existing = cur.execute(
                "SELECT id, spread, total FROM betting_lines WHERE game_id=? LIMIT 1",
                (game_id,)
            ).fetchone()
            if existing:
                cur.execute(
                    "UPDATE betting_lines SET spread=COALESCE(spread, ?), total=COALESCE(total, ?) WHERE id=?",
                    (spread, total, existing['id'])
                )
            else:
                cur.execute(
                    "INSERT INTO betting_lines (game_id, spread, total) VALUES (?,?,?)",
                    (game_id, spread, total)
                )
    except Exception as _e:
        logger.debug(f"[betting_lines] upsert failed: {_e}")


def _cache_market_lines_for_predictions(sport, predictions, limit=20):
    if sport not in ['NBA', 'MLB'] or not predictions:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        attempts = 0
        for pred in predictions:
            if pred.get('home_score') is not None:
                continue
            game_id = pred.get('game_id')
            if not game_id:
                continue
            if attempts >= limit:
                break
            try:
                cols = [r['name'] for r in cur.execute("PRAGMA table_info('betting_lines')").fetchall()]
                has_extra = any(c in cols for c in ['sport', 'game_date', 'home_team', 'away_team'])
                if has_extra:
                    existing = cur.execute(
                        "SELECT spread, total FROM betting_lines WHERE sport=? AND game_id=? ORDER BY fetched_at DESC LIMIT 1",
                        (sport, game_id)
                    ).fetchone()
                else:
                    existing = cur.execute(
                        "SELECT spread, total FROM betting_lines WHERE game_id=? LIMIT 1",
                        (game_id,)
                    ).fetchone()
                if existing and (existing['spread'] is not None or existing['total'] is not None):
                    continue
            except Exception:
                pass
            line = _fetch_live_market_line(
                sport,
                game_id,
                pred.get('game_date'),
                pred.get('home_team_id'),
                pred.get('away_team_id')
            )
            attempts += 1
            if line and (line.get('spread') is not None or line.get('total') is not None):
                _upsert_betting_line(
                    conn,
                    sport,
                    game_id,
                    pred.get('game_date'),
                    pred.get('home_team_id'),
                    pred.get('away_team_id'),
                    line.get('spread'),
                    line.get('total'),
                    line.get('source')
                )
        conn.commit()
        conn.close()
    except Exception as _e:
        logger.debug(f"[{sport}] cache market lines failed: {_e}")


def _cache_market_lines_for_results(sport, daily_results, limit=20):
    if sport not in ['NBA', 'MLB'] or not daily_results:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        attempts = 0
        for dd in daily_results.values():
            for g in dd.get('games', []):
                if attempts >= limit:
                    break
                gid = g.get('game_id')
                gd = g.get('date')
                if not gid or not gd:
                    continue
                try:
                    cols = [r['name'] for r in cur.execute("PRAGMA table_info('betting_lines')").fetchall()]
                    has_extra = any(c in cols for c in ['sport', 'game_date', 'home_team', 'away_team'])
                    if has_extra:
                        existing = cur.execute(
                            "SELECT spread, total FROM betting_lines WHERE sport=? AND game_id=? ORDER BY fetched_at DESC LIMIT 1",
                            (sport, gid)
                        ).fetchone()
                    else:
                        existing = cur.execute(
                            "SELECT spread, total FROM betting_lines WHERE game_id=? LIMIT 1",
                            (gid,)
                        ).fetchone()
                    if existing and (existing['spread'] is not None or existing['total'] is not None):
                        continue
                except Exception:
                    pass
                try:
                    gd_dt = parse_date(gd)
                    if gd_dt and sport in ['NBA', 'MLB'] and abs((datetime.now() - gd_dt).days) > 3:
                        continue
                except Exception:
                    pass
                line = _fetch_live_market_line(
                    sport,
                    gid,
                    gd,
                    g.get('home'),
                    g.get('away')
                )
                attempts += 1
                if line and (line.get('spread') is not None or line.get('total') is not None):
                    _upsert_betting_line(
                        conn,
                        sport,
                        gid,
                        gd,
                        g.get('home'),
                        g.get('away'),
                        line.get('spread'),
                        line.get('total'),
                        line.get('source')
                    )
            if attempts >= limit:
                break
        conn.commit()
        conn.close()
    except Exception as _e:
        logger.debug(f"[{sport}] cache market results failed: {_e}")


def _fetch_live_market_line(
    sport: str,
    game_id: str,
    game_date: str = None,
    home_team: str = None,
    away_team: str = None
):
    """
    Fetch market spread/total for a game from ESPN Core API.
    Returns {'spread': float|None, 'total': float|None, 'source': str} or None.
    """
    sport_path = CORE_API_SPORT_PATHS.get(sport)
    if not sport_path or not game_id:
        return None
    event_candidates = []
    raw_event_id = str(game_id).split('_')[-1]
    if raw_event_id:
        event_candidates.append(raw_event_id)

    # NHL uses local game IDs (e.g., NHL_2025021109) that don't map to ESPN events.
    # For those, resolve event ID via date + teams on ESPN scoreboard first.
    needs_matchup_lookup = (
        sport == 'NHL'
        and game_date
        and home_team
        and away_team
        and (not raw_event_id.startswith('401'))
    )
    mapped_event_id = (
        _resolve_espn_event_id_by_matchup(sport, game_date, home_team, away_team)
        if needs_matchup_lookup else None
    )
    if mapped_event_id:
        event_candidates = [mapped_event_id] + [eid for eid in event_candidates if eid != mapped_event_id]

    if not event_candidates:
        return None
        return None

    sport_slug, league_slug = sport_path
    for event_id in event_candidates:
        odds_url = (
            f"https://sports.core.api.espn.com/v2/sports/{sport_slug}/leagues/{league_slug}/"
            f"events/{event_id}/competitions/{event_id}/odds"
        )

        try:
            odds_data = _cached_get(odds_url, timeout=8)
            items = odds_data.get('items', []) if isinstance(odds_data, dict) else []
            if not items:
                continue

            chosen = None
            for item in items:
                if item.get('spread') is not None or item.get('overUnder') is not None:
                    chosen = item
                    break
            if chosen is None:
                chosen = items[0]

            def _to_num(v):
                try:
                    return float(v) if v is not None else None
                except Exception:
                    return None

            spread_val = _to_num(chosen.get('spread'))
            total_val = _to_num(chosen.get('overUnder'))
            if spread_val is None and total_val is None:
                continue

            return {
                'spread': spread_val,
                'total': total_val,
                'source': (
                    'ESPN Core API (matchup fallback)'
                    if mapped_event_id and str(event_id) == str(mapped_event_id)
                    else 'ESPN Core API (live fallback)'
                ),
            }
        except Exception:
            continue

    return None

app = Flask(__name__)
CORS(app, origins=[
    'https://underdogs.bet',
    'https://www.underdogs.bet',
    'http://localhost:3000',
    'http://localhost:5000',
])

@app.context_processor
def inject_globals():
    """Make global template variables available in every template automatically."""
    # Determine current sport from request args or view context
    _sport = request.view_args.get('sport', '') if request.view_args else ''
    return {
        'stripe_donation_url': STRIPE_DONATION_URL,
        'contact_email': CONTACT_EMAIL,
        'social_links': SOCIAL_LINKS,
        'soccer_enabled': SOCCER_ENABLED,
        'ga_tracking_id': GA_TRACKING_ID,
        'sport_seo_slug': SPORT_SEO_SLUGS.get(_sport, ''),
        'sport_results_slug': _SPORT_RESULTS_SLUGS.get(_sport, ''),
    }

@app.after_request
def add_header(response):
    """Add headers to allow iframe embedding from underdogs.bet"""
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    response.headers['Content-Security-Policy'] = (
        "frame-ancestors 'self' https://underdogs.bet https://www.underdogs.bet "
        "http://localhost:3000"
    )
    return response

import os as _os
_DATA_DIR = '/data' if _os.path.isdir('/data') else '.'
DATABASE = _os.path.join(_DATA_DIR, 'sports_predictions_original.db')
# Absolute path to this file's directory — used for template loading
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))
ODDS_ENGINE_URL = _os.environ.get('ODDS_ENGINE_URL')

# ── Auth + Premium System ─────────────────────────────────────────────────────
from auth_system import init_auth, is_premium_user
init_auth(app, db_path=DATABASE)
_TRAFFIC_TZ = 'America/New_York'

def _traffic_now():
    try:
        return datetime.now(ZoneInfo(_TRAFFIC_TZ))
    except Exception:
        return datetime.now()

def log_site_visit(endpoint):
    """Track site visits for analytics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        visit_date = _traffic_now().strftime('%Y-%m-%d')
        ip_address = request.remote_addr if request else None
        user_agent = request.headers.get('User-Agent') if request else None
        
        cursor.execute('''
            INSERT INTO site_visits (visit_date, ip_address, user_agent, endpoint)
            VALUES (?, ?, ?, ?)
        ''', (visit_date, ip_address, user_agent, endpoint))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error logging site visit: {e}")

SPORTS = {
    'NHL': {'name': 'NHL', 'icon': '🏒', 'color': '#1e3a8a'},
    'NFL': {'name': 'NFL', 'icon': '🏈', 'color': '#059669'},
    'NBA': {'name': 'NBA', 'icon': '🏀', 'color': '#dc2626'},
    'MLB': {'name': 'MLB', 'icon': '⚾', 'color': '#9333ea'},
    'NCAAF': {'name': 'NCAA Football', 'icon': '🏟️', 'color': '#ea580c'},
    'NCAAB': {'name': 'NCAA Basketball', 'icon': '🎓', 'color': '#0891b2'},
    'NCAAW': {'name': "NCAA Women's Basketball", 'icon': '🏀', 'color': '#db2777'},
    'WNBA': {'name': 'WNBA', 'icon': '🏀', 'color': '#f97316'},
    'SOCCER': {'name': 'Soccer', 'icon': '⚽', 'color': '#22c55e'},
}
SOCCER_ENABLED = True

# ── SEO-friendly URL slugs ─────────────────────────────────────────────────────
SPORT_SEO_SLUGS = {
    'NHL': 'nhl-picks',
    'NBA': 'nba-picks',
    'NFL': 'nfl-picks',
    'MLB': 'mlb-picks',
    'NCAAB': 'ncaab-picks',
    'NCAAW': 'ncaaw-picks',
    'NCAAF': 'ncaaf-picks',
    'WNBA': 'wnba-picks',
    'SOCCER': 'soccer-picks',
}
_SEO_SLUG_TO_SPORT = {v: k for k, v in SPORT_SEO_SLUGS.items()}
_SPORT_RESULTS_SLUGS = {k: v.replace('-picks', '-results') for k, v in SPORT_SEO_SLUGS.items()}
_RESULTS_SLUG_TO_SPORT = {v: k for k, v in _SPORT_RESULTS_SLUGS.items()}

_MONTH_NAMES = {
    1: 'january', 2: 'february', 3: 'march', 4: 'april',
    5: 'may', 6: 'june', 7: 'july', 8: 'august',
    9: 'september', 10: 'october', 11: 'november', 12: 'december',
}
_MONTH_NAME_TO_NUM = {v: k for k, v in _MONTH_NAMES.items()}

# Sport-specific background images for predictions pages
SPORT_BG_IMAGES = {
    'NFL': '/static/sandro-schuh-HgwY_YQ1m0w-unsplash.jpg',
    'NCAAF': '/static/sandro-schuh-HgwY_YQ1m0w-unsplash.jpg',
    'SOCCER': '/static/maxim-hopman-xyDkHkvDYp4-unsplash.jpg',
    'NBA': '/static/IMG_2695.jpeg',
    'WNBA': '/static/IMG_2695.jpeg',
    'NCAAB': '/static/IMG_2695.jpeg',
    'NCAAW': '/static/IMG_2695.jpeg',
    'MLB': '/static/baseball.jpg',
    'NHL': '/static/seth-hoffman-HwZTYUkIP6c-unsplash.jpg',
}

# Curated soccer leagues (ESPN metadata → canonical display names)
SOCCER_LEAGUE_ORDER = [
    'English Premier League',
    'UEFA Champions League',
    'UEFA Europa League',
    'UEFA Europa Conference League',
    'Spanish LaLiga',
    'German Bundesliga',
    'Italian Serie A',
    'French Ligue 1',
    'Dutch Eredivisie',
    'Portuguese Primeira Liga',
    'EFL Championship',
    'FA Cup',
    'EFL Cup',
    'Major League Soccer',
    'Liga MX',
    'Copa Libertadores',
    'FIFA World Cup',
    'FIFA World Cup Qualifiers (UEFA)',
    'FIFA World Cup Qualifiers (CONMEBOL)',
    'FIFA World Cup Qualifiers (CAF)',
    'FIFA World Cup Qualifiers (CONCACAF)',
    'Spanish Segunda División',
    'CONCACAF Champions Cup',
    'Leagues Cup',
    'USL Championship',
]
_SOCCER_LEAGUE_CANONICAL = {
    'english premier league': 'English Premier League',
    'premier league': 'English Premier League',
    'epl': 'English Premier League',
    'eng.1': 'English Premier League',
    'fa cup': 'FA Cup',
    'english fa cup': 'FA Cup',
    'carabao cup': 'EFL Cup',
    'english carabao cup': 'EFL Cup',
    'english league cup': 'EFL Cup',
    'efl cup': 'EFL Cup',
    'league cup': 'EFL Cup',
    'eng.2': 'EFL Championship',
    'efl championship': 'EFL Championship',
    'league championship': 'EFL Championship',
    'english league championship': 'EFL Championship',
    'uefa champions league': 'UEFA Champions League',
    'champions league': 'UEFA Champions League',
    'uefa champions league qualifiers': 'UEFA Champions League',
    'uefa europa league': 'UEFA Europa League',
    'europa league': 'UEFA Europa League',
    'uefa europa league qualifiers': 'UEFA Europa League',
    'uefa europa conference league': 'UEFA Europa Conference League',
    'uefa conference league': 'UEFA Europa Conference League',
    'europa conference league': 'UEFA Europa Conference League',
    'conference league': 'UEFA Europa Conference League',
    'uefa europa conference league qualifiers': 'UEFA Europa Conference League',
    'spanish laliga': 'Spanish LaLiga',
    'laliga': 'Spanish LaLiga',
    'la liga': 'Spanish LaLiga',
    'esp.1': 'Spanish LaLiga',
    'spanish laliga 2': 'Spanish Segunda División',
    'spanish laliga2': 'Spanish Segunda División',
    'segunda división': 'Spanish Segunda División',
    'segunda division': 'Spanish Segunda División',
    'la liga 2': 'Spanish Segunda División',
    'esp.2': 'Spanish Segunda División',
    'german bundesliga': 'German Bundesliga',
    'bundesliga': 'German Bundesliga',
    'ger.1': 'German Bundesliga',
    'italian serie a': 'Italian Serie A',
    'serie a': 'Italian Serie A',
    'ita.1': 'Italian Serie A',
    'french ligue 1': 'French Ligue 1',
    'ligue 1': 'French Ligue 1',
    'fra.1': 'French Ligue 1',
    'fifa world cup': 'FIFA World Cup',
    'world cup': 'FIFA World Cup',
    'fifa world cup qualifying': 'FIFA World Cup Qualifiers (UEFA)',
    'fifa world cup qualifiers': 'FIFA World Cup Qualifiers (UEFA)',
    'world cup qualifiers': 'FIFA World Cup Qualifiers (UEFA)',
    'uefa world cup qualifiers': 'FIFA World Cup Qualifiers (UEFA)',
    'fifa world cup qualifying - uefa': 'FIFA World Cup Qualifiers (UEFA)',
    'fifa world cup qualifying - conmebol': 'FIFA World Cup Qualifiers (CONMEBOL)',
    'fifa world cup qualifying - caf': 'FIFA World Cup Qualifiers (CAF)',
    'fifa world cup qualifying - concacaf': 'FIFA World Cup Qualifiers (CONCACAF)',
    'conmebol world cup qualifiers': 'FIFA World Cup Qualifiers (CONMEBOL)',
    'caf world cup qualifiers': 'FIFA World Cup Qualifiers (CAF)',
    'concacaf world cup qualifiers': 'FIFA World Cup Qualifiers (CONCACAF)',
    'major league soccer': 'Major League Soccer',
    'mls': 'Major League Soccer',
    'usa.1': 'Major League Soccer',
    'liga mx': 'Liga MX',
    'mexican liga bbva mx': 'Liga MX',
    'bbva mx': 'Liga MX',
    'mex.1': 'Liga MX',
    'concacaf champions cup': 'CONCACAF Champions Cup',
    'concacaf champions league': 'CONCACAF Champions Cup',
    'leagues cup': 'Leagues Cup',
    'usl championship': 'USL Championship',
    'usa.2': 'USL Championship',
    'dutch eredivisie': 'Dutch Eredivisie',
    'eredivisie': 'Dutch Eredivisie',
    'ned.1': 'Dutch Eredivisie',
    'portuguese primeira liga': 'Portuguese Primeira Liga',
    'primeira liga': 'Portuguese Primeira Liga',
    'por.1': 'Portuguese Primeira Liga',
    'copa libertadores': 'Copa Libertadores',
    'conmebol libertadores': 'Copa Libertadores',
}

SOCCER_LEAGUE_ENDPOINTS = {
    'English Premier League': 'eng.1',
    'FA Cup': 'eng.fa',
    'EFL Cup': 'eng.league_cup',
    'EFL Championship': 'eng.2',
    'UEFA Champions League': 'uefa.champions',
    'UEFA Europa League': 'uefa.europa',
    'UEFA Europa Conference League': 'uefa.europa.conf',
    'Spanish LaLiga': 'esp.1',
    'Spanish Segunda División': 'esp.2',
    'German Bundesliga': 'ger.1',
    'Italian Serie A': 'ita.1',
    'French Ligue 1': 'fra.1',
    'FIFA World Cup': 'fifa.world',
    'FIFA World Cup Qualifiers (UEFA)': 'fifa.worldq.uefa',
    'FIFA World Cup Qualifiers (CONMEBOL)': 'fifa.worldq.conmebol',
    'FIFA World Cup Qualifiers (CAF)': 'fifa.worldq.caf',
    'FIFA World Cup Qualifiers (CONCACAF)': 'fifa.worldq.concacaf',
    'Major League Soccer': 'usa.1',
    'Liga MX': 'mex.1',
    'Dutch Eredivisie': 'ned.1',
    'Portuguese Primeira Liga': 'por.1',
    'Copa Libertadores': 'conmebol.libertadores',
    'CONCACAF Champions Cup': 'concacaf.champions',
    'Leagues Cup': 'concacaf.leagues.cup',
    'USL Championship': None,
}

def _canonical_soccer_league_name(league_name: str):
    if not league_name:
        return None
    key = league_name.strip().lower()
    return _SOCCER_LEAGUE_CANONICAL.get(key)

def _canonical_soccer_league_from_event(event, competition):
    league = (event.get('league') or {}) if event else {}
    comp_league = (competition.get('league') or {}) if competition else {}
    candidates = [
        league.get('name'), league.get('shortName'), league.get('abbreviation'),
        comp_league.get('name'), comp_league.get('shortName'), comp_league.get('abbreviation'),
    ]
    for raw in candidates:
        canonical = _canonical_soccer_league_name(raw)
        if canonical:
            return canonical
    return None

def _ordered_soccer_leagues(leagues):
    if not leagues:
        return []
    league_set = {l for l in leagues if l}
    ordered = [l for l in SOCCER_LEAGUE_ORDER if l in league_set]
    extras = sorted(league_set - set(SOCCER_LEAGUE_ORDER))
    return ordered + extras

def _soccer_league_slug(name: str) -> str:
    if not name:
        return ''
    import re as _re
    slug = _re.sub(r'[^a-z0-9]+', '-', name.strip().lower())
    return slug.strip('-')

SOCCER_LEAGUE_SLUGS = {_soccer_league_slug(n): n for n in SOCCER_LEAGUE_ORDER}

def _soccer_league_from_slug(slug: str):
    if not slug:
        return None
    return SOCCER_LEAGUE_SLUGS.get(slug.strip().lower())

def _get_soccer_model_bundle(completed_games, league_name=None):
    league_key = _soccer_league_slug(league_name) if league_name else 'all'
    cache_key = f"soccer_bundle_{league_key}"
    now_ts = _time.time()
    cached = _SOCCER_MODEL_CACHE.get(cache_key)
    if cached and (now_ts - cached.get('ts', 0)) < _SOCCER_MODEL_TTL:
        return cached.get('bundle')
    def _val(game, key):
        if isinstance(game, dict):
            return game.get(key)
        try:
            return game[key]
        except Exception:
            return None

    # Merge passed-in games with completed games from DB
    # This ensures the model has enough training data even if the
    # ESPN live feed only returns upcoming games
    filtered = []
    seen_keys = set()
    
    # First add passed-in completed games
    for game in (completed_games or []):
        league_raw = _val(game, 'league')
        league = _canonical_soccer_league_name(league_raw) or league_raw
        if league_name and league != league_name:
            continue
        if _val(game, 'home_score') is None or _val(game, 'away_score') is None:
            continue
        gd = game if isinstance(game, dict) else dict(game)
        key = (_val(game, 'game_id'), _val(game, 'home_team_id'), _val(game, 'away_team_id'))
        seen_keys.add(key)
        filtered.append(gd)

    # Then supplement from DB if we don't have enough
    if len(filtered) < 12:
        try:
            conn = get_db_connection()
            league_filter = league_name or '%'
            db_games = conn.execute('''
                SELECT game_id, game_date, home_team_id, away_team_id,
                       home_score, away_score, league
                FROM games
                WHERE sport = 'SOCCER'
                  AND home_score IS NOT NULL
                  AND league LIKE ?
                ORDER BY game_date DESC
                LIMIT 200
            ''', (f'%{league_name}%' if league_name else '%',)).fetchall()
            conn.close()
            for row in db_games:
                key = (row['game_id'], row['home_team_id'], row['away_team_id'])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                filtered.append(dict(row))
        except Exception as _e:
            logger.debug(f"[soccer] DB supplement failed: {_e}")

    bundle = build_soccer_model_bundle(filtered, league_name=league_name)
    _SOCCER_MODEL_CACHE[cache_key] = {'ts': now_ts, 'bundle': bundle}
    return bundle

# ── Public-facing model brand names ───────────────────────────────────────────
# Maps internal identifiers → user-facing names shown in UI / API responses.
# Internal variables, files, and training logic are UNCHANGED.
MODEL_DISPLAY_NAMES = {
    'glicko2':   'Grinder2',
    'trueskill': 'Takedown',
    'elo':       'Edge',
    'xgboost':   'XSharp',
    'ensemble':  'Sharp Consensus',
}
SOCCER_MODEL_LABELS = {
    'glicko2': 'Grinder2',
    'trueskill': 'Takedown',
    'elo': 'Edge',
    'xgboost': 'XSharp',
    'ensemble': 'Sharp Consensus',
}

import nfl_data_py as nfl

# ── Puck-Line Cover Probability Configuration ─────────────────────────────────
# Standard deviation for goal-differential normal distribution (tunable per sport).
# Only NHL uses puck-line display; all others keep raw spread in the UI.
PUCK_LINE_STD: dict = {
    'NHL':   1.5,
    'NBA':  12.0,
    'NFL':  10.0,
    'MLB':   2.0,
    'NCAAB': 12.0,
    'NCAAW': 11.0,
    'NCAAF': 14.0,
    'WNBA':  12.0,
    'SOCCER': 1.2,
}
_PUCK_LINE_VALUE = 1.5  # NHL puck line is always ±1.5


def compute_puck_line_prob(spread: float, sport: str = 'NHL') -> dict:
    """Convert an XSharp goal-differential spread into puck-line cover probabilities.

    spread > 0  → home team favored
    spread < 0  → away team favored

    Steps:
      1. Assume goal-differential ~ N(|spread|, std)
      2. P_cover_fav = 1 - CDF(1.5 | |spread|, std)   (favorite wins by >1.5)
      3. P_cover_dog =     CDF(1.5 | |spread|, std)   (underdog keeps it within 1.5)
      4. Tag: STRONG ≥55%, LEAN 52–55%, NO EDGE otherwise

    Returns dict with keys:
      puck_line_fav_prob  – favourite -1.5 cover % (0–100)
      puck_line_dog_prob  – underdog  +1.5 cover % (0–100)
      puck_line_tag       – STRONG -1.5 / LEAN -1.5 / STRONG +1.5 / LEAN +1.5 / NO EDGE
      puck_line_fav_side  – 'home' or 'away'
    """
    from scipy.stats import norm
    std  = PUCK_LINE_STD.get(sport, 1.5)
    line = _PUCK_LINE_VALUE
    abs_spread = abs(spread)

    p_fav = float(1.0 - norm.cdf(line, loc=abs_spread, scale=std))
    p_dog = float(norm.cdf(line, loc=abs_spread, scale=std))
    p_fav_pct = round(p_fav * 100, 1)
    p_dog_pct = round(p_dog * 100, 1)

    if p_fav_pct >= 55:
        tag = 'STRONG -1.5'
    elif p_fav_pct >= 52:
        tag = 'LEAN -1.5'
    elif p_dog_pct >= 55:
        tag = 'STRONG +1.5'
    elif p_dog_pct >= 52:
        tag = 'LEAN +1.5'
    else:
        tag = 'NO EDGE'

    return {
        'puck_line_fav_prob': p_fav_pct,
        'puck_line_dog_prob': p_dog_pct,
        'puck_line_tag':      tag,
        'puck_line_fav_side': 'home' if spread >= 0 else 'away',
    }


def update_nfl_scores():
    """
    Fetches and updates NFL scores for the 2025 season.
    Also inserts new games (including playoffs) that don't exist in database.
    """
    try:
        logger.info("Fetching 2025 NFL schedule to update scores...")
        schedule = nfl.import_schedules([2025])
        
        if schedule.empty:
            logger.warning("No NFL schedule data found for the 2025 season.")
            return

        finished_games = schedule[schedule['result'].notna()].copy()

        if finished_games.empty:
            logger.info("No new finished NFL games with results found.")
            return

        logger.info(f"Found {len(finished_games)} finished NFL games to update.")
        
        # Team abbreviation to full name mapping for NFL
        nfl_abbr_to_full = {
            'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens',
            'BUF': 'Buffalo Bills', 'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears',
            'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns', 'DAL': 'Dallas Cowboys',
            'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
            'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars',
            'KC': 'Kansas City Chiefs', 'LV': 'Las Vegas Raiders', 'LAC': 'Los Angeles Chargers',
            'LAR': 'Los Angeles Rams', 'LA': 'Los Angeles Rams', 'MIA': 'Miami Dolphins',
            'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints',
            'NYG': 'New York Giants', 'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles',
            'PIT': 'Pittsburgh Steelers', 'SF': 'San Francisco 49ers', 'SEA': 'Seattle Seahawks',
            'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders'
        }
        
        conn = get_db_connection()
        cursor = conn.cursor()
        updates_count = 0
        inserts_count = 0

        for _, game in finished_games.iterrows():
            game_id = game['game_id']
            
            # Check if game exists
            existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = 'NFL'", (game_id,)).fetchone()
            
            if existing:
                # Update existing game
                cursor.execute("""
                    UPDATE games
                    SET home_score = ?, away_score = ?, status = 'final'
                    WHERE sport = 'NFL' AND game_id = ?
                """, (game['home_score'], game['away_score'], game_id))
                if cursor.rowcount > 0:
                    updates_count += 1
            else:
                # Insert new game (including playoffs)
                try:
                    home_team = nfl_abbr_to_full.get(game['home_team'], game['home_team'])
                    away_team = nfl_abbr_to_full.get(game['away_team'], game['away_team'])
                    game_date = str(game['gameday']) if pd.notna(game.get('gameday')) else str(game.get('game_date', ''))
                    
                    cursor.execute("""
                        INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                    """, ('NFL', 'NFL', game_id, 2025, game_date, home_team, away_team, game['home_score'], game['away_score']))
                    inserts_count += 1
                    logger.info(f"Inserted new NFL game: {away_team} @ {home_team} (Week {game.get('week', '?')})")
                except Exception as insert_error:
                    logger.error(f"Error inserting NFL game {game_id}: {insert_error}")

        conn.commit()
        conn.close()
        logger.info(f"Successfully updated {updates_count} and inserted {inserts_count} NFL game scores.")

    except Exception as e:
        logger.error(f"An error occurred while updating NFL scores: {e}")

def update_nhl_scores():
    """
    Fetches and updates NHL scores using the NHL API.
    Gets scores from the last 30 days (to catch any missing games).
    """
    try:
        lookback_days = 10
        logger.info(f"Fetching NHL scores from API (last {lookback_days} days)...")
        
        # Fetch recent window to keep request latency low while still catching missed finals.
        from datetime import datetime, timedelta
        today = datetime.now()
        start_date = today - timedelta(days=lookback_days)
        
        # NHL team abbreviation to full name mapping
        nhl_team_map = {
            'ANA': 'Anaheim Ducks', 'BOS': 'Boston Bruins', 'BUF': 'Buffalo Sabres',
            'CGY': 'Calgary Flames', 'CAR': 'Carolina Hurricanes', 'CHI': 'Chicago Blackhawks',
            'COL': 'Colorado Avalanche', 'CBJ': 'Columbus Blue Jackets', 'DAL': 'Dallas Stars',
            'DET': 'Detroit Red Wings', 'EDM': 'Edmonton Oilers', 'FLA': 'Florida Panthers',
            'LAK': 'Los Angeles Kings', 'MIN': 'Minnesota Wild', 'MTL': 'Montreal Canadiens',
            'NSH': 'Nashville Predators', 'NJD': 'New Jersey Devils', 'NYI': 'New York Islanders',
            'NYR': 'New York Rangers', 'OTT': 'Ottawa Senators', 'PHI': 'Philadelphia Flyers',
            'PIT': 'Pittsburgh Penguins', 'SJS': 'San Jose Sharks', 'SEA': 'Seattle Kraken',
            'STL': 'St. Louis Blues', 'TBL': 'Tampa Bay Lightning', 'TOR': 'Toronto Maple Leafs',
            'VAN': 'Vancouver Canucks', 'VGK': 'Vegas Golden Knights', 'WSH': 'Washington Capitals',
            'WPG': 'Winnipeg Jets', 'UTA': 'Utah Hockey Club'
        }
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        updates_count = 0
        current_date = start_date
        
        # Iterate through lookback window
        while current_date <= today:
            date_str = current_date.strftime('%Y-%m-%d')
            
            try:
                # Fetch scores for this date from NHL API
                url = f"https://api-web.nhle.com/v1/score/{date_str}"
                response = requests.get(url, timeout=3)  # Shorter timeout
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get('games', [])
                    
                    for game in games:
                        # Only process finished games
                        if game.get('gameState') in ['OFF', 'FINAL']:
                            home_abbr = game['homeTeam']['abbrev']
                            away_abbr = game['awayTeam']['abbrev']
                            home_score = game['homeTeam'].get('score', 0)
                            away_score = game['awayTeam'].get('score', 0)
                            
                            # Convert abbreviations to full names
                            home_team = nhl_team_map.get(home_abbr, home_abbr)
                            away_team = nhl_team_map.get(away_abbr, away_abbr)
                            
                            game_id = f"NHL_{game.get('id')}"
                            
                            # Check if game exists
                            existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = 'NHL'", (game_id,)).fetchone()
                            
                            if existing:
                                # Update existing game
                                cursor.execute("""
                                    UPDATE games
                                    SET home_score = ?, away_score = ?, status = 'final'
                                    WHERE sport = 'NHL' 
                                      AND game_id = ?
                                      AND (home_score IS NULL OR home_score != ?)
                                """, (home_score, away_score, game_id, home_score))
                            else:
                                # Insert new completed game
                                try:
                                    cursor.execute("""
                                        INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                                    """, ('NHL', 'NHL', game_id, 2025, date_str, home_team, away_team, home_score, away_score))
                                    logger.info(f"Inserted new NHL game: {away_team} @ {home_team} ({date_str})")
                                except Exception as insert_error:
                                    logger.error(f"Error inserting NHL game {game_id}: {insert_error}")
                            
                            if cursor.rowcount > 0:
                                updates_count += 1
                
            except Exception as date_error:
                # Skip silently to avoid log spam
                pass
            
            current_date += timedelta(days=1)
        
        conn.commit()
        conn.close()
        logger.info(f"Successfully updated {updates_count} NHL game scores.")
        
    except Exception as e:
        logger.error(f"An error occurred while updating NHL scores: {e}")

def update_nba_scores():
    """
    Fetches and updates NBA scores using ESPN API.
    Checks last 7 days for score updates.
    """
    update_espn_scores('NBA')

def update_espn_scores(sport):
    """
    Generic ESPN API score updater for NBA, NCAAB, NCAAF, MLB, WNBA.
    Checks last 7 days for score updates.
    """
    ESPN_ENDPOINTS = {
        'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
        'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
        'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
        'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
        'NCAAW': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard',
        'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
        'SOCCER': 'https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard',
    }
    
    if sport == 'SOCCER':
        try:
            logger.info("Fetching SOCCER scores from ESPN league endpoints...")
            conn = get_db_connection()
            cursor = conn.cursor()
            updates_count = 0
            request_count = 0

            try:
                completed_count = conn.execute(
                    "SELECT COUNT(*) FROM games WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL",
                    (sport,)
                ).fetchone()[0]
            except Exception:
                completed_count = 0

            days_back = 14 if completed_count < 50 else 3
            max_requests = 140 if completed_count < 50 else 50
            today = datetime.now()

            for days_offset in range(days_back):
                if request_count >= max_requests:
                    break
                date_str = (today - timedelta(days=days_offset)).strftime('%Y%m%d')
                for league_label in SOCCER_LEAGUE_ORDER:
                    if request_count >= max_requests:
                        break
                    league_code = SOCCER_LEAGUE_ENDPOINTS.get(league_label)
                    if not league_code:
                        continue
                    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard?dates={date_str}"
                    request_count += 1
                    try:
                        response = requests.get(url, timeout=10)
                        response.raise_for_status()
                        data = response.json()
                    except Exception as e:
                        logger.debug(f"Error fetching SOCCER league {league_code} for {date_str}: {e}")
                        continue

                    league_info = (data.get('leagues', [{}])[0] or {}) if isinstance(data, dict) else {}
                    league_name = _canonical_soccer_league_name(league_info.get('name')) or league_label
                    events = data.get('events', []) if isinstance(data, dict) else []

                    for event in events:
                        competition = event.get('competitions', [{}])[0]
                        competitors = competition.get('competitors', [])
                        if len(competitors) != 2:
                            continue
                        status_info = event.get('status', {}).get('type', {})
                        status_name = status_info.get('name', '')
                        if not status_name.startswith('STATUS_FINAL'):
                            continue

                        home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                        away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                        if not home or not away:
                            continue

                        home_team = home.get('team', {}).get('displayName', '')
                        away_team = away.get('team', {}).get('displayName', '')
                        try:
                            home_score = int(home.get('score', 0))
                            away_score = int(away.get('score', 0))
                        except Exception:
                            continue

                        event_dt = event.get('date', '')
                        game_date = _espn_event_date_to_local(event_dt) or (today - timedelta(days=days_offset)).strftime('%Y-%m-%d')
                        event_id = event.get('id', '')
                        game_id = f"{sport}_{league_code}_{event_id}"

                        existing = cursor.execute(
                            "SELECT 1 FROM games WHERE game_id = ? AND sport = ?",
                            (game_id, sport)
                        ).fetchone()

                        if existing:
                            cursor.execute(
                                """
                                UPDATE games
                                SET home_score = ?, away_score = ?, status = 'final'
                                WHERE sport = ?
                                  AND game_id = ?
                                  AND (home_score IS NULL OR home_score != ?)
                                """,
                                (home_score, away_score, sport, game_id, home_score)
                            )
                        else:
                            try:
                                cursor.execute(
                                    """
                                    INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                                    """,
                                    (sport, league_name or sport, game_id, 2025, game_date, home_team, away_team, home_score, away_score)
                                )
                                logger.info(f"Inserted new {sport} game: {away_team} @ {home_team} ({game_date})")
                            except Exception as insert_error:
                                logger.error(f"Error inserting {sport} game {game_id}: {insert_error}")

                        if cursor.rowcount > 0:
                            updates_count += 1

            conn.commit()
            conn.close()
            if updates_count > 0:
                logger.info(f"Successfully updated {updates_count} {sport} game scores.")
            else:
                logger.info(f"No {sport} score updates needed.")
        except Exception as e:
            logger.error(f"An error occurred while updating {sport} scores: {e}")
        return

    if sport not in ESPN_ENDPOINTS:
        logger.warning(f"No ESPN endpoint for {sport}")
        return
    
    try:
        logger.info(f"Fetching {sport} scores from ESPN API (last 7 days)...")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        updates_count = 0
        
        # Check last 7 days
        for days_back in range(7):
            check_date = datetime.now() - timedelta(days=days_back)
            date_str = check_date.strftime('%Y%m%d')
            
            extra_params = '&groups=50&limit=357' if sport == 'NCAAB' else ''
            url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}{extra_params}"
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                events = data.get('events', [])
                
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    # Get status
                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', '')
                    
                    if status_name not in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    league_name = None
                    try:
                        league_name = (
                            event.get('league', {}) or {}
                        ).get('name') or (
                            competition.get('league', {}) or {}
                        ).get('name')
                    except Exception:
                        league_name = None
                    if sport == 'SOCCER':
                        league_name = _canonical_soccer_league_from_event(event, competition)
                        if not league_name:
                            continue
                    
                    try:
                        home_score = int(home.get('score', 0))
                        away_score = int(away.get('score', 0))
                    except:
                        continue
                    
                    game_date = check_date.strftime('%Y-%m-%d')
                    game_id = f"{sport}_{event.get('id')}"
                    
                    # Check if game exists
                    existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = ?", (game_id, sport)).fetchone()
                    
                    if existing:
                        # Update existing game
                        cursor.execute("""
                            UPDATE games
                            SET home_score = ?, away_score = ?, status = 'final'
                            WHERE sport = ?
                              AND game_id = ?
                              AND (home_score IS NULL OR home_score != ?)
                        """, (home_score, away_score, sport, game_id, home_score))
                    else:
                        # Insert new completed game
                        try:
                            cursor.execute("""
                                INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                            """, (sport, league_name or sport, game_id, 2025, game_date, home_team, away_team, home_score, away_score))
                            logger.info(f"Inserted new {sport} game: {away_team} @ {home_team} ({game_date})")
                        except Exception as insert_error:
                            logger.error(f"Error inserting {sport} game {game_id}: {insert_error}")
                    
                    if cursor.rowcount > 0:
                        updates_count += 1
                
            except Exception as e:
                logger.debug(f"Error fetching {sport} for {date_str}: {e}")
        
        conn.commit()
        conn.close()
        if updates_count > 0:
            logger.info(f"Successfully updated {updates_count} {sport} game scores.")
        else:
            logger.info(f"No {sport} score updates needed.")
        
    except Exception as e:
        logger.error(f"An error occurred while updating {sport} scores: {e}")

def update_ncaab_scores():
    """Update NCAAB scores from ESPN API"""
    update_espn_scores('NCAAB')

def update_ncaaf_scores():
    """Update NCAAF scores from ESPN API"""
    update_espn_scores('NCAAF')

def update_mlb_scores():
    """Update MLB scores from ESPN API"""
    update_espn_scores('MLB')

def update_wnba_scores():
    """Update WNBA scores from ESPN API"""
    update_espn_scores('WNBA')

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist (safe to run on every startup)."""
    conn = sqlite3.connect(DATABASE)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT, league TEXT, game_id TEXT UNIQUE,
            season INTEGER, game_date TEXT,
            home_team_id TEXT, away_team_id TEXT,
            home_score REAL, away_score REAL, status TEXT
        );
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, sport TEXT, league TEXT,
            game_date TEXT, home_team_id TEXT, away_team_id TEXT,
            elo_home_prob REAL, xgboost_home_prob REAL,
            logistic_home_prob REAL, meta_home_prob REAL,
            win_probability REAL, locked INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS site_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_date TEXT, ip_address TEXT,
            user_agent TEXT, endpoint TEXT
        );
        CREATE TABLE IF NOT EXISTS betting_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, home_moneyline REAL, away_moneyline REAL,
            spread REAL, total REAL,
            home_implied_prob REAL, away_implied_prob REAL,
            num_bookmakers INTEGER
        );
        CREATE TABLE IF NOT EXISTS engine_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT, league TEXT, game_id TEXT,
            game_date TEXT, home_team TEXT, away_team TEXT,
            home_moneyline REAL, away_moneyline REAL,
            spread REAL, total REAL,
            spread_price_home REAL, spread_price_away REAL,
            total_over_price REAL, total_under_price REAL,
            source TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS game_goalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            home_goalie TEXT, away_goalie TEXT,
            home_goalie_save_pct REAL, away_goalie_save_pct REAL,
            home_goalie_gaa REAL, away_goalie_gaa REAL
        );
        CREATE TABLE IF NOT EXISTS betting_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, spread REAL, total REAL
        );
        CREATE TABLE IF NOT EXISTS injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL,
            team_name TEXT NOT NULL,
            player_name TEXT NOT NULL,
            position TEXT,
            status TEXT,
            injury_type TEXT,
            return_date TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sport, team_name, player_name)
        );
    ''')
    conn.commit()
    conn.close()
    logger.info("Database tables initialised.")

def _ensure_engine_odds_columns():
    try:
        conn = sqlite3.connect(DATABASE)
        cols = [row[1] for row in conn.execute("PRAGMA table_info('engine_odds')").fetchall()]
        missing = {
            'spread_price_home': 'REAL',
            'spread_price_away': 'REAL',
            'total_over_price': 'REAL',
            'total_under_price': 'REAL',
        }
        for col, col_type in missing.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE engine_odds ADD COLUMN {col} {col_type}")
        conn.commit()
        conn.close()
    except Exception as _e:
        logger.debug(f"[engine_odds] column ensure failed: {_e}")


# Run on every startup — creates tables if missing, no-op if they exist
try:
    init_db()
    _ensure_engine_odds_columns()
except Exception as _dbe:
    logger.warning(f"init_db failed: {_dbe}")

def parse_date(date_str):
    """Parse date string from multiple formats (DD/MM/YYYY or YYYY-MM-DD)"""
    try:
        # Strip timestamp if present (everything after space)
        date_only = date_str.split(' ')[0] if ' ' in date_str else date_str
        
        # Try YYYY-MM-DD format first (new format)
        try:
            return datetime.strptime(date_only, '%Y-%m-%d')
        except:
            # Fall back to DD/MM/YYYY format (old format)
            return datetime.strptime(date_only, '%d/%m/%Y')
    except:
        return None

def _to_float_safe(val, default=None):
    if val is None:
        return default
    if isinstance(val, (float, int)):
        return float(val)
    if isinstance(val, bytes):
        try:
            return float(val)
        except Exception:
            try:
                import struct
                if len(val) == 8:
                    return struct.unpack('d', val)[0]
                if len(val) == 4:
                    return struct.unpack('f', val)[0]
            except Exception:
                return default
    try:
        return float(val)
    except Exception:
        return default

def _to_date_str(val):
    if not val:
        return None
    if isinstance(val, bytes):
        try:
            val = val.decode('utf-8', errors='ignore')
        except Exception:
            return None
    return str(val)

def _espn_event_date_to_local(date_str, tz_name='America/New_York'):
    """Convert ESPN event ISO date (UTC) to local game date string."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.astimezone(ZoneInfo(tz_name)).strftime('%Y-%m-%d')
    except Exception:
        return date_str[:10]

# ============================================================================
# V2 PREDICTION SYSTEM HELPER
# ============================================================================

def get_v2_prediction(sport, home_team, away_team, game_date=None):
    """
    Get predictions from the v2 system (Glicko-2 + Stacked Ensemble + Calibration)
    
    Returns dict with probabilities or None if v2 not available for this sport
    """
    model_sport = 'NCAAB' if sport == 'NCAAW' else sport
    if not HAS_V2_SYSTEM or model_sport not in V2_PREDICTORS:
        return None
    
    try:
        predictor = V2_PREDICTORS[model_sport]
        game_df = pd.DataFrame([{
            'home_team': home_team,
            'away_team': away_team,
            'date': game_date or datetime.now().strftime('%Y-%m-%d')
        }])
        
        pred = predictor.predict(game_df)
        row = pred.iloc[0]
        
        return {
            'home_prob': row['home_win_prob'],
            'away_prob': row['away_win_prob'],
            'confidence': row['confidence'],
            'model_agreement': row['model_agreement'],
            'predicted_winner': row['predicted_winner'],
            'expected_home_score': row.get('expected_home_score'),
            'expected_away_score': row.get('expected_away_score'),
            
            # Individual model probabilities for display
            'glicko2_prob': row.get('glicko2_prob'),
            'trueskill_prob': row.get('trueskill_prob'),
            'xgboost_prob': row.get('xgboost_prob'),
            
            # Ratings
            'home_glicko2': row.get('home_glicko2'),
            'away_glicko2': row.get('away_glicko2'),
            'home_trueskill_mu': row.get('home_trueskill_mu'),
            'away_trueskill_mu': row.get('away_trueskill_mu'),
            
            'is_v2': True,
        }
    except Exception as e:
        logger.warning(f"V2 prediction failed for {away_team} @ {home_team}: {e}")
        return None

# ============================================================================
# DATA LOADING FUNCTIONS
# ============================================================================

# ── Cached helpers for spread/total predictors ──────────────────────────────────
_sp_instances: dict = {}   # {sport: (ScorePredictor, timestamp)}
_sp_TTL = 3600             # re-fetch team stats at most once per hour


def _build_team_stats_from_db(sport: str) -> dict:
    """
    Compute team offense/defense PPG from completed games already in the DB.

    Used as a baseline for sports (e.g. NCAAB) where ESPN's /teams endpoint
    only covers ~30 major programs and misses hundreds of small-conference teams.
    Requires >= 3 completed games per team to produce a stat entry.
    """
    try:
        from collections import defaultdict
        conn = get_db_connection()
        rows = conn.execute(
            'SELECT home_team_id, away_team_id, home_score, away_score '
            'FROM games WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL',
            (sport,)
        ).fetchall()
        conn.close()

        totals = defaultdict(lambda: {'scored': 0.0, 'allowed': 0.0, 'games': 0})
        for row in rows:
            h, a, hs, as_ = row[0], row[1], row[2], row[3]
            if hs is None or as_ is None:
                continue
            totals[h]['scored']  += float(hs);  totals[h]['allowed'] += float(as_);  totals[h]['games'] += 1
            totals[a]['scored']  += float(as_); totals[a]['allowed'] += float(hs);  totals[a]['games'] += 1

        return {
            team: {'offense': d['scored'] / d['games'], 'defense': d['allowed'] / d['games']}
            for team, d in totals.items()
            if d['games'] >= 3  # minimum sample
        }
    except Exception as _e:
        logger.debug(f"_build_team_stats_from_db({sport}) failed: {_e}")
        return {}


def _score_predictor_instance(sport):
    """
    Return a ScorePredictor whose team_stats are cached for the day.

    Strategy:
      1. Build a baseline from completed DB games (covers ALL teams that have played).
      2. Try ESPN API (covers major-conference teams with richer season-level stats).
      3. Merge: DB is the base layer; ESPN overrides where available.

    This ensures small-conference NCAAB teams (and any sport with a large team pool)
    still get spread/total predictions even when ESPN's /teams endpoint omits them.
    """
    try:
        from score_predictor import ScorePredictor
    except ImportError:
        return None
    now = _time.time()
    cached = _sp_instances.get(sport)
    if cached and (now - cached[1]) < _sp_TTL:
        return cached[0]
    sp = ScorePredictor()
    from datetime import datetime as _dt_inner
    _cache_key = f"{sport}_{_dt_inner.now().strftime('%Y-%m-%d')}"

    # 1. DB-derived baseline (all teams with >= 3 games)
    _db_stats = _build_team_stats_from_db(sport)

    # 2. ESPN API (may be empty or partial for large leagues like NCAAB)
    try:
        _api_stats = sp.fetch_team_stats(sport)
    except Exception:
        _api_stats = {}

    # 3. Merge: DB base, ESPN overrides (ESPN data is richer for teams it covers)
    _stats = {**_db_stats, **(_api_stats or {})}

    if _stats:
        sp.team_stats_cache[_cache_key] = _stats
    _sp_instances[sport] = (sp, now)
    logger.debug(f"[{sport}] team_stats loaded: {len(_stats)} teams "
                 f"(db={len(_db_stats)}, api={len(_api_stats or {})})")
    return sp


_xgb_sport_models: dict = {}  # populated lazily; re-uses xgb_spread_model._MODEL_CACHE


def _get_xgb_spread_model(sport):
    """Build (or return cached) XGBSpreadTotalPredictor for `sport`."""
    try:
        from xgb_spread_model import get_or_train_model
    except ImportError:
        return None
    # Need completed games from DB and team stats
    try:
        team_stats = _build_team_stats_from_db(sport) or {}
        if not team_stats:
            sp = _score_predictor_instance(sport)
            if sp:
                team_stats = sp.team_stats_cache.get(
                    f"{sport}_{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}", {}
                ) or {}
        conn = get_db_connection()
        rows = conn.execute(
            'SELECT home_team_id, away_team_id, home_score, away_score, game_date '
            'FROM games WHERE sport=? AND home_score IS NOT NULL ORDER BY game_date',
            (sport,)
        ).fetchall()
        conn.close()
        games = [dict(r) for r in rows]
        if not team_stats or not games:
            return None
        return get_or_train_model(sport, games, team_stats)
    except Exception as e:
        logger.debug(f"_get_xgb_spread_model error for {sport}: {e}")
        return None


# ESPN injury endpoints keyed by sport
_INJURY_ENDPOINTS = {
    'NBA':   'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries',
    'NHL':   'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries',
    'NFL':   'https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries',
    'MLB':   'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/injuries',
    'NCAAW': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/injuries',
    'WNBA':  'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries',
}

# Only flag statuses that materially impact a team's chances
_INJURY_SHOW_STATUSES = {'Out', 'Doubtful', 'Injured Reserve', 'IR', 'Suspended'}


def _fetch_injuries(sport: str) -> dict:
    """
    Returns {team_display_name: [{name, status, reason}]} for Out/Doubtful players.
    Uses the 15-min _cached_get cache so it is only fetched once per request cycle.
    Returns {} silently on any error so a bad injury fetch never breaks predictions.
    """
    url = _INJURY_ENDPOINTS.get(sport)
    if not url:
        return {}

    def _from_db():
        try:
            conn = get_db_connection()
            rows = conn.execute('''
                SELECT team_name, player_name, status, injury_type
                FROM injuries
                WHERE sport = ?
            ''', (sport,)).fetchall()
            conn.close()
            result = {}
            for row in rows:
                status = row['status']
                if status not in _INJURY_SHOW_STATUSES:
                    continue
                team = row['team_name'] or ''
                if not team:
                    continue
                result.setdefault(team, []).append({
                    'name': row['player_name'] or '?',
                    'status': status,
                    'reason': row['injury_type'] or ''
                })
            return result
        except Exception as _db_err:
            logger.debug(f"[injuries] db fallback failed for {sport}: {_db_err}")
            return {}

    try:
        data = _cached_get(url, timeout=5)
        result = {}
        for team_group in data.get('injuries', []):
            team_name = team_group.get('displayName', '')
            players = []
            for inj in team_group.get('injuries', []):
                status = inj.get('status', '')
                if status not in _INJURY_SHOW_STATUSES:
                    continue
                athlete = inj.get('athlete', {})
                short_name = athlete.get('shortName', athlete.get('displayName', '?'))
                # Extract injury body part from shortComment e.g. "Player (knee) is out..."
                comment = inj.get('shortComment', '')
                import re as _re
                match = _re.search(r'\(([^)]{1,20})\)', comment)
                reason = match.group(1) if match else ''
                players.append({'name': short_name, 'status': status, 'reason': reason})
            if players:
                result[team_name] = players
        if result:
            return result
        return _from_db()
    except Exception as _ie:
        logger.debug(f"[injuries] fetch failed for {sport}: {_ie}")
        return _from_db()


def get_upcoming_predictions(sport, days=365):
    """Get ALL game predictions from season start - both completed and upcoming
    
    Loads games from database for all sports including NHL
    
    USER REQUIREMENT: Show ALL games from season start (Oct 7 for NHL), not just upcoming!
    """
    
    # Fast in-process cache to avoid repeated heavy prediction recomputation.
    cache_key = f"{sport}_upcoming_predictions"
    now_ts = _time.time()
    cache_ttl = _PREDICTIONS_TTL_BY_SPORT.get(sport, 180)
    cached = _PREDICTIONS_CACHE.get(cache_key)
    if cached and (now_ts - cached['ts']) < cache_ttl:
        return _copy.deepcopy(cached['data'])

    # Load game data based on sport
    if sport == 'NHL':
        # NHL: Pull from ESPN API (to get correct schedule)
        try:
            nhl_api = NHLAPI()
            # Keep NHL predictions responsive in production (avoid timeout on huge windows).
            # This route must stay below common reverse-proxy timeout budgets.
            api_games = nhl_api.get_recent_and_upcoming_games(days_back=2, days_forward=7)
            
            # For each API game, check if prediction exists in DB
            conn = get_db_connection()
            for game in api_games:
                # Try to find match in database by date and team names
                existing = conn.execute('''
                    SELECT g.game_id, p.elo_home_prob, p.xgboost_home_prob, p.meta_home_prob
                    FROM games g
                    LEFT JOIN predictions p ON g.game_id = p.game_id
                    WHERE g.sport = 'NHL' 
                      AND date(g.game_date) = date(?) 
                      AND g.home_team_id = ? 
                      AND g.away_team_id = ?
                ''', (game['game_date'], game['home_team_name'], game['away_team_name'])).fetchone()
                
                if existing:
                    game['game_id'] = existing['game_id']
                    game['stored_elo_prob'] = existing['elo_home_prob']
                    game['stored_xgb_prob'] = existing['xgboost_home_prob']
                    game['stored_ensemble_prob'] = existing['meta_home_prob']
            
            conn.close()
            
            # Build dates list from API games
            all_games_with_dates = [(parse_date(g['game_date']), g) for g in api_games if parse_date(g['game_date'])]
            all_games_with_dates.sort(key=lambda x: x[0])
        except Exception as e:
            logger.error(f"Error fetching NHL games from ESPN API: {e}")
            all_games_with_dates = []
    
    elif sport == 'SOCCER':
        # Loop -1 to +7 days so the predictions page shows multiple upcoming dates.
        # Each league needs its own request; results are cached for 15 min so
        # subsequent page loads within the TTL window are instant.
        api_games = []
        for days_offset in range(-1, 8):
            _check_date = datetime.now() + timedelta(days=days_offset)
            _date_str   = _check_date.strftime('%Y%m%d')
            for league_label, league_code in SOCCER_LEAGUE_ENDPOINTS.items():
                if not league_code:
                    continue
                url = (
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                    f"{league_code}/scoreboard?dates={_date_str}"
                )
                try:
                    data = _cached_get(url)
                except Exception as e:
                    logger.debug(f"Error fetching SOCCER {league_code} for {_date_str}: {e}")
                    continue
                league_info = (data.get('leagues', [{}])[0] or {}) if isinstance(data, dict) else {}
                league_name = _canonical_soccer_league_name(league_info.get('name')) or league_label
                events = data.get('events', []) if isinstance(data, dict) else []

                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    if len(competitors) != 2:
                        continue
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    if not home or not away:
                        continue
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    event_id  = event.get('id', '')
                    status_info  = event.get('status', {}).get('type', {})
                    status_name  = status_info.get('name', '')
                    home_score = away_score = None
                    if status_name.startswith('STATUS_FINAL'):
                        try:
                            home_score = int(home.get('score', 0))
                            away_score = int(away.get('score', 0))
                        except Exception:
                            pass
                    event_dt  = event.get('date', '')
                    game_date = _espn_event_date_to_local(event_dt) or _check_date.strftime('%Y-%m-%d')
                    api_games.append({
                        'game_id':      f"{sport}_{league_code}_{event_id}",
                        'home_team_id': home_team,
                        'away_team_id': away_team,
                        'game_date':    game_date,
                        'home_score':   home_score,
                        'away_score':   away_score,
                        'league':       league_name,
                    })

        # Enrich with stored predictions from database
        conn = get_db_connection()
        for game in api_games:
            pred = conn.execute('''
                SELECT elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                FROM predictions WHERE game_id = ? AND sport = ?
            ''', (game['game_id'], sport)).fetchone()
            if pred:
                game['stored_elo_prob']      = pred['elo_home_prob']
                game['stored_xgb_prob']      = pred['xgboost_home_prob']
                game['stored_ensemble_prob'] = pred['win_probability']
        conn.close()

        # Build dates list
        all_games_with_dates = [(parse_date(g['game_date']), g) for g in api_games if parse_date(g['game_date'])]
        all_games_with_dates.sort(key=lambda x: x[0])

        # Remove duplicates (same matchup on same date across league requests)
        seen = set()
        unique_games = []
        for date, game in all_games_with_dates:
            key = (date.strftime('%Y-%m-%d'), game['home_team_id'], game['away_team_id'])
            if key not in seen:
                seen.add(key)
                unique_games.append((date, game))
        all_games_with_dates = unique_games

        # Persist completed soccer games to DB so the results page and
        # weekly tally have data even between score-updater runs.
        try:
            _conn_soc = get_db_connection()
            _cur_soc  = _conn_soc.cursor()
            _soc_n    = 0
            for _sd, _sg in all_games_with_dates:
                if _sg.get('home_score') is not None:
                    _ex = _cur_soc.execute(
                        'SELECT 1 FROM games WHERE game_id=? AND sport=?',
                        (_sg['game_id'], sport)
                    ).fetchone()
                    if not _ex:
                        try:
                            _cur_soc.execute('''
                                INSERT INTO games
                                (sport, league, game_id, season, game_date,
                                 home_team_id, away_team_id, home_score, away_score, status)
                                VALUES (?,?,?,?,?,?,?,?,?,\'final\')
                            ''', (
                                sport,
                                _sg.get('league') or sport,
                                _sg['game_id'], 2025, _sg['game_date'],
                                _sg['home_team_id'], _sg['away_team_id'],
                                _sg['home_score'], _sg['away_score'],
                            ))
                            _soc_n += 1
                        except Exception:
                            pass
            if _soc_n > 0:
                _conn_soc.commit()
                logger.info(f"[SOCCER] stored {_soc_n} completed games in DB")
            _conn_soc.close()
        except Exception as _soc_err:
            logger.debug(f"[SOCCER] game storage failed: {_soc_err}")

    elif sport in ['NBA', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'MLB', 'WNBA']:
        # Load from ESPN API and database (includes playoffs)
        ESPN_ENDPOINTS = {
            'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
            'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
            'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
            'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
            'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
            'NCAAW': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard',
            'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
        }
        
        api_games = []

        # NBA/NFL/NCAAF need a longer forward horizon (regular season + playoffs).
        if sport in ['NBA', 'NFL', 'NCAAF']:
            # NFL/NCAAF: look back further to catch completed season + playoffs
            _lookback = 240 if sport in ('NFL', 'NCAAF') else 7
            start_str = (datetime.now() - timedelta(days=_lookback)).strftime('%Y%m%d')
            end_str = (datetime.now() + timedelta(days=120)).strftime('%Y%m%d')
            try:
                url = f"{ESPN_ENDPOINTS[sport]}?dates={start_str}-{end_str}&limit=500"
                data = _cached_get(url)
                events = data.get('events', [])
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    if len(competitors) != 2:
                        continue

                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    if not home or not away:
                        continue

                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    event_id = event.get('id', '')
                    league_name = None
                    try:
                        league_name = (
                            event.get('league', {}) or {}
                        ).get('name') or (
                            competition.get('league', {}) or {}
                        ).get('name')
                    except Exception:
                        league_name = None
                    # ESPN dates are UTC; convert to local game-day (Eastern)
                    _raw_dt = event.get('date', '')
                    game_date = _espn_event_date_to_local(_raw_dt) or datetime.now().strftime('%Y-%m-%d')

                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', 'scheduled')
                    home_score = None
                    away_score = None
                    if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                        try:
                            home_score = int(home.get('score', 0))
                            away_score = int(away.get('score', 0))
                        except:
                            pass

                    api_games.append({
                        'game_id': f"{sport}_{event_id}",
                        'home_team_id': home_team,
                        'away_team_id': away_team,
                        'game_date': game_date,
                        'home_score': home_score,
                        'away_score': away_score,
                        'league': league_name or sport,
                    })
            except Exception as e:
                logger.debug(f"Error fetching {sport} range {start_str}-{end_str}: {e}")
        else:
            # Other sports: keep shorter day-by-day window.
            for days_offset in range(-7, 15):
                check_date = datetime.now() + timedelta(days=days_offset)
                date_str = check_date.strftime('%Y%m%d')
                
                try:
                    extra_params = '&groups=50&limit=357' if sport == 'NCAAB' else ''
                    url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}{extra_params}"
                    data = _cached_get(url)
                    
                    events = data.get('events', [])
                    
                    for event in events:
                        competition = event.get('competitions', [{}])[0]
                        competitors = competition.get('competitors', [])
                        
                        if len(competitors) != 2:
                            continue
                        
                        home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                        away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                        
                        if not home or not away:
                            continue
                        
                        home_team = home.get('team', {}).get('displayName', '')
                        away_team = away.get('team', {}).get('displayName', '')
                        league_name = None
                        try:
                            league_name = (
                                event.get('league', {}) or {}
                            ).get('name') or (
                                competition.get('league', {}) or {}
                            ).get('name')
                        except Exception:
                            league_name = None
                        if sport == 'SOCCER':
                            league_name = _canonical_soccer_league_from_event(event, competition)
                            if not league_name:
                                continue
                        event_id = event.get('id', '')
                        
                        # Get status
                        status_info = event.get('status', {}).get('type', {})
                        status_name = status_info.get('name', 'scheduled')
                        
                        home_score = None
                        away_score = None
                        
                        if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                            try:
                                home_score = int(home.get('score', 0))
                                away_score = int(away.get('score', 0))
                            except:
                                pass
                        
                        event_dt = event.get('date', '')
                        game_date = _espn_event_date_to_local(event_dt) or check_date.strftime('%Y-%m-%d')
                        api_games.append({
                            'game_id': f"{sport}_{event_id}",
                            'home_team_id': home_team,
                            'away_team_id': away_team,
                            'game_date': game_date,
                            'home_score': home_score,
                            'away_score': away_score,
                            'league': league_name or sport,
                        })
                        
                except Exception as e:
                    logger.debug(f"Error fetching {sport} for {date_str}: {e}")
        
        # NFL/NCAAF fallback: if ESPN returned nothing (offseason), load from database
        if not api_games and sport in ('NFL', 'NCAAF'):
            conn = get_db_connection()
            all_games_raw = conn.execute('''
                SELECT g.*,
                       p.elo_home_prob as stored_elo_prob,
                       p.xgboost_home_prob as stored_xgb_prob,
                       p.win_probability as stored_ensemble_prob
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ?
            ''', (sport, sport)).fetchall()
            conn.close()
            for g in all_games_raw:
                gd = dict(g)
                gd['home_team_id'] = gd.get('home_team_id', '')
                gd['away_team_id'] = gd.get('away_team_id', '')
                api_games.append(gd)

        # Enrich with stored predictions from database
        conn = get_db_connection()
        for game in api_games:
            if game.get('stored_elo_prob') is not None:
                continue  # already enriched from DB fallback
            pred = conn.execute('''
                SELECT elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                FROM predictions WHERE game_id = ? AND sport = ?
            ''', (game['game_id'], sport)).fetchone()
            
            if pred:
                game['stored_elo_prob'] = pred['elo_home_prob']
                game['stored_xgb_prob'] = pred['xgboost_home_prob']
                game['stored_ensemble_prob'] = pred['win_probability']
        conn.close()
        
        # Build dates list
        all_games_with_dates = [(parse_date(g['game_date']), g) for g in api_games if parse_date(g['game_date'])]
        all_games_with_dates.sort(key=lambda x: x[0])
        
        # Remove duplicates (same matchup on same date)
        seen = set()
        unique_games = []
        for date, game in all_games_with_dates:
            key = (date.strftime('%Y-%m-%d'), game['home_team_id'], game['away_team_id'])
            if key not in seen:
                seen.add(key)
                unique_games.append((date, game))
        all_games_with_dates = unique_games

        # ── Store completed API games in DB for team stat derivation & XGB training ──
        # Without this, _build_team_stats_from_db returns empty and _get_xgb_spread_model
        # cannot train, causing missing spread/total/injury data on the predictions page.
        if sport in ('NBA', 'NFL', 'NCAAB', 'NCAAW', 'WNBA', 'MLB', 'SOCCER'):
            try:
                _conn_store = get_db_connection()
                _cur_store = _conn_store.cursor()
                _stored_n = 0
                for _sd, _sg in all_games_with_dates:
                    if _sg.get('home_score') is not None:
                        _existing = _cur_store.execute(
                            'SELECT 1 FROM games WHERE game_id=? AND sport=?',
                            (_sg['game_id'], sport)
                        ).fetchone()
                        if not _existing:
                            try:
                                _cur_store.execute('''
                                    INSERT INTO games
                                    (sport, league, game_id, season, game_date,
                                     home_team_id, away_team_id, home_score, away_score, status)
                                    VALUES (?,?,?,?,?,?,?,?,?,'final')
                                ''', (sport, _sg.get('league') or sport, _sg['game_id'], 2025,
                                      _sg['game_date'], _sg['home_team_id'],
                                      _sg['away_team_id'], _sg['home_score'],
                                      _sg['away_score']))
                                _stored_n += 1
                            except Exception:
                                pass
                if _stored_n > 0:
                    _conn_store.commit()
                    logger.info(f"[{sport}] stored {_stored_n} completed API games in DB")
                _conn_store.close()
            except Exception as _store_err:
                logger.debug(f"[{sport}] API game storage failed: {_store_err}")

    else:
        # NFL and other sports: load from database
        conn = get_db_connection()
        all_games_raw = conn.execute('''
            SELECT g.*, 
                   p.elo_home_prob as stored_elo_prob,
                   p.xgboost_home_prob as stored_xgb_prob,
                   p.win_probability as stored_ensemble_prob,
                   gg.home_goalie, gg.away_goalie,
                   gg.home_goalie_save_pct, gg.away_goalie_save_pct,
                   gg.home_goalie_gaa, gg.away_goalie_gaa,
                   bo.home_moneyline, bo.away_moneyline,
                   bo.spread, bo.total,
                   bo.home_implied_prob, bo.away_implied_prob,
                   bo.num_bookmakers
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            LEFT JOIN game_goalies gg ON g.id = gg.game_id
            LEFT JOIN (
                SELECT game_id, 
                       home_moneyline, away_moneyline, spread, total,
                       home_implied_prob, away_implied_prob, num_bookmakers
                FROM betting_odds
                GROUP BY game_id
            ) bo ON g.id = bo.game_id
            WHERE g.sport = ?
        ''', (sport, sport)).fetchall()
        all_games_raw = [dict(g) for g in all_games_raw]
        conn.close()
        
        all_games_with_dates = []
        for game in all_games_raw:
            parsed_date = parse_date(game['game_date'])
            if parsed_date:
                all_games_with_dates.append((parsed_date, game))
        all_games_with_dates.sort(key=lambda x: x[0])
    
    # Split into completed (for Elo training) and all (for predictions)
    completed_games = [g for d, g in all_games_with_dates if g.get('home_score') is not None]
    if sport == 'SOCCER':
        try:
            conn_hist = get_db_connection()
            rows = conn_hist.execute(
                'SELECT game_id, home_team_id, away_team_id, home_score, away_score, game_date '
                'FROM games WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL',
                (sport,)
            ).fetchall()
            conn_hist.close()
            history = [dict(r) for r in rows]
            if history:
                existing_ids = {g.get('game_id') for g in completed_games if g.get('game_id')}
                for g in history:
                    gid = g.get('game_id')
                    if gid and gid in existing_ids:
                        continue
                    completed_games.append(g)
        except Exception as _se:
            logger.debug(f"[SOCCER] history load failed: {_se}")
    soccer_history_count = None
    if sport == 'SOCCER':
        try:
            conn_hist = get_db_connection()
            rows = conn_hist.execute(
                'SELECT home_team_id, away_team_id, home_score, away_score, game_date '
                'FROM games WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL',
                (sport,)
            ).fetchall()
            conn_hist.close()
            history = [dict(r) for r in rows]
            if history:
                completed_games = completed_games + history
            soccer_history_count = len(history)
        except Exception as _se:
            logger.debug(f"[SOCCER] history load failed: {_se}")
            soccer_history_count = 0

    # ── NHL: inject team stats directly from completed API games ─────────────
    # The ESPN /teams endpoint doesn't expose NHL goals-per-game stats, and the
    # DB may not yet be populated (update_nhl_scores is only called on results page).
    # We already have 30 days of completed games here with real scores, so we
    # build GPG/GAPG from those and push them into the ScorePredictor cache.
    # This runs every request so the stats are always fresh, regardless of TTL.
    if sport == 'NHL' and completed_games:
        try:
            from collections import defaultdict as _dd_nhl
            _nhl_totals = _dd_nhl(lambda: {'scored': 0.0, 'allowed': 0.0, 'n': 0})
            for _cg in completed_games:
                _h  = _cg.get('home_team_id') or _cg.get('home_team_name', '')
                _a  = _cg.get('away_team_id') or _cg.get('away_team_name', '')
                _hs = _cg.get('home_score')
                _as = _cg.get('away_score')
                if _h and _a and _hs is not None and _as is not None:
                    _nhl_totals[_h]['scored']  += float(_hs)
                    _nhl_totals[_h]['allowed'] += float(_as)
                    _nhl_totals[_h]['n']       += 1
                    _nhl_totals[_a]['scored']  += float(_as)
                    _nhl_totals[_a]['allowed'] += float(_hs)
                    _nhl_totals[_a]['n']       += 1
            _nhl_api_stats = {
                t: {'offense': d['scored'] / d['n'], 'defense': d['allowed'] / d['n']}
                for t, d in _nhl_totals.items() if d['n'] >= 3
            }
            if _nhl_api_stats:
                _sp_nhl = _score_predictor_instance(sport)
                if _sp_nhl:
                    _ck_nhl = f"NHL_{datetime.now().strftime('%Y-%m-%d')}"
                    # Merge: existing richer stats take precedence; API stats fill gaps
                    _existing_nhl = _sp_nhl.team_stats_cache.get(_ck_nhl, {})
                    _sp_nhl.team_stats_cache[_ck_nhl] = {**_nhl_api_stats, **_existing_nhl}
                    logger.debug(f"[NHL] injected {len(_nhl_api_stats)} team stats from API games")
        except Exception as _nhl_stat_err:
            logger.debug(f"[NHL] team stats injection failed: {_nhl_stat_err}")

    # Train Elo system on all completed games (with home/away splits tracking)
    elo_ratings = {}
    home_away_stats = {}  # Track home/away performance
    K_FACTORS = {'NHL': 22, 'NFL': 35, 'NBA': 18, 'MLB': 14, 'NCAAF': 30, 'NCAAB': 25}
    k_factor = K_FACTORS.get(sport, 20)
    
    def get_elo(team):
        return elo_ratings.get(team, 1500)
    
    def expected_score(rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    
    def get_home_away_stats(team):
        if team not in home_away_stats:
            home_away_stats[team] = {'home_wins': 0, 'home_games': 0, 'away_wins': 0, 'away_games': 0}
        return home_away_stats[team]
    
    # Train Elo and track home/away performance
    for game in completed_games:
        home_rating = get_elo(game['home_team_id'])
        away_rating = get_elo(game['away_team_id'])
        
        expected_home = expected_score(home_rating, away_rating)
        actual_home = 1 if game['home_score'] > game['away_score'] else 0
        
        elo_ratings[game['home_team_id']] = home_rating + k_factor * (actual_home - expected_home)
        elo_ratings[game['away_team_id']] = away_rating + k_factor * ((1-actual_home) - (1-expected_home))
        
        # Track home/away splits
        home_stats = get_home_away_stats(game['home_team_id'])
        away_stats = get_home_away_stats(game['away_team_id'])
        
        home_stats['home_games'] += 1
        away_stats['away_games'] += 1
        
        if actual_home == 1:
            home_stats['home_wins'] += 1
        else:
            away_stats['away_wins'] += 1
    
    # Display logic: Show ALL past games + future games for ONE MONTH from today
    season_starts = {
        'NHL': datetime(2024, 10, 7),
        'NFL': datetime(2024, 9, 4),
        'NBA': datetime(2024, 10, 21),
        'MLB': datetime(2025, 3, 27),
        'NCAAF': datetime(2024, 8, 30),
        'NCAAB': datetime(2024, 11, 4),
        'NCAAW': datetime(2024, 11, 4),
        'WNBA': datetime(2025, 5, 14),
        'SOCCER': datetime(2024, 8, 1),
    }
    season_start = season_starts.get(sport, datetime(2025, 1, 1))
    
    # Calculate cutoff horizon by sport
    # Use module-level datetime/timedelta imports to avoid local shadowing
    today = datetime.now()
    future_window_days = {
        'NBA': 120,
    }
    future_cutoff = today + timedelta(days=future_window_days.get(sport, 30))
    
    predictions = []
    # Fetch injuries once for the whole request (15-min cache keeps it fast)
    _injuries = _fetch_injuries(sport)

    for game_date, game in all_games_with_dates:
        # Show games from season start up to one month from today
        if game_date >= season_start and game_date <= future_cutoff:
            # ============================================================
            # SOCCER MODELS + V2 PREDICTION SYSTEM
            # ============================================================
            soccer_pred = None
            soccer_note = None
            if sport == 'SOCCER':
                soccer_league = _canonical_soccer_league_name(game.get('league')) or game.get('league')
                soccer_bundle = _get_soccer_model_bundle(completed_games, soccer_league)
                if soccer_bundle and getattr(soccer_bundle, 'ready', False):
                    soccer_pred = soccer_bundle.predict(
                        game.get('home_team_id') or game.get('home_team_name'),
                        game.get('away_team_id') or game.get('away_team_name'),
                    )
                elif soccer_bundle:
                    soccer_note = soccer_bundle.reason
                else:
                    soccer_note = "Soccer models are unavailable."

            v2_pred = None
            if sport != 'SOCCER':
                v2_pred = get_v2_prediction(
                        sport, 
                        game.get('home_team_id') or game.get('home_team_name'),
                        game.get('away_team_id') or game.get('away_team_name'),
                        game.get('game_date')
                    )

            if soccer_pred:
                elo_prob = soccer_pred.get('elo_prob')
                xgb_prob = soccer_pred.get('poisson_reg_prob')
                ensemble_prob = soccer_pred.get('ensemble_prob')
                if xgb_prob is None:
                    xgb_prob = elo_prob
                if ensemble_prob is None:
                    ensemble_prob = elo_prob
                game['glicko2_prob'] = soccer_pred.get('poisson_xg_prob')
                game['trueskill_prob'] = soccer_pred.get('markov_prob')
                game['v2_expected_home'] = soccer_pred.get('expected_home_score')
                game['v2_expected_away'] = soccer_pred.get('expected_away_score')
                game['is_v2'] = True
                game['soccer_model_note'] = soccer_note
            elif v2_pred:
                # Use actual stored Elo prob from DB; fall back to Elo rating computation
                stored_elo = game.get('stored_elo_prob')
                if stored_elo is not None:
                    elo_prob = float(stored_elo)
                else:
                    home_rating = get_elo(game.get('home_team_id', ''))
                    away_rating = get_elo(game.get('away_team_id', ''))
                    elo_prob = expected_score(home_rating, away_rating)
                _xgb_raw = v2_pred.get('xgboost_prob')
                xgb_prob = _xgb_raw if _xgb_raw is not None else v2_pred['home_prob']

                # Build ensemble from individual model probs.
                # The meta-learner (v2_pred['home_prob']) frequently defaults to ~0.49
                # when team-name lookup fails, so we compute a weighted blend instead.
                _g2 = v2_pred.get('glicko2_prob')
                _ts = v2_pred.get('trueskill_prob')
                _wp = []
                if _g2       is not None: _wp.append((_g2,      0.30))
                if _ts       is not None: _wp.append((_ts,      0.30))
                if _xgb_raw  is not None: _wp.append((_xgb_raw, 0.25))
                _wp.append((elo_prob, 0.15))
                _tw = sum(w for _, w in _wp)
                ensemble_prob = sum(p * w for p, w in _wp) / _tw

                # Store model probabilities for display (Glicko-2 and TrueSkill only)
                game['glicko2_prob'] = v2_pred.get('glicko2_prob')
                game['trueskill_prob'] = v2_pred.get('trueskill_prob')
                
                # Store v2 metadata for display
                game['v2_confidence'] = v2_pred.get('confidence')
                game['v2_agreement'] = v2_pred.get('model_agreement')
                game['v2_expected_home'] = v2_pred.get('expected_home_score')
                game['v2_expected_away'] = v2_pred.get('expected_away_score')
                game['is_v2'] = True
            else:
                # Fallback to basic Elo for sports without v2
                home_rating = get_elo(game['home_team_id'])
                away_rating = get_elo(game['away_team_id'])
                elo_prob = expected_score(home_rating, away_rating)
                
                # Basic enhancements for non-v2 sports
                goalie_boost = 0.0
                if game.get('home_goalie_save_pct') and game.get('away_goalie_save_pct'):
                    save_pct_diff = float(game['home_goalie_save_pct']) - float(game['away_goalie_save_pct'])
                    goalie_boost = save_pct_diff * 0.3
                
                market_boost = 0.0
                if game.get('home_implied_prob') and game.get('away_implied_prob'):
                    market_home_prob = float(game['home_implied_prob'])
                    market_boost = (market_home_prob - 0.5) * 0.15
                
                home_stats = get_home_away_stats(game['home_team_id'])
                away_stats = get_home_away_stats(game['away_team_id'])
                home_win_pct = home_stats['home_wins'] / home_stats['home_games'] if home_stats['home_games'] > 0 else 0.5
                away_win_pct = away_stats['away_wins'] / away_stats['away_games'] if away_stats['away_games'] > 0 else 0.5
                split_boost = (home_win_pct - away_win_pct) * 0.1
                
                xgb_prob = min(0.95, max(0.05, elo_prob + goalie_boost + market_boost * 0.5 + split_boost))

                if game.get('home_implied_prob'):
                    ensemble_prob = (xgb_prob * 0.5 + elo_prob * 0.3 + float(game['home_implied_prob']) * 0.2)
                else:
                    ensemble_prob = (xgb_prob * 0.6 + elo_prob * 0.4)
                
                if sport == 'NFL':
                    ensemble_prob = elo_prob
            
            # Add predictions to game dict
            game_dict = dict(game)
            for _k in (
                'market_spread',
                'market_total',
                'home_moneyline',
                'away_moneyline',
                'spread_price_home',
                'spread_price_away',
                'total_over_price',
                'total_under_price',
                'odds_reason',
            ):
                if _k not in game_dict:
                    game_dict[_k] = None
            game_dict['elo_prob'] = round(elo_prob * 100, 1)
            game_dict['xgb_prob'] = round(xgb_prob * 100, 1)
            game_dict['ensemble_prob'] = round(ensemble_prob * 100, 1)
            game_dict['predicted_winner'] = game['home_team_id'] if ensemble_prob > 0.5 else game['away_team_id']
            
            # Ensure date has no time in GUI
            from datetime import datetime as _dt
            game_dict['game_date'] = _dt.strftime(game_date, '%Y-%m-%d')
            
            # Add V2 metadata
            home_stats = get_home_away_stats(game['home_team_id'])
            away_stats = get_home_away_stats(game['away_team_id'])
            home_win_pct = home_stats['home_wins'] / home_stats['home_games'] if home_stats['home_games'] > 0 else 0.5
            away_win_pct = away_stats['away_wins'] / away_stats['away_games'] if away_stats['away_games'] > 0 else 0.5
            game_dict['has_goalie_data'] = bool(game.get('home_goalie_save_pct'))
            game_dict['has_odds_data'] = bool(game.get('home_implied_prob'))
            game_dict['home_win_pct_home'] = round(home_win_pct * 100, 1)
            game_dict['away_win_pct_away'] = round(away_win_pct * 100, 1)
            
            # V2 model metadata (Glicko-2 + Stacked Ensemble)
            game_dict['is_v2'] = game.get('is_v2', False)
            game_dict['v2_confidence'] = game.get('v2_confidence')
            game_dict['v2_agreement'] = game.get('v2_agreement')
            game_dict['v2_expected_home'] = game.get('v2_expected_home')
            game_dict['v2_expected_away'] = game.get('v2_expected_away')
            
            # Individual model probabilities - ALWAYS pass through
            _g2 = game.get('glicko2_prob')
            _ts = game.get('trueskill_prob')
            game_dict['glicko2_prob'] = round(_g2 * 100, 1) if _g2 is not None else None
            game_dict['trueskill_prob'] = round(_ts * 100, 1) if _ts is not None else None
            if sport == 'SOCCER':
                if game_dict['glicko2_prob'] is None or game_dict['trueskill_prob'] is None:
                    game_dict['model_data_note'] = soccer_note or (
                        "Soccer model outputs are unavailable for this matchup."
                    )
                else:
                    game_dict['model_data_note'] = None
            else:
                game_dict['model_data_note'] = None

            # ── Spread / Total predictions ───────────────────────────────────
            # Naive formula (ScorePredictor) and XGBoost model
            # These are only computed for upcoming games (no final score yet)
            game_dict['naive_home_score'] = None
            game_dict['naive_away_score'] = None
            game_dict['naive_spread'] = None
            game_dict['naive_total'] = None
            game_dict['xgb_home_score'] = None
            game_dict['xgb_away_score'] = None
            game_dict['xgb_spread'] = None
            game_dict['xgb_total'] = None
            # Puck-line (NHL) or raw-spread (other sports) display fields
            game_dict['puck_line_fav_prob'] = None
            game_dict['puck_line_dog_prob'] = None
            game_dict['puck_line_tag']      = None
            game_dict['puck_line_fav_side'] = None
            game_dict['spread_total_note']  = None

            if game_dict.get('home_score') is None:  # upcoming game only
                if sport == 'SOCCER':
                    if soccer_pred and soccer_pred.get('expected_home_score') is not None:
                        exp_home = soccer_pred.get('expected_home_score')
                        exp_away = soccer_pred.get('expected_away_score')
                        if exp_home is not None and exp_away is not None:
                            game_dict['naive_home_score'] = round(exp_home, 2)
                            game_dict['naive_away_score'] = round(exp_away, 2)
                            game_dict['naive_spread'] = round(exp_home - exp_away, 2)
                            game_dict['naive_total'] = round(exp_home + exp_away, 2)
                    if game_dict.get('naive_spread') is None:
                        try:
                            _sp = _score_predictor_instance(sport)
                            if _sp:
                                nh, na, ns, nt = _sp.predict_score(
                                    game_dict.get('home_team_id', ''),
                                    game_dict.get('away_team_id', ''),
                                    sport,
                                )
                                if nh is not None:
                                    game_dict['naive_home_score'] = nh
                                    game_dict['naive_away_score'] = na
                                    game_dict['naive_spread'] = ns
                                    game_dict['naive_total'] = nt
                        except Exception as _e:
                            logger.debug(f"ScorePredictor error: {_e}")
                    if game_dict.get('naive_spread') is None:
                        game_dict['spread_total_note'] = soccer_note or (
                            "Soccer spread/total requires team scoring rates; data not ready yet."
                        )
                else:
                    try:
                        from score_predictor import ScorePredictor
                        _sp = _score_predictor_instance(sport)
                        if _sp:
                            nh, na, ns, nt = _sp.predict_score(
                                game_dict.get('home_team_id', ''),
                                game_dict.get('away_team_id', ''),
                                sport,
                            )
                            if nh is not None:
                                game_dict['naive_home_score'] = nh
                                game_dict['naive_away_score'] = na
                                game_dict['naive_spread'] = ns
                                game_dict['naive_total'] = nt
                    except Exception as _e:
                        logger.debug(f"ScorePredictor error: {_e}")

                    # Fallback to Vegas-style predictor if naive stats are still missing
                    if game_dict.get('naive_spread') is None:
                        try:
                            from vegas_score_predictor import VegasScorePredictor
                            _vsp = VegasScorePredictor(db_path=DATABASE)
                            vh, va, vs, vt = _vsp.predict_score_vegas_method(
                                game_dict.get('home_team_id', ''),
                                game_dict.get('away_team_id', ''),
                                sport
                            )
                            if vh is not None:
                                game_dict['naive_home_score'] = vh
                                game_dict['naive_away_score'] = va
                                game_dict['naive_spread'] = vs
                                game_dict['naive_total'] = vt
                        except Exception as _ve:
                            logger.debug(f"VegasScorePredictor error: {_ve}")

                try:
                    _xm = _get_xgb_spread_model(sport)
                    if _xm:
                        result = _xm.predict(
                            game_dict.get('home_team_id', ''),
                            game_dict.get('away_team_id', ''),
                        )
                        if result and result[0] is not None:
                            game_dict['xgb_home_score'] = result[0]
                            game_dict['xgb_away_score'] = result[1]
                            game_dict['xgb_spread'] = result[2]
                            game_dict['xgb_total'] = result[3]
                except Exception as _e:
                    logger.debug(f"XGBSpread error: {_e}")

                # ── MLB: pitching-enhanced prediction (35% Elo / 45% Pitching / 20% ML) ─
                if sport == 'MLB':
                    try:
                        from mlb_runs_model import get_or_train_mlb_model as _get_mlb_model
                        from mlb_pitching import get_mlb_pitching_adjustment as _get_pitching
                        import math as _math
                        _ht = game_dict.get('home_team_id', '')
                        _at = game_dict.get('away_team_id', '')
                        # 1. ML correction: runs model spread → probability
                        _ml_prob = 0.5
                        _mlbm = _get_mlb_model(DATABASE)
                        if _mlbm:
                            _mlb_result = _mlbm.predict(_ht, _at)
                            if _mlb_result and _mlb_result[0] is not None:
                                game_dict['xgb_home_score'] = _mlb_result[0]
                                game_dict['xgb_away_score'] = _mlb_result[1]
                                game_dict['xgb_spread']     = _mlb_result[2]
                                game_dict['xgb_total']      = _mlb_result[3]
                                _mlb_spread = float(_mlb_result[2])
                                _ml_prob = 0.5 * (1.0 + _math.erf(_mlb_spread / (3.0 * _math.sqrt(2))))
                        # 2. Pitching adjustment from ESPN probable starters
                        _pitch = _get_pitching(_ht, _at)
                        _pitch_prob = _pitch.get('pitching_prob', 0.5)
                        # 3. Elo baseline (already computed above as elo_prob 0-1)
                        _elo_base = elo_prob  # from v2 predictor, already 0-1 scale
                        # 4. Blend: 35% Elo + 45% Pitching + 20% ML
                        _blended = 0.35 * _elo_base + 0.45 * _pitch_prob + 0.20 * _ml_prob
                        _blended = max(0.05, min(0.95, _blended))
                        # Override all model display slots
                        game_dict['elo_prob']       = round(_elo_base * 100, 1)
                        game_dict['xgb_prob']       = round(_ml_prob * 100, 1)
                        game_dict['glicko2_prob']   = round(_pitch_prob * 100, 1)
                        game_dict['trueskill_prob'] = round(_pitch_prob * 100, 1)
                        game_dict['ensemble_prob']  = round(_blended * 100, 1)
                        game_dict['predicted_winner'] = _ht if _blended > 0.5 else _at
                    except Exception as _mlbe:
                        logger.debug(f"MLB enhanced prediction error: {_mlbe}")

                # ── NHL: invert XSharp spread (model picks opposite side) ──────────
                if sport == 'NHL' and game_dict.get('xgb_spread') is not None:
                    game_dict['xgb_spread'] = -game_dict['xgb_spread']
                    if game_dict.get('xgb_home_score') is not None and game_dict.get('xgb_away_score') is not None:
                        _tmp = game_dict['xgb_home_score']
                        game_dict['xgb_home_score'] = game_dict['xgb_away_score']
                        game_dict['xgb_away_score'] = _tmp

                # ── NHL: convert XSharp spread → puck-line cover probabilities ──────────
                # puck_line_* fields are the betting-facing output shown in the UI.
                if sport == 'NHL' and game_dict.get('xgb_spread') is not None:
                    try:
                        _pl = compute_puck_line_prob(game_dict['xgb_spread'], sport)
                        game_dict.update(_pl)
                    except Exception as _ple:
                        logger.debug(f"[NHL] puck_line_prob error: {_ple}")

            # ── Injury warnings (upcoming games only) ─────────────────────────
            if game_dict.get('home_score') is None:
                _ht = game_dict.get('home_team_id', '')
                _at = game_dict.get('away_team_id', '')
                game_dict['home_injuries'] = _injuries.get(_ht, [])
                game_dict['away_injuries'] = _injuries.get(_at, [])
            else:
                game_dict['home_injuries'] = []
                game_dict['away_injuries'] = []

            predictions.append(game_dict)
    
    _attach_engine_odds_to_predictions(sport, predictions, limit=40)

    # Soccer: when the odds engine has no spread line, fall back to the model's
    # naive spread/total so the predictions page shows our own line instead of
    # "no sportsbook spread line found".
    if sport == 'SOCCER':
        for _sp_pred in predictions:
            if _sp_pred.get('home_score') is not None:
                continue  # completed game — skip
            if _sp_pred.get('market_spread') is None:
                _fb_spread = _sp_pred.get('naive_spread') or _sp_pred.get('xgb_spread')
                if _fb_spread is not None:
                    _sp_pred['market_spread'] = round(float(_fb_spread), 2)
            if _sp_pred.get('market_total') is None:
                _fb_total = _sp_pred.get('naive_total') or _sp_pred.get('xgb_total')
                if _fb_total is not None:
                    _sp_pred['market_total'] = round(float(_fb_total), 2)

    # For NBA/MLB/NCAAW/SOCCER: Save newly generated predictions to database so Results page can use them
    if sport in ['NBA', 'MLB', 'NCAAW', 'SOCCER']:
        _cache_market_lines_for_predictions(sport, predictions, limit=20)
        conn_save = get_db_connection()
        cursor_save = conn_save.cursor()
        saved_count = 0
        
        for pred in predictions:
            # Only save if game has game_id and no scores yet (not played)
            if pred.get('game_id') and pred.get('home_score') is None:
                # Check if prediction already exists
                existing = cursor_save.execute('''
                    SELECT id FROM predictions WHERE game_id = ? AND sport = ?
                ''', (pred['game_id'], sport)).fetchone()
                
                if not existing:
                    # Save new prediction (locked by default when first saved)
                    try:
                        cursor_save.execute('''
                            INSERT INTO predictions (
                                game_id, sport, league, game_date, home_team_id, away_team_id,
                                elo_home_prob, xgboost_home_prob, win_probability, locked
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ''', (
                            pred['game_id'], sport, pred.get('league') or sport, pred['game_date'],
                            pred['home_team_id'], pred['away_team_id'],
                            pred['elo_prob'] / 100.0,
                            pred['xgb_prob'] / 100.0,
                            pred['ensemble_prob'] / 100.0
                        ))
                        saved_count += 1
                    except Exception as e:
                        logger.error(f"Error saving prediction for {pred['game_id']}: {e}")
        
        if saved_count > 0:
            conn_save.commit()
            logger.info(f"Saved {saved_count} new {sport} predictions to database")
        conn_save.close()
    
    _PREDICTIONS_CACHE[cache_key] = {'ts': _time.time(), 'data': _copy.deepcopy(predictions)}
    return predictions

def _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=None):
    """Weighted blend matching get_upcoming_predictions weights.
    Avoids v2['home_prob'] which defaults to ~0.49 when team names fail lookup."""
    _wp = []
    if glicko2_prob   is not None: _wp.append((glicko2_prob,   0.30))
    if trueskill_prob is not None: _wp.append((trueskill_prob, 0.30))
    if xgb_prob       is not None: _wp.append((xgb_prob,       0.25))
    if elo_prob       is not None: _wp.append((elo_prob,       0.15))
    _tw = sum(w for _, w in _wp)
    return sum(p * w for p, w in _wp) / _tw if _tw > 0 else fallback


def calculate_nfl_weekly_performance():
    """Calculate NFL model performance week by week using actual stored predictions
    
    Gets completed games and results from nfl_data_py API,
    then looks up predictions from database.
    """
    try:
        # Fetch 2025 NFL schedule with results from API - this is the source of truth
        schedule = nfl.import_schedules([2025])
        
        if schedule.empty:
            return None
        
        # Filter to completed games only (games with results)
        completed_games = schedule[schedule['result'].notna()].copy()
        
        if completed_games.empty:
            return None
        
        # Get database connection for predictions
        conn = get_db_connection()
        
        # Team abbreviation to full name mapping
        abbr_to_full = {
            'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens',
            'BUF': 'Buffalo Bills', 'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears',
            'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns', 'DAL': 'Dallas Cowboys',
            'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
            'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars',
            'KC': 'Kansas City Chiefs', 'LV': 'Las Vegas Raiders', 'LAC': 'Los Angeles Chargers',
            'LAR': 'Los Angeles Rams', 'LA': 'Los Angeles Rams', 'MIA': 'Miami Dolphins',
            'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints',
            'NYG': 'New York Giants', 'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles',
            'PIT': 'Pittsburgh Steelers', 'SF': 'San Francisco 49ers', 'SEA': 'Seattle Seahawks',
            'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders'
        }
        
        weekly_results = {}

        # Process each completed game from API
        for _, api_game in completed_games.iterrows():
            week = int(api_game['week'])
            game_id = api_game['game_id']

            # Look up stored predictions from database
            pred = conn.execute('''
                SELECT p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM predictions p
                WHERE p.game_id = ? AND p.sport = 'NFL'
            ''', (game_id,)).fetchone()

            if not pred or pred[0] is None:
                continue

            # Get team full names
            home_team_full = abbr_to_full.get(api_game['home_team'], api_game['home_team'])
            away_team_full = abbr_to_full.get(api_game['away_team'], api_game['away_team'])

            # Stored DB predictions
            elo_prob = float(pred[0]) if pred[0] else None
            xgb_prob = float(pred[1]) if pred[1] else elo_prob
            ens_prob = elo_prob  # start with elo as fallback

            # V2 model predictions
            v2 = get_v2_prediction('NFL', home_team_full, away_team_full, str(api_game['gameday']))
            glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
            trueskill_prob = v2.get('trueskill_prob') if v2 else None
            if v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
                ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

            actual_home_win = api_game['home_score'] > api_game['away_score']

            if week not in weekly_results:
                weekly_results[week] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   > 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob > 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       > 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       > 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       > 0.5) == actual_home_win if ens_prob       is not None else None

            for model, prob, correct in [
                ('glicko2',   glicko2_prob,   glicko2_correct),
                ('trueskill', trueskill_prob, trueskill_correct),
                ('elo',       elo_prob,       elo_correct),
                ('xgboost',   xgb_prob,       xgb_correct),
                ('ensemble',  ens_prob,       ens_correct),
            ]:
                if prob is not None:
                    weekly_results[week][model]['total'] += 1
                    if correct:
                        weekly_results[week][model]['correct'] += 1

            weekly_results[week]['games'].append({
                'game_id':          game_id,
                'date':             str(api_game['gameday']),
                'away':             away_team_full,
                'home':             home_team_full,
                'away_score':       int(api_game['away_score']),
                'home_score':       int(api_game['home_score']),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob       * 100, 1) if elo_prob       is not None else None,
                'xgb_prob':         round(xgb_prob       * 100, 1) if xgb_prob       is not None else None,
                'ens_prob':         round(ens_prob       * 100, 1) if ens_prob       is not None else None,
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       ens_correct,
            })

        conn.close()

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )

        return weekly_results
        
    except Exception as e:
        logger.error(f"Error calculating NFL weekly performance: {e}")
        return None

def calculate_nhl_weekly_performance():
    """Calculate NHL model performance week by week
    
    Uses data from database since NHL doesn't have a simple API like nfl_data_py.
    Returns a consistent sample of the most recent fully-graded games so every
    model is compared over the same set.
    """
    try:
        from datetime import datetime, timedelta
        conn = get_db_connection()

        # Build the NHL results page from a consistent recent sample.
        target_games = 200
        candidate_games = 320

        # Get recent completed NHL games through yesterday only.
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        games = conn.execute('''
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.meta_home_prob
            FROM games g
            LEFT JOIN predictions p ON (p.sport = 'NHL' AND (p.game_id = g.game_id OR 
                (date(p.game_date) = date(g.game_date) AND p.home_team_id = g.home_team_id AND p.away_team_id = g.away_team_id)))
            WHERE g.sport = 'NHL'
              AND g.season = 2025 
              AND g.home_score IS NOT NULL
              AND date(g.game_date) <= ?
            ORDER BY g.game_date DESC
            LIMIT ?
        ''', (yesterday, candidate_games)).fetchall()
        conn.close()
        
        if not games:
            return None
        weekly_results = {}
        included_games = 0
        for game in games:
            game_date = parse_date(game['game_date'])
            if not game_date:
                continue

            # Extract stored predictions first (fast path)
            elo_prob = float(game['elo_home_prob']) if game['elo_home_prob'] is not None else None
            xgb_prob = (
                float(game['xgboost_home_prob'])
                if game['xgboost_home_prob'] is not None
                else None
            )
            meta_prob = (
                float(game['meta_home_prob'])
                if game['meta_home_prob'] is not None
                else None
            )

            if elo_prob is None and xgb_prob is None and meta_prob is None:
                continue
            if elo_prob is None:
                elo_prob = meta_prob if meta_prob is not None else xgb_prob
            # Compute v2 predictions for the selected recent window only.
            # Only compute for recent games where v2 data matters most.
            glicko2_prob = None
            trueskill_prob = None
            v2 = None
            try:
                v2 = get_v2_prediction('NHL', game['home_team_id'], game['away_team_id'], game['game_date'])
                glicko2_prob = v2.get('glicko2_prob') if v2 else None
                trueskill_prob = v2.get('trueskill_prob') if v2 else None
            except Exception:
                pass

            if xgb_prob is None and v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
            if xgb_prob is None:
                xgb_prob = elo_prob
            if meta_prob is None:
                meta_prob = _compute_ensemble_prob(
                    glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=elo_prob
                )

            # Require full model availability so every card uses the same sample.
            if any(prob is None for prob in [glicko2_prob, trueskill_prob, elo_prob, xgb_prob, meta_prob]):
                continue

            actual_home_win = game['home_score'] > game['away_score']
            bucket = game['game_date'].split()[0]

            if bucket not in weekly_results:
                weekly_results[bucket] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   > 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob > 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       > 0.5) == actual_home_win
            xgb_correct       = (xgb_prob       > 0.5) == actual_home_win
            meta_correct      = (meta_prob      > 0.5) == actual_home_win
            weekly_results[bucket]['elo']['total'] += 1
            if elo_correct: weekly_results[bucket]['elo']['correct'] += 1

            weekly_results[bucket]['xgboost']['total'] += 1
            if xgb_correct: weekly_results[bucket]['xgboost']['correct'] += 1

            weekly_results[bucket]['ensemble']['total'] += 1
            if meta_correct: weekly_results[bucket]['ensemble']['correct'] += 1

            weekly_results[bucket]['glicko2']['total'] += 1
            if glicko2_correct: weekly_results[bucket]['glicko2']['correct'] += 1

            weekly_results[bucket]['trueskill']['total'] += 1
            if trueskill_correct: weekly_results[bucket]['trueskill']['correct'] += 1

            weekly_results[bucket]['games'].append({
                'game_id':         game['game_id'],
                'date':             game['game_date'].split()[0],
                'away':             game['away_team_id'],
                'home':             game['home_team_id'],
                'away_score':       int(game['away_score']),
                'home_score':       int(game['home_score']),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob  * 100, 1),
                'xgb_prob':         round(xgb_prob  * 100, 1),
                'ens_prob':         round(meta_prob * 100, 1),
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       meta_correct,
            })
            included_games += 1
            if included_games >= target_games:
                break

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )
        return weekly_results
    except Exception as e:
        logger.error(f"Error calculating NHL weekly performance: {e}")
        return None

def calculate_nba_weekly_performance():
    """Calculate NBA model performance week by week using v2 model predictions."""
    def to_float(val):
        if val is None:
            return None
        if isinstance(val, (float, int)):
            return float(val)
        if isinstance(val, bytes):
            try:
                import struct
                if len(val) == 8:
                    return struct.unpack('d', val)[0]
                elif len(val) == 4:
                    return struct.unpack('f', val)[0]
            except:
                pass
            return None
        try:
            return float(val)
        except:
            return None

    try:
        conn = get_db_connection()
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        games = conn.execute('''
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
            FROM games g
            LEFT JOIN predictions p
              ON p.sport = 'NBA' AND (
                   p.game_id = g.game_id
                   OR (
                        date(p.game_date) = date(g.game_date)
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                   )
              )
            WHERE g.sport = 'NBA'
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) <= ?
            ORDER BY g.game_date
        ''', (yesterday,)).fetchall()
        conn.close()

        if not games:
            return None

        first_game_date = parse_date(games[0]['game_date'])
        season_start = first_game_date if first_game_date else datetime(2025, 10, 21)
        weekly_results = {}

        for game in games:
            game_date = parse_date(game['game_date'])
            if not game_date:
                continue

            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game['home_score']
            away_score = game['away_score']

            if home_score is None or away_score is None:
                continue

            days_since_start = (game_date - season_start).days
            week = (days_since_start // 7) + 1

            # Stored DB predictions
            elo_prob  = to_float(game['elo_home_prob'])
            xgb_prob  = to_float(game['xgboost_home_prob']) or elo_prob
            ens_prob  = to_float(game['win_probability']) or elo_prob

            # V2 model predictions (Glicko-2, TrueSkill)
            v2 = get_v2_prediction('NBA', home_team, away_team, game['game_date'])
            glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
            trueskill_prob = v2.get('trueskill_prob') if v2 else None
            if v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
                ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

            actual_home_win = home_score > away_score

            if week not in weekly_results:
                weekly_results[week] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   > 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob > 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       > 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       > 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       > 0.5) == actual_home_win if ens_prob       is not None else None

            for model, prob, correct in [
                ('glicko2',   glicko2_prob,   glicko2_correct),
                ('trueskill', trueskill_prob, trueskill_correct),
                ('elo',       elo_prob,       elo_correct),
                ('xgboost',   xgb_prob,       xgb_correct),
                ('ensemble',  ens_prob,       ens_correct),
            ]:
                if prob is not None:
                    weekly_results[week][model]['total'] += 1
                    if correct:
                        weekly_results[week][model]['correct'] += 1

            weekly_results[week]['games'].append({
                'game_id':         game['game_id'],
                'date':             game['game_date'].split()[0],
                'away':             away_team,
                'home':             home_team,
                'away_score':       int(away_score),
                'home_score':       int(home_score),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob  * 100, 1) if elo_prob  is not None else None,
                'xgb_prob':         round(xgb_prob  * 100, 1) if xgb_prob  is not None else None,
                'ens_prob':         round(ens_prob  * 100, 1) if ens_prob  is not None else None,
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       ens_correct,
            })

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )

        return weekly_results

    except Exception as e:
        logger.error(f"Error calculating NBA weekly performance: {e}")
        return None

def calculate_model_performance(sport):
    """Calculate overall performance per model using stored DB predictions + v2 live inference."""
    conn = get_db_connection()
    results_data = conn.execute('''
        SELECT
            g.game_date, g.home_team_id, g.away_team_id,
            g.away_score, g.home_score,
            p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob,
            p.win_probability as ensemble_prob
        FROM games g
        LEFT JOIN predictions p ON
            g.sport = p.sport AND
            g.game_date = p.game_date AND
            g.home_team_id = p.home_team_id AND
            g.away_team_id = p.away_team_id
        WHERE g.sport = ? AND g.home_score IS NOT NULL
        ORDER BY g.game_date ASC
    ''', (sport,)).fetchall()
    conn.close()

    if len(results_data) == 0:
        return None

    models_list = ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']
    results = {m: {'correct': 0, 'total': 0} for m in models_list}
    dates = []

    def to_float(val):
        if val is None:
            return None
        if isinstance(val, (float, int)):
            return float(val)
        if isinstance(val, bytes):
            try:
                import struct
                if len(val) == 8:
                    return struct.unpack('d', val)[0]
                elif len(val) == 4:
                    return struct.unpack('f', val)[0]
                return float(val.decode('utf-8', errors='ignore'))
            except:
                return None
        try:
            return float(val)
        except:
            return None

    for row in results_data:
        home_score = to_float(row[4])
        away_score = to_float(row[3])
        if home_score is None or away_score is None:
            continue
        actual_home_win = home_score > away_score

        # Stored DB probs
        elo_prob = to_float(row[5])
        xgb_prob = to_float(row[6])
        ens_prob = to_float(row[8])

        # V2 live inference
        v2 = get_v2_prediction(sport, row[1], row[2], row[0])
        glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
        trueskill_prob = v2.get('trueskill_prob') if v2 else None
        if v2:
            xgb_prob = v2.get('xgboost_prob', xgb_prob)
            ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

        for model, prob in [
            ('glicko2',   glicko2_prob),
            ('trueskill', trueskill_prob),
            ('elo',       elo_prob),
            ('xgboost',   xgb_prob),
            ('ensemble',  ens_prob),
        ]:
            if prob is not None:
                results[model]['total'] += 1
                if (prob > 0.5) == actual_home_win:
                    results[model]['correct'] += 1

        dates.append(parse_date(row[0]))

    performance = {}
    for model in models_list:
        total = results[model]['total']
        performance[model] = {
            'accuracy': round(results[model]['correct'] / total * 100, 1) if total > 0 else 0.0,
            'correct':  results[model]['correct'],
            'total':    total
        }
    valid_dates = [d for d in dates if d is not None]
    performance['date_range'] = (
        f"{min(valid_dates).strftime('%d/%m/%Y')} - {max(valid_dates).strftime('%d/%m/%Y')}"
        if valid_dates else '—'
    )
    performance['total_games'] = len(results_data)
    return performance


# Sport-specific O/U benchmarks (season average game totals)
_OU_BENCH = {'NBA': 226.0, 'NHL': 6.1, 'NCAAB': 145.0, 'NCAAW': 140.0, 'NCAAF': 56.0, 'MLB': 9.0, 'NFL': 47.0, 'WNBA': 158.0}


def _compute_spread_total_for_daily(sport, daily_results):
    """Compute XSharp spread/total grading for games already in daily_results (in-place).
    Returns aggregate stats dict or None if the XGB model is unavailable."""
    try:
        _xgb = _get_xgb_spread_model(sport)
        _sp = None
        if not _xgb:
            if sport in ['NBA', 'MLB']:
                _sp = _score_predictor_instance(sport)
            if not _sp:
                return None

        conn = get_db_connection()
        _line_by_key = {}
        _line_by_id = {}
        try:
            cols = [r['name'] for r in conn.execute("PRAGMA table_info('betting_lines')").fetchall()]
            has_extra = any(c in cols for c in ['sport', 'game_date', 'home_team', 'away_team'])
        except Exception:
            cols = []
            has_extra = False

        try:
            if has_extra:
                rows = conn.execute('''
                    SELECT game_id, game_date, home_team, away_team, spread, total, fetched_at
                    FROM betting_lines
                    WHERE sport=?
                    ORDER BY fetched_at DESC
                ''', (sport,)).fetchall()
            else:
                rows = conn.execute('''
                    SELECT game_id, spread, total
                    FROM betting_lines
                ''').fetchall()
            for r in rows:
                if r['game_id']:
                    _line_by_id[str(r['game_id'])] = {'spread': r['spread'], 'total': r['total']}
                if has_extra:
                    gd = (r['game_date'] or '')[:10]
                    hk = _normalize_team_key_for_sport(sport, r['home_team'])
                    ak = _normalize_team_key_for_sport(sport, r['away_team'])
                    key = (gd, hk, ak)
                    if gd and hk and ak and key not in _line_by_key:
                        _line_by_key[key] = {'spread': r['spread'], 'total': r['total']}
        except Exception:
            pass

        # Betting odds fallback (game_id may be stored as numeric or text)
        try:
            odds_rows = conn.execute('SELECT game_id, spread, total FROM betting_odds').fetchall()
            for r in odds_rows:
                if r['game_id'] is None:
                    continue
                _line_by_id[str(r['game_id'])] = {
                    'spread': r['spread'],
                    'total': r['total'],
                }
        except Exception:
            pass
        conn.close()

        st_cov = st_gr = tt_cor = tt_gr = 0
        live_attempts = 0
        live_cap = 10 if sport == 'NBA' else 5
        for dd in daily_results.values():
            for g in dd.get('games', []):
                h, a = g['home'], g['away']
                gd = g['date']
                gid = str(g.get('game_id') or '')
                hs, as_ = g['home_score'], g['away_score']

                try:
                    if _xgb:
                        xp = _xgb.predict(h, a)
                        xs = round(float(xp[2]), 1) if xp and xp[2] is not None else None
                        xt = round(float(xp[3]), 1) if xp and xp[3] is not None else None
                    elif _sp:
                        nh, na, ns, nt = _sp.predict_score(h, a, sport)
                        xs = round(float(ns), 1) if ns is not None else None
                        xt = round(float(nt), 1) if nt is not None else None
                    else:
                        xs = xt = None
                except Exception:
                    xs = xt = None

                hk = _normalize_team_key_for_sport(sport, h)
                ak = _normalize_team_key_for_sport(sport, a)
                ms = g.get('market_spread')
                mt = g.get('market_total')
                ml = _line_by_id.get(gid) or _line_by_key.get((gd, hk, ak), {})
                if (not ml) and sport == 'NBA' and gd:
                    try:
                        _dt = parse_date(gd)
                    except Exception:
                        _dt = None
                    if _dt:
                        for _offset in (-1, 1):
                            alt = (_dt + timedelta(days=_offset)).strftime('%Y-%m-%d')
                            ml = _line_by_key.get((alt, hk, ak), {})
                            if ml:
                                break
                if ms is None:
                    try:
                        ms = float(ml['spread']) if ml.get('spread') is not None else None
                    except Exception:
                        ms = None
                if mt is None:
                    try:
                        mt = float(ml['total']) if ml.get('total') is not None else None
                    except Exception:
                        mt = None

                # Live fallback for missing market lines (recent games only)
                if (ms is None or mt is None) and live_attempts < live_cap and gd:
                    try:
                        if sport == 'NBA':
                            gd_dt = parse_date(gd)
                            if gd_dt and abs((datetime.now() - gd_dt).days) > 3:
                                raise Exception("skip live fetch for older NBA dates")
                        live_attempts += 1
                        live_line = _fetch_live_market_line(sport, gid, gd, h, a)
                        if live_line:
                            if ms is None:
                                ms = live_line.get('spread')
                            if mt is None:
                                mt = live_line.get('total')
                            if sport == 'NBA' and (ms is not None or mt is not None):
                                try:
                                    _conn_line = get_db_connection()
                                    _upsert_betting_line(_conn_line, sport, gid, gd, h, a, ms, mt, live_line.get('source'))
                                    _conn_line.commit()
                                    _conn_line.close()
                                except Exception:
                                    pass
                    except Exception:
                        pass

                am = hs - as_
                at = hs + as_

                sp_disp = sp_ok = None
                tp_disp = tp_ok = None
                g['market_spread_reason'] = None
                g['market_total_reason'] = None
                g['spread_pick_reason'] = None
                g['total_pick_reason'] = None

                if sport == 'MLB':
                    run_line = 1.5
                    g['market_spread_label'] = "Run Line ±1.5"
                    g['market_spread'] = None

                    if xs is None:
                        g['spread_pick_reason'] = "model score unavailable"
                    else:
                        if xs >= run_line:
                            pick_team = h
                            pick_line = -run_line
                        elif xs <= -run_line:
                            pick_team = a
                            pick_line = -run_line
                        else:
                            pick_team = a if xs > 0 else h
                            pick_line = run_line
                        sp_disp = 'HOME' if pick_team == h else 'AWAY'
                        g['spread_pick_label'] = f"{pick_team} {pick_line:+.1f}"
                        if hs is not None and as_ is not None:
                            if pick_team == h:
                                if pick_line < 0:
                                    sp_ok = am > run_line
                                else:
                                    sp_ok = am >= -run_line
                            else:
                                if pick_line < 0:
                                    sp_ok = am < -run_line
                                else:
                                    sp_ok = am <= run_line
                            st_gr += 1
                            if sp_ok:
                                st_cov += 1

                    if mt is None and xt is not None:
                        mt = xt
                        g['market_total_reason'] = "XSharp total (fallback)"
                        g['market_total'] = mt
                        g['total_pick_label'] = f"XSharp {mt:.1f}"
                        g['total_pick_reason'] = "fallback line"
                    else:
                        g['market_total'] = mt
                        if mt is None:
                            g['market_total_reason'] = "no sportsbook total line found"
                            g['total_pick_reason'] = "no sportsbook total line"
                        elif xt is None:
                            g['total_pick_reason'] = "model score unavailable"
                        else:
                            if abs(xt - mt) < 1e-9:
                                tp_disp = 'PUSH'
                            else:
                                tp_disp = 'OVER' if xt > mt else 'UNDER'
                                if abs(at - mt) >= 1e-9:
                                    aou = 'OVER' if at > mt else 'UNDER'
                                    tp_ok = (tp_disp == aou)
                                    tt_gr += 1
                                    if tp_ok:
                                        tt_cor += 1
                            if tp_disp in ('OVER', 'UNDER'):
                                g['total_pick_label'] = f"{tp_disp.title()} {mt:.1f}"
                            elif tp_disp == 'PUSH':
                                g['total_pick_label'] = "PUSH"

                else:
                    g['market_spread'] = ms
                    g['market_total'] = mt
                    if ms is None:
                        g['market_spread_reason'] = "no sportsbook spread line found"
                    if mt is None:
                        g['market_total_reason'] = "no sportsbook total line found"

                    if xs is not None and ms is not None:
                        dm = xs + ms
                        da = am + ms
                        if abs(dm) < 1e-9:
                            sp_disp = 'PUSH'
                        elif abs(da) < 1e-9:
                            sp_disp = 'HOME' if dm > 0 else 'AWAY'
                        else:
                            m_side = 'HOME' if dm > 0 else 'AWAY'
                            a_side = 'HOME' if da > 0 else 'AWAY'
                            sp_disp = m_side
                            sp_ok = (m_side == a_side)
                            st_gr += 1
                            if sp_ok:
                                st_cov += 1
                    elif xs is None:
                        g['spread_pick_reason'] = "model score unavailable"

                    if xt is not None and mt is not None:
                        if abs(xt - mt) < 1e-9:
                            tp_disp = 'PUSH'
                        else:
                            tp_disp = 'OVER' if xt > mt else 'UNDER'
                            if abs(at - mt) >= 1e-9:
                                aou = 'OVER' if at > mt else 'UNDER'
                                tp_ok = (tp_disp == aou)
                                tt_gr += 1
                                if tp_ok:
                                    tt_cor += 1
                    elif xt is None:
                        g['total_pick_reason'] = "model score unavailable"

                    # Display-ready strings for the unified table
                    g['spread_pick_label'] = None
                    if sp_disp in ('HOME', 'AWAY') and ms is not None:
                        g['spread_line_display'] = f"{ms:+.1f}" if sp_disp == 'HOME' else f"{-ms:+.1f}"
                        pick_team = h if sp_disp == 'HOME' else a
                        g['spread_pick_label'] = f"{pick_team} {g['spread_line_display']}"
                    else:
                        g['spread_line_display'] = None
                    g['total_pick_label'] = None
                    if tp_disp in ('OVER', 'UNDER') and mt is not None:
                        g['total_line_display'] = f"{tp_disp.title()} {mt:.1f}"
                        g['total_pick_label'] = g['total_line_display']
                    elif tp_disp == 'PUSH':
                        g['total_pick_label'] = "PUSH"
                    else:
                        g['total_line_display'] = None

                g['spread_pick'] = sp_disp
                g['spread_correct'] = sp_ok
                g['total_pick'] = tp_disp
                g['total_correct'] = tp_ok

        return {
            'spread_covered': st_cov,
            'spread_graded': st_gr,
            'spread_pct': round(st_cov / st_gr * 100, 1) if st_gr > 0 else 0,
            'total_correct': tt_cor,
            'total_graded': tt_gr,
            'total_pct': round(tt_cor / tt_gr * 100, 1) if tt_gr > 0 else 0,
        }
    except Exception as e:
        logger.debug(f"[{sport}] spread/total integration skipped: {e}")
        return None


def _ou_stats(daily_results, sport):
    """Compute over/under counts from daily_results game scores vs sport benchmark."""
    bench = _OU_BENCH.get(sport, 0)
    total_over = total_under = total_games_ou = total_score_sum = 0
    for dd in daily_results.values():
        for g in dd.get('games', []):
            tot = (g.get('away_score') or 0) + (g.get('home_score') or 0)
            if tot > 0:
                total_games_ou += 1
                total_score_sum += tot
                if tot > bench:
                    total_over += 1
                else:
                    total_under += 1
    avg_total = round(total_score_sum / total_games_ou, 1) if total_games_ou > 0 else 0
    return total_over, total_under, total_games_ou, avg_total, bench


def compute_overall_stats_from_daily(daily_results):
    """Compute per-model totals from a daily_results dict (used by DAILY_RESULTS_TEMPLATE).
    
    All models show stats over the SAME games - only games where ALL models
    have predictions are counted. This ensures fair comparison.
    """
    model_configs = [
        ('glicko2',   'glicko2_correct', 'glicko2_prob'),
        ('trueskill', 'trueskill_correct', 'trueskill_prob'),
        ('elo',       'elo_correct', 'elo_prob'),
        ('xgboost',   'xgb_correct', 'xgb_prob'),
        ('ensemble',  'ens_correct', 'ens_prob'),
    ]
    overall = {m: {'correct': 0, 'total': 0} for m, _, _ in model_configs}
    
    for date_data in daily_results.values():
        for game in date_data.get('games', []):
            if game.get('skip_grading'):
                continue
            # Only count games where ALL models have probability data
            # This ensures fair comparison across all models
            all_models_have_data = all(
                game.get(prob_key) is not None 
                for _, _, prob_key in model_configs
            )
            
            if not all_models_have_data:
                continue  # Skip games where any model is missing data
            
            # Now count this game for ALL models (fair comparison)
            for model_name, correct_key, prob_key in model_configs:
                correct_val = game.get(correct_key)
                overall[model_name]['total'] += 1
                if correct_val:
                    overall[model_name]['correct'] += 1
    
    for model_name, _, _ in model_configs:
        t = overall[model_name]['total']
        c = overall[model_name]['correct']
        overall[model_name]['accuracy'] = (
            round(c / t * 100, 1) if t > 0 else 0.0
        )
    return overall


def _tally_spread_total(games):
    """Compute spread and O/U records from a list of games."""
    spread = {'correct': 0, 'total': 0, 'pushes': 0}
    total = {'correct': 0, 'total': 0, 'pushes': 0}
    for g in games:
        if g.get('skip_grading'):
            continue
        # Spread
        sp = g.get('spread_correct')
        sp_pick = g.get('spread_pick')
        if sp_pick == 'PUSH':
            spread['pushes'] += 1
        elif sp is not None:
            spread['total'] += 1
            if sp:
                spread['correct'] += 1
        # Total (O/U)
        tp = g.get('total_correct')
        tp_pick = g.get('total_pick')
        if tp_pick == 'PUSH':
            total['pushes'] += 1
        elif tp is not None:
            total['total'] += 1
            if tp:
                total['correct'] += 1
    for d in [spread, total]:
        d['accuracy'] = round(d['correct'] / d['total'] * 100, 1) if d['total'] > 0 else 0.0
    return spread, total


def compute_daily_model_tally(daily_results, target_date):
    """Compute per-model correct/total + spread/total for a single date."""
    if not daily_results or not target_date:
        return None
    day_bucket = daily_results.get(target_date)
    if not day_bucket or not day_bucket.get('games'):
        return None
    model_configs = [
        ('glicko2',   'glicko2_correct', 'glicko2_prob'),
        ('trueskill', 'trueskill_correct', 'trueskill_prob'),
        ('elo',       'elo_correct', 'elo_prob'),
        ('xgboost',   'xgb_correct', 'xgb_prob'),
        ('ensemble',  'ens_correct', 'ens_prob'),
    ]
    tally = {m: {'correct': 0, 'total': 0} for m, _, _ in model_configs}
    for game in day_bucket.get('games', []):
        if game.get('skip_grading'):
            continue
        for model_name, correct_key, prob_key in model_configs:
            if game.get(prob_key) is None:
                continue
            tally[model_name]['total'] += 1
            if game.get(correct_key):
                tally[model_name]['correct'] += 1
    for model_name, _, _ in model_configs:
        t = tally[model_name]['total']
        c = tally[model_name]['correct']
        tally[model_name]['accuracy'] = round(c / t * 100, 1) if t > 0 else 0.0
    tally['games'] = len(day_bucket.get('games', []))
    # Add spread + O/U tally
    sp, ou = _tally_spread_total(day_bucket.get('games', []))
    tally['spread'] = sp
    tally['total_ou'] = ou
    return tally


def compute_daily_model_tally_from_weekly(weekly_results, target_date):
    """Compute per-model tally for a date using weekly_results structure (NFL)."""
    if not weekly_results or not target_date:
        return None
    daily_results = {target_date: {'games': []}}
    for week_data in weekly_results.values():
        for game in week_data.get('games', []):
            if game.get('date') == target_date:
                daily_results[target_date]['games'].append(game)
    return compute_daily_model_tally(daily_results, target_date)


def _date_in_range(date_str, start_date, end_date):
    try:
        d = parse_date(date_str)
    except Exception:
        d = None
    if not d:
        return False
    if start_date and d < start_date:
        return False
    if end_date and d > end_date:
        return False
    return True

def compute_model_tally_for_range(daily_results, start_date=None, end_date=None):
    model_configs = [
        ('glicko2',   'glicko2_correct', 'glicko2_prob'),
        ('trueskill', 'trueskill_correct', 'trueskill_prob'),
        ('elo',       'elo_correct', 'elo_prob'),
        ('xgboost',   'xgb_correct', 'xgb_prob'),
        ('ensemble',  'ens_correct', 'ens_prob'),
    ]
    tally = {m: {'correct': 0, 'total': 0} for m, _, _ in model_configs}
    total_games = 0
    all_games = []
    for date_key, day_data in daily_results.items():
        if not _date_in_range(date_key, start_date, end_date):
            continue
        games = day_data.get('games', [])
        total_games += len(games)
        all_games.extend(games)
        for game in games:
            if game.get('skip_grading'):
                continue
            for model_name, correct_key, prob_key in model_configs:
                if game.get(prob_key) is None:
                    continue
                tally[model_name]['total'] += 1
                if game.get(correct_key):
                    tally[model_name]['correct'] += 1
    for model_name, _, _ in model_configs:
        t = tally[model_name]['total']
        c = tally[model_name]['correct']
        tally[model_name]['accuracy'] = round(c / t * 100, 1) if t > 0 else 0.0
    tally['games'] = total_games
    # Add spread + O/U tally
    sp, ou = _tally_spread_total(all_games)
    tally['spread'] = sp
    tally['total_ou'] = ou
    return tally


def _roi_entry():
    return {
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "units_won": 0.0,
        "units_risked": 0,
        "graded": 0,
        "missing_odds": 0,
        "roi_pct": None,
        "reason": None,
    }

def compute_roi_for_range(daily_results, start_date=None, end_date=None):
    summary = {
        "moneyline": _roi_entry(),
        "spread": _roi_entry(),
        "total": _roi_entry(),
    }
    for date_key, day_data in daily_results.items():
        if not _date_in_range(date_key, start_date, end_date):
            continue
        for g in day_data.get("games", []):
            if g.get("skip_grading"):
                continue
            home_score = g.get("home_score")
            away_score = g.get("away_score")
            if home_score is None or away_score is None:
                continue

            # Moneyline ROI based on ensemble win prob
            ens_prob = g.get("ens_prob")
            if ens_prob is not None:
                pick_home = ens_prob >= 50
                home_win = home_score > away_score
                if home_score == away_score:
                    home_win = None
                odds = g.get("home_moneyline") if pick_home else g.get("away_moneyline")
                entry = summary["moneyline"]
                if home_win is None:
                    entry["pushes"] += 1
                elif odds is None:
                    entry["missing_odds"] += 1
                else:
                    units = _american_units(odds)
                    if units is None:
                        entry["missing_odds"] += 1
                    else:
                        entry["units_risked"] += 1
                        entry["graded"] += 1
                        if (pick_home and home_win) or ((not pick_home) and (not home_win)):
                            entry["wins"] += 1
                            entry["units_won"] += units
                        else:
                            entry["losses"] += 1
                            entry["units_won"] -= 1

            # Spread ROI based on xSharp pick/grade
            spread_pick = g.get("spread_pick")
            spread_correct = g.get("spread_correct")
            if spread_pick and spread_pick != "PUSH" and spread_correct is not None:
                entry = summary["spread"]
                if spread_pick == "HOME":
                    odds = g.get("spread_price_home")
                else:
                    odds = g.get("spread_price_away")
                if odds is None:
                    entry["missing_odds"] += 1
                else:
                    units = _american_units(odds)
                    if units is None:
                        entry["missing_odds"] += 1
                    else:
                        entry["units_risked"] += 1
                        entry["graded"] += 1
                        if spread_correct is True:
                            entry["wins"] += 1
                            entry["units_won"] += units
                        else:
                            entry["losses"] += 1
                            entry["units_won"] -= 1
            elif spread_pick == "PUSH":
                summary["spread"]["pushes"] += 1

            # Total ROI based on xSharp pick/grade
            total_pick = g.get("total_pick")
            total_correct = g.get("total_correct")
            if total_pick and total_pick != "PUSH" and total_correct is not None:
                entry = summary["total"]
                if total_pick == "OVER":
                    odds = g.get("total_over_price")
                else:
                    odds = g.get("total_under_price")
                if odds is None:
                    entry["missing_odds"] += 1
                else:
                    units = _american_units(odds)
                    if units is None:
                        entry["missing_odds"] += 1
                    else:
                        entry["units_risked"] += 1
                        entry["graded"] += 1
                        if total_correct is True:
                            entry["wins"] += 1
                            entry["units_won"] += units
                        else:
                            entry["losses"] += 1
                            entry["units_won"] -= 1
            elif total_pick == "PUSH":
                summary["total"]["pushes"] += 1
    for entry in summary.values():
        if entry["units_risked"] > 0:
            entry["roi_pct"] = round((entry["units_won"] / entry["units_risked"]) * 100, 2)
        else:
            if entry["graded"] == 0:
                entry["reason"] = "No graded bets in range."
            elif entry["missing_odds"] > 0:
                entry["reason"] = "Odds missing for graded bets."
    return summary

def build_roi_cards(roi_daily, roi_weekly, roi_total):
    def _format_entry(entry):
        if not entry:
            return {"roi": "—", "detail": "—"}
        if entry.get("roi_pct") is None:
            return {"roi": "—", "detail": entry.get("reason") or "—"}
        units = entry.get("units_won", 0.0)
        wins = entry.get("wins", 0)
        losses = entry.get("losses", 0)
        pushes = entry.get("pushes", 0)
        return {
            "roi": f"{entry['roi_pct']}%",
            "detail": f"{wins}-{losses}-{pushes}, {units:+.2f}u",
        }
    return {
        "moneyline": {
            "daily": _format_entry(roi_daily.get("moneyline") if roi_daily else None),
            "weekly": _format_entry(roi_weekly.get("moneyline") if roi_weekly else None),
            "total": _format_entry(roi_total.get("moneyline") if roi_total else None),
        },
        "spread": {
            "daily": _format_entry(roi_daily.get("spread") if roi_daily else None),
            "weekly": _format_entry(roi_weekly.get("spread") if roi_weekly else None),
            "total": _format_entry(roi_total.get("spread") if roi_total else None),
        },
        "total": {
            "daily": _format_entry(roi_daily.get("total") if roi_daily else None),
            "weekly": _format_entry(roi_weekly.get("total") if roi_weekly else None),
            "total": _format_entry(roi_total.get("total") if roi_total else None),
        },
    }


def compute_overall_stats_from_weekly(weekly_results):
    """Compute per-model totals from a weekly_results dict (used by NFL_WEEKLY_RESULTS_TEMPLATE)."""
    models = ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']
    overall = {m: {'correct': 0, 'total': 0} for m in models}
    for week_data in weekly_results.values():
        for model in models:
            if model in week_data:
                overall[model]['correct'] += week_data[model].get('correct', 0)
                overall[model]['total']   += week_data[model].get('total', 0)
    for model in models:
        t = overall[model]['total']
        overall[model]['accuracy'] = (
            round(overall[model]['correct'] / t * 100, 1) if t > 0 else 0.0
        )
    return overall


# ============================================================================
# BASE TEMPLATE
# ============================================================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {% if page_title is defined and page_title %}{% set _meta_title = page_title %}
    {% elif sport_info is defined %}{% set _meta_title = sport_info.name ~ ' — underdogs.bet' %}
    {% else %}{% set _meta_title = 'underdogs.bet' %}{% endif %}
    {% if page_description is defined and page_description %}{% set _meta_desc = page_description %}
    {% elif sport_info is defined %}{% set _meta_desc = sport_info.name ~ ' predictions, results, spreads, and totals powered by AI.' %}
    {% else %}{% set _meta_desc = 'AI-powered sports predictions for NHL, NBA, NFL, MLB, NCAAB, NCAAW, NCAAF, WNBA, and Soccer.' %}{% endif %}
    {% if page_image is defined and page_image %}{% set _meta_image = page_image %}
    {% else %}{% set _meta_image = request.url_root.rstrip('/') ~ '/static/Logo.PNG' %}{% endif %}
    <title>{{ _meta_title }}</title>
    <meta name="description" content="{{ _meta_desc }}">
    <meta property="og:title" content="{{ _meta_title }}">
    <meta property="og:description" content="{{ _meta_desc }}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://www.underdogs.bet{{ request.path }}">
    <meta property="og:image" content="https://www.underdogs.bet/static/Logo.PNG">
    <meta property="og:site_name" content="underdogs.bet">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{{ _meta_title }}">
    <meta name="twitter:description" content="{{ _meta_desc }}">
    <meta name="twitter:image" content="https://www.underdogs.bet/static/Logo.PNG">
    <link rel="canonical" href="https://www.underdogs.bet{{ request.path }}">
    <link rel="icon" type="image/png" href="/static/Logo.PNG">
    <link rel="apple-touch-icon" href="/static/Logo.PNG">
    {% if ga_tracking_id %}
    <!-- Google Analytics gtag.js snippet -->
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-JWHPL9X6SY">
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-JWHPL9X6SY');
    </script>
    {% endif %}
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "underdogs.bet",
      "url": "https://www.underdogs.bet",
      "logo": "https://www.underdogs.bet/static/Logo.PNG",
      "sameAs": [
        "https://x.com/underdogs_bet",
        "https://instagram.com/underdogs.bet",
        "https://facebook.com/underdogs.bet",
        "https://tiktok.com/@underdog.bet",
        "https://youtube.com/@Underdogsbet"
      ]
    }
    </script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #fff;
            min-height: 100vh;
        }
        .navbar {
            background: rgba(7, 10, 20, 0.35);
            padding: 14px 28px;
            border-bottom: none;
            box-shadow: none;
            backdrop-filter: blur(16px);
            position: sticky;
            top: 0;
            z-index: 1000;
        }
        .navbar-content {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
        }
        .logo-img {
            height: 64px;
            width: auto;
            display: block;
        }
        .hamburger {
            display: flex;
            flex-direction: column;
            cursor: pointer;
            gap: 5px;
            padding: 7px;
            border-radius: 10px;
            background: transparent;
            border: none;
        }
        .hamburger:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        .hamburger span {
            width: 24px;
            height: 2px;
            background: #cbd5e1;
            border-radius: 2px;
            transition: 0.3s;
        }
        .nav-links {
            position: absolute;
            top: 64px;
            right: 22px;
            background: rgba(7, 10, 20, 0.98);
            flex-direction: column;
            gap: 2px;
            padding: 10px;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px;
            display: none;
            min-width: 200px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.4);
        }
        .nav-links.active { display: flex; }
        .nav-links a {
            color: #fff;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s;
            white-space: nowrap;
        }
        .nav-section-title { display: none; }
        .nav-divider { display: none; }
        .nav-section-title {
            font-size: 0.65em;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: #64748b;
            padding: 6px 8px;
        }
        .nav-divider {
            height: 1px;
            background: rgba(255, 255, 255, 0.1);
            margin: 6px 0;
        }
        .nav-links a:hover {
            color: #fff;
        }
        .nav-links a.active {
            color: #fff;
        }
        .nav-donate-btn {
            background: linear-gradient(135deg, #fbbf24, #f59e0b);
            color: #fff !important;
            font-weight: 800 !important;
            padding: 8px 14px;
            border-radius: 10px;
            transition: opacity 0.2s !important;
            white-space: nowrap;
        }
        .nav-donate-btn:hover { opacity: 0.9; color: #fff !important; }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
        }
        .site-footer {
            background: rgba(7, 10, 20, 0.4);
            backdrop-filter: blur(16px);
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            padding: 18px 30px;
            color: #94a3b8;
            font-size: 0.78em;
        }
        .footer-inner {
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            flex-wrap: wrap;
        }
        .footer-left { display: flex; align-items: center; gap: 14px; }
        .footer-logo-img { height: 32px; width: auto; }
        .footer-email a { color: #94a3b8; text-decoration: none; font-size: 0.95em; }
        .footer-email a:hover { color: #fff; }
        .footer-center { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .footer-center a { color: #94a3b8; text-decoration: none; font-size: 0.95em; }
        .footer-center a:hover { color: #fff; }
        .footer-center span { color: rgba(255,255,255,0.2); }
        .footer-right { color: #64748b; font-size: 0.9em; white-space: nowrap; }
        .footer-socials { display: flex; align-items: center; gap: 14px; }
        .footer-socials a { display: flex; opacity: 0.6; transition: opacity 0.2s; }
        .footer-socials a:hover { opacity: 1; }
        @media (max-width: 700px) {
            .footer-inner { flex-direction: column; align-items: center; text-align: center; gap: 12px; }
        }
        @media (max-width: 768px) {
            .nav-links {
                left: 0;
                right: 0;
                top: 70px;
                padding: 20px;
                border-radius: 0;
                border-left: none;
                border-right: none;
                border-bottom: 2px solid #334155;
            }
            .nav-links a, .nav-links .nav-group-title {
                padding: 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
            .container {
                padding: 20px 15px;
            }
        }
        /* Nav dropdown groups */
        .nav-group { position: relative; }
        .nav-group-title { color: #fbbf24; font-weight: 700; cursor: pointer; padding: 8px 10px; border-radius: 8px; display: block; font-size: 0.88em; }
        .nav-group-title:hover { background: rgba(255,255,255,0.08); }
        .nav-group-items { display: none; padding-left: 12px; }
        .nav-group:hover .nav-group-items, .nav-group.open .nav-group-items { display: flex; flex-direction: column; }
        .nav-group-items a { font-size: 0.84em; padding: 6px 10px !important; opacity: 0.9; }
        .nav-group-items a:hover { opacity: 1; color: #fbbf24; }
        {% block extra_styles %}{% endblock %}
    </style>
</head>
<body>
    <div class="navbar">
        <div class="navbar-content">
            <a href="/" class="logo">
                <img src="/static/Logo.PNG" alt="underdogs.bet" class="logo-img">
            </a>
            <div class="hamburger" onclick="toggleMenu()">
                <span></span>
                <span></span>
                <span></span>
            </div>
            <div class="nav-links" id="navLinks">
                <a href="/" class="{{ 'active' if page == 'home' else '' }}">Home</a>
                <div class="nav-group" onclick="this.classList.toggle('open')">
                    <span class="nav-group-title">🏆 Join</span>
                    <div class="nav-group-items">
                        <a href="/plans" style="color:#fbbf24;">🏆 Premium</a>
                        {% if is_logged_in %}
                        <a href="/logout">Logout</a>
                        {% else %}
                        <a href="/login" style="color:#10b981;">Login</a>
                        <a href="/signup">Sign Up</a>
                        {% endif %}
                    </div>
                </div>
                <div class="nav-group" onclick="this.classList.toggle('open')">
                    <span class="nav-group-title">🏀 Sports</span>
                    <div class="nav-group-items">
                        <a href="/nhl-picks">🏒 NHL</a>
                        <a href="/nba-picks">🏀 NBA</a>
                        <a href="/mlb-picks">⚾ MLB</a>
                        <a href="/nfl-picks">🏈 NFL</a>
                        <a href="/ncaab-picks">🎓 NCAAB</a>
                        <a href="/ncaaw-picks">🏀 NCAAW</a>
                        <a href="/ncaaf-picks">🏟️ NCAAF</a>
                        <a href="/wnba-picks">🏀 WNBA</a>
                        {% if soccer_enabled %}
                        <a href="/soccer-picks">⚽ Soccer</a>
                        {% endif %}
                    </div>
                </div>
                <div class="nav-group" onclick="this.classList.toggle('open')">
                    <span class="nav-group-title" style="color:#cbd5e1;">Resources</span>
                    <div class="nav-group-items">
                        <a href="/tutorial">Tutorial</a>
                        <a href="/privacy">Privacy</a>
                        <a href="/terms">Terms</a>
                    </div>
                </div>
                <div class="nav-group" onclick="this.classList.toggle('open')">
                    <span class="nav-group-title" style="color:#cbd5e1;">Socials</span>
                    <div class="nav-group-items">
                        <a href="https://x.com/underdogs_bet" target="_blank">X</a>
                        <a href="https://instagram.com/underdogs.bet" target="_blank">Instagram</a>
                        <a href="https://facebook.com/underdogs.bet" target="_blank">Facebook</a>
                        <a href="https://tiktok.com/@underdog.bet" target="_blank">TikTok</a>
                        <a href="https://youtube.com/@Underdogsbet" target="_blank">YouTube</a>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <div class="container">
        {% block content %}{% endblock %}
    </div>
    <footer class="site-footer">
        <div class="footer-inner">
            <div class="footer-left">
                <a href="/"><img src="/static/Logo.PNG" alt="underdogs.bet" class="footer-logo-img"></a>
                <div class="footer-email"><a href="mailto:{{ contact_email }}">{{ contact_email }}</a></div>
            </div>
            <div class="footer-center">
                <a href="/tutorial">Tutorial</a><span>&middot;</span>
                <a href="/privacy">Privacy</a><span>&middot;</span>
                <a href="/terms">Terms</a>
            </div>
            <div class="footer-socials">
                <a href="https://x.com/underdogs_bet" target="_blank" rel="noopener" title="X"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></a>
                <a href="https://instagram.com/underdogs.bet" target="_blank" rel="noopener" title="Instagram"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg></a>
                <a href="https://facebook.com/underdogs.bet" target="_blank" rel="noopener" title="Facebook"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg></a>
                <a href="https://tiktok.com/@underdog.bet" target="_blank" rel="noopener" title="TikTok"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg></a>
                <a href="https://youtube.com/@Underdogsbet" target="_blank" rel="noopener" title="YouTube"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg></a>
            </div>
            <div class="footer-right">&copy; 2026 underdogs.bet. ALL RIGHTS RESERVED.</div>
        </div>
    </footer>
    
    <script>
        function toggleMenu() {
            const navLinks = document.getElementById('navLinks');
            navLinks.classList.toggle('active');
        }
        
        // Close menu when clicking a link
        document.addEventListener('DOMContentLoaded', function() {
            const navLinks = document.getElementById('navLinks');
            const links = navLinks.querySelectorAll('a');
            links.forEach(link => {
                link.addEventListener('click', function() {
                    navLinks.classList.remove('active');
                });
            });
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', function(event) {
            const navLinks = document.getElementById('navLinks');
            const hamburger = document.querySelector('.hamburger');
            const navbar = document.querySelector('.navbar');
            
            // If click is outside navbar entirely, close menu
            if (!navbar.contains(event.target)) {
                navLinks.classList.remove('active');
            }
        });
    </script>
</body>
</html>
"""

TUTORIAL_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
        body{background:url('/static/felix-yu-Ii7adwWwNh4-unsplash.jpg') center/cover no-repeat fixed !important;}
        body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.85);z-index:0;}
        body>*{position:relative;z-index:1;}
        .tutorial-wrap{max-width:900px;margin:0 auto;padding:20px 0 60px;}
        .tutorial-card{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:24px;margin-bottom:18px;}
        .tutorial-card h1{font-size:2em;margin-bottom:8px;}
        .tutorial-card h2{font-size:1.35em;margin:6px 0 8px;}
        .tutorial-card p{color:#cbd5e1;line-height:1.7;}
        .tutorial-card ul{margin:8px 0 0 20px;color:#cbd5e1;line-height:1.7;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="tutorial-wrap">
        <div class="tutorial-card">
            <h1>📊 How to Read Our Picks</h1>
            <p>Each game card shows our AI predictions. Here’s what each section means.</p>
        </div>

        <div class="tutorial-card">
            <h2>🏒 Game Card Layout</h2>
            <ul>
                <li><strong>Top team</strong> = Away team</li>
                <li><strong>Bottom team</strong> = Home team</li>
                <li>The <strong>▶ arrow</strong> next to a team = our consensus moneyline pick</li>
                <li>Moneyline odds appear next to team names when available</li>
            </ul>
        </div>

        <div class="tutorial-card">
            <h2>📈 Home Win %</h2>
            <p>The five model percentages all represent the <strong>home team’s win probability</strong>.</p>
            <ul>
                <li><strong>Grinder2</strong> = Glicko-2 model</li>
                <li><strong>Takedown</strong> = TrueSkill model</li>
                <li><strong>Edge</strong> = Elo model</li>
                <li><strong>XSharp</strong> = XGBoost model</li>
                <li><strong>Sharp Consensus</strong> = weighted blend of all models</li>
            </ul>
            <p>If the number is above 50%, the models favor the home team. If it’s below 50%, they favor the away team.</p>
        </div>

        <div class="tutorial-card">
            <h2>🔒 Premium Picks</h2>
            <p>Free users get moneyline picks and win percentages. Premium unlocks:</p>
            <ul>
                <li><strong>XSharp Score</strong> = predicted final score</li>
                <li><strong>XSharp Spread</strong> = model spread projection</li>
                <li><strong>XSharp Total</strong> = model total projection</li>
                <li><strong>Our Spread / Our Total</strong> = calibrated market-style lines</li>
            </ul>
        </div>

        <div class="tutorial-card">
            <h2>📉 NHL Puck Line</h2>
            <p>For hockey, spreads are shown as puck line probabilities:</p>
            <ul>
                <li><strong>-1.5</strong> = favorite must win by 2+</li>
                <li><strong>+1.5</strong> = underdog can lose by 1 and still cover</li>
                <li><strong>STRONG</strong> = 55%+ confidence</li>
                <li><strong>LEAN</strong> = 52–55% confidence</li>
            </ul>
        </div>

        <div class="tutorial-card">
            <h2>⚠️ Analysis / Injuries</h2>
            <p>Open the <strong>Analysis</strong> section under a game card to see important injury info for both teams.</p>
        </div>

        <div class="tutorial-card">
            <h2>📅 Navigation</h2>
            <ul>
                <li>Use the date bubbles at the top to jump between dates</li>
                <li>Switch between <strong>Predictions</strong> and <strong>Results</strong></li>
                <li>The results page tracks how each model performed on completed games</li>
            </ul>
        </div>
    </div>
""")

# ============================================================================
# DAILY REPORT TEMPLATE (marketing / proof-of-performance)
# ============================================================================

DAILY_REPORT_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    body{background:url('/static/IMG_2695.jpeg') center/cover no-repeat fixed !important;}
    body::before{content:'';position:fixed;inset:0;background:rgba(7,10,20,0.88);z-index:0;}
    body>*{position:relative;z-index:1;}
    @media(max-width:768px){body{background-attachment:scroll !important;}}
    .rpt-wrap{max-width:760px;margin:0 auto;padding:10px 0 60px;}
    .rpt-header{text-align:center;margin-bottom:28px;}
    .rpt-header h1{font-size:1.8em;margin-bottom:6px;}
    .rpt-header .rpt-date{color:#fbbf24;font-size:1.15em;font-weight:700;}
    .rpt-header .rpt-sub{color:#94a3b8;font-size:0.9em;margin-top:6px;}
    .rpt-sport-block{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:14px;padding:20px;margin-bottom:16px;}
    .rpt-sport-title{font-size:1.1em;font-weight:800;color:#fff;margin-bottom:14px;text-align:center;}
    .rpt-sport-title span{color:#fbbf24;}
    .rpt-cat-label{font-size:0.72em;text-transform:uppercase;letter-spacing:0.5px;color:#94a3b8;text-align:center;margin:12px 0 6px;font-weight:600;}
    .rpt-cat-label:first-child{margin-top:0;}
    .rpt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;}
    .rpt-card{background:rgba(255,255,255,0.06);border-radius:10px;padding:10px 6px;text-align:center;}
    .rpt-card.hl{border:2px solid #fbbf24;}
    .rpt-model{font-size:0.72em;opacity:0.85;margin-bottom:3px;}
    .rpt-acc{font-size:1.35em;font-weight:800;}
    .rpt-acc.g{color:#10b981;}.rpt-acc.y{color:#fbbf24;}.rpt-acc.r{color:#ef4444;}.rpt-acc.x{color:#94a3b8;}
    .rpt-rec{font-size:0.78em;opacity:0.8;}
    .rpt-sou-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
    .rpt-total{text-align:center;font-size:0.9em;color:#94a3b8;margin-bottom:18px;}
    .rpt-total strong{color:#fff;font-size:1.1em;}
    .rpt-actions{display:flex;gap:10px;justify-content:center;margin-top:28px;flex-wrap:wrap;}
    .rpt-btn{padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:700;font-size:0.88em;transition:all 0.2s;display:inline-flex;align-items:center;gap:7px;border:none;}
    .rpt-btn:hover{opacity:0.85;transform:translateY(-1px);}
    .rpt-btn-x{background:#000;color:#fff;border:1px solid rgba(255,255,255,0.2);}
    .rpt-btn-fb{background:#1877f2;color:#fff;}
    .rpt-btn-ig{background:linear-gradient(45deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);color:#fff;}
    .rpt-btn-tk{background:#000;color:#fff;border:1px solid rgba(255,255,255,0.2);}
    .rpt-btn-copy{background:rgba(255,255,255,0.1);color:#fff;border:1px solid rgba(255,255,255,0.2);cursor:pointer;}
    .rpt-btn-copy.copied{background:#10b981;border-color:#10b981;}
    .rpt-btn-cta{background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;}
    .rpt-share-row{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-bottom:12px;}
    .rpt-cta-row{display:flex;justify-content:center;}
    .rpt-sharing{font-size:0.78em;color:#94a3b8;text-align:center;margin-top:6px;}
    @media(max-width:500px){.rpt-grid{grid-template-columns:repeat(3,1fr);}.rpt-acc{font-size:1.1em;}.rpt-sou-row{grid-template-columns:1fr;}}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="rpt-wrap" id="reportCapture">
        <div class="rpt-header">
            <h1>Daily Betting Results Report</h1>
            <div class="rpt-date">{{ report_display }}</div>
            <div class="rpt-sub">All results tracked, transparent, and verified.</div>
        </div>

        <div class="rpt-total">Games Graded: <strong>{{ total_games }}</strong></div>

        {% if total_games == 0 %}
        <div style="text-align:center;padding:40px;opacity:0.7;">No completed games found for this date.</div>
        {% else %}

        {% for st in sport_tallies %}
        <div class="rpt-sport-block">
            <div class="rpt-sport-title">{{ st.info.icon }} <span>{{ st.info.name }}</span> &mdash; {{ st.tally.games }} games</div>

            <div class="rpt-cat-label">Moneyline</div>
            <div class="rpt-grid">
                {% for mk, mlabel in model_labels %}
                {% set m = st.tally.get(mk, {}) %}
                <div class="rpt-card {% if mk == 'ensemble' %}hl{% endif %}">
                    <div class="rpt-model">{{ mlabel }}</div>
                    {% if m.total > 0 %}
                    <div class="rpt-acc {% if m.accuracy >= 60 %}g{% elif m.accuracy >= 50 %}y{% else %}r{% endif %}">{{ m.accuracy }}%</div>
                    <div class="rpt-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                    {% else %}
                    <div class="rpt-acc x">&mdash;</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>

            {% set sp = st.tally.get('spread', {}) %}
            {% set ou = st.tally.get('total_ou', {}) %}
            {% if sp.total > 0 or ou.total > 0 %}
            <div class="rpt-sou-row" style="margin-top:10px;">
                {% if sp.total > 0 %}
                <div>
                    <div class="rpt-cat-label">Spread</div>
                    <div class="rpt-card hl">
                        <div class="rpt-acc {% if sp.accuracy >= 55 %}g{% elif sp.accuracy >= 48 %}y{% else %}r{% endif %}">{{ sp.accuracy }}%</div>
                        <div class="rpt-rec">{{ sp.correct }}-{{ sp.total - sp.correct }}{% if sp.pushes %}-{{ sp.pushes }}{% endif %}</div>
                    </div>
                </div>
                {% endif %}
                {% if ou.total > 0 %}
                <div>
                    <div class="rpt-cat-label">Over/Under</div>
                    <div class="rpt-card hl">
                        <div class="rpt-acc {% if ou.accuracy >= 55 %}g{% elif ou.accuracy >= 48 %}y{% else %}r{% endif %}">{{ ou.accuracy }}%</div>
                        <div class="rpt-rec">{{ ou.correct }}-{{ ou.total - ou.correct }}{% if ou.pushes %}-{{ ou.pushes }}{% endif %}</div>
                    </div>
                </div>
                {% endif %}
            </div>
            {% endif %}
        </div>
        {% endfor %}

        {% endif %}
    </div>

    <div class="rpt-actions" style="flex-direction:column;align-items:center;">
        <div class="rpt-share-row">
            <button class="rpt-btn rpt-btn-x" onclick="shareScreenshot('x')" title="Share on X"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></button>
            <button class="rpt-btn rpt-btn-fb" onclick="shareScreenshot('fb')" title="Share on Facebook"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg></button>
            <button class="rpt-btn rpt-btn-ig" onclick="shareScreenshot('ig')" title="Share on Instagram"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg></button>
            <button class="rpt-btn rpt-btn-tk" onclick="shareScreenshot('tk')" title="Share on TikTok"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg></button>
            <button class="rpt-btn rpt-btn-copy" onclick="shareScreenshot('save')" title="Save Image"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>
        </div>
        <div class="rpt-sharing" id="shareStatus"></div>
        <div class="rpt-cta-row" style="margin-top:12px;">
            <a class="rpt-btn rpt-btn-cta" href="/">View Today's Picks &rarr;</a>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <script>
    function shareScreenshot(platform){
        var status=document.getElementById('shareStatus');
        status.textContent='Generating image...';
        var el=document.getElementById('reportCapture');
        html2canvas(el,{backgroundColor:'#0f172a',scale:2,useCORS:true}).then(function(canvas){
            canvas.toBlob(function(blob){
                var file=new File([blob],'underdogs-daily-report.png',{type:'image/png'});
                if(platform==='save'){
                    var a=document.createElement('a');
                    a.href=URL.createObjectURL(blob);
                    a.download='underdogs-daily-report.png';
                    a.click();
                    status.textContent='Image saved!';
                } else if(navigator.canShare && navigator.canShare({files:[file]})){
                    navigator.share({files:[file],title:'underdogs.bet Daily Report',url:'https://www.underdogs.bet/daily-report'}).then(function(){status.textContent='Shared!';}).catch(function(){fallbackShare(platform,blob,status);});
                } else {
                    fallbackShare(platform,blob,status);
                }
            },'image/png');
        }).catch(function(err){status.textContent='Screenshot failed: '+err;});
    }
    function fallbackShare(platform,blob,status){
        var url='https://www.underdogs.bet/daily-report';
        var text=encodeURIComponent('underdogs.bet Daily Results Report — check our AI picks performance');
        // Save image to downloads
        var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='underdogs-daily-report.png';a.click();
        // Copy image to clipboard
        try{navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);}catch(e){}
        // Open the platform
        if(platform==='x') window.open('https://twitter.com/intent/tweet?text='+text+'&url='+encodeURIComponent(url),'_blank');
        else if(platform==='fb') window.open('https://www.facebook.com/sharer/sharer.php?u='+encodeURIComponent(url),'_blank');
        else if(platform==='ig') window.open('https://instagram.com/underdogs.bet','_blank');
        else if(platform==='tk') window.open('https://tiktok.com/@underdog.bet','_blank');
        status.textContent='Image saved & copied to clipboard — paste it into your post!';
    }
    </script>
""")

# ============================================================================
# VALUE BETTING TEMPLATE (NHL only)
# ============================================================================

VALUE_BETTING_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.5em; margin-bottom: 30px; text-align: center; }
    .section-tabs { display: flex; gap: 10px; margin-bottom: 30px; justify-content: center; }
    .tab { padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: rgba(255, 255, 255, 0.1); color: white; }
    .tab.active { background: linear-gradient(135deg, #3b82f6, #2563eb); }
    .value-picks-container { background: rgba(255, 255, 255, 0.05); border-radius: 15px; padding: 25px; }
    .pick-card { background: rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 20px; margin-bottom: 20px; border-left: 4px solid; }
    .pick-card.HIGH { border-left-color: #10b981; }
    .pick-card.MEDIUM { border-left-color: #fbbf24; }
    .pick-card.LOW { border-left-color: #3b82f6; }
    .matchup { font-size: 1.4em; font-weight: bold; margin-bottom: 10px; }
    .pick-team { color: #10b981; font-size: 1.2em; font-weight: bold; }
    .edge-badge { display: inline-block; padding: 6px 14px; border-radius: 6px; font-weight: bold; margin: 5px; }
    .edge-badge.HIGH { background: #10b981; color: white; }
    .edge-badge.MEDIUM { background: #fbbf24; color: black; }
    .edge-badge.LOW { background: #3b82f6; color: white; }
    .situational { display: flex; gap: 15px; flex-wrap: wrap; margin-top: 10px; font-size: 0.9em; opacity: 0.9; }
    .situational-item { background: rgba(255, 255, 255, 0.1); padding: 6px 12px; border-radius: 6px; }
    .warning { color: #ef4444; font-weight: bold; }
    .no-picks { text-align: center; padding: 60px; opacity: 0.7; font-size: 1.2em; }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - VALUE BETTING PICKS</h1>
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab active">💰 Value Picks</a>
        <a href="/{{ sport_results_slug }}" class="tab">🎯 Results</a>
    </div>
    <div style="text-align: center; margin-bottom: 30px; padding: 20px; background: rgba(251, 191, 36, 0.1); border-radius: 10px;">
        <p style="font-size: 1.2em; margin-bottom: 10px;">✅ <strong>Only showing games with +5% or higher edge</strong></p>
        <p style="opacity: 0.8;">Situational factors (rest, back-to-back, form) applied to find mispriced lines</p>
    </div>
    <div class="value-picks-container">
        {% if predictions %}
            {% for pred in predictions %}
            <div class="pick-card {{ pred.confidence }}">
                <div class="matchup">{{ pred.away_team }} @ {{ pred.home_team }}</div>
                <div style="margin: 15px 0;">
                    <span class="edge-badge {{ pred.confidence }}">{{ pred.edge }}% EDGE</span>
                    <span class="edge-badge {{ pred.confidence }}">{{ pred.confidence }} CONFIDENCE</span>
                    {% if pred.best_line %}<span style="padding: 6px 14px; background: rgba(255,255,255,0.2); border-radius: 6px; font-weight: bold;">Best Line: {{ pred.best_line }}</span>{% endif %}
                </div>
                <div style="font-size: 1.1em; margin: 10px 0;">
                    🎯 <span class="pick-team">{{ pred.pick }}</span>
                </div>
                <div style="margin: 10px 0; padding: 10px; background: rgba(255,255,255,0.05); border-radius: 6px;">
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 0.9em;">
                        <div><strong>Edge:</strong> {{ (pred.elo_prob * 100)|round(1) }}%</div>
                        <div><strong>XSharp:</strong> {{ (pred.xgb_prob * 100)|round(1) }}%</div>
                        <div><strong>Sharp Consensus:</strong> {{ (pred.ensemble_prob * 100)|round(1) }}%</div>
                    </div>
                    <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <strong>Adjusted:</strong> {{ (pred.adjusted_prob * 100)|round(1) }}% &nbsp;|&nbsp; <strong>Market:</strong> {{ (pred.market_prob * 100)|round(1) }}%
                    </div>
                </div>
                <div class="situational">
                    <div class="situational-item">📅 {{ pred.game_date }}</div>
                    <div class="situational-item">🏠 Rest: {{ pred.home_rest }}d</div>
                    <div class="situational-item">✈️ Rest: {{ pred.away_rest }}d</div>
                    {% if pred.home_b2b %}<div class="situational-item warning">⚠️ Home B2B</div>{% endif %}
                    {% if pred.away_b2b %}<div class="situational-item warning">⚠️ Away B2B</div>{% endif %}
                    {% if pred.situational_edge != 0 %}<div class="situational-item">📊 Sit. Edge: {{ (pred.situational_edge * 100)|round(1) }}%</div>{% endif %}
                </div>
            </div>
            {% endfor %}
        {% else %}
        <div class="no-picks">
            ❌ No value bets found for today<br>
            <span style="opacity: 0.7; font-size: 0.9em;">Market is efficiently priced or no games available</span>
        </div>
        {% endif %}
    </div>
""")

# ============================================================================
# TRAFFIC DASHBOARD TEMPLATE
# ============================================================================

TRAFFIC_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.2em; margin-bottom: 20px; text-align: center; }
    .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }
    .stat-card { background:rgba(255,255,255,0.08); border-radius:10px; padding:14px; text-align:center; border:1px solid rgba(255,255,255,0.12); }
    .stat-label { font-size:0.8em; opacity:0.8; margin-bottom:6px; }
    .stat-value { font-size:1.8em; font-weight:800; color:#fbbf24; }
    .table-card { background:rgba(255,255,255,0.06); border-radius:12px; padding:16px; border:1px solid rgba(255,255,255,0.1); margin-bottom:16px; }
    table { width:100%; border-collapse: collapse; font-size:0.9em; }
    th { text-align:left; padding:10px; border-bottom:1px solid rgba(255,255,255,0.15); color:#fbbf24; }
    td { padding:8px 10px; border-bottom:1px solid rgba(255,255,255,0.08); }
    .no-data { text-align:center; padding:40px 12px; opacity:0.75; }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">📈 Site Traffic</h1>
    {% if traffic_source %}
    <div style="text-align:center;opacity:0.7;margin-bottom:10px;">Source: {{ traffic_source }}</div>
    {% endif %}
    {% if traffic_ga_url %}
    <div style="text-align:center;margin-bottom:14px;">
        <a href="{{ traffic_ga_url }}" target="_blank" style="display:inline-block;padding:8px 14px;border-radius:8px;background:rgba(251,191,36,0.15);border:1px solid rgba(251,191,36,0.5);color:#fbbf24;text-decoration:none;font-weight:700;">Open Google Analytics</a>
    </div>
    {% endif %}
    {% if traffic_error %}
    <div class="table-card" style="border-color:rgba(239,68,68,0.4);color:#fecaca;">
        {{ traffic_error }}
    </div>
    {% endif %}
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">Today</div>
            <div class="stat-value">{{ today_visits }}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Last 7 Days</div>
            <div class="stat-value">{{ week_visits }}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total</div>
            <div class="stat-value">{{ total_visits }}</div>
        </div>
    </div>

    <div class="table-card">
        <h2 style="margin-bottom:10px;">Top Pages</h2>
        {% if top_endpoints %}
        <table>
            <thead>
                <tr><th>Endpoint</th><th>Visits</th></tr>
            </thead>
            <tbody>
                {% for row in top_endpoints %}
                <tr><td>{{ row.endpoint }}</td><td>{{ row.count }}</td></tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="no-data">No endpoint visits recorded yet.</div>
        {% endif %}
    </div>

    <div class="table-card">
        <h2 style="margin-bottom:10px;">Daily Visits (Last 14 Days)</h2>
        {% if daily_visits %}
        <table>
            <thead>
                <tr><th>Date</th><th>Visits</th></tr>
            </thead>
            <tbody>
                {% for row in daily_visits %}
                <tr><td>{{ row.date }}</td><td>{{ row.count }}</td></tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="no-data">No daily visit data available yet.</div>
        {% endif %}
    </div>
""")

# ============================================================================
# PREDICTIONS TEMPLATE
# ============================================================================

PREDICTIONS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #3b82f6, #2563eb);
    }
    .predictions-table {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 25px;
        overflow-x: auto;
        max-height: 800px;
        overflow-y: auto;
    }
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th {
        background: #1e293b;
        padding: 15px;
        text-align: left;
        font-weight: 600;
        border-bottom: 2px solid #fbbf24;
        position: sticky;
        top: 0;
        z-index: 10;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }
    td {
        padding: 15px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    tr:hover {
        background: rgba(255, 255, 255, 0.05);
    }
    .model-pred {
        text-align: center;
        font-weight: bold;
    }
    .high-conf {
        color: #10b981;
    }
    .med-conf {
        color: #fbbf24;
    }
    .low-conf {
        color: #ef4444;
    }
    .no-data {
        text-align: center;
        padding: 60px 20px;
        font-size: 1.3em;
        opacity: 0.7;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Predictions</h1>
    
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab active">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}" class="tab">🎯 Results</a>
    </div>
    
    {% if today_date in sorted_dates %}
    <div style="text-align: center; margin-bottom: 20px;">
        <a href="#date-{{ today_date }}" style="background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #000; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">⚡ Skip to Today</a>
    </div>
    {% endif %}
    
    <div class="predictions-table">
        {% if grouped_predictions %}
            {% for date in sorted_dates %}
            <div id="date-{{ date }}" style="margin-bottom: 40px;">
                <h2 style="color: #fbbf24; margin-bottom: 15px; padding-left: 10px; {% if date == today_date %}background: rgba(251, 191, 36, 0.1); padding: 10px; border-radius: 8px;{% endif %}">
                    {% if group_by == 'week' %}Week {{ date }}{% else %}📅 {{ date }}{% endif %}
                    {% if date == today_date %} <span style="background: #10b981; color: white; padding: 4px 12px; border-radius: 4px; font-size: 0.8em; margin-left: 10px;">TODAY</span>{% endif %}
                </h2>
                <table style="margin-bottom: 20px;">
                    <thead>
                        <tr>
                            <th>Matchup</th>
                            <th style="background: #1e40af;">Grinder2</th>
                            <th style="background: #7c3aed;">Takedown</th>
                            <th style="background: #059669;">Edge</th>
                            <th style="background: #dc2626;">XSharp</th>
                            <th style="background: #fbbf24; color: #000;">Sharp Consensus</th>
                            <th>Pick</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for pred in grouped_predictions[date] %}
                        <tr>
                            <td>{{ pred.away_team_id }} @ <strong>{{ pred.home_team_id }}</strong></td>
                            <td class="model-pred" style="color: #60a5fa;">{{ pred.glicko2_prob if pred.glicko2_prob else '-' }}{% if pred.glicko2_prob %}%{% endif %}</td>
                            <td class="model-pred" style="color: #a78bfa;">{{ pred.trueskill_prob if pred.trueskill_prob else '-' }}{% if pred.trueskill_prob %}%{% endif %}</td>
                            <td class="model-pred" style="color: #34d399;">{{ pred.elo_prob if pred.elo_prob else '-' }}{% if pred.elo_prob %}%{% endif %}</td>
                            <td class="model-pred" style="color: #f87171;">{{ pred.xgb_prob }}%</td>
                            <td class="model-pred {% if pred.ensemble_prob > 60 %}high-conf{% elif pred.ensemble_prob > 55 %}med-conf{% else %}low-conf{% endif %}" style="font-size: 1.1em;">{{ pred.ensemble_prob }}%</td>
                            <td class="{% if pred.ensemble_prob > 60 %}high-conf{% elif pred.ensemble_prob > 55 %}med-conf{% else %}low-conf{% endif %}"><strong>{{ pred.predicted_winner }}</strong></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endfor %}
        {% else %}
        <div class="no-data">No upcoming predictions available for {{ sport_info.name }}</div>
        {% endif %}
    </div>
""")

# ============================================================================
# RESULTS TEMPLATE
# ============================================================================

NHL_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #10b981, #059669);
    }
    .results-table-container {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 20px;
        overflow-x: auto;
    }
    .results-header {
        text-align: center;
        margin-bottom: 20px;
    }
    .results-header h2 {
        color: #fbbf24;
        font-size: 1.8em;
        margin-bottom: 10px;
    }
    .results-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.95em;
    }
    .results-table th {
        background: rgba(255, 255, 255, 0.1);
        padding: 12px 8px;
        text-align: left;
        font-weight: bold;
        color: #fbbf24;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
    }
    .results-table td {
        padding: 10px 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    .results-table tr:hover {
        background: rgba(255, 255, 255, 0.05);
    }
    .prob-high {
        color: #10b981;
        font-weight: bold;
    }
    .prob-low {
        color: #ef4444;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Completed Games Results</h1>
    
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}" class="tab active">🎯 Results</a>
    </div>
    
    <div class="results-container">
        <div class="results-header">
            <h2>📅 2025-26 Season - All Completed Games</h2>
            <p style="opacity: 0.8;">Model predictions shown as home team win probability (%)</p>
        </div>
        
        <table class="results-table">
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Away Team</th>
                    <th>Home Team</th>
                    <th>Grinder2</th>
                    <th>Takedown</th>
                    <th>Edge</th>
                    <th>XSharp</th>
                    <th>Sharp Consensus</th>
                </tr>
            </thead>
            <tbody>
                {% for game in results %}
                <tr>
                    <td>{{ game.date }}</td>
                    <td>{{ game.away }}</td>
                    <td>{{ game.home }}</td>
                    <td class="{% if game.glicko2_home|float >= 60 %}prob-high{% elif game.glicko2_home|float <= 40 %}prob-low{% endif %}">{{ game.glicko2_home if game.glicko2_home else '-' }}</td>
                    <td class="{% if game.trueskill_home|float >= 60 %}prob-high{% elif game.trueskill_home|float <= 40 %}prob-low{% endif %}">{{ game.trueskill_home if game.trueskill_home else '-' }}</td>
                    <td class="{% if game.elo_home|float >= 60 %}prob-high{% elif game.elo_home|float <= 40 %}prob-low{% endif %}">{{ game.elo_home }}%</td>
                    <td class="{% if game.xgb_home|float >= 60 %}prob-high{% elif game.xgb_home|float <= 40 %}prob-low{% endif %}">{{ game.xgb_home }}%</td>
                    <td class="{% if game.meta_home|float >= 60 %}prob-high{% elif game.meta_home|float <= 40 %}prob-low{% endif %}">{{ game.meta_home }}%</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        <div style="margin-top: 30px; text-align: center; padding: 20px; background: rgba(255, 255, 255, 0.1); border-radius: 10px;">
            <p style="font-size: 1.1em; margin-bottom: 10px;">📊 <strong>Total Games:</strong> {{ results|length }}</p>
            <p style="opacity: 0.8;">Values shown are home team win probabilities. Higher % = model favors home team.</p>
        </div>
    </div>
""")

RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #10b981, #059669);
    }
    .results-container {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 30px;
    }
    .date-range {
        text-align: center;
        font-size: 1.3em;
        margin-bottom: 10px;
        color: #fbbf24;
    }
    .test-info {
        text-align: center;
        font-size: 1.1em;
        margin-bottom: 30px;
        opacity: 0.9;
    }
    .models-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 20px;
    }
    .model-card {
        background: rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 25px;
        text-align: center;
        border: 2px solid rgba(255, 255, 255, 0.2);
    }
    .model-card.ensemble {
        border: 3px solid #fbbf24;
    }
    .model-name {
        font-size: 1.3em;
        font-weight: bold;
        margin-bottom: 15px;
        color: #fbbf24;
    }
    .model-accuracy {
        font-size: 3.5em;
        font-weight: bold;
        margin: 15px 0;
    }
    .model-record {
        font-size: 1.2em;
        opacity: 0.9;
    }
    .no-data {
        text-align: center;
        padding: 60px 20px;
        font-size: 1.3em;
        opacity: 0.7;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <div style="margin-bottom: 20px;">
        <a href="/" style="display: inline-block; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 8px; text-decoration: none; color: white; font-weight: 600;">← Back to Home</a>
    </div>
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Results</h1>
    
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}" class="tab active">🎯 Results</a>
    </div>
        {% set model_cards
    <div class="results-container">
        {% if performance %}
        <div class="date-range">📅 Test Period: {{ performance.date_range }}</div>
        <div class="test-info">Tested on {{ performance.total_games }} completed games</div>
        
        <div class="models-grid">
            <!-- Rating-Based Models -->
            <div class="model-card" style="border-color: #1e40af;">
                <div class="model-name" style="color: #60a5fa;">📊 Grinder2</div>
                <div class="model-accuracy">{{ performance.glicko2.accuracy if performance.glicko2 else '—' }}{% if performance.glicko2 %}%{% endif %}</div>
                <div class="model-record">{% if performance.glicko2 %}{{ performance.glicko2.correct }}-{{ performance.glicko2.total - performance.glicko2.correct }}{% else %}No data{% endif %}</div>
            </div>
            
            <div class="model-card" style="border-color: #7c3aed;">
                <div class="model-name" style="color: #a78bfa;">🎯 Takedown</div>
                <div class="model-accuracy">{{ performance.trueskill.accuracy if performance.trueskill else '—' }}{% if performance.trueskill %}%{% endif %}</div>
                <div class="model-record">{% if performance.trueskill %}{{ performance.trueskill.correct }}-{{ performance.trueskill.total - performance.trueskill.correct }}{% else %}No data{% endif %}</div>
            </div>
            
            <div class="model-card" style="border-color: #059669;">
                <div class="model-name" style="color: #34d399;">📊 Edge</div>
                <div class="model-accuracy">{{ performance.elo.accuracy if performance.elo else '—' }}{% if performance.elo %}%{% endif %}</div>
                <div class="model-record">{% if performance.elo %}{{ performance.elo.correct }}-{{ performance.elo.total - performance.elo.correct }}{% else %}No data{% endif %}</div>
            </div>
            
            <!-- ML Models -->
            <div class="model-card" style="border-color: #dc2626;">
                <div class="model-name" style="color: #f87171;">🤖 XSharp</div>
                <div class="model-accuracy">{{ performance.xgboost.accuracy }}%</div>
                <div class="model-record">{{ performance.xgboost.correct }}-{{ performance.xgboost.total - performance.xgboost.correct }}</div>
            </div>
            
            <!-- Sharp Consensus -->
            <div class="model-card ensemble" style="grid-column: span 2;">
                <div class="model-name">🏆 Sharp Consensus</div>
                <div class="model-accuracy" style="font-size: 4em;">{{ performance.ensemble.accuracy }}%</div>
                <div class="model-record" style="font-size: 1.4em;">{{ performance.ensemble.correct }}-{{ performance.ensemble.total - performance.ensemble.correct }}</div>
            </div>
        </div>
        {% else %}
        <div class="no-data">Not enough data to calculate performance for {{ sport_info.name }}</div>
        {% endif %}
    </div>
""")

# Daily Results Template (for NHL/NBA/NCAAB etc.)
DAILY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.2em; margin-bottom: 20px; text-align: center; padding:22px 18px; border:1px solid rgba(255,255,255,0.1); border-radius:12px; position:relative; overflow:hidden; z-index:1; }
    {% if sport_bg_image %}.page-title::before{content:'';position:absolute;inset:0;background:url('{{ sport_bg_image }}') center/cover no-repeat;z-index:-2;}
    .page-title::after{content:'';position:absolute;inset:0;background:rgba(7,10,20,0.6);z-index:-1;}{% endif %}
    .section-tabs { display: flex; gap: 8px; margin-bottom: 20px; justify-content: center; flex-wrap: wrap; }
    .tab { padding: 10px 22px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: rgba(255,255,255,0.1); color: white; font-size: 0.9em; }
    .tab.active { background: linear-gradient(135deg, #10b981, #059669); }
    /* Type toggle */
    .type-toggle { display:flex; gap:6px; justify-content:center; margin-bottom:16px; }
    .toggle-btn { padding:8px 18px; border-radius:6px; border:2px solid rgba(255,255,255,0.2); background:rgba(255,255,255,0.06); color:white; font-weight:600; font-size:0.85em; cursor:pointer; transition:all 0.2s; }
    .toggle-btn.active { background:linear-gradient(135deg,#8b5cf6,#6d28d9); border-color:#8b5cf6; }
    .toggle-btn:hover { border-color:#8b5cf6; }
    .league-slider { display:flex; align-items:center; justify-content:center; gap:10px; margin:10px 0 16px; }
    .league-badges { display:flex; gap:8px; overflow-x:auto; padding:4px; max-width:860px; }
    .league-pill { background:rgba(255,255,255,0.08); border:2px solid rgba(255,255,255,0.15); border-radius:20px; padding:6px 14px; font-size:0.8em; font-weight:600; white-space:nowrap; cursor:pointer; transition:all 0.2s; color:#e2e8f0; text-decoration:none; display:inline-flex; align-items:center; }
    .league-pill.active { background:#fbbf24; border-color:#fbbf24; color:#0f172a; }
    .league-pill:hover { border-color:#fbbf24; }
    /* Date navigation */
    .date-nav { display:flex; align-items:center; justify-content:center; gap:12px; margin:16px 0; padding:12px 16px; background:rgba(255,255,255,0.05); border-radius:12px; }
    .nav-arrow { background:rgba(251,191,36,0.2); border:2px solid #fbbf24; color:#fbbf24; font-size:1.3em; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer; transition:all 0.2s; user-select:none; flex-shrink:0; }
    .nav-arrow:hover { background:rgba(251,191,36,0.4); transform:scale(1.1); }
    .date-bubbles { display:flex; gap:8px; overflow-x:auto; padding:4px; max-width:820px; }
    .date-bubble { background:rgba(255,255,255,0.1); border:2px solid rgba(255,255,255,0.2); border-radius:22px; padding:8px 15px; min-width:100px; text-align:center; cursor:pointer; transition:all 0.2s; white-space:nowrap; font-weight:500; font-size:0.84em; }
    .date-bubble:hover { border-color:#fbbf24; }
    .date-bubble.active { background:#fbbf24; border-color:#fbbf24; color:#0f172a; font-weight:700; }
    .date-bubble.today { border-color:#10b981; color:#10b981; }
    .date-bubble.active.today { background:#10b981; color:white; }
    /* Date sections */
    .date-section { display:none; background:rgba(255,255,255,0.05); border-radius:12px; padding:20px; margin-bottom:20px; }
    .date-section.visible { display:block; }
    .date-header { color:#fbbf24; font-size:1.3em; margin-bottom:14px; padding-bottom:10px; border-bottom:2px solid rgba(255,255,255,0.2); }
    .results-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:16px; }
    .result-card { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:12px; overflow:hidden; transition:border-color 0.2s; }
    .result-card:hover { border-color:#fbbf24; }
    .result-status { padding:6px 14px; font-size:0.72em; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; color:#10b981; background:rgba(16,185,129,0.12); }
    .result-body { display:flex; padding:12px 14px; gap:12px; }
    .teams-section { flex:1; min-width:0; }
    .team-row { display:flex; align-items:center; justify-content:space-between; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.05); }
    .team-row:last-child { border-bottom:none; }
    .team-name { font-size:0.95em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .team-name.winner { font-weight:700; }
    .score-box { font-size:1.05em; font-weight:700; color:#fbbf24; margin-left:8px; }
    .model-panel { background:rgba(139,92,246,0.12); border-left:3px solid #8b5cf6; padding:10px 12px; min-width:170px; max-width:200px; display:flex; flex-direction:column; gap:4px; }
    .panel-title { font-size:0.66em; color:#a78bfa; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; margin-bottom:2px; }
    .model-row { display:flex; justify-content:space-between; font-size:0.82em; padding:2px 0; }
    .model-lbl { opacity:0.85; }
    .model-right { display:flex; align-items:center; gap:6px; }
    .model-val { font-weight:600; }
    .ensemble-badge { background:rgba(16,185,129,0.2); border:1px solid #10b981; color:#10b981; padding:5px; border-radius:5px; text-align:center; font-weight:700; margin-top:4px; font-size:0.8em; }
    .result-footer { border-top:1px solid rgba(255,255,255,0.07); padding:8px 12px; display:flex; gap:14px; flex-wrap:wrap; background:rgba(0,0,0,0.18); }
    .sf-item { display:flex; flex-direction:column; gap:1px; }
    .sf-label { color:#94a3b8; font-size:0.72em; text-transform:uppercase; letter-spacing:0.3px; }
    .sf-val { font-weight:600; font-size:0.85em; color:#e2e8f0; }
    .pick-ok { color:#10b981; font-weight:700; }
    .pick-no { color:#ef4444; font-weight:700; }
    .section-ml, .section-spread, .section-total { display:block; }
    .model-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin-bottom:16px; }
    @media(max-width:900px){ .model-grid { grid-template-columns:repeat(3,1fr); } }
    .model-card { background:rgba(255,255,255,0.06); border-radius:10px; padding:12px; text-align:center; }
    .model-card.highlight { border:2px solid #fbbf24; }
    .model-label { font-size:0.78em; opacity:0.8; margin-bottom:4px; }
    .model-acc { font-size:1.4em; font-weight:700; color:#10b981; }
    .model-rec { font-size:0.82em; opacity:0.85; }
    .daily-tally { background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.12); border-radius:12px; padding:16px; margin-bottom:16px; }
    .daily-tally h2 { text-align:center; margin:0 0 12px 0; font-size:1.15em; color:#fbbf24; }
    .daily-tally-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
    .daily-tally-card { background:rgba(255,255,255,0.06); border-radius:8px; padding:10px; text-align:center; }
    .daily-tally-card.highlight { border:2px solid #fbbf24; }
    .daily-model { font-size:0.78em; opacity:0.85; margin-bottom:4px; }
    .daily-acc { font-size:1.35em; font-weight:700; }
    .daily-rec { font-size:0.8em; opacity:0.8; }
    @media(max-width:640px){ .roi-grid{grid-template-columns:1fr !important;} }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} — Results</h1>
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Results</a>
    </div>
        {% set model_cards = [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Consensus','ensemble')] %}
        {% set label_glicko2 = 'Grinder2' %}
        {% set label_trueskill = 'Takedown' %}
        {% set label_elo = 'Edge' %}
        {% set label_xgb = 'XSharp' %}
        {% set label_ensemble = 'Consensus' %}
        {% if daily_results and overall_stats %}
        {% if soccer_leagues %}
        <div class="league-slider">
            <div class="league-badges" id="leagueBubbles">
                {% for lg in soccer_leagues %}
                <a class="league-pill {% if lg.active %}active{% endif %}" href="{{ lg.url }}">{{ lg.name }}</a>
                {% endfor %}
            </div>
        </div>
        {% endif %}
        {% set ens = overall_stats.ensemble %}

        <!-- ── Daily Tally ── -->
        {% if daily_tally %}
        <div class="daily-tally">
            <h2>Last Night's Tally — {{ daily_tally_date }} ({{ daily_tally_games }} games)</h2>
            <div style="font-size:0.78em;text-align:center;opacity:0.7;margin-bottom:6px;">MONEYLINE</div>
            <div class="daily-tally-grid">
                {% for m_label, m_key in model_cards %}
                {% set m = daily_tally[m_key] %}
                <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                    <div class="daily-model">{{ m_label }}</div>
                    {% if m.total > 0 %}
                    <div class="daily-acc">{{ m.accuracy }}%</div>
                    <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no graded games</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
            {% if daily_tally.spread is defined and daily_tally.total_ou is defined %}
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
                <div class="daily-tally-card" style="border:1px solid rgba(139,92,246,0.4);">
                    <div class="daily-model">📈 Spread</div>
                    {% if daily_tally.spread.total > 0 %}
                    <div class="daily-acc" style="color:{% if daily_tally.spread.accuracy >= 52 %}#10b981{% elif daily_tally.spread.accuracy >= 48 %}#fbbf24{% else %}#ef4444{% endif %};">{{ daily_tally.spread.accuracy }}%</div>
                    <div class="daily-rec">{{ daily_tally.spread.correct }}-{{ daily_tally.spread.total - daily_tally.spread.correct }}{% if daily_tally.spread.pushes %}-{{ daily_tally.spread.pushes }}{% endif %}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no spread data</div>
                    {% endif %}
                </div>
                <div class="daily-tally-card" style="border:1px solid rgba(251,191,36,0.4);">
                    <div class="daily-model">🎲 Over/Under</div>
                    {% if daily_tally.total_ou.total > 0 %}
                    <div class="daily-acc" style="color:{% if daily_tally.total_ou.accuracy >= 52 %}#10b981{% elif daily_tally.total_ou.accuracy >= 48 %}#fbbf24{% else %}#ef4444{% endif %};">{{ daily_tally.total_ou.accuracy }}%</div>
                    <div class="daily-rec">{{ daily_tally.total_ou.correct }}-{{ daily_tally.total_ou.total - daily_tally.total_ou.correct }}{% if daily_tally.total_ou.pushes %}-{{ daily_tally.total_ou.pushes }}{% endif %}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no O/U data</div>
                    {% endif %}
                </div>
            </div>
            {% endif %}
        </div>
        {% else %}
        <div class="daily-tally" style="text-align:center;">
            No graded games for {{ daily_tally_date }}.
        </div>
        {% endif %}

        <!-- ── Last 7 Days Tally ── -->
        {% if weekly_tally %}
        <div class="daily-tally">
            <h2>Last 7 Days Tally — {{ weekly_tally_date_range }} ({{ weekly_tally_games }} games)</h2>
            <div style="font-size:0.78em;text-align:center;opacity:0.7;margin-bottom:6px;">MONEYLINE</div>
            <div class="daily-tally-grid">
                {% for m_label, m_key in model_cards %}
                {% set m = weekly_tally[m_key] %}
                <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                    <div class="daily-model">{{ m_label }}</div>
                    {% if m.total > 0 %}
                    <div class="daily-acc">{{ m.accuracy }}%</div>
                    <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no graded games</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
            {% if weekly_tally.spread is defined and weekly_tally.total_ou is defined %}
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
                <div class="daily-tally-card" style="border:1px solid rgba(139,92,246,0.4);">
                    <div class="daily-model">📈 Spread</div>
                    {% if weekly_tally.spread.total > 0 %}
                    <div class="daily-acc" style="color:{% if weekly_tally.spread.accuracy >= 52 %}#10b981{% elif weekly_tally.spread.accuracy >= 48 %}#fbbf24{% else %}#ef4444{% endif %};">{{ weekly_tally.spread.accuracy }}%</div>
                    <div class="daily-rec">{{ weekly_tally.spread.correct }}-{{ weekly_tally.spread.total - weekly_tally.spread.correct }}{% if weekly_tally.spread.pushes %}-{{ weekly_tally.spread.pushes }}{% endif %}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no spread data</div>
                    {% endif %}
                </div>
                <div class="daily-tally-card" style="border:1px solid rgba(251,191,36,0.4);">
                    <div class="daily-model">🎲 Over/Under</div>
                    {% if weekly_tally.total_ou.total > 0 %}
                    <div class="daily-acc" style="color:{% if weekly_tally.total_ou.accuracy >= 52 %}#10b981{% elif weekly_tally.total_ou.accuracy >= 48 %}#fbbf24{% else %}#ef4444{% endif %};">{{ weekly_tally.total_ou.accuracy }}%</div>
                    <div class="daily-rec">{{ weekly_tally.total_ou.correct }}-{{ weekly_tally.total_ou.total - weekly_tally.total_ou.correct }}{% if weekly_tally.total_ou.pushes %}-{{ weekly_tally.total_ou.pushes }}{% endif %}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no O/U data</div>
                    {% endif %}
                </div>
            </div>
            {% endif %}
        </div>
        {% else %}
        <div class="daily-tally" style="text-align:center;">
            No graded games for last 7 days.
        </div>
        {% endif %}

        <!-- ── ROI Cards ── -->
        {% if roi_cards %}
        <div style="background:linear-gradient(135deg,#1e293b,#0f172a);border:2px solid #fbbf24;border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
            <h2 style="text-align:center;margin:0 0 16px 0;font-size:1.3em;">💰 ROI Tracker (1u flat bets)</h2>
            <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">
                {% for mkt, mkt_label in [('moneyline','Moneyline'),('spread','Spread'),('total','Total (O/U)')] %}
                {% set c = roi_cards[mkt] %}
                <div style="background:rgba(255,255,255,0.06);border-radius:10px;padding:14px;">
                    <div style="font-size:0.82em;text-align:center;opacity:0.8;margin-bottom:8px;font-weight:700;">{{ mkt_label }}</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;text-align:center;font-size:0.78em;">
                        <div><div style="opacity:0.6;">7 Days</div><div style="font-weight:700;color:{% if c.weekly.roi != '—' and '-' not in c.weekly.roi %}#10b981{% elif c.weekly.roi != '—' %}#ef4444{% else %}#94a3b8{% endif %};">{{ c.weekly.roi }}</div><div style="opacity:0.7;font-size:0.9em;">{{ c.weekly.detail }}</div></div>
                        <div><div style="opacity:0.6;">Season</div><div style="font-weight:700;color:{% if c.total.roi != '—' and '-' not in c.total.roi %}#10b981{% elif c.total.roi != '—' %}#ef4444{% else %}#94a3b8{% endif %};">{{ c.total.roi }}</div><div style="opacity:0.7;font-size:0.9em;">{{ c.total.detail }}</div></div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        <!-- ── Combined Stats Banner ── -->
        <div style="background:linear-gradient(135deg,#1e293b,#0f172a);border:2px solid #10b981;border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
            <h2 style="text-align:center;margin:0 0 6px 0;font-size:1.5em;">🏆 Season Performance</h2>
            <div id="seasonInfoBox" style="display:none;background:rgba(0,0,0,0.6);border:1px solid rgba(255,255,255,0.15);border-radius:8px;padding:12px 16px;margin:0 0 14px;font-size:0.78em;color:#cbd5e1;line-height:1.6;text-align:center;">
                Results are tracked from the start of the {{ sport_info.name }} season. All completed games with available model predictions are graded automatically. Game counts reflect actual games graded — some games may lack model data due to missing stats or early-season data gaps. Numbers grow daily as more games are played.
            </div>
            <div style="text-align:center;margin-bottom:14px;"><span onclick="var b=document.getElementById('seasonInfoBox');b.style.display=b.style.display==='none'?'block':'none';" style="cursor:pointer;font-size:0.75em;color:#94a3b8;border:1px solid rgba(255,255,255,0.15);border-radius:12px;padding:3px 10px;">ⓘ What do these numbers mean?</span></div>
            <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px;">
                <div style="background:rgba(255,255,255,0.07);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;margin-bottom:4px;">🎯 Moneyline (Consensus)</div>
                    <div style="font-size:2em;font-weight:bold;color:{% if ens.accuracy>=55 %}#10b981{% elif ens.accuracy>=50 %}#fbbf24{% else %}#ef4444{% endif %};">{{ ens.accuracy }}%</div>
                    <div style="font-size:0.85em;opacity:0.85;">{{ ens.correct }}-{{ ens.total - ens.correct }} ({{ ens.total }} games)</div>
                </div>
                {% if spread_total_stats is defined and spread_total_stats %}
                <div style="background:rgba(255,255,255,0.07);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;margin-bottom:4px;">📈 Spread (XSharp)</div>
                    <div style="font-size:2em;font-weight:bold;color:{% if spread_total_stats.spread_pct>=52 %}#10b981{% elif spread_total_stats.spread_pct>=50 %}#fbbf24{% else %}#ef4444{% endif %};">{{ spread_total_stats.spread_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.85;">{{ spread_total_stats.spread_covered }}-{{ spread_total_stats.spread_graded - spread_total_stats.spread_covered }} ({{ spread_total_stats.spread_graded }} graded)</div>
                </div>
                <div style="background:rgba(255,255,255,0.07);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;margin-bottom:4px;">🎲 O/U (XSharp)</div>
                    <div style="font-size:2em;font-weight:bold;color:{% if spread_total_stats.total_pct>=52 %}#10b981{% elif spread_total_stats.total_pct>=50 %}#fbbf24{% else %}#ef4444{% endif %};">{{ spread_total_stats.total_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.85;">{{ spread_total_stats.total_correct }}-{{ spread_total_stats.total_graded - spread_total_stats.total_correct }} ({{ spread_total_stats.total_graded }} graded)</div>
                </div>
                {% else %}
                <div style="background:rgba(255,255,255,0.07);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;">📈 Spread</div><div style="font-size:1.5em;color:#94a3b8;">—</div></div>
                <div style="background:rgba(255,255,255,0.07);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;">🎲 O/U</div><div style="font-size:1.5em;color:#94a3b8;">—</div></div>
                {% endif %}
            </div>
            <div style="border-top:1px solid rgba(255,255,255,0.12);padding-top:12px;"></div>
        </div>


        <!-- ── Model Records ── -->
        <h3 style="text-align:center;font-size:1.15em;margin:0 0 12px;color:#e2e8f0;">Moneyline Accuracy by Model</h3>
        <div class="model-grid">
            {% for m_label, m_key in model_cards %}
            {% set m = overall_stats[m_key] %}
            <div class="model-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                <div class="model-label">{{ m_label }}</div>
                {% if m.total > 0 %}
                <div class="model-acc">{{ m.accuracy }}%</div>
                <div class="model-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                {% else %}
                <div class="model-acc" style="color:#94a3b8;">—</div>
                <div class="model-rec">no graded games</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        <!-- ── Type Toggle ── -->
        <div class="type-toggle">
            <button class="toggle-btn active" onclick="filterSections('all',this)">ALL</button>
            <button class="toggle-btn" onclick="filterSections('ml',this)">Moneyline</button>
            <button class="toggle-btn" onclick="filterSections('spread',this)">Spread</button>
            <button class="toggle-btn" onclick="filterSections('total',this)">Total</button>
        </div>

        <!-- ── Date Slider ── -->
        <div class="date-nav">
            <div class="nav-arrow" onclick="previousWeek()">&#8249;</div>
            <div class="date-bubbles" id="dateBubbles"></div>
            <div class="nav-arrow" onclick="nextWeek()">&#8250;</div>
        </div>

        {% for date in sorted_dates %}
        {% set date_data = daily_results[date] %}
        <div id="date-{{ date }}" class="date-section">
            <div class="date-header">📅 {{ date }}{% if date == today_date %} <span style="background:#10b981;color:white;padding:3px 10px;border-radius:4px;font-size:0.65em;margin-left:8px;">TODAY</span>{% endif %}</div>

            <div class="results-grid">
                {% for game in date_data.games %}
                {% set home_wins = game.home_score > game.away_score %}
                {% set away_wins = game.away_score > game.home_score %}
                {% set actual_spread = (game.home_score - game.away_score) %}
                {% set actual_total = (game.home_score + game.away_score) %}
                <div class="result-card" data-league="{{ game.league if game.league else 'Other' }}">
                    <div class="result-status">FINAL</div>
                    <div class="result-body">
                        <div class="teams-section">
                            <div class="team-row">
                                <span class="team-name {% if away_wins %}winner{% endif %}">{{ game.away }}{% if game.away_moneyline is defined and game.away_moneyline is not none %} <span style="font-size:0.8em;color:{% if game.away_moneyline < 0 %}#10b981{% else %}#fbbf24{% endif %};font-weight:700;">{% if game.away_moneyline > 0 %}+{% endif %}{{ game.away_moneyline }}</span>{% endif %}</span>
                                <span class="score-box">{{ game.away_score }}</span>
                            </div>
                            <div class="team-row">
                                <span class="team-name {% if home_wins %}winner{% endif %}">{{ game.home }}{% if game.home_moneyline is defined and game.home_moneyline is not none %} <span style="font-size:0.8em;color:{% if game.home_moneyline < 0 %}#10b981{% else %}#fbbf24{% endif %};font-weight:700;">{% if game.home_moneyline > 0 %}+{% endif %}{{ game.home_moneyline }}</span>{% endif %}</span>
                                <span class="score-box">{{ game.home_score }}</span>
                            </div>
                        </div>
                        <div class="model-panel section-ml">
                            <div class="panel-title">Moneyline Models</div>
                            <div class="model-row">
                                <span class="model-lbl" style="color:#60a5fa;">{{ label_glicko2 }}</span>
                                <span class="model-right">
                                    <span class="model-val">{{ game.glicko2_prob if game.glicko2_prob is not none else '—' }}{% if game.glicko2_prob is not none %}%{% endif %}</span>
                                    {% if game.glicko2_correct is not none %}<span class="{{ 'pick-ok' if game.glicko2_correct else 'pick-no' }}">{{ '✅' if game.glicko2_correct else '❌' }}</span>{% endif %}
                                </span>
                            </div>
                            <div class="model-row">
                                <span class="model-lbl" style="color:#a78bfa;">{{ label_trueskill }}</span>
                                <span class="model-right">
                                    <span class="model-val">{{ game.trueskill_prob if game.trueskill_prob is not none else '—' }}{% if game.trueskill_prob is not none %}%{% endif %}</span>
                                    {% if game.trueskill_correct is not none %}<span class="{{ 'pick-ok' if game.trueskill_correct else 'pick-no' }}">{{ '✅' if game.trueskill_correct else '❌' }}</span>{% endif %}
                                </span>
                            </div>
                            <div class="model-row">
                                <span class="model-lbl" style="color:#34d399;">{{ label_elo }}</span>
                                <span class="model-right">
                                    <span class="model-val">{{ game.elo_prob if game.elo_prob is not none else '—' }}{% if game.elo_prob is not none %}%{% endif %}</span>
                                    {% if game.elo_correct is not none %}<span class="{{ 'pick-ok' if game.elo_correct else 'pick-no' }}">{{ '✅' if game.elo_correct else '❌' }}</span>{% endif %}
                                </span>
                            </div>
                            <div class="model-row">
                                <span class="model-lbl" style="color:#f87171;">{{ label_xgb }}</span>
                                <span class="model-right">
                                    <span class="model-val">{{ game.xgb_prob if game.xgb_prob is not none else '—' }}{% if game.xgb_prob is not none %}%{% endif %}</span>
                                    {% if game.xgb_correct is not none %}<span class="{{ 'pick-ok' if game.xgb_correct else 'pick-no' }}">{{ '✅' if game.xgb_correct else '❌' }}</span>{% endif %}
                                </span>
                            </div>
                            <div class="ensemble-badge">{{ label_ensemble|upper }} {{ game.ens_prob }}% {% if game.ens_correct is not none %}<span class="{{ 'pick-ok' if game.ens_correct else 'pick-no' }}">{{ '✅' if game.ens_correct else '❌' }}</span>{% endif %}</div>
                            {% if game.model_data_note %}<div style="font-size:0.7em;color:#94a3b8;margin-top:4px;">{{ game.model_data_note }}</div>{% endif %}
                        </div>
                    </div>
                    <div class="result-footer section-spread">
                        <div class="sf-item">
                            <span class="sf-label">Model Spread Pick</span>
                            <span class="sf-val">
                                {% if game.spread_pick_label %}{{ game.spread_pick_label }}
                                {% elif game.spread_pick_reason is defined and game.spread_pick_reason %}{{ game.spread_pick_reason }}
                                {% else %}—{% endif %}
                                {% if game.spread_correct is not none %}<span class="{{ 'pick-ok' if game.spread_correct else 'pick-no' }}">{{ '✅' if game.spread_correct else '❌' }}</span>{% endif %}
                            </span>
                        </div>
                        <div class="sf-item">
                            <span class="sf-label">Our Spread</span>
                            <span class="sf-val">
                                {% if game.market_spread_label is defined and game.market_spread_label %}{{ game.market_spread_label }}
                                {% elif game.market_spread is not none %}{{ "%+.1f"|format(game.market_spread) }}
                                {% elif game.market_spread_reason is defined and game.market_spread_reason %}{{ game.market_spread_reason }}
                                {% else %}—{% endif %}
                            </span>
                        </div>
                        <div class="sf-item">
                            <span class="sf-label">Actual Spread</span>
                            <span class="sf-val">{{ "%+.1f"|format(actual_spread) }}</span>
                        </div>
                    </div>
                    <div class="result-footer section-total">
                        <div class="sf-item">
                            <span class="sf-label">Model O/U Pick</span>
                            <span class="sf-val">
                                {% if game.total_pick_label %}{{ game.total_pick_label }}
                                {% elif game.total_pick_reason is defined and game.total_pick_reason %}{{ game.total_pick_reason }}
                                {% else %}—{% endif %}
                                {% if game.total_correct is not none %}<span class="{{ 'pick-ok' if game.total_correct else 'pick-no' }}">{{ '✅' if game.total_correct else '❌' }}</span>{% endif %}
                            </span>
                        </div>
                        <div class="sf-item">
                            <span class="sf-label">Our Total</span>
                            <span class="sf-val">
                                {% if game.market_total is not none %}{{ "%.1f"|format(game.market_total) }}
                                {% elif game.market_total_reason is defined and game.market_total_reason %}{{ game.market_total_reason }}
                                {% else %}—{% endif %}
                            </span>
                        </div>
                        <div class="sf-item">
                            <span class="sf-label">Actual Total</span>
                            <span class="sf-val">{{ "%.1f"|format(actual_total) }}</span>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}

    {% else %}
    <div style="text-align:center;padding:60px;opacity:0.7;">No results data available yet.</div>
    {% endif %}
<script>
    /* ── Section filter toggle ── */
    function filterSections(mode, btn) {
        document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        document.querySelectorAll('.section-ml,.section-spread,.section-total').forEach(el => {
            el.style.display = (mode === 'all' || el.classList.contains('section-' + mode)) ? '' : 'none';
        });
    }
    /* ── Date slider ── */
    const allDates = {{ sorted_dates|reverse|list|tojson }};
    const today = '{{ today_date }}';
    let currentWeekStart = 0, activeDate = null;
    const datesPerWeek = 7;
    function fmtDate(ds) {
        const d = new Date(ds+'T12:00:00');
        const days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return days[d.getDay()]+', '+months[d.getMonth()]+' '+d.getDate();
    }
    function showDate(date) {
        document.querySelectorAll('.date-section').forEach(s=>s.classList.remove('visible'));
        const sec=document.getElementById('date-'+date);
        if(sec){sec.classList.add('visible');activeDate=date;}
    }
    function renderBubbles() {
        const c=document.getElementById('dateBubbles'); c.innerHTML='';
        const end=Math.min(currentWeekStart+datesPerWeek,allDates.length);
        const week=allDates.slice(currentWeekStart,end);
        if(activeDate && !week.includes(activeDate)){activeDate=week[week.length-1];showDate(activeDate);}
        week.forEach(date=>{
            const b=document.createElement('div'); b.className='date-bubble';
            if(date===today)b.classList.add('today');
            if(date===activeDate)b.classList.add('active');
            b.textContent=fmtDate(date);
            b.onclick=()=>{document.querySelectorAll('.date-bubble').forEach(x=>x.classList.remove('active'));b.classList.add('active');showDate(date);};
            c.appendChild(b);
        });
    }
    function previousWeek(){if(currentWeekStart>0){currentWeekStart=Math.max(0,currentWeekStart-datesPerWeek);renderBubbles();}}
    function nextWeek(){if(currentWeekStart+datesPerWeek<allDates.length){currentWeekStart+=datesPerWeek;renderBubbles();}}
    document.addEventListener('DOMContentLoaded',()=>{
        if(allDates.length>0){
            const lastIdx=allDates.length-1;
            currentWeekStart=Math.max(0,lastIdx-datesPerWeek+1);
            activeDate=allDates[lastIdx];
        }
        showDate(activeDate);renderBubbles();
    });
</script>
""")

# NFL Weekly Results Template
NFL_WEEKLY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
        padding:20px 18px;
        border:1px solid rgba(255,255,255,0.1);
        border-radius:12px;
        position:relative;
        overflow:hidden;
    }
    {% if sport_bg_image %}.page-title::before{content:'';position:absolute;inset:0;background:url('{{ sport_bg_image }}') center/cover no-repeat;z-index:-2;}
    .page-title::after{content:'';position:absolute;inset:0;background:rgba(7,10,20,0.6);z-index:-1;}{% endif %}
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #10b981, #059669);
    }
    .week-section {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 25px;
        margin-bottom: 30px;
    }
    .week-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 20px;
        padding-bottom: 15px;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
    }
    .week-title {
        font-size: 1.8em;
        color: #fbbf24;
        font-weight: bold;
    }
    .week-models {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 15px;
        margin-bottom: 20px;
    }
    .week-model-card {
        background: rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 15px;
        text-align: center;
    }
    .week-model-card.best {
        border: 2px solid #10b981;
        background: rgba(16, 185, 129, 0.1);
    }
    .daily-tally { background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.12); border-radius:12px; padding:16px; margin-bottom:20px; }
    .daily-tally h2 { text-align:center; margin:0 0 12px 0; font-size:1.2em; color:#fbbf24; }
    .daily-tally-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
    .daily-tally-card { background:rgba(255,255,255,0.06); border-radius:8px; padding:10px; text-align:center; }
    .daily-tally-card.highlight { border:2px solid #fbbf24; }
    .daily-model { font-size:0.78em; opacity:0.85; margin-bottom:4px; }
    .daily-acc { font-size:1.35em; font-weight:700; }
    .daily-rec { font-size:0.8em; opacity:0.8; }
    .model-label {
        font-size: 0.9em;
        opacity: 0.8;
        margin-bottom: 5px;
    }
    .model-perf {
        font-size: 1.8em;
        font-weight: bold;
        color: #fbbf24;
    }
    .model-record {
        font-size: 0.9em;
        opacity: 0.8;
        margin-top: 5px;
    }
    .games-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9em;
    }
    .games-table th {
        background: rgba(255, 255, 255, 0.1);
        padding: 10px;
        text-align: left;
        font-weight: bold;
        color: #fbbf24;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
    }
    .games-table td {
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    .games-table tr:hover {
        background: rgba(255, 255, 255, 0.05);
    }
    .score {
        font-weight: bold;
    }
    .winner {
        color: #10b981;
    }
    .loser {
        color: #ef4444;
    }
    .prob-correct {
        color: #10b981;
        font-weight: bold;
    }
    .prob-wrong {
        color: #ef4444;
    }
    .no-data {
        text-align: center;
        padding: 60px 20px;
        font-size: 1.3em;
        opacity: 0.7;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Week by Week Results</h1>
    
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}" class="tab active">🎯 Results</a>
    </div>
    
    {% if daily_tally %}
    <div class="daily-tally">
        <h2>Last Night's Tally — {{ daily_tally_date }} ({{ daily_tally_games }} games)</h2>
        <div class="daily-tally-grid">
            {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')]
            {% set m = daily_tally[m_key] %}
            <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                <div class="daily-model">{{ m_label }}</div>
                {% if m.total > 0 %}
                <div class="daily-acc">{{ m.accuracy }}%</div>
                <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                {% else %}
                <div class="daily-acc" style="color:#94a3b8;">—</div>
                <div class="daily-rec">no graded games</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% else %}
    <div class="daily-tally" style="text-align:center;">
        No graded games for {{ daily_tally_date }}.
    </div>
    {% endif %}
    {% if weekly_tally %}
    <div class="daily-tally">
        <h2>Last 7 Days Tally — {{ weekly_tally_date_range }} ({{ weekly_tally_games }} games)</h2>
        <div class="daily-tally-grid">
            {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
            {% set m = weekly_tally[m_key] %}
            <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                <div class="daily-model">{{ m_label }}</div>
                {% if m.total > 0 %}
                <div class="daily-acc">{{ m.accuracy }}%</div>
                <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                {% else %}
                <div class="daily-acc" style="color:#94a3b8;">—</div>
                <div class="daily-rec">no graded games</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% else %}
    <div class="daily-tally" style="text-align:center;">
        No graded games for last 7 days.
    </div>
    {% endif %}
    {% if weekly_results and overall_stats %}
        {% set ens = overall_stats.ensemble %}
        <!-- Overall per-model performance -->
        <div style="background: linear-gradient(135deg, #1e293b, #0f172a); border: 2px solid #10b981; border-radius: 15px; padding: 25px; margin-bottom: 25px;">
            <h2 style="text-align: center; margin: 0 0 20px 0; font-size: 1.8em;">🏆 Overall Model Performance &mdash; {{ ens.total }} Games</h2>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px;">
                {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
                {% set m = overall_stats[m_key] %}
                <div style="background: rgba(255,255,255,0.08); border-radius: 10px; padding: 15px; text-align: center; {% if m_key == 'ensemble' %}border: 2px solid #fbbf24; grid-column: span 4;{% endif %}">
                    <div style="font-size: 0.9em; opacity: 0.8; margin-bottom: 4px;">{{ m_label }}</div>
                    <div style="font-size: {% if m_key == 'ensemble' %}2.8em{% else %}1.9em{% endif %}; font-weight: bold; color: {% if m.accuracy >= 55 %}#10b981{% elif m.accuracy >= 50 %}#fbbf24{% else %}#ef4444{% endif %};">{{ m.accuracy }}%</div>
                    <div style="font-size: 0.9em; opacity: 0.85;">{{ m.correct }}-{{ m.total - m.correct }}</div>
                </div>
                {% endfor %}
            </div>
            <div style="border-top: 1px solid rgba(255,255,255,0.15); padding-top: 15px;"></div>
        </div>
        {% for week_num in weekly_results|dictsort(reverse=true) %}
        {% set week_data = weekly_results[week_num[0]] %}
        {% set best_acc = [week_data.glicko2.accuracy, week_data.trueskill.accuracy, week_data.elo.accuracy, week_data.xgboost.accuracy, week_data.ensemble.accuracy]|max %}
        <div class="week-section">
            <div class="week-header">
                <div class="week-title">🏈 Week {{ week_num[0] }}</div>
                <div style="opacity: 0.8;">{{ week_data.games|length }} Games</div>
            </div>
            <div class="week-models">
                {% for wm_label, wm_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
                {% set wm = week_data[wm_key] %}
                <div class="week-model-card {% if wm.accuracy == best_acc %}best{% endif %}">
                    <div class="model-label">{{ wm_label }}</div>
                    <div class="model-perf">{{ wm.accuracy }}%</div>
                    <div class="model-record">{{ wm.correct }}-{{ wm.total - wm.correct }}</div>
                </div>
                {% endfor %}
            </div>
            <table class="games-table">
                <thead><tr>
                    <th>Date</th><th>Matchup</th><th>Score</th>
                    <th>Grinder2</th><th>Takedown</th><th>Edge</th>
                    <th>XSharp</th><th>Sharp Consensus</th>
                </tr></thead>
                <tbody>
                    {% for game in week_data.games %}
                    <tr>
                        <td>{{ game.date }}</td>
                        <td>
                            <span class="{% if game.away_score > game.home_score %}winner{% else %}loser{% endif %}">{{ game.away }}</span> @
                            <span class="{% if game.home_score > game.away_score %}winner{% else %}loser{% endif %}">{{ game.home }}</span>
                        </td>
                        <td class="score">{{ game.away_score }} - {{ game.home_score }}</td>
                        <td class="{% if game.glicko2_correct %}prob-correct{% elif game.glicko2_correct == false %}prob-wrong{% endif %}">{% if game.glicko2_correct is not none %}{% if game.glicko2_correct %}✅{% else %}❌{% endif %} {{ game.glicko2_prob }}%{% else %}—{% endif %}</td>
                        <td class="{% if game.trueskill_correct %}prob-correct{% elif game.trueskill_correct == false %}prob-wrong{% endif %}">{% if game.trueskill_correct is not none %}{% if game.trueskill_correct %}✅{% else %}❌{% endif %} {{ game.trueskill_prob }}%{% else %}—{% endif %}</td>
                        <td class="{% if game.elo_correct %}prob-correct{% elif game.elo_correct == false %}prob-wrong{% endif %}">{% if game.elo_correct is not none %}{% if game.elo_correct %}✅{% else %}❌{% endif %} {{ game.elo_prob }}%{% else %}—{% endif %}</td>
                        <td class="{% if game.xgb_correct %}prob-correct{% elif game.xgb_correct == false %}prob-wrong{% endif %}">{% if game.xgb_correct is not none %}{% if game.xgb_correct %}✅{% else %}❌{% endif %} {{ game.xgb_prob }}%{% else %}—{% endif %}</td>
                        <td class="{% if game.ens_correct %}prob-correct{% elif game.ens_correct == false %}prob-wrong{% endif %}">{% if game.ens_correct is not none %}{% if game.ens_correct %}✅{% else %}❌{% endif %} {{ game.ens_prob }}%{% else %}—{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
    {% else %}
        <div class="no-data">No completed NFL games available yet.</div>
    {% endif %}
""")

# ============================================================================
# ROUTES
# ============================================================================

# Verified season-to-date accuracy numbers shown on the landing page.
# Update these manually when you have fresh backtested results.
_LANDING_ACCURACY = {
    'NHL':  77.0,
    'NFL':  56.8,
    'NBA':  68.5,
    'MLB':  58.0,
    'NCAAB': 65.0,
}
# Month/day windows for "live" status on landing page.
_SEASON_WINDOWS = {
    'NHL':   ((10, 1), (6, 30)),
    'NBA':   ((10, 1), (6, 30)),
    'MLB':   ((3, 20), (11, 5)),
    'NFL':   ((9, 1), (2, 20)),
    'NCAAF': ((8, 15), (1, 20)),
    'NCAAB': ((11, 1), (4, 15)),
    'NCAAW': ((11, 1), (4, 15)),
    'WNBA':  ((5, 1), (10, 15)),
    'SOCCER':((8, 1), (6, 30)),
}
_LANDING_SPORT_ORDER = ['NHL', 'NBA', 'NCAAB', 'NCAAW', 'MLB', 'SOCCER', 'NFL', 'NCAAF', 'WNBA']
_LANDING_SPORT_SHORT = {
    'NCAAB': 'NCAAB',
    'NCAAW': 'NCAAW',
    'NCAAF': 'NCAAF',
    'SOCCER': 'Soccer',
}


def get_landing_accuracy(sport):
    """Return hardcoded accuracy for the landing page stats bar."""
    return _LANDING_ACCURACY.get(sport, 0.0)
def _season_window_for_date(sport, today):
    window = _SEASON_WINDOWS.get(sport)
    if not window:
        return None, None
    (sm, sd), (em, ed) = window
    if (sm, sd) <= (em, ed):
        start = datetime(today.year, sm, sd)
        end = datetime(today.year, em, ed)
    else:
        if (today.month, today.day) >= (sm, sd):
            start = datetime(today.year, sm, sd)
            end = datetime(today.year + 1, em, ed)
        else:
            start = datetime(today.year - 1, sm, sd)
            end = datetime(today.year, em, ed)
    return start, end

def get_season_status(sport, today=None):
    today = today or datetime.now()
    start, end = _season_window_for_date(sport, today)
    if not start or not end:
        return 'Live Now', True
    if start <= today <= end:
        return 'Live Now', True
    if today < start:
        days_until = (start - today).days
        return ('Starting Soon' if days_until <= 60 else 'Offseason'), False
    next_start = datetime(start.year + 1, start.month, start.day)
    days_until = (next_start - today).days
    return ('Starting Soon' if days_until <= 60 else 'Offseason'), False

def _weekly_banner_message_for_sport(sport, start_dt, end_dt):
    sport_info = SPORTS.get(sport, {'name': sport})
    sport_name = sport_info.get('name', sport)
    daily_results = _banner_daily_results_for_range(sport, start_dt, end_dt)
    if not daily_results:
        return None, None
    weekly_tally = compute_model_tally_for_range(daily_results, start_dt, end_dt)
    if not weekly_tally:
        return None, None
    model_labels = [
        ('glicko2', 'Grinder2'),
        ('trueskill', 'Takedown'),
        ('elo', 'Edge'),
        ('xgboost', 'XSharp'),
        ('ensemble', 'Consensus'),
    ]
    best_key = None
    best_label = None
    best_acc = None
    best_total = 0
    best_correct = 0
    for key, label in model_labels:
        data = weekly_tally.get(key) or {}
        total = data.get('total', 0)
        correct = data.get('correct', 0)
        if total <= 0:
            continue
        acc = data.get('accuracy')
        if acc is None:
            acc = round((correct / total) * 100, 1) if total > 0 else None
        if acc is None:
            continue
        if best_acc is None or acc > best_acc or (acc == best_acc and total > best_total):
            best_key = key
            best_label = label
            best_acc = acc
            best_total = total
            best_correct = correct
    if best_acc is None:
        return None, None
    msg = f"{sport_name} {best_label}: {best_acc}% ({best_correct}-{best_total - best_correct})"
    return msg, best_acc

def _build_weekly_banner_messages(sport_keys, days=7, max_items=4):
    if not sport_keys:
        return []
    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=max(days, 1) - 1)
    ranked = []
    for key in sport_keys:
        msg, acc = _weekly_banner_message_for_sport(key, start_dt, end_dt)
        if msg and acc is not None:
            ranked.append((acc, msg))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in ranked[:max_items]]

def _get_cached_weekly_banner_messages(sport_keys, days=7, max_items=4):
    now_ts = _time.time()
    cached = _LANDING_BANNER_CACHE
    if cached and (now_ts - cached.get('ts', 0)) < _LANDING_BANNER_TTL:
        return cached.get('messages', [])
    try:
        messages = _build_weekly_banner_messages(sport_keys, days=days, max_items=max_items)
    except Exception as _e:
        logger.debug(f"Weekly banner build failed: {_e}")
        return cached.get('messages', [])
    _LANDING_BANNER_CACHE.update({'ts': now_ts, 'messages': messages})
    return messages

# ── Stripe payment link — replace with your link from dashboard.stripe.com/payment-links
STRIPE_DONATION_URL = 'https://buy.stripe.com/8x228sabu7aV7uj43nao800'
CONTACT_EMAIL = 'underdogsbetemail@gmail.com'
_SOCIAL_ICONS = {
    'X': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M18.244 2H21l-6.588 7.53L22 22h-6.828l-5.35-6.16L4.59 22H2l7.03-8.04L2 2h6.93l4.84 5.6L18.244 2zm-1.2 18h1.9L7.04 4H5.02l12.02 16z"/></svg>',
    'Instagram': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M7.5 2C4.46 2 2 4.46 2 7.5v9C2 19.54 4.46 22 7.5 22h9c3.04 0 5.5-2.46 5.5-5.5v-9C22 4.46 19.54 2 16.5 2h-9zm9 2c1.93 0 3.5 1.57 3.5 3.5v9c0 1.93-1.57 3.5-3.5 3.5h-9C5.57 20 4 18.43 4 16.5v-9C4 5.57 5.57 4 7.5 4h9zm-4.5 3a5 5 0 100 10 5 5 0 000-10zm0 2a3 3 0 110 6 3 3 0 010-6zm5.25-.75a1.25 1.25 0 11-2.5 0 1.25 1.25 0 012.5 0z"/></svg>',
    'Facebook': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M22 12.07C22 6.49 17.52 2 11.94 2S1.88 6.49 1.88 12.07c0 4.99 3.66 9.12 8.44 9.88v-6.99H7.9v-2.89h2.42V9.41c0-2.4 1.43-3.72 3.62-3.72 1.05 0 2.15.19 2.15.19v2.36h-1.21c-1.2 0-1.58.74-1.58 1.5v1.8h2.69l-.43 2.89h-2.26v6.99c4.78-.76 8.44-4.89 8.44-9.88z"/></svg>',
    'TikTok': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M21 8.5c-1.9-.1-3.4-1.7-3.5-3.6V2h-3.2v13.1c0 1.4-1.1 2.5-2.5 2.5s-2.5-1.1-2.5-2.5 1.1-2.5 2.5-2.5c.3 0 .6.1.9.1V9.5c-.3 0-.6-.1-.9-.1-3.1 0-5.6 2.5-5.6 5.6s2.5 5.6 5.6 5.6 5.6-2.5 5.6-5.6V9.4c1 1 2.4 1.6 3.9 1.6V8.5z"/></svg>',
    'YouTube': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M23.5 6.2a3 3 0 00-2.1-2.1C19.5 3.5 12 3.5 12 3.5s-7.5 0-9.4.6a3 3 0 00-2.1 2.1A31.4 31.4 0 000 12a31.4 31.4 0 00.5 5.8 3 3 0 002.1 2.1c1.9.6 9.4.6 9.4.6s7.5 0 9.4-.6a3 3 0 002.1-2.1A31.4 31.4 0 0024 12a31.4 31.4 0 00-.5-5.8zM9.7 15.5V8.5l6.2 3.5-6.2 3.5z"/></svg>',
}
SOCIAL_LINKS = [
    {'label': 'X', 'url': 'https://x.com/underdogs_bet', 'icon': _SOCIAL_ICONS['X']},
    {'label': 'Instagram', 'url': 'https://instagram.com/underdogs.bet', 'icon': _SOCIAL_ICONS['Instagram']},
    {'label': 'Facebook', 'url': 'https://facebook.com/underdogs.bet', 'icon': _SOCIAL_ICONS['Facebook']},
    {'label': 'TikTok', 'url': 'https://tiktok.com/@underdog.bet', 'icon': _SOCIAL_ICONS['TikTok']},
    {'label': 'YouTube', 'url': 'https://youtube.com/@Underdogsbet', 'icon': _SOCIAL_ICONS['YouTube']},
]
GA_TRACKING_ID = _os.environ.get('GA_TRACKING_ID', 'G-JWHPL9X6SY')
GA_PROPERTY_ID = _os.environ.get('GA_PROPERTY_ID', '530749291')
GA_CREDENTIALS_JSON = _os.environ.get('GA_CREDENTIALS_JSON')
GA_OAUTH_CLIENT_ID = _os.environ.get('GA_OAUTH_CLIENT_ID')
GA_OAUTH_CLIENT_SECRET = _os.environ.get('GA_OAUTH_CLIENT_SECRET')
GA_OAUTH_REFRESH_TOKEN = _os.environ.get('GA_OAUTH_REFRESH_TOKEN')

def _fetch_ga_traffic():
    if not GA_PROPERTY_ID:
        return None, "GA_PROPERTY_ID not configured."
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, OrderBy
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except Exception:
        return None, "Google Analytics client libraries not installed."
    try:
        creds = None
        credential_errors = []
        if GA_CREDENTIALS_JSON:
            try:
                raw = GA_CREDENTIALS_JSON.strip()
                if raw.startswith('{'):
                    creds = service_account.Credentials.from_service_account_info(
                        json.loads(raw),
                        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
                    )
                else:
                    creds = service_account.Credentials.from_service_account_file(
                        GA_CREDENTIALS_JSON,
                        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
                    )
            except Exception as exc:
                credential_errors.append(f"Service account load failed: {exc}")
                creds = None
        if not creds and GA_OAUTH_CLIENT_ID and GA_OAUTH_CLIENT_SECRET and GA_OAUTH_REFRESH_TOKEN:
            try:
                creds = Credentials(
                    None,
                    refresh_token=GA_OAUTH_REFRESH_TOKEN.strip(),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=GA_OAUTH_CLIENT_ID,
                    client_secret=GA_OAUTH_CLIENT_SECRET,
                    scopes=["https://www.googleapis.com/auth/analytics.readonly"],
                )
                creds.refresh(Request())
            except Exception as exc:
                credential_errors.append(f"OAuth refresh failed: {exc}")
                creds = None
        if not creds:
            return None, "; ".join(credential_errors) if credential_errors else "GA credentials not configured."
        client = BetaAnalyticsDataClient(credentials=creds)
    except Exception as exc:
        return None, f"Failed to load GA credentials: {exc}"

    property_path = f"properties/{GA_PROPERTY_ID}"
    today_dt = _traffic_now()
    today_str = today_dt.strftime('%Y-%m-%d')
    start_14 = (today_dt - timedelta(days=13)).strftime('%Y-%m-%d')
    start_7 = (today_dt - timedelta(days=6)).strftime('%Y-%m-%d')

    try:
        daily_report = client.run_report(
            property=property_path,
            date_ranges=[DateRange(start_date=start_14, end_date=today_str)],
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
        )
        daily_visits = []
        for row in daily_report.rows:
            raw_date = row.dimension_values[0].value
            date_fmt = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            count = int(row.metric_values[0].value or 0)
            daily_visits.append({'date': date_fmt, 'count': count})
        today_visits = next((d['count'] for d in daily_visits if d['date'] == today_str), 0)
        week_visits = sum(d['count'] for d in daily_visits if d['date'] >= start_7)

        total_report = client.run_report(
            property=property_path,
            date_ranges=[DateRange(start_date="2005-01-01", end_date=today_str)],
            metrics=[Metric(name="sessions")],
        )
        total_visits = int(total_report.rows[0].metric_values[0].value) if total_report.rows else 0

        top_report = client.run_report(
            property=property_path,
            date_ranges=[DateRange(start_date=start_14, end_date=today_str)],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="sessions")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=15,
        )
        top_endpoints = []
        for row in top_report.rows:
            path = row.dimension_values[0].value
            count = int(row.metric_values[0].value or 0)
            top_endpoints.append({'endpoint': path, 'count': count})

        return {
            'today_visits': today_visits,
            'week_visits': week_visits,
            'total_visits': total_visits,
            'top_endpoints': top_endpoints,
            'daily_visits': sorted(daily_visits, key=lambda x: x['date'], reverse=True),
        }, None
    except Exception:
        return None, "Failed to fetch Google Analytics data."

_SPORT_ML_UNITS_CACHE: dict = {'ts': 0, 'items': []}
_SPORT_ML_UNITS_TTL = 1800  # 30 min

_SPORT_ICONS_LANDING = {
    'NHL': '🏒', 'NBA': '🏀', 'NFL': '🏈', 'MLB': '⚾',
    'NCAAB': '🎓', 'NCAAF': '🏟️', 'WNBA': '🏀', 'SOCCER': '⚽', 'NCAAW': '🏀',
}


def _get_sport_ml_units_banner():
    """Compute flat-bet consensus ML units per sport from graded predictions."""
    now_ts = _time.time()
    cached = _SPORT_ML_UNITS_CACHE
    if cached and (now_ts - cached.get('ts', 0)) < _SPORT_ML_UNITS_TTL:
        return cached.get('items', [])
    items = []
    try:
        conn = get_db_connection()
        rows = conn.execute('''
            SELECT
                p.sport,
                SUM(CASE
                    WHEN p.win_probability > 0.5 AND g.home_score > g.away_score THEN 1.0
                    WHEN p.win_probability <= 0.5 AND g.away_score > g.home_score THEN 1.0
                    ELSE -1.0
                END) AS units,
                COUNT(*) AS total,
                SUM(CASE
                    WHEN p.win_probability > 0.5 AND g.home_score > g.away_score THEN 1
                    WHEN p.win_probability <= 0.5 AND g.away_score > g.home_score THEN 1
                    ELSE 0
                END) AS wins
            FROM predictions p
            JOIN games g ON p.game_id = g.game_id
            WHERE g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND p.win_probability IS NOT NULL
              AND g.home_score != g.away_score
              AND p.sport IS NOT NULL
            GROUP BY p.sport
            ORDER BY p.sport
        ''').fetchall()
        conn.close()
        sport_order = ['NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']
        rows_by_sport = {r[0]: r for r in rows}
        for sport in sport_order:
            row = rows_by_sport.get(sport)
            if not row:
                continue
            total = int(row[2]) if row[2] else 0
            if total < 5:
                continue
            units = float(row[1]) if row[1] is not None else 0.0
            wins  = int(row[3]) if row[3] else 0
            losses = total - wins
            icon = _SPORT_ICONS_LANDING.get(sport, '🏆')
            sign = '+' if units >= 0 else ''
            items.append({
                'label':    f"{icon} {sport} Moneyline",
                'units':    f"{sign}{units:.1f}u",
                'record':   f"{wins}-{losses}",
                'positive': units >= 0,
            })
    except Exception as _ue:
        logger.debug(f"ML units banner failed: {_ue}")
    _SPORT_ML_UNITS_CACHE.update({'ts': now_ts, 'items': items})
    return items


@app.route('/')
def landing_page():
    """Landing page — redesigned with hero, stats, donation, and sport cards"""
    log_site_visit('/')
    nhl_accuracy = get_landing_accuracy('NHL')
    nfl_accuracy = get_landing_accuracy('NFL')
    nba_accuracy = get_landing_accuracy('NBA')
    games_graded = 0
    predictions_logged = 0
    try:
        _conn = get_db_connection()
        games_graded = _conn.execute(
            "SELECT COUNT(*) FROM games WHERE home_score IS NOT NULL AND away_score IS NOT NULL"
        ).fetchone()[0]
        predictions_logged = _conn.execute(
            "SELECT COUNT(*) FROM predictions"
        ).fetchone()[0]
        _conn.close()
    except Exception as _e:
        logger.debug(f"Landing stats query failed: {_e}")
    today = datetime.now()
    landing_sports = []
    for sport_key in _LANDING_SPORT_ORDER:
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        info = SPORTS.get(sport_key)
        if not info:
            continue
        status_text, is_live = get_season_status(sport_key, today=today)
        landing_sports.append({
            'key': sport_key,
            'seo_slug': SPORT_SEO_SLUGS.get(sport_key, sport_key.lower() + '-picks'),
            'icon': info['icon'],
            'name': _LANDING_SPORT_SHORT.get(sport_key, info['name']),
            'status': status_text,
            'is_live': is_live,
        })
    sports_covered = len(landing_sports)
    banner_sports = [s['key'] for s in landing_sports]
    weekly_banner_messages = list(_MANUAL_BANNER_ITEMS)
    units_banner_items = _get_sport_ml_units_banner()

    # Build "Today's Top Picks" from live sports with upcoming games
    todays_picks = []
    try:
        _tp_tz = ZoneInfo('America/New_York')
        _tp_today = datetime.now(_tp_tz).strftime('%Y-%m-%d')
    except Exception:
        _tp_today = datetime.now().strftime('%Y-%m-%d')
    for _tp_sport in ['NHL', 'NBA', 'MLB', 'SOCCER']:
        if _tp_sport == 'SOCCER' and not SOCCER_ENABLED:
            continue
        try:
            _tp_preds = get_upcoming_predictions(_tp_sport)
            for _tp in _tp_preds:
                if _tp.get('home_score') is not None:
                    continue  # skip completed
                if _tp.get('game_date') != _tp_today:
                    continue
                _ens = _tp.get('ensemble_prob')
                if _ens is None:
                    continue
                _home = _tp.get('home_team_id', '')
                _away = _tp.get('away_team_id', '')
                _pick = _home if _ens > 50 else _away
                todays_picks.append({
                    'away': _away, 'home': _home,
                    'pick': _pick, 'prob': round(_ens, 1),
                    'sport': _tp_sport,
                    'slug': SPORT_SEO_SLUGS.get(_tp_sport, ''),
                })
                if len(todays_picks) >= 4:
                    break
        except Exception:
            pass
        if len(todays_picks) >= 4:
            break

    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>underdogs.bet – Daily AI Sports Picks &amp; Betting Predictions</title>
    <meta name="description" content="Get accurate AI-powered picks for NHL, NBA, MLB, NFL and more. Full spreads, totals, and score predictions updated daily. Start free — upgrade for premium edges.">
    <meta property="og:title" content="underdogs.bet – Daily AI Sports Picks &amp; Betting Predictions">
    <meta property="og:description" content="AI-powered daily picks for NHL, NBA, MLB, NFL and more. Spreads, totals, score predictions. Free moneyline picks — premium for full card.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://www.underdogs.bet/">
    <meta property="og:image" content="https://www.underdogs.bet/static/Logo.PNG">
    <meta property="og:site_name" content="underdogs.bet">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Free AI Sports Picks &amp; Betting Predictions | underdogs.bet">
    <meta name="twitter:description" content="Get free daily sports picks powered by AI models. NBA, NHL, MLB predictions with win probabilities, spreads &amp; totals. No subscriptions. Always free.">
    <meta name="twitter:image" content="https://www.underdogs.bet/static/Logo.PNG">
    <link rel="canonical" href="https://www.underdogs.bet{{ request.path }}">
    <link rel="icon" type="image/png" href="/static/Logo.PNG">
    <link rel="apple-touch-icon" href="/static/Logo.PNG">
    {% if ga_tracking_id %}
    <!-- Google Analytics gtag.js snippet -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={{ ga_tracking_id }}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){dataLayer.push(arguments);}
      gtag('js', new Date());
      gtag('config', 'G-JWHPL9X6SY');
    </script>
    {% endif %}
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "underdogs.bet",
      "url": "https://www.underdogs.bet",
      "logo": "https://www.underdogs.bet/static/Logo.PNG",
      "description": "Free AI-powered sports picks and betting predictions for NBA, NHL, MLB and more.",
      "sameAs": [
        "https://x.com/underdogs_bet",
        "https://instagram.com/underdogs.bet",
        "https://facebook.com/underdogs.bet",
        "https://tiktok.com/@underdog.bet",
        "https://youtube.com/@Underdogsbet"
      ]
    }
    </script>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"WebSite","name":"underdogs.bet","url":"https://www.underdogs.bet","potentialAction":{"@type":"SearchAction","target":"https://www.underdogs.bet/?q={search_term_string}","query-input":"required name=search_term_string"}}
    </script>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Underdogs Edge Premium","description":"AI-powered sports betting picks with spreads, totals, and score projections across 9 sports.","brand":{"@type":"Brand","name":"underdogs.bet"},"image":["https://www.underdogs.bet/static/Logo.PNG"],"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.7","bestRating":"5","ratingCount":"48"},"review":{"@type":"Review","author":{"@type":"Person","name":"underdogs.bet user"},"reviewRating":{"@type":"Rating","ratingValue":"5","bestRating":"5"},"reviewBody":"Accurate AI picks with full transparency. Spreads and totals are consistently on point."},"offers":[{"@type":"Offer","price":"19.99","priceCurrency":"USD","availability":"https://schema.org/InStock","priceValidUntil":"2027-12-31","name":"Monthly","url":"https://www.underdogs.bet/plans","hasMerchantReturnPolicy":{"@type":"MerchantReturnPolicy","applicableCountry":"US","returnPolicyCategory":"https://schema.org/MerchantReturnNotPermitted"},"shippingDetails":{"@type":"OfferShippingDetails","shippingRate":{"@type":"MonetaryAmount","value":"0","currency":"USD"},"shippingDestination":{"@type":"DefinedRegion","addressCountry":"US"},"deliveryTime":{"@type":"ShippingDeliveryTime","handlingTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"},"transitTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"}}}},{"@type":"Offer","price":"149.99","priceCurrency":"USD","availability":"https://schema.org/InStock","priceValidUntil":"2027-12-31","name":"Yearly","url":"https://www.underdogs.bet/plans","hasMerchantReturnPolicy":{"@type":"MerchantReturnPolicy","applicableCountry":"US","returnPolicyCategory":"https://schema.org/MerchantReturnNotPermitted"},"shippingDetails":{"@type":"OfferShippingDetails","shippingRate":{"@type":"MonetaryAmount","value":"0","currency":"USD"},"shippingDestination":{"@type":"DefinedRegion","addressCountry":"US"},"deliveryTime":{"@type":"ShippingDeliveryTime","handlingTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"},"transitTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"}}}}]}
    </script>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        :root{
            --gold:#fbbf24;--gold2:#f59e0b;
            --green:#10b981;--red:#ef4444;
            --bg:#0f172a;--surface:rgba(255,255,255,0.05);
            --border:rgba(255,255,255,0.1);
        }
        body{
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            background: #0f172a url('/static/Logo.PNG') center center / cover no-repeat;
            color:#fff;
            min-height:100vh;
            overflow-x:hidden;
            position:relative;
        }
        body::before{
            content:'';
            position:fixed;
            inset:0;
            background:rgba(7,10,20,0.65);
            z-index:0;
        }
        body > *{position:relative;z-index:1;}

        /* ── Navbar ── */
        .navbar{
            background:rgba(7,10,20,0.35);
            padding: 14px 28px;
            border-bottom: none;
            box-shadow: none;
            backdrop-filter: blur(16px);
            position: sticky;
            top: 0;
            z-index: 1000;
        }
        .navbar-content {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
        }
        .logo-img {
            height: 44px;
            width: auto;
            display: block;
        }
        .hamburger {
            display: flex;
            flex-direction: column;
            cursor: pointer;
            gap: 5px;
            padding: 7px;
            border-radius: 10px;
            background: transparent;
            border: none;
        }
        .hamburger:hover {
            background: rgba(255, 255, 255, 0.08);
        }
        .hamburger span {
            width: 24px;
            height: 2px;
            background: #cbd5e1;
            border-radius: 2px;
            transition: 0.3s;
        }
        .nav-links {
            position: absolute;
            top: 64px;
            right: 22px;
            background: rgba(7, 10, 20, 0.98);
            flex-direction: column;
            gap: 2px;
            padding: 10px;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px;
            display: none;
            min-width: 200px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.4);
        }
        .nav-links.active { display: flex; }
        .nav-links a {
            color: #fff;
            text-decoration: none;
            font-weight: 600;
            font-size: 0.88em;
            transition: all 0.2s;
            white-space: nowrap;
            padding: 8px 10px;
            border-radius: 8px;
        }
        .nav-links a:hover { color: #fff; background: rgba(255,255,255,0.08); }
        .nav-links a.active { color: #fff; background: rgba(255,255,255,0.12); }
        .nav-section-title { display: none; }
        .nav-divider { display: none; }
        .nav-donate-btn {
            background: linear-gradient(135deg, #fbbf24, #f59e0b);
            color: #fff !important;
            font-weight: 800 !important;
            padding: 8px 14px;
            border-radius: 10px;
            transition: opacity 0.2s !important;
            white-space: nowrap;
        }
        .nav-donate-btn:hover { opacity: 0.9; color: #fff !important; }

        /* ── Hero ── */
        .hero{
            text-align:center;
            padding:90px 30px 60px;
            position:relative;
            overflow:hidden;
        }
        .hero::before{
            content:'';
            position:absolute;inset:0;
            background:radial-gradient(ellipse 80% 60% at 50% 0%,rgba(99,102,241,.25) 0%,transparent 70%);
            pointer-events:none;
        }
        .hero-badge{
            display:inline-flex;align-items:center;gap:8px;
            background:rgba(16,185,129,.15);border:1px solid rgba(255,255,255,.35);
            color:#fff;font-size:.82em;font-weight:700;
            padding:6px 16px;border-radius:20px;margin:18px auto 0;
            letter-spacing:.5px;
        }
        .hero h1{
            font-size:clamp(2.4em,6vw,4.2em);
            font-weight:900;
            line-height:1.1;
            margin-bottom:18px;
            color:#fff;
        }
        .hero-subhead{
            font-size:clamp(1.05em,2.6vw,1.35em);
            color:#fff;
            max-width:600px;
            margin:0 0 28px;
            line-height:1.6;
            font-weight:700;
        }
        .hero-ctas{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;}
        .btn-primary{
            background:linear-gradient(135deg,#6366f1,#4f46e5);
            color:#fff;font-weight:700;font-size:1em;
            padding:14px 32px;border-radius:10px;
            text-decoration:none;transition:transform .2s,box-shadow .2s;
            box-shadow:0 4px 20px rgba(99,102,241,.4);
        }
        .btn-primary:hover{transform:translateY(-2px);box-shadow:0 6px 28px rgba(99,102,241,.5);}
        .btn-donate-hero{
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            color:#fff;font-weight:700;font-size:1em;
            padding:14px 32px;border-radius:10px;
            text-decoration:none;transition:transform .2s,box-shadow .2s;
            box-shadow:0 4px 20px rgba(251,191,36,.3);
        }
        .btn-donate-hero:hover{transform:translateY(-2px);box-shadow:0 6px 28px rgba(251,191,36,.45);}

        /* ── Weekly banner ── */
        .weekly-banner{
            margin:-8px auto 18px;
            max-width:1200px;
            width:100%;
            background:linear-gradient(90deg,rgba(15,23,42,0.95),rgba(30,41,59,0.95));
            border:1px solid rgba(251,191,36,0.35);
            border-radius:16px;
            padding:14px 18px;
            display:flex;
            flex-direction:column;
            gap:10px;
            align-items:center;
            text-align:center;
            box-shadow:0 8px 24px rgba(0,0,0,0.25);
            overflow:hidden;
        }
        .weekly-banner-label{
            font-size:0.7em;
            text-transform:uppercase;
            letter-spacing:0.7px;
            color:#fff;
            font-weight:800;
        }
        .weekly-banner-lines{
            width:100%;
            overflow:hidden;
        }
        .weekly-banner-track{
            display:inline-flex;
            align-items:center;
            gap:12px;
            width:max-content;
            white-space:nowrap;
            will-change:transform;
            animation:weekly-marquee 26s linear infinite;
        }
        .weekly-banner-line{
            background:rgba(255,255,255,0.06);
            border:1px solid rgba(255,255,255,0.12);
            border-radius:999px;
            padding:6px 14px;
            font-size:0.95em;
            font-weight:700;
            color:#fff;
            white-space:nowrap;
            display:flex;
            gap:10px;
            align-items:center;
            flex:0 0 auto;
        }
        @keyframes weekly-marquee{
            0%{transform:translateX(0);}
            100%{transform:translateX(-50%);}
        }

        /* ── Free banner ── */
        .free-banner{
            max-width:860px;margin:60px auto 0;
            background:linear-gradient(135deg,rgba(16,185,129,.15),rgba(5,150,105,.1));
            border:1px solid rgba(16,185,129,.35);
            border-radius:16px;padding:28px 36px;
            display:flex;gap:12px;align-items:center;justify-content:center;
            flex-direction:column;text-align:center;
        }
        .free-icon{font-size:2.2em;display:inline-flex;align-items:center;justify-content:center;}
        .free-title{font-size:1.15em;font-weight:800;color:#fff;margin-bottom:6px;}
        .free-body{font-size:.93em;color:#fff;line-height:1.6;max-width:620px;}

        /* ── Sports grid ── */
        .section{padding:120px 30px 70px;max-width:1200px;margin:0 auto;}
        .section-title{
            text-align:center;font-size:1.9em;font-weight:800;
            margin-bottom:8px;
        }
        .section-title.secondary{
            font-size:1.4em;
            margin-top:22px;
        }
        .section-sub{text-align:center;color:#fff;font-size:.93em;margin-bottom:40px;}
        .sport-slider{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px 0 32px;}
        .slider-arrow{background:rgba(255,255,255,0.12);border:2px solid rgba(255,255,255,0.6);color:#fff;font-size:1.3em;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;user-select:none;flex-shrink:0;}
        .slider-arrow:hover{background:rgba(255,255,255,0.25);transform:scale(1.08);}
        .sport-badges{display:flex;gap:8px;overflow-x:auto;padding:4px;max-width:860px;scroll-behavior:smooth;}
        .sport-pill{display:flex;align-items:center;gap:8px;padding:8px 14px;border-radius:20px;text-decoration:none;background:rgba(255,255,255,0.08);border:2px solid rgba(255,255,255,0.15);color:#fff;font-size:.82em;font-weight:700;white-space:nowrap;transition:all .2s;}
        .sport-pill:hover{border-color:var(--gold);color:#fff;}
        .sport-pill.live{background:rgba(16,185,129,.18);border-color:rgba(16,185,129,.5);}
        .sport-pill-status{font-weight:600;opacity:.9;font-size:.7em;text-transform:uppercase;letter-spacing:.4px;color:#fff;}
        .sports-grid{
            display:grid;
            grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
            gap:16px;
        }
        .sport-card{
            background:var(--surface);border:1px solid var(--border);
            border-radius:14px;padding:28px 20px;
            text-align:center;text-decoration:none;color:inherit;
            transition:border-color .2s,transform .2s,box-shadow .2s;
            position:relative;overflow:hidden;
        }
        .sport-card:hover{border-color:var(--gold);transform:translateY(-4px);box-shadow:0 8px 24px rgba(251,191,36,.15);}
        .sport-card.live{border-color:rgba(16,185,129,.4);}
        .sport-card.live:hover{border-color:var(--green);box-shadow:0 8px 24px rgba(16,185,129,.2);}
        .live-dot{
            position:absolute;top:12px;right:12px;
            width:8px;height:8px;border-radius:50%;background:var(--green);
            box-shadow:0 0 0 3px rgba(16,185,129,.25);
            animation:pulse 1.8s infinite;
        }
        @keyframes pulse{
            0%,100%{box-shadow:0 0 0 3px rgba(16,185,129,.25);}
            50%{box-shadow:0 0 0 7px rgba(16,185,129,.0);}
        }
        .sport-icon{font-size:2.8em;margin-bottom:10px;}
        .sport-name{font-size:1.15em;font-weight:700;margin-bottom:4px;}
        .sport-status{font-size:.78em;color:#fff;text-transform:uppercase;letter-spacing:.5px;}
        .sport-status.live-text{color:#fff;font-weight:700;}

        /* ── How it works ── */
        .how-section{
            background:rgba(255,255,255,.02);
            border-top:none;
            border-bottom:none;
        }
        .steps-grid{
            display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px;
        }
        .step{
            background:var(--surface);border:1px solid var(--border);
            border-radius:14px;padding:28px 24px;text-align:center;
        }
        .step-num{
            width:42px;height:42px;border-radius:50%;
            background:linear-gradient(135deg,#6366f1,#4f46e5);
            display:flex;align-items:center;justify-content:center;
            font-weight:900;font-size:1.1em;margin:0 auto 14px;
        }
        .step-title{font-weight:700;font-size:1em;margin-bottom:8px;}
        .step-body{font-size:.86em;color:#fff;line-height:1.6;}

        /* ── Moneyline Units Banner ── */
        .units-marquee-wrap{
            overflow:hidden;
            width:100%;
            margin-top:20px;
        }
        .units-marquee-track{
            display:inline-flex;
            align-items:center;
            gap:14px;
            width:max-content;
            white-space:nowrap;
            animation:weekly-marquee 36s linear infinite;
        }
        .units-pill{
            display:inline-flex;
            align-items:center;
            gap:10px;
            padding:10px 22px;
            border-radius:999px;
            font-weight:700;
            font-size:0.93em;
            white-space:nowrap;
            flex:0 0 auto;
            border:1px solid rgba(255,255,255,0.15);
            background:rgba(255,255,255,0.06);
        }
        .units-pill.positive{
            border-color:rgba(16,185,129,0.45);
            background:rgba(16,185,129,0.12);
        }
        .units-pill.negative{
            border-color:rgba(239,68,68,0.45);
            background:rgba(239,68,68,0.12);
        }
        .up-label{color:#fff;}
        .up-units{font-size:1.05em;font-weight:900;color:#10b981;}
        .units-pill.negative .up-units{color:#ef4444;}
        .up-rec{color:#94a3b8;font-size:0.82em;}

        /* ── Footer ── */
        .site-footer{
            background:rgba(7,10,20,0.4);
            backdrop-filter:blur(16px);
            border-top:1px solid rgba(255,255,255,0.06);
            padding:18px 30px 80px;
            color:#94a3b8;
            font-size:0.78em;
        }
        .footer-inner{
            max-width:1200px;
            margin:0 auto;
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:20px;
            flex-wrap:wrap;
        }
        .footer-left{
            display:flex;
            align-items:center;
            gap:14px;
        }
        .footer-logo-img{height:32px;width:auto;}
        .footer-email a{color:#94a3b8;text-decoration:none;font-size:0.95em;}
        .footer-email a:hover{color:#fff;}
        .footer-center{
            display:flex;
            align-items:center;
            gap:12px;
            flex-wrap:wrap;
        }
        .footer-center a{color:#94a3b8;text-decoration:none;font-size:0.95em;}
        .footer-center a:hover{color:#fff;}
        .footer-center span{color:rgba(255,255,255,0.2);}
        .footer-right{color:#64748b;font-size:0.9em;white-space:nowrap;}
        .footer-socials{
            display:flex;
            align-items:center;
            gap:14px;
        }
        .footer-socials a{display:flex;opacity:0.6;transition:opacity 0.2s;}
        .footer-socials a:hover{opacity:1;}
        .footer-socials img{width:20px;height:20px;filter:brightness(0) invert(1);}

        /* ── Responsive ── */
        @media(max-width:700px){
            .footer-inner{flex-direction:column;align-items:center;text-align:center;gap:12px;}
        }
        @media(max-width:640px){
            .hero{padding:60px 20px 40px;}
            .free-banner{flex-direction:column;}
            .donate-card{padding:36px 24px;}
            .weekly-banner{margin:0 16px;}
        }
        @media (min-width: 769px) {
            body{background-attachment:fixed;}
        }
        @media (max-width: 768px) {
            body{
                background:#0f172a;
                background-attachment:scroll;
            }
            body::before{
                background:
                    linear-gradient(rgba(7,10,20,0.65), rgba(7,10,20,0.65)),
                    url('/static/Logo.PNG') center 90px / cover no-repeat;
            }
        }
        @media (max-width: 768px) {
            .nav-links {
                left: 0;
                right: 0;
                top: 70px;
                padding: 20px;
                border-radius: 0;
                border-left: none;
                border-right: none;
                border-bottom: 2px solid #334155;
            }
            .nav-links a, .nav-links .nav-group-title {
                padding: 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
        }
        .nav-group { position: relative; }
        .nav-group-title { color: #fbbf24; font-weight: 700; cursor: pointer; padding: 8px 10px; border-radius: 8px; display: block; font-size: 0.88em; }
        .nav-group-title:hover { background: rgba(255,255,255,0.08); }
        .nav-group-items { display: none; padding-left: 12px; }
        .nav-group:hover .nav-group-items, .nav-group.open .nav-group-items { display: flex; flex-direction: column; }
        .nav-group-items a { font-size: 0.84em; padding: 6px 10px !important; opacity: 0.9; }
        .nav-group-items a:hover { opacity: 1; color: #fbbf24; }
    </style>
</head>
<body>

<!-- Navbar -->
<div class="navbar">
    <div class="navbar-content">
        <a href="/" class="logo">
            <img src="/static/Logo.PNG" alt="underdogs.bet" class="logo-img">
        </a>
        <div class="hamburger" onclick="toggleMenu()">
            <span></span>
            <span></span>
            <span></span>
        </div>
        <div class="nav-links" id="navLinks">
            <a href="/" class="active">Home</a>
            <div class="nav-group" onclick="this.classList.toggle('open')">
                <span class="nav-group-title">🏆 Join</span>
                <div class="nav-group-items">
                    <a href="/plans" style="color:#fbbf24;">🏆 Premium</a>
                    {% if is_logged_in %}
                    <a href="/logout">Logout</a>
                    {% else %}
                    <a href="/login" style="color:#10b981;">Login</a>
                    <a href="/signup">Sign Up</a>
                    {% endif %}
                </div>
            </div>
            <div class="nav-group" onclick="this.classList.toggle('open')">
                <span class="nav-group-title">🏀 Sports</span>
                <div class="nav-group-items">
                    <a href="/nhl-picks">🏒 NHL</a>
                    <a href="/nba-picks">🏀 NBA</a>
                    <a href="/mlb-picks">⚾ MLB</a>
                    <a href="/nfl-picks">🏈 NFL</a>
                    <a href="/ncaab-picks">🎓 NCAAB</a>
                    <a href="/ncaaw-picks">🏀 NCAAW</a>
                    <a href="/ncaaf-picks">🏟️ NCAAF</a>
                    <a href="/wnba-picks">🏀 WNBA</a>
                    {% if soccer_enabled %}
                    <a href="/soccer-picks">⚽ Soccer</a>
                    {% endif %}
                </div>
            </div>
            <div class="nav-group" onclick="this.classList.toggle('open')">
                <span class="nav-group-title" style="color:#cbd5e1;">Resources</span>
                <div class="nav-group-items">
                    <a href="/tutorial">Tutorial</a>
                    <a href="/privacy">Privacy</a>
                    <a href="/terms">Terms</a>
                </div>
            </div>
            <div class="nav-group" onclick="this.classList.toggle('open')">
                <span class="nav-group-title" style="color:#cbd5e1;">Socials</span>
                <div class="nav-group-items">
                    <a href="https://x.com/underdogs_bet" target="_blank">X</a>
                    <a href="https://instagram.com/underdogs.bet" target="_blank">Instagram</a>
                    <a href="https://facebook.com/underdogs.bet" target="_blank">Facebook</a>
                    <a href="https://tiktok.com/@underdog.bet" target="_blank">TikTok</a>
                    <a href="https://youtube.com/@Underdogsbet" target="_blank">YouTube</a>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Hero -->
<div class="hero" style="text-align:left;padding:100px 40px 50px;">
    <h1 class="hero-slide" style="animation:slideIn 0.8s ease-out both;">AI Sports Predictions<br>With Real Results</h1>
    <p class="hero-subhead hero-slide" style="text-align:left;max-width:620px;animation:slideIn 0.8s ease-out 0.2s both;">Data-driven picks across {{ sports_covered }} sports &mdash; tracked, transparent, and updated daily. Every prediction graded with full results history.</p>
    <div class="hero-slide" style="display:flex;gap:12px;margin-top:20px;animation:slideIn 0.8s ease-out 0.4s both;">
        <a href="/nba-picks" style="background:#fff;color:#0f172a;padding:14px 28px;border-radius:8px;font-weight:800;text-decoration:none;font-size:0.95em;">View Today's Picks</a>
        <a href="/plans" style="background:transparent;color:#fff;padding:14px 28px;border-radius:8px;font-weight:700;text-decoration:none;font-size:0.95em;border:1px solid rgba(255,255,255,0.3);">Unlock Full Model</a>
    </div>
    <p class="hero-slide" style="font-size:0.78em;color:#94a3b8;margin-top:12px;animation:slideIn 0.8s ease-out 0.5s both;">Today's picks update daily &mdash; full history available.</p>
</div>
<style>
@keyframes slideIn{from{opacity:0;transform:translateX(-40px);}to{opacity:1;transform:translateX(0);}}
.hero-slide{opacity:0;}
</style>

<!-- Proof Section -->
<div style="max-width:800px;margin:0 auto;padding:0 24px 20px;">
    <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(16,185,129,0.25);border-radius:14px;padding:20px 24px;">
        <div style="display:flex;justify-content:center;gap:20px;flex-wrap:wrap;text-align:center;">
            <div style="min-width:120px;">
                <div style="font-size:1.8em;font-weight:900;color:#10b981;">{{ games_graded }}+</div>
                <div style="font-size:0.75em;color:#94a3b8;">Games Graded</div>
            </div>
            <div style="min-width:120px;">
                <div style="font-size:1.8em;font-weight:900;color:#10b981;">{{ sports_covered }}</div>
                <div style="font-size:0.75em;color:#94a3b8;">Sports Covered</div>
            </div>
            <div style="min-width:120px;">
                <div style="font-size:1.8em;font-weight:900;color:#10b981;">5</div>
                <div style="font-size:0.75em;color:#94a3b8;">AI Models</div>
            </div>
            <div style="min-width:120px;">
                <div style="font-size:1.8em;font-weight:900;color:#10b981;">Daily</div>
                <div style="font-size:0.75em;color:#94a3b8;">Updates</div>
            </div>
        </div>
        <p style="text-align:center;font-size:0.78em;color:#94a3b8;margin-top:12px;">All results are tracked and updated daily. <a href="/results" style="color:#fbbf24;text-decoration:none;">View full results &rarr;</a></p>
    </div>
</div>

<!-- Sticky Bottom Bar -->
<div style="position:fixed;bottom:0;left:0;right:0;z-index:100;background:rgba(7,10,20,0.45);backdrop-filter:blur(16px);border-top:1px solid rgba(251,191,36,0.15);padding:12px 24px;display:flex;align-items:center;justify-content:space-between;">
    <div style="display:flex;align-items:center;gap:12px;">
        <div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,#fbbf24,#f59e0b);display:flex;align-items:center;justify-content:center;font-size:1.1em;">🏆</div>
        <span style="font-weight:700;color:#e2e8f0;font-size:0.92em;">Premium Membership</span>
    </div>
    <a href="/plans" style="background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;padding:10px 24px;border-radius:999px;font-weight:800;text-decoration:none;font-size:0.88em;box-shadow:0 4px 16px rgba(251,191,36,0.3);">Join Now</a>
</div>
<style>body{padding-bottom:66px;}</style>

<!-- Sports grid -->
<div class="section">
    <h2 class="section-title">Browse Picks by Sport</h2>
    <div class="sports-grid">
        {% for s in landing_sports %}
        <a href="/{{ s.seo_slug }}" class="sport-card {% if s.is_live %}live{% endif %}">
            {% if s.is_live %}<div class="live-dot"></div>{% endif %}
            <div class="sport-icon">{{ s.icon }}</div>
            <div class="sport-name">{{ s.name }}</div>
            <div class="sport-status {% if s.is_live %}live-text{% endif %}">{{ s.status }}</div>
        </a>
        {% endfor %}
    </div>
</div>

<!-- Today's Top Picks
{% if todays_picks %}
<div class="section" style="padding-top:20px;">
    <h2 class="section-title">Today's Top Picks</h2>
    <p class="section-sub">Free moneyline picks from today's games, powered by our consensus model.</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;max-width:900px;margin:0 auto;">
        {% for tp in todays_picks %}
        <a href="/{{ tp.slug }}" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:12px;padding:18px 20px;text-decoration:none;color:#fff;transition:border-color 0.2s;">
            <div style="font-size:0.72em;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">{{ tp.sport }}</div>
            <div style="font-weight:700;font-size:1.05em;margin-bottom:6px;">{{ tp.away }} vs {{ tp.home }}</div>
            <div style="font-size:0.9em;color:#10b981;font-weight:700;">▶ Free Pick: {{ tp.pick }} ML</div>
        </a>
        {% endfor %}
    </div>
</div>
{% endif %}

<!-- Weekly banner -->
{% if weekly_banner_messages %}
<div class="weekly-banner" style="margin-top:30px;">
    <div class="weekly-banner-label">Featured AI Model Results</div>
    <div class="weekly-banner-lines">
        <div class="weekly-banner-track">
            {% for item in weekly_banner_messages %}
            <div class="weekly-banner-line">
                <span class="wb-title">{{ item.label }}</span>
                <span class="wb-pct">{{ item.pct }}</span>
                <span class="wb-rec">{{ item.record }}</span>
            </div>
            {% endfor %}
            {% for item in weekly_banner_messages %}
            <div class="weekly-banner-line">
                <span class="wb-title">{{ item.label }}</span>
                <span class="wb-pct">{{ item.pct }}</span>
                <span class="wb-rec">{{ item.record }}</span>
            </div>
            {% endfor %}
        </div>
    </div>
</div>
{% endif %}

<!-- How it works -->
<div class="how-section">
    <div class="section">
        <h2 class="section-title">How It Works</h2>
        <div class="steps-grid">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-title">Live Data</div>
                <div class="step-body">Real-time stats, matchups, and historical performance across 9 sports.</div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-title">AI Models</div>
                <div class="step-body">5 independent models generate win probabilities for every game.</div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-title">Projections</div>
                <div class="step-body">Predicted scores, spreads, and totals for each matchup.</div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-title">Consensus</div>
                <div class="step-body">All models combine into one pick—highlighting real edges.</div>
            </div>
        </div>
    </div>
</div>

<!-- Why Different -->
<div class="section" style="padding-top:10px;padding-bottom:40px;">
    <h2 class="section-title">Why Our Picks Are Different</h2>
    <p class="section-sub" style="max-width:640px;margin:0 auto;">Most bettors follow public trends. Our system analyzes matchups, projections, and real-time data to find edges the market misses &mdash; then tracks every result with full transparency.</p>
</div>

<!-- Season Performance -->
{% if units_banner_items %}
<div class="section" style="padding-top:10px;padding-bottom:50px;">
    <h2 class="section-title" style="margin-bottom:10px;">Season Performance</h2>
    <p class="section-sub">All results tracked. No edits. Full transparency.</p>
    <div class="units-marquee-wrap">
        <div class="units-marquee-track">
            {% for item in units_banner_items %}
            <div class="units-pill {% if item.positive %}positive{% else %}negative{% endif %}">
                <span class="up-label">{{ item.label }}</span>
                <span class="up-units">{{ item.units }}</span>
                <span class="up-rec">{{ item.record }}</span>
            </div>
            {% endfor %}
            {% for item in units_banner_items %}
            <div class="units-pill {% if item.positive %}positive{% else %}negative{% endif %}">
                <span class="up-label">{{ item.label }}</span>
                <span class="up-units">{{ item.units }}</span>
                <span class="up-rec">{{ item.record }}</span>
            </div>
            {% endfor %}
        </div>
    </div>
</div>
{% endif %}

<!-- Free vs Premium -->
<div class="section" style="padding-top:10px;padding-bottom:30px;">
    <h2 class="section-title">Free vs Premium</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:700px;margin:0 auto;">
        <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:22px;">
            <h4 style="font-size:1em;font-weight:700;margin-bottom:12px;color:#e2e8f0;">Free</h4>
            <ul style="list-style:none;padding:0;font-size:0.85em;color:#94a3b8;line-height:2;">
                <li>&#10003; Moneyline picks</li>
                <li>&#10003; Model win percentages</li>
                <li>&#10003; Full results tracking</li>
            </ul>
        </div>
        <div style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.25);border-radius:12px;padding:22px;">
            <h4 style="font-size:1em;font-weight:700;margin-bottom:12px;color:#fbbf24;">Premium</h4>
            <ul style="list-style:none;padding:0;font-size:0.85em;color:#cbd5e1;line-height:2;">
                <li>&#10003; Spread picks</li>
                <li>&#10003; Total (over/under) picks</li>
                <li>&#10003; Score projections</li>
                <li>&#10003; Full model output</li>
            </ul>
            <a href="/plans" style="display:inline-block;margin-top:14px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;padding:10px 22px;border-radius:8px;font-weight:800;text-decoration:none;font-size:0.88em;">Unlock Premium Picks</a>
        </div>
    </div>
</div>

<!-- SEO Text -->
<div class="section" style="padding-top:0;padding-bottom:20px;">
    <p style="max-width:700px;margin:0 auto;font-size:0.82em;color:#64748b;line-height:1.7;text-align:center;">underdogs.bet provides AI-powered sports predictions across NBA, NFL, MLB, NHL, and more. Our system analyzes real-time data, team performance, and matchups to generate daily picks. Every prediction is tracked with full transparency so users can evaluate real performance over time.</p>
</div>

<!-- SEO Internal Links -->
<div class="section" style="padding-top:10px;padding-bottom:40px;text-align:center;">
    <h3 style="font-size:1.1em;font-weight:700;margin-bottom:14px;color:#e2e8f0;">Today's Picks by Sport</h3>
    <div style="display:flex;flex-wrap:wrap;justify-content:center;gap:10px;">
        <a href="/mlb-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">MLB Picks Today</a>
        <a href="/nba-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">NBA Picks Today</a>
        <a href="/nhl-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">NHL Picks Today</a>
        <a href="/nfl-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">NFL Picks Today</a>
        <a href="/soccer-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">Soccer Picks Today</a>
        <a href="/ncaab-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">NCAAB Picks Today</a>
        <a href="/wnba-picks" style="color:#94a3b8;text-decoration:none;font-size:0.88em;padding:6px 14px;border:1px solid rgba(255,255,255,0.12);border-radius:8px;">WNBA Picks Today</a>
    </div>
</div>

<!-- Daily Results Box -->
<div style="max-width:720px;margin:0 auto 30px;padding:0 24px;">
    <div style="position:relative;overflow:hidden;border-radius:16px;border:1px solid rgba(255,255,255,0.15);">
        <div style="position:absolute;inset:0;background:url('/static/seth-hoffman-HwZTYUkIP6c-unsplash.jpg') center/cover no-repeat;"></div>
        <div style="position:absolute;inset:0;background:linear-gradient(135deg,rgba(7,10,20,0.88),rgba(15,23,42,0.92));"></div>
        <div style="position:relative;padding:32px 28px;text-align:center;">
            <h2 style="font-size:1.5em;font-weight:900;background:linear-gradient(90deg,#fff 0%,#fbbf24 40%,#f59e0b 60%,#fff 100%);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:shineText 3s linear infinite;">Daily Betting Results Report</h2>
            <p style="color:#cbd5e1;font-size:0.9em;margin:10px 0 20px;max-width:480px;margin-left:auto;margin-right:auto;">Yesterday's performance across all sports and models &mdash; tracked, transparent, verified.</p>
            <a href="/results" style="display:inline-block;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;padding:14px 32px;border-radius:10px;font-weight:800;text-decoration:none;font-size:0.95em;box-shadow:0 4px 20px rgba(251,191,36,0.3);transition:transform 0.2s;" onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='none'">View Full Results</a>
        </div>
    </div>
</div>
<style>@keyframes shineText{to{background-position:200% center;}}</style>

<!-- Footer -->
<footer class="site-footer">
    <div class="footer-inner">
        <div class="footer-left">
            <a href="/"><img src="/static/Logo.PNG" alt="underdogs.bet" class="footer-logo-img"></a>
            <div class="footer-email"><a href="mailto:{{ contact_email }}">{{ contact_email }}</a></div>
        </div>
        <div class="footer-center">
            <a href="/tutorial">Tutorial</a><span>·</span>
            <a href="/privacy">Privacy</a><span>·</span>
            <a href="/terms">Terms</a>
        </div>
        <div class="footer-socials">
            <a href="https://x.com/underdogs_bet" target="_blank" rel="noopener" title="X"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg></a>
            <a href="https://instagram.com/underdogs.bet" target="_blank" rel="noopener" title="Instagram"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zM12 0C8.741 0 8.333.014 7.053.072 2.695.272.273 2.69.073 7.052.014 8.333 0 8.741 0 12c0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98C8.333 23.986 8.741 24 12 24c3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98C15.668.014 15.259 0 12 0zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z"/></svg></a>
            <a href="https://facebook.com/underdogs.bet" target="_blank" rel="noopener" title="Facebook"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg></a>
            <a href="https://tiktok.com/@underdog.bet" target="_blank" rel="noopener" title="TikTok"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></svg></a>
            <a href="https://youtube.com/@Underdogsbet" target="_blank" rel="noopener" title="YouTube"><svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg></a>
        </div>
        <div class="footer-right">© 2026 underdogs.bet. ALL RIGHTS RESERVED.</div>
    </div>
</footer>

<script>
    function toggleMenu() {
        const navLinks = document.getElementById('navLinks');
        if (navLinks) navLinks.classList.toggle('active');
    }
    document.addEventListener('DOMContentLoaded', function() {
        const navLinks = document.getElementById('navLinks');
        if (!navLinks) return;
        navLinks.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', function() {
                navLinks.classList.remove('active');
            });
        });
    });
    document.addEventListener('click', function(event) {
        const navLinks = document.getElementById('navLinks');
        const navbar = document.querySelector('.navbar');
        if (!navLinks || !navbar) return;
        if (!navbar.contains(event.target)) {
            navLinks.classList.remove('active');
        }
    });
    function scrollSports(direction) {
        const scroller = document.getElementById('sportBubbles');
        if (!scroller) return;
        const step = scroller.clientWidth * 0.8;
        scroller.scrollBy({ left: direction * step, behavior: 'smooth' });
    }
    document.addEventListener('DOMContentLoaded', function() {
        // banner is static list now
    });
</script>

</body>
</html>
    """, nhl_accuracy=nhl_accuracy, nfl_accuracy=nfl_accuracy, nba_accuracy=nba_accuracy,
         games_graded=games_graded, predictions_logged=predictions_logged,
         stripe_url=STRIPE_DONATION_URL, landing_sports=landing_sports,
         sports_covered=sports_covered, weekly_banner_messages=weekly_banner_messages,
         units_banner_items=units_banner_items,
         todays_picks=todays_picks)

_SITE_DOMAIN = 'https://www.underdogs.bet'

@app.route('/robots.txt')
def robots_txt():
    body = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /checkout/
Disallow: /stripe/
Disallow: /auth/

Sitemap: {_SITE_DOMAIN}/sitemap.xml
"""
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now()
    urls = []

    # Homepage
    urls.append((_SITE_DOMAIN + '/', 'daily', '1.0'))

    # Sport picks + results pages (only in-season sports get dated pages)
    for sport_key in SPORTS.keys():
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        picks_slug = SPORT_SEO_SLUGS.get(sport_key)
        results_slug = _SPORT_RESULTS_SLUGS.get(sport_key)
        _status, _is_live = get_season_status(sport_key, today=now)
        if picks_slug:
            urls.append((f"{_SITE_DOMAIN}/{picks_slug}", 'daily', '0.9'))
        if results_slug and _is_live:
            urls.append((f"{_SITE_DOMAIN}/{results_slug}", 'daily', '0.8'))
        # Daily SEO pages only for in-season sports
        if picks_slug and _is_live:
            for days_back in range(8):
                d = now - timedelta(days=days_back)
                month_name = _MONTH_NAMES.get(d.month, 'january')
                daily_url = f"{_SITE_DOMAIN}/{picks_slug}-{month_name}-{d.day}-{d.year}"
                urls.append((daily_url, 'daily', '0.7'))

    # Static pages
    urls.append((_SITE_DOMAIN + '/daily-report', 'daily', '0.8'))
    urls.append((_SITE_DOMAIN + '/plans', 'weekly', '0.8'))
    urls.append((_SITE_DOMAIN + '/tutorial', 'monthly', '0.5'))
    urls.append((_SITE_DOMAIN + '/privacy', 'monthly', '0.3'))
    urls.append((_SITE_DOMAIN + '/terms', 'monthly', '0.3'))
    urls.append((_SITE_DOMAIN + '/login', 'monthly', '0.4'))
    urls.append((_SITE_DOMAIN + '/signup', 'monthly', '0.4'))

    urlset = "\n".join(
        f'<url><loc>{loc}</loc><lastmod>{today}</lastmod><changefreq>{freq}</changefreq><priority>{prio}</priority></url>'
        for loc, freq, prio in urls
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urlset}\n</urlset>'
    return Response(xml, mimetype='application/xml')

# ── SEO picks routes ──────────────────────────────────────────────────────────

@app.route('/<slug>')
def seo_picks_page(slug):
    """Handle SEO-friendly URLs like /nhl-picks, /nba-picks, /nhl-results, etc."""
    # Check picks slugs
    sport = _SEO_SLUG_TO_SPORT.get(slug)
    if sport:
        return sport_predictions(sport)
    # Check results slugs
    sport = _RESULTS_SLUG_TO_SPORT.get(slug)
    if sport:
        return sport_results(sport)
    # Not a known SEO slug — fall through to 404
    return "Page not found", 404


@app.route('/<slug>-<month>-<int:day>-<int:year>')
def seo_daily_picks(slug, month, day, year):
    """Daily SEO pages like /nhl-picks-april-9-2026"""
    full_slug = f"{slug}"
    sport = _SEO_SLUG_TO_SPORT.get(full_slug)
    if not sport:
        return "Page not found", 404
    month_num = _MONTH_NAME_TO_NUM.get(month.lower())
    if not month_num:
        return "Invalid date", 404
    target_date = f"{year}-{month_num:02d}-{day:02d}"
    # Render the predictions page filtered to this date
    return sport_predictions(sport, filter_date=target_date)


# ── 301 redirects from old URLs ───────────────────────────────────────────────

@app.route('/sport/<sport>/predictions')
def old_sport_predictions_redirect(sport):
    """301 redirect old /sport/X/predictions to new SEO URL."""
    slug = SPORT_SEO_SLUGS.get(sport)
    if slug:
        return redirect(f'/{slug}', code=301)
    return "Sport not found", 404


@app.route('/sport/<sport>/results')
def old_sport_results_redirect(sport):
    """301 redirect old /sport/X/results to new SEO URL."""
    slug = _SPORT_RESULTS_SLUGS.get(sport)
    if slug:
        return redirect(f'/{slug}', code=301)
    return "Sport not found", 404


@app.route('/sport/<sport>')
def sport_home(sport):
    """Redirect to new SEO URL"""
    slug = SPORT_SEO_SLUGS.get(sport)
    if slug:
        return redirect(f'/{slug}', code=301)
    return "Sport not found", 404


@app.route('/daily-report')
def daily_report_page():
    """Daily Betting Results Report — marketing/proof-of-performance page."""
    from collections import defaultdict
    try:
        _tz = ZoneInfo('America/New_York')
        yesterday_dt = datetime.now(_tz) - timedelta(days=1)
    except Exception:
        yesterday_dt = datetime.now() - timedelta(days=1)
    report_date = yesterday_dt.strftime('%Y-%m-%d')
    report_display = yesterday_dt.strftime('%B %d, %Y')

    # Gather yesterday's tally for each active sport
    sport_tallies = []
    total_games = 0
    agg_models = {}
    agg_spread = {'correct': 0, 'total': 0, 'pushes': 0}
    agg_ou = {'correct': 0, 'total': 0, 'pushes': 0}

    # Quick score syncs (lightweight API calls only, no ESPN odds engine)
    for _sync in ['NHL', 'NBA', 'MLB']:
        try:
            if _sync == 'NHL':
                update_nhl_scores()
            else:
                update_espn_scores(_sync)
        except Exception:
            pass
    # Soccer: fetch ONLY yesterday's date directly (skip full update_espn_scores which is too slow)
    try:
        _soc_date_str = yesterday_dt.strftime('%Y%m%d')
        _soc_conn = get_db_connection()
        _soc_cursor = _soc_conn.cursor()
        _soc_count = 0
        for _soc_league in SOCCER_LEAGUE_ORDER:
            _soc_code = SOCCER_LEAGUE_ENDPOINTS.get(_soc_league)
            if not _soc_code:
                continue
            try:
                _soc_resp = requests.get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{_soc_code}/scoreboard?dates={_soc_date_str}', timeout=5)
                if _soc_resp.status_code != 200:
                    continue
                _soc_data = _soc_resp.json()
                _soc_lg_info = (_soc_data.get('leagues', [{}])[0] or {}) if isinstance(_soc_data, dict) else {}
                _soc_lg_name = _canonical_soccer_league_name(_soc_lg_info.get('name')) or _soc_league
                for _soc_ev in (_soc_data.get('events', []) if isinstance(_soc_data, dict) else []):
                    _soc_comp = _soc_ev.get('competitions', [{}])[0]
                    _soc_comps = _soc_comp.get('competitors', [])
                    if len(_soc_comps) != 2:
                        continue
                    _soc_st = _soc_ev.get('status', {}).get('type', {}).get('name', '')
                    if not _soc_st.startswith('STATUS_FINAL'):
                        continue
                    _soc_home = next((c for c in _soc_comps if c.get('homeAway') == 'home'), None)
                    _soc_away = next((c for c in _soc_comps if c.get('homeAway') == 'away'), None)
                    if not _soc_home or not _soc_away:
                        continue
                    _soc_ht = _soc_home.get('team', {}).get('displayName', '')
                    _soc_at = _soc_away.get('team', {}).get('displayName', '')
                    try:
                        _soc_hs = int(_soc_home.get('score', 0))
                        _soc_as = int(_soc_away.get('score', 0))
                    except Exception:
                        continue
                    _soc_gd = _espn_event_date_to_local(_soc_ev.get('date', '')) or report_date
                    _soc_gid = f'SOCCER_{_soc_code}_{_soc_ev.get("id", "")}'
                    _soc_ex = _soc_cursor.execute('SELECT 1 FROM games WHERE game_id=? AND sport=?', (_soc_gid, 'SOCCER')).fetchone()
                    if _soc_ex:
                        _soc_cursor.execute('UPDATE games SET home_score=?, away_score=?, status="final" WHERE game_id=? AND sport=? AND (home_score IS NULL OR home_score!=?)', (_soc_hs, _soc_as, _soc_gid, 'SOCCER', _soc_hs))
                    else:
                        try:
                            _soc_cursor.execute('INSERT INTO games (sport,league,game_id,season,game_date,home_team_id,away_team_id,home_score,away_score,status) VALUES (?,?,?,?,?,?,?,?,?,"final")', ('SOCCER', _soc_lg_name, _soc_gid, 2025, _soc_gd, _soc_ht, _soc_at, _soc_hs, _soc_as))
                            _soc_count += 1
                        except Exception:
                            pass
            except Exception:
                continue
        _soc_conn.commit()
        _soc_conn.close()
        if _soc_count > 0:
            logger.info(f'Daily report: inserted {_soc_count} Soccer games for {report_date}')
    except Exception as _soc_e:
        logger.debug(f'Daily report Soccer sync: {_soc_e}')

    # Query DB for yesterday's completed games only (fast, no external API calls)
    for sport_key in ['NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'SOCCER']:
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        if sport_key not in SPORTS:
            continue
        try:
            conn = get_db_connection()
            completed_games = conn.execute('''
                SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ? AND g.home_score IS NOT NULL
                AND (g.game_date LIKE ? OR g.game_date = ?)
                ORDER BY g.game_date DESC LIMIT 50
            ''', (sport_key, sport_key, f'{report_date}%', report_date)).fetchall()
            conn.close()
            if not completed_games:
                continue
            daily_results = defaultdict(lambda: {'games': []})
            for game in completed_games:
                home_score = _to_float_safe(game['home_score'])
                away_score = _to_float_safe(game['away_score'])
                if home_score is None or away_score is None:
                    continue
                home_won = home_score > away_score
                is_draw = sport_key == 'SOCCER' and abs(home_score - away_score) < 1e-9
                if is_draw:
                    home_won = None
                home_team = game['home_team_id']
                away_team = game['away_team_id']
                _raw_date = _to_date_str(game['game_date'])
                game_date = _raw_date[:10] if _raw_date else None
                if not game_date:
                    continue
                elo_prob = _to_float_safe(game['elo_home_prob'], 0.5)
                xgb_prob = _to_float_safe(game['xgboost_home_prob'])
                if xgb_prob is None:
                    xgb_prob = elo_prob
                ens_prob = _to_float_safe(game['win_probability'])
                if ens_prob is None:
                    ens_prob = elo_prob
                v2 = get_v2_prediction(sport_key, home_team, away_team, game_date) if sport_key != 'SOCCER' else None
                glicko2_prob = v2.get('glicko2_prob') if v2 else None
                trueskill_prob = v2.get('trueskill_prob') if v2 else None
                if v2:
                    xgb_prob = v2.get('xgboost_prob', xgb_prob)
                    ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)
                game_info = {
                    'game_id': game['game_id'],
                    'date': game_date,
                    'home': home_team, 'away': away_team,
                    'home_score': int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
                    'away_score': int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
                    'home_win': home_won, 'is_draw': is_draw,
                    'glicko2_prob': round(glicko2_prob * 100, 1) if glicko2_prob is not None else None,
                    'trueskill_prob': round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                    'elo_prob': round(elo_prob * 100, 1),
                    'xgb_prob': round(xgb_prob * 100, 1),
                    'ens_prob': round(ens_prob * 100, 1),
                    'glicko2_correct': (glicko2_prob > 0.5) == home_won if glicko2_prob is not None and home_won is not None else None,
                    'trueskill_correct': (trueskill_prob > 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
                    'elo_correct': (elo_prob > 0.5) == home_won if home_won is not None else None,
                    'xgb_correct': (xgb_prob > 0.5) == home_won if home_won is not None else None,
                    'ens_correct': (ens_prob > 0.5) == home_won if home_won is not None else None,
                    'skip_grading': True if home_won is None else False,
                }
                daily_results[game_date]['games'].append(game_info)
            # Compute spread/total grading (DB-only, no external API calls)
            try:
                _compute_spread_total_for_daily(sport_key, daily_results)
            except Exception:
                pass  # spread/total may be unavailable but moneyline still works
            tally = compute_daily_model_tally(daily_results, report_date)
            if not tally or tally.get('games', 0) == 0:
                continue
            sport_tallies.append({'sport': sport_key, 'info': SPORTS[sport_key], 'tally': tally})
            total_games += tally.get('games', 0)
            for mk in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                mt = tally.get(mk, {})
                if mk not in agg_models:
                    agg_models[mk] = {'correct': 0, 'total': 0}
                agg_models[mk]['correct'] += mt.get('correct', 0)
                agg_models[mk]['total'] += mt.get('total', 0)
            sp = tally.get('spread', {})
            agg_spread['correct'] += sp.get('correct', 0)
            agg_spread['total'] += sp.get('total', 0)
            agg_spread['pushes'] += sp.get('pushes', 0)
            ou = tally.get('total_ou', {})
            agg_ou['correct'] += ou.get('correct', 0)
            agg_ou['total'] += ou.get('total', 0)
            agg_ou['pushes'] += ou.get('pushes', 0)
        except Exception as e:
            logger.error(f"Daily report {sport_key}: {e}")
            continue

    # Compute aggregate accuracies
    for mk in agg_models:
        t = agg_models[mk]['total']
        agg_models[mk]['accuracy'] = round(agg_models[mk]['correct'] / t * 100, 1) if t > 0 else 0.0
    agg_spread['accuracy'] = round(agg_spread['correct'] / agg_spread['total'] * 100, 1) if agg_spread['total'] > 0 else 0.0
    agg_ou['accuracy'] = round(agg_ou['correct'] / agg_ou['total'] * 100, 1) if agg_ou['total'] > 0 else 0.0

    model_labels = [
        ('glicko2', '⭐ Grinder2'),
        ('trueskill', '🎯 Takedown'),
        ('elo', '📊 Edge'),
        ('xgboost', '🤖 XSharp'),
        ('ensemble', '🏆 Consensus'),
    ]

    share_text = f"underdogs.bet Daily Report — {report_display}%0A"
    ens = agg_models.get('ensemble', {})
    if ens.get('total', 0) > 0:
        share_text += f"Consensus: {ens['accuracy']}% ({ens['correct']}-{ens['total'] - ens['correct']})%0A"
    share_text += f"{total_games} games graded%0Ahttps://www.underdogs.bet/daily-report"

    return render_template_string(DAILY_REPORT_TEMPLATE,
        page='daily-report',
        page_title=f'Daily Betting Results Report — {report_date}',
        page_description=f'AI model performance report for {report_display}. Moneyline, spread, and over/under results across all sports.',
        report_date=report_date,
        report_display=report_display,
        total_games=total_games,
        sport_tallies=sport_tallies,
        agg_models=agg_models,
        agg_spread=agg_spread,
        agg_ou=agg_ou,
        model_labels=model_labels,
        share_text=share_text,
    )


@app.route('/tutorial')
def tutorial_page():
    return render_template_string(
        TUTORIAL_TEMPLATE,
        page='tutorial',
        page_title='How to Use This Page',
        page_description='Learn how to read model predictions, scores, spreads, and totals on the picks pages.'
    )

@app.route('/nhl')
def nhl_shortcut():
    return redirect('/nhl-picks', code=301)

@app.route('/nba')
def nba_shortcut():
    return redirect('/nba-picks', code=301)

@app.route('/mlb')
def mlb_shortcut():
    return redirect('/mlb-picks', code=301)

@app.route('/nfl')
def nfl_shortcut():
    return redirect('/nfl-picks', code=301)

@app.route('/ncaab')
def ncaab_shortcut():
    return redirect('/ncaab-picks', code=301)

@app.route('/ncaaw')
def ncaaw_shortcut():
    return redirect('/ncaaw-picks', code=301)

@app.route('/ncaaf')
def ncaaf_shortcut():
    return redirect('/ncaaf-picks', code=301)

@app.route('/wnba')
def wnba_shortcut():
    return redirect('/wnba-picks', code=301)

@app.route('/soccer')
def soccer_shortcut():
    return redirect('/soccer-picks', code=301)

@app.route('/results')
def results_shortcut():
    return redirect('/daily-report', code=301)

@app.route('/donate')
def donate_shortcut():
    return redirect(STRIPE_DONATION_URL)

@app.route('/privacy')
def privacy_page():
    return render_template('privacy.html')

@app.route('/terms')
def terms_page():
    return render_template('terms.html')

@app.route('/sport/SOCCER/predictions/<league_slug>')
def soccer_predictions_league(league_slug):
    return redirect(f'/soccer-picks?league={league_slug}', code=301)

@app.route('/sport/SOCCER/results/<league_slug>')
def soccer_results_league(league_slug):
    return redirect(f'/soccer-results?league={league_slug}', code=301)

def sport_predictions(sport, filter_date=None):
    """Show upcoming predictions for a sport"""
    log_site_visit(f'/{SPORT_SEO_SLUGS.get(sport, sport)}')
    if sport not in SPORTS:
        return "Sport not found", 404
    if sport == 'SOCCER' and not SOCCER_ENABLED:
        return "Soccer predictions are temporarily hidden while data loads.", 404
    prediction_error = None
    try:
        predictions = get_upcoming_predictions(sport)
    except Exception as e:
        logger.error(f"Error loading {sport} predictions: {e}")
        predictions = []
        prediction_error = (
            f"{sport} predictions could not be loaded because an upstream data/model dependency failed. "
            "Please refresh in a minute."
        )

    try:
        _attach_engine_odds_to_predictions(sport, predictions)
    except Exception as _odds_err:
        logger.debug(f"Odds attachment failed for {sport}: {_odds_err}")

    for pred in predictions:
        for _k in (
            'market_spread',
            'market_total',
            'home_moneyline',
            'away_moneyline',
            'spread_price_home',
            'spread_price_away',
            'total_over_price',
            'total_under_price',
            'odds_reason',
        ):
            if _k not in pred:
                pred[_k] = None

    soccer_leagues = None
    selected_league = None
    if sport == 'SOCCER':
        selected_slug = request.args.get('league')
        filtered = []
        leagues = []
        for pred in predictions:
            league_raw = pred.get('league')
            league_name = _canonical_soccer_league_name(league_raw) or league_raw
            if not league_name or league_name not in SOCCER_LEAGUE_ORDER:
                continue
            pred['league'] = league_name
            leagues.append(league_name)
            filtered.append(pred)
        soccer_league_list = _ordered_soccer_leagues(leagues) if leagues else SOCCER_LEAGUE_ORDER
        selected_league = _soccer_league_from_slug(selected_slug) if selected_slug else None
        # If a specific league is selected, filter to it. Otherwise show ALL.
        if selected_league:
            filtered = [p for p in filtered if p.get('league') == selected_league]
        predictions = filtered
        soccer_leagues = [
            {
                'name': 'All Leagues',
                'slug': '',
                'active': selected_league is None,
                'url': '/soccer-picks',
            }
        ] + [
            {
                'name': lg,
                'slug': _soccer_league_slug(lg),
                'active': lg == selected_league,
                'url': f"/soccer-picks?league={_soccer_league_slug(lg)}",
            }
            for lg in soccer_league_list
        ]
    
    # Group predictions by date for NHL/NBA, by week for NFL
    from collections import defaultdict
    grouped_predictions = defaultdict(list)
    try:
        today_date = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        today_date = datetime.now().strftime('%Y-%m-%d')
    
    if sport in ['NHL', 'NBA']:
        # Group by date
        for pred in predictions:
            date_key = pred['game_date']
            grouped_predictions[date_key].append(pred)
    elif sport == 'NFL':
        # Group by date (ESPN data doesn't have week numbers)
        for pred in predictions:
            date_key = pred['game_date']
            grouped_predictions[date_key].append(pred)
    else:
        # Default: group by date
        for pred in predictions:
            date_key = pred['game_date']
            grouped_predictions[date_key].append(pred)
    
    # Sort dates
    sorted_dates = sorted(grouped_predictions.keys())

    # Filter to specific date if requested (daily SEO pages)
    if filter_date:
        if filter_date in grouped_predictions:
            grouped_predictions = {filter_date: grouped_predictions[filter_date]}
            sorted_dates = [filter_date]
        else:
            grouped_predictions = {}
            sorted_dates = []

    # soccer_leagues already computed above for soccer
    
    # Load ESPN-style template (absolute path so Render/gunicorn always finds it)
    with open(_os.path.join(_BASE_DIR, 'espn_predictions_template.html'), 'r') as f:
        espn_template = f.read()
    
    return render_template_string(
        espn_template,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
        sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
        sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
        predictions=predictions,
        prediction_error=prediction_error,
        grouped_predictions=grouped_predictions,
        sorted_dates=sorted_dates,
        today_date=today_date,
        group_by='week' if sport == 'NFL' else 'date',
        soccer_leagues=soccer_leagues
    )

def sport_results(sport):
    """Show model performance results for a sport"""
    try:
        if sport not in SPORTS:
            return "Sport not found", 404
        if sport == 'SOCCER' and not SOCCER_ENABLED:
            return "Soccer results are temporarily hidden while data loads.", 404
        
        if sport == 'NFL':
            update_nfl_scores()
            # Also sync from ESPN to catch playoff games nfl_data_py might miss
            try:
                update_espn_scores('NFL')
            except Exception:
                pass
            weekly_results = calculate_nfl_weekly_performance()
            overall_stats = compute_overall_stats_from_weekly(weekly_results) if weekly_results else {}
            yesterday_dt = datetime.now() - timedelta(days=1)
            daily_tally_date = yesterday_dt.strftime('%Y-%m-%d')
            daily_tally = compute_daily_model_tally_from_weekly(weekly_results, daily_tally_date) if weekly_results else None
            daily_tally_games = daily_tally.get('games', 0) if daily_tally else 0
            daily_results = _daily_results_from_weekly(weekly_results) if weekly_results else {}
            _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
            weekly_start_dt = yesterday_dt - timedelta(days=6)
            weekly_tally = compute_model_tally_for_range(daily_results, weekly_start_dt, yesterday_dt) if daily_results else None
            weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
            weekly_tally_date_range = f"{weekly_start_dt.strftime('%Y-%m-%d')} to {yesterday_dt.strftime('%Y-%m-%d')}"
            roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt) if daily_results else None
            roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, yesterday_dt) if daily_results else None
            roi_total = compute_roi_for_range(daily_results, None, None) if daily_results else None
            roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total) if daily_results else None
            return render_template_string(
                NFL_WEEKLY_RESULTS_TEMPLATE,
                page=sport,
                sport=sport,
                sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                weekly_results=weekly_results,
                overall_stats=overall_stats,
                daily_tally=daily_tally,
                daily_tally_date=daily_tally_date,
                daily_tally_games=daily_tally_games,
                weekly_tally=weekly_tally,
                weekly_tally_date_range=weekly_tally_date_range,
                weekly_tally_games=weekly_tally_games,
                roi_cards=roi_cards
            )
        
        if sport == 'NHL':
            cache_key = f'{sport}_moneyline_results_html'
            cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300)
            cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
            if isinstance(cached_page, dict):
                cached_ts = cached_page.get('ts')
                cached_html = cached_page.get('html')
                if cached_ts is not None and cached_html and (_time.time() - cached_ts) < cache_ttl:
                    return cached_html

            try:
                # Run NHL score sync at most once every 10 minutes for this process.
                sync_key = f'{sport}_results_score_sync_ts'
                sync_entry = _SPORT_RESULTS_CACHE.get(sync_key)
                sync_last_ts = sync_entry.get('ts') if isinstance(sync_entry, dict) else None
                now_ts = _time.time()
                if sync_last_ts is None or (now_ts - sync_last_ts) >= 600:
                    update_nhl_scores()
                    _SPORT_RESULTS_CACHE[sync_key] = {'ts': now_ts}
            except Exception as e:
                logger.error(f"NHL score sync failed (continuing with existing data): {e}")
            weekly_results = calculate_nhl_weekly_performance()
            
            if not weekly_results:
                return "<h1>NHL results could not be loaded because no completed NHL games were available for grading yet.</h1>"
            
            # Regroup by date instead of week
            from collections import defaultdict
            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            
            try:
                for week, week_data in weekly_results.items():
                    for game in week_data['games']:
                        date_key = game['date']
                        daily_results[date_key]['games'].append(game)
                
                # Filter to only show recent dates up to yesterday.
                # Keep overall stats from all games, but render fewer date sections
                # so the page stays fast and doesn't block production workers.
                yesterday_dt = datetime.now() - timedelta(days=1)
                yesterday = yesterday_dt.strftime('%Y-%m-%d')
                sorted_dates = sorted([d for d in daily_results.keys() if d <= yesterday], reverse=True)[:7]
                
                overall_stats = compute_overall_stats_from_daily(daily_results)
                _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)

                _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
                _st_stats = _compute_spread_total_for_daily(sport, daily_results)
                daily_tally_date = yesterday
                daily_tally = compute_daily_model_tally(daily_results, daily_tally_date)
                daily_tally_games = daily_tally.get('games', 0) if daily_tally else 0
                weekly_start_dt = yesterday_dt - timedelta(days=6)
                weekly_tally = compute_model_tally_for_range(daily_results, weekly_start_dt, yesterday_dt)
                weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
                weekly_tally_date_range = f"{weekly_start_dt.strftime('%Y-%m-%d')} to {yesterday_dt.strftime('%Y-%m-%d')}"
                roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
                roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, yesterday_dt)
                roi_total = compute_roi_for_range(daily_results, None, None)
                roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)

                rendered = render_template_string(
                    DAILY_RESULTS_TEMPLATE,
                    page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                    sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                    sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                    daily_results=daily_results, sorted_dates=sorted_dates,
                    today_date=today_date, overall_stats=overall_stats,
                    total_over=_ov, total_under=_un, total_games_ou=_gou,
                    avg_total=_avg, ou_bench=_bench,
                    spread_total_stats=_st_stats,
                    daily_tally=daily_tally,
                    daily_tally_date=daily_tally_date,
                    daily_tally_games=daily_tally_games,
                    weekly_tally=weekly_tally,
                    weekly_tally_date_range=weekly_tally_date_range,
                    weekly_tally_games=weekly_tally_games,
                    roi_cards=roi_cards
                )
                _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
                return rendered
            except Exception as e:
                logger.error(f"Error processing NHL results: {e}")
                return f"<h1>NHL results page failed to render because of a processing error: {str(e)}</h1>"
        
        if sport == 'NBA':
            cache_key = f'{sport}_daily_results_html'
            cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)
            cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
            if isinstance(cached_page, dict):
                cached_ts = cached_page.get('ts')
                cached_html = cached_page.get('html')
                if cached_ts is not None and cached_html and (_time.time() - cached_ts) < cache_ttl:
                    return cached_html
            try:
                update_nba_scores()
            except Exception as e:
                logger.error(f"NBA score sync failed (continuing with existing data): {e}")
            try:
                weekly_results = calculate_nba_weekly_performance()
                logger.info(f"NBA weekly_results: {weekly_results is not None}, weeks: {list(weekly_results.keys()) if weekly_results else 'None'}")
                if not weekly_results:
                    return "<h1>NBA results could not be loaded because no completed NBA games were available for grading yet.</h1>"
                
                # Regroup by date instead of week
                from collections import defaultdict
                daily_results = defaultdict(lambda: {'games': []})
                today_date = datetime.now().strftime('%Y-%m-%d')
                
                for week, week_data in weekly_results.items():
                    for game in week_data['games']:
                        date_key = game['date']
                        daily_results[date_key]['games'].append(game)
                
                # Render recent dates only to keep response size manageable.
                yesterday_dt = datetime.now() - timedelta(days=1)
                yesterday = yesterday_dt.strftime('%Y-%m-%d')
                sorted_dates = sorted([d for d in daily_results.keys() if d <= yesterday], reverse=True)[:7]
                
                overall_stats = compute_overall_stats_from_daily(daily_results)
                _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
                _cache_market_lines_for_results(sport, daily_results, limit=20)
                _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
                _st_stats = _compute_spread_total_for_daily(sport, daily_results)
                daily_tally_date = yesterday
                daily_tally = compute_daily_model_tally(daily_results, daily_tally_date)
                daily_tally_games = daily_tally.get('games', 0) if daily_tally else 0
                weekly_start_dt = yesterday_dt - timedelta(days=6)
                weekly_tally = compute_model_tally_for_range(daily_results, weekly_start_dt, yesterday_dt)
                weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
                weekly_tally_date_range = f"{weekly_start_dt.strftime('%Y-%m-%d')} to {yesterday_dt.strftime('%Y-%m-%d')}"
                roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
                roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, yesterday_dt)
                roi_total = compute_roi_for_range(daily_results, None, None)
                roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
                rendered = render_template_string(
                    DAILY_RESULTS_TEMPLATE,
                    page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                    sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                    sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                    daily_results=daily_results, sorted_dates=sorted_dates,
                    today_date=today_date, overall_stats=overall_stats,
                    total_over=_ov, total_under=_un, total_games_ou=_gou,
                    avg_total=_avg, ou_bench=_bench,
                    spread_total_stats=_st_stats,
                    daily_tally=daily_tally,
                    daily_tally_date=daily_tally_date,
                    daily_tally_games=daily_tally_games,
                    weekly_tally=weekly_tally,
                    weekly_tally_date_range=weekly_tally_date_range,
                    weekly_tally_games=weekly_tally_games,
                    roi_cards=roi_cards
                )
                _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
                return rendered
            except Exception as e:
                logger.error(f"Error processing NBA results: {e}")
                return f"<h1>NBA results page failed to render because of a processing error: {str(e)}</h1>"

        # Handle NCAAB
        if sport in ['NCAAB', 'NCAAW', 'NCAAF', 'MLB', 'WNBA', 'SOCCER']:
            selected_league = None
            selected_slug = None
            if sport == 'SOCCER':
                selected_slug = request.args.get('league')
                selected_league = _soccer_league_from_slug(selected_slug)
                if not selected_league and selected_slug:
                    selected_league = None
            cache_key = f'{sport}_daily_results_html'
            skip_cache = False
            if sport == 'SOCCER':
                if selected_league:
                    cache_key = f'{sport}_daily_results_html_{_soccer_league_slug(selected_league)}'
                if not selected_slug:
                    skip_cache = True
            cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)
            if not skip_cache:
                cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
                if isinstance(cached_page, dict):
                    cached_ts = cached_page.get('ts')
                    cached_html = cached_page.get('html')
                    if cached_ts is not None and cached_html and (_time.time() - cached_ts) < cache_ttl:
                        return cached_html
            # Update scores first
            update_espn_scores(sport)
            
            # Get completed games from database
            conn = get_db_connection()
            completed_games = conn.execute('''
                SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ? AND g.home_score IS NOT NULL
                ORDER BY g.game_date DESC
                LIMIT 100
            ''', (sport, sport)).fetchall()
            conn.close()
            if sport == 'SOCCER' and not selected_slug:
                league_counts = {}
                for game in completed_games:
                    league_name = _canonical_soccer_league_name(game['league']) or game['league']
                    if league_name and league_name in SOCCER_LEAGUE_ORDER:
                        league_counts[league_name] = league_counts.get(league_name, 0) + 1
                if league_counts:
                    selected_league = next((lg for lg in SOCCER_LEAGUE_ORDER if league_counts.get(lg)), None)
                if not selected_league:
                    selected_league = SOCCER_LEAGUE_ORDER[0] if SOCCER_LEAGUE_ORDER else None
                if selected_league:
                    cache_key = f'{sport}_daily_results_html_{_soccer_league_slug(selected_league)}'
            soccer_bundle = None
            if sport == 'SOCCER':
                soccer_bundle = _get_soccer_model_bundle(completed_games, selected_league)
            
            if not completed_games:
                # Show message for offseason sports
                offseason_msg = "" 
                if sport in ['MLB', 'WNBA']:
                    offseason_msg = f"<p>The {SPORTS[sport]['name']} season has ended. Results from the 2025 season will be available next year.</p>"
                return f"<h1>No {SPORTS[sport]['name']} results data available yet.</h1>{offseason_msg}<p><a href='/'>← Back to Home</a></p>"
            
            # Process into daily results format
            from collections import defaultdict
            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            
            for game in completed_games:
                home_score = _to_float_safe(game['home_score'])
                away_score = _to_float_safe(game['away_score'])
                if home_score is None or away_score is None:
                    continue
                home_won = home_score > away_score
                is_draw = False
                if sport == 'SOCCER' and abs(home_score - away_score) < 1e-9:
                    is_draw = True
                    home_won = None
                home_team = game['home_team_id']
                away_team = game['away_team_id']
                _raw_date = _to_date_str(game['game_date'])
                game_date = _raw_date[:10] if _raw_date else None
                league_name = game.get('league') if isinstance(game, dict) else game['league']
                if sport == 'SOCCER':
                    league_name = _canonical_soccer_league_name(league_name) or league_name
                    if not league_name or league_name not in SOCCER_LEAGUE_ORDER:
                        continue
                    if selected_league and league_name != selected_league:
                        continue

                # Stored DB probs
                elo_prob = _to_float_safe(game['elo_home_prob'], 0.5)
                xgb_prob = _to_float_safe(game['xgboost_home_prob'])
                if xgb_prob is None:
                    xgb_prob = _to_float_safe(game['elo_home_prob'], 0.5)
                ens_prob = _to_float_safe(game['win_probability'])
                if ens_prob is None:
                    ens_prob = _to_float_safe(game['elo_home_prob'], 0.5)

                soccer_pred = None
                model_note = None
                if sport == 'SOCCER' and soccer_bundle and getattr(soccer_bundle, 'ready', False):
                    soccer_pred = soccer_bundle.predict(home_team, away_team)
                elif sport == 'SOCCER' and soccer_bundle:
                    model_note = soccer_bundle.reason

                if soccer_pred:
                    glicko2_prob = soccer_pred.get('poisson_xg_prob')
                    trueskill_prob = soccer_pred.get('markov_prob')
                    elo_prob = soccer_pred.get('elo_prob') or elo_prob
                    xgb_prob = soccer_pred.get('poisson_reg_prob') or xgb_prob or elo_prob
                    ens_prob = soccer_pred.get('ensemble_prob') or ens_prob or elo_prob
                else:
                    v2 = get_v2_prediction(sport, home_team, away_team, game_date) if sport != 'SOCCER' else None
                    glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
                    trueskill_prob = v2.get('trueskill_prob') if v2 else None
                    if v2:
                        xgb_prob = v2.get('xgboost_prob', xgb_prob)
                        ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)
                    if sport == 'SOCCER' and (glicko2_prob is None or trueskill_prob is None):
                        model_note = model_note or "Soccer model outputs are unavailable for this matchup."
                game_info = {
                    'game_id':         game['game_id'],
                    'date':             game_date or 'Unknown',
                    'home':             home_team,
                    'away':             away_team,
                    'league':           league_name or sport,
                    'home_score':       int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
                    'away_score':       int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
                    'home_win':         home_won,
                    'is_draw':          is_draw,
                    'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                    'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                    'elo_prob':         round(elo_prob  * 100, 1),
                    'xgb_prob':         round(xgb_prob  * 100, 1),
                    'ens_prob':         round(ens_prob  * 100, 1),
                    'glicko2_correct':   (glicko2_prob   > 0.5) == home_won if glicko2_prob   is not None and home_won is not None else None,
                    'trueskill_correct': (trueskill_prob > 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
                    'elo_correct':       (elo_prob  > 0.5) == home_won if home_won is not None else None,
                    'xgb_correct':       (xgb_prob  > 0.5) == home_won if home_won is not None else None,
                    'ens_correct':       (ens_prob  > 0.5) == home_won if home_won is not None else None,
                    'skip_grading':      True if home_won is None else False,
                    'model_data_note':   model_note,
                }
                daily_results[game_info['date']]['games'].append(game_info)

            sorted_dates = sorted(daily_results.keys(), reverse=True)[:30]
            overall_stats = compute_overall_stats_from_daily(daily_results)
            _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
            _cache_market_lines_for_results(sport, daily_results, limit=20)
            _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
            _st_stats = _compute_spread_total_for_daily(sport, daily_results)
            yesterday_dt = datetime.now() - timedelta(days=1)
            daily_tally_date = yesterday_dt.strftime('%Y-%m-%d')
            daily_tally = compute_daily_model_tally(daily_results, daily_tally_date)
            daily_tally_games = daily_tally.get('games', 0) if daily_tally else 0
            weekly_start_dt = yesterday_dt - timedelta(days=6)
            weekly_tally = compute_model_tally_for_range(daily_results, weekly_start_dt, yesterday_dt)
            weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
            weekly_tally_date_range = f"{weekly_start_dt.strftime('%Y-%m-%d')} to {yesterday_dt.strftime('%Y-%m-%d')}"
            roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
            roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, yesterday_dt)
            roi_total = compute_roi_for_range(daily_results, None, None)
            roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
            soccer_leagues = None
            if sport == 'SOCCER':
                soccer_leagues = [
                    {
                        'name': lg,
                        'slug': _soccer_league_slug(lg),
                        'active': lg == selected_league,
                    'url': f"/soccer-results?league={_soccer_league_slug(lg)}",
                    }
                    for lg in SOCCER_LEAGUE_ORDER
                ]

            rendered = render_template_string(
                DAILY_RESULTS_TEMPLATE,
                page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                daily_results=daily_results, sorted_dates=sorted_dates,
                today_date=today_date, overall_stats=overall_stats,
                total_over=_ov, total_under=_un, total_games_ou=_gou,
                avg_total=_avg, ou_bench=_bench,
                spread_total_stats=_st_stats,
                daily_tally=daily_tally,
                daily_tally_date=daily_tally_date,
                daily_tally_games=daily_tally_games,
                weekly_tally=weekly_tally,
                weekly_tally_date_range=weekly_tally_date_range,
                weekly_tally_games=weekly_tally_games,
                roi_cards=roi_cards,
                soccer_leagues=soccer_leagues
            )
            _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
            return rendered
        
        performance = calculate_model_performance(sport)
        return render_template_string(
            RESULTS_TEMPLATE,
            page=sport,
            sport=sport,
            sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
            sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
            sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
            performance=performance
        )
    except Exception as e:
        logger.exception(f"Error loading /sport/{sport}/results: {e}")
        return (
            f"<h1>{sport} moneyline results are temporarily unavailable because the server hit an internal processing error.</h1>"
            f"<p>Please refresh in 30-60 seconds. If it persists, the latest server traceback is now logged for diagnosis.</p>"
        ), 200

def get_upcoming_api_games_for_spreads(sport, days_ahead=7):
    """Get upcoming games from API for spread/total picks (next N days)"""
    api_games = []
    
    if sport == 'NHL':
        try:
            nhl_api = NHLAPI()
            api_games_raw = nhl_api.get_recent_and_upcoming_games(days_back=0, days_forward=days_ahead)
            # Normalize keys to match what spreads generator expects
            api_games = []
            for game in api_games_raw:
                api_games.append({
                    'home_team_name': game.get('home_team_name'),
                    'away_team_name': game.get('away_team_name'),
                    'game_date': game.get('game_date')
                })
        except Exception as e:
            logger.error(f"Error fetching NHL games from API: {e}")
    
    elif sport in ['NBA', 'NCAAB', 'NCAAW', 'NCAAF', 'MLB', 'WNBA']:
        ESPN_ENDPOINTS = {
            'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
            'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
            'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
            'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
            'NCAAW': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard',
            'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
        }
        
        # Fetch games from ESPN API (next N days)
        for days_offset in range(0, days_ahead + 1):
            check_date = datetime.now() + timedelta(days=days_offset)
            date_str = check_date.strftime('%Y%m%d')
            
            try:
                url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                events = data.get('events', [])
                
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    
                    # Get status to skip completed games
                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', 'scheduled')
                    
                    # Skip completed games
                    if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_OT2']:
                        continue
                    
                    api_games.append({
                        'home_team_name': home_team,
                        'away_team_name': away_team,
                        'game_date': check_date.strftime('%Y-%m-%d'),
                    })
            except Exception as e:
                logger.debug(f"Error fetching {sport} for {date_str}: {e}")
    
    elif sport == 'NFL':
        # NFL: Pull from ESPN API similar to other sports
        try:
            api_games_raw = []
            for days_offset in range(0, days_ahead + 1):
                check_date = datetime.now() + timedelta(days=days_offset)
                date_str = check_date.strftime('%Y%m%d')
                
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={date_str}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                events = data.get('events', [])
                
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    
                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', 'scheduled')
                    
                    if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_OT2']:
                        continue
                    
                    api_games_raw.append({
                        'home_team_name': home_team,
                        'away_team_name': away_team,
                        'game_date': check_date.strftime('%Y-%m-%d'),
                    })
            api_games = api_games_raw
        except Exception as e:
            logger.error(f"Error fetching NFL games from API: {e}")
    
    return api_games

@app.route('/sport/<sport>/spreads')
def sport_spread_total_picks(sport):
    """Redirect to predictions page (spreads now shown inline on predictions card)"""
    if sport not in SPORTS:
        return "Sport not found", 404
    return redirect(url_for('sport_predictions', sport=sport))



@app.route('/sport/<sport>/spreads/results')
def sport_spread_total_results(sport):
    """Spread & total results — XSharp only, graded against market spread/total lines."""
    if sport not in SPORTS:
        return "Sport not found", 404
    # All sports now show spread/total on the unified results page
    return redirect(f'/sport/{sport}/results')

@app.route('/sport/<sport>/ats')
def sport_ats_picks(sport):
    """Show ATS betting picks for a sport"""
    if sport not in SPORTS:
        return "Sport not found", 404
    
    # Initialize ATS system
    ats = ATSSystem()
    
    # Get all picks for next 7 days
    all_picks = ats.get_all_picks(sport, days_ahead=7)
    
    ml_picks = all_picks['moneyline']
    spread_picks = all_picks['spread']
    total_picks = all_picks['totals']
    
    # Get ATS records for context
    ats_records = ats.calculate_ats_records(sport, lookback_days=30)
    ou_records = ats.calculate_over_under_records(sport, lookback_days=30)
    
    return render_template_string(
        ATS_PICKS_TEMPLATE,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
        ml_picks=ml_picks,
        spread_picks=spread_picks,
        total_picks=total_picks,
        ats_records=ats_records.head(10).to_dict('records') if not ats_records.empty else [],
        ou_records=ou_records.head(10).to_dict('records') if not ou_records.empty else []
    )

@app.route('/admin/traffic')
def admin_traffic():
    """Simple traffic dashboard for site visits."""
    try:
        ga_data, ga_error = _fetch_ga_traffic()
        traffic_source = "Google Analytics"
        traffic_error = None
        traffic_ga_url = (
            f"https://analytics.google.com/analytics/web/#/p{GA_PROPERTY_ID}/reports/overview"
            if GA_PROPERTY_ID else None
        )
        if ga_data:
            traffic_error = ga_error
            today_visits = ga_data['today_visits']
            week_visits = ga_data['week_visits']
            total_visits = ga_data['total_visits']
            top_endpoints = ga_data['top_endpoints']
            daily_visits = ga_data['daily_visits']
        else:
            traffic_error = ga_error or "Google Analytics data is not available."
            today_visits = "N/A"
            week_visits = "N/A"
            total_visits = "N/A"
            top_endpoints = []
            daily_visits = []

        return render_template_string(
            TRAFFIC_TEMPLATE,
            page='traffic',
            today_visits=today_visits,
            week_visits=week_visits,
            total_visits=total_visits,
            top_endpoints=top_endpoints,
            daily_visits=daily_visits,
            traffic_source=traffic_source,
            traffic_error=traffic_error,
            traffic_ga_url=traffic_ga_url,
        )
    except Exception as e:
        logger.error(f"Error loading traffic dashboard: {e}")
        return "<h1>Traffic dashboard failed to load because the stats could not be read.</h1>"

# ============================================================================
# API ENDPOINTS FOR FRONTEND INTEGRATION
# ============================================================================

@app.route('/api/picks/<sport>', methods=['GET'])
def api_get_picks(sport):
    """API endpoint to get picks for a sport (for Next.js frontend)"""
    log_site_visit(f'/api/picks/{sport}')
    
    if sport.upper() not in SPORTS:
        return jsonify({'error': 'Sport not found'}), 404
    
    try:
        predictions = get_upcoming_predictions(sport.upper())
        
        # Convert to simple JSON format for frontend
        picks = []
        for pred in predictions:
            picks.append({
                'date': pred['game_date'],
                'matchup': f"{pred['away_team_id']} @ {pred['home_team_id']}",
                'homeTeam': pred['home_team_id'],
                'awayTeam': pred['away_team_id'],
                'pick': pred['predicted_winner'],
                'winPercent': pred['ensemble_prob'],
                'edge': pred.get('elo_prob'),
                'xsharp': pred.get('xgb_prob'),
                'grinder2': pred.get('glicko2_prob'),
                'takedown': pred.get('trueskill_prob')
            })
        
        return jsonify({
            'sport': sport.upper(),
            'picks': picks,
            'count': len(picks)
        })
    except Exception as e:
        logger.error(f"Error in API picks endpoint for {sport}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/traffic', methods=['GET'])
def api_get_traffic_stats():
    """Get site traffic statistics"""
    try:
        conn = get_db_connection()
        
        # Get today's visits
        today_dt = _traffic_now()
        today = today_dt.strftime('%Y-%m-%d')
        today_visits = conn.execute('''
            SELECT COUNT(*) FROM site_visits WHERE date(visit_date) = date(?)
        ''', (today,)).fetchone()[0]
        
        # Get last 7 days
        week_ago = (today_dt - timedelta(days=6)).strftime('%Y-%m-%d')
        week_visits = conn.execute('''
            SELECT COUNT(*) FROM site_visits WHERE date(visit_date) >= date(?)
        ''', (week_ago,)).fetchone()[0]
        
        # Get total visits
        total_visits = conn.execute('SELECT COUNT(*) FROM site_visits').fetchone()[0]
        
        # Get top endpoints
        top_endpoints = conn.execute('''
            SELECT endpoint, COUNT(*) as count 
            FROM site_visits 
            GROUP BY endpoint 
            ORDER BY count DESC 
            LIMIT 10
        ''').fetchall()
        
        conn.close()
        
        return jsonify({
            'today': today_visits,
            'last_7_days': week_visits,
            'total': total_visits,
            'top_endpoints': [{'endpoint': row[0], 'count': row[1]} for row in top_endpoints]
        })
    except Exception as e:
        logger.error(f"Error getting traffic stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sports', methods=['GET'])
def api_get_sports():
    """Get list of available sports"""
    return jsonify({
        'sports': [{
            'code': code,
            'name': info['name'],
            'icon': info['icon']
        } for code, info in SPORTS.items()]
    })

if __name__ == '__main__':
    import os, socket
    # Use $PORT from Railway/Render, fall back to auto-finding a local port
    env_port = os.environ.get('PORT')
    if env_port:
        port = int(env_port)
    else:
        port = 5000
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('0.0.0.0', port)) != 0:
                    break
                port += 1

    print("\n" + "="*60)
    print("🎯 underdogs.bet - Multi-Sport Prediction Platform")
    print("="*60)
    print(f"🌐 Visit http://0.0.0.0:{port}")
    print("="*60 + "\n")
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False, threaded=True)
