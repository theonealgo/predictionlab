#!/usr/bin/env python3
"""
predictionlab.io - Multi-Sport Prediction Platform
==================================================
Complete platform with Dashboard, Predictions, and Results pages for all sports.
5-Model System: Glicko-2, TrueSkill, Elo, XGBoost, Ensemble
"""

from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, Response, send_from_directory, abort, has_request_context
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
import glob
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


def _init_datadog_tracing():
    """Enable ddtrace before Flask app import side-effects (no-op unless DD_TRACE_ENABLED)."""
    flag = (_os_v2.environ.get('DD_TRACE_ENABLED') or '').lower()
    if flag not in ('1', 'true', 'yes') and not _os_v2.environ.get('DD_API_KEY'):
        return
    try:
        from ddtrace import config, patch_all
        patch_all()
        config.service = _os_v2.environ.get('DD_SERVICE', 'predictionlab')
        config.env = _os_v2.environ.get('DD_ENV', 'production')
        if _os_v2.environ.get('DD_VERSION'):
            config.version = _os_v2.environ['DD_VERSION']
        logger.info('[datadog] ddtrace enabled service=%s env=%s', config.service, config.env)
    except Exception as _dde:
        logger.warning('[datadog] ddtrace init failed: %s', _dde)


_init_datadog_tracing()

import time as _time
import copy as _copy
# NOTE: several MODULE-LEVEL blocks reference the bare global name `threading`
# (the odds/predictions prewarm thread starts and _persist_predictions_to_disk's
# threading.get_ident()). Those are NOT covered by the function-local `import
# threading` statements elsewhere, nor by the aliased `import threading as
# _preds_thr`. Without this top-level import they raised a swallowed NameError,
# silently disabling the prewarmers AND the predictions disk cache on every boot.
import threading
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
    'NBA': 600,
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
# Stale-while-revalidate: serve cached HTML up to this age while refreshing off-path.
_SPORT_PREDICTIONS_PAGE_STALE_MAX = {
    'MLB': 3600,
    'SOCCER': 1800,
    'NBA': 1200,
    'NHL': 1200,
    'NFL': 1800,
    'NCAAB': 1200,
    'NCAAW': 1200,
    'NCAAF': 1800,
    'WNBA': 1200,
}
_MANUAL_BANNER_ITEMS = [
    {'label': 'NHL ⭐ Grinder2', 'pct': '83.3%', 'record': '40-8'},
    {'label': '🎲 NBA O/U (XSharp)', 'pct': '82.6%', 'record': '247/299'},
    {'label': 'MLB 🎯 Moneyline (Sharp Consensus)', 'pct': '60.0%', 'record': '60-40'},
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


_PL_BOOK_ODDS_LIMIT_BY_SPORT = {
    # Soccer slates are large (cups + multi-league). Scoreboard nested odds cover most;
    # Core fetch still needed when scoreboard returns null odds for a competition.
    'SOCCER': 100,
    'NBA': 80,
    'MLB': 80,
    'NHL': 60,
    'NFL': 60,
    'WNBA': 50,
    'NCAAB': 40,
    'NCAAW': 40,
    'NCAAF': 40,
}

_OFFSEASON_SPORTS_HINT = {
    'NCAAB': 'College basketball picks return when the season schedule is live on ESPN (typically November–April).',
    'NCAAW': "Women's college basketball picks return when the season schedule is live on ESPN (typically November–April).",
    'NFL': 'NFL picks return when the regular season schedule is published (typically September–February).',
    'NCAAF': 'College football picks return when the fall schedule is live on ESPN (typically August–January).',
}


def _daily_results_game_count(daily_results) -> int:
    if not daily_results:
        return 0
    return sum(len(dd.get('games') or []) for dd in daily_results.values())


def _recent_result_dates(daily_results, *, yesterday=None, limit=7, recent_window_days=21):
    """Prefer recent graded days (through yesterday); fall back to older dates if none."""
    if not daily_results:
        return []
    if yesterday is None:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    ydt = parse_date(yesterday) or (datetime.now() - timedelta(days=1))
    cutoff_dt = ydt - timedelta(days=recent_window_days)

    def _date_key_dt(dk):
        return parse_date(dk) or datetime.min

    recent = sorted(
        (
            d for d in daily_results.keys()
            if d and daily_results[d].get('games')
            and (_dk := _date_key_dt(d)) >= cutoff_dt and _dk <= ydt
        ),
        key=_date_key_dt,
        reverse=True,
    )
    if recent:
        return recent[:limit]
    dates = sorted(
        (d for d in daily_results.keys() if d and _date_key_dt(d) <= ydt),
        key=_date_key_dt,
        reverse=True,
    )
    if not dates:
        dates = sorted(
            (d for d in daily_results.keys() if d),
            key=_date_key_dt,
            reverse=True,
        )
    return dates[:limit]


def _picks_display_dates(grouped_predictions, today_date):
    """Dates for picks nav + default visible day (must have upcoming games when possible)."""
    if not grouped_predictions:
        return [], today_date
    upcoming = []
    for dk in sorted(grouped_predictions.keys()):
        if not dk or dk == 'TBD':
            continue
        games = grouped_predictions[dk]
        if any(isinstance(g, dict) and g.get('home_score') is None for g in games):
            upcoming.append(dk)
    if upcoming:
        if today_date in upcoming:
            default = today_date
        else:
            future = [d for d in upcoming if d >= today_date]
            default = future[0] if future else upcoming[-1]
        return upcoming, default
    all_dates = sorted(d for d in grouped_predictions.keys() if d and d != 'TBD')
    if not all_dates:
        return [], today_date
    window = all_dates[-14:]
    default = today_date if today_date in window else window[-1]
    return window, default


_PICKS_ROBOTS_INDEX = 'index,follow,max-image-preview:large,max-snippet:-1'
_PICKS_ROBOTS_NOINDEX = 'noindex,follow'


def _picks_grouped_has_games(grouped_predictions):
    """True when a picks slate has at least one real game card to show."""
    if not grouped_predictions:
        return False
    for games in grouped_predictions.values():
        if games:
            return True
    return False


def _picks_robots_meta(*, sport=None, filter_date=None, grouped_predictions=None):
    """Index hubs + dated pages with real picks; noindex thin/empty dated URLs.

    Dated GOLF pages are always noindex (ephemeral / often empty) so they do not
    burn crawl budget ("Crawled - currently not indexed").
    """
    sport_key = (sport or '').upper()
    if filter_date and sport_key == 'GOLF':
        return _PICKS_ROBOTS_NOINDEX
    if filter_date and not _picks_grouped_has_games(grouped_predictions):
        return _PICKS_ROBOTS_NOINDEX
    return _PICKS_ROBOTS_INDEX


def _picks_page_canonical_url(*, sport=None, filter_date=None, grouped_predictions=None):
    """Self-canonical when the dated slate has games; otherwise point at the sport hub."""
    sport_key = (sport or '').upper()
    if filter_date and sport_key and (
        sport_key == 'GOLF' or not _picks_grouped_has_games(grouped_predictions)
    ):
        slug = SPORT_SEO_SLUGS.get(sport_key) or f'{sport_key.lower()}-picks'
        return f'{_SITE_DOMAIN}/{slug}'
    return _seo_canonical_url()


# All-Star / skills / exhibition sides (e.g. WNBA TEAM COOP vs TEAM SPOON) have
# no real book odds or logos — showing them as picks collapses to 50% / — / PK.
_PLACEHOLDER_TEAM_RE = re.compile(r'^TEAM\s+[A-Z0-9]+$', re.I)
_ALLSTAR_EVENT_RE = re.compile(
    r'all[\s-]?stars?|rising\s+stars?|team\s+coop|team\s+spoon|'
    r'american\s+league|national\s+league|'
    r'^american\s+all|^national\s+all',
    re.I,
)
_PLACEHOLDER_TEAM_NAMES = frozenset({
    'TEAM COOP', 'TEAM SPOON', 'TEAM USA', 'WORLD', 'EAST', 'WEST',
    'AMERICAN ALL-STARS', 'NATIONAL ALL-STARS',
    'AMERICAN ALL STARS', 'NATIONAL ALL STARS',
    'AL ALL-STARS', 'NL ALL-STARS',
})


def _is_placeholder_team_name(name):
    """True for All-Star / exhibition sides (TEAM COOP) or unresolved brackets (TBD)."""
    n = (name or '').strip()
    if not n:
        return True
    up = n.upper()
    if up in _PLACEHOLDER_TEAM_NAMES:
        return True
    if _PLACEHOLDER_TEAM_RE.match(n):
        return True
    if _ALLSTAR_EVENT_RE.search(n):
        return True
    _n = n.lower()
    if any(_m in _n for _m in (
        'winner', 'loser', 'tbd', 'tba', 'round of', 'qualifier',
        'to be determined', 'to be decided', 'winner of', 'loser of',
    )):
        return True
    return False


def _is_exhibition_espn_competition(competition, event=None):
    """True for ESPN All-Star / exhibition competitions (not regular-season games)."""
    comp = competition or {}
    typ = ((comp.get('type') or {}).get('abbreviation') or '').upper()
    if typ in ('ALLSTAR', 'ALL-STAR', 'EXHIBITION'):
        return True
    notes = comp.get('notes') or []
    headlines = ' '.join(
        str(n.get('headline') or '') for n in notes if isinstance(n, dict)
    )
    event_name = ''
    if isinstance(event, dict):
        event_name = str(event.get('name') or event.get('shortName') or '')
    blob = f'{event_name} {headlines}'.strip()
    return bool(blob and _ALLSTAR_EVENT_RE.search(blob))


def _is_exhibition_matchup(home, away, *, event_name=''):
    if _is_placeholder_team_name(home) or _is_placeholder_team_name(away):
        return True
    blob = f'{event_name or ""} {home or ""} {away or ""}'
    return bool(_ALLSTAR_EVENT_RE.search(blob))


def _filter_exhibition_predictions(predictions):
    """Drop All-Star / placeholder matchups from a picks slate."""
    out = []
    for pred in predictions or []:
        if not isinstance(pred, dict):
            continue
        home = pred.get('home_team_id') or pred.get('home') or ''
        away = pred.get('away_team_id') or pred.get('away') or ''
        if _is_exhibition_matchup(home, away, event_name=pred.get('event_name') or ''):
            continue
        out.append(pred)
    return out


def _picks_for_filter_date(predictions, filter_date):
    """Games for a daily SEO URL — include finished games for that calendar day."""
    dated_preds = []
    for pred in predictions or []:
        if not isinstance(pred, dict):
            continue
        if (pred.get('game_date') or '') != filter_date:
            continue
        away = pred.get('away_team_id')
        home = pred.get('home_team_id')
        if not away or not home or away == 'TBD' or home == 'TBD':
            continue
        if _is_exhibition_matchup(home, away):
            continue
        dated_preds.append(pred)
    return dated_preds


def _fetch_db_games_for_picks_date(sport, filter_date):
    """Load stored games for a daily SEO URL when the live slate window omits that day."""
    try:
        conn = get_db_connection()
        rows = conn.execute(
            '''
            SELECT g.*,
                   p.elo_home_prob AS stored_elo_prob,
                   p.xgboost_home_prob AS stored_xgb_prob,
                   p.win_probability AS stored_ensemble_prob
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND date(g.game_date) = date(?)
            ''',
            (sport, sport, filter_date),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.debug('DB picks date load failed for %s %s: %s', sport, filter_date, exc)
        return []
    games = []
    for row in rows:
        gd = dict(row)
        away = gd.get('away_team_id') or ''
        home = gd.get('home_team_id') or ''
        if not away or not home or away == 'TBD' or home == 'TBD':
            continue
        gd['game_date'] = filter_date
        if _is_exhibition_matchup(home, away):
            continue
        games.append(gd)
    return games


def _results_page_html_usable(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    if any(
        phrase in low
        for phrase in (
            'class="no-data"',
            'moneyline results are temporarily unavailable',
            'results could not be loaded because no completed',
            'no results data available yet',
        )
    ):
        return False
    if 'game-card' in low or 'week-section' in low:
        return True
    # Snapshot-only page: season banner without recent game cards.
    if 'season performance' in low and 'moneyline accuracy by model' in low:
        return True
    return False


def _trim_cache(cache: dict, ttl: float, max_entries: int = 200) -> None:
    """Evict expired entries then, if still over max_entries, drop the oldest ones."""
    now = _time.time()
    expired = [k for k, v in cache.items() if isinstance(v, dict) and (now - v.get('ts', now)) > ttl]
    for k in expired:
        cache.pop(k, None)
    if len(cache) > max_entries:
        sorted_keys = sorted(
            (k for k, v in cache.items() if isinstance(v, dict)),
            key=lambda k: cache[k].get('ts', 0)
        )
        for k in sorted_keys[:len(cache) - max_entries]:
            cache.pop(k, None)


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


def _share_gone_response(message='This share link has expired.'):
    """Ephemeral share URLs should not be indexed; 410 removes them from Google faster than 404."""
    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<title>{message}</title></head>'
        f'<body><p>{message}</p></body></html>'
    )
    return Response(
        html,
        status=410,
        mimetype='text/html; charset=utf-8',
        headers={
            'X-Robots-Tag': 'noindex, nofollow',
            'Cache-Control': 'private, no-store',
        },
    )


def _share_gone_plain(message='Not found'):
    return Response(
        message,
        status=410,
        mimetype='text/plain',
        headers={'X-Robots-Tag': 'noindex, nofollow', 'Cache-Control': 'private, no-store'},
    )


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
        _trim_cache(_API_CACHE, _API_TTL, max_entries=300)
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
        'vegasknights': 'vegasgoldenknights',
    }
    txt = alias_map.get(txt, txt)
    return txt

_NHL_TEAM_ABBREV_ALIASES = {
    'ana': 'anaheimducks', 'bos': 'bostonbruins', 'buf': 'buffalosabres',
    'cgy': 'calgaryflames', 'car': 'carolinahurricanes', 'chi': 'chicagoblackhawks',
    'col': 'coloradoavalanche', 'cbj': 'columbusbluejackets', 'dal': 'dallasstars',
    'det': 'detroitredwings', 'edm': 'edmontonoilers', 'fla': 'floridapanthers',
    'lak': 'losangeleskings', 'min': 'minnesotawild', 'mtl': 'montrealcanadiens',
    'nsh': 'nashvillepredators', 'njd': 'newjerseydevils', 'nyi': 'newyorkislanders',
    'nyr': 'newyorkrangers', 'ott': 'ottawasenators', 'phi': 'philadelphiaflyers',
    'pit': 'pittsburghpenguins', 'sjs': 'sanjosesharks', 'sea': 'seattlekraken',
    'stl': 'stlouisblues', 'tb': 'tampabaylightning', 'tbl': 'tampabaylightning',
    'tor': 'torontomapleleafs', 'van': 'vancouvercanucks', 'vgk': 'vegasgoldenknights',
    'wsh': 'washingtoncapitals', 'wpg': 'winnipegjets', 'uta': 'utahmammoth',
    'utah': 'utahmammoth',
}

_TEAM_ALIAS_BY_SPORT = {
    'NHL': _NHL_TEAM_ABBREV_ALIASES,
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
# PL pick-card display: cap implied probs and American ML (avoid -9000 from 99%+ model %).
_PL_PROB_CLAMP_MIN_PCT = 1.0
_PL_PROB_CLAMP_MAX_PCT = 99.0
_PL_ML_CLAMP_MIN = -1000
_PL_ML_CLAMP_MAX = 1000


def _prob_to_american(p):
    """Convert a win probability (0-1) to American odds."""
    if p is None or p <= 0 or p >= 1:
        return None
    if p >= 0.5:
        return -round((p / (1 - p)) * 100)
    return round(((1 - p) / p) * 100)


def _normalize_home_win_prob_pct(prob):
    """Return home win probability on 0–100 scale (accepts 0–1 or 0–100 input)."""
    p = _safe_float(prob)
    if p is None or p <= 0:
        return None
    if p <= 1.0:
        p *= 100.0
    return max(_PL_PROB_CLAMP_MIN_PCT, min(_PL_PROB_CLAMP_MAX_PCT, p))


def _clamp_pl_american_ml(val):
    """Clamp American ML to a sane display range (e.g. no -9000)."""
    if val is None:
        return None
    try:
        o = int(round(float(val)))
    except (TypeError, ValueError):
        return None
    if o < 0:
        return max(o, _PL_ML_CLAMP_MIN)
    return min(o, _PL_ML_CLAMP_MAX)


def _compute_odds_from_prob(home_prob_pct, vig=_ODDS_VIG, *, apply_vig=True, clamp_ml=True):
    """Compute American moneyline odds from home win probability.

    home_prob_pct: 65.0 or 0.65 for 65% home win chance.
    apply_vig: when False, fair ML from model % (PL pick cards).
    clamp_ml: cap extremes to ±_PL_ML_CLAMP_MAX.
    Returns dict with moneyline_home, moneyline_away.
    """
    pct = _normalize_home_win_prob_pct(home_prob_pct)
    if pct is None:
        return None
    hp = pct / 100.0
    ap = 1.0 - hp
    if apply_vig and vig:
        total = hp + ap
        ph = hp / total
        pa = ap / total
        vig_factor = 1 + vig
        ph = min(ph * vig_factor, 0.99)
        pa = min(pa * vig_factor, 0.99)
    else:
        ph, pa = hp, ap
    out = {
        'moneyline_home': _prob_to_american(ph),
        'moneyline_away': _prob_to_american(pa),
    }
    if clamp_ml:
        out['moneyline_home'] = _clamp_pl_american_ml(out['moneyline_home'])
        out['moneyline_away'] = _clamp_pl_american_ml(out['moneyline_away'])
    return out


def _soccer_threeway_probs(binary_home, draw):
    """Convert binary home prob (home + 0.5×draw) and draw to 3-way (0–1 each)."""
    bh = _safe_float(binary_home)
    dp = _safe_float(draw)
    if bh is None or dp is None:
        return None, None, None
    if bh > 1.0:
        bh /= 100.0
    if dp > 1.0:
        dp /= 100.0
    hw = max(0.0, bh - 0.5 * dp)
    aw = max(0.0, 1.0 - hw - dp)
    total = hw + dp + aw
    if total <= 0:
        return None, None, None
    return hw / total, dp / total, aw / total


def _compute_odds_from_threeway(
    home_pct, draw_pct, away_pct, vig=_ODDS_VIG, *, apply_vig=True, clamp_ml=True,
):
    """American ML for soccer 3-way (home / draw / away)."""
    hp = _safe_float(home_pct)
    dp = _safe_float(draw_pct)
    ap = _safe_float(away_pct)
    if hp is None or dp is None or ap is None:
        return None
    if hp <= 1.0:
        hp *= 100.0
    if dp <= 1.0:
        dp *= 100.0
    if ap <= 1.0:
        ap *= 100.0
    total = hp + dp + ap
    if total <= 0:
        return None
    ph, pd, pa = hp / total, dp / total, ap / total
    if apply_vig and vig:
        vig_factor = 1 + vig
        ph = min(ph * vig_factor, 0.99)
        pd = min(pd * vig_factor, 0.99)
        pa = min(pa * vig_factor, 0.99)
        renorm = ph + pd + pa
        if renorm > 0:
            ph, pd, pa = ph / renorm, pd / renorm, pa / renorm
    out = {
        'moneyline_home': _prob_to_american(ph),
        'moneyline_draw': _prob_to_american(pd),
        'moneyline_away': _prob_to_american(pa),
    }
    if clamp_ml:
        for k in out:
            out[k] = _clamp_pl_american_ml(out[k])
    return out


def _soccer_ml_pick_correct(home_pct, draw_pct, away_pct, home_won, is_draw):
    """Grade 3-way soccer ML: pick is highest of home/draw/away model %."""
    if home_pct is None:
        return None
    dp = draw_pct if draw_pct is not None else 0.0
    ap = away_pct if away_pct is not None else max(0.0, 100.0 - home_pct - dp)
    pick = max(
        [('home', home_pct), ('draw', dp), ('away', ap)],
        key=lambda x: x[1],
    )[0]
    if is_draw:
        return pick == 'draw'
    if home_won is True:
        return pick == 'home'
    if home_won is False:
        return pick == 'away'
    return None


def _soccer_model_correct(binary_home_dec, draw_dec, home_won, is_draw):
    """Grade one soccer model prob (binary home + draw) against result."""
    hw, dw, aw = _soccer_threeway_probs(binary_home_dec, draw_dec)
    if hw is None:
        return None
    return _soccer_ml_pick_correct(hw * 100, dw * 100, aw * 100, home_won, is_draw)


def _apply_soccer_ml_grading(
    game_info,
    *,
    draw_dec,
    glicko2_prob,
    trueskill_prob,
    elo_prob,
    xgb_prob,
    ens_prob,
    home_won,
    is_draw,
):
    """Set 3-way soccer ML correct flags; grade draws instead of skip_grading."""
    if draw_dec is None:
        game_info['glicko2_correct'] = (glicko2_prob >= 0.5) == home_won if glicko2_prob is not None and home_won is not None else None
        game_info['trueskill_correct'] = (trueskill_prob >= 0.5) == home_won if trueskill_prob is not None and home_won is not None else None
        game_info['elo_correct'] = (elo_prob >= 0.5) == home_won if elo_prob is not None and home_won is not None else None
        game_info['xgb_correct'] = (xgb_prob >= 0.5) == home_won if xgb_prob is not None and home_won is not None else None
        game_info['ens_correct'] = (ens_prob >= 0.5) == home_won if ens_prob is not None and home_won is not None else None
        game_info['skip_grading'] = home_won is None
        return
    _hw, _dw, _aw = _soccer_threeway_probs(ens_prob, draw_dec)
    if _hw is not None:
        game_info['draw_prob'] = round(_dw * 100, 1)
        game_info['home_win_prob'] = round(_hw * 100, 1)
        game_info['away_win_prob'] = round(_aw * 100, 1)
    game_info['glicko2_correct'] = _soccer_model_correct(glicko2_prob, draw_dec, home_won, is_draw) if glicko2_prob is not None else None
    game_info['trueskill_correct'] = _soccer_model_correct(trueskill_prob, draw_dec, home_won, is_draw) if trueskill_prob is not None else None
    game_info['elo_correct'] = _soccer_model_correct(elo_prob, draw_dec, home_won, is_draw)
    game_info['xgb_correct'] = _soccer_model_correct(xgb_prob, draw_dec, home_won, is_draw)
    game_info['ens_correct'] = _soccer_model_correct(ens_prob, draw_dec, home_won, is_draw)
    game_info['skip_grading'] = False


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
    """Attach PL-model odds to completed game results from ensemble win probability.

    PL Odds are derived from ens_prob (ensemble of Glicko2, TrueSkill, XGBoost,
    ELO) with vig applied — this is the PL column, independent of Books and XSharp.
    """
    if not daily_results:
        return
    for dd in daily_results.values():
        for g in dd.get('games', []):
            ens = g.get('ens_prob')
            ml = _compute_odds_from_prob(ens, apply_vig=True, clamp_ml=True)
            if ml:
                g['home_moneyline'] = ml['moneyline_home']
                g['away_moneyline'] = ml['moneyline_away']
            g.setdefault('spread_price_home', -110)
            g.setdefault('spread_price_away', -110)
            g.setdefault('total_over_price', -110)
            g.setdefault('total_under_price', -110)
            g['odds_source'] = 'pl_model'


def _attach_engine_odds_to_predictions(sport, predictions, limit=40):
    """Attach PL-model odds to upcoming predictions from ensemble win probability.

    PL Odds are the ensemble model's own moneyline projection (Glicko2 + TrueSkill
    + XGBoost + ELO → ens_prob → American ML with vig).  This is the PL column —
    completely independent of Books (pl_book_odds_api) and XSharp (xgb_*).
    """
    if not predictions:
        return
    for pred in predictions:
        if pred.get('home_score') is not None:
            continue
        ens = pred.get('ens_prob') or pred.get('ensemble_prob')
        ml = _compute_odds_from_prob(ens, apply_vig=True, clamp_ml=True)
        if ml:
            pred['home_moneyline'] = ml['moneyline_home']
            pred['away_moneyline'] = ml['moneyline_away']
        pred.setdefault('spread_price_home', -110)
        pred.setdefault('spread_price_away', -110)
        pred.setdefault('total_over_price', -110)
        pred.setdefault('total_under_price', -110)
        pred['odds_source'] = 'pl_model'


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
        _trim_cache(_H2H_PROJECTION_CACHE, 3600, max_entries=500)
        _H2H_PROJECTION_CACHE[cache_key] = {'ts': now_ts, 'data': None}
        return None
    home_pts = []
    away_pts = []
    totals = []
    # Win-loss(-draw) record from the perspective of the UPCOMING home/away teams
    # (hp = upcoming home team's score in that meeting, regardless of venue).
    home_wins = away_wins = draws = 0
    for r in rows:
        try:
            hs = float(r['home_score'])
            as_ = float(r['away_score'])
        except Exception:
            continue
        if r['home_team_id'] == home_team:
            hp, ap = hs, as_
        else:
            hp, ap = as_, hs
        home_pts.append(hp)
        away_pts.append(ap)
        totals.append(hs + as_)
        if hp > ap:
            home_wins += 1
        elif ap > hp:
            away_wins += 1
        else:
            draws += 1
    if len(home_pts) < min_games:
        _trim_cache(_H2H_PROJECTION_CACHE, 3600, max_entries=500)
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
        'home_wins': home_wins,
        'away_wins': away_wins,
        'draws': draws,
    }
    _trim_cache(_H2H_PROJECTION_CACHE, 3600, max_entries=500)
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
                pred['h2h_last10_home_wins'] = proj.get('home_wins')
                pred['h2h_last10_away_wins'] = proj.get('away_wins')
                pred['h2h_last10_draws'] = proj.get('draws')
            else:
                pred.setdefault('our_total', None)
                pred.setdefault('our_total_games', 0)
                pred.setdefault('h2h_last10_total', None)
                pred.setdefault('h2h_last10_games', 0)
                pred.setdefault('h2h_last10_home_wins', None)
                pred.setdefault('h2h_last10_away_wins', None)
                pred.setdefault('h2h_last10_draws', None)
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


# Model-level fade: invert pick parameters when historical win rate < ~55%.
# Book odds layer is never modified.
SPREAD_FADE_SPORTS = frozenset({'NCAAB', 'SOCCER'})
XSHARP_SPREAD_FADE_SPORTS = frozenset({'WNBA'})
SOCCER_ML_FADE_SPORTS = frozenset({'SOCCER'})
# WNBA season ML models below 55% (Consensus/ensemble kept as-is at ~60%).
WNBA_ML_FADE_PROB_KEYS = (
    'glicko2_prob', 'trueskill_prob', 'elo_prob', 'xgb_prob',
)
OU_FADE_SPORTS = frozenset()


def _fade_spread(val):
    """Negate spread (parameter-level fade — model picks opposite ATS side)."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v == 0:
        return 0.0
    return _round_to_half(-v)


_mlb_fade_spread = _fade_spread  # backward-compatible alias


def _fade_ml_prob(val):
    """Invert home win probability (100-pct or 1-p)."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if v > 1:
        return round(100.0 - v, 1)
    return round(1.0 - v, 4)


def _fade_total_around_line(val, line):
    """Reflect model total around market line (flips O/U side vs same book total)."""
    if val is None or line is None:
        return None
    try:
        v = float(val)
        ln = float(line)
    except (TypeError, ValueError):
        return None
    return _round_to_half(2.0 * ln - v)


def _apply_spread_fade(d: dict) -> None:
    """Invert XSharp + PL spreads on one game/prediction dict (once)."""
    if not isinstance(d, dict) or d.get('_spread_faded') or d.get('_mlb_spread_faded'):
        return
    faded = False
    for key in ('xgb_spread', 'our_spread', 'xsharp_spread'):
        if d.get(key) is not None:
            d[key] = _fade_spread(d[key])
            faded = True
    if not faded:
        return
    if d.get('xgb_home_score') is not None and d.get('xgb_away_score') is not None:
        d['xgb_home_score'], d['xgb_away_score'] = d['xgb_away_score'], d['xgb_home_score']
    if d.get('xsharp_home_score') is not None and d.get('xsharp_away_score') is not None:
        d['xsharp_home_score'], d['xsharp_away_score'] = (
            d['xsharp_away_score'], d['xsharp_home_score'],
        )
    if d.get('our_spread') is not None and d.get('our_total') is not None:
        h, a = _scores_from_spread_total(d['our_spread'], d['our_total'])
        if h is not None:
            d['our_home_pts'] = h
            d['our_away_pts'] = a
    if d.get('xgb_spread') is not None:
        d['xsharp_spread'] = d['xgb_spread']
    d['_spread_faded'] = True
    d['_mlb_spread_faded'] = True


_apply_mlb_spread_fade = _apply_spread_fade  # backward-compatible alias


def _apply_ncaab_spread_fade(d: dict) -> None:
    _apply_spread_fade(d)


def _apply_soccer_spread_fade(d: dict) -> None:
    _apply_spread_fade(d)


def _apply_nhl_spread_fade(d: dict) -> None:
    _apply_spread_fade(d)


def _apply_xsharp_spread_fade(d: dict) -> None:
    """Invert XSharp spread only (WNBA — PL our_spread unchanged)."""
    if not isinstance(d, dict) or d.get('_xsharp_spread_faded'):
        return
    faded = False
    for key in ('xgb_spread', 'xsharp_spread'):
        if d.get(key) is not None:
            d[key] = _fade_spread(d[key])
            faded = True
    if not faded:
        return
    if d.get('xgb_home_score') is not None and d.get('xgb_away_score') is not None:
        d['xgb_home_score'], d['xgb_away_score'] = d['xgb_away_score'], d['xgb_home_score']
    if d.get('xsharp_home_score') is not None and d.get('xsharp_away_score') is not None:
        d['xsharp_home_score'], d['xsharp_away_score'] = (
            d['xsharp_away_score'], d['xsharp_home_score'],
        )
    d['_xsharp_spread_faded'] = True


def _apply_selective_ml_fade(d: dict, prob_keys) -> None:
    """Invert listed ML probability fields (0–1 or 0–100)."""
    if not isinstance(d, dict):
        return
    faded = False
    for key in prob_keys:
        if d.get(key) is not None:
            d[key] = _fade_ml_prob(d[key])
            faded = True
    if faded:
        d['_ml_faded'] = True


def _apply_soccer_ml_fade(d: dict) -> None:
    """Invert all soccer ML model probabilities (strong anti-predictive)."""
    if not isinstance(d, dict) or d.get('_soccer_ml_faded'):
        return
    faded = False
    for key in (
        'glicko2_prob', 'trueskill_prob', 'elo_prob', 'xgb_prob',
        'ens_prob', 'ensemble_prob', 'win_probability',
    ):
        if d.get(key) is not None:
            d[key] = _fade_ml_prob(d[key])
            faded = True
    if not faded:
        return
    if d.get('our_home_pts') is not None and d.get('our_away_pts') is not None:
        d['our_home_pts'], d['our_away_pts'] = d['our_away_pts'], d['our_home_pts']
    if d.get('xgb_home_score') is not None and d.get('xgb_away_score') is not None:
        d['xgb_home_score'], d['xgb_away_score'] = d['xgb_away_score'], d['xgb_home_score']
    d['_soccer_ml_faded'] = True
    d['_ml_faded'] = True


def _prob_as_fraction(val):
    p = _safe_float(val)
    if p is None:
        return None
    return p / 100.0 if p > 1 else p


def _recompute_daily_ml_grading_after_fade(g: dict, sport: str) -> None:
    """Refresh per-model correct flags after ML parameter fades."""
    if not isinstance(g, dict) or g.get('skip_grading'):
        return
    home_won = g.get('home_win')
    is_draw = bool(g.get('is_draw'))
    if sport == 'SOCCER':
        g2 = _prob_as_fraction(g.get('glicko2_prob'))
        ts = _prob_as_fraction(g.get('trueskill_prob'))
        el = _prob_as_fraction(g.get('elo_prob'))
        xg = _prob_as_fraction(g.get('xgb_prob'))
        ens = _prob_as_fraction(g.get('ens_prob'))
        _apply_soccer_ml_grading(
            g,
            draw_dec=None,
            glicko2_prob=g2,
            trueskill_prob=ts,
            elo_prob=el,
            xgb_prob=xg,
            ens_prob=ens,
            home_won=home_won,
            is_draw=is_draw,
        )
        return
    if home_won is None:
        return
    for prob_key, correct_key in (
        ('glicko2_prob', 'glicko2_correct'),
        ('trueskill_prob', 'trueskill_correct'),
        ('elo_prob', 'elo_correct'),
        ('xgb_prob', 'xgb_correct'),
        ('ens_prob', 'ens_correct'),
    ):
        p = _prob_as_fraction(g.get(prob_key))
        if p is None:
            continue
        g[correct_key] = (p >= 0.5) == home_won


def _apply_ml_fade(d: dict) -> None:
    """Invert PL ensemble moneyline probability (once)."""
    if not isinstance(d, dict) or d.get('_ml_faded'):
        return
    faded = False
    for key in ('ensemble_prob', 'ens_prob', 'win_probability'):
        if d.get(key) is not None:
            d[key] = _fade_ml_prob(d[key])
            faded = True
    if not faded:
        return
    if d.get('our_home_pts') is not None and d.get('our_away_pts') is not None:
        d['our_home_pts'], d['our_away_pts'] = d['our_away_pts'], d['our_home_pts']
    if d.get('xgb_home_score') is not None and d.get('xgb_away_score') is not None:
        d['xgb_home_score'], d['xgb_away_score'] = d['xgb_away_score'], d['xgb_home_score']
    d['_ml_faded'] = True


def _apply_ou_fade(d: dict, market_total=None) -> None:
    """Reflect model totals around book/market line (once)."""
    if not isinstance(d, dict) or d.get('_ou_faded'):
        return
    mt = market_total
    if mt is None:
        mt = _safe_float(d.get('book_total')) or _safe_float(d.get('market_total'))
    if mt is None:
        return
    faded = False
    for key in ('xgb_total', 'our_total', 'xsharp_total', 'naive_total', 'predicted_total'):
        if d.get(key) is not None:
            flipped = _fade_total_around_line(d[key], mt)
            if flipped is not None:
                d[key] = flipped
                faded = True
    if not faded:
        return
    if d.get('xgb_spread') is not None and d.get('xgb_total') is not None:
        h, a = _scores_from_spread_total(d['xgb_spread'], d['xgb_total'])
        if h is not None:
            d['xgb_home_score'] = h
            d['xgb_away_score'] = a
    if d.get('our_spread') is not None and d.get('our_total') is not None:
        h, a = _scores_from_spread_total(d['our_spread'], d['our_total'])
        if h is not None:
            d['our_home_pts'] = h
            d['our_away_pts'] = a
    d['_ou_faded'] = True


def _recompute_ml_correct_after_fade(g: dict) -> None:
    """Refresh ens_correct after ML fade for daily results grading."""
    home_won = g.get('home_win')
    if home_won is None:
        return
    ep = _safe_float(g.get('ens_prob'))
    if ep is None:
        ep = _safe_float(g.get('ensemble_prob'))
    if ep is None:
        return
    if ep > 1:
        ep = ep / 100.0
    g['ens_correct'] = (ep >= 0.5) == home_won


def _apply_model_fades_for_sport(sport, d: dict, *, market_total=None) -> None:
    if sport in SPREAD_FADE_SPORTS:
        _apply_spread_fade(d)
    elif sport in XSHARP_SPREAD_FADE_SPORTS:
        _apply_xsharp_spread_fade(d)
    if sport in SOCCER_ML_FADE_SPORTS:
        _apply_soccer_ml_fade(d)
    elif sport == 'WNBA':
        _apply_selective_ml_fade(d, WNBA_ML_FADE_PROB_KEYS)
    if sport in OU_FADE_SPORTS:
        _apply_ou_fade(d, market_total=market_total)


def _apply_fades_to_daily_results(sport, daily_results) -> None:
    """Apply model-parameter fades + regrade ML flags for a daily_results dict."""
    if not daily_results:
        return
    needs_ml_regrade = sport in SOCCER_ML_FADE_SPORTS or sport == 'WNBA'
    for bucket in daily_results.values():
        for g in bucket.get('games') or []:
            if not isinstance(g, dict):
                continue
            _apply_model_fades_for_sport(sport, g)
            if needs_ml_regrade:
                _recompute_daily_ml_grading_after_fade(g, sport)


def _apply_model_fades_batch(sport, items) -> None:
    if not items:
        return
    for d in items:
        if isinstance(d, dict):
            _apply_model_fades_for_sport(sport, d)


def _apply_mlb_spread_fade_batch(sport, items) -> None:
    _apply_model_fades_batch(sport, items)


# Card face win %: best model per sport (PL column = ensemble; XSharp = xgb_* only).
BEST_MODEL_BY_SPORT = {
    'NHL': ('ensemble_prob', 'Sharp Consensus'),
    'MLB': ('ensemble_prob', 'Sharp Consensus'),
    'SOCCER': ('xgb_prob', 'XSharp'),
    'NBA': ('ensemble_prob', 'Sharp Consensus'),
    'WNBA': ('ensemble_prob', 'Sharp Consensus'),
    'NCAAB': ('ensemble_prob', 'Sharp Consensus'),
    'NCAAW': ('ensemble_prob', 'Sharp Consensus'),
    'NFL': ('ensemble_prob', 'Sharp Consensus'),
    'NCAAF': ('ensemble_prob', 'Sharp Consensus'),
}


def _scores_from_spread_total(spread, total):
    """Project home/away points from spread+total; home+away always equals total exactly."""
    try:
        s, t = float(spread), float(total)
    except (TypeError, ValueError):
        return None, None

    def _half(v):
        return round(v * 2) / 2.0

    home = _half((t + s) / 2.0)
    away = t - home  # derived so home + away == total exactly
    if home == away and abs(s) >= 0.25:
        if s > 0:
            home += 0.5
            away -= 0.5
        elif s < 0:
            away += 0.5
            home -= 0.5
    return home, away


def _safe_float(value, default=None):
    """Coerce DB/model values to float; reject corrupt bytes and NaN."""
    if value is None:
        return default
    if isinstance(value, (bytes, bytearray)):
        return default
    try:
        import math as _mf
        out = float(value)
        if _mf.isnan(out) or _mf.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def _espn_event_id_for_book(sport, game_id, game_date, home_team, away_team):
    """ESPN event id for Core odds — numeric game_id or scoreboard matchup lookup."""
    raw = str(game_id or '').split('_')[-1]
    if raw.isdigit() and raw.startswith('401'):
        return raw
    if sport in CORE_API_SPORT_PATHS and game_date and home_team and away_team:
        resolved = _resolve_espn_event_id_by_matchup(
            sport, game_date, home_team, away_team,
        )
        if resolved:
            return str(resolved)
    return raw if raw.isdigit() else None


def _book_row_is_synthetic(source) -> bool:
    src = (source or '').lower()
    # ESPN Core API and pl_book_odds_api rows are always real sportsbook data.
    # The word "fallback" in ESPN source names refers to the *event-ID resolution
    # method* (matchup lookup vs direct ID), not to synthetic/model-generated odds.
    if 'espn' in src or 'pl_book_odds' in src or 'draftkings' in src:
        return False
    return 'model' in src or 'engine' in src or 'fallback' in src


def _bulk_load_book_lines_from_db(sport, game_ids):
    """Latest betting_lines row per game_id (spread, total, ML)."""
    by_gid = {}
    if not game_ids:
        return by_gid
    try:
        conn = get_db_connection()
        chunk = 400
        for i in range(0, len(game_ids), chunk):
            part = [str(g) for g in game_ids[i:i + chunk]]
            ph = ','.join('?' * len(part))
            rows = conn.execute(
                f"""SELECT game_id, spread, total, home_moneyline, away_moneyline, source
                    FROM betting_lines WHERE sport=? AND game_id IN ({ph})
                    ORDER BY fetched_at DESC""",
                [sport] + part,
            ).fetchall()
            for r in rows:
                gid = str(r['game_id'])
                if gid not in by_gid:
                    by_gid[gid] = dict(r)
        conn.close()
    except Exception as _dbe:
        logger.debug(f"[book bulk db] {sport}: {_dbe}")
    return by_gid


def _bulk_load_book_lines_by_matchup(sport, limit=5000):
    """Latest betting_lines row per (game_date, home, away) when game_id keys differ."""
    by_key = {}
    try:
        conn = get_db_connection()
        cols = [r['name'] for r in conn.execute("PRAGMA table_info('betting_lines')").fetchall()]
        has_extra = any(c in cols for c in ('game_date', 'home_team', 'away_team'))
        if has_extra:
            rows = conn.execute(
                """SELECT game_id, game_date, home_team, away_team, spread, total,
                          home_moneyline, away_moneyline, source
                   FROM betting_lines WHERE sport=?
                   ORDER BY fetched_at DESC LIMIT ?""",
                (sport, int(limit)),
            ).fetchall()
            for r in rows:
                gd = (r['game_date'] or '')[:10]
                hk = _normalize_team_key_for_sport(sport, r['home_team'])
                ak = _normalize_team_key_for_sport(sport, r['away_team'])
                if gd and hk and ak:
                    key = (gd, hk, ak)
                    if key not in by_key:
                        by_key[key] = dict(r)
        conn.close()
    except Exception as _dbe:
        logger.debug(f"[book matchup db] {sport}: {_dbe}")
    return by_key


def _apply_db_book_row_to_pred(pred: dict, row: dict) -> None:
    """Hydrate book_* from a betting_lines row (never model-sourced rows)."""
    if _book_row_is_synthetic(row.get('source')):
        return
    if row.get('home_moneyline') is not None and pred.get('book_home_moneyline') is None:
        pred['book_home_moneyline'] = int(row['home_moneyline'])
    if row.get('away_moneyline') is not None and pred.get('book_away_moneyline') is None:
        pred['book_away_moneyline'] = int(row['away_moneyline'])
    if row.get('spread') is not None and pred.get('book_spread') is None:
        pred['book_spread'] = row['spread']
    if row.get('total') is not None and pred.get('book_total') is None:
        pred['book_total'] = row['total']
    if pred.get('book_spread') is not None or pred.get('book_home_moneyline') is not None:
        pred.setdefault('book_odds_source', row.get('source') or 'betting_lines')
    _ensure_book_moneylines(pred)


def _hydrate_book_lines_db_only(sport, predictions):
    """Fill book_* from betting_lines only (no ESPN HTTP — safe inside get_upcoming)."""
    if not predictions:
        return
    upcoming = [
        p for p in predictions
        if isinstance(p, dict) and p.get('home_score') is None and p.get('game_id')
    ]
    if not upcoming:
        return
    by_gid = _bulk_load_book_lines_from_db(
        sport, [str(p['game_id']) for p in upcoming],
    )
    by_key = _bulk_load_book_lines_by_matchup(sport)
    for pred in upcoming:
        gid = str(pred.get('game_id') or '')
        row = by_gid.get(gid)
        if not row:
            gd = (pred.get('game_date') or '')[:10]
            hk = _normalize_team_key_for_sport(sport, pred.get('home_team_id'))
            ak = _normalize_team_key_for_sport(sport, pred.get('away_team_id'))
            if gd and hk and ak:
                row = by_key.get((gd, hk, ak))
        if row:
            _apply_db_book_row_to_pred(pred, row)


def _refresh_books_on_predictions(sport, predictions, today_date=None, prioritize=None):
    """DB hydrate + ESPN Core book lines for every sport on the picks path."""
    if not predictions:
        return
    if today_date is None:
        try:
            today_date = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
        except Exception:
            today_date = datetime.now().strftime('%Y-%m-%d')
    _hydrate_book_lines_db_only(sport, predictions)
    _prio = prioritize if prioritize is not None else _upcoming_preds_for_book_fetch(
        predictions, today_date,
    )
    _attach_pl_book_odds_to_predictions(
        sport,
        predictions,
        limit=_PL_BOOK_ODDS_LIMIT_BY_SPORT.get(sport, 60),
        prioritize=_prio,
    )


# Throttle state for off-request-path live book-odds refresh (see
# _start_background_book_refresh). Keyed by sport -> last-start epoch seconds.
_BOOK_REFRESH_TS: dict = {}
_BOOK_REFRESH_MIN_INTERVAL = 120  # seconds between background refreshes per sport


def _start_background_book_refresh(sport, predictions, today_date=None, prioritize=None):
    """Refresh live sportsbook odds OFF the request path.

    The picks page hydrates book_* from the betting_lines DB synchronously (fast)
    and calls this to pull fresh ESPN Core / DraftKings lines in a throttled
    daemon thread. Previously the live fetch (up to ~60-80 HTTP calls, up to 3x
    per page load) ran on the request path and made cold loads take ~45s. Fresh
    lines are persisted back to betting_lines so the next request renders them
    from the DB -- mirroring the results page's background score-sync pattern.
    """
    if not predictions:
        return
    # Only bother if a visible upcoming card is still missing complete book odds
    # after the synchronous DB hydrate.
    try:
        needs = [
            p for p in predictions
            if isinstance(p, dict) and p.get('home_score') is None
            and p.get('game_id') and _pred_needs_book_fetch(p)
        ]
    except Exception:
        needs = []
    if not needs:
        return
    now_ts = _time.time()
    last = _BOOK_REFRESH_TS.get(sport)
    if last is not None and (now_ts - last) < _BOOK_REFRESH_MIN_INTERVAL:
        return
    _BOOK_REFRESH_TS[sport] = now_ts
    # Deep-copy so the worker never mutates request-owned dicts mid-render.
    try:
        snapshot = _copy.deepcopy(needs)
        prio_snapshot = _copy.deepcopy([
            p for p in prioritize
            if isinstance(p, dict) and p.get('home_score') is None and p.get('game_id')
        ]) if prioritize else None
    except Exception:
        return

    def _run():
        try:
            _attach_pl_book_odds_to_predictions(
                sport,
                snapshot,
                limit=_PL_BOOK_ODDS_LIMIT_BY_SPORT.get(sport, 60),
                prioritize=prio_snapshot,
            )
        except Exception as _bg_bk:
            logger.debug(f"[{sport}] background book refresh failed: {_bg_bk}")

    try:
        import threading as _thr
        _thr.Thread(target=_run, daemon=True, name=f'book-refresh-{sport}').start()
    except Exception as _bt:
        logger.debug(f"[{sport}] could not start background book refresh: {_bt}")


# Single-flight guard for stale-while-revalidate prediction rebuilds. A normal
# request serves the (possibly stale) cache instantly and, when it is past TTL,
# kicks the heavy model build here onto a daemon thread so no request blocks on
# it. Only one rebuild runs per sport at a time.
import threading as _preds_thr
_PREDICTIONS_REFRESH_INFLIGHT: set = set()
_PREDICTIONS_REFRESH_LOCK = _preds_thr.Lock()


def _start_background_predictions_refresh(sport, days=365):
    """Recompute a sport's prediction slate OFF the request path.

    Spawns a daemon thread that calls get_upcoming_predictions(..., _force_rebuild=True),
    which rebuilds the cards, refreshes book odds and repopulates _PREDICTIONS_CACHE.
    Single-flighted per sport so overlapping stale requests don't stack rebuilds.
    """
    with _PREDICTIONS_REFRESH_LOCK:
        if sport in _PREDICTIONS_REFRESH_INFLIGHT:
            return
        _PREDICTIONS_REFRESH_INFLIGHT.add(sport)

    def _run():
        try:
            get_upcoming_predictions(sport, days=days, _force_rebuild=True)
        except Exception as _bg_pred:
            logger.debug(f"[{sport}] background predictions refresh failed: {_bg_pred}")
        finally:
            with _PREDICTIONS_REFRESH_LOCK:
                _PREDICTIONS_REFRESH_INFLIGHT.discard(sport)

    try:
        _preds_thr.Thread(
            target=_run, daemon=True, name=f'preds-refresh-{sport}',
        ).start()
    except Exception as _pt:
        # If the thread can't start, clear the flag so a later request retries.
        with _PREDICTIONS_REFRESH_LOCK:
            _PREDICTIONS_REFRESH_INFLIGHT.discard(sport)
        logger.debug(f"[{sport}] could not start background predictions refresh: {_pt}")


def _upcoming_preds_for_book_fetch(predictions, today_date, horizon_days=8):
    """Upcoming games from today through horizon — picks page book API budget."""
    try:
        end = (
            datetime.strptime(today_date, '%Y-%m-%d') + timedelta(days=horizon_days)
        ).strftime('%Y-%m-%d')
    except Exception:
        end = today_date
    out = []
    for pred in predictions:
        if not isinstance(pred, dict) or pred.get('home_score') is not None:
            continue
        gd = pred.get('game_date') or ''
        if today_date <= gd <= end:
            out.append(pred)
    out.sort(key=lambda p: (p.get('game_date') or '', str(p.get('game_id') or '')))
    return out


def _pred_needs_book_fetch(pred: dict) -> bool:
    return not (
        pred.get('book_spread') is not None
        and pred.get('book_total') is not None
        and _valid_book_ml(pred.get('book_home_moneyline'))
        and _valid_book_ml(pred.get('book_away_moneyline'))
    )


def _attach_pl_book_odds_to_predictions(sport, predictions, limit=30, prioritize=None):
    """Attach sportsbook lines (DB cache first, then ESPN Core / DraftKings) for card display."""
    if not predictions:
        return
    try:
        from pl_book_odds_api import build_pl_book_odds
    except ImportError:
        build_pl_book_odds = None
    upcoming = [
        p for p in predictions
        if isinstance(p, dict) and p.get('home_score') is None and p.get('game_id')
    ]
    if not upcoming:
        return

    by_gid = _bulk_load_book_lines_from_db(
        sport, [str(p['game_id']) for p in upcoming],
    )
    by_key = _bulk_load_book_lines_by_matchup(sport)
    for pred in upcoming:
        gid = str(pred.get('game_id') or '')
        row = by_gid.get(gid)
        if not row:
            gd = (pred.get('game_date') or '')[:10]
            hk = _normalize_team_key_for_sport(sport, pred.get('home_team_id'))
            ak = _normalize_team_key_for_sport(sport, pred.get('away_team_id'))
            if gd and hk and ak:
                row = by_key.get((gd, hk, ak))
        if row:
            _apply_db_book_row_to_pred(pred, row)

    priority_ids = set()
    ordered = []
    if prioritize:
        for pred in prioritize:
            if (
                isinstance(pred, dict)
                and pred.get('home_score') is None
                and pred.get('game_id')
            ):
                ordered.append(pred)
                priority_ids.add(str(pred['game_id']))
    for pred in upcoming:
        gid = str(pred.get('game_id') or '')
        if gid not in priority_ids:
            ordered.append(pred)

    today = datetime.now().strftime('%Y-%m-%d')
    ordered.sort(
        key=lambda p: (
            0 if str(p.get('game_id')) in priority_ids else 1,
            0 if (p.get('game_date') or '') >= today else 1,
            p.get('game_date') or '0000',
            str(p.get('game_id') or ''),
        ),
        reverse=True,
    )

    attempts = 0
    for pred in ordered:
        if attempts >= limit:
            break
        if not _pred_needs_book_fetch(pred):
            continue
        game_id = pred.get('game_id')
        home = pred.get('home_team_id', '')
        away = pred.get('away_team_id', '')
        gd = pred.get('game_date')
        espn_eid = _espn_event_id_for_book(sport, game_id, gd, home, away)
        if not espn_eid:
            continue
        attempts += 1
        row = None
        if build_pl_book_odds:
            row = build_pl_book_odds(
                sport,
                game_id,
                home,
                away,
                gd,
                league_name=pred.get('league') or pred.get('league_name'),
                espn_event_id=espn_eid,
            )
        if row:
            _apply_pl_book_row_to_game(pred, row)
            _persist_pl_book_row(sport, pred, row)
            continue
        try:
            live = _fetch_live_market_line(
                sport, game_id, gd, home, away,
                league_name=pred.get('league') or pred.get('league_name'),
            )
        except Exception:
            live = None
        if live and (live.get('spread') is not None or live.get('total') is not None):
            if live.get('spread') is not None:
                pred['book_spread'] = live['spread']
            if live.get('total') is not None:
                pred['book_total'] = live['total']
            if live.get('home_moneyline') is not None:
                pred['book_home_moneyline'] = int(live['home_moneyline'])
            if live.get('away_moneyline') is not None:
                pred['book_away_moneyline'] = int(live['away_moneyline'])
            pred['book_odds_source'] = live.get('source') or 'ESPN Core API'
            _ensure_book_moneylines(pred)

    for pred in upcoming:
        _ensure_book_moneylines(pred)


def _fetch_pl_book_line_for_game(sport, game_id, home, away, game_date, league_name=None):
    """ESPN Core / DK closing line for one game (completed or upcoming)."""
    try:
        from pl_book_odds_api import build_pl_book_odds
    except ImportError:
        return None
    gid = str(game_id or '')
    if not gid.split('_')[-1].isdigit():
        return None
    return build_pl_book_odds(
        sport, gid, home or '', away or '', game_date, league_name=league_name,
    )


def _valid_book_ml(val) -> bool:
    try:
        return val is not None and int(val) != 0
    except (TypeError, ValueError):
        return False


def _ensure_book_moneylines(pred: dict) -> None:
    """Fill book_home/away_moneyline when spread exists but ESPN live path omitted ML.

    Priority for the spread source used to estimate ML:
    1. book_spread  — real sportsbook spread (best)
    2. disp_book_spread — display-flip of book_spread (same data)
    3. market_spread — PL engine-computed spread (last resort, labeled Est.)
    """
    if _valid_book_ml(pred.get('book_home_moneyline')) and _valid_book_ml(pred.get('book_away_moneyline')):
        return
    # Soccer (and other 3-way markets) must NOT derive moneylines from a 2-way
    # spread — that produces nonsensical odds where both teams show '+' money.
    # Only real 3-way sportsbook lines (attached elsewhere) should populate these;
    # otherwise leave them blank so the card shows '—' instead of fake numbers.
    _is_threeway = (
        pred.get('draw_prob') is not None
        or pred.get('face_draw_prob') is not None
        or pred.get('pl_model_draw_ml') is not None
        or str(pred.get('game_id') or '').upper().startswith('SOCCER')
    )
    if _is_threeway:
        return
    bs = _safe_float(pred.get('book_spread'))
    if bs is None:
        ds = _safe_float(pred.get('disp_book_spread'))
        if ds is not None:
            bs = -ds
            pred['book_spread'] = bs
    # Last resort: use the PL engine's spread projection (labeled Est.) so the
    # pick card always shows a number rather than — when ESPN has no odds data.
    if bs is None:
        ms = _safe_float(pred.get('market_spread'))
        if ms is not None:
            bs = ms
    if bs is None:
        return
    try:
        from pl_book_odds_api import _ml_from_spread_fallback
        h_ml, a_ml = _ml_from_spread_fallback(bs)
    except Exception:
        return
    if not _valid_book_ml(pred.get('book_home_moneyline')) and h_ml is not None:
        pred['book_home_moneyline'] = int(h_ml)
        pred['book_ml_estimated'] = True
    if not _valid_book_ml(pred.get('book_away_moneyline')) and a_ml is not None:
        pred['book_away_moneyline'] = int(a_ml)
        pred['book_ml_estimated'] = True


def _apply_pl_book_row_to_game(g: dict, row: dict) -> None:
    """Merge sportsbook row onto a game/pred dict — book_* only, never model market_*."""
    if not row:
        return
    if row.get('spread') is not None:
        g['book_spread'] = row['spread']
    if row.get('total') is not None:
        g['book_total'] = row['total']
    if row.get('home_moneyline') is not None:
        g['book_home_moneyline'] = row['home_moneyline']
    if row.get('away_moneyline') is not None:
        g['book_away_moneyline'] = row['away_moneyline']
    if row.get('provider'):
        g['book_provider'] = row['provider']
    g['book_odds_source'] = row.get('source') or 'pl_book_odds_api'
    _ensure_book_moneylines(g)


def _persist_pl_book_row(sport, g: dict, row: dict) -> None:
    if not row or not g.get('game_id'):
        return
    try:
        conn = get_db_connection()
        _upsert_betting_line(
            conn,
            sport,
            str(g['game_id']),
            g.get('date') or g.get('game_date'),
            g.get('home') or g.get('home_team_id'),
            g.get('away') or g.get('away_team_id'),
            row.get('spread'),
            row.get('total'),
            row.get('source') or 'pl_book_odds_api',
            home_moneyline=row.get('home_moneyline'),
            away_moneyline=row.get('away_moneyline'),
        )
        conn.commit()
        conn.close()
    except Exception as _pe:
        logger.debug(f"[book persist] {sport} {g.get('game_id')}: {_pe}")


def _attach_book_odds_to_daily_results(sport, daily_results, api_limit=25):
    """Attach Books ML/spread/total on completed games (DB lines first, then ESPN API)."""
    if not daily_results:
        return
    games = []
    for dd in daily_results.values():
        for g in dd.get('games', []):
            games.append(g)
    if not games:
        return
    missing_totals = sum(1 for g in games if _safe_float(g.get('book_total')) is None)
    if api_limit is None or api_limit < 0:
        api_limit = min(300, max(missing_totals, 40))
    elif api_limit > 0 and missing_totals > api_limit:
        api_limit = min(300, missing_totals)
    game_ids = [str(g['game_id']) for g in games if g.get('game_id')]
    by_gid = _bulk_load_book_lines_from_db(sport, game_ids)
    by_key = _bulk_load_book_lines_by_matchup(sport)
    games.sort(key=lambda g: (g.get('date') or ''), reverse=True)
    api_attempts = 0
    try:
        from pl_book_odds_api import build_pl_book_odds
    except ImportError:
        build_pl_book_odds = None
    for g in games:
        gid = str(g.get('game_id') or '')
        gd = (g.get('date') or '')[:10]
        hk = _normalize_team_key_for_sport(sport, g.get('home'))
        ak = _normalize_team_key_for_sport(sport, g.get('away'))
        row = by_gid.get(gid) if gid else None
        if not row and gd and hk and ak:
            row = by_key.get((gd, hk, ak))
        if row:
            if row.get('home_moneyline') is not None:
                g['book_home_moneyline'] = int(row['home_moneyline'])
            if row.get('away_moneyline') is not None:
                g['book_away_moneyline'] = int(row['away_moneyline'])
            if row.get('spread') is not None and g.get('book_spread') is None:
                g['book_spread'] = row['spread']
            if row.get('total') is not None and g.get('book_total') is None:
                g['book_total'] = row['total']
        # Skip API only when we already have the O/U line (spread alone is not enough).
        if g.get('book_total') is not None:
            continue
        if not build_pl_book_odds or api_attempts >= api_limit:
            continue
        raw_eid = gid.split('_')[-1]
        if not raw_eid.isdigit():
            continue
        api_row = build_pl_book_odds(
            sport, gid, g.get('home', ''), g.get('away', ''), g.get('date'),
            league_name=g.get('league') or g.get('league_name'),
        )
        api_attempts += 1
        if not api_row:
            continue
        _apply_pl_book_row_to_game(g, api_row)
        _persist_pl_book_row(sport, g, api_row)


def _set_card_book_lines(card: dict) -> None:
    """Books column — only real sportsbook fields (never model/engine ML or market lines)."""
    bs = _safe_float(card.get('book_spread'))
    if bs is not None:
        card['disp_book_spread'] = _round_to_half(-bs)
    bt = _safe_float(card.get('book_total'))
    if bt is not None:
        card['disp_book_total'] = _round_to_half(bt)


def _pl_home_prob_for_spread_display(card: dict):
    """Home win % (0–100) used to sign-normalize disp_pl_spread for card UI only."""
    disp = _safe_float(card.get('disp_ml_prob'))
    if disp is not None:
        return _normalize_home_win_prob_pct(disp)
    # Do not use _ensemble_prob_pre_enforce here; it is intentionally pre-correction
    # diagnostic state and can re-introduce spread/winner contradictions on cards.
    for key in ('ensemble_prob', 'ens_prob'):
        v = _safe_float(card.get(key))
        if v is not None:
            return _normalize_home_win_prob_pct(v)
    return None


def _set_card_pl_spread(card: dict, sport: str = 'NBA') -> None:
    """Populate disp_pl_spread; flip sign only when it opposes PL ML direction.

    our_spread / disp_pl_spread use home-centric convention (positive = home favored),
    matching fmt_spread_line and pl_book_odds_api book_spread after disp_book flip.
    Does not mutate our_spread or model probabilities.
    """
    sp = _best_pl_spread(card)
    if sp is None:
        sp = _first_pred_float(card, ('our_spread', 'market_spread', 'naive_spread'))
    if sp is None:
        card.pop('disp_pl_spread', None)
        return
    sp = _round_to_half(float(sp))
    # Align spread sign to moneyline pick so projected scores / run line agree.
    # MLB previously skipped this ("faded" spread) and showed Nationals 6–Phillies 4.5
    # while Consensus/G2 picked Phillies. Use ensemble ML — not efficiency-derived
    # disp_ml_prob — so we do not no-op when disp_ml_prob was itself spread-built.
    if sport == 'MLB':
        hp = None
        for key in ('ensemble_prob', 'ens_prob'):
            v = _safe_float(card.get(key))
            if v is not None:
                hp = _normalize_home_win_prob_pct(v)
                break
        if hp is None:
            hp = _pl_home_prob_for_spread_display(card)
    else:
        hp = _pl_home_prob_for_spread_display(card)
    if hp is not None and sp != 0 and abs(hp - 50.0) >= 0.05:
        home_ml_fav = hp > 50.0
        if home_ml_fav and sp < 0:
            sp = -sp
        elif not home_ml_fav and sp > 0:
            sp = -sp
    card['disp_pl_spread'] = sp


def _set_card_projected_scores(card: dict) -> None:
    """Projected Score box — derive PL/XSharp from spread+total (half-point increments)."""
    _home_id = card.get('home_team_id') or card.get('home')
    _away_id = card.get('away_team_id') or card.get('away')
    _picked = card.get('predicted_winner') or card.get('face_pick_team')

    ps = _safe_float(card.get('disp_pl_spread')) or _safe_float(card.get('our_spread'))
    pt = _safe_float(card.get('disp_pl_total')) or _safe_float(card.get('our_total'))
    if ps is not None and pt is not None:
        xh, xa = _scores_from_spread_total(ps, pt)
        if xh is not None:
            _pl_winner = _home_id if xh >= xa else _away_id
            if _picked and _pl_winner and _pl_winner != _picked:
                # Regenerate: swap so the ML pick has the higher projected score.
                # Keep total; flip displayed PL spread to match.
                card['pl_proj_home_pts'] = _round_to_half(xa)
                card['pl_proj_away_pts'] = _round_to_half(xh)
                if _safe_float(card.get('disp_pl_spread')) is not None:
                    card['disp_pl_spread'] = -float(card['disp_pl_spread'])
            else:
                card['pl_proj_home_pts'] = _round_to_half(xh)
                card['pl_proj_away_pts'] = _round_to_half(xa)

    xs = _safe_float(card.get('disp_xs_spread')) or _safe_float(card.get('xgb_spread'))
    xt = _safe_float(card.get('disp_xs_total')) or _safe_float(card.get('xgb_total'))
    if xs is not None and xt is not None:
        xh, xa = _scores_from_spread_total(xs, xt)
        if xh is not None:
            _xs_winner = _home_id if xh >= xa else _away_id
            if _picked and _xs_winner and _xs_winner != _picked:
                card['xs_proj_home_pts'] = _round_to_half(xa)
                card['xs_proj_away_pts'] = _round_to_half(xh)
                if _safe_float(card.get('disp_xs_spread')) is not None:
                    card['disp_xs_spread'] = -float(card['disp_xs_spread'])
            else:
                card['xs_proj_home_pts'] = _round_to_half(xh)
                card['xs_proj_away_pts'] = _round_to_half(xa)


def _set_card_pl_moneylines(card: dict) -> None:
    """PL moneyline hero on each pick card — derived from ensemble model probability.

    Uses ens_prob / ensemble_prob (consensus of Glicko2, TrueSkill, XGBoost, ELO)
    with vig applied.  This keeps PL completely independent of Books and XSharp.
    Soccer: 3-way home/draw/away when draw_prob is present.
    """
    draw_pct = _safe_float(card.get('draw_prob'))
    home_win_pct = _safe_float(card.get('home_win_prob'))
    away_win_pct = _safe_float(card.get('away_win_prob'))
    if draw_pct is not None and home_win_pct is not None:
        if away_win_pct is None:
            away_win_pct = max(0.0, 100.0 - home_win_pct - draw_pct)
        ml = _compute_odds_from_threeway(
            home_win_pct, draw_pct, away_win_pct, apply_vig=True, clamp_ml=True,
        )
        if ml:
            card['pl_model_home_ml'] = ml.get('moneyline_home')
            card['pl_model_draw_ml'] = ml.get('moneyline_draw')
            card['pl_model_away_ml'] = ml.get('moneyline_away')
        return
    # V2 games (NHL/NBA/MLB/NFL): always use calibrated ensemble_prob for PL odds.
    # disp_ml_prob is efficiency-spread-derived and can point the opposite direction
    # from the V2 model — e.g. efficiency says Knicks -4 (Spurs 37%) while V2 says
    # Spurs 64%. Using disp_ml_prob would produce Spurs +198 while the pick is Spurs.
    # The favourite picked by the model MUST have a negative moneyline.
    if card.get('is_v2'):
        prob = (_safe_float(card.get('ensemble_prob'))
                or _safe_float(card.get('ens_prob')))
    else:
        prob = (_safe_float(card.get('disp_ml_prob'))
                or _safe_float(card.get('ens_prob'))
                or _safe_float(card.get('ensemble_prob')))
    ml = _compute_odds_from_prob(prob, apply_vig=True, clamp_ml=True)
    if ml:
        card['pl_model_home_ml'] = ml.get('moneyline_home')
        card['pl_model_away_ml'] = ml.get('moneyline_away')


def _attach_nba_efficiency_to_daily_results(sport, daily_results) -> None:
    """Attach PL spread/total (and scores) on completed NBA games from efficiency data."""
    if sport not in ('NBA', 'WNBA') or not daily_results:
        return
    try:
        from team_efficiency import precompute_team_efficiencies, compute_efficiency_projection_from
        from weighted_total_predictor import prefetch_recent_scoreboards
    except ImportError:
        return
    try:
        prefetch_recent_scoreboards(sport=sport, days=14)
        teams, games = set(), []
        for dd in daily_results.values():
            for g in dd.get('games', []):
                h, a = g.get('home'), g.get('away')
                if h and a:
                    teams.add(h)
                    teams.add(a)
                    games.append(g)
        if not teams:
            return
        eff_map = precompute_team_efficiencies(
            list(teams), sport=sport, n_games=5,
            max_lookback_days=14, total_budget_seconds=12.0, max_workers=12,
        )
        for g in games:
            h, a = g.get('home'), g.get('away')
            he, ae = eff_map.get(h), eff_map.get(a)
            if not (he and ae):
                continue
            proj = compute_efficiency_projection_from(
                he, ae, sport=sport,
                xsharp_total=g.get('xgb_total'),
                xsharp_spread=g.get('xgb_spread'),
            )
            if g.get('our_spread') is None and proj.get('projected_spread') is not None:
                g['our_spread'] = _round_to_half(proj['projected_spread'])
            if g.get('our_total') is None and proj.get('projected_total') is not None:
                g['our_total'] = _round_to_half(proj['projected_total'])
            if g.get('our_home_pts') is None and proj.get('home_pts') is not None:
                g['our_home_pts'] = round(float(proj['home_pts']))
            if g.get('our_away_pts') is None and proj.get('away_pts') is not None:
                g['our_away_pts'] = round(float(proj['away_pts']))
    except Exception as _nba_eff:
        logger.debug(f"[nba-eff] daily results attach failed: {_nba_eff}")


def _fill_xsharp_from_efficiency_if_missing(g: dict, sport: str) -> None:
    """When XGB predict misses a team, use efficiency projection for XSharp display/grading."""
    if sport not in ('NBA', 'WNBA') or (g.get('xgb_spread') is not None and g.get('xgb_total') is not None):
        return
    h, a = g.get('home'), g.get('away')
    if not (h and a):
        return
    try:
        from team_efficiency import compute_efficiency_projection
    except ImportError:
        return
    try:
        proj = compute_efficiency_projection(h, a, sport=sport, n_games=5)
        if not proj:
            return
        if g.get('xgb_spread') is None and proj.get('projected_spread') is not None:
            g['xgb_spread'] = _round_to_half(proj['projected_spread'])
        if g.get('xgb_total') is None and proj.get('projected_total') is not None:
            g['xgb_total'] = _round_to_half(proj['projected_total'])
    except Exception as _xfe:
        logger.debug(f"[eff] xsharp fallback failed {h} vs {a}: {_xfe}")


def _prepare_result_card_display(g: dict, sport: str) -> None:
    """Display fields for results cards (same layout as predictions)."""
    g['away_team_id'] = g.get('away') or g.get('away_team_id')
    g['home_team_id'] = g.get('home') or g.get('home_team_id')
    xs = _first_pred_float(g, ('xgb_spread', 'xsharp_spread'))
    if xs is not None:
        g['disp_xs_spread'] = _round_to_half(xs)
    pt = _best_pl_total(g)
    if pt is not None:
        g['disp_pl_total'] = pt
    elif g.get('our_total') is not None:
        g['disp_pl_total'] = _round_to_half(g['our_total'])
    xt = _first_pred_float(g, ('xgb_total', 'xsharp_total', 'xgb_total_adj'))
    if xt is not None:
        g['disp_xs_total'] = _round_to_half(xt)
    _set_card_book_lines(g)
    _set_card_pl_moneylines(g)
    _set_card_pl_spread(g, sport=sport)
    _set_card_projected_scores(g)
    _set_card_edge_pct(g, sport=sport)


# ===== SECTION: Issue 5 — Efficiency moneyline grading for results tallies =====
def _grade_efficiency_ml_from_spread(sport, daily_results):
    """Cheap Team Efficiency moneyline grading from the PL/efficiency spread
    already attached during spread/total grading (no new ESPN/API calls).

    Sets efficiency_prob / efficiency_pick / efficiency_correct per game so the
    results tallies can surface Efficiency as a 6th graded model. Games without
    an efficiency/PL spread keep efficiency_prob=None and render as N/A ("—")."""
    if not daily_results:
        return
    try:
        from sports import team_efficiency_attach as _eff
    except Exception as _imp_e:
        logger.debug(f"[eff-ml] module import failed: {_imp_e}")
        return
    if sport not in _eff.EFFICIENCY_GRADING_SPORTS:
        return
    # Seed efficiency_spread from the already-computed PL/"Our" spread so the
    # grader uses a genuine efficiency projection rather than re-fetching ESPN.
    for dd in daily_results.values():
        for g in dd.get('games', []):
            if g.get('skip_grading'):
                continue
            if g.get('efficiency_spread') is None and g.get('our_spread') is not None:
                g['efficiency_spread'] = g['our_spread']
    try:
        _eff.apply_efficiency_ml_grading(sport, daily_results)
    except Exception as _eff_ml_e:
        logger.debug(f"[eff-ml] moneyline grading failed for {sport}: {_eff_ml_e}")


def _mark_exhibition_skip_grading(daily_results):
    """All-Star / placeholder sides must not pollute Last Night or Last-7 tallies.

    Picks already filter these; results historically graded TEAM COOP/SPOON etc.
    as real moneylines (0-1, no books) and the tally fallback treated that day as
    the latest slate.
    """
    if not daily_results:
        return
    for dd in daily_results.values():
        for g in dd.get('games') or []:
            if not isinstance(g, dict):
                continue
            if _is_exhibition_matchup(
                g.get('home') or g.get('home_team_id') or '',
                g.get('away') or g.get('away_team_id') or '',
                event_name=g.get('event_name') or '',
            ):
                g['skip_grading'] = True
                g['exhibition'] = True


def _gradable_result_games(games):
    """Games that count toward model / ATS tallies (excludes exhibitions & draws)."""
    out = []
    for g in games or []:
        if not isinstance(g, dict):
            continue
        if g.get('skip_grading'):
            continue
        out.append(g)
    return out


def _finalize_daily_result_cards(sport, daily_results):
    """Book lines + card display keys for every completed game (all sports)."""
    if not daily_results:
        return
    _mark_exhibition_skip_grading(daily_results)
    try:
        _attach_book_odds_to_daily_results(sport, daily_results, api_limit=25)
    except Exception as _bk:
        logger.debug(f"Book odds on results for {sport}: {_bk}")
    for dd in daily_results.values():
        for g in dd.get('games', []):
            _prepare_result_card_display(g, sport)
    # Derive Efficiency moneyline win% from the spread attached above.
    _grade_efficiency_ml_from_spread(sport, daily_results)




# ── Soccer team logos (ESPN CDN, persistent ID cache) ─────────────────────────
_SOCCER_TEAM_ESPN_ID_PATH = _os_v2.path.join(_V2_BASE, 'data', 'soccer_team_espn_ids.json')
_SOCCER_TEAM_ESPN_ID: dict = {}


def _load_soccer_team_espn_ids():
    global _SOCCER_TEAM_ESPN_ID
    try:
        if _os_v2.path.isfile(_SOCCER_TEAM_ESPN_ID_PATH):
            with open(_SOCCER_TEAM_ESPN_ID_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                _SOCCER_TEAM_ESPN_ID = {str(k): str(v) for k, v in raw.items()}
    except Exception as e:
        logger.debug(f"Could not load soccer team ESPN IDs: {e}")


def _save_soccer_team_espn_ids():
    try:
        _os_v2.makedirs(_os_v2.path.dirname(_SOCCER_TEAM_ESPN_ID_PATH), exist_ok=True)
        with open(_SOCCER_TEAM_ESPN_ID_PATH, 'w', encoding='utf-8') as f:
            json.dump(_SOCCER_TEAM_ESPN_ID, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.debug(f"Could not save soccer team ESPN IDs: {e}")


def _normalize_soccer_team_name(name):
    if not name:
        return ''
    s = str(name).strip().lower()
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _register_soccer_team(name, espn_id, *, persist=True):
    if not name or espn_id is None:
        return
    key = _normalize_soccer_team_name(name)
    if not key:
        return
    sid = str(espn_id).strip()
    if not sid:
        return
    if _SOCCER_TEAM_ESPN_ID.get(key) == sid:
        return
    _SOCCER_TEAM_ESPN_ID[key] = sid
    if persist:
        _save_soccer_team_espn_ids()


def _register_soccer_from_competitor(comp, *, persist=True):
    if not comp:
        return
    team = comp.get('team') or {}
    espn_id = team.get('id')
    if not espn_id:
        return
    for alias in (
        team.get('displayName'),
        team.get('shortDisplayName'),
        team.get('abbreviation'),
        team.get('name'),
    ):
        if alias:
            _register_soccer_team(alias, espn_id, persist=False)
    if persist:
        _save_soccer_team_espn_ids()


def _soccer_espn_logo_url(team_name):
    key = _normalize_soccer_team_name(team_name)
    espn_id = _SOCCER_TEAM_ESPN_ID.get(key) if key else None
    if espn_id:
        return f'https://a.espncdn.com/i/teamlogos/soccer/500/{espn_id}.png'
    return '/static/pl-logo.svg'


_load_soccer_team_espn_ids()


# ── Team logos (ESPN CDN) for prediction cards ───────────────────────────────
_TEAM_LOGO_SLUG = {
    'NBA': 'nba', 'MLB': 'mlb', 'NHL': 'nhl', 'NFL': 'nfl', 'WNBA': 'wnba',
    'NCAAB': 'mens-college-basketball', 'NCAAF': 'college-football',
}
_TEAM_NAME_TO_ABBR = {
    'NBA': {
        'Atlanta Hawks': 'atl', 'Boston Celtics': 'bos', 'Brooklyn Nets': 'bkn',
        'Charlotte Hornets': 'cha', 'Chicago Bulls': 'chi', 'Cleveland Cavaliers': 'cle',
        'Dallas Mavericks': 'dal', 'Denver Nuggets': 'den', 'Detroit Pistons': 'det',
        'Golden State Warriors': 'gs', 'Houston Rockets': 'hou', 'Indiana Pacers': 'ind',
        'LA Clippers': 'lac', 'Los Angeles Clippers': 'lac', 'Los Angeles Lakers': 'lal',
        'Memphis Grizzlies': 'mem', 'Miami Heat': 'mia', 'Milwaukee Bucks': 'mil',
        'Minnesota Timberwolves': 'min', 'New Orleans Pelicans': 'no', 'New York Knicks': 'ny',
        'Oklahoma City Thunder': 'okc', 'Orlando Magic': 'orl', 'Philadelphia 76ers': 'phi',
        'Phoenix Suns': 'phx', 'Portland Trail Blazers': 'por', 'Sacramento Kings': 'sac',
        'San Antonio Spurs': 'sa', 'Toronto Raptors': 'tor', 'Utah Jazz': 'utah',
        'Washington Wizards': 'wsh',
    },
    'MLB': {
        'Arizona Diamondbacks': 'ari', 'Atlanta Braves': 'atl', 'Baltimore Orioles': 'bal',
        'Boston Red Sox': 'bos', 'Chicago Cubs': 'chc', 'Chicago White Sox': 'chw',
        'Cincinnati Reds': 'cin', 'Cleveland Guardians': 'cle', 'Colorado Rockies': 'col',
        'Detroit Tigers': 'det', 'Houston Astros': 'hou', 'Kansas City Royals': 'kc',
        'Los Angeles Angels': 'laa', 'Los Angeles Dodgers': 'lad', 'Miami Marlins': 'mia',
        'Milwaukee Brewers': 'mil', 'Minnesota Twins': 'min', 'New York Mets': 'nym',
        'New York Yankees': 'nyy', 'Oakland Athletics': 'oak', 'Philadelphia Phillies': 'phi',
        'Pittsburgh Pirates': 'pit', 'San Diego Padres': 'sd', 'San Francisco Giants': 'sf',
        'Seattle Mariners': 'sea', 'St. Louis Cardinals': 'stl', 'Tampa Bay Rays': 'tb',
        'Texas Rangers': 'tex', 'Toronto Blue Jays': 'tor', 'Washington Nationals': 'wsh',
    },
    'NHL': {
        'Anaheim Ducks': 'ana', 'Arizona Coyotes': 'ari', 'Boston Bruins': 'bos',
        'Buffalo Sabres': 'buf', 'Calgary Flames': 'cgy', 'Carolina Hurricanes': 'car',
        'Chicago Blackhawks': 'chi', 'Colorado Avalanche': 'col', 'Columbus Blue Jackets': 'cbj',
        'Dallas Stars': 'dal', 'Detroit Red Wings': 'det', 'Edmonton Oilers': 'edm',
        'Florida Panthers': 'fla', 'Los Angeles Kings': 'la', 'Minnesota Wild': 'min',
        'Montreal Canadiens': 'mtl', 'Nashville Predators': 'nsh', 'New Jersey Devils': 'nj',
        'New York Islanders': 'nyi', 'New York Rangers': 'nyr', 'Ottawa Senators': 'ott',
        'Philadelphia Flyers': 'phi', 'Pittsburgh Penguins': 'pit', 'San Jose Sharks': 'sj',
        'Seattle Kraken': 'sea', 'St. Louis Blues': 'stl', 'Tampa Bay Lightning': 'tb',
        'Toronto Maple Leafs': 'tor', 'Utah Hockey Club': 'uta', 'Vancouver Canucks': 'van',
        'Vegas Golden Knights': 'vgk', 'Washington Capitals': 'wsh', 'Winnipeg Jets': 'wpg',
    },
    'NFL': {
        'Arizona Cardinals': 'ari', 'Atlanta Falcons': 'atl', 'Baltimore Ravens': 'bal',
        'Buffalo Bills': 'buf', 'Carolina Panthers': 'car', 'Chicago Bears': 'chi',
        'Cincinnati Bengals': 'cin', 'Cleveland Browns': 'cle', 'Dallas Cowboys': 'dal',
        'Denver Broncos': 'den', 'Detroit Lions': 'det', 'Green Bay Packers': 'gb',
        'Houston Texans': 'hou', 'Indianapolis Colts': 'ind', 'Jacksonville Jaguars': 'jax',
        'Kansas City Chiefs': 'kc', 'Las Vegas Raiders': 'lv', 'Los Angeles Chargers': 'lac',
        'Los Angeles Rams': 'lar', 'Miami Dolphins': 'mia', 'Minnesota Vikings': 'min',
        'New England Patriots': 'ne', 'New Orleans Saints': 'no', 'New York Giants': 'nyg',
        'New York Jets': 'nyj', 'Philadelphia Eagles': 'phi', 'Pittsburgh Steelers': 'pit',
        'San Francisco 49ers': 'sf', 'Seattle Seahawks': 'sea', 'Tampa Bay Buccaneers': 'tb',
        'Tennessee Titans': 'ten', 'Washington Commanders': 'wsh',
    },
    'WNBA': {
        'Atlanta Dream': 'atl', 'Chicago Sky': 'chi', 'Connecticut Sun': 'conn',
        'Dallas Wings': 'dal', 'Golden State Valkyries': 'gsv', 'Indiana Fever': 'ind',
        'Las Vegas Aces': 'lv', 'Los Angeles Sparks': 'la', 'Minnesota Lynx': 'min',
        'New York Liberty': 'ny', 'Phoenix Mercury': 'phx', 'Seattle Storm': 'sea',
        'Washington Mystics': 'wsh',
    },
}


def team_logo_url(sport: str, team_name: str) -> str:
    """ESPN team logo for prediction card header."""
    if not (sport and team_name):
        return '/static/pl-logo.svg'
    if sport == 'SOCCER':
        return _soccer_espn_logo_url(team_name)
    slug = _TEAM_LOGO_SLUG.get(sport)
    abbr = (_TEAM_NAME_TO_ABBR.get(sport) or {}).get(team_name)
    if not slug or not abbr:
        return '/static/pl-logo.svg'
    return f'https://a.espncdn.com/i/teamlogos/{slug}/500/{abbr}.png'




def _first_pred_float(pred: dict, keys):
    for key in keys:
        val = _safe_float(pred.get(key))
        if val is not None:
            return val
    return None


def _best_pl_spread(pred: dict):
    """PL Model spread: our_spread from odds engine — never XSharp or raw H2H avgs."""
    if pred.get('our_spread') is not None:
        try:
            return _round_to_half(float(pred['our_spread']))
        except (TypeError, ValueError):
            pass
    if pred.get('our_method') in ('efficiency', 'team-avg-fallback') and pred.get('our_spread') is not None:
        try:
            return _round_to_half(float(pred['our_spread']))
        except (TypeError, ValueError):
            pass
    if pred.get('our_avg_home') is not None and pred.get('our_avg_away') is not None:
        try:
            diff = float(pred['our_avg_home']) - float(pred['our_avg_away'])
            if abs(diff) >= 0.25:
                return _round_to_half(diff)
        except (TypeError, ValueError):
            pass
    if pred.get('our_home_pts') is not None and pred.get('our_away_pts') is not None:
        try:
            diff = float(pred['our_home_pts']) - float(pred['our_away_pts'])
            if abs(diff) >= 0.25:
                return _round_to_half(diff)
        except (TypeError, ValueError):
            pass
    if pred.get('our_spread') is not None and pred.get('our_total') is not None:
        try:
            return _round_to_half(float(pred['our_spread']))
        except (TypeError, ValueError):
            pass
    return None


def _best_pl_total(pred: dict):
    for _k in ('our_total', 'naive_total', 'market_total', 'h2h_last10_total'):
        _v = pred.get(_k)
        if _v is None:
            continue
        try:
            return _round_to_half(float(_v))
        except (TypeError, ValueError):
            continue
    return None


def _ensure_xsharp_lines(pred: dict) -> None:
    """Fill XSharp spread/total/score fields when the spread model did not run."""
    if pred.get('home_score') is not None:
        return
    pairs = (
        ('xgb_spread', 'naive_spread'),
        ('xgb_total', 'naive_total'),
        ('xgb_home_score', 'naive_home_score'),
        ('xgb_away_score', 'naive_away_score'),
    )
    for xk, nk in pairs:
        if pred.get(xk) is None and pred.get(nk) is not None:
            pred[xk] = pred[nk]
    if pred.get('xgb_home_score') is None and pred.get('xgb_spread') is not None and pred.get('xgb_total') is not None:
        h, a = _scores_from_spread_total(pred['xgb_spread'], pred['xgb_total'])
        if h is not None:
            pred['xgb_home_score'] = h
            pred['xgb_away_score'] = a


def _sync_pl_scores_from_line(pred: dict, spread, total) -> None:
    """Set our_spread, our_total, and projected scores from a line."""
    try:
        s, t = float(spread), float(total)
    except (TypeError, ValueError):
        return
    pred['our_spread'] = _round_to_half(s)
    pred['our_total'] = _round_to_half(t)
    h, a = _scores_from_spread_total(s, t)
    if h is not None:
        pred['our_home_pts'] = h
        pred['our_away_pts'] = a


def _break_tied_projection_scores(pred: dict, home_key: str, away_key: str, prob_keys=()) -> None:
    """Never show a tied projected score when spread or win prob implies a favorite."""
    h, a = pred.get(home_key), pred.get(away_key)
    if h is None or a is None:
        return
    try:
        hf, af = float(h), float(a)
    except (TypeError, ValueError):
        return
    if hf != af:
        return
    for sk in ('our_spread', 'xgb_spread', 'naive_spread', 'market_spread'):
        sv = pred.get(sk)
        if sv is None:
            continue
        try:
            s = float(sv)
        except (TypeError, ValueError):
            continue
        if abs(s) < 0.25:
            continue
        total = pred.get('our_total') or pred.get('xgb_total') or pred.get('naive_total') or (hf + af)
        nh, na = _scores_from_spread_total(s, total)
        if nh is not None and nh != na:
            pred[home_key] = nh
            pred[away_key] = na
            return
    prob = None
    for pk in prob_keys:
        pv = pred.get(pk)
        if pv is not None:
            try:
                prob = float(pv)
                break
            except (TypeError, ValueError):
                continue
    if prob is None:
        return
    if prob >= 50.5:
        pred[home_key] = hf + 1
        pred[away_key] = af - 1
    elif prob <= 49.5:
        pred[away_key] = af + 1
        pred[home_key] = hf - 1


def _align_pl_model_odds(pred: dict) -> None:
    """Keep PL Model spread, total, and projected score internally consistent."""
    spread = _best_pl_spread(pred)
    total = _best_pl_total(pred)
    if spread is not None and total is not None:
        _sync_pl_scores_from_line(pred, spread, total)
        return
    if pred.get('our_home_pts') is not None and pred.get('our_away_pts') is not None:
        try:
            h = float(pred['our_home_pts'])
            a = float(pred['our_away_pts'])
            if spread is None:
                spread = _round_to_half(h - a)
            if total is None:
                total = _round_to_half(h + a)
            if spread is not None and total is not None:
                _sync_pl_scores_from_line(pred, spread, total)
        except (TypeError, ValueError):
            pass


def _finalize_prediction_odds(pred: dict) -> None:
    """Single pass: backfill XSharp, align PL, mirror display keys, break score ties."""
    if pred.get('home_score') is not None:
        return
    _ensure_xsharp_lines(pred)
    if pred.get('xgb_spread') is not None and pred.get('xgb_total') is not None:
        xh, xa = _scores_from_spread_total(pred['xgb_spread'], pred['xgb_total'])
        if xh is not None:
            pred['xgb_home_score'] = xh
            pred['xgb_away_score'] = xa
    _break_tied_projection_scores(
        pred, 'xgb_home_score', 'xgb_away_score', ('xgb_prob', 'ensemble_prob'),
    )
    _align_pl_model_odds(pred)
    _break_tied_projection_scores(
        pred, 'our_home_pts', 'our_away_pts', ('ensemble_prob', 'xgb_prob'),
    )
    pred['xsharp_spread'] = pred.get('xgb_spread')
    pred['xsharp_total'] = pred.get('xgb_total')
    pred['xsharp_home_score'] = pred.get('xgb_home_score')
    pred['xsharp_away_score'] = pred.get('xgb_away_score')


def _set_card_edge_pct(pred: dict, sport: str = 'NBA') -> None:
    """Expose model-vs-book edge % on cards (MLB decision layer uses model_win_pct)."""
    if sport != 'MLB':
        pred['face_edge_pct'] = None
        return

    model_wp = _safe_float(pred.get('model_win_pct'))
    if model_wp is None:
        ens = _safe_float(pred.get('ensemble_prob'))
        if ens is not None:
            if ens <= 1.0:
                ens *= 100.0
            pw = pred.get('predicted_winner')
            ht = pred.get('home_team_id')
            home_picked = (pw == ht) if pw and ht else ens >= 50.0
            model_wp = ens if home_picked else (100.0 - ens)

    if model_wp is None:
        cached = _safe_float(pred.get('edge_pct'))
        pred['face_edge_pct'] = round(cached, 1) if cached is not None else None
        return

    home_ml = _safe_float(pred.get('book_home_moneyline'))
    away_ml = _safe_float(pred.get('book_away_moneyline'))
    if home_ml is None or away_ml is None:
        cached = _safe_float(pred.get('edge_pct'))
        pred['face_edge_pct'] = round(cached, 1) if cached is not None else None
        return

    pick_p = model_wp / 100.0
    pw = pred.get('predicted_winner')
    ht = pred.get('home_team_id')
    at = pred.get('away_team_id')
    if pw == ht:
        pick_ml, opp_ml = home_ml, away_ml
    elif pw == at:
        pick_ml, opp_ml = away_ml, home_ml
    else:
        ens = _safe_float(pred.get('ensemble_prob'))
        if ens is not None and ens <= 1.0:
            ens *= 100.0
        home_picked = (ens or 50.0) >= 50.0
        pick_ml = home_ml if home_picked else away_ml
        opp_ml = away_ml if home_picked else home_ml
    _, devig, _, _ = calculate_ev_devigged(pick_p, pick_ml, opp_ml)
    if devig is None:
        cached = _safe_float(pred.get('edge_pct'))
        pred['face_edge_pct'] = round(cached, 1) if cached is not None else None
        return

    edge = round((pick_p - devig) * 100.0, 1)
    pred['edge_pct'] = edge
    pred['face_edge_pct'] = edge
    pred['implied_win_pct'] = round(devig * 100.0, 1)


def _prepare_pred_card_face(pred: dict, sport: str = 'NBA') -> None:
    """Precompute card-face win % from the best model for this sport."""
    prob_key, label = BEST_MODEL_BY_SPORT.get(sport, ('ensemble_prob', 'Sharp Consensus'))
    draw_pct = _safe_float(pred.get('draw_prob'))
    home_win_pct = _safe_float(pred.get('home_win_prob'))
    away_win_pct = _safe_float(pred.get('away_win_prob'))
    if sport == 'SOCCER' and draw_pct is not None and home_win_pct is not None:
        home_prob = round(home_win_pct, 1)
        away_prob = round(away_win_pct if away_win_pct is not None else 100.0 - home_win_pct - draw_pct, 1)
        draw_prob = round(draw_pct, 1)
        pred['face_draw_prob'] = draw_prob
    else:
        draw_prob = None
        # Soccer XSharp face: poisson_reg (xgb_prob) first; fall back to PL ensemble
        # when xgb is missing but real ensemble data exists — never fake elo 50/50.
        if sport == 'SOCCER':
            home_prob = _safe_float(pred.get('xgb_prob'))
            if home_prob is None:
                home_prob = _safe_float(pred.get('ensemble_prob'))
        else:
            home_prob = _safe_float(pred.get(prob_key))
            if home_prob is None and prob_key != 'ensemble_prob':
                home_prob = _safe_float(pred.get('ensemble_prob'))
            if home_prob is None:
                home_prob = _safe_float(pred.get('elo_prob'))
        if home_prob is not None:
            if home_prob <= 1.0:
                home_prob *= 100.0
            home_prob = round(home_prob, 1)
            away_prob = round(100.0 - home_prob, 1)
        else:
            home_prob = away_prob = None
    pred['face_model_label'] = label
    pred['face_home_prob'] = home_prob
    pred['face_away_prob'] = away_prob
    if draw_prob is not None and home_prob is not None:
        outcomes = [
            ('home', home_prob, pred.get('home_team_id')),
            ('draw', draw_prob, 'Draw'),
            ('away', away_prob, pred.get('away_team_id')),
        ]
        _pick = max(outcomes, key=lambda x: x[1])
        pred['face_pick_team'] = _pick[2]
        pred['face_pick_confidence'] = _pick[1]
    elif home_prob is not None:
        if home_prob >= away_prob:
            pred['face_pick_team'] = pred.get('home_team_id')
            pred['face_pick_confidence'] = home_prob
        else:
            pred['face_pick_team'] = pred.get('away_team_id')
            pred['face_pick_confidence'] = away_prob
    else:
        pred['face_pick_team'] = pred.get('predicted_winner')
        _fp = _safe_float(pred.get('ensemble_prob'))
        if _fp is not None:
            if _fp <= 1.0:
                _fp *= 100.0
            pred['face_pick_confidence'] = round(_fp if _fp >= 50 else 100.0 - _fp, 1)
        else:
            pred['face_pick_confidence'] = None



def _prepare_pred_card_display(pred: dict, sport: str = 'NBA') -> None:
    """Precompute odds fields for the picks template (avoids fragile nested Jinja)."""
    if pred.get('home_score') is not None:
        return
    _raw_pl_sp = _best_pl_spread(pred)
    if _raw_pl_sp is None:
        _raw_pl_sp = _first_pred_float(
            pred, ('our_spread', 'market_spread', 'naive_spread'),
        )
        if _raw_pl_sp is not None:
            _raw_pl_sp = _round_to_half(_raw_pl_sp)
    pred['disp_pl_total'] = _best_pl_total(pred)
    if pred['disp_pl_total'] is None:
        pred['disp_pl_total'] = _first_pred_float(
            pred, ('our_total', 'naive_total', 'market_total', 'h2h_last10_total'),
        )
        if pred['disp_pl_total'] is not None:
            pred['disp_pl_total'] = _round_to_half(pred['disp_pl_total'])
    pred['disp_xs_spread'] = _first_pred_float(
        pred, ('xsharp_spread', 'xgb_spread', 'naive_spread', 'market_spread'),
    )
    if pred['disp_xs_spread'] is not None:
        pred['disp_xs_spread'] = _round_to_half(pred['disp_xs_spread'])
    pred['disp_xs_total'] = _first_pred_float(
        pred, ('xsharp_total', 'xgb_total', 'naive_total', 'market_total'),
    )
    if pred['disp_xs_total'] is not None:
        pred['disp_xs_total'] = _round_to_half(pred['disp_xs_total'])
    pred['disp_xs_away'] = _first_pred_float(
        pred, ('xsharp_away_score', 'xgb_away_score', 'naive_away_score'),
    )
    pred['disp_xs_home'] = _first_pred_float(
        pred, ('xsharp_home_score', 'xgb_home_score', 'naive_home_score'),
    )
    # NBA-specific: consensus total, pace, variance/confidence tiers
    _ct = pred.get('consensus_total')
    pred['disp_consensus_total'] = _round_to_half(float(_ct)) if _ct is not None else None
    pred['disp_pl_pace'] = pred.get('our_pace')
    pred['disp_variance_tier'] = pred.get('pl_variance_tier')
    pred['disp_confidence_tier'] = pred.get('pl_confidence_tier')
    # PL Model moneyline: derive from PL spread when efficiency projection is available.
    # This ensures the PL column is internally consistent (pick always matches spread).
    _conf = pred.get('pl_confidence_tier') or pred.get('confidence_tier')
    _regression = {'High': 0.0, 'Med': 0.15, 'Low': 0.35}.get(_conf, 0.0)
    _pl_sp = _safe_float(_raw_pl_sp)
    _our_method = pred.get('our_method')
    # MLB: never rebuild disp_ml_prob / predicted_winner from efficiency spread —
    # that caused projected Nationals 6–Phillies 4.5 while Consensus picked Phillies.
    if (
        sport != 'MLB'
        and _pl_sp is not None
        and abs(_pl_sp) >= 1.0
        and _our_method in ('efficiency', 'team-avg-fallback')
    ):
        import math as _mt
        _sigma_pl = 12.0  # NBA spread distribution std dev
        _pl_home_prob = 50.0 + 50.0 * _mt.erf(_pl_sp / (_sigma_pl * _mt.sqrt(2)))
        if _regression > 0.0:
            _pl_home_prob -= _regression * (_pl_home_prob - 50.0)
        pred['disp_ml_prob'] = round(_pl_home_prob, 1)
        # Ensure predicted_winner matches PL spread direction
        if _pl_sp > 0:
            pred['predicted_winner'] = pred.get('home_team_id')
        else:
            pred['predicted_winner'] = pred.get('away_team_id')
    else:
        # Fallback: confidence-penalized ensemble_prob
        _raw_ens = _safe_float(pred.get('ensemble_prob'))
        if _raw_ens is not None:
            if _regression > 0.0:
                pred['disp_ml_prob'] = round(_raw_ens - _regression * (_raw_ens - 50.0), 1)
            else:
                pred['disp_ml_prob'] = _raw_ens
        else:
            pred['disp_ml_prob'] = None

    _ensure_book_moneylines(pred)
    _set_card_book_lines(pred)
    _set_card_pl_spread(pred, sport=sport)
    _sync_pick_winner_to_pl_spread(pred, sport=sport)
    if sport == 'MLB':
        _set_mlb_spread_pick_label(pred)
    _set_card_game_time(pred)
    _set_card_pl_moneylines(pred)
    _set_card_projected_scores(pred)
    _set_card_edge_pct(pred, sport=sport)
    _prepare_pred_card_face(pred, sport=sport)


def _sync_pick_winner_to_pl_spread(pred: dict, sport: str = 'NBA') -> None:
    """Align predicted_winner with PL spread after disp sign normalization."""
    if pred.get('home_score') is not None:
        return
    # MLB: moneyline (ensemble) is source of truth. disp_pl_spread is flipped to
    # match ML in _set_card_pl_spread — do not override the pick from raw our_spread.
    if sport == 'MLB':
        ens = _safe_float(pred.get('ensemble_prob')) or _safe_float(pred.get('ens_prob'))
        if ens is not None:
            ens_pct = _normalize_home_win_prob_pct(ens)
            if ens_pct is not None:
                pred['predicted_winner'] = (
                    pred.get('home_team_id') if ens_pct >= 50.0 else pred.get('away_team_id')
                )
        return
    _min_spread = {'NHL': 0.3, 'WNBA': 1.0}.get(sport, 3.0)
    sp = _safe_float(pred.get('disp_pl_spread'))
    if sp is None:
        sp = _safe_float(pred.get('our_spread'))
    if sp is None or abs(sp) < _min_spread:
        return
    pred['predicted_winner'] = (
        pred.get('home_team_id') if sp > 0 else pred.get('away_team_id')
    )


def _enforce_pick_spread_consistency(pred: dict, sport: str = 'NBA') -> None:
    """Ensure the moneyline pick direction matches the spread.

    An 8-point spread implies ~75% win probability for the favored team.
    If our_spread says 'NYK -8' but ensemble_prob says 'CLE wins', that is
    an internal contradiction — correct ensemble_prob to match the spread.
    Also corrects xgb_prob to match xgb_spread in the XSharp column.
    """
    import math as _mc
    if pred.get('home_score') is not None:
        return

    # Sport-specific σ for the spread normal distribution.
    _sigma = {'NBA': 12.0, 'NCAAB': 10.0, 'NFL': 14.0, 'NCAAF': 16.0,
              'NHL': 1.2, 'MLB': 1.5, 'WNBA': 11.0}.get(sport, 12.0)
    _min_spread = {'NHL': 0.3, 'MLB': 0.5}.get(sport, 3.0)  # threshold per sport

    def _spread_to_pct(sp):
        """Home win probability (0-100) implied by a spread."""
        return 50.0 + 50.0 * _mc.erf(float(sp) / (_sigma * _mc.sqrt(2)))

    # ── PL Model: our_spread → ensemble_prob ──────────────────────────────
    our_sp = _safe_float(pred.get('our_spread'))
    ens = _safe_float(pred.get('ensemble_prob'))
    if our_sp is not None and abs(our_sp) >= _min_spread and ens is not None:
        implied = _spread_to_pct(our_sp)
        if pred.get('is_v2'):
            # V2 game: trust the calibrated ensemble model, not the efficiency spread.
            # Only align predicted_winner with what the ensemble already says.
            if ens >= 50.0:
                pred['predicted_winner'] = pred.get('home_team_id')
            else:
                pred['predicted_winner'] = pred.get('away_team_id')
        else:
            if our_sp > 0 and ens < 50.0:
                # Spread says home wins; pick incorrectly says away — override.
                pred['ensemble_prob'] = round(implied, 1)
                pred['predicted_winner'] = pred.get('home_team_id')
            elif our_sp < 0 and ens >= 50.0:
                # Spread says away wins; pick incorrectly says home — override.
                pred['ensemble_prob'] = round(implied, 1)
                pred['predicted_winner'] = pred.get('away_team_id')

    # ── XSharp: xgb_spread → xgb_prob ────────────────────────────────────
    xgb_sp = _safe_float(pred.get('xgb_spread'))
    xgb_p = _safe_float(pred.get('xgb_prob'))
    if xgb_sp is not None and abs(xgb_sp) >= _min_spread and xgb_p is not None:
        implied_xs = _spread_to_pct(xgb_sp)
        if xgb_sp > 0 and xgb_p < 50.0:
            pred['xgb_prob'] = round(implied_xs, 1)
        elif xgb_sp < 0 and xgb_p >= 50.0:
            pred['xgb_prob'] = round(implied_xs, 1)


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
            if len(_MLB_BULLPEN_FATIGUE_CACHE) > 500:
                for _k in list(_MLB_BULLPEN_FATIGUE_CACHE)[:200]:
                    _MLB_BULLPEN_FATIGUE_CACHE.pop(_k, None)
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
        if len(_MLB_BULLPEN_FATIGUE_CACHE) > 500:
            for _k in list(_MLB_BULLPEN_FATIGUE_CACHE)[:200]:
                _MLB_BULLPEN_FATIGUE_CACHE.pop(_k, None)
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


def _park_weather_total_adjustment(sport, home_team, game_date=None, game_id=None, away_team=None):
    """Return a park/weather adjustment for the total projection."""
    if sport == 'MLB':
        try:
            from mlb_context import weather_park_total_adjustment
            return weather_park_total_adjustment(
                home_team, away_team=away_team, game_date=game_date, game_id=game_id,
            ).total_adj
        except Exception:
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
                    try:
                        g['our_spread'] = _round_to_half(float(proj['avg_home']) - float(proj['avg_away']))
                    except (TypeError, ValueError):
                        pass
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


def _model_probs_from_row_and_v2(
    sport,
    home_team,
    away_team,
    game_row,
    game_date,
    *,
    skip_v2=False,
    v2_budget_ok=True,
):
    """Load Grinder2/Takedown/Edge/XSharp/Consensus probs (0–1) from DB row + optional v2."""
    glicko2_prob = _to_float_safe(_row_field(game_row, 'glicko_home_prob'))
    trueskill_prob = _to_float_safe(_row_field(game_row, 'trueskill_home_prob'))
    elo_prob = _to_float_safe(_row_field(game_row, 'elo_home_prob'))
    xgb_prob = _to_float_safe(_row_field(game_row, 'xgboost_home_prob'))
    ens_prob = _to_float_safe(_row_field(game_row, 'meta_home_prob'))
    if ens_prob is None:
        ens_prob = _to_float_safe(_row_field(game_row, 'win_probability'))
    if glicko2_prob is None:
        glicko2_prob = _to_float_safe(_row_field(game_row, 'catboost_home_prob'))
    if trueskill_prob is None:
        trueskill_prob = _to_float_safe(_row_field(game_row, 'logistic_home_prob'))
    if elo_prob is None:
        elo_prob = _to_float_safe(_row_field(game_row, 'catboost_home_prob'))

    _snapshot_build = _os.environ.get('PL_SNAPSHOT_BUILD') == '1'
    need_v2 = any(
        p is None for p in (glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob)
    )
    if skip_v2 and sport == 'NHL':
        need_v2 = False
    if sport != 'SOCCER' and need_v2 and v2_budget_ok:
        run_v2 = _snapshot_build or sport == 'NHL'
        if not run_v2 and game_date:
            _gd = parse_date(game_date)
            if _gd:
                run_v2 = (datetime.now() - _gd).days <= 21
        if run_v2:
            v2 = get_v2_prediction(sport, home_team, away_team, game_date)
            if v2:
                if glicko2_prob is None:
                    glicko2_prob = v2.get('glicko2_prob')
                if trueskill_prob is None:
                    trueskill_prob = v2.get('trueskill_prob')
                if xgb_prob is None:
                    xgb_prob = v2.get('xgboost_prob')
                if ens_prob is None:
                    ens_prob = v2.get('home_prob')
                if elo_prob is None:
                    elo_prob = v2.get('catboost_prob') or v2.get('home_prob')

    if elo_prob is None:
        elo_prob = _to_float_safe(_row_field(game_row, 'catboost_home_prob'))
    if elo_prob is None and ens_prob is not None:
        elo_prob = ens_prob
    if sport == 'NHL' and ens_prob is None:
        ens_prob = _compute_ensemble_prob(
            glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=None,
        )
    elif ens_prob is None and any(
        p is not None for p in (glicko2_prob, trueskill_prob, xgb_prob, elo_prob)
    ):
        ens_prob = _compute_ensemble_prob(
            glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=None,
        )
    return glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob


_FROZEN_V2_RESULTS_GRADING_CACHE: dict = {}
_NBA_FROZEN_V2_RESULTS_CACHE = _FROZEN_V2_RESULTS_GRADING_CACHE


def _model_probs_for_grading(sport, game_row, home_team, away_team, game_date_key):
    """DB-first moneyline probs for results grading; frozen v2 fills historical gaps."""
    glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = _model_probs_from_row_and_v2(
        sport,
        home_team,
        away_team,
        game_row,
        game_date_key,
        skip_v2=True,
    )
    stored_ens = _to_float_safe(_row_field(game_row, 'win_probability'))
    had_stored_ens = stored_ens is not None
    if stored_ens is not None:
        ens_prob = stored_ens
    elif ens_prob is None:
        meta_ens = _to_float_safe(_row_field(game_row, 'meta_home_prob'))
        if meta_ens is not None:
            ens_prob = meta_ens
            had_stored_ens = True

    need_frozen = any(
        p is None for p in (glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob)
    )
    if need_frozen:
        cache_key = f'{sport}|{home_team}|{away_team}|{game_date_key}'
        if cache_key not in _FROZEN_V2_RESULTS_GRADING_CACHE:
            _FROZEN_V2_RESULTS_GRADING_CACHE[cache_key] = _frozen_get_v2_prediction(
                sport, home_team, away_team, game_date_key,
            )
        v2 = _FROZEN_V2_RESULTS_GRADING_CACHE[cache_key]
        if v2:
            if glicko2_prob is None:
                glicko2_prob = _to_float_safe(v2.get('glicko2_prob'))
            if trueskill_prob is None:
                trueskill_prob = _to_float_safe(v2.get('trueskill_prob'))
            if xgb_prob is None:
                xgb_prob = _to_float_safe(v2.get('xgboost_prob'))
            if elo_prob is None:
                elo_prob = _to_float_safe(v2.get('home_prob'))

    if sport == 'WNBA' and _os.environ.get('PL_SNAPSHOT_BUILD') == '1':
        if glicko2_prob is None and elo_prob is not None:
            glicko2_prob = elo_prob
        if trueskill_prob is None and xgb_prob is not None:
            trueskill_prob = xgb_prob
        elif trueskill_prob is None and elo_prob is not None:
            trueskill_prob = elo_prob

    if not had_stored_ens and any(
        p is not None for p in (glicko2_prob, trueskill_prob, xgb_prob, elo_prob)
    ):
        ens_prob = _compute_ensemble_prob(
            glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=None,
        )
    if ens_prob is None and need_frozen:
        cache_key = f'{sport}|{home_team}|{away_team}|{game_date_key}'
        v2 = _FROZEN_V2_RESULTS_GRADING_CACHE.get(cache_key)
        if v2:
            ens_prob = _to_float_safe(v2.get('home_prob'))

    return glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob


def _banner_daily_results_for_range(sport, start_dt, end_dt, *, playoffs=False, skip_v2=None):
    if sport == 'NFL':
        weekly_results = calculate_nfl_weekly_performance()
        if weekly_results:
            daily = _daily_results_from_weekly(weekly_results)
            if daily and _daily_results_game_count(daily):
                return daily
    if sport == 'NBA':
        weekly_results = calculate_nba_weekly_performance()
        if weekly_results:
            daily = _daily_results_from_weekly(weekly_results)
            if daily and _daily_results_game_count(daily):
                return daily

    start_sql = start_dt.strftime('%Y-%m-%d') if start_dt else None
    end_sql = end_dt.strftime('%Y-%m-%d') if end_dt else None

    try:
        conn = get_db_connection()
        prob_sql = _predictions_prob_select_sql(conn)
        if start_sql and end_sql:
            rows = conn.execute(f'''
                SELECT g.*,
                       {prob_sql}
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ?
                  AND g.home_score IS NOT NULL
                  AND g.away_score IS NOT NULL
                  AND date(g.game_date) >= ?
                  AND date(g.game_date) <= ?
                ORDER BY g.game_date DESC
            ''', (sport, sport, start_sql, end_sql)).fetchall()
        else:
            rows = conn.execute(f'''
                SELECT g.*,
                       {prob_sql}
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ?
                  AND g.home_score IS NOT NULL
                  AND g.away_score IS NOT NULL
                ORDER BY g.game_date DESC
                LIMIT 5000
            ''', (sport, sport)).fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"_banner_daily_results_for_range failed for {sport}: {e}")
        return None

    if sport == 'NHL':
        rows = _dedupe_nhl_game_rows(rows, apply_season_cap=not playoffs)
    elif start_sql and end_sql:
        rows = [
            r for r in rows
            if _date_in_range(_normalize_game_date_key(r['game_date']), start_dt, end_dt)
        ]
    rows = _sort_game_rows_by_date_desc(rows)
    if sport != 'NHL' and not _os.environ.get('PL_SNAPSHOT_BUILD') == '1':
        rows = rows[:800]

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
        game_date = _normalize_game_date_key(game['game_date'])
        league_name = game.get('league') if isinstance(game, dict) else game['league']
        if sport == 'SOCCER':
            league_name = _canonical_soccer_league_name(league_name) or league_name
            if not league_name or league_name not in SOCCER_LEAGUE_ORDER:
                continue

        glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = _model_probs_for_grading(
            sport, game, home_team, away_team, game_date,
        )

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
            'elo_prob':         round(elo_prob  * 100, 1) if elo_prob is not None else None,
            'xgb_prob':         round(xgb_prob  * 100, 1) if xgb_prob is not None else None,
            'ens_prob':         round(ens_prob  * 100, 1) if ens_prob is not None else None,
        }
        _apply_soccer_ml_grading(
            game_info,
            draw_dec=None,
            glicko2_prob=glicko2_prob,
            trueskill_prob=trueskill_prob,
            elo_prob=elo_prob,
            xgb_prob=xgb_prob,
            ens_prob=ens_prob,
            home_won=home_won,
            is_draw=is_draw,
        )
        daily_results[game_info['date']]['games'].append(game_info)
    return daily_results


def _upsert_betting_line(
    conn,
    sport,
    game_id,
    game_date,
    home_team,
    away_team,
    spread,
    total,
    source=None,
    home_moneyline=None,
    away_moneyline=None,
):
    try:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info('betting_lines')").fetchall()]
    except Exception:
        cols = []
    has_extra = any(c in cols for c in ['sport', 'game_date', 'home_team', 'away_team'])
    has_ml = 'home_moneyline' in cols and 'away_moneyline' in cols
    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.cursor()
    try:
        if has_extra:
            existing = cur.execute(
                "SELECT id, spread, total, home_moneyline, away_moneyline FROM betting_lines "
                "WHERE sport=? AND game_id=? ORDER BY fetched_at DESC LIMIT 1",
                (sport, game_id)
            ).fetchone()
            if existing:
                if has_ml:
                    cur.execute(
                        "UPDATE betting_lines SET spread=COALESCE(?, spread), total=COALESCE(?, total), "
                        "home_moneyline=COALESCE(?, home_moneyline), away_moneyline=COALESCE(?, away_moneyline), "
                        "source=COALESCE(?, source), fetched_at=? WHERE id=?",
                        (
                            spread, total, home_moneyline, away_moneyline,
                            source or 'live', now_ts, existing['id'],
                        ),
                    )
                else:
                    cur.execute(
                        "UPDATE betting_lines SET spread=COALESCE(?, spread), total=COALESCE(?, total), fetched_at=? WHERE id=?",
                        (spread, total, now_ts, existing['id']),
                    )
            else:
                if has_ml:
                    cur.execute(
                        "INSERT INTO betting_lines "
                        "(sport, game_id, game_date, home_team, away_team, spread, total, "
                        "home_moneyline, away_moneyline, source, fetched_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            sport, game_id, game_date, home_team, away_team,
                            spread, total, home_moneyline, away_moneyline,
                            source or 'live', now_ts,
                        ),
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
    if not predictions:
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
                has_ml_cols = 'home_moneyline' in cols and 'away_moneyline' in cols
                if has_extra:
                    existing = cur.execute(
                        "SELECT spread, total, home_moneyline, away_moneyline FROM betting_lines "
                        "WHERE sport=? AND game_id=? ORDER BY fetched_at DESC LIMIT 1",
                        (sport, game_id)
                    ).fetchone()
                else:
                    existing = cur.execute(
                        "SELECT spread, total FROM betting_lines WHERE game_id=? LIMIT 1",
                        (game_id,)
                    ).fetchone()
                # Skip only when we already have spread/total AND moneylines — if
                # moneylines are NULL we still need to fetch so we can fill them in.
                if existing and (existing['spread'] is not None or existing['total'] is not None):
                    if not has_ml_cols:
                        continue
                    if existing['home_moneyline'] is not None and existing['away_moneyline'] is not None:
                        continue
            except Exception:
                pass
            line = _fetch_live_market_line(
                sport,
                game_id,
                pred.get('game_date'),
                pred.get('home_team_id'),
                pred.get('away_team_id'),
                league_name=pred.get('league') or pred.get('league_name'),
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
                    line.get('source'),
                    home_moneyline=line.get('home_moneyline'),
                    away_moneyline=line.get('away_moneyline'),
                )
        conn.commit()
        conn.close()
    except Exception as _e:
        logger.debug(f"[{sport}] cache market lines failed: {_e}")


def _attach_market_lines_to_predictions(sport, predictions):
    """Read market spread/total from betting_lines DB and attach to pred dicts."""
    if not predictions:
        return
    game_ids = [p.get('game_id') for p in predictions if p.get('game_id') and p.get('home_score') is None]
    if not game_ids:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cols = [r['name'] for r in cur.execute("PRAGMA table_info('betting_lines')").fetchall()]
        has_sport_col = 'sport' in cols
        placeholders = ','.join('?' * len(game_ids))
        if has_sport_col:
            rows = cur.execute(
                f"""SELECT game_id, spread, total, home_moneyline, away_moneyline, source
                    FROM betting_lines WHERE sport=? AND game_id IN ({placeholders})
                    ORDER BY fetched_at DESC""",
                [sport] + game_ids
            ).fetchall()
        else:
            rows = cur.execute(
                f"SELECT game_id, spread, total FROM betting_lines WHERE game_id IN ({placeholders})",
                game_ids
            ).fetchall()
        conn.close()
        line_map = {}
        for row in rows:
            gid = row['game_id']
            if gid not in line_map:
                line_map[gid] = dict(row)
        for pred in predictions:
            gid = pred.get('game_id')
            if gid and gid in line_map:
                lm = line_map[gid]
                if pred.get('market_spread') is None and lm.get('spread') is not None:
                    pred['market_spread'] = lm['spread']
                if pred.get('market_total') is None and lm.get('total') is not None:
                    pred['market_total'] = lm['total']
                _apply_db_book_row_to_pred(pred, lm)
    except Exception as _e:
        logger.debug(f"[{sport}] attach market lines failed: {_e}")


def _cache_market_lines_for_results(sport, daily_results, limit=20):
    if not daily_results:
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
                    has_ml_cols = 'home_moneyline' in cols and 'away_moneyline' in cols
                    if has_extra:
                        existing = cur.execute(
                            "SELECT spread, total, home_moneyline, away_moneyline FROM betting_lines "
                            "WHERE sport=? AND game_id=? ORDER BY fetched_at DESC LIMIT 1",
                            (sport, gid)
                        ).fetchone()
                    else:
                        existing = cur.execute(
                            "SELECT spread, total FROM betting_lines WHERE game_id=? LIMIT 1",
                            (gid,)
                        ).fetchone()
                    if existing and (existing['spread'] is not None or existing['total'] is not None):
                        if not has_ml_cols:
                            continue
                        if existing['home_moneyline'] is not None and existing['away_moneyline'] is not None:
                            continue
                except Exception:
                    pass
                try:
                    gd_dt = parse_date(gd)
                    if gd_dt and abs((datetime.now() - gd_dt).days) > 7:
                        continue
                except Exception:
                    pass
                line = _fetch_live_market_line(
                    sport,
                    gid,
                    gd,
                    g.get('home'),
                    g.get('away'),
                    league_name=g.get('league') or g.get('league_name'),
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
                        line.get('source'),
                        home_moneyline=line.get('home_moneyline'),
                        away_moneyline=line.get('away_moneyline'),
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
    away_team: str = None,
    league_name: str = None,
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
        if not soccer_league_slugs:
            try:
                from pl_book_odds_api import _soccer_slug_from_league_name
                _mapped = _soccer_slug_from_league_name(league_name)
            except Exception:
                _mapped = None
            if _mapped:
                soccer_league_slugs.append(_mapped)
        if not soccer_league_slugs:
            soccer_league_slugs = [
                'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1',
                'uefa.champions', 'uefa.europa', 'uefa.europa.conf',
                'usa.1', 'mex.1', 'ned.1', 'por.1', 'afc.champions',
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

                def _to_int_ml(v):
                    try:
                        return int(round(float(v))) if v is not None else None
                    except (TypeError, ValueError):
                        return None

                home_ml = _to_int_ml((chosen.get('homeTeamOdds') or {}).get('moneyLine'))
                away_ml = _to_int_ml((chosen.get('awayTeamOdds') or {}).get('moneyLine'))
                if spread_val is not None and (home_ml is None or away_ml is None):
                    try:
                        from pl_book_odds_api import _ml_from_spread_fallback
                        h_fb, a_fb = _ml_from_spread_fallback(spread_val)
                        home_ml = home_ml if home_ml is not None else h_fb
                        away_ml = away_ml if away_ml is not None else a_fb
                    except Exception:
                        pass

                return {
                    'spread': spread_val,
                    'total': total_val,
                    'home_moneyline': home_ml,
                    'away_moneyline': away_ml,
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
_SITE_DOMAIN = f'https://{_CANONICAL_HOST}'


def _resolve_legacy_seo_target(path):
    """Map legacy paths to final SEO path (+query if needed). None if already final.

    Defined early so host redirects can collapse www/http + old paths into one 301.
    Looks up SPORT_SEO_SLUGS at call time (populated below).
    """
    path = (path or '/').rstrip('/') or '/'
    m = re.match(r'^/sport/SOCCER/predictions/([^/]+)$', path, re.I)
    if m:
        return f'/soccer-picks?league={m.group(1)}'
    m = re.match(r'^/sport/SOCCER/results/([^/]+)$', path, re.I)
    if m:
        return f'/soccer-results?league={m.group(1)}'
    m = re.match(r'^/sport/([^/]+)/(predictions|results)$', path, re.I)
    if m:
        sport = m.group(1).upper()
        kind = m.group(2).lower()
        slug_map = SPORT_SEO_SLUGS if kind == 'predictions' else _SPORT_RESULTS_SLUGS
        slug = slug_map.get(sport)
        if slug:
            return f'/{slug}'
    m = re.match(r'^/sport/([^/]+)$', path, re.I)
    if m:
        slug = SPORT_SEO_SLUGS.get(m.group(1).upper())
        if slug:
            return f'/{slug}'
    if path.endswith('-predictions'):
        return path[: -len('-predictions')] + '-picks'
    if path.endswith('-prediction'):
        return path[: -len('-prediction')] + '-picks'
    return None


@app.before_request
def enforce_canonical_domain():
    """Redirect underdogs.bet / www / http variants to canonical https://predictionlab.io.

    When rewriting host/scheme, also collapse legacy /sport/*/predictions paths into
    the final SEO URL so crawlers get a single 301 (not www→apex then path→slug).
    """
    host = (request.host or '').split(':')[0].lower()
    if not host or host in {'localhost', '127.0.0.1'} or host.endswith('.local'):
        return None
    if not (host.endswith('underdogs.bet') or host.endswith('predictionlab.io')):
        return None
    target_host = _CANONICAL_HOST
    is_https = request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower() == 'https'
    needs_redirect = (host != target_host) or (not is_https)
    legacy = _resolve_legacy_seo_target(request.path)
    if not needs_redirect:
        if request.path == '/' and request.args.get('q'):
            return redirect(f"https://{target_host}/", code=301)
        return None
    if legacy:
        full_path = legacy
    else:
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
    try:
        _is_premium = is_premium_user()
    except Exception:
        _is_premium = False
    return {
        'stripe_donation_url': STRIPE_DONATION_URL,
        'contact_email': CONTACT_EMAIL,
        'social_links': SOCIAL_LINKS,
        'soccer_enabled': SOCCER_ENABLED,
        'ga_tracking_id': GA_TRACKING_ID,
        'sport_seo_slug': SPORT_SEO_SLUGS.get(_sport, ''),
        'sport_results_slug': _SPORT_RESULTS_SLUGS.get(_sport, ''),
        'is_logged_in': _logged_in,
        'is_premium': _is_premium,
        'wnba_enabled': _wnba_live,
        'team_logo_url': team_logo_url,
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
    """Track site visits for analytics (non-blocking).

    The DB insert runs in a background thread so the analytics write never adds
    latency to the response and never competes for SQLite's write lock on the
    request path (a common cause of site-wide slowness).
    """
    try:
        visit_date = _traffic_now().strftime('%Y-%m-%d')
        ip_address = request.remote_addr if request else None
        user_agent = request.headers.get('User-Agent') if request else None
    except Exception:
        return

    def _write():
        try:
            conn = get_db_connection()
            conn.execute('''
                INSERT INTO site_visits (visit_date, ip_address, user_agent, endpoint)
                VALUES (?, ?, ?, ?)
            ''', (visit_date, ip_address, user_agent, endpoint))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error logging site visit: {e}")

    try:
        import threading as _thr
        _thr.Thread(target=_write, daemon=True, name='site-visit').start()
    except Exception:
        _write()

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
    'TENNIS': {'name': 'Tennis', 'icon': '🎾', 'color': '#16a34a'},
    'UFC':    {'name': 'UFC / MMA', 'icon': '🥊', 'color': '#b91c1c'},
    'GOLF':   {'name': 'Golf', 'icon': '⛳', 'color': '#0369a1'},
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
    'TENNIS': 'tennis-picks',
    'UFC':    'ufc-picks',
    'GOLF':   'golf-picks',
}
_SEO_SLUG_TO_SPORT = {v: k for k, v in SPORT_SEO_SLUGS.items()}
_SPORT_RESULTS_SLUGS = {k: v.replace('-picks', '-results') for k, v in SPORT_SEO_SLUGS.items()}
_RESULTS_SLUG_TO_SPORT = {v: k for k, v in _SPORT_RESULTS_SLUGS.items()}
_INDIVIDUAL_SPORT_LOADERS = {}  # populated after sport modules import (new sports)

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

# Distinct games.league values per curated league (filled from DB on first soccer results load).
_SOCCER_LEAGUE_DB_VARIANTS = None

SOCCER_RESULTS_GAMES_PER_LEAGUE = 250


def _ensure_soccer_league_db_variants(conn):
    """Map curated league name → set of raw DB `games.league` strings."""
    global _SOCCER_LEAGUE_DB_VARIANTS
    if _SOCCER_LEAGUE_DB_VARIANTS is not None:
        return _SOCCER_LEAGUE_DB_VARIANTS
    variants = {lg: {lg} for lg in SOCCER_LEAGUE_ORDER}
    try:
        rows = conn.execute(
            "SELECT DISTINCT league FROM games WHERE sport = 'SOCCER' AND league IS NOT NULL"
        ).fetchall()
        for row in rows:
            raw = row['league'] if hasattr(row, 'keys') else row[0]
            if not raw:
                continue
            canon = _canonical_soccer_league_name(raw) or raw
            if canon in variants:
                variants[canon].add(raw)
    except Exception as exc:
        logger.debug(f"[soccer] league variant scan failed: {exc}")
    _SOCCER_LEAGUE_DB_VARIANTS = variants
    return variants


def _soccer_curated_league_game_counts(conn):
    """Completed-game counts per curated league (full DB, not the global LIMIT slice)."""
    counts = {lg: 0 for lg in SOCCER_LEAGUE_ORDER}
    try:
        rows = conn.execute('''
            SELECT league, COUNT(*) AS n
            FROM games
            WHERE sport = 'SOCCER' AND home_score IS NOT NULL
            GROUP BY league
        ''').fetchall()
        for row in rows:
            raw = row['league']
            n = row['n']
            canon = _canonical_soccer_league_name(raw) or raw
            if canon in counts:
                counts[canon] += int(n)
    except Exception as exc:
        logger.debug(f"[soccer] league counts failed: {exc}")
    return counts


def _fetch_soccer_completed_games(conn, selected_league=None, limit=None):
    """Load completed soccer games for one curated league (or all if league is None)."""
    limit = limit or SOCCER_RESULTS_GAMES_PER_LEAGUE
    base_sql = '''
        SELECT g.*,
               p.elo_home_prob,
               p.xgboost_home_prob,
               p.logistic_home_prob,
               p.win_probability,
               p.catboost_home_prob,
               p.meta_home_prob,
               p.glicko_home_prob,
               p.trueskill_home_prob
        FROM games g
        LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'SOCCER'
        WHERE g.sport = 'SOCCER' AND g.home_score IS NOT NULL
    '''
    if not selected_league:
        return conn.execute(
            base_sql + ' ORDER BY g.game_date DESC LIMIT ?',
            (limit,),
        ).fetchall()
    variants = _ensure_soccer_league_db_variants(conn)
    names = sorted(variants.get(selected_league) or {selected_league})
    if not names:
        names = [selected_league]
    placeholders = ','.join('?' * len(names))
    return conn.execute(
        base_sql + f' AND g.league IN ({placeholders}) ORDER BY g.game_date DESC LIMIT ?',
        (*names, limit),
    ).fetchall()


def _invalidate_soccer_league_db_variants():
    global _SOCCER_LEAGUE_DB_VARIANTS
    _SOCCER_LEAGUE_DB_VARIANTS = None


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
    'AFC Champions League Elite': 'afc.champions',
    'AFC Champions League Two': 'afc.cup',
    'USL Championship': None,
}

SOCCER_PICKS_DAYS_BACK = 1
SOCCER_PICKS_DAYS_FORWARD = 13
_SOCCER_ESPN_LEAGUE_ID_CACHE: dict = {}
_SOCCER_ESPN_LEAGUE_ID_TTL = 86400
_SOCCER_ALL_SCOREBOARD_URL = (
    'https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard'
)


def _espn_soccer_league_id_map():
    """ESPN numeric league id -> curated league name (cached ~24h)."""
    now_ts = _time.time()
    cached = _SOCCER_ESPN_LEAGUE_ID_CACHE.get('bundle')
    if cached and (now_ts - cached.get('ts', 0)) < _SOCCER_ESPN_LEAGUE_ID_TTL:
        return cached.get('data') or {}
    id_map = {}
    for label, code in SOCCER_LEAGUE_ENDPOINTS.items():
        if not code:
            continue
        try:
            data = _cached_get(
                f'https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard',
                timeout=8,
            )
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        league_info = (data.get('leagues', [{}])[0] or {})
        league_id = league_info.get('id')
        if league_id is None:
            continue
        canonical = _canonical_soccer_league_name(league_info.get('name')) or label
        id_map[str(league_id)] = canonical
    _SOCCER_ESPN_LEAGUE_ID_CACHE['bundle'] = {'ts': now_ts, 'data': id_map}
    return id_map


def _soccer_league_from_espn_uid(uid: str, id_map: dict):
    if not uid or not id_map:
        return None
    match = re.search(r'~l:(\d+)~', uid)
    if not match:
        return None
    return id_map.get(match.group(1))


def _parse_espn_line_number(raw):
    """Parse ESPN line strings like '-0.5', '+1.5', 'o2.5' into float."""
    if raw is None:
        return None
    try:
        s = str(raw).strip().replace('½', '.5').replace(' ', '')
        s = s.replace('o', '').replace('u', '').replace('O', '').replace('U', '')
        if not s or s in {'-', '+', 'PK', 'pk', 'EVEN', 'even'}:
            return 0.0 if s.upper() in {'PK', 'EVEN'} else None
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_espn_american_odds(raw):
    """Parse American odds from int/float/string ('-160', '+380')."""
    if raw is None:
        return None
    try:
        return int(round(float(str(raw).strip().replace(',', ''))))
    except (TypeError, ValueError):
        return None


def _parse_espn_embedded_odds_item(item):
    """Parse one ESPN odds object (Core flat fields or scoreboard nested widgets).

    Returns dict with home_ml/away_ml/spread/total/provider, or None if unusable/live.
    Book spread convention: negative = home favored (same as ESPN Core `spread`).
    """
    if not isinstance(item, dict):
        return None
    prov = ((item.get('provider') or {}).get('name') or '')
    if 'live' in prov.lower():
        return None

    home_ml = _parse_espn_american_odds(
        (item.get('homeTeamOdds') or {}).get('moneyLine')
    )
    away_ml = _parse_espn_american_odds(
        (item.get('awayTeamOdds') or {}).get('moneyLine')
    )
    spread = _parse_espn_line_number(item.get('spread'))
    total = _parse_espn_line_number(item.get('overUnder'))

    # Current site.api scoreboard DraftKings widget: nested pointSpread / moneyline.
    if spread is None:
        ps_home = ((item.get('pointSpread') or {}).get('home') or {})
        ps_close = ps_home.get('close') or ps_home.get('open') or {}
        spread = _parse_espn_line_number(ps_close.get('line'))
    if home_ml is None or away_ml is None:
        ml = item.get('moneyline') or {}
        if home_ml is None:
            h_block = (ml.get('home') or {})
            h_close = h_block.get('close') or h_block.get('open') or {}
            home_ml = _parse_espn_american_odds(h_close.get('odds'))
        if away_ml is None:
            a_block = (ml.get('away') or {})
            a_close = a_block.get('close') or a_block.get('open') or {}
            away_ml = _parse_espn_american_odds(a_close.get('odds'))
    if total is None:
        tot = item.get('total') or {}
        over = (tot.get('over') or {})
        o_close = over.get('close') or over.get('open') or {}
        total = _parse_espn_line_number(o_close.get('line'))

    if home_ml is None and away_ml is None and spread is None and total is None:
        return None
    return {
        'home_ml': home_ml,
        'away_ml': away_ml,
        'spread': spread,
        'total': total,
        'provider': prov or 'ESPN',
    }


def _fetch_soccer_scoreboard_api_games(days_back=None, days_forward=None):
    """Curated soccer games from ESPN soccer/all (one request per day, all leagues)."""
    days_back = SOCCER_PICKS_DAYS_BACK if days_back is None else days_back
    days_forward = SOCCER_PICKS_DAYS_FORWARD if days_forward is None else days_forward
    id_map = _espn_soccer_league_id_map()
    api_games = []
    for days_offset in range(-days_back, days_forward + 1):
        check_date = datetime.now() + timedelta(days=days_offset)
        date_str = check_date.strftime('%Y%m%d')
        try:
            data = _cached_get(f'{_SOCCER_ALL_SCOREBOARD_URL}?dates={date_str}', timeout=15)
        except Exception as e:
            logger.debug(f'Error fetching SOCCER all scoreboard for {date_str}: {e}')
            continue
        if not isinstance(data, dict):
            continue
        for event in data.get('events', []) or []:
            status_name = (event.get('status') or {}).get('type', {}).get('name', '')
            is_final = status_name.startswith('STATUS_FINAL')
            competition = (event.get('competitions', [{}])[0] or {})
            competitors = competition.get('competitors', []) or []
            if len(competitors) != 2:
                continue
            league_name = _soccer_league_from_espn_uid(competition.get('uid', ''), id_map)
            if not league_name:
                league_name = _canonical_soccer_league_from_event(event, competition)
            if not league_name or league_name not in SOCCER_LEAGUE_ORDER:
                continue
            home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
            away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
            if not home or not away:
                continue
            _register_soccer_from_competitor(home)
            _register_soccer_from_competitor(away)
            home_team = (home.get('team') or {}).get('displayName', '')
            away_team = (away.get('team') or {}).get('displayName', '')
            event_id = event.get('id', '')
            league_code = SOCCER_LEAGUE_ENDPOINTS.get(league_name) or 'all'
            event_dt = event.get('date', '') or competition.get('date', '')
            game_date = _espn_event_date_to_local(event_dt) or check_date.strftime('%Y-%m-%d')
            home_score = away_score = None
            if is_final:
                try:
                    home_score = int(home.get('score', 0))
                    away_score = int(away.get('score', 0))
                except Exception:
                    continue
            # Extract book odds directly from the scoreboard response.
            # Supports legacy flat fields (spread/homeTeamOdds) and current nested
            # DraftKings widgets (pointSpread/moneyline). Core API still used later
            # when scoreboard odds are null (common for some cups).
            _sb_home_ml = _sb_away_ml = _sb_spread = _sb_total = None
            _sb_source = None
            for _odds_item in (competition.get('odds') or []):
                parsed = _parse_espn_embedded_odds_item(_odds_item)
                if not parsed:
                    continue
                _sb_home_ml = parsed.get('home_ml')
                _sb_away_ml = parsed.get('away_ml')
                _sb_spread = parsed.get('spread')
                _sb_total = parsed.get('total')
                _sb_source = parsed.get('provider') or 'ESPN'
                if _sb_home_ml is not None or _sb_spread is not None or _sb_total is not None:
                    break  # use first valid provider
            _game_entry = {
                'game_id': f'SOCCER_{league_code}_{event_id}',
                'home_team_id': home_team,
                'away_team_id': away_team,
                'game_date': game_date,
                'event_date': event_dt or None,
                'home_score': home_score,
                'away_score': away_score,
                'league': league_name,
            }
            if _sb_home_ml is not None:
                _game_entry['book_home_moneyline'] = _sb_home_ml
                _game_entry['book_away_moneyline'] = _sb_away_ml
                _game_entry['book_odds_source'] = _sb_source or 'ESPN Scoreboard'
            if _sb_spread is not None:
                _game_entry['book_spread'] = _sb_spread
            if _sb_total is not None:
                _game_entry['book_total'] = _sb_total
            api_games.append(_game_entry)
    return api_games


def _fetch_soccer_upcoming_api_games(days_back=None, days_forward=None):
    return _fetch_soccer_scoreboard_api_games(days_back=days_back, days_forward=days_forward)


def _hydrate_soccer_team_logos(team_names, league_code=None):
    """Warm ESPN team ID cache for names missing from cache (lightweight scoreboard scan)."""
    if not team_names:
        return
    missing = {
        n for n in team_names
        if n and _normalize_soccer_team_name(n) not in _SOCCER_TEAM_ESPN_ID
    }
    if not missing:
        return
    endpoints = [league_code] if league_code else [c for c in SOCCER_LEAGUE_ENDPOINTS.values() if c]
    if not endpoints:
        return
    req_budget = min(12, max(4, len(endpoints) * 2))
    requests_made = 0
    today = datetime.now()
    for days_offset in range(0, 14):
        if not missing or requests_made >= req_budget:
            break
        date_str = (today - timedelta(days=days_offset)).strftime('%Y%m%d')
        for code in endpoints:
            if not missing or requests_made >= req_budget:
                break
            url = (
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                f"{code}/scoreboard?dates={date_str}"
            )
            try:
                data = _cached_get(url)
                requests_made += 1
            except Exception:
                continue
            for event in data.get('events', []) if isinstance(data, dict) else []:
                comp = (event.get('competitions') or [{}])[0]
                for competitor in comp.get('competitors', []) or []:
                    _register_soccer_from_competitor(competitor, persist=False)
            _save_soccer_team_espn_ids()
            missing = {
                n for n in missing
                if _normalize_soccer_team_name(n) not in _SOCCER_TEAM_ESPN_ID
            }


def _canonical_soccer_league_name(league_name: str):
    if not league_name:
        return None
    stripped = league_name.strip()
    if stripped in SOCCER_LEAGUE_ORDER:
        return stripped
    key = stripped.lower()
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
_SOCCER_ENDPOINT_SLUGS = {
    (code or '').strip().lower(): name
    for name, code in SOCCER_LEAGUE_ENDPOINTS.items()
    if code
}

def _soccer_league_from_slug(slug: str):
    if not slug:
        return None
    key = slug.strip().lower()
    league = SOCCER_LEAGUE_SLUGS.get(key)
    if league:
        return league
    return _SOCCER_ENDPOINT_SLUGS.get(key)


def _resolve_soccer_league_slug(raw_slug):
    """Normalize ?league= values (hyphen slugs or ESPN codes) to the site slug."""
    league = _soccer_league_from_slug(raw_slug or '')
    if not league:
        return None
    return _soccer_league_slug(league)


def _seo_canonical_url(path=None):
    """Build canonical URL; soccer league filters keep ?league= so each league page is indexable."""
    path = path or getattr(request, 'path', '/') or '/'
    base = f"https://predictionlab.io{path}"
    if path not in ('/soccer-picks', '/soccer-results'):
        return base
    league_slug = _resolve_soccer_league_slug(request.args.get('league'))
    if league_slug:
        return f"{base}?league={league_slug}"
    return base


def _filter_soccer_picks(predictions, selected_slug=None):
    """Curate soccer picks and league picker; filter only when ?league= is set."""
    filtered = []
    leagues = []
    leagues_with_upcoming = set()  # leagues that have at least one upcoming game
    try:
        _today = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        _today = datetime.now().strftime('%Y-%m-%d')

    for pred in predictions:
        league_raw = pred.get('league')
        league_name = _canonical_soccer_league_name(league_raw) or league_raw
        if not league_name or league_name not in SOCCER_LEAGUE_ORDER:
            continue
        pred['league'] = league_name
        leagues.append(league_name)
        filtered.append(pred)
        # Mark leagues that have games today or in the future
        gd = pred.get('game_date') or ''
        if gd >= _today and pred.get('home_score') is None:
            leagues_with_upcoming.add(league_name)

    # Always show full curated league slider (ESPN may only have games in 1–2 comps today).
    soccer_league_list = list(SOCCER_LEAGUE_ORDER)
    selected_league = _soccer_league_from_slug(selected_slug) if selected_slug else None
    if selected_league:
        filtered = [p for p in filtered if p.get('league') == selected_league]

    # leagues_with_any: leagues that have any predictions at all (upcoming or recent)
    leagues_with_any = set(leagues)
    soccer_leagues = [
        {
            'name': 'All Leagues',
            'slug': '',
            'active': selected_league is None,
            'live': bool(leagues_with_upcoming),
            'has_games': bool(leagues_with_any),
            'url': '/soccer-picks',
        }
    ] + [
        {
            'name': lg,
            'slug': _soccer_league_slug(lg),
            'active': lg == selected_league,
            'live': lg in leagues_with_upcoming,
            'has_games': lg in leagues_with_any,
            'url': f"/soccer-picks?league={_soccer_league_slug(lg)}",
        }
        for lg in soccer_league_list
    ]
    return filtered, soccer_leagues, selected_league


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

    # Supplement from DB using ESPN league-name variants (e.g. "Spanish LALIGA 2"
    # for curated "Spanish Segunda División") — LIKE on canonical name misses rows.
    _min_games = 10 if league_name else 12
    if len(filtered) < _min_games:
        try:
            conn = get_db_connection()
            if league_name:
                variants = _ensure_soccer_league_db_variants(conn)
                league_names = sorted(variants.get(league_name) or {league_name})
            else:
                league_names = None
            if league_names:
                placeholders = ','.join('?' * len(league_names))
                db_games = conn.execute(
                    f'''
                    SELECT game_id, game_date, home_team_id, away_team_id,
                           home_score, away_score, league
                    FROM games
                    WHERE sport = 'SOCCER'
                      AND home_score IS NOT NULL
                      AND league IN ({placeholders})
                    ORDER BY game_date DESC
                    LIMIT 200
                    ''',
                    league_names,
                ).fetchall()
            else:
                db_games = conn.execute('''
                    SELECT game_id, game_date, home_team_id, away_team_id,
                           home_score, away_score, league
                    FROM games
                    WHERE sport = 'SOCCER'
                      AND home_score IS NOT NULL
                    ORDER BY game_date DESC
                    LIMIT 200
                ''').fetchall()
            conn.close()
            for row in db_games:
                key = (row['game_id'], row['home_team_id'], row['away_team_id'])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                filtered.append(dict(row))
        except Exception as _e:
            logger.debug(f"[soccer] DB supplement failed: {_e}")

    bundle = build_soccer_model_bundle(filtered, league_name=league_name, min_games=_min_games)
    _trim_cache(_SOCCER_MODEL_CACHE, _SOCCER_MODEL_TTL, max_entries=50)
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
        default_lookback_days = 120

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

            today = datetime.now()

            # Detect gap: find most recent stored soccer result date
            try:
                _last_date_row = conn.execute(
                    "SELECT MAX(game_date) FROM games WHERE sport=? AND home_score IS NOT NULL",
                    (sport,)
                ).fetchone()
                _last_date_str = _last_date_row[0] if _last_date_row else None
                if _last_date_str:
                    _last_dt = datetime.strptime(_last_date_str[:10], '%Y-%m-%d')
                    _gap_days = max(0, (today - _last_dt).days - 1)
                else:
                    _gap_days = 30
            except Exception:
                _gap_days = 14

            # Use date-range API calls (one per league) when gap > 7 days,
            # so we backfill months of missing data without hundreds of requests.
            # ESPN supports dates=YYYYMMDD-YYYYMMDD for a range window.
            _use_range = _gap_days > 7
            if _use_range:
                # Chunk into 30-day windows to stay within ESPN's response limits
                _backfill_days = min(_gap_days + 2, 180)
                _chunk_size = 30
                _date_ranges = []
                _ptr = 0
                while _ptr < _backfill_days:
                    _end_offset = _ptr
                    _start_offset = min(_ptr + _chunk_size - 1, _backfill_days - 1)
                    _start_str = (today - timedelta(days=_start_offset)).strftime('%Y%m%d')
                    _end_str   = (today - timedelta(days=_end_offset)).strftime('%Y%m%d')
                    _date_ranges.append(f"{_start_str}-{_end_str}")
                    _ptr += _chunk_size
                logger.info(f"[SOCCER] gap={_gap_days}d — backfilling {len(_date_ranges)} range(s) × {len(SOCCER_LEAGUE_ORDER)} leagues")
            else:
                _days_recent = max(7, _gap_days + 2)
                _date_ranges = [
                    (today - timedelta(days=d)).strftime('%Y%m%d')
                    for d in range(_days_recent)
                ]

            max_requests = 300
            for _dr in _date_ranges:
                for league_label in SOCCER_LEAGUE_ORDER:
                    if request_count >= max_requests:
                        break
                    league_code = SOCCER_LEAGUE_ENDPOINTS.get(league_label)
                    if not league_code:
                        continue
                    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard?dates={_dr}&limit=200"
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
                        _register_soccer_from_competitor(home)
                        _register_soccer_from_competitor(away)

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
                                    (sport, league_name or sport, game_id, today.year, game_date, home_team, away_team, home_score, away_score)
                                )
                                logger.info(f"Inserted new {sport} game: {away_team} @ {home_team} ({game_date})")
                            except Exception as insert_error:
                                logger.error(f"Error inserting {sport} game {game_id}: {insert_error}")

                        if cursor.rowcount > 0:
                            updates_count += 1

            conn.commit()
            _invalidate_soccer_league_db_variants()
            conn.close()
            if updates_count > 0:
                logger.info(f"Successfully updated {updates_count} {sport} game scores.")
                # Clear results page HTML cache so next load shows fresh data
                stale = [k for k in _SPORT_RESULTS_CACHE if k.startswith(f'{sport}_daily_results_html')]
                for _sk in stale:
                    _SPORT_RESULTS_CACHE.pop(_sk, None)
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
        CREATE TABLE IF NOT EXISTS player_prop_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league TEXT NOT NULL,
            pick_date TEXT NOT NULL,
            player_name TEXT NOT NULL,
            team TEXT,
            prop_type TEXT NOT NULL,
            pick TEXT NOT NULL,
            line REAL NOT NULL,
            projection REAL,
            odds REAL,
            line_source TEXT,
            created_at TEXT,
            UNIQUE(league, pick_date, player_name, prop_type)
        );
        CREATE INDEX IF NOT EXISTS idx_ppp_league_date ON player_prop_picks(league, pick_date);
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


_PREDICTIONS_PROB_SELECT_CACHE = None


def _row_field(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _predictions_prob_select_sql(conn=None):
    """Build predictions JOIN columns that exist in this DB (avoids missing-column SQL failures)."""
    global _PREDICTIONS_PROB_SELECT_CACHE
    if _PREDICTIONS_PROB_SELECT_CACHE is not None:
        return _PREDICTIONS_PROB_SELECT_CACHE
    base = (
        'p.elo_home_prob',
        'p.xgboost_home_prob',
        'p.logistic_home_prob',
        'p.win_probability',
    )
    optional = (
        'catboost_home_prob',
        'meta_home_prob',
        'glicko_home_prob',
        'trueskill_home_prob',
    )
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    try:
        table_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
        }
    except Exception:
        table_cols = set(optional)
    finally:
        if close_conn:
            conn.close()
    parts = list(base) + [f'p.{col}' for col in optional if col in table_cols]
    _PREDICTIONS_PROB_SELECT_CACHE = ',\n                       '.join(parts)
    return _PREDICTIONS_PROB_SELECT_CACHE


def _ensure_predictions_prob_columns():
    """Add optional Grinder2/Takedown columns when missing (older production DBs)."""
    global _PREDICTIONS_PROB_SELECT_CACHE
    try:
        conn = sqlite3.connect(DATABASE)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        for col in ('catboost_home_prob', 'glicko_home_prob', 'trueskill_home_prob'):
            if col not in cols:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL")
        conn.commit()
        conn.close()
        _PREDICTIONS_PROB_SELECT_CACHE = None
    except Exception as _e:
        logger.debug(f"[predictions] column ensure failed: {_e}")


# Run on every startup — creates tables if missing, no-op if they exist
try:
    init_db()
    _ensure_engine_odds_columns()
    _ensure_predictions_prob_columns()
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
            # Defer past Render boot/health window — workers=1; sync HTTP+DB
            # here wedges /healthz and the homepage on every redeploy.
            import time as _t
            _t.sleep(120)
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
            # Defer past boot — loading the props engine (~30s) on workers=1
            # right after import wedges every request including /healthz.
            import time as _t
            _t.sleep(120)
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


# ── ESPN odds-engine cache pre-warmer ────────────────────────────────────────
# odds_engine_espn has a 15-minute in-memory cache.  On Render, workers restart
# frequently which always starts the cache cold — meaning the first page load
# after restart triggers ~31 synchronous ESPN HTTP requests and times out.
# This background thread warms the cache for every active sport immediately at
# startup, and refreshes it every 12 minutes so it never expires mid-session.
_PREWARM_SPORTS = ['NBA', 'NHL', 'MLB', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'SOCCER']

def _prewarm_espn_odds_cache():
    import time as _time
    try:
        from odds_engine_espn import get_all_team_stats as _warm
    except Exception:
        return
    # Defer past Render's post-deploy health window. workers=1: a burst of ESPN
    # HTTP on boot wedges /healthz + homepage. Yield between sports for the same.
    _time.sleep(60)
    while True:
        for _sport in _PREWARM_SPORTS:
            try:
                _warm(_sport)
                logger.debug(f"[odds-prewarm] {_sport} warmed")
            except Exception as _we:
                logger.debug(f"[odds-prewarm] {_sport} failed: {_we}")
            _time.sleep(3)   # keep the worker responsive between sports
        _time.sleep(720)   # re-warm every 12 min — before the 15-min TTL expires

try:
    threading.Thread(target=_prewarm_espn_odds_cache, daemon=True, name='odds-prewarm').start()
except Exception as _owe:
    logger.debug(f"[odds-prewarm] failed to start: {_owe}")


# ── Predictions cache disk persistence ─────────────────────────────────
# _PREDICTIONS_CACHE is in-memory, so every (frequent, on Render) worker restart
# starts cold and the first request per sport pays the full synchronous build
# (soccer ≈ 10s: ESPN slate + live odds HTTP + model compute) — bad for SEO /
# uptime tests. We mirror each built slate to disk and seed the in-memory cache
# from it at startup, so the first post-restart request serves a warm slate
# instantly and stale-while-revalidate refreshes it in the background.
# Stored on the persistent disk (_DATA_DIR is '/data' on Render, '.' locally) so
# cached slates survive not just worker restarts but full REDEPLOYS — the app
# directory is wiped on every deploy, which would otherwise leave the first
# post-deploy request with no warm slate to fall back on if the live build fails.
_PREDICTIONS_DISK_CACHE_DIR = _os_v2.path.join(_DATA_DIR, '.cache', 'predictions')
# Don't seed slates older than this on boot (dates would be too stale to show
# even for the brief window before the background refresh completes).
_PREDICTIONS_DISK_MAX_AGE = 2 * 24 * 3600  # 2 days


def _persist_predictions_to_disk(cache_key, entry):
    """Atomically mirror one cached prediction slate to disk (best-effort)."""
    try:
        import pickle
        _os_v2.makedirs(_PREDICTIONS_DISK_CACHE_DIR, exist_ok=True)
        path = _os_v2.path.join(_PREDICTIONS_DISK_CACHE_DIR, f'{cache_key}.pkl')
        tmp = f'{path}.{_os_v2.getpid()}.{threading.get_ident()}.tmp'
        with open(tmp, 'wb') as _f:
            pickle.dump(
                {'key': cache_key, 'ts': entry.get('ts'), 'data': entry.get('data')},
                _f, protocol=pickle.HIGHEST_PROTOCOL,
            )
        _os_v2.replace(tmp, path)  # atomic on POSIX
    except Exception as _pe:
        logger.debug(f"[preds-disk] persist failed for {cache_key}: {_pe}")


def _predictions_cache_key_aliases(sport):
    """Current + prior slate keys so a version bump does not cold-start the site."""
    return (
        f"{sport}_upcoming_predictions_v8",
        f"{sport}_upcoming_predictions_v7",
        f"{sport}_upcoming_predictions_v6",
    )


def _promote_predictions_cache_aliases():
    """Copy older versioned slate keys onto the current v8 key when v8 is missing."""
    try:
        sports = set()
        for key in list(_PREDICTIONS_CACHE.keys()):
            if not isinstance(key, str) or '_upcoming_predictions_v' not in key:
                continue
            sports.add(key.split('_upcoming_predictions_v', 1)[0])
        for sport in sports:
            keys = _predictions_cache_key_aliases(sport)
            current = keys[0]
            if _PREDICTIONS_CACHE.get(current, {}).get('data'):
                continue
            for alias in keys[1:]:
                entry = _PREDICTIONS_CACHE.get(alias)
                if isinstance(entry, dict) and entry.get('data'):
                    _PREDICTIONS_CACHE[current] = entry
                    break
    except Exception:
        pass


def _load_predictions_disk_cache():
    """Seed _PREDICTIONS_CACHE from disk at startup so cold starts serve warm."""
    try:
        if not _os_v2.path.isdir(_PREDICTIONS_DISK_CACHE_DIR):
            return
        import pickle
        now_ts = _time.time()
        loaded = 0
        for _fn in _os_v2.listdir(_PREDICTIONS_DISK_CACHE_DIR):
            if not _fn.endswith('.pkl'):
                continue
            path = _os_v2.path.join(_PREDICTIONS_DISK_CACHE_DIR, _fn)
            try:
                with open(path, 'rb') as _f:
                    d = pickle.load(_f)
            except Exception:
                continue
            key = d.get('key') or _fn[:-4]
            ts = d.get('ts') or 0
            data = d.get('data')
            if not data or (now_ts - ts) > _PREDICTIONS_DISK_MAX_AGE:
                continue
            existing = _PREDICTIONS_CACHE.get(key)
            if existing and existing.get('ts', 0) >= ts:
                continue  # keep the fresher in-memory entry
            _PREDICTIONS_CACHE[key] = {'ts': ts, 'data': data}
            loaded += 1
        _promote_predictions_cache_aliases()
        if loaded:
            logger.info(f"[preds-disk] seeded {loaded} prediction slate(s) from disk")
    except Exception as _le:
        logger.debug(f"[preds-disk] load failed: {_le}")


def _recover_cached_predictions(sport):
    """Best-effort fallback slate when a live rebuild raises.

    Returns a deep copy of the most recent cached prediction slate for `sport`
    (in-memory first, then disk-seeded), ignoring the normal TTL — a stale slate
    is far better than the 'could not be loaded' error banner when an upstream
    data/model dependency hiccups. Returns None when nothing is cached at all.
    """
    data = None
    for cache_key in _predictions_cache_key_aliases(sport):
        entry = _PREDICTIONS_CACHE.get(cache_key)
        data = entry.get('data') if isinstance(entry, dict) else None
        if data:
            break
    if not data:
        # Nothing in memory (e.g. right after a restart/redeploy) — try the disk mirror.
        try:
            _load_predictions_disk_cache()
        except Exception:
            pass
        for cache_key in _predictions_cache_key_aliases(sport):
            entry = _PREDICTIONS_CACHE.get(cache_key)
            data = entry.get('data') if isinstance(entry, dict) else None
            if data:
                break
    if not data:
        return None
    try:
        return _copy.deepcopy(data)
    except Exception:
        return data


# ── Predictions cache pre-warmer ─────────────────────────────────────────────
# get_upcoming_predictions serves stale-while-revalidate: after the first build
# per sport, requests are instant and rebuilds happen off the request path. This
# one-shot warmer builds every sport's slate once at startup so the FIRST
# request after a (frequent, on Render) worker restart is also instant instead
# of paying the multi-second cold model build synchronously.
# SOCCER is warmed FIRST: it is the slowest cold build (multi-league ESPN slate
# + live odds) and the most SEO-sensitive, so it should be ready before the
# lighter sports.
_PREDICTIONS_PREWARM_SPORTS = ['MLB', 'SOCCER', 'NBA', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA']

def _prewarm_predictions_cache():
    import time as _t
    # get_upcoming_predictions is defined further down this module; wait for it.
    _fn = None
    for _ in range(120):
        _fn = globals().get('get_upcoming_predictions')
        if _fn is not None:
            break
        _t.sleep(1)
    if _fn is None:
        return
    # Long defer so /healthz + homepage win the single Render worker after every
    # redeploy. Force-rebuilding all sports ~5s after boot (old behavior) held
    # the GIL / SQLite write lock and made even /healthz hang with 0 bytes.
    _t.sleep(60)
    for _sport in _PREDICTIONS_PREWARM_SPORTS:
        # Disk seed already put a usable slate in memory — do NOT force-rebuild
        # on the boot path. Stale-while-revalidate refreshes on the first sport
        # page hit. Only cold-build sports that have nothing cached.
        _ck = f"{_sport}_upcoming_predictions_v8"
        _entry = _PREDICTIONS_CACHE.get(_ck)
        if isinstance(_entry, dict) and _entry.get('data'):
            logger.info(f"[preds-prewarm] {_sport} already seeded — skip force rebuild")
            continue
        # Respect the same single-flight guard so an on-demand background refresh
        # and this warmer never rebuild the same sport at the same time.
        with _PREDICTIONS_REFRESH_LOCK:
            if _sport in _PREDICTIONS_REFRESH_INFLIGHT:
                continue
            _PREDICTIONS_REFRESH_INFLIGHT.add(_sport)
        try:
            _fn(_sport, _force_rebuild=True)
            logger.info(f"[preds-prewarm] {_sport} warmed")
        except Exception as _we:
            logger.debug(f"[preds-prewarm] {_sport} failed: {_we}")
        finally:
            with _PREDICTIONS_REFRESH_LOCK:
                _PREDICTIONS_REFRESH_INFLIGHT.discard(_sport)
        # Give the worker room to serve requests between heavy builds.
        _t.sleep(15)

# Seed the in-memory cache from disk BEFORE the prewarmer runs so the very first
# request after a restart serves a warm (stale) slate instantly instead of
# blocking on the cold build while the prewarmer is still working through its
# list. Stale-while-revalidate refreshes each slate in the background.
try:
    _load_predictions_disk_cache()
except Exception as _lde:
    logger.debug(f"[preds-disk] startup seed failed: {_lde}")

try:
    threading.Thread(target=_prewarm_predictions_cache, daemon=True, name='preds-prewarm').start()
except Exception as _ppe:
    logger.debug(f"[preds-prewarm] failed to start: {_ppe}")


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


def _normalize_game_date_key(val):
    """Canonical YYYY-MM-DD bucket for daily_results keys (handles mixed DB formats)."""
    raw = _to_date_str(val)
    if not raw:
        return None
    dt = parse_date(raw)
    return dt.strftime('%Y-%m-%d') if dt else None


def _sort_game_rows_by_date_desc(rows):
    """Sort sqlite game rows by parsed game_date descending."""
    return sorted(
        rows,
        key=lambda r: parse_date(_normalize_game_date_key(r['game_date'])) or datetime.min,
        reverse=True,
    )


def _compute_results_tally_bundle(
    daily_results,
    yesterday_dt,
    *,
    season_start_dt=None,
    sport=None,
    league_scoped=False,
):
    """Daily + weekly tallies; when the calendar week is empty, use the latest window with games."""
    # Safety net: exhibition sides (WNBA All-Star TEAM COOP/SPOON, etc.) never count.
    _mark_exhibition_skip_grading(daily_results)
    yesterday = yesterday_dt.strftime('%Y-%m-%d')
    weekly_start_dt = yesterday_dt - timedelta(days=6)
    weekly_end_dt = yesterday_dt
    weekly_tally_date_range = f"{weekly_start_dt.strftime('%Y-%m-%d')} to {yesterday}"
    results_stale_notice = False

    daily_tally_date = yesterday
    daily_tally = compute_daily_model_tally(daily_results, daily_tally_date)
    weekly_tally = compute_model_tally_for_range(daily_results, weekly_start_dt, weekly_end_dt)
    weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0

    if not daily_tally and daily_results:
        dated = _dated_games_in_daily_results(daily_results, season_start_dt=season_start_dt)
        if dated:
            daily_tally_date = dated[0][1]
            daily_tally = compute_daily_model_tally(daily_results, daily_tally_date)
            if daily_tally_date != yesterday:
                results_stale_notice = True

    use_soccer_matchday_window = (
        sport == 'SOCCER'
        and daily_results
        and not league_scoped
    )
    if use_soccer_matchday_window:
        win = _soccer_weekly_tally_window(
            daily_results, season_start_dt=season_start_dt, n_matchdays=7,
        )
        if win[0] is not None:
            weekly_start_dt, weekly_end_dt, weekly_tally_date_range = win
            weekly_tally = compute_model_tally_for_range(
                daily_results, weekly_start_dt, weekly_end_dt,
            )
            weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
    elif weekly_tally_games == 0 and daily_results and not league_scoped:
        dated = _dated_games_in_daily_results(
            daily_results, season_start_dt=season_start_dt, before_dt=yesterday_dt,
        )
        if dated:
            latest_dt, _ = dated[0]
            fallback_start = (
                max(latest_dt - timedelta(days=6), season_start_dt)
                if season_start_dt else latest_dt - timedelta(days=6)
            )
            weekly_start_dt = fallback_start
            weekly_end_dt = latest_dt
            weekly_tally = compute_model_tally_for_range(
                daily_results, weekly_start_dt, weekly_end_dt,
            )
            weekly_tally_games = weekly_tally.get('games', 0) if weekly_tally else 0
            weekly_tally_date_range = (
                f"{fallback_start.strftime('%Y-%m-%d')} to {latest_dt.strftime('%Y-%m-%d')}"
            )
    daily_tally_games = daily_tally.get('games', 0) if daily_tally else 0
    return {
        'daily_tally': daily_tally,
        'daily_tally_date': daily_tally_date,
        'daily_tally_games': daily_tally_games,
        'weekly_tally': weekly_tally,
        'weekly_tally_date_range': weekly_tally_date_range,
        'weekly_tally_games': weekly_tally_games,
        'results_stale_notice': results_stale_notice,
        'weekly_start_dt': weekly_start_dt,
        'weekly_end_dt': weekly_end_dt,
    }

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


def _format_card_game_time(pred: dict, tz_name: str = 'America/New_York') -> str | None:
    """Format ESPN/ISO start time for pick card header (e.g. '7:10 PM ET')."""
    raw = pred.get('game_time') or pred.get('event_date') or pred.get('commence_time')
    if not raw:
        return None
    try:
        if isinstance(raw, datetime):
            dt = raw
        else:
            s = str(raw).strip()
            if not s:
                return None
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo('UTC'))
        local = dt.astimezone(ZoneInfo(tz_name))
        label = local.strftime('%I:%M %p').lstrip('0')
        return f"{label} ET" if tz_name == 'America/New_York' else label
    except Exception:
        return None


def _set_card_game_time(card: dict) -> None:
    """Populate card.game_time for pick templates when a start timestamp exists."""
    if card.get('home_score') is not None:
        return
    formatted = _format_card_game_time(card)
    if formatted:
        card['game_time'] = formatted


def _set_mlb_spread_pick_label(card: dict) -> None:
    """Run-line pick label from faded model spread (same sign as disp_pl_spread)."""
    sp = _safe_float(card.get('disp_pl_spread'))
    if sp is None:
        sp = _safe_float(_best_pl_spread(card))
    if sp is None:
        sp = _first_pred_float(card, ('our_spread', 'xgb_spread'))
    if sp is None:
        return
    h = card.get('home_team_id') or card.get('home')
    a = card.get('away_team_id') or card.get('away')
    if not (h and a):
        return
    run_line = 1.5
    pick_team = h if sp > 0 else a
    card['spread_pick_label'] = f"{pick_team} {-run_line:+.1f}"

# ============================================================================
# V2 PREDICTION SYSTEM HELPER
# ============================================================================

def _v2_model_sport(sport):
    """Map display sport to trained v2 model key (NCAAW shares NCAAB weights)."""
    return 'NCAAB' if sport == 'NCAAW' else sport


def get_v2_prediction(sport, home_team, away_team, game_date=None):
    """
    Get predictions from the v2 system (Glicko-2 + Stacked Ensemble + Calibration)
    
    Returns dict with probabilities or None if v2 not available for this sport
    """
    model_sport = _v2_model_sport(sport)
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
            'catboost_prob': row.get('catboost_prob'),
            
            # Ratings
            'home_glicko2': row.get('home_glicko2'),
            'away_glicko2': row.get('away_glicko2'),
            'home_trueskill_mu': row.get('home_trueskill_mu'),
            'away_trueskill_mu': row.get('away_trueskill_mu'),
            
            'is_v2': True,
        }
        _trim_cache(_V2_PREDICTION_CACHE, _V2_PREDICTION_TTL_SECONDS, max_entries=1000)
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

    Team-based sports only. Individual sports (Golf/Tennis/UFC)
    use their own athlete matchup pipelines.
    """

    # Individual sports do not use team stats
    if sport.upper() in {'GOLF', 'TENNIS', 'UFC'}:
        return None

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
    if len(_sp_instances) >= 6:
        oldest = min(_sp_instances, key=lambda k: _sp_instances[k][1])
        del _sp_instances[oldest]
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
    model_sport = _v2_model_sport(sport)
    # Need completed games from DB and team stats
    try:
        team_stats = _build_team_stats_from_db(sport) or {}
        if not team_stats and model_sport != sport:
            team_stats = _build_team_stats_from_db(model_sport) or {}
        if not team_stats:
            sp = _score_predictor_instance(sport)
            if sp:
                team_stats = sp.team_stats_cache.get(
                    f"{sport}_{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}", {}
                ) or {}
        conn = get_db_connection()
        games_sport = model_sport if sport == 'NCAAW' else sport
        rows = conn.execute(
            'SELECT home_team_id, away_team_id, home_score, away_score, game_date '
            'FROM games WHERE sport=? AND home_score IS NOT NULL ORDER BY game_date',
            (games_sport,)
        ).fetchall()
        conn.close()
        games = [dict(r) for r in rows]
        if not team_stats or not games:
            return None
        return get_or_train_model(model_sport, games, team_stats)
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


# Picks page display window (per sport). The picks page only renders the
# near-term slate; the results page serves full history. Sports mapped here
# build prediction cards only within [today - past, today + future] days, which
# avoids running model inference over the whole season on every rebuild — e.g.
# without this, NBA/NFL/etc. rebuild a card for every game since season start
# (a full season can be ~1,300 games) on each stale-while-revalidate refresh.
# 'past' matches the picks route's recent-finals cutoff; 'future' is sized per
# cadence: daily sports a few days, weekly sports (NFL/NCAAF) enough to reach
# next week's slate. Sports NOT listed keep the legacy season-start..+30d
# behavior — NHL and SOCCER are intentionally omitted because their loaders are
# already API-bounded to a small recent+upcoming window, and TENNIS/UFC/GOLF
# render through separate individual-sport loaders, not this build path.
_PICKS_DISPLAY_WINDOW = {
    # Daily-cadence sports: recent finals + a few days of upcoming games.
    'MLB':   {'past': 3, 'future': 3},
    'NBA':   {'past': 3, 'future': 5},
    # future=7 so real games still show during All-Star week gaps
    'WNBA':  {'past': 3, 'future': 7},
    'NCAAB': {'past': 3, 'future': 5},
    'NCAAW': {'past': 3, 'future': 5},
    # Weekly-cadence sports: widen 'future' so next week's slate still shows.
    'NFL':   {'past': 3, 'future': 10},
    'NCAAF': {'past': 3, 'future': 10},
}


def get_upcoming_predictions(sport, days=365, _force_rebuild=False):
    """Get a sport's near-term prediction slate (recent finals + upcoming games).
    
    Loads games from database for all sports including NHL
    
    Only games inside the per-sport picks display window are built into cards
    (Elo still trains on all completed games); full history is on the results page.
    
    Serving is stale-while-revalidate: any cached slate is returned immediately
    (with book odds re-hydrated from the DB), and when it is past its TTL the
    heavy rebuild is kicked onto a daemon thread instead of blocking the request.
    Background rebuilds call this with _force_rebuild=True to bypass the cache.
    """
    
    # Only off-request-path builds (prewarm / background refresh, invoked with
    # _force_rebuild=True) may perform slow synchronous live-odds HTTP. A
    # request-path COLD miss (_force_rebuild=False, empty cache) stays fast: it
    # hydrates odds from the DB only and defers the live fetch to a background
    # rebuild so no user/crawler request blocks ~10s on synchronous odds HTTP.
    _live_odds_ok = bool(_force_rebuild)

    # Fast in-process cache to avoid repeated heavy prediction recomputation.
    # v7: drop All-Star / placeholder matchups (WNBA COOP/SPOON, MLB ASG, etc.).
    cache_key = f"{sport}_upcoming_predictions_v8"
    now_ts = _time.time()
    cache_ttl = _PREDICTIONS_TTL_BY_SPORT.get(sport, 180)
    cached = _PREDICTIONS_CACHE.get(cache_key)
    if not _force_rebuild and cached:
        _cached_preds = cached.get('data')
        if _cached_preds:
            # Stale-while-revalidate: if the cache is past its TTL, rebuild in the
            # background but still serve the stale slate now so no request blocks
            # on the multi-second model build.
            if (now_ts - cached['ts']) >= cache_ttl:
                _start_background_predictions_refresh(sport, days)
            out = _filter_exhibition_predictions(_copy.deepcopy(_cached_preds))
            try:
                # Fast path: hydrate book_* from the betting_lines DB synchronously
                # and refresh live ESPN/DK odds OFF the request path (throttled
                # daemon) so cache hits never block on synchronous odds HTTP.
                _hydrate_book_lines_db_only(sport, out)
                for _bp in out:
                    if isinstance(_bp, dict):
                        _ensure_book_moneylines(_bp)
                _start_background_book_refresh(sport, out)
            except Exception as _bk_cache:
                logger.debug(f"[{sport}] book hydrate on predictions cache hit: {_bk_cache}")
            _apply_mlb_spread_fade_batch(sport, out)
            return out

    # Request-path cold miss: never block ~60–90s on a full v2/XGB rebuild.
    # Serve disk/recovered slate or a fast DB+ESPN skeleton; full rebuild off-path.
    _fast_cold_build = False
    _cold_refresh_started = False
    if not _force_rebuild:
        _recovered = _recover_cached_predictions(sport)
        if _recovered:
            _start_background_predictions_refresh(sport, days)
            _cold_refresh_started = True
            out = _filter_exhibition_predictions(_recovered)
            try:
                _hydrate_book_lines_db_only(sport, out)
                for _bp in out:
                    if isinstance(_bp, dict):
                        _ensure_book_moneylines(_bp)
                _start_background_book_refresh(sport, out)
            except Exception as _bk_cold:
                logger.debug(f"[{sport}] book hydrate on cold-recovered slate: {_bk_cold}")
            _apply_mlb_spread_fade_batch(sport, out)
            return out
        with _PREDICTIONS_REFRESH_LOCK:
            _already_refreshing = sport in _PREDICTIONS_REFRESH_INFLIGHT
        if not _already_refreshing:
            _start_background_predictions_refresh(sport, days)
            _cold_refresh_started = True
        _fast_cold_build = True

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
                    game['stored_elo_prob'] = _to_float_safe(existing['elo_home_prob'])
                    game['stored_xgb_prob'] = _to_float_safe(existing['xgboost_home_prob'])
                    game['stored_ensemble_prob'] = _to_float_safe(existing['meta_home_prob'])
            
            conn.close()
            
            # Build dates list from API games
            all_games_with_dates = [(parse_date(g['game_date']), g) for g in api_games if parse_date(g['game_date'])]
            all_games_with_dates.sort(key=lambda x: x[0])
        except Exception as e:
            logger.error(f"Error fetching NHL games from ESPN API: {e}")
            all_games_with_dates = []
    
    elif sport == 'SOCCER':
        # ESPN soccer/all: ~9 requests for full multi-league slate (vs 300+ per-league calls).
        api_games = _fetch_soccer_upcoming_api_games()

        # Enrich with stored predictions from database
        conn = get_db_connection()
        for game in api_games:
            pred = conn.execute('''
                SELECT elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                FROM predictions WHERE game_id = ? AND sport = ?
            ''', (game['game_id'], sport)).fetchone()
            if pred:
                game['stored_elo_prob'] = _to_float_safe(pred['elo_home_prob'])
                game['stored_xgb_prob'] = _to_float_safe(pred['xgboost_home_prob'])
                game['stored_ensemble_prob'] = _to_float_safe(pred['win_probability'])
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

    elif sport in _INDIVIDUAL_SPORT_LOADERS:
        all_games_with_dates = _INDIVIDUAL_SPORT_LOADERS[sport]()
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
                    if _is_exhibition_espn_competition(competition, event) or _is_exhibition_matchup(
                        home_team, away_team, event_name=event.get('name') or ''
                    ):
                        continue
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
                        'event_date': _raw_dt or None,
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
                game['stored_elo_prob'] = _to_float_safe(pred['elo_home_prob'])
                game['stored_xgb_prob'] = _to_float_safe(pred['xgboost_home_prob'])
                game['stored_ensemble_prob'] = _to_float_safe(pred['win_probability'])
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
    soccer_history_count = None
    if sport == 'SOCCER':
        try:
            conn_hist = get_db_connection()
            rows = conn_hist.execute(
                'SELECT game_id, home_team_id, away_team_id, home_score, away_score, '
                'game_date, league '
                'FROM games WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL',
                (sport,)
            ).fetchall()
            conn_hist.close()
            history = [dict(r) for r in rows]
            soccer_history_count = len(history)
            if history:
                existing_ids = {g.get('game_id') for g in completed_games if g.get('game_id')}
                for g in history:
                    gid = g.get('game_id')
                    if gid and gid in existing_ids:
                        continue
                    completed_games.append(g)
                    if gid:
                        existing_ids.add(gid)
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
    
    # Display logic: build cards only inside the per-sport picks window
    # (recent finals + near-term upcoming); full history lives on the results page.
    season_starts = {
        'NHL': datetime(2025, 10, 7),
        'NFL': datetime(2025, 9, 4),
        'NBA': datetime(2025, 10, 21),
        'MLB': datetime(2026, 3, 27),
        'NCAAF': datetime(2025, 8, 30),
        'NCAAB': datetime(2025, 11, 4),
        'NCAAW': datetime(2025, 11, 4),
        'WNBA': datetime(2026, 5, 8),
        'SOCCER': datetime(2025, 8, 1),
    }
    season_start = season_starts.get(sport, datetime(2025, 1, 1))
    
    # Use module-level datetime/timedelta imports to avoid local shadowing
    today = datetime.now()
    future_window_days = {
        'NBA': 30,
    }
    future_cutoff = today + timedelta(days=future_window_days.get(sport, 30))

    # ── Picks display window ───────────────────────────────────────
    # Only build prediction cards for the near-term slate; the results page
    # serves full history. Sports listed in _PICKS_DISPLAY_WINDOW build cards
    # only within [today - past, today + future]; unlisted sports keep the
    # legacy season-start..+30d behavior.
    _picks_window = _PICKS_DISPLAY_WINDOW.get(sport)
    if _picks_window:
        _display_floor = today - timedelta(days=_picks_window['past'])
        _display_ceiling = today + timedelta(days=_picks_window['future'])
    else:
        _display_floor = season_start
        _display_ceiling = future_cutoff
    
    predictions = []
    # Fetch injuries once for the whole request (15-min cache keeps it fast)
    _injuries = {} if _fast_cold_build else _fetch_injuries(sport)
    # Build heavy model objects once per page render (not once per game row).
    _xgb_model_page = None
    if not _fast_cold_build:
        try:
            _xgb_model_page = _get_xgb_spread_model(sport)
        except Exception:
            _xgb_model_page = None

    # Pre-fetch book moneylines from betting_lines for edge calculation.
    # The betting_odds join in the SQL above reads from an older table that is
    # never written to; betting_lines is the live cache from pl_book_odds_api.
    _book_ml_lookup: dict = {}
    try:
        _bl_conn = get_db_connection()
        _bl_cols = [r['name'] for r in _bl_conn.execute("PRAGMA table_info('betting_lines')").fetchall()]
        if 'home_moneyline' in _bl_cols and 'sport' in _bl_cols:
            _bl_rows = _bl_conn.execute(
                "SELECT game_id, home_moneyline, away_moneyline FROM betting_lines "
                "WHERE sport=? AND home_moneyline IS NOT NULL",
                (sport,)
            ).fetchall()
            for _bl in _bl_rows:
                _gid = _bl['game_id']
                if _gid and _gid not in _book_ml_lookup:
                    _book_ml_lookup[_gid] = {
                        'home': _bl['home_moneyline'],
                        'away': _bl['away_moneyline'],
                    }
        _bl_conn.close()
    except Exception:
        pass

    for game_date, game in all_games_with_dates:
        # Only build cards inside the picks display window (see _PICKS_DISPLAY_WINDOW);
        # full history is served by the results page.
        if game_date >= _display_floor and game_date <= _display_ceiling:
            _home_nm = game.get('home_team_id') or game.get('home_team_name') or ''
            _away_nm = game.get('away_team_id') or game.get('away_team_name') or ''
            if _is_exhibition_matchup(_home_nm, _away_nm, event_name=game.get('event_name') or ''):
                continue
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
            # Always run V2 for non-soccer sports (including finished games) so
            # Grinder2 / Takedown probabilities stay on the card; see frozen DB
            # snapshot block below so moneyline stack does not drift after scores.
            if sport != 'SOCCER' and not _fast_cold_build:
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
                # Treat this as insufficient only when all model outputs are missing.
                # A true 50/50 probability is still valid model output and should render.
                _available_count = sum(
                    1 for p in [elo_prob, xgb_prob, ensemble_prob, _g2_soc, _ts_soc] if p is not None
                )
                if _available_count == 0:
                    # Insufficient data — avoid synthetic 50/50 defaults on card face.
                    elo_prob = None
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
                elo_prob = None
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

            # Finished games: restore the published Elo / XSharp / ensemble snapshot
            # from the predictions row so displayed picks cannot drift after the final.
            if is_completed and sport != 'SOCCER':
                _fp_se = game.get('stored_ensemble_prob')
                _fp_sx = game.get('stored_xgb_prob')
                _fp_selo = game.get('stored_elo_prob')
                _fp_elo = _to_float_safe(_fp_selo)
                _fp_xgb = _to_float_safe(_fp_sx)
                _fp_ens = _to_float_safe(_fp_se)
                if _fp_elo is not None:
                    elo_prob = _fp_elo
                if _fp_xgb is not None:
                    xgb_prob = _fp_xgb
                if _fp_ens is not None:
                    ensemble_prob = _fp_ens
                if v2_pred:
                    game['glicko2_prob'] = v2_pred.get('glicko2_prob')
                    game['trueskill_prob'] = v2_pred.get('trueskill_prob')
            
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
            if sport == 'SOCCER' and soccer_pred and soccer_pred.get('draw_prob') is not None and ensemble_prob is not None:
                _hw, _dw, _aw = _soccer_threeway_probs(ensemble_prob, soccer_pred['draw_prob'])
                if _hw is not None:
                    game_dict['draw_prob'] = round(_dw * 100, 1)
                    game_dict['home_win_prob'] = round(_hw * 100, 1)
                    game_dict['away_win_prob'] = round(_aw * 100, 1)
                    _best = max(
                        [('home', _hw, game['home_team_id']),
                         ('draw', _dw, 'Draw'),
                         ('away', _aw, game['away_team_id'])],
                        key=lambda x: x[1],
                    )
                    game_dict['predicted_winner'] = _best[2]
            elif ensemble_prob is not None:
                game_dict['predicted_winner'] = game['home_team_id'] if ensemble_prob > 0.5 else game['away_team_id']
            elif elo_prob is not None:
                game_dict['predicted_winner'] = game['home_team_id'] if elo_prob > 0.5 else game['away_team_id']
            else:
                game_dict['predicted_winner'] = None if sport == 'SOCCER' else game['home_team_id']
            
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
            elif game_dict.get('home_score') is None and not _fast_cold_build:
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

            if game_dict.get('home_score') is None and not _fast_cold_build:
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

            if game_dict.get('home_score') is None and not _fast_cold_build:
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
                        # Edge = model probability vs BOOK (sportsbook) implied probability.
                        # Primary: home_implied_prob from betting_odds join (may be empty).
                        # Fallback 1: _book_ml_lookup pre-fetched from betting_lines (live cache).
                        # Fallback 2: game_dict['home_moneyline'] from betting_odds join (real book data).
                        # Do NOT derive from PL model moneyline — that produces edge ≈ 0.
                        _gid_edge = game_dict.get('game_id', '')
                        _bl_entry = _book_ml_lookup.get(_gid_edge, {})
                        _home_ml = (_to_float_safe(_bl_entry.get('home'))
                                    or _to_float_safe(game_dict.get('home_moneyline')))
                        _away_ml = (_to_float_safe(_bl_entry.get('away'))
                                    or _to_float_safe(game_dict.get('away_moneyline')))
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

                        # Rule #10: MLB contextual layers (bullpen, lineup, park, SP form, umpire, timing).
                        from mlb_context import apply_mlb_context_layers as _apply_mlb_ctx
                        _home_inj = game_dict.get('home_injuries') or []
                        _away_inj = game_dict.get('away_injuries') or []
                        _book_total = _to_float_safe(game_dict.get('book_total') or game_dict.get('total'))
                        _model_total = _to_float_safe(game_dict.get('xgb_total'))
                        _ctx = _apply_mlb_ctx(
                            home_team=_ht,
                            away_team=_at,
                            game_date=_gdate,
                            game_id=game_dict.get('game_id'),
                            pitch=_pitch,
                            home_injuries=_home_inj,
                            away_injuries=_away_inj,
                            pre_blended=_pre_blended,
                            home_mkt=_home_mkt,
                            home_ml=_home_ml,
                            away_ml=_away_ml,
                            book_total=_book_total,
                            model_total=_model_total,
                            umpire_name=game_dict.get('umpire_name'),
                            injury_conf_default=_MLB_INJURY_CONF_DEFAULT,
                        )
                        _inj_conf = _ctx.injury_confidence
                        _home_adj = _ctx.home_ml_adj
                        _away_adj = _ctx.away_ml_adj
                        _total_adj = _ctx.total_adj
                        _home_tier = _ctx.home_tier
                        _away_tier = _ctx.away_tier
                        _home_t1 = _ctx.home_tier1
                        _home_t2 = _ctx.home_tier2
                        _away_t1 = _ctx.away_tier1
                        _away_t2 = _ctx.away_tier2
                        _home_relief_out = _ctx.home_relief_out
                        _away_relief_out = _ctx.away_relief_out

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
                        game_dict['mlb_context_diagnostics'] = _ctx.diagnostics.to_dict()
                        game_dict['early_market_projection'] = _ctx.early_market
                        game_dict['mlb_lineup_confirmed'] = _ctx.lineup_confirmed

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

            # Real book lines from betting_odds join (NHL/DB path) — Books column only.
            if game.get('spread') is not None and game_dict.get('book_spread') is None:
                game_dict['book_spread'] = game['spread']
            if game.get('total') is not None and game_dict.get('book_total') is None:
                game_dict['book_total'] = game['total']
            if game.get('home_moneyline') is not None and game_dict.get('book_home_moneyline') is None:
                game_dict['book_home_moneyline'] = int(game['home_moneyline'])
            if game.get('away_moneyline') is not None and game_dict.get('book_away_moneyline') is None:
                game_dict['book_away_moneyline'] = int(game['away_moneyline'])
            if game_dict.get('book_spread') is not None or game_dict.get('book_home_moneyline') is not None:
                game_dict.setdefault('book_odds_source', 'betting_odds')
            _ensure_book_moneylines(game_dict)

            # Picks page: skip finals (results page owns completed games).
            if is_completed:
                continue

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

    # Cache market lines and hydrate book_* from DB (ESPN fetch runs on picks page).
    _upcoming_n = sum(1 for p in predictions if p.get('home_score') is None)
    _ml_limit = min(80, max(20, _upcoming_n)) if sport == 'MLB' else min(40, max(20, _upcoming_n))
    if _live_odds_ok:
        # Off-request-path build (prewarm/background): fetch live market lines.
        _cache_market_lines_for_predictions(sport, predictions, limit=_ml_limit)
    _attach_market_lines_to_predictions(sport, predictions)
    _hydrate_book_lines_db_only(sport, predictions)
    if sport in ['NBA', 'MLB', 'NCAAW', 'SOCCER', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA']:
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
                    _elo_save = pred.get('elo_prob')
                    _xgb_save = pred.get('xgb_prob')
                    _ens_save = pred.get('ensemble_prob')
                    if _elo_save is None or _xgb_save is None or _ens_save is None:
                        continue
                    try:
                        cursor_save.execute('''
                            INSERT INTO predictions (
                                game_id, sport, league, game_date, home_team_id, away_team_id,
                                elo_home_prob, xgboost_home_prob, win_probability, locked
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ''', (
                            pred['game_id'], sport, pred.get('league') or sport, pred['game_date'],
                            pred['home_team_id'], pred['away_team_id'],
                            float(_elo_save) / 100.0,
                            float(_xgb_save) / 100.0,
                            float(_ens_save) / 100.0,
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

    # Model-level fades (spread / ML / O-U) once per prediction dict
    _apply_model_fades_batch(sport, predictions)

    # SOCCER: the ML fade above inverts the two-way model probs (ensemble/xgb/…)
    # so the Pick Confidence boxes point at the correct side. But the 3-way
    # home_win_prob/away_win_prob/predicted_winner were derived from the
    # PRE-fade ensemble far above, so the card face (win % + green "favored"
    # highlight) would still point at the stale/wrong team. Recompute the
    # 3-way split (and predicted_winner) from the FADED ensemble so the face
    # can never contradict the models below it.
    if sport == 'SOCCER':
        for _sp in predictions:
            if _sp.get('home_score') is not None:
                continue  # completed game — face favored box not shown
            _dp = _safe_float(_sp.get('draw_prob'))
            _ep = _safe_float(_sp.get('ensemble_prob'))
            if _dp is None or _ep is None:
                continue  # no 3-way path (face falls back to faded xgb_prob)
            _hw, _dw, _aw = _soccer_threeway_probs(_ep, _dp)
            if _hw is None:
                continue
            _sp['home_win_prob'] = round(_hw * 100, 1)
            _sp['away_win_prob'] = round(_aw * 100, 1)
            _sp['draw_prob'] = round(_dw * 100, 1)
            _sp['predicted_winner'] = max(
                [('home', _hw, _sp.get('home_team_id')),
                 ('draw', _dw, 'Draw'),
                 ('away', _aw, _sp.get('away_team_id'))],
                key=lambda x: x[1],
            )[2]

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
                    pred['our_spread'] = _round_to_half(proj['projected_spread'])
                    pred['our_total'] = _round_to_half(proj['projected_total'])
                    if pred['our_spread'] is not None and pred['our_total'] is not None:
                        _h, _a = _scores_from_spread_total(pred['our_spread'], pred['our_total'])
                        if _h is not None:
                            pred['our_home_pts'] = _h
                            pred['our_away_pts'] = _a
                        else:
                            pred['our_home_pts'] = _round_to_half(proj['home_pts']) if proj['home_pts'] is not None else None
                            pred['our_away_pts'] = _round_to_half(proj['away_pts']) if proj['away_pts'] is not None else None
                    else:
                        pred['our_home_pts'] = _round_to_half(proj['home_pts']) if proj['home_pts'] is not None else None
                        pred['our_away_pts'] = _round_to_half(proj['away_pts']) if proj['away_pts'] is not None else None
                    pred['our_home_eff'] = home_eff
                    pred['our_away_eff'] = away_eff
                    pred['our_pace']     = proj['avg_pace']
                    pred['our_method']   = 'efficiency'
                    pred['pl_variance_tier']   = proj.get('variance_tier')
                    pred['pl_confidence_tier'] = proj.get('confidence_tier')
                    # Consensus total: blend PL efficiency + XSharp totals
                    if xs_total is not None and pred['our_total'] is not None:
                        _delta = abs(float(pred['our_total']) - float(xs_total))
                        if _delta <= 0.5:
                            pred['consensus_total'] = _round_to_half(
                                (float(pred['our_total']) + float(xs_total)) / 2.0
                            )
                        else:
                            pred['consensus_total'] = _round_to_half(
                                0.6 * float(pred['our_total']) + 0.4 * float(xs_total)
                            )
                        pred['pl_model_delta'] = round(float(pred['our_total']) - float(xs_total), 1)
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
            # EV must be computed against BOOK (sportsbook) lines, not PL model odds.
            # book_home_moneyline is set by _hydrate_book_lines_db_only (runs before this).
            # home_moneyline is now the PL model's odds — comparing to itself gives EV ≈ 0.
            _home_ml   = (_to_float_safe(_pred.get('book_home_moneyline'))
                          or _to_float_safe(_pred.get('home_moneyline')))
            _away_ml   = (_to_float_safe(_pred.get('book_away_moneyline'))
                          or _to_float_safe(_pred.get('away_moneyline')))
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
            # Prefer XSharp total (aligns with market); PL efficiency can over-project.
            _xgb_tot  = _to_float_safe(_pred.get('xgb_total'))
            _our_tot  = _xgb_tot if _xgb_tot is not None else _to_float_safe(_pred.get('our_total'))
            _mkt_tot  = _to_float_safe(_pred.get('market_total'))
            _total_ev = None
            if _our_tot is not None and _mkt_tot is not None:
                _tot_edge  = _our_tot - _mkt_tot
                # Cap edge at ±4 pts (NBA/NCAAB) / ±1 (NHL/MLB) to prevent absurd EVs
                _tot_cap   = {'NHL': 0.75, 'MLB': 0.75}.get(sport, 4.0)
                _tot_edge  = max(-_tot_cap, min(_tot_cap, _tot_edge))
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

    try:
        if _live_odds_ok:
            # Off-request-path build: pull fresh live sportsbook odds now.
            _refresh_books_on_predictions(sport, predictions)
        else:
            # Request-path cold miss: keep TTFB low — hydrate book_* from the DB
            # only (no live HTTP) and refresh live odds OFF the request path.
            _hydrate_book_lines_db_only(sport, predictions)
            for _bp in predictions:
                if isinstance(_bp, dict):
                    _ensure_book_moneylines(_bp)
            _start_background_book_refresh(sport, predictions)
    except Exception as _bk_build:
        logger.debug(f"[{sport}] book refresh before predictions cache store: {_bk_build}")

    predictions = _filter_exhibition_predictions(predictions)
    _trim_cache(_PREDICTIONS_CACHE, cache_ttl, max_entries=50)
    if predictions:
        _entry = {'ts': _time.time(), 'data': _copy.deepcopy(predictions)}
        _PREDICTIONS_CACHE[cache_key] = _entry
        # Mirror to disk so a worker restart can serve this slate warm.
        _persist_predictions_to_disk(cache_key, _entry)
    # A request-path cold miss skipped live odds above; kick a full off-path
    # rebuild so the cache (and disk) repopulate with live odds within seconds.
    # Single-flighted, so this can't stack duplicate rebuilds.
    if not _live_odds_ok and predictions and not _cold_refresh_started:
        _start_background_predictions_refresh(sport, days)
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

            # Get team full names
            home_team_full = abbr_to_full.get(api_game['home_team'], api_game['home_team'])
            away_team_full = abbr_to_full.get(api_game['away_team'], api_game['away_team'])

            if not pred or pred[0] is None:
                # No stored prediction (e.g. Super Bowl / playoff game never visited).
                # Fall back to live Elo so the game still shows in results.
                try:
                    _hr = get_elo(home_team_full)
                    _ar = get_elo(away_team_full)
                    elo_prob = expected_score(_hr, _ar) if _hr and _ar else 0.5
                except Exception:
                    elo_prob = 0.5
                xgb_prob = elo_prob
                ens_prob = elo_prob
            else:
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
    """Calculate NHL model performance for the current regular season (Oct–Apr).

    Used by legacy callers; the results page uses _banner_daily_results_for_range.
    V2 inference runs only for games in the last 21 days to avoid worker timeouts.
    """
    try:
        import time as _t
        from datetime import datetime, timedelta
        _wall_start = _t.time()
        _WALL_BUDGET = 25.0  # seconds — must finish before Render's 30s timeout

        conn = get_db_connection()

        yesterday_dt = datetime.now() - timedelta(days=1)
        season_start_dt, season_end_dt = _nhl_results_regular_season_bounds(yesterday_dt)
        season_start = season_start_dt.strftime('%Y-%m-%d')
        yesterday = min(season_end_dt, yesterday_dt).strftime('%Y-%m-%d')

        games = conn.execute('''
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.meta_home_prob
            FROM games g
            LEFT JOIN predictions p ON (
                p.sport = 'NHL' AND (
                    p.game_id = g.game_id OR
                    (date(p.game_date) = date(g.game_date)
                     AND p.home_team_id = g.home_team_id
                     AND p.away_team_id = g.away_team_id)
                )
            )
            WHERE g.sport = 'NHL'
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) >= ?
              AND date(g.game_date) <= ?
            ORDER BY g.game_date DESC
        ''', (season_start, yesterday)).fetchall()
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

            # Stop if we've used the wall-clock budget (prevents Render timeout).
            if _t.time() - _wall_start > _WALL_BUDGET:
                logger.info(f"[NHL results] wall budget hit after {included_games} games")
                break

            glicko2_prob = None
            trueskill_prob = None
            v2 = None
            days_ago = (datetime.now() - game_date).days if game_date else 999
            if days_ago <= 21:
                try:
                    v2 = get_v2_prediction('NHL', game['home_team_id'], game['away_team_id'], game['game_date'])
                    glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
                    trueskill_prob = v2.get('trueskill_prob') if v2 else None
                except Exception:
                    pass

            if xgb_prob is None and v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
            if elo_prob is None:
                elo_prob = xgb_prob
            if xgb_prob is None:
                xgb_prob = elo_prob
            if meta_prob is None:
                meta_prob = _compute_ensemble_prob(
                    glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=elo_prob
                )

            # Skip only if we truly have nothing (V2 model unavailable for this matchup).
            if all(p is None for p in [glicko2_prob, trueskill_prob, elo_prob, xgb_prob, meta_prob]):
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

def _nba_model_probs_for_grading(game_row, home_team, away_team, game_date_key):
    """NBA wrapper — see _model_probs_for_grading."""
    return _model_probs_for_grading('NBA', game_row, home_team, away_team, game_date_key)


def calculate_nba_weekly_performance():
    """Calculate NBA model performance week by week using stored + frozen model predictions."""
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
        prob_sql = _predictions_prob_select_sql(conn)

        games = conn.execute(f'''
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.predicted_total,
                   {prob_sql}
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

            game_date_key = _normalize_game_date_key(game['game_date'])
            glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = _nba_model_probs_for_grading(
                game, home_team, away_team, game_date_key,
            )
            if xgb_prob is None:
                xgb_prob = elo_prob

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

            _pt = to_float(game['predicted_total'])
            weekly_results[week]['games'].append({
                'game_id':         game['game_id'],
                'date':             game['game_date'].split()[0],
                'away':             away_team,
                'home':             home_team,
                'away_score':       int(away_score),
                'home_score':       int(home_score),
                'predicted_total':  _round_to_half(_pt) if _pt is not None else None,
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


def _compute_spread_total_for_daily(sport, daily_results, *, skip_efficiency=False):
    """Compute XSharp spread/total grading for games already in daily_results (in-place).
    Returns aggregate stats dict (may be None for spread/total if model unavailable,
    but market lines and H2H are always attached)."""
    st_cov = st_gr = st_push = tt_cor = tt_gr = tt_push = 0
    pl_st_cov = pl_st_gr = pl_st_push = pl_tt_cor = pl_tt_gr = pl_tt_push = 0
    try:
        _apply_fades_to_daily_results(sport, daily_results)
        # H2H last-10 "Our Total" (used as the O/U line the model is compared to)
        try:
            _attach_h2h_projection_to_daily_results(sport, daily_results, n=10)
        except Exception as _h2he:
            logger.debug(f"[h2h] daily attach failed for {sport}: {_h2he}")
        if not skip_efficiency:
            try:
                _attach_nba_efficiency_to_daily_results(sport, daily_results)
            except Exception as _ne:
                logger.debug(f"[nba-eff] pre-compute failed for {sport}: {_ne}")
        _game_count = sum(len(dd.get('games', [])) for dd in daily_results.values())
        _snapshot_build = _os.environ.get('PL_SNAPSHOT_BUILD') == '1'
        _skip_heavy_predict = _game_count > 500 and not _snapshot_build
        _xgb = None
        _sp = None
        if not _skip_heavy_predict:
            try:
                _xgb = _get_xgb_spread_model(sport)
            except Exception:
                pass
            if sport in ['NBA', 'MLB', 'WNBA']:
                try:
                    _sp = _score_predictor_instance(sport)
                except Exception:
                    pass
        elif _game_count > 0:
            logger.info(
                f"[{sport}] spread/total: skipping per-game XGB/SP predict for {_game_count} games "
                "(using H2H + efficiency totals; prevents worker timeout)"
            )
        _has_model = bool(_xgb or _sp)

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
            _line_limit = '' if _snapshot_build else ' LIMIT 2000'
            if has_extra:
                rows = conn.execute(f'''
                    SELECT game_id, game_date, home_team, away_team, spread, total, fetched_at
                    FROM betting_lines
                    WHERE sport=?
                    ORDER BY fetched_at DESC{_line_limit}
                ''', (sport,)).fetchall()
            else:
                rows = conn.execute(f'''
                    SELECT game_id, spread, total
                    FROM betting_lines{_line_limit}
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
            odds_rows = conn.execute('SELECT game_id, spread, total FROM betting_odds LIMIT 2000').fetchall()
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

        live_attempts = 0
        live_cap = 25 if sport == 'SOCCER' else (15 if sport == 'NBA' else 8)
        _xgb_pair_cache = {}
        for dd in daily_results.values():
            for g in dd.get('games', []):
                h, a = g['home'], g['away']
                gd = g['date']
                gid = str(g.get('game_id') or '')
                hs, as_ = g['home_score'], g['away_score']
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
                if ms is None:
                    try:
                        bs = _safe_float(g.get('book_spread'))
                        if bs is not None:
                            ms = float(bs)
                    except Exception:
                        pass
                if mt is None:
                    try:
                        bt = _safe_float(g.get('book_total'))
                        if bt is not None:
                            mt = float(bt)
                    except Exception:
                        pass

                try:
                    if _xgb:
                        _pair_key = (hk, ak, round(mt, 1) if mt is not None else None)
                        _cached = _xgb_pair_cache.get(_pair_key)
                        if _cached is not None:
                            xs, xt = _cached
                        else:
                            xp = _xgb.predict(h, a, vegas_total=mt)
                            xs = round(float(xp[2]), 1) if xp and xp[2] is not None else None
                            xt = round(float(xp[3]), 1) if xp and xp[3] is not None else None
                            _xgb_pair_cache[_pair_key] = (xs, xt)
                    if (xs is None or xt is None) and _sp:
                        nh, na, ns, nt = _sp.predict_score(h, a, sport)
                        if xs is None and ns is not None:
                            xs = round(float(ns), 1)
                        if xt is None and nt is not None:
                            xt = round(float(nt), 1)
                except Exception:
                    xs = xt = None
                if xs is None or xt is None:
                    _fill_xsharp_from_efficiency_if_missing(g, sport)
                    xs = g.get('xgb_spread') if xs is None else xs
                    xt = g.get('xgb_total') if xt is None else xt
                if xt is None and g.get('predicted_total') is not None:
                    try:
                        xt = round(float(g['predicted_total']), 1)
                    except (TypeError, ValueError):
                        pass
                if xs is None and g.get('our_spread') is not None:
                    try:
                        xs = round(float(g['our_spread']), 1)
                    except (TypeError, ValueError):
                        pass
                if xt is None and g.get('our_home_pts') is not None and g.get('our_away_pts') is not None:
                    try:
                        xt = round(float(g['our_home_pts']) + float(g['our_away_pts']), 1)
                    except (TypeError, ValueError):
                        pass
                g['xgb_total'] = xt
                g['xgb_spread'] = xs
                if sport == 'NHL' and xs is not None:
                    xs = -xs
                    g['xgb_spread'] = xs
                _grade_xt = xt
                if _grade_xt is None:
                    _grade_xt = _safe_float(g.get('our_total'))
                if _grade_xt is None:
                    _grade_xt = _safe_float(g.get('predicted_total'))

                # Live fallback for missing market lines (recent games only)
                if (ms is None or mt is None) and live_attempts < live_cap and gd:
                    try:
                        if sport == 'NBA':
                            gd_dt = parse_date(gd)
                            if gd_dt and abs((datetime.now() - gd_dt).days) > 3:
                                raise Exception("skip live fetch for older NBA dates")
                        elif sport == 'SOCCER':
                            gd_dt = parse_date(gd)
                            if gd_dt and abs((datetime.now() - gd_dt).days) > 21:
                                raise Exception("skip live fetch for older SOCCER dates")
                        live_attempts += 1
                        live_line = _fetch_live_market_line(
                            sport, gid, gd, h, a,
                            league_name=g.get('league') or g.get('league_name'),
                        )
                        if live_line:
                            if ms is None:
                                ms = live_line.get('spread')
                            if mt is None:
                                mt = live_line.get('total')
                            if (ms is not None or mt is not None):
                                try:
                                    _conn_line = get_db_connection()
                                    _upsert_betting_line(
                                        _conn_line, sport, gid, gd, h, a, ms, mt,
                                        live_line.get('source'),
                                        home_moneyline=live_line.get('home_moneyline'),
                                        away_moneyline=live_line.get('away_moneyline'),
                                    )
                                    _conn_line.commit()
                                    _conn_line.close()
                                except Exception:
                                    pass
                    except Exception:
                        pass

                if mt is None and sport in ('NBA', 'WNBA', 'NHL', 'MLB', 'NFL', 'SOCCER'):
                    try:
                        gd_dt = parse_date(gd) if gd else None
                        if gd_dt is None or (datetime.now() - gd_dt).days <= 21:
                            _bk = _fetch_pl_book_line_for_game(
                                sport, gid, h, a, gd,
                                league_name=g.get('league') or g.get('league_name'),
                            )
                            if _bk:
                                _apply_pl_book_row_to_game(g, _bk)
                                _persist_pl_book_row(sport, g, _bk)
                                if ms is None and _bk.get('spread') is not None:
                                    ms = float(_bk['spread'])
                                if mt is None and _bk.get('total') is not None:
                                    mt = float(_bk['total'])
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
                    park_adj = _park_weather_total_adjustment(
                        sport, h, game_date=gd, game_id=g.get('game_id'), away_team=a,
                    )
                    adj_xt = _grade_xt + inj_adj + rest_adj + park_adj if _grade_xt is not None else None
                    our_total_h2h = g.get('our_total')
                    g['xgb_total_adj'] = round(adj_xt, 2) if adj_xt is not None else None
                    g['total_adj_breakdown'] = {
                        'injury': round(inj_adj, 2),
                        'rest': round(rest_adj, 2),
                        'park': round(park_adj, 2),
                    }
                    sportsbook_mt = mt
                    if sportsbook_mt is None:
                        sportsbook_mt = _safe_float(g.get('book_total'))
                    _book_mt_only = _safe_float(g.get('book_total'))
                    if _book_mt_only is not None:
                        sportsbook_mt = _book_mt_only
                    total_fallback_used = False
                    if sportsbook_mt is None:
                        if our_total_h2h is not None:
                            mt = round(float(our_total_h2h), 1)
                            g['market_total_reason'] = "H2H last-10"
                        elif _OU_BENCH.get(sport):
                            mt = float(_OU_BENCH[sport])
                            g['market_total_reason'] = "sport benchmark (not graded)"
                            total_fallback_used = True
                        elif adj_xt is not None:
                            mt = round(adj_xt, 1)
                            g['market_total_reason'] = "XSharp total (not graded)"
                            total_fallback_used = True
                    _grade_mt = sportsbook_mt if sportsbook_mt is not None else (mt if not total_fallback_used else None)
                    g['market_total'] = mt if mt is not None else sportsbook_mt
                    if _grade_mt is None and mt is None:
                        g['market_total_reason'] = g.get('market_total_reason') or "no total line found"
                        g['total_pick_reason'] = g.get('total_pick_reason') or "no total line"
                    elif adj_xt is None:
                        g['total_pick_reason'] = "model score unavailable"
                    elif _grade_mt is not None:
                        if sport in OU_FADE_SPORTS:
                            _apply_ou_fade(g, market_total=_grade_mt)
                            _grade_xt = _safe_float(g.get('xgb_total')) or _grade_xt
                            adj_xt = (
                                _grade_xt + inj_adj + rest_adj + park_adj
                                if _grade_xt is not None else None
                            )
                            g['xgb_total_adj'] = round(adj_xt, 2) if adj_xt is not None else None
                            our_total_h2h = g.get('our_total')
                        edge = adj_xt - _grade_mt
                        tp_disp = 'OVER' if edge >= 0 else 'UNDER'
                        if abs(at - _grade_mt) >= 1e-9:
                            aou = 'OVER' if at > _grade_mt else 'UNDER'
                            tp_ok = (tp_disp == aou)
                            tt_gr += 1
                            if tp_ok:
                                tt_cor += 1
                        else:
                            tp_disp = 'PUSH'
                            tt_gr += 1
                            tt_push += 1
                        strong = False
                        if our_total_h2h is not None:
                            h2h_edge = our_total_h2h - _grade_mt
                            strong = (h2h_edge > 0 and edge > 0) or (h2h_edge < 0 and edge < 0)
                        g['strong_ou'] = strong
                        _lbl_mt = sportsbook_mt if sportsbook_mt is not None else _grade_mt
                        label = f"{tp_disp.title()} {_lbl_mt:.1f}"
                        if strong and abs(edge) >= _ou_edge_threshold(sport):
                            label += " ★"
                        g['total_pick_label'] = label
                        pl_tot = _safe_float(g.get('our_total'))
                        if pl_tot is not None:
                            if abs(at - _grade_mt) >= 1e-9:
                                pl_tt_gr += 1
                                pl_pick = 'OVER' if pl_tot >= _grade_mt else 'UNDER'
                                pl_tot_ok = pl_pick == ('OVER' if at > _grade_mt else 'UNDER')
                                if pl_tot_ok:
                                    pl_tt_cor += 1
                                g['pl_total_correct'] = pl_tot_ok
                            else:
                                pl_tt_gr += 1
                                pl_tt_push += 1
                                g['pl_total_correct'] = None
                    elif total_fallback_used:
                        g['total_pick_reason'] = "benchmark only (not graded)"

                    ps = _safe_float(g.get('our_spread'))
                    if ps is not None and hs is not None and as_ is not None:
                        if ps >= run_line:
                            pl_pick_team = h
                            pl_pick_line = -run_line
                        elif ps <= -run_line:
                            pl_pick_team = a
                            pl_pick_line = -run_line
                        else:
                            pl_pick_team = a if ps > 0 else h
                            pl_pick_line = run_line
                        if pl_pick_team == h:
                            if pl_pick_line < 0:
                                pl_ok = am > run_line
                            else:
                                pl_ok = am >= -run_line
                        else:
                            if pl_pick_line < 0:
                                pl_ok = am < -run_line
                            else:
                                pl_ok = am <= run_line
                        pl_st_gr += 1
                        if pl_ok:
                            pl_st_cov += 1
                        g['pl_spread_correct'] = pl_ok

                else:
                    # ── Non-MLB grading: Spread uses Vegas (unchanged).
                    #    O/U uses Vegas market_total vs XSharp xgb_total + post-hoc
                    #    adjustments (injury / rest / park / weather) + CLV threshold
                    #    + consensus-of-two (STRONG when H2H agrees).
                    inj_adj = _injury_total_adjustment(sport, h, a)
                    rest_adj = _rest_total_adjustment(sport, h, a, gd)
                    park_adj = _park_weather_total_adjustment(sport, h)
                    adj_xt = _grade_xt + inj_adj + rest_adj + park_adj if _grade_xt is not None else None
                    our_total_h2h = g.get('our_total')
                    g['xgb_total_adj'] = round(adj_xt, 2) if adj_xt is not None else None
                    g['total_adj_breakdown'] = {
                        'injury': round(inj_adj, 2),
                        'rest': round(rest_adj, 2),
                        'park': round(park_adj, 2),
                    }
                    sportsbook_mt = mt
                    if sportsbook_mt is None:
                        sportsbook_mt = _safe_float(g.get('book_total'))
                    _book_mt_only = _safe_float(g.get('book_total'))
                    if _book_mt_only is not None:
                        sportsbook_mt = _book_mt_only
                    total_fallback_used = False
                    if sportsbook_mt is None:
                        if our_total_h2h is not None:
                            mt = round(float(our_total_h2h), 1)
                            g['market_total_reason'] = "H2H last-10"
                        elif _OU_BENCH.get(sport):
                            mt = float(_OU_BENCH[sport])
                            g['market_total_reason'] = (
                                "sport benchmark (snapshot)"
                                if _snapshot_build else "sport benchmark (not graded)"
                            )
                            total_fallback_used = not _snapshot_build
                        elif adj_xt is not None:
                            mt = round(adj_xt, 1)
                            g['market_total_reason'] = (
                                "XSharp total (snapshot)"
                                if _snapshot_build else "XSharp total (not graded)"
                            )
                            total_fallback_used = not _snapshot_build
                    g['market_spread'] = ms
                    g['market_total'] = mt if mt is not None else sportsbook_mt
                    if ms is None:
                        g['market_spread_reason'] = "no sportsbook spread line found"
                    if mt is None and sportsbook_mt is None:
                        g['market_total_reason'] = g.get('market_total_reason') or "no sportsbook total line found"

                    # Fallback spread: pick-em for non-NHL; NHL snapshot uses ±1.5 puck line.
                    if ms is None and xs is not None and sport != 'NHL':
                        ms = 0.0
                        g['market_spread_reason'] = "pick-em (fallback)"
                    elif (
                        ms is None and xs is not None and sport == 'NHL' and _snapshot_build
                    ):
                        ens = _safe_float(g.get('ens_prob'))
                        if ens is not None:
                            ms = -_PUCK_LINE_VALUE if ens >= 50 else _PUCK_LINE_VALUE
                        else:
                            ms = -_PUCK_LINE_VALUE if xs >= 0 else _PUCK_LINE_VALUE
                        g['market_spread_reason'] = "puck line ±1.5 (fallback)"
                    g['market_spread'] = ms
                    if xs is not None and ms is not None:
                        dm = xs + ms
                        da = am + ms
                        if abs(dm) < 1e-9:
                            sp_disp = 'PUSH'
                            st_push += 1
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

                    ps = _safe_float(g.get('our_spread'))
                    if ps is not None and ms is not None:
                        dm_pl = ps + ms
                        da_pl = am + ms
                        if abs(dm_pl) < 1e-9:
                            pl_st_push += 1
                        elif abs(da_pl) >= 1e-9:
                            pl_side = 'HOME' if dm_pl > 0 else 'AWAY'
                            act_side = 'HOME' if da_pl > 0 else 'AWAY'
                            pl_st_gr += 1
                            if pl_side == act_side:
                                pl_st_cov += 1
                            g['pl_spread_correct'] = (pl_side == act_side)
                        elif abs(dm_pl) < 1e-9:
                            g['pl_spread_correct'] = None

                    _grade_mt = sportsbook_mt if sportsbook_mt is not None else (mt if not total_fallback_used else None)
                    if sport in OU_FADE_SPORTS and _grade_mt is not None:
                        _apply_ou_fade(g, market_total=_grade_mt)
                        _grade_xt = _safe_float(g.get('xgb_total')) or _grade_xt
                        adj_xt = (
                            _grade_xt + inj_adj + rest_adj + park_adj
                            if _grade_xt is not None else None
                        )
                        g['xgb_total_adj'] = round(adj_xt, 2) if adj_xt is not None else None
                        our_total_h2h = g.get('our_total')
                    if adj_xt is not None and _grade_mt is not None:
                        edge = adj_xt - _grade_mt
                        tp_disp = 'OVER' if edge >= 0 else 'UNDER'
                        if abs(at - _grade_mt) >= 1e-9:
                            aou = 'OVER' if at > _grade_mt else 'UNDER'
                            tp_ok = (tp_disp == aou)
                            tt_gr += 1
                            if tp_ok:
                                tt_cor += 1
                        else:
                            tp_disp = 'PUSH'
                            tt_gr += 1
                            tt_push += 1
                        line_for_label = _grade_mt
                        strong = False
                        if our_total_h2h is not None:
                            h2h_edge = our_total_h2h - _grade_mt
                            strong = (h2h_edge > 0 and edge > 0) or (h2h_edge < 0 and edge < 0)
                        g['strong_ou'] = strong and abs(edge) >= _ou_edge_threshold(sport)
                        pl_tot = _safe_float(g.get('our_total'))
                        if pl_tot is not None:
                            pl_pick = 'OVER' if pl_tot >= _grade_mt else 'UNDER'
                            if abs(at - _grade_mt) >= 1e-9:
                                pl_tt_gr += 1
                                pl_tot_ok = pl_pick == ('OVER' if at > _grade_mt else 'UNDER')
                                if pl_tot_ok:
                                    pl_tt_cor += 1
                                g['pl_total_correct'] = pl_tot_ok
                            else:
                                pl_tt_gr += 1
                                pl_tt_push += 1
                                g['pl_total_correct'] = None
                    elif adj_xt is not None and mt is not None and total_fallback_used:
                        edge = adj_xt - mt
                        tp_disp = 'OVER' if edge >= 0 else 'UNDER'
                        g['total_pick_reason'] = "benchmark only (not graded)"
                        line_for_label = mt
                        g['strong_ou'] = False
                    elif _grade_xt is None:
                        g['total_pick_reason'] = "model score unavailable"
                        line_for_label = None
                    elif mt is None:
                        g['total_pick_reason'] = "no sportsbook total line"
                        line_for_label = None
                    else:
                        line_for_label = None

                    # Display-ready strings for the unified table
                    g['spread_pick_label'] = None
                    if sp_disp in ('HOME', 'AWAY') and ms is not None:
                        g['spread_line_display'] = f"{ms:+.1f}" if sp_disp == 'HOME' else f"{-ms:+.1f}"
                        pick_team = h if sp_disp == 'HOME' else a
                        g['spread_pick_label'] = f"{pick_team} {g['spread_line_display']}"
                    else:
                        g['spread_line_display'] = None
                    g['total_pick_label'] = None
                    if tp_disp in ('OVER', 'UNDER') and line_for_label is not None:
                        g['total_line_display'] = f"{tp_disp.title()} {line_for_label:.1f}"
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

        _tt_decided = tt_gr - tt_push
        _pl_tt_decided = pl_tt_gr - pl_tt_push
        stats = {
            'spread_covered': st_cov,
            'spread_graded': st_gr + st_push,
            'spread_pushes': st_push,
            'spread_pct': round(st_cov / st_gr * 100, 1) if st_gr > 0 else None,
            'total_correct': tt_cor,
            'total_graded': tt_gr,
            'total_pushes': tt_push,
            'total_pct': round(tt_cor / _tt_decided * 100, 1) if _tt_decided > 0 else None,
            'pl_spread_covered': pl_st_cov,
            'pl_spread_graded': pl_st_gr + pl_st_push,
            'pl_spread_pushes': pl_st_push,
            'pl_spread_pct': round(pl_st_cov / pl_st_gr * 100, 1) if pl_st_gr > 0 else None,
            'pl_total_correct': pl_tt_cor,
            'pl_total_graded': pl_tt_gr,
            'pl_total_pushes': pl_tt_push,
            'pl_total_pct': round(pl_tt_cor / _pl_tt_decided * 100, 1) if _pl_tt_decided > 0 else None,
        }
        if st_gr == 0 and tt_gr == 0:
            logger.warning(
                f"[{sport}] spread/total: 0 graded games "
                f"(xgb={bool(_xgb)} score_pred={bool(_sp)}); check model imports on server"
            )
        return stats
    except Exception as e:
        logger.error(f"[{sport}] spread/total integration error: {e}", exc_info=True)
        if st_gr > 0 or tt_gr > 0:
            return {
                'spread_covered': st_cov,
                'spread_graded': st_gr,
                'spread_pct': round(st_cov / st_gr * 100, 1) if st_gr > 0 else 0,
                'total_correct': tt_cor,
                'total_graded': tt_gr,
                'total_pct': round(tt_cor / tt_gr * 100, 1) if tt_gr > 0 else 0,
            }
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
        ('efficiency', 'efficiency_correct', 'efficiency_prob'),
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


_ML_PERF_MODEL_KEYS = (
    ('glicko2', 'Grinder2'),
    ('trueskill', 'Takedown'),
    ('elo', 'Edge'),
    ('xgboost', 'XSharp'),
    ('ensemble', 'Sharp Consensus'),
)


def _best_ml_model_stats(overall_stats):
    """Highest-accuracy moneyline model with at least one graded game."""
    best = None
    for key, label in _ML_PERF_MODEL_KEYS:
        m = (overall_stats or {}).get(key) or {}
        total = int(m.get('total') or 0)
        if total <= 0:
            continue
        acc = m.get('accuracy')
        if acc is None:
            acc = round(int(m.get('correct') or 0) / total * 100, 1)
        if (
            best is None
            or acc > best['accuracy']
            or (acc == best['accuracy'] and total > best['total'])
        ):
            best = {
                'key': key,
                'label': label,
                'total': total,
                'correct': int(m.get('correct') or 0),
                'accuracy': acc,
            }
    return best


def _best_market_side(st, *, xsharp_prefix, pl_prefix, xsharp_label, pl_label):
    """Pick better-performing spread or O/U layer (XSharp vs PL) when both exist."""
    st = st or {}

    def _side(prefix):
        if prefix == 'spread':
            graded = int(st.get('spread_graded') or 0)
            wins = int(st.get('spread_covered') or 0)
            pct = st.get('spread_pct')
        elif prefix == 'total':
            graded = int(st.get('total_graded') or 0)
            wins = int(st.get('total_correct') or 0)
            pct = st.get('total_pct')
        elif prefix == 'pl_spread':
            graded = int(st.get('pl_spread_graded') or 0)
            wins = int(st.get('pl_spread_covered') or 0)
            pct = st.get('pl_spread_pct')
        else:
            graded = int(st.get('pl_total_graded') or 0)
            wins = int(st.get('pl_total_correct') or 0)
            pct = st.get('pl_total_pct')
        if graded <= 0 or pct is None:
            return None
        return {'graded': graded, 'wins': wins, 'pct': pct}

    xs = _side(xsharp_prefix)
    pl = _side(pl_prefix)
    if xs and pl:
        pick = xs if xs['pct'] >= pl['pct'] else pl
        label = xsharp_label if xs['pct'] >= pl['pct'] else pl_label
        return label, pick['pct'], pick['wins'], pick['graded']
    if xs:
        return xsharp_label, xs['pct'], xs['wins'], xs['graded']
    if pl:
        return pl_label, pl['pct'], pl['wins'], pl['graded']
    return xsharp_label, None, 0, 0


def _build_season_performance_summary(
    overall_stats,
    spread_total_stats,
    *,
    scope_label=None,
    games_expected=None,
    games_in_scope=None,
):
    """Season banner metrics — headline ML/spread/O/U use best-performing model per market."""
    st = spread_total_stats or {}
    best_ml = _best_ml_model_stats(overall_stats)
    if best_ml:
        ml_total = best_ml['total']
        ml_correct = best_ml['correct']
        ml_accuracy = best_ml['accuracy']
        ml_model_label = best_ml['label']
        ml_model_key = best_ml['key']
    else:
        ens = (overall_stats or {}).get('ensemble') or {}
        ml_total = int(ens.get('total') or 0)
        ml_correct = int(ens.get('correct') or 0)
        ml_accuracy = ens.get('accuracy') if ml_total > 0 else None
        ml_model_label = 'Sharp Consensus'
        ml_model_key = 'ensemble'

    spread_label, sp_pct, sp_wins, sp_gr = _best_market_side(
        st,
        xsharp_prefix='spread',
        pl_prefix='pl_spread',
        xsharp_label='XSharp',
        pl_label='Prediction Lab',
    )
    ou_label, ou_pct, ou_wins, ou_gr = _best_market_side(
        st,
        xsharp_prefix='total',
        pl_prefix='pl_total',
        xsharp_label='XSharp',
        pl_label='Prediction Lab',
    )
    return {
        'ml_total': ml_total,
        'ml_correct': ml_correct,
        'ml_accuracy': ml_accuracy,
        'ml_model_label': ml_model_label,
        'ml_model_key': ml_model_key,
        'spread_graded': sp_gr,
        'spread_covered': sp_wins,
        'spread_pct': sp_pct,
        'spread_model_label': spread_label,
        'spread_note': None,
        'ou_graded': ou_gr,
        'ou_correct': ou_wins,
        'ou_pct': ou_pct,
        'ou_model_label': ou_label,
        'ou_note': None,
        'scope_label': scope_label,
        'games_expected': games_expected,
        'games_in_scope': games_in_scope,
    }


def _results_page_meta(sport):
    name = SPORTS[sport]['name']
    return {
        'page_title': f'{name} Results | predictionlab.io',
        'page_description': (
            f'{name} season model accuracy and verified betting results — '
            'moneyline, spread, and over/under performance.'
        ),
        'canonical_url': _seo_canonical_url(),
    }


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
    games = _gradable_result_games(day_bucket.get('games', []))
    if not games:
        # Exhibition-only day (e.g. WNBA All-Star) — treat as empty for Last Night.
        return None
    # ===== SECTION: Issue 5 — Efficiency as a 6th graded moneyline model =====
    model_configs = [
        ('glicko2',   'glicko2_correct', 'glicko2_prob'),
        ('trueskill', 'trueskill_correct', 'trueskill_prob'),
        ('elo',       'elo_correct', 'elo_prob'),
        ('xgboost',   'xgb_correct', 'xgb_prob'),
        ('ensemble',  'ens_correct', 'ens_prob'),
        ('efficiency', 'efficiency_correct', 'efficiency_prob'),
    ]
    tally = {m: {'correct': 0, 'total': 0} for m, _, _ in model_configs}
    for game in games:
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
    tally['games'] = len(games)
    # Add spread + O/U tally
    sp, ou = _tally_spread_total(games)
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
    # ===== SECTION: Issue 5 — Efficiency as a 6th graded moneyline model =====
    model_configs = [
        ('glicko2',   'glicko2_correct', 'glicko2_prob'),
        ('trueskill', 'trueskill_correct', 'trueskill_prob'),
        ('elo',       'elo_correct', 'elo_prob'),
        ('xgboost',   'xgb_correct', 'xgb_prob'),
        ('ensemble',  'ens_correct', 'ens_prob'),
        ('efficiency', 'efficiency_correct', 'efficiency_prob'),
    ]
    tally = {m: {'correct': 0, 'total': 0} for m, _, _ in model_configs}
    total_games = 0
    all_games = []
    for date_key, day_data in daily_results.items():
        if not _date_in_range(date_key, start_date, end_date):
            continue
        games = _gradable_result_games(day_data.get('games', []))
        total_games += len(games)
        all_games.extend(games)
        for game in games:
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
    <meta property="og:url" content="{{ canonical_url or ('https://predictionlab.io' ~ request.path) }}">
    <meta property="og:site_name" content="predictionlab.io">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:title" content="{{ _meta_title }}">
    <meta name="twitter:description" content="{{ _meta_desc }}">
    <link rel="canonical" href="{{ canonical_url or ('https://predictionlab.io' ~ request.path) }}">
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
        .nav-cta-premium{display:inline-flex;align-items:center;padding:9px 16px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-size:0.84em;font-weight:800;text-decoration:none;letter-spacing:0.2px;white-space:nowrap;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 14px rgba(251,191,36,0.35);}
        .nav-cta-premium:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(251,191,36,0.45);}
        .tv-premium-cta-row{display:flex;align-items:stretch;gap:8px;margin:10px 12px 6px;}
        .tv-premium-cta{display:flex;align-items:center;justify-content:center;flex:1;margin:0;padding:12px 14px;border-radius:10px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-weight:800;font-size:0.92em;text-decoration:none;letter-spacing:0.2px;text-align:center;}
        .tv-premium-cta:hover{box-shadow:0 4px 14px rgba(251,191,36,0.4);}
        .tv-premium-cta-weekly{background:linear-gradient(90deg,#312e81 0%,#6d28d9 48%,#db2777 100%);color:#fff;}
        .tv-premium-cta-weekly:hover{box-shadow:0 4px 14px rgba(109,40,217,0.4);color:#fff;}
        .join-premium-bar{display:none;position:fixed;left:0;right:0;bottom:0;z-index:999;background:#0f172a;border-top:1px solid rgba(255,255,255,0.12);}
        .join-premium-inner{max-width:1200px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;}
        .join-premium-copy{color:#e2e8f0;font-size:0.86em;font-weight:600;line-height:1.35;}
        .join-premium-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
        .join-premium-btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 14px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;text-decoration:none;font-weight:800;font-size:0.82em;white-space:nowrap;}
        .join-premium-btn-weekly{background:linear-gradient(90deg,#312e81 0%,#6d28d9 48%,#db2777 100%);color:#fff;}
        .join-premium-close{border:1px solid rgba(255,255,255,0.3);background:transparent;color:#fff;border-radius:999px;width:28px;height:28px;line-height:1;cursor:pointer;font-size:18px;}
        @media(max-width:480px){.nav-cta{padding:8px 14px;font-size:0.8em;}.nav-cta-premium{padding:8px 12px;font-size:0.78em;}}
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
    <link rel="stylesheet" href="/static/css/research-theme.css">
    <link rel="stylesheet" href="/static/css/picks-nav-overrides.css">
</head>
<body>
    {% include "partials/research_header.html" %}
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
          {% if not is_premium %}
          <div class="tv-premium-cta-row">
            <a href="/checkout/weekly" class="tv-premium-cta tv-premium-cta-weekly">Try a Week</a>
            <a href="/plans" class="tv-premium-cta">&#11088; Join Premium</a>
          </div>
          {% endif %}
          <div class="tv-menu-list">
            <button class="tv-menu-btn" onclick="tvSub(\'picks\')"><span class="tv-menu-label">Picks &amp; Predictions</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvSub(\'props\')"><span class="tv-menu-label">Props &amp; Models</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvSub(\'results\')"><span class="tv-menu-label">Results &amp; Tracking</span><span class="tv-menu-arrow">&#8250;</span></button>
            {# Desktop .pl2-nav (Blog + Pricing) is hidden on mobile — keep both in every hamburger #}
            <a href="/blog" class="tv-menu-btn" style="text-decoration:none;"><span class="tv-menu-label">Blog</span></a>
            <a href="/plans" class="tv-menu-btn" style="text-decoration:none;"><span class="tv-menu-label">Pricing</span></a>
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
            <a class="share-icon" href="{{ 'https://www.instagram.com/' if request.path == '/daily-report' else 'https://instagram.com/predictionlab.io' }}" target="_blank" rel="noopener" aria-label="Instagram"><img src="/static/icons/social/instagram.svg" alt="Instagram"></a>
            <a class="share-icon" href="{{ 'https://www.tiktok.com/upload?lang=en' if request.path == '/daily-report' else 'https://predictionlab.io' }}" target="_blank" rel="noopener" aria-label="TikTok"><img src="/static/icons/social/tiktok.svg" alt="TikTok"></a>
            <a class="share-icon" href="https://www.linkedin.com/sharing/share-offsite/?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on LinkedIn"><img src="/static/icons/social/linkedin.svg" alt="LinkedIn"></a>
            <a class="share-icon" href="https://www.reddit.com/submit?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Reddit"><img src="/static/icons/social/reddit.svg" alt="Reddit"></a>
            <a class="share-icon" href="https://www.tumblr.com/widgets/share/tool?canonicalUrl={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Tumblr"><img src="/static/icons/social/tumblr.svg" alt="Tumblr"></a>
            <a class="share-icon" href="https://api.whatsapp.com/send?text={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on WhatsApp"><img src="/static/icons/social/whatsapp.svg" alt="WhatsApp"></a>
            <a class="share-icon" href="https://telegram.me/share/url?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Telegram"><img src="/static/icons/social/telegram.svg" alt="Telegram"></a>
        </div>
    </div>
    {% include "partials/site_directory_footer.html" %}
    
    <script>
var TV_MENUS={picks:{title:'Picks & Predictions',items:[{l:'NBA',h:'/nba-picks'},{l:'MLB',h:'/mlb-picks'},{l:'NHL',h:'/nhl-picks'},{l:'NFL',h:'/nfl-picks'}{% if soccer_enabled %},{l:'Soccer',h:'/soccer-picks'}{% endif %},{l:'NCAAB',h:'/ncaab-picks'},{l:'NCAAF',h:'/ncaaf-picks'},{l:'NCAAW',h:'/ncaaw-picks'},{l:'WNBA',h:'/wnba-picks'}]},props:{title:'Props & Models',items:[{l:'Player Props',h:'/player-props'},{l:'Model Performance',h:'/performance'},{l:'AI Picks Today',h:'/ai-sports-betting-picks-today'},{l:'Daily Results',h:'/daily-report'},{l:'Model vs Sportsbooks',h:'/our-model-vs-sportsbooks'},{l:'Tutorial',h:'/tutorial'}]},results:{title:'Results & Tracking',items:[{l:'All Sports Results',h:'/all-sports-results'},{l:'Daily Results',h:'/daily-report'},{l:'Historical Performance',h:'/performance'},{l:'Download CSV',h:'/picks/export.csv'}]},community:{title:'Community',items:[{l:'X / Twitter',h:'https://x.com/predictionlab_io',ext:true},{l:'Instagram',h:'https://instagram.com/predictionlab.io',ext:true},{l:'Reddit',h:'https://reddit.com/r/sportsbetting',ext:true},{l:'Telegram',h:'https://t.me/predictionlab',ext:true}]},company:{title:'Company',items:[{l:'Join Premium',h:'/plans',cls:'highlight'},{l:'Plans & Pricing',h:'/plans'},{l:'Blog',h:'/blog'},{l:'FAQ',h:'/faq'},{l:'Tutorial',h:'/tutorial'},{l:'Contact',h:'/contact'},{l:'Privacy',h:'/privacy'},{l:'Terms',h:'/terms'},{l:'Refund Policy',h:'/refund-policy'},{l:'Responsible Gaming',h:'/responsible-gaming'}]}};
function tvOpen(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.add('open');if(d)d.classList.add('open');document.body.style.overflow='hidden';if(h)h.setAttribute('aria-expanded','true');}
function tvClose(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.remove('open');if(d)d.classList.remove('open');document.body.style.overflow='';if(h)h.setAttribute('aria-expanded','false');setTimeout(function(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';},280);}
function tvSub(key){var menu=TV_MENUS[key];if(!menu)return;var html='';menu.items.forEach(function(item){var ext=item.ext?' target="_blank" rel="noopener"':'';var cls='tv-sub-link'+(item.cls?' '+item.cls:'');var extIcon=item.ext?' <span class="ext">&#8599;</span>':'';html+='<a href="'+item.h+'" class="'+cls+'"'+ext+'>'+item.l+extIcon+'</a>';});document.getElementById('tvSub').innerHTML=html;document.getElementById('tvDrawerTitle').textContent=menu.title;document.getElementById('tvBackBtn').style.display='';document.getElementById('tvMain').className='tv-panel hidden-left';document.getElementById('tvSub').className='tv-panel visible';}
function tvBack(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';}
function tvToggleMore(btn){var el=document.getElementById('tvMoreItems');var open=el.style.display==='block';el.style.display=open?'none':'block';var arrow=btn.querySelector('.tv-more-arrow');if(arrow)arrow.style.transform=open?'':'rotate(90deg)';}
function toggleAcctMenu(e){e.stopPropagation();document.getElementById('acctMenu').classList.toggle('open');}
document.addEventListener('click',function(){var m=document.getElementById('acctMenu');if(m)m.classList.remove('open');});
var _srchFilter='all';
var _srchDefaults=[{l:'Join Premium',h:'/plans',s:'all'},{l:'NBA Picks',h:'/nba-picks',s:'nba'},{l:'NFL Picks',h:'/nfl-picks',s:'nfl'},{l:'MLB Picks',h:'/mlb-picks',s:'mlb'},{l:'NHL Picks',h:'/nhl-picks',s:'nhl'},{l:'NCAAB Picks',h:'/ncaab-picks',s:'ncaab'},{l:'NCAAF Picks',h:'/ncaaf-picks',s:'ncaaf'},{l:'WNBA Picks',h:'/wnba-picks',s:'wnba'}{% if soccer_enabled %},{l:'Soccer Picks',h:'/soccer-picks',s:'all'}{% endif %},{l:'Tennis Picks',h:'/tennis-picks',s:'all'},{l:'UFC Picks',h:'/ufc-picks',s:'all'},{l:'Golf Picks',h:'/golf-picks',s:'all'},{l:'Player Props',h:'/player-props',s:'props'},{l:'Model Performance',h:'/performance',s:'props'},{l:'Daily Results',h:'/daily-report',s:'all'}];
function openSrch(){document.getElementById('srchOverlay').classList.add('open');document.body.style.overflow='hidden';setTimeout(function(){document.getElementById('srchInput').focus();},60);renderSrchItems('');}
function closeSrch(){document.getElementById('srchOverlay').classList.remove('open');document.body.style.overflow='';document.getElementById('srchInput').value='';}
function closeSrchOutside(e){if(e.target===document.getElementById('srchOverlay'))closeSrch();}
function renderSrchItems(q){var items=_srchDefaults.filter(function(i){return(_srchFilter==='all'||i.s===_srchFilter)&&(!q||i.l.toLowerCase().includes(q.toLowerCase()));});var el=document.getElementById('srchItems');if(!items.length){el.innerHTML='<div class="srch-empty">No results found</div>';return;}el.innerHTML=items.map(function(i){return'<a class="srch-item" href="'+i.h+'"><span class="srch-item-label">'+i.l+'</span><span class="srch-item-sport">'+i.s.toUpperCase()+'</span></a>';}).join('');}
document.addEventListener('DOMContentLoaded',function(){var inp=document.getElementById('srchInput');if(inp){inp.addEventListener('input',function(){renderSrchItems(this.value);});}document.querySelectorAll('.srch-filter').forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll('.srch-filter').forEach(function(b){b.classList.remove('active');});this.classList.add('active');_srchFilter=this.dataset.s;renderSrchItems(document.getElementById('srchInput').value);});});});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){tvClose();closeSrch();}});
    </script>
    {% if not is_premium %}
    <div class="join-premium-bar" id="joinPremiumBar" role="complementary" aria-label="Join premium" style="display:block;">
        <div class="join-premium-inner">
            <span class="join-premium-copy">Join premium for spreads, totals, projected scores, and full model edge.</span>
            <div class="join-premium-actions">
                <a href="/checkout/weekly" class="join-premium-btn join-premium-btn-weekly">Try a Week</a>
                <a href="/plans" class="join-premium-btn">Join Premium</a>
                <button type="button" class="join-premium-close" onclick="document.getElementById('joinPremiumBar').style.display='none';" aria-label="Close">×</button>
            </div>
        </div>
    </div>
    {% endif %}
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
        .contact-alert{border-radius:10px;padding:12px 14px;margin:0 0 18px;font-size:0.98em;line-height:1.5;}
        .contact-alert.ok{background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46;}
        .contact-alert.err{background:#fef2f2;border:1px solid #fca5a5;color:#991b1b;}
        .contact-form label{display:block;font-weight:700;color:#0f172a;margin:14px 0 6px;font-size:0.95em;}
        .contact-form input,.contact-form select,.contact-form textarea{
            width:100%;padding:11px 12px;border:1px solid #cbd5e1;border-radius:10px;
            font-size:1em;font-family:inherit;color:#0f172a;background:#fff;
        }
        .contact-form textarea{min-height:140px;resize:vertical;}
        .contact-form input:focus,.contact-form select:focus,.contact-form textarea:focus{
            outline:2px solid #00529B;outline-offset:1px;border-color:#00529B;
        }
        .contact-hp{position:absolute;left:-9999px;opacity:0;height:0;width:0;overflow:hidden;}
        .contact-submit{
            margin-top:18px;background:#00529B;color:#fff;border:none;border-radius:10px;
            padding:12px 22px;font-size:1em;font-weight:800;cursor:pointer;
        }
        .contact-submit:hover{background:#003d73;}
        .contact-note{font-size:0.92em;color:#64748b;margin-top:12px;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="contact-wrap">
        <div class="contact-card">
            <h1>Questions, Suggestions, or Technical Issues?</h1>
            <p>We want to make your experience using predictionlab.io the best it can be. If you need help, find a bug, or have a suggestion, we want to hear about it! We are always looking for ways to ensure our customers have the best edge possible.</p>
            {% if contact_sent %}
            <div class="contact-alert ok">Thanks — your message was sent. Our team will reply from <strong>{{ support_email }}</strong> as soon as we can.</div>
            {% elif contact_error %}
            <div class="contact-alert err">{{ contact_error }}</div>
            {% endif %}
            <form class="contact-form" method="post" action="/contact">
                <label for="contact-name">Your name</label>
                <input id="contact-name" name="name" type="text" required maxlength="120" autocomplete="name" value="{{ form_name or '' }}">
                <label for="contact-email">Your email</label>
                <input id="contact-email" name="email" type="email" required maxlength="254" autocomplete="email" value="{{ form_email or '' }}">
                <label for="contact-topic">Topic</label>
                <select id="contact-topic" name="topic">
                    <option value="support"{% if form_topic == 'support' %} selected{% endif %}>Help / technical issue</option>
                    <option value="suggestion"{% if form_topic == 'suggestion' %} selected{% endif %}>Suggestion</option>
                    <option value="billing"{% if form_topic == 'billing' %} selected{% endif %}>Billing / Premium</option>
                    <option value="other"{% if form_topic == 'other' %} selected{% endif %}>Other</option>
                </select>
                <label for="contact-message">Message</label>
                <textarea id="contact-message" name="message" required minlength="10" maxlength="5000">{{ form_message or '' }}</textarea>
                <div class="contact-hp" aria-hidden="true">
                    <label for="contact-website">Website</label>
                    <input id="contact-website" name="website" type="text" tabindex="-1" autocomplete="off">
                </div>
                <button class="contact-submit" type="submit">Send message</button>
                <p class="contact-note">Messages go to our support inbox at {{ support_email }}.</p>
            </form>
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

ALL_SPORTS_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    body{background:#ffffff !important;color:#0f172a;}
    .asr-wrap{max-width:1100px;margin:0 auto;padding:10px 0 60px;}
    .asr-header{text-align:center;margin-bottom:28px;}
    .asr-header h1{font-size:1.75em;margin-bottom:8px;}
    .asr-header p{color:#475569;font-size:0.95em;max-width:640px;margin:0 auto;line-height:1.6;}
    .asr-section{margin-bottom:32px;}
    .asr-section h2{font-size:1.15em;margin:0 0 12px;color:#0f172a;}
    .asr-table-wrap{overflow-x:auto;border:1px solid rgba(15,23,42,0.12);border-radius:12px;background:#fff;}
    table.asr-table{width:100%;border-collapse:collapse;font-size:0.88em;}
    table.asr-table th,table.asr-table td{padding:10px 12px;text-align:center;border-bottom:1px solid rgba(15,23,42,0.08);}
    table.asr-table th{background:#f8fafc;font-weight:700;color:#334155;font-size:0.78em;text-transform:uppercase;letter-spacing:0.04em;}
    table.asr-table td:first-child,table.asr-table th:first-child{text-align:left;min-width:120px;}
    table.asr-table tr:last-child td{border-bottom:none;}
    table.asr-table a.sport-link{color:#0f172a;font-weight:700;text-decoration:none;}
    table.asr-table a.sport-link:hover{color:#00529B;text-decoration:underline;}
    .asr-pct{font-weight:800;font-size:1.05em;}
    .asr-rec{font-size:0.78em;color:#64748b;margin-top:2px;}
    .asr-info{cursor:help;opacity:0.75;font-size:0.85em;}
    .asr-empty{text-align:center;padding:48px 20px;color:#64748b;border:1px dashed rgba(15,23,42,0.2);border-radius:12px;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="asr-wrap">
        <div class="asr-header">
            <h1>All Sports Prediction Results</h1>
            <p>Track season-to-date model accuracy for moneyline, spread, and over/under picks across all sports. Updated regularly as games finalize.</p>
        </div>

        {% if not dashboard_rows %}
        <div class="asr-empty">Season results are not available yet. Check back after the next update.</div>
        {% else %}

        <div class="asr-section">
            <h2>Moneyline</h2>
            <div class="asr-table-wrap">
                <table class="asr-table">
                    <thead>
                        <tr>
                            <th>Sport</th>
                            {% for _k, label in ml_models %}<th>{{ label }}</th>{% endfor %}
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in dashboard_rows %}
                        <tr>
                            <td><a class="sport-link" href="{{ row.results_url }}">{{ row.icon }} {{ row.name }}</a></td>
                            {% for key, label in ml_models %}
                            {% set c = row.ml[key] %}
                            <td>
                                {% if c.n %}<div class="asr-pct">{{ c.pct }}%</div><div class="asr-rec">{{ c.record }}<span class="asr-info" title="Number of Games"> ⓘ</span></div>{% else %}—{% endif %}
                            </td>
                            {% endfor %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="asr-section">
            <h2>Spread vs book</h2>
            <div class="asr-table-wrap">
                <table class="asr-table">
                    <thead><tr><th>Sport</th><th>XSharp</th><th>PL</th></tr></thead>
                    <tbody>
                        {% for row in dashboard_rows %}
                        <tr>
                            <td><a class="sport-link" href="{{ row.results_url }}">{{ row.icon }} {{ row.name }}</a></td>
                            {% for col in ('spread_xsharp', 'spread_pl') %}
                            {% set c = row[col] %}
                            <td>{% if c.n %}<div class="asr-pct">{{ c.pct }}%</div><div class="asr-rec">{{ c.record }}<span class="asr-info" title="Number of Games"> ⓘ</span></div>{% else %}—{% endif %}</td>
                            {% endfor %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="asr-section">
            <h2>Over / Under vs book</h2>
            <div class="asr-table-wrap">
                <table class="asr-table">
                    <thead><tr><th>Sport</th><th>XSharp</th><th>PL</th></tr></thead>
                    <tbody>
                        {% for row in dashboard_rows %}
                        <tr>
                            <td><a class="sport-link" href="{{ row.results_url }}">{{ row.icon }} {{ row.name }}</a></td>
                            {% for col in ('ou_xsharp', 'ou_pl') %}
                            {% set c = row[col] %}
                            <td>{% if c.n %}<div class="asr-pct">{{ c.pct }}%</div><div class="asr-rec">{{ c.record }}<span class="asr-info" title="Number of Games"> ⓘ</span></div>{% else %}—{% endif %}</td>
                            {% endfor %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        {% endif %}
    </div>
""")

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
                <a class="rpt-btn rpt-btn-copy" href="{{ st.share_image_view_url }}" target="_blank" rel="nofollow noopener">Fullscreen {{ st.info.name }}</a>
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
    .league-pill-count { margin-left:6px; font-size:0.85em; opacity:0.75; font-weight:700; }
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
.games-grid, .results-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:12px; align-items:start; }
    @media(max-width:480px){ .games-grid, .results-grid { grid-template-columns:1fr; } .game-card { max-width:100%; } }
    .game-card {
        background:#ffffff;
        border:1px solid #E2E8F0;
        border-radius:12px;
        overflow:hidden;
        box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06);
        transition:border-color 0.2s, box-shadow 0.2s;
    }
    .game-card:hover { border-color:#cbd5e1; box-shadow:0 10px 28px rgba(15,23,42,0.12), 0 2px 6px rgba(15,23,42,0.08); }
    .card-hero { padding:12px 12px 10px; border-bottom:1px solid #E2E8F0; }
    .card-hero-meta-line { text-align:center; font-size:0.68em; color:#64748b; text-transform:uppercase; font-weight:600; margin-bottom:10px; }
    .teams-split { display:grid; grid-template-columns:1fr auto 1fr; align-items:start; gap:8px; }
    .team-col { display:flex; flex-direction:column; align-items:center; text-align:center; min-width:0; }
    .teams-split .team-logo { width:48px; height:48px; object-fit:contain; margin-bottom:4px; }
    .teams-split .team-name { font-size:0.78em; font-weight:800; margin-bottom:8px; color:#0f172a; }
    .teams-at { font-size:0.72em; font-weight:800; color:#94a3b8; padding-top:18px; }
    .ml-stack { display:flex; flex-direction:column; gap:4px; width:100%; }
    .ml-line { display:flex; flex-direction:column; align-items:center; gap:1px; }
    .ml-src { font-size:0.58em; font-weight:700; text-transform:uppercase; color:#94a3b8; }
    .ml-src.books { color:#0f766e; }
    .ml-src.pl { color:#92400e; }
    .ml-num { font-size:0.92em; font-weight:800; }
    .ml-num.fav { color:#00C076; }
    .ml-num.dog { color:#92400e; }
    .final-score { font-size:1.2em; font-weight:800; color:#0f172a; }
    .final-score.score-winner { color:#00C076; }
    .odds-pricing-section { border-top:1px solid rgba(15,23,42,0.08); padding:10px 12px 12px; background:#f8fafc; }
    .odds-pricing-title { font-size:0.68em; color:#0F172A; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; margin-bottom:8px; }
    .odds-pricing-table { width:100%; border-collapse:collapse; font-size:0.8em; }
    .odds-pricing-table th { text-align:left; font-weight:700; padding:7px 8px; border-bottom:1px solid #e2e8f0; color:#64748b; font-size:0.72em; text-transform:uppercase; }
    .odds-pricing-table td { padding:7px 8px; border-bottom:1px solid #f1f5f9; font-weight:600; color:#0f172a; }
    .odds-pricing-table th.col-books, .val-books { color:#0f766e; }
    .odds-pricing-table th.col-pl, .val-pl { color:#92400e; }
    .odds-pricing-table th.col-xs, .val-xs { color:#1d4ed8; }
    .market-k { color:#64748b; font-size:0.72em; text-transform:uppercase; }
    .proj-score-box { margin-top:8px; padding:8px 10px; background:#fff; border:1px solid #e2e8f0; border-radius:8px; }
    .proj-score-title { font-size:0.68em; text-transform:uppercase; font-weight:700; color:#0F172A; margin-bottom:6px; }
    .proj-row { display:flex; justify-content:space-between; gap:8px; padding:5px 0; border-top:1px solid #f1f5f9; font-size:0.78em; font-weight:600; }
    .proj-row:first-of-type { border-top:none; padding-top:0; }
    .proj-model { font-size:0.68em; text-transform:uppercase; font-weight:700; }
    .proj-model.pl { color:#92400e; }
    .proj-model.xs { color:#1d4ed8; }
    .proj-val { color:#0f172a; text-align:right; }
    .pick-conf-bar { border-top:1px solid rgba(15,23,42,0.08); padding:10px 12px 12px; background:rgba(15,23,42,0.03); }
    .odds-extras-footer { border-top:1px solid rgba(15,23,42,0.07); padding:8px 12px 10px; display:flex; gap:14px; flex-wrap:wrap; background:rgba(15,23,42,0.03); }
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
    .pick-conf-bar { border-top:1px solid rgba(15,23,42,0.08); padding:10px 12px 12px; background:rgba(15,23,42,0.03); }
    .pick-conf-title { font-size:0.68em; color:#0F172A; text-transform:uppercase; font-weight:700; letter-spacing:0.5px; margin-bottom:8px; }
    .pick-conf-grid { display:grid; grid-template-columns:repeat(6,1fr); gap:6px; align-items:stretch; }
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
    .model-grid { display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin-bottom:16px; }
    @media(max-width:900px){ .model-grid { grid-template-columns:repeat(3,1fr); } }
    .model-card { background:#ffffff; border:1px solid #E2E8F0; border-radius:12px; padding:12px; text-align:center; box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06); }
    .model-card.highlight { border:2px solid #fbbf24; }
    .model-label { font-size:0.78em; opacity:0.8; margin-bottom:4px; }
    .model-acc { font-size:1.4em; font-weight:700; color:#00C076; }
    .model-rec { font-size:0.82em; opacity:0.85; }
    .daily-tally { background:#ffffff; border:1px solid #E2E8F0; border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 4px 18px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.06); }
    .daily-tally-head { display:flex; align-items:center; justify-content:center; gap:10px; flex-wrap:wrap; margin:0 0 12px 0; }
    .daily-tally-head h2 { text-align:center; margin:0; font-size:1.15em; color:#0F172A; font-weight:700; }
    .tally-share-wrap { position:relative; display:inline-flex; align-items:center; }
    .tally-share-btn { appearance:none; cursor:pointer; padding:5px 12px; border-radius:8px; border:1px solid rgba(15,23,42,0.2); background:#fff; color:#0f172a; font-size:0.72em; font-weight:800; letter-spacing:0.2px; display:inline-flex; align-items:center; gap:5px; line-height:1.2; }
    .tally-share-btn:hover { border-color:#00529B; background:rgba(0,82,155,0.08); color:#00529B; }
    .tally-share-btn.copied { background:#00C076; border-color:#00C076; color:#fff; }
    .tally-share-menu { position:absolute; top:calc(100% + 6px); right:0; z-index:30; min-width:148px; padding:6px; background:#fff; border:1px solid rgba(15,23,42,0.14); border-radius:10px; box-shadow:0 8px 24px rgba(15,23,42,0.12); display:flex; flex-direction:column; gap:4px; }
    .tally-share-menu[hidden] { display:none !important; }
    .tally-share-menu button { appearance:none; border:none; background:transparent; text-align:left; padding:8px 10px; border-radius:8px; font-size:0.78em; font-weight:700; color:#0f172a; cursor:pointer; }
    .tally-share-menu button:hover { background:#f1f5f9; color:#00529B; }
    .daily-tally-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
    .daily-tally-card { background:#ffffff; border:1px solid #E2E8F0; border-radius:10px; padding:10px; text-align:center; box-shadow:0 2px 12px rgba(15,23,42,0.06); }
    .daily-tally-card.highlight { border:2px solid #fbbf24; }
    .daily-model { font-size:0.78em; opacity:0.85; margin-bottom:4px; }
    .daily-acc { font-size:1.35em; font-weight:700; }
    .daily-rec { font-size:0.8em; opacity:0.8; }
    @media(max-width:640px){ .roi-grid{grid-template-columns:1fr !important;} .daily-tally-head{gap:8px;} .tally-share-btn{padding:6px 12px; font-size:0.74em;} }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} Results, Performance and Model Accuracy</h1>
    <div class="section-tabs">
        <a href="/{{ sport_seo_slug }}" class="tab">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}" class="tab active">🎯 Results</a>
    </div>
        {% set model_cards = [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
        {# ── SECTION: Issue 5 — Efficiency as a 6th graded model in daily/weekly tally ── #}
        {% set tally_model_cards = model_cards + [('⚡ Efficiency','efficiency')] %}
        {% set label_glicko2 = 'Grinder2' %}
        {% set label_trueskill = 'Takedown' %}
        {% set label_elo = 'Edge' %}
        {% set label_xgb = 'XSharp' %}
        {% set label_ensemble = 'Sharp Consensus' %}
        {% set label_efficiency = 'Efficiency' %}
        {% if results_snapshot_notice is defined and results_snapshot_notice %}
        <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:8px;padding:12px 16px;margin:0 0 14px;font-size:0.85em;color:#9a3412;text-align:center;">
            {{ results_snapshot_notice }}
        </div>
        {% endif %}
        {% if overall_stats %}
        {% if soccer_leagues %}
        <div class="league-slider">
            <div class="league-badges" id="leagueBubbles">
                {% for lg in soccer_leagues %}
                <a class="league-pill {% if lg.active %}active{% endif %}" href="{{ lg.url }}">{{ lg.name }}{% if lg.count is defined and lg.count %}<span class="league-pill-count">{{ lg.count }}</span>{% endif %}</a>
                {% endfor %}
            </div>
        </div>
        {% endif %}
        {% set ens = overall_stats.ensemble %}

        <!-- ── Daily Tally ── -->
        {% if daily_tally %}
        <div class="daily-tally">
            {% set _ens = daily_tally.get('ensemble') if daily_tally else none %}
            <div class="daily-tally-head">
                <h2>Last Night's {{ sport_info.name }} Results — {{ daily_tally_date }} ({{ daily_tally_games }} games)</h2>
                <div class="tally-share-wrap">
                    <button type="button" class="tally-share-btn" id="dailyTallyShareBtn" aria-haspopup="true" aria-expanded="false" aria-label="Share Last Night's {{ sport_info.name }} Results"
                        data-sport="{{ sport_info.name }}"
                        data-icon="{{ sport_info.icon }}"
                        data-date="{{ daily_tally_date }}"
                        data-games="{{ daily_tally_games }}"
                        data-ens-acc="{% if _ens and _ens.total %}{{ _ens.accuracy }}{% endif %}"
                        data-ens-rec="{% if _ens and _ens.total %}{{ _ens.correct }}-{{ _ens.total - _ens.correct }}{% endif %}"
                        data-spread-acc="{% if daily_tally.spread is defined and daily_tally.spread.total %}{{ daily_tally.spread.accuracy }}{% endif %}"
                        data-ou-acc="{% if daily_tally.total_ou is defined and daily_tally.total_ou.total %}{{ daily_tally.total_ou.accuracy }}{% endif %}"
                        data-share-url="https://predictionlab.io/{{ sport_results_slug }}">Share</button>
                    <div class="tally-share-menu" id="dailyTallyShareMenu" hidden role="menu">
                        <button type="button" data-share-action="copy" role="menuitem">Copy text</button>
                        <button type="button" data-share-action="twitter" role="menuitem">Post on X</button>
                    </div>
                </div>
            </div>
            <div style="font-size:0.78em;text-align:center;opacity:0.7;margin-bottom:6px;">MONEYLINE</div>
            <div class="daily-tally-grid">
                {% for m_label, m_key in tally_model_cards %}
                {% set m = daily_tally.get(m_key) %}
                <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                    <div class="daily-model">{{ m_label }}</div>
                    {% if m and m.total > 0 %}
                    <div class="daily-acc">{{ m.accuracy }}%</div>
                    <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">—</div>
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
            No completed games for {{ daily_tally_date }}.
        </div>
        {% endif %}

        <!-- ── Last 7 Days Tally ── -->
        {% if weekly_tally %}
        <div class="daily-tally">
            <h2>Last 7 Days {{ sport_info.name }} Results — {{ weekly_tally_date_range }} ({{ weekly_tally_games }} games)</h2>
            <div style="font-size:0.78em;text-align:center;opacity:0.7;margin-bottom:6px;">MONEYLINE</div>
            <div class="daily-tally-grid">
                {% for m_label, m_key in tally_model_cards %}
                {% set m = weekly_tally.get(m_key) %}
                <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                    <div class="daily-model">{{ m_label }}</div>
                    {% if m and m.total > 0 %}
                    <div class="daily-acc">{{ m.accuracy }}%</div>
                    <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                    {% else %}
                    <div class="daily-acc" style="color:#94a3b8;">—</div>
                    <div class="daily-rec">—</div>
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
            —
        </div>
        {% endif %}

        <!-- ── ROI Cards ── -->
        {% if roi_cards %}
        <div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
            <h2 style="text-align:center;margin:0 0 4px 0;font-size:1.3em;color:#0f172a;">💰 Model Performance (Flat Unit Tracking)</h2>
            <p style="text-align:center;margin:0 0 14px;font-size:0.78em;color:#64748b;">Percentages are <strong>unit ROI</strong> (profit per $1 risked), not moneyline win rate.</p>
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
            <h2 style="text-align:center;margin:0 0 6px 0;font-size:1.5em;color:#0f172a;">🏆 Season Performance{% if selected_league %} — {{ selected_league }}{% endif %}</h2>
            {% set sp = season_perf if season_perf is defined and season_perf else none %}
            {% if not sp and overall_stats and overall_stats.ensemble %}
            {% set _ens = overall_stats.ensemble %}
            {% set sp = {'ml_total': _ens.total, 'ml_correct': _ens.correct, 'ml_accuracy': _ens.accuracy, 'ml_model_label': 'Sharp Consensus', 'spread_graded': 0, 'spread_covered': 0, 'spread_pct': none, 'spread_model_label': 'XSharp', 'ou_graded': 0, 'ou_correct': 0, 'ou_pct': none, 'ou_model_label': 'XSharp'} %}
            {% endif %}
            <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px;">
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎯 Moneyline{% if sp and sp.ml_model_label %} ({{ sp.ml_model_label }}){% endif %}</div>
                    {% if sp and sp.ml_total > 0 %}
                    <div style="font-size:2em;font-weight:bold;color:{% if sp.ml_accuracy>=55 %}#00C076{% elif sp.ml_accuracy>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ sp.ml_accuracy }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ sp.ml_correct }}-{{ sp.ml_total - sp.ml_correct }} <span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>
                    {% else %}
                    <div style="font-size:1.5em;color:#94a3b8;">—</div>
                    <div style="font-size:0.85em;color:#64748b;">—</div>
                    {% endif %}
                </div>
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">📈 Spread{% if sp and sp.spread_model_label %} ({{ sp.spread_model_label }}){% endif %}</div>
                    {% if sp and sp.spread_graded > 0 and sp.spread_pct is not none %}
                    <div style="font-size:2em;font-weight:bold;color:{% if sp.spread_pct>=52 %}#00C076{% elif sp.spread_pct>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ sp.spread_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ sp.spread_covered }}-{{ sp.spread_graded - sp.spread_covered }} <span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>
                    {% else %}
                    <div style="font-size:1.5em;color:#94a3b8;">—</div>
                    <div style="font-size:0.85em;color:#64748b;">not graded yet</div>
                    {% endif %}
                </div>
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎲 O/U{% if sp and sp.ou_model_label %} ({{ sp.ou_model_label }}){% endif %}</div>
                    {% if sp and sp.ou_graded > 0 and sp.ou_pct is not none %}
                    <div style="font-size:2em;font-weight:bold;color:{% if sp.ou_pct>=52 %}#00C076{% elif sp.ou_pct>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ sp.ou_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ sp.ou_correct }}-{{ sp.ou_graded - sp.ou_correct }} <span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>
                    {% else %}
                    <div style="font-size:1.5em;color:#94a3b8;">—</div>
                    <div style="font-size:0.85em;color:#64748b;">not graded yet</div>
                    {% endif %}
                </div>
            </div>
            <div style="border-top:1px solid rgba(15,23,42,0.12);padding-top:12px;"></div>
        </div>

        {% if playoff_perf is defined and playoff_perf %}
        <div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
            <h2 style="text-align:center;margin:0 0 6px 0;font-size:1.35em;color:#0f172a;">🏒 Playoff Performance (live)</h2>
            {% if playoff_perf.scope_label %}
            <p style="text-align:center;margin:0 0 10px;font-size:0.82em;color:#64748b;">{{ playoff_perf.scope_label }}{% if playoff_perf.games_in_scope %} — {{ playoff_perf.games_in_scope }} games graded{% endif %}</p>
            {% endif %}
            {% set sp = playoff_perf %}
            <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:8px;">
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎯 Moneyline</div>
                    {% if sp.ml_total > 0 %}
                    <div style="font-size:1.8em;font-weight:bold;color:{% if sp.ml_accuracy>=55 %}#00C076{% elif sp.ml_accuracy>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ sp.ml_accuracy }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ sp.ml_correct }}-{{ sp.ml_total - sp.ml_correct }}</div>
                    {% else %}<div style="font-size:1.2em;color:#94a3b8;">—</div>{% endif %}
                </div>
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">📈 Spread</div>
                    {% if sp.spread_graded > 0 and sp.spread_pct is not none %}
                    <div style="font-size:1.8em;font-weight:bold;color:{% if sp.spread_pct>=52 %}#00C076{% elif sp.spread_pct>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ sp.spread_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ sp.spread_covered }}-{{ sp.spread_graded - sp.spread_covered }}</div>
                    {% else %}<div style="font-size:1.2em;color:#94a3b8;">—</div>{% endif %}
                </div>
                <div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">
                    <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎲 O/U</div>
                    {% if sp.ou_graded > 0 and sp.ou_pct is not none %}
                    <div style="font-size:1.8em;font-weight:bold;color:{% if sp.ou_pct>=52 %}#00C076{% elif sp.ou_pct>=50 %}#fbbf24{% else %}#D93025{% endif %};">{{ sp.ou_pct }}%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">{{ sp.ou_correct }}-{{ sp.ou_graded - sp.ou_correct }}</div>
                    {% else %}<div style="font-size:1.2em;color:#94a3b8;">—</div>{% endif %}
                </div>
            </div>
        </div>
        {% endif %}


        <!-- ── Model Records ── -->
        <h3 style="text-align:center;font-size:1.15em;margin:0 0 12px;color:#0f172a;">Moneyline Accuracy by Model</h3>
        <div class="model-grid">
            {% for m_label, m_key in tally_model_cards %}
            {% set m = overall_stats[m_key] %}
            <div class="model-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                <div class="model-label">{{ m_label }}</div>
                {% if m.total > 0 %}
                <div class="model-acc">{{ m.accuracy }}%</div>
                <div class="model-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                {% else %}
                <div class="model-acc" style="color:#94a3b8;">—</div>
                <div class="model-rec">—</div>
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

            <div class="games-grid">
                {% for game in date_data.games %}
                {% set home_wins = game.home_score > game.away_score %}
                {% set away_wins = game.away_score > game.home_score %}
                {% set actual_spread = (game.home_score - game.away_score) %}
                {% set actual_total = (game.home_score + game.away_score) %}
                {% set away_team = game.away %}
                {% set home_team = game.home %}
                {% set away_score = game.away_score %}
                {% set home_score = game.home_score %}
                {% set _force_rl = (sport == 'MLB') %}
                {% set _spread_label = 'Run Line' if sport == 'MLB' else ('Puck Line' if sport == 'NHL' else 'Spread') %}
                <div class="game-card" data-league="{{ game.league if game.league else 'Other' }}">
                    {% set card = game %}
                    {% set is_results = true %}
                    {% set is_final = true %}
                    {% set is_premium = true %}
                    {% set spread_label = _spread_label %}
                    {% set force_rl = _force_rl %}
                    {% set away_score = game.away_score %}
                    {% set home_score = game.home_score %}
                    {% set show_pick_arrow = false %}
                    {% set conf_models = [
                        {'name': label_glicko2, 'prob': game.glicko2_prob, 'correct': game.glicko2_correct, 'key': 'glicko2'},
                        {'name': label_trueskill, 'prob': game.trueskill_prob, 'correct': game.trueskill_correct, 'key': 'trueskill'},
                        {'name': label_elo, 'prob': game.elo_prob, 'correct': game.elo_correct, 'key': 'elo'},
                        {'name': label_xgb, 'prob': game.xgb_prob, 'correct': game.xgb_correct, 'key': 'xgb'},
                        {'name': label_ensemble, 'prob': game.ens_prob, 'correct': game.ens_correct, 'key': 'consensus'},
                        {'name': label_efficiency, 'prob': game.efficiency_prob, 'correct': game.efficiency_correct, 'key': 'efficiency'}
                    ] %}
                    {% include 'includes/game_card_body.html' %}
                    {% if game.model_data_note %}<div style="font-size:0.7em;color:#94a3b8;padding:4px 12px 8px;text-align:center;">{{ game.model_data_note }}</div>{% endif %}
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
    /* ── Share Last Night's Results ── */
    function buildTallySharePayload(btn) {
        const sport = btn.dataset.sport || '';
        const icon = btn.dataset.icon || '';
        const date = btn.dataset.date || '';
        const games = btn.dataset.games || '0';
        const ensAcc = btn.dataset.ensAcc || '';
        const ensRec = btn.dataset.ensRec || '';
        const spread = btn.dataset.spreadAcc || '';
        const ou = btn.dataset.ouAcc || '';
        const url = btn.dataset.shareUrl || window.location.href;
        const lines = [(icon + " Last Night's " + sport + " Results (" + date + ') — ' + games + ' games').replace(/^\\s+/, '')];
        if (ensAcc) lines.push('Sharp Consensus: ' + ensAcc + '% (' + ensRec + ')');
        const extras = [];
        if (spread) extras.push('Spread ' + spread + '%');
        if (ou) extras.push('O/U ' + ou + '%');
        if (extras.length) lines.push(extras.join(' · '));
        const body = lines.join('\\n');
        const fullText = body + '\\n' + url + '\\nvia PredictionLab';
        return { title: "Last Night's " + sport + " Results", body: body, fullText: fullText, url: url };
    }
    function setTallyShareMenuOpen(open) {
        const btn = document.getElementById('dailyTallyShareBtn');
        const menu = document.getElementById('dailyTallyShareMenu');
        if (!btn || !menu) return;
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    async function copyTallyShareText(btn) {
        const payload = buildTallySharePayload(btn);
        try {
            await navigator.clipboard.writeText(payload.fullText);
        } catch (_) {
            const ta = document.createElement('textarea');
            ta.value = payload.fullText;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        }
        const prev = btn.textContent;
        btn.textContent = 'Copied';
        btn.classList.add('copied');
        setTimeout(function(){ btn.textContent = prev; btn.classList.remove('copied'); }, 1600);
    }
    function tweetTallyShare(btn) {
        const payload = buildTallySharePayload(btn);
        window.open('https://twitter.com/intent/tweet?text=' + encodeURIComponent(payload.fullText), '_blank', 'noopener');
    }
    async function shareDailyTally(btn) {
        const payload = buildTallySharePayload(btn);
        if (navigator.share) {
            try {
                await navigator.share({ title: payload.title, text: payload.body + '\\nvia PredictionLab', url: payload.url });
                setTallyShareMenuOpen(false);
                return;
            } catch (e) {
                if (e && e.name === 'AbortError') return;
            }
        }
        setTallyShareMenuOpen(true);
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
        const shareBtn = document.getElementById('dailyTallyShareBtn');
        const shareMenu = document.getElementById('dailyTallyShareMenu');
        if (shareBtn) {
            shareBtn.addEventListener('click', function(e){
                e.preventDefault();
                e.stopPropagation();
                shareDailyTally(shareBtn);
            });
        }
        if (shareMenu) {
            shareMenu.addEventListener('click', function(e){
                const actionBtn = e.target.closest('[data-share-action]');
                if (!actionBtn || !shareBtn) return;
                e.preventDefault();
                e.stopPropagation();
                const action = actionBtn.getAttribute('data-share-action');
                if (action === 'copy') copyTallyShareText(shareBtn);
                if (action === 'twitter') tweetTallyShare(shareBtn);
                setTallyShareMenuOpen(false);
            });
        }
        document.addEventListener('click', function(){ setTallyShareMenuOpen(false); });
        document.addEventListener('keydown', function(e){ if (e.key === 'Escape') setTallyShareMenuOpen(false); });
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
    .daily-tally-head { display:flex; align-items:center; justify-content:center; gap:10px; flex-wrap:wrap; margin:0 0 12px 0; }
    .daily-tally-head h2 { text-align:center; margin:0; font-size:1.2em; color:#0F172A; font-weight:700; }
    .tally-share-wrap { position:relative; display:inline-flex; align-items:center; }
    .tally-share-btn { appearance:none; cursor:pointer; padding:5px 12px; border-radius:8px; border:1px solid rgba(15,23,42,0.2); background:#fff; color:#0f172a; font-size:0.72em; font-weight:800; letter-spacing:0.2px; display:inline-flex; align-items:center; gap:5px; line-height:1.2; }
    .tally-share-btn:hover { border-color:#00529B; background:rgba(0,82,155,0.08); color:#00529B; }
    .tally-share-btn.copied { background:#00C076; border-color:#00C076; color:#fff; }
    .tally-share-menu { position:absolute; top:calc(100% + 6px); right:0; z-index:30; min-width:148px; padding:6px; background:#fff; border:1px solid rgba(15,23,42,0.14); border-radius:10px; box-shadow:0 8px 24px rgba(15,23,42,0.12); display:flex; flex-direction:column; gap:4px; }
    .tally-share-menu[hidden] { display:none !important; }
    .tally-share-menu button { appearance:none; border:none; background:transparent; text-align:left; padding:8px 10px; border-radius:8px; font-size:0.78em; font-weight:700; color:#0f172a; cursor:pointer; }
    .tally-share-menu button:hover { background:#f1f5f9; color:#00529B; }
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
        {% set _ens = daily_tally.get('ensemble') if daily_tally else none %}
        <div class="daily-tally-head">
            <h2>Last Night's {{ sport_info.name }} Results — {{ daily_tally_date }} ({{ daily_tally_games }} games)</h2>
            <div class="tally-share-wrap">
                <button type="button" class="tally-share-btn" id="dailyTallyShareBtn" aria-haspopup="true" aria-expanded="false" aria-label="Share Last Night's {{ sport_info.name }} Results"
                    data-sport="{{ sport_info.name }}"
                    data-icon="{{ sport_info.icon }}"
                    data-date="{{ daily_tally_date }}"
                    data-games="{{ daily_tally_games }}"
                    data-ens-acc="{% if _ens and _ens.total %}{{ _ens.accuracy }}{% endif %}"
                    data-ens-rec="{% if _ens and _ens.total %}{{ _ens.correct }}-{{ _ens.total - _ens.correct }}{% endif %}"
                    data-spread-acc=""
                    data-ou-acc=""
                    data-share-url="https://predictionlab.io/{{ sport_results_slug }}">Share</button>
                <div class="tally-share-menu" id="dailyTallyShareMenu" hidden role="menu">
                    <button type="button" data-share-action="copy" role="menuitem">Copy text</button>
                    <button type="button" data-share-action="twitter" role="menuitem">Post on X</button>
                </div>
            </div>
        </div>
        <div class="daily-tally-grid">
            {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
            {% set m = daily_tally[m_key] %}
            <div class="daily-tally-card {% if m_key == 'ensemble' %}highlight{% endif %}">
                <div class="daily-model">{{ m_label }}</div>
                {% if m.total > 0 %}
                <div class="daily-acc">{{ m.accuracy }}%</div>
                <div class="daily-rec">{{ m.correct }}-{{ m.total - m.correct }}</div>
                {% else %}
                <div class="daily-acc" style="color:#94a3b8;">—</div>
                <div class="daily-rec">—</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% else %}
    <div class="daily-tally" style="text-align:center;">
        No completed games for {{ daily_tally_date }}.
    </div>
    {% endif %}
    {% if weekly_tally %}
    <div class="daily-tally">
        <h2>Last 7 Days {{ sport_info.name }} Results — {{ weekly_tally_date_range }} ({{ weekly_tally_games }} games)</h2>
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
                <div class="daily-rec">—</div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% else %}
    <div class="daily-tally" style="text-align:center;">
        —
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
<script>
    function buildTallySharePayload(btn) {
        const sport = btn.dataset.sport || '';
        const icon = btn.dataset.icon || '';
        const date = btn.dataset.date || '';
        const games = btn.dataset.games || '0';
        const ensAcc = btn.dataset.ensAcc || '';
        const ensRec = btn.dataset.ensRec || '';
        const spread = btn.dataset.spreadAcc || '';
        const ou = btn.dataset.ouAcc || '';
        const url = btn.dataset.shareUrl || window.location.href;
        const lines = [(icon + " Last Night's " + sport + " Results (" + date + ') — ' + games + ' games').replace(/^\\s+/, '')];
        if (ensAcc) lines.push('Sharp Consensus: ' + ensAcc + '% (' + ensRec + ')');
        const extras = [];
        if (spread) extras.push('Spread ' + spread + '%');
        if (ou) extras.push('O/U ' + ou + '%');
        if (extras.length) lines.push(extras.join(' · '));
        const body = lines.join('\\n');
        const fullText = body + '\\n' + url + '\\nvia PredictionLab';
        return { title: "Last Night's " + sport + " Results", body: body, fullText: fullText, url: url };
    }
    function setTallyShareMenuOpen(open) {
        const btn = document.getElementById('dailyTallyShareBtn');
        const menu = document.getElementById('dailyTallyShareMenu');
        if (!btn || !menu) return;
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    async function copyTallyShareText(btn) {
        const payload = buildTallySharePayload(btn);
        try {
            await navigator.clipboard.writeText(payload.fullText);
        } catch (_) {
            const ta = document.createElement('textarea');
            ta.value = payload.fullText;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        }
        const prev = btn.textContent;
        btn.textContent = 'Copied';
        btn.classList.add('copied');
        setTimeout(function(){ btn.textContent = prev; btn.classList.remove('copied'); }, 1600);
    }
    function tweetTallyShare(btn) {
        const payload = buildTallySharePayload(btn);
        window.open('https://twitter.com/intent/tweet?text=' + encodeURIComponent(payload.fullText), '_blank', 'noopener');
    }
    async function shareDailyTally(btn) {
        const payload = buildTallySharePayload(btn);
        if (navigator.share) {
            try {
                await navigator.share({ title: payload.title, text: payload.body + '\\nvia PredictionLab', url: payload.url });
                setTallyShareMenuOpen(false);
                return;
            } catch (e) {
                if (e && e.name === 'AbortError') return;
            }
        }
        setTallyShareMenuOpen(true);
    }
    document.addEventListener('DOMContentLoaded', function(){
        const shareBtn = document.getElementById('dailyTallyShareBtn');
        const shareMenu = document.getElementById('dailyTallyShareMenu');
        if (shareBtn) {
            shareBtn.addEventListener('click', function(e){
                e.preventDefault();
                e.stopPropagation();
                shareDailyTally(shareBtn);
            });
        }
        if (shareMenu) {
            shareMenu.addEventListener('click', function(e){
                const actionBtn = e.target.closest('[data-share-action]');
                if (!actionBtn || !shareBtn) return;
                e.preventDefault();
                e.stopPropagation();
                const action = actionBtn.getAttribute('data-share-action');
                if (action === 'copy') copyTallyShareText(shareBtn);
                if (action === 'twitter') tweetTallyShare(shareBtn);
                setTallyShareMenuOpen(false);
            });
        }
        document.addEventListener('click', function(){ setTallyShareMenuOpen(false); });
        document.addEventListener('keydown', function(e){ if (e.key === 'Escape') setTallyShareMenuOpen(false); });
    });
</script>
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
# ===== SECTION: Season calendar (regular-season + playoff start/end) =====
# Single source of truth for when each sport's regular season and playoffs run,
# so off-season messaging and season/phase detection stay accurate without
# manual date edits scattered across the app. Values are (month, day) windows
# that recur each year; `notes` records the authoritative 2025 dates provided
# for reference. Update a sport here when its league shifts its calendar.
SEASON_CALENDAR = {
    'NHL':    {'regular': ((10, 1), (4, 17)),  'playoffs': ((4, 17), (6, 30)),
               'notes': '2025-26 regular season opens Oct 7; Stanley Cup Playoffs mid-Apr through June.'},
    'NBA':    {'regular': ((10, 1), (4, 14)),  'playoffs': ((4, 15), (6, 30)),
               'notes': '2025-26 regular season opens Oct 27; Playoffs Apr 15 – Jun 22 (2025).'},
    'MLB':    {'regular': ((3, 20), (9, 30)),  'playoffs': ((10, 1), (11, 5)),
               'notes': '2025 season Mar 27 – Nov 2; postseason Oct, World Series ends ~Oct 25 – Nov 2.'},
    'NFL':    {'regular': ((9, 1), (1, 7)),    'playoffs': ((1, 8), (2, 20)),
               'notes': '2025 season opens Sep 4; Playoffs begin Jan 8; Super Bowl ~Feb 9.'},
    'NCAAF':  {'regular': ((8, 15), (12, 13)), 'playoffs': ((12, 14), (1, 20)),
               'notes': '2025 season Aug 23; conference championships Dec 13; CFP championship ~Jan 20.'},
    'NCAAB':  {'regular': ((11, 1), (3, 17)),  'playoffs': ((3, 18), (4, 15)),
               'notes': '2024-25 March Madness Mar 18 – Apr 7; regular season opens early November.'},
    'NCAAW':  {'regular': ((11, 1), (3, 17)),  'playoffs': ((3, 18), (4, 15)),
               'notes': "Women's NCAA tournament runs late Mar – early Apr; season opens early November."},
    'WNBA':   {'regular': ((5, 8), (9, 14)),   'playoffs': ((9, 15), (10, 15)),
               'notes': 'Regular season typically mid-May; playoffs Sep – Oct.'},
    'SOCCER': {'regular': ((8, 1), (6, 30)),   'playoffs': None,
               'notes': 'European league calendar (e.g. EPL / eng.1) runs Aug – May; no single playoff bracket.'},
}

# Month/day windows for "live" status on landing page.
# Derived from SEASON_CALENDAR (regular-season start -> playoff end) so the live
# window and the calendar can never drift apart.
_SEASON_WINDOWS = {
    _cal_sport: (_cal_info['regular'][0],
                 (_cal_info['playoffs'][1] if _cal_info.get('playoffs') else _cal_info['regular'][1]))
    for _cal_sport, _cal_info in SEASON_CALENDAR.items()
}

# Regular-season game counts per team (league totals = teams * games / 2 where applicable).
SPORT_REGULAR_SEASON_GAMES_PER_TEAM = {
    'NHL': 82,
    'NBA': 82,
    'MLB': 162,
    'NFL': 17,
    'WNBA': 44,
    'NCAAF': 12,
    'NCAAB': 30,
    'NCAAW': 30,
}
_SPORT_TEAM_COUNTS = {
    'NHL': 32, 'NBA': 30, 'MLB': 30, 'NFL': 32, 'WNBA': 12,
    'NCAAF': 136, 'NCAAB': 362, 'NCAAW': 362,
}
SPORT_REGULAR_SEASON_LEAGUE_GAMES = {
    s: SPORT_REGULAR_SEASON_GAMES_PER_TEAM[s] * _SPORT_TEAM_COUNTS[s] // 2
    for s in SPORT_REGULAR_SEASON_GAMES_PER_TEAM
    if s in _SPORT_TEAM_COUNTS
}
_NHL_RESULTS_REGULAR_SEASON_MD = ((10, 1), (4, 30))  # Oct–Apr regular season (excludes playoffs)

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


def _nhl_results_regular_season_bounds(ref_dt=None):
    """Oct–Apr bounds for the NHL season that contains ref_dt (regular season only)."""
    ref_dt = ref_dt or datetime.now()
    (sm, sd), (em, ed) = _NHL_RESULTS_REGULAR_SEASON_MD
    if (ref_dt.month, ref_dt.day) >= (sm, sd):
        season_start_year = ref_dt.year
    elif (ref_dt.month, ref_dt.day) <= (em, ed):
        season_start_year = ref_dt.year - 1
    else:
        season_start_year = ref_dt.year - 1
    start = datetime(season_start_year, sm, sd)
    end = datetime(season_start_year + 1, em, ed)
    return start, end


_NHL_FRANCHISE_TEAM_KEYS = frozenset({
    'anaheimducks', 'bostonbruins', 'buffalosabres', 'calgaryflames',
    'carolinahurricanes', 'chicagoblackhawks', 'coloradoavalanche',
    'columbusbluejackets', 'dallasstars', 'detroitredwings', 'edmontonoilers',
    'floridapanthers', 'losangeleskings', 'minnesotawild', 'montrealcanadiens',
    'nashvillepredators', 'newjerseydevils', 'newyorkislanders', 'newyorkrangers',
    'ottawasenators', 'philadelphiaflyers', 'pittsburghpenguins', 'sanjosesharks',
    'seattlekraken', 'stlouisblues', 'tampabaylightning', 'torontomapleleafs',
    'utahmammoth', 'vancouvercanucks', 'vegasgoldenknights', 'washingtoncapitals',
    'winnipegjets',
})


def _nhl_results_match_key(row):
    """Canonical (date, home, away) for NHL results dedupe."""
    if isinstance(row, dict):
        raw_date = row.get('game_date') or row.get('date')
        home = row.get('home_team_id') or row.get('home')
        away = row.get('away_team_id') or row.get('away')
    else:
        raw_date = row['game_date']
        home = row['home_team_id']
        away = row['away_team_id']
    game_date = _normalize_game_date_key(raw_date)
    if not game_date:
        return None
    hk = _normalize_team_key_for_sport('NHL', home)
    ak = _normalize_team_key_for_sport('NHL', away)
    if not hk or not ak:
        return None
    return (game_date, hk, ak)


def _nhl_row_prediction_score(row):
    """Prefer DB rows that carry stored model probabilities."""
    prob_cols = (
        'glicko_home_prob', 'trueskill_home_prob', 'meta_home_prob',
        'elo_home_prob', 'xgboost_home_prob', 'catboost_home_prob',
        'win_probability',
    )
    score = 0
    for col in prob_cols:
        try:
            if row[col] is not None:
                score += 1
        except (KeyError, IndexError, TypeError):
            pass
    return score


def _is_nhl_franchise_regular_season_row(row):
    key = _nhl_results_match_key(row)
    if not key:
        return False
    _, hk, ak = key
    return hk in _NHL_FRANCHISE_TEAM_KEYS and ak in _NHL_FRANCHISE_TEAM_KEYS


def _dedupe_nhl_game_rows(rows, *, apply_season_cap=True):
    """Drop duplicate NHL rows (mixed game_id / team aliases / int'l games)."""
    if not rows:
        return []
    best = {}
    for row in rows:
        if not _is_nhl_franchise_regular_season_row(row):
            continue
        key = _nhl_results_match_key(row)
        score = _nhl_row_prediction_score(row)
        prev = best.get(key)
        if prev is None or score > prev[0]:
            best[key] = (score, row)
    deduped = [pair[1] for pair in best.values()]
    deduped.sort(
        key=lambda r: parse_date(_normalize_game_date_key(r['game_date'])) or datetime.min,
    )
    if not apply_season_cap:
        return deduped
    cap = SPORT_REGULAR_SEASON_GAMES_PER_TEAM.get('NHL', 82)
    team_counts = {}
    kept = []
    for row in deduped:
        _, hk, ak = _nhl_results_match_key(row)
        if team_counts.get(hk, 0) >= cap or team_counts.get(ak, 0) >= cap:
            continue
        team_counts[hk] = team_counts.get(hk, 0) + 1
        team_counts[ak] = team_counts.get(ak, 0) + 1
        kept.append(row)
    league_max = SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL')
    if league_max and len(kept) > league_max:
        kept = kept[:league_max]
    return kept


def _dedupe_daily_results(daily_results, sport=None):
    """Remove duplicate games inside daily_results buckets (NHL only)."""
    if sport != 'NHL' or not daily_results:
        return daily_results
    from collections import defaultdict
    best = {}
    for bucket in daily_results.values():
        for game in bucket.get('games') or []:
            key = _nhl_results_match_key(game)
            if not key or not _is_nhl_franchise_regular_season_row(game):
                continue
            score = _nhl_row_prediction_score(game)
            prev = best.get(key)
            if prev is None or score > prev[0]:
                best[key] = (score, game)
    if not best:
        return daily_results
    out = defaultdict(lambda: {'games': []})
    ordered = sorted(
        (pair[1] for pair in best.values()),
        key=lambda g: parse_date(g.get('date')) or datetime.min,
        reverse=True,
    )
    cap = SPORT_REGULAR_SEASON_GAMES_PER_TEAM.get('NHL', 82)
    team_counts = {}
    league_max = SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL')
    for game in ordered:
        key = _nhl_results_match_key(game)
        if not key:
            continue
        _, hk, ak = key
        if team_counts.get(hk, 0) >= cap or team_counts.get(ak, 0) >= cap:
            continue
        if league_max and sum(len(b.get('games') or []) for b in out.values()) >= league_max:
            break
        team_counts[hk] = team_counts.get(hk, 0) + 1
        team_counts[ak] = team_counts.get(ak, 0) + 1
        out[game.get('date') or key[0]]['games'].append(game)
    return out


def _nhl_results_games_in_scope(daily_results):
    """Graded NHL regular-season games for banner subtitle (never above league max)."""
    count = _daily_results_game_count(daily_results)
    league_max = SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL')
    if league_max:
        return min(count, league_max)
    return count


def _nhl_season_label(ref_dt=None):
    ref_dt = ref_dt or datetime.now()
    start, _ = _nhl_results_regular_season_bounds(ref_dt)
    return f'{start.year}-{str(start.year + 1)[-2:]}'


def _nhl_regular_season_complete(ref_dt=None):
    ref_dt = ref_dt or datetime.now()
    _, end = _nhl_results_regular_season_bounds(ref_dt)
    return ref_dt.date() > end.date()


def _nhl_playoff_window(ref_dt=None):
    """May–Jun playoff window for the NHL season containing ref_dt."""
    ref_dt = ref_dt or datetime.now()
    _, reg_end = _nhl_results_regular_season_bounds(ref_dt)
    start = datetime(reg_end.year, reg_end.month, reg_end.day) + timedelta(days=1)
    end = min(ref_dt - timedelta(days=1), datetime(reg_end.year + 1, 6, 30))
    if end < start:
        end = start
    return start, end


def _nhl_snapshot_json_path(ref_dt=None, phase='regular'):
    """Resolved path to committed NHL season snapshot JSON (first existing candidate)."""
    label = _nhl_season_label(ref_dt)
    fname = f'NHL_{label}_{phase}.json'
    for base in (_V2_BASE, _BASE_DIR, _os_v2.path.dirname(_os_v2.path.abspath(__file__))):
        path = _os_v2.path.join(base, 'data', 'season_snapshots', fname)
        if _os_v2.path.isfile(path):
            return path
    return _os_v2.path.join(_V2_BASE, 'data', 'season_snapshots', fname)


def _load_nhl_season_snapshot(ref_dt=None, phase='regular'):
    """Load committed season JSON (no import side-effects — works on Render)."""
    path = _nhl_snapshot_json_path(ref_dt, phase)
    if not _os_v2.path.isfile(path):
        logger.debug('NHL season snapshot missing: %s', path)
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('NHL season snapshot read failed (%s): %s', path, exc)
        return None
    if not isinstance(data, dict) or data.get('sport') != 'NHL':
        return None
    return data


def _stats_from_nhl_snapshot(snapshot):
    if not snapshot:
        return None
    ou = snapshot.get('ou_summary') or {}
    overall_stats = snapshot.get('overall_stats') or {}
    spread_total_stats = snapshot.get('spread_total_stats') or {}
    old_perf = snapshot.get('season_perf') or {}
    season_perf = _build_season_performance_summary(
        overall_stats,
        spread_total_stats,
        scope_label=old_perf.get('scope_label'),
        games_expected=snapshot.get('games_expected'),
        games_in_scope=snapshot.get('games_in_scope'),
    )
    return {
        'overall_stats': overall_stats,
        'spread_total_stats': spread_total_stats,
        'season_perf': season_perf,
        'total_over': ou.get('total_over', 0),
        'total_under': ou.get('total_under', 0),
        'total_games_ou': ou.get('total_games_ou', 0),
        'avg_total': ou.get('avg_total', 0),
        'ou_bench': ou.get('ou_bench', 0),
        'roi_total': snapshot.get('roi_total'),
    }


ALL_SPORTS_DASHBOARD_SPORTS = [
    'NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'SOCCER',
]
_ML_DASHBOARD_MODELS = (
    ('glicko2', 'Grinder2'),
    ('trueskill', 'Takedown'),
    ('elo', 'Edge'),
    ('xgboost', 'XSharp'),
    ('ensemble', 'Sharp Consensus'),
    ('efficiency', 'Efficiency'),
)


def _all_sports_snapshot_dir():
    """Resolved snapshot directory (same multi-base lookup as NHL snapshots — no src import)."""
    for base in (_V2_BASE, _BASE_DIR, _os_v2.path.dirname(_os_v2.path.abspath(__file__))):
        path = _os_v2.path.join(base, 'data', 'season_snapshots')
        if _os_v2.path.isdir(path):
            return path
    return _os_v2.path.join(_V2_BASE, 'data', 'season_snapshots')


def _load_all_sports_season_snapshots():
    """Load newest committed regular-season JSON per sport (no live regrade)."""
    snap_dir = _all_sports_snapshot_dir()
    rows = []
    if not _os_v2.path.isdir(snap_dir):
        logger.debug('All-sports snapshot dir missing: %s', snap_dir)
        return rows
    for sport in ALL_SPORTS_DASHBOARD_SPORTS:
        pattern = _os_v2.path.join(snap_dir, f'{sport}_*_regular.json')
        paths = sorted(glob.glob(pattern), reverse=True)
        snap = None
        for path in paths:
            try:
                with open(path, encoding='utf-8') as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get('sport') == sport:
                snap = data
                break
        if snap:
            rows.append(snap)
    return rows


def _fmt_snapshot_ml_cell(overall, model_key):
    m = (overall or {}).get(model_key) or {}
    total = int(m.get('total') or 0)
    correct = int(m.get('correct') or 0)
    if total <= 0:
        return {'pct': None, 'record': '—', 'n': 0}
    pct = m.get('accuracy')
    if pct is None:
        pct = round(correct / total * 100, 1)
    return {
        'pct': pct,
        'record': f'{correct}-{total - correct}',
        'n': total,
    }


def _fmt_snapshot_market_cell(st, *, graded_key, win_key, pct_key, push_key=None):
    st = st or {}
    graded = int(st.get(graded_key) or 0)
    if graded <= 0:
        return {'pct': None, 'record': '—', 'n': 0}
    wins = int(st.get(win_key) or 0)
    pushes = int(st.get(push_key) or 0) if push_key else 0
    losses = max(0, graded - pushes - wins)
    return {
        'pct': st.get(pct_key),
        'record': f'{wins}-{losses}',
        'n': graded,
    }


def _build_all_sports_dashboard_rows(snapshots):
    rows = []
    for snap in snapshots:
        sport = snap.get('sport')
        if sport not in SPORTS:
            continue
        overall = snap.get('overall_stats') or {}
        st = snap.get('spread_total_stats') or {}
        ml_cols = {
            key: _fmt_snapshot_ml_cell(overall, key) for key, _ in _ML_DASHBOARD_MODELS
        }
        rows.append({
            'sport': sport,
            'name': SPORTS[sport]['name'],
            'icon': SPORTS[sport].get('icon', ''),
            'season': snap.get('season') or '',
            'games_in_scope': snap.get('games_in_scope'),
            'ml': ml_cols,
            'spread_xsharp': _fmt_snapshot_market_cell(
                st,
                graded_key='spread_graded',
                win_key='spread_covered',
                pct_key='spread_pct',
                push_key='spread_pushes',
            ),
            'spread_pl': _fmt_snapshot_market_cell(
                st,
                graded_key='pl_spread_graded',
                win_key='pl_spread_covered',
                pct_key='pl_spread_pct',
                push_key='pl_spread_pushes',
            ),
            'ou_xsharp': _fmt_snapshot_market_cell(
                st,
                graded_key='total_graded',
                win_key='total_correct',
                pct_key='total_pct',
                push_key='total_pushes',
            ),
            'ou_pl': _fmt_snapshot_market_cell(
                st,
                graded_key='pl_total_graded',
                win_key='pl_total_correct',
                pct_key='pl_total_pct',
                push_key='pl_total_pushes',
            ),
            'results_url': f"/{_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results')}",
        })
    return rows


def _attach_nhl_display_grading(sport, daily_results):
    """Book lines + spread/O/U for a small display slice (not full season)."""
    if not daily_results or not _daily_results_game_count(daily_results):
        return None
    _attach_book_odds_to_daily_results(sport, daily_results, api_limit=80)
    _cache_market_lines_for_results(sport, daily_results, limit=40)
    _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
    st = _compute_spread_total_for_daily(sport, daily_results)
    _finalize_daily_result_cards(sport, daily_results)
    return st


def _results_season_bounds(sport, ref_dt=None):
    """Date window for season-performance stats on results pages (NHL wired)."""
    if sport == 'NHL':
        return _nhl_results_regular_season_bounds(ref_dt)
    return _season_window_for_date(sport, ref_dt or datetime.now())


def _subset_daily_results(daily_results, start_dt, end_dt):
    """Shallow slice of daily_results by calendar date (shared game dicts)."""
    from collections import defaultdict
    if not daily_results:
        return defaultdict(lambda: {'games': []})
    out = defaultdict(lambda: {'games': []})
    for date_key, bucket in daily_results.items():
        if _date_in_range(date_key, start_dt, end_dt):
            out[date_key]['games'] = list(bucket.get('games') or [])
    return out


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


def get_season_phase(sport, today=None):
    """Classify a sport's current phase using SEASON_CALENDAR.

    Returns (phase, start_dt, end_dt) where phase is one of 'regular',
    'playoffs', or 'offseason'. start_dt/end_dt bound the current phase (the
    live season is anchored on `today`, correctly handling wrap-around seasons
    like the NHL's Oct–Jun span). Returns ('unknown', None, None) for sports
    with no modeled calendar entry.
    """
    today = today or datetime.now()
    info = SEASON_CALENDAR.get(sport)
    if not info:
        return 'unknown', None, None
    live_start, live_end = _season_window_for_date(sport, today)
    if not live_start or not live_end or not (live_start <= today <= live_end):
        return 'offseason', live_start, live_end
    playoffs = info.get('playoffs')
    if not playoffs:
        return 'regular', live_start, live_end
    (pm, pd), _ = playoffs
    # The playoff start recurs annually; pick the concrete year that lands
    # inside the current live window (handles seasons that cross New Year).
    playoff_start = None
    for yr in (live_start.year, live_end.year):
        cand = datetime(yr, pm, pd)
        if live_start <= cand <= live_end:
            playoff_start = cand
            break
    if playoff_start is None:
        return 'regular', live_start, live_end
    if today >= playoff_start:
        return 'playoffs', playoff_start, live_end
    return 'regular', live_start, playoff_start

# ===== SECTION: Off-season messaging =====
def _next_season_start(sport, today=None):
    """Datetime of the upcoming season start for a sport (or None if the sport
    is in-season or has no modeled season window)."""
    today = today or datetime.now()
    min_live = _SPORT_MIN_LIVE_DATES.get(sport)
    if min_live and today < min_live:
        return min_live
    start, end = _season_window_for_date(sport, today)
    if not start or not end:
        return None
    if today < start:
        return start
    if today > end:
        window = _SEASON_WINDOWS.get(sport)
        if not window:
            return None
        (sm, sd), _ = window
        return datetime(start.year + 1, sm, sd)
    return None

def _offseason_message(sport, today=None):
    """Human 'season starts <date>' message for an off-season sport, or None if
    the sport is currently live/in-season. Falls back to a static hint for
    sports without a modeled season window."""
    today = today or datetime.now()
    try:
        _status, _is_live = get_season_status(sport, today=today)
    except Exception:
        _status, _is_live = (None, True)
    if _is_live:
        return None
    name = SPORTS.get(sport, {}).get('name', sport)
    start = _next_season_start(sport, today)
    if start:
        _date_label = start.strftime('%B %d, %Y').replace(' 0', ' ')
        return (f"{name} is in the off-season. The next season starts {_date_label}. "
                "Until then, you can review last season's results below.")
    return _OFFSEASON_SPORTS_HINT.get(sport) or (
        f"{name} is currently in the off-season. Check back when the schedule is "
        "live, or review last season's results below.")

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
        ('ensemble', 'Sharp Consensus'),
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
SUPPORT_EMAIL = 'support.predictionlab@gmail.com'
CONTACT_EMAIL = SUPPORT_EMAIL  # public / schema.org only — not a mailto on /contact
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


# ===== SECTION: Placeholder matchup filtering =====
# Bracket/TBD markers (soccer cups, etc.). All-Star / TEAM COOP names are handled
# by the earlier _is_placeholder_team_name definition — do NOT redefine it here.
_PLACEHOLDER_TEAM_MARKERS = (
    'winner', 'loser', 'tbd', 'tba', 'round of', 'qualifier',
    'to be determined', 'to be decided', 'winner of', 'loser of',
)


def _is_bracket_placeholder_team_name(name):
    """True for unresolved bracket labels (e.g. soccer "Round of 32 Winner")."""
    if not name:
        return True
    _n = str(name).strip().lower()
    if not _n:
        return True
    return any(_m in _n for _m in _PLACEHOLDER_TEAM_MARKERS)


def _homepage_pick_today_str():
    try:
        return datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d')


def _homepage_matchup_key(sport, away, home) -> str:
    """Stable board key: same series matchup must only appear once on the homepage."""
    return f"{(sport or '').strip()}::{(away or '').strip()}::{(home or '').strip()}"


def _dedupe_homepage_picks(picks, limit=6):
    """Keep first occurrence per game_id and per sport/away/home matchup."""
    _out = []
    _seen = set()
    for _p in picks:
        if not isinstance(_p, dict):
            continue
        _gid = str(_p.get('game_id') or '').strip()
        _mkey = _homepage_matchup_key(_p.get('sport'), _p.get('away'), _p.get('home'))
        if (_gid and _gid in _seen) or _mkey in _seen:
            continue
        if _gid:
            _seen.add(_gid)
        _seen.add(_mkey)
        _out.append(_p)
        if len(_out) >= limit:
            break
    return _out


def _home_win_prob_from_pred(pred) -> float | None:
    for _k in ('ensemble_prob', 'elo_prob', 'xgb_prob'):
        _v = pred.get(_k)
        if _v is None:
            continue
        try:
            _f = float(_v)
            return _f / 100.0 if _f > 1.0 else _f
        except Exception:
            continue
    return None


def _cached_slate_for_homepage(sport):
    """Read a sport slate from memory/disk only — avoid cold rebuild on homepage."""
    for _ck in _predictions_cache_key_aliases(sport):
        _entry = _PREDICTIONS_CACHE.get(_ck)
        _data = _entry.get('data') if isinstance(_entry, dict) else None
        if _data:
            return _data
    return _recover_cached_predictions(sport) or []


def _fill_homepage_picks_from_live_slates(todays_picks, target=6):
    """When the predictions DB is empty/stale, hydrate the board from live slates."""
    if len(todays_picks) >= target:
        return
    _today = _homepage_pick_today_str()
    _existing = set()
    for p in todays_picks:
        _gid = str(p.get('game_id') or '').strip()
        if _gid:
            _existing.add(_gid)
        _existing.add(_homepage_matchup_key(p.get('sport'), p.get('away'), p.get('home')))
    _pool = []
    for _sport in ('MLB', 'NBA', 'NHL', 'WNBA', 'NFL', 'NCAAB', 'SOCCER'):
        if _sport == 'SOCCER' and not SOCCER_ENABLED:
            continue
        _slate = _cached_slate_for_homepage(_sport)
        for _pred in _slate:
            if not isinstance(_pred, dict) or _pred.get('home_score') is not None:
                continue
            _gd = (_pred.get('game_date') or '')[:10]
            if _gd != _today:
                continue
            _home = _pred.get('home_team_id') or ''
            _away = _pred.get('away_team_id') or ''
            if _is_placeholder_team_name(_home) or _is_placeholder_team_name(_away):
                continue
            _ens = _home_win_prob_from_pred(_pred)
            if _ens is None:
                continue
            _home_picked = _ens >= 0.5
            _pick_prob = _ens if _home_picked else (1.0 - _ens)
            _pool.append({
                'game_id': _pred.get('game_id'),
                'away': _away,
                'home': _home,
                'pick': _home if _home_picked else _away,
                'prob': round(_pick_prob * 100, 1),
                'home_prob': round(_ens * 100, 1),
                'away_prob': round((1.0 - _ens) * 100, 1),
                'pick_side': 'home' if _home_picked else 'away',
                'is_live': False,
                'sport': _sport,
                'slug': SPORT_SEO_SLUGS.get(_sport, ''),
                'fallback_score': abs(_ens - 0.5),
            })
    # Never call get_upcoming_predictions() here. Render runs workers=1; a cold
    # MLB/NBA/NHL rebuild on the homepage request path wedges every thread and
    # the whole site (including /static) times out with 0 bytes.
    _pool.sort(key=lambda x: x['fallback_score'], reverse=True)
    for _row in _pool:
        _gid = str(_row.get('game_id') or '').strip()
        _key = _homepage_matchup_key(_row['sport'], _row['away'], _row['home'])
        if (_gid and _gid in _existing) or _key in _existing:
            continue
        if _gid:
            _existing.add(_gid)
        _existing.add(_key)
        todays_picks.append(_row)
        if len(todays_picks) >= target:
            break


def build_todays_top_picks():
    """Up to six ranked value picks for landing + /promo/top-picks-today."""
    todays_picks = []
    _tp_today = _homepage_pick_today_str()
    _target = 6
    try:
        _tp_conn = get_db_connection()
        _tp_rows = _tp_conn.execute('''
            SELECT p.game_id, p.sport, p.home_team_id, p.away_team_id, p.win_probability,
                   p.game_date, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob,
                   p.meta_home_prob, b.home_implied_prob, b.away_implied_prob
            FROM predictions p
            LEFT JOIN games g ON p.game_id = g.game_id AND g.sport = p.sport
            LEFT JOIN betting_odds b ON p.game_id = b.game_id
            WHERE date(p.game_date) BETWEEN date(?, '-1 day') AND date(?, '+1 day')
              AND (g.home_score IS NULL OR g.game_id IS NULL)
              AND p.win_probability IS NOT NULL
              AND p.sport IN ('NHL', 'NBA', 'MLB', 'WNBA', 'NFL', 'NCAAB', 'SOCCER')
            ORDER BY p.game_date ASC
            LIMIT 80
        ''', (_tp_today, _tp_today)).fetchall()
        _tp_conn.close()
        _candidates = []
        for _tp in _tp_rows:
            _ens_home = float(_tp['win_probability'])
            _home = _tp['home_team_id']
            _away = _tp['away_team_id']
            # Skip unresolved bracket placeholders (e.g. soccer "Round of 32 12 Winner")
            if _is_placeholder_team_name(_home) or _is_placeholder_team_name(_away):
                continue
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
            _gd = str(_tp['game_date'] or '')[:10]

            _candidates.append({
                'game_id': _tp['game_id'],
                'away': _away,
                'home': _home,
                'pick': _pick,
                'prob': round(_pick_prob * 100, 1),
                'home_prob': round(_ens_home * 100, 1),
                'away_prob': round((1.0 - _ens_home) * 100, 1),
                'pick_side': 'home' if _home_picked else 'away',
                'is_live': False,
                'sport': _tp['sport'],
                'slug': SPORT_SEO_SLUGS.get(_tp['sport'], ''),
                'game_date': _gd,
                'quality_score': _quality_score,
                'fallback_score': abs(_ens_home - 0.5),
            })

        # Prefer today's slate, then quality. Dedupe by ESPN game_id AND matchup
        # (MLB series days share sport/away/home but have different game_ids).
        _seen_keys = set()
        _scored = sorted(
            _candidates,
            key=lambda x: (
                0 if x.get('game_date') == _tp_today else 1,
                -x['quality_score'],
            ),
        )
        for _row in _scored:
            _gid = str(_row.get('game_id') or '').strip()
            _mkey = _homepage_matchup_key(_row['sport'], _row['away'], _row['home'])
            if (_gid and _gid in _seen_keys) or _mkey in _seen_keys:
                continue
            if _gid:
                _seen_keys.add(_gid)
            _seen_keys.add(_mkey)
            todays_picks.append({
                'game_id': _row.get('game_id'),
                'away': _row['away'], 'home': _row['home'],
                'pick': _row['pick'], 'prob': _row['prob'],
                'home_prob': _row.get('home_prob'), 'away_prob': _row.get('away_prob'),
                'pick_side': _row.get('pick_side'), 'is_live': _row.get('is_live', False),
                'sport': _row['sport'], 'slug': _row['slug'],
            })
            if len(todays_picks) >= _target:
                break

        if len(todays_picks) < _target:
            _fallback = sorted(
                _candidates,
                key=lambda x: (
                    0 if x.get('game_date') == _tp_today else 1,
                    -x['fallback_score'],
                ),
            )
            for _row in _fallback:
                _gid = str(_row.get('game_id') or '').strip()
                _key = _homepage_matchup_key(_row['sport'], _row['away'], _row['home'])
                if (_gid and _gid in _seen_keys) or _key in _seen_keys:
                    continue
                if _gid:
                    _seen_keys.add(_gid)
                _seen_keys.add(_key)
                todays_picks.append({
                    'game_id': _row.get('game_id'),
                    'away': _row['away'], 'home': _row['home'],
                    'pick': _row['pick'], 'prob': _row['prob'],
                    'home_prob': _row.get('home_prob'), 'away_prob': _row.get('away_prob'),
                    'pick_side': _row.get('pick_side'), 'is_live': _row.get('is_live', False),
                    'sport': _row['sport'], 'slug': _row['slug'],
                })
                if len(todays_picks) >= _target:
                    break
    except Exception as _tp_err:
        logger.debug(f"Today's Top Picks DB query failed: {_tp_err}")
    _fill_homepage_picks_from_live_slates(todays_picks, target=_target)
    return _dedupe_homepage_picks(todays_picks, limit=_target)


@app.route('/healthz')
def healthz():
    """Lightweight probe for Render/load balancers (no DB or model work)."""
    return 'ok', 200


_LANDING_PAGE_CACHE = {'ts': 0, 'html': None}
_LANDING_PAGE_TTL = 120  # seconds — homepage is identical for all anonymous visitors

@app.route('/', methods=['GET', 'HEAD'])
def landing_page():
    """Primary landing page — new research design (homepage_preview.html)."""
    if request.method == 'HEAD':
        return '', 200
    try:
        log_site_visit('/')
    except Exception:
        pass
    # Serve a short-lived cached homepage to anonymous visitors so the DB queries
    # + blog/units aggregation + 68KB render don't run on every hit.
    try:
        _anon = not (getattr(current_user, 'is_authenticated', False) and current_user.is_authenticated)
    except Exception:
        _anon = True
    if _anon:
        _cached = _LANDING_PAGE_CACHE.get('html')
        if _cached and (_time.time() - _LANDING_PAGE_CACHE.get('ts', 0)) < _LANDING_PAGE_TTL:
            return _cached
    rendered = render_template('homepage_preview.html', **_build_landing_preview_context())
    if _anon and isinstance(rendered, str) and rendered:
        # Never cache an empty live board — stale empty homepage confuses visitors.
        if rendered.count('class="pl2-pick-card"') >= 1:
            _LANDING_PAGE_CACHE['ts'] = _time.time()
            _LANDING_PAGE_CACHE['html'] = rendered
    return rendered

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
        return jsonify({'detail': 'Authentication required.'}), 401
    if not is_premium_user():
        return jsonify({'detail': 'Premium subscription required.'}), 403
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


_PERF_MODEL_ORDER = ['Grinder2', 'Takedown', 'Edge', 'XSharp', 'Sharp Consensus']
_TEAM_PERF_MODEL_ORDER = _PERF_MODEL_ORDER + ['Efficiency']
_TEAM_PERF_ML_CONFIG = [
    ('glicko2_prob', 'Grinder2'),
    ('trueskill_prob', 'Takedown'),
    ('elo_prob', 'Edge'),
    ('xgb_prob', 'XSharp'),
    ('ens_prob', 'Sharp Consensus'),
]
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
    model_sport = _v2_model_sport(sport)
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
            'Sharp Consensus': meta_prob,
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

    return main_table, sport_tables


def _team_perf_ml_correct_for_team(game, team, prob_key):
    """Grade moneyline from the given team's perspective (every game they played)."""
    home, away = game.get('home'), game.get('away')
    if team not in (home, away):
        return None
    prob = game.get(prob_key)
    if prob is None:
        return None
    if game.get('is_draw') or game.get('home_win') is None:
        return None
    is_home = team == home
    team_prob = float(prob) if is_home else (100.0 - float(prob))
    picked_this_team = team_prob >= 50.0
    team_won = bool(game['home_win']) if is_home else (not bool(game['home_win']))
    return picked_this_team == team_won


def _team_perf_accumulate(rollup, sport, team, model, correct):
    if correct is None:
        return
    key = (sport, team, model)
    if key not in rollup:
        rollup[key] = {'total': 0, 'wins': 0, 'losses': 0}
    rollup[key]['total'] += 1
    if correct:
        rollup[key]['wins'] += 1
    else:
        rollup[key]['losses'] += 1


def _build_team_performance_rows(sport_filter: str = ''):
    """
    Team cards: full current-season graded picks per team (ML + spread + O/U).
    Independent of the main performance page last-N filter.
    """
    sports = [sport_filter] if sport_filter else list(_PERF_SPORT_OPTIONS)
    rollup = {}
    ref_dt = datetime.now()

    for sport in sports:
        if sport not in _PERF_SPORT_OPTIONS:
            continue
        start_dt, end_dt = _results_season_bounds(sport, ref_dt)
        daily_results = _banner_daily_results_for_range(sport, start_dt, end_dt)
        if not daily_results:
            continue
        try:
            _compute_spread_total_for_daily(sport, daily_results)
        except Exception as exc:
            logger.warning(f"[team-perf] spread/total grading failed for {sport}: {exc}")

        for day_data in daily_results.values():
            for game in day_data.get('games', []):
                if game.get('skip_grading'):
                    continue
                home = game.get('home')
                away = game.get('away')
                if not home or not away:
                    continue
                for team in (home, away):
                    for prob_key, model in _TEAM_PERF_ML_CONFIG:
                        correct = _team_perf_ml_correct_for_team(game, team, prob_key)
                        _team_perf_accumulate(rollup, sport, team, model, correct)

                    sp_pick = game.get('spread_pick')
                    sp_ok = game.get('spread_correct')
                    if sp_pick not in (None, 'PUSH') and sp_ok is not None:
                        _team_perf_accumulate(rollup, sport, team, 'XSharp', sp_ok)

                    tp_pick = game.get('total_pick')
                    tp_ok = game.get('total_correct')
                    if tp_pick not in (None, 'PUSH') and tp_ok is not None:
                        _team_perf_accumulate(rollup, sport, team, 'XSharp', tp_ok)

                    pl_sp = game.get('pl_spread_correct')
                    if pl_sp is not None:
                        _team_perf_accumulate(rollup, sport, team, 'Efficiency', pl_sp)

                    pl_tot = game.get('pl_total_correct')
                    if pl_tot is not None:
                        _team_perf_accumulate(rollup, sport, team, 'Efficiency', pl_tot)

    by_team = {}
    for (sport, team, model), vals in rollup.items():
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
    for row in by_team.values():
        ordered_models = {m: row['models'].get(m) for m in _TEAM_PERF_MODEL_ORDER}
        row['models'] = ordered_models
        team_chart_rows.append(row)

    team_chart_rows.sort(key=lambda x: (-x['total_n'], x['team']))
    return team_chart_rows[:120]


@app.route('/player-props')
def player_props_page():
    """Player props page — wired into the main app via /player-props-api/ routes.

    Rendered for every logged-in user. Non-premium users get ONE unlocked prop
    as a teaser (rest locked/blurred); premium users get full access. The
    per-tab gating is enforced by the /player-props-api/* routes below.
    """
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    return render_template('player_props.html')


@app.route('/player-props/assets/<path:asset_path>')
def player_props_assets(asset_path):
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    assets_dir = _os.path.join(_BASE_DIR, 'standalone-player-props', 'frontend', 'dist', 'assets')
    return send_from_directory(assets_dir, asset_path)


def _props_locked_payload(extra=None):
    """Standard 'upgrade to unlock' JSON for non-premium users (no redirect)."""
    body = {'locked': True, 'is_premium': False,
            'detail': 'Upgrade to unlock full player props access.'}
    if extra:
        body.update(extra)
    return jsonify(body)


def _props_offseason_payload(league):
    """Off-season notice payload for a league with no live games/props.

    Returns None when the league is in-season (or has no modeled calendar) so
    callers fall through to normal handling. Prevents the props engine from
    fabricating a synthetic off-season slate and stops stale prior-season rows
    from surfacing in the Streaks/Results tabs.
    """
    # Soccer props are multi-league and year-round; the modeled EPL window
    # (Aug 1–Jun 30) leaves a false July gap that blocks props while games
    # and lines are still available.
    if league == 'SOCCER':
        return None
    try:
        _status, is_live = get_season_status(league)
    except Exception:
        return None
    if is_live:
        return None
    name = SPORTS.get(league, {}).get('name', league)
    try:
        nxt = _next_season_start(league)
    except Exception:
        nxt = None
    when = ''
    if nxt:
        when = f" The next {name} season starts {nxt.strftime('%B %d, %Y').replace(' 0', ' ')}."
    return {'league': league, 'off_season': True, 'count': 0, 'items': [],
            'detail': f"{name} is in the off-season — no games or props right now.{when}"}


@app.route('/player-props-api/leagues')
def player_props_api_leagues():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
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
        return _props_locked_payload()
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
        off = _props_offseason_payload(league)
        if off is not None:
            return jsonify(off)
        data = engine_mod.get_league_data(league)
        # Snapshot today's picks (real book lines only) so Results/Streaks can
        # grade them against box scores later. Never blocks the response.
        try:
            _snapshot_prop_picks(league, data.get('props') or [])
        except Exception:
            pass
        rows = engine_mod.filter_props(data.get('props', []), prop_type=prop_type, side=side, min_ev=min_ev)
        premium = is_premium_user()
        if not premium:
            # Teaser: unlock only the single best (first, already EV-sorted) prop.
            # Every other row is returned as a locked stub with no pick/number
            # data so the client can blur it without leaking the projection.
            locked = []
            for i, r in enumerate(rows):
                if i == 0:
                    unlocked = dict(r)
                    unlocked['locked'] = False
                    locked.append(unlocked)
                else:
                    locked.append({
                        'player_id': r.get('player_id'),
                        'player_name': r.get('player_name'),
                        'team': r.get('team'),
                        'prop_type': r.get('prop_type'),
                        'locked': True,
                    })
            rows = locked
        resp = {'league': league, 'count': len(rows), 'items': rows,
                'is_premium': premium, 'free_unlocked': (0 if premium else 1),
                'lines_real': data.get('lines_real', False),
                'line_source': data.get('line_source')}
        if premium:
            if 'excluded_players' in data:
                resp['excluded_players'] = data['excluded_players']
            if 'model_variance' in data:
                resp['model_variance'] = data['model_variance']
            if 'sanity_flags' in data:
                resp['sanity_flags'] = data['sanity_flags']
        return jsonify(resp)
    except Exception as exc:
        return jsonify({'detail': str(exc)}), 500


# Line sources backed by a REAL sportsbook number (safe to snapshot + grade).
# ESPN's free DraftKings feed is the default; the Odds API path is optional.
_REAL_LINE_SOURCES = ('espn_props', 'the_odds_api')


def _snapshot_prop_picks(league: str, props: list):
    """Persist today's live picks that carry a REAL sportsbook line so they can
    be graded later against box scores.
    Only rows sourced from a real book line (line_source in
    ('espn_props','the_odds_api')) are stored — never synthetic/internal lines —
    so we never fabricate graded history. Deduped to one pick per (player,
    prop). Safe to call on every /props view (INSERT OR IGNORE keeps the first
    snapshot of the day).
    /props view (INSERT OR IGNORE keeps the first snapshot of the day).
    """
    if not props:
        return
    try:
        engine_mod, _ = _load_props_modules()
        deduped = engine_mod.filter_props(props)
    except Exception:
        deduped = props
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    now_iso = datetime.now(ZoneInfo("America/New_York")).isoformat()
    conn = get_db_connection()
    try:
        for r in deduped:
            src = r.get('line_source')
            if src not in _REAL_LINE_SOURCES:
                continue
            line = r.get('line')
            if line is None:
                continue
            pick = r.get('picked_side')
            odds = r.get('odds_over') if pick == 'OVER' else r.get('odds_under')
            conn.execute(
                '''INSERT OR IGNORE INTO player_prop_picks
                   (league, pick_date, player_name, team, prop_type, pick, line, projection, odds, line_source, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (league, today, r.get('player_name'), r.get('team'), r.get('prop_type'),
                 pick, float(line), r.get('projection'), odds, src, now_iso)
            )
        conn.commit()
    finally:
        conn.close()


def _grade_and_store_props(league: str, for_date_str: str):
    """Grade previously-snapshotted picks for a date against real box-score
    actuals and persist HIT/MISS to player_prop_results.

    Picks are graded against the REAL sportsbook line they were snapshotted with
    (never a re-derived internal line). N/A (no completed stat) and PUSH
    (actual == line) are not stored, so hit rate stays truthful.
    """
    conn = get_db_connection()
    try:
        picks = [dict(r) for r in conn.execute(
            'SELECT * FROM player_prop_picks WHERE league=? AND pick_date=?',
            (league, for_date_str)
        ).fetchall()]
    finally:
        conn.close()
    if not picks:
        return
    try:
        engine_mod, _ = _load_props_modules()
        actuals = engine_mod.get_actuals_for_date(league, for_date_str)
    except Exception:
        actuals = {}
    if not actuals:
        return
    conn = get_db_connection()
    try:
        for pk in picks:
            nm = str(pk.get('player_name') or '').lower()
            pt = pk.get('prop_type')
            actual = (actuals.get(nm) or {}).get(pt)
            if actual is None:
                continue  # N/A: no completed box-score stat — do not store
            line = float(pk.get('line') or 0.0)
            if float(actual) == line:
                continue  # PUSH: do not count toward hit rate
            hit = (actual > line and pk.get('pick') == 'OVER') or (actual < line and pk.get('pick') == 'UNDER')
            conn.execute(
                '''INSERT OR REPLACE INTO player_prop_results
                   (league, result_date, player_name, team, prop_type, pick, line, projection, actual, result)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (league, for_date_str,
                 pk.get('player_name'), pk.get('team'), pt,
                 pk.get('pick'), line, pk.get('projection'),
                 round(float(actual), 2), 'HIT' if hit else 'MISS')
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
                elif r['result'] == 'MISS': b['losses'] += 1
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
    league = (request.args.get('league') or '').strip().upper()
    for_date = (request.args.get('date') or '').strip() or None
    off = _props_offseason_payload(league)
    if off is not None:
        return jsonify(off)
    if not is_premium_user():
        return _props_locked_payload()
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
    league = (request.args.get('league') or 'NBA').strip().upper()
    off = _props_offseason_payload(league)
    if off is not None:
        return jsonify(off)
    if not is_premium_user():
        return _props_locked_payload()
    try:
        engine_mod, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        return jsonify(engine_mod.get_diagnostics(league))
    except Exception as exc:
        return jsonify({'detail': str(exc)}), 500


def _query_prop_streaks(league: str, limit: int = 200):
    """Compute per-(player, prop) hit streaks from stored graded results.

    Reads only real HIT/MISS rows from player_prop_results (N/A never stored),
    then derives each player+market's current streak, recent form, and hit rate.
    Returns hot streaks (longest active HIT runs) first, then cold streaks.
    """
    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            'SELECT player_name, team, prop_type, result, result_date '
            'FROM player_prop_results '
            "WHERE league=? AND result IN ('HIT','MISS') "
            'ORDER BY result_date ASC',
            (league,)
        ).fetchall()]
    finally:
        conn.close()

    groups = {}
    for r in rows:
        groups.setdefault((r['player_name'], r['prop_type']), []).append(r)

    items = []
    for (pname, pt), rr in groups.items():
        results = [x['result'] for x in rr]
        games = len(results)
        hits = sum(1 for x in results if x == 'HIT')
        last = results[-1]
        streak = 0
        for x in reversed(results):
            if x == last:
                streak += 1
            else:
                break
        items.append({
            'player_name': pname,
            'team': rr[-1]['team'],
            'prop_type': pt,
            'games': games,
            'hits': hits,
            'misses': games - hits,
            'hit_rate': round(hits / games * 100.0, 1) if games else 0.0,
            'streak_type': last,
            'streak_len': streak,
            'recent': results[-5:],
            'last_date': rr[-1]['result_date'],
        })

    # Hot (active HIT) streaks first, longest first; then cold streaks.
    items.sort(key=lambda x: (
        0 if x['streak_type'] == 'HIT' else 1,
        -x['streak_len'],
        -x['hit_rate'],
        -x['games'],
    ))
    return {'league': league, 'count': len(items), 'items': items[:limit]}


@app.route('/player-props-api/streaks')
def player_props_api_streaks():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    league = (request.args.get('league') or '').strip().upper()
    off = _props_offseason_payload(league)
    if off is not None:
        return jsonify(off)
    if not is_premium_user():
        return _props_locked_payload()
    try:
        engine_mod, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        return jsonify(_query_prop_streaks(league))
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
    last_n = 200  # Default: full-season scan is ~50s+ and looks like a hang.
    if last_n_raw == 'all':
        last_n = None
    elif last_n_raw in ('50', '100', '200'):
        last_n = int(last_n_raw)

    main_table, sport_tables = _build_performance_page_data(sport_filter=sport, last_n=last_n)
    # Team cards regrade a full season per sport (~30s for all sports); load only
    # when a single sport is selected so /performance responds in a few seconds.
    team_chart_rows = _build_team_performance_rows(sport_filter=sport) if sport else []
    return render_template(
        'performance.html',
        page='performance',
        selected_sport=sport,
        selected_last_n=(str(last_n) if last_n else 'all'),
        sport_options=_PERF_SPORT_OPTIONS,
        model_order=_PERF_MODEL_ORDER,
        team_model_order=_TEAM_PERF_MODEL_ORDER,
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
        return Response('Authentication required.', status=401, mimetype='text/plain')
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
Disallow: /share/
Disallow: /api/
Disallow: /login
Disallow: /signup

Sitemap: {_SITE_DOMAIN}/sitemap.xml
"""
    return Response(body, mimetype='text/plain')


@app.route('/llms.txt')
def llms_txt():
    path = _os.path.join(_BASE_DIR, 'llms.txt')
    try:
        with open(path, encoding='utf-8') as fh:
            body = fh.read()
    except OSError as exc:
        logger.warning('[llms.txt] read failed (%s): %s', path, exc)
        body = '# Prediction Lab\n\n> https://predictionlab.io\n'
    return Response(body, mimetype='text/plain; charset=utf-8')


@app.route('/ai.txt')
def ai_txt():
    body = """User-agent: *
Allow: /

# AI discovery
LLMs: https://predictionlab.io/llms.txt
Sitemap: https://predictionlab.io/sitemap.xml

# Canonical contact for AI indexing
Contact: support.predictionlab@gmail.com
"""
    return Response(body, mimetype='text/plain')


_CONTACT_TOPIC_LABELS = {
    'support': 'Help / technical issue',
    'suggestion': 'Suggestion',
    'billing': 'Billing / Premium',
    'other': 'Other',
}


def _send_contact_form_email(name, reply_to, topic, message):
    """Deliver contact form to SUPPORT_EMAIL via SMTP (configure SMTP_PASSWORD on Render)."""
    import smtplib
    from email.message import EmailMessage

    smtp_password = (_os.environ.get('SMTP_PASSWORD') or _os.environ.get('CONTACT_SMTP_PASSWORD') or '').strip()
    if not smtp_password:
        logger.warning('[contact] SMTP_PASSWORD not set — contact form cannot send mail')
        return False, 'Email is not configured yet. Please try again later or DM us on X @predictionlab_io.'

    smtp_host = (_os.environ.get('SMTP_HOST') or 'smtp.gmail.com').strip()
    smtp_port = int(_os.environ.get('SMTP_PORT') or '587')
    smtp_user = (_os.environ.get('SMTP_USER') or SUPPORT_EMAIL).strip()
    to_addr = (_os.environ.get('CONTACT_TO_EMAIL') or SUPPORT_EMAIL).strip()
    topic_label = _CONTACT_TOPIC_LABELS.get(topic, topic or 'General')
    subject = f'[predictionlab.io] {topic_label} — {name}'
    body = (
        f'Contact form on predictionlab.io\n\n'
        f'Name: {name}\n'
        f'Reply-To: {reply_to}\n'
        f'Topic: {topic_label}\n\n'
        f'{message}\n'
    )
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_addr
    msg['Reply-To'] = reply_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
        return True, None
    except Exception as exc:
        logger.error(f'[contact] send failed: {exc}', exc_info=True)
        return False, 'We could not send your message right now. Please try again in a few minutes.'


def _validate_contact_submission():
    """Parse POST /contact; returns (ok, error_message, payload_dict)."""
    if request.method != 'POST':
        return False, None, {}
    if (request.form.get('website') or '').strip():
        return True, None, {}  # honeypot — pretend success
    name = (request.form.get('name') or '').strip()
    reply_to = (request.form.get('email') or '').strip().lower()
    topic = (request.form.get('topic') or 'support').strip().lower()
    message = (request.form.get('message') or '').strip()
    if len(name) < 2:
        return False, 'Please enter your name.', {'name': name, 'email': reply_to, 'topic': topic, 'message': message}
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', reply_to):
        return False, 'Please enter a valid email address.', {'name': name, 'email': reply_to, 'topic': topic, 'message': message}
    if topic not in _CONTACT_TOPIC_LABELS:
        topic = 'other'
    if len(message) < 10:
        return False, 'Please enter a longer message (at least 10 characters).', {'name': name, 'email': reply_to, 'topic': topic, 'message': message}
    if len(message) > 5000:
        return False, 'Message is too long (max 5000 characters).', {'name': name, 'email': reply_to, 'topic': topic, 'message': message}
    return True, None, {'name': name, 'email': reply_to, 'topic': topic, 'message': message}


def _sitemap_loc_is_canonical(loc):
    """Only allow final https://predictionlab.io/... URLs (no www/http/legacy /sport/)."""
    if not loc or not loc.startswith(_SITE_DOMAIN):
        return False
    if 'www.' in loc or loc.startswith('http://') or '/sport/' in loc:
        return False
    return True


@app.route('/sitemap.xml')
def sitemap_xml():
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now()
    urls = []

    # Homepage — apex HTTPS only (never www / http / redirecting URLs)
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
        # Dated SEO URLs only for calendar team sports that are in-season.
        # Skip GOLF/TENNIS/UFC (no season window → always "live") — those dated
        # pages are often empty and waste crawl budget ("Crawled - not indexed").
        # Keep today + yesterday only; older empty dated URLs stay 200 + noindex.
        if picks_slug and _is_live and sport_key in SEASON_CALENDAR:
            for days_back in range(2):
                d = now - timedelta(days=days_back)
                month_name = _MONTH_NAMES.get(d.month, 'january')
                daily_url = f"{_SITE_DOMAIN}/{picks_slug}-{month_name}-{d.day}-{d.year}"
                urls.append((daily_url, 'daily', '0.7'))

    # Static / evergreen pages. Thin auto Trends blog is noindex — omit from sitemap.
    # Do not list /auth/*, /share/*, or legacy /sport/*/predictions (robots or 301s).
    urls.append((_SITE_DOMAIN + '/all-sports-results', 'weekly', '0.75'))
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
    urls.append((_SITE_DOMAIN + '/refund-policy', 'monthly', '0.3'))
    urls.append((_SITE_DOMAIN + '/responsible-gaming', 'monthly', '0.4'))


    # Defense: never advertise redirecting URLs (www, http, /sport/*/predictions)
    urls = [(loc, freq, prio) for loc, freq, prio in urls if _sitemap_loc_is_canonical(loc)]

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


@app.route('/all-sports-results')
def all_sports_results_page():
    """Season dashboard from frozen JSON snapshots (no live regrade on load)."""
    try:
        snapshots = _load_all_sports_season_snapshots()
        dashboard_rows = _build_all_sports_dashboard_rows(snapshots)
    except Exception:
        logger.exception('all_sports_results_page failed loading snapshots')
        dashboard_rows = []
    return render_template_string(
        ALL_SPORTS_RESULTS_TEMPLATE,
        page='all-sports-results',
        page_title='All Sports Results | Season Model Performance | predictionlab.io',
        page_description=(
            'Season moneyline, spread, and over/under model results across NHL, NBA, MLB, '
            'NFL, NCAAB, NCAAF, WNBA, and Soccer.'
        ),
        dashboard_rows=dashboard_rows,
        ml_models=_ML_DASHBOARD_MODELS,
    )


# ── SEO picks routes ──────────────────────────────────────────────────────────

@app.route('/<slug>')
def seo_picks_page(slug):
    """Handle SEO-friendly URLs like /nhl-picks, /nba-picks, /nhl-results, etc."""
    if slug.endswith('-predictions'):
        return redirect(f"{_SITE_DOMAIN}/{slug.replace('-predictions', '-picks')}", code=301)
    if slug.endswith('-prediction'):
        return redirect(f"{_SITE_DOMAIN}/{slug.replace('-prediction', '-picks')}", code=301)
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
    """301 redirect old /sport/X/predictions to new SEO URL (absolute canonical)."""
    slug = SPORT_SEO_SLUGS.get((sport or '').upper())
    if slug:
        return redirect(f'{_SITE_DOMAIN}/{slug}', code=301)
    return "Sport not found", 404


@app.route('/sport/<sport>/results')
def old_sport_results_redirect(sport):
    """301 redirect old /sport/X/results to new SEO URL (absolute canonical)."""
    slug = _SPORT_RESULTS_SLUGS.get((sport or '').upper())
    if slug:
        return redirect(f'{_SITE_DOMAIN}/{slug}', code=301)
    return "Sport not found", 404


@app.route('/sport/<sport>')
def sport_home(sport):
    """Redirect to new SEO URL (absolute canonical)."""
    slug = SPORT_SEO_SLUGS.get((sport or '').upper())
    if slug:
        return redirect(f'{_SITE_DOMAIN}/{slug}', code=301)
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
                            _soc_cursor.execute('INSERT INTO games (sport,league,game_id,season,game_date,home_team_id,away_team_id,home_score,away_score,status) VALUES (?,?,?,?,?,?,?,?,?,"final")', ('SOCCER', _soc_lg_name, _soc_gid, yesterday_dt.year, _soc_gd, _soc_ht, _soc_at, _soc_hs, _soc_as))
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
                game_date = _normalize_game_date_key(game['game_date'])
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
                _finalize_daily_result_cards(sport_key, daily_results)
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
        ('ensemble', '🏆 Sharp Consensus'),
        ('efficiency', '⚡ Efficiency'),
    ]

    share_text = f"predictionlab.io Daily Report — {report_display}%0A"
    ens = agg_models.get('ensemble', {})
    if ens.get('total', 0) > 0:
        share_text += f"Sharp Consensus: {ens['accuracy']}% ({ens['correct']}-{ens['total'] - ens['correct']})%0A"
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
    if not _SHARE_TOKEN_RE.match(token or ''):
        return _share_gone_plain('Image not found')
    entry = _get_share_cache_entry(token)
    if not entry:
        return _share_gone_plain('Image not found')
    payload = entry.get('payload') or {}
    if payload.get('type') != 'predictions':
        return _share_gone_plain('Image not found')
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
        return _share_gone_response()
    entry = _get_share_cache_entry(token)
    if not entry or (entry.get('payload') or {}).get('type') != 'predictions':
        return _share_gone_response()
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
    return Response(
        html,
        mimetype='text/html; charset=utf-8',
        headers={'Cache-Control': 'private, max-age=120', 'X-Robots-Tag': 'noindex, nofollow'},
    )


@app.route('/share/daily-report/<token>.<fmt>')
def share_daily_report_image(token, fmt):
    fmt = (fmt or '').lower()
    if fmt not in ('jpg', 'jpeg', 'png'):
        return "Unsupported format", 400
    if not _SHARE_TOKEN_RE.match(token or ''):
        return _share_gone_plain('Image not found')
    entry = _get_share_cache_entry(token)
    if not entry:
        return _share_gone_plain('Image not found')
    payload = entry.get('payload') or {}
    if payload.get('type') != 'daily-report':
        return _share_gone_plain('Image not found')
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
        return _share_gone_response()
    entry = _get_share_cache_entry(token)
    if not entry or (entry.get('payload') or {}).get('type') != 'daily-report':
        return _share_gone_response()
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
    return Response(
        html,
        mimetype='text/html; charset=utf-8',
        headers={'Cache-Control': 'private, max-age=120', 'X-Robots-Tag': 'noindex, nofollow'},
    )


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

@app.route('/contact', methods=['GET', 'POST'])
def contact_page():
    form_name = form_email = form_message = form_topic = ''
    contact_sent = False
    contact_error = None

    if request.method == 'POST':
        ok, err, payload = _validate_contact_submission()
        form_name = payload.get('name', '')
        form_email = payload.get('email', '')
        form_message = payload.get('message', '')
        form_topic = payload.get('topic', 'support')
        if ok and not err and payload:
            sent, send_err = _send_contact_form_email(
                payload['name'], payload['email'], payload['topic'], payload['message'],
            )
            if sent:
                return redirect('/contact?sent=1')
            contact_error = send_err
        elif ok and not payload:
            return redirect('/contact?sent=1')
        else:
            contact_error = err
    else:
        contact_sent = request.args.get('sent') == '1'

    return render_template_string(
        CONTACT_PAGE_TEMPLATE,
        page='contact',
        page_title='Contact us | predictionlab.io',
        page_description='Questions, suggestions, or technical issues for predictionlab.io — send our support team a message.',
        support_email=SUPPORT_EMAIL,
        contact_sent=contact_sent,
        contact_error=contact_error,
        form_name=form_name,
        form_email=form_email,
        form_message=form_message,
        form_topic=form_topic,
    )

@app.route('/privacy')
def privacy_page():
    return render_template('privacy.html')

@app.route('/terms')
def terms_page():
    return render_template('terms.html')

@app.route('/refund-policy')
def refund_policy_page():
    return render_template('refund_policy.html')

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
    return redirect(f'{_SITE_DOMAIN}/soccer-picks?league={league_slug}', code=301)

@app.route('/sport/SOCCER/results/<league_slug>')
def soccer_results_league(league_slug):
    return redirect(f'{_SITE_DOMAIN}/soccer-results?league={league_slug}', code=301)


def _render_espn_picks_page(**ctx):
    """Render picks page — templates/ file first, then root copy (with {% include %} support)."""
    last_err = None
    try:
        return render_template('espn_predictions_template.html', **ctx)
    except Exception as _e:
        last_err = _e
        logger.exception('render_template espn_predictions_template failed: %s', _e)
    for _path in (
        _os.path.join(_BASE_DIR, 'templates', 'espn_predictions_template.html'),
        _os.path.join(_BASE_DIR, 'espn_predictions_template.html'),
    ):
        if not _os.path.isfile(_path):
            continue
        try:
            with open(_path, encoding='utf-8') as _f:
                _src = _f.read()
            return render_template_string(_src, **ctx)
        except Exception as _e2:
            last_err = _e2
            logger.exception('render_template_string picks failed (%s): %s', _path, _e2)
    raise RuntimeError(f'Picks template render failed: {last_err}')


def _predictions_fallback_page(sport, filter_date=None):
    """Safe fallback HTML for SEO picks pages when dynamic rendering fails."""
    sport_info = SPORTS.get(sport, {'name': sport, 'icon': '🏆'})
    safe_title = f"{sport_info['name']} Predictions | predictionlab.io"
    if filter_date:
        safe_title = f"{sport_info['name']} Predictions for {filter_date} | predictionlab.io"
    # Thin fallback / empty dated pages should not compete for indexing.
    robots = _PICKS_ROBOTS_NOINDEX if filter_date else _PICKS_ROBOTS_INDEX
    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ safe_title }}</title>
    <meta name="description" content="Daily AI-powered {{ sport_info.name }} predictions, game forecasts, and model projections on predictionlab.io.">
    <meta name="robots" content="{{ robots }}">
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
    """, sport_info=sport_info, sport_slug=SPORT_SEO_SLUGS.get(sport, sport.lower() + '-picks'), safe_title=safe_title, robots=robots)

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
        cache_key = f"pred_page::v20::{sport}::{filter_date or 'all'}::{selected_slug or 'default'}"
        cache_ttl = _SPORT_PREDICTIONS_PAGE_TTL.get(sport, 180)
        cached_page = _SPORT_PREDICTIONS_PAGE_CACHE.get(cache_key)
        if isinstance(cached_page, dict):
            cached_ts = cached_page.get('ts')
            cached_html = cached_page.get('html')
            _page_age = (_time.time() - cached_ts) if cached_ts is not None else None
            _page_usable = (
                cached_ts is not None
                and cached_html
                and 'game-card' in cached_html
                and 'no predictions available' not in cached_html.lower()
                and 'refreshing this page right now' not in cached_html.lower()
            )
            if _page_usable and _page_age is not None and _page_age < cache_ttl:
                return cached_html
            # Stale-while-revalidate: serve last good HTML while models refresh.
            _stale_max = _SPORT_PREDICTIONS_PAGE_STALE_MAX.get(sport, 900)
            if _page_usable and _page_age is not None and _page_age < _stale_max:
                if _page_age >= cache_ttl:
                    _start_background_predictions_refresh(sport)
                return cached_html
    prediction_error = None
    try:
        predictions = get_upcoming_predictions(sport)
    except Exception as e:
        import traceback as _tb_pred
        logger.error(f"Error loading {sport} predictions: {e}\n{_tb_pred.format_exc()}")
        # Graceful degradation: a live build failure (transient upstream data/
        # model hiccup, cold-start resource spike, ESPN timeout, etc.) must NOT
        # blank the page. Serve the last good slate (memory or persistent disk)
        # if we have one; only show the error banner when nothing is cached.
        predictions = _recover_cached_predictions(sport) or []
        if predictions:
            logger.warning(
                "%s predictions: build failed, serving last cached slate (%d games) instead of error banner.",
                sport, len(predictions),
            )
        else:
            prediction_error = (
                f"{sport} predictions could not be loaded because an upstream data/model dependency failed. "
                "Please refresh in a minute."
            )
    predictions = _filter_exhibition_predictions(predictions)
    if filter_date:
        _seen = {
            (p.get('game_date'), p.get('home_team_id'), p.get('away_team_id'))
            for p in predictions
            if isinstance(p, dict)
        }
        for _dg in _fetch_db_games_for_picks_date(sport, filter_date):
            _key = (_dg.get('game_date'), _dg.get('home_team_id'), _dg.get('away_team_id'))
            if _key not in _seen:
                predictions.append(_dg)
                _seen.add(_key)
    # ===== SECTION: Off-season messaging =====
    # When a sport is out of season with no predictions, surface a friendly
    # "season starts <date>" notice + a link to last season's results instead
    # of a bare "No predictions available".
    offseason_notice = None
    if not predictions and not prediction_error:
        _off_msg = _offseason_message(sport)
        if _off_msg:
            offseason_notice = {
                'message': _off_msg,
                'results_url': '/' + _SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                'results_label': "View last season's results",
            }

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
            'our_spread',
            'our_total',
            'our_home_pts',
            'our_away_pts',
            'xgb_spread',
            'xgb_total',
            'xgb_home_score',
            'xgb_away_score',
            'naive_spread',
            'naive_total',
            'naive_home_score',
            'naive_away_score',
            'h2h_last10_total',
            'h2h_last10_games',
            'h2h_last10_meetings',
            'xsharp_spread',
            'xsharp_total',
            'xsharp_home_score',
            'xsharp_away_score',
            'book_spread',
            'book_total',
            'book_home_moneyline',
            'book_away_moneyline',
            'disp_pl_spread',
            'disp_pl_total',
            'disp_xs_spread',
            'disp_xs_total',
            'disp_book_spread',
            'disp_book_total',
            'pl_model_home_ml',
            'pl_model_away_ml',
            'pl_proj_home_pts',
            'pl_proj_away_pts',
            'xs_proj_home_pts',
            'xs_proj_away_pts',
            'game_time',
        ):
            if _k not in pred:
                if _k == 'h2h_last10_games':
                    pred[_k] = 0
                elif _k == 'h2h_last10_meetings':
                    pred[_k] = []
                else:
                    pred[_k] = None

    soccer_leagues = None
    selected_league = None
    if sport == 'SOCCER':
        predictions, soccer_leagues, selected_league = _filter_soccer_picks(
            predictions, request.args.get('league'),
        )

    try:
        today_date = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        today_date = datetime.now().strftime('%Y-%m-%d')

    # Hydrate book_* from the betting_lines DB synchronously (fast, no HTTP). The
    # live ESPN/DK odds refresh runs OFF the request path below so the page never
    # blocks on dozens of synchronous odds calls (previously ~45s cold loads).
    try:
        _hydrate_book_lines_db_only(sport, predictions)
        for _bp in predictions:
            if isinstance(_bp, dict):
                _ensure_book_moneylines(_bp)
    except Exception as _early_bk:
        logger.debug(f"[{sport}] early book hydrate on picks page: {_early_bk}")

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

    # Group games for the picks page.
    # Soccer always includes completed games from the last 21 days so leagues
    # don't go blank after their season ends — users can still see what was picked.
    # Other sports: upcoming games only, with a 7-day fallback when season is over.
    from collections import defaultdict
    from datetime import date as _date_cls
    _cutoff_21 = (datetime.now() - timedelta(days=21)).strftime('%Y-%m-%d')
    # Keep just-finished games on the slate for a few days so a game does not
    # vanish from the predictions page the moment it goes final.
    _cutoff_recent_final = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')

    grouped_predictions = defaultdict(list)
    for pred in predictions:
        _has_score = pred.get('home_score') is not None
        _is_upcoming = not _has_score
        _pred_gd = pred.get('game_date') or ''
        # Soccer: also show completed games from last 21 days
        _is_soccer_recent = (
            sport == 'SOCCER' and
            _has_score and
            _pred_gd >= _cutoff_21
        )
        # All sports: keep recently-finished games (last 3 days) on the slate so
        # a completed game stays visible instead of disappearing.
        _is_recent_final = _has_score and _pred_gd >= _cutoff_recent_final
        if not _is_upcoming and not _is_soccer_recent and not _is_recent_final:
            continue
        if not pred.get('home_team_id') or not pred.get('away_team_id'):
            continue
        if pred.get('home_team_id') == 'TBD' or pred.get('away_team_id') == 'TBD':
            continue
        date_key = pred.get('game_date') or 'TBD'
        grouped_predictions[date_key].append(pred)

    # For non-soccer (or soccer with no DB results in feed): fallback to last 7 days
    if not grouped_predictions:
        for pred in predictions:
            if pred.get('home_score') is None:
                continue
            if not pred.get('home_team_id') or not pred.get('away_team_id'):
                continue
            if pred.get('home_team_id') == 'TBD' or pred.get('away_team_id') == 'TBD':
                continue
            date_key = pred.get('game_date') or 'TBD'
            if date_key == 'TBD':
                continue
            try:
                _gd = _date_cls.fromisoformat(date_key)
                _td = _date_cls.fromisoformat(today_date)
                if (_td - _gd).days <= 7:
                    grouped_predictions[date_key].append(pred)
            except Exception:
                pass

    # Sort dates — picks UI shows upcoming games; falls back to recent completed games
    sorted_dates, default_pick_date = _picks_display_dates(grouped_predictions, today_date)

    # Filter to specific date if requested (daily SEO pages)
    if filter_date:
        if filter_date in grouped_predictions:
            grouped_predictions = {filter_date: grouped_predictions[filter_date]}
            sorted_dates = [filter_date]
            default_pick_date = filter_date
        else:
            dated_preds = _picks_for_filter_date(predictions, filter_date)
            if dated_preds:
                grouped_predictions = {filter_date: dated_preds}
                sorted_dates = [filter_date]
                default_pick_date = filter_date
            else:
                grouped_predictions = {}
                sorted_dates = []
                default_pick_date = filter_date

    _book_priority = []
    for _dk in sorted_dates:
        _book_priority.extend(grouped_predictions.get(_dk, []))
    try:
        # Re-hydrate from DB (covers rows written since the early hydrate) and kick
        # off the throttled background live-odds refresh for the visible slate.
        _hydrate_book_lines_db_only(sport, predictions)
        for _bp in predictions:
            if isinstance(_bp, dict):
                _ensure_book_moneylines(_bp)
        _start_background_book_refresh(
            sport, predictions, today_date=today_date, prioritize=_book_priority,
        )
    except Exception as _card_bk:
        logger.debug(f"PL book odds on picks page for {sport}: {_card_bk}")

    for pred in predictions:
        if not isinstance(pred, dict):
            continue
        # Prepare each card independently so a single malformed prediction cannot
        # raise and blank out the entire picks page for the sport.
        try:
            for _eff_key in ('our_home_eff', 'our_away_eff'):
                _v = pred.get(_eff_key)
                if isinstance(_v, dict):
                    pred[_eff_key] = types.SimpleNamespace(**_v)
            _finalize_prediction_odds(pred)
            _ens_pre = _safe_float(pred.get('ensemble_prob'))
            if _ens_pre is not None:
                pred['_ensemble_prob_pre_enforce'] = _ens_pre
            _enforce_pick_spread_consistency(pred, sport=sport)
            _prepare_pred_card_display(pred, sport=sport)
            # Finished games kept on the slate still need a card face so the
            # pre-game prediction % renders (the odds/display prep above no-ops
            # once a final score exists).
            if pred.get('home_score') is not None:
                _prepare_pred_card_face(pred, sport=sport)
        except Exception as _card_prep_err:
            logger.warning(
                "Card prep failed for one %s prediction (%s @ %s): %s",
                sport, pred.get('away_team_id'), pred.get('home_team_id'), _card_prep_err,
            )
            continue

    try:
        from flask_login import current_user as _cu
        _pred_li = getattr(_cu, 'is_authenticated', False) and _cu.is_authenticated
    except Exception:
        _pred_li = False

    _render_ctx = dict(
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
        sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
        sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
        predictions=predictions,
        prediction_error=prediction_error,
        grouped_predictions=grouped_predictions,
        sorted_dates=sorted_dates,
        default_pick_date=default_pick_date,
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
        team_logo_url=team_logo_url,
        is_premium=is_premium_user(),
        offseason_notice=offseason_notice,
        robots_meta=_picks_robots_meta(
            sport=sport,
            filter_date=filter_date,
            grouped_predictions=grouped_predictions,
        ),
        canonical_url=_picks_page_canonical_url(
            sport=sport,
            filter_date=filter_date,
            grouped_predictions=grouped_predictions,
        ),
    )
    try:
        if sport == 'GOLF':
            rendered = render_template(
                'golf_predictions.html',
                **_render_ctx
            )
        else:
            rendered = _render_espn_picks_page(**_render_ctx)

    except Exception as _pred_render_err:
        logger.exception(f"Predictions render fallback for {sport} ({filter_date}): {_pred_render_err}")
        return _predictions_fallback_page(sport, filter_date=filter_date)
    _default_games = grouped_predictions.get(default_pick_date, []) if grouped_predictions else []
    _default_with_books = sum(
        1 for g in _default_games
        if isinstance(g, dict) and g.get('book_home_moneyline') is not None
    )
    _books_ok_for_cache = (
        not _default_games
        or _default_with_books >= max(1, len(_default_games) // 2)
    )
    if (
        cache_key
        and rendered
        and grouped_predictions
        and sorted_dates
        and _books_ok_for_cache
        and rendered.count('class="game-card"') >= 1
        and 'no predictions available' not in rendered.lower()
        and 'upstream data/model dependency failed' not in rendered.lower()
    ):
        _trim_cache(_SPORT_PREDICTIONS_PAGE_CACHE, _SPORT_PREDICTIONS_PAGE_TTL.get(sport, 180), max_entries=50)
        _SPORT_PREDICTIONS_PAGE_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
    return rendered

def sport_results(sport):
    """Show model performance results for a sport"""
    season_start_dt = None
    try:
        if sport not in SPORTS:
            return "Sport not found", 404
        if sport == 'SOCCER' and not SOCCER_ENABLED:
            return "Soccer results are temporarily hidden while data loads.", 404

        # New individual sports (Tennis/UFC/Golf) render via their own module pipeline.
        if sport in _SPORT_RESULTS_RENDERERS:
            try:
                _new_sport_html = _SPORT_RESULTS_RENDERERS[sport](sport)
                if _new_sport_html:
                    return _new_sport_html
            except Exception as _new_res_e:
                logger.exception(f"new-sport results render failed for {sport}: {_new_res_e}")

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
                try:
                    overall_stats = compute_overall_stats_from_weekly(weekly_results)
                    daily_results = _daily_results_from_weekly(weekly_results)
                    yesterday_dt = datetime.now() - timedelta(days=1)
                    tally_bundle = _compute_results_tally_bundle(
                daily_results, yesterday_dt, season_start_dt=season_start_dt,
            )
                    daily_tally = tally_bundle['daily_tally']
                    daily_tally_date = tally_bundle['daily_tally_date']
                    daily_tally_games = tally_bundle['daily_tally_games']
                    weekly_tally = tally_bundle['weekly_tally']
                    weekly_tally_date_range = tally_bundle['weekly_tally_date_range']
                    weekly_tally_games = tally_bundle['weekly_tally_games']
                    weekly_start_dt = tally_bundle['weekly_start_dt']
                    weekly_end_dt = tally_bundle['weekly_end_dt']
                    results_stale_notice = tally_bundle['results_stale_notice']
                    _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
                    roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
                    roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
                    roi_total = compute_roi_for_range(daily_results, None, None)
                    roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
                    return render_template_string(
                        NFL_WEEKLY_RESULTS_TEMPLATE,
                        **_results_page_meta(sport),
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
                        roi_cards=roi_cards,
                        results_stale_notice=results_stale_notice,
                    )
                except Exception as nfl_tpl_err:
                    logger.exception(
                        "NFL weekly template render failed; falling back to DB daily cards: %s",
                        nfl_tpl_err,
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
                game_date = _normalize_game_date_key(game['game_date']) or 'Unknown'
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

            yesterday_dt = datetime.now() - timedelta(days=1)
            yesterday = yesterday_dt.strftime('%Y-%m-%d')
            sorted_dates = _recent_result_dates(daily_results, yesterday=yesterday, limit=30)
            overall_stats = compute_overall_stats_from_daily(daily_results)
            _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
            _attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
            _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
            _st_stats = _compute_spread_total_for_daily(sport, daily_results)
            _finalize_daily_result_cards(sport, daily_results)
            season_perf = _build_season_performance_summary(overall_stats, _st_stats)
            tally_bundle = _compute_results_tally_bundle(
                daily_results, yesterday_dt, season_start_dt=season_start_dt,
            )
            daily_tally = tally_bundle['daily_tally']
            daily_tally_date = tally_bundle['daily_tally_date']
            daily_tally_games = tally_bundle['daily_tally_games']
            weekly_tally = tally_bundle['weekly_tally']
            weekly_tally_date_range = tally_bundle['weekly_tally_date_range']
            weekly_tally_games = tally_bundle['weekly_tally_games']
            weekly_start_dt = tally_bundle['weekly_start_dt']
            weekly_end_dt = tally_bundle['weekly_end_dt']
            results_stale_notice = tally_bundle['results_stale_notice']
            roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
            roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
            roi_total = compute_roi_for_range(daily_results, None, None)
            roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
            return render_template_string(
                DAILY_RESULTS_TEMPLATE,
                **_results_page_meta(sport),
                page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                daily_results=daily_results, sorted_dates=sorted_dates,
                today_date=today_date, overall_stats=overall_stats,
                total_over=_ov, total_under=_un, total_games_ou=_gou,
                avg_total=_avg, ou_bench=_bench,
                spread_total_stats=_st_stats,
                season_perf=season_perf,
                daily_tally=daily_tally,
                daily_tally_date=daily_tally_date,
                daily_tally_games=daily_tally_games,
                weekly_tally=weekly_tally,
                weekly_tally_date_range=weekly_tally_date_range,
                weekly_tally_games=weekly_tally_games,
                roi_cards=roi_cards,
                results_stale_notice=results_stale_notice,
                results_snapshot_notice=None,
                soccer_leagues=None
            )
        
        if sport == 'NHL':
            cache_key = f'{sport}_moneyline_results_html_v4'
            cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300)
            cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
            if isinstance(cached_page, dict):
                cached_ts = cached_page.get('ts')
                cached_html = cached_page.get('html')
                if (
                    cached_ts is not None
                    and cached_html
                    and (_time.time() - cached_ts) < cache_ttl
                    and _results_page_html_usable(cached_html)
                ):
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

            now_dt = datetime.now()
            yesterday_dt = now_dt - timedelta(days=1)
            regular_complete = _nhl_regular_season_complete(now_dt)
            results_snapshot_notice = None
            snapshot_raw = (
                _load_nhl_season_snapshot(now_dt, 'regular') if regular_complete else None
            )
            snapshot_stats = _stats_from_nhl_snapshot(snapshot_raw)
            if regular_complete and snapshot_raw and not snapshot_stats:
                logger.warning('NHL season snapshot present but stats extraction failed')

            season_start_dt, season_end_dt = _results_season_bounds('NHL', yesterday_dt)
            season_end_eff = min(season_end_dt, yesterday_dt) if season_end_dt else yesterday_dt
            season_daily = None
            if not snapshot_stats:
                season_daily = _banner_daily_results_for_range(
                    sport, season_start_dt, season_end_eff,
                )
                if not season_daily:
                    if regular_complete:
                        season_daily = defaultdict(lambda: {'games': []})
                        if _os_v2.path.isfile(_nhl_snapshot_json_path(now_dt, 'regular')):
                            snapshot_stats = _stats_from_nhl_snapshot(
                                _load_nhl_season_snapshot(now_dt, 'regular'),
                            )
                        if not snapshot_stats:
                            results_snapshot_notice = (
                                'Frozen 2025-26 regular-season stats are not available on this '
                                'server yet. Season summary will appear after deploy; playoff '
                                'game cards load below when games are graded.'
                            )
                    else:
                        return _results_fallback_page(
                            sport,
                            "NHL results could not be loaded because no completed NHL games "
                            "were available for grading yet.",
                        )

            playoff_daily = None
            playoff_perf = None
            if regular_complete:
                pf_start, pf_end = _nhl_playoff_window(now_dt)
                pf_end_eff = min(pf_end, yesterday_dt)
                if pf_start <= pf_end_eff:
                    playoff_daily = _banner_daily_results_for_range(
                        sport, pf_start, pf_end_eff, playoffs=True,
                    )
                if playoff_daily and _daily_results_game_count(playoff_daily):
                    pf_st = _attach_nhl_display_grading(sport, playoff_daily)
                    pf_overall = compute_overall_stats_from_daily(playoff_daily)
                    playoff_perf = _build_season_performance_summary(
                        pf_overall,
                        pf_st,
                        scope_label='NHL playoffs (live)',
                        games_in_scope=_daily_results_game_count(playoff_daily),
                    )

            if playoff_daily and _daily_results_game_count(playoff_daily):
                daily_results = playoff_daily
            elif snapshot_stats:
                card_start = max(season_start_dt, yesterday_dt - timedelta(days=30))
                daily_results = _banner_daily_results_for_range(
                    sport, card_start, season_end_eff,
                    playoffs=False, skip_v2=True,
                )
                if not daily_results or not _daily_results_game_count(daily_results):
                    daily_results = defaultdict(lambda: {'games': []})
                else:
                    _attach_nhl_display_grading(sport, daily_results)
            else:
                display_start_dt = yesterday_dt - timedelta(days=30)
                daily_results = _subset_daily_results(season_daily, display_start_dt, yesterday_dt)
                if not _daily_results_game_count(daily_results):
                    daily_results = season_daily

            today_date = now_dt.strftime('%Y-%m-%d')

            try:
                yesterday = yesterday_dt.strftime('%Y-%m-%d')
                sorted_dates = _recent_result_dates(daily_results, yesterday=yesterday, limit=7)

                if snapshot_stats:
                    overall_stats = snapshot_stats['overall_stats']
                    _st_stats = snapshot_stats['spread_total_stats']
                    season_perf = snapshot_stats['season_perf']
                    _ov = snapshot_stats['total_over']
                    _un = snapshot_stats['total_under']
                    _gou = snapshot_stats['total_games_ou']
                    _avg = snapshot_stats['avg_total']
                    _bench = snapshot_stats['ou_bench']
                    roi_total = snapshot_stats.get('roi_total') or {}
                else:
                    overall_stats = compute_overall_stats_from_daily(season_daily)
                    _ov, _un, _gou, _avg, _bench = _ou_stats(season_daily, sport)
                    _attach_book_odds_to_daily_results(sport, season_daily, api_limit=300)
                    _cache_market_lines_for_results(sport, season_daily, limit=80)
                    _attach_engine_odds_to_daily_results(sport, season_daily, limit=40)
                    _st_stats = _compute_spread_total_for_daily(sport, season_daily)
                    _finalize_daily_result_cards(sport, season_daily)
                    season_perf = _build_season_performance_summary(
                        overall_stats,
                        _st_stats,
                        scope_label='NHL regular season (Oct–Apr)',
                        games_expected=SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL'),
                        games_in_scope=_nhl_results_games_in_scope(season_daily),
                    )
                    roi_total = compute_roi_for_range(season_daily, None, None)

                if (
                    not snapshot_stats
                    and daily_results is not season_daily
                    and _daily_results_game_count(daily_results)
                ):
                    _attach_nhl_display_grading(sport, daily_results)

                week_start = yesterday_dt - timedelta(days=6)
                week_end = min(yesterday_dt, season_end_eff)
                tally_daily = _banner_daily_results_for_range(
                    sport, week_start, week_end, playoffs=False, skip_v2=True,
                )
                if tally_daily and _daily_results_game_count(tally_daily):
                    _attach_nhl_display_grading(sport, tally_daily)
                elif season_daily and _daily_results_game_count(season_daily):
                    tally_daily = season_daily
                else:
                    tally_daily = daily_results

                tally_bundle = _compute_results_tally_bundle(
                    tally_daily, yesterday_dt, season_start_dt=season_start_dt,
                )
                daily_tally = tally_bundle['daily_tally']
                daily_tally_date = tally_bundle['daily_tally_date']
                daily_tally_games = tally_bundle['daily_tally_games']
                weekly_tally = tally_bundle['weekly_tally']
                weekly_tally_date_range = tally_bundle['weekly_tally_date_range']
                weekly_tally_games = tally_bundle['weekly_tally_games']
                weekly_start_dt = tally_bundle['weekly_start_dt']
                weekly_end_dt = tally_bundle['weekly_end_dt']
                results_stale_notice = tally_bundle['results_stale_notice']
                roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
                roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
                if not snapshot_stats:
                    roi_total = compute_roi_for_range(season_daily, None, None)
                roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)

                rendered = render_template_string(
                    DAILY_RESULTS_TEMPLATE,
                    **_results_page_meta(sport),
                    page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                    sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                    sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                    daily_results=daily_results, sorted_dates=sorted_dates,
                    today_date=today_date, overall_stats=overall_stats,
                    total_over=_ov, total_under=_un, total_games_ou=_gou,
                    avg_total=_avg, ou_bench=_bench,
                    spread_total_stats=_st_stats,
                    season_perf=season_perf,
                    playoff_perf=playoff_perf,
                    results_snapshot_notice=results_snapshot_notice,
                    daily_tally=daily_tally,
                    daily_tally_date=daily_tally_date,
                    daily_tally_games=daily_tally_games,
                    weekly_tally=weekly_tally,
                    weekly_tally_date_range=weekly_tally_date_range,
                    weekly_tally_games=weekly_tally_games,
                    roi_cards=roi_cards,
                    results_stale_notice=results_stale_notice,
                )
                if isinstance(rendered, str) and (
                    (snapshot_stats or _daily_results_game_count(daily_results))
                    and _results_page_html_usable(rendered)
                ):
                    _trim_cache(_SPORT_RESULTS_CACHE, _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
                    _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
                return rendered
            except Exception as e:
                logger.error(f"Error processing NHL results: {e}")
                return f"<h1>NHL results page failed to render because of a processing error: {str(e)}</h1>"
        
        if sport == 'NBA':
            cache_key = f'{sport}_daily_results_html_v8'
            cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)
            cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
            if isinstance(cached_page, dict):
                cached_ts = cached_page.get('ts')
                cached_html = cached_page.get('html')
                if (
                    cached_ts is not None
                    and cached_html
                    and (_time.time() - cached_ts) < cache_ttl
                    and _results_page_html_usable(cached_html)
                ):
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
                sorted_dates = _recent_result_dates(daily_results, yesterday=yesterday, limit=7)
                if not sorted_dates:
                    return _results_fallback_page(
                        sport,
                        "NBA results could not be loaded because no completed games were available for grading yet.",
                    )
                
                overall_stats = compute_overall_stats_from_daily(daily_results)
                _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
                _attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
                _cache_market_lines_for_results(sport, daily_results, limit=150)
                _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
                _st_stats = _compute_spread_total_for_daily(sport, daily_results)
                _finalize_daily_result_cards(sport, daily_results)
                season_perf = _build_season_performance_summary(overall_stats, _st_stats)
                tally_bundle = _compute_results_tally_bundle(
                daily_results, yesterday_dt, season_start_dt=season_start_dt,
            )
                daily_tally = tally_bundle['daily_tally']
                daily_tally_date = tally_bundle['daily_tally_date']
                daily_tally_games = tally_bundle['daily_tally_games']
                weekly_tally = tally_bundle['weekly_tally']
                weekly_tally_date_range = tally_bundle['weekly_tally_date_range']
                weekly_tally_games = tally_bundle['weekly_tally_games']
                weekly_start_dt = tally_bundle['weekly_start_dt']
                weekly_end_dt = tally_bundle['weekly_end_dt']
                results_stale_notice = tally_bundle['results_stale_notice']
                roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
                roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
                roi_total = compute_roi_for_range(daily_results, None, None)
                roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
                rendered = render_template_string(
                    DAILY_RESULTS_TEMPLATE,
                    **_results_page_meta(sport),
                    page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                    sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                    sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                    daily_results=daily_results, sorted_dates=sorted_dates,
                    today_date=today_date, overall_stats=overall_stats,
                    total_over=_ov, total_under=_un, total_games_ou=_gou,
                    avg_total=_avg, ou_bench=_bench,
                    spread_total_stats=_st_stats,
                    season_perf=season_perf,
                    daily_tally=daily_tally,
                    daily_tally_date=daily_tally_date,
                    daily_tally_games=daily_tally_games,
                    weekly_tally=weekly_tally,
                    weekly_tally_date_range=weekly_tally_date_range,
                    weekly_tally_games=weekly_tally_games,
                    roi_cards=roi_cards,
                    results_stale_notice=results_stale_notice,
                    results_snapshot_notice=None,
                )
                if _daily_results_game_count(daily_results) and _results_page_html_usable(rendered):
                    _trim_cache(_SPORT_RESULTS_CACHE, _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
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
            cache_key = f'{sport}_daily_results_html_v3'
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
                    if (
                        cached_ts is not None
                        and cached_html
                        and (_time.time() - cached_ts) < cache_ttl
                        and _results_page_html_usable(cached_html)
                    ):
                        return cached_html
            # Update scores in background so the page is never blocked by API calls.
            # Soccer backfill can take 30-60s (100+ requests); run async always.
            sync_key = f'{sport}_results_score_sync_ts'
            sync_entry = _SPORT_RESULTS_CACHE.get(sync_key)
            sync_last_ts = sync_entry.get('ts') if isinstance(sync_entry, dict) else None
            now_ts = _time.time()
            if sync_last_ts is None or (now_ts - sync_last_ts) >= 600:
                _SPORT_RESULTS_CACHE[sync_key] = {'ts': now_ts}
                import threading as _thr
                _thr.Thread(
                    target=update_espn_scores,
                    args=(sport,),
                    daemon=True,
                    name=f'score-sync-{sport}',
                ).start()
            
            conn = get_db_connection()
            soccer_league_counts = {}
            if sport == 'SOCCER':
                soccer_league_counts = _soccer_curated_league_game_counts(conn)
                if not selected_slug:
                    active_leagues = [
                        lg for lg in SOCCER_LEAGUE_ORDER if soccer_league_counts.get(lg, 0) > 0
                    ]
                    if active_leagues:
                        selected_league = max(
                            active_leagues,
                            key=lambda lg: soccer_league_counts.get(lg, 0),
                        )
                    if not selected_league:
                        selected_league = SOCCER_LEAGUE_ORDER[0] if SOCCER_LEAGUE_ORDER else None
                    if selected_league:
                        cache_key = f'{sport}_daily_results_html_{_soccer_league_slug(selected_league)}'
                completed_games = _fetch_soccer_completed_games(
                    conn, selected_league, SOCCER_RESULTS_GAMES_PER_LEAGUE,
                )
                completed_games = _sort_game_rows_by_date_desc(completed_games)
            else:
                prob_sql = _predictions_prob_select_sql(conn)
                season_start_dt, season_end_dt = _results_season_bounds(sport, datetime.now())
                season_end_sql = season_end_dt.strftime('%Y-%m-%d') if season_end_dt else None
                season_start_sql = season_start_dt.strftime('%Y-%m-%d') if season_start_dt else None
                if season_start_sql and season_end_sql:
                    completed_games = conn.execute(f'''
                        SELECT g.*,
                               {prob_sql}
                        FROM games g
                        LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                        WHERE g.sport = ? AND g.home_score IS NOT NULL
                          AND date(g.game_date) >= ?
                          AND date(g.game_date) <= ?
                        ORDER BY g.game_date DESC
                    ''', (sport, sport, season_start_sql, season_end_sql)).fetchall()
                else:
                    completed_games = conn.execute(f'''
                        SELECT g.*,
                               {prob_sql}
                        FROM games g
                        LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                        WHERE g.sport = ? AND g.home_score IS NOT NULL
                        ORDER BY g.game_date DESC
                        LIMIT 3000
                    ''', (sport, sport)).fetchall()
                completed_games = _sort_game_rows_by_date_desc(completed_games)
                # Bound per-request work so the results page cannot time out
                # (fixes WNBA 502 / MLB slow load). Games are already sorted newest
                # first; the displayed cards and weekly/season tallies use recent
                # games, and this also caps how many live book-odds API calls
                # _attach_book_odds_to_daily_results can attempt.
                if len(completed_games) > 800:
                    completed_games = completed_games[:800]
            conn.close()
            soccer_bundle = None
            if sport == 'SOCCER':
                soccer_bundle = _get_soccer_model_bundle(completed_games, selected_league)
                _soccer_team_names = set()
                for _sg in completed_games:
                    try:
                        _soccer_team_names.add(_sg['home_team_id'])
                        _soccer_team_names.add(_sg['away_team_id'])
                    except Exception:
                        pass
                _soccer_league_code = SOCCER_LEAGUE_ENDPOINTS.get(selected_league) if selected_league else None
                _hydrate_soccer_team_logos(_soccer_team_names, league_code=_soccer_league_code)
            
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
                    game_date = _normalize_game_date_key(game['game_date'])
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

                    # Stored DB probs + frozen v2 backfill (no 21-day live v2 cap).
                    glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = _model_probs_for_grading(
                        sport, game, home_team, away_team, game_date,
                    )

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
                    elif sport == 'SOCCER' and (glicko2_prob is None or trueskill_prob is None):
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
                        'elo_prob':         round(elo_prob  * 100, 1) if elo_prob is not None else None,
                        'xgb_prob':         round(xgb_prob  * 100, 1) if xgb_prob is not None else None,
                        'ens_prob':         round(ens_prob  * 100, 1) if ens_prob is not None else None,
                        'model_data_note':   model_note,
                    }
                    _draw_dec = soccer_pred.get('draw_prob') if soccer_pred else None
                    _apply_soccer_ml_grading(
                        game_info,
                        draw_dec=_draw_dec if sport == 'SOCCER' else None,
                        glicko2_prob=glicko2_prob,
                        trueskill_prob=trueskill_prob,
                        elo_prob=elo_prob,
                        xgb_prob=xgb_prob,
                        ens_prob=ens_prob,
                        home_won=home_won,
                        is_draw=is_draw,
                    )
                    daily_results[game_info['date']]['games'].append(game_info)
                except Exception as _row_err:
                    _gid = None
                    try:
                        _gid = game['game_id']
                    except Exception:
                        pass
                    logger.warning(f"Skipping {sport} results row (game_id={_gid}): {_row_err}")
                    continue

            yesterday_dt = datetime.now() - timedelta(days=1)
            yesterday = yesterday_dt.strftime('%Y-%m-%d')
            if sport == 'SOCCER':
                sorted_dates = _recent_result_dates(
                    daily_results, yesterday=yesterday, limit=60, recent_window_days=90,
                )
            else:
                sorted_dates = _recent_result_dates(daily_results, yesterday=yesterday, limit=30)
            overall_stats = compute_overall_stats_from_daily(daily_results)
            _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
            _attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
            _cache_market_lines_for_results(sport, daily_results, limit=150)
            _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
            _st_stats = _compute_spread_total_for_daily(sport, daily_results)
            _finalize_daily_result_cards(sport, daily_results)
            season_perf = _build_season_performance_summary(overall_stats, _st_stats)
            if _st_stats and int(_st_stats.get('total_graded') or 0) == 0 and int((overall_stats or {}).get('ensemble', {}).get('total') or 0) > 0:
                logger.warning(
                    f"[{sport}] results O/U still 0 graded after book attach "
                    f"(check /data betting_lines totals + pl_book_odds_api on Render)"
                )
            tally_bundle = _compute_results_tally_bundle(
                daily_results, yesterday_dt, season_start_dt=season_start_dt,
            )
            daily_tally = tally_bundle['daily_tally']
            daily_tally_date = tally_bundle['daily_tally_date']
            daily_tally_games = tally_bundle['daily_tally_games']
            weekly_tally = tally_bundle['weekly_tally']
            weekly_tally_date_range = tally_bundle['weekly_tally_date_range']
            weekly_tally_games = tally_bundle['weekly_tally_games']
            weekly_start_dt = tally_bundle['weekly_start_dt']
            weekly_end_dt = tally_bundle['weekly_end_dt']
            results_stale_notice = tally_bundle['results_stale_notice']
            roi_daily = compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
            roi_weekly = compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
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
                        'count': soccer_league_counts.get(lg, 0),
                    }
                    for lg in SOCCER_LEAGUE_ORDER
                    if soccer_league_counts.get(lg, 0) > 0
                ]

            rendered = render_template_string(
                DAILY_RESULTS_TEMPLATE,
                **_results_page_meta(sport),
                page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                daily_results=daily_results, sorted_dates=sorted_dates,
                today_date=today_date, overall_stats=overall_stats,
                total_over=_ov, total_under=_un, total_games_ou=_gou,
                avg_total=_avg, ou_bench=_bench,
                spread_total_stats=_st_stats,
                season_perf=season_perf,
                daily_tally=daily_tally,
                daily_tally_date=daily_tally_date,
                daily_tally_games=daily_tally_games,
                weekly_tally=weekly_tally,
                weekly_tally_date_range=weekly_tally_date_range,
                weekly_tally_games=weekly_tally_games,
                roi_cards=roi_cards,
                soccer_leagues=soccer_leagues,
                results_stale_notice=results_stale_notice,
                results_snapshot_notice=None,
                selected_league=selected_league,
                league_db_total=soccer_league_counts.get(selected_league, 0) if sport == 'SOCCER' else None,
            )
            if _daily_results_game_count(daily_results) and _results_page_html_usable(rendered):
                _trim_cache(_SPORT_RESULTS_CACHE, _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
                _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
            return rendered
        
        performance = calculate_model_performance(sport)
        return render_template_string(
            RESULTS_TEMPLATE,
            **_results_page_meta(sport),
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




# ============================================================================
# NEW SPORTS (Tennis / UFC / Golf) — module wiring + ported results helpers
# Guarded so an import failure can never stop the app from booting.
# ============================================================================

try:
    from sports import SOCCER as _soccer_sport
except Exception as _e_soc:
    _soccer_sport = None
    print(f"⚠️ soccer module for new-sports grading not loaded: {_e_soc}")


try:
    from sports import TENNIS as _tennis_sport
    print("✅ TENNIS import OK")
except Exception as e:
    print(f"❌ TENNIS import failed: {e}")
    _tennis_sport = None


try:
    from sports import UFC as _ufc_sport
    print("✅ UFC import OK")
except Exception as e:
    print(f"❌ UFC import failed: {e}")
    _ufc_sport = None


try:
    from sports import GOLF as _golf_sport
    print("✅ GOLF import OK")
except Exception as e:
    print(f"❌ GOLF import failed: {e}")
    _golf_sport = None


try:
    from sports import WNBA as _wnba_sport
    from sports.WNBA import _apply_wnba_snapshot_flip
    print("✅ WNBA import OK")
except Exception as e:
    print(f"❌ WNBA import failed: {e}")
    _wnba_sport = None

    def _apply_wnba_snapshot_flip(data):
        return data


# Register individual sports after imports complete
_INDIVIDUAL_SPORT_LOADERS = {}

if _tennis_sport:
    _INDIVIDUAL_SPORT_LOADERS[_tennis_sport.SPORT] = _tennis_sport.load_upcoming_games

if _ufc_sport:
    _INDIVIDUAL_SPORT_LOADERS[_ufc_sport.SPORT] = _ufc_sport.load_upcoming_games

if _golf_sport:
    _INDIVIDUAL_SPORT_LOADERS[_golf_sport.SPORT] = _golf_sport.load_upcoming_games


print("INDIVIDUAL LOADERS:", _INDIVIDUAL_SPORT_LOADERS.keys())


# Register results renderers
_SPORT_RESULTS_RENDERERS = {}

if _tennis_sport:
    _SPORT_RESULTS_RENDERERS['TENNIS'] = _tennis_sport.render_sport_results_page

if _ufc_sport:
    _SPORT_RESULTS_RENDERERS['UFC'] = _ufc_sport.render_sport_results_page

if _golf_sport:
    _SPORT_RESULTS_RENDERERS['GOLF'] = _golf_sport.render_sport_results_page

if _wnba_sport:
    _SPORT_RESULTS_RENDERERS['WNBA'] = _wnba_sport.render_sport_results_page


print("✅ New sports (Tennis/UFC/Golf/WNBA) modules loaded")


try:
    from sports import team_efficiency_attach as _eff_attach
except Exception as _eff_imp:
    _eff_attach = None
    print(f"⚠️ team_efficiency_attach not loaded: {_eff_imp}")


# ===== Ported helpers for new sports (Tennis/UFC/Golf) results path =====

def _grade_efficiency_for_results(sport, daily_results) -> None:
    """Per-game Efficiency ML grading on results cards (all grading sports)."""
    if (
        not _eff_attach
        or sport not in _eff_attach.EFFICIENCY_GRADING_SPORTS
        or not daily_results
    ):
        return
    try:
        _eff_attach.grade_efficiency_for_daily_results(sport, daily_results)
    except Exception as exc:
        logger.debug(f"[eff] results grading failed for {sport}: {exc}")


def _load_sport_season_snapshot(sport, phase='regular'):
    """Load newest committed season JSON for a sport (no live regrade)."""
    snap_dir = _all_sports_snapshot_dir()
    pattern = _os_v2.path.join(snap_dir, f'{sport}_*_{phase}.json')
    paths = sorted(glob.glob(pattern), reverse=True)
    for path in paths:
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug('Season snapshot read failed (%s): %s', path, exc)
            continue
        if isinstance(data, dict) and data.get('sport') == sport:
            return _apply_wnba_snapshot_flip(data) if sport == 'WNBA' else data
    return None


def _merge_snapshot_efficiency_into_overall(overall_stats, sport):
    """Promote frozen-snapshot model stats when live grading covered fewer games.

    Previously this only promoted Efficiency, which left the OTHER models showing
    a tiny live-graded sample next to a season-sized Efficiency record — e.g. the
    soccer results page (league-scoped, ~5 graded games per model) showed
    Efficiency 1630-436 but Grinder2/Takedown/Edge/XSharp/Consensus at 0-5. Now
    EVERY model is promoted from the snapshot when the snapshot graded more games,
    so the whole panel is consistent. For sports that already render from the
    snapshot, current == snapshot, so this is a no-op."""
    snap = _load_sport_season_snapshot(sport)
    if not snap:
        return overall_stats
    snap_overall = snap.get('overall_stats') or {}
    if not isinstance(snap_overall, dict):
        return overall_stats
    stats = dict(overall_stats or {})
    # snap_overall is already WNBA-flipped by _load_sport_season_snapshot, so no
    # extra flip here (that would double-flip).
    for model_key, snap_m in snap_overall.items():
        if not isinstance(snap_m, dict):
            continue
        snap_total = int(snap_m.get('total') or 0)
        if snap_total <= 0:
            continue
        cur = (overall_stats or {}).get(model_key) or {}
        cur_total = int(cur.get('total') or 0)
        if snap_total <= cur_total:
            continue
        snap_correct = int(snap_m.get('correct') or 0)
        stats[model_key] = {
            'correct': snap_correct,
            'total': snap_total,
            'accuracy': round(snap_correct / snap_total * 100, 1) if snap_total else 0.0,
        }
    return stats


def _results_date_query_active():
    if not has_request_context():
        return False
    return bool((request.args.get('date') or '').strip())


def _results_page_date_kwargs(daily_results, sorted_dates):
    """Template kwargs for results date picker (?date= filter)."""
    view_daily, view_dates, selected, available = _apply_results_date_filter(
        daily_results, sorted_dates,
    )
    return {
        'daily_results': view_daily,
        'sorted_dates': view_dates,
        'available_dates': available,
        'selected_results_date': selected,
    }


def _start_background_score_sync(sport, sync_fn=None):
    """Fire-and-forget score sync — never block page render on ESPN."""
    sync_key = f'{sport}_results_score_sync_ts'
    sync_entry = _SPORT_RESULTS_CACHE.get(sync_key)
    sync_last_ts = sync_entry.get('ts') if isinstance(sync_entry, dict) else None
    now_ts = _time.time()
    if sync_last_ts is not None and (now_ts - sync_last_ts) < 600:
        return
    _SPORT_RESULTS_CACHE[sync_key] = {'ts': now_ts}
    if sync_fn is None:
        if sport == 'NHL':
            sync_fn = update_nhl_scores
        else:
            sync_fn = lambda s=sport: update_espn_scores(s)
    import threading as _thr
    _thr.Thread(
        target=sync_fn,
        daemon=True,
        name=f'score-sync-{sport}',
    ).start()


ALL_SPORTS_DASHBOARD_SPORTS = [
    'NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'SOCCER',
    'TENNIS', 'UFC', 'GOLF',
]
_ML_DASHBOARD_MODELS = (
    ('glicko2', 'Grinder2'),
    ('trueskill', 'Takedown'),
    ('elo', 'Edge'),
    ('xgboost', 'XSharp'),
    ('ensemble', 'Sharp Consensus'),
    ('efficiency', 'Efficiency'),
)


def _stale_page_cache_get(cache_dict: dict, cache_key: str, ttl: float):
    """Return (html, needs_revalidate). Serves stale HTML up to 5× TTL under load."""
    entry = cache_dict.get(cache_key)
    if not isinstance(entry, dict):
        return None, False
    html = entry.get('html')
    ts = entry.get('ts')
    if not html or ts is None:
        return None, False
    age = _time.time() - ts
    if age < ttl:
        return html, False
    if age < ttl * _STALE_PAGE_TTL_MULTIPLIER:
        return html, True
    return None, False


def _normalize_overall_stats(raw):
    """Ensure committed snapshot overall_stats matches live grading shape."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, m in raw.items():
        if not isinstance(m, dict):
            continue
        total = int(m.get('total') or 0)
        correct = int(m.get('correct') or 0)
        acc = m.get('accuracy')
        if acc is None:
            acc = round(correct / total * 100, 1) if total > 0 else 0.0
        else:
            acc = round(float(acc), 1)
        out[key] = {'correct': correct, 'total': total, 'accuracy': acc}
    return out


def _stats_from_season_snapshot(snapshot):
    if not snapshot:
        return None
    ou = snapshot.get('ou_summary') or {}
    overall_stats = _normalize_overall_stats(snapshot.get('overall_stats') or {})
    spread_total_stats = snapshot.get('spread_total_stats') or {}
    old_perf = snapshot.get('season_perf') or {}
    season_perf = _build_season_performance_summary(
        overall_stats,
        spread_total_stats,
        scope_label=old_perf.get('scope_label'),
        games_expected=snapshot.get('games_expected'),
        games_in_scope=snapshot.get('games_in_scope'),
    )
    return {
        'overall_stats': overall_stats,
        'spread_total_stats': spread_total_stats,
        'season_perf': season_perf,
        'total_over': ou.get('total_over', 0),
        'total_under': ou.get('total_under', 0),
        'total_games_ou': ou.get('total_games_ou', 0),
        'avg_total': ou.get('avg_total', 0),
        'ou_bench': ou.get('ou_bench', 0),
        'roi_total': snapshot.get('roi_total'),
    }


_stats_from_nhl_snapshot = _stats_from_season_snapshot


# ===== Ported results helpers for new sports (transitive deps) =====


def _apply_results_date_filter(daily_results, sorted_dates):
    """Honor ?date= on results pages — single-day view when valid."""
    available_dates = _all_result_dates_sorted(daily_results)
    if not has_request_context():
        return daily_results, sorted_dates, None, available_dates
    raw = (request.args.get('date') or '').strip()
    if not raw:
        return daily_results, sorted_dates, None, available_dates
    key = _resolve_results_date_key(daily_results, raw)
    if not key:
        return daily_results, sorted_dates, None, available_dates
    games = daily_results.get(key, {}).get('games') or []
    filtered = {key: {'games': list(games)}}
    return filtered, [key], key, available_dates


def _resolve_results_date_key(daily_results, date_str):
    """Match ?date=YYYY-MM-DD to a daily_results bucket key."""
    if not date_str or not daily_results:
        return None
    want = _normalize_game_date_key(date_str) or str(date_str).strip()
    if want in daily_results and daily_results[want].get('games'):
        return want
    for dk, bucket in daily_results.items():
        if not bucket.get('games'):
            continue
        if (_normalize_game_date_key(dk) or dk) == want:
            return dk
    return None


def _all_result_dates_sorted(daily_results):
    """All calendar buckets with games, newest first (for results date dropdown)."""
    if not daily_results:
        return []

    def _date_key_dt(dk):
        return parse_date(dk) or datetime.min

    keys = []
    for dk, bucket in daily_results.items():
        if dk and bucket.get('games'):
            keys.append(_normalize_game_date_key(dk) or dk)
    return sorted(set(keys), key=_date_key_dt, reverse=True)


def _dated_games_in_daily_results(daily_results, *, season_start_dt=None, before_dt=None):
    """Sorted (date, date_key) pairs that have at least one gradable (non-exhibition) game."""
    dated = []
    for dk, bucket in (daily_results or {}).items():
        if not dk or not bucket:
            continue
        dt = parse_date(dk)
        if not dt:
            continue
        if not _gradable_result_games(bucket.get('games')):
            continue
        dated.append((dt, dk))
    if season_start_dt:
        dated = [(dt, dk) for dt, dk in dated if dt >= season_start_dt]
    if before_dt is not None:
        dated = [(dt, dk) for dt, dk in dated if dt <= before_dt]
    dated.sort(key=lambda x: x[0], reverse=True)
    return dated


# ============================================================================

# Ported footer pages: /blog, /edge-performance, /results/downloads

# ============================================================================


# --- constants/templates ---

BLOG_ARCHIVE_TEMPLATE = """{% extends "base.html" %}
{% block title %}Prediction Lab Blog | predictionlab.io{% endblock %}
{% block head_meta %}
    <meta name="description" content="Daily sports news, AI-generated betting insights, game previews, market breakdowns, and model analysis from predictionlab.io.">
    <meta name="robots" content="noindex,follow">
    <link rel="canonical" href="{{ site_domain }}/blog">
{% endblock %}
{% block extra_styles %}
    <style>
        .blog-page{line-height:1.65}
        .blog-page .posts a{color:#00529B;text-decoration:none;font-weight:800}
        .blog-page .posts a:hover{text-decoration:underline}
        .blog-page .top{margin-bottom:26px}
        .blog-page .eyebrow{display:inline-flex;background:#fbbf24;color:#000;border-radius:999px;padding:4px 10px;font-size:0.74rem;font-weight:900;letter-spacing:0.4px;text-transform:uppercase;margin-bottom:12px}
        .blog-page h1{font-size:clamp(2rem,5vw,3rem);line-height:1.08;margin-bottom:12px}
        .blog-page .sub{color:#334155;font-size:1rem;max-width:720px}
        .blog-page .posts{display:grid;gap:16px;margin-top:24px}
        .blog-page .posts details.blog-post-item{border:1px solid rgba(15,23,42,0.14);border-radius:14px;background:#fff;padding:0;box-shadow:0 8px 24px rgba(15,23,42,0.05);overflow:hidden}
        .blog-page .posts details.blog-post-item[open]{border-color:rgba(0,82,155,0.35)}
        .blog-page .posts summary{list-style:none;cursor:pointer;padding:20px}
        .blog-page .posts summary::-webkit-details-marker{display:none}
        .blog-page .posts summary::after{content:'+';float:right;font-size:1.4rem;line-height:1;color:#64748b;font-weight:400}
        .blog-page .posts details[open] summary::after{content:'−'}
        .blog-page .posts .blog-post-body{padding:0 20px 20px;border-top:1px solid rgba(15,23,42,0.08)}
        .blog-page .posts .blog-post-body p{color:#334155;margin-bottom:14px}
        .blog-page .posts .blog-post-body p:last-child{margin-bottom:0}
        .blog-page .posts .blog-post-title{display:block;font-size:1.22rem;line-height:1.35;margin:0 0 8px;color:#0f172a;font-weight:800}
        .blog-page .posts .news-list{margin-top:16px;padding-top:14px;border-top:1px solid rgba(15,23,42,0.08)}
        .blog-page .posts .news-list h3{font-size:0.95rem;margin:0 0 10px;color:#0f172a}
        .blog-page .posts .news-list a{display:block;margin-bottom:8px;color:#00529B;font-weight:700;text-decoration:none}
        .blog-page .posts .news-list a:hover{text-decoration:underline}
        .blog-page .posts .meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;color:#64748b;font-size:0.84rem;font-weight:700}
        .blog-page .posts .tag{background:#f8fafc;border:1px solid rgba(15,23,42,0.12);color:#0f172a;border-radius:999px;padding:2px 8px;font-size:0.72rem;font-weight:900;text-transform:uppercase}
        .blog-page .posts .blog-excerpt{color:#334155;margin:0}
        .blog-page .back{display:inline-flex;margin-bottom:24px}
        #soro-blog{min-height:480px;margin-top:8px}
        .blog-section{margin-top:40px}
        .blog-section-title{font-size:1.35rem;color:#0f172a;margin:0 0 16px;padding-bottom:10px;border-bottom:2px solid rgba(15,23,42,0.08)}
    </style>
{% endblock %}
{% block content %}
<div class="blog-page">
    <a class="back" href="/">← Back to PredictionLab</a>
    <header class="top">
        <span class="eyebrow">Daily articles</span>
        <h1>Prediction Lab Blog</h1>
        <p class="sub">Game-day previews for today's slate — matchups, times, and links to Prediction Lab model picks.</p>
    </header>
    <section class="blog-section" aria-label="Soro blog feed">
        <h2 class="blog-section-title">Trending in Sports</h2>
        <div id="soro-blog"></div>
        <script src="https://app.trysoro.com/api/embed/7713f25e-b95b-4eb2-a414-ec63a136d16f" defer></script>
    </section>
    <section class="blog-section" aria-label="Prediction Lab articles">
        <h2 class="blog-section-title">Latest from Prediction Lab</h2>
        <div class="posts">
        {% for post in posts %}
        <details class="blog-post-item" id="{{ post.slug }}">
            <summary>
                <div class="meta"><span class="tag">{{ post.sport_tag }}</span><time datetime="{{ post.date }}">{{ post.display_date }}</time></div>
                <span class="blog-post-title">{{ post.title }}</span>
                <p class="blog-excerpt">{{ post.excerpt }}</p>
            </summary>
            <div class="blog-post-body">
                {% for paragraph in post.body %}
                <p>{{ paragraph }}</p>
                {% endfor %}
                {% if post.news_items %}
                <div class="news-list">
                    <h3>Related coverage</h3>
                    {% for item in post.news_items %}
                    {% if item.url and item.source != 'Google Trends' and 'trends.google' not in (item.url or '') and 'Google Trends' not in (item.topic or '') %}
                    <a href="{{ item.url }}" target="_blank" rel="noopener noreferrer">{{ item.sport }}: {{ item.topic }}</a>
                    {% endif %}
                    {% endfor %}
                </div>
                {% endif %}
            </div>
        </details>
        {% endfor %}
        </div>
    </section>
</div>
<script>
(function () {
    var hash = (window.location.hash || '').replace(/^#/, '');
    if (!hash) return;
    var target = document.getElementById(hash);
    if (target && target.tagName === 'DETAILS') {
        target.open = true;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
})();
</script>
{% endblock %}"""

DOWNLOADS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
        .dl-wrap{max-width:920px;margin:0 auto;padding:24px 0 60px;}
        .dl-head{text-align:center;margin-bottom:24px;}
        .dl-head h1{font-size:2em;color:#0f172a;margin-bottom:8px;}
        .dl-head p{color:#334155;line-height:1.6;max-width:640px;margin:0 auto;}
        .dl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;}
        .dl-card{background:#fff;border:1px solid #cbd5e1;border-radius:14px;padding:18px 18px 16px;box-shadow:0 4px 14px rgba(15,23,42,0.06);}
        .dl-card-top{display:flex;align-items:center;gap:10px;margin-bottom:12px;}
        .dl-icon{font-size:1.6em;}
        .dl-name{font-weight:800;color:#0f172a;font-size:1.05em;}
        .dl-btns{display:flex;flex-direction:column;gap:8px;}
        .dl-btn{display:flex;align-items:center;justify-content:center;gap:6px;text-decoration:none;font-weight:700;font-size:0.86em;border-radius:10px;padding:9px 12px;transition:background .15s,border-color .15s;}
        .dl-btn.results{background:#00529B;color:#fff;}
        .dl-btn.results:hover{background:#0466c4;}
        .dl-btn.picks{background:#fff;color:#0f172a;border:1px solid rgba(15,23,42,0.25);}
        .dl-btn.picks:hover{border-color:#00529B;color:#00529B;}
        .dl-allrow{text-align:center;margin-top:22px;}
        .dl-all{display:inline-flex;align-items:center;gap:6px;text-decoration:none;font-weight:700;font-size:0.86em;color:#475569;border:1px solid rgba(15,23,42,0.2);border-radius:10px;padding:9px 16px;}
        .dl-all:hover{border-color:#00529B;color:#00529B;}
        .dl-note{text-align:center;color:#64748b;font-size:0.8em;margin-top:14px;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="dl-wrap">
        <div class="dl-head">
            <h1>Download Results &amp; Picks (CSV)</h1>
            <p>Export season-to-date model results or pick history for any sport as a CSV file — ready for Excel, Google Sheets, or your own analysis.</p>
        </div>
        <div class="dl-grid">
            {% for s in download_sports %}
            <div class="dl-card">
                <div class="dl-card-top">
                    <span class="dl-icon">{{ s.icon }}</span>
                    <span class="dl-name">{{ s.name }}</span>
                </div>
                <div class="dl-btns">
                    <a class="dl-btn results" href="/results/export.csv?sport={{ s.key }}">⬇ Results CSV</a>
                    <a class="dl-btn picks" href="/picks/export.csv?sport={{ s.key }}">⬇ Picks CSV</a>
                </div>
            </div>
            {% endfor %}
        </div>
        <div class="dl-allrow">
            <a class="dl-all" href="/results/export.csv">⬇ Download ALL sports (combined results CSV)</a>
        </div>
        <p class="dl-note">CSV downloads require a premium account. Files reflect completed, graded games.</p>
    </div>
""")

EDGE_PERFORMANCE_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
        .edge-wrap{max-width:960px;margin:0 auto;padding:18px 0 60px;}
        .edge-head h1{font-size:1.7rem;color:#0f172a;margin:0 0 6px;}
        .edge-head p{color:#475569;font-size:0.92rem;margin:0 0 16px;line-height:1.6;}
        .edge-filters{display:flex;gap:10px;align-items:end;margin-bottom:18px;flex-wrap:wrap;}
        .edge-filters select{min-width:160px;padding:9px 10px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;color:#0f172a;font-weight:600;}
        .edge-filters button{padding:10px 16px;border-radius:10px;border:none;background:#00529B;color:#fff;font-weight:800;cursor:pointer;}
        .edge-card{background:#fff;border:1px solid #E0E4E8;border-radius:14px;padding:18px;box-shadow:0 4px 16px rgba(15,23,42,0.06);margin-bottom:16px;}
        .edge-card h2{font-size:1.05rem;color:#0f172a;margin:0 0 12px;}
        .edge-table{width:100%;border-collapse:collapse;font-size:0.88rem;}
        .edge-table th{text-align:left;padding:8px 10px;font-size:0.7rem;text-transform:uppercase;letter-spacing:.5px;color:#64748b;border-bottom:2px solid #E0E4E8;}
        .edge-table th:not(:first-child),.edge-table td:not(:first-child){text-align:center;}
        .edge-table td{padding:9px 10px;border-bottom:1px solid #f1f5f9;color:#0f172a;}
        .edge-table tr.small td{opacity:0.55;}
        .wr-good{color:#059669;font-weight:800;}.wr-mid{color:#d97706;font-weight:800;}.wr-bad{color:#dc2626;font-weight:800;}
        .roi-pos{color:#059669;font-weight:800;}.roi-neg{color:#dc2626;font-weight:800;}
        .small-tag{font-size:0.68rem;color:#b45309;font-weight:700;margin-left:4px;}
        .bar-row{display:flex;align-items:center;gap:10px;margin-bottom:8px;}
        .bar-label{width:70px;font-size:0.8rem;font-weight:700;color:#334155;flex-shrink:0;}
        .bar-track{flex:1;background:#f1f5f9;border-radius:6px;height:22px;overflow:hidden;position:relative;}
        .bar-fill{height:100%;border-radius:6px;}
        .bar-val{width:90px;text-align:right;font-size:0.8rem;font-weight:700;flex-shrink:0;}
        .edge-empty{color:#64748b;font-size:0.9rem;padding:20px;text-align:center;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="edge-wrap">
        <div class="edge-head">
            <h1>Edge Value Performance</h1>
            <p>How reliable is our Edge % signal? This shows the real win rate and ROI for completed picks at each edge level, per sport — so you can see whether higher edge has actually meant better results.</p>
        </div>
        <form method="GET" action="/edge-performance" class="edge-filters">
            <label>Sport
                <select name="sport" onchange="this.form.submit()">
                    {% for s in sports %}<option value="{{ s }}" {% if s == league %}selected{% endif %}>{{ s }}</option>{% endfor %}
                </select>
            </label>
            <button type="submit">Apply</button>
        </form>

        {% if edge_perf.graded < 1 %}
        <div class="edge-card"><div class="edge-empty">No completed {{ league }} picks have been graded yet. This page fills in automatically as games finish — no estimated or simulated numbers are shown.</div></div>
        {% else %}
        <div class="edge-card">
            <h2>Edge Performance — {{ league }} ({{ edge_perf.graded }} graded picks)</h2>
            <table class="edge-table">
                <thead><tr><th>Edge Range</th><th>Win Rate</th><th>ROI</th><th>Sample Size</th></tr></thead>
                <tbody>
                {% for b in edge_perf.edge_table %}
                    <tr class="{{ 'small' if b.small else '' }}">
                        <td style="font-weight:700;">{{ b.bucket }}</td>
                        <td>{% if b.win_rate is not none %}<span class="{{ 'wr-good' if b.win_rate>=55 else 'wr-mid' if b.win_rate>=50 else 'wr-bad' }}">{{ b.win_rate }}%</span>{% else %}—{% endif %}</td>
                        <td>{% if b.roi is not none %}<span class="{{ 'roi-pos' if b.roi>0 else 'roi-neg' }}">{{ '+' if b.roi>0 else '' }}{{ b.roi }}%</span>{% else %}—{% endif %}</td>
                        <td>{{ b.sample }}{% if b.small %}<span class="small-tag">⚠ small sample</span>{% endif %}</td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>

        <div class="edge-card">
            <h2>Edge vs ROI</h2>
            {% set maxabs = 1 %}
            {% for b in edge_perf.edge_table %}{% if b.roi is not none and (b.roi|abs) > maxabs %}{% set maxabs = b.roi|abs %}{% endif %}{% endfor %}
            {% for b in edge_perf.edge_table %}
            <div class="bar-row">
                <div class="bar-label">{{ b.bucket }}</div>
                <div class="bar-track">
                    {% if b.roi is not none %}<div class="bar-fill" style="width:{{ ((b.roi|abs) / maxabs * 100)|round(0,'floor') }}%;background:{{ '#059669' if b.roi>0 else '#dc2626' }};"></div>{% endif %}
                </div>
                <div class="bar-val">{% if b.roi is not none %}{{ '+' if b.roi>0 else '' }}{{ b.roi }}% ROI{% else %}no data{% endif %}</div>
            </div>
            {% endfor %}
            <p style="color:#64748b;font-size:0.8rem;margin:8px 0 0;">Shows whether ROI scales with edge or breaks down at high edge values. Specific to {{ league }}.</p>
        </div>

        <div class="edge-card">
            <h2>Edge Distribution</h2>
            <p style="color:#64748b;font-size:0.82rem;margin:0 0 12px;">What share of completed {{ league }} picks fell into each edge range.</p>
            {% for d in edge_perf.edge_distribution %}
            <div class="bar-row">
                <div class="bar-label">{{ d.bucket }}</div>
                <div class="bar-track"><div class="bar-fill" style="width:{{ d.pct or 0 }}%;background:#2563eb;"></div></div>
                <div class="bar-val">{{ d.pct if d.pct is not none else 0 }}% ({{ d.count }})</div>
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>
""")

_BLOG_CACHE_TTL = 300

_BLOG_NEWS_CACHE_TTL = 900

_BLOG_POSTS_FILE = _os.path.join(_BASE_DIR, 'data', 'blog_posts.json')
# Append-only quarantine sink — Render disk rewrite on /blog load dumps spam here.
_BLOG_TRENDS_QUARANTINE_FILE = _os.path.join(
    _BASE_DIR, 'data', 'blog_posts_quarantine_trends.json'
)

# OWNER (2026-08-03): Google Trends → blog auto-publish is PERMANENTLY OFF.
# Category-17 CA Trends RSS returned non-sports queries (Disney, stocks, weather…)
# wrapped in a “Google Trends Betting Angle / after N+ Google searches” template.
# Do NOT re-enable. Game-day previews (MLB/WNBA/UFC) replace that path.
# Shipping this to predictionlab.io requires owner Manual Deploy — agents never push.
# Even if this flag is flipped True, _fetch_google_trends / _generate_trend_blog_post
# still return empty — Trends titles must never be recreated.
# CRITICAL: every /blog load also rewrites data/blog_posts.json on disk so an old
# Render volume JSON cannot keep serving Trends spam after code-only deploys.
_BLOG_AUTO_TRENDS_ENABLED = False

# In-season US-popular sports that may auto-publish game-day preview posts.
# Tennis / ATP / Washington Open / etc. are NOT in this list — no Trends leftovers.
# Expand later (NBA/NFL/NHL/CFB/tennis) only when those leagues are live and reviewed.
_BLOG_GAME_DAY_SPORTS = ('MLB', 'WNBA', 'UFC')

_BLOG_GAME_DAY_ESPN = {
    'MLB': ('baseball', 'mlb'),
    'WNBA': ('basketball', 'wnba'),
    'UFC': ('mma', 'ufc'),
}

_BLOG_GAME_DAY_AUTO_PUBLISH = True
_BLOG_GAME_DAY_MAX_POSTS = 24

_CONF_BUCKETS = [
    ("90-100%", 90, 1000), ("85-89%", 85, 90), ("80-84%", 80, 85),
    ("75-79%", 75, 80), ("70-74%", 70, 75), ("65-69%", 65, 70),
    ("Below 65%", -1, 65),
]

_EDGE_BUCKETS = [
    ("0–5%", 0, 5), ("5–10%", 5, 10), ("10–20%", 10, 20),
    ("20–30%", 20, 30), ("30–40%", 30, 40), ("40%+", 40, 1e9),
]

_EDGE_DIST_BUCKETS = [
    ("0–10%", 0, 10), ("10–20%", 10, 20),
    ("20–30%", 20, 30), ("30%+", 30, 1e9),
]

_ESPN_NEWS_FEEDS = [
    ('MLB', 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/news'),
    ('NBA', 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news'),
    ('NFL', 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/news'),
    ('NHL', 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news'),
    ('WNBA', 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/news'),
    ('NCAAB', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/news'),
    ('NCAAF', 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/news'),
]

_EV_BUCKETS = [
    ("+30% and above", 30, 1e9), ("+20% to +30%", 20, 30),
    ("+10% to +20%", 10, 20), ("0% to +10%", 0, 10),
    ("Negative EV", -1e9, 0),
]

# Retired — was only used by Trends→blog. Kept empty so nothing can pin Trends topics.
_PINNED_SPORTS_TRENDS_20260616: list[dict] = []


# --- helper functions ---

def _blog_template_posts():
    posts = _get_blog_posts(include_generated=True)
    return [{**p, 'display_date': _blog_display_date(p)} for p in posts]

def _blog_today_et():
    try:
        return datetime.now(ZoneInfo('America/New_York'))
    except Exception:
        return datetime.now()

def _is_google_trends_blog_spam(post: dict) -> bool:
    """True for Trends-template filler — never show or persist."""
    if not isinstance(post, dict):
        return False
    slug = str(post.get('slug') or '').lower()
    title = str(post.get('title') or '').lower()
    excerpt = str(post.get('excerpt') or '')
    source = str(post.get('source') or '')
    topic = str(post.get('topic') or '')
    sport_tag = str(post.get('sport_tag') or post.get('sport') or '')
    body_parts: list[str] = []
    body = post.get('body') or post.get('content') or []
    if isinstance(body, list):
        body_parts = [str(p) for p in body]
    elif isinstance(body, str):
        body_parts = [body]
    body_blob = '\n'.join(body_parts)
    news_blob = ''
    news = post.get('news_items') or []
    if isinstance(news, list):
        news_blob = json.dumps(news, ensure_ascii=False)
    # Full raw dump catches nested/odd shapes Render may still have on disk.
    try:
        raw_blob = json.dumps(post, ensure_ascii=False)
    except Exception:
        raw_blob = ''
    hay = (
        f'{slug}\n{title}\n{excerpt}\n{source}\n{topic}\n{sport_tag}\n'
        f'{body_blob}\n{news_blob}\n{raw_blob}'
    ).lower()
    if 'google-trends-betting-angle' in slug or 'google-trends-betting-angle' in hay:
        return True
    if 'google trends' in hay:
        return True
    if 'betting angle' in title or 'betting angle' in slug or 'betting angle' in hay:
        return True
    if 'trends.google.com' in hay or 'trends.google' in hay:
        return True
    if str(source).strip().lower() == 'google trends':
        return True
    if 'sports searches moving fastest' in hay:
        return True
    if 'google searches is one of the sports searches' in hay:
        return True
    if re.search(r'after\s+\d+\+?\s+google searches', hay):
        return True
    if 'temporary trend feed' in hay:
        return True
    return False


def _append_trends_blog_quarantine(spam_posts: list) -> None:
    """Append purged Trends posts to quarantine JSON (never user-facing)."""
    if not spam_posts:
        return
    try:
        existing: list = []
        if _os.path.exists(_BLOG_TRENDS_QUARANTINE_FILE):
            with open(_BLOG_TRENDS_QUARANTINE_FILE, encoding='utf-8') as fh:
                payload = json.load(fh)
            if isinstance(payload, dict):
                existing = list(payload.get('posts') or [])
            elif isinstance(payload, list):
                existing = list(payload)
        seen = {
            str((p or {}).get('slug') or '')
            for p in existing
            if isinstance(p, dict)
        }
        for post in spam_posts:
            if not isinstance(post, dict):
                continue
            slug = str(post.get('slug') or '')
            if slug and slug in seen:
                continue
            existing.append(post)
            if slug:
                seen.add(slug)
        _os.makedirs(_os.path.dirname(_BLOG_TRENDS_QUARANTINE_FILE), exist_ok=True)
        with open(_BLOG_TRENDS_QUARANTINE_FILE, 'w', encoding='utf-8') as fh:
            json.dump(
                {
                    'quarantined_at_note': (
                        'Google Trends auto-blog spam. Not user-facing. '
                        'Appended by _purge_google_trends_from_blog_disk on /blog load.'
                    ),
                    'posts': existing,
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )
            fh.write('\n')
    except Exception as exc:
        logger.warning(f"Blog Trends quarantine write failed: {exc}")


def _purge_google_trends_from_blog_disk() -> int:
    """CRITICAL for Render: strip Trends spam from data/blog_posts.json on disk.

    Called on every /blog load. Filters in-memory alone is not enough — an old
    Render persistent JSON would otherwise keep the spam forever after a code
    deploy. Rewrites the file whenever any Trends / Betting Angle post remains.
    Returns number of posts removed. Idempotent when already clean.
    """
    path = _BLOG_POSTS_FILE
    if not _os.path.exists(path):
        return 0
    try:
        with open(path, encoding='utf-8') as fh:
            raw_text = fh.read()
        payload = json.loads(raw_text) if raw_text.strip() else {'posts': []}
    except Exception as exc:
        logger.warning(f"Blog Trends disk purge: read failed: {exc}")
        return 0

    items = payload.get('posts', payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return 0

    clean: list = []
    spam: list = []
    for raw in items:
        if isinstance(raw, dict) and _is_google_trends_blog_spam(raw):
            spam.append(raw)
        elif isinstance(raw, dict):
            clean.append(raw)
        # drop non-dicts silently

    # Nuclear text pass: if markers remain in the file even after per-post filter,
    # force-drop any item whose serialized form still matches.
    markers = (
        'google-trends-betting-angle',
        'google trends betting angle',
        'google trends',
        'betting angle',
        'trends.google',
        'sports searches moving fastest',
    )
    lower_file = raw_text.lower()
    if not spam and any(m in lower_file for m in markers):
        clean2: list = []
        spam2: list = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                blob = json.dumps(raw, ensure_ascii=False).lower()
            except Exception:
                blob = str(raw).lower()
            if any(m in blob for m in markers):
                spam2.append(raw)
            else:
                clean2.append(raw)
        if spam2:
            clean, spam = clean2, spam2

    if not spam:
        return 0

    _append_trends_blog_quarantine(spam)
    try:
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        # Serialize only clean posts (legacy daily digests also stripped here).
        serializable = []
        for post in clean:
            if not isinstance(post, dict):
                continue
            slug = str(post.get('slug') or '')
            if _is_legacy_daily_blog_slug(slug):
                continue
            if _is_google_trends_blog_spam(post):
                continue
            serializable.append(post)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'posts': serializable}, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
        # Bust cache so the next load sees the rewritten file.
        _BLOG_CACHE.update({'ts': 0, 'posts': []})
        logger.warning(
            "Purged %s Google Trends blog post(s) from %s (quarantined)",
            len(spam),
            path,
        )
    except Exception as exc:
        logger.warning(f"Blog Trends disk purge: rewrite failed: {exc}")
        return 0
    return len(spam)


def _get_blog_posts(include_generated=True, todays_picks=None) -> list[dict]:
    # Unavoidable on every blog assemble: rewrite Render disk if Trends remain.
    purged = _purge_google_trends_from_blog_disk()
    posts = [p for p in _load_blog_posts_from_json() if not _is_google_trends_blog_spam(p)]
    if include_generated and _BLOG_GAME_DAY_AUTO_PUBLISH:
        by_slug = {p['slug']: p for p in posts}
        today_dt = _blog_today_et()
        today_str = today_dt.strftime('%Y-%m-%d')
        merged = 0
        try:
            for post in _generate_game_day_blog_posts(today=today_dt):
                if not post or _is_google_trends_blog_spam(post):
                    continue
                by_slug[post['slug']] = post
                merged += 1
        except Exception as exc:
            logger.debug(f"Game-day blog merge failed: {exc}")
        by_slug = _prune_stale_auto_blog_posts(by_slug, keep_date=today_str)
        # Never keep Trends spam even if somehow still on disk
        by_slug = {
            slug: post for slug, post in by_slug.items()
            if not _is_google_trends_blog_spam(post)
            and 'google-trends-betting-angle' not in str(slug).lower()
        }
        posts = list(by_slug.values())
        # Persist when game-day merged OR when we just wiped Trends off disk —
        # keeps Render volume aligned with the in-memory clean list.
        if merged > 0 or purged > 0:
            _persist_blog_posts_to_json(posts)
    elif purged > 0:
        _persist_blog_posts_to_json(posts)
    posts.sort(key=_blog_date_key, reverse=True)
    return posts

def _is_legacy_daily_blog_slug(slug: str) -> bool:
    """Old ESPN-headline daily digests — retired with Trends filler."""
    return bool(re.match(r'^prediction-lab-blog-\d{4}-\d{2}-\d{2}$', str(slug or '').strip().lower()))

def _is_auto_generated_blog_slug(slug: str) -> bool:
    s = str(slug or '').strip().lower()
    if _is_legacy_daily_blog_slug(s):
        return True
    if 'google-trends-betting-angle' in s:
        return True
    # Game-day previews: mlb-cardinals-at-yankees-preview-2026-08-03
    if re.search(r'-preview-\d{4}-\d{2}-\d{2}$', s):
        return True
    return False

def _auto_blog_slug_date(slug: str):
    s = str(slug or '').strip().lower()
    m = re.search(r'(\d{4}-\d{2}-\d{2})$', s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%Y-%m-%d').strftime('%Y-%m-%d')
    except Exception:
        return None

def _prune_stale_auto_blog_posts(by_slug: dict, *, keep_date: str) -> dict:
    """Drop stale server-generated posts; keep manual archive entries."""
    keep_date = str(keep_date or '').strip()[:10]
    if not keep_date:
        return by_slug
    out = {}
    for slug, post in (by_slug or {}).items():
        if _is_google_trends_blog_spam(post) or 'google-trends-betting-angle' in str(slug).lower():
            continue  # quarantine forever — never keep Trends filler
        if _is_legacy_daily_blog_slug(slug):
            continue  # retired daily ESPN-digest template
        if not _is_auto_generated_blog_slug(slug):
            out[slug] = post
            continue
        slug_date = _auto_blog_slug_date(slug)
        if slug_date == keep_date:
            out[slug] = post
    return out

def _persist_blog_posts_to_json(posts: list[dict]) -> None:
    """Persist game-day (and manual) archive posts. Never writes Trends spam.

    Empty ``posts`` is allowed — used after a full Trends wipe so Render disk
    cannot keep serving an old spam-filled JSON.
    """
    try:
        _os.makedirs(_os.path.dirname(_BLOG_POSTS_FILE), exist_ok=True)
        serializable = []
        for post in (posts or []):
            if not isinstance(post, dict) or _is_google_trends_blog_spam(post):
                continue
            slug = str(post.get('slug') or '')
            if 'google-trends-betting-angle' in slug.lower() or _is_legacy_daily_blog_slug(slug):
                continue
            serializable.append({
                'title': post.get('title'),
                'slug': post.get('slug'),
                'date': post.get('date'),
                'sport_tag': post.get('sport_tag'),
                'excerpt': post.get('excerpt'),
                'body': post.get('body') or [],
                'news_items': post.get('news_items') if isinstance(post.get('news_items'), list) else [],
            })
        with open(_BLOG_POSTS_FILE, 'w', encoding='utf-8') as fh:
            json.dump({'posts': serializable}, fh, indent=2, ensure_ascii=False)
            fh.write('\n')
        _BLOG_CACHE.update({'ts': _time.time(), 'posts': list(serializable)})
    except Exception as exc:
        logger.debug(f"Blog JSON persist failed: {exc}")

def _blog_display_date(post: dict) -> str:
    dt = _blog_date_key(post)
    if dt == datetime.min:
        return str(post.get('date') or '').strip()
    return dt.strftime('%B %d, %Y').replace(' 0', ' ')

def _blog_date_key(post: dict):
    raw = post.get('date') or post.get('published_at') or ''
    raw = str(raw).strip()[:10]
    try:
        return datetime.strptime(raw, '%Y-%m-%d')
    except Exception:
        return datetime.min

def _load_blog_posts_from_json() -> list[dict]:
    now_ts = _time.time()
    if _BLOG_CACHE.get('posts') and (now_ts - _BLOG_CACHE.get('ts', 0)) < _BLOG_CACHE_TTL:
        return [p for p in (_BLOG_CACHE.get('posts') or []) if not _is_google_trends_blog_spam(p)]
    posts: list[dict] = []
    try:
        if _os.path.exists(_BLOG_POSTS_FILE):
            with open(_BLOG_POSTS_FILE, encoding='utf-8') as fh:
                payload = json.load(fh)
            items = payload.get('posts', payload) if isinstance(payload, dict) else payload
            if isinstance(items, list):
                for raw in items:
                    post = _normalize_blog_post(raw)
                    if (
                        post
                        and not _is_google_trends_blog_spam(post)
                        and not _is_google_trends_blog_spam(raw if isinstance(raw, dict) else {})
                        and not _is_legacy_daily_blog_slug(post.get('slug'))
                    ):
                        posts.append(post)
    except Exception as exc:
        logger.debug(f"Blog JSON load failed: {exc}")
    posts.sort(key=_blog_date_key, reverse=True)
    _BLOG_CACHE.update({'ts': now_ts, 'posts': posts})
    return list(posts)

def _fetch_google_trends(geo: str = 'CA', category: int = 17, limit: int = 20) -> list[dict]:
    """DISABLED forever — never fetch Trends RSS for blogging (flag ignored)."""
    _ = (geo, category, limit, _BLOG_AUTO_TRENDS_ENABLED)
    return []

def _trend_items_for_blog(limit: int = 15) -> list[dict]:
    """DISABLED forever — Trends→blog pipeline retired Aug 2026."""
    _ = (limit, _BLOG_AUTO_TRENDS_ENABLED, _PINNED_SPORTS_TRENDS_20260616)
    return []

def _generate_trend_blog_post(item: dict, date_str: str, display_date: str) -> dict | None:
    """DISABLED forever — never recreate “Google Trends Betting Angle” titles."""
    _ = (item, date_str, display_date, _BLOG_AUTO_TRENDS_ENABLED)
    return None

def _infer_trend_sport(query: str) -> str:
    """Legacy helper; unused — Trends blogging is off."""
    _ = query
    return 'Sports'

def _blog_picks_path(sport: str) -> str:
    slug = SPORT_SEO_SLUGS.get(str(sport or '').upper())
    return f'/{slug}' if slug else '/'

def _blog_competitor_label(comp: dict) -> str:
    team = comp.get('team') or {}
    athlete = comp.get('athlete') or {}
    return (
        team.get('displayName')
        or team.get('name')
        or athlete.get('displayName')
        or athlete.get('fullName')
        or ''
    ).strip()

def _blog_record_summary(comp: dict) -> str:
    for rec in (comp.get('records') or []):
        if str(rec.get('type') or '').lower() in ('total', 'overall') or str(rec.get('name') or '').lower() == 'overall':
            summary = str(rec.get('summary') or '').strip()
            if summary:
                return summary
    for rec in (comp.get('records') or []):
        summary = str(rec.get('summary') or '').strip()
        if summary:
            return summary
    return ''

def _blog_probable_pitcher(comp: dict) -> str:
    for prob in (comp.get('probables') or []):
        if 'pitch' not in str(prob.get('name') or '').lower() and 'pitch' not in str(prob.get('displayName') or '').lower():
            # still accept first probable if unlabeled
            pass
        athlete = prob.get('athlete') or {}
        name = (athlete.get('displayName') or athlete.get('fullName') or '').strip()
        if not name:
            continue
        record = str(prob.get('record') or '').strip()
        return f"{name} {record}".strip() if record else name
    return ''

def _blog_event_local_time(event: dict, competition: dict) -> str:
    raw = (
        competition.get('date')
        or event.get('date')
        or (competition.get('status') or {}).get('type', {}).get('shortDetail')
    )
    if not raw:
        return ''
    try:
        if isinstance(raw, str) and 'T' in raw:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            local = dt.astimezone(ZoneInfo('America/New_York'))
            return local.strftime('%I:%M %p').lstrip('0') + ' ET'
    except Exception:
        pass
    detail = ((competition.get('status') or event.get('status') or {}).get('type') or {}).get('shortDetail') or ''
    # e.g. "8/3 - 7:05 PM EDT"
    if ' - ' in detail:
        return detail.split(' - ', 1)[1].strip()
    return str(detail).strip()

def _blog_parse_scoreboard_event(sport: str, event: dict, target_date: str) -> dict | None:
    competitions = event.get('competitions') or []
    if not competitions:
        return None
    competition = competitions[0] or {}
    event_date = _espn_event_date_to_local(
        competition.get('date') or event.get('date') or competition.get('startDate')
    )
    if event_date and event_date != target_date:
        return None
    competitors = competition.get('competitors') or []
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
    if not home or not away:
        # UFC / MMA often omit homeAway
        away, home = competitors[0], competitors[1]
    away_name = _blog_competitor_label(away)
    home_name = _blog_competitor_label(home)
    if not away_name or not home_name:
        return None
    venue = ((competition.get('venue') or {}).get('fullName') or '').strip()
    city = ((competition.get('venue') or {}).get('address') or {}).get('city') or ''
    status = ((competition.get('status') or event.get('status') or {}).get('type') or {})
    status_name = str(status.get('name') or status.get('description') or '').strip()
    return {
        'sport': sport,
        'event_id': str(event.get('id') or competition.get('id') or ''),
        'away': away_name,
        'home': home_name,
        'away_record': _blog_record_summary(away),
        'home_record': _blog_record_summary(home),
        'away_pitcher': _blog_probable_pitcher(away) if sport == 'MLB' else '',
        'home_pitcher': _blog_probable_pitcher(home) if sport == 'MLB' else '',
        'venue': venue,
        'city': city,
        'local_time': _blog_event_local_time(event, competition),
        'status': status_name,
        'date': target_date,
        'is_mma': sport == 'UFC',
    }

def _fetch_game_day_slate(sport: str, date_str: str) -> list[dict]:
    """Pull today's ESPN scoreboard events for one gated blog sport."""
    sport = str(sport or '').upper()
    if sport not in _BLOG_GAME_DAY_SPORTS:
        return []
    pair = _BLOG_GAME_DAY_ESPN.get(sport)
    if not pair:
        return []
    espn_sport, espn_league = pair
    ymd = date_str.replace('-', '')
    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{espn_sport}/"
        f"{espn_league}/scoreboard?dates={ymd}"
    )
    try:
        data = _cached_get(url, timeout=12)
    except Exception as exc:
        logger.debug(f"Game-day scoreboard fetch failed ({sport} {date_str}): {exc}")
        return []
    events = data.get('events') if isinstance(data, dict) else None
    if not isinstance(events, list):
        return []
    out = []
    for event in events:
        parsed = _blog_parse_scoreboard_event(sport, event, date_str)
        if parsed:
            out.append(parsed)
    return out

def _generate_game_preview_post(game: dict, display_date: str) -> dict:
    """Neutral slate preview — schedule facts + Prediction Lab picks CTA. No fake news."""
    sport = str(game.get('sport') or '').upper()
    away = game.get('away') or 'Away'
    home = game.get('home') or 'Home'
    date_str = str(game.get('date') or '')[:10]
    is_mma = bool(game.get('is_mma')) or sport == 'UFC'
    matchup = f"{away} vs {home}" if is_mma else f"{away} at {home}"
    title = f"{matchup} — {display_date} preview"
    slug_core = f"{away}-vs-{home}" if is_mma else f"{away}-at-{home}"
    slug = _slugify_blog(f"{sport.lower()}-{slug_core}-preview-{date_str}")

    time_bit = game.get('local_time') or ''
    venue = game.get('venue') or ''
    city = game.get('city') or ''
    venue_bit = venue
    if city and city.lower() not in venue.lower():
        venue_bit = f"{venue} ({city})" if venue else city

    lead_parts = [f"{matchup} is on the {sport} board for {display_date}"]
    if time_bit:
        lead_parts[0] += f", scheduled for {time_bit}"
    if venue_bit:
        lead_parts[0] += f" at {venue_bit}"
    lead_parts[0] += "."

    body = [lead_parts[0]]

    form_bits = []
    if game.get('away_record'):
        form_bits.append(f"{away} enter at {game['away_record']}")
    if game.get('home_record'):
        form_bits.append(f"{home} sit at {game['home_record']}")
    if form_bits:
        body.append(' and '.join(form_bits) + " on the season standings board heading into tip/first pitch.")

    if sport == 'MLB' and (game.get('away_pitcher') or game.get('home_pitcher')):
        ap = game.get('away_pitcher') or 'TBD'
        hp = game.get('home_pitcher') or 'TBD'
        body.append(f"Probable starters: {away} — {ap}; {home} — {hp}.")

    picks_path = _blog_picks_path(sport)
    body.append(
        f"This is a schedule preview built from the day's {sport} slate — not a rumor roundup. "
        f"For model win probabilities and moneyline context on this matchup, open Prediction Lab's "
        f"{sport} picks board at {picks_path}."
    )
    body.append(
        "Compare the model's projected win probability with the available market price before you bet, "
        "and use the daily results report afterward to see how completed cards graded."
    )

    excerpt = _blog_excerpt(' '.join(body), 2)
    return {
        'title': title,
        'slug': slug,
        'date': date_str,
        'sport_tag': sport,
        'excerpt': excerpt,
        'body': body,
        'news_items': [{
            'sport': sport,
            'topic': f"{sport} picks — live model board",
            'summary_hint': f"Prediction Lab {sport} predictions",
            'source': 'Prediction Lab',
            'url': picks_path,
        }],
    }

def _generate_game_day_blog_posts(today=None, sports=None) -> list[dict]:
    """Build one preview post per game for gated in-season sports."""
    today_dt = today or _blog_today_et()
    if hasattr(today_dt, 'strftime'):
        date_str = today_dt.strftime('%Y-%m-%d')
        display_date = today_dt.strftime('%B %d, %Y').replace(' 0', ' ')
    else:
        date_str = str(today_dt)[:10]
        display_date = date_str
    allow = tuple(sports) if sports else _BLOG_GAME_DAY_SPORTS
    posts: list[dict] = []
    for sport in allow:
        sport_u = str(sport).upper()
        if sport_u not in _BLOG_GAME_DAY_SPORTS:
            continue
        for game in _fetch_game_day_slate(sport_u, date_str):
            posts.append(_generate_game_preview_post(game, display_date))
            if len(posts) >= _BLOG_GAME_DAY_MAX_POSTS:
                return posts
    return posts

def _rebuild_game_day_blog_archive(today=None, persist: bool = True) -> list[dict]:
    """CLI/helper: regenerate today's game-day posts and optionally write blog_posts.json."""
    today_dt = today or _blog_today_et()
    today_str = today_dt.strftime('%Y-%m-%d') if hasattr(today_dt, 'strftime') else str(today_dt)[:10]
    existing = [p for p in _load_blog_posts_from_json() if not _is_google_trends_blog_spam(p)]
    by_slug = {p['slug']: p for p in existing}
    for post in _generate_game_day_blog_posts(today=today_dt):
        by_slug[post['slug']] = post
    by_slug = _prune_stale_auto_blog_posts(by_slug, keep_date=today_str)
    posts = list(by_slug.values())
    posts.sort(key=_blog_date_key, reverse=True)
    if persist:
        _persist_blog_posts_to_json(posts)
    return posts

def _slugify_blog(value: str) -> str:
    value = (value or '').strip().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-') or 'prediction-lab-blog'

def _blog_excerpt(text: str, max_sentences: int = 3) -> str:
    clean = re.sub(r'\s+', ' ', (text or '')).strip()
    if not clean:
        return ''
    parts = re.split(r'(?<=[.!?])\s+', clean)
    return ' '.join(parts[:max_sentences]).strip()

def _normalize_blog_post(raw: dict):
    if not isinstance(raw, dict):
        return None
    title = str(raw.get('title') or '').strip()
    if not title:
        return None
    date_str = str(raw.get('date') or raw.get('published_at') or '').strip()[:10]
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    body = raw.get('body') or raw.get('content') or []
    if isinstance(body, str):
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    elif isinstance(body, list):
        paragraphs = [str(p).strip() for p in body if str(p).strip()]
    else:
        paragraphs = []
    excerpt = str(raw.get('excerpt') or '').strip()
    if not excerpt:
        excerpt = _blog_excerpt(' '.join(paragraphs), 3)
    slug = str(raw.get('slug') or '').strip() or _slugify_blog(title)
    sport_tag = str(raw.get('sport_tag') or raw.get('sport') or 'AI Picks').strip()
    return {
        'title': title,
        'slug': _slugify_blog(slug),
        'date': date_str,
        'sport_tag': sport_tag,
        'excerpt': excerpt,
        'body': paragraphs,
        'news_items': raw.get('news_items') if isinstance(raw.get('news_items'), list) else [],
    }

def _fetch_espn_news_items(limit=5) -> list[dict]:
    now_ts = _time.time()
    cached = _BLOG_NEWS_CACHE
    if cached.get('items') and (now_ts - cached.get('ts', 0)) < _BLOG_NEWS_CACHE_TTL:
        return list(cached.get('items') or [])[:limit]
    items = []
    seen = set()
    for sport, url in _ESPN_NEWS_FEEDS:
        try:
            resp = requests.get(url, timeout=2.0, params={'limit': 4})
            if resp.status_code != 200:
                continue
            payload = resp.json()
            articles = payload.get('articles') or []
            for article in articles:
                headline = str(article.get('headline') or article.get('title') or '').strip()
                if not headline:
                    continue
                key = headline.lower()
                if key in seen:
                    continue
                seen.add(key)
                desc = str(article.get('description') or '').strip()
                links = article.get('links') or {}
                web_link = links.get('web') if isinstance(links, dict) else {}
                href = web_link.get('href') if isinstance(web_link, dict) else None
                items.append({
                    'sport': sport,
                    'topic': _blog_news_topic(headline),
                    'summary_hint': _blog_excerpt(desc, 1),
                    'source': 'ESPN',
                    'url': href,
                })
                if len(items) >= limit:
                    _BLOG_NEWS_CACHE.update({'ts': now_ts, 'items': items})
                    return list(items)
        except Exception as exc:
            logger.debug(f"ESPN news feed failed for {sport}: {exc}")
            continue
    _BLOG_NEWS_CACHE.update({'ts': now_ts, 'items': items})
    return list(items)[:limit]

def _news_market_paragraph(item: dict) -> str:
    sport = item.get('sport') or 'sports'
    topic = item.get('topic') or 'a developing story'
    return (
        f"In {sport}, the news cycle is centered on {topic}. "
        "From a betting perspective, that kind of update matters because roster availability, team form, travel spots, and public market reaction can all change how moneyline, spread, and totals prices should be interpreted."
    )

def _blog_news_topic(headline: str) -> str:
    topic = re.sub(r'\s+', ' ', (headline or '')).strip()
    topic = re.sub(r'^[\'"]|[\'"]$', '', topic)
    return topic[:140].rstrip()

def _edge_performance(league: str) -> dict:
    """Edge-bucketed historical performance (win-rate, ROI, sample) + edge
    distribution. Built ONLY from completed, graded props with a stored edge
    (the `ev` column). Never fabricated."""
    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT result, ev, odds FROM player_prop_results "
            "WHERE league=? AND result IN ('HIT','MISS') AND ev IS NOT NULL",
            (league,)
        ).fetchall()]
    finally:
        conn.close()

    # Bucket by the MAGNITUDE of the edge (|ev|). The model's edge can point
    # either way (especially for flipped sports), so the meaningful signal is
    # "how strong is the edge", not its sign — this is what calibrates to win %.
    for r in rows:
        r["edge_mag"] = abs(float(r["ev"])) if r.get("ev") is not None else None

    edge_table = _bucket_stats(rows, _EDGE_BUCKETS, "edge_mag")
    total = len(rows)

    # Edge distribution: what % of completed picks fall in each broad bucket.
    dist = []
    for label, lo, hi in _EDGE_DIST_BUCKETS:
        n = sum(1 for r in rows
                if r.get("edge_mag") is not None and
                ((r["edge_mag"] >= lo) if hi >= 1e9 else (lo <= r["edge_mag"] < hi)))
        dist.append({"bucket": label, "count": n,
                     "pct": round(n / total * 100, 1) if total else None})

    return {
        "league": league,
        "graded": total,
        "edge_table": edge_table,
        "edge_distribution": dist,
    }

def _bucket_stats(graded_rows, buckets, value_key):
    """Win rate + sample + ROI per bucket for a metric (confidence, ev, edge).
    A bucket whose upper bound is >= 1e9 is treated as open-ended (no max)."""
    out = []
    for label, lo, hi in buckets:
        wins = total = 0
        profit = 0.0
        for r in graded_rows:
            v = r.get(value_key)
            if v is None:
                continue
            v = float(v)
            in_bucket = (v >= lo) if hi >= 1e9 else (lo <= v < hi)
            if not in_bucket:
                continue
            won = r["result"] == "HIT"
            total += 1
            if won:
                wins += 1
            profit += _american_profit(r.get("odds"), won)
        out.append({
            "bucket": label,
            "win_rate": round(wins / total * 100, 1) if total else None,
            "record": f"{wins}-{total - wins}",
            "sample": total,
            "roi": round(profit / total * 100, 1) if total else None,
            "small": 0 < total < 15,
        })
    return out

def _american_profit(odds, won: bool) -> float:
    """Profit on a 1-unit stake at American odds (loss = -1)."""
    if not won:
        return -1.0
    try:
        o = float(odds)
    except (TypeError, ValueError):
        o = -110.0  # assume standard juice if odds missing
    if o == 0:
        o = -110.0
    return (o / 100.0) if o > 0 else (100.0 / abs(o))


# --- routes ---

@app.route('/edge-performance')
def edge_performance_page():
    """Edge Value Performance — how the Edge % signal calibrates to real
    completed-pick win rate and ROI, per sport."""
    _PROP_LEAGUES = ['NBA', 'WNBA', 'NHL', 'MLB', 'NCAAB', 'NCAAW', 'NCAAF',
                     'NFL', 'SOCCER']
    league = (request.args.get('sport') or 'NBA').strip().upper()
    if league not in _PROP_LEAGUES:
        league = 'NBA'
    try:
        edge_perf = _edge_performance(league)
    except Exception:
        logger.exception('edge_performance_page failed')
        edge_perf = {'league': league, 'graded': 0, 'edge_table': [], 'edge_distribution': []}
    return render_template_string(
        EDGE_PERFORMANCE_TEMPLATE,
        page='edge-performance',
        page_title='Edge Value Performance | predictionlab.io',
        page_description='See how our Edge % signal has actually performed — real '
                         'win rate and ROI by edge level for each sport.',
        league=league,
        sports=_PROP_LEAGUES,
        edge_perf=edge_perf,
    )

@app.route('/downloads')
@app.route('/results/downloads')
def downloads_page():
    """Per-sport CSV download hub (Results menu → Download CSV)."""
    download_sports = [
        {'key': k, 'name': SPORTS[k]['name'], 'icon': SPORTS[k].get('icon', '')}
        for k in ALL_SPORTS_DASHBOARD_SPORTS if k in SPORTS
    ]
    return render_template_string(
        DOWNLOADS_TEMPLATE,
        page='downloads',
        page_title='Download Results & Picks CSV by Sport | predictionlab.io',
        page_description='Download season model results or pick history as a CSV for any '
                         'sport — NBA, NHL, MLB, NFL, NCAAB, WNBA, Soccer and more.',
        download_sports=download_sports,
    )

@app.route('/blog')
def blog_archive_page():
    # First hit after Manual Deploy rewrites Render's on-disk blog_posts.json
    # if any Google Trends / Betting Angle spam is still present.
    _purge_google_trends_from_blog_disk()
    posts = _blog_template_posts()
    return render_template_string(
        BLOG_ARCHIVE_TEMPLATE,
        posts=posts,
        site_domain=_SITE_DOMAIN,
        page='blog',
        page_title='Prediction Lab Blog | predictionlab.io',
        page_description='Daily sports news, AI-generated betting insights, game previews, market breakdowns, and model analysis from predictionlab.io.',
    )

@app.route('/blog/<slug>')
def blog_post_redirect(slug):
    """Per-slug blog URLs are ephemeral (game-day previews rotate daily).

    301 to the archive so Google consolidates on /blog instead of crawling
    soft hash redirects ("Crawled - currently not indexed").
    Trends Betting Angle slugs also trigger an on-disk purge + noindex.
    """
    clean_slug = _slugify_blog(slug)
    if (
        'google-trends-betting-angle' in clean_slug.lower()
        or _is_google_trends_blog_spam({'slug': clean_slug, 'title': clean_slug})
    ):
        _purge_google_trends_from_blog_disk()
    resp = redirect(f'{_SITE_DOMAIN}/blog', code=301)
    resp.headers['X-Robots-Tag'] = 'noindex, follow'
    return resp


# Blog caches (annotated assignments missed by the bulk port)
_BLOG_CACHE: dict = {'ts': 0, 'posts': []}
_BLOG_NEWS_CACHE: dict = {'ts': 0, 'items': []}




# ===== Homepage (research design) context builders =====

def _build_landing_preview_context():
    games_graded = 0
    predictions_logged = 0
    latest_graded_game = None
    try:
        _conn = get_db_connection()
        games_graded = _conn.execute(
            "SELECT COUNT(*) FROM games WHERE home_score IS NOT NULL AND away_score IS NOT NULL"
        ).fetchone()[0]
        predictions_logged = _conn.execute(
            "SELECT COUNT(*) FROM predictions"
        ).fetchone()[0]
        # ===== SECTION: Homepage last-night correct pick =====
        # Show the model's best CORRECT pick from the most recent slate we have
        # graded (i.e. "the game we got right last night"). Derive the pick from
        # win_probability (home-win prob, 0-1): >=0.5 favored home, else away.
        # We first find the most recent date that HAS a correct pick, then take
        # the highest-confidence correct pick on that date. Anchoring to the most
        # recent graded date (instead of the global most-recent row) stops the
        # card from silently drifting to a weeks-old game when a slate is sparse.
        # NOTE: predictions and games can disagree on game_id and can be off by
        # one calendar day (predictions are stored in UTC, so a late US-night game
        # lands on the next date). A strict game_id join therefore silently drops
        # every recent slate and the card drifts weeks into the past. Anchor on the
        # graded GAME (authoritative date + scores) and match the prediction by
        # sport + both team names within a +/-1 day window, preferring the closest
        # dated / highest-confidence correct pick.
        _graded_row = _conn.execute(
            """
            WITH correct_picks AS (
                SELECT g.sport AS sport, g.game_date AS game_date,
                       g.away_team_id AS away_team_id, g.home_team_id AS home_team_id,
                       p.win_probability AS win_probability,
                       g.away_score AS away_score, g.home_score AS home_score,
                       g.id AS gid,
                       ABS(julianday(date(p.game_date)) - julianday(date(g.game_date))) AS date_gap
                FROM games g
                JOIN predictions p ON p.sport = g.sport
                    AND p.home_team_id = g.home_team_id
                    AND p.away_team_id = g.away_team_id
                    AND ABS(julianday(date(p.game_date)) - julianday(date(g.game_date))) <= 1
                WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL
                  AND g.home_score != g.away_score
                  AND p.win_probability IS NOT NULL
                  AND (
                    (p.win_probability >= 0.5 AND g.home_score > g.away_score)
                    OR
                    (p.win_probability < 0.5 AND g.away_score > g.home_score)
                  )
            )
            SELECT sport, game_date, away_team_id, home_team_id,
                   win_probability, away_score, home_score
            FROM correct_picks
            WHERE date(game_date) = (SELECT MAX(date(game_date)) FROM correct_picks)
            ORDER BY date_gap ASC, ABS(win_probability - 0.5) DESC, gid DESC
            LIMIT 1
            """
        ).fetchone()
        if _graded_row:
            _wp = float(_graded_row['win_probability'] or 0)
            _wp_pct = _wp * 100 if _wp <= 1 else _wp
            _home_pick = _wp_pct >= 50.0
            latest_graded_game = {
                'sport': _graded_row['sport'],
                'date': _graded_row['game_date'],
                'away': _graded_row['away_team_id'],
                'home': _graded_row['home_team_id'],
                'pick': _graded_row['home_team_id'] if _home_pick else _graded_row['away_team_id'],
                'probability': round(_wp_pct if _home_pick else (100 - _wp_pct), 1),
                'away_score': _graded_row['away_score'],
                'home_score': _graded_row['home_score'],
            }
        _conn.close()
    except Exception as _e:
        logger.debug(f"Landing preview stats query failed: {_e}")

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
    active_sport = next((s for s in landing_sports if s.get('is_live')), landing_sports[0] if landing_sports else None)

    todays_picks = build_todays_top_picks()
    blog_posts = [
        {**post, 'display_date': _blog_display_date(post)}
        for post in _get_blog_posts(include_generated=True, todays_picks=todays_picks)
    ]
    latest_blog_post = blog_posts[0] if blog_posts else None
    preview_units = [
        item for item in _get_sport_ml_units_banner()
        if 'SOCCER' not in item.get('label', '').upper()
    ]

    return {
        'games_graded': games_graded,
        'predictions_logged': predictions_logged,
        'landing_sports': landing_sports,
        'active_sport_slug': active_sport.get('seo_slug') if active_sport else 'mlb-picks',
        'active_sport_name': active_sport.get('name') if active_sport else 'MLB',
        'sports_covered': len(landing_sports),
        'weekly_banner_messages': list(_MANUAL_BANNER_ITEMS),
        'units_banner_items': preview_units,
        'todays_picks': todays_picks,
        'latest_graded_game': latest_graded_game,
        'latest_blog_post': latest_blog_post,
        'recent_blog_posts': blog_posts[1:4],
    }

def _build_fast_landing_preview_context():
    """Lightweight homepage context for production cold starts."""
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
    active_sport = next((s for s in landing_sports if s.get('is_live')), landing_sports[0] if landing_sports else None)
    return {
        'games_graded': 0,
        'predictions_logged': 0,
        'landing_sports': landing_sports,
        'active_sport_slug': active_sport.get('seo_slug') if active_sport else 'mlb-picks',
        'active_sport_name': active_sport.get('name') if active_sport else 'MLB',
        'sports_covered': len(landing_sports),
        'weekly_banner_messages': list(_MANUAL_BANNER_ITEMS),
        'units_banner_items': [],
        'todays_picks': [],
        'latest_graded_game': None,
        'latest_blog_post': None,
        'recent_blog_posts': [],
    }

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
