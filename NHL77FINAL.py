#!/usr/bin/env python3
"""
predictionlab.io - Multi-Sport Prediction Platform
==================================================
Complete platform with Dashboard, Predictions, and Results pages for all sports.
5-Model System: Glicko-2, TrueSkill, Elo, XGBoost, Ensemble
"""

from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, Response, send_from_directory, abort
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import sys
import re
import csv
import io
import uuid
import importlib
import importlib.util
import types
from collections import defaultdict
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
try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:
    Image = ImageDraw = ImageFont = None
    _HAS_PIL = False

# ── Module-level HTTP request cache (15-min TTL) ──────────────────────────────
_API_CACHE: dict = {}
_API_TTL = 900  # seconds
_PREDICTIONS_CACHE: dict = {}
_V2_PREDICTION_CACHE: dict = {}
_V2_PREDICTION_TTL_SECONDS = 900
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
_DAILY_REPORT_CACHE = {'ts': 0, 'date': None, 'html': None}
_DAILY_REPORT_TTL = 300
_SPORT_PREDICTIONS_PAGE_CACHE: dict = {}
_SPORT_PREDICTIONS_PAGE_TTL = {
    'SOCCER': 300,
    'MLB': 240,
    'NHL': 180,
    'NBA': 180,
    'NFL': 240,
    'NCAAB': 240,
    'NCAAW': 240,
    'NCAAF': 240,
    'WNBA': 240,
}
_MANUAL_BANNER_ITEMS = [
    {'label': 'NHL ⭐ Grinder2', 'pct': '83.3%', 'record': '40-8'},
    {'label': '🎲 NBA O/U (XSharp)', 'pct': '82.6%', 'record': '247/299'},
    {'label': 'MLB 🎯 Moneyline (Consensus)', 'pct': '60.0%', 'record': '60-40'},
    {'label': 'NHL 📊 Edge', 'pct': '56.5%', 'record': '113-87'},
]
_SHARE_IMAGE_CACHE_DIR = _os_v2.path.join(_os_v2.path.dirname(_os_v2.path.abspath(__file__)), '.cache', 'share_images')
_SHARE_TOKEN_RE = re.compile(r'^[a-f0-9]{32}$')
_SHARE_IMAGE_TTL_SECONDS = 3600
_SHARE_IMAGE_MAX_ITEMS = 500
_PROPS_ENGINE_MODULE = None
_PROPS_CONFIG_MODULE = None
# Standalone props live under backend/app; must not use top-level name "app" (root app.py shadows it).
_STANDALONE_PROPS_PKG = "_standalone_player_props"


def _cleanup_share_image_cache():
    """Remove stale or excess share-image JSON files (disk-backed for multi-worker processes)."""
    try:
        _os.makedirs(_SHARE_IMAGE_CACHE_DIR, exist_ok=True)
    except OSError:
        return
    now_ts = _time.time()
    paths = []
    try:
        for fn in _os.listdir(_SHARE_IMAGE_CACHE_DIR):
            if not fn.endswith('.json'):
                continue
            path = _os.path.join(_SHARE_IMAGE_CACHE_DIR, fn)
            try:
                st = _os.stat(path)
                paths.append((st.st_mtime, path))
            except OSError:
                continue
    except OSError:
        return
    for mtime, path in paths:
        if now_ts - mtime > _SHARE_IMAGE_TTL_SECONDS:
            try:
                _os.unlink(path)
            except OSError:
                pass
    paths = []
    try:
        for fn in _os.listdir(_SHARE_IMAGE_CACHE_DIR):
            if not fn.endswith('.json'):
                continue
            path = _os.path.join(_SHARE_IMAGE_CACHE_DIR, fn)
            try:
                st = _os.stat(path)
                paths.append((st.st_mtime, path))
            except OSError:
                continue
    except OSError:
        return
    paths.sort(key=lambda x: x[0])
    while len(paths) > _SHARE_IMAGE_MAX_ITEMS:
        _, oldest = paths.pop(0)
        try:
            _os.unlink(oldest)
        except OSError:
            pass


def _get_share_cache_entry(token: str):
    """Load share payload written by any worker; validates token shape and TTL."""
    if not token or not _SHARE_TOKEN_RE.match(token):
        return None
    path = _os.path.join(_SHARE_IMAGE_CACHE_DIR, f'{token}.json')
    if not _os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    ts = float(data.get('ts') or 0)
    if _time.time() - ts > _SHARE_IMAGE_TTL_SECONDS:
        try:
            _os.unlink(path)
        except OSError:
            pass
        return None
    return data


def _load_props_modules():
    global _PROPS_ENGINE_MODULE, _PROPS_CONFIG_MODULE
    if _PROPS_ENGINE_MODULE and _PROPS_CONFIG_MODULE:
        return _PROPS_ENGINE_MODULE, _PROPS_CONFIG_MODULE
    backend_root = _os.path.join(_BASE_DIR, "standalone-player-props", "backend")
    app_dir = _os.path.join(backend_root, "app")
    if not _os.path.isdir(app_dir):
        raise RuntimeError("Standalone props backend missing.")
    pkg_name = _STANDALONE_PROPS_PKG
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [app_dir]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg
    importlib.import_module(f"{pkg_name}.config")
    importlib.import_module(f"{pkg_name}.data_sources")
    cfg_mod = sys.modules[f"{pkg_name}.config"]
    eng_mod = importlib.import_module(f"{pkg_name}.engine")
    _PROPS_CONFIG_MODULE = cfg_mod
    _PROPS_ENGINE_MODULE = eng_mod
    return _PROPS_ENGINE_MODULE, _PROPS_CONFIG_MODULE


def _register_share_image(payload: dict) -> str:
    _cleanup_share_image_cache()
    token = uuid.uuid4().hex
    try:
        _os.makedirs(_SHARE_IMAGE_CACHE_DIR, exist_ok=True)
    except OSError:
        pass
    path = _os.path.join(_SHARE_IMAGE_CACHE_DIR, f'{token}.json')
    data = {'ts': _time.time(), 'payload': payload}
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        _os.replace(tmp, path)
    except OSError:
        try:
            if _os.path.isfile(tmp):
                _os.unlink(tmp)
        except OSError:
            pass
        raise
    return token


def _get_share_font(size: int, bold: bool = False):
    if not _HAS_PIL:
        return None
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _fit_text_font(draw, text: str, max_width: int, start_size: int, min_size: int = 20, bold: bool = True):
    if not text:
        return _get_share_font(start_size, bold=bold), text
    cleaned = str(text)
    for size in range(start_size, min_size - 1, -1):
        font = _get_share_font(size, bold=bold)
        if not font:
            continue
        bbox = draw.textbbox((0, 0), cleaned, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font, cleaned
    return _get_share_font(min_size, bold=bold), cleaned


def _render_predictions_share_image(payload: dict, fmt: str):
    if not _HAS_PIL:
        return None, None
    # 1080×1920, 9:16 vertical — fills a phone screen on TikTok/Reels (landscape 16:9 letterboxes on mobile).
    width, height = 1080, 1920
    pad = 44
    cx = width // 2
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    title_font = _get_share_font(92, bold=True)
    sub_font = _get_share_font(56, bold=True)
    rows = [r for r in (payload.get('cards') or [])[:3]]
    n = len(rows)
    title = f"{payload.get('sport', '')} Predictions"
    ht = 64
    draw.text((pad, ht), title, fill=(15, 23, 42), font=title_font)
    draw.text((pad, ht + 98), str(payload.get('date', '')), fill=(71, 85, 105), font=sub_font)
    header_bottom = ht + 98 + 62
    bottom_reserve = 48
    available = max(200, height - header_bottom - bottom_reserve)
    gap = 20
    max_name_w = width - 2 * pad - 32
    if n > 0:
        total_gap = gap * (n - 1)
        raw_slot = (available - total_gap) // n
        slot_height = max(380, min(560, raw_slot))
        block_h = n * slot_height + total_gap
        if block_h > available:
            slot_height = max(340, (available - total_gap) // n)
            block_h = n * slot_height + total_gap
        row_top = header_bottom + max(0, (available - block_h) // 2)
        vs_font = _get_share_font(52, bold=True)
        check_font = _get_share_font(48, bold=True)
        team_start = max(58, int(slot_height * 0.11))
        team_min = max(36, int(slot_height * 0.065))
        for idx, item in enumerate(rows):
            y1 = row_top + idx * (slot_height + gap)
            y2 = y1 + slot_height
            draw.rounded_rectangle((pad, y1, width - pad, y2), radius=24, outline=(203, 213, 225), width=3, fill=(255, 255, 255))
            away = str(item.get('away_team') or '')
            home = str(item.get('home_team') or '')
            away_font, away_text = _fit_text_font(draw, away, max_width=max_name_w, start_size=team_start, min_size=team_min, bold=True)
            home_font, home_text = _fit_text_font(draw, home, max_width=max_name_w, start_size=team_start, min_size=team_min, bold=True)
            away_bbox = draw.textbbox((0, 0), away_text, font=away_font)
            home_bbox = draw.textbbox((0, 0), home_text, font=home_font)
            away_w = away_bbox[2] - away_bbox[0]
            home_w = home_bbox[2] - home_bbox[0]
            away_y = y1 + int(slot_height * 0.12)
            vs_bbox = draw.textbbox((0, 0), "VS", font=vs_font)
            vs_w = vs_bbox[2] - vs_bbox[0]
            vs_y = y1 + int(slot_height * 0.42)
            home_y = y1 + int(slot_height * 0.66)
            draw.text((cx - away_w // 2, away_y), away_text, fill=(15, 23, 42), font=away_font)
            draw.text((cx - vs_w // 2, vs_y), "VS", fill=(100, 116, 139), font=vs_font)
            draw.text((cx - home_w // 2, home_y), home_text, fill=(15, 23, 42), font=home_font)
            if item.get('pick_side') == 'away':
                ax = cx - away_w // 2 - 54
                draw.rounded_rectangle((ax, away_y - 10, ax + 44, away_y + 38), radius=8, fill=(34, 197, 94))
                draw.text((ax + 9, away_y - 8), "✓", fill=(255, 255, 255), font=check_font)
            if item.get('pick_side') == 'home':
                hx = cx - home_w // 2 - 54
                draw.rounded_rectangle((hx, home_y - 10, hx + 44, home_y + 38), radius=8, fill=(34, 197, 94))
                draw.text((hx + 9, home_y - 8), "✓", fill=(255, 255, 255), font=check_font)
    output = io.BytesIO()
    out_fmt = 'JPEG' if fmt in ('jpg', 'jpeg') else 'PNG'
    if out_fmt == 'JPEG':
        image.save(output, format=out_fmt, quality=93, optimize=True, subsampling=0)
        mimetype = 'image/jpeg'
    else:
        image.save(output, format=out_fmt, optimize=True)
        mimetype = 'image/png'
    output.seek(0)
    return output.getvalue(), mimetype


def _render_daily_report_share_image(payload: dict, fmt: str):
    if not _HAS_PIL:
        return None, None
    width, height = 1080, 1920
    pad = 48
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    title_font = _get_share_font(78, bold=True)
    sub_font = _get_share_font(48, bold=True)
    label_font = _get_share_font(42, bold=True)
    val_font = _get_share_font(58, bold=True)
    meta_font = _get_share_font(46, bold=True)
    y = 72
    draw.text((pad, y), f"{payload.get('sport_name', '')} Results", fill=(15, 23, 42), font=title_font)
    y += 100
    draw.text((pad, y), str(payload.get('report_display', '')), fill=(71, 85, 105), font=sub_font)
    y += 72
    draw.text((pad, y), f"Games graded: {payload.get('games', 0)}", fill=(15, 23, 42), font=sub_font)
    y += 100
    card_h = 148
    gap = 18
    models = payload.get('models') or []
    for idx, model in enumerate(models[:5]):
        cy = y + idx * (card_h + gap)
        draw.rounded_rectangle((pad, cy, width - pad, cy + card_h), radius=22, outline=(203, 213, 225), width=3, fill=(248, 250, 252))
        draw.text((pad + 22, cy + 18), model.get('label', ''), fill=(51, 65, 85), font=label_font)
        acc = str(model.get('acc', '—'))
        rec = str(model.get('record', ''))
        acc_bbox = draw.textbbox((0, 0), acc, font=val_font)
        acc_w = acc_bbox[2] - acc_bbox[0]
        draw.text((width - pad - 22 - acc_w, cy + 28), acc, fill=(15, 23, 42), font=val_font)
        if rec:
            draw.text((pad + 22, cy + 86), rec, fill=(71, 85, 105), font=sub_font)
    y = y + min(len(models), 5) * (card_h + gap) + 36
    spread = payload.get('spread') or {}
    ou = payload.get('ou') or {}
    if spread.get('label'):
        draw.text((pad, y), f"Spread: {spread.get('label')}", fill=(15, 23, 42), font=meta_font)
        y += 64
    if ou.get('label'):
        draw.text((pad, y), f"Over/Under: {ou.get('label')}", fill=(15, 23, 42), font=meta_font)
    output = io.BytesIO()
    out_fmt = 'JPEG' if fmt in ('jpg', 'jpeg') else 'PNG'
    if out_fmt == 'JPEG':
        image.save(output, format=out_fmt, quality=93, optimize=True, subsampling=0)
        mimetype = 'image/jpeg'
    else:
        image.save(output, format=out_fmt, optimize=True)
        mimetype = 'image/png'
    output.seek(0)
    return output.getvalue(), mimetype


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
    """Attach odds to completed game results for ROI calculation.
    ESPN engine disabled — uses model-probability fallback only to avoid
    hundreds of HTTP requests that kill the Render worker."""
    if not daily_results:
        return
    for dd in daily_results.values():
        for g in dd.get('games', []):
            # Model-probability fallback only (no ESPN API calls)
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
    """Attach odds to upcoming predictions.
    ESPN engine disabled — uses model-probability fallback only to avoid
    hundreds of HTTP requests that kill the Render worker."""
    if not predictions:
        return
    for pred in predictions:
        if pred.get('home_score') is not None:
            continue
        # Model-probability fallback only (no ESPN API calls)
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


# ─────────────────────────────────────────────────────────────────────────────
# H2H (head-to-head) projected total
#
# "Our Total" is the last-N head-to-head games average between the two teams
# (default N=10), across all sports.
#
#   avg_home = mean of the (upcoming) home team's scores in past H2H games
#   avg_away = mean of the (upcoming) away team's scores in past H2H games
#   our_total  = avg_home + avg_away
#
# XGBoost's xgb_total is left untouched and is compared against our_total to
# produce the OVER / UNDER pick. Spread logic is completely unchanged.
# ─────────────────────────────────────────────────────────────────────────────
_H2H_PROJECTION_CACHE: dict = {}
_H2H_PROJECTION_TTL = 900  # 15 minutes


def _compute_h2h_projection(
    conn,
    sport: str,
    home_team: str,
    away_team: str,
    n: int = 10,
    min_games: int = 2,
):
    """Return last-N H2H projection for (home_team vs away_team) or None.

    Output dict keys:
        games_used, avg_home, avg_away, our_total, our_spread, totals (list),
        over_vs (callable placeholder) -- trend counts computed on demand.
    """
    if not (sport and home_team and away_team):
        return None
    cache_key = (sport, home_team, away_team, n)
    cached = _H2H_PROJECTION_CACHE.get(cache_key)
    now_ts = _time.time()
    if cached and (now_ts - cached['ts']) < _H2H_PROJECTION_TTL:
        return cached['data']
    try:
        rows = conn.execute(
            '''
            SELECT home_team_id, away_team_id, home_score, away_score, game_date
            FROM games
            WHERE sport = ?
              AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND (
                    (home_team_id = ? AND away_team_id = ?)
                 OR (home_team_id = ? AND away_team_id = ?)
              )
            ORDER BY date(game_date) DESC
            LIMIT ?
            ''',
            (sport, home_team, away_team, away_team, home_team, int(n)),
        ).fetchall()
    except Exception as _e:
        logger.debug(f"[h2h] query failed for {sport} {home_team} vs {away_team}: {_e}")
        return None
    if not rows or len(rows) < min_games:
        _H2H_PROJECTION_CACHE[cache_key] = {'ts': now_ts, 'data': None}
        return None
    home_pts = []
    away_pts = []
    totals = []
    for r in rows:
        try:
            hs = float(r['home_score'])
            as_ = float(r['away_score'])
        except Exception:
            continue
        if r['home_team_id'] == home_team:
            home_pts.append(hs)
            away_pts.append(as_)
        else:
            home_pts.append(as_)
            away_pts.append(hs)
        totals.append(hs + as_)
    if len(home_pts) < min_games:
        _H2H_PROJECTION_CACHE[cache_key] = {'ts': now_ts, 'data': None}
        return None
    avg_home = sum(home_pts) / len(home_pts)
    avg_away = sum(away_pts) / len(away_pts)
    data = {
        'games_used': len(home_pts),
        'avg_home': round(avg_home, 2),
        'avg_away': round(avg_away, 2),
        'our_total': round(avg_home + avg_away, 1),
        'totals': totals,
    }
    _H2H_PROJECTION_CACHE[cache_key] = {'ts': now_ts, 'data': data}
    return data


def _attach_h2h_projection_to_predictions(sport, predictions, n: int = 10):
    """Set pred['our_total'] and pred['our_spread'] using last-N H2H averages."""
    if not predictions:
        return
    try:
        conn = get_db_connection()
    except Exception as _e:
        logger.debug(f"[h2h] db connect failed for {sport}: {_e}")
        return
    try:
        for pred in predictions:
            ht = pred.get('home_team_id')
            at = pred.get('away_team_id')
            proj = _compute_h2h_projection(conn, sport, ht, at, n=n)
            if proj:
                pred['our_total'] = proj['our_total']
                pred['our_total_games'] = proj['games_used']
                pred['our_avg_home'] = proj['avg_home']
                pred['our_avg_away'] = proj['avg_away']
                # Keep H2H reference for UI (results page labels this "H2H Last 10";
                # NBA may later replace our_total with an efficiency projection).
                pred['h2h_last10_total'] = proj['our_total']
                pred['h2h_last10_games'] = proj['games_used']
            else:
                pred.setdefault('our_total', None)
                pred.setdefault('our_total_games', 0)
                pred.setdefault('h2h_last10_total', None)
                pred.setdefault('h2h_last10_games', 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# O/U model enhancements (post-hoc adjustments that DO NOT modify xgb_total)
#
# These produce a `pick_total` = xgb_total + injury_adj + rest_adj + park_adj
# used purely for grading / pick display. The raw xgb_total stays unchanged.
# ─────────────────────────────────────────────────────────────────────────────
_INJURY_OUT_STATUSES = {'Out', 'Injured Reserve', 'Inactive'}
_INJURY_DOUBTFUL_STATUSES = {'Doubtful'}

# Caches populated once per request so we don't open one DB connection per game.
_INJURY_COUNT_CACHE: dict = {'ts': 0, 'sport': None, 'data': {}}
_LAST_GAME_DATE_CACHE: dict = {'ts': 0, 'sport': None, 'data': {}}
_ENH_CACHE_TTL = 120  # 2 minutes — refreshed naturally by page-cache TTLs

# Points lost per starter ruled Out. Doubtful is treated at ~50%.
_INJURY_OUT_POINTS_PER_STARTER = {
    'NBA': 2.5, 'NCAAB': 2.0, 'NCAAW': 2.0, 'WNBA': 2.0,
    'NFL': 3.0, 'NCAAF': 3.0,
    'NHL': 0.25, 'MLB': 0.4, 'SOCCER': 0.15,
}
# MLB decision-layer parameters (no model retraining).
_MLB_EDGE_THRESHOLD = 0.05
_MLB_FAVORITE_EDGE_THRESHOLD = 0.08
_MLB_UNDERDOG_MIN_PROB = 0.42
_MLB_NOISE_MODEL_GAP = 0.02
_MLB_INJURY_CONF_DEFAULT = 0.75
_MLB_BULLPEN_FATIGUE_CACHE: dict = {}


def _round_to_half(value):
    """Round to nearest 0.5 (standard sportsbook increment)."""
    try:
        return round(float(value) * 2) / 2
    except (TypeError, ValueError):
        return value


def _odds_to_implied(odds):
    """American odds → raw implied probability (vig still included)."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def calculate_ev(model_prob, american_odds, stake=100):
    """
    EV% using model probability vs actual payout at given American odds.
    Positive = value bet. Formula: (p * net_payout - (1-p)) * 100.
    """
    try:
        p = float(model_prob)
        o = float(american_odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    net_payout = o / 100.0 if o > 0 else 100.0 / abs(o)
    return round((p * net_payout - (1.0 - p)) * 100.0, 1)


def calculate_ev_devigged(model_prob, pick_odds, opp_odds):
    """
    EV% with de-vigged market probability as baseline.
    Steps:
      1. Convert both sides to implied prob (with vig).
      2. Normalize (remove vig) → true no-vig probability.
      3. EV = (model_p * net_payout - (1 - model_p)) * 100.
    Returns (ev_pct, devig_prob, implied_prob, vig_pct) for debugging.
    """
    p_impl_pick = _odds_to_implied(pick_odds)
    p_impl_opp  = _odds_to_implied(opp_odds)
    if p_impl_pick is None or p_impl_opp is None:
        return None, None, None, None
    total_impl = p_impl_pick + p_impl_opp
    if total_impl <= 0:
        return None, None, None, None
    vig_pct     = round((total_impl - 1.0) * 100.0, 2)
    devig_prob  = round(p_impl_pick / total_impl, 4)   # true no-vig probability
    try:
        p = float(model_prob)
        o = float(pick_odds)
    except (TypeError, ValueError):
        return None, devig_prob, round(p_impl_pick, 4), vig_pct
    net_payout = o / 100.0 if o > 0 else 100.0 / abs(o)
    ev_pct = round((p * net_payout - (1.0 - p)) * 100.0, 1)
    return ev_pct, devig_prob, round(p_impl_pick, 4), vig_pct


def _american_to_implied_prob(odds):
    """Convert American odds to implied probability."""
    try:
        o = float(odds)
    except Exception:
        return None
    if o == 0:
        return None
    if o > 0:
        return 100.0 / (o + 100.0)
    return abs(o) / (abs(o) + 100.0)


def _mlb_pitcher_quality_tier(era, xera=None, whip=None, kbb=None, recent_form=None):
    """Return pitcher tier + score using available run-prevention indicators."""
    score = 0.0
    count = 0
    for v in (era, xera):
        if v is None:
            continue
        count += 1
        if v <= 3.15:
            score += 1.0
        elif v <= 3.7:
            score += 0.75
        elif v <= 4.3:
            score += 0.5
        elif v <= 4.9:
            score += 0.3
        else:
            score += 0.1
    if whip is not None:
        count += 1
        if whip <= 1.10:
            score += 1.0
        elif whip <= 1.22:
            score += 0.75
        elif whip <= 1.32:
            score += 0.5
        elif whip <= 1.45:
            score += 0.3
        else:
            score += 0.1
    if kbb is not None:
        count += 1
        if kbb >= 4.0:
            score += 1.0
        elif kbb >= 3.0:
            score += 0.75
        elif kbb >= 2.2:
            score += 0.5
        elif kbb >= 1.6:
            score += 0.3
        else:
            score += 0.1
    if recent_form is not None:
        count += 1
        if recent_form <= 2.8:
            score += 1.0
        elif recent_form <= 3.5:
            score += 0.75
        elif recent_form <= 4.2:
            score += 0.5
        elif recent_form <= 5.0:
            score += 0.3
        else:
            score += 0.1
    avg = (score / count) if count else 0.5
    if avg >= 0.86:
        return 'elite', avg
    if avg >= 0.67:
        return 'above_avg', avg
    if avg >= 0.45:
        return 'average', avg
    if avg >= 0.30:
        return 'below_avg', avg
    return 'replacement', avg


def _mlb_recent_pitcher_form(pitcher_name):
    """Approximate last-3-start form from recent game logs in local DB."""
    if not pitcher_name:
        return None
    try:
        conn = get_db_connection()
        rows = conn.execute(
            '''
            SELECT ERA
            FROM player_game_logs
            WHERE sport='MLB' AND player_name=?
            ORDER BY game_date DESC
            LIMIT 3
            ''',
            (pitcher_name,),
        ).fetchall()
        conn.close()
        vals = []
        for r in rows:
            try:
                vals.append(float(r['ERA']))
            except Exception:
                continue
        if not vals:
            return None
        return sum(vals) / len(vals)
    except Exception:
        return None


def _mlb_lineup_tier(position):
    p = str(position or '').upper()
    if p in {'SS', 'CF', '1B', '3B', 'DH'}:
        return 1
    if p in {'2B', 'LF', 'RF', 'C'}:
        return 2
    return 3


def _mlb_bullpen_fatigue_boost(team_name, game_date):
    """Estimate bullpen fatigue from prior game timing + runs allowed."""
    if not team_name or not game_date:
        return 0.0, 0.0, False
    gday = str(game_date)[:10]
    cache_key = f"{team_name}|{gday}"
    cached = _MLB_BULLPEN_FATIGUE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        conn = get_db_connection()
        row = conn.execute(
            '''
            SELECT date(game_date) AS d,
                   CASE WHEN home_team_id=? THEN away_score ELSE home_score END AS runs_allowed
            FROM games
            WHERE sport='MLB'
              AND (home_team_id=? OR away_team_id=?)
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND date(game_date) < date(?)
            ORDER BY date(game_date) DESC
            LIMIT 1
            ''',
            (team_name, team_name, team_name, gday),
        ).fetchone()
        conn.close()
        if not row or not row['d']:
            _MLB_BULLPEN_FATIGUE_CACHE[cache_key] = (0.0, 0.0, False)
            return 0.0, 0.0, False
        prev = datetime.strptime(row['d'], '%Y-%m-%d')
        cur = datetime.strptime(gday, '%Y-%m-%d')
        is_b2b = (cur - prev).days <= 1
        runs_allowed = float(row['runs_allowed']) if row['runs_allowed'] is not None else 4.0
        boost = 0.0
        total_adj = 0.0
        if is_b2b and runs_allowed >= 6:
            boost = 0.02
            total_adj = 0.5
        elif is_b2b:
            boost = 0.01
            total_adj = 0.5
        _MLB_BULLPEN_FATIGUE_CACHE[cache_key] = (boost, total_adj, is_b2b)
        return boost, total_adj, is_b2b
    except Exception:
        return 0.0, 0.0, False
# Rest (back-to-back) penalty applied to each team if their prior completed game
# was the day before the current game.
_B2B_PENALTY = {
    'NBA': 1.5, 'NCAAB': 1.0, 'NCAAW': 1.0, 'WNBA': 1.0,
    'NFL': 0.0, 'NCAAF': 0.0,
    'NHL': 0.15, 'MLB': 0.1, 'SOCCER': 0.05,
}
# CLV edge thresholds: minimum |xgb_total - market_total| needed to post a pick.
_OU_EDGE_THRESHOLD = {
    'NBA': 2.5, 'NCAAB': 2.5, 'NCAAW': 2.5, 'WNBA': 2.5,
    'NFL': 1.5, 'NCAAF': 2.5,
    'NHL': 0.25, 'MLB': 0.4, 'SOCCER': 0.25,
}
# MLB park/weather factor relative to neutral 8.9 baseline.
_MLB_PARK_FACTORS = {
    'Colorado Rockies': +1.2, 'Boston Red Sox': +0.4, 'Cincinnati Reds': +0.3,
    'Chicago Cubs': +0.2, 'Baltimore Orioles': +0.2, 'Arizona Diamondbacks': +0.1,
    'San Francisco Giants': -0.4, 'San Diego Padres': -0.3, 'Oakland Athletics': -0.3,
    'Miami Marlins': -0.3, 'Seattle Mariners': -0.2,
}
# NFL rough weather (cold/wind) factor by home team outdoor stadium.
_NFL_COLD_TEAMS = {
    'Buffalo Bills', 'Green Bay Packers', 'Chicago Bears', 'Cleveland Browns',
    'Pittsburgh Steelers', 'Denver Broncos', 'Cincinnati Bengals', 'New England Patriots',
    'Philadelphia Eagles', 'New York Jets', 'New York Giants', 'Washington Commanders',
    'Kansas City Chiefs',
}


def _load_injury_counts(sport):
    """Load all injury counts for a sport once and cache them per process."""
    cache = _INJURY_COUNT_CACHE
    now_ts = _time.time()
    if cache.get('sport') == sport and (now_ts - cache.get('ts', 0)) < _ENH_CACHE_TTL:
        return cache['data']
    data: dict = {}
    try:
        conn = get_db_connection()
        rows = conn.execute(
            'SELECT team_name, status FROM injuries WHERE sport=?',
            (sport,),
        ).fetchall()
        conn.close()
        agg: dict = {}
        for r in rows:
            t = r['team_name'] or ''
            if not t:
                continue
            bucket = agg.setdefault(t, {'out': 0, 'dbt': 0})
            if r['status'] in _INJURY_OUT_STATUSES:
                bucket['out'] += 1
            elif r['status'] in _INJURY_DOUBTFUL_STATUSES:
                bucket['dbt'] += 1
        for t, b in agg.items():
            data[t] = min(5.0, b['out'] + 0.5 * b['dbt'])
    except Exception as _e:
        logger.debug(f"[injuries] bulk load failed for {sport}: {_e}")
    _INJURY_COUNT_CACHE.update({'ts': now_ts, 'sport': sport, 'data': data})
    return data


def _count_out_injured_starters(sport, team_name):
    """Return a weighted count of top impact players ruled Out/Doubtful (cached)."""
    if not (sport and team_name):
        return 0.0
    return _load_injury_counts(sport).get(team_name, 0.0)


def _injury_total_adjustment(sport, home_team, away_team):
    """Subtract points from projected total based on Out/Doubtful players on both rosters."""
    pts_per = _INJURY_OUT_POINTS_PER_STARTER.get(sport, 0.0)
    if not pts_per:
        return 0.0
    adj = 0.0
    adj -= pts_per * _count_out_injured_starters(sport, home_team)
    adj -= pts_per * _count_out_injured_starters(sport, away_team)
    return adj


def _load_team_game_dates(sport):
    """Load every team's sorted list of completed game dates once per process.
    Returns {team: [date_str asc]}. O(1) lookup for 'last game before X'.
    """
    cache = _LAST_GAME_DATE_CACHE
    now_ts = _time.time()
    if cache.get('sport') == sport and (now_ts - cache.get('ts', 0)) < _ENH_CACHE_TTL:
        return cache['data']
    data: dict = {}
    try:
        conn = get_db_connection()
        rows = conn.execute(
            '''SELECT home_team_id, away_team_id, date(game_date) AS d
               FROM games
               WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL
               ORDER BY date(game_date)''',
            (sport,),
        ).fetchall()
        conn.close()
        for r in rows:
            d = r['d']
            if not d:
                continue
            for t in (r['home_team_id'], r['away_team_id']):
                if not t:
                    continue
                lst = data.setdefault(t, [])
                if not lst or lst[-1] != d:
                    lst.append(d)
    except Exception as _e:
        logger.debug(f"[rest] bulk load failed for {sport}: {_e}")
    _LAST_GAME_DATE_CACHE.update({'ts': now_ts, 'sport': sport, 'data': data})
    return data


def _last_game_date_for_team(sport, team, before_date):
    """Return the most recent completed game_date (YYYY-MM-DD) for team strictly before a date."""
    if not (sport and team and before_date):
        return None
    dates = _load_team_game_dates(sport).get(team)
    if not dates:
        return None
    # Binary search for rightmost date < before_date.
    import bisect
    i = bisect.bisect_left(dates, str(before_date)[:10])
    if i <= 0:
        return None
    return dates[i - 1]


def _rest_total_adjustment(sport, home_team, away_team, game_date):
    """Penalise total if either team is on a back-to-back (prior game exactly 1 day before)."""
    penalty = _B2B_PENALTY.get(sport, 0.0)
    if not penalty or not game_date:
        return 0.0
    from datetime import datetime as _dt, timedelta as _td
    try:
        gd = _dt.strptime(str(game_date)[:10], '%Y-%m-%d')
    except Exception:
        return 0.0
    total_adj = 0.0
    for team in (home_team, away_team):
        last = _last_game_date_for_team(sport, team, gd.strftime('%Y-%m-%d'))
        if not last:
            continue
        try:
            ld = _dt.strptime(last[:10], '%Y-%m-%d')
        except Exception:
            continue
        if (gd - ld).days <= 1:
            total_adj -= penalty
    return total_adj


def _park_weather_total_adjustment(sport, home_team):
    """Return a park/weather adjustment for the total projection."""
    if sport == 'MLB':
        return _MLB_PARK_FACTORS.get(home_team, 0.0)
    if sport == 'NFL':
        # Cold / outdoor stadiums lean slightly UNDER in winter months.
        from datetime import datetime as _dt
        month = _dt.now().month
        if home_team in _NFL_COLD_TEAMS and month in (11, 12, 1, 2):
            return -1.5
    return 0.0


def _ou_edge_threshold(sport):
    return _OU_EDGE_THRESHOLD.get(sport, 0.0)


def _attach_h2h_projection_to_daily_results(sport, daily_results, n: int = 10):
    """Set g['our_total']/g['our_spread'] on each completed game using prior H2H."""
    if not daily_results:
        return
    try:
        conn = get_db_connection()
    except Exception as _e:
        logger.debug(f"[h2h] db connect failed for {sport}: {_e}")
        return
    try:
        for dd in daily_results.values():
            for g in dd.get('games', []):
                ht = g.get('home')
                at = g.get('away')
                proj = _compute_h2h_projection(conn, sport, ht, at, n=n)
                if proj:
                    g['our_total'] = proj['our_total']
                    g['our_total_games'] = proj['games_used']
                    g['our_avg_home'] = proj['avg_home']
                    g['our_avg_away'] = proj['avg_away']
                else:
                    g.setdefault('our_total', None)
                    g.setdefault('our_total_games', 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
            'glicko2_correct':   (glicko2_prob   >= 0.5) == home_won if glicko2_prob   is not None and home_won is not None else None,
            'trueskill_correct': (trueskill_prob >= 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
            'elo_correct':       (elo_prob  >= 0.5) == home_won if home_won is not None else None,
            'xgb_correct':       (xgb_prob  >= 0.5) == home_won if home_won is not None else None,
            'ens_correct':       (ens_prob  >= 0.5) == home_won if ens_prob is not None and home_won is not None else None,
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
    if sport not in ['NBA', 'MLB', 'SOCCER'] or not predictions:
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
    if sport not in ['NBA', 'MLB', 'SOCCER'] or not daily_results:
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
                    if gd_dt and sport in ['NBA', 'MLB', 'SOCCER'] and abs((datetime.now() - gd_dt).days) > 3:
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

    sport_slug, league_slug = sport_path
    # Soccer game_ids are formatted 'SOCCER_<espn-league-code>_<event_id>'.
    # ESPN's core API requires a real league slug (not 'all'), so parse it out.
    soccer_league_slugs = []
    if sport == 'SOCCER':
        try:
            parts = str(game_id).split('_')
            if len(parts) >= 3:
                soccer_league_slugs.append(parts[1])
        except Exception:
            pass
        # Fallback: probe the most common leagues if we cannot parse the slug.
        if not soccer_league_slugs:
            soccer_league_slugs = [
                'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1',
                'uefa.champions', 'uefa.europa', 'uefa.europa.conf',
                'usa.1', 'mex.1', 'ned.1', 'por.1',
            ]
    for event_id in event_candidates:
        league_candidates = soccer_league_slugs if sport == 'SOCCER' else [league_slug]
        for _league_slug in league_candidates:
            odds_url = (
                f"https://sports.core.api.espn.com/v2/sports/{sport_slug}/leagues/{_league_slug}/"
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
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app, origins=[
    'https://predictionlab.io',
    'https://predictionlab.io',
    'http://localhost:3000',
    'http://localhost:5000',
])

_CANONICAL_HOST = 'predictionlab.io'

@app.before_request
def enforce_canonical_domain():
    """Redirect underdogs.bet/http variants to canonical https://predictionlab.io."""
    host = (request.host or '').split(':')[0].lower()
    if not host or host in {'localhost', '127.0.0.1'} or host.endswith('.local'):
        return None
    if not (host.endswith('underdogs.bet') or host.endswith('predictionlab.io')):
        return None
    target_host = _CANONICAL_HOST
    is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
    needs_redirect = (host != target_host) or (not is_https)
    if not needs_redirect:
        # Canonicalize noisy homepage query URLs seen by crawlers (/?q=...).
        if request.path == '/' and request.args.get('q'):
            return redirect(f"https://{target_host}/", code=301)
        return None
    # request.full_path includes trailing '?' when no query string; strip it.
    full_path = request.full_path[:-1] if request.full_path.endswith('?') else request.full_path
    return redirect(f"https://{target_host}{full_path}", code=301)

@app.context_processor
def inject_globals():
    """Make global template variables available in every template automatically."""
    # Determine current sport from request args or view context
    _sport = request.view_args.get('sport', '') if request.view_args else ''
    try:
        from flask_login import current_user as _cu
        _logged_in = getattr(_cu, 'is_authenticated', False) and _cu.is_authenticated
    except Exception:
        _logged_in = False
    try:
        _wnba_status, _wnba_live = get_season_status('WNBA')
    except Exception:
        _wnba_live = True
    return {
        'stripe_donation_url': STRIPE_DONATION_URL,
        'contact_email': CONTACT_EMAIL,
        'social_links': SOCIAL_LINKS,
        'soccer_enabled': SOCCER_ENABLED,
        'ga_tracking_id': GA_TRACKING_ID,
        'sport_seo_slug': SPORT_SEO_SLUGS.get(_sport, ''),
        'sport_results_slug': _SPORT_RESULTS_SLUGS.get(_sport, ''),
        'is_logged_in': _logged_in,
        'wnba_enabled': _wnba_live,
    }

@app.after_request
def add_header(response):
    """Add headers to allow iframe embedding from underdogs.bet"""
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    response.headers['Content-Security-Policy'] = (
        "frame-ancestors 'self' https://underdogs.bet https://predictionlab.io "
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

# Image backgrounds removed site-wide
SPORT_BG_IMAGES = {
    'NFL': '',
    'NCAAF': '',
    'SOCCER': '',
    'NBA': '',
    'WNBA': '',
    'NCAAB': '',
    'NCAAW': '',
    'MLB': '',
    'NHL': '',
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
        default_lookback_days = 10
        
        # Fetch recent window to keep request latency low while still catching missed finals.
        from datetime import datetime, timedelta
        today = datetime.now()
        
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

        # Backfill from the most recent graded date forward so results never get stuck.
        latest_row = cursor.execute(
            """
            SELECT MAX(date(game_date))
            FROM games
            WHERE sport = 'NHL'
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            """
        ).fetchone()
        latest_completed = latest_row[0] if latest_row else None
        lookback_days = default_lookback_days
        if latest_completed:
            try:
                latest_dt = datetime.strptime(str(latest_completed), '%Y-%m-%d')
                gap_days = (today.date() - latest_dt.date()).days
                lookback_days = max(default_lookback_days, gap_days + 2)
            except Exception:
                pass
        lookback_days = min(max(lookback_days, default_lookback_days), 120)
        logger.info(f"Fetching NHL scores from API (last {lookback_days} days)...")
        start_date = today - timedelta(days=lookback_days)
        
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
                                    """, ('NHL', 'NHL', game_id, int(str(date_str)[:4]), date_str, home_team, away_team, home_score, away_score))
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
        CREATE TABLE IF NOT EXISTS player_prop_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league TEXT NOT NULL,
            result_date TEXT NOT NULL,
            player_name TEXT NOT NULL,
            team TEXT,
            prop_type TEXT NOT NULL,
            pick TEXT NOT NULL,
            line REAL NOT NULL,
            projection REAL,
            actual REAL,
            result TEXT NOT NULL,
            UNIQUE(league, result_date, player_name, prop_type)
        );
        CREATE INDEX IF NOT EXISTS idx_ppr_league_date ON player_prop_results(league, result_date);
        CREATE INDEX IF NOT EXISTS idx_pred_home_team ON predictions(home_team_id);
        CREATE INDEX IF NOT EXISTS idx_pred_away_team ON predictions(away_team_id);
        CREATE INDEX IF NOT EXISTS idx_pred_sport ON predictions(sport);
        CREATE INDEX IF NOT EXISTS idx_pred_game_date ON predictions(game_date);
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


def _maybe_backfill_soccer_on_startup():
    """If the DB has fewer than 200 completed Soccer games in the last 90 days,
    run the historical backfill in a background thread so Soccer results pages
    have data. Guarded by a file flag + a DB-count threshold so it only runs when
    truly needed."""
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM games
               WHERE sport='SOCCER' AND home_score IS NOT NULL
                 AND date(game_date) >= date('now','-90 days')"""
        ).fetchone()
        conn.close()
        recent_n = row['n'] if row else 0
    except Exception as _e:
        logger.debug(f"[soccer-backfill] count check failed: {_e}")
        return
    if recent_n >= 200:
        return  # already populated
    flag_path = _os.path.join(_os.path.dirname(DATABASE), '.soccer_backfill_ran')
    if _os.path.exists(flag_path):
        return  # already attempted this deploy
    import threading
    def _run():
        try:
            logger.info(f"[soccer-backfill] starting (recent_n={recent_n})...")
            from backfill_soccer import backfill as _bf
            _bf()
            try:
                open(flag_path, 'w').write('done')
            except Exception:
                pass
            logger.info("[soccer-backfill] finished.")
        except Exception as _be:
            logger.warning(f"[soccer-backfill] failed: {_be}")
    threading.Thread(target=_run, daemon=True, name='soccer-backfill').start()

try:
    _maybe_backfill_soccer_on_startup()
except Exception as _sbe:
    logger.debug(f"[soccer-backfill] hook error: {_sbe}")


def _maybe_backfill_props_on_startup():
    """Run the NBA props backfill in a background thread if yesterday's data is missing.

    Checks if the DB already has graded props for the past 7 days before spawning
    a thread; guards against repeated runs within the same calendar day via a flag file.
    """
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM player_prop_results
               WHERE league='NBA'
                 AND result_date >= date('now','-7 days')"""
        ).fetchone()
        conn.close()
        recent_n = row['n'] if row else 0
    except Exception as _e:
        logger.debug(f"[props-backfill] count check failed: {_e}")
        return

    if recent_n >= 50:
        return  # already populated

    from datetime import date as _date2
    today_str = str(_date2.today())
    flag_path = _os.path.join(_os.path.dirname(DATABASE), f'.props_backfill_{today_str}')
    if _os.path.exists(flag_path):
        return  # already ran today

    import threading
    def _run():
        try:
            logger.info("[props-backfill] starting…")
            from backfill_props import run as _bf_run
            _bf_run(dry_run=False)
            try:
                open(flag_path, 'w').write('done')
            except Exception:
                pass
            logger.info("[props-backfill] finished.")
        except Exception as _be:
            logger.warning(f"[props-backfill] failed: {_be}")
    threading.Thread(target=_run, daemon=True, name='props-backfill').start()


try:
    _maybe_backfill_props_on_startup()
except Exception as _pbe:
    logger.debug(f"[props-backfill] hook error: {_pbe}")


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
    
    cache_date = (game_date or datetime.now().strftime('%Y-%m-%d'))
    cache_key = f"{model_sport}|{home_team}|{away_team}|{cache_date}"
    now_ts = _time.time()
    cached = _V2_PREDICTION_CACHE.get(cache_key)
    if cached and (now_ts - cached['ts']) < _V2_PREDICTION_TTL_SECONDS:
        return _copy.deepcopy(cached['data'])

    try:
        predictor = V2_PREDICTORS[model_sport]
        game_df = pd.DataFrame([{
            'home_team': home_team,
            'away_team': away_team,
            'date': cache_date
        }])
        
        pred = predictor.predict(game_df)
        row = pred.iloc[0]
        
        result = {
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
        _V2_PREDICTION_CACHE[cache_key] = {'ts': now_ts, 'data': _copy.deepcopy(result)}
        return result
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
                SELECT team_name, player_name, position, status, injury_type
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
                    'position': row['position'] or '',
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
                pos = (athlete.get('position') or {}).get('abbreviation', '')
                # Extract injury body part from shortComment e.g. "Player (knee) is out..."
                comment = inj.get('shortComment', '')
                import re as _re
                match = _re.search(r'\(([^)]{1,20})\)', comment)
                reason = match.group(1) if match else ''
                players.append({'name': short_name, 'position': pos, 'status': status, 'reason': reason})
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
        # Loop -1 to +3 days (reduced from +7 to cut API calls on cold start).
        # Each league needs its own request; results are cached for 15 min.
        api_games = []
        for days_offset in range(-1, 4):
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

        # All sports use a single date-range API call (1 request) instead of
        # day-by-day loops (22+ requests) that kill the Render worker.
        if sport in ['NBA', 'NFL', 'NCAAF', 'MLB', 'WNBA', 'NCAAB', 'NCAAW']:
            # Tight windows: enough for predictions page without overloading Render
            _SPORT_WINDOWS = {
                'NFL':   (14, 14, 200),   # offseason — small
                'NCAAF': (14, 14, 200),   # offseason — small
                'NBA':   (3,  7,  200),   # playoffs — tight
                'MLB':   (3,  5,  100),   # daily — tight
                'WNBA':  (3,  7,  100),
                'NCAAB': (3,  7,  200),
                'NCAAW': (3,  7,  200),
            }
            _lookback, _forward, _api_limit = _SPORT_WINDOWS.get(sport, (3, 7, 200))
            start_str = (datetime.now() - timedelta(days=_lookback)).strftime('%Y%m%d')
            end_str = (datetime.now() + timedelta(days=_forward)).strftime('%Y%m%d')
            _extra = '&groups=50' if sport == 'NCAAB' else ''
            try:
                url = f"{ESPN_ENDPOINTS[sport]}?dates={start_str}-{end_str}&limit={_api_limit}{_extra}"
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
        # (day-by-day fallback removed — all sports now use date-range above)
        
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
        'WNBA': datetime(2026, 5, 8),
        'SOCCER': datetime(2024, 8, 1),
    }
    season_start = season_starts.get(sport, datetime(2025, 1, 1))
    
    # Calculate cutoff horizon by sport
    # Use module-level datetime/timedelta imports to avoid local shadowing
    today = datetime.now()
    future_window_days = {
        'NBA': 30,
    }
    future_cutoff = today + timedelta(days=future_window_days.get(sport, 30))
    
    predictions = []
    # Fetch injuries once for the whole request (15-min cache keeps it fast)
    _injuries = _fetch_injuries(sport)
    # Build heavy model objects once per page render (not once per game row).
    _xgb_model_page = None
    try:
        _xgb_model_page = _get_xgb_spread_model(sport)
    except Exception:
        _xgb_model_page = None

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
            is_completed = game.get('home_score') is not None
            if sport != 'SOCCER' and not is_completed:
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
                _g2_soc = soccer_pred.get('poisson_xg_prob')
                _ts_soc = soccer_pred.get('markov_prob')
                # Count how many valid model outputs we have
                _valid_count = sum(1 for p in [elo_prob, xgb_prob, _g2_soc, _ts_soc] if p is not None and abs(p - 0.5) > 0.005)
                if _valid_count < 2:
                    # Insufficient data — don't show fake predictions
                    elo_prob = 0.5
                    xgb_prob = None
                    ensemble_prob = None
                    game['glicko2_prob'] = None
                    game['trueskill_prob'] = None
                    game['soccer_model_note'] = soccer_note or 'Insufficient data for reliable prediction'
                else:
                    if xgb_prob is None:
                        xgb_prob = elo_prob
                    if ensemble_prob is None:
                        ensemble_prob = elo_prob
                    game['glicko2_prob'] = _g2_soc
                    game['trueskill_prob'] = _ts_soc
                    game['soccer_model_note'] = None
                game['v2_expected_home'] = soccer_pred.get('expected_home_score')
                game['v2_expected_away'] = soccer_pred.get('expected_away_score')
                game['is_v2'] = True
            elif sport == 'SOCCER' and not soccer_pred:
                # Soccer without model data — show insufficient data
                elo_prob = 0.5
                xgb_prob = None
                ensemble_prob = None
                game['glicko2_prob'] = None
                game['trueskill_prob'] = None
                game['soccer_model_note'] = soccer_note or 'Insufficient data for reliable prediction'
                game['is_v2'] = False
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
            game_dict['elo_prob'] = round(elo_prob * 100, 1) if elo_prob is not None else None
            game_dict['xgb_prob'] = round(xgb_prob * 100, 1) if xgb_prob is not None else None
            game_dict['ensemble_prob'] = round(ensemble_prob * 100, 1) if ensemble_prob is not None else None
            if ensemble_prob is not None:
                game_dict['predicted_winner'] = game['home_team_id'] if ensemble_prob > 0.5 else game['away_team_id']
            elif elo_prob is not None:
                game_dict['predicted_winner'] = game['home_team_id'] if elo_prob > 0.5 else game['away_team_id']
            else:
                game_dict['predicted_winner'] = game['home_team_id']  # fallback
            
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

            # Compute expensive spread/total projections for upcoming games only.
            # Completed-game cards rely on stored lines/results and should render fast.
            if game_dict.get('home_score') is None and sport == 'SOCCER':
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
            elif game_dict.get('home_score') is None:
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

            if game_dict.get('home_score') is None:
                try:
                    if _xgb_model_page:
                        result = _xgb_model_page.predict(
                            game_dict.get('home_team_id', ''),
                            game_dict.get('away_team_id', ''),
                        )
                        if result and result[0] is not None:
                            game_dict['xgb_home_score'] = round(result[0])
                            game_dict['xgb_away_score'] = round(result[1])
                            game_dict['xgb_spread'] = _round_to_half(result[2]) if result[2] is not None else None
                            game_dict['xgb_total'] = _round_to_half(result[3]) if result[3] is not None else None
                except Exception as _e:
                    logger.debug(f"XGBSpread error: {_e}")

            if game_dict.get('home_score') is None:
                # ── MLB: pitching-enhanced prediction (upcoming games only
                #    so we do not retroactively rewrite picks for completed games) ─
                if sport == 'MLB':
                    try:
                        from mlb_runs_model import get_or_train_mlb_model as _get_mlb_model
                        import math as _math
                        _ht = game_dict.get('home_team_id', '')
                        _at = game_dict.get('away_team_id', '')
                        _gdate = game_dict.get('game_date')
                        _home_mkt = _to_float_safe(game_dict.get('home_implied_prob'))
                        _away_mkt = _to_float_safe(game_dict.get('away_implied_prob'))
                        _home_ml = _to_float_safe(game_dict.get('home_moneyline'))
                        _away_ml = _to_float_safe(game_dict.get('away_moneyline'))
                        if _home_mkt is None and _home_ml is not None:
                            _home_mkt = _american_to_implied_prob(_home_ml)
                        if _away_mkt is None and _away_ml is not None:
                            _away_mkt = _american_to_implied_prob(_away_ml)

                        # 1. ML correction: runs model spread → probability
                        _ml_prob = 0.5
                        _mlbm = _get_mlb_model(DATABASE)
                        if _mlbm:
                            _mlb_result = _mlbm.predict(_ht, _at)
                            if _mlb_result and _mlb_result[0] is not None:
                                game_dict['xgb_home_score'] = round(_mlb_result[0])
                                game_dict['xgb_away_score'] = round(_mlb_result[1])
                                game_dict['xgb_spread']     = _round_to_half(_mlb_result[2]) if _mlb_result[2] is not None else None
                                game_dict['xgb_total']      = _round_to_half(_mlb_result[3]) if _mlb_result[3] is not None else None
                                _mlb_spread = float(_mlb_result[2])
                                _ml_prob = 0.5 * (1.0 + _math.erf(_mlb_spread / (3.0 * _math.sqrt(2))))
                        # 2. Pitching adjustment (cached, single ESPN API call)
                        _pitch_prob = 0.5
                        _pitch = {}
                        try:
                            from mlb_pitching import get_mlb_pitching_adjustment as _get_pitching
                            _pitch = _get_pitching(_ht, _at)
                            _pitch_prob = _pitch.get('pitching_prob', 0.5)
                        except Exception:
                            pass

                        # 3. Elo / v2 baselines (dynamic weighting for MLB)
                        _elo_base = elo_prob

                        _g2_prob = _to_float_safe(game.get('glicko2_prob'), _elo_base)
                        _ts_prob = _to_float_safe(game.get('trueskill_prob'), _elo_base)
                        _xgb_prob = _to_float_safe(_ml_prob, _elo_base)
                        _ens_prob = _to_float_safe(ensemble_prob, _elo_base)

                        # Rule #5: MLB dynamic model weighting.
                        # Increase XGB + TrueSkill, reduce Elo + Glicko-2 influence.
                        _weights = {
                            'xgb': 0.35,
                            'trueskill': 0.27,
                            'ensemble': 0.23,
                            'elo': 0.08,
                            'glicko2': 0.07,
                        }

                        # Rule #2: Value underdog boosts XGB + Ensemble, reduces rating systems.
                        _pre_blended = (
                            _weights['xgb'] * _xgb_prob
                            + _weights['trueskill'] * _ts_prob
                            + _weights['ensemble'] * _ens_prob
                            + _weights['elo'] * _elo_base
                            + _weights['glicko2'] * _g2_prob
                        )
                        _value_underdog = False
                        if _home_mkt is not None and _away_mkt is not None:
                            _market_pick_home = (_home_mkt <= _away_mkt)
                            _model_pick_home = _pre_blended >= 0.5
                            if _model_pick_home != _market_pick_home:
                                _dog_model_prob = _pre_blended if _model_pick_home else (1.0 - _pre_blended)
                                _dog_market_prob = _home_mkt if _model_pick_home else _away_mkt
                                if (_dog_model_prob - _dog_market_prob) >= _MLB_EDGE_THRESHOLD and _dog_model_prob >= _MLB_UNDERDOG_MIN_PROB:
                                    _value_underdog = True
                                    _weights.update({'xgb': 0.40, 'ensemble': 0.28, 'trueskill': 0.22, 'elo': 0.06, 'glicko2': 0.04})

                        _blended = (
                            _weights['xgb'] * _xgb_prob
                            + _weights['trueskill'] * _ts_prob
                            + _weights['ensemble'] * _ens_prob
                            + _weights['elo'] * _elo_base
                            + _weights['glicko2'] * _g2_prob
                        )

                        # Rule #10: role-based injury adjustment layer.
                        _inj_conf = _MLB_INJURY_CONF_DEFAULT
                        if not (_pitch.get('home_sp_name') and _pitch.get('away_sp_name')):
                            _inj_conf = 0.55
                        elif not (game_dict.get('home_injuries') or game_dict.get('away_injuries')):
                            _inj_conf = 0.65

                        _home_inj = game_dict.get('home_injuries') or []
                        _away_inj = game_dict.get('away_injuries') or []
                        _home_adj = 0.0
                        _away_adj = 0.0
                        _total_adj = 0.0

                        def _apply_pitcher_scratch(injury_list, sp_name, side_quality):
                            if not sp_name:
                                return 0.0, 0.0, False
                            scratched = False
                            for inj in injury_list:
                                name = (inj.get('name') or '').lower()
                                pos = (inj.get('position') or '').upper()
                                status = inj.get('status') or ''
                                if status not in _INJURY_OUT_STATUSES and status not in _INJURY_DOUBTFUL_STATUSES:
                                    continue
                                if 'P' not in pos and 'PITCH' not in (inj.get('reason') or '').upper():
                                    continue
                                if sp_name.lower() in name or name in sp_name.lower():
                                    scratched = True
                                    break
                            if not scratched:
                                return 0.0, 0.0, False
                            # Elite -> replacement is largest delta.
                            if side_quality in ('elite',):
                                return 0.15, 1.5, True
                            if side_quality in ('above_avg',):
                                return 0.09, 1.0, True
                            if side_quality in ('average',):
                                return 0.05, 0.7, True
                            return 0.02, 0.5, True

                        _home_tier, _home_q = _mlb_pitcher_quality_tier(
                            _pitch.get('home_sp_era'),
                            _pitch.get('home_sp_xera'),
                            _pitch.get('home_sp_whip'),
                            _pitch.get('home_sp_kbb'),
                            _mlb_recent_pitcher_form(_pitch.get('home_sp_name')),
                        )
                        _away_tier, _away_q = _mlb_pitcher_quality_tier(
                            _pitch.get('away_sp_era'),
                            _pitch.get('away_sp_xera'),
                            _pitch.get('away_sp_whip'),
                            _pitch.get('away_sp_kbb'),
                            _mlb_recent_pitcher_form(_pitch.get('away_sp_name')),
                        )

                        # If home SP scratched, boost away win prob (and vice versa).
                        _away_boost, _away_total_bump, _home_sp_scratched = _apply_pitcher_scratch(_home_inj, _pitch.get('home_sp_name'), _home_tier)
                        _home_boost, _home_total_bump, _away_sp_scratched = _apply_pitcher_scratch(_away_inj, _pitch.get('away_sp_name'), _away_tier)
                        _away_adj += _away_boost
                        _home_adj += _home_boost
                        _total_adj += (_away_total_bump + _home_total_bump)

                        def _lineup_adjustments(injury_list):
                            t1 = t2 = 0
                            for inj in injury_list:
                                pos = (inj.get('position') or '').upper()
                                status = inj.get('status') or ''
                                if status not in _INJURY_OUT_STATUSES and status not in _INJURY_DOUBTFUL_STATUSES:
                                    continue
                                if pos in {'P', 'SP', 'RP', 'CP', 'CL'}:
                                    continue
                                tier = _mlb_lineup_tier(pos)
                                if tier == 1:
                                    t1 += 1
                                elif tier == 2:
                                    t2 += 1
                            boost = t1 * 0.025 + t2 * 0.012
                            if t1 >= 2:
                                boost += 0.02
                            return boost, t1, t2

                        _away_lineup_boost, _home_t1, _home_t2 = _lineup_adjustments(_home_inj)
                        _home_lineup_boost, _away_t1, _away_t2 = _lineup_adjustments(_away_inj)
                        _away_adj += _away_lineup_boost
                        _home_adj += _home_lineup_boost

                        def _bullpen_adjustments(injury_list, team_name):
                            key_relief = 0
                            for inj in injury_list:
                                status = inj.get('status') or ''
                                if status not in _INJURY_OUT_STATUSES and status not in _INJURY_DOUBTFUL_STATUSES:
                                    continue
                                pos = (inj.get('position') or '').upper()
                                if pos in {'RP', 'CP', 'CL'}:
                                    key_relief += 1
                            boost = min(0.03, key_relief * 0.012)
                            fat_boost, fat_total, _ = _mlb_bullpen_fatigue_boost(team_name, _gdate)
                            return boost + fat_boost, fat_total, key_relief

                        _away_bp_boost, _home_bp_total, _home_relief_out = _bullpen_adjustments(_home_inj, _ht)
                        _home_bp_boost, _away_bp_total, _away_relief_out = _bullpen_adjustments(_away_inj, _at)
                        _away_adj += _away_bp_boost
                        _home_adj += _home_bp_boost
                        _total_adj += (_home_bp_total + _away_bp_total)

                        _raw_delta = (_home_adj - _away_adj) * _inj_conf

                        # Rule #10D: scale adjustment when market already moved.
                        _market_scale = 1.0
                        if _home_mkt is not None:
                            _observed_move = abs(_home_mkt - _pre_blended)
                            _expected_move = max(0.001, abs(_raw_delta))
                            if _observed_move >= 0.7 * _expected_move:
                                _market_scale = 0.4
                        _adj_delta = _raw_delta * _market_scale
                        _blended = max(0.05, min(0.95, _blended + _adj_delta))
                        if game_dict.get('xgb_total') is not None:
                            game_dict['xgb_total'] = round(float(game_dict['xgb_total']) + _total_adj * _inj_conf * _market_scale, 2)

                        # Rule #1 + #3 + #4 + #6 + #8 decision layer.
                        _implied = _home_mkt if _blended >= 0.5 else _away_mkt
                        _model_pick_prob = _blended if _blended >= 0.5 else (1.0 - _blended)
                        _edge = (_model_pick_prob - _implied) if _implied is not None else 0.0
                        _is_favorite_pick = False
                        _pick_odds = _home_ml if _blended >= 0.5 else _away_ml
                        if _pick_odds is not None:
                            _is_favorite_pick = _pick_odds <= -170

                        _mvals = [_xgb_prob, _ts_prob, _ens_prob, _elo_base, _g2_prob]
                        _mvals = [v for v in _mvals if v is not None]
                        _low_conf_noise = False
                        if len(_mvals) >= 3:
                            _mvals_sorted = sorted(_mvals, reverse=True)
                            _low_conf_noise = abs(_mvals_sorted[0] - _mvals_sorted[2]) < _MLB_NOISE_MODEL_GAP

                        _bet_type = 'ML'
                        if _blended >= 0.60 and game_dict.get('xgb_spread') is not None and abs(float(game_dict['xgb_spread'])) >= 1.4:
                            _bet_type = 'Run Line'

                        _pass_reason = None
                        if _implied is not None and _edge < _MLB_EDGE_THRESHOLD:
                            _pass_reason = 'edge_below_threshold'
                        if _is_favorite_pick and _edge < _MLB_FAVORITE_EDGE_THRESHOLD:
                            _pass_reason = 'favorite_edge_too_small'
                        if _low_conf_noise:
                            _pass_reason = 'low_confidence_model_noise'

                        _tier = 'Tier 3'
                        _units = 0.0
                        if _pass_reason:
                            _bet_type = 'Pass'
                            _tier = 'No Bet'
                        elif _edge >= 0.08:
                            _tier = 'Tier 1'
                            _units = 1.0
                        elif _edge >= _MLB_EDGE_THRESHOLD:
                            _tier = 'Tier 2'
                            _units = 0.5
                        else:
                            _bet_type = 'Pass'
                            _tier = 'No Bet'

                        _conf = int(round(min(100.0, max(0.0, 50.0 + (_edge * 500.0) + (_inj_conf * 20.0) + (5.0 if _value_underdog else 0.0) - (8.0 if _low_conf_noise else 0.0)))))

                        _blended = max(0.05, min(0.95, _blended))
                        game_dict['elo_prob']       = round(_elo_base * 100, 1)
                        game_dict['xgb_prob']       = round(_xgb_prob * 100, 1)
                        game_dict['glicko2_prob']   = round(_g2_prob * 100, 1)
                        game_dict['trueskill_prob'] = round(_ts_prob * 100, 1)
                        game_dict['ensemble_prob']  = round(_blended * 100, 1)
                        game_dict['predicted_winner'] = _ht if _blended > 0.5 else _at
                        game_dict['model_win_pct'] = round(_model_pick_prob * 100.0, 1)
                        game_dict['implied_win_pct'] = round((_implied or 0.0) * 100.0, 1) if _implied is not None else None
                        game_dict['edge_pct'] = round(_edge * 100.0, 2)
                        game_dict['adjusted_edge_pct'] = round(_edge * 100.0, 2)
                        game_dict['bet_tier'] = _tier
                        game_dict['bet_units'] = _units
                        game_dict['bet_type'] = _bet_type
                        game_dict['confidence_score'] = _conf
                        game_dict['value_underdog'] = _value_underdog
                        game_dict['mlb_low_confidence'] = _low_conf_noise
                        game_dict['mlb_pass_reason'] = _pass_reason
                        game_dict['injury_confidence_factor'] = round(_inj_conf, 2)
                        game_dict['injury_market_scale'] = round(_market_scale, 2)
                        game_dict['injury_adjustment_home_pct'] = round((_home_adj * _inj_conf * _market_scale) * 100.0, 2)
                        game_dict['injury_adjustment_away_pct'] = round((_away_adj * _inj_conf * _market_scale) * 100.0, 2)
                        game_dict['mlb_pitcher_tiers'] = {'home': _home_tier, 'away': _away_tier}
                        game_dict['mlb_lineup_absences'] = {
                            'home_tier1': _home_t1, 'home_tier2': _home_t2,
                            'away_tier1': _away_t1, 'away_tier2': _away_t2,
                        }
                        game_dict['mlb_bullpen_flags'] = {
                            'home_key_relief_out': _home_relief_out,
                            'away_key_relief_out': _away_relief_out,
                        }

                        # Rule #7 tracking placeholders (for later close update job).
                        game_dict['opening_home_moneyline'] = _home_ml
                        game_dict['opening_away_moneyline'] = _away_ml
                        game_dict['closing_home_moneyline'] = _home_ml
                        game_dict['closing_away_moneyline'] = _away_ml
                        game_dict['opening_home_implied_prob'] = _home_mkt
                        game_dict['opening_away_implied_prob'] = _away_mkt
                        game_dict['closing_home_implied_prob'] = _home_mkt
                        game_dict['closing_away_implied_prob'] = _away_mkt
                        game_dict['clv_home'] = 0.0
                        game_dict['clv_away'] = 0.0
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
    
    if sport not in ('MLB', 'SOCCER'):
        try:
            _attach_engine_odds_to_predictions(sport, predictions, limit=40)
        except Exception as _eoe:
            logger.debug(f"Engine odds failed in get_upcoming_predictions for {sport}: {_eoe}")

    # Soccer: when the odds engine has no spread line
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
        _ml_limit = 5 if sport == 'MLB' else 20
        _cache_market_lines_for_predictions(sport, predictions, limit=_ml_limit)
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
    
    # H2H last-10 projection for "Our Total" / "Our Spread" (all sports)
    try:
        _attach_h2h_projection_to_predictions(sport, predictions, n=10)
    except Exception as _h2he:
        logger.debug(f"[h2h] attach failed for {sport}: {_h2he}")

    # NBA-only: replace H2H "Our Total"/"Our Spread" with an efficiency-based
    # projection (per-team ORtg/DRtg/Pace from ESPN box scores — the same math
    # the books use). Pre-computes every team in tonight's slate IN PARALLEL
    # with a 10s wall-clock budget so a slow ESPN response can never freeze
    # the page. Falls back to per-team last-3 scoring averages when box-score
    # data isn't usable.
    if sport == 'NBA':
        _nba_t0 = _time.time()
        try:
            from team_efficiency import (
                precompute_team_efficiencies,
                compute_efficiency_projection_from,
            )
            from weighted_total_predictor import (
                compute_team_avg_projection,
                prefetch_recent_scoreboards,
            )

            # 1) Warm scoreboard cache in parallel (≤2s typical)
            prefetch_recent_scoreboards(sport='NBA', days=14)

            # 2) Pre-compute efficiency for every unique team, in parallel,
            #    with a HARD 10s budget. Teams that don't finish → None →
            #    will fall back to per-team-avg in the prediction loop below.
            unique_teams = []
            seen = set()
            for pred in predictions:
                for t in (pred.get('home_team_id'), pred.get('away_team_id')):
                    if t and t not in seen:
                        seen.add(t)
                        unique_teams.append(t)

            eff_map = precompute_team_efficiencies(
                unique_teams, sport='NBA', n_games=5,
                max_lookback_days=14, total_budget_seconds=10.0, max_workers=16,
            )

            # 3) Attach to each prediction
            eff_hits = eff_misses = 0
            for pred in predictions:
                ht = pred.get('home_team_id')
                at = pred.get('away_team_id')
                if not (ht and at):
                    continue
                xs_total  = pred.get('xgb_total')
                xs_spread = pred.get('xgb_spread')
                home_eff = eff_map.get(ht)
                away_eff = eff_map.get(at)

                if home_eff and away_eff:
                    proj = compute_efficiency_projection_from(
                        home_eff, away_eff, sport='NBA',
                        xsharp_total=xs_total, xsharp_spread=xs_spread,
                    )
                    pred['our_total']    = _round_to_half(proj['projected_total'])
                    pred['our_spread']   = _round_to_half(proj['projected_spread'])
                    pred['our_home_pts'] = round(proj['home_pts']) if proj['home_pts'] is not None else None
                    pred['our_away_pts'] = round(proj['away_pts']) if proj['away_pts'] is not None else None
                    pred['our_home_eff'] = home_eff
                    pred['our_away_eff'] = away_eff
                    pred['our_pace']     = proj['avg_pace']
                    pred['our_method']   = 'efficiency'
                    eff_hits += 1
                    continue

                # Fallback: per-team last-3 scoring average
                try:
                    fb = compute_team_avg_projection(
                        home_team=ht, away_team=at, sport='NBA',
                        xsharp_total=xs_total, xsharp_spread=xs_spread,
                        n_games=3, max_lookback_days=14,
                    )
                except Exception as _fb_e:
                    fb = None
                    logger.debug(f"[team-avg fallback] {ht} vs {at}: {_fb_e}")
                if fb:
                    pred['our_total']       = fb['projected_total']
                    pred['our_spread']      = fb['projected_spread']
                    pred['our_home_avg']    = fb['home_avg']
                    pred['our_away_avg']    = fb['away_avg']
                    pred['our_total_games'] = fb['games_used']
                    pred['our_method']      = 'team-avg-fallback'
                    if xs_total is not None:
                        o, u = fb['total_record']
                        pred['total_trend_record']  = f"{o}-{u} Over"
                    if xs_spread is not None:
                        c, n = fb['spread_record']
                        pred['spread_trend_record'] = f"{c}-{n} ATS"
                eff_misses += 1

            logger.info(
                f"[NBA proj] efficiency={eff_hits} fallback={eff_misses} "
                f"total_time={_time.time() - _nba_t0:.2f}s"
            )
        except Exception as _nbae:
            logger.debug(f"[NBA projection] attach failed: {_nbae}")

    # ── EV calculations for NBA / WNBA / NHL / MLB / NFL upcoming games ─────
    if sport in ('NBA', 'WNBA', 'NHL', 'MLB', 'NFL'):
        import math as _math_ev
        _SPREAD_SIGMA = 12.0
        _TOTAL_SIGMA  = 20.0
        for _pred in predictions:
            if _pred.get('home_score') is not None:
                _pred.setdefault('ml_ev', None)
                _pred.setdefault('spread_ev', None)
                _pred.setdefault('total_ev', None)
                _pred.setdefault('best_ev_pick', None)
                continue

            # ── per-game local variables only ──
            _ens_pct   = _to_float_safe(_pred.get('ensemble_prob'))
            _model_p   = (_ens_pct / 100.0) if _ens_pct is not None else None
            _home_picked = (_model_p is not None and _model_p >= 0.5)
            _pick_p    = _model_p if _home_picked else ((1.0 - _model_p) if _model_p is not None else None)
            _home_ml   = _to_float_safe(_pred.get('home_moneyline'))
            _away_ml   = _to_float_safe(_pred.get('away_moneyline'))
            _pick_ml   = _home_ml if _home_picked else _away_ml
            _opp_ml    = _away_ml if _home_picked else _home_ml
            _ht        = _pred.get('home_team_id', '?')
            _at        = _pred.get('away_team_id', '?')

            # ── ML EV with de-vig ──
            _ml_ev = None
            if _pick_p is not None and _pick_ml is not None and _opp_ml is not None:
                _ml_ev, _devig, _impl, _vig = calculate_ev_devigged(_pick_p, _pick_ml, _opp_ml)
                logger.debug(
                    f"[EV] {_at}@{_ht} | model={round(_pick_p*100,1)}% "
                    f"implied={round((_impl or 0)*100,1)}% devig={round((_devig or 0)*100,1)}% "
                    f"vig={_vig}% odds={_pick_ml} EV={_ml_ev}%"
                )

            # ── Spread EV ──
            _our_sp  = _to_float_safe(_pred.get('our_spread'))
            _mkt_sp  = _to_float_safe(_pred.get('market_spread'))
            _spread_ev = None
            if _pick_p is not None and _mkt_sp is not None and _our_sp is not None:
                _sp_edge    = abs(_our_sp) - abs(_mkt_sp)
                _sp_cover_p = 0.5 * (1.0 + _math_ev.erf(_sp_edge / (_SPREAD_SIGMA * _math_ev.sqrt(2))))
                _spread_ev  = calculate_ev(_sp_cover_p, -110)

            # ── Total EV ──
            _our_tot = _to_float_safe(_pred.get('our_total'))
            _mkt_tot = _to_float_safe(_pred.get('market_total'))
            _total_ev = None
            if _our_tot is not None and _mkt_tot is not None:
                _tot_edge  = _our_tot - _mkt_tot
                _over_p    = 0.5 * (1.0 + _math_ev.erf(_tot_edge / (_TOTAL_SIGMA * _math_ev.sqrt(2))))
                _actual_p  = _over_p if _tot_edge >= 0 else (1.0 - _over_p)
                _total_ev  = calculate_ev(_actual_p, -110)

            _pred['ml_ev']     = _ml_ev
            _pred['spread_ev'] = _spread_ev
            _pred['total_ev']  = _total_ev

            _ev_map = {}
            if _ml_ev     is not None and _ml_ev     > 0: _ev_map['Spread'] = _ml_ev
            if _spread_ev is not None and _spread_ev > 0: _ev_map['Spread'] = _spread_ev
            if _total_ev  is not None and _total_ev  > 0: _ev_map['Total']  = _total_ev
            _pred['best_ev_pick'] = max(_ev_map, key=_ev_map.get) if _ev_map else None

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

            glicko2_correct   = (glicko2_prob   >= 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob >= 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       >= 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       >= 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       >= 0.5) == actual_home_win if ens_prob       is not None else None

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
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
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

            # Keep recent completed games even if some model probs are missing.
            # Tally logic already skips missing model fields per game.
            if all(prob is None for prob in [glicko2_prob, trueskill_prob, elo_prob, xgb_prob, meta_prob]):
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

            glicko2_correct   = (glicko2_prob   >= 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob >= 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       >= 0.5) == actual_home_win
            xgb_correct       = (xgb_prob       >= 0.5) == actual_home_win
            meta_correct      = (meta_prob      >= 0.5) == actual_home_win
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
                if elo_prob is None:
                    elo_prob = v2.get('home_prob')
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

            glicko2_correct   = (glicko2_prob   >= 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob >= 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       >= 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       >= 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       >= 0.5) == actual_home_win if ens_prob       is not None else None

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
        # H2H last-10 "Our Total" (used as the O/U line the model is compared to)
        try:
            _attach_h2h_projection_to_daily_results(sport, daily_results, n=10)
        except Exception as _h2he:
            logger.debug(f"[h2h] daily attach failed for {sport}: {_h2he}")
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
        live_cap = 10 if sport in ('NBA', 'SOCCER') else 5
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
                # Preserve XSharp model projections on the row so the UI can show
                # both "XSharp Total" (model projection) and "Our Total" (H2H line).
                g['xgb_total'] = xt
                g['xgb_spread'] = xs

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
                        if sport in ('NBA', 'SOCCER'):
                            gd_dt = parse_date(gd)
                            if gd_dt and abs((datetime.now() - gd_dt).days) > 3:
                                raise Exception(f"skip live fetch for older {sport} dates")
                        live_attempts += 1
                        live_line = _fetch_live_market_line(sport, gid, gd, h, a)
                        if live_line:
                            if ms is None:
                                ms = live_line.get('spread')
                            if mt is None:
                                mt = live_line.get('total')
                            if sport in ('NBA', 'SOCCER') and (ms is not None or mt is not None):
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

                    # ── MLB total grading: XSharp (+ park/rest/injury adj) vs Vegas total ──
                    inj_adj = _injury_total_adjustment(sport, h, a)
                    rest_adj = _rest_total_adjustment(sport, h, a, gd)
                    park_adj = _park_weather_total_adjustment(sport, h)
                    adj_xt = xt + inj_adj + rest_adj + park_adj if xt is not None else None
                    our_total_h2h = g.get('our_total')
                    g['xgb_total_adj'] = round(adj_xt, 2) if adj_xt is not None else None
                    g['total_adj_breakdown'] = {
                        'injury': round(inj_adj, 2),
                        'rest': round(rest_adj, 2),
                        'park': round(park_adj, 2),
                    }
                    # Grading line: Vegas → H2H → sport benchmark.
                    # Fallback totals are shown but not graded for ROI/record cards.
                    total_fallback_used = False
                    if mt is None:
                        if our_total_h2h is not None:
                            mt = round(float(our_total_h2h), 1)
                            g['market_total_reason'] = "H2H last-10 (fallback)"
                            total_fallback_used = True
                        elif _OU_BENCH.get(sport):
                            mt = float(_OU_BENCH[sport])
                            g['market_total_reason'] = "sport benchmark (fallback)"
                            total_fallback_used = True
                        elif adj_xt is not None:
                            mt = round(adj_xt, 1)
                            g['market_total_reason'] = "XSharp total (fallback)"
                            total_fallback_used = True
                    g['market_total'] = mt
                    if mt is None:
                        g['market_total_reason'] = g.get('market_total_reason') or "no sportsbook total line found"
                        g['total_pick_reason'] = g.get('total_pick_reason') or "no sportsbook total line"
                    elif adj_xt is None:
                        g['total_pick_reason'] = "model score unavailable"
                    else:
                        edge = adj_xt - mt
                        tp_disp = 'OVER' if edge >= 0 else 'UNDER'
                        if (not total_fallback_used) and abs(at - mt) >= 1e-9:
                            aou = 'OVER' if at > mt else 'UNDER'
                            tp_ok = (tp_disp == aou)
                            tt_gr += 1
                            if tp_ok:
                                tt_cor += 1
                        elif total_fallback_used:
                            g['total_pick_reason'] = "fallback total line (not graded)"
                        strong = False
                        if our_total_h2h is not None:
                            h2h_edge = our_total_h2h - mt
                            strong = (h2h_edge > 0 and edge > 0) or (h2h_edge < 0 and edge < 0)
                        g['strong_ou'] = strong
                        label = f"{tp_disp.title()} {mt:.1f}"
                        if strong and abs(edge) >= _ou_edge_threshold(sport):
                            label += " ★"
                        g['total_pick_label'] = label

                else:
                    # ── Non-MLB grading: Spread uses Vegas (unchanged).
                    #    O/U uses Vegas market_total vs XSharp xgb_total + post-hoc
                    #    adjustments (injury / rest / park / weather) + CLV threshold
                    #    + consensus-of-two (STRONG when H2H agrees).
                    inj_adj = _injury_total_adjustment(sport, h, a)
                    rest_adj = _rest_total_adjustment(sport, h, a, gd)
                    park_adj = _park_weather_total_adjustment(sport, h)
                    adj_xt = xt + inj_adj + rest_adj + park_adj if xt is not None else None
                    our_total_h2h = g.get('our_total')
                    g['xgb_total_adj'] = round(adj_xt, 2) if adj_xt is not None else None
                    g['total_adj_breakdown'] = {
                        'injury': round(inj_adj, 2),
                        'rest': round(rest_adj, 2),
                        'park': round(park_adj, 2),
                    }
                    # Grading line fallback for O/U: Vegas → H2H → sport benchmark.
                    # Fallback totals are shown but not graded for ROI/record cards.
                    total_fallback_used = False
                    if mt is None:
                        if our_total_h2h is not None:
                            mt = round(float(our_total_h2h), 1)
                            g['market_total_reason'] = "H2H last-10 (fallback)"
                            total_fallback_used = True
                        elif _OU_BENCH.get(sport):
                            mt = float(_OU_BENCH[sport])
                            g['market_total_reason'] = "sport benchmark (fallback)"
                            total_fallback_used = True
                        elif adj_xt is not None:
                            mt = round(adj_xt, 1)
                            g['market_total_reason'] = "XSharp total (fallback)"
                            total_fallback_used = True
                    g['market_spread'] = ms
                    g['market_total'] = mt
                    if ms is None:
                        g['market_spread_reason'] = "no sportsbook spread line found"
                    if mt is None:
                        g['market_total_reason'] = g.get('market_total_reason') or "no sportsbook total line found"

                    # Fallback spread: if Vegas spread missing, use pick-em (0)
                    # so every game with a model spread gets graded.
                    if ms is None and xs is not None:
                        ms = 0.0
                        g['market_spread_reason'] = "pick-em (fallback)"
                    g['market_spread'] = ms
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

                    if adj_xt is not None and mt is not None:
                        edge = adj_xt - mt
                        tp_disp = 'OVER' if edge >= 0 else 'UNDER'
                        if (not total_fallback_used) and abs(at - mt) >= 1e-9:
                            aou = 'OVER' if at > mt else 'UNDER'
                            tp_ok = (tp_disp == aou)
                            tt_gr += 1
                            if tp_ok:
                                tt_cor += 1
                        elif total_fallback_used:
                            g['total_pick_reason'] = "fallback total line (not graded)"
                        strong = False
                        if our_total_h2h is not None:
                            h2h_edge = our_total_h2h - mt
                            strong = (h2h_edge > 0 and edge > 0) or (h2h_edge < 0 and edge < 0)
                        g['strong_ou'] = strong and abs(edge) >= _ou_edge_threshold(sport)
                    elif xt is None:
                        g['total_pick_reason'] = "model score unavailable"
                    elif mt is None:
                        g['total_pick_reason'] = "no sportsbook total line"

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
                        label = g['total_line_display']
                        if g.get('strong_ou'):
                            label += " ★"
                        g['total_pick_label'] = label
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
            for model_name, correct_key, prob_key in model_configs:
                if game.get(prob_key) is None:
                    continue
                overall[model_name]['total'] += 1
                if game.get(correct_key):
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
    """Flat unit performance tracker: Win = +1u, Loss = -1u, Push = 0u.
    No sportsbook odds used. Every graded pick is 1 unit risked."""
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

            # Moneyline: flat +1u win, -1u loss
            ens_prob = g.get("ens_prob")
            if ens_prob is not None:
                pick_home = ens_prob >= 50
                home_win = home_score > away_score
                if home_score == away_score:
                    home_win = None
                entry = summary["moneyline"]
                if home_win is None:
                    entry["pushes"] += 1
                else:
                    entry["units_risked"] += 1
                    entry["graded"] += 1
                    correct = (pick_home and home_win) or ((not pick_home) and (not home_win))
                    if correct:
                        entry["wins"] += 1
                        entry["units_won"] += 1.0
                    else:
                        entry["losses"] += 1
                        entry["units_won"] -= 1.0

            # Spread: flat +1u win, -1u loss
            spread_pick = g.get("spread_pick")
            spread_correct = g.get("spread_correct")
            if spread_pick and spread_pick != "PUSH" and spread_correct is not None:
                entry = summary["spread"]
                entry["units_risked"] += 1
                entry["graded"] += 1
                if spread_correct is True:
                    entry["wins"] += 1
                    entry["units_won"] += 1.0
                else:
                    entry["losses"] += 1
                    entry["units_won"] -= 1.0
            elif spread_pick == "PUSH":
                summary["spread"]["pushes"] += 1

            # Total (O/U): flat +1u win, -1u loss
            total_pick = g.get("total_pick")
            total_correct = g.get("total_correct")
            if total_pick and total_pick != "PUSH" and total_correct is not None:
                entry = summary["total"]
                entry["units_risked"] += 1
                entry["graded"] += 1
                if total_correct is True:
                    entry["wins"] += 1
                    entry["units_won"] += 1.0
                else:
                    entry["losses"] += 1
                    entry["units_won"] -= 1.0
            elif total_pick == "PUSH":
                summary["total"]["pushes"] += 1
    for entry in summary.values():
        if entry["units_risked"] > 0:
            entry["roi_pct"] = round((entry["units_won"] / entry["units_risked"]) * 100, 2)
        else:
            if entry["graded"] == 0:
                entry["reason"] = "No graded picks in range."
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
    <link rel="icon" href="/static/pl-logo.svg" type="image/svg+xml">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {% if page_title is defined and page_title %}{% set _meta_title = page_title %}
    {% elif sport_info is defined %}{% set _meta_title = sport_info.name ~ ' Predictions | predictionlab.io' %}
    {% else %}{% set _meta_title = 'AI Sports Predictions & Game Forecasts | predictionlab.io' %}{% endif %}
    {% if page_description is defined and page_description %}{% set _meta_desc = page_description %}
    {% elif sport_info is defined %}{% set _meta_desc = sport_info.name ~ ' AI predictions, game forecasts, and model projections — predictionlab.io.' %}
    {% else %}{% set _meta_desc = 'Daily AI-powered sports predictions, game forecasts, model projections, and live performance tracking across major sports.' %}{% endif %}
    <title>{{ _meta_title }}</title>
    <meta name="description" content="{{ _meta_desc }}">
    <meta property="og:title" content="{{ _meta_title }}">
    <meta property="og:description" content="{{ _meta_desc }}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://predictionlab.io{{ request.path }}">
    <meta property="og:site_name" content="predictionlab.io">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="{{ _meta_title }}">
    <meta name="twitter:description" content="{{ _meta_desc }}">
    <link rel="canonical" href="https://predictionlab.io{{ request.path }}">
    <link rel="stylesheet" href="/static/css/picks-nav-overrides.css">
    <meta name="author" content="predictionlab.io">
    <meta name="publisher" content="GoodsandMore Inc.">
    <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&display=swap" onload="this.onload=null;this.rel='stylesheet'">
    <noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&display=swap"></noscript>
    <script>
    (function(){
        function initGA(){
            if (window.__gaLoaded) return;
            window.__gaLoaded = true;
            var s = document.createElement('script');
            s.async = true;
            s.src = 'https://www.googletagmanager.com/gtag/js?id=G-R4XM0WKTGG';
            document.head.appendChild(s);
            window.dataLayer = window.dataLayer || [];
            window.gtag = window.gtag || function(){window.dataLayer.push(arguments);};
            gtag('js', new Date());
            gtag('config', 'G-R4XM0WKTGG');
        }
        if ('requestIdleCallback' in window) {
            requestIdleCallback(initGA, { timeout: 2500 });
        } else {
            window.addEventListener('load', function(){ setTimeout(initGA, 800); }, { once: true });
        }
    })();
    </script>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "predictionlab.io",
      "url": "https://predictionlab.io",
      "sameAs": [
        "https://x.com/predictionlab_io",
        "https://instagram.com/predictionlab.io",
        "https://facebook.com/predictionlab.io",
        "https://predictionlab.io",
        "https://predictionlab.io"
      ]
    }
    </script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background:
                radial-gradient(1200px 600px at 70% -10%, rgba(251,191,36,0.10), transparent 60%),
                radial-gradient(900px 500px at -10% 20%, rgba(16,185,129,0.05), transparent 60%),
                #ffffff;
            color: #0f172a;
            min-height: 100vh;
        }
        .navbar {
            background: #ffffff !important;
            padding: 10px 0;
            border-bottom: 1px solid #E0E3EB;
            box-shadow: 0 2px 8px rgba(26,29,35,0.05);
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
            position: relative;
            gap: 12px;
            padding: 0 20px;
        }
        .logo{display:inline-flex;align-items:center;text-decoration:none;flex-shrink:0;order:2;border-radius:10px;}
        .logo img,.logo .pl-brand-logo__img{display:block;height:36px;width:auto;max-height:42px;max-width:min(220px,42vw);object-fit:contain;}
        a.pl-brand-logo.pl-brand-logo--holding{outline:2px solid rgba(0,82,155,0.35);outline-offset:2px;}
        .nav-cta{display:inline-flex;align-items:center;padding:9px 20px;border-radius:999px;background:linear-gradient(135deg,#6366f1 0%,#4f46e5 100%);color:#fff;font-size:0.84em;font-weight:700;text-decoration:none;letter-spacing:0.3px;white-space:nowrap;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 16px rgba(99,102,241,0.45),inset 0 1px 0 rgba(255,255,255,0.15);}
        .nav-cta:hover{transform:translateY(-1px);box-shadow:0 6px 22px rgba(99,102,241,0.6),inset 0 1px 0 rgba(255,255,255,0.15);}
        @media(max-width:480px){.nav-cta{padding:8px 14px;font-size:0.8em;}}
        .hamburger{display:flex;flex-direction:column;justify-content:center;gap:5px;cursor:pointer;padding:7px 9px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;flex-shrink:0;order:1;}
        .hamburger:hover{background:#f8fafc;}
        .hamburger span{width:20px;height:1.5px;background:#0f172a;border-radius:2px;transition:all .2s;}
        .tv-overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:1998;backdrop-filter:blur(2px);}
        .tv-overlay.open{display:block;}
        .tv-drawer{position:fixed;top:0;left:0;height:100%;width:min(280px,100vw);background:#fff;z-index:1999;transform:translateX(-100%);transition:transform .28s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;box-shadow:4px 0 32px rgba(15,23,42,0.18);}
        .tv-drawer.open{transform:translateX(0);}
        .tv-drawer-header{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid #e2e8f0;flex-shrink:0;}
        .tv-drawer-title{font-weight:800;font-size:1rem;color:#0f172a;}
        .tv-header-btns{display:flex;gap:8px;align-items:center;}
        .tv-back-btn{background:none;border:none;font-size:1.3rem;cursor:pointer;color:#475569;padding:4px 8px;border-radius:6px;line-height:1;}
        .tv-back-btn:hover{background:#f1f5f9;}
        .tv-close-btn{background:none;border:none;font-size:1.1rem;cursor:pointer;color:#475569;padding:4px 8px;border-radius:6px;line-height:1;}
        .tv-close-btn:hover{background:#f1f5f9;}
        .tv-panels{flex:1;overflow:hidden;position:relative;}
        .tv-panel{position:absolute;inset:0;overflow-y:auto;transition:transform .25s cubic-bezier(.4,0,.2,1);}
        .tv-panel.hidden-left{transform:translateX(-100%);}
        .tv-panel.hidden-right{transform:translateX(100%);}
        .tv-panel.visible{transform:translateX(0);}
        .tv-today-strip{padding:12px 16px;background:#f8fafc;border-bottom:1px solid #e2e8f0;}
        .tv-today-label{font-size:0.68em;font-weight:800;text-transform:uppercase;letter-spacing:0.6px;color:#64748b;margin-bottom:8px;}
        .tv-today-picks{display:flex;flex-direction:column;gap:6px;}
        .tv-today-pick{display:flex;align-items:center;justify-content:space-between;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:7px 10px;text-decoration:none;color:inherit;}
        .tv-today-pick:hover{border-color:#00529B;background:#f0f7ff;}
        .tv-pick-match{font-size:0.78em;font-weight:700;color:#0f172a;}
        .tv-pick-edge{font-size:0.72em;font-weight:800;color:#00C076;background:#f0fdf4;border-radius:6px;padding:2px 7px;}
        .tv-menu-list{padding:8px;}
        .tv-menu-btn{width:100%;display:flex;align-items:center;gap:12px;padding:11px 12px;border:none;background:none;cursor:pointer;border-radius:8px;text-align:left;transition:background .15s;}
        .tv-menu-btn:hover{background:#f1f5f9;}
        .tv-menu-label{flex:1;font-size:0.9rem;font-weight:700;color:#0f172a;}
        .tv-menu-arrow{color:#94a3b8;font-size:1rem;}
        .tv-sub-link{display:flex;align-items:center;gap:10px;padding:10px 14px;text-decoration:none;color:#1e293b;font-size:0.88rem;font-weight:600;border-radius:8px;margin:1px 8px;transition:background .12s;}
        .tv-sub-link:hover{background:#f1f5f9;color:#00529B;}
        .tv-sub-link.highlight{color:#00529B;font-weight:800;}
        .tv-sub-link .ext{font-size:0.7em;color:#94a3b8;margin-left:2px;}
        .nav-search-wrap{position:relative;flex:1;max-width:560px;width:100%;min-width:0;margin:0 20px;order:3;}
        .nav-search{display:flex;align-items:center;gap:8px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:999px;padding:7px 14px;cursor:text;transition:border-color .15s;}
        .nav-search:hover{border-color:#cbd5e1;}
        .nav-search svg{color:#94a3b8;flex-shrink:0;}
        .nav-search input{flex:1;min-width:0;border:none;outline:none;background:transparent;color:#0f172a;font-size:0.88em;cursor:text;}
        .nav-search input::placeholder{color:#94a3b8;}
        .nav-actions{display:flex;align-items:center;gap:8px;flex-shrink:0;margin-left:auto;order:4;}
        .acct-wrap{position:relative;display:flex;align-items:center;gap:8px;}
        .acct-btn{width:34px;height:34px;border-radius:50%;border:1.5px solid #e2e8f0;background:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;}
        .acct-btn:hover{border-color:#00529B;background:#f0f7ff;}
        .acct-menu{display:none;position:absolute;top:calc(100% + 8px);right:0;width:160px;background:#fff;border:1px solid #e2e8f0;border-radius:12px;box-shadow:0 8px 24px rgba(15,23,42,0.12);z-index:1100;padding:6px;}
        .acct-menu.open{display:block;}
        .acct-menu a{display:block;padding:9px 12px;font-size:0.85em;font-weight:600;color:#1e293b;text-decoration:none;border-radius:8px;}
        .acct-menu a:hover{background:#f1f5f9;color:#00529B;}
        .acct-menu-divider{height:1px;background:#f1f5f9;margin:4px 0;}
        .srch-overlay{display:none;position:fixed;inset:0;z-index:2100;background:rgba(15,23,42,0.4);backdrop-filter:blur(3px);}
        .srch-overlay.open{display:block;}
        .srch-box{position:absolute;top:70px;left:50%;transform:translateX(-50%);width:min(680px,96vw);background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(15,23,42,0.18);overflow:hidden;}
        .srch-input-row{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #f1f5f9;}
        .srch-input-row svg{color:#94a3b8;flex-shrink:0;}
        .srch-input-row input{flex:1;border:none;outline:none;font-size:1rem;color:#0f172a;}
        .srch-input-row input::placeholder{color:#94a3b8;}
        .srch-close{background:none;border:none;cursor:pointer;color:#94a3b8;font-size:1.1rem;padding:4px 6px;border-radius:6px;}
        .srch-close:hover{background:#f1f5f9;color:#0f172a;}
        .srch-filters{display:flex;gap:6px;padding:10px 14px;overflow-x:auto;border-bottom:1px solid #f1f5f9;scrollbar-width:none;}
        .srch-filters::-webkit-scrollbar{display:none;}
        .srch-filter{flex-shrink:0;padding:5px 12px;border-radius:999px;border:1px solid #e2e8f0;background:#fff;font-size:0.78em;font-weight:700;cursor:pointer;color:#475569;}
        .srch-filter.active,.srch-filter:hover{background:#0f172a;color:#fff;border-color:#0f172a;}
        .srch-items{max-height:340px;overflow-y:auto;padding:8px 0;}
        .srch-item{display:flex;align-items:center;gap:10px;padding:10px 16px;text-decoration:none;color:#1e293b;}
        .srch-item:hover{background:#f8fafc;}
        .srch-item-label{font-size:0.88em;font-weight:600;flex:1;}
        .srch-item-sport{font-size:0.72em;font-weight:700;color:#94a3b8;text-transform:uppercase;}
        .srch-empty{padding:24px 16px;text-align:center;font-size:0.85em;color:#94a3b8;}
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
        }
        .site-footer {
            background: #ffffff;
            border-top: 1px solid rgba(15,23,42,0.12);
            padding: 22px 24px 28px;
            color: #475569;
            font-size: 0.88em;
        }
        .footer-outer { max-width: 1200px; margin: 0 auto; }
        .footer-brand { margin-bottom: 18px; }
        .footer-columns-3 {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 28px 36px;
            align-items: start;
        }
        .footer-heading {
            font-size: 0.72em;
            text-transform: uppercase;
            letter-spacing: 0.55px;
            font-weight: 800;
            color: #0f172a;
            margin: 0 0 12px;
        }
        .footer-col-blk a {
            display: block;
            font-size: 0.88em;
            line-height: 1.85;
            color: #475569;
            text-decoration: none;
            font-weight: 500;
            padding: 2px 0;
        }
        .footer-col-blk a:hover { color: #00529B; text-decoration: underline; }
        .footer-bottom { margin-top: 22px; padding-top: 16px; border-top: 1px solid rgba(15,23,42,0.1); font-size: 0.82em; color: #475569; }
        .share-strip { max-width: 1200px; margin: 0 auto 10px; padding: 10px 16px; display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; background: rgba(244,247,249,0.7); border: 1px solid rgba(15,23,42,0.1); border-radius: 12px; }
        .share-strip-label { font-size: 0.82em; font-weight: 800; color: #0f172a; letter-spacing: 0.2px; }
        .share-icons { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .share-icon { width: 30px; height: 30px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; border: 1px solid rgba(15,23,42,0.14); background: #fff; }
        .share-icon img { width: 16px; height: 16px; display: block; }
        .share-icon img { width: 16px; height: 16px; display: block; }
        .share-icon .txt { display:none; font-size: 0.64rem; font-weight: 800; line-height: 1; color: #0f172a; letter-spacing: 0.1px; }
        .share-icon:hover { border-color: #00529B; background: rgba(0,82,155,0.08); }
        @media (max-width: 720px) {
            .footer-columns-3 { grid-template-columns: 1fr; gap: 22px; }
        }
        @media (max-width: 768px) { .nav-search-wrap{display:none;} .container{padding:20px 15px;} }
        /* Nav dropdown groups */
        .nav-group { position: relative; }
        .nav-group-title { color: #00529B; font-weight: 700; cursor: pointer; padding: 8px 10px; border-radius: 8px; display: block; font-size: 0.88em; }
        .nav-group-title:hover { background: rgba(0,82,155,0.08); }
        .nav-group-items { display: none; padding-left: 12px; }
        .nav-group.open .nav-group-items { display: flex; flex-direction: column; }
        .nav-group-items a { font-size: 0.84em; padding: 6px 10px !important; opacity: 0.9; }
        .nav-group-items a:hover { opacity: 1; color: #00529B; }
        {% block extra_styles %}{% endblock %}
    </style>
</head>
<body>
    <div class="navbar">
    <div class="navbar-content">
        <button type="button" class="hamburger" onclick="tvOpen()" aria-label="Open navigation menu" aria-expanded="false" id="navHamburger"><span></span><span></span><span></span></button>
        <a href="/" class="logo pl-brand-logo" aria-label="Prediction Lab home" title="Home — hold the logo to download full quality">
            <img class="pl-brand-logo__img" src="/static/PLLOGO.PNG" alt="Prediction Lab" width="200" height="60" decoding="async" fetchpriority="high" data-pl-logo-hq="/static/PLLOGO.PNG" draggable="false">
        </a>

        <div class="nav-search-wrap">
            <div class="nav-search" onclick="openSrch()">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <input type="text" placeholder="Search teams, leagues, props..." readonly onclick="openSrch()">
            </div>
        </div>

        <div class="nav-actions">
            <div class="acct-wrap">
                <button type="button" class="acct-btn" onclick="toggleAcctMenu(event)" aria-label="Account">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                </button>
                <div class="acct-menu" id="acctMenu">
                    {% if is_logged_in %}
                    <a href="/logout">Sign Out</a>
                    {% else %}
                    <a href="/login">Sign In</a>
                    <a href="/signup">Sign Up</a>
                    {% endif %}
                    <div class="acct-menu-divider"></div>
                    <a href="/faq">Help</a>
                </div>
                {% if not is_logged_in %}
                <a href="/signup" class="nav-cta">Get Started</a>
                {% endif %}
            </div>
        </div>
    </div>
</div>
    
    <div class="tv-overlay" id="tvOverlay" onclick="tvClose()"></div>
    <div class="tv-drawer" id="tvDrawer">
      <div class="tv-drawer-header">
        <div class="tv-header-btns"><button class="tv-back-btn" id="tvBackBtn" onclick="tvBack()" style="display:none">&#8249;</button><span class="tv-drawer-title" id="tvDrawerTitle">Menu</span></div>
        <button class="tv-close-btn" onclick="tvClose()">&#x2715;</button>
      </div>
      <div class="tv-panels">
        <div class="tv-panel visible" id="tvMain">
          {% if todays_picks is defined and todays_picks %}
          <div class="tv-today-strip">
            <div class="tv-today-label">&#9889; Today\'s Best Picks</div>
            <div class="tv-today-picks">
              {% for tp in todays_picks[:3] %}{% set _pct = tp.prob if tp.prob >= 50 else (100 - tp.prob)|round(1) %}
              <a class="tv-today-pick" href="/{{ tp.slug }}"><span class="tv-pick-match">{{ tp.sport }} &middot; {{ tp.away }} vs {{ tp.home }}</span><span class="tv-pick-edge">{{ _pct }}%</span></a>
              {% endfor %}
            </div>
          </div>
          {% endif %}
          <div class="tv-menu-list">
            <button class="tv-menu-btn" onclick="tvSub(\'picks\')"><span class="tv-menu-label">Picks &amp; Predictions</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvSub(\'props\')"><span class="tv-menu-label">Props &amp; Models</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvSub(\'results\')"><span class="tv-menu-label">Results &amp; Tracking</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvToggleMore(this)"><span class="tv-menu-label">More</span><span class="tv-more-arrow" style="color:#94a3b8;font-size:0.85rem;transition:transform .2s;">&#8250;</span></button>
            <div id="tvMoreItems" style="display:none;padding-left:8px;border-left:2px solid #f1f5f9;margin:2px 8px 2px 14px;">
              <button class="tv-menu-btn" style="padding:10px 10px;" onclick="tvSub(\'community\')"><span class="tv-menu-label" style="font-size:0.88rem;">Community</span><span class="tv-menu-arrow">&#8250;</span></button>
              <button class="tv-menu-btn" style="padding:10px 10px;" onclick="tvSub(\'company\')"><span class="tv-menu-label" style="font-size:0.88rem;">Company</span><span class="tv-menu-arrow">&#8250;</span></button>
            </div>
          </div>
        </div>
        <div class="tv-panel hidden-right" id="tvSub"></div>
      </div>
    </div>
    <div class="srch-overlay" id="srchOverlay" onclick="closeSrchOutside(event)">
      <div class="srch-box">
        <div class="srch-input-row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input type="text" id="srchInput" placeholder="Search teams, leagues, or matchups...">
          <button class="srch-close" onclick="closeSrch()">&#x2715;</button>
        </div>
        <div class="srch-filters">
          <button class="srch-filter active" data-s="all">All</button>
          <button class="srch-filter" data-s="nba">NBA</button>
          <button class="srch-filter" data-s="nfl">NFL</button>
          <button class="srch-filter" data-s="mlb">MLB</button>
          <button class="srch-filter" data-s="nhl">NHL</button>
          <button class="srch-filter" data-s="ncaab">NCAAB</button>
          <button class="srch-filter" data-s="ncaaf">NCAAF</button>
          <button class="srch-filter" data-s="wnba">WNBA</button>
          <button class="srch-filter" data-s="props">Props</button>
        </div>
        <div class="srch-items" id="srchItems"></div>
      </div>
    </div>
    <div class="container">
        {% block content %}{% endblock %}
    </div>
    <div class="share-strip">
        <span class="share-strip-label">Share on social media</span>
        <div class="share-icons">
            <a class="share-icon" href="https://x.com/intent/post?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on X"><img src="/static/icons/social/x.svg" alt="X"></a>
            <a class="share-icon" href="https://www.facebook.com/sharer/sharer.php?u={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Facebook"><img src="/static/icons/social/facebook.svg" alt="Facebook"></a>
            <a class="share-icon" href="{{ 'https://www.instagram.com/' if request.path == '/daily-report' else 'https://instagram.com/predictionlab' }}" target="_blank" rel="noopener" aria-label="Instagram"><img src="/static/icons/social/instagram.svg" alt="Instagram"></a>
            <a class="share-icon" href="{{ 'https://www.tiktok.com/upload?lang=en' if request.path == '/daily-report' else 'https://predictionlab.io' }}" target="_blank" rel="noopener" aria-label="TikTok"><img src="/static/icons/social/tiktok.svg" alt="TikTok"></a>
            <a class="share-icon" href="https://www.linkedin.com/sharing/share-offsite/?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on LinkedIn"><img src="/static/icons/social/linkedin.svg" alt="LinkedIn"></a>
            <a class="share-icon" href="https://www.reddit.com/submit?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Reddit"><img src="/static/icons/social/reddit.svg" alt="Reddit"></a>
            <a class="share-icon" href="https://www.tumblr.com/widgets/share/tool?canonicalUrl={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Tumblr"><img src="/static/icons/social/tumblr.svg" alt="Tumblr"></a>
            <a class="share-icon" href="https://api.whatsapp.com/send?text={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on WhatsApp"><img src="/static/icons/social/whatsapp.svg" alt="WhatsApp"></a>
            <a class="share-icon" href="https://telegram.me/share/url?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Telegram"><img src="/static/icons/social/telegram.svg" alt="Telegram"></a>
        </div>
    </div>
    <footer class="site-footer">
        <div class="footer-outer">
            <div class="footer-brand"><a href="/" class="logo" aria-label="Prediction Lab home">Prediction Lab</a></div>
            <div class="footer-columns-3">
                <div class="footer-col-blk">
                    <div class="footer-heading">Company</div>
                    <a href="/plans">Plans &amp; pricing</a>
                    <a href="/tutorial">Tutorial</a>
                    <a href="/contact">Contact us</a>
                    <a href="/privacy">Privacy</a>
                    <a href="/terms">Terms</a>
                    <a href="/responsible-gaming">Responsible gaming</a>
                </div>
                <div class="footer-col-blk">
                    <div class="footer-heading">Product</div>
                    <a href="/faq">FAQ</a>
                    <a href="/daily-report">Daily results report</a>
                    <a href="/search">Search</a>
                    <a href="/performance">Model performance</a>
                    <a href="/ai-sports-betting-picks-today">AI picks today</a>
                    <a href="/what-are-ai-sports-betting-picks">What are AI picks</a>
                    <a href="/our-model-vs-sportsbooks">Model vs sportsbooks</a>
                </div>
                <div class="footer-col-blk">
                    <div class="footer-heading">Social</div>
                    <a href="https://x.com/predictionlab_io" target="_blank" rel="noopener">X (Twitter)</a>
                    <a href="https://instagram.com/predictionlab.io" target="_blank" rel="noopener">Instagram</a>
                    <a href="https://facebook.com/predictionlab.io" target="_blank" rel="noopener">Facebook</a>
                    <a href="https://predictionlab.io" target="_blank" rel="noopener">TikTok</a>
                    <a href="https://predictionlab.io" target="_blank" rel="noopener">YouTube</a>
                </div>
            </div>
            <div class="footer-bottom">&copy; 2026 predictionlab.io. ALL RIGHTS RESERVED.</div>
        </div>
    </footer>
    
    <script>
var TV_MENUS={picks:{title:'Picks & Predictions',items:[{l:'NBA',h:'/nba-picks'},{l:'MLB',h:'/mlb-picks'},{l:'NHL',h:'/nhl-picks'},{l:'NFL',h:'/nfl-picks'}{% if soccer_enabled %},{l:'Soccer',h:'/soccer-picks'}{% endif %},{l:'NCAAB',h:'/ncaab-picks'},{l:'NCAAF',h:'/ncaaf-picks'},{l:'NCAAW',h:'/ncaaw-picks'},{l:'WNBA',h:'/wnba-picks'},{l:'View All →',h:'/',cls:'highlight'}]},props:{title:'Props & Models',items:[{l:'Player Props',h:'/player-props'},{l:'Model Performance',h:'/performance'},{l:'AI Picks Today',h:'/ai-sports-betting-picks-today'},{l:'Daily Results',h:'/daily-report'},{l:'Model vs Sportsbooks',h:'/our-model-vs-sportsbooks'},{l:'Tutorial',h:'/tutorial'}]},results:{title:'Results & Tracking',items:[{l:'Daily Results',h:'/daily-report'},{l:'Historical Performance',h:'/performance'},{l:'Download CSV',h:'/picks/export.csv'}]},community:{title:'Community',items:[{l:'X / Twitter',h:'https://x.com/predictionlab_io',ext:true},{l:'Instagram',h:'https://instagram.com/predictionlab.io',ext:true},{l:'Reddit',h:'https://reddit.com/r/sportsbetting',ext:true},{l:'Telegram',h:'https://t.me/predictionlab',ext:true}]},company:{title:'Company',items:[{l:'Plans & Pricing',h:'/plans'},{l:'FAQ',h:'/faq'},{l:'Contact',h:'/contact'},{l:'Privacy',h:'/privacy'},{l:'Terms',h:'/terms'}]}};
function tvOpen(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.add('open');if(d)d.classList.add('open');document.body.style.overflow='hidden';if(h)h.setAttribute('aria-expanded','true');}
function tvClose(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.remove('open');if(d)d.classList.remove('open');document.body.style.overflow='';if(h)h.setAttribute('aria-expanded','false');setTimeout(function(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';},280);}
function tvSub(key){var menu=TV_MENUS[key];if(!menu)return;var html='';menu.items.forEach(function(item){var ext=item.ext?' target="_blank" rel="noopener"':'';var cls='tv-sub-link'+(item.cls?' '+item.cls:'');var extIcon=item.ext?' <span class="ext">&#8599;</span>':'';html+='<a href="'+item.h+'" class="'+cls+'"'+ext+'>'+item.l+extIcon+'</a>';});document.getElementById('tvSub').innerHTML=html;document.getElementById('tvDrawerTitle').textContent=menu.title;document.getElementById('tvBackBtn').style.display='';document.getElementById('tvMain').className='tv-panel hidden-left';document.getElementById('tvSub').className='tv-panel visible';}
function tvBack(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';}
function tvToggleMore(btn){var el=document.getElementById('tvMoreItems');var open=el.style.display==='block';el.style.display=open?'none':'block';var arrow=btn.querySelector('.tv-more-arrow');if(arrow)arrow.style.transform=open?'':'rotate(90deg)';}
function toggleAcctMenu(e){e.stopPropagation();document.getElementById('acctMenu').classList.toggle('open');}
document.addEventListener('click',function(){var m=document.getElementById('acctMenu');if(m)m.classList.remove('open');});
var _srchFilter='all';
var _srchDefaults=[{l:'NBA Picks',h:'/nba-picks',s:'nba'},{l:'NFL Picks',h:'/nfl-picks',s:'nfl'},{l:'MLB Picks',h:'/mlb-picks',s:'mlb'},{l:'NHL Picks',h:'/nhl-picks',s:'nhl'},{l:'NCAAB Picks',h:'/ncaab-picks',s:'ncaab'},{l:'NCAAF Picks',h:'/ncaaf-picks',s:'ncaaf'},{l:'WNBA Picks',h:'/wnba-picks',s:'wnba'}{% if soccer_enabled %},{l:'Soccer Picks',h:'/soccer-picks',s:'all'}{% endif %},{l:'Player Props',h:'/player-props',s:'props'},{l:'Model Performance',h:'/performance',s:'props'},{l:'Daily Results',h:'/daily-report',s:'all'}];
function openSrch(){document.getElementById('srchOverlay').classList.add('open');document.body.style.overflow='hidden';setTimeout(function(){document.getElementById('srchInput').focus();},60);renderSrchItems('');}
function closeSrch(){document.getElementById('srchOverlay').classList.remove('open');document.body.style.overflow='';document.getElementById('srchInput').value='';}
function closeSrchOutside(e){if(e.target===document.getElementById('srchOverlay'))closeSrch();}
function renderSrchItems(q){var items=_srchDefaults.filter(function(i){return(_srchFilter==='all'||i.s===_srchFilter)&&(!q||i.l.toLowerCase().includes(q.toLowerCase()));});var el=document.getElementById('srchItems');if(!items.length){el.innerHTML='<div class="srch-empty">No results found</div>';return;}el.innerHTML=items.map(function(i){return'<a class="srch-item" href="'+i.h+'"><span class="srch-item-label">'+i.l+'</span><span class="srch-item-sport">'+i.s.toUpperCase()+'</span></a>';}).join('');}
document.addEventListener('DOMContentLoaded',function(){var inp=document.getElementById('srchInput');if(inp){inp.addEventListener('input',function(){renderSrchItems(this.value);});}document.querySelectorAll('.srch-filter').forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll('.srch-filter').forEach(function(b){b.classList.remove('active');});this.classList.add('active');_srchFilter=this.dataset.s;renderSrchItems(document.getElementById('srchInput').value);});});});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){tvClose();closeSrch();}});
    </script>
    <script src="/static/js/pl-header-logo.js" defer></script>
</body>
</html>
"""

# Static HTML footers for picks / results / utility pages (no Jinja).
_SEO_PICKS_PAGE_FOOTER = """
    <div class="seo-picks-footer" style="max-width:1200px;margin:40px auto 0;padding:26px 22px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:14px;color:#334155;line-height:1.75;font-size:0.95rem;">
        <h2 style="color:#fff;font-size:1.2rem;margin:0 0 12px;">How These AI Picks Are Generated</h2>
        <p style="margin-bottom:14px;">The picks on this page are generated using a data-driven sports betting model that analyzes market odds, historical performance, and team-level trends. Instead of relying on opinions or public sentiment, the model looks for pricing inefficiencies across sportsbooks to identify potential value.</p>
        <p style="margin-bottom:22px;">This approach is designed to stay consistent over time. While individual results can vary from day to day, the goal is long-term profitability based on disciplined, repeatable analysis.</p>
        <h2 style="color:#fff;font-size:1.2rem;margin:0 0 12px;">What to Expect From These Picks</h2>
        <p style="margin-bottom:14px;">These picks are not meant to guarantee wins on a daily basis. Sports betting naturally includes variance, and even strong edges can result in short-term losses. The focus is on maintaining a structured approach and tracking performance over a larger sample size.</p>
        <p style="margin-bottom:22px;">Users should approach these picks with proper bankroll management and realistic expectations.</p>
        <h2 style="color:#fff;font-size:1.2rem;margin:0 0 12px;">Full Transparency &amp; Results Tracking</h2>
        <p style="margin-bottom:14px;">Every pick published is tracked and recorded. There is no cherry-picking or selective reporting. You can review historical performance and verify results directly on our results pages.</p>
        <p style="margin-bottom:22px;">If you're looking to evaluate long-term performance, we recommend checking the latest results and trends across each sport.</p>
        <h2 style="color:#fff;font-size:1.2rem;margin:0 0 12px;">Learn More About the Model</h2>
        <p style="margin-bottom:10px;">If you're new to AI sports betting picks, you can learn more about how the system works and how it compares to traditional betting approaches:</p>
        <ul style="margin:0 0 14px 22px;">
            <li style="margin-bottom:6px;"><a href="/ai-sports-betting-picks-today" style="color:#fbbf24;font-weight:600;text-decoration:none;">AI picks overview</a></li>
            <li style="margin-bottom:6px;"><a href="/what-are-ai-sports-betting-picks" style="color:#fbbf24;font-weight:600;text-decoration:none;">What AI picks are</a></li>
            <li style="margin-bottom:6px;"><a href="/our-model-vs-sportsbooks" style="color:#fbbf24;font-weight:600;text-decoration:none;">Model vs sportsbooks</a></li>
        </ul>
        <p style="margin:0;">This helps provide a clearer understanding of the strategy behind the picks and how they are generated.</p>
    </div>
"""

_SEO_RESULTS_PAGE_FOOTER = """
    <div class="seo-results-footer" style="max-width:1200px;margin:40px auto 0;padding:26px 22px;background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;color:#334155;line-height:1.75;font-size:0.95rem;">
        <h2 style="color:#0f172a;font-size:1.2rem;margin:0 0 12px;">Understanding These Results</h2>
        <p style="margin-bottom:14px;">The results displayed on this page reflect all tracked picks generated by the model. Performance is measured using standard sports betting metrics such as win percentage, units gained or lost, and overall return on investment.</p>
        <p style="margin-bottom:22px;">These metrics provide a clearer picture of performance beyond simple win/loss records.</p>
        <h2 style="color:#0f172a;font-size:1.2rem;margin:0 0 12px;">Why Transparency Matters</h2>
        <p style="margin-bottom:14px;">All results are recorded without modification or filtering. This ensures that users can evaluate the model based on complete and accurate data rather than selective highlights.</p>
        <p style="margin-bottom:22px;">Transparency is a core part of the approach, allowing users to build trust through consistent tracking.</p>
        <h2 style="color:#0f172a;font-size:1.2rem;margin:0 0 12px;">Reviewing Picks Alongside Results</h2>
        <p style="margin-bottom:14px;">For the best understanding of performance, results should be viewed alongside the original picks. This gives context to how the model operates and how outcomes compare over time.</p>
        <p style="margin:0;">You can explore daily picks pages to see how selections were made and how they performed.</p>
    </div>
"""

_SEO_UTILITY_FAQ_FOOTER = """
    <div class="seo-utility-footer" style="max-width:900px;margin:36px auto 0;padding:20px 22px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:14px;color:#475569;line-height:1.75;font-size:0.93rem;">
        <p style="margin:0 0 10px;"><strong style="color:#0f172a;">More answers:</strong> See the full <a href="/faq" style="color:#00529B;font-weight:700;text-decoration:none;">Frequently Asked Questions</a>.</p>
        <p style="margin:0;">Bet responsibly: only risk what you can afford to lose. These tools support informed decisions—they do not replace judgment, discipline, or bankroll management.</p>
    </div>
"""

CONTACT_PAGE_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
        .contact-wrap{max-width:720px;margin:0 auto;padding:8px 0 40px;}
        .contact-card{background:#fff;border:1px solid #cbd5e1;border-radius:14px;padding:28px 26px;}
        .contact-card h1{font-size:1.75em;color:#0f172a;margin:0 0 16px;line-height:1.25;}
        .contact-card p{color:#334155;line-height:1.75;margin:0 0 14px;font-size:1.02em;}
        .contact-email{font-size:1.05em;font-weight:800;margin-top:18px;}
        .contact-email a{color:#00529B;text-decoration:none;}
        .contact-email a:hover{text-decoration:underline;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="contact-wrap">
        <div class="contact-card">
            <h1>Questions, Suggestions, or Technical Issues?</h1>
            <p>We want to make your experience using predictionlab.io the best it can be. If you need help, find a bug, or have a suggestion, we want to hear about it! We are always looking for ways to ensure our customers have the best edge possible.</p>
            <p class="contact-email">Email: <a href="mailto:{{ contact_email }}">{{ contact_email }}</a></p>
        </div>
    </div>
""")

RESPONSIBLE_GAMING_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
        .rg-wrap{max-width:800px;margin:0 auto;padding:20px 0 60px;}
        .rg-card{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:24px;margin-bottom:18px;}
        .rg-card h1{font-size:1.8em;margin-bottom:12px;}
        .rg-card h2{font-size:1.2em;margin:6px 0 12px;color:#fbbf24;}
        .rg-card p{color:#334155;line-height:1.7;margin-bottom:12px;}
        .rg-card a{color:#fbbf24;text-decoration:none;font-weight:600;}
        .rg-card a:hover{text-decoration:underline;}
        .rg-resource{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:16px;margin-bottom:12px;}
        .rg-resource h3{font-size:1em;margin-bottom:6px;color:#e2e8f0;}
        .rg-resource p{font-size:0.88em;margin-bottom:0;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="rg-wrap">
        <div class="rg-card">
            <h1>Responsible Gaming &amp; Resources</h1>
            <p>predictionlab.io provides data-driven sports predictions for informational purposes. We do not promote irresponsible gambling. If betting is becoming a concern, support resources are available below. Please bet responsibly and only wager what you can afford to lose.</p>
        </div>
        <div class="rg-card">
            <h2>Canada Support Resources</h2>
            <div class="rg-resource">
                <h3><a href="https://www.connexontario.ca/" target="_blank" rel="noopener">ConnexOntario</a></h3>
                <p>Free, confidential support for gambling, mental health, and addiction services in Ontario.</p>
            </div>
            <div class="rg-resource">
                <h3><a href="https://www.responsiblegambling.org/" target="_blank" rel="noopener">Responsible Gambling Council</a></h3>
                <p>Provides education and resources to promote responsible gambling in Canada.</p>
            </div>
        </div>
        <div class="rg-card">
            <h2>United States Support Resources</h2>
            <div class="rg-resource">
                <h3><a href="https://www.ncpgambling.org/" target="_blank" rel="noopener">National Council on Problem Gambling</a></h3>
                <p>24/7 confidential helpline and resources for individuals experiencing gambling problems. Call 1-800-522-4700.</p>
            </div>
            <div class="rg-resource">
                <h3><a href="https://www.gamblersanonymous.org/" target="_blank" rel="noopener">Gamblers Anonymous</a></h3>
                <p>Peer support organization for individuals looking to stop gambling.</p>
            </div>
        </div>
        <div class="rg-card">
            <p style="text-align:center;font-style:italic;color:#94a3b8;">If you or someone you know may have a gambling problem, reaching out for help is the first step.</p>
        </div>
""" + _SEO_UTILITY_FAQ_FOOTER + """
    </div>
""")

TUTORIAL_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
        .tutorial-wrap{max-width:900px;margin:0 auto;padding:20px 0 60px;}
        .tutorial-card{background:#fff;border:1px solid #cbd5e1;border-radius:14px;padding:24px;margin-bottom:18px;box-shadow:0 4px 18px rgba(15,23,42,0.06);}
        .tutorial-card h1{font-size:2em;margin-bottom:8px;color:#0f172a;}
        .tutorial-card h2{font-size:1.35em;margin:6px 0 8px;color:#0f172a;}
        .tutorial-card p{color:#334155;line-height:1.7;}
        .tutorial-card ul{margin:8px 0 0 20px;color:#334155;line-height:1.7;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="tutorial-wrap">
        <div class="tutorial-card">
            <h1>Tutorial</h1>
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
            <h2>📊 Model Confidence &amp; Pick Side</h2>
            <p>The models now display a confidence percentage and the team each model is picking.</p>
            <ul>
                <li><strong>Grinder2</strong> = Team rating model</li>
                <li><strong>Takedown</strong> = Matchup analysis model</li>
                <li><strong>Edge</strong> = Performance rating model</li>
                <li><strong>XSharp</strong> = Machine learning model</li>
                <li><strong>Sharp Consensus</strong> = Weighted blend of all models</li>
            </ul>
            <p>Each model card shows both the confidence % and the side it favors, so you can quickly see where model agreement is strongest.</p>
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
""" + _SEO_UTILITY_FAQ_FOOTER + """
    </div>
""")

# ============================================================================
# DAILY REPORT TEMPLATE (marketing / proof-of-performance)
# ============================================================================

DAILY_REPORT_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    body{background:#ffffff !important;color:#0f172a;}
    body::before{content:'';position:fixed;inset:0;background:transparent;z-index:0;}
    body>*{position:relative;z-index:1;}
    @media(max-width:768px){body{background-attachment:scroll !important;}}
    .rpt-wrap{max-width:760px;margin:0 auto;padding:10px 0 60px;}
    
    .rpt-header{text-align:center;margin-bottom:28px;}
    .rpt-header h1{font-size:1.8em;margin-bottom:6px;}
    .rpt-header .rpt-date{color:#fbbf24;font-size:1.15em;font-weight:700;}
    .rpt-header .rpt-sub{color:#334155;font-size:0.9em;margin-top:6px;}
    .rpt-sport-block{background:#ffffff;border:1px solid rgba(15,23,42,0.14);border-radius:14px;padding:20px;margin-bottom:16px;}
    .rpt-sport-title{font-size:1.1em;font-weight:800;color:#0f172a;margin-bottom:14px;text-align:center;}
    .rpt-sport-title span{color:#fbbf24;}
    .rpt-cat-label{font-size:0.72em;text-transform:uppercase;letter-spacing:0.5px;color:#94a3b8;text-align:center;margin:12px 0 6px;font-weight:600;}
    .rpt-cat-label:first-child{margin-top:0;}
    .rpt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;}
    .rpt-card{background:#f8fafc;border:1px solid rgba(15,23,42,0.1);border-radius:10px;padding:10px 6px;text-align:center;}
    .rpt-card.hl{border:2px solid #fbbf24;}
    .rpt-model{font-size:0.72em;opacity:0.85;margin-bottom:3px;}
    .rpt-acc{font-size:1.35em;font-weight:800;}
    .rpt-acc.g{color:#00C076;}.rpt-acc.y{color:#fbbf24;}.rpt-acc.r{color:#D93025;}.rpt-acc.x{color:#94a3b8;}
    .rpt-rec{font-size:0.78em;opacity:0.8;}
    .rpt-sou-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
    .rpt-total{text-align:center;font-size:0.9em;color:#334155;margin-bottom:18px;}
    .rpt-total strong{color:#0f172a;font-size:1.1em;}
    .rpt-actions{display:flex;gap:10px;justify-content:center;margin-top:28px;flex-wrap:wrap;}
    .rpt-btn{padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:700;font-size:0.88em;transition:all 0.2s;display:inline-flex;align-items:center;gap:7px;border:none;}
    .rpt-btn:hover{opacity:0.85;transform:translateY(-1px);}
    .rpt-btn-copy{background:#ffffff;color:#0f172a;border:1px solid rgba(15,23,42,0.25);cursor:pointer;}
    .rpt-btn-copy.copied{background:#00C076;border-color:#00C076;}
    .rpt-btn-cta{background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;}
    .rpt-share-row{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-bottom:12px;}
    .rpt-btn-group{display:inline-flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px;}
    .rpt-save-help{font-size:0.84em;color:#334155;text-align:center;margin:0 0 8px;}
    .rpt-cta-row{display:flex;justify-content:center;}
    .rpt-sharing{font-size:0.78em;color:#334155;text-align:center;margin-top:6px;}
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
        <div class="rpt-sport-block" id="sportCapture{{ loop.index0 }}" data-sport-name="{{ st.info.name }}">
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
            {% for st in sport_tallies %}
            <span class="rpt-btn-group">
                <a class="rpt-btn rpt-btn-copy" href="{{ st.share_image_src }}" download="daily-results.jpg">Download {{ st.info.name }}</a>
                <a class="rpt-btn rpt-btn-copy" href="{{ st.share_image_view_url }}" target="_blank" rel="noopener">Fullscreen {{ st.info.name }}</a>
            </span>
            {% endfor %}
        </div>
        <div class="rpt-cta-row" style="margin-top:12px;">
            <a class="rpt-btn rpt-btn-cta" href="/">View Today's Picks &rarr;</a>
        </div>
    </div>
""" + _SEO_RESULTS_PAGE_FOOTER + """
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
    .tab.active { background: #bfdbfe; color: #0f172a; border: 1px solid #93c5fd; }
    .value-picks-container { background: rgba(255, 255, 255, 0.05); border-radius: 15px; padding: 25px; }
    .pick-card { background: rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 20px; margin-bottom: 20px; border-left: 4px solid; }
    .pick-card.HIGH { border-left-color: #00C076; }
    .pick-card.MEDIUM { border-left-color: #fbbf24; }
    .pick-card.LOW { border-left-color: #3b82f6; }
    .matchup { font-size: 1.4em; font-weight: bold; margin-bottom: 10px; }
    .pick-team { color: #00C076; font-size: 1.2em; font-weight: bold; }
    .edge-badge { display: inline-block; padding: 6px 14px; border-radius: 6px; font-weight: bold; margin: 5px; }
    .edge-badge.HIGH { background: #00C076; color: white; }
    .edge-badge.MEDIUM { background: #fbbf24; color: black; }
    .edge-badge.LOW { background: #3b82f6; color: white; }
    .situational { display: flex; gap: 15px; flex-wrap: wrap; margin-top: 10px; font-size: 0.9em; opacity: 0.9; }
    .situational-item { background: rgba(255, 255, 255, 0.1); padding: 6px 12px; border-radius: 6px; }
    .warning { color: #D93025; font-weight: bold; }
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
""" + _SEO_PICKS_PAGE_FOOTER + """
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
        background: #bfdbfe;
        color: #0f172a;
        border: 1px solid #93c5fd;
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
        color: #00C076;
    }
    .med-conf {
        color: #fbbf24;
    }
    .low-conf {
        color: #D93025;
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
                    {% if date == today_date %} <span style="background: #00C076; color: white; padding: 4px 12px; border-radius: 4px; font-size: 0.8em; margin-left: 10px;">TODAY</span>{% endif %}
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
""" + _SEO_PICKS_PAGE_FOOTER + """
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
        background: #bfdbfe;
        color: #0f172a;
        border: 1px solid #93c5fd;
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
        color: #00C076;
        font-weight: bold;
    }
    .prob-low {
        color: #D93025;
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
""" + _SEO_RESULTS_PAGE_FOOTER + """
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
        background: #bfdbfe;
        color: #0f172a;
        border: 1px solid #93c5fd;
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
        background: #ffffff;
        border: 2px solid rgba(15,23,42,0.14);
        border-radius: 12px;
        padding: 25px;
        text-align: center;
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
        <a href="/" style="display: inline-block; padding: 10px 20px; background: #ffffff; border:1px solid rgba(15,23,42,0.18); border-radius: 8px; text-decoration: none; color: #0f172a; font-weight: 600;">← Back to Home</a>
    </div>
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} Results, Performance and Model Accuracy</h1>
    
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}" class="tab active">🎯 Results</a>
    </div>
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
""" + _SEO_RESULTS_PAGE_FOOTER + """
""")

# Daily Results Template (for NHL/NBA/NCAAB etc.)
DAILY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.2em; margin-bottom: 20px; text-align: center; padding:22px 18px; border:1px solid rgba(15,23,42,0.14); border-radius:12px; position:relative; overflow:hidden; z-index:1; background:#ffffff; color:#0f172a; }
    .section-tabs { display: flex; gap: 8px; margin-bottom: 20px; justify-content: center; flex-wrap: wrap; }
    .tab { padding: 10px 22px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: #ffffff; color: #0f172a; border:1px solid rgba(15,23,42,0.18); font-size: 0.9em; }
    .tab.active { background: #bfdbfe; color: #0f172a; border: 1px solid #93c5fd; }
    /* Type toggle */
    .type-toggle { display:flex; gap:6px; justify-content:center; margin-bottom:16px; }
    .toggle-btn { padding:8px 18px; border-radius:6px; border:2px solid rgba(15,23,42,0.2); background:#fff; color:#0f172a; font-weight:600; font-size:0.85em; cursor:pointer; transition:all 0.2s; }
    .toggle-btn.active { background:linear-gradient(135deg,#8b5cf6,#6d28d9); border-color:#8b5cf6; }
    .toggle-btn:hover { border-color:#8b5cf6; }
    .league-slider { display:flex; align-items:center; justify-content:center; gap:10px; margin:10px 0 16px; }
    .league-badges { display:flex; gap:8px; overflow-x:auto; padding:4px; max-width:860px; }
    .league-pill { background:#ffffff; border:2px solid rgba(15,23,42,0.15); border-radius:20px; padding:6px 14px; font-size:0.8em; font-weight:600; white-space:nowrap; cursor:pointer; transition:all 0.2s; color:#0f172a; text-decoration:none; display:inline-flex; align-items:center; }
    .league-pill.active { background:#fbbf24; border-color:#fbbf24; color:#0f172a; }
    .league-pill:hover { border-color:#fbbf24; }
    /* Date navigation */
    .date-nav { display:flex; align-items:center; justify-content:center; gap:12px; margin:16px 0; padding:12px 16px; background:#ffffff; border:1px solid rgba(15,23,42,0.12); border-radius:12px; }
    .nav-arrow { background:rgba(251,191,36,0.2); border:2px solid #fbbf24; color:#fbbf24; font-size:1.3em; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer; transition:all 0.2s; user-select:none; flex-shrink:0; }
    .nav-arrow:hover { background:rgba(251,191,36,0.4); transform:scale(1.1); }
    .date-bubbles { display:flex; gap:8px; overflow-x:auto; padding:4px; max-width:820px; }
    .date-bubble { background:#ffffff; border:2px solid rgba(15,23,42,0.2); border-radius:22px; padding:8px 15px; min-width:100px; text-align:center; cursor:pointer; transition:all 0.2s; white-space:nowrap; font-weight:500; font-size:0.84em; color:#0f172a; }
    .date-bubble:hover { border-color:#fbbf24; }
    .date-bubble.active { background:#fbbf24; border-color:#fbbf24; color:#0f172a; font-weight:700; }
    .date-bubble.today { border-color:#00C076; color:#00C076; }
    .date-bubble.active.today { background:#00C076; color:white; }
    /* Date sections */
    .date-section { display:none; background:#ffffff; border:1px solid rgba(15,23,42,0.12); border-radius:12px; padding:20px; margin-bottom:20px; }
    .date-section.visible { display:block; }
    .date-header { color:#0F172A; font-size:1.3em; font-weight:700; margin-bottom:14px; padding-bottom:10px; border-bottom:2px solid #E2E8F0; }
    .results-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(420px, 100%),1fr)); gap:16px; }
    @media(max-width:480px){ .results-grid { grid-template-columns:1fr; } .result-card { max-width:100%; } }
    .result-card {
        background:#ffffff;
        border:1px solid #E2E8F0;
        border-radius:12px;
        overflow:hidden;
        box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06);
        transition:border-color 0.2s, box-shadow 0.2s;
    }
    .result-card:hover { border-color:#cbd5e1; box-shadow:0 10px 28px rgba(15,23,42,0.12), 0 2px 6px rgba(15,23,42,0.08); }
    .result-status { padding:6px 14px; font-size:0.72em; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; color:#00C076; background:rgba(16,185,129,0.12); }
    .result-body { display:flex; padding:12px 14px; gap:12px; }
    .teams-section { flex:1; min-width:0; }
    .team-row { display:flex; align-items:center; justify-content:space-between; padding:6px 0; border-bottom:1px solid rgba(15,23,42,0.08); }
    .team-row:last-child { border-bottom:none; }
    .team-name { font-size:0.95em; white-space:normal; overflow:visible; text-overflow:clip; word-break:break-word; line-height:1.25; }
    .team-name.winner { font-weight:700; }
    .score-box { font-size:1.05em; font-weight:700; color:#fbbf24; margin-left:8px; }
    .model-panel { background:#ffffff; border:1px solid rgba(139,92,246,0.35); border-left:3px solid #8b5cf6; padding:10px 12px; min-width:170px; max-width:200px; display:flex; flex-direction:column; gap:4px; }
    .panel-title { font-size:0.66em; color:#0F172A; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; margin-bottom:2px; }
    .model-row { display:flex; justify-content:space-between; font-size:0.82em; padding:2px 0; }
    .model-lbl { opacity:0.85; }
    .model-right { display:flex; align-items:center; gap:6px; }
    .model-val { font-weight:600; }
    .ensemble-badge { background:rgba(16,185,129,0.2); border:1px solid #00C076; color:#00C076; padding:5px; border-radius:5px; text-align:center; font-weight:700; margin-top:4px; font-size:0.8em; }
    .result-footer { border-top:1px solid rgba(15,23,42,0.09); padding:8px 12px; display:flex; gap:14px; flex-wrap:wrap; background:#ffffff; }
    .sf-item { display:flex; flex-direction:column; gap:1px; }
    .sf-label { color:#94a3b8; font-size:0.72em; text-transform:uppercase; letter-spacing:0.3px; }
    .sf-val { font-weight:600; font-size:0.85em; color:#0f172a; }
    .pick-ok { color:#00C076; font-weight:700; }
    .pick-no { color:#D93025; font-weight:700; }
    /* Pick confidence grid (results cards) */
    .pick-conf-bar { border-top:1px solid rgba(15,23,42,0.08); padding:10px 12px 12px; background:#ffffff; }
    .pick-conf-title { font-size:0.68em; color:#0F172A; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; margin-bottom:8px; }
    .pick-conf-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:6px; align-items:stretch; }
    @media(max-width:520px){ .pick-conf-grid{ grid-template-columns:repeat(3,1fr); } }
    .pc-box { background:#ffffff; border:1px solid #E2E8F0; border-radius:8px; padding:6px 4px; text-align:center; display:flex; flex-direction:column; justify-content:space-between; align-items:center; gap:3px; min-width:0; min-height:86px; box-shadow:0 1px 4px rgba(15,23,42,0.05); }
    .pc-box.consensus { border-color:rgba(251,191,36,0.5); background:rgba(251,191,36,0.1); }
    .pc-box.correct { border-color:rgba(16,185,129,0.5); }
    .pc-box.wrong { border-color:rgba(239,68,68,0.45); }
    .pc-name { font-size:0.68em; font-weight:700; color:#0F172A; text-transform:uppercase; letter-spacing:0.3px; white-space:normal; overflow:visible; text-overflow:clip; max-width:100%; width:100%; line-height:1.15; word-break:break-word; min-height:28px; display:flex; align-items:center; justify-content:center; }
    .pc-val { font-size:0.95em; font-weight:800; color:#0f172a; }
    .pc-side { font-size:0.6em; font-weight:700; text-transform:uppercase; letter-spacing:0.3px; padding:2px 6px; border-radius:4px; display:inline-flex; align-items:center; justify-content:center; gap:3px; white-space:normal; overflow:visible; text-overflow:clip; max-width:100%; width:100%; box-sizing:border-box; text-align:center; line-height:1.15; word-break:break-word; min-height:24px; }
    .pc-side.home { color:#00C076; background:rgba(16,185,129,0.15); }
    .pc-side.away { color:#fbbf24; background:rgba(251,191,36,0.15); }
    .section-ml, .section-spread, .section-total { display:block; }
    .model-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin-bottom:16px; }
    @media(max-width:900px){ .model-grid { grid-template-columns:repeat(3,1fr); } }
    .model-card { background:#ffffff; border:1px solid #E2E8F0; border-radius:12px; padding:12px; text-align:center; box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06); }
    .model-card.highlight { border:2px solid #fbbf24; }
    .model-label { font-size:0.78em; opacity:0.8; margin-bottom:4px; }
    .model-acc { font-size:1.4em; font-weight:700; color:#00C076; }
    .model-rec { font-size:0.82em; opacity:0.85; }
    .daily-tally { background:#ffffff; border:1px solid #E2E8F0; border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06); }
    .daily-tally h2 { text-align:center; margin:0 0 12px 0; font-size:1.15em; color:#0F172A; font-weight:700; }
    .daily-tally-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
    .daily-tally-card { background:#ffffff; border:1px solid #E2E8F0; border-radius:10px; padding:10px; text-align:center; box-shadow:0 2px 12px rgba(15,23,42,0.06); }
    .daily-tally-card.highlight { border:2px solid #fbbf24; }
    .daily-model { font-size:0.78em; opacity:0.85; margin-bottom:4px; }
    .daily-acc { font-size:1.35em; font-weight:700; }
    .daily-rec { font-size:0.8em; opacity:0.8; }
    @media(max-width:640px){ .roi-grid{grid-template-columns:1fr !important;} }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} Results, Performance and Model Accuracy</h1>
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
                    <div class="daily-acc" style="color:{% if daily_tally.spread.accuracy >= 52 %}#00C076{% elif daily_tally.spread.accuracy >= 48 %}#fbbf24{% else %}#D93025{% endif %};">{{ daily_tally.spread.accuracy }}%</div>
                    <div class="daily-rec">{{ daily_tally.spread.correct }}-{{ daily_tally.spread.total - daily_tally.spread.correct }}{% if daily_tally.spread.pushes %}-{{ daily_tally.spread.pushes }}{% endif %}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no spread data</div>
                    {% endif %}
                </div>
                <div class="daily-tally-card" style="border:1px solid rgba(251,191,36,0.4);">
                    <div class="daily-model">🎲 Over/Under</div>
                    {% if daily_tally.total_ou.total > 0 %}
                    <div class="daily-acc" style="color:{% if daily_tally.total_ou.accuracy >= 52 %}#00C076{% elif daily_tally.total_ou.accuracy >= 48 %}#fbbf24{% else %}#D93025{% endif %};">{{ daily_tally.total_ou.accuracy }}%</div>
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
                    <div class="daily-acc" style="color:{% if weekly_tally.spread.accuracy >= 52 %}#00C076{% elif weekly_tally.spread.accuracy >= 48 %}#fbbf24{% else %}#D93025{% endif %};">{{ weekly_tally.spread.accuracy }}%</div>
                    <div class="daily-rec">{{ weekly_tally.spread.correct }}-{{ weekly_tally.spread.total - weekly_tally.spread.correct }}{% if weekly_tally.spread.pushes %}-{{ weekly_tally.spread.pushes }}{% endif %}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">no spread data</div>
                    {% endif %}
                </div>
                <div class="daily-tally-card" style="border:1px solid rgba(251,191,36,0.4);">
                    <div class="daily-model">🎲 Over/Under</div>
                    {% if weekly_tally.total_ou.total > 0 %}
                    <div class="daily-acc" style="color:{% if weekly_tally.total_ou.accuracy >= 52 %}#00C076{% elif weekly_tally.total_ou.accuracy >= 48 %}#fbbf24{% else %}#D93025{% endif %};">{{ weekly_tally.total_ou.accuracy }}%</div>
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
        <div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
            <h2 style="text-align:center;margin:0 0 16px 0;font-size:1.3em;color:#0f172a;">💰 Model Performance (Flat Unit Tracking)</h2>
            <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">
                {% for mkt, mkt_label in [('moneyline','Moneyline'),('spread','Spread'),('total','Total (O/U)')] %}
                {% set c = roi_cards[mkt] %}
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:10px;padding:14px;color:#0f172a;">
                    <div style="font-size:0.82em;text-align:center;opacity:0.9;margin-bottom:8px;font-weight:700;color:#334155;">{{ mkt_label }}</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;text-align:center;font-size:0.78em;color:#334155;">
                        <div><div style="opacity:0.8;">7 Days</div><div style="font-weight:700;color:{% if c.weekly.roi != '—' and '-' not in c.weekly.roi %}#00C076{% elif c.weekly.roi != '—' %}#D93025{% else %}#94a3b8{% endif %};">{{ c.weekly.roi }}</div><div style="opacity:0.85;font-size:0.9em;">{{ c.weekly.detail }}</div></div>
                        <div><div style="opacity:0.8;">Season</div><div style="font-weight:700;color:{% if c.total.roi != '—' and '-' not in c.total.roi %}#00C076{% elif c.total.roi != '—' %}#D93025{% else %}#94a3b8{% endif %};">{{ c.total.roi }}</div><div style="opacity:0.85;font-size:0.9em;">{{ c.total.detail }}</div></div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        <!-- ── Combined Stats Banner ── -->
        <div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
            <h2 style="text-align:center;margin:0 0 6px 0;font-size:1.5em;color:#0f172a;">🏆 Season Performance</h2>
            <div id="seasonInfoBox" style="display:none;background:#f8fafc;border:1px solid rgba(15,23,42,0.15);border-radius:8px;padding:12px 16px;margin:0 0 14px;font-size:0.78em;color:#334155;line-height:1.6;text-align:center;">
                Results are tracked from the start of the {{ sport_info.name }} season. All completed games with available model predictions are graded automatically. Game counts reflect actual games graded — some games may lack model data due to missing stats or early-season data gaps. Numbers grow daily as more games are played.
            </div>
            <div style="text-align:center;margin-bottom:14px;"><span onclick="var b=document.getElementById('seasonInfoBox');b.style.display=b.style.display==='none'?'block':'none';" style="cursor:pointer;font-size:0.75em;color:#475569;border:1px solid rgba(15,23,42,0.18);border-radius:12px;padding:3px 10px;background:#f8fafc;">ⓘ What do these numbers mean?</span></div>
            <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px;">
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎯 Moneyline (Consensus)</div>
                    <div style="font-size:2em;font-weight:bold;color:{% if ens.accuracy>=55 %}#00C076{% elif ens.accuracy>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ ens.accuracy }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ ens.correct }}-{{ ens.total - ens.correct }} ({{ ens.total }} games)</div>
                </div>
                {% if spread_total_stats is defined and spread_total_stats %}
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">📈 Spread (XSharp)</div>
                    <div style="font-size:2em;font-weight:bold;color:{% if spread_total_stats.spread_pct>=52 %}#00C076{% elif spread_total_stats.spread_pct>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ spread_total_stats.spread_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ spread_total_stats.spread_covered }}-{{ spread_total_stats.spread_graded - spread_total_stats.spread_covered }} ({{ spread_total_stats.spread_graded }} graded)</div>
                </div>
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎲 O/U (XSharp)</div>
                    <div style="font-size:2em;font-weight:bold;color:{% if spread_total_stats.total_pct>=52 %}#00C076{% elif spread_total_stats.total_pct>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ spread_total_stats.total_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ spread_total_stats.total_correct }}-{{ spread_total_stats.total_graded - spread_total_stats.total_correct }} ({{ spread_total_stats.total_graded }} graded)</div>
                </div>
                {% else %}
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;">📈 Spread</div><div style="font-size:1.5em;color:#94a3b8;">—</div></div>
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.8;">🎲 O/U</div><div style="font-size:1.5em;color:#94a3b8;">—</div></div>
                {% endif %}
            </div>
            <div style="border-top:1px solid rgba(15,23,42,0.12);padding-top:12px;"></div>
        </div>


        <!-- ── Model Records ── -->
        <h3 style="text-align:center;font-size:1.15em;margin:0 0 12px;color:#0f172a;">Moneyline Accuracy by Model</h3>
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
            <div class="date-header">📅 {{ date }}{% if date == today_date %} <span style="background:#00C076;color:white;padding:3px 10px;border-radius:4px;font-size:0.65em;margin-left:8px;">TODAY</span>{% endif %}</div>

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
                                <span class="team-name {% if away_wins %}winner{% endif %}">{{ game.away }}{% if game.away_moneyline is defined and game.away_moneyline is not none %} <span style="font-size:0.8em;color:{% if game.away_moneyline < 0 %}#00C076{% else %}#fbbf24{% endif %};font-weight:700;">{% if game.away_moneyline > 0 %}+{% endif %}{{ game.away_moneyline }}</span>{% endif %}</span>
                                <span class="score-box">{{ game.away_score }}</span>
                            </div>
                            <div class="team-row">
                                <span class="team-name {% if home_wins %}winner{% endif %}">{{ game.home }}{% if game.home_moneyline is defined and game.home_moneyline is not none %} <span style="font-size:0.8em;color:{% if game.home_moneyline < 0 %}#00C076{% else %}#fbbf24{% endif %};font-weight:700;">{% if game.home_moneyline > 0 %}+{% endif %}{{ game.home_moneyline }}</span>{% endif %}</span>
                                <span class="score-box">{{ game.home_score }}</span>
                            </div>
                        </div>
                    </div>
                    <div class="pick-conf-bar section-ml">
                        <div class="pick-conf-title">Pick Confidence</div>
                        <div class="pick-conf-grid">
                            {% for m in [
                                {'name': label_glicko2, 'prob': game.glicko2_prob, 'correct': game.glicko2_correct, 'key': 'glicko2'},
                                {'name': label_trueskill, 'prob': game.trueskill_prob, 'correct': game.trueskill_correct, 'key': 'trueskill'},
                                {'name': label_elo, 'prob': game.elo_prob, 'correct': game.elo_correct, 'key': 'elo'},
                                {'name': label_xgb, 'prob': game.xgb_prob, 'correct': game.xgb_correct, 'key': 'xgb'},
                                {'name': label_ensemble, 'prob': game.ens_prob, 'correct': game.ens_correct, 'key': 'consensus'}
                            ] %}
                            <div class="pc-box {% if m.key == 'consensus' %}consensus{% endif %} {% if m.correct == true %}correct{% elif m.correct == false %}wrong{% endif %}">
                                <div class="pc-name">{{ m.name }}</div>
                                {% if m.prob is not none %}
                                <div class="pc-val">{% if m.prob >= 50 %}{{ m.prob }}{% else %}{{ "%.1f"|format(100 - m.prob) }}{% endif %}%</div>
                                <div class="pc-side {% if m.prob >= 50 %}home{% else %}away{% endif %}" title="{% if m.prob >= 50 %}{{ game.home }}{% else %}{{ game.away }}{% endif %}">{% if m.prob >= 50 %}{{ (game.home|string).split()[-1] }}{% else %}{{ (game.away|string).split()[-1] }}{% endif %}{% if m.correct == true %} ✅{% elif m.correct == false %} ❌{% endif %}</div>
                                {% else %}
                                <div class="pc-val" style="color:#64748b;">—</div>
                                <div class="pc-side" style="color:#64748b;background:transparent;">—</div>
                                {% endif %}
                            </div>
                            {% endfor %}
                        </div>
                        {% if game.model_data_note %}<div style="font-size:0.7em;color:#94a3b8;margin-top:6px;text-align:center;">{{ game.model_data_note }}</div>{% endif %}
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
                            <span class="sf-label">XSharp Total</span>
                            <span class="sf-val sf-xgb">{% if game.xgb_total is not none %}{{ "%.1f"|format(game.xgb_total) }}{% if game.xgb_total_adj is defined and game.xgb_total_adj is not none and (game.xgb_total_adj - game.xgb_total)|abs > 0.05 %} <span style="color:#a78bfa;font-size:0.78em;">→ {{ "%.1f"|format(game.xgb_total_adj) }}</span>{% endif %}{% else %}—{% endif %}</span>
                        </div>
                        <div class="sf-item">
                            <span class="sf-label">H2H Last 10</span>
                            <span class="sf-val">{% if game.our_total is defined and game.our_total is not none %}{{ "%.1f"|format(game.our_total) }}{% else %}—{% endif %}</span>
                        </div>
                        {% if game.market_total is not none %}
                        <div class="sf-item">
                            <span class="sf-label">Market Total</span>
                            <span class="sf-val">{{ "%.1f"|format(game.market_total) }}</span>
                        </div>
                        {% endif %}
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
""" + _SEO_RESULTS_PAGE_FOOTER + """
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
        border:1px solid rgba(15,23,42,0.14);
        border-radius:12px;
        position:relative;
        overflow:hidden;
        background:#ffffff;
        color:#0f172a;
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
        background: #ffffff;
        border:1px solid rgba(15,23,42,0.18);
        color: #0f172a;
    }
    .tab.active {
        background: #bfdbfe;
        color: #0f172a;
        border: 1px solid #93c5fd;
    }
    .week-section {
        background: #ffffff;
        border:1px solid rgba(15,23,42,0.12);
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
        border-bottom: 2px solid rgba(15,23,42,0.15);
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
        background: #f8fafc;
        border:1px solid rgba(15,23,42,0.1);
        border-radius: 10px;
        padding: 15px;
        text-align: center;
    }
    .week-model-card.best {
        border: 2px solid #00C076;
        background: rgba(16, 185, 129, 0.1);
    }
    .daily-tally { background:#ffffff; border:1px solid #E2E8F0; border-radius:12px; padding:16px; margin-bottom:20px; box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06); }
    .daily-tally h2 { text-align:center; margin:0 0 12px 0; font-size:1.2em; color:#0F172A; font-weight:700; }
    .daily-tally-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
    .daily-tally-card { background:#ffffff; border:1px solid #E2E8F0; border-radius:10px; padding:10px; text-align:center; box-shadow:0 2px 12px rgba(15,23,42,0.06); }
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
        background: #f8fafc;
        padding: 10px;
        text-align: left;
        font-weight: bold;
        color: #fbbf24;
        border-bottom: 2px solid rgba(15,23,42,0.15);
    }
    .games-table td {
        padding: 8px 10px;
        border-bottom: 1px solid rgba(15,23,42,0.12);
        color:#0f172a;
    }
    .games-table tr:hover {
        background: rgba(15,23,42,0.04);
    }
    .score {
        font-weight: bold;
    }
    .winner {
        color: #00C076;
    }
    .loser {
        color: #D93025;
    }
    .prob-correct {
        color: #00C076;
        font-weight: bold;
    }
    .prob-wrong {
        color: #D93025;
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
        <div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:15px;padding:25px;margin-bottom:25px;">
            <h2 style="text-align:center;margin:0 0 20px 0;font-size:1.8em;color:#0f172a;">🏆 Overall Model Performance &mdash; {{ ens.total }} Games</h2>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px;">
                {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
                {% set m = overall_stats[m_key] %}
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:10px;padding:15px;text-align:center;{% if m_key == 'ensemble' %}border:2px solid #fbbf24; grid-column: span 4;{% endif %}">
                    <div style="font-size:0.9em;opacity:0.9;margin-bottom:4px;color:#334155;">{{ m_label }}</div>
                    <div style="font-size: {% if m_key == 'ensemble' %}2.8em{% else %}1.9em{% endif %}; font-weight: bold; color: {% if m.accuracy >= 55 %}#00C076{% elif m.accuracy >= 50 %}#fbbf24{% else %}#D93025{% endif %};">{{ m.accuracy }}%</div>
                    <div style="font-size:0.9em;opacity:0.9;color:#334155;">{{ m.correct }}-{{ m.total - m.correct }}</div>
                </div>
                {% endfor %}
            </div>
            <div style="border-top:1px solid rgba(15,23,42,0.12);padding-top:15px;"></div>
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
                        <td class="{% if game.glicko2_correct %}prob-correct{% elif game.glicko2_correct == false %}prob-wrong{% endif %}">{% if game.glicko2_correct is not none %}{% if game.glicko2_correct %}✅{% else %}❌{% endif %} {% if game.glicko2_prob >= 50 %}{{ game.glicko2_prob }}{% else %}{{ "%.1f"|format(100 - game.glicko2_prob) }}{% endif %}%{% else %}—{% endif %}</td>
                        <td class="{% if game.trueskill_correct %}prob-correct{% elif game.trueskill_correct == false %}prob-wrong{% endif %}">{% if game.trueskill_correct is not none %}{% if game.trueskill_correct %}✅{% else %}❌{% endif %} {% if game.trueskill_prob >= 50 %}{{ game.trueskill_prob }}{% else %}{{ "%.1f"|format(100 - game.trueskill_prob) }}{% endif %}%{% else %}—{% endif %}</td>
                        <td class="{% if game.elo_correct %}prob-correct{% elif game.elo_correct == false %}prob-wrong{% endif %}">{% if game.elo_correct is not none %}{% if game.elo_correct %}✅{% else %}❌{% endif %} {% if game.elo_prob >= 50 %}{{ game.elo_prob }}{% else %}{{ "%.1f"|format(100 - game.elo_prob) }}{% endif %}%{% else %}—{% endif %}</td>
                        <td class="{% if game.xgb_correct %}prob-correct{% elif game.xgb_correct == false %}prob-wrong{% endif %}">{% if game.xgb_correct is not none %}{% if game.xgb_correct %}✅{% else %}❌{% endif %} {% if game.xgb_prob >= 50 %}{{ game.xgb_prob }}{% else %}{{ "%.1f"|format(100 - game.xgb_prob) }}{% endif %}%{% else %}—{% endif %}</td>
                        <td class="{% if game.ens_correct %}prob-correct{% elif game.ens_correct == false %}prob-wrong{% endif %}">{% if game.ens_correct is not none %}{% if game.ens_correct %}✅{% else %}❌{% endif %} {% if game.ens_prob >= 50 %}{{ game.ens_prob }}{% else %}{{ "%.1f"|format(100 - game.ens_prob) }}{% endif %}%{% else %}—{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
    {% else %}
        <div class="no-data">No completed NFL games available yet.</div>
    {% endif %}
""" + _SEO_RESULTS_PAGE_FOOTER + """
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
    'WNBA':  ((5, 8), (10, 15)),
    'SOCCER':((8, 1), (6, 30)),
}

_SPORT_MIN_LIVE_DATES = {
    'WNBA': datetime(2026, 5, 8),
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
    min_live = _SPORT_MIN_LIVE_DATES.get(sport)
    if min_live and today < min_live:
        days_until = (min_live - today).days
        return ('Starting Soon' if days_until <= 60 else 'Offseason'), False
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
CONTACT_EMAIL = 'nmesghali@gmail.com'
_SOCIAL_ICONS = {
    'X': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M18.244 2H21l-6.588 7.53L22 22h-6.828l-5.35-6.16L4.59 22H2l7.03-8.04L2 2h6.93l4.84 5.6L18.244 2zm-1.2 18h1.9L7.04 4H5.02l12.02 16z"/></svg>',
    'Instagram': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M7.5 2C4.46 2 2 4.46 2 7.5v9C2 19.54 4.46 22 7.5 22h9c3.04 0 5.5-2.46 5.5-5.5v-9C22 4.46 19.54 2 16.5 2h-9zm9 2c1.93 0 3.5 1.57 3.5 3.5v9c0 1.93-1.57 3.5-3.5 3.5h-9C5.57 20 4 18.43 4 16.5v-9C4 5.57 5.57 4 7.5 4h9zm-4.5 3a5 5 0 100 10 5 5 0 000-10zm0 2a3 3 0 110 6 3 3 0 010-6zm5.25-.75a1.25 1.25 0 11-2.5 0 1.25 1.25 0 012.5 0z"/></svg>',
    'Facebook': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M22 12.07C22 6.49 17.52 2 11.94 2S1.88 6.49 1.88 12.07c0 4.99 3.66 9.12 8.44 9.88v-6.99H7.9v-2.89h2.42V9.41c0-2.4 1.43-3.72 3.62-3.72 1.05 0 2.15.19 2.15.19v2.36h-1.21c-1.2 0-1.58.74-1.58 1.5v1.8h2.69l-.43 2.89h-2.26v6.99c4.78-.76 8.44-4.89 8.44-9.88z"/></svg>',
    'TikTok': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M21 8.5c-1.9-.1-3.4-1.7-3.5-3.6V2h-3.2v13.1c0 1.4-1.1 2.5-2.5 2.5s-2.5-1.1-2.5-2.5 1.1-2.5 2.5-2.5c.3 0 .6.1.9.1V9.5c-.3 0-.6-.1-.9-.1-3.1 0-5.6 2.5-5.6 5.6s2.5 5.6 5.6 5.6 5.6-2.5 5.6-5.6V9.4c1 1 2.4 1.6 3.9 1.6V8.5z"/></svg>',
    'YouTube': '<svg role="img" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M23.5 6.2a3 3 0 00-2.1-2.1C19.5 3.5 12 3.5 12 3.5s-7.5 0-9.4.6a3 3 0 00-2.1 2.1A31.4 31.4 0 000 12a31.4 31.4 0 00.5 5.8 3 3 0 002.1 2.1c1.9.6 9.4.6 9.4.6s7.5 0 9.4-.6a3 3 0 002.1-2.1A31.4 31.4 0 0024 12a31.4 31.4 0 00-.5-5.8zM9.7 15.5V8.5l6.2 3.5-6.2 3.5z"/></svg>',
}
SOCIAL_LINKS = [
    {'label': 'X', 'url': 'https://x.com/predictionlab_io', 'icon': _SOCIAL_ICONS['X']},
    {'label': 'Instagram', 'url': 'https://instagram.com/predictionlab.io', 'icon': _SOCIAL_ICONS['Instagram']},
    {'label': 'Facebook', 'url': 'https://facebook.com/predictionlab.io', 'icon': _SOCIAL_ICONS['Facebook']},
    {'label': 'TikTok', 'url': 'https://predictionlab.io', 'icon': _SOCIAL_ICONS['TikTok']},
    {'label': 'YouTube', 'url': 'https://predictionlab.io', 'icon': _SOCIAL_ICONS['YouTube']},
]
GA_TRACKING_ID = _os.environ.get('GA_TRACKING_ID', 'G-R4XM0WKTGG')
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


def build_todays_top_picks():
    """Up to four ranked value picks for landing + /promo/top-picks-today."""
    todays_picks = []
    try:
        _tp_tz = ZoneInfo('America/New_York')
        _tp_today = datetime.now(_tp_tz).strftime('%Y-%m-%d')
    except Exception:
        _tp_today = datetime.now().strftime('%Y-%m-%d')
    try:
        _tp_conn = get_db_connection()
        _tp_rows = _tp_conn.execute('''
            SELECT p.game_id, p.sport, p.home_team_id, p.away_team_id, p.win_probability,
                   p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.meta_home_prob,
                   b.home_implied_prob, b.away_implied_prob
            FROM predictions p
            LEFT JOIN games g ON p.game_id = g.game_id AND g.sport = p.sport
            LEFT JOIN betting_odds b ON p.game_id = b.game_id
            WHERE date(p.game_date) = ?
              AND (g.home_score IS NULL OR g.game_id IS NULL)
              AND p.win_probability IS NOT NULL
              AND p.sport IN ('NHL', 'NBA', 'MLB', 'SOCCER')
            ORDER BY p.game_date ASC
            LIMIT 80
        ''', (_tp_today,)).fetchall()
        _tp_conn.close()
        _candidates = []
        for _tp in _tp_rows:
            _ens_home = float(_tp['win_probability'])
            _home = _tp['home_team_id']
            _away = _tp['away_team_id']
            _home_picked = _ens_home >= 0.5
            _pick_prob = _ens_home if _home_picked else (1.0 - _ens_home)
            _pick = _home if _home_picked else _away

            _model_vals = []
            for _k in ('elo_home_prob', 'xgboost_home_prob', 'logistic_home_prob', 'meta_home_prob', 'win_probability'):
                _v = _tp[_k]
                if _v is None:
                    continue
                try:
                    _model_vals.append(float(_v))
                except Exception:
                    continue
            _agreement_bonus = 0.0
            if len(_model_vals) >= 2:
                _aligned = [v if _home_picked else (1.0 - v) for v in _model_vals]
                _spread = max(_aligned) - min(_aligned)
                _agreement_bonus = max(0.0, 0.18 - _spread) * 120.0

            _implied = _tp['home_implied_prob'] if _home_picked else _tp['away_implied_prob']
            _edge_bonus = 0.0
            if _implied is not None:
                try:
                    _edge_bonus = (_pick_prob - float(_implied)) * 160.0
                except Exception:
                    _edge_bonus = 0.0

            _conf_bonus = (_pick_prob - 0.5) * 55.0
            _heavy_penalty = max(0.0, _pick_prob - 0.77) * 130.0
            _quality_score = _conf_bonus + _edge_bonus + _agreement_bonus - _heavy_penalty

            _candidates.append({
                'game_id': _tp['game_id'],
                'away': _away,
                'home': _home,
                'pick': _pick,
                'prob': round(_pick_prob * 100, 1),
                'sport': _tp['sport'],
                'slug': SPORT_SEO_SLUGS.get(_tp['sport'], ''),
                'quality_score': _quality_score,
                'fallback_score': abs(_ens_home - 0.5),
            })

        _seen_game_ids = set()
        _scored = sorted(_candidates, key=lambda x: x['quality_score'], reverse=True)
        for _row in _scored:
            _gid = _row.get('game_id') or f"{_row['sport']}::{_row['away']}::{_row['home']}"
            if _gid in _seen_game_ids:
                continue
            _seen_game_ids.add(_gid)
            todays_picks.append({
                'away': _row['away'], 'home': _row['home'],
                'pick': _row['pick'], 'prob': _row['prob'],
                'sport': _row['sport'], 'slug': _row['slug'],
            })
            if len(todays_picks) >= 4:
                break

        if len(todays_picks) < 4:
            _picked_keys = {f"{p['sport']}::{p['away']}::{p['home']}" for p in todays_picks}
            _fallback = sorted(_candidates, key=lambda x: x['fallback_score'], reverse=True)
            for _row in _fallback:
                _key = f"{_row['sport']}::{_row['away']}::{_row['home']}"
                if _key in _picked_keys:
                    continue
                _picked_keys.add(_key)
                todays_picks.append({
                    'away': _row['away'], 'home': _row['home'],
                    'pick': _row['pick'], 'prob': _row['prob'],
                    'sport': _row['sport'], 'slug': _row['slug'],
                })
                if len(todays_picks) >= 4:
                    break
    except Exception as _tp_err:
        logger.debug(f"Today's Top Picks DB query failed: {_tp_err}")
    return todays_picks


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
    seo_archive_links = []
    for _sport_key in ['NHL', 'NBA', 'MLB', 'SOCCER']:
        if _sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        _slug = SPORT_SEO_SLUGS.get(_sport_key)
        if not _slug:
            continue
        for _days_back in range(1, 4):
            _d = today - timedelta(days=_days_back)
            _m_name = _MONTH_NAMES.get(_d.month, 'january')
            seo_archive_links.append({
                'url': f"/{_slug}-{_m_name}-{_d.day}-{_d.year}",
                'label': f"{_sport_key} picks {_d.strftime('%b')} {_d.day}, {_d.year}",
            })

    _landing_share_url = 'https://predictionlab.io/'
    _landing_share_title = 'predictionlab.io Performance Stats'
    _landing_share_body = (
        f"{_landing_share_title}\n\n"
        "NBA Totals (2025/2026): 704-500 (+204u)\n"
        "NBA Spreads: 822-395 (+427u)\n"
        "NHL Spreads: 124-65 (+59u)\n"
        "NHL Totals (7 days): 8-1 (+7u)\n\n"
        "Our models are continuously evaluated across seasons to detect market inefficiencies and pricing edges.\n\n"
        f"{_landing_share_url}"
    )
    _landing_share_tweet = (
        "predictionlab.io Performance Stats — NBA Totals 704-500 (+204u), NBA Spreads +427u, "
        "NHL Spreads +59u, NHL Totals 8-1 (+7u). Tracked AI picks & results: "
        + _landing_share_url
    )

    todays_picks = build_todays_top_picks()

    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <link rel="icon" href="/static/pl-logo.svg" type="image/svg+xml">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Sports Predictions & Game Forecasts | predictionlab.io</title>
    <meta name="description" content="Daily AI-powered sports predictions, game forecasts, model projections, and live performance tracking across major sports.">
    <meta property="og:title" content="AI Sports Predictions & Game Forecasts | predictionlab.io">
    <meta property="og:description" content="Daily AI-powered sports predictions, game forecasts, model projections, and live performance tracking across major sports.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://predictionlab.io/">
    <meta property="og:site_name" content="predictionlab.io">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="AI Sports Predictions & Game Forecasts | predictionlab.io">
    <meta name="twitter:description" content="Daily AI-powered sports predictions, game forecasts, model projections, and live performance tracking across major sports.">
    <link rel="canonical" href="https://predictionlab.io{{ request.path }}">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="preload" as="style" href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=Bebas+Neue&display=swap" onload="this.onload=null;this.rel='stylesheet'">
    <noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=Bebas+Neue&display=swap"></noscript>
    {% if ga_tracking_id %}
    <script>
      (function(){
        function initGA(){
          if (window.__gaLoaded) return;
          window.__gaLoaded = true;
          var s = document.createElement('script');
          s.async = true;
          s.src = 'https://www.googletagmanager.com/gtag/js?id={{ ga_tracking_id }}';
          document.head.appendChild(s);
          window.dataLayer = window.dataLayer || [];
          window.gtag = window.gtag || function(){window.dataLayer.push(arguments);};
          gtag('js', new Date());
          gtag('config', '{{ ga_tracking_id }}');
        }
        if ('requestIdleCallback' in window) {
          requestIdleCallback(initGA, { timeout: 2500 });
        } else {
          window.addEventListener('load', function(){ setTimeout(initGA, 800); }, { once: true });
        }
      })();
    </script>
    {% endif %}
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "predictionlab.io",
      "url": "https://predictionlab.io",
      "description": "Daily AI-powered sports predictions, game forecasts, and model projections across major sports.",
      "email": "nmesghali@gmail.com",
      "telephone": "+1-519-992-8484",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "980 Lake Trail Drive",
        "addressLocality": "Windsor",
        "addressRegion": "Ontario",
        "postalCode": "N9G 2R8",
        "addressCountry": "CA"
      },
      "parentOrganization": {
        "@type": "Corporation",
        "name": "GoodsandMore Inc."
      },
      "sameAs": [
        "https://x.com/predictionlab_io",
        "https://instagram.com/predictionlab.io",
        "https://facebook.com/predictionlab.io",
        "https://predictionlab.io",
        "https://predictionlab.io"
      ]
    }
    </script>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"WebSite","name":"predictionlab.io","url":"https://predictionlab.io","potentialAction":{"@type":"SearchAction","target":"https://predictionlab.io/search?query={search_term_string}","query-input":"required name=search_term_string"}}
    </script>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"LocalBusiness","name":"predictionlab.io","url":"https://predictionlab.io","email":"nmesghali@gmail.com","telephone":"+1-519-992-8484","parentOrganization":{"@type":"Corporation","name":"GoodsandMore Inc."},"address":{"@type":"PostalAddress","streetAddress":"980 Lake Trail Drive","addressLocality":"Windsor","addressRegion":"Ontario","postalCode":"N9G 2R8","addressCountry":"CA"}}
    </script>
    <!-- FAQPage schema lives on /faq now (dedicated page). -->

    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Prediction Lab Premium","description":"AI-powered sports predictions with spreads, totals, and score projections across major sports.","brand":{"@type":"Brand","name":"predictionlab.io"},"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.7","bestRating":"5","ratingCount":"48"},"review":{"@type":"Review","author":{"@type":"Person","name":"predictionlab.io user"},"reviewRating":{"@type":"Rating","ratingValue":"5","bestRating":"5"},"reviewBody":"Strong model transparency and useful projections across spreads and totals."},"offers":[{"@type":"Offer","price":"19.99","priceCurrency":"USD","availability":"https://schema.org/InStock","priceValidUntil":"2027-12-31","name":"Monthly","url":"https://predictionlab.io/plans","hasMerchantReturnPolicy":{"@type":"MerchantReturnPolicy","applicableCountry":"US","returnPolicyCategory":"https://schema.org/MerchantReturnNotPermitted"},"shippingDetails":{"@type":"OfferShippingDetails","shippingRate":{"@type":"MonetaryAmount","value":"0","currency":"USD"},"shippingDestination":{"@type":"DefinedRegion","addressCountry":"US"},"deliveryTime":{"@type":"ShippingDeliveryTime","handlingTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"},"transitTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"}}}},{"@type":"Offer","price":"149.99","priceCurrency":"USD","availability":"https://schema.org/InStock","priceValidUntil":"2027-12-31","name":"Yearly","url":"https://predictionlab.io/plans","hasMerchantReturnPolicy":{"@type":"MerchantReturnPolicy","applicableCountry":"US","returnPolicyCategory":"https://schema.org/MerchantReturnNotPermitted"},"shippingDetails":{"@type":"OfferShippingDetails","shippingRate":{"@type":"MonetaryAmount","value":"0","currency":"USD"},"shippingDestination":{"@type":"DefinedRegion","addressCountry":"US"},"deliveryTime":{"@type":"ShippingDeliveryTime","handlingTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"},"transitTime":{"@type":"QuantitativeValue","minValue":"0","maxValue":"0","unitCode":"d"}}}}]}
    </script>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        :root{
            --gold:#fbbf24;--gold2:#f59e0b;
            --green:#00C076;--red:#D93025;
            --bg:#ffffff;--surface:#F4F7F9;
            --border:#E0E4E8;
            --text:#1A1D23;
            --link:#00529B;
        }
        body{
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            background:#ffffff;
            color:var(--text);
            min-height:100vh;
            padding-bottom:58px;
            overflow-x:hidden;
            position:relative;
        }
        body::before{
            content:'';
            position:fixed;
            inset:0;
            background:transparent;
            z-index:0;
        }
        body > *{position:relative;z-index:1;}
/* ── Navbar ── */
.navbar {
    background: #ffffff !important;
    padding: 10px 0;
    border-bottom: 1px solid #E0E3EB;
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
    padding: 0 20px;
    gap: 20px;
}

.logo {
    order: 1; /* Logo on far left */
}

.nav-search-wrap {
    order: 2; /* Search in middle */
    flex: 1;
    max-width: 600px;
    display: flex;
    justify-content: center;
}

.nav-actions {
    order: 3; /* Buttons on far right */
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
}

.nav-search {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    background: #f0f3fa;
    border: 1px solid #e0e3eb;
    border-radius: 999px;
    padding: 8px 16px;
    cursor: text;
}

.nav-search svg {
    color: #131722;
}

.nav-search input {
    border: none;
    outline: none;
    background: transparent;
    color: #131722;
    width: 100%;
}

.nav-actions {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
}

.acct-wrap {
    position: relative;
}

.acct-btn {
    width: 34px;
    height: 34px;
    border-radius: 50%;
    border: 1px solid #e0e3eb;
    background: #fff;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #131722;
}

.acct-menu {
    display: none;
    position: absolute;
    top: calc(100% + 8px);
    right: 0;
    width: 160px;
    background: #fff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(15,23,42,0.12);
    z-index: 1100;
    padding: 6px;
}

.acct-menu.open {
    display: block;
}

.acct-menu a {
    display: block;
    padding: 9px 12px;
    font-size: 0.85em;
    font-weight: 600;
    color: #1e293b;
    text-decoration: none;
    border-radius: 8px;
}

.acct-menu-divider {
    height: 1px;
    background: #f1f5f9;
    margin: 4px 0;
}
        .nav-cta{display:inline-flex;align-items:center;padding:9px 22px;border-radius:999px;background:linear-gradient(135deg,#6366f1 0%,#4f46e5 100%);color:#fff;font-size:0.84em;font-weight:700;text-decoration:none;letter-spacing:0.3px;white-space:nowrap;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 16px rgba(99,102,241,0.5),inset 0 1px 0 rgba(255,255,255,0.15);}
        .nav-cta:hover{transform:translateY(-1px);box-shadow:0 6px 22px rgba(99,102,241,0.65),inset 0 1px 0 rgba(255,255,255,0.15);}
        @media(max-width:480px){.nav-cta{padding:8px 14px;font-size:0.8em;}}
        .srch-overlay{display:none;position:fixed;inset:0;z-index:2100;background:rgba(15,23,42,0.4);backdrop-filter:blur(3px);}
        .srch-overlay.open{display:block;}
        .srch-box{position:absolute;top:70px;left:50%;transform:translateX(-50%);width:min(680px,96vw);background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(15,23,42,0.18);overflow:hidden;}
        .srch-input-row{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #f1f5f9;}
        .srch-input-row svg{color:#94a3b8;flex-shrink:0;}
        .srch-input-row input{flex:1;border:none;outline:none;font-size:1rem;color:#0f172a;}
        .srch-input-row input::placeholder{color:#94a3b8;}
        .srch-close{background:none;border:none;cursor:pointer;color:#94a3b8;font-size:1.1rem;padding:4px 6px;border-radius:6px;}
        .srch-close:hover{background:#f1f5f9;color:#0f172a;}
        .srch-filters{display:flex;gap:6px;padding:10px 14px;overflow-x:auto;border-bottom:1px solid #f1f5f9;scrollbar-width:none;}
        .srch-filters::-webkit-scrollbar{display:none;}
        .srch-filter{flex-shrink:0;padding:5px 12px;border-radius:999px;border:1px solid #e2e8f0;background:#fff;font-size:0.78em;font-weight:700;cursor:pointer;color:#475569;}
        .srch-filter.active,.srch-filter:hover{background:#0f172a;color:#fff;border-color:#0f172a;}
        .srch-items{max-height:340px;overflow-y:auto;padding:8px 0;}
        .srch-item{display:flex;align-items:center;gap:10px;padding:10px 16px;text-decoration:none;color:#1e293b;}
        .srch-item:hover{background:#f8fafc;}
        .srch-item-label{font-size:0.88em;font-weight:600;flex:1;}
        .srch-item-sport{font-size:0.72em;font-weight:700;color:#94a3b8;text-transform:uppercase;}
        .srch-empty{padding:24px 16px;text-align:center;font-size:0.85em;color:#94a3b8;}
        .search-results-wrap{
            max-width:1200px;
            margin:14px auto 0;
            padding:0 24px;
        }
        .search-results{
            display:none;
            background:#ffffff;
            border:1px solid rgba(15,23,42,0.16);
            border-radius:12px;
            padding:14px 16px;
            box-shadow:0 8px 20px rgba(15,23,42,0.08);
        }
        .search-results.show{display:block;}
        .search-results h3{
            margin:0 0 8px;
            font-size:0.98em;
            color:#0f172a;
        }
        .search-results p{margin:0 0 8px;color:#334155;font-size:0.9em;}
        .search-results ul{margin:0;padding-left:18px;color:#0f172a;font-size:0.88em;display:grid;gap:5px;}
        .search-results a{color:var(--link);text-decoration:underline;}
        .perf-dashboard{
            max-width:860px;margin:0 auto;padding:14px 16px;background:#fff;
            border:1px solid rgba(15,23,42,0.16);border-radius:12px;
        }
        .perf-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:10px;}
        .perf-stat{background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:10px;padding:10px 12px;}
        .perf-label{font-size:0.72em;color:#475569;text-transform:uppercase;letter-spacing:0.4px;}
        .perf-value{font-size:1.05em;font-weight:800;color:#0f172a;margin-top:2px;}
        .perf-controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
        .perf-controls select,.perf-controls input{padding:7px 10px;border:1px solid rgba(15,23,42,0.18);border-radius:8px;background:#fff;color:#0f172a;}
        .perf-apply-btn{padding:8px 14px;border:1px solid #00529B;background:#00529B;color:#fff;border-radius:8px;font-weight:700;cursor:pointer;}
        .question-buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;}
        .question-buttons button{border:1px solid rgba(15,23,42,0.2);background:#fff;border-radius:999px;padding:6px 10px;font-size:0.78em;cursor:pointer;color:#0f172a;}
        .perf-answer{margin-top:12px;background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:10px;padding:10px 12px;}
        .perf-answer-title{font-size:0.82em;color:#334155;font-weight:700;margin-bottom:8px;}
        .perf-answer-list{display:grid;gap:6px;}
        .perf-answer-item{display:flex;justify-content:space-between;gap:10px;padding:7px 8px;background:#fff;border:1px solid rgba(15,23,42,0.1);border-radius:8px;font-size:0.8em;color:#0f172a;}
        .perf-empty{font-size:0.82em;color:#475569;background:#fff;border:1px dashed rgba(15,23,42,0.18);border-radius:8px;padding:10px;}
        .logo{display:inline-flex;align-items:center;text-decoration:none;flex-shrink:0;order:2;border-radius:10px;}
        .logo img,.logo .pl-brand-logo__img{display:block;height:36px;width:auto;max-height:42px;max-width:min(220px,42vw);object-fit:contain;}
        a.pl-brand-logo.pl-brand-logo--holding{outline:2px solid rgba(0,82,155,0.35);outline-offset:2px;}
        .hamburger{display:flex;flex-direction:column;justify-content:center;gap:5px;cursor:pointer;padding:7px 9px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;flex-shrink:0;}
        .hamburger:hover{background:#f8fafc;}
        .hamburger span{width:20px;height:1.5px;background:#0f172a;border-radius:2px;transition:all .2s;}
        .tv-overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:1998;backdrop-filter:blur(2px);}
        .tv-overlay.open{display:block;}
        .tv-drawer{position:fixed;top:0;left:0;height:100%;width:min(280px,100vw);background:#fff;z-index:1999;transform:translateX(-100%);transition:transform .28s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;box-shadow:4px 0 32px rgba(15,23,42,0.18);}
        .tv-drawer.open{transform:translateX(0);}
        .tv-drawer-header{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid #e2e8f0;flex-shrink:0;}
        .tv-drawer-title{font-weight:800;font-size:1rem;color:#0f172a;}
        .tv-header-btns{display:flex;gap:8px;align-items:center;}
        .tv-back-btn{background:none;border:none;font-size:1.3rem;cursor:pointer;color:#475569;padding:4px 8px;border-radius:6px;line-height:1;}
        .tv-back-btn:hover{background:#f1f5f9;}
        .tv-close-btn{background:none;border:none;font-size:1.1rem;cursor:pointer;color:#475569;padding:4px 8px;border-radius:6px;line-height:1;}
        .tv-close-btn:hover{background:#f1f5f9;}
        .tv-panels{flex:1;overflow:hidden;position:relative;}
        .tv-panel{position:absolute;inset:0;overflow-y:auto;transition:transform .25s cubic-bezier(.4,0,.2,1);}
        .tv-panel.hidden-left{transform:translateX(-100%);}
        .tv-panel.hidden-right{transform:translateX(100%);}
        .tv-panel.visible{transform:translateX(0);}
        .tv-menu-list{padding:8px;}
        .tv-menu-btn{width:100%;display:flex;align-items:center;gap:12px;padding:11px 12px;border:none;background:none;cursor:pointer;border-radius:8px;text-align:left;transition:background .15s;}
        .tv-menu-btn:hover{background:#f1f5f9;}
        .tv-menu-label{flex:1;font-size:0.9rem;font-weight:700;color:#0f172a;}
        .tv-menu-arrow{color:#94a3b8;font-size:1rem;}
        .tv-sub-link{display:flex;align-items:center;gap:10px;padding:10px 14px;text-decoration:none;color:#1e293b;font-size:0.88rem;font-weight:600;border-radius:8px;margin:1px 8px;transition:background .12s;}
        .tv-sub-link:hover{background:#f1f5f9;color:#00529B;}
        .tv-sub-link.highlight{color:#00529B;font-weight:800;}
        .tv-sub-link .ext{font-size:0.7em;color:#94a3b8;margin-left:2px;}

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
            background:#ffffff;
            border:1px solid rgba(15,23,42,0.18);
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
            color:#0f172a;
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
            background:#f8fafc;
            border:1px solid rgba(15,23,42,0.14);
            border-radius:999px;
            padding:6px 14px;
            font-size:0.95em;
            font-weight:700;
            color:#0f172a;
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
        .free-title{font-size:1.15em;font-weight:800;color:#0f172a;margin-bottom:6px;}
        .free-body{font-size:.93em;color:#334155;line-height:1.6;max-width:620px;}

        /* ── Sports grid ── */
        .section{padding:120px 30px 70px;max-width:1200px;margin:0 auto;}
        .section-title{
            text-align:center;font-size:1.9em;font-weight:800;
            margin-bottom:8px;
            color:var(--text);
        }
        .section-title.secondary{
            font-size:1.4em;
            margin-top:22px;
        }
        .section-sub{text-align:center;color:#334155;font-size:.93em;margin-bottom:40px;}
        .sport-slider{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px 0 32px;}
        .slider-arrow{background:rgba(255,255,255,0.12);border:2px solid rgba(255,255,255,0.6);color:#fff;font-size:1.3em;width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s;user-select:none;flex-shrink:0;}
        .slider-arrow:hover{background:rgba(255,255,255,0.25);transform:scale(1.08);}
        .sport-badges{display:flex;gap:8px;overflow-x:auto;padding:4px;max-width:860px;scroll-behavior:smooth;}
        .sport-pill{display:flex;align-items:center;gap:8px;padding:8px 14px;border-radius:20px;text-decoration:none;background:#ffffff;border:2px solid rgba(15,23,42,0.18);color:#0f172a;font-size:.82em;font-weight:700;white-space:nowrap;transition:all .2s;}
        .sport-pill:hover{border-color:var(--gold);color:#0f172a;}
        .sport-pill.live{background:rgba(16,185,129,.18);border-color:rgba(16,185,129,.5);}
        .sport-pill-status{font-weight:600;opacity:.9;font-size:.7em;text-transform:uppercase;letter-spacing:.4px;color:#334155;}
        .sports-grid{
            display:grid;
            grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
            gap:16px;
        }
        .sport-card{
            background:#ffffff;border:1px solid var(--border);
            border-radius:14px;padding:28px 20px;
            text-align:center;text-decoration:none;color:inherit;
            transition:border-color .2s,transform .2s,box-shadow .2s;
            position:relative;overflow:hidden;
        }
        .sport-card:hover{border-color:#cdd6dc;transform:translateY(-4px);box-shadow:0 8px 24px rgba(26,29,35,.10);}
        .sport-card.live{border-color:rgba(16,185,129,.4);}
        .sport-card.live:hover{border-color:var(--green);box-shadow:0 8px 24px rgba(16,185,129,.2);}
        .live-dot{
            position:absolute;top:12px;right:12px;
            width:8px;height:8px;border-radius:50%;background:var(--green);
            box-shadow:0 0 0 3px rgba(16,185,129,.25);
            animation:pulse 1.8s infinite;
            will-change:transform,opacity;
        }
        @keyframes pulse{
            0%,100%{transform:scale(1);opacity:1;}
            50%{transform:scale(1.15);opacity:.55;}
        }
        .sport-icon{font-size:2.8em;margin-bottom:10px;}
        .sport-name{font-size:1.15em;font-weight:700;margin-bottom:4px;}
        .sport-status{font-size:.78em;color:#334155;text-transform:uppercase;letter-spacing:.5px;}
        .sport-status.live-text{color:#0f172a;font-weight:700;}

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
            background:#ffffff;border:1px solid var(--border);
            border-radius:14px;padding:28px 24px;text-align:center;
        }
        .step-num{
            width:42px;height:42px;border-radius:50%;
            background:#93c5fd;
            display:flex;align-items:center;justify-content:center;
            font-weight:900;font-size:1.1em;margin:0 auto 14px;
            color:#1e3a8a !important;
        }
        .step-title{font-weight:700;font-size:1em;margin-bottom:8px;}
        .step-body{font-size:.86em;color:#334155;line-height:1.6;}

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
        .up-label{color:#0f172a;}
        .up-units{font-size:1.05em;font-weight:900;color:#00C076;}
        .units-pill.negative .up-units{color:#D93025;}
        .up-rec{color:#475569;font-size:0.82em;}

        /* ── Footer (matches site chrome) ── */
        .site-footer{
            background:rgba(255,255,255,0.72);
            border-top:1px solid rgba(15,23,42,0.12);
            padding:22px 24px 28px;
            color:#475569;
            font-size:0.88em;
            backdrop-filter:saturate(140%) blur(2px);
        }
        .footer-outer{max-width:1200px;margin:0 auto;}
        .footer-brand{margin-bottom:18px;}
        .footer-columns-3{
            display:grid;
            grid-template-columns:repeat(3,minmax(0,1fr));
            gap:28px 36px;
            align-items:start;
        }
        .footer-heading{
            font-size:0.72em;
            text-transform:uppercase;
            letter-spacing:0.55px;
            font-weight:800;
            color:#0f172a;
            margin:0 0 12px;
        }
        .footer-col-blk a{
            display:block;
            font-size:0.88em;
            line-height:1.85;
            color:#475569;
            text-decoration:none;
            font-weight:500;
            padding:2px 0;
        }
        .footer-col-blk a:hover{color:#00529B;text-decoration:underline;}
        .footer-bottom{margin-top:22px;padding-top:16px;border-top:1px solid rgba(15,23,42,0.1);font-size:0.82em;color:#475569;opacity:0.78;}
        .share-strip{max-width:1200px;margin:0 auto 10px;padding:10px 16px;display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;background:rgba(244,247,249,0.7);border:1px solid rgba(15,23,42,0.1);border-radius:12px;}
        .share-strip-label{font-size:0.82em;font-weight:800;color:#0f172a;letter-spacing:0.2px;}
        .share-icons{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
        .share-icon{width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;border-radius:999px;border:1px solid rgba(15,23,42,0.14);background:#fff;}
                        .share-icon img{width:16px;height:16px;display:block;}
        .share-icon .txt{display:none;font-size:0.64rem;font-weight:800;line-height:1;color:#0f172a;letter-spacing:0.1px;}
                .share-icon:hover{border-color:#00529B;background:rgba(0,82,155,0.08);}
        .join-premium-bar{display:none;position:fixed;left:0;right:0;bottom:0;z-index:999;background:#0f172a;border-top:1px solid rgba(255,255,255,0.12);}
        .join-premium-inner{max-width:1200px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;}
        .join-premium-copy{color:#e2e8f0;font-size:0.86em;font-weight:600;line-height:1.35;}
        .join-premium-actions{display:flex;align-items:center;gap:8px;}
        .join-premium-btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 14px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;text-decoration:none;font-weight:800;font-size:0.82em;}
        .join-premium-close{border:1px solid rgba(255,255,255,0.3);background:transparent;color:#fff;border-radius:999px;width:28px;height:28px;line-height:1;cursor:pointer;font-size:18px;}
        .join-premium-close:hover{background:rgba(255,255,255,0.1);}

        /* ── Responsive ── */
        @media(max-width:720px){
            .footer-columns-3{grid-template-columns:1fr;gap:22px;}
        }
        @media(max-width:640px){
            .hero{width:calc(100% - 32px) !important;margin:12px auto 0 !important;}
            .hero>div{padding:70px 28px 52px !important;}
            .hero>div>div[style*="gap:40px"]{gap:24px !important;}
            .free-banner{flex-direction:column;}
            .donate-card{padding:36px 24px;}
            .weekly-banner{margin:0 16px;}
            .join-premium-inner{padding:8px 12px;}
            .join-premium-copy{font-size:0.8em;}
        }
        @media (min-width: 769px) {
            body{background-attachment:fixed;}
        }
        @media (max-width: 1100px) {
            .navbar-content { flex-wrap: wrap; justify-content: center; }
            .nav-search-wrap { order: 3; width: 100%; max-width: 100%; }
        }
        @media (max-width: 768px) {
            body{
                background:#ffffff;
                background-attachment:scroll;
            }
            body::before{
                background:transparent;
            }
            .navbar-content {
                display: grid;
                grid-template-columns: auto auto 1fr auto;
                grid-template-areas:
                    "ham logo search actions";
                align-items: center;
                gap: 0 10px;
            }
            .navbar .hamburger { grid-area: ham; display: flex; margin-right: 0; }
            .navbar .logo { grid-area: logo; justify-self: start; }
            .nav-search-wrap { grid-area: search; width: 100%; max-width: none; }
            .nav-actions { grid-area: actions; display: flex; justify-content: end; }
        }
        .nav-group { position: relative; }
        .nav-group-title { color: #00529B; font-weight: 700; cursor: pointer; padding: 8px 10px; border-radius: 8px; display: block; font-size: 0.88em; }
        .nav-group-title:hover { background: rgba(0,82,155,0.08); }
        .nav-group-items { display: none; padding-left: 12px; }
        .nav-group.open .nav-group-items { display: flex; flex-direction: column; }
        .nav-group-items a { font-size: 0.84em; padding: 6px 10px !important; opacity: 0.9; }
        .nav-group-items a:hover { opacity: 1; color: #00529B; }
        /* Skip link for accessibility */
        .skip-link { position:absolute; left:-9999px; top:0; z-index:2000; background:#fbbf24; color:#0f172a; padding:10px 14px; font-weight:800; border-radius:0 0 8px 0; text-decoration:none; }
        .skip-link:focus { left:0; outline:2px solid #0f172a; }
        #main-content, .site-footer { color: var(--text); }
    </style>
</head>
<body>
<a href="#main-content" class="skip-link">Skip to main content</a>

<!-- Navbar -->
<div class="navbar">
    <div class="navbar-content">
        <button type="button" class="hamburger" onclick="tvOpen()" aria-label="Open navigation menu" aria-expanded="false" id="navHamburger"><span></span><span></span><span></span></button>
        <div class="nav-search-wrap">
            <div class="nav-search" onclick="openSrch()">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <input type="text" placeholder="Search teams, leagues, props..." readonly onclick="openSrch()">
            </div>
        </div>
        <a href="/" class="logo pl-brand-logo" aria-label="Prediction Lab home" title="Home — hold the logo to download full quality"><img class="pl-brand-logo__img" src="/static/PLLOGO.PNG" alt="Prediction Lab" width="200" height="60" decoding="async" fetchpriority="high" data-pl-logo-hq="/static/PLLOGO.PNG" draggable="false"></a>
        <div class="nav-actions">
            <div class="acct-wrap">
                <button class="acct-btn" onclick="toggleAcctMenu(event)" aria-label="Account">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                </button>
                <div class="acct-menu" id="acctMenu">
                    {% if is_logged_in %}
                    <a href="/logout">Sign Out</a>
                    {% else %}
                    <a href="/login">Sign In</a>
                    <a href="/signup">Sign Up</a>
                    {% endif %}
                    <div class="acct-menu-divider"></div>
                    <a href="/faq">Help</a>
                </div>
            </div>
            <a href="/signup" class="nav-cta">Get Started</a>
        </div>
    </div>
</div>

<div class="tv-overlay" id="tvOverlay" onclick="tvClose()"></div>
<div class="tv-drawer" id="tvDrawer">
  <div class="tv-drawer-header">
    <div class="tv-header-btns"><button class="tv-back-btn" id="tvBackBtn" onclick="tvBack()" style="display:none">&#8249;</button><span class="tv-drawer-title" id="tvDrawerTitle">Menu</span></div>
    <button class="tv-close-btn" onclick="tvClose()">&#x2715;</button>
  </div>
  <div class="tv-panels">
    <div class="tv-panel visible" id="tvMain">
      <div class="tv-menu-list">
        <button class="tv-menu-btn" onclick="tvSub(\'picks\')"><span class="tv-menu-label">Picks &amp; Predictions</span><span class="tv-menu-arrow">&#8250;</span></button>
        <button class="tv-menu-btn" onclick="tvSub(\'props\')"><span class="tv-menu-label">Props &amp; Models</span><span class="tv-menu-arrow">&#8250;</span></button>
        <button class="tv-menu-btn" onclick="tvSub(\'results\')"><span class="tv-menu-label">Results &amp; Tracking</span><span class="tv-menu-arrow">&#8250;</span></button>
        <button class="tv-menu-btn" onclick="tvToggleMore(this)"><span class="tv-menu-label">More</span><span class="tv-more-arrow" style="color:#94a3b8;font-size:0.85rem;transition:transform .2s;">&#8250;</span></button>
        <div id="tvMoreItems" style="display:none;padding-left:8px;border-left:2px solid #f1f5f9;margin:2px 8px 2px 14px;">
          <button class="tv-menu-btn" style="padding:10px 10px;" onclick="tvSub(\'community\')"><span class="tv-menu-label" style="font-size:0.88rem;">Community</span><span class="tv-menu-arrow">&#8250;</span></button>
          <button class="tv-menu-btn" style="padding:10px 10px;" onclick="tvSub(\'company\')"><span class="tv-menu-label" style="font-size:0.88rem;">Company</span><span class="tv-menu-arrow">&#8250;</span></button>
        </div>
      </div>
    </div>
    <div class="tv-panel hidden-right" id="tvSub"></div>
  </div>
</div>

<div class="srch-overlay" id="srchOverlay" onclick="closeSrchOutside(event)">
  <div class="srch-box">
    <div class="srch-input-row">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input type="text" id="srchInput" placeholder="Search teams, leagues, or matchups...">
      <button class="srch-close" onclick="closeSrch()">&#x2715;</button>
    </div>
    <div class="srch-filters">
      <button class="srch-filter active" data-s="all">All</button>
      <button class="srch-filter" data-s="nba">NBA</button>
      <button class="srch-filter" data-s="nfl">NFL</button>
      <button class="srch-filter" data-s="mlb">MLB</button>
      <button class="srch-filter" data-s="nhl">NHL</button>
      <button class="srch-filter" data-s="ncaab">NCAAB</button>
      <button class="srch-filter" data-s="ncaaf">NCAAF</button>
      <button class="srch-filter" data-s="wnba">WNBA</button>
      <button class="srch-filter" data-s="props">Props</button>
    </div>
    <div class="srch-items" id="srchItems"></div>
  </div>
</div>
<!-- Hero -->
<main id="main-content">
<div class="hero" style="background:#0f172a;border:1px solid rgba(255,255,255,0.07);border-radius:16px;margin:18px auto 0;max-width:1200px;width:calc(100% - 48px);padding:0;">
    <div style="max-width:1100px;margin:0 auto;padding:calc(130px + 0.5in) 60px calc(90px + 0.5in);text-align:left;">
        <h1 class="hero-slide" style="animation:slideIn 0.8s ease-out both;">See The Edge First.</h1>
        <p class="hero-subhead hero-slide" style="text-align:left;max-width:620px;animation:slideIn 0.8s ease-out 0.2s both;">Data-driven picks updated daily across every major sport.</p>
        <div class="hero-slide" style="display:flex;gap:12px;margin-top:28px;animation:slideIn 0.8s ease-out 0.4s both;">
            <a href="/signup" style="background:#e2e8f0;color:#0f172a;padding:15px 32px;border-radius:10px;font-weight:800;text-decoration:none;font-size:1em;box-shadow:0 6px 20px rgba(0,0,0,0.25);">Get Started Free</a>
        </div>
        <p class="hero-slide" style="font-size:0.76em;color:rgba(255,255,255,0.38);margin-top:12px;animation:slideIn 0.8s ease-out 0.5s both;">Free Moneyline Plays &nbsp;&bull;&nbsp; No credit card required.</p>
        <div class="hero-slide" style="display:flex;gap:40px;margin-top:64px;padding-top:40px;border-top:1px solid rgba(255,255,255,0.08);flex-wrap:wrap;animation:slideIn 0.8s ease-out 0.6s both;">
            <div>
                <div style="font-size:1.7em;font-weight:900;color:#00C076;line-height:1;">{{ games_graded }}+</div>
                <div style="font-size:0.72em;color:rgba(255,255,255,0.45);font-weight:600;margin-top:4px;text-transform:uppercase;letter-spacing:0.4px;">Games Graded</div>
            </div>
            <div>
                <div style="font-size:1.7em;font-weight:900;color:#00C076;line-height:1;">{{ sports_covered }}</div>
                <div style="font-size:0.72em;color:rgba(255,255,255,0.45);font-weight:600;margin-top:4px;text-transform:uppercase;letter-spacing:0.4px;">Sports Covered</div>
            </div>
            <div>
                <div style="font-size:1.7em;font-weight:900;color:#00C076;line-height:1;">5</div>
                <div style="font-size:0.72em;color:rgba(255,255,255,0.45);font-weight:600;margin-top:4px;text-transform:uppercase;letter-spacing:0.4px;">AI Models</div>
            </div>
            <div>
                <div style="font-size:1.7em;font-weight:900;color:#00C076;line-height:1;">Daily</div>
                <div style="font-size:0.72em;color:rgba(255,255,255,0.45);font-weight:600;margin-top:4px;text-transform:uppercase;letter-spacing:0.4px;">Updates</div>
            </div>
        </div>
    </div>
</div>
<style>
@keyframes slideIn{from{opacity:0;transform:translateX(-40px);}to{opacity:1;transform:translateX(0);}}
.hero-slide{opacity:0;}
</style>

<!-- Today's AI Picks (live product preview) -->
{% if todays_picks %}
<div class="section" style="margin-top:1.5in;padding-top:24px;padding-bottom:8px;">
    <div style="text-align:center;margin-bottom:8px;">
        <span style="display:inline-flex;align-items:center;gap:8px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.4);color:#00C076;font-size:0.78em;font-weight:800;letter-spacing:0.4px;text-transform:uppercase;padding:5px 14px;border-radius:999px;">
            <span style="display:inline-block;width:8px;height:8px;background:#00C076;border-radius:50%;animation:pulseDot 1.6s infinite;"></span>
            Winning Results Tracked Daily
        </span>
    </div>
    <h2 class="section-title" style="margin-bottom:6px;">Top Value Picks Today</h2>
    <p class="section-sub" style="color:#334155;">Ranked by edge quality, model agreement, and confidence</p>
    <div style="display:flex;flex-direction:column;gap:14px;max-width:600px;margin:0 auto;">
        {% for tp in todays_picks %}
        {% set _disp_pct = tp.prob if tp.prob >= 50 else (100 - tp.prob)|round(1) %}
        <a href="/{{ tp.slug }}" style="display:block;background:#ffffff;border:1px solid rgba(15,23,42,0.18);border-radius:14px;padding:16px 18px;text-decoration:none;color:inherit;transition:transform .18s, border-color .18s, box-shadow .18s;" onmouseover="this.style.transform='translateY(-2px)';this.style.borderColor='rgba(251,191,36,0.5)';this.style.boxShadow='0 10px 22px rgba(15,23,42,0.12)';" onmouseout="this.style.transform='none';this.style.borderColor='rgba(15,23,42,0.18)';this.style.boxShadow='none';">
            <div style="font-size:0.68em;color:#fbbf24;text-transform:uppercase;letter-spacing:0.6px;font-weight:800;margin-bottom:8px;">{{ tp.sport }}</div>
            <div style="font-weight:800;font-size:1.02em;color:#0f172a;line-height:1.35;margin-bottom:10px;">{{ tp.away }} <span style="color:#64748b;font-weight:600;">vs</span> {{ tp.home }}</div>
            <div style="display:flex;align-items:baseline;gap:10px;">
                <span style="color:#00C076;font-size:0.9em;font-weight:800;">▶ {{ tp.pick }}</span>
                <span style="color:#0f172a;font-weight:800;">{{ _disp_pct }}%</span>
                <span style="color:#64748b;font-size:0.78em;font-weight:600;">Moneyline</span>
            </div>
        </a>
        {% endfor %}
    </div>
    <div style="max-width:600px;margin:16px auto 0;text-align:center;">
        <a href="/promo/top-picks-today" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:11px 22px;border-radius:10px;background:#0f172a;color:#fff;font-weight:800;font-size:0.88em;text-decoration:none;border:1px solid rgba(15,23,42,0.4);box-shadow:0 4px 14px rgba(15,23,42,0.15);">Share Picks &#x2197;</a>
        <div style="font-size:0.72em;color:#64748b;margin-top:8px;line-height:1.45;">Daily picks in one shareable page.</div>
    </div>
</div>
<style>@keyframes pulseDot{0%,100%{opacity:1;}50%{opacity:0.4;}}</style>
{% endif %}

<!-- Sports grid -->
<div class="section">
    <h2 class="section-title">Today’s Picks by Sport</h2>
    <p class="section-sub" style="color:#334155;">Live model projections updated daily</p>
    <div class="sports-grid">
        {% for s in landing_sports %}
        <a href="/{{ s.seo_slug }}" class="sport-card {% if s.is_live %}live{% endif %}" style="transition:transform .18s, border-color .18s, box-shadow .18s;" onmouseover="this.style.transform='translateY(-3px)';this.style.borderColor='rgba(251,191,36,0.5)';this.style.boxShadow='0 10px 28px rgba(0,0,0,0.35)';" onmouseout="this.style.transform='none';this.style.borderColor='';this.style.boxShadow='none';">
            {% if s.is_live %}<div class="live-dot"></div>{% endif %}
            <div class="sport-icon">{{ s.icon }}</div>
            <div class="sport-name">{{ s.name }}</div>
            <div class="sport-status {% if s.is_live %}live-text{% endif %}">{{ s.status }}</div>
            <div style="margin-top:8px;font-size:0.72em;color:#334155;">Today’s projections available</div>
            <div style="margin-top:4px;font-size:0.78em;color:#fbbf24;font-weight:700;">View Picks →</div>
        </a>
        {% endfor %}
    </div>
</div>

<!-- Model Performance -->
<div class="section" style="padding-top:10px;padding-bottom:10px;">
    <div style="max-width:860px;margin:0 auto;background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:18px 20px;text-align:center;">
        <h2 style="font-size:1.2rem;font-weight:900;color:#0f172a;margin:0 0 8px;">Model Performance</h2>
        <p style="color:#334155;font-size:0.9em;line-height:1.7;margin:0 0 12px;">See completed-game performance by model and confidence bucket, with sample sizes and color-coded hit rates.</p>
        {% if weekly_banner_messages %}
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;justify-content:center;">
            {% for item in weekly_banner_messages[:3] %}
            <span style="display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;border:1px solid rgba(15,23,42,0.14);background:#f8fafc;color:#0f172a;font-size:0.78em;font-weight:700;">
                <span style="color:#00529B;">Live</span> {{ item.label }} {{ item.pct }} ({{ item.record }})
            </span>
            {% endfor %}
        </div>
        {% endif %}
        <a href="/performance" style="display:inline-flex;align-items:center;justify-content:center;background:#00529B;color:#fff;padding:10px 16px;border-radius:10px;text-decoration:none;font-size:0.88em;font-weight:800;">Open Model Performance</a>
    </div>
</div>

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

<!-- Daily Results Box (above How It Works) -->
<div style="max-width:720px;margin:44px auto 32px;padding:0 24px;">
    <div style="position:relative;overflow:hidden;border-radius:16px;border:1px solid rgba(15,23,42,0.16);background:#ffffff;">
        <div style="position:relative;padding:32px 28px;text-align:center;">
            <h2 style="font-size:1.5em;font-weight:900;color:#fbbf24;">Daily Betting Results Report</h2>
            <p style="color:#334155;font-size:0.9em;margin:10px 0 20px;max-width:480px;margin-left:auto;margin-right:auto;">Yesterday's performance across all sports and models &mdash; tracked, transparent, verified.</p>
            <a href="/results" style="display:inline-block;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;padding:14px 32px;border-radius:10px;font-weight:800;text-decoration:none;font-size:0.95em;box-shadow:0 4px 20px rgba(251,191,36,0.3);transition:transform 0.2s;" onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='none'">View Full Results</a>
        </div>
    </div>
</div>
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

<!-- See What You're Missing -->
<div class="section" style="padding-top:10px;padding-bottom:30px;">
    <h2 class="section-title">See What You’re Missing</h2>
    <p class="section-sub" style="color:#334155;">The public sees picks. Members see the edge.</p>
    <div class="landing-pricing-row">
        <div class="landing-price-card" style="background:#ffffff;border:1px solid rgba(15,23,42,0.22);border-radius:14px;padding:24px;">
            <h3 style="font-size:1.05em;font-weight:800;margin:0 0 8px;color:#0f172a;">Free Picks</h3>
            <div style="font-size:0.78em;color:#94a3b8;font-weight:700;margin:0 0 10px;text-transform:uppercase;letter-spacing:0.4px;line-height:1.35;min-height:2.7em;">Updated daily &middot; no subscription</div>
            <ul class="landing-price-list" style="list-style:none;padding:0;margin:0;font-size:0.9em;color:#0f172a;line-height:1.65;display:flex;flex-direction:column;gap:10px;">
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Moneyline picks across 9 sports</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Model-generated win probability for every game</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Proprietary AI odds engine pricing (not public consensus)</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Multi-model consensus signal strength</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Expanded dataset weighting (injuries, pace, efficiency, market movement)</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Fully tracked historical performance (transparent results)</span></li>
            </ul>
            <a href="/nba-picks" class="landing-price-cta landing-price-cta--light" style="text-align:center;background:#fff;color:#0f172a;border:1px solid rgba(15,23,42,0.32);border-radius:10px;font-weight:800;text-decoration:none;font-size:0.9em;box-shadow:0 2px 8px rgba(15,23,42,0.08);">View Free Picks</a>
        </div>
        <div class="landing-price-card" style="background:#fffdf5;border:1px solid rgba(251,191,36,0.5);border-radius:14px;padding:24px;">
            <h3 style="font-size:1.05em;font-weight:800;margin:0 0 8px;color:#fbbf24;">Full AI Model Access</h3>
            <div style="font-size:0.78em;color:#fde68a;font-weight:700;margin:0 0 10px;text-transform:uppercase;letter-spacing:0.4px;line-height:1.35;min-height:2.7em;">Everything in Free, plus</div>
            <ul class="landing-price-list" style="list-style:none;padding:0;margin:0;font-size:0.9em;color:#0f172a;line-height:1.65;display:flex;flex-direction:column;gap:10px;">
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Spread betting models (edge-based pricing)</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Over/Under totals with projected game flow</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Predicted final scores (simulation-based outputs)</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Enhanced multi-model consensus signals</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Player props picks and projections</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Model performance page access</span></li>
            </ul>
            <a href="/plans" class="landing-price-cta landing-price-cta--gold" style="text-align:center;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;border-radius:10px;font-weight:800;text-decoration:none;font-size:0.9em;box-shadow:0 4px 18px rgba(251,191,36,0.25);">Unlock Model Edge</a>
        </div>
    </div>
    <p style="max-width:860px;margin:14px auto 0;text-align:center;font-size:0.8em;color:#94a3b8;line-height:1.5;">Free moneyline picks and premium spreads, totals, and scores are all updated daily as schedules, injuries, and markets change.</p>
    <style>
        .landing-pricing-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:stretch; gap:18px; max-width:860px; margin:0 auto; }
        .landing-price-card { display:flex; flex-direction:column; min-height:100%; }
        .landing-price-card .landing-price-list { flex:1 1 auto; min-height:16.5rem; }
        .landing-price-cta { display:flex; align-items:center; justify-content:center; margin-top:auto; min-height:48px; padding:0 22px; box-sizing:border-box; flex-shrink:0; }
        @media (max-width: 768px) {
            .landing-pricing-row { grid-template-columns:1fr !important; }
            .landing-price-card .landing-price-list { min-height:0; }
        }
    </style>
</div>

<!-- Why Different (above FAQ) -->
<div class="section" style="padding-top:10px;padding-bottom:40px;">
    <div style="max-width:900px;margin:0 auto;">
        <h2 class="section-title">Why Our Picks Are Different</h2>
        <div style="max-width:720px;margin:0 auto;color:#1A1D23;line-height:1.75;font-size:0.95em;text-align:left;">
            <p style="margin-bottom:14px;">Most bettors rely on public trends, hot streaks, and guesswork. That&rsquo;s why they lose.</p>
            <p style="margin-bottom:14px;">Our AI sports betting picks are built differently.</p>
            <p style="margin-bottom:14px;">We use a proprietary odds engine powered by four independent AI prediction models to analyze matchups, player performance, advanced team metrics, and real-time market movement. Instead of following sportsbook lines, we generate our own probabilities to uncover +EV betting opportunities the market often misprices.</p>
            <p style="margin-bottom:14px;">This approach allows us to identify value before it becomes obvious. While most bettors chase line movement, our system is designed to stay ahead of it.</p>
            <p style="margin-bottom:14px;">Every pick is backed by data &mdash; not opinions, narratives, or social media hype. Our models continuously process new information, adjusting predictions based on injuries, form, and betting market shifts. The result is a smarter, more consistent approach to sports betting predictions.</p>
            <p style="margin-bottom:14px;">Transparency is a core part of what we do. Every result is tracked publicly, with no cherry-picked wins or hidden losses. You can see exactly how the model performs over time, giving you full confidence in the system behind the picks.</p>
            <p style="margin-bottom:14px;">If you&rsquo;re looking for the best betting picks today, built on real data and AI-driven analysis, you&rsquo;re in the right place.</p>
            <p style="margin-bottom:0;">Our goal isn&rsquo;t just to win short-term &mdash; it&rsquo;s to create a long-term edge using disciplined, data-driven betting strategies that outperform the average bettor.</p>
        </div>
    </div>
</div>

<!-- FAQ moved to /faq — link is in the footer only. -->

<!-- SEO Text -->
<div class="section" style="padding-top:0;padding-bottom:20px;">
    <p style="max-width:760px;margin:0 auto;font-size:0.92em;color:#334155;line-height:1.8;text-align:center;">Free AI sports picks and predictions for NBA, NFL, MLB, NHL, soccer, and more. Our models generate daily projections for moneyline, spreads, and totals using real-time data and multi-model consensus &mdash; every pick tracked with full transparency so you can evaluate real performance over time.</p>
</div>

<!-- SEO Internal Links -->
<div class="section" style="padding-top:10px;padding-bottom:40px;text-align:center;">
    <h3 style="font-size:1.15em;font-weight:800;margin-bottom:14px;color:#0f172a;">Browse AI Picks by League</h3>
    <div class="browse-league-grid" style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;max-width:1100px;margin:0 auto;">
        <a href="/mlb-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">MLB AI Picks &amp; Projections</a>
        <a href="/nba-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">NBA AI Picks &amp; Projections</a>
        <a href="/nhl-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">NHL AI Picks &amp; Projections</a>
        <a href="/nfl-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">NFL AI Picks &amp; Projections</a>
        <a href="/soccer-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">Soccer AI Picks &amp; Projections</a>
        <a href="/ncaab-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">NCAAB AI Picks &amp; Projections</a>
        <a href="/ncaaf-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">NCAAF AI Picks &amp; Projections</a>
        <a href="/ncaaw-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">NCAAW AI Picks &amp; Projections</a>
        <a href="/wnba-picks" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">WNBA AI Picks &amp; Projections</a>
        <a href="/daily-report" style="color:#0f172a;text-decoration:none;font-size:0.9em;font-weight:600;padding:8px 16px;border:1px solid rgba(15,23,42,0.2);border-radius:8px;background:#ffffff;">Daily Betting Results Report</a>
    </div>
    <style>
        @media (max-width: 980px) {
            .browse-league-grid { grid-template-columns: repeat(2, minmax(0,1fr)) !important; }
        }
    </style>
</div>

</main>

<!-- Footer -->
<div class="share-strip">
    <span class="share-strip-label">Share on social media</span>
    <div class="share-icons">
        <a class="share-icon" href="https://x.com/intent/post?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on X"><img src="/static/icons/social/x.svg" alt="X"></a>
        <a class="share-icon" href="https://www.facebook.com/sharer/sharer.php?u={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Facebook"><img src="/static/icons/social/facebook.svg" alt="Facebook"></a>
        <a class="share-icon" href="{{ 'https://www.instagram.com/' if request.path == '/daily-report' else 'https://instagram.com/predictionlab' }}" target="_blank" rel="noopener" aria-label="Instagram"><img src="/static/icons/social/instagram.svg" alt="Instagram"></a>
        <a class="share-icon" href="{{ 'https://www.tiktok.com/upload?lang=en' if request.path == '/daily-report' else 'https://predictionlab.io' }}" target="_blank" rel="noopener" aria-label="TikTok"><img src="/static/icons/social/tiktok.svg" alt="TikTok"></a>
        <a class="share-icon" href="https://www.linkedin.com/sharing/share-offsite/?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on LinkedIn"><img src="/static/icons/social/linkedin.svg" alt="LinkedIn"></a>
        <a class="share-icon" href="https://www.reddit.com/submit?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Reddit"><img src="/static/icons/social/reddit.svg" alt="Reddit"></a>
        <a class="share-icon" href="https://www.tumblr.com/widgets/share/tool?canonicalUrl={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Tumblr"><img src="/static/icons/social/tumblr.svg" alt="Tumblr"></a>
        <a class="share-icon" href="https://api.whatsapp.com/send?text={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on WhatsApp"><img src="/static/icons/social/whatsapp.svg" alt="WhatsApp"></a>
        <a class="share-icon" href="https://telegram.me/share/url?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Telegram"><img src="/static/icons/social/telegram.svg" alt="Telegram"></a>
    </div>
</div>
<footer class="site-footer">
    <div class="footer-outer">
        <div class="footer-brand"><a href="/" aria-label="Prediction Lab home" style="font-weight:900;font-size:1.05em;color:#0f172a;text-decoration:none;letter-spacing:0.2px;">Prediction Lab</a></div>
        <div class="footer-columns-3">
            <div class="footer-col-blk">
                <div class="footer-heading">Company</div>
                <a href="/plans">Plans &amp; pricing</a>
                <a href="/tutorial">Tutorial</a>
                <a href="/contact">Contact us</a>
                <a href="/privacy">Privacy</a>
                <a href="/terms">Terms</a>
                <a href="/responsible-gaming">Responsible gaming</a>
            </div>
            <div class="footer-col-blk">
                <div class="footer-heading">Product</div>
                <a href="/faq">FAQ</a>
                <a href="/daily-report">Daily results report</a>
                <a href="/search">Search</a>
                <a href="/performance">Model performance</a>
                <a href="/ai-sports-betting-picks-today">AI picks today</a>
                <a href="/what-are-ai-sports-betting-picks">What are AI picks</a>
                <a href="/our-model-vs-sportsbooks">Model vs sportsbooks</a>
            </div>
            <div class="footer-col-blk">
                <div class="footer-heading">Social</div>
                <a href="https://x.com/predictionlab_io" target="_blank" rel="noopener">X (Twitter)</a>
                <a href="https://instagram.com/predictionlab.io" target="_blank" rel="noopener">Instagram</a>
                <a href="https://facebook.com/predictionlab.io" target="_blank" rel="noopener">Facebook</a>
                <a href="https://predictionlab.io" target="_blank" rel="noopener">TikTok</a>
                <a href="https://predictionlab.io" target="_blank" rel="noopener">YouTube</a>
            </div>
        </div>
        <div class="footer-bottom">&copy; 2026 predictionlab.io. ALL RIGHTS RESERVED.</div>
    </div>
</footer>

{% if not is_logged_in %}
<div class="join-premium-bar" id="joinPremiumBar" role="complementary" aria-label="Join premium">
    <div class="join-premium-inner">
        <span class="join-premium-copy">Join premium for spreads, totals, projected scores, and full model edge.</span>
        <div class="join-premium-actions">
            <a href="/plans" class="join-premium-btn">Join Now</a>
            <button type="button" class="join-premium-close" onclick="document.getElementById('joinPremiumBar').style.display='none';" aria-label="Close">×</button>
        </div>
    </div>
</div>
{% endif %}

<script>
    var TV_MENUS={picks:{title:'Picks & Predictions',items:[{l:'NBA',h:'/nba-picks'},{l:'MLB',h:'/mlb-picks'},{l:'NHL',h:'/nhl-picks'},{l:'NFL',h:'/nfl-picks'}{% if soccer_enabled %},{l:'Soccer',h:'/soccer-picks'}{% endif %},{l:'NCAAB',h:'/ncaab-picks'},{l:'NCAAF',h:'/ncaaf-picks'},{l:'NCAAW',h:'/ncaaw-picks'},{l:'WNBA',h:'/wnba-picks'},{l:'View All →',h:'/',cls:'highlight'}]},props:{title:'Props & Models',items:[{l:'Player Props',h:'/player-props'},{l:'Model Performance',h:'/performance'},{l:'AI Picks Today',h:'/ai-sports-betting-picks-today'},{l:'Daily Results',h:'/daily-report'},{l:'Model vs Sportsbooks',h:'/our-model-vs-sportsbooks'},{l:'Tutorial',h:'/tutorial'}]},results:{title:'Results & Tracking',items:[{l:'Daily Results',h:'/daily-report'},{l:'Historical Performance',h:'/performance'},{l:'Download CSV',h:'/picks/export.csv'}]},community:{title:'Community',items:[{l:'X / Twitter',h:'https://x.com/predictionlab_io',ext:true},{l:'Instagram',h:'https://instagram.com/predictionlab.io',ext:true},{l:'Reddit',h:'https://reddit.com/r/sportsbetting',ext:true},{l:'Telegram',h:'https://t.me/predictionlab',ext:true}]},company:{title:'Company',items:[{l:'Plans & Pricing',h:'/plans'},{l:'FAQ',h:'/faq'},{l:'Contact',h:'/contact'},{l:'Privacy',h:'/privacy'},{l:'Terms',h:'/terms'}]}};
    function tvOpen(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.add('open');if(d)d.classList.add('open');document.body.style.overflow='hidden';if(h)h.setAttribute('aria-expanded','true');}
    function tvClose(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.remove('open');if(d)d.classList.remove('open');document.body.style.overflow='';if(h)h.setAttribute('aria-expanded','false');setTimeout(function(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';},280);}
    function tvSub(key){var menu=TV_MENUS[key];if(!menu)return;var html='';menu.items.forEach(function(item){var ext=item.ext?' target="_blank" rel="noopener"':'';var cls='tv-sub-link'+(item.cls?' '+item.cls:'');var extIcon=item.ext?' <span class="ext">&#8599;</span>':'';html+='<a href="'+item.h+'" class="'+cls+'"'+ext+'>'+item.l+extIcon+'</a>';});document.getElementById('tvSub').innerHTML=html;document.getElementById('tvDrawerTitle').textContent=menu.title;document.getElementById('tvBackBtn').style.display='';document.getElementById('tvMain').className='tv-panel hidden-left';document.getElementById('tvSub').className='tv-panel visible';}
    function tvBack(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';}
    function tvToggleMore(btn){var el=document.getElementById('tvMoreItems');var open=el.style.display==='block';el.style.display=open?'none':'block';var arrow=btn.querySelector('.tv-more-arrow');if(arrow)arrow.style.transform=open?'':'rotate(90deg)';}
    function toggleAcctMenu(e){e.stopPropagation();document.getElementById('acctMenu').classList.toggle('open');}
    document.addEventListener('click',function(){var m=document.getElementById('acctMenu');if(m)m.classList.remove('open');});
    var _srchFilter='all';
    var _srchDefaults=[{l:'NBA Picks',h:'/nba-picks',s:'nba'},{l:'NFL Picks',h:'/nfl-picks',s:'nfl'},{l:'MLB Picks',h:'/mlb-picks',s:'mlb'},{l:'NHL Picks',h:'/nhl-picks',s:'nhl'},{l:'NCAAB Picks',h:'/ncaab-picks',s:'ncaab'},{l:'NCAAF Picks',h:'/ncaaf-picks',s:'ncaaf'},{l:'WNBA Picks',h:'/wnba-picks',s:'wnba'}{% if soccer_enabled %},{l:'Soccer Picks',h:'/soccer-picks',s:'all'}{% endif %},{l:'Player Props',h:'/player-props',s:'props'},{l:'Model Performance',h:'/performance',s:'props'},{l:'Daily Results',h:'/daily-report',s:'all'}];
    function openSrch(){document.getElementById('srchOverlay').classList.add('open');document.body.style.overflow='hidden';setTimeout(function(){document.getElementById('srchInput').focus();},60);renderSrchItems('');}
    function closeSrch(){document.getElementById('srchOverlay').classList.remove('open');document.body.style.overflow='';document.getElementById('srchInput').value='';}
    function closeSrchOutside(e){if(e.target===document.getElementById('srchOverlay'))closeSrch();}
    function renderSrchItems(q){var items=_srchDefaults.filter(function(i){return(_srchFilter==='all'||i.s===_srchFilter)&&(!q||i.l.toLowerCase().includes(q.toLowerCase()));});var el=document.getElementById('srchItems');if(!items.length){el.innerHTML='<div class="srch-empty">No results found</div>';return;}el.innerHTML=items.map(function(i){return'<a class="srch-item" href="'+i.h+'"><span class="srch-item-label">'+i.l+'</span><span class="srch-item-sport">'+i.s.toUpperCase()+'</span></a>';}).join('');}
    document.addEventListener('DOMContentLoaded',function(){var inp=document.getElementById('srchInput');if(inp){inp.addEventListener('input',function(){renderSrchItems(this.value);});}document.querySelectorAll('.srch-filter').forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll('.srch-filter').forEach(function(b){b.classList.remove('active');});this.classList.add('active');_srchFilter=this.dataset.s;renderSrchItems(document.getElementById('srchInput').value);});});});
    document.addEventListener('keydown',function(e){if(e.key==='Escape'){tvClose();closeSrch();}});
    document.addEventListener('DOMContentLoaded', function() {
        const premiumBar = document.getElementById('joinPremiumBar');
        const searchForm = document.getElementById('navSearchForm');
        const searchInput = document.getElementById('navSearchInput');
        const autocompleteEl = document.getElementById('searchAutocomplete');
        const resultsEl = document.getElementById('searchResults');
        if (premiumBar) {
            const showBar = function(){ premiumBar.style.display = 'block'; };
            if ('requestIdleCallback' in window) requestIdleCallback(showBar, { timeout: 1800 });
            else setTimeout(showBar, 1200);
        }
        const teams = [
            { name: "Detroit Pistons", sport: "NBA", slug: "detroit-pistons" },
            { name: "Detroit Red Wings", sport: "NHL", slug: "detroit-red-wings" },
            { name: "Detroit Tigers", sport: "MLB", slug: "detroit-tigers" },
            { name: "Boston Celtics", sport: "NBA", slug: "boston-celtics" },
        ];
        if (searchForm && resultsEl) {
            let debounceTimer = null;
            if (searchInput && autocompleteEl) {
                searchInput.addEventListener('input', function() {
                    const q = (searchInput.value || '').trim().toLowerCase();
                    clearTimeout(debounceTimer);
                    debounceTimer = setTimeout(() => {
                        if (!q) {
                            autocompleteEl.classList.remove('show');
                            autocompleteEl.innerHTML = '';
                            return;
                        }
                        const matches = teams.filter(t => t.name.toLowerCase().includes(q)).slice(0, 5);
                        autocompleteEl.innerHTML = matches.map(t => `<div class="search-item" data-slug="${t.slug}"><span>${t.name}</span><small>${t.sport}</small></div>`).join('') || '<div class="search-item"><span>No team matches</span></div>';
                        autocompleteEl.classList.add('show');
                    }, 300);
                });
                autocompleteEl.addEventListener('click', function(e) {
                    const item = e.target.closest('[data-slug]');
                    if (!item) return;
                    window.location.href = `/teams/${item.getAttribute('data-slug')}`;
                });
            }
            searchForm.addEventListener('submit', async function(event) {
                event.preventDefault();
                const input = searchForm.querySelector('input[name="query"]');
                const query = (input?.value || '').trim();
                if (!query) {
                    resultsEl.classList.remove('show');
                    resultsEl.innerHTML = '';
                    return;
                }
                resultsEl.classList.add('show');
                resultsEl.innerHTML = '<p>Searching...</p>';
                try {
                    const resp = await fetch(`/api/search?query=${encodeURIComponent(query)}`, { headers: { 'Accept': 'application/json' } });
                    const data = await resp.json();
                    const modelLine = data.matched_model ? `<p><strong>Model:</strong> ${data.matched_model.public_name} -> ${data.matched_model.internal_name}${data.confidence_threshold ? ` (confidence >= ${data.confidence_threshold}%)` : ''}</p>` : '';
                    const modelItems = (data.model_results || []).map(r => `<li>${r.sport}: ${r.record} (${r.accuracy}%)${r.filtered_games !== null && r.filtered_games !== undefined ? ` - ${r.filtered_games} games at threshold` : ''}</li>`).join('');
                    const localTeamItems = (data.team_results || []).map(r => `<li>${r.sport}: ${r.away_team} vs ${r.home_team} (${r.game_date}) - pick: ${r.predicted_winner} (${r.win_probability}%)</li>`).join('');
                    const espnItems = (data.espn_results || []).map(r => `<li>${r.sport}: ${r.away_team} at ${r.home_team} (${r.status})</li>`).join('');
                    const routeLine = data.suggested_route ? `<p><strong>Suggested page:</strong> <a href="${data.suggested_route}">${data.suggested_route}</a></p>` : '';
                    const empty = (!modelItems && !localTeamItems && !espnItems) ? '<p>No matches found yet. Try a team name, league, or model alias.</p>' : '';
                    resultsEl.innerHTML = `
                        <h3>Search Results</h3>
                        ${modelLine}
                        ${routeLine}
                        ${modelItems ? `<p><strong>Model Performance</strong></p><ul>${modelItems}</ul>` : ''}
                        ${localTeamItems ? `<p style="margin-top:10px;"><strong>Our Prediction Matches</strong></p><ul>${localTeamItems}</ul>` : ''}
                        ${espnItems ? `<p style="margin-top:10px;"><strong>Latest ESPN Matchups</strong></p><ul>${espnItems}</ul>` : ''}
                        ${empty}
                    `;
                } catch (_err) {
                    resultsEl.innerHTML = '<p>Search temporarily unavailable. Please try again.</p>';
                }
            });
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
    <script src="/static/js/pl-header-logo.js" defer></script>

</body>
</html>
    """, nhl_accuracy=nhl_accuracy, nfl_accuracy=nfl_accuracy, nba_accuracy=nba_accuracy,
         games_graded=games_graded, predictions_logged=predictions_logged,
         stripe_url=STRIPE_DONATION_URL, landing_sports=landing_sports,
         sports_covered=sports_covered, weekly_banner_messages=weekly_banner_messages,
         units_banner_items=units_banner_items,
         seo_archive_links=seo_archive_links,
         todays_picks=todays_picks,
         landing_share_url=_landing_share_url,
         landing_share_title=_landing_share_title,
         landing_share_body=_landing_share_body,
         landing_share_tweet=_landing_share_tweet)

_SITE_DOMAIN = 'https://predictionlab.io'

_PUBLIC_TO_INTERNAL_MODEL = {
    'grinder2': 'Glicko-2',
    'takedown': 'TrueSkill',
    'edge': 'Elo',
    'xsharp': 'XGBoost',
    'sharp consensus': 'Ensemble',
}

_MODEL_BACKTEST_COLS = {
    'Glicko-2': ('elo_correct', 'elo_accuracy', 'elo_home_prob'),
    'Elo': ('elo_correct', 'elo_accuracy', 'elo_home_prob'),
    'TrueSkill': ('consensus_correct', 'consensus_accuracy', 'logistic_home_prob'),
    'XGBoost': ('xgboost_correct', 'xgboost_accuracy', 'xgboost_home_prob'),
    'Ensemble': ('combined_correct', 'combined_accuracy', 'meta_home_prob'),
}

_SPORT_TO_ROUTE = {
    'NHL': '/nhl-picks',
    'NBA': '/nba-picks',
    'MLB': '/mlb-picks',
    'NFL': '/nfl-picks',
    'NCAAB': '/ncaab-picks',
    'NCAAW': '/ncaaw-picks',
    'NCAAF': '/ncaaf-picks',
    'WNBA': '/wnba-picks',
    'SOCCER': '/soccer-picks',
}

_ESPN_SCOREBOARD_ENDPOINTS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
}

_TEAM_DIRECTORY = {
    'detroit-pistons': {'sport': 'NBA', 'name': 'Detroit Pistons'},
    'detroit-red-wings': {'sport': 'NHL', 'name': 'Detroit Red Wings'},
    'detroit-tigers': {'sport': 'MLB', 'name': 'Detroit Tigers'},
    'boston-celtics': {'sport': 'NBA', 'name': 'Boston Celtics'},
}

def _parse_search_model(query_text: str):
    q = query_text.lower()
    for public_name, internal_name in _PUBLIC_TO_INTERNAL_MODEL.items():
        if public_name in q:
            return public_name, internal_name
    return None, None

def _parse_confidence_threshold(query_text: str):
    q = query_text.lower()
    match = re.search(r'(\d{2,3})\s*%?', q)
    if not match:
        return None
    value = max(0, min(100, int(match.group(1))))
    if any(tok in q for tok in ('over', 'above', '>=', '>','at least')):
        return value
    return None

def _search_model_performance(conn, internal_model: str, threshold: int | None):
    if not internal_model:
        return []
    correct_col, accuracy_col, prob_col = _MODEL_BACKTEST_COLS.get(
        internal_model, ('combined_correct', 'combined_accuracy', 'meta_home_prob')
    )
    rows = conn.execute("SELECT * FROM model_backtest_results ORDER BY sport").fetchall()
    results = []
    for row in rows:
        sport = row['sport']
        total_games = int(row['total_games'] or 0)
        correct = int(row[correct_col] or 0)
        accuracy = round(float(row[accuracy_col] or 0), 1)
        filtered_games = None
        if threshold is not None:
            threshold_pct = threshold / 100.0
            filtered_games = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM predictions
                WHERE sport = ?
                  AND {prob_col} IS NOT NULL
                  AND (
                        {prob_col} >= ?
                     OR (1.0 - {prob_col}) >= ?
                  )
                """,
                (sport, threshold_pct, threshold_pct)
            ).fetchone()[0]
        results.append({
            'sport': sport,
            'record': f'{correct}-{max(total_games - correct, 0)}',
            'accuracy': accuracy,
            'filtered_games': filtered_games,
        })
    return results

def _search_local_team_predictions(conn, query_text: str):
    like = f"%{query_text.lower()}%"
    rows = conn.execute(
        """
        SELECT sport, game_date, away_team_id, home_team_id, predicted_winner, win_probability
        FROM predictions
        WHERE LOWER(COALESCE(home_team_id,'')) LIKE ?
           OR LOWER(COALESCE(away_team_id,'')) LIKE ?
        ORDER BY created_at DESC
        LIMIT 6
        """,
        (like, like)
    ).fetchall()
    return [{
        'sport': r['sport'],
        'game_date': r['game_date'],
        'away_team': r['away_team_id'],
        'home_team': r['home_team_id'],
        'predicted_winner': r['predicted_winner'],
        'win_probability': round(float(r['win_probability'] or 0) * 100, 1),
    } for r in rows]

def _search_espn_team_matches(query_text: str):
    q = query_text.lower()
    matches = []
    for sport, endpoint in _ESPN_SCOREBOARD_ENDPOINTS.items():
        if len(matches) >= 6:
            break
        try:
            data = _cached_get(endpoint, timeout=6) or {}
            for ev in data.get('events', []):
                comp = (ev.get('competitions') or [{}])[0]
                teams = comp.get('competitors') or []
                if len(teams) < 2:
                    continue
                home = next((t for t in teams if t.get('homeAway') == 'home'), teams[0])
                away = next((t for t in teams if t.get('homeAway') == 'away'), teams[-1])
                home_name = ((home.get('team') or {}).get('displayName') or '').strip()
                away_name = ((away.get('team') or {}).get('displayName') or '').strip()
                if q in home_name.lower() or q in away_name.lower():
                    matches.append({
                        'sport': sport,
                        'home_team': home_name,
                        'away_team': away_name,
                        'status': (comp.get('status') or {}).get('type', {}).get('shortDetail', 'Scheduled'),
                    })
                    if len(matches) >= 6:
                        break
        except Exception:
            continue
    return matches

def _build_search_payload(raw_query: str):
    q = (raw_query or '').strip()
    if not q:
        return {
            'query': '',
            'matched_model': None,
            'confidence_threshold': None,
            'model_results': [],
            'team_results': [],
            'espn_results': [],
            'suggested_route': '/',
        }
    public_model, internal_model = _parse_search_model(q)
    threshold = _parse_confidence_threshold(q)
    payload = {
        'query': q,
        'matched_model': (
            {'public_name': public_model.title(), 'internal_name': internal_model}
            if internal_model else None
        ),
        'confidence_threshold': threshold,
        'model_results': [],
        'team_results': [],
        'espn_results': [],
        'suggested_route': None,
    }
    try:
        conn = get_db_connection()
        payload['team_results'] = _search_local_team_predictions(conn, q)
        payload['model_results'] = _search_model_performance(conn, internal_model, threshold)
        conn.close()
    except Exception:
        pass
    payload['espn_results'] = _search_espn_team_matches(q)
    if payload['team_results']:
        top_sport = (payload['team_results'][0].get('sport') or '').upper()
        payload['suggested_route'] = _SPORT_TO_ROUTE.get(top_sport)
    elif payload['espn_results']:
        top_sport = (payload['espn_results'][0].get('sport') or '').upper()
        payload['suggested_route'] = _SPORT_TO_ROUTE.get(top_sport)
    elif internal_model or threshold is not None:
        payload['suggested_route'] = '/results'
    return payload

@app.route('/api/search')
def api_search():
    return jsonify(_build_search_payload(request.args.get('query', '')))

@app.route('/api/performance-data')
def api_performance_data():
    """Per-model, per-game performance rows for client-side filtering UI."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    rows_out = []
    filtered_rows = []
    meta = {'predictions_count': 0, 'matched_results_count': 0, 'rows_out_count': 0}

    raw_model = (request.args.get('model') or '').strip()
    raw_sport = (request.args.get('sport') or '').strip()
    raw_conf = (request.args.get('min_conf') or request.args.get('confidence') or '').strip()
    raw_consensus = (request.args.get('consensus') or request.args.get('min_consensus') or '').strip()

    req_model = '' if raw_model.lower() in ('', 'all', 'all models') else raw_model
    req_sport = '' if raw_sport.lower() in ('', 'all') else raw_sport.upper()
    try:
        req_min_conf = max(0.0, min(100.0, float(raw_conf))) if raw_conf != '' else None
    except Exception:
        req_min_conf = None
    try:
        req_min_consensus = max(0.0, min(100.0, float(raw_consensus))) if raw_consensus != '' else None
    except Exception:
        req_min_consensus = None

    games_where = ["g.home_score IS NOT NULL", "g.away_score IS NOT NULL"]
    games_params = []
    if req_sport:
        games_where.append("UPPER(g.sport) = ?")
        games_params.append(req_sport)

    # Base dataset is last 200 completed games per sport (or for selected sport).
    base_sql = f"""
        WITH ranked_games AS (
            SELECT
                g.sport,
                g.game_id,
                date(g.game_date) AS game_date,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
                ROW_NUMBER() OVER (
                    PARTITION BY UPPER(g.sport)
                    ORDER BY date(g.game_date) DESC
                ) AS rn
            FROM games g
            WHERE {' AND '.join(games_where)}
        ),
        selected_games AS (
            SELECT *
            FROM ranked_games
            WHERE rn <= 200
        ),
        game_pred_ranked AS (
            SELECT
                sg.sport,
                sg.game_id,
                sg.game_date,
                sg.home_team_id,
                sg.away_team_id,
                sg.home_score,
                sg.away_score,
                p.elo_home_prob,
                p.logistic_home_prob,
                p.xgboost_home_prob,
                p.catboost_home_prob,
                p.meta_home_prob,
                ROW_NUMBER() OVER (
                    PARTITION BY sg.sport, sg.game_id, sg.game_date, sg.home_team_id, sg.away_team_id
                    ORDER BY datetime(COALESCE(p.created_at, p.game_date)) DESC
                ) AS pred_rn
            FROM selected_games sg
            LEFT JOIN predictions p
              ON UPPER(p.sport) = UPPER(sg.sport)
             AND (
                p.game_id = sg.game_id
                OR (
                    date(p.game_date) = sg.game_date
                    AND p.home_team_id = sg.home_team_id
                    AND p.away_team_id = sg.away_team_id
                )
             )
        ),
        base AS (
            SELECT
                UPPER(sport) AS sport,
                game_date AS date,
                home_score,
                away_score,
                elo_home_prob,
                logistic_home_prob,
                xgboost_home_prob,
                catboost_home_prob,
                meta_home_prob
            FROM game_pred_ranked
            WHERE pred_rn = 1
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND home_score != away_score
        ),
        model_rows AS (
            SELECT
                sport,
                date,
                'Grinder2' AS model,
                ROUND(MAX(COALESCE(catboost_home_prob, elo_home_prob), 1.0 - COALESCE(catboost_home_prob, elo_home_prob)) * 100.0, 1) AS confidence,
                CASE WHEN meta_home_prob IS NULL
                     THEN ROUND(MAX(COALESCE(catboost_home_prob, elo_home_prob), 1.0 - COALESCE(catboost_home_prob, elo_home_prob)) * 100.0, 1)
                     ELSE ROUND(MAX(meta_home_prob, 1.0 - meta_home_prob) * 100.0, 1)
                END AS consensus,
                CASE
                    WHEN (COALESCE(catboost_home_prob, elo_home_prob) >= 0.5 AND home_score > away_score)
                      OR (COALESCE(catboost_home_prob, elo_home_prob) < 0.5 AND home_score < away_score)
                    THEN 'win' ELSE 'loss'
                END AS result,
                CASE
                    WHEN (COALESCE(catboost_home_prob, elo_home_prob) >= 0.5 AND home_score > away_score)
                      OR (COALESCE(catboost_home_prob, elo_home_prob) < 0.5 AND home_score < away_score)
                    THEN 1 ELSE -1
                END AS units
            FROM base
            WHERE COALESCE(catboost_home_prob, elo_home_prob) IS NOT NULL

            UNION ALL

            SELECT
                sport,
                date,
                'Edge' AS model,
                ROUND(MAX(elo_home_prob, 1.0 - elo_home_prob) * 100.0, 1) AS confidence,
                CASE WHEN meta_home_prob IS NULL
                     THEN ROUND(MAX(elo_home_prob, 1.0 - elo_home_prob) * 100.0, 1)
                     ELSE ROUND(MAX(meta_home_prob, 1.0 - meta_home_prob) * 100.0, 1)
                END AS consensus,
                CASE
                    WHEN (elo_home_prob >= 0.5 AND home_score > away_score)
                      OR (elo_home_prob < 0.5 AND home_score < away_score)
                    THEN 'win' ELSE 'loss'
                END AS result,
                CASE
                    WHEN (elo_home_prob >= 0.5 AND home_score > away_score)
                      OR (elo_home_prob < 0.5 AND home_score < away_score)
                    THEN 1 ELSE -1
                END AS units
            FROM base
            WHERE elo_home_prob IS NOT NULL

            UNION ALL

            SELECT
                sport,
                date,
                'Takedown' AS model,
                ROUND(MAX(logistic_home_prob, 1.0 - logistic_home_prob) * 100.0, 1) AS confidence,
                CASE WHEN meta_home_prob IS NULL
                     THEN ROUND(MAX(logistic_home_prob, 1.0 - logistic_home_prob) * 100.0, 1)
                     ELSE ROUND(MAX(meta_home_prob, 1.0 - meta_home_prob) * 100.0, 1)
                END AS consensus,
                CASE
                    WHEN (logistic_home_prob >= 0.5 AND home_score > away_score)
                      OR (logistic_home_prob < 0.5 AND home_score < away_score)
                    THEN 'win' ELSE 'loss'
                END AS result,
                CASE
                    WHEN (logistic_home_prob >= 0.5 AND home_score > away_score)
                      OR (logistic_home_prob < 0.5 AND home_score < away_score)
                    THEN 1 ELSE -1
                END AS units
            FROM base
            WHERE logistic_home_prob IS NOT NULL

            UNION ALL

            SELECT
                sport,
                date,
                'XSharp' AS model,
                ROUND(MAX(xgboost_home_prob, 1.0 - xgboost_home_prob) * 100.0, 1) AS confidence,
                CASE WHEN meta_home_prob IS NULL
                     THEN ROUND(MAX(xgboost_home_prob, 1.0 - xgboost_home_prob) * 100.0, 1)
                     ELSE ROUND(MAX(meta_home_prob, 1.0 - meta_home_prob) * 100.0, 1)
                END AS consensus,
                CASE
                    WHEN (xgboost_home_prob >= 0.5 AND home_score > away_score)
                      OR (xgboost_home_prob < 0.5 AND home_score < away_score)
                    THEN 'win' ELSE 'loss'
                END AS result,
                CASE
                    WHEN (xgboost_home_prob >= 0.5 AND home_score > away_score)
                      OR (xgboost_home_prob < 0.5 AND home_score < away_score)
                    THEN 1 ELSE -1
                END AS units
            FROM base
            WHERE xgboost_home_prob IS NOT NULL

            UNION ALL

            SELECT
                sport,
                date,
                'Sharp Consensus' AS model,
                ROUND(MAX(meta_home_prob, 1.0 - meta_home_prob) * 100.0, 1) AS confidence,
                ROUND(MAX(meta_home_prob, 1.0 - meta_home_prob) * 100.0, 1) AS consensus,
                CASE
                    WHEN (meta_home_prob >= 0.5 AND home_score > away_score)
                      OR (meta_home_prob < 0.5 AND home_score < away_score)
                    THEN 'win' ELSE 'loss'
                END AS result,
                CASE
                    WHEN (meta_home_prob >= 0.5 AND home_score > away_score)
                      OR (meta_home_prob < 0.5 AND home_score < away_score)
                    THEN 1 ELSE -1
                END AS units
            FROM base
            WHERE meta_home_prob IS NOT NULL
        )
        SELECT sport, date, model, confidence, consensus, result, units
        FROM model_rows
        WHERE 1=1
    """

    where_conditions = []
    sql_params = list(games_params)
    if req_sport:
        where_conditions.append("sport = ?")
        sql_params.append(req_sport)
    if req_model:
        where_conditions.append("model = ?")
        sql_params.append(req_model)
    if req_min_conf is not None:
        where_conditions.append("confidence >= ?")
        sql_params.append(req_min_conf)
    if req_min_consensus is not None:
        where_conditions.append("consensus >= ?")
        sql_params.append(req_min_consensus)

    final_sql = base_sql
    if where_conditions:
        final_sql += " AND " + " AND ".join(where_conditions)
    final_sql += " ORDER BY date DESC"

    try:
        conn = get_db_connection()
        try:
            meta['predictions_count'] = int(conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] or 0)
            meta['matched_results_count'] = int(conn.execute(
                """
                SELECT COUNT(*)
                FROM predictions p
                LEFT JOIN games g ON g.sport = p.sport AND g.game_id = p.game_id
                WHERE COALESCE(p.actual_home_score, g.home_score) IS NOT NULL
                  AND COALESCE(p.actual_away_score, g.away_score) IS NOT NULL
                """
            ).fetchone()[0] or 0)
        except Exception:
            pass

        logger.info(f"[perf] SQL query: {final_sql}")
        logger.info(f"[perf] SQL params: {sql_params}")

        filtered_rows_db = conn.execute(final_sql, tuple(sql_params)).fetchall()
        filtered_rows = [{
            'sport': (r['sport'] or '').upper(),
            'date': r['date'] or '',
            'model': r['model'],
            'confidence': float(r['confidence'] or 0),
            'consensus': float(r['consensus'] or 0),
            'result': r['result'],
            'units': float(r['units'] or 0),
        } for r in filtered_rows_db]

        # Keep rows payload for UI/debug, unfiltered by request parameters.
        all_rows_sql = base_sql + " ORDER BY date DESC"
        rows_out_db = conn.execute(all_rows_sql, tuple(games_params)).fetchall()
        rows_out = [{
            'sport': (r['sport'] or '').upper(),
            'date': r['date'] or '',
            'model': r['model'],
            'confidence': float(r['confidence'] or 0),
            'consensus': float(r['consensus'] or 0),
            'result': r['result'],
            'units': float(r['units'] or 0),
        } for r in rows_out_db]
        conn.close()
    except Exception as e:
        logger.exception(f"[perf] performance-data query failed: {e}")
        rows_out = []
        filtered_rows = []

    # If stored prediction joins are sparse for a selected sport/model, fall back
    # to v2 probabilities across the same last 200 completed games.
    fallback_used = False
    if req_sport and req_model and len(filtered_rows) < 20:
        try:
            conn = get_db_connection()
            fallback_games = conn.execute(
                """
                SELECT
                    date(g.game_date) AS game_date,
                    g.home_team_id,
                    g.away_team_id,
                    g.home_score,
                    g.away_score,
                    p.elo_home_prob,
                    p.logistic_home_prob,
                    p.xgboost_home_prob,
                    p.catboost_home_prob,
                    p.meta_home_prob
                FROM games g
                LEFT JOIN predictions p
                  ON UPPER(p.sport) = UPPER(g.sport)
                 AND (
                    p.game_id = g.game_id
                    OR (
                        date(p.game_date) = date(g.game_date)
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                    )
                 )
                WHERE UPPER(g.sport) = ?
                  AND g.home_score IS NOT NULL
                  AND g.away_score IS NOT NULL
                  AND g.home_score != g.away_score
                ORDER BY date(g.game_date) DESC
                LIMIT 200
                """,
                (req_sport,)
            ).fetchall()
            conn.close()

            def _f(v):
                try:
                    return float(v) if v is not None else None
                except Exception:
                    return None

            fallback_rows = []
            for g in fallback_games:
                date_key = g['game_date']
                home = g['home_team_id']
                away = g['away_team_id']
                home_score = _f(g['home_score'])
                away_score = _f(g['away_score'])
                if home_score is None or away_score is None or home_score == away_score:
                    continue

                v2 = None
                try:
                    v2 = get_v2_prediction(req_sport, home, away, date_key)
                except Exception:
                    v2 = None

                glicko2_prob = _f(v2.get('glicko2_prob')) if v2 else None
                trueskill_prob = _f(v2.get('trueskill_prob')) if v2 else None
                elo_prob = _f(g['elo_home_prob'])
                logistic_prob = _f(g['logistic_home_prob'])
                xgb_prob = _f(g['xgboost_home_prob'])
                catboost_prob = _f(g['catboost_home_prob'])
                if v2:
                    xgb_prob = _f(v2.get('xgboost_prob')) if _f(v2.get('xgboost_prob')) is not None else xgb_prob
                if elo_prob is None:
                    elo_prob = catboost_prob or glicko2_prob or xgb_prob
                if catboost_prob is None:
                    catboost_prob = glicko2_prob or elo_prob
                meta_prob = _f(g['meta_home_prob'])
                if meta_prob is None:
                    meta_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=elo_prob or 0.5)

                model_prob_map = {
                    'Grinder2': glicko2_prob if glicko2_prob is not None else catboost_prob,
                    'Takedown': trueskill_prob if trueskill_prob is not None else logistic_prob,
                    'Edge': elo_prob,
                    'XSharp': xgb_prob,
                    'Sharp Consensus': meta_prob,
                }
                prob = model_prob_map.get(req_model)
                if prob is None:
                    continue

                confidence = round(max(prob, 1.0 - prob) * 100.0, 1)
                consensus = round(max(meta_prob, 1.0 - meta_prob) * 100.0, 1) if meta_prob is not None else confidence
                if req_min_conf is not None and confidence < req_min_conf:
                    continue
                if req_min_consensus is not None and consensus < req_min_consensus:
                    continue

                home_won = home_score > away_score
                picked_home = prob >= 0.5
                was_correct = picked_home == home_won
                fallback_rows.append({
                    'sport': req_sport,
                    'date': date_key,
                    'model': req_model,
                    'confidence': confidence,
                    'consensus': consensus,
                    'result': 'win' if was_correct else 'loss',
                    'units': 1.0 if was_correct else -1.0,
                })

            if len(fallback_rows) > len(filtered_rows):
                filtered_rows = fallback_rows
                fallback_used = True
        except Exception as e:
            logger.debug(f"[perf] v2 fallback failed: {e}")

    wins = sum(1 for r in filtered_rows if r.get('result') == 'win')
    total = len(filtered_rows)
    losses = max(total - wins, 0)
    units = sum(float(r.get('units') or 0) for r in filtered_rows)
    win_pct = round((wins / total) * 100.0, 1) if total else None

    meta['rows_out_count'] = len(rows_out)
    meta['filtered_count'] = total
    meta['filters'] = {
        'model': req_model,
        'sport': req_sport,
        'min_conf': req_min_conf,
        'min_consensus': req_min_consensus,
    }
    meta['sql'] = final_sql
    meta['sql_params'] = sql_params
    meta['v2_fallback_used'] = fallback_used
    meta['message'] = 'No bets match current filters.' if total == 0 else None

    return jsonify({
        'rows': rows_out,
        'filtered_rows': filtered_rows,
        'summary': {
            'total_bets': total,
            'wins': wins,
            'losses': losses,
            'win_pct': win_pct,
            'units': units,
        },
        'meta': meta
    })


_PERF_MODEL_ORDER = ['Grinder2', 'Takedown', 'Edge', 'XSharp', 'Consensus']
_PERF_BUCKET_ORDER = [
    '85%+',
    '80-84%',
    '75-79%',
    '70-74%',
    '65-69%',
    '60-64%',
    '55-59%',
    '50-54%',
    '45-49%',
    '40-44%',
    '35-39%',
    '30-34%',
    '25-29%',
    '20-24%',
    '<20%',
]
_PERF_SPORT_OPTIONS = ['NBA', 'NHL', 'MLB', 'NFL', 'NCAAB', 'NCAAF']


# ── Frozen prediction output — exact copy from March 8 reference (NHL77FINAL.py) ──
# DO NOT modify this function. It is the reference model output as-shipped.
def _frozen_get_v2_prediction(sport, home_team, away_team, game_date=None):
    """Frozen reference: prediction output logic as of March 8 2026."""
    if not HAS_V2_SYSTEM or sport not in V2_PREDICTORS:
        return None
    try:
        predictor = V2_PREDICTORS[sport]
        game_df = pd.DataFrame([{
            'home_team': home_team,
            'away_team': away_team,
            'date': game_date or datetime.now().strftime('%Y-%m-%d')
        }])
        pred = predictor.predict(game_df)
        row = pred.iloc[0]
        return {
            'home_prob':           row['home_win_prob'],
            'away_prob':           row['away_win_prob'],
            'confidence':          row['confidence'],
            'model_agreement':     row['model_agreement'],
            'predicted_winner':    row['predicted_winner'],
            'expected_home_score': row.get('expected_home_score'),
            'expected_away_score': row.get('expected_away_score'),
            'glicko2_prob':        row.get('glicko2_prob'),
            'trueskill_prob':      row.get('trueskill_prob'),
            'xgboost_prob':        row.get('xgboost_prob'),
            'home_glicko2':        row.get('home_glicko2'),
            'away_glicko2':        row.get('away_glicko2'),
            'home_trueskill_mu':   row.get('home_trueskill_mu'),
            'away_trueskill_mu':   row.get('away_trueskill_mu'),
            'is_v2': True,
        }
    except Exception as _fe:
        logger.warning(f"[frozen_v2] {away_team} @ {home_team}: {_fe}")
        return None


def _build_performance_page_data(sport_filter: str = '', last_n: int | None = None):
    """
    Build performance using Excel-style logic:
      - Confidence bucket from picked-side confidence (max(p, 1-p) * 100)
      - Wins/Losses counted from binary correctness
      - Base set is last N UNIQUE completed games (from games table first)
    """
    where_parts = ["g.home_score IS NOT NULL", "g.away_score IS NOT NULL", "g.home_score != g.away_score"]
    params = []
    if _PERF_SPORT_OPTIONS:
        placeholders = ",".join(["?"] * len(_PERF_SPORT_OPTIONS))
        where_parts.append(f"UPPER(g.sport) IN ({placeholders})")
        params.extend(_PERF_SPORT_OPTIONS)
    if sport_filter:
        where_parts.append("UPPER(g.sport) = ?")
        params.append(sport_filter)

    game_sql = f"""
        SELECT
            UPPER(g.sport) AS sport,
            g.game_id,
            date(g.game_date) AS game_date,
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score
        FROM games g
        WHERE {' AND '.join(where_parts)}
        ORDER BY date(g.game_date) DESC, g.game_id DESC
        {('LIMIT ?' if last_n else '')}
    """
    game_params = list(params)
    if last_n:
        game_params.append(int(last_n))

    conn = get_db_connection()
    games = conn.execute(game_sql, tuple(game_params)).fetchall()

    def _flt(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def _bucket_for_conf(confidence):
        if confidence >= 85: return '85%+'
        if confidence >= 80: return '80-84%'
        if confidence >= 75: return '75-79%'
        if confidence >= 70: return '70-74%'
        if confidence >= 65: return '65-69%'
        if confidence >= 60: return '60-64%'
        if confidence >= 55: return '55-59%'
        if confidence >= 50: return '50-54%'
        if confidence >= 45: return '45-49%'
        if confidence >= 40: return '40-44%'
        if confidence >= 35: return '35-39%'
        if confidence >= 30: return '30-34%'
        if confidence >= 25: return '25-29%'
        if confidence >= 20: return '20-24%'
        return '<20%'

    # Aggregate containers
    main_rollup = {}
    sport_rows = {}
    team_rows = {}

    pred_sql_exact = """
        SELECT elo_home_prob, logistic_home_prob, xgboost_home_prob, catboost_home_prob, meta_home_prob
        FROM predictions
        WHERE UPPER(sport) = ? AND game_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """

    for g in games:
        sport = (g['sport'] or '').upper()
        game_id = g['game_id']
        date_key = g['game_date']
        home = g['home_team_id']
        away = g['away_team_id']
        hs = _flt(g['home_score'])
        aw = _flt(g['away_score'])
        if hs is None or aw is None or hs == aw:
            continue
        home_won = hs > aw

        pred = conn.execute(pred_sql_exact, (sport, game_id)).fetchone()

        elo_prob = _flt(pred['elo_home_prob']) if pred else None
        logi_prob = _flt(pred['logistic_home_prob']) if pred else None
        xgb_prob = _flt(pred['xgboost_home_prob']) if pred else None
        cat_prob = _flt(pred['catboost_home_prob']) if pred else None
        meta_prob = _flt(pred['meta_home_prob']) if pred else None

        # Always use frozen reference prediction output (March 8 model, unconditional)
        _ms = 'NCAAB' if sport == 'NCAAW' else sport
        v2 = _frozen_get_v2_prediction(_ms, home, away, date_key)
        glicko2_prob = _flt(v2.get('glicko2_prob')) if v2 else None
        trueskill_prob = _flt(v2.get('trueskill_prob')) if v2 else None
        if v2:
            if xgb_prob is None:
                xgb_prob = _flt(v2.get('xgboost_prob'))
            meta_prob = _flt(v2.get('home_prob')) if _flt(v2.get('home_prob')) is not None else meta_prob
        if elo_prob is None:
            elo_prob = cat_prob or glicko2_prob or xgb_prob
        if cat_prob is None:
            cat_prob = glicko2_prob or elo_prob
        if logi_prob is None:
            logi_prob = trueskill_prob
        if meta_prob is None:
            meta_prob = _compute_ensemble_prob(
                glicko2_prob,
                trueskill_prob,
                xgb_prob,
                elo_prob,
                fallback=elo_prob or 0.5
            )

        model_prob = {
            'Grinder2': glicko2_prob if glicko2_prob is not None else cat_prob,
            'Takedown': trueskill_prob if trueskill_prob is not None else logi_prob,
            'Edge': elo_prob,
            'XSharp': xgb_prob,
            'Consensus': meta_prob,
        }

        for model in _PERF_MODEL_ORDER:
            p = model_prob.get(model)
            if p is None:
                continue
            # Match CSV/Excel workflow exactly: bucket on rounded confidence value.
            confidence = round(max(p, 1.0 - p) * 100.0, 1)
            bucket = _bucket_for_conf(confidence)
            if bucket not in _PERF_BUCKET_ORDER:
                continue
            picked_team = home if p >= 0.5 else away
            correct = 1 if ((p >= 0.5) == home_won) else 0

            main_key = (model, bucket)
            if main_key not in main_rollup:
                main_rollup[main_key] = {'total': 0, 'wins': 0, 'losses': 0}
            main_rollup[main_key]['total'] += 1
            main_rollup[main_key]['wins'] += correct
            main_rollup[main_key]['losses'] += (1 - correct)

            sport_key = (sport, model, bucket)
            if sport_key not in sport_rows:
                sport_rows[sport_key] = {'total': 0, 'wins': 0, 'losses': 0}
            sport_rows[sport_key]['total'] += 1
            sport_rows[sport_key]['wins'] += correct
            sport_rows[sport_key]['losses'] += (1 - correct)

            # Team-specific rollup (picked team + confidence bucket)
            team_key = (sport, picked_team, model, bucket)
            if team_key not in team_rows:
                team_rows[team_key] = {'total': 0, 'wins': 0, 'losses': 0}
            team_rows[team_key]['total'] += 1
            team_rows[team_key]['wins'] += correct
            team_rows[team_key]['losses'] += (1 - correct)

    conn.close()

    def _cell(data):
        if not data or data['total'] <= 0:
            return None
        total = data['total']
        wins = data['wins']
        losses = data['losses']
        win_pct = round((wins / total) * 100.0, 1) if total else None
        return {'n': total, 'wins': wins, 'losses': losses, 'win_pct': win_pct}

    main_table = {b: {m: _cell(main_rollup.get((m, b))) for m in _PERF_MODEL_ORDER} for b in _PERF_BUCKET_ORDER}
    sports_present = sorted({k[0] for k in sport_rows.keys() if k[0]})
    sport_tables = {
        sport: {b: {m: _cell(sport_rows.get((sport, m, b))) for m in _PERF_MODEL_ORDER} for b in _PERF_BUCKET_ORDER}
        for sport in sports_present
    }

    # Team cards: one row per team with per-model record and win %
    team_model_rollup = {}
    for (sport, team, model, _bucket), vals in team_rows.items():
        key = (sport, team, model)
        if key not in team_model_rollup:
            team_model_rollup[key] = {'total': 0, 'wins': 0, 'losses': 0}
        team_model_rollup[key]['total'] += vals['total']
        team_model_rollup[key]['wins'] += vals['wins']
        team_model_rollup[key]['losses'] += vals['losses']

    by_team = {}
    for (sport, team, model), vals in team_model_rollup.items():
        if sport_filter and sport != sport_filter:
            continue
        team_key = (sport, team)
        if team_key not in by_team:
            by_team[team_key] = {'sport': sport, 'team': team, 'models': {}, 'total_n': 0}
        n = vals['total']
        w = vals['wins']
        l = vals['losses']
        by_team[team_key]['models'][model] = {
            'n': n,
            'wins': w,
            'losses': l,
            'win_pct': round((w / n) * 100.0, 1) if n else 0.0,
        }
        by_team[team_key]['total_n'] += n

    team_chart_rows = []
    for _, row in by_team.items():
        ordered_models = {}
        for m in _PERF_MODEL_ORDER:
            ordered_models[m] = row['models'].get(m)
        row['models'] = ordered_models
        team_chart_rows.append(row)

    team_chart_rows.sort(key=lambda x: (-x['total_n'], x['team']))
    team_chart_rows = team_chart_rows[:120]
    return main_table, sport_tables, team_chart_rows


@app.route('/player-props')
def player_props_page():
    """Player props page — wired into the main app via /player-props-api/ routes."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    return render_template('player_props.html')


@app.route('/player-props/assets/<path:asset_path>')
def player_props_assets(asset_path):
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    assets_dir = _os.path.join(_BASE_DIR, 'standalone-player-props', 'frontend', 'dist', 'assets')
    return send_from_directory(assets_dir, asset_path)


@app.route('/player-props-api/leagues')
def player_props_api_leagues():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    try:
        _, config_mod = _load_props_modules()
        return jsonify({'leagues': list(getattr(config_mod, 'SUPPORTED_LEAGUES', []))})
    except Exception as exc:
        return jsonify({'detail': f'Props API unavailable: {exc}'}), 503


@app.route('/player-props-api/players')
def player_props_api_players():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    league = (request.args.get('league') or '').strip().upper()
    try:
        engine_mod, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        data = engine_mod.get_league_data(league)
        resp = {'league': league, 'count': len(data.get('players', [])), 'items': data.get('players', [])}
        if 'excluded_players' in data:
            resp['excluded_players'] = data['excluded_players']
        return jsonify(resp)
    except Exception as exc:
        return jsonify({'detail': str(exc)}), 500


@app.route('/player-props-api/props')
def player_props_api_props():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    league = (request.args.get('league') or '').strip().upper()
    prop_type = (request.args.get('prop_type') or '').strip() or None
    side = (request.args.get('side') or '').strip() or None
    min_ev_raw = (request.args.get('min_ev') or '').strip()
    min_ev = None
    if min_ev_raw:
        try:
            min_ev = float(min_ev_raw)
        except Exception:
            min_ev = None
    try:
        engine_mod, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        data = engine_mod.get_league_data(league)
        rows = engine_mod.filter_props(data.get('props', []), prop_type=prop_type, side=side, min_ev=min_ev)
        resp = {'league': league, 'count': len(rows), 'items': rows}
        if 'excluded_players' in data:
            resp['excluded_players'] = data['excluded_players']
        if 'model_variance' in data:
            resp['model_variance'] = data['model_variance']
        if 'sanity_flags' in data:
            resp['sanity_flags'] = data['sanity_flags']
        return jsonify(resp)
    except Exception as exc:
        return jsonify({'detail': str(exc)}), 500


def _grade_and_store_props(league: str, for_date_str: str):
    """Grade props for a given date using the engine and persist to DB."""
    engine_mod, _ = _load_props_modules()
    graded = engine_mod.get_league_results(league, for_date=for_date_str)
    rows = graded.get('items') or []
    if not rows:
        return
    conn = get_db_connection()
    try:
        for r in rows:
            conn.execute(
                '''INSERT OR REPLACE INTO player_prop_results
                   (league, result_date, player_name, team, prop_type, pick, line, projection, actual, result)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (league, for_date_str,
                 r.get('player_name'), r.get('team'), r.get('prop_type'),
                 r.get('pick'), r.get('line'), r.get('projection'),
                 r.get('actual'), r.get('result'))
            )
        conn.commit()
    finally:
        conn.close()


def _query_prop_results(league: str, for_date: str | None = None):
    """Return items + summary for a date (default yesterday) + cumulative stats."""
    from datetime import date as _date, timedelta as _td
    today = datetime.now(ZoneInfo("America/New_York")).date()
    if for_date:
        try:
            target = _date.fromisoformat(for_date)
        except Exception:
            target = today - _td(days=1)
    else:
        target = today - _td(days=1)
    target_str = str(target)

    # Try to grade+store today's target if not already stored
    try:
        _grade_and_store_props(league, target_str)
    except Exception:
        pass

    conn = get_db_connection()
    try:
        # Night rows for display
        rows = conn.execute(
            'SELECT * FROM player_prop_results WHERE league=? AND result_date=? ORDER BY player_name, prop_type',
            (league, target_str)
        ).fetchall()
        items = [dict(r) for r in rows]

        # Summary for the target date
        def _tally(rr):
            hits = sum(1 for r in rr if r['result'] == 'HIT')
            misses = sum(1 for r in rr if r['result'] == 'MISS')
            by_pt = {}
            for r in rr:
                pt = r['prop_type']
                b = by_pt.setdefault(pt, {'wins': 0, 'losses': 0})
                if r['result'] == 'HIT': b['wins'] += 1
                else: b['losses'] += 1
            return {'wins': hits, 'losses': misses, 'by_prop_type': by_pt}

        night_summary = _tally(items)

        # Last 7 days
        week_start = str(target - _td(days=6))
        week_rows = [dict(r) for r in conn.execute(
            'SELECT * FROM player_prop_results WHERE league=? AND result_date BETWEEN ? AND ?',
            (league, week_start, target_str)
        ).fetchall()]
        week_summary = _tally(week_rows)

        # All-time totals + by prop type
        agg = conn.execute(
            "SELECT MIN(result_date) as earliest, "
            "SUM(result='HIT') as hits, SUM(result='MISS') as misses "
            "FROM player_prop_results WHERE league=?", (league,)
        ).fetchone()
        season_hits    = agg['hits']    or 0
        season_misses  = agg['misses']  or 0
        tracking_since = agg['earliest'] or None

        # All-time breakdown by prop type
        pt_rows = conn.execute(
            "SELECT prop_type, "
            "SUM(result='HIT') as hits, SUM(result='MISS') as misses "
            "FROM player_prop_results WHERE league=? "
            "GROUP BY prop_type ORDER BY (hits+misses) DESC",
            (league,)
        ).fetchall()
        season_by_prop = {r['prop_type']: {'wins': r['hits'] or 0, 'losses': r['misses'] or 0} for r in pt_rows}

        return {
            'league': league,
            'result_date': target_str,
            'count': len(items),
            'items': items,
            'summary': {
                'overall': {'wins': night_summary['wins'], 'losses': night_summary['losses']},
                'by_prop_type': night_summary['by_prop_type'],
            },
            'week_summary': {'wins': week_summary['wins'], 'losses': week_summary['losses']},
            'season_summary': {'wins': season_hits, 'losses': season_misses},
            'season_by_prop': season_by_prop,
            'tracking_since': tracking_since,
        }
    finally:
        conn.close()


@app.route('/player-props-api/results')
def player_props_api_results():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    league = (request.args.get('league') or '').strip().upper()
    for_date = (request.args.get('date') or '').strip() or None
    try:
        engine_mod, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        return jsonify(_query_prop_results(league, for_date))
    except Exception as exc:
        return jsonify({'detail': str(exc)}), 500


@app.route('/player-props-api/diagnostics')
def player_props_api_diagnostics():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    league = (request.args.get('league') or 'NBA').strip().upper()
    try:
        engine_mod, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        return jsonify(engine_mod.get_diagnostics(league))
    except Exception as exc:
        return jsonify({'detail': str(exc)}), 500


@app.route('/performance')
def performance_page():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    sport = (request.args.get('sport') or '').strip().upper()
    if sport not in _PERF_SPORT_OPTIONS:
        sport = ''
    last_n_raw = (request.args.get('last_n') or '').strip().lower()
    last_n = None
    if last_n_raw in ('50', '100', '200'):
        last_n = int(last_n_raw)

    main_table, sport_tables, team_chart_rows = _build_performance_page_data(sport_filter=sport, last_n=last_n)
    return render_template(
        'performance.html',
        page='performance',
        selected_sport=sport,
        selected_last_n=(str(last_n) if last_n else ''),
        sport_options=_PERF_SPORT_OPTIONS,
        model_order=_PERF_MODEL_ORDER,
        bucket_order=_PERF_BUCKET_ORDER,
        main_table=main_table,
        sport_tables=sport_tables,
        team_chart_rows=team_chart_rows,
    )


@app.route('/performance/audit.csv')
def performance_audit_csv():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    sport = (request.args.get('sport') or '').strip().upper()
    if sport not in _PERF_SPORT_OPTIONS:
        sport = ''
    try:
        last_n = int((request.args.get('last_n') or '50').strip())
    except Exception:
        last_n = 50
    last_n = max(1, min(500, last_n))

    where_parts = [
        "g.home_score IS NOT NULL",
        "g.away_score IS NOT NULL",
        "g.home_score != g.away_score",
    ]
    params = []
    if _PERF_SPORT_OPTIONS:
        placeholders = ",".join(["?"] * len(_PERF_SPORT_OPTIONS))
        where_parts.append(f"UPPER(g.sport) IN ({placeholders})")
        params.extend(_PERF_SPORT_OPTIONS)
    if sport:
        where_parts.append("UPPER(g.sport) = ?")
        params.append(sport)

    sql = f"""
        SELECT
            UPPER(g.sport) AS sport,
            g.game_id,
            date(g.game_date) AS game_date,
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score
        FROM games g
        WHERE {' AND '.join(where_parts)}
        ORDER BY date(g.game_date) DESC, g.game_id DESC
        LIMIT ?
    """
    params.append(last_n)
    conn = get_db_connection()
    rows = conn.execute(sql, tuple(params)).fetchall()

    def _flt(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    out = io.StringIO()
    writer = csv.writer(out)
    # Column layout intentionally matches your Excel formulas:
    # H = picked_team, I = confidence_pct, K = correct_binary
    writer.writerow([
        'sport',                 # A
        'game_date',             # B
        'game_id',               # C
        'model',                 # D
        'away_team',             # E
        'home_team',             # F
        'actual_winner',         # G
        'picked_team',           # H
        'confidence_pct',        # I
        'confidence_bucket',     # J
        'correct_binary',        # K
        'away_score',            # L
        'home_score',            # M
        'model_home_prob',       # N
        'prob_source',           # O
    ])

    pred_sql_exact = """
        SELECT
            elo_home_prob,
            logistic_home_prob,
            xgboost_home_prob,
            catboost_home_prob,
            meta_home_prob
        FROM predictions
        WHERE UPPER(sport) = ? AND game_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
    """

    def _bucket_for_conf(confidence):
        if confidence >= 85: return '85%+'
        if confidence >= 80: return '80-84%'
        if confidence >= 75: return '75-79%'
        if confidence >= 70: return '70-74%'
        if confidence >= 65: return '65-69%'
        if confidence >= 60: return '60-64%'
        if confidence >= 55: return '55-59%'
        if confidence >= 50: return '50-54%'
        if confidence >= 45: return '45-49%'
        if confidence >= 40: return '40-44%'
        if confidence >= 35: return '35-39%'
        if confidence >= 30: return '30-34%'
        if confidence >= 25: return '25-29%'
        if confidence >= 20: return '20-24%'
        return '<20%'

    for r in rows:
        row_sport = (r['sport'] or '').upper()
        home = r['home_team_id']
        away = r['away_team_id']
        hs = _flt(r['home_score'])
        aw = _flt(r['away_score'])
        if hs is None or aw is None or hs == aw:
            continue
        home_won = hs > aw
        actual_winner = home if home_won else away

        pred = conn.execute(pred_sql_exact, (row_sport, r['game_id'])).fetchone()
        elo_prob = _flt(pred['elo_home_prob']) if pred else None
        logi_prob = _flt(pred['logistic_home_prob']) if pred else None
        xgb_prob = _flt(pred['xgboost_home_prob']) if pred else None
        cat_prob = _flt(pred['catboost_home_prob']) if pred else None
        meta_prob = _flt(pred['meta_home_prob']) if pred else None

        # Always use frozen reference prediction output (March 8 model, unconditional)
        _model_sport = 'NCAAB' if row_sport == 'NCAAW' else row_sport
        v2 = _frozen_get_v2_prediction(_model_sport, home, away, r['game_date'])
        glicko2_prob = _flt(v2.get('glicko2_prob')) if v2 else None
        trueskill_prob = _flt(v2.get('trueskill_prob')) if v2 else None
        if v2:
            if xgb_prob is None:
                xgb_prob = _flt(v2.get('xgboost_prob'))
            # Use calibrated ensemble from frozen model directly (March 8 behaviour)
            meta_prob = _flt(v2.get('home_prob')) if _flt(v2.get('home_prob')) is not None else meta_prob
        if elo_prob is None:
            elo_prob = cat_prob or glicko2_prob or xgb_prob
        if cat_prob is None:
            cat_prob = glicko2_prob or elo_prob
        if logi_prob is None:
            logi_prob = trueskill_prob
        if meta_prob is None:
            meta_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=elo_prob or 0.5)

        model_prob = {
            'grinder2': glicko2_prob if glicko2_prob is not None else cat_prob,
            'takedown': trueskill_prob if trueskill_prob is not None else logi_prob,
            'edge': elo_prob,
            'xsharp': xgb_prob,
            'consensus': meta_prob,
        }

        for model_name in ['grinder2', 'takedown', 'edge', 'xsharp', 'consensus']:
            prob = model_prob.get(model_name)
            if prob is None:
                continue
            pick_home = prob >= 0.5
            picked_team = home if pick_home else away
            confidence = round(max(prob, 1.0 - prob) * 100.0, 1)
            bucket = _bucket_for_conf(confidence)
            correct = 1 if picked_team == actual_winner else 0
            source = 'stored'
            if model_name == 'grinder2' and glicko2_prob is not None:
                source = 'v2_glicko2'
            elif model_name == 'takedown' and trueskill_prob is not None:
                source = 'v2_trueskill'
            elif model_name == 'xsharp' and v2 and (pred is None or _flt(pred['xgboost_home_prob']) is None):
                source = 'v2_xgboost'
            elif model_name == 'consensus' and (pred is None or _flt(pred['meta_home_prob']) is None):
                source = 'computed_ensemble'

            writer.writerow([
                row_sport,                 # A
                r['game_date'],            # B
                r['game_id'],              # C
                model_name,                # D
                away,                      # E
                home,                      # F
                actual_winner,             # G
                picked_team,               # H
                confidence,                # I
                bucket,                    # J
                correct,                   # K
                int(aw),                   # L
                int(hs),                   # M
                round(prob, 6),            # N
                source,                    # O
            ])

    csv_body = out.getvalue()
    out.close()
    conn.close()
    file_sport = sport if sport else 'ALL'
    return Response(
        csv_body,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="performance_audit_{file_sport}_{last_n}.csv"'},
    )

@app.route('/picks/export.csv')
def picks_export_csv():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    sport = (request.args.get('sport') or '').strip().upper() or None
    try:
        conn = get_db_connection()
        query = '''
            SELECT g.game_date, g.sport, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.win_probability,
                   bl.spread AS market_spread, bl.total AS market_total
            FROM games g
            LEFT JOIN predictions p ON (
                p.sport = g.sport AND (
                    p.game_id = g.game_id OR (
                        date(p.game_date) = date(g.game_date)
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                    )
                )
            )
            LEFT JOIN betting_lines bl ON bl.game_id = g.game_id
        '''
        params = []
        if sport:
            query += ' WHERE g.sport = ?'
            params.append(sport)
        query += ' ORDER BY g.game_date DESC LIMIT 500'
        rows = conn.execute(query, params).fetchall()
        conn.close()
    except Exception as exc:
        return Response(f'Export failed: {exc}', status=500, mimetype='text/plain')
    import io as _io2, csv as _csv2
    out = _io2.StringIO()
    w = _csv2.writer(out)
    w.writerow(['date','sport','home_team','away_team','home_score','away_score','result',
                'glicko2_prob','trueskill_prob','xgb_prob','ensemble_prob',
                'ml_pick','ml_correct','market_spread','market_total'])
    _picks_v2_cache = {}
    for r in rows:
        hs = _to_float_safe(r['home_score'])
        aws = _to_float_safe(r['away_score'])
        result = ''
        if hs is not None and aws is not None:
            result = 'home_win' if hs > aws else ('away_win' if aws > hs else 'draw')
        _ps = (r['sport'] or '').upper()
        _pm = 'NCAAB' if _ps == 'NCAAW' else _ps
        _ck = f"{_pm}|{r['home_team_id']}|{r['away_team_id']}|{r['game_date']}"
        if _ck not in _picks_v2_cache:
            _picks_v2_cache[_ck] = _frozen_get_v2_prediction(_pm, r['home_team_id'], r['away_team_id'], r['game_date'])
        _v2 = _picks_v2_cache[_ck]
        _g2  = round(_v2['glicko2_prob']  * 100, 1) if _v2 and _v2.get('glicko2_prob')  is not None else None
        _ts  = round(_v2['trueskill_prob'] * 100, 1) if _v2 and _v2.get('trueskill_prob') is not None else None
        _xgb = round((_v2.get('xgboost_prob') or 0) * 100, 1) if _v2 else None
        _ens = round(_v2['home_prob'] * 100, 1) if _v2 and _v2.get('home_prob') is not None else _to_float_safe(r['win_probability'])
        _ens_raw = (_v2['home_prob'] if _v2 and _v2.get('home_prob') is not None else _to_float_safe(r['win_probability'])) or 0.5
        ml_pick = 'home' if _ens_raw >= 0.5 else 'away'
        ml_correct = ''
        if result:
            ml_correct = 'yes' if (ml_pick == 'home' and result == 'home_win') or (ml_pick == 'away' and result == 'away_win') else 'no'
        w.writerow([r['game_date'], r['sport'], r['home_team_id'], r['away_team_id'],
                    r['home_score'], r['away_score'], result,
                    _g2, _ts, _xgb, _ens, ml_pick, ml_correct,
                    r['market_spread'], r['market_total']])
    body = out.getvalue()
    out.close()
    fname = f"picks_export_{sport or 'ALL'}.csv"
    return Response(body, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


@app.route('/results/export.csv')
def results_export_csv():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    sport = (request.args.get('sport') or '').strip().upper() or None
    date_from = (request.args.get('from') or '').strip() or None
    date_to = (request.args.get('to') or '').strip() or None
    try:
        conn = get_db_connection()
        where_clauses = ["g.home_score IS NOT NULL AND g.away_score IS NOT NULL"]
        params = []
        if sport:
            where_clauses.append("g.sport = ?")
            params.append(sport)
        if date_from:
            where_clauses.append("date(g.game_date) >= date(?)")
            params.append(date_from)
        if date_to:
            where_clauses.append("date(g.game_date) <= date(?)")
            params.append(date_to)
        where_sql = ' AND '.join(where_clauses)
        query = f'''
            SELECT g.game_date, g.sport, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.win_probability, p.elo_home_prob, p.xgboost_home_prob,
                   p.glicko2_home_prob, p.trueskill_home_prob,
                   bl.spread AS market_spread, bl.total AS market_total,
                   bl.home_ml, bl.away_ml
            FROM games g
            LEFT JOIN predictions p ON (
                p.sport = g.sport AND (
                    p.game_id = g.game_id OR (
                        date(p.game_date) = date(g.game_date)
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                    )
                )
            )
            LEFT JOIN betting_lines bl ON bl.game_id = g.game_id
            WHERE {where_sql}
            ORDER BY g.game_date DESC
            LIMIT 2000
        '''
        rows = conn.execute(query, params).fetchall()
        conn.close()
    except Exception as exc:
        return Response(f'Export failed: {exc}', status=500, mimetype='text/plain')
    import io as _io3, csv as _csv3
    out = _io3.StringIO()
    w = _csv3.writer(out)
    w.writerow([
        'date', 'sport', 'home_team', 'away_team',
        'home_score', 'away_score', 'winner',
        'ml_pick', 'ml_correct',
        'market_spread', 'ats_cover', 'market_total', 'ou_result',
        'ensemble_prob', 'glicko2_prob', 'trueskill_prob', 'xgb_prob',
        'home_ml', 'away_ml',
    ])
    _res_v2_cache = {}
    for r in rows:
        hs = _to_float_safe(r['home_score'])
        aws = _to_float_safe(r['away_score'])
        if hs is None or aws is None:
            continue
        winner = 'home' if hs > aws else ('away' if aws > hs else 'draw')
        _rs = (r['sport'] or '').upper()
        _rm = 'NCAAB' if _rs == 'NCAAW' else _rs
        _rk = f"{_rm}|{r['home_team_id']}|{r['away_team_id']}|{r['game_date']}"
        if _rk not in _res_v2_cache:
            _res_v2_cache[_rk] = _frozen_get_v2_prediction(_rm, r['home_team_id'], r['away_team_id'], r['game_date'])
        _v2 = _res_v2_cache[_rk]
        _ens_raw = (_v2['home_prob'] if _v2 and _v2.get('home_prob') is not None else _to_float_safe(r['win_probability'])) or 0.5
        _ens   = round(_ens_raw * 100, 1)
        _g2    = round(_v2['glicko2_prob']  * 100, 1) if _v2 and _v2.get('glicko2_prob')  is not None else None
        _ts    = round(_v2['trueskill_prob'] * 100, 1) if _v2 and _v2.get('trueskill_prob') is not None else None
        _xgb   = round((_v2.get('xgboost_prob') or 0) * 100, 1) if _v2 else None
        ml_pick = 'home' if _ens_raw >= 0.5 else 'away'
        ml_correct = 'yes' if ml_pick == winner else ('push' if winner == 'draw' else 'no')
        spread = _to_float_safe(r['market_spread'])
        ats_cover = ''
        if spread is not None:
            margin = hs - aws
            if margin + spread > 0:
                ats_cover = 'home_covered'
            elif margin + spread < 0:
                ats_cover = 'away_covered'
            else:
                ats_cover = 'push'
        total = _to_float_safe(r['market_total'])
        ou_result = ''
        if total is not None:
            combined = hs + aws
            if combined > total:
                ou_result = 'over'
            elif combined < total:
                ou_result = 'under'
            else:
                ou_result = 'push'
        w.writerow([
            r['game_date'], r['sport'], r['home_team_id'], r['away_team_id'],
            hs, aws, winner,
            ml_pick, ml_correct,
            spread, ats_cover, total, ou_result,
            _ens, _g2, _ts, _xgb,
            r['home_ml'], r['away_ml'],
        ])
    body = out.getvalue()
    out.close()
    sport_tag = sport or 'ALL'
    fname = f"results_export_{sport_tag}.csv"
    return Response(body, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


@app.route('/teams/<slug>')
def team_lookup(slug):
    """Team slug route (Next.js-style equivalent) -> best sport page."""
    team = _TEAM_DIRECTORY.get((slug or '').lower())
    if team:
        route = _SPORT_TO_ROUTE.get((team.get('sport') or '').upper())
        if route:
            return redirect(route)
    return redirect(url_for('landing_page'))

@app.route('/search')
def site_search():
    """No-JS fallback: redirect to best-matching page."""
    payload = _build_search_payload(request.args.get('query', ''))
    if payload.get('suggested_route'):
        return redirect(payload['suggested_route'])
    return redirect(url_for('landing_page'))

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


@app.route('/llms.txt')
def llms_txt():
    body = """# predictionlab.io

> AI-powered sports betting picks and probability-based projections.

## About
- Brand: predictionlab.io
- Parent organization: GoodsandMore Inc. (Canada)
- URL: https://predictionlab.io
- Contact: nmesghali@gmail.com

## What We Offer
- Free daily moneyline picks
- Premium spread, totals, and score projections
- Multi-model AI consensus and transparent tracking

## Core Pages
- Home: https://predictionlab.io/
- Daily report: https://predictionlab.io/daily-report
- Plans: https://predictionlab.io/plans
- AI picks today: https://predictionlab.io/ai-sports-betting-picks-today
- What are AI picks: https://predictionlab.io/what-are-ai-sports-betting-picks
- Model vs sportsbooks: https://predictionlab.io/our-model-vs-sportsbooks
- Privacy: https://predictionlab.io/privacy
- Terms: https://predictionlab.io/terms

## Notes
- Picks are informational and educational, not guaranteed outcomes.
- Sports betting involves risk and variance.
"""
    return Response(body, mimetype='text/plain')


@app.route('/ai.txt')
def ai_txt():
    body = """User-agent: *
Allow: /

# AI discovery
LLMs: https://predictionlab.io/llms.txt
Sitemap: https://predictionlab.io/sitemap.xml

# Canonical contact for AI indexing
Contact: nmesghali@gmail.com
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
    urls.append((_SITE_DOMAIN + '/llms.txt', 'monthly', '0.2'))
    urls.append((_SITE_DOMAIN + '/ai.txt', 'monthly', '0.2'))
    urls.append((_SITE_DOMAIN + '/ai-sports-betting-picks-today', 'weekly', '0.7'))
    urls.append((_SITE_DOMAIN + '/what-are-ai-sports-betting-picks', 'weekly', '0.7'))
    urls.append((_SITE_DOMAIN + '/our-model-vs-sportsbooks', 'weekly', '0.7'))
    urls.append((_SITE_DOMAIN + '/privacy', 'monthly', '0.3'))
    urls.append((_SITE_DOMAIN + '/terms', 'monthly', '0.3'))
    urls.append((_SITE_DOMAIN + '/responsible-gaming', 'monthly', '0.4'))
    urls.append((_SITE_DOMAIN + '/login', 'monthly', '0.4'))
    urls.append((_SITE_DOMAIN + '/signup', 'monthly', '0.4'))

    urlset = "\n".join(
        f'<url><loc>{loc}</loc><lastmod>{today}</lastmod><changefreq>{freq}</changefreq><priority>{prio}</priority></url>'
        for loc, freq, prio in urls
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urlset}\n</urlset>'
    return Response(xml, mimetype='application/xml')

# ── Promo (screenshot-friendly; not indexed) ──────────────────────────────────

PROMO_TOP_PICKS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex,nofollow">
    <title>Today&apos;s top projections — predictionlab.io</title>
    <link rel="icon" href="/static/pl-logo.svg" type="image/svg+xml">
    <style>
        * { box-sizing: border-box; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; color: #0f172a; padding: 28px 18px 40px; }
        h1 { text-align: center; font-size: 1.35rem; font-weight: 900; margin: 0 0 6px; letter-spacing: -0.02em; }
        .sub { text-align: center; font-size: 0.82rem; color: #64748b; margin: 0 0 22px; }
        .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; max-width: 820px; margin: 0 auto; }
        @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }
        .card { background: #fff; border: 1px solid rgba(15,23,42,0.14); border-radius: 14px; padding: 16px 16px 14px; box-shadow: 0 6px 20px rgba(15,23,42,0.08); }
        .sport { font-size: 0.65rem; color: #f59e0b; text-transform: uppercase; font-weight: 800; letter-spacing: 0.55px; margin-bottom: 8px; }
        .match { font-weight: 800; font-size: 0.98rem; line-height: 1.35; margin-bottom: 10px; }
        .row { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
        .pick { color: #00C076; font-weight: 800; font-size: 0.88rem; }
        .pct { font-weight: 800; font-size: 0.95rem; }
        .ml { font-size: 0.74rem; color: #64748b; font-weight: 600; }
        .foot { text-align: center; margin-top: 22px; font-size: 0.78rem; color: #94a3b8; }
        .foot a { color: #00529B; font-weight: 700; text-decoration: none; }
        .empty { text-align: center; max-width: 420px; margin: 40px auto; color: #64748b; font-size: 0.95rem; }
    </style>
</head>
<body>
    <h1>Top value picks today</h1>
    <p class="sub">Moneyline — ranked for edge &amp; model agreement</p>
    {% if picks %}
    <div class="grid">
        {% for tp in picks[:4] %}
        {% set _disp_pct = tp.prob if tp.prob >= 50 else (100 - tp.prob)|round(1) %}
        <div class="card">
            <div class="sport">{{ tp.sport }}</div>
            <div class="match">{{ tp.away }} <span style="color:#94a3b8;font-weight:600;">vs</span> {{ tp.home }}</div>
            <div class="row">
                <span class="pick">▶ {{ tp.pick }}</span>
                <span class="pct">{{ _disp_pct }}%</span>
                <span class="ml">Moneyline</span>
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <p class="empty">No top picks loaded yet. Check back after today&rsquo;s predictions are published.</p>
    {% endif %}
    <div class="foot"><a href="https://predictionlab.io/">predictionlab.io</a></div>
</body>
</html>"""


@app.route('/promo/top-picks-today')
def promo_top_picks_today():
    """Single tab with all four top value picks — for screenshots and ads."""
    log_site_visit('/promo/top-picks-today')
    picks = build_todays_top_picks()
    return render_template_string(PROMO_TOP_PICKS_TEMPLATE, picks=picks)


# ── SEO picks routes ──────────────────────────────────────────────────────────

@app.route('/<slug>')
def seo_picks_page(slug):
    """Handle SEO-friendly URLs like /nhl-picks, /nba-picks, /nhl-results, etc."""
    # Check picks slugs
    sport = _SEO_SLUG_TO_SPORT.get(slug)
    if sport:
        try:
            return sport_predictions(sport)
        except Exception as _seo_pick_err:
            logger.exception(f"seo_picks_page fallback for {slug}: {_seo_pick_err}")
            return _predictions_fallback_page(sport)
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
    try:
        return sport_predictions(sport, filter_date=target_date)
    except Exception as _seo_daily_err:
        logger.exception(f"seo_daily_picks fallback for {slug}-{target_date}: {_seo_daily_err}")
        return _predictions_fallback_page(sport, filter_date=target_date)


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
    now_ts = _time.time()
    if (
        _DAILY_REPORT_CACHE.get('html')
        and _DAILY_REPORT_CACHE.get('date') == report_date
        and (now_ts - _DAILY_REPORT_CACHE.get('ts', 0)) < _DAILY_REPORT_TTL
    ):
        return _DAILY_REPORT_CACHE['html']

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
    _daily_today = datetime.now()
    for sport_key in ['NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'SOCCER']:
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        if sport_key not in SPORTS:
            continue
        # Daily report must only include active in-season sports.
        _status, _is_live = get_season_status(sport_key, today=_daily_today)
        if not _is_live:
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
                    'glicko2_correct': (glicko2_prob >= 0.5) == home_won if glicko2_prob is not None and home_won is not None else None,
                    'trueskill_correct': (trueskill_prob >= 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
                    'elo_correct': (elo_prob >= 0.5) == home_won if home_won is not None else None,
                    'xgb_correct': (xgb_prob >= 0.5) == home_won if home_won is not None else None,
                    'ens_correct': (ens_prob >= 0.5) == home_won if home_won is not None else None,
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
            _model_payload = []
            for mk in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                mt = tally.get(mk, {}) or {}
                total_m = mt.get('total', 0) or 0
                correct_m = mt.get('correct', 0) or 0
                _model_payload.append({
                    'label': mk.upper(),
                    'acc': f"{mt.get('accuracy', 0)}%" if total_m > 0 else "—",
                    'record': f"{correct_m}-{max(total_m - correct_m, 0)}" if total_m > 0 else "",
                })
            _daily_payload = {
                'type': 'daily-report',
                'sport_name': SPORTS[sport_key]['name'],
                'report_display': report_display,
                'games': tally.get('games', 0),
                'models': _model_payload,
                'spread': {'label': f"{tally.get('spread', {}).get('accuracy', 0)}%" if (tally.get('spread', {}).get('total', 0) or 0) > 0 else ''},
                'ou': {'label': f"{tally.get('total_ou', {}).get('accuracy', 0)}%" if (tally.get('total_ou', {}).get('total', 0) or 0) > 0 else ''},
            }
            _daily_token = _register_share_image(_daily_payload)
            sport_tallies.append({
                'sport': sport_key,
                'info': SPORTS[sport_key],
                'tally': tally,
                'share_image_src': url_for('share_daily_report_image', token=_daily_token, fmt='jpg'),
                'share_image_view_url': url_for('share_daily_report_view', token=_daily_token),
            })
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

    share_text = f"predictionlab.io Daily Report — {report_display}%0A"
    ens = agg_models.get('ensemble', {})
    if ens.get('total', 0) > 0:
        share_text += f"Consensus: {ens['accuracy']}% ({ens['correct']}-{ens['total'] - ens['correct']})%0A"
    share_text += f"{total_games} games graded%0Ahttps://predictionlab.io/daily-report"

    rendered = render_template_string(DAILY_REPORT_TEMPLATE,
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
    _DAILY_REPORT_CACHE.update({'ts': _time.time(), 'date': report_date, 'html': rendered})
    return rendered


@app.route('/share/predictions/<token>.<fmt>')
def share_predictions_image(token, fmt):
    fmt = (fmt or '').lower()
    if fmt not in ('jpg', 'jpeg', 'png'):
        return "Unsupported format", 400
    entry = _get_share_cache_entry(token)
    if not entry:
        return "Image not found", 404
    payload = entry.get('payload') or {}
    if payload.get('type') != 'predictions':
        return "Image not found", 404
    img_bytes, mimetype = _render_predictions_share_image(payload, fmt)
    if not img_bytes:
        return "Image engine unavailable", 503
    return Response(
        img_bytes,
        mimetype=mimetype,
        headers={
            'Cache-Control': 'private, max-age=300',
            'Content-Disposition': 'inline; filename="picks.jpg"',
        },
    )


@app.route('/share/predictions/view/<token>')
def share_predictions_view(token: str):
    """Minimal full-view page: image only (no site chrome in the document). For TikTok, still prefer Download and upload from Photos to avoid the browser address bar in recordings."""
    if not _SHARE_TOKEN_RE.match(token or ''):
        abort(404)
    entry = _get_share_cache_entry(token)
    if not entry or (entry.get('payload') or {}).get('type') != 'predictions':
        abort(404)
    img_href = url_for('share_predictions_image', token=token, fmt='jpg')
    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=4">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>\u200b</title>'
        '<style>html,body{margin:0;padding:0;height:100%;background:#fff}'
        '.w{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:0}'
        'img{display:block;width:100vmin;max-width:100%;height:auto;max-height:100vh;object-fit:contain}</style></head>'
        f'<body><div class="w"><img src="{img_href}" alt="" decoding="async" fetchpriority="high"></div></body></html>'
    )
    return Response(html, mimetype='text/html; charset=utf-8', headers={'Cache-Control': 'private, max-age=120'})


@app.route('/share/daily-report/<token>.<fmt>')
def share_daily_report_image(token, fmt):
    fmt = (fmt or '').lower()
    if fmt not in ('jpg', 'jpeg', 'png'):
        return "Unsupported format", 400
    entry = _get_share_cache_entry(token)
    if not entry:
        return "Image not found", 404
    payload = entry.get('payload') or {}
    if payload.get('type') != 'daily-report':
        return "Image not found", 404
    img_bytes, mimetype = _render_daily_report_share_image(payload, fmt)
    if not img_bytes:
        return "Image engine unavailable", 503
    return Response(
        img_bytes,
        mimetype=mimetype,
        headers={
            'Cache-Control': 'private, max-age=300',
            'Content-Disposition': 'inline; filename="results.jpg"',
        },
    )


@app.route('/share/daily-report/view/<token>')
def share_daily_report_view(token: str):
    if not _SHARE_TOKEN_RE.match(token or ''):
        abort(404)
    entry = _get_share_cache_entry(token)
    if not entry or (entry.get('payload') or {}).get('type') != 'daily-report':
        abort(404)
    img_href = url_for('share_daily_report_image', token=token, fmt='jpg')
    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=4">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>\u200b</title>'
        '<style>html,body{margin:0;padding:0;height:100%;background:#fff}'
        '.w{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:0}'
        'img{display:block;width:100vmin;max-width:100%;height:auto;max-height:100vh;object-fit:contain}</style></head>'
        f'<body><div class="w"><img src="{img_href}" alt="" decoding="async" fetchpriority="high"></div></body></html>'
    )
    return Response(html, mimetype='text/html; charset=utf-8', headers={'Cache-Control': 'private, max-age=120'})


@app.route('/tutorial')
def tutorial_page():
    return render_template_string(
        TUTORIAL_TEMPLATE,
        page='tutorial',
        page_title='Tutorial | predictionlab.io',
        page_description='How to read model predictions, scores, spreads, and totals on the picks pages.'
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

@app.route('/responsible-gaming')
def responsible_gaming_page():
    return render_template_string(RESPONSIBLE_GAMING_TEMPLATE,
        page='responsible-gaming',
        page_title='Responsible Gaming Resources | predictionlab.io',
        page_description='Find responsible gaming resources and support in Canada and the United States. predictionlab.io promotes safe and responsible play.'
    )

@app.route('/contact')
def contact_page():
    return render_template_string(
        CONTACT_PAGE_TEMPLATE,
        page='contact',
        page_title='Contact us | predictionlab.io',
        page_description='Questions, suggestions, or technical issues for predictionlab.io — reach our team by email.',
    )

@app.route('/privacy')
def privacy_page():
    return render_template('privacy.html')

@app.route('/terms')
def terms_page():
    return render_template('terms.html')

@app.route('/ai-sports-betting-picks-today')
def ai_picks_today_page():
    return render_template('ai_picks_today.html')

@app.route('/what-are-ai-sports-betting-picks')
def what_are_ai_picks_page():
    return render_template('what_are_ai_picks.html')

@app.route('/our-model-vs-sportsbooks')
def model_vs_sportsbooks_page():
    return render_template('model_vs_sportsbooks.html')

@app.route('/faq')
def faq_page():
    log_site_visit('/faq')
    return render_template('faq.html')

@app.route('/sport/SOCCER/predictions/<league_slug>')
def soccer_predictions_league(league_slug):
    return redirect(f'/soccer-picks?league={league_slug}', code=301)

@app.route('/sport/SOCCER/results/<league_slug>')
def soccer_results_league(league_slug):
    return redirect(f'/soccer-results?league={league_slug}', code=301)

def _predictions_fallback_page(sport, filter_date=None):
    """Safe fallback HTML for SEO picks pages when dynamic rendering fails."""
    sport_info = SPORTS.get(sport, {'name': sport, 'icon': '🏆'})
    safe_title = f"{sport_info['name']} Predictions | predictionlab.io"
    if filter_date:
        safe_title = f"{sport_info['name']} Predictions for {filter_date} | predictionlab.io"
    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ safe_title }}</title>
    <meta name="description" content="Daily AI-powered {{ sport_info.name }} predictions, game forecasts, and model projections on predictionlab.io.">
    <meta name="robots" content="noindex, follow">
    <link rel="canonical" href="https://predictionlab.io/{{ sport_slug }}">
    <style>
        body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px;}
        .card{max-width:680px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:24px;text-align:center;}
        a{color:#fbbf24;text-decoration:none;font-weight:700;}
    </style>
</head>
<body>
    <div class="card">
        <h1 style="margin-top:0;">{{ sport_info.icon }} {{ sport_info.name }} Picks</h1>
        <p>We are refreshing this page right now. Please check the main picks feed below.</p>
        <p><a href="/{{ sport_slug }}">Open {{ sport_info.name }} picks</a></p>
    </div>
</body>
</html>
    """, sport_info=sport_info, sport_slug=SPORT_SEO_SLUGS.get(sport, sport.lower() + '-picks'), safe_title=safe_title)

def _results_fallback_page(sport, message):
    """Safe fallback HTML for results pages when processing fails."""
    sport_info = SPORTS.get(sport, {'name': sport, 'icon': '🏆'})
    return render_template_string(
        BASE_TEMPLATE.replace('{% block content %}{% endblock %}', """
        <div style="max-width:920px;margin:26px auto;background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:22px;">
            <h1 style="margin:0 0 8px;">{{ sport_info.icon }} {{ sport_info.name }} Results</h1>
            <p style="color:#334155;line-height:1.7;">{{ message }}</p>
            <p style="margin-top:10px;"><a href="/{{ sport_results_slug }}" style="color:#00529B;font-weight:700;">Refresh results page</a></p>
        </div>
        """ + _SEO_RESULTS_PAGE_FOOTER + """
        """),
        page=sport,
        sport=sport,
        sport_info=sport_info,
        sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
        sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
        message=message
    )

def sport_predictions(sport, filter_date=None):
    """Show upcoming predictions for a sport"""
    log_site_visit(f'/{SPORT_SEO_SLUGS.get(sport, sport)}')
    if sport not in SPORTS:
        return "Sport not found", 404
    if sport == 'SOCCER' and not SOCCER_ENABLED:
        return "Soccer predictions are temporarily hidden while data loads.", 404
    cache_key = None
    selected_slug = request.args.get('league', '') if sport == 'SOCCER' else ''
    if not current_user.is_authenticated:
        cache_key = f"pred_page::{sport}::{filter_date or 'all'}::{selected_slug or 'default'}"
        cache_ttl = _SPORT_PREDICTIONS_PAGE_TTL.get(sport, 180)
        cached_page = _SPORT_PREDICTIONS_PAGE_CACHE.get(cache_key)
        if isinstance(cached_page, dict):
            cached_ts = cached_page.get('ts')
            cached_html = cached_page.get('html')
            if cached_ts is not None and cached_html and (_time.time() - cached_ts) < cache_ttl:
                return cached_html
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
            # espn_predictions_template uses pred.our_spread / pred.xgb_spread / …;
            # missing keys on plain dicts raise in Jinja (NBA paths always had these).
            'our_spread',
            'our_total',
            'xgb_spread',
            'xgb_total',
            'naive_spread',
            'naive_total',
            'h2h_last10_total',
            'h2h_last10_games',
        ):
            if _k not in pred:
                pred[_k] = None if _k != 'h2h_last10_games' else 0

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
        # Default to first available league if none selected (loading all leagues causes OOM)
        if not selected_league and soccer_league_list:
            selected_league = soccer_league_list[0]
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

    try:
        today_date = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        today_date = datetime.now().strftime('%Y-%m-%d')

    # Social-share image payload: top 3 unique upcoming predictions from today's slate
    # (fallback to next available date if no games today).
    shareable_by_matchup = {}
    for pred in predictions:
        if pred.get('home_score') is not None:
            continue
        game_date = pred.get('game_date') or ''
        away_team = pred.get('away_team_id') or ''
        home_team = pred.get('home_team_id') or ''
        matchup_key = f"{game_date}|{'|'.join(sorted([away_team, home_team]))}"
        base_prob = pred.get('ensemble_prob')
        if base_prob is None:
            base_prob = pred.get('elo_prob')
        if base_prob is None:
            base_prob = pred.get('xgb_prob')
        try:
            prob_val = float(base_prob)
        except Exception:
            continue
        pick_side = 'home' if prob_val >= 50.0 else 'away'
        candidate = {
            'away_team': away_team,
            'home_team': home_team,
            'game_date': game_date,
            'pick_side': pick_side,
            'pick_team': (home_team if pick_side == 'home' else away_team),
            'confidence': round(prob_val if prob_val >= 50.0 else (100.0 - prob_val), 1),
        }
        existing = shareable_by_matchup.get(matchup_key)
        if (not existing) or (candidate['confidence'] > existing['confidence']):
            shareable_by_matchup[matchup_key] = candidate

    shareable_pool = list(shareable_by_matchup.values())
    date_pool = {}
    for item in shareable_pool:
        date_pool.setdefault(item['game_date'], []).append(item)
    target_date = today_date if today_date in date_pool else (sorted(date_pool.keys())[0] if date_pool else '')
    shareable_cards = date_pool.get(target_date, [])
    shareable_cards.sort(key=lambda x: (-x['confidence'], x['away_team'], x['home_team']))
    shareable_cards = shareable_cards[:3]
    share_image_src = None
    share_image_view_url = None
    if shareable_cards:
        _pred_payload = {
            'type': 'predictions',
            'sport': SPORTS.get(sport, {}).get('name', sport),
            'date': target_date or today_date,
            'cards': shareable_cards,
        }
        _pred_token = _register_share_image(_pred_payload)
        share_image_src = url_for('share_predictions_image', token=_pred_token, fmt='jpg')
        share_image_view_url = url_for('share_predictions_view', token=_pred_token)

    # Group predictions by date for NHL/NBA, by week for NFL
    from collections import defaultdict
    grouped_predictions = defaultdict(list)
    if sport in ['NHL', 'NBA']:
        # Group by date
        for pred in predictions:
            date_key = pred.get('game_date') or 'TBD'
            grouped_predictions[date_key].append(pred)
    elif sport == 'NFL':
        # Group by date (ESPN data doesn't have week numbers)
        for pred in predictions:
            date_key = pred.get('game_date') or 'TBD'
            grouped_predictions[date_key].append(pred)
    else:
        # Default: group by date
        for pred in predictions:
            date_key = pred.get('game_date') or 'TBD'
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

    # Nested efficiency payloads are plain dicts for some sports; Jinja attribute access
    # (pred.our_home_eff.ortg) fails on dicts — wrap as SimpleNamespace for template compatibility.
    for pred in predictions:
        if not isinstance(pred, dict):
            continue
        for _eff_key in ('our_home_eff', 'our_away_eff'):
            _v = pred.get(_eff_key)
            if isinstance(_v, dict):
                pred[_eff_key] = types.SimpleNamespace(**_v)

    # soccer_leagues already computed above for soccer

    try:
        from flask_login import current_user as _cu
        _pred_li = getattr(_cu, 'is_authenticated', False) and _cu.is_authenticated
    except Exception:
        _pred_li = False

    try:
        # Load ESPN-style template (absolute path so Render/gunicorn always finds it)
        with open(_os.path.join(_BASE_DIR, 'espn_predictions_template.html'), 'r') as f:
            espn_template = f.read()
        rendered = render_template_string(
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
            soccer_leagues=soccer_leagues,
            shareable_cards=shareable_cards,
            share_image_src=share_image_src,
            share_image_view_url=share_image_view_url,
            is_logged_in=_pred_li,
            soccer_enabled=SOCCER_ENABLED,
            ga_tracking_id=GA_TRACKING_ID,
            todays_picks=[],
        )
    except Exception as _pred_render_err:
        logger.exception(f"Predictions render fallback for {sport} ({filter_date}): {_pred_render_err}")
        return _predictions_fallback_page(sport, filter_date=filter_date)
    if cache_key:
        _SPORT_PREDICTIONS_PAGE_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
    return rendered

def sport_results(sport):
    """Show model performance results for a sport"""
    try:
        if sport not in SPORTS:
            return "Sport not found", 404
        if sport == 'SOCCER' and not SOCCER_ENABLED:
            return "Soccer results are temporarily hidden while data loads.", 404
        
        if sport == 'NFL':
            weekly_results = None
            try:
                update_nfl_scores()
                # Also sync from ESPN to catch playoff games nfl_data_py might miss
                try:
                    update_espn_scores('NFL')
                except Exception:
                    pass
                weekly_results = calculate_nfl_weekly_performance()
            except Exception as nfl_sync_err:
                logger.exception(f"NFL sync/performance pipeline failed; falling back to DB-only render: {nfl_sync_err}")

            if weekly_results:
                overall_stats = compute_overall_stats_from_weekly(weekly_results)
                yesterday_dt = datetime.now() - timedelta(days=1)
                daily_tally_date = yesterday_dt.strftime('%Y-%m-%d')
                daily_tally = compute_daily_model_tally_from_weekly(weekly_results, daily_tally_date)
                daily_tally_games = daily_tally.get('games', 0) if daily_tally else 0
                daily_results = _daily_results_from_weekly(weekly_results)
                _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
                weekly_start_dt = yesterday_dt - timedelta(days=6)
                weekly_tally = compute_model_tally_for_range(daily_results, weekly_start_dt, yesterday_dt)
                weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
                weekly_tally_date_range = f"{weekly_start_dt.strftime('%Y-%m-%d')} to {yesterday_dt.strftime('%Y-%m-%d')}"
                roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
                roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, yesterday_dt)
                roi_total = compute_roi_for_range(daily_results, None, None)
                roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
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

            # Fallback path: render from existing DB data if the live NFL pipeline fails.
            conn = get_db_connection()
            completed_games = conn.execute('''
                SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'NFL'
                WHERE g.sport = 'NFL' AND g.home_score IS NOT NULL
                ORDER BY g.game_date DESC
                LIMIT 100
            ''').fetchall()
            conn.close()
            if not completed_games:
                return _results_fallback_page(sport, "NFL moneyline results are temporarily unavailable because no completed NFL games are stored yet.")

            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            for game in completed_games:
                home_score = _to_float_safe(game['home_score'])
                away_score = _to_float_safe(game['away_score'])
                if home_score is None or away_score is None:
                    continue
                home_won = home_score > away_score
                _raw_date = _to_date_str(game['game_date'])
                game_date = _raw_date[:10] if _raw_date else 'Unknown'
                elo_prob = _to_float_safe(game['elo_home_prob'], 0.5)
                xgb_prob = _to_float_safe(game['xgboost_home_prob'], elo_prob)
                ens_prob = _to_float_safe(game['win_probability'], elo_prob)
                game_info = {
                    'game_id': game['game_id'],
                    'date': game_date,
                    'home': game['home_team_id'],
                    'away': game['away_team_id'],
                    'league': 'NFL',
                    'home_score': int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
                    'away_score': int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
                    'home_win': home_won,
                    'is_draw': False,
                    'glicko2_prob': None,
                    'trueskill_prob': None,
                    'elo_prob': round(elo_prob * 100, 1),
                    'xgb_prob': round(xgb_prob * 100, 1),
                    'ens_prob': round(ens_prob * 100, 1),
                    'glicko2_correct': None,
                    'trueskill_correct': None,
                    'elo_correct': (elo_prob >= 0.5) == home_won,
                    'xgb_correct': (xgb_prob >= 0.5) == home_won,
                    'ens_correct': (ens_prob >= 0.5) == home_won,
                    'skip_grading': False,
                }
                daily_results[game_date]['games'].append(game_info)

            sorted_dates = sorted(daily_results.keys(), reverse=True)[:30]
            overall_stats = compute_overall_stats_from_daily(daily_results)
            _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
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
            return render_template_string(
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
                soccer_leagues=None
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
            yesterday_dt = datetime.now() - timedelta(days=1)
            lookback_start_dt = yesterday_dt - timedelta(days=140)
            daily_results = _banner_daily_results_for_range(sport, lookback_start_dt, yesterday_dt)
            if not daily_results:
                return _results_fallback_page(sport, "NHL results could not be loaded because no completed NHL games were available for grading yet.")

            today_date = datetime.now().strftime('%Y-%m-%d')

            try:
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
                    return _results_fallback_page(sport, "NBA results could not be loaded because no completed NBA games were available for grading yet.")
                
                # Regroup by date instead of week
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
            min_live = _SPORT_MIN_LIVE_DATES.get(sport)
            if min_live and datetime.now() < min_live:
                launch_txt = min_live.strftime('%B %-d, %Y')
                return _results_fallback_page(
                    sport,
                    f"{SPORTS[sport]['name']} regular season results will appear once games begin on {launch_txt}."
                )
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
            # Update scores at most once every 10 minutes per sport.
            sync_key = f'{sport}_results_score_sync_ts'
            sync_entry = _SPORT_RESULTS_CACHE.get(sync_key)
            sync_last_ts = sync_entry.get('ts') if isinstance(sync_entry, dict) else None
            now_ts = _time.time()
            if sync_last_ts is None or (now_ts - sync_last_ts) >= 600:
                update_espn_scores(sport)
                _SPORT_RESULTS_CACHE[sync_key] = {'ts': now_ts}
            
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
                    offseason_msg = f" The {SPORTS[sport]['name']} season has ended. Results from the 2025 season will be available next year."
                return _results_fallback_page(sport, f"No {SPORTS[sport]['name']} results data available yet. {offseason_msg}")
            
            # Process into daily results format
            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            
            for game in completed_games:
                try:
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
                    try:
                        if isinstance(game, dict):
                            league_name = game.get('league')
                        else:
                            league_name = game['league'] if 'league' in game.keys() else None
                    except Exception:
                        league_name = None
                    if league_name is None and sport != 'SOCCER':
                        league_name = sport
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
                        'glicko2_correct':   (glicko2_prob   >= 0.5) == home_won if glicko2_prob   is not None and home_won is not None else None,
                        'trueskill_correct': (trueskill_prob >= 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
                        'elo_correct':       (elo_prob  >= 0.5) == home_won if home_won is not None else None,
                        'xgb_correct':       (xgb_prob  >= 0.5) == home_won if home_won is not None else None,
                        'ens_correct':       (ens_prob  >= 0.5) == home_won if home_won is not None else None,
                        'skip_grading':      True if home_won is None else False,
                        'model_data_note':   model_note,
                    }
                    daily_results[game_info['date']]['games'].append(game_info)
                except Exception as _row_err:
                    _gid = None
                    try:
                        _gid = game['game_id']
                    except Exception:
                        pass
                    logger.warning(f"Skipping {sport} results row (game_id={_gid}): {_row_err}")
                    continue

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
        return _results_fallback_page(
            sport,
            f"{sport} moneyline results are temporarily unavailable because the server hit an internal processing error. Please refresh in 30-60 seconds."
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
    print("🎯 predictionlab.io - Multi-Sport Prediction Platform")
    print("="*60)
    print(f"🌐 Visit http://0.0.0.0:{port}")
    print("="*60 + "\n")
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False, threaded=True)
