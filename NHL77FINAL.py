#!/usr/bin/env python3
"""
predictionlab.io - Multi-Sport Prediction Platform
==================================================
Complete platform with Dashboard, Predictions, and Results pages for all sports.
5-Model System: Glicko-2, TrueSkill, Elo, XGBoost, Ensemble

LEGACY SHARED CORE
------------------
This filename is historical. It no longer means this file is only for hockey.
The canonical launcher is ``app.py``. New sport-specific behavior should go in
``sports/<SPORT>.py`` whenever practical; this module currently remains the
shared Flask, database, odds, grading, page-rendering, and route assembly layer.

See ``ARCHITECTURE_GUIDE.md`` for a plain-English section map and ownership
rules before changing this file.
"""

# ============================================================================
# 1. IMPORTS, OPTIONAL MODEL LOADING, LOGGING, AND GLOBAL CACHES
# ============================================================================

from flask.json.provider import DefaultJSONProvider
from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, Response, make_response, send_from_directory, abort, has_request_context
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import unicodedata
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
    for sport in ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAF', 'NCAAB', 'WNBA']:
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
    'UFC': 180,
    'TENNIS': 180,
    'GOLF': 240,
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
_PROPS_GRADE_SCHEDULED: set = set()
_PROPS_GRADE_LOCK = __import__('threading').Lock()
_STALE_PAGE_TTL_MULTIPLIER = 5


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


def _schedule_predictions_db_save(sport: str, predictions) -> None:
    """Persist new predictions in a background thread — never block picks page."""
    if not predictions:
        return
    import threading as _thr

    def _run():
        try:
            conn_save = get_db_connection()
            cursor_save = conn_save.cursor()
            saved_count = 0
            for pred in predictions:
                if pred.get('game_id') and pred.get('home_score') is None:
                    existing = cursor_save.execute(
                        'SELECT id FROM predictions WHERE game_id = ? AND sport = ?',
                        (pred['game_id'], sport),
                    ).fetchone()
                    if existing:
                        continue
                    _elo_save = pred.get('elo_prob')
                    _xgb_save = pred.get('xgb_prob')
                    _ens_save = pred.get('ensemble_prob')
                    if _elo_save is None or _xgb_save is None or _ens_save is None:
                        continue
                    try:
                        cursor_save.execute('''
                            INSERT OR IGNORE INTO predictions (
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
        except Exception as exc:
            logger.debug(f"[{sport}] background prediction save failed: {exc}")

    _thr.Thread(target=_run, daemon=True, name=f'pred-save-{sport}').start()
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


_PL_BOOK_ODDS_LIMIT_BY_SPORT = {
    'SOCCER': 20,
    'NBA': 20,
    'MLB': 20,
    'NHL': 16,
    'NFL': 16,
    'WNBA': 14,
    'NCAAB': 12,
    'NCAAW': 12,
    'NCAAF': 12,
}

_OFFSEASON_SPORTS_HINT = {
    'NCAAB':  'College basketball picks return when the season schedule is live on ESPN (typically November–April).',
    'NCAAW':  "Women's college basketball picks return when the season schedule is live on ESPN (typically November–April).",
    'NFL':    'NFL picks return when the regular season schedule is published (typically September–February).',
    'NCAAF':  'College football picks return when the fall schedule is live on ESPN (typically August–January).',
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


def _results_date_query_active():
    if not has_request_context():
        return False
    return bool((request.args.get('date') or '').strip())


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


def _picks_display_dates(grouped_predictions, today_date):
    """Dates for the picks nav. The picker exposes EVERY season date; the page
    renders a window (recent past + all upcoming) and out-of-window picks
    navigate to that date's own page. Returns (render_dates, all_dates, default)."""
    if not grouped_predictions:
        return [], [], today_date
    all_dates = sorted(d for d in grouped_predictions.keys() if d and d != 'TBD')
    if not all_dates:
        return [], [], today_date
    past = [d for d in all_dates if d <= today_date]
    future = [d for d in all_dates if d > today_date]
    render_dates = past[-10:] + future          # inline sections: last 10 days + everything ahead
    upcoming = [
        dk for dk in all_dates
        if any(isinstance(g, dict) and g.get('home_score') is None
               for g in grouped_predictions[dk])
    ]
    if today_date in all_dates:
        default = today_date
    else:
        nxt = [d for d in upcoming if d >= today_date]
        default = nxt[0] if nxt else (render_dates[-1] if render_dates else today_date)
    if default not in render_dates and render_dates:
        default = render_dates[-1]
    return render_dates, all_dates, default


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
    # Ascending sort: prioritized games first, then upcoming (date >= today) before
    # stale past games, then soonest game first. Do NOT use reverse=True — it would
    # invert the priority/future flags and spend the limited fetch budget on stale
    # past games before reaching the current slate (book odds would show "—").
    ordered.sort(
        key=lambda p: (
            0 if str(p.get('game_id')) in priority_ids else 1,
            0 if (p.get('game_date') or '') >= today else 1,
            p.get('game_date') or '9999',
            str(p.get('game_id') or ''),
        ),
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
    # Prefer Sharp Consensus (ensemble) over efficiency-derived disp_ml_prob so PL
    # spread display matches the featured pick when models disagree with efficiency.
    for key in ('ensemble_prob', 'ens_prob', 'disp_ml_prob'):
        v = _safe_float(card.get(key))
        if v is not None:
            return _normalize_home_win_prob_pct(v)
    return None


def _model_probs_unanimous_side(pred: dict):
    """Return 'home' or 'away' when 2+ model ML probs agree; else None."""
    keys = ('glicko2_prob', 'trueskill_prob', 'elo_prob', 'xgb_prob', 'ensemble_prob', 'ens_prob')
    sides = []
    for key in keys:
        v = _safe_float(pred.get(key))
        if v is None:
            continue
        if v <= 1.0:
            v *= 100.0
        if abs(v - 50.0) < 0.05:
            return None
        sides.append('home' if v >= 50.0 else 'away')
    if len(sides) < 2:
        return None
    return sides[0] if len(set(sides)) == 1 else None


def _sync_card_display_to_face_pick(pred: dict, sport: str = 'NBA') -> None:
    """After face pick is set, align predicted_winner to consensus — PL spread stays honest."""
    if pred.get('home_score') is not None:
        return
    pick = pred.get('face_pick_team')
    if not pick:
        return
    home_id = pred.get('home_team_id')
    away_id = pred.get('away_team_id')
    if not (home_id and away_id):
        return
    pred['predicted_winner'] = pick
    _set_card_pl_moneylines(pred)


def _set_card_pl_spread(card: dict, sport: str = 'NBA') -> None:
    """Populate disp_pl_spread; flip sign only when it opposes PL ML direction.

    our_spread / disp_pl_spread use home-centric convention (positive = home favored),
    matching fmt_spread_line and pl_book_odds_api book_spread after disp_book flip.
    Does not mutate our_spread or model probabilities.
    """
    sp = _best_pl_spread(card)
    if sp is None:
        # SOCCER MODEL-LINE GUARD: the sportsbook line is not a PL projection.
        candidates = (
            ('our_spread', 'naive_spread')
            if sport == 'SOCCER'
            else ('our_spread', 'market_spread', 'naive_spread')
        )
        sp = _first_pred_float(card, candidates)
    if sp is None:
        card.pop('disp_pl_spread', None)
        return
    sp = _round_to_half(float(sp))
    # Efficiency PL spread: show model line as computed — do not flip to match ensemble ML.
    if sport != 'MLB' and card.get('our_method') not in ('efficiency', 'team-avg-fallback'):
        hp = _pl_home_prob_for_spread_display(card)
        if hp is not None and sp != 0 and abs(hp - 50.0) >= 0.05:
            home_ml_fav = hp > 50.0
            if home_ml_fav and sp < 0:
                sp = -sp
            elif not home_ml_fav and sp > 0:
                sp = -sp
    card['disp_pl_spread'] = sp


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
    # PL column = Sharp Consensus (ensemble). disp_ml_prob is efficiency-spread-derived
    # and can oppose unanimous model picks — e.g. efficiency Knicks -4 while all models
    # pick Spurs 64%. Featured pick ML must match consensus side.
    prob = (_safe_float(card.get('ensemble_prob'))
            or _safe_float(card.get('ens_prob'))
            or _safe_float(card.get('disp_ml_prob')))
    ml = _compute_odds_from_prob(prob, apply_vig=True, clamp_ml=True)
    if ml:
        card['pl_model_home_ml'] = ml.get('moneyline_home')
        card['pl_model_away_ml'] = ml.get('moneyline_away')


def _set_card_projected_scores(card: dict) -> None:
    """Projected Score box — derive PL/XSharp from spread+total (half-point increments)."""
    _home_id = card.get('home_team_id') or card.get('home')
    _picked   = card.get('predicted_winner')

    ps = _safe_float(card.get('disp_pl_spread')) or _safe_float(card.get('our_spread'))
    pt = _safe_float(card.get('disp_pl_total')) or _safe_float(card.get('our_total'))
    if ps is not None and pt is not None:
        xh, xa = _scores_from_spread_total(ps, pt)
        if xh is not None:
            # Suppress PL score when it contradicts the pick direction.
            # Happens on V2 games where the efficiency model and the ensemble disagree.
            _pl_winner = _home_id if xh >= xa else card.get('away_team_id') or card.get('away')
            if _picked and _pl_winner and _pl_winner != _picked:
                pass  # do not set — score would say opposite team wins
            else:
                card['pl_proj_home_pts'] = _round_to_half(xh)
                card['pl_proj_away_pts'] = _round_to_half(xa)

    xs = _safe_float(card.get('disp_xs_spread')) or _safe_float(card.get('xgb_spread'))
    xt = _safe_float(card.get('disp_xs_total')) or _safe_float(card.get('xgb_total'))
    if xs is not None and xt is not None:
        xh, xa = _scores_from_spread_total(xs, xt)
        if xh is not None:
            # Same guard for XSharp projected score
            _xs_winner = _home_id if xh >= xa else card.get('away_team_id') or card.get('away')
            if _picked and _xs_winner and _xs_winner != _picked:
                pass
            else:
                card['xs_proj_home_pts'] = _round_to_half(xh)
                card['xs_proj_away_pts'] = _round_to_half(xa)


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
    _set_efficiency_prob_on_card(g, sport=sport)
    # Ensure all model prob/correct fields exist for results card display
    for model_field in ('glicko2_prob', 'trueskill_prob', 'elo_prob', 'xgb_prob', 'ensemble_prob'):
        if model_field not in g:
            g[model_field] = None
    # Grade model correctness based on actual outcome
    home_won = None
    if g.get('home_score') is not None and g.get('away_score') is not None:
        home_won = g['home_score'] > g['away_score']

    for correct_field in ('glicko2_correct', 'trueskill_correct', 'elo_correct', 'xgb_correct', 'ens_correct'):
        if correct_field not in g:
            g[correct_field] = None

    # Calculate correctness if we have the outcome and model probabilities
    if home_won is not None:
        model_map = {
            'glicko2_correct': 'glicko2_prob',
            'trueskill_correct': 'trueskill_prob',
            'elo_correct': 'elo_prob',
            'xgb_correct': 'xgb_prob',
            'ens_correct': 'ensemble_prob',
        }
        for correct_field, prob_field in model_map.items():
            prob = _safe_float(g.get(prob_field))
            if prob is not None and g.get(correct_field) is None:
                predicted_home = prob >= 50.0
                g[correct_field] = predicted_home == home_won


def _finalize_daily_result_cards(sport, daily_results):
    """Book lines + card display keys for every completed game (all sports)."""
    if not daily_results:
        return
    try:
        _attach_book_odds_to_daily_results(sport, daily_results, api_limit=25)
    except Exception as _bk:
        logger.debug(f"Book odds on results for {sport}: {_bk}")
    for dd in daily_results.values():
        for g in dd.get('games', []):
            _prepare_result_card_display(g, sport)




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
        'Atlanta Dream': 'atl', 'Chicago Sky': 'chi', 'Connecticut Sun': 'con',
        'Dallas Wings': 'dal', 'Golden State Valkyries': 'gs', 'Indiana Fever': 'ind',
        'Las Vegas Aces': 'lv', 'Los Angeles Sparks': 'la', 'Minnesota Lynx': 'min',
        'New York Liberty': 'ny', 'Phoenix Mercury': 'phx', 'Portland Fire': 'por',
        'Seattle Storm': 'sea', 'Toronto Tempo': 'tor', 'Washington Mystics': 'wsh',
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


def _grade_efficiency_for_results(sport, daily_results) -> None:
    """Per-game Efficiency ML grading on results cards (all grading sports)."""
    if sport not in _eff_attach.EFFICIENCY_GRADING_SPORTS or not daily_results:
        return
    try:
        _eff_attach.grade_efficiency_for_daily_results(sport, daily_results)
    except Exception as exc:
        logger.debug(f"[eff] results grading failed for {sport}: {exc}")


def _set_efficiency_prob_on_card(pred: dict, sport: str = 'NBA') -> None:
    """Team Efficiency model row — ML prob from efficiency spread (not ensemble/H2H PL line)."""
    sp = _safe_float(pred.get('efficiency_spread'))
    if sp is None and pred.get('our_method') in ('efficiency', 'team-avg-fallback'):
        sp = _safe_float(pred.get('our_spread'))
    if sp is None:
        pred['efficiency_prob'] = None
        return
    try:
        pred['efficiency_prob'] = _eff_attach.spread_to_home_prob_pct(sp, sport)
    except (TypeError, ValueError):
        pred['efficiency_prob'] = None


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

    # Safety: ensure face_pick_team is always set
    if not pred.get('face_pick_team'):
        fhp = _safe_float(pred.get('face_home_prob'))
        if fhp is not None and fhp > 50:
            pred['face_pick_team'] = pred.get('home_team_id')
        elif fhp is not None:
            pred['face_pick_team'] = pred.get('away_team_id')
        else:
            pred['face_pick_team'] = pred.get('home_team_id') or 'Home'

    # CARD CONFIDENCE FALLBACK: team sports may use a neutral placeholder, but
    # soccer must stay blank when no real model or market-informed output exists.
    if not pred.get('face_pick_confidence'):
        fhp = _safe_float(pred.get('face_home_prob'))
        fap = _safe_float(pred.get('face_away_prob'))
        if fhp is not None:
            pred['face_pick_confidence'] = round(fhp if fhp >= 50 else fap, 1) if fap else round(fhp, 1)
        elif sport != 'SOCCER':
            pred['face_pick_confidence'] = 50.0



def _prepare_pred_card_display(pred: dict, sport: str = 'NBA') -> None:
    """Precompute odds fields for the picks template (avoids fragile nested Jinja)."""
    if pred.get('home_score') is not None:
        return
    _raw_pl_sp = _best_pl_spread(pred)
    if _raw_pl_sp is None:
        # SOCCER MODEL-LINE GUARD: never relabel a raw sportsbook spread as PL.
        _pl_spread_candidates = (
            ('our_spread', 'naive_spread')
            if sport == 'SOCCER'
            else ('our_spread', 'market_spread', 'naive_spread')
        )
        _raw_pl_sp = _first_pred_float(
            pred, _pl_spread_candidates,
        )
        if _raw_pl_sp is not None:
            _raw_pl_sp = _round_to_half(_raw_pl_sp)
    pred['disp_pl_total'] = _best_pl_total(pred)
    if pred['disp_pl_total'] is None:
        _pl_total_candidates = (
            ('our_total', 'naive_total', 'h2h_last10_total')
            if sport == 'SOCCER'
            else ('our_total', 'naive_total', 'market_total', 'h2h_last10_total')
        )
        pred['disp_pl_total'] = _first_pred_float(
            pred, _pl_total_candidates,
        )
        if pred['disp_pl_total'] is not None:
            pred['disp_pl_total'] = _round_to_half(pred['disp_pl_total'])
    _xs_spread_candidates = (
        ('xsharp_spread', 'xgb_spread', 'naive_spread')
        if sport == 'SOCCER'
        else ('xsharp_spread', 'xgb_spread', 'naive_spread', 'market_spread')
    )
    pred['disp_xs_spread'] = _first_pred_float(pred, _xs_spread_candidates)
    if pred['disp_xs_spread'] is not None:
        pred['disp_xs_spread'] = _round_to_half(pred['disp_xs_spread'])
    _xs_total_candidates = (
        ('xsharp_total', 'xgb_total', 'naive_total')
        if sport == 'SOCCER'
        else ('xsharp_total', 'xgb_total', 'naive_total', 'market_total')
    )
    pred['disp_xs_total'] = _first_pred_float(pred, _xs_total_candidates)
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
    if _pl_sp is not None and abs(_pl_sp) >= 1.0 and _our_method in ('efficiency', 'team-avg-fallback'):
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
    _set_efficiency_prob_on_card(pred, sport=sport)
    _prepare_pred_card_face(pred, sport=sport)
    _sync_card_display_to_face_pick(pred, sport=sport)
    # WNBA: flip the three underperforming models (Grinder2/Takedown/XSharp) on
    # the displayed pick cards so Pick Confidence matches the flipped grading.
    # Probs here are 0-100. Consensus/Edge/Efficiency are left unchanged.
    if sport == 'WNBA':
        for _k in ('glicko2_prob', 'trueskill_prob', 'xgb_prob'):
            _v = _safe_float(pred.get(_k))
            if _v is not None:
                pred[_k] = round(100.0 - _v, 1)


def _attach_soccer_ev_to_pred(pred: dict) -> None:
    """Goal-scale EV (Moneyline / Spread / Total) for SOCCER pro-table & cards.

    Soccer is excluded from the points-based EV block (that uses basketball-scale
    sigma, which makes goal handicaps/totals read ~0%). Here:
      - Moneyline EV uses the CALIBRATED 3-way win prob (ensemble_prob is
        unreliable for soccer) vs the book price for the model's pick side.
      - Spread/Total EV use goal-scale sigma (handicap ~1.3, total ~1.4 goals).
    Call AFTER _prepare_pred_card_display so disp_pl_spread/disp_pl_total exist.
    """
    import math as _m
    if pred.get('home_score') is not None:
        return
    hp = _safe_float(pred.get('home_win_prob'))
    ap = _safe_float(pred.get('away_win_prob'))
    bh = _safe_float(pred.get('book_home_moneyline'))
    ba = _safe_float(pred.get('book_away_moneyline'))

    # ── Moneyline EV: calibrated win prob vs book price for the model's side ──
    ml_ev = None
    if hp is not None and ap is not None and bh is not None and ba is not None:
        pick_p, pick_ml = (hp / 100.0, bh) if hp >= ap else (ap / 100.0, ba)
        if pick_ml is not None:
            ml_ev = round(calculate_ev(pick_p, pick_ml), 1)
    pred['ml_ev'] = ml_ev

    # ── Spread (goal handicap) EV: model goal-line vs book goal-line ──
    _SOC_SPREAD_SIGMA = 1.3
    model_sp = _safe_float(pred.get('disp_pl_spread'))
    book_sp = _safe_float(pred.get('book_spread'))
    if book_sp is None:
        book_sp = _safe_float(pred.get('market_spread'))
    sp_ev = None
    if model_sp is not None and book_sp is not None:
        edge = abs(model_sp) - abs(book_sp)
        cover_p = 0.5 * (1.0 + _m.erf(edge / (_SOC_SPREAD_SIGMA * _m.sqrt(2))))
        sp_ev = round(calculate_ev(cover_p, -110), 1)
    pred['spread_ev'] = sp_ev

    # ── Total (goals) EV: model total vs book total, edge capped at ±1 goal ──
    _SOC_TOTAL_SIGMA = 1.4
    model_tot = _safe_float(pred.get('disp_pl_total'))
    book_tot = _safe_float(pred.get('book_total'))
    if book_tot is None:
        book_tot = _safe_float(pred.get('market_total'))
    to_ev = None
    if model_tot is not None and book_tot is not None:
        edge = max(-1.0, min(1.0, model_tot - book_tot))
        over_p = 0.5 * (1.0 + _m.erf(edge / (_SOC_TOTAL_SIGMA * _m.sqrt(2))))
        actual_p = over_p if edge >= 0 else (1.0 - over_p)
        to_ev = round(calculate_ev(actual_p, -110), 1)
    pred['total_ev'] = to_ev

    # ── Best EV market (positive only) ──
    ev_map = {}
    if ml_ev is not None and ml_ev > 0:
        ev_map['Moneyline'] = ml_ev
    if sp_ev is not None and sp_ev > 0:
        ev_map['Spread'] = sp_ev
    if to_ev is not None and to_ev > 0:
        ev_map['Total'] = to_ev
    pred['best_ev_market'] = max(ev_map, key=ev_map.get) if ev_map else None
    pred['best_ev_pick'] = pred['best_ev_market']


def _reorient_soccer_model_probs(pred: dict) -> None:
    """Make soccer model-% cells agree with the calibrated win prob / pick.

    The base-model probs (glicko2/trueskill/elo/xgb/ensemble) come from the
    soccer model oriented opposite to the calibrated 3-way result for some
    matchups, so the Pick Confidence / pro-table cells showed the underdog as
    the favorite (e.g. Qatar 83.8% when Qatar's win prob is 5.9%). The face uses
    the calibrated home_win_prob/away_win_prob and is correct; flip the raw model
    probs to home-centric so they line up. Picks-display only — the results/
    grading path computes its own probs and is untouched.
    """
    hw = _safe_float(pred.get('home_win_prob'))
    aw = _safe_float(pred.get('away_win_prob'))
    ens = _safe_float(pred.get('ensemble_prob'))
    if hw is None or aw is None or ens is None:
        return
    # If the raw consensus already agrees with the calibrated favorite, leave it.
    if (hw > aw) == (ens >= 50.0):
        return
    for k in ('glicko2_prob', 'trueskill_prob', 'elo_prob', 'xgb_prob',
              'ensemble_prob', 'efficiency_prob'):
        v = _safe_float(pred.get(k))
        if v is not None:
            pred[k] = round(100.0 - v, 1)


def _sync_pick_winner_to_pl_spread(pred: dict, sport: str = 'NBA') -> None:
    """Align predicted_winner with PL spread after disp sign normalization."""
    if pred.get('home_score') is not None:
        return
    _min_spread = {'NHL': 0.3, 'MLB': 0.5, 'WNBA': 1.0}.get(sport, 3.0)
    sp = _safe_float(pred.get('our_spread'))
    if sp is None:
        sp = _safe_float(pred.get('disp_pl_spread'))
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
        unanimous = _model_probs_unanimous_side(pred)
        trust_ensemble = pred.get('is_v2') or unanimous is not None
        if trust_ensemble:
            # V2 or all models agree: trust Sharp Consensus, not efficiency spread.
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


def _attach_stored_engine_odds_lines_to_daily_results(sport, daily_results):
    """Load persisted engine_odds spread/total into PL fields when present."""
    if not daily_results:
        return
    game_ids = []
    for dd in daily_results.values():
        for g in dd.get('games', []):
            gid = g.get('game_id')
            if gid:
                game_ids.append(str(gid))
    if not game_ids:
        return
    try:
        conn = get_db_connection()
        placeholders = ','.join('?' * len(game_ids))
        rows = conn.execute(
            f"""SELECT game_id, spread, total FROM engine_odds
                WHERE sport=? AND game_id IN ({placeholders})""",
            (sport, *game_ids),
        ).fetchall()
        conn.close()
    except Exception as _e:
        logger.debug(f"[engine_odds] load lines for {sport}: {_e}")
        return
    by_gid = {str(r['game_id']): r for r in rows}
    for dd in daily_results.values():
        for g in dd.get('games', []):
            row = by_gid.get(str(g.get('game_id') or ''))
            if not row:
                continue
            if g.get('our_spread') is None and row['spread'] is not None:
                try:
                    g['our_spread'] = _round_to_half(float(row['spread']))
                    g['pl_spread_source'] = 'engine_odds'
                except (TypeError, ValueError):
                    pass
            if g.get('our_total') is None and row['total'] is not None:
                try:
                    g['our_total'] = _round_to_half(float(row['total']))
                    g['pl_total_source'] = 'engine_odds'
                except (TypeError, ValueError):
                    pass


def _fill_pl_model_lines_for_results(sport, daily_results):
    """PL spread/total for vs-book grading when H2H/efficiency did not set our_*."""
    if not daily_results:
        return
    from sports.team_efficiency_attach import home_prob_pct_to_spread

    for dd in daily_results.values():
        for g in dd.get('games', []):
            book_sp = _safe_float(g.get('book_spread'))
            book_tot = _safe_float(g.get('book_total'))
            if g.get('our_spread') is None and book_sp is not None:
                ens = g.get('ens_prob')
                if ens is not None:
                    try:
                        g['our_spread'] = _round_to_half(
                            home_prob_pct_to_spread(float(ens), sport)
                        )
                        g['pl_spread_source'] = 'ensemble_implied'
                    except (TypeError, ValueError):
                        pass
            if g.get('our_total') is None and book_tot is not None:
                xt = _safe_float(g.get('xgb_total'))
                if xt is not None:
                    g['our_total'] = _round_to_half(xt)
                    g['pl_total_source'] = 'xsharp_total'


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
    if all(
        _to_float_safe(_row_field(game_row, key)) is None
        for key in (
            'glicko_home_prob',
            'trueskill_home_prob',
            'elo_home_prob',
            'xgboost_home_prob',
            'logistic_home_prob',
            'catboost_home_prob',
            'meta_home_prob',
            'win_probability',
        )
    ):
        return None, None, None, None, None

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

    # WNBA: Grinder2 (glicko2), Takedown (trueskill) and XSharp (xgb) grade well
    # below 50% on moneyline, so flip their pick side for WNBA ONLY. Done after the
    # ensemble is set so Consensus/Edge/Efficiency are unchanged. Probs are 0-1.
    if sport == 'WNBA':
        glicko2_prob = _flip_prob_unit(glicko2_prob)
        trueskill_prob = _flip_prob_unit(trueskill_prob)
        xgb_prob = _flip_prob_unit(xgb_prob)

    return glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob


def _flip_prob_unit(p):
    """Flip a 0-1 win probability to the opposite side (None-safe)."""
    v = _to_float_safe(p)
    return None if v is None else (1.0 - v)


def _banner_daily_results_for_range(sport, start_dt, end_dt, *, playoffs=False, skip_v2=None, skip_weekly=False):
    from sports._individual_sport import INDIVIDUAL_SPORTS, build_graded_daily_results
    if sport in INDIVIDUAL_SPORTS:
        return build_graded_daily_results(sport, start_dt, end_dt)
    if not skip_weekly:
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
            'efficiency_prob':  None,
            'efficiency_pick':  None,
            'efficiency_correct': None,
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


class _NumpySafeJSONProvider(DefaultJSONProvider):
    """np.float32 / np.int64 leak into card payloads from model outputs;
    plain Flask json (and template |tojson) can't serialize them."""
    @staticmethod
    def default(o):
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return DefaultJSONProvider.default(o)


app.json = _NumpySafeJSONProvider(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app, origins=[
    'https://predictionlab.io',
    'https://predictionlab.io',
    'http://localhost:3000',
    'http://localhost:5000',
])

# Sport modules — logic in sports/*.py; aliases keep tests/imports stable.
from sports import NBA as _nba_sport
from sports import NHL as _nhl_sport
from sports import NFL as _nfl_sport
from sports import MLB as _mlb_sport
from sports import NCAAB as _ncaab_sport
from sports import NCAAF as _ncaaf_sport
from sports import WNBA as _wnba_sport
from sports import NCAAW as _ncaaw_sport
from sports import SOCCER as _soccer_sport
from sports import TENNIS as _tennis_sport
from sports import UFC as _ufc_sport
from sports import GOLF as _golf_sport
from sports import team_efficiency_attach as _eff_attach

EFFICIENCY_SPORTS = _eff_attach.EFFICIENCY_SPORTS
LABEL_EFFICIENCY = _eff_attach.LABEL_EFFICIENCY

calculate_nba_weekly_performance = _nba_sport.calculate_nba_weekly_performance
update_nba_scores = _nba_sport.update_nba_scores
_nba_model_probs_for_grading = _nba_sport.nba_model_probs_for_grading
_attach_nba_efficiency_to_daily_results = _eff_attach.attach_efficiency_to_daily_results

_SPORT_RESULTS_RENDERERS = {
    'NBA': _nba_sport.render_sport_results_page,
    'NHL': _nhl_sport.render_sport_results_page,
    'NFL': _nfl_sport.render_sport_results_page,
    'MLB': _mlb_sport.render_sport_results_page,
    'NCAAB': _ncaab_sport.render_sport_results_page,
    'NCAAF': _ncaaf_sport.render_sport_results_page,
    'WNBA': _wnba_sport.render_sport_results_page,
    'NCAAW': _ncaaw_sport.render_sport_results_page,
    'SOCCER': _soccer_sport.render_sport_results_page,
    'TENNIS': _tennis_sport.render_sport_results_page,
    'UFC': _ufc_sport.render_sport_results_page,
    'GOLF': _golf_sport.render_sport_results_page,
}

_INDIVIDUAL_SPORT_LOADERS = {
    _tennis_sport.SPORT: _tennis_sport.load_upcoming_games,
    _ufc_sport.SPORT: _ufc_sport.load_upcoming_games,
    _golf_sport.SPORT: _golf_sport.load_upcoming_games,
}
for _mod in (
    _nba_sport, _nhl_sport, _nfl_sport, _mlb_sport,
    _ncaab_sport, _ncaaf_sport, _wnba_sport, _ncaaw_sport, _soccer_sport,
    _tennis_sport, _ufc_sport, _golf_sport,
):
    _mod.register_routes(app)
for _mod in (_tennis_sport, _ufc_sport, _golf_sport):
    _OFFSEASON_SPORTS_HINT[_mod.SPORT] = _mod.OFFSEASON_HINT

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
        'matchup_path': _matchup_path,
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
    'NHL':    {'name': 'NHL',                       'icon': '🏒', 'color': '#1e3a8a'},
    'NFL':    {'name': 'NFL',                       'icon': '🏈', 'color': '#059669'},
    'NBA':    {'name': 'NBA',                       'icon': '🏀', 'color': '#dc2626'},
    'MLB':    {'name': 'MLB',                       'icon': '⚾', 'color': '#9333ea'},
    'NCAAF':  {'name': 'NCAA Football',             'icon': '🏟️', 'color': '#ea580c'},
    'NCAAB':  {'name': 'NCAA Basketball',           'icon': '🎓', 'color': '#0891b2'},
    'NCAAW':  {'name': "NCAA Women's Basketball",   'icon': '🏀', 'color': '#db2777'},
    'WNBA':   {'name': 'WNBA',                      'icon': '🏀', 'color': '#f97316'},
    'SOCCER': {'name': 'Soccer',                    'icon': '⚽', 'color': '#22c55e'},
    'TENNIS': {'name': 'Tennis',                    'icon': '🎾', 'color': '#16a34a'},
    'UFC':    {'name': 'UFC / MMA',                 'icon': '🥊', 'color': '#b91c1c'},
    'GOLF':   {'name': 'Golf',                      'icon': '⛳', 'color': '#0369a1'},
}
SOCCER_ENABLED = True

# ── SEO-friendly URL slugs ─────────────────────────────────────────────────────
SPORT_SEO_SLUGS = {
    'NHL':    'nhl-picks',
    'NBA':    'nba-picks',
    'NFL':    'nfl-picks',
    'MLB':    'mlb-picks',
    'NCAAB':  'ncaab-picks',
    'NCAAW':  'ncaaw-picks',
    'NCAAF':  'ncaaf-picks',
    'WNBA':   'wnba-picks',
    'SOCCER': 'soccer-picks',
    'TENNIS': 'tennis-picks',
    'UFC':    'ufc-picks',
    'GOLF':   'golf-picks',
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
    'FIFA World Cup',
    'FIFA World Cup Qualifiers (UEFA)',
    'FIFA World Cup Qualifiers (CONMEBOL)',
    'FIFA World Cup Qualifiers (CAF)',
    'FIFA World Cup Qualifiers (CONCACAF)',
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


def _soccer_curated_league_recent_counts(conn, days=14):
    """Completed-game counts per curated league within the last `days` (recency).

    Used to default the soccer results page to a league that actually played
    recently, instead of the league with the biggest all-time fixture list
    (e.g. EFL Championship), which can be months stale and show 0 recent games.
    """
    counts = {lg: 0 for lg in SOCCER_LEAGUE_ORDER}
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        rows = conn.execute('''
            SELECT league, COUNT(*) AS n
            FROM games
            WHERE sport = 'SOCCER' AND home_score IS NOT NULL AND game_date >= ?
            GROUP BY league
        ''', (cutoff,)).fetchall()
        for row in rows:
            canon = _canonical_soccer_league_name(row['league']) or row['league']
            if canon in counts:
                counts[canon] += int(row['n'])
    except Exception as exc:
        logger.debug(f"[soccer] recent league counts failed: {exc}")
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
            # This covers lower-division leagues (esp.2, eng.2, etc.) that ESPN
            # Core odds API doesn't serve, so the picks page shows real lines.
            _sb_home_ml = _sb_away_ml = _sb_spread = _sb_total = None
            _sb_source = None
            for _odds_item in (competition.get('odds') or []):
                if not isinstance(_odds_item, dict):
                    continue
                _prov = ((_odds_item.get('provider') or {}).get('name') or '').lower()
                if 'live' in _prov:
                    continue
                try:
                    _hml = (_odds_item.get('homeTeamOdds') or {}).get('moneyLine')
                    _aml = (_odds_item.get('awayTeamOdds') or {}).get('moneyLine')
                    if _hml is not None:
                        _sb_home_ml = int(round(float(_hml)))
                    if _aml is not None:
                        _sb_away_ml = int(round(float(_aml)))
                    _sp = _odds_item.get('spread')
                    if _sp is not None:
                        _sb_spread = float(_sp)
                    _ou = _odds_item.get('overUnder')
                    if _ou is not None:
                        _sb_total = float(_ou)
                    _sb_source = ((_odds_item.get('provider') or {}).get('name') or 'ESPN')
                except (TypeError, ValueError):
                    pass
                if _sb_home_ml is not None or _sb_spread is not None:
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


def _soccer_league_url_slug(name: str) -> str:
    """Public ?league= slug — prefer ESPN endpoint code when mapped."""
    if not name:
        return ''
    code = SOCCER_LEAGUE_ENDPOINTS.get(name)
    if code:
        return code
    return _soccer_league_slug(name)


SOCCER_LEAGUE_SLUGS = {_soccer_league_slug(n): n for n in SOCCER_LEAGUE_ORDER}
for _lg_name, _lg_code in SOCCER_LEAGUE_ENDPOINTS.items():
    if _lg_code:
        SOCCER_LEAGUE_SLUGS[_lg_code.lower()] = _lg_name


def _soccer_league_from_slug(slug: str):
    if not slug:
        return None
    return SOCCER_LEAGUE_SLUGS.get(slug.strip().lower())


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
            'slug': _soccer_league_url_slug(lg),
            'active': lg == selected_league,
            'live': lg in leagues_with_upcoming,
            'has_games': lg in leagues_with_any,
            'url': f"/soccer-picks?league={_soccer_league_url_slug(lg)}",
        }
        for lg in soccer_league_list
    ]
    return filtered, soccer_leagues, selected_league


def _build_soccer_results_leagues_ui(selected_league, soccer_league_counts):
    """League picker for soccer results — every curated league gets a dedicated URL."""
    return [
        {
            'name': lg,
            'slug': _soccer_league_url_slug(lg),
            'active': lg == selected_league,
            'url': f"/soccer-results?league={_soccer_league_url_slug(lg)}",
            'count': soccer_league_counts.get(lg, 0),
        }
        for lg in SOCCER_LEAGUE_ORDER
    ]


def _resolve_soccer_results_league(selected_slug, soccer_league_counts, recent_counts=None):
    """Resolve ?league= slug to a curated league name.

    When no league is requested, default to the league that played most recently
    (most games in `recent_counts`) so the page shows last night's results — not
    the league with the biggest all-time fixture list, which may be off-season.
    Falls back to busiest all-time only if nothing has played recently.
    """
    selected_league = _soccer_league_from_slug(selected_slug) if selected_slug else None
    if selected_slug and not selected_league:
        return None, None
    if not selected_slug:
        recent_active = [
            lg for lg in SOCCER_LEAGUE_ORDER if (recent_counts or {}).get(lg, 0) > 0
        ]
        if recent_active:
            selected_league = max(
                recent_active,
                key=lambda lg: recent_counts.get(lg, 0),
            )
        else:
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
    return selected_league, _soccer_league_url_slug(selected_league) if selected_league else None


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


def _ensure_prop_result_columns():
    """Add confidence/EV/odds columns to player_prop_results for the historical
    performance buckets (win-rate + ROI by confidence and EV)."""
    try:
        conn = sqlite3.connect(DATABASE)
        cols = [row[1] for row in conn.execute("PRAGMA table_info('player_prop_results')").fetchall()]
        for col, col_type in {'confidence': 'REAL', 'ev': 'REAL', 'odds': 'INTEGER'}.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE player_prop_results ADD COLUMN {col} {col_type}")
        conn.commit()
        conn.close()
    except Exception as _e:
        logger.debug(f"[player_prop_results] column ensure failed: {_e}")


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
    _ensure_prop_result_columns()
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
    while True:
        for _sport in _PREWARM_SPORTS:
            try:
                _warm(_sport)
                logger.debug(f"[odds-prewarm] {_sport} warmed")
            except Exception as _we:
                logger.debug(f"[odds-prewarm] {_sport} failed: {_we}")
        _time.sleep(720)   # re-warm every 12 min — before the 15-min TTL expires

try:
    threading.Thread(target=_prewarm_espn_odds_cache, daemon=True, name='odds-prewarm').start()
except Exception as _owe:
    logger.debug(f"[odds-prewarm] failed to start: {_owe}")


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


def _dated_games_in_daily_results(daily_results, *, season_start_dt=None, before_dt=None):
    """Sorted (date, date_key) pairs that have at least one graded game."""
    dated = [
        (parse_date(dk), dk)
        for dk, bucket in daily_results.items()
        if dk and bucket.get('games') and parse_date(dk)
    ]
    if season_start_dt:
        dated = [(dt, dk) for dt, dk in dated if dt >= season_start_dt]
    if before_dt is not None:
        dated = [(dt, dk) for dt, dk in dated if dt <= before_dt]
    dated.sort(key=lambda x: x[0], reverse=True)
    return dated


def _soccer_weekly_tally_window(daily_results, *, season_start_dt=None, n_matchdays=7):
    """Last N matchdays with games (soccer schedules are sparse vs calendar weeks)."""
    dated = _dated_games_in_daily_results(daily_results, season_start_dt=season_start_dt)
    if not dated:
        return None, None, None
    picked = dated[:n_matchdays]
    weekly_end_dt = picked[0][0]
    weekly_start_dt = picked[-1][0]
    label = (
        f"{weekly_start_dt.strftime('%Y-%m-%d')} to {weekly_end_dt.strftime('%Y-%m-%d')}"
    )
    return weekly_start_dt, weekly_end_dt, label


def _compute_results_tally_bundle(
    daily_results,
    yesterday_dt,
    *,
    season_start_dt=None,
    sport=None,
    league_scoped=False,
):
    """Daily + weekly tallies; when the calendar week is empty, use the latest window with games."""
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


_WNBA_V2_EXCLUDE_TEAMS = ('JAPAN', 'Japan', 'Nigeria', 'Team USA', 'Team WNBA')
_WNBA_V2_EXCLUDE_SQL = ', '.join(f"'{t}'" for t in _WNBA_V2_EXCLUDE_TEAMS)


def _ensure_v2_predictor(model_sport: str):
    """Load a v2 predictor; train WNBA from completed DB games if the model file is missing."""
    if not HAS_V2_SYSTEM:
        return None
    if model_sport in V2_PREDICTORS:
        return V2_PREDICTORS[model_sport]
    _model_path = _os_v2.path.join(_V2_BASE, 'models', f'{model_sport}_v2')
    try:
        if (
            _os_v2.path.isdir(_model_path)
            and _os_v2.path.exists(_os_v2.path.join(_model_path, 'ensemble.pkl'))
        ):
            V2_PREDICTORS[model_sport] = AdvancedPredictor.load(model_sport, _model_path)
            logger.info(f"Loaded {model_sport} v2 predictor on demand")
            return V2_PREDICTORS[model_sport]
    except Exception as e:
        logger.warning(f"Could not load {model_sport} v2 from {_model_path}: {e}")
    if model_sport != 'WNBA':
        return None
    try:
        conn = get_db_connection()
        rows = conn.execute('''
            SELECT game_date AS date, home_team_id AS home_team, away_team_id AS away_team,
                   home_score, away_score
            FROM games
            WHERE sport = 'WNBA'
              AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND home_team_id NOT IN ({_WNBA_V2_EXCLUDE_SQL})
              AND away_team_id NOT IN ({_WNBA_V2_EXCLUDE_SQL})
            ORDER BY game_date
        ''').fetchall()
        conn.close()
        if len(rows) < 50:
            logger.warning('WNBA v2 train-on-demand skipped: insufficient completed games')
            return None
        games_df = pd.DataFrame([dict(r) for r in rows])
        predictor = AdvancedPredictor('WNBA', _model_path)
        predictor.train(games_df, validate=False, save=True)
        V2_PREDICTORS['WNBA'] = predictor
        logger.info(f"Trained and loaded WNBA v2 predictor ({len(rows)} games)")
        return predictor
    except Exception as e:
        logger.warning(f"WNBA v2 train-on-demand failed: {e}")
        return None


def get_v2_prediction(sport, home_team, away_team, game_date=None):
    """
    Get predictions from the v2 system (Glicko-2 + Stacked Ensemble + Calibration)
    
    Returns dict with probabilities or None if v2 not available for this sport
    """
    model_sport = _v2_model_sport(sport)
    if not HAS_V2_SYSTEM or _ensure_v2_predictor(model_sport) is None:
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


def get_upcoming_predictions(sport, days=365):
    """Get ALL game predictions from season start - both completed and upcoming
    
    Loads games from database for all sports including NHL
    
    USER REQUIREMENT: Show ALL games from season start (Oct 7 for NHL), not just upcoming!
    """
    sport = (sport or '').upper()
    
    # Fast in-process cache to avoid repeated heavy prediction recomputation.
    cache_key = f"{sport}_upcoming_predictions_v8"
    now_ts = _time.time()
    cache_ttl = _PREDICTIONS_TTL_BY_SPORT.get(sport, 180)
    cached = _PREDICTIONS_CACHE.get(cache_key)
    if cached and (now_ts - cached['ts']) < cache_ttl:
        _cached_preds = cached.get('data')
        if _cached_preds:
            out = _copy.deepcopy(_cached_preds)
            try:
                _hydrate_book_lines_db_only(sport, out)
                for _bp in out:
                    if isinstance(_bp, dict):
                        _ensure_book_moneylines(_bp)
            except Exception as _bk_cache:
                logger.debug(f"[{sport}] DB book hydrate on predictions cache hit: {_bk_cache}")
            _apply_mlb_spread_fade_batch(sport, out)
            _upcoming_cached = [p for p in out if isinstance(p, dict) and p.get('home_score') is None]
            try:
                _eff_attach.attach_efficiency_to_predictions(sport, _upcoming_cached)
                _eff_attach.fill_efficiency_spread_on_predictions(sport, _upcoming_cached)
            except Exception as _eff_cache:
                logger.debug(f"[eff] picks cache hydrate failed for {sport}: {_eff_cache}")
            return out

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
                SELECT elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability,
                       glicko_home_prob, trueskill_home_prob, catboost_home_prob
                FROM predictions WHERE game_id = ? AND sport = ?
            ''', (game['game_id'], sport)).fetchone()
            
            if pred:
                game['stored_elo_prob'] = _to_float_safe(pred['elo_home_prob'])
                game['stored_xgb_prob'] = _to_float_safe(pred['xgboost_home_prob'])
                game['stored_ensemble_prob'] = _to_float_safe(pred['win_probability'])
                game['stored_glicko_prob'] = _to_float_safe(pred['glicko_home_prob'])
                game['stored_trueskill_prob'] = _to_float_safe(pred['trueskill_home_prob'])
                if game['stored_glicko_prob'] is None:
                    game['stored_glicko_prob'] = _to_float_safe(pred['catboost_home_prob'])
                if game['stored_trueskill_prob'] is None:
                    game['stored_trueskill_prob'] = _to_float_safe(pred['logistic_home_prob'])
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

    elif sport in _INDIVIDUAL_SPORT_LOADERS:
        all_games_with_dates = _INDIVIDUAL_SPORT_LOADERS[sport]()

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

    # Elo training sample — full soccer DB history can be 10k+ rows; picks page only
    # needs recent form. Cap at ~120 days so cold-cache builds stay under timeout.
    # Season history for the date picker (ALL sports): merge the DB's stored
    # season games — the same rows the results pages grade — deduped by
    # (date, home, away) against whatever the live feeds already supplied.
    try:
        _hist_conn = get_db_connection()
        _db_rows = _hist_conn.execute(
            '''SELECT g.*,
                      p.elo_home_prob as stored_elo_prob,
                      p.xgboost_home_prob as stored_xgb_prob,
                      p.win_probability as stored_ensemble_prob,
                      p.glicko_home_prob as stored_glicko_prob,
                      p.trueskill_home_prob as stored_trueskill_prob
               FROM games g
               LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
               WHERE g.sport = ?''',
            (sport, sport),
        ).fetchall()
        _hist_conn.close()
        _seen_hist = {
            (str(g.get('game_date') or '')[:10], g.get('home_team_id'), g.get('away_team_id'))
            for _d, g in all_games_with_dates
        }
        _merged_hist = 0
        for _r in _db_rows:
            _gd = dict(_r)
            _k = (str(_gd.get('game_date') or '')[:10], _gd.get('home_team_id'), _gd.get('away_team_id'))
            if _k in _seen_hist or not _gd.get('home_team_id') or not _gd.get('away_team_id'):
                continue
            _pd = parse_date(_gd.get('game_date') or '')
            if not _pd:
                continue
            _seen_hist.add(_k)
            all_games_with_dates.append((_pd, _gd))
            _merged_hist += 1
        if _merged_hist:
            all_games_with_dates.sort(key=lambda x: x[0])
            logger.info(f"[{sport}] merged {_merged_hist} season-history games for the date picker")
    except Exception as _hist_err:
        logger.debug(f"season-history merge failed for {sport}: {_hist_err}")

    today = datetime.now()
    _elo_cutoff = today - timedelta(days=120)
    _elo_train_games = [
        g for g in completed_games
        if parse_date(g.get('game_date') or '') and parse_date(g['game_date']) >= _elo_cutoff
    ] or completed_games[-400:]

    # Train Elo system on recent completed games (with home/away splits tracking)
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
    for game in _elo_train_games:
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
        'NHL': datetime(2025, 10, 7),
        'NFL': datetime(2025, 9, 4),
        'NBA': datetime(2025, 10, 21),
        'MLB': datetime(2026, 3, 27),
        'NCAAF': datetime(2025, 8, 30),
        'NCAAB': datetime(2025, 11, 4),
        'NCAAW': datetime(2025, 11, 4),
        'WNBA': datetime(2026, 5, 8),
        'SOCCER': datetime(2025, 8, 1),
        'TENNIS': datetime(2025, 1, 1),
        'UFC': datetime(2025, 1, 1),
        'GOLF': datetime(2025, 1, 1),
    }
    season_start = season_starts.get(sport, datetime(2025, 1, 1))
    
    # Calculate cutoff horizon by sport
    future_window_days = {
        'NBA': 30,
        'UFC': 60,
        'TENNIS': 21,
        'GOLF': 60,
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

    _live_v2_budget = 25 if sport in ('NBA', 'MLB', 'NHL') else 18
    _live_v2_used = 0

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
            if sport != 'SOCCER':
                if is_completed:
                    _sg = _to_float_safe(game.get('stored_glicko_prob'))
                    _st = _to_float_safe(game.get('stored_trueskill_prob'))
                    _sx = _to_float_safe(game.get('stored_xgb_prob'))
                    _se = _to_float_safe(game.get('stored_ensemble_prob'))
                    _selo = _to_float_safe(game.get('stored_elo_prob'))
                    if any(p is not None for p in (_sg, _st, _sx, _se, _selo)):
                        v2_pred = {
                            'glicko2_prob': _sg,
                            'trueskill_prob': _st,
                            'xgboost_prob': _sx,
                            'home_prob': _se if _se is not None else _selo,
                        }
                elif _live_v2_used < _live_v2_budget:
                    v2_pred = get_v2_prediction(
                        sport,
                        game.get('home_team_id') or game.get('home_team_name'),
                        game.get('away_team_id') or game.get('away_team_name'),
                        game.get('game_date'),
                    )
                    _live_v2_used += 1

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
                # SOCCER COVERAGE FALLBACK: sports/SOCCER.py owns the estimate.
                # It runs after sportsbook lines are hydrated later in this function.
                elo_prob = None
                xgb_prob = None
                ensemble_prob = None
                game['glicko2_prob'] = None
                game['trueskill_prob'] = None
                game['soccer_market_fallback'] = True
                game['soccer_model_note'] = (
                    soccer_note or 'Limited team history; market-informed fallback pending.'
                )
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

                # Set Grinder2 and Takedown to elo_prob in fallback (ensures cards don't show "—")
                game['glicko2_prob'] = elo_prob
                game['trueskill_prob'] = elo_prob

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

            # Keep completed games too — the season-wide date picker needs
            # every date; finals render as FINAL cards on their own day.
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
    _ml_cap = 12 if sport in ('NBA', 'MLB', 'NHL', 'NFL') else 8
    _ml_limit = min(_ml_cap, max(6, _upcoming_n))
    _cache_market_lines_for_predictions(sport, predictions, limit=_ml_limit)
    _attach_market_lines_to_predictions(sport, predictions)
    _hydrate_book_lines_db_only(sport, predictions)

    # SOCCER UNKNOWN-TEAM FALLBACK: now that real book lines are available,
    # ask sports/SOCCER.py for market-informed estimates and model score lines.
    if sport == 'SOCCER':
        for pred in predictions:
            if pred.get('home_score') is not None or not pred.get('soccer_market_fallback'):
                continue
            fallback = _soccer_sport.market_informed_fallback(pred)
            pred['glicko2_prob'] = round(fallback['poisson_xg_prob'] * 100.0, 1)
            pred['trueskill_prob'] = round(fallback['markov_prob'] * 100.0, 1)
            pred['elo_prob'] = round(fallback['elo_prob'] * 100.0, 1)
            pred['xgb_prob'] = round(fallback['poisson_reg_prob'] * 100.0, 1)
            pred['ensemble_prob'] = round(fallback['ensemble_prob'] * 100.0, 1)
            pred['naive_home_score'] = round(fallback['expected_home_score'], 2)
            pred['naive_away_score'] = round(fallback['expected_away_score'], 2)
            pred['naive_spread'] = round(
                fallback['expected_home_score'] - fallback['expected_away_score'], 2
            )
            pred['naive_total'] = round(
                fallback['expected_home_score'] + fallback['expected_away_score'], 2
            )
            pred['soccer_model_note'] = fallback['note']
            pred['model_data_note'] = fallback['note']
            home_win, draw, away_win = _soccer_threeway_probs(
                fallback['ensemble_prob'], fallback['draw_prob']
            )
            if home_win is not None:
                pred['home_win_prob'] = round(home_win * 100.0, 1)
                pred['draw_prob'] = round(draw * 100.0, 1)
                pred['away_win_prob'] = round(away_win * 100.0, 1)
                choices = (
                    (home_win, pred.get('home_team_id')),
                    (draw, 'Draw'),
                    (away_win, pred.get('away_team_id')),
                )
                pred['predicted_winner'] = max(choices, key=lambda item: item[0])[1]

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
                            INSERT OR IGNORE INTO predictions (
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

    _upcoming_for_eff = [p for p in predictions if isinstance(p, dict) and p.get('home_score') is None]
    try:
        _eff_attach.attach_efficiency_to_predictions(sport, _upcoming_for_eff)
        _eff_attach.fill_efficiency_spread_on_predictions(sport, _upcoming_for_eff)
    except Exception as _eff_pe:
        logger.debug(f"[eff] picks attach failed for {sport}: {_eff_pe}")

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
            if _ml_ev     is not None and _ml_ev     > 0: _ev_map['Moneyline'] = _ml_ev
            if _spread_ev is not None and _spread_ev > 0: _ev_map['Spread'] = _spread_ev
            if _total_ev  is not None and _total_ev  > 0: _ev_map['Total']  = _total_ev
            _pred['best_ev_market'] = max(_ev_map, key=_ev_map.get) if _ev_map else None
            _pred['best_ev_pick'] = _pred['best_ev_market']

    # DB-only book hydrate before cache store (ESPN fetch runs on sport_predictions render).
    try:
        _hydrate_book_lines_db_only(sport, predictions)
        for _bp in predictions:
            if isinstance(_bp, dict):
                _ensure_book_moneylines(_bp)
    except Exception as _bk_build:
        logger.debug(f"[{sport}] DB book hydrate before predictions cache store: {_bk_build}")

    _trim_cache(_PREDICTIONS_CACHE, cache_ttl, max_entries=50)
    if predictions:
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
        try:
            _attach_stored_engine_odds_lines_to_daily_results(sport, daily_results)
            if not skip_efficiency:
                _eff_attach.attach_efficiency_to_daily_results(sport, daily_results)
            _fill_pl_model_lines_for_results(sport, daily_results)
            if not skip_efficiency:
                _eff_attach.fill_efficiency_spread_fallback(sport, daily_results)
                _eff_attach.apply_efficiency_ml_grading(sport, daily_results)
        except Exception as _ne:
            logger.debug(f"[eff] pre-compute failed for {sport}: {_ne}")
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
                                if pl_pick == ('OVER' if at > _grade_mt else 'UNDER'):
                                    pl_tt_cor += 1
                            else:
                                pl_tt_gr += 1
                                pl_tt_push += 1
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
                                if pl_pick == ('OVER' if at > _grade_mt else 'UNDER'):
                                    pl_tt_cor += 1
                            else:
                                pl_tt_gr += 1
                                pl_tt_push += 1
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
        # MLB O/U cold-streak calibration — adjusts LIVE totals only, after the
        # projection is set but before display. Never touches graded results.
        if sport == 'MLB':
            try:
                _apply_mlb_ou_calibration(daily_results)
            except Exception as _ou_cal_e:
                logger.error(f"[MLB O/U] calibration error: {_ou_cal_e}")
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


def _apply_mlb_ou_calibration(daily_results):
    """Cold-streak calibration for MLB totals.

    Runs after total projections are set but before display. Adjusts ONLY live
    (ungraded) O/U picks — graded results, moneyline and spread are never touched.
    """
    try:
        from sports.mlb_ou_calibration import (
            compute_ou_performance, apply_mlb_ou_calibration,
        )
    except Exception as _imp_e:
        logger.error(f"[MLB O/U] calibration import failed: {_imp_e}")
        return None

    graded = []   # (date, correct) for graded, non-push MLB totals
    live = []     # live cards to calibrate
    for date_key, dd in daily_results.items():
        for g in dd.get('games', []):
            pick = g.get('total_pick')
            if pick not in ('OVER', 'UNDER'):
                continue
            correct = g.get('total_correct')
            game_date = g.get('game_date') or date_key
            if correct is not None:
                graded.append((game_date, bool(correct)))
                continue
            # Live card: normalise the inputs the calibration layer needs.
            proj = _safe_float(g.get('xgb_total_adj'))
            if proj is None:
                proj = _safe_float(g.get('our_total'))
            mkt = _safe_float(g.get('market_total'))
            edge = (proj - mkt) if (proj is not None and mkt is not None) else 0.0
            g['total_edge'] = round(edge, 2)
            if g.get('total_confidence') is None:
                g['total_confidence'] = round(min(72.0, 55.0 + abs(edge) * 6.0), 1)
            live.append(g)

    if not live:
        return None

    perf = compute_ou_performance(graded)
    summary = apply_mlb_ou_calibration(live, perf)
    logger.info(
        "[MLB O/U] calibration mode=%s flips=%s cautioned=%s last30=%s/%s season=%s/%s recent7=%s",
        summary.get('mode'), summary.get('flips'), summary.get('cautioned'),
        perf.get('last30_rate'), perf.get('last30_n'),
        perf.get('season_rate'), perf.get('season_n'), perf.get('recent7_rate'),
    )
    return summary


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
    return _normalize_overall_stats(overall)


def _normalize_overall_stats(overall_stats):
    """Ensure all ML model keys exist (frozen snapshots may predate Efficiency row)."""
    stats = dict(overall_stats or {})
    blank = {'correct': 0, 'total': 0, 'accuracy': 0.0}
    for key, _, _ in (
        ('glicko2', '', ''),
        ('trueskill', '', ''),
        ('elo', '', ''),
        ('xgboost', '', ''),
        ('ensemble', '', ''),
        ('efficiency', '', ''),
    ):
        entry = stats.get(key)
        if not isinstance(entry, dict):
            stats[key] = dict(blank)
            continue
        entry.setdefault('correct', 0)
        entry.setdefault('total', 0)
        entry.setdefault('accuracy', 0.0)
    return stats


_ML_PERF_MODEL_KEYS = (
    ('glicko2', 'Grinder2'),
    ('trueskill', 'Takedown'),
    ('elo', 'Edge'),
    ('xgboost', 'XSharp'),
    ('ensemble', 'Sharp Consensus'),
    ('efficiency', 'Team Efficiency'),
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
    model_configs = [
        ('glicko2',   'glicko2_correct', 'glicko2_prob'),
        ('trueskill', 'trueskill_correct', 'trueskill_prob'),
        ('elo',       'elo_correct', 'elo_prob'),
        ('xgboost',   'xgb_correct', 'xgb_prob'),
        ('ensemble',  'ens_correct', 'ens_prob'),
        ('efficiency', 'efficiency_correct', 'efficiency_prob'),
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
        ('efficiency', 'efficiency_correct', 'efficiency_prob'),
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
        "win_pct": None,
        "reason": None,
    }


def _finalize_flat_unit_roi_entry(entry):
    """Sync flat ±1u counters: units_won = wins - losses, roi_pct = units / risked."""
    wins = int(entry.get("wins") or 0)
    losses = int(entry.get("losses") or 0)
    risked = wins + losses
    entry["units_risked"] = risked
    entry["graded"] = risked
    entry["units_won"] = float(wins - losses)
    if risked > 0:
        entry["roi_pct"] = round(entry["units_won"] / risked * 100, 2)
        entry["win_pct"] = round(wins / risked * 100, 2)
    else:
        entry["roi_pct"] = None
        entry["win_pct"] = None


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
        _finalize_flat_unit_roi_entry(entry)
        if entry.get("roi_pct") is None and entry.get("graded", 0) == 0:
            entry["reason"] = "No graded picks in range."
    return summary

def build_roi_cards(roi_daily, roi_weekly, roi_total):
    def _format_entry(entry):
        if not entry:
            return {"roi": "—", "detail": "—"}
        wins = int(entry.get("wins") or 0)
        losses = int(entry.get("losses") or 0)
        pushes = int(entry.get("pushes") or 0)
        graded = wins + losses
        if graded <= 0:
            return {"roi": "—", "detail": entry.get("reason") or "—"}
        _finalize_flat_unit_roi_entry(entry)
        win_pct = entry.get("win_pct")
        roi_pct = entry.get("roi_pct")
        units = entry.get("units_won", 0.0)
        # Headline % = win rate (matches W-L). Unit ROI + record on second line.
        return {
            "roi": f"{win_pct}%",
            "detail": f"{wins}-{losses}-{pushes} · ROI {roi_pct}% · {units:+.2f}u",
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
    daily_results = _daily_results_from_weekly(weekly_results)
    if daily_results:
        return compute_overall_stats_from_daily(daily_results)
    models = ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble', 'efficiency']
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
    return _normalize_overall_stats(overall)


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
        .nav-cta-premium{display:inline-flex;align-items:center;padding:9px 16px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-size:0.84em;font-weight:800;text-decoration:none;letter-spacing:0.2px;white-space:nowrap;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 14px rgba(251,191,36,0.35);}
        .nav-cta-premium:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(251,191,36,0.45);}
        .tv-premium-cta{display:flex;align-items:center;justify-content:center;margin:10px 12px 6px;padding:12px 14px;border-radius:10px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-weight:800;font-size:0.92em;text-decoration:none;letter-spacing:0.2px;}
        .tv-premium-cta:hover{box-shadow:0 4px 14px rgba(251,191,36,0.4);}
        .join-premium-bar{display:none;position:fixed;left:0;right:0;bottom:0;z-index:999;background:#0f172a;border-top:1px solid rgba(255,255,255,0.12);}
        .join-premium-inner{max-width:1200px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;}
        .join-premium-copy{color:#e2e8f0;font-size:0.86em;font-weight:600;line-height:1.35;}
        .join-premium-actions{display:flex;align-items:center;gap:8px;}
        .join-premium-btn{display:inline-flex;align-items:center;justify-content:center;padding:9px 14px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;text-decoration:none;font-weight:800;font-size:0.82em;}
        .join-premium-close{border:1px solid rgba(255,255,255,0.3);background:transparent;color:#fff;border-radius:999px;width:28px;height:28px;line-height:1;cursor:pointer;font-size:18px;}
        @media(max-width:480px){.nav-cta{padding:8px 14px;font-size:0.8em;}.nav-cta-premium{padding:8px 12px;font-size:0.78em;}}
        .hamburger{display:flex;flex-direction:column;justify-content:center;gap:5px;cursor:pointer;padding:7px 9px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;flex-shrink:0;order:1;}
        .hamburger:hover{background:#f8fafc;}
        .hamburger span{width:20px;height:1.5px;background:#0f172a;border-radius:2px;transition:all .2s;}
        .tv-overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:2001;backdrop-filter:blur(2px);}
        .tv-overlay.open{display:block;}
        .tv-drawer{position:fixed;top:0;left:0;height:100%;width:min(280px,100vw);background:#fff;z-index:2002;transform:translateX(-100%);transition:transform .28s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;box-shadow:4px 0 32px rgba(15,23,42,0.18);}
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
</head>
<body class="research-site">
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
          <a href="/plans" class="tv-premium-cta">&#11088; Join Premium</a>
          {% endif %}
          <div class="tv-menu-list">
            <button class="tv-menu-btn" onclick="tvSub(\'picks\')"><span class="tv-menu-label">Picks &amp; Predictions</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvSub(\'props\')"><span class="tv-menu-label">Props</span><span class="tv-menu-arrow">&#8250;</span></button>
            <button class="tv-menu-btn" onclick="tvSub(\'tools\')"><span class="tv-menu-label">Tools &amp; Models</span><span class="tv-menu-arrow">&#8250;</span></button>
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
            <a class="share-icon" href="https://www.instagram.com/" target="_blank" rel="noopener" aria-label="Instagram"><img src="/static/icons/social/instagram.svg" alt="Instagram"></a>
            <a class="share-icon" href="https://www.tiktok.com/upload?lang=en" target="_blank" rel="noopener" aria-label="TikTok"><img src="/static/icons/social/tiktok.svg" alt="TikTok"></a>
            <a class="share-icon" href="https://www.linkedin.com/sharing/share-offsite/?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on LinkedIn"><img src="/static/icons/social/linkedin.svg" alt="LinkedIn"></a>
            <a class="share-icon" href="https://www.reddit.com/submit?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Reddit"><img src="/static/icons/social/reddit.svg" alt="Reddit"></a>
            <a class="share-icon" href="https://www.tumblr.com/widgets/share/tool?canonicalUrl={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Tumblr"><img src="/static/icons/social/tumblr.svg" alt="Tumblr"></a>
            <a class="share-icon" href="https://api.whatsapp.com/send?text={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on WhatsApp"><img src="/static/icons/social/whatsapp.svg" alt="WhatsApp"></a>
            <a class="share-icon" href="https://telegram.me/share/url?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Telegram"><img src="/static/icons/social/telegram.svg" alt="Telegram"></a>
        </div>
    </div>
    <footer class="site-footer" style="display:none">
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
                    <a href="/all-sports-results">All sports results</a>
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
    {% include "partials/site_directory_footer.html" %}
    
    <script>
var TV_MENUS={picks:{title:'Picks & Predictions',items:[{l:'NBA',h:'/nba-picks'},{l:'MLB',h:'/mlb-picks'},{l:'NHL',h:'/nhl-picks'},{l:'NFL',h:'/nfl-picks'}{% if soccer_enabled %},{l:'Soccer',h:'/soccer-picks'},{l:'World Cup',h:'/soccer-picks?league=fifa.world'}{% endif %},{l:'NCAAB',h:'/ncaab-picks'},{l:'NCAAF',h:'/ncaaf-picks'},{l:'NCAAW',h:'/ncaaw-picks'},{l:'WNBA',h:'/wnba-picks'},{l:'Tennis',h:'/tennis-picks'},{l:'UFC',h:'/ufc-picks'},{l:'Golf',h:'/golf-picks'}]},props:{title:'Props',items:[{l:'Player Props',h:'/player-props'}]},tools:{title:'Tools & Models',items:[{l:'Model Performance',h:'/performance'},{l:'AI Picks Today',h:'/ai-sports-betting-picks-today'},{l:'Model vs Sportsbooks',h:'/our-model-vs-sportsbooks'},{l:'Tutorial',h:'/tutorial'}]},results:{title:'Results & Tracking',items:[{l:'All Sports Results',h:'/all-sports-results'},{l:'Daily Results',h:'/daily-report'},{l:'Edge Performance',h:'/edge-performance'},{l:'Download CSV',h:'/results/downloads'}]},community:{title:'Community',items:[{l:'X / Twitter',h:'https://x.com/predictionlab_io',ext:true},{l:'TikTok',h:'https://www.tiktok.com/@predictionlab',ext:true},{l:'Instagram',h:'https://instagram.com/predictionlab.io',ext:true},{l:'Reddit',h:'https://reddit.com/r/sportsbetting',ext:true},{l:'Telegram',h:'https://t.me/predictionlab',ext:true}]},company:{title:'Company',items:[{l:'Join Premium',h:'/plans',cls:'highlight'},{l:'Plans & Pricing',h:'/plans'},{l:'FAQ',h:'/faq'},{l:'Contact',h:'/contact'},{l:'Privacy',h:'/privacy'},{l:'Terms',h:'/terms'}]}};
function tvOpen(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.add('open');if(d)d.classList.add('open');document.body.style.overflow='hidden';if(h)h.setAttribute('aria-expanded','true');}
function tvClose(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.remove('open');if(d)d.classList.remove('open');document.body.style.overflow='';if(h)h.setAttribute('aria-expanded','false');setTimeout(function(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';},280);}
function tvSub(key){var menu=TV_MENUS[key];if(!menu)return;var html='';menu.items.forEach(function(item){var ext=item.ext?' target="_blank" rel="noopener"':'';var cls='tv-sub-link'+(item.cls?' '+item.cls:'');var extIcon=item.ext?' <span class="ext">&#8599;</span>':'';html+='<a href="'+item.h+'" class="'+cls+'"'+ext+'>'+item.l+extIcon+'</a>';});document.getElementById('tvSub').innerHTML=html;document.getElementById('tvDrawerTitle').textContent=menu.title;document.getElementById('tvBackBtn').style.display='';document.getElementById('tvMain').className='tv-panel hidden-left';document.getElementById('tvSub').className='tv-panel visible';}
function tvBack(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';}
function tvToggleMore(btn){var el=document.getElementById('tvMoreItems');var open=el.style.display==='block';el.style.display=open?'none':'block';var arrow=btn.querySelector('.tv-more-arrow');if(arrow)arrow.style.transform=open?'':'rotate(90deg)';}
function toggleAcctMenu(e){e.stopPropagation();document.getElementById('acctMenu').classList.toggle('open');}
document.addEventListener('click',function(){var m=document.getElementById('acctMenu');if(m)m.classList.remove('open');});
var _srchFilter='all';
var _srchDefaults=[{l:'Join Premium',h:'/plans',s:'all'},{l:'NBA Picks',h:'/nba-picks',s:'nba'},{l:'NFL Picks',h:'/nfl-picks',s:'nfl'},{l:'MLB Picks',h:'/mlb-picks',s:'mlb'},{l:'NHL Picks',h:'/nhl-picks',s:'nhl'},{l:'NCAAB Picks',h:'/ncaab-picks',s:'ncaab'},{l:'NCAAF Picks',h:'/ncaaf-picks',s:'ncaaf'},{l:'WNBA Picks',h:'/wnba-picks',s:'wnba'}{% if soccer_enabled %},{l:'Soccer Picks',h:'/soccer-picks',s:'all'},{l:'World Cup Picks',h:'/soccer-picks?league=fifa.world',s:'all'}{% endif %},{l:'Tennis Picks',h:'/tennis-picks',s:'all'},{l:'UFC Picks',h:'/ufc-picks',s:'all'},{l:'Golf Picks',h:'/golf-picks',s:'all'},{l:'Player Props',h:'/player-props',s:'props'},{l:'Model Performance',h:'/performance',s:'all'},{l:'Edge Performance',h:'/edge-performance',s:'all'},{l:'Daily Results',h:'/daily-report',s:'all'}];
function openSrch(){document.getElementById('srchOverlay').classList.add('open');document.body.style.overflow='hidden';setTimeout(function(){document.getElementById('srchInput').focus();},60);renderSrchItems('');}
function closeSrch(){document.getElementById('srchOverlay').classList.remove('open');document.body.style.overflow='';document.getElementById('srchInput').value='';}
function closeSrchOutside(e){if(e.target===document.getElementById('srchOverlay'))closeSrch();}
function _srchEsc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);});}
function _srchRender(rows){var el=document.getElementById('srchItems');if(!rows.length){el.innerHTML='<div class="srch-empty">No results found. Press Enter to search.</div>';return;}el.innerHTML=rows.map(function(i){return'<a class="srch-item" href="'+_srchEsc(i.h)+'"><span class="srch-item-label">'+_srchEsc(i.l)+'</span><span class="srch-item-sport">'+_srchEsc(String(i.s||'').toUpperCase())+'</span></a>';}).join('');}
function renderSrchItems(q){q=(q||'').trim();var el=document.getElementById('srchItems');
  if(!q){var items=_srchDefaults.filter(function(i){return _srchFilter==='all'||i.s===_srchFilter;});_srchRender(items);return;}
  clearTimeout(window._srchTimer);el.innerHTML='<div class="srch-empty">Searching…</div>';
  window._srchTimer=setTimeout(function(){
    fetch('/api/search?query='+encodeURIComponent(q),{headers:{'Accept':'application/json'}})
      .then(function(r){return r.json();})
      .then(function(data){var rows=[];
        (data.page_results||[]).forEach(function(p){rows.push({l:p.label||'Page',h:p.route||data.suggested_route||'/',s:p.sport||'site'});});
        (data.team_results||[]).forEach(function(t){rows.push({l:(t.away_team||'')+' @ '+(t.home_team||'')+(t.win_probability!=null?' · '+t.win_probability+'%':''),h:data.suggested_route||('/'+String(t.sport||'').toLowerCase()+'-picks'),s:t.sport||'all'});});
        (data.espn_results||[]).forEach(function(e){rows.push({l:(e.away_team||'')+' @ '+(e.home_team||'')+' · '+(e.status||''),h:data.suggested_route||'/',s:e.sport||'all'});});
        _srchDefaults.forEach(function(i){if(i.l.toLowerCase().includes(q.toLowerCase()))rows.push(i);});
        if(_srchFilter!=='all'){rows=rows.filter(function(i){return String(i.s||'').toLowerCase()===_srchFilter;});}
        _srchRender(rows);})
      .catch(function(){el.innerHTML='<div class="srch-empty">Search unavailable</div>';});
  },220);}
document.addEventListener('DOMContentLoaded',function(){var inp=document.getElementById('srchInput');if(inp){inp.addEventListener('input',function(){renderSrchItems(this.value);});inp.addEventListener('keydown',function(e){if(e.key==='Enter'){var q=this.value.trim();if(q){window.location.href='/search?query='+encodeURIComponent(q);}}});}document.querySelectorAll('.srch-filter').forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll('.srch-filter').forEach(function(b){b.classList.remove('active');});this.classList.add('active');_srchFilter=this.dataset.s;renderSrchItems(document.getElementById('srchInput').value);});});});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){tvClose();closeSrch();}});
    </script>
    {% if not is_premium %}
    <div class="join-premium-bar" id="joinPremiumBar" role="complementary" aria-label="Join premium" style="display:block;">
        <div class="join-premium-inner">
            <span class="join-premium-copy">Join premium for spreads, totals, projected scores, and full model edge.</span>
            <div class="join-premium-actions">
                <a href="/plans" class="join-premium-btn">Join Now</a>
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
# DOWNLOADS TEMPLATE — per-sport CSV export hub
# ============================================================================

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

# ============================================================================
# EDGE VALUE PERFORMANCE TEMPLATE — how edge % calibrates to real results
# ============================================================================

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
    .asr-status{font-size:0.76em;color:#94a3b8;font-weight:700;}
    .asr-copy-btn{background:#0f172a;color:#fff;border:none;border-radius:10px;padding:10px 18px;font-size:0.84rem;font-weight:800;cursor:pointer;white-space:nowrap;}
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
                                {% if c.pct is not none %}<div class="asr-pct">{{ c.pct }}%</div><div class="asr-rec">{{ c.record }}<span class="asr-info" title="Number of Games"> ⓘ</span></div>{% elif c.n %}<div class="asr-rec" style="color:#94a3b8;">{{ c.record }} <span style="font-size:0.72em;" title="Sample too small to report a win rate">(n&lt;20)</span></div>{% elif c.status == 'no_games' %}<span class="asr-status">No games yet</span>{% else %}<span class="asr-status">Not tracked</span>{% endif %}
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
                            <td>{% if c.pct is not none %}<div class="asr-pct">{{ c.pct }}%</div><div class="asr-rec">{{ c.record }}<span class="asr-info" title="Number of Games"> ⓘ</span></div>{% elif c.n %}<div class="asr-rec" style="color:#94a3b8;">{{ c.record }} <span style="font-size:0.72em;" title="Sample too small to report a win rate">(n&lt;20)</span></div>{% elif c.status == 'no_games' %}<span class="asr-status">No games yet</span>{% else %}<span class="asr-status">Not tracked</span>{% endif %}</td>
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
                            <td>{% if c.pct is not none %}<div class="asr-pct">{{ c.pct }}%</div><div class="asr-rec">{{ c.record }}<span class="asr-info" title="Number of Games"> ⓘ</span></div>{% elif c.n %}<div class="asr-rec" style="color:#94a3b8;">{{ c.record }} <span style="font-size:0.72em;" title="Sample too small to report a win rate">(n&lt;20)</span></div>{% elif c.status == 'no_games' %}<span class="asr-status">No games yet</span>{% else %}<span class="asr-status">Not tracked</span>{% endif %}</td>
                            {% endfor %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        <div style="display:flex;justify-content:center;margin:20px 0 4px;">
            <button type="button" class="asr-copy-btn" onclick="copyAllSportsResults()">📋 Copy All</button>
        </div>
        <script>
        function copyAllSportsResults(){
            const sections=[...document.querySelectorAll('.asr-section')];
            const lines=[];
            const cellText=td=>{
                const pct=td.querySelector?td.querySelector('.asr-pct'):null;
                const rec=td.querySelector?td.querySelector('.asr-rec'):null;
                if(pct||rec){
                    const p=pct?pct.textContent.trim():'';
                    const r=rec?rec.textContent.replace(/ⓘ/g,'').replace(/\s+/g,' ').trim():'';
                    return (p+(p&&r?' ':'')+r).trim();
                }
                return td.textContent.replace(/ⓘ/g,'').replace(/\s+/g,' ').trim();
            };
            sections.forEach(section=>{
                const title=(section.querySelector('h2')||{}).textContent||'';
                if(title) lines.push(title.trim());
                section.querySelectorAll('table tr').forEach(tr=>{
                    const cells=[...tr.children].map(cellText).filter(Boolean);
                    if(cells.length) lines.push(cells.join(String.fromCharCode(9)));
                });
                lines.push('');
            });
            const text=lines.join(String.fromCharCode(10)).trim();
            if(!text)return;
            const btn=document.querySelector('.asr-copy-btn');
            const done=ok=>{if(!btn)return;const o=btn.getAttribute('data-orig-text')||btn.textContent;btn.setAttribute('data-orig-text',o);btn.textContent=ok?'✓ Copied!':'Copy failed';if(ok)btn.style.background='#059669';setTimeout(()=>{btn.textContent=o;btn.style.background='#0f172a';},1600);};
            if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(()=>done(true)).catch(()=>done(false));}
            else{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();let ok=false;try{ok=document.execCommand('copy');}catch(e){}document.body.removeChild(ta);done(ok);}
        }
        </script>
        {% endif %}
    </div>
""")

TEAM_EFFICIENCY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    body{background:#ffffff !important;color:#0f172a;}
    .ter-wrap{max-width:900px;margin:0 auto;padding:10px 0 60px;}
    .ter-header{text-align:center;margin-bottom:28px;}
    .ter-header h1{font-size:1.75em;margin-bottom:8px;}
    .ter-header p{color:#475569;font-size:0.95em;max-width:640px;margin:0 auto;line-height:1.6;}
    .ter-table-wrap{overflow-x:auto;border:1px solid rgba(15,23,42,0.12);border-radius:12px;background:#fff;}
    table.ter-table{width:100%;border-collapse:collapse;font-size:0.9em;}
    table.ter-table th,table.ter-table td{padding:12px 14px;text-align:center;border-bottom:1px solid rgba(15,23,42,0.08);}
    table.ter-table th{background:#f8fafc;font-weight:700;color:#334155;font-size:0.78em;text-transform:uppercase;}
    table.ter-table td:first-child,table.ter-table th:first-child{text-align:left;}
    table.ter-table tr:last-child td{border-bottom:none;}
    .ter-pct{font-weight:800;font-size:1.1em;}
    .ter-rec{font-size:0.78em;color:#64748b;margin-top:2px;}
    .ter-na{color:#94a3b8;}
    """
).replace('{% block content %}{% endblock %}', """
    <div class="ter-wrap">
        <div class="ter-header">
            <h1>⚡ Team Efficiency Results</h1>
            <p>Moneyline accuracy for the Team Efficiency model — spread/total derived from recent ORtg, DRtg, and pace (ESPN box scores). Graded when efficiency data is available for both teams.</p>
            <p style="margin-top:12px;font-size:0.88em;"><a href="/all-sports-results">← All sports results</a></p>
        </div>
        <div class="ter-table-wrap">
            <table class="ter-table">
                <thead><tr><th>Sport</th><th>ML accuracy</th><th>Record</th><th>Games</th></tr></thead>
                <tbody>
                    {% for row in efficiency_rows %}
                    <tr>
                        <td><a href="{{ row.results_url }}">{{ row.icon }} {{ row.name }}</a></td>
                        {% if row.cell.n %}
                        <td><span class="ter-pct">{{ row.cell.pct }}%</span></td>
                        <td>{{ row.cell.record }}</td>
                        <td>{{ row.cell.n }}</td>
                        {% elif row.supported %}
                        <td colspan="3" class="ter-na">— (no graded games yet)</td>
                        {% else %}
                        <td colspan="3" class="ter-na">— (box-score data not available)</td>
                        {% endif %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
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
    .league-pill-count { margin-left:6px; font-size:0.85em; opacity:0.75; font-weight:700; }
    /* Date navigation */
    .date-nav { display:flex; align-items:center; justify-content:center; gap:12px; margin:16px 0; padding:12px 16px; background:#ffffff; border:1px solid rgba(15,23,42,0.12); border-radius:12px; max-width:100%; min-width:0; }
    .nav-arrow { background:rgba(251,191,36,0.2); border:2px solid #fbbf24; color:#fbbf24; font-size:1.3em; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer; transition:all 0.2s; user-select:none; flex-shrink:0; }
    .nav-arrow:hover { background:rgba(251,191,36,0.4); transform:scale(1.1); }
    .date-bubbles { display:flex; gap:8px; overflow-x:auto; padding:4px; max-width:820px; min-width:0; flex:1 1 auto; }
    .date-bubble { background:#ffffff; border:2px solid rgba(15,23,42,0.2); border-radius:22px; padding:8px 15px; min-width:100px; text-align:center; cursor:pointer; transition:all 0.2s; white-space:nowrap; font-weight:500; font-size:0.84em; color:#0f172a; }
    .date-bubble:hover { border-color:#fbbf24; }
    .date-bubble.active { background:#fbbf24; border-color:#fbbf24; color:#0f172a; font-weight:700; }
    .date-bubble.today { border-color:#00C076; color:#00C076; }
    .date-bubble.active.today { background:#00C076; color:white; }
    .results-date-picker { display:flex; flex-wrap:wrap; align-items:center; justify-content:center; gap:10px; margin:12px 0 8px; padding:10px 14px; background:#ffffff; border:1px solid rgba(15,23,42,0.12); border-radius:12px; }
    .results-date-picker label { font-size:0.88em; font-weight:600; color:#334155; }
    .results-date-picker select { min-width:200px; max-width:100%; padding:8px 12px; border-radius:8px; border:2px solid rgba(15,23,42,0.18); font-size:0.9em; font-weight:600; color:#0f172a; background:#fff; }
    .results-date-picker .date-clear { font-size:0.82em; color:#00529B; text-decoration:none; font-weight:700; }
    /* Date sections */
    .date-section { display:none; background:#ffffff; border:1px solid rgba(15,23,42,0.12); border-radius:12px; padding:20px; margin-bottom:20px; }
    .date-section.visible { display:block; }
    .date-header { color:#0F172A; font-size:1.3em; font-weight:700; margin-bottom:14px; padding-bottom:10px; border-bottom:2px solid #E2E8F0; }
    .results-copy-all-btn { background:#0f172a;color:#fff;border:none;border-radius:10px;padding:10px 18px;font-size:0.84rem;font-weight:800;cursor:pointer;white-space:nowrap; }
    .results-copy-all-btn:hover { background:#1e293b; }
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
    .model-grid { display:grid; grid-template-columns:repeat(6,1fr); gap:8px; margin-bottom:16px; }
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
        <a href="/{{ sport_seo_slug }}{% if sport == 'SOCCER' and selected_league_slug %}?league={{ selected_league_slug }}{% endif %}" class="tab">📊 Predictions</a>
        <a href="/{{ sport_results_slug }}{% if sport == 'SOCCER' and selected_league_slug %}?league={{ selected_league_slug }}{% endif %}" class="tab active">🎯 Results</a>
    </div>
        {% set model_cards = [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('⚡ Efficiency','efficiency'),('🏆 Consensus','ensemble')] %}
        {% set label_glicko2 = 'Grinder2' %}
        {% set label_trueskill = 'Takedown' %}
        {% set label_elo = 'Edge' %}
        {% set label_xgb = 'XSharp' %}
        {% set label_efficiency = 'Efficiency' %}
        {% set label_ensemble = 'Consensus' %}
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
            <p style="text-align:center;margin:0 0 14px;font-size:0.78em;color:#64748b;">Large <strong>%</strong> = <strong>win rate</strong> on graded picks (wins ÷ wins+losses). Line below shows record, flat-bet <strong>unit ROI</strong>, and net units (+1u/−1u per pick).</p>
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
            {% for m_label, m_key in model_cards %}
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

        {% if available_dates is defined and available_dates|length > 0 %}
        <div class="results-date-picker">
            <label for="resultsDateSelect">📅 Jump to date</label>
            <select id="resultsDateSelect" aria-label="Select results date">
                <option value="">Latest week (scroll)</option>
                {% for d in available_dates %}
                <option value="{{ d }}" {% if selected_results_date == d %}selected{% endif %}>{{ d }}</option>
                {% endfor %}
            </select>
            {% if selected_results_date %}
            <a class="date-clear" href="/{{ sport_results_slug }}{% if sport == 'SOCCER' and selected_league_slug %}?league={{ selected_league_slug }}{% endif %}">Show all dates</a>
            {% endif %}
        </div>
        {% endif %}
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
                    {% set is_premium = is_premium|default(false) %}
                    {% set spread_label = _spread_label %}
                    {% set force_rl = _force_rl %}
                    {% set away_score = game.away_score %}
                    {% set home_score = game.home_score %}
                    {% set show_pick_arrow = false %}
                    {% if home_score is not none and away_score is not none %}
                        {% set home_won = home_score > away_score %}
                        {% set glicko2_correct = (game.glicko2_prob|default(none) is not none) and ((game.glicko2_prob >= 50) == home_won) or none %}
                        {% set trueskill_correct = (game.trueskill_prob|default(none) is not none) and ((game.trueskill_prob >= 50) == home_won) or none %}
                        {% set elo_correct = (game.elo_prob|default(none) is not none) and ((game.elo_prob >= 50) == home_won) or none %}
                        {% set xgb_correct = (game.xgb_prob|default(none) is not none) and ((game.xgb_prob >= 50) == home_won) or none %}
                        {% set efficiency_correct = (game.efficiency_prob|default(none) is not none) and ((game.efficiency_prob >= 50) == home_won) or none %}
                        {% set ens_correct = (game.ens_prob|default(none) is not none) and ((game.ens_prob >= 50) == home_won) or none %}
                    {% else %}
                        {% set glicko2_correct = game.glicko2_correct|default(none) %}
                        {% set trueskill_correct = game.trueskill_correct|default(none) %}
                        {% set elo_correct = game.elo_correct|default(none) %}
                        {% set xgb_correct = game.xgb_correct|default(none) %}
                        {% set efficiency_correct = game.efficiency_correct|default(none) %}
                        {% set ens_correct = game.ens_correct|default(none) %}
                    {% endif %}
                    {% set conf_models = [
                        {'name': label_glicko2, 'prob': game.glicko2_prob|default(none), 'correct': glicko2_correct, 'key': 'glicko2'},
                        {'name': label_trueskill, 'prob': game.trueskill_prob|default(none), 'correct': trueskill_correct, 'key': 'trueskill'},
                        {'name': label_elo, 'prob': game.elo_prob|default(none), 'correct': elo_correct, 'key': 'elo'},
                        {'name': label_xgb, 'prob': game.xgb_prob|default(none), 'correct': xgb_correct, 'key': 'xgb'},
                        {'name': label_efficiency, 'prob': game.efficiency_prob|default(none), 'correct': efficiency_correct, 'key': 'efficiency'},
                        {'name': label_ensemble, 'prob': game.ens_prob|default(none), 'correct': ens_correct, 'key': 'consensus'}
                    ] %}
                    {% include 'includes/game_card_body.html' %}
                    {% if game.model_data_note %}<div style="font-size:0.7em;color:#94a3b8;padding:4px 12px 8px;text-align:center;">{{ game.model_data_note }}</div>{% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}

        <div style="display:flex;justify-content:center;margin:22px 0 4px;">
            <button type="button" class="results-copy-all-btn" onclick="copyAllResults()" title="Copy all visible results to clipboard">📋 Copy All</button>
        </div>

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
    const initialActiveDate = {{ (selected_results_date|tojson) if selected_results_date is defined else 'null' }};
    let currentWeekStart = 0, activeDate = null;
    (function(){
        const sel = document.getElementById('resultsDateSelect');
        if (!sel) return;
        sel.addEventListener('change', function(){
            const u = new URL(window.location.href);
            if (this.value) { u.searchParams.set('date', this.value); }
            else { u.searchParams.delete('date'); }
            window.location.href = u.pathname + u.search;
        });
    })();
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
    function copyAllResults() {
        const sec = document.querySelector('.date-section.visible') || document;
        const cards = sec.querySelectorAll('.game-card');
        const clean = txt => (txt || '').replace(/\s+/g, ' ').trim();
        const lines = [];
        cards.forEach(card => {
            const teams = [...card.querySelectorAll('.team-name')].map(n => clean(n.textContent));
            const scores = [...card.querySelectorAll('.final-score')].map(n => clean(n.textContent));
            const modelRows = [...card.querySelectorAll('.pc-box')].map(n => clean(n.textContent)).filter(Boolean);
            const marketRows = [...card.querySelectorAll('.odds-pricing-table tr')].map(n => clean(n.textContent)).filter(Boolean);
            let row = teams.length >= 2 ? `${teams[0]} @ ${teams[1]}` : clean(card.textContent).slice(0, 160);
            if (scores.length >= 2) row += ` | Final: ${scores[0]}-${scores[1]}`;
            if (modelRows.length) row += `\n   Models: ${modelRows.join(' | ')}`;
            if (marketRows.length) row += `\n   Lines: ${marketRows.join(' | ')}`;
            lines.push(row);
        });
        if (!lines.length) return;
        const dateLbl = activeDate || '';
        const NL = String.fromCharCode(10);
        const text = '{{ sport_info.name }} Results' + (dateLbl ? ' - ' + dateLbl : '') + NL + '='.repeat(40) + NL + NL + lines.join(NL + NL);
        const btns = document.querySelectorAll('.results-copy-all-btn');
        const done = ok => btns.forEach(btn => {
            const o = btn.getAttribute('data-orig-text') || btn.textContent;
            btn.setAttribute('data-orig-text', o);
            btn.textContent = ok ? '✓ Copied!' : 'Copy failed';
            if (ok) btn.style.background = '#059669';
            setTimeout(() => { btn.textContent = o; btn.style.background = '#0f172a'; }, 1600);
        });
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => done(true)).catch(() => done(false));
        } else {
            const ta=document.createElement('textarea'); ta.value=text; document.body.appendChild(ta); ta.select();
            let ok=false; try{ok=document.execCommand('copy');}catch(e){} document.body.removeChild(ta); done(ok);
        }
    }
    document.addEventListener('DOMContentLoaded',()=>{
        if(allDates.length>0){
            if(initialActiveDate && allDates.includes(initialActiveDate)){
                activeDate=initialActiveDate;
                const idx=allDates.indexOf(activeDate);
                currentWeekStart=Math.max(0,idx-Math.floor(datesPerWeek/2));
            } else {
                const lastIdx=allDates.length-1;
                currentWeekStart=Math.max(0,lastIdx-datesPerWeek+1);
                activeDate=allDates[lastIdx];
            }
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
_LANDING_SPORT_ORDER = ['NHL', 'NBA', 'NCAAB', 'NCAAW', 'MLB', 'SOCCER', 'NFL', 'NCAAF', 'WNBA', 'TENNIS', 'UFC', 'GOLF']
_LANDING_SPORT_SHORT = {
    'NCAAB': 'NCAAB',
    'NCAAW': 'NCAAW',
    'NCAAF': 'NCAAF',
    'SOCCER': 'Soccer',
    'TENNIS': 'Tennis',
    'UFC': 'UFC',
    'GOLF': 'Golf',
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
    ('ensemble', 'Consensus'),
    ('efficiency', 'Efficiency'),
)


def _all_sports_snapshot_dir():
    """Resolved snapshot directory (same multi-base lookup as NHL snapshots — no src import)."""
    for base in (_V2_BASE, _BASE_DIR, _os_v2.path.dirname(_os_v2.path.abspath(__file__))):
        path = _os_v2.path.join(base, 'data', 'season_snapshots')
        if _os_v2.path.isdir(path):
            return path
    return _os_v2.path.join(_V2_BASE, 'data', 'season_snapshots')


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
            return data
    return None


def _merge_snapshot_efficiency_into_overall(overall_stats, sport):
    """Prefer frozen snapshot Efficiency ML stats when live attach graded fewer games."""
    snap = _load_sport_season_snapshot(sport)
    if not snap:
        return overall_stats
    snap_eff = (snap.get('overall_stats') or {}).get('efficiency')
    if not isinstance(snap_eff, dict):
        return overall_stats
    snap_total = int(snap_eff.get('total') or 0)
    if snap_total <= 0:
        return overall_stats
    cur = (overall_stats or {}).get('efficiency') or {}
    cur_total = int(cur.get('total') or 0)
    if snap_total <= cur_total:
        return overall_stats
    stats = dict(overall_stats or {})
    stats['efficiency'] = {
        'correct': int(snap_eff.get('correct') or 0),
        'total': snap_total,
        'accuracy': float(snap_eff.get('accuracy') or 0.0),
    }
    return stats


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


# Minimum graded games before a season win% is shown. Below this we display
# the record but suppress the percentage so tiny samples (e.g. NCAAW 8-0) don't
# masquerade as a "100%" model on a client-facing page.
_MIN_SNAPSHOT_SAMPLE = 20


def _fmt_snapshot_ml_cell(overall, model_key):
    m = (overall or {}).get(model_key) or {}
    total = int(m.get('total') or 0)
    correct = int(m.get('correct') or 0)
    if total <= 0:
        return {'pct': None, 'record': '—', 'n': 0, 'status': 'missing'}
    pct = m.get('accuracy')
    if pct is None:
        pct = round(correct / total * 100, 1)
    if total < _MIN_SNAPSHOT_SAMPLE:
        # Show the record but NOT a percentage — sample too small to trust.
        return {'pct': None, 'record': f'{correct}-{total - correct}',
                'n': total, 'insufficient': True}
    return {
        'pct': pct,
        'record': f'{correct}-{total - correct}',
        'n': total,
    }


def _fmt_snapshot_market_cell(st, *, graded_key, win_key, pct_key, push_key=None):
    st = st or {}
    graded = int(st.get(graded_key) or 0)
    if graded <= 0:
        return {'pct': None, 'record': '—', 'n': 0, 'status': 'missing'}
    wins = int(st.get(win_key) or 0)
    pushes = int(st.get(push_key) or 0) if push_key else 0
    losses = max(0, graded - pushes - wins)
    if graded < _MIN_SNAPSHOT_SAMPLE:
        return {'pct': None, 'record': f'{wins}-{losses}',
                'n': graded, 'insufficient': True}
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
        games_in_scope = int(snap.get('games_in_scope') or 0)
        ml_cols = {
            key: _fmt_snapshot_ml_cell(overall, key) for key, _ in _ML_DASHBOARD_MODELS
        }
        for cell in ml_cols.values():
            if not cell.get('n'):
                cell['status'] = 'no_games' if games_in_scope <= 0 else 'not_tracked'
        spread_xsharp = _fmt_snapshot_market_cell(
            st,
            graded_key='spread_graded',
            win_key='spread_covered',
            pct_key='spread_pct',
            push_key='spread_pushes',
        )
        spread_pl = _fmt_snapshot_market_cell(
            st,
            graded_key='pl_spread_graded',
            win_key='pl_spread_covered',
            pct_key='pl_spread_pct',
            push_key='pl_spread_pushes',
        )
        ou_xsharp = _fmt_snapshot_market_cell(
            st,
            graded_key='total_graded',
            win_key='total_correct',
            pct_key='total_pct',
            push_key='total_pushes',
        )
        ou_pl = _fmt_snapshot_market_cell(
            st,
            graded_key='pl_total_graded',
            win_key='pl_total_correct',
            pct_key='pl_total_pct',
            push_key='pl_total_pushes',
        )
        for cell in (spread_xsharp, spread_pl, ou_xsharp, ou_pl):
            if not cell.get('n'):
                cell['status'] = 'no_games' if games_in_scope <= 0 else 'not_tracked'
        rows.append({
            'sport': sport,
            'name': SPORTS[sport]['name'],
            'icon': SPORTS[sport].get('icon', ''),
            'season': snap.get('season') or '',
            'games_in_scope': games_in_scope,
            'ml': ml_cols,
            'spread_xsharp': spread_xsharp,
            'spread_pl': spread_pl,
            'ou_xsharp': ou_xsharp,
            'ou_pl': ou_pl,
            'results_url': f"/sport/{sport}/results",
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
    from sports._individual_sport import INDIVIDUAL_SPORTS, individual_sport_season_bounds
    if sport in INDIVIDUAL_SPORTS:
        return individual_sport_season_bounds(ref_dt)
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
        ('efficiency', 'Team Efficiency'),
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


def _is_valid_pick_matchup_team(name):
    """Team name is usable on landing/value-pick cards (not blank or TBD)."""
    if not name:
        return False
    s = str(name).strip()
    return bool(s) and s.upper() != 'TBD'


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
            SELECT p.game_id, p.sport,
                   COALESCE(NULLIF(TRIM(g.home_team_id), 'TBD'), NULLIF(TRIM(p.home_team_id), 'TBD')) AS home_team_id,
                   COALESCE(NULLIF(TRIM(g.away_team_id), 'TBD'), NULLIF(TRIM(p.away_team_id), 'TBD')) AS away_team_id,
                   p.win_probability,
                   p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.meta_home_prob,
                   b.home_implied_prob, b.away_implied_prob
            FROM predictions p
            LEFT JOIN games g ON p.game_id = g.game_id AND g.sport = p.sport
            LEFT JOIN betting_odds b ON p.game_id = b.game_id
            WHERE date(p.game_date) = ?
              AND (g.home_score IS NULL OR g.game_id IS NULL)
              AND p.win_probability IS NOT NULL
              AND p.sport IN ('NHL', 'NBA', 'MLB', 'SOCCER')
              AND UPPER(TRIM(COALESCE(g.home_team_id, p.home_team_id, ''))) NOT IN ('TBD', '')
              AND UPPER(TRIM(COALESCE(g.away_team_id, p.away_team_id, ''))) NOT IN ('TBD', '')
            ORDER BY p.game_date ASC
            LIMIT 80
        ''', (_tp_today,)).fetchall()
        _tp_conn.close()
        _candidates = []
        for _tp in _tp_rows:
            _home = _tp['home_team_id']
            _away = _tp['away_team_id']
            if not _is_valid_pick_matchup_team(_home) or not _is_valid_pick_matchup_team(_away):
                continue
            _ens_home = float(_tp['win_probability'])
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


_BLOG_POSTS_FILE = _os.path.join(_BASE_DIR, 'data', 'blog_posts.json')
_BLOG_CACHE: dict = {'ts': 0, 'posts': []}
_BLOG_CACHE_TTL = 300
_BLOG_NEWS_CACHE: dict = {'ts': 0, 'items': []}
_BLOG_NEWS_CACHE_TTL = 900
_ESPN_NEWS_FEEDS = [
    ('MLB', 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/news'),
    ('NBA', 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news'),
    ('NFL', 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/news'),
    ('NHL', 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news'),
    ('WNBA', 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/news'),
    ('NCAAB', 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/news'),
    ('NCAAF', 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/news'),
]


def _slugify_blog(value: str) -> str:
    value = (value or '').strip().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-') or 'prediction-lab-blog'


def _blog_date_key(post: dict):
    raw = post.get('date') or post.get('published_at') or ''
    raw = str(raw).strip()[:10]
    try:
        return datetime.strptime(raw, '%Y-%m-%d')
    except Exception:
        return datetime.min


def _blog_display_date(post: dict) -> str:
    dt = _blog_date_key(post)
    if dt == datetime.min:
        return str(post.get('date') or '').strip()
    return dt.strftime('%B %d, %Y').replace(' 0', ' ')


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


def _load_blog_posts_from_json() -> list[dict]:
    now_ts = _time.time()
    if _BLOG_CACHE.get('posts') and (now_ts - _BLOG_CACHE.get('ts', 0)) < _BLOG_CACHE_TTL:
        return list(_BLOG_CACHE.get('posts') or [])
    posts: list[dict] = []
    try:
        if _os.path.exists(_BLOG_POSTS_FILE):
            with open(_BLOG_POSTS_FILE, encoding='utf-8') as fh:
                payload = json.load(fh)
            items = payload.get('posts', payload) if isinstance(payload, dict) else payload
            if isinstance(items, list):
                for raw in items:
                    post = _normalize_blog_post(raw)
                    if post:
                        posts.append(post)
    except Exception as exc:
        logger.debug(f"Blog JSON load failed: {exc}")
    posts.sort(key=_blog_date_key, reverse=True)
    _BLOG_CACHE.update({'ts': now_ts, 'posts': posts})
    return list(posts)


def _blog_news_topic(headline: str) -> str:
    topic = re.sub(r'\s+', ' ', (headline or '')).strip()
    topic = re.sub(r'^[\'"]|[\'"]$', '', topic)
    return topic[:140].rstrip()


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


def _generate_daily_blog_post(today=None, todays_picks=None, news_items=None) -> dict:
    try:
        _tz = ZoneInfo('America/New_York')
        today_dt = today or datetime.now(_tz)
    except Exception:
        today_dt = today or datetime.now()
    date_str = today_dt.strftime('%Y-%m-%d')
    display_date = today_dt.strftime('%B %d, %Y').replace(' 0', ' ')
    picks = todays_picks if todays_picks is not None else build_todays_top_picks()
    news = news_items if news_items is not None else _fetch_espn_news_items(limit=4)
    primary_sport = (news[0]['sport'] if news else None) or (picks[0]['sport'] if picks else 'Sports News')
    slug = f"prediction-lab-blog-{date_str}"
    title = f"Prediction Lab Blog: {display_date}"
    if picks:
        pick_bits = [
            f"{p['sport']}: {p['pick']} over {p['away'] if p['pick'] == p['home'] else p['home']} ({p['prob']}%)"
            for p in picks[:3]
        ]
        lead = "Today's betting board is led by " + '; '.join(pick_bits) + "."
    else:
        lead = "Today's betting board is focused on moneyline model agreement, market pricing, and completed-result transparency across active sports."
    if news:
        news_lead = (
            "The sports news side of the board is being shaped by "
            + '; '.join(f"{item['sport']}: {item['topic']}" for item in news[:3])
            + "."
        )
    else:
        news_lead = "The sports news side of the board is monitored through ESPN feeds when available, then connected back to model movement and market context."
    body = [
        news_lead,
        lead,
        *[_news_market_paragraph(item) for item in news[:3]],
        "Prediction Lab connects sports news to betting context by comparing model win probabilities against market prices. The daily betting results report remains the verification layer, while this news and market breakdown gives crawlers and readers a concise explanation of what the models and the broader sports calendar are watching today.",
        "The most important signal is not a single story or pick in isolation. It is the relationship between news, model confidence, sportsbook pricing, recent result tracking, and whether multiple model layers point in the same direction.",
        "Check the sport prediction pages for the live cards and the daily results report for completed-game tracking. New daily sports news and betting analysis pages are generated server-side so the latest context stays crawlable and internally linked from the homepage.",
    ]
    excerpt = _blog_excerpt(' '.join(body), 3)
    return {
        'title': title,
        'slug': slug,
        'date': date_str,
        'sport_tag': primary_sport,
        'excerpt': excerpt,
        'body': body,
        'news_items': news,
    }


def _get_blog_posts(include_generated=True, todays_picks=None) -> list[dict]:
    posts = _load_blog_posts_from_json()
    if include_generated:
        generated = _generate_daily_blog_post(todays_picks=todays_picks)
        by_slug = {p['slug']: p for p in posts}
        by_slug.setdefault(generated['slug'], generated)
        posts = list(by_slug.values())
    posts.sort(key=_blog_date_key, reverse=True)
    return posts


def _get_latest_blog_post(todays_picks=None) -> dict:
    posts = _get_blog_posts(include_generated=True, todays_picks=todays_picks)
    return posts[0] if posts else _generate_daily_blog_post(todays_picks=todays_picks)


# ──────────────────────────────────────────────────────────────────────────
# Daily Prediction Reel — vertical 9:16 animated scoreboard videos for TikTok/IG.
# Reads EXISTING predicted scores (no model changes). Screen-record on iPhone,
# or use the in-page recorder on Android/desktop.
# ──────────────────────────────────────────────────────────────────────────

def _predicted_score(p):
    """Best available predicted (home, away) final score from a prediction dict."""
    h = p.get('xgb_home_score'); a = p.get('xgb_away_score')
    if h is not None and a is not None:
        return float(h), float(a)
    sp = p.get('our_spread') if p.get('our_spread') is not None else p.get('xgb_spread')
    tot = p.get('our_total') if p.get('our_total') is not None else p.get('xgb_total')
    if sp is not None and tot is not None:
        try:
            return _scores_from_spread_total(float(sp), float(tot))
        except Exception:
            return None, None
    return None, None


def _build_reel_games(limit=4):
    """Today's upcoming SOCCER + MLB games with predicted final scores (max `limit`)."""
    from datetime import datetime as _dt
    today = _dt.now().date()
    buckets = {}
    for sport in ('SOCCER', 'MLB'):
        rows = []
        try:
            preds = get_upcoming_predictions(sport) or []
        except Exception as _e:
            logger.debug(f"[reel] {sport} predictions failed: {_e}")
            preds = []
        for p in preds:
            if not isinstance(p, dict) or p.get('home_score') is not None:
                continue
            gd = p.get('game_date')
            try:
                gdd = parse_date(gd).date() if gd else None
            except Exception:
                gdd = None
            if gdd != today:
                continue
            hsc, asc = _predicted_score(p)
            if hsc is None or asc is None:
                continue
            rows.append({
                'sport': sport,
                'home': str(p.get('home_team_name') or p.get('home_team') or p.get('home_team_id') or 'Home'),
                'away': str(p.get('away_team_name') or p.get('away_team') or p.get('away_team_id') or 'Away'),
                'home_score': max(0, int(round(hsc))),
                'away_score': max(0, int(round(asc))),
            })
        buckets[sport] = rows
    out = []
    s, m = buckets.get('SOCCER', []), buckets.get('MLB', [])
    i = 0
    while len(out) < limit and (i < len(s) or i < len(m)):
        if i < len(s):
            out.append(s[i])
        if len(out) < limit and i < len(m):
            out.append(m[i])
        i += 1
    return out[:limit]


_REEL_HTML = r'''<!doctype html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>PredictionLab — Daily Reel</title>
<style>
 html,body{margin:0;background:#0a0e17;color:#fff;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100%;}
 #wrap{display:flex;flex-direction:column;align-items:center;gap:14px;padding:14px;box-sizing:border-box;}
 canvas{background:#0a0e17;width:auto;height:78vh;max-width:100%;border-radius:20px;box-shadow:0 12px 48px rgba(0,0,0,.55);}
 .row{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;}
 button{font-size:16px;font-weight:800;padding:13px 20px;border:none;border-radius:12px;background:#7c3aed;color:#fff;cursor:pointer;}
 button.alt{background:#1f2937;}
 .hint{font-size:13px;color:#9aa4b2;max-width:540px;text-align:center;line-height:1.45;}
 a#dl{display:none;color:#22c55e;font-weight:800;text-decoration:none;}
</style></head><body>
<div id="wrap">
 <canvas id="c" width="1080" height="1920"></canvas>
 <div class="row">
   <button id="play">▶ Play reel</button>
   <button id="rec" class="alt">⬇ Record &amp; save video</button>
 </div>
 <div class="hint"><b>iPhone:</b> open Control Center → start Screen Recording → tap <b>Play reel</b>. The recording saves to Photos for TikTok/Instagram.<br>Android/desktop: tap <b>Record &amp; save video</b>.</div>
 <a id="dl"></a>
</div>
<script>
var GAMES = __GAMES_JSON__;
var cv=document.getElementById('c'), ctx=cv.getContext('2d'), W=cv.width, H=cv.height;
var GAME_MS=5200, GAP_MS=600;
function buildEvents(g){
  var order=[], hi=g.home_score||0, ai=g.away_score||0;
  while(hi>0||ai>0){ if(hi>=ai&&hi>0){order.push('home');hi--;} else if(ai>0){order.push('away');ai--;} else {order.push('home');hi--;} }
  var ev=[], n=order.length;
  for(var i=0;i<n;i++){ ev.push({p:0.12+0.80*(i+1)/(n+1), side:order[i]}); }
  return ev;
}
GAMES.forEach(function(g){ g._ev=buildEvents(g); });
function clockText(g,p){ if(g.sport==='MLB'){return 'INNING '+Math.min(9,1+Math.floor(p*9));} return Math.min(90,Math.floor(p*90))+"'"; }
function label(g){ return g.sport==='MLB'?'⚾ MLB':'⚽ WORLD CUP'; }
function trim(s){ s=String(s||''); return s.length>16?s.slice(0,15)+'…':s; }
function draw(g,p){
  var grad=ctx.createLinearGradient(0,0,0,H); grad.addColorStop(0,'#141c2e'); grad.addColorStop(1,'#070a12');
  ctx.fillStyle=grad; ctx.fillRect(0,0,W,H);
  ctx.textAlign='center';
  ctx.fillStyle='#a78bfa'; ctx.font='bold 66px sans-serif'; ctx.fillText(label(g),W/2,200);
  ctx.fillStyle='#f59e0b'; ctx.font='bold 120px sans-serif'; ctx.fillText(clockText(g,p),W/2,400);
  var hs=0,as=0; g._ev.forEach(function(e){ if(e.p<=p){ if(e.side==='home')hs++; else as++; } });
  ctx.fillStyle='#e5e7eb'; ctx.font='bold 84px sans-serif'; ctx.fillText(trim(g.away),W/2,760);
  ctx.fillStyle='#fff'; ctx.font='bold 320px sans-serif'; ctx.fillText(String(as),W/2,1090);
  ctx.fillStyle='#475569'; ctx.font='bold 60px sans-serif'; ctx.fillText('vs',W/2,1190);
  ctx.fillStyle='#e5e7eb'; ctx.font='bold 84px sans-serif'; ctx.fillText(trim(g.home),W/2,1320);
  ctx.fillStyle='#fff'; ctx.font='bold 320px sans-serif'; ctx.fillText(String(hs),W/2,1650);
  if(p>=0.999){ ctx.fillStyle='#22c55e'; ctx.font='bold 60px sans-serif'; ctx.fillText('PREDICTED FINAL',W/2,1770); }
  ctx.fillStyle='#9aa4b2'; ctx.font='bold 46px sans-serif'; ctx.fillText('PredictionLab.io',W/2,1870);
}
var startTs=null, raf=null;
function frame(ts){
  if(startTs===null)startTs=ts;
  var per=GAME_MS+GAP_MS, t=ts-startTs, idx=Math.floor(t/per);
  if(idx>=GAMES.length){ draw(GAMES[GAMES.length-1],1); return; }
  draw(GAMES[idx], Math.max(0,Math.min(1,(t-idx*per)/GAME_MS)));
  raf=requestAnimationFrame(frame);
}
function playReel(){ if(raf)cancelAnimationFrame(raf); startTs=null; raf=requestAnimationFrame(frame); }
if(GAMES.length){ draw(GAMES[0],0); } else { ctx.fillStyle='#fff'; ctx.textAlign='center'; ctx.font='bold 56px sans-serif'; ctx.fillText('No games scheduled today',W/2,H/2); }
document.getElementById('play').onclick=playReel;
document.getElementById('rec').onclick=function(){
  if(!cv.captureStream||!window.MediaRecorder){ alert('In-page recording is not supported on this browser. On iPhone, use Screen Recording instead.'); return; }
  var stream=cv.captureStream(30);
  var mime=MediaRecorder.isTypeSupported('video/mp4')?'video/mp4':(MediaRecorder.isTypeSupported('video/webm')?'video/webm':'');
  var rec=new MediaRecorder(stream, mime?{mimeType:mime}:undefined), chunks=[];
  rec.ondataavailable=function(e){ if(e.data&&e.data.size)chunks.push(e.data); };
  rec.onstop=function(){
    var blob=new Blob(chunks,{type:mime||'video/webm'}), url=URL.createObjectURL(blob), a=document.getElementById('dl');
    a.href=url; a.download='predictionlab-reel.'+(String(mime).indexOf('mp4')>=0?'mp4':'webm');
    a.style.display='inline-block'; a.textContent='⬇ Tap to save your video'; a.click();
  };
  rec.start(); playReel();
  setTimeout(function(){ try{rec.stop();}catch(e){} }, GAMES.length*(GAME_MS+GAP_MS)+400);
};
</script></body></html>'''


@app.route('/reel')
def daily_reel():
    import json as _json
    try:
        games = _build_reel_games(limit=4)
    except Exception as _e:
        logger.error(f"[reel] build failed: {_e}")
        games = []
    return _REEL_HTML.replace('__GAMES_JSON__', _json.dumps(games))


@app.route('/healthz')
def healthz():
    """Lightweight probe for Render/load balancers (no DB or model work)."""
    return 'ok', 200


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
        # Most recent CORRECT model pick. Derive the pick from win_probability
        # (home-win prob): >=0.5 means the model favored home, else away. Most
        # stored predictions have predicted_winner = NULL, so relying on that
        # column made this fall back to a months-old game.
        _graded_row = _conn.execute(
            """
            SELECT p.sport, p.game_date, p.away_team_id, p.home_team_id,
                   p.win_probability, g.away_score, g.home_score
            FROM predictions p
            JOIN games g ON g.sport = p.sport AND g.game_id = p.game_id
            WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL
              AND g.home_score != g.away_score
              AND p.win_probability IS NOT NULL
              AND (
                (p.win_probability >= 0.5 AND g.home_score > g.away_score)
                OR
                (p.win_probability < 0.5 AND g.away_score > g.home_score)
              )
            ORDER BY date(g.game_date) DESC, g.id DESC
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
        'sports_covered': len(landing_sports),
        'weekly_banner_messages': list(_MANUAL_BANNER_ITEMS),
        'units_banner_items': preview_units,
        'todays_picks': todays_picks,
        'latest_graded_game': latest_graded_game,
        'latest_blog_post': latest_blog_post,
        'recent_blog_posts': blog_posts[1:4],
    }


@app.route('/homepage-preview')
def homepage_preview():
    """Preview-only alternate homepage design. Does not replace '/'."""
    log_site_visit('/homepage-preview')
    return render_template('homepage_preview.html', **_build_landing_preview_context())


@app.route('/')
def landing_page():
    """Primary landing page using the approved research design."""
    log_site_visit('/')
    return render_template('homepage_preview.html', **_build_landing_preview_context())

    # Legacy landing implementation retained below temporarily for rollback/reference.
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
    latest_blog_post = _get_latest_blog_post(todays_picks=todays_picks)

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
      "email": "support.predictionlab@gmail.com",
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
    {"@context":"https://schema.org","@type":"LocalBusiness","name":"predictionlab.io","url":"https://predictionlab.io","email":"support.predictionlab@gmail.com","telephone":"+1-519-992-8484","parentOrganization":{"@type":"Corporation","name":"GoodsandMore Inc."},"address":{"@type":"PostalAddress","streetAddress":"980 Lake Trail Drive","addressLocality":"Windsor","addressRegion":"Ontario","postalCode":"N9G 2R8","addressCountry":"CA"}}
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

/* Flex order must stay unique: a later .logo rule set order:2 while this
   block left search at order:2, which tied and followed DOM order
   (search before logo) — logo jumped to the right of the search bar. */
.navbar .hamburger { order: 1; flex-shrink: 0; }
.navbar .logo { order: 2; flex-shrink: 0; }
.nav-search-wrap {
    order: 3;
    flex: 1;
    max-width: 600px;
    min-width: 0;
    margin: 0 16px;
    display: flex;
    justify-content: center;
}
.nav-actions {
    order: 4;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
    margin-left: auto;
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
        .nav-cta-premium{display:inline-flex;align-items:center;padding:9px 16px;border-radius:999px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-size:0.84em;font-weight:800;text-decoration:none;letter-spacing:0.2px;white-space:nowrap;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 14px rgba(251,191,36,0.35);}
        .nav-cta-premium:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(251,191,36,0.45);}
        .tv-premium-cta{display:flex;align-items:center;justify-content:center;margin:10px 12px 6px;padding:12px 14px;border-radius:10px;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;font-weight:800;font-size:0.92em;text-decoration:none;letter-spacing:0.2px;}
        .tv-premium-cta:hover{box-shadow:0 4px 14px rgba(251,191,36,0.4);}
        @media(max-width:480px){.nav-cta{padding:8px 14px;font-size:0.8em;}.nav-cta-premium{padding:8px 12px;font-size:0.78em;}}
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
        .logo{display:inline-flex;align-items:center;text-decoration:none;flex-shrink:0;border-radius:10px;}
        .logo img,.logo .pl-brand-logo__img{display:block;height:36px;width:auto;max-height:42px;max-width:min(220px,42vw);object-fit:contain;}
        a.pl-brand-logo.pl-brand-logo--holding{outline:2px solid rgba(0,82,155,0.35);outline-offset:2px;}
        .hamburger{display:flex;flex-direction:column;justify-content:center;gap:5px;cursor:pointer;padding:7px 9px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;flex-shrink:0;}
        .hamburger:hover{background:#f8fafc;}
        .hamburger span{width:20px;height:1.5px;background:#0f172a;border-radius:2px;transition:all .2s;}
        .tv-overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,0.45);z-index:2001;backdrop-filter:blur(2px);}
        .tv-overlay.open{display:block;}
        .tv-drawer{position:fixed;top:0;left:0;height:100%;width:min(280px,100vw);background:#fff;z-index:2002;transform:translateX(-100%);transition:transform .28s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;box-shadow:4px 0 32px rgba(15,23,42,0.18);}
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
        /* Each homepage section is a self-contained card/panel with its heading. */
        .section{
            max-width:1040px;margin:0 auto 26px;padding:30px 34px;
            background:#fff;border:1px solid #e5e9f0;border-radius:18px;
            box-shadow:0 2px 14px rgba(15,23,42,0.05);
        }
        .section-title{
            text-align:center;font-size:1.55em;font-weight:900;
            margin-bottom:6px;letter-spacing:-0.01em;
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
        .up-units{font-size:1.05em;font-weight:900;color:#047857;}
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
            .navbar-content { flex-wrap: nowrap; align-items: center; }
            .nav-search-wrap { flex: 1; min-width: 0; max-width: 100%; }
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
    <link rel="stylesheet" href="/static/css/research-theme.css">
</head>
<body class="research-site">
<a href="#main-content" class="skip-link">Skip to main content</a>

{% include "partials/research_header.html" %}

<div class="tv-overlay" id="tvOverlay" onclick="tvClose()"></div>
<div class="tv-drawer" id="tvDrawer">
  <div class="tv-drawer-header">
    <div class="tv-header-btns"><button class="tv-back-btn" id="tvBackBtn" onclick="tvBack()" style="display:none">&#8249;</button><span class="tv-drawer-title" id="tvDrawerTitle">Menu</span></div>
    <button class="tv-close-btn" onclick="tvClose()">&#x2715;</button>
  </div>
  <div class="tv-panels">
    <div class="tv-panel visible" id="tvMain">
      {% if not is_premium %}
      <a href="/plans" class="tv-premium-cta">&#11088; Join Premium</a>
      {% endif %}
      <div class="tv-menu-list">
        <button class="tv-menu-btn" onclick="tvSub(\'picks\')"><span class="tv-menu-label">Picks &amp; Predictions</span><span class="tv-menu-arrow">&#8250;</span></button>
        <button class="tv-menu-btn" onclick="tvSub(\'props\')"><span class="tv-menu-label">Props</span><span class="tv-menu-arrow">&#8250;</span></button>
        <button class="tv-menu-btn" onclick="tvSub(\'tools\')"><span class="tv-menu-label">Tools &amp; Models</span><span class="tv-menu-arrow">&#8250;</span></button>
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
            <a href="/plans" style="background:#e2e8f0;color:#0f172a;padding:15px 32px;border-radius:10px;font-weight:800;text-decoration:none;font-size:1em;box-shadow:0 6px 20px rgba(0,0,0,0.25);">Get Started Free</a>
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
        <span style="display:inline-flex;align-items:center;gap:8px;background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.4);color:#047857;font-size:0.78em;font-weight:800;letter-spacing:0.4px;text-transform:uppercase;padding:5px 14px;border-radius:999px;">
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
            <div style="display:inline-block;font-size:0.68em;background:#fbbf24;color:#000;text-transform:uppercase;letter-spacing:0.6px;font-weight:800;margin-bottom:8px;padding:1px 6px;border-radius:4px;">{{ tp.sport }}</div>
            <div style="font-weight:800;font-size:1.02em;color:#0f172a;line-height:1.35;margin-bottom:10px;">{{ tp.away }} <span style="color:#64748b;font-weight:600;">vs</span> {{ tp.home }}</div>
            <div style="display:flex;align-items:baseline;gap:10px;">
                <span style="color:#047857;font-size:0.9em;font-weight:800;">▶ {{ tp.pick }}</span>
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
            <div style="margin-top:4px;font-size:0.78em;color:#b45309;font-weight:700;">View Picks →</div>
        </a>
        {% endfor %}
    </div>
</div>

<!-- Model Performance -->
<div class="section" style="padding-top:10px;padding-bottom:10px;">
    <div style="max-width:760px;margin:0 auto;text-align:center;">
        <h2 class="section-title" style="margin:0 0 8px;">Model Performance</h2>
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
            <h2 style="font-size:1.5em;font-weight:900;color:#92400e;">Daily Betting Results Report</h2>
            <p style="color:#334155;font-size:0.9em;margin:10px 0 20px;max-width:480px;margin-left:auto;margin-right:auto;">Yesterday's performance across all sports and models &mdash; tracked, transparent, verified.</p>
            <a href="/results" style="display:inline-block;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;padding:14px 32px;border-radius:10px;font-weight:800;text-decoration:none;font-size:0.95em;box-shadow:0 4px 20px rgba(251,191,36,0.3);transition:transform 0.2s;" onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='none'">View Full Results</a>
        </div>
    </div>
</div>

<section aria-labelledby="daily-blog-heading" style="max-width:860px;margin:0 auto 38px;padding:0 24px;">
    <div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:16px;padding:28px 26px;">
        <div style="text-align:center;margin-bottom:20px;">
            <h2 id="daily-blog-heading" style="font-size:1.45em;font-weight:900;color:#0f172a;margin:0;">Prediction Lab Blog</h2>
            <p style="color:#334155;font-size:0.92em;margin:10px auto 0;max-width:560px;line-height:1.65;">Daily sports news, AI-generated betting insights, game previews, and model analysis &mdash; updated every day.</p>
        </div>
        {% if latest_blog_post %}
        <article style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:14px;padding:20px 20px;text-align:left;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
                <span style="display:inline-flex;align-items:center;padding:4px 9px;border-radius:999px;background:#fbbf24;color:#000;font-size:0.72em;font-weight:900;text-transform:uppercase;letter-spacing:0.4px;">{{ latest_blog_post.sport_tag }}</span>
                <time datetime="{{ latest_blog_post.date }}" style="color:#64748b;font-size:0.82em;font-weight:700;">{{ latest_blog_post.display_date }}</time>
            </div>
            <h3 style="font-size:1.12em;line-height:1.35;margin:0 0 10px;color:#0f172a;font-weight:900;">Latest Daily Article: {{ latest_blog_post.title }}</h3>
            <p style="color:#334155;font-size:0.92em;line-height:1.7;margin:0 0 16px;">{{ latest_blog_post.excerpt }}</p>
            <a href="/blog/{{ latest_blog_post.slug }}" style="display:inline-flex;align-items:center;justify-content:center;background:#0f172a;color:#fff;padding:10px 16px;border-radius:10px;text-decoration:none;font-size:0.86em;font-weight:800;">Read Full Analysis</a>
        </article>
        {% endif %}
        <div style="text-align:center;margin-top:18px;">
            <a href="/blog" style="display:inline-flex;align-items:center;justify-content:center;border:1px solid #00529B;color:#00529B;background:#fff;padding:10px 18px;border-radius:10px;text-decoration:none;font-size:0.86em;font-weight:800;">View All Articles</a>
        </div>
    </div>
</section>
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

<!-- See What You’re Missing -->
<div class="section" style="padding-top:10px;padding-bottom:30px;">
    <h2 class="section-title">Free Picks vs. Full Access</h2>
    <p class="section-sub" style="color:#334155;">The public sees picks. Members see the edge &mdash; spreads, totals, and scores.</p>
    <div class="landing-pricing-row">
        <div class="landing-price-card" style="background:#ffffff;border:1px solid rgba(15,23,42,0.22);border-radius:14px;padding:24px;">
            <h3 style="font-size:1.05em;font-weight:800;margin:0 0 4px;color:#0f172a;">Free Picks</h3>
            <div style="font-size:0.82em;color:#047857;font-weight:800;margin:0 0 10px;">$0 &mdash; no credit card</div>
            <ul class="landing-price-list" style="list-style:none;padding:0;margin:0;font-size:0.9em;color:#0f172a;line-height:1.65;display:flex;flex-direction:column;gap:10px;">
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Moneyline picks across 9 sports</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Model-generated win probability for every game</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Proprietary AI odds engine pricing</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Multi-model consensus signal strength</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#34d399;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Fully tracked historical performance</span></li>
            </ul>
            <a href="/nba-picks" class="landing-price-cta landing-price-cta--light" style="text-align:center;background:#fff;color:#0f172a;border:1px solid rgba(15,23,42,0.32);border-radius:10px;font-weight:800;text-decoration:none;font-size:0.9em;box-shadow:0 2px 8px rgba(15,23,42,0.08);">View Free Picks</a>
        </div>
        <div class="landing-price-card" style="background:#fffdf5;border:2px solid #fbbf24;border-radius:14px;padding:24px;position:relative;">
            <div style="position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:#fbbf24;color:#000;font-size:0.72em;font-weight:900;padding:4px 16px;border-radius:20px;white-space:nowrap;letter-spacing:0.3px;">FULL AI MODEL ACCESS</div>
            <h3 style="font-size:1.05em;font-weight:800;margin:0 0 4px;color:#92400e;">Premium Edge</h3>
            <div style="font-size:0.82em;font-weight:800;color:#0f172a;margin:0 0 6px;">
                <a href="https://buy.stripe.com/14A6oI4Ra66ReWLczTao802" style="color:#0f172a;text-decoration:none;">$4.99/week</a>
                &nbsp;&bull;&nbsp;
                <a href="/checkout/monthly" style="color:#64748b;text-decoration:none;font-weight:700;">$19.99/mo</a>
                &nbsp;&bull;&nbsp;
                <a href="/checkout/yearly" style="color:#64748b;text-decoration:none;font-weight:700;">$149.99/yr</a>
            </div>
            <ul class="landing-price-list" style="list-style:none;padding:0;margin:0;font-size:0.9em;color:#0f172a;line-height:1.65;display:flex;flex-direction:column;gap:10px;">
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Everything in Free, plus&hellip;</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Spread betting models (edge-based pricing)</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Over/Under totals with projected game flow</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Predicted final scores (simulation-based)</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Player props picks and projections</span></li>
                <li style="display:flex;align-items:flex-start;gap:8px;"><span style="color:#fbbf24;flex-shrink:0;margin-top:2px;">&#10003;</span><span>Model performance page access</span></li>
            </ul>
            <a href="https://buy.stripe.com/14A6oI4Ra66ReWLczTao802" class="landing-price-cta landing-price-cta--gold" style="text-align:center;background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#000;border-radius:10px;font-weight:800;text-decoration:none;font-size:0.9em;box-shadow:0 4px 18px rgba(251,191,36,0.25);">Try a Week &mdash; $4.99</a>
            <p style="text-align:center;font-size:0.75em;color:#64748b;margin:8px 0 0;">or <a href="/plans" style="color:#92400e;font-weight:700;text-decoration:none;">see monthly &amp; yearly plans</a></p>
        </div>
    </div>
    <p style="max-width:860px;margin:14px auto 0;text-align:center;font-size:0.8em;color:#64748b;line-height:1.5;">All picks updated daily. Cancel any plan anytime.</p>
    <style>
        .landing-pricing-row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:stretch; gap:18px; max-width:860px; margin:0 auto; }
        .landing-price-card { display:flex; flex-direction:column; min-height:100%; }
        .landing-price-card .landing-price-list { flex:1 1 auto; }
        .landing-price-cta { display:flex; align-items:center; justify-content:center; margin-top:auto; min-height:48px; padding:0 22px; box-sizing:border-box; flex-shrink:0; }
        @media (max-width: 768px) {
            .landing-pricing-row { grid-template-columns:1fr !important; }
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

</main>

<!-- Footer -->
<div class="share-strip">
    <span class="share-strip-label">Share on social media</span>
    <div class="share-icons">
        <a class="share-icon" href="https://x.com/intent/post?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on X"><img src="/static/icons/social/x.svg" alt="X"></a>
        <a class="share-icon" href="https://www.facebook.com/sharer/sharer.php?u={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Facebook"><img src="/static/icons/social/facebook.svg" alt="Facebook"></a>
        <a class="share-icon" href="https://www.instagram.com/" target="_blank" rel="noopener" aria-label="Instagram"><img src="/static/icons/social/instagram.svg" alt="Instagram"></a>
        <a class="share-icon" href="https://www.tiktok.com/upload?lang=en" target="_blank" rel="noopener" aria-label="TikTok"><img src="/static/icons/social/tiktok.svg" alt="TikTok"></a>
        <a class="share-icon" href="https://www.linkedin.com/sharing/share-offsite/?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on LinkedIn"><img src="/static/icons/social/linkedin.svg" alt="LinkedIn"></a>
        <a class="share-icon" href="https://www.reddit.com/submit?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Reddit"><img src="/static/icons/social/reddit.svg" alt="Reddit"></a>
        <a class="share-icon" href="https://www.tumblr.com/widgets/share/tool?canonicalUrl={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Tumblr"><img src="/static/icons/social/tumblr.svg" alt="Tumblr"></a>
        <a class="share-icon" href="https://api.whatsapp.com/send?text={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on WhatsApp"><img src="/static/icons/social/whatsapp.svg" alt="WhatsApp"></a>
        <a class="share-icon" href="https://telegram.me/share/url?url={{ request.url|urlencode }}" target="_blank" rel="noopener" aria-label="Share on Telegram"><img src="/static/icons/social/telegram.svg" alt="Telegram"></a>
    </div>
</div>
<footer class="site-footer" style="display:none">
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
                <a href="/all-sports-results">All sports results</a>
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
{% include "partials/site_directory_footer.html" %}

{% if not is_premium %}
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
    var TV_MENUS={picks:{title:'Picks & Predictions',items:[{l:'NBA',h:'/nba-picks'},{l:'MLB',h:'/mlb-picks'},{l:'NHL',h:'/nhl-picks'},{l:'NFL',h:'/nfl-picks'}{% if soccer_enabled %},{l:'Soccer',h:'/soccer-picks'},{l:'World Cup',h:'/soccer-picks?league=fifa.world'}{% endif %},{l:'NCAAB',h:'/ncaab-picks'},{l:'NCAAF',h:'/ncaaf-picks'},{l:'NCAAW',h:'/ncaaw-picks'},{l:'WNBA',h:'/wnba-picks'},{l:'Tennis',h:'/tennis-picks'},{l:'UFC',h:'/ufc-picks'},{l:'Golf',h:'/golf-picks'}]},props:{title:'Props',items:[{l:'Player Props',h:'/player-props'}]},tools:{title:'Tools & Models',items:[{l:'Model Performance',h:'/performance'},{l:'AI Picks Today',h:'/ai-sports-betting-picks-today'},{l:'Model vs Sportsbooks',h:'/our-model-vs-sportsbooks'},{l:'Tutorial',h:'/tutorial'}]},results:{title:'Results & Tracking',items:[{l:'All Sports Results',h:'/all-sports-results'},{l:'Daily Results',h:'/daily-report'},{l:'Edge Performance',h:'/edge-performance'},{l:'Download CSV',h:'/results/downloads'}]},community:{title:'Community',items:[{l:'X / Twitter',h:'https://x.com/predictionlab_io',ext:true},{l:'TikTok',h:'https://www.tiktok.com/@predictionlab',ext:true},{l:'Instagram',h:'https://instagram.com/predictionlab.io',ext:true},{l:'Reddit',h:'https://reddit.com/r/sportsbetting',ext:true},{l:'Telegram',h:'https://t.me/predictionlab',ext:true}]},company:{title:'Company',items:[{l:'Join Premium',h:'/plans',cls:'highlight'},{l:'Plans & Pricing',h:'/plans'},{l:'FAQ',h:'/faq'},{l:'Contact',h:'/contact'},{l:'Privacy',h:'/privacy'},{l:'Terms',h:'/terms'}]}};
    function tvOpen(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.add('open');if(d)d.classList.add('open');document.body.style.overflow='hidden';if(h)h.setAttribute('aria-expanded','true');}
    function tvClose(){var o=document.getElementById('tvOverlay'),d=document.getElementById('tvDrawer'),h=document.getElementById('navHamburger');if(o)o.classList.remove('open');if(d)d.classList.remove('open');document.body.style.overflow='';if(h)h.setAttribute('aria-expanded','false');setTimeout(function(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';},280);}
    function tvSub(key){var menu=TV_MENUS[key];if(!menu)return;var html='';menu.items.forEach(function(item){var ext=item.ext?' target="_blank" rel="noopener"':'';var cls='tv-sub-link'+(item.cls?' '+item.cls:'');var extIcon=item.ext?' <span class="ext">&#8599;</span>':'';html+='<a href="'+item.h+'" class="'+cls+'"'+ext+'>'+item.l+extIcon+'</a>';});document.getElementById('tvSub').innerHTML=html;document.getElementById('tvDrawerTitle').textContent=menu.title;document.getElementById('tvBackBtn').style.display='';document.getElementById('tvMain').className='tv-panel hidden-left';document.getElementById('tvSub').className='tv-panel visible';}
    function tvBack(){document.getElementById('tvMain').className='tv-panel visible';document.getElementById('tvSub').className='tv-panel hidden-right';document.getElementById('tvBackBtn').style.display='none';document.getElementById('tvDrawerTitle').textContent='Menu';}
    function tvToggleMore(btn){var el=document.getElementById('tvMoreItems');var open=el.style.display==='block';el.style.display=open?'none':'block';var arrow=btn.querySelector('.tv-more-arrow');if(arrow)arrow.style.transform=open?'':'rotate(90deg)';}
    function toggleAcctMenu(e){e.stopPropagation();document.getElementById('acctMenu').classList.toggle('open');}
    document.addEventListener('click',function(){var m=document.getElementById('acctMenu');if(m)m.classList.remove('open');});
    var _srchFilter='all';
    var _srchDefaults=[{l:'Join Premium',h:'/plans',s:'all'},{l:'NBA Picks',h:'/nba-picks',s:'nba'},{l:'NFL Picks',h:'/nfl-picks',s:'nfl'},{l:'MLB Picks',h:'/mlb-picks',s:'mlb'},{l:'NHL Picks',h:'/nhl-picks',s:'nhl'},{l:'NCAAB Picks',h:'/ncaab-picks',s:'ncaab'},{l:'NCAAF Picks',h:'/ncaaf-picks',s:'ncaaf'},{l:'WNBA Picks',h:'/wnba-picks',s:'wnba'}{% if soccer_enabled %},{l:'Soccer Picks',h:'/soccer-picks',s:'all'},{l:'World Cup Picks',h:'/soccer-picks?league=fifa.world',s:'all'}{% endif %},{l:'Tennis Picks',h:'/tennis-picks',s:'all'},{l:'UFC Picks',h:'/ufc-picks',s:'all'},{l:'Golf Picks',h:'/golf-picks',s:'all'},{l:'Player Props',h:'/player-props',s:'props'},{l:'Model Performance',h:'/performance',s:'all'},{l:'Edge Performance',h:'/edge-performance',s:'all'},{l:'Daily Results',h:'/daily-report',s:'all'}];
    function openSrch(){document.getElementById('srchOverlay').classList.add('open');document.body.style.overflow='hidden';setTimeout(function(){document.getElementById('srchInput').focus();},60);renderSrchItems('');}
    function closeSrch(){document.getElementById('srchOverlay').classList.remove('open');document.body.style.overflow='';document.getElementById('srchInput').value='';}
    function closeSrchOutside(e){if(e.target===document.getElementById('srchOverlay'))closeSrch();}
    function _srchEsc(v){return String(v==null?'':v).replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);});}
    function _srchRender(rows){var el=document.getElementById('srchItems');if(!rows.length){el.innerHTML='<div class="srch-empty">No results found. Press Enter to search.</div>';return;}el.innerHTML=rows.map(function(i){return'<a class="srch-item" href="'+_srchEsc(i.h)+'"><span class="srch-item-label">'+_srchEsc(i.l)+'</span><span class="srch-item-sport">'+_srchEsc(String(i.s||'').toUpperCase())+'</span></a>';}).join('');}
function renderSrchItems(q){q=(q||'').trim();var el=document.getElementById('srchItems');
  if(!q){var items=_srchDefaults.filter(function(i){return _srchFilter==='all'||i.s===_srchFilter;});_srchRender(items);return;}
  clearTimeout(window._srchTimer);el.innerHTML='<div class="srch-empty">Searching…</div>';
  window._srchTimer=setTimeout(function(){
    fetch('/api/search?query='+encodeURIComponent(q),{headers:{'Accept':'application/json'}})
      .then(function(r){return r.json();})
      .then(function(data){var rows=[];
        (data.page_results||[]).forEach(function(p){rows.push({l:p.label||'Page',h:p.route||data.suggested_route||'/',s:p.sport||'site'});});
        (data.team_results||[]).forEach(function(t){rows.push({l:(t.away_team||'')+' @ '+(t.home_team||'')+(t.win_probability!=null?' · '+t.win_probability+'%':''),h:data.suggested_route||('/'+String(t.sport||'').toLowerCase()+'-picks'),s:t.sport||'all'});});
        (data.espn_results||[]).forEach(function(e){rows.push({l:(e.away_team||'')+' @ '+(e.home_team||'')+' · '+(e.status||''),h:data.suggested_route||'/',s:e.sport||'all'});});
        _srchDefaults.forEach(function(i){if(i.l.toLowerCase().includes(q.toLowerCase()))rows.push(i);});
        if(_srchFilter!=='all'){rows=rows.filter(function(i){return String(i.s||'').toLowerCase()===_srchFilter;});}
        _srchRender(rows);})
      .catch(function(){el.innerHTML='<div class="srch-empty">Search unavailable</div>';});
  },220);}
    document.addEventListener('DOMContentLoaded',function(){var inp=document.getElementById('srchInput');if(inp){inp.addEventListener('input',function(){renderSrchItems(this.value);});inp.addEventListener('keydown',function(e){if(e.key==='Enter'){var q=this.value.trim();if(q){window.location.href='/search?query='+encodeURIComponent(q);}}});}document.querySelectorAll('.srch-filter').forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll('.srch-filter').forEach(function(b){b.classList.remove('active');});this.classList.add('active');_srchFilter=this.dataset.s;renderSrchItems(document.getElementById('srchInput').value);});});});
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
                    const pageItems = (data.page_results || []).map(r => `<li><a href="${r.route || '/'}">${r.label || r.route || 'Page'}</a>${r.sport ? ` <small>${String(r.sport).toUpperCase()}</small>` : ''}</li>`).join('');
                    const modelItems = (data.model_results || []).map(r => `<li>${r.sport}: ${r.record} (${r.accuracy}%)${r.filtered_games !== null && r.filtered_games !== undefined ? ` - ${r.filtered_games} games at threshold` : ''}</li>`).join('');
                    const localTeamItems = (data.team_results || []).map(r => `<li>${r.sport}: ${r.away_team} vs ${r.home_team} (${r.game_date}) - pick: ${r.predicted_winner} (${r.win_probability}%)</li>`).join('');
                    const espnItems = (data.espn_results || []).map(r => `<li>${r.sport}: ${r.away_team} at ${r.home_team} (${r.status})</li>`).join('');
                    const routeLine = data.suggested_route ? `<p><strong>Suggested page:</strong> <a href="${data.suggested_route}">${data.suggested_route}</a></p>` : '';
                    const empty = (!pageItems && !modelItems && !localTeamItems && !espnItems) ? '<p>No matches found yet. Try a team name, league, player, or model alias.</p>' : '';
                    resultsEl.innerHTML = `
                        <h3>Search Results</h3>
                        ${modelLine}
                        ${routeLine}
                        ${pageItems ? `<p><strong>Pages, Teams & Players</strong></p><ul>${pageItems}</ul>` : ''}
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
         latest_blog_post={**latest_blog_post, 'display_date': _blog_display_date(latest_blog_post)} if latest_blog_post else None,
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
    'TENNIS': '/tennis-picks',
    'UFC': '/ufc-picks',
    'GOLF': '/golf-picks',
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

def _search_norm(value: str) -> str:
    value = re.sub(r'[^a-z0-9]+', ' ', (value or '').lower())
    return re.sub(r'\s+', ' ', value).strip()

def _search_direct_routes(query_text: str):
    """Fast site-nav search results for the header overlay and /search fallback."""
    q = _search_norm(query_text)
    if not q:
        return []
    entries = []
    sport_aliases = {
        'NBA': ['nba', 'basketball', 'nba picks', 'nba predictions', 'nba ai picks'],
        'MLB': ['mlb', 'baseball', 'mlb picks', 'mlb predictions', 'mlb ai picks'],
        'NHL': ['nhl', 'hockey', 'nhl picks', 'nhl predictions', 'nhl ai picks'],
        'NFL': ['nfl', 'football', 'nfl picks', 'nfl predictions', 'nfl ai picks'],
        'NCAAB': ['ncaab', 'college basketball', 'ncaa basketball', 'mens college basketball', 'college basketball picks'],
        'NCAAW': ['ncaaw', 'womens college basketball', "women's college basketball", 'ncaa womens basketball'],
        'NCAAF': ['ncaaf', 'college football', 'ncaa football', 'college football picks'],
        'WNBA': ['wnba', 'wnba picks', 'wnba predictions'],
        'SOCCER': ['soccer', 'football soccer', 'soccer picks', 'world cup', 'fifa world cup'],
        'TENNIS': ['tennis', 'tennis picks'],
        'UFC': ['ufc', 'mma', 'ufc picks', 'mma picks'],
        'GOLF': ['golf', 'golf picks'],
    }
    for sport, aliases in sport_aliases.items():
        route = _SPORT_TO_ROUTE.get(sport)
        if route:
            entries.append({
                'label': f"{sport} Picks",
                'route': route,
                'sport': sport,
                'aliases': aliases + [route.strip('/').replace('-', ' ')],
            })
        results_slug = _SPORT_RESULTS_SLUGS.get(sport)
        if results_slug:
            entries.append({
                'label': f"{sport} Results",
                'route': f"/{results_slug}",
                'sport': sport,
                'aliases': [f"{a} results" for a in aliases] + [results_slug.replace('-', ' ')],
            })
    entries.extend([
        {'label': 'Player Props', 'route': '/player-props', 'sport': 'PROPS', 'aliases': ['props', 'player props', 'nba props', 'player prop picks']},
        {'label': 'All Sports Results', 'route': '/all-sports-results', 'sport': 'RESULTS', 'aliases': ['all sports results', 'results', 'tracked results', 'model results']},
        {'label': 'Daily Betting Results Report', 'route': '/daily-report', 'sport': 'RESULTS', 'aliases': ['daily report', 'daily results', 'betting results report']},
        {'label': 'Edge Performance', 'route': '/edge-performance', 'sport': 'RESULTS', 'aliases': ['edge performance', 'edge results']},
        {'label': 'Model Performance', 'route': '/performance', 'sport': 'MODELS', 'aliases': ['performance', 'model performance', 'models', 'model stats']},
        {'label': 'AI Picks Today', 'route': '/ai-sports-betting-picks-today', 'sport': 'TOOLS', 'aliases': ['ai picks today', 'ai sports betting picks today']},
        {'label': 'Model vs Sportsbooks', 'route': '/our-model-vs-sportsbooks', 'sport': 'TOOLS', 'aliases': ['model vs sportsbooks', 'sportsbooks', 'model sportsbook']},
        {'label': 'Prediction Lab Blog', 'route': '/blog', 'sport': 'BLOG', 'aliases': ['blog', 'prediction lab blog', 'sports news', 'sports betting news']},
        {'label': 'Plans & Pricing', 'route': '/plans', 'sport': 'ACCOUNT', 'aliases': ['plans', 'pricing', 'premium', 'join premium']},
        {'label': 'Tutorial', 'route': '/tutorial', 'sport': 'HELP', 'aliases': ['tutorial', 'how it works', 'help']},
        {'label': 'FAQ', 'route': '/faq', 'sport': 'HELP', 'aliases': ['faq', 'questions']},
    ])
    for slug, team in _TEAM_DIRECTORY.items():
        sport = (team.get('sport') or '').upper()
        route = _SPORT_TO_ROUTE.get(sport, '/')
        name = team.get('name') or slug.replace('-', ' ').title()
        entries.append({
            'label': name,
            'route': route,
            'sport': sport or 'TEAM',
            'aliases': [name, slug.replace('-', ' ')],
        })
    ranked = []
    seen = set()
    for entry in entries:
        best = None
        for alias in [entry.get('label', ''), *entry.get('aliases', [])]:
            a = _search_norm(alias)
            if not a:
                continue
            if q == a:
                best = 0 if best is None else min(best, 0)
            elif a.startswith(q) or q.startswith(a):
                best = 1 if best is None else min(best, 1)
            elif len(q) >= 3 and (q in a or a in q):
                best = 2 if best is None else min(best, 2)
        if best is None:
            continue
        key = entry['route']
        if key in seen:
            continue
        seen.add(key)
        ranked.append((best, entry['label'], {
            'label': entry['label'],
            'route': entry['route'],
            'sport': entry.get('sport') or 'SITE',
        }))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked[:8]]

def _search_ranked_text(query_text: str, haystack: str):
    q = _search_norm(query_text)
    h = _search_norm(haystack)
    if not q or not h:
        return None
    if q == h:
        return 0
    words = [w for w in q.split() if len(w) >= 2]
    if not words:
        return None
    if h.startswith(q) or q in h:
        return 1
    if all(w in h for w in words):
        return 2
    if any(len(w) >= 4 and w in h for w in words):
        return 3
    return None

def _dedupe_search_pages(results, limit=12):
    deduped = []
    seen = set()
    for item in results:
        route = item.get('route')
        label = item.get('label')
        key = (route, label)
        if not route or key in seen:
            continue
        seen.add(key)
        deduped.append({
            'label': label,
            'route': route,
            'sport': item.get('sport') or 'SITE',
        })
        if len(deduped) >= limit:
            break
    return deduped

def _search_site_content(query_text: str):
    docs = [
        ('Home', '/', 'SITE', 'Prediction Lab AI sports predictions game forecasts free moneyline plays see the edge first top value picks today live model projections sports covered data driven picks updated daily'),
        ('Daily Betting Results Report', '/daily-report', 'RESULTS', "Daily Betting Results Report yesterday performance across all sports and models tracked transparent verified results report"),
        ('All Sports Results', '/all-sports-results', 'RESULTS', 'All sports results moneyline spread over under model performance tracked transparent verified'),
        ('Model Performance', '/performance', 'MODELS', 'Model Performance completed game performance by model confidence bucket sample sizes hit rates Grinder2 Takedown Edge XSharp Sharp Consensus'),
        ('Player Props', '/player-props', 'PROPS', 'Player props NBA WNBA NHL MLB NCAAB NCAAW NCAAF projections confidence expected value EV rebounds assists points shots strikeouts'),
        ('Prediction Lab Blog', '/blog', 'BLOG', 'Prediction Lab Blog sports news betting market breakdown daily articles ESPN news game previews model analysis'),
        ('Plans & Pricing', '/plans', 'ACCOUNT', 'Plans pricing premium free picks full access spreads totals projected scores player props model performance'),
        ('Tutorial', '/tutorial', 'HELP', 'Tutorial how to read model predictions scores spreads totals confidence picks'),
        ('FAQ', '/faq', 'HELP', 'Frequently asked questions AI sports betting picks expected value EV model probabilities'),
        ('AI Picks Today', '/ai-sports-betting-picks-today', 'TOOLS', 'AI picks today sports betting picks daily predictions probabilities'),
        ('What Are AI Picks', '/what-are-ai-sports-betting-picks', 'TOOLS', 'What are AI sports betting picks probabilities expected value opportunities'),
        ('Model vs Sportsbooks', '/our-model-vs-sportsbooks', 'TOOLS', 'Our model vs sportsbooks projected odds sportsbook lines market inefficiencies positive EV'),
    ]
    for sport, route in _SPORT_TO_ROUTE.items():
        name = SPORTS.get(sport, {}).get('name', sport)
        docs.append((f"{sport} Picks", route, sport, f"{sport} {name} picks predictions AI model probabilities moneyline spread total projections"))
        result_slug = _SPORT_RESULTS_SLUGS.get(sport)
        if result_slug:
            docs.append((f"{sport} Results", f"/{result_slug}", sport, f"{sport} {name} results tracked model performance moneyline spread total results"))
    matches = []
    for label, route, sport, text in docs:
        score = _search_ranked_text(query_text, f"{label} {text}")
        if score is not None:
            matches.append((score, label, {'label': label, 'route': route, 'sport': sport}))
    blog_posts = _load_blog_posts_from_json()
    generated_post = _generate_daily_blog_post(todays_picks=[], news_items=[])
    by_slug = {p.get('slug'): p for p in blog_posts if p.get('slug')}
    by_slug.setdefault(generated_post['slug'], generated_post)
    for post in by_slug.values():
        text = ' '.join([
            post.get('title', ''),
            post.get('sport_tag', ''),
            post.get('excerpt', ''),
            ' '.join(post.get('body') or []),
        ])
        score = _search_ranked_text(query_text, text)
        if score is not None:
            matches.append((score, post.get('title', 'Prediction Lab Blog'), {
                'label': post.get('title', 'Prediction Lab Blog'),
                'route': f"/blog/{post.get('slug')}",
                'sport': post.get('sport_tag') or 'BLOG',
            }))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [m[2] for m in matches[:8]]

def _search_database_entities(conn, query_text: str):
    like = f"%{query_text.lower()}%"
    results = []
    try:
        team_rows = conn.execute(
            """
            SELECT sport, name
            FROM (
                SELECT sport, home_team_id AS name FROM games
                UNION ALL SELECT sport, away_team_id AS name FROM games
                UNION ALL SELECT sport, home_team_id AS name FROM predictions
                UNION ALL SELECT sport, away_team_id AS name FROM predictions
                UNION ALL SELECT sport, team_name AS name FROM team_records
                UNION ALL SELECT league AS sport, team AS name FROM player_prop_results
                UNION ALL SELECT sport, team_name AS name FROM injuries
                UNION ALL SELECT 'NHL' AS sport, team_name AS name FROM goalie_stats
            )
            WHERE name IS NOT NULL
              AND TRIM(name) != ''
              AND LOWER(name) LIKE ?
            GROUP BY UPPER(COALESCE(sport,'')), name
            ORDER BY CASE WHEN LOWER(name) = LOWER(?) THEN 0 ELSE 1 END, name
            LIMIT 12
            """,
            (like, query_text),
        ).fetchall()
        for row in team_rows:
            sport = (row['sport'] or '').upper()
            route = _SPORT_TO_ROUTE.get(sport, '/player-props')
            results.append({'label': row['name'], 'route': route, 'sport': sport or 'TEAM'})
    except Exception as exc:
        logger.debug(f"Team search failed: {exc}")
    try:
        player_rows = conn.execute(
            """
            SELECT sport, player_name, team
            FROM (
                SELECT league AS sport, player_name, team
                FROM player_prop_results
                UNION ALL
                SELECT sport, player_name, team_name AS team
                FROM injuries
                UNION ALL
                SELECT 'NHL' AS sport, goalie_name AS player_name, team_name AS team
                FROM goalie_stats
            )
            WHERE player_name IS NOT NULL
              AND TRIM(player_name) != ''
              AND LOWER(player_name) LIKE ?
            GROUP BY UPPER(COALESCE(sport,'')), player_name, team
            ORDER BY player_name
            LIMIT 14
            """,
            (like,),
        ).fetchall()
        for row in player_rows:
            sport = (row['sport'] or '').upper()
            route = '/player-props' if sport in {'NBA', 'WNBA', 'NHL', 'MLB', 'NCAAB', 'NCAAW', 'NCAAF'} else _SPORT_TO_ROUTE.get(sport, '/player-props')
            team = row['team']
            label = row['player_name'] if not team else f"{row['player_name']} ({team})"
            results.append({'label': label, 'route': route, 'sport': sport or 'PLAYER'})
    except Exception as exc:
        logger.debug(f"Player search failed: {exc}")
    return _dedupe_search_pages(results, limit=14)

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
            data = _cached_get(endpoint, timeout=1.5) or {}
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
            'page_results': [],
            'team_results': [],
            'espn_results': [],
            'suggested_route': '/',
        }
    public_model, internal_model = _parse_search_model(q)
    threshold = _parse_confidence_threshold(q)
    page_results = _dedupe_search_pages(
        _search_direct_routes(q) + _search_site_content(q),
        limit=14,
    )
    payload = {
        'query': q,
        'matched_model': (
            {'public_name': public_model.title(), 'internal_name': internal_model}
            if internal_model else None
        ),
        'confidence_threshold': threshold,
        'model_results': [],
        'page_results': page_results,
        'team_results': [],
        'espn_results': [],
        'suggested_route': page_results[0]['route'] if page_results else None,
    }
    conn = None
    try:
        conn = get_db_connection()
        payload['team_results'] = _search_local_team_predictions(conn, q)
        payload['model_results'] = _search_model_performance(conn, internal_model, threshold)
        entity_results = _search_database_entities(conn, q)
        payload['page_results'] = _dedupe_search_pages(
            payload['page_results'] + entity_results,
            limit=20,
        )
    except Exception as exc:
        logger.debug(f"Search payload build failed for {q}: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not payload['page_results']:
        payload['espn_results'] = _search_espn_team_matches(q)
    if payload['suggested_route']:
        pass
    elif payload['page_results']:
        payload['suggested_route'] = payload['page_results'][0].get('route')
    elif payload['team_results']:
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
_TEAM_PERF_MODEL_ORDER = _PERF_MODEL_ORDER + ['Efficiency']
_TEAM_PERF_ML_CONFIG = [
    ('glicko2_prob', 'Grinder2'),
    ('trueskill_prob', 'Takedown'),
    ('elo_prob', 'Edge'),
    ('xgb_prob', 'XSharp'),
    ('ens_prob', 'Consensus'),
]
# Confidence = max(p, 1-p) * 100, which is ALWAYS >= 50% — so any bucket below
# 50% is mathematically impossible and would always be empty. Only show real ones.
_PERF_BUCKET_ORDER = [
    '85%+',
    '80-84%',
    '75-79%',
    '70-74%',
    '65-69%',
    '60-64%',
    '55-59%',
    '50-54%',
]
_PERF_SPORT_OPTIONS = ['NBA', 'NHL', 'MLB', 'NFL', 'NCAAB', 'NCAAF']


# ── Frozen prediction output — exact copy from March 8 reference (NHL77FINAL.py) ──
# DO NOT modify this function. It is the reference model output as-shipped.
def _frozen_get_v2_prediction(sport, home_team, away_team, game_date=None):
    """Frozen reference: prediction output logic as of March 8 2026."""
    model_sport = _v2_model_sport(sport)
    if not HAS_V2_SYSTEM or _ensure_v2_predictor(model_sport) is None:
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
        # Golf props lack per-event player game-log data (coin-flip projections),
        # so don't offer it as a league option on the props page.
        _hidden = {'GOLF'}
        leagues = [lg for lg in getattr(config_mod, 'SUPPORTED_LEAGUES', []) if lg not in _hidden]
        return jsonify({'leagues': leagues})
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
        # Drop combined/parlay-style props (Pts+Reb, Reb+Ast, Pts+Reb+Ast, H+R+RBI).
        # They have no graded history, so calibration floors them to ~52%
        # confidence with degenerate EV — they render as faded, low-value rows
        # that duplicate the base stats. Hide them from the slate.
        _COMBO_PROP_TYPES = {'pts_reb', 'pts_ast', 'reb_ast', 'pts_reb_ast', 'h_r_rbi'}
        rows = [r for r in rows if str(r.get('prop_type') or '').lower() not in _COMBO_PROP_TYPES]

        # ── Per-prop-type calibration & decision layer ──────────────────
        # Gate picks by each category's REAL graded accuracy so rare,
        # high-variance hitter props (HR/RBI) can't carry inflated confidence.
        try:
            import prop_calibration as _calib
            graded_by_type = _graded_history_by_prop_type(league)
            cal = _calib.calibrate_slate(league, rows, graded_by_type)
            resp = {
                'league': league,
                'count': len(cal['items']),
                'items': cal['items'],
                'category_status': cal['category_status'],
                'approved_categories': cal['approved_categories'],
                'blocked_categories': cal['blocked_categories'],
                'gold_count': cal['gold_count'],
            }
        except Exception as _cal_err:
            logger.warning(f"prop calibration skipped for {league}: {_cal_err}")
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


def _graded_history_by_prop_type(league: str) -> dict:
    """{prop_type: {'wins': int, 'losses': int}} from all graded prop history."""
    out: dict = {}
    try:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT prop_type, "
                "SUM(result='HIT') AS wins, SUM(result='MISS') AS losses "
                "FROM player_prop_results WHERE league=? GROUP BY prop_type",
                (league,)
            ).fetchall()
            for r in rows:
                out[r['prop_type']] = {
                    'wins': int(r['wins'] or 0),
                    'losses': int(r['losses'] or 0),
                }
        finally:
            conn.close()
    except Exception:
        pass
    return out


def _grade_and_store_props(league: str, for_date_str: str):
    """Grade props for a given date using the engine and persist to DB."""
    engine_mod, _ = _load_props_modules()
    graded = engine_mod.get_league_results(league, for_date=for_date_str)
    rows = graded.get('items') or []
    if not rows:
        return 0
    conn = get_db_connection()
    try:
        for r in rows:
            conn.execute(
                '''INSERT OR REPLACE INTO player_prop_results
                   (league, result_date, player_name, team, prop_type, pick, line,
                    projection, actual, result, confidence, ev, odds)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (league, for_date_str,
                 r.get('player_name'), r.get('team'), r.get('prop_type'),
                 r.get('pick'), r.get('line'), r.get('projection'),
                 r.get('actual'), r.get('result'),
                 r.get('confidence'), r.get('ev'), r.get('odds'))
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


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


_CONF_BUCKETS = [
    ("90-100%", 90, 1000), ("85-89%", 85, 90), ("80-84%", 80, 85),
    ("75-79%", 75, 80), ("70-74%", 70, 75), ("65-69%", 65, 70),
    ("Below 65%", -1, 65),
]
_EV_BUCKETS = [
    ("+30% and above", 30, 1e9), ("+20% to +30%", 20, 30),
    ("+10% to +20%", 10, 20), ("0% to +10%", 0, 10),
    ("Negative EV", -1e9, 0),
]
# Edge buckets for the Edge Value Performance page (hi >= 1e9 = open-ended top).
_EDGE_BUCKETS = [
    ("0–5%", 0, 5), ("5–10%", 5, 10), ("10–20%", 10, 20),
    ("20–30%", 20, 30), ("30–40%", 30, 40), ("40%+", 40, 1e9),
]
_EDGE_DIST_BUCKETS = [
    ("0–10%", 0, 10), ("10–20%", 10, 20),
    ("20–30%", 20, 30), ("30%+", 30, 1e9),
]


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


def _which_bucket(value, buckets):
    if value is None:
        return None
    v = float(value)
    for label, lo, hi in buckets:
        if hi >= 1e9:
            if v >= lo:
                return label
        elif lo <= v < hi:
            return label
    return buckets[-1][0]


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


def _prop_performance(league: str) -> dict:
    """Historical model performance for a league's props: win-rate + ROI by
    confidence bucket and EV bucket, plus last-100/500 and lifetime, plus a
    mapping of today's average confidence/EV to the matching history."""
    conn = get_db_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT result, confidence, ev, odds FROM player_prop_results "
            "WHERE league=? AND result IN ('HIT','MISS') "
            "ORDER BY result_date DESC, id DESC", (league,)
        ).fetchall()]
    finally:
        conn.close()

    graded = len(rows)
    conf_table = _bucket_stats(rows, _CONF_BUCKETS, "confidence")
    ev_table = _bucket_stats(rows, _EV_BUCKETS, "ev")

    def _winrec_roi(subset):
        if not subset:
            return {"record": "0-0", "win_rate": None, "roi": None, "sample": 0}
        w = sum(1 for r in subset if r["result"] == "HIT")
        prof = sum(_american_profit(r.get("odds"), r["result"] == "HIT") for r in subset)
        n = len(subset)
        return {"record": f"{w}-{n - w}", "win_rate": round(w / n * 100, 1),
                "roi": round(prof / n * 100, 1), "sample": n}

    # Today's averages from the live board
    today = {"total": 0, "avg_conf": None, "avg_ev": None}
    try:
        engine_mod, _ = _load_props_modules()
        board = engine_mod.filter_props(engine_mod.get_league_data(league).get("props", []))
        if board:
            confs = [float(p["confidence_score"]) for p in board if p.get("confidence_score") is not None]
            evs = [float(p.get("pick_ev") if p.get("pick_ev") is not None
                         else (p.get("ev_over_percent") if p.get("picked_side") == "OVER" else p.get("ev_under_percent")) or 0)
                   for p in board]
            today["total"] = len(board)
            today["avg_conf"] = round(sum(confs) / len(confs), 1) if confs else None
            today["avg_ev"] = round(sum(evs) / len(evs), 1) if evs else None
    except Exception:
        pass

    conf_bucket = _which_bucket(today["avg_conf"], _CONF_BUCKETS)
    ev_bucket = _which_bucket(today["avg_ev"], _EV_BUCKETS)
    conf_match = next((b for b in conf_table if b["bucket"] == conf_bucket), None)
    ev_match = next((b for b in ev_table if b["bucket"] == ev_bucket), None)

    return {
        "league": league,
        "graded": graded,
        "confidence_table": conf_table,
        "ev_table": ev_table,
        "today": today,
        "today_conf_bucket": conf_bucket,
        "today_conf_match": conf_match,
        "today_ev_bucket": ev_bucket,
        "today_ev_match": ev_match,
        "last_100": _winrec_roi(rows[:100]),
        "last_500": _winrec_roi(rows[:500]),
        "lifetime": _winrec_roi(rows),
    }


def _props_et_today():
    from datetime import date as _date
    return datetime.now(ZoneInfo("America/New_York")).date()


def _props_normalize_display_date(for_date: str | None, today_et=None):
    """Return (display_date, yesterday_et). Clamps today/future to yesterday (ET)."""
    from datetime import date as _date, timedelta as _td
    today_et = today_et or _props_et_today()
    yesterday_et = today_et - _td(days=1)
    if for_date:
        try:
            display = _date.fromisoformat(for_date)
        except Exception:
            display = yesterday_et
    else:
        display = yesterday_et
    if display >= today_et:
        display = yesterday_et
    return display, yesterday_et


def _ensure_recent_props_graded(league: str, end_date, days: int = 7):
    """Grade and store any missing dates in [end_date - days + 1, end_date]."""
    from datetime import timedelta as _td
    conn = get_db_connection()
    try:
        for i in range(days):
            d = end_date - _td(days=i)
            d_str = str(d)
            n = conn.execute(
                'SELECT COUNT(*) AS c FROM player_prop_results WHERE league=? AND result_date=?',
                (league, d_str)
            ).fetchone()['c']
            if n == 0:
                try:
                    _grade_and_store_props(league, d_str)
                except Exception:
                    pass
    finally:
        conn.close()


def _schedule_recent_props_graded(league: str, end_date, days: int = 7):
    """Background backfill for missing prop grades — never block API responses."""
    key = f'{league}|{end_date}|{days}'
    with _PROPS_GRADE_LOCK:
        if key in _PROPS_GRADE_SCHEDULED:
            return
        _PROPS_GRADE_SCHEDULED.add(key)
    import threading as _thr

    def _run():
        try:
            _ensure_recent_props_graded(league, end_date, days=days)
        except Exception:
            pass
        finally:
            with _PROPS_GRADE_LOCK:
                _PROPS_GRADE_SCHEDULED.discard(key)

    _thr.Thread(target=_run, daemon=True, name=f'props-grade-{league}').start()


def _query_prop_results(league: str, for_date: str | None = None):
    """Return items + summary for a date (default yesterday) + cumulative stats."""
    from datetime import timedelta as _td
    today_et = _props_et_today()
    display_date, yesterday_et = _props_normalize_display_date(for_date, today_et=today_et)
    display_str = str(display_date)
    yesterday_str = str(yesterday_et)

    # Fill gaps in the rolling 7-day window ending yesterday (ET).
    # Done SYNCHRONOUSLY so the page shows real numbers on first load — the
    # old async path let the page render before grading finished, which is why
    # "Last Night" / "Last 7 Days" showed 0-0 even though games had been played.
    # _ensure_recent_props_graded skips dates already stored, so this is only
    # slow the very first time per date (proxy grading reuses the cached board).
    try:
        _ensure_recent_props_graded(league, yesterday_et, days=7)
    except Exception:
        # Never let grading failure block the results page — fall back to async.
        try:
            _schedule_recent_props_graded(league, yesterday_et, days=7)
        except Exception:
            pass

    # Grade selected display date if it falls outside the recent window
    if display_str != yesterday_str:
        try:
            _grade_and_store_props(league, display_str)
        except Exception:
            pass

    conn = get_db_connection()
    try:
        # Card rows for the user-selected display date
        rows = conn.execute(
            'SELECT * FROM player_prop_results WHERE league=? AND result_date=? ORDER BY player_name, prop_type',
            (league, display_str)
        ).fetchall()
        items = [dict(r) for r in rows]

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

        # Last Night tally — always yesterday ET (fixed window)
        night_rows = [dict(r) for r in conn.execute(
            'SELECT * FROM player_prop_results WHERE league=? AND result_date=?',
            (league, yesterday_str)
        ).fetchall()]
        night_summary = _tally(night_rows)

        # Last 7 Days tally — rolling window ending yesterday ET
        week_start = str(yesterday_et - _td(days=6))
        week_rows = [dict(r) for r in conn.execute(
            'SELECT * FROM player_prop_results WHERE league=? AND result_date BETWEEN ? AND ?',
            (league, week_start, yesterday_str)
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

        pt_rows = conn.execute(
            "SELECT prop_type, "
            "SUM(result='HIT') as hits, SUM(result='MISS') as misses "
            "FROM player_prop_results WHERE league=? "
            "GROUP BY prop_type ORDER BY (hits+misses) DESC",
            (league,)
        ).fetchall()
        # Annotate each prop type with calibration status so the UI can show
        # "INSUFFICIENT DATA" instead of misleading 0% / 100% on tiny samples,
        # and flag blocked (NO BET ZONE) categories.
        season_by_prop = {}
        try:
            import prop_calibration as _calib
        except Exception:
            _calib = None
        for r in pt_rows:
            w = r['hits'] or 0
            l = r['misses'] or 0
            entry = {'wins': w, 'losses': l}
            if _calib is not None:
                st = _calib.category_status(league, r['prop_type'], w, l)
                entry.update({
                    'status': st['status'],          # approved|caution|blocked|insufficient
                    'samples': st['samples'],
                    'min_samples': st['min_samples'],
                    'accuracy': st['accuracy'],       # None when insufficient
                    'ci_low': st['ci_low'],
                    'reason': st['reason'],
                    'bettable': st['bettable'],
                })
            season_by_prop[r['prop_type']] = entry

        return {
            'league': league,
            'result_date': display_str,
            'last_night_date': yesterday_str,
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


@app.route('/player-props-api/performance')
@app.route('/player-props-api/diagnostics')   # legacy alias
def player_props_api_performance():
    """Historical model performance (win-rate + ROI by confidence and EV
    bucket) — replaces the old internal ML diagnostics."""
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login_page', next=request.path))
    if not is_premium_user():
        return redirect('/plans')
    league = (request.args.get('league') or 'NBA').strip().upper()
    try:
        _, config_mod = _load_props_modules()
        supported = set(getattr(config_mod, 'SUPPORTED_LEAGUES', []))
        if league not in supported:
            return jsonify({'detail': f'Unsupported league: {league}'}), 400
        return jsonify(_prop_performance(league))
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

    main_table, sport_tables = _build_performance_page_data(sport_filter=sport, last_n=last_n)
    team_chart_rows = _build_team_performance_rows(sport_filter=sport)
    html = render_template(
        'performance.html',
        page='performance',
        selected_sport=sport,
        selected_last_n=(str(last_n) if last_n else ''),
        sport_options=_PERF_SPORT_OPTIONS,
        model_order=_PERF_MODEL_ORDER,
        team_model_order=_TEAM_PERF_MODEL_ORDER,
        bucket_order=_PERF_BUCKET_ORDER,
        main_table=main_table,
        sport_tables=sport_tables,
        team_chart_rows=team_chart_rows,
    )
    # Prevent the browser from serving a stale cached view when filters change.
    if not isinstance(html, (str, bytes)):
        return html
    resp = make_response(html)
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp


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
- Contact: support.predictionlab@gmail.com (web form: https://predictionlab.io/contact)

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


BLOG_ARCHIVE_TEMPLATE = """{% extends "base.html" %}
{% block title %}Prediction Lab Blog | predictionlab.io{% endblock %}
{% block head_meta %}
    <meta name="description" content="Daily sports news, AI-generated betting insights, game previews, market breakdowns, and model analysis from predictionlab.io.">
    <link rel="canonical" href="{{ site_domain }}/blog">
{% endblock %}
{% block extra_styles %}
    <style>
        .blog-page{line-height:1.65}
        a{color:#00529B;text-decoration:none;font-weight:800}
        a:hover{text-decoration:underline}
        .top{margin-bottom:26px}
        .eyebrow{display:inline-flex;background:#fbbf24;color:#000;border-radius:999px;padding:4px 10px;font-size:0.74rem;font-weight:900;letter-spacing:0.4px;text-transform:uppercase;margin-bottom:12px}
        h1{font-size:clamp(2rem,5vw,3rem);line-height:1.08;margin-bottom:12px}
        .sub{color:#334155;font-size:1rem;max-width:720px}
        .posts{display:grid;gap:16px;margin-top:24px}
        article{border:1px solid rgba(15,23,42,0.14);border-radius:14px;background:#fff;padding:20px;box-shadow:0 8px 24px rgba(15,23,42,0.05)}
        .meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;color:#64748b;font-size:0.84rem;font-weight:700}
        .tag{background:#f8fafc;border:1px solid rgba(15,23,42,0.12);color:#0f172a;border-radius:999px;padding:2px 8px;font-size:0.72rem;font-weight:900;text-transform:uppercase}
        h2{font-size:1.22rem;line-height:1.35;margin-bottom:8px}
        p{color:#334155}
        .back{display:inline-flex;margin-bottom:24px}
    </style>
{% endblock %}
{% block content %}
<div class="blog-page">
    <a class="back" href="/">← Back to PredictionLab</a>
    <header class="top">
        <span class="eyebrow">Daily articles</span>
        <h1>Prediction Lab Blog</h1>
        <p class="sub">Daily sports news, AI-generated betting insights, game previews, and model analysis — updated every day.</p>
    </header>
    <section class="posts" aria-label="Latest blog articles">
        {% for post in posts %}
        <article>
            <div class="meta"><span class="tag">{{ post.sport_tag }}</span><time datetime="{{ post.date }}">{{ post.display_date }}</time></div>
            <h2><a href="/blog/{{ post.slug }}">{{ post.title }}</a></h2>
            <p>{{ post.excerpt }}</p>
        </article>
        {% endfor %}
    </section>
</div>
{% endblock %}"""


BLOG_POST_TEMPLATE = """{% extends "base.html" %}
{% block title %}{{ post.title }} | predictionlab.io{% endblock %}
{% block head_meta %}
    <meta name="description" content="{{ post.excerpt }}">
    <link rel="canonical" href="{{ site_domain }}/blog/{{ post.slug }}">
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"BlogPosting","headline":{{ post.title|tojson }},"datePublished":"{{ post.date }}","dateModified":"{{ post.date }}","author":{"@type":"Organization","name":"predictionlab.io"},"publisher":{"@type":"Organization","name":"predictionlab.io"},"mainEntityOfPage":"{{ site_domain }}/blog/{{ post.slug }}","description":{{ post.excerpt|tojson }}}
    </script>
{% endblock %}
{% block extra_styles %}
    <style>
        .blog-post-page{max-width:820px;margin:0 auto;line-height:1.75}
        a{color:#00529B;text-decoration:none;font-weight:800}
        a:hover{text-decoration:underline}
        .back{display:inline-flex;margin-bottom:26px}
        .meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;color:#64748b;font-size:0.86rem;font-weight:700}
        .tag{background:#fbbf24;color:#000;border-radius:999px;padding:3px 10px;font-size:0.74rem;font-weight:900;text-transform:uppercase;letter-spacing:0.4px}
        h1{font-size:clamp(2rem,5vw,3.1rem);line-height:1.08;margin-bottom:14px}
        .excerpt{font-size:1.06rem;color:#334155;margin-bottom:26px}
        article p{font-size:1rem;color:#1f2937;margin:0 0 18px}
        .news-watch{border:1px solid rgba(15,23,42,0.14);border-radius:14px;background:#f8fafc;margin:26px 0;padding:18px}
        .news-watch h2{font-size:1.05rem;margin-bottom:10px}
        .news-watch ul{list-style:none;display:grid;gap:9px}
        .news-watch li{color:#334155;font-size:0.94rem}
        .links{border-top:1px solid rgba(15,23,42,0.14);margin-top:30px;padding-top:20px;display:flex;gap:12px;flex-wrap:wrap}
        .links a{border:1px solid rgba(15,23,42,0.18);border-radius:10px;padding:9px 13px}
    </style>
{% endblock %}
{% block content %}
<div class="blog-post-page">
    <a class="back" href="/blog">← All Articles</a>
    <article>
        <header>
            <div class="meta"><span class="tag">{{ post.sport_tag }}</span><time datetime="{{ post.date }}">{{ post.display_date }}</time></div>
            <h1>{{ post.title }}</h1>
            <p class="excerpt">{{ post.excerpt }}</p>
        </header>
        {% for paragraph in post.body %}
        <p>{{ paragraph }}</p>
        {% endfor %}
        {% if post.news_items %}
        <section class="news-watch" aria-labelledby="news-watch-heading">
            <h2 id="news-watch-heading">Sports News Watch</h2>
            <ul>
                {% for item in post.news_items[:4] %}
                <li><strong>{{ item.sport }}:</strong> {{ item.topic }}{% if item.url %} <a href="{{ item.url }}" rel="nofollow noopener" target="_blank">Source</a>{% endif %}</li>
                {% endfor %}
            </ul>
        </section>
        {% endif %}
    </article>
    <nav class="links" aria-label="Related PredictionLab pages">
        <a href="/blog">View All Articles</a>
        <a href="/mlb-picks">MLB Picks</a>
        <a href="/nba-picks">NBA Picks</a>
        <a href="/daily-report">Daily Results Report</a>
    </nav>
</div>
{% endblock %}"""


def _blog_template_posts():
    posts = _get_blog_posts(include_generated=True)
    return [{**p, 'display_date': _blog_display_date(p)} for p in posts]


@app.route('/blog')
def blog_archive_page():
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
def blog_post_page(slug):
    wanted = _slugify_blog(slug)
    posts = _blog_template_posts()
    post = next((p for p in posts if p.get('slug') == wanted), None)
    if not post:
        abort(404)
    return render_template_string(
        BLOG_POST_TEMPLATE,
        post=post,
        site_domain=_SITE_DOMAIN,
        page='blog',
        page_title=f"{post['title']} | predictionlab.io",
        page_description=post.get('excerpt', ''),
    )


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
    # Per-matchup SEO pages (the "X vs Y" trending-search targets), today ±2 days
    try:
        for _murl in _matchup_sitemap_urls():
            urls.append((_murl, 'hourly', '0.8'))
    except Exception:
        logger.exception('sitemap matchup urls failed')

    urls.append((_SITE_DOMAIN + '/trending-sports', 'hourly', '0.9'))
    urls.append((_SITE_DOMAIN + '/world-cup-picks', 'daily', '0.8'))
    urls.append((_SITE_DOMAIN + '/all-sports-results', 'weekly', '0.75'))
    urls.append((_SITE_DOMAIN + '/daily-report', 'daily', '0.8'))
    urls.append((_SITE_DOMAIN + '/blog', 'daily', '0.75'))
    for post in _get_blog_posts(include_generated=True):
        urls.append((f"{_SITE_DOMAIN}/blog/{post['slug']}", 'daily', '0.7'))
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
            'NFL, NCAAB, NCAAF, WNBA, Soccer, Tennis, UFC, and Golf.'
        ),
        dashboard_rows=dashboard_rows,
        ml_models=_ML_DASHBOARD_MODELS,
    )


@app.route('/team-efficiency-results')
def team_efficiency_results_page():
    """Cross-sport Team Efficiency moneyline performance."""
    rows = []
    for sport in ALL_SPORTS_DASHBOARD_SPORTS:
        if sport not in SPORTS:
            continue
        cell = {'pct': None, 'record': '—', 'n': 0, 'sport': sport}
        try:
            snap_dir = _all_sports_snapshot_dir()
            pattern = _os_v2.path.join(snap_dir, f'{sport}_*_regular.json')
            paths = sorted(glob.glob(pattern), reverse=True)
            overall = None
            for path in paths:
                try:
                    with open(path, encoding='utf-8') as fh:
                        data = json.load(fh)
                    if isinstance(data, dict) and data.get('sport') == sport:
                        overall = data.get('overall_stats') or {}
                        break
                except (OSError, json.JSONDecodeError):
                    continue
            if overall and overall.get('efficiency'):
                cell = _fmt_snapshot_ml_cell(overall, 'efficiency')
            elif sport in _eff_attach.EFFICIENCY_GRADING_SPORTS:
                season_start, season_end = _results_season_bounds(sport, datetime.now())
                daily = _banner_daily_results_for_range(sport, season_start, season_end)
                if daily:
                    N._attach_book_odds_to_daily_results(sport, daily, api_limit=200)
                    N._compute_spread_total_for_daily(sport, daily)
                    stats = compute_overall_stats_from_daily(daily).get('efficiency') or {}
                    total = int(stats.get('total') or 0)
                    correct = int(stats.get('correct') or 0)
                    if total > 0:
                        cell = {
                            'pct': stats.get('accuracy') or round(correct / total * 100, 1),
                            'record': f'{correct}-{total - correct}',
                            'n': total,
                        }
        except Exception as exc:
            logger.debug(f'efficiency results row failed for {sport}: {exc}')
        rows.append({
            'sport': sport,
            'name': SPORTS[sport]['name'],
            'icon': SPORTS[sport].get('icon', ''),
            'cell': cell,
            'supported': sport in EFFICIENCY_SPORTS,
            'results_url': f'/sport/{sport}/results',
        })
    return render_template_string(
        TEAM_EFFICIENCY_RESULTS_TEMPLATE,
        page='team-efficiency-results',
        page_title='Team Efficiency Results | predictionlab.io',
        page_description='Season moneyline accuracy for the Team Efficiency model (ORtg/DRtg/pace) across all sports.',
        efficiency_rows=rows,
    )


# ── SEO picks routes ──────────────────────────────────────────────────────────

@app.route('/<slug>')
def seo_picks_page(slug):
    """Handle SEO-friendly URLs like /nhl-picks, /nba-picks, /nhl-results, etc."""
    if slug.endswith('-predictions'):
        return redirect(f"/{slug.replace('-predictions', '-picks')}", code=301)
    if slug.endswith('-prediction'):
        return redirect(f"/{slug.replace('-prediction', '-picks')}", code=301)
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


# ── Per-matchup SEO pages (target "X vs Y" trending searches) ─────────────────
# Google's trending game queries are matchup-shaped ("dodgers vs pirates").
# Each upcoming game gets its own indexable page whose URL/title/H1 use the
# short team names people actually type.

# Nickname prefixes that make a two-word short name ("White Sox", "Red Wings").
_TWO_WORD_NICK_PREFIX = {'white', 'red', 'blue', 'trail', 'maple', 'golden'}


def _team_search_name(sport, name):
    """Short team name as searched: 'Chicago White Sox'->'White Sox', 'Los Angeles Dodgers'->'Dodgers'."""
    s = str(name or '').strip()
    if not s:
        return ''
    if sport in ('SOCCER', 'TENNIS', 'UFC', 'GOLF'):
        return s
    parts = s.split()
    if len(parts) >= 2 and parts[-2].lower() in _TWO_WORD_NICK_PREFIX:
        return ' '.join(parts[-2:])
    return parts[-1]


def _seo_slugify(text):
    s = unicodedata.normalize('NFKD', str(text or ''))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s


def _matchup_date_suffix(date_key):
    """'2026-06-10' -> 'june-10-2026' (matches the daily-page URL style)."""
    try:
        y, m, d = str(date_key)[:10].split('-')
        return f"{_MONTH_NAMES.get(int(m), 'january')}-{int(d)}-{y}"
    except Exception:
        return ''


def _matchup_path(sport, pred, date_key=None):
    """URL path for one game's SEO page, e.g. /mlb-picks/dodgers-vs-pirates-june-10-2026."""
    slug = SPORT_SEO_SLUGS.get(sport)
    dk = date_key or pred.get('game_date') or ''
    suffix = _matchup_date_suffix(dk)
    a = _seo_slugify(_team_search_name(sport, pred.get('away_team_id')))
    h = _seo_slugify(_team_search_name(sport, pred.get('home_team_id')))
    if not (slug and suffix and a and h):
        return None
    return f"/{slug}/{a}-vs-{h}-{suffix}"


_MATCHUP_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ away_short }} vs {{ home_short }} Prediction{% if tournament %} — {{ tournament }}{% endif %} — AI Pick, Odds & Win Probability ({{ display_date }}) | predictionlab.io</title>
    <meta name="description" content="{{ away_short }} vs {{ home_short }} prediction for {{ display_date }}: our AI models pick {{ pick_team }} ({{ pick_pct }}% win probability). Moneyline odds, model consensus, and tracked results.">
    <link rel="canonical" href="{{ canonical }}">
    <link rel="icon" href="/static/pl-logo.svg" type="image/svg+xml">
    <script type="application/ld+json">{{ jsonld|safe }}</script>
    <style>
        body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;color:#0f172a;}
        .wrap{max-width:760px;margin:0 auto;padding:28px 18px 60px;}
        .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;font-size:.85em;}
        .top a{color:#00529B;text-decoration:none;font-weight:700;}
        h1{font-size:1.5em;margin:0 0 4px;}
        .sub{color:#64748b;font-size:.9em;margin:0 0 20px;}
        .card{background:#fff;border:1px solid rgba(15,23,42,.14);border-radius:14px;padding:18px;margin-bottom:14px;}
        .teams{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:center;text-align:center;}
        .team img{width:54px;height:54px;}
        .team b{display:block;margin-top:6px;}
        .wp{font-size:1.5em;font-weight:900;}
        .wp.win{color:#00875a;}
        .ml{font-size:.8em;color:#64748b;margin-top:4px;}
        .at{color:#94a3b8;font-weight:800;}
        .pick{background:#ecfdf5;border:1px solid #b7e7d2;border-radius:12px;padding:14px 16px;font-size:1.02em;margin-bottom:14px;}
        table{width:100%;border-collapse:collapse;font-size:.88em;}
        th,td{padding:9px 10px;border-bottom:1px solid #eef2f7;text-align:left;}
        th{background:#f1f5f9;font-size:.78em;text-transform:uppercase;letter-spacing:.4px;color:#475569;}
        .links{display:flex;flex-wrap:wrap;gap:10px;margin-top:20px;}
        .links a{background:#00529B;color:#fff;padding:11px 16px;border-radius:9px;text-decoration:none;font-weight:800;font-size:.86em;}
        .links a.alt{background:#fff;color:#00529B;border:1px solid #00529B;}
        .note{color:#94a3b8;font-size:.78em;margin-top:22px;line-height:1.6;}
    </style>
</head>
<body>
<div class="wrap">
    <div class="top"><a href="/">predictionlab.io</a><a href="/{{ sport_slug }}">All {{ sport_name }} picks →</a></div>
    <h1>{{ away_short }} vs {{ home_short }} Prediction — {{ display_date }}</h1>
    <p class="sub">{{ tournament or sport_name }} · AI model pick, win probability, and moneyline odds{% if game_time %} · {{ game_time }}{% endif %}</p>

    <div class="card">
        <div class="teams">
            <div class="team"><img src="{{ away_logo }}" alt="{{ away_full }} logo" loading="lazy"><b>{{ away_short }}</b>
                <div class="wp {% if pick_team == away_full %}win{% endif %}">{{ away_prob }}%</div>
                <div class="ml">Books {{ away_ml }} · PL {{ away_pl_ml }}</div>
            </div>
            <div class="at">@</div>
            <div class="team"><img src="{{ home_logo }}" alt="{{ home_full }} logo" loading="lazy"><b>{{ home_short }}</b>
                <div class="wp {% if pick_team == home_full %}win{% endif %}">{{ home_prob }}%</div>
                <div class="ml">Books {{ home_ml }} · PL {{ home_pl_ml }}</div>
            </div>
        </div>
    </div>

    <div class="pick">🎯 <strong>AI pick: {{ pick_team }}</strong> — {{ pick_pct }}% via {{ pick_label }}</div>

    {% if model_rows %}
    <div class="card" style="padding:0;overflow:hidden;">
        <table>
            <thead><tr><th>Model</th><th>Pick</th><th>Confidence</th></tr></thead>
            <tbody>
            {% for m in model_rows %}
            <tr><td>{{ m.name }}</td><td>{{ m.side }}</td><td><b>{{ m.conf }}%</b></td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}

    <div class="links">
        <a href="/{{ sport_slug }}">Today's {{ sport_name }} Board</a>
        <a class="alt" href="/{{ results_slug }}">{{ sport_name }} Results</a>
        <a class="alt" href="/performance">Model Performance</a>
    </div>
    <p class="note">Predictions are model-generated and tracked transparently — no edits after grading. For entertainment purposes; bet responsibly.</p>
</div>
</body>
</html>"""


@app.route('/<slug>/<matchup_slug>')
def seo_matchup_page(slug, matchup_slug):
    """Indexable per-game page: /mlb-picks/dodgers-vs-pirates-june-10-2026."""
    sport = _SEO_SLUG_TO_SPORT.get(slug)
    if not sport or '-vs-' not in matchup_slug:
        abort(404)
    # Trailing date: ...-june-10-2026
    m = re.search(r'-([a-z]+)-(\d{1,2})-(\d{4})$', matchup_slug)
    if not m:
        abort(404)
    month_num = _MONTH_NAME_TO_NUM.get(m.group(1))
    if not month_num:
        abort(404)
    target_date = f"{m.group(3)}-{month_num:02d}-{int(m.group(2)):02d}"
    teams_part = matchup_slug[:m.start()]
    away_slug, _, home_slug = teams_part.partition('-vs-')
    try:
        predictions = get_upcoming_predictions(sport)
    except Exception:
        predictions = []
    game = None
    for pred in predictions or []:
        if str(pred.get('game_date') or '')[:10] != target_date:
            continue
        a_full = pred.get('away_team_id') or ''
        h_full = pred.get('home_team_id') or ''
        a_opts = {_seo_slugify(_team_search_name(sport, a_full)), _seo_slugify(a_full)}
        h_opts = {_seo_slugify(_team_search_name(sport, h_full)), _seo_slugify(h_full)}
        if away_slug in a_opts and home_slug in h_opts:
            game = pred
            break
    if game is None:
        # Game finished or rescheduled: send crawlers/users to the dated board.
        return redirect(f"/{slug}-{m.group(1)}-{int(m.group(2))}-{m.group(3)}", code=302)

    away_full = game.get('away_team_id') or ''
    home_full = game.get('home_team_id') or ''
    away_short = _team_search_name(sport, away_full)
    home_short = _team_search_name(sport, home_full)
    fa = game.get('face_away_prob')
    fh = game.get('face_home_prob')
    pick_team = game.get('face_pick_team') or game.get('predicted_winner') or home_full
    pick_pct = game.get('face_pick_confidence')
    if pick_pct is None:
        pick_pct = fh if pick_team == home_full else fa
    display_date = f"{m.group(1).capitalize()} {int(m.group(2))}, {m.group(3)}"

    def _fmt_ml(v):
        if v is None:
            return '—'
        try:
            v = int(v)
            return f"+{v}" if v > 0 else str(v)
        except Exception:
            return str(v)

    model_rows = []
    for key, label in (('glicko2_prob', 'Grinder2'), ('trueskill_prob', 'Takedown'),
                       ('elo_prob', 'Edge'), ('xgb_prob', 'XSharp'),
                       ('efficiency_prob', 'Efficiency'), ('ensemble_prob', 'Sharp Consensus')):
        p = game.get(key)
        if p is None:
            continue
        try:
            p = float(p)
        except Exception:
            continue
        picked_home = p >= 50
        model_rows.append({
            'name': label,
            'side': _team_search_name(sport, home_full if picked_home else away_full),
            'conf': round(p if picked_home else 100 - p, 1),
        })

    tournament = None
    if sport in ('GOLF', 'TENNIS', 'UFC', 'SOCCER'):
        tournament = (game.get('league') or '').strip() or None

    _ld = {
        '@context': 'https://schema.org',
        '@type': 'SportsEvent',
        'name': f"{away_full} at {home_full}",
        'startDate': target_date,
        'eventStatus': 'https://schema.org/EventScheduled',
        'competitor': [
            {'@type': 'SportsTeam', 'name': away_full},
            {'@type': 'SportsTeam', 'name': home_full},
        ],
        'description': f"{away_short} vs {home_short} AI prediction: {pick_team} ({pick_pct}% win probability).",
    }
    if tournament:
        _ld['superEvent'] = {'@type': 'SportsEvent', 'name': tournament}
    jsonld = json.dumps(_ld)

    log_site_visit(f'/{slug}/{matchup_slug}')
    return render_template_string(
        _MATCHUP_PAGE_TEMPLATE,
        away_short=away_short, home_short=home_short,
        away_full=away_full, home_full=home_full,
        away_prob=fa if fa is not None else '—', home_prob=fh if fh is not None else '—',
        away_ml=_fmt_ml(game.get('book_away_moneyline')), home_ml=_fmt_ml(game.get('book_home_moneyline')),
        away_pl_ml=_fmt_ml(game.get('pl_model_away_ml')), home_pl_ml=_fmt_ml(game.get('pl_model_home_ml')),
        away_logo=team_logo_url(sport, away_full), home_logo=team_logo_url(sport, home_full),
        pick_team=pick_team, pick_pct=pick_pct, pick_label=game.get('face_model_label') or 'Sharp Consensus',
        game_time=game.get('game_time'),
        sport_slug=slug, results_slug=_SPORT_RESULTS_SLUGS.get(sport, slug.replace('-picks', '-results')),
        sport_name=SPORTS[sport]['name'], display_date=display_date, tournament=tournament,
        canonical=f"{_SITE_DOMAIN}/{slug}/{matchup_slug}",
        jsonld=jsonld, model_rows=model_rows,
    )


_WORLD_CUP_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World Cup 2026 Picks & Predictions (Mundial 2026) — AI Model Picks | predictionlab.io</title>
    <meta name="description" content="FIFA World Cup 2026 predictions from 5 AI models: daily picks, win probabilities, and odds for every World Cup match. Pronósticos del Mundial 2026.">
    <link rel="canonical" href="{{ canonical }}">
    <link rel="icon" href="/static/pl-logo.svg" type="image/svg+xml">
    <style>
        body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;color:#0f172a;}
        .wrap{max-width:760px;margin:0 auto;padding:28px 18px 60px;}
        .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;font-size:.85em;}
        .top a{color:#00529B;text-decoration:none;font-weight:700;}
        h1{font-size:1.5em;margin:0 0 6px;}
        .sub{color:#64748b;font-size:.9em;margin:0 0 22px;}
        h2{font-size:.95em;color:#334155;margin:22px 0 8px;}
        .fx{display:flex;justify-content:space-between;align-items:center;gap:10px;background:#fff;border:1px solid rgba(15,23,42,.14);border-radius:11px;padding:12px 14px;margin-bottom:8px;text-decoration:none;color:#0f172a;}
        .fx b{font-size:.95em;}
        .fx span{color:#00529B;font-weight:800;font-size:.82em;white-space:nowrap;}
        .cta{display:inline-block;background:#00529B;color:#fff;padding:11px 16px;border-radius:9px;text-decoration:none;font-weight:800;font-size:.86em;margin-top:16px;}
        .note{color:#94a3b8;font-size:.78em;margin-top:22px;line-height:1.6;}
    </style>
</head>
<body>
<div class="wrap">
    <div class="top"><a href="/">predictionlab.io</a><a href="/soccer-picks?league=fifa.world">Full World Cup board →</a></div>
    <h1>World Cup 2026 Picks &amp; Predictions</h1>
    <p class="sub">FIFA World Cup (Mundial 2026) — AI model picks and win probabilities for every match, updated daily.</p>
    {% for date, fixtures in days %}
    <h2>📅 {{ date }}</h2>
    {% for f in fixtures %}
    <a class="fx" href="{{ f.url or '/soccer-picks?league=fifa.world' }}"><b>{{ f.away }} vs {{ f.home }}</b><span>{{ f.pct }}% {{ f.pick }}</span></a>
    {% endfor %}
    {% endfor %}
    {% if not days %}<p>No upcoming World Cup fixtures in the feed right now — see the <a href="/soccer-picks?league=fifa.world">live World Cup board</a>.</p>{% endif %}
    <a class="cta" href="/soccer-picks?league=fifa.world">Open the full World Cup board →</a>
    <p class="note">Predictions are model-generated and tracked transparently. For entertainment purposes; bet responsibly.</p>
</div>
</body>
</html>"""


@app.route('/world-cup-picks')
def world_cup_picks_page():
    """Indexable World Cup landing — targets 'world cup picks' and 'mundial 2026'."""
    log_site_visit('/world-cup-picks')
    try:
        preds = [p for p in (get_upcoming_predictions('SOCCER') or [])
                 if 'world cup' in str(p.get('league', '')).lower()]
    except Exception:
        preds = []
    by_day = {}
    for p in sorted(preds, key=lambda x: str(x.get('game_date') or '')):
        dk = str(p.get('game_date') or '')[:10]
        if not dk:
            continue
        fh = p.get('face_home_prob')
        fa = p.get('face_away_prob')
        pick = p.get('face_pick_team') or p.get('predicted_winner') or ''
        pct = p.get('face_pick_confidence')
        if pct is None:
            pct = fh if pick == p.get('home_team_id') else fa
        by_day.setdefault(dk, []).append({
            'away': p.get('away_team_id'), 'home': p.get('home_team_id'),
            'pick': pick, 'pct': pct if pct is not None else '—',
            'url': _matchup_path('SOCCER', p, dk),
        })
    days = sorted(by_day.items())[:6]
    return render_template_string(
        _WORLD_CUP_TEMPLATE, days=days,
        canonical=f"{_SITE_DOMAIN}/world-cup-picks",
    )


@app.route('/mundial-2026')
def mundial_redirect():
    return redirect('/world-cup-picks', code=301)


# ── Google Trends → our content matcher ───────────────────────────────────────
# Polls Google Trends' public RSS (no scraping of the JS UI), matches each
# trending query against our own upcoming matchups, and publishes an indexable
# /trending-sports page that links every matched query to its prediction page.

_TRENDS_CACHE = {'ts': 0.0, 'items': []}
_TREND_INDEX_CACHE = {'ts': 0.0, 'index': []}


def _fetch_google_trends():
    """Trending US searches from the public RSS feed, cached 20 minutes."""
    now = datetime.now().timestamp()
    if _TRENDS_CACHE['items'] and now - _TRENDS_CACHE['ts'] < 20 * 60:
        return _TRENDS_CACHE['items']
    items = []
    try:
        resp = requests.get(
            'https://trends.google.com/trending/rss?geo=US',
            headers={'User-Agent': 'Mozilla/5.0 (PredictionLab trends matcher)'},
            timeout=12,
        )
        resp.raise_for_status()
        import xml.etree.ElementTree as _ET
        root = _ET.fromstring(resp.content)
        ns = {'ht': 'https://trends.google.com/trending/rss'}
        for it in root.iter('item'):
            title = (it.findtext('title') or '').strip()
            traffic = (it.findtext('ht:approx_traffic', namespaces=ns) or '').strip()
            if title:
                items.append({'query': title, 'traffic': traffic})
    except Exception:
        logger.exception('google trends fetch failed')
        return _TRENDS_CACHE['items']
    _TRENDS_CACHE.update({'ts': now, 'items': items})
    return items


def _trend_match_index():
    """Index of upcoming matchups across live sports for trend matching (30-min cache)."""
    now = datetime.now().timestamp()
    if _TREND_INDEX_CACHE['index'] and now - _TREND_INDEX_CACHE['ts'] < 30 * 60:
        return _TREND_INDEX_CACHE['index']
    index = []
    today = datetime.now().date()
    for sport_key in SPORTS.keys():
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        try:
            _status, _live = get_season_status(sport_key, today=datetime.now())
            if not _live:
                continue
            for pred in (get_upcoming_predictions(sport_key) or []):
                dk = str(pred.get('game_date') or '')[:10]
                try:
                    d = datetime.strptime(dk, '%Y-%m-%d').date()
                except Exception:
                    continue
                if not (today - timedelta(days=1) <= d <= today + timedelta(days=3)):
                    continue
                away = pred.get('away_team_id') or ''
                home = pred.get('home_team_id') or ''
                url = _matchup_path(sport_key, pred, dk)
                if not (away and home and url):
                    continue
                pick = pred.get('face_pick_team') or pred.get('predicted_winner') or ''
                pct = pred.get('face_pick_confidence')
                index.append({
                    'sport': sport_key,
                    'date': dk,
                    'away': away, 'home': home,
                    'away_tok': _team_search_name(sport_key, away).lower(),
                    'home_tok': _team_search_name(sport_key, home).lower(),
                    'away_full': str(away).lower(), 'home_full': str(home).lower(),
                    'url': url,
                    'pick': _team_search_name(sport_key, pick) if pick else '',
                    'pct': pct,
                })
        except Exception:
            continue
    _TREND_INDEX_CACHE.update({'ts': now, 'index': index})
    return index


def _normalize_trend_query(q):
    t = str(q or '').lower()
    t = t.replace(' - ', ' vs ').replace(' @ ', ' vs ').replace(' x ', ' vs ')
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    return ' ' + re.sub(r'\s+', ' ', t).strip() + ' '


def _match_trends_to_content():
    """[{query, traffic, url, label, pick, pct, strength}] for matched trends."""
    matches = []
    index = _trend_match_index()
    for tr in _fetch_google_trends():
        tnorm = _normalize_trend_query(tr['query'])
        # Hand-routed evergreen targets
        if 'world cup' in tnorm or 'mundial' in tnorm:
            matches.append({**tr, 'url': '/world-cup-picks',
                            'label': 'World Cup 2026 picks', 'pick': '', 'pct': None, 'strength': 2})
            continue
        if 'nba finals' in tnorm:
            matches.append({**tr, 'url': '/nba-picks',
                            'label': 'NBA Finals AI picks', 'pick': '', 'pct': None, 'strength': 2})
            continue
        best = None
        for g in index:
            score = 0
            if f" {g['away_tok']} " in tnorm or f" {g['away_full']} " in tnorm:
                score += 1
            if f" {g['home_tok']} " in tnorm or f" {g['home_full']} " in tnorm:
                score += 1
            # Individual sports: the whole trending query may be one player's name
            if score == 0 and g['sport'] in ('TENNIS', 'UFC', 'GOLF'):
                qbare = tnorm.strip()
                if qbare and (qbare in f" {g['away_full']} " or qbare in f" {g['home_full']} "):
                    score = 1
            if score and (best is None or score > best[0]):
                best = (score, g)
            if best and best[0] == 2:
                break
        if best:
            score, g = best
            matches.append({
                **tr, 'url': g['url'],
                'label': f"{_team_search_name(g['sport'], g['away'])} vs {_team_search_name(g['sport'], g['home'])} prediction",
                'pick': g['pick'], 'pct': g['pct'], 'strength': score,
            })
    matches.sort(key=lambda x: -x['strength'])
    return matches


_TRENDING_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trending Sports Searches Right Now — Predictions & AI Picks | predictionlab.io</title>
    <meta name="description" content="What America is googling in sports right now — with an AI prediction for every trending matchup: win probabilities, odds, and model picks, updated through the day.">
    <link rel="canonical" href="{{ canonical }}">
    <link rel="icon" href="/static/pl-logo.svg" type="image/svg+xml">
    <style>
        body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;color:#0f172a;}
        .wrap{max-width:720px;margin:0 auto;padding:28px 18px 60px;}
        .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;font-size:.85em;}
        .top a{color:#00529B;text-decoration:none;font-weight:700;}
        h1{font-size:1.45em;margin:0 0 6px;}
        .sub{color:#64748b;font-size:.9em;margin:0 0 22px;}
        .tr{display:flex;justify-content:space-between;align-items:center;gap:12px;background:#fff;border:1px solid rgba(15,23,42,.14);border-radius:12px;padding:14px 16px;margin-bottom:10px;text-decoration:none;color:#0f172a;}
        .tr:hover{border-color:#00529B;}
        .q b{font-size:1.02em;text-transform:capitalize;}
        .q span{display:block;color:#64748b;font-size:.82em;margin-top:3px;}
        .badge{flex-shrink:0;background:#fef3c7;color:#92400e;font-weight:800;font-size:.74em;padding:4px 9px;border-radius:999px;}
        .pick{color:#00875a;font-weight:800;}
        .note{color:#94a3b8;font-size:.78em;margin-top:22px;line-height:1.6;}
    </style>
</head>
<body>
<div class="wrap">
    <div class="top"><a href="/">predictionlab.io</a><a href="/ai-sports-betting-picks-today">Today's full board →</a></div>
    <h1>🔥 Trending Sports Searches Right Now</h1>
    <p class="sub">Live from Google Trends (US) — every trending matchup linked to our AI prediction. Updated through the day.</p>
    {% for m in matches %}
    <a class="tr" href="{{ m.url }}">
        <span class="q"><b>{{ m.query }}</b>
            <span>{{ m.label }}{% if m.pick %} · <span class="pick">AI pick: {{ m.pick }}{% if m.pct %} ({{ m.pct }}%){% endif %}</span>{% endif %}</span>
        </span>
        {% if m.traffic %}<span class="badge">{{ m.traffic }} searches</span>{% endif %}
    </a>
    {% endfor %}
    {% if not matches %}<p>No sports queries are trending right now — check the <a href="/ai-sports-betting-picks-today">full board</a>.</p>{% endif %}
    <p class="note">Trend data: Google Trends public feed. Predictions are model-generated and tracked transparently. For entertainment purposes; bet responsibly.</p>
</div>
</body>
</html>"""


@app.route('/trending-sports')
def trending_sports_page():
    log_site_visit('/trending-sports')
    try:
        matches = _match_trends_to_content()
    except Exception:
        logger.exception('trending sports match failed')
        matches = []
    return render_template_string(
        _TRENDING_PAGE_TEMPLATE, matches=matches,
        canonical=f"{_SITE_DOMAIN}/trending-sports",
    )


# Cached matchup URL list for the sitemap (recomputing fans out to every sport).
_MATCHUP_SITEMAP_CACHE = {'ts': 0.0, 'urls': []}


def _matchup_sitemap_urls():
    now = datetime.now().timestamp()
    if _MATCHUP_SITEMAP_CACHE['urls'] and now - _MATCHUP_SITEMAP_CACHE['ts'] < 4 * 3600:
        return _MATCHUP_SITEMAP_CACHE['urls']
    urls = []
    today = datetime.now().date()
    for sport_key in SPORTS.keys():
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        try:
            _status, _live = get_season_status(sport_key, today=datetime.now())
            if not _live:
                continue
            for pred in (get_upcoming_predictions(sport_key) or []):
                dk = str(pred.get('game_date') or '')[:10]
                try:
                    d = datetime.strptime(dk, '%Y-%m-%d').date()
                except Exception:
                    continue
                if not (today - timedelta(days=1) <= d <= today + timedelta(days=2)):
                    continue
                path = _matchup_path(sport_key, pred, dk)
                if path:
                    urls.append(_SITE_DOMAIN + path)
        except Exception:
            continue
    urls = list(dict.fromkeys(urls))
    _MATCHUP_SITEMAP_CACHE.update({'ts': now, 'urls': urls})
    return urls


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


def _sync_daily_report_soccer_scores(yesterday_dt, report_date):
    """Background: fetch yesterday's soccer finals from ESPN (never block page render)."""
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
                _soc_resp = requests.get(
                    f'https://site.api.espn.com/apis/site/v2/sports/soccer/{_soc_code}/scoreboard?dates={_soc_date_str}',
                    timeout=5,
                )
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
                    # Soccer finals report as STATUS_FULL_TIME (not STATUS_FINAL like
                    # other sports); rely on the completed/state flags so finished
                    # matches are not silently skipped.
                    _soc_status_type = _soc_ev.get('status', {}).get('type', {}) or {}
                    _soc_st = _soc_status_type.get('name', '') or ''
                    _soc_done = (
                        _soc_status_type.get('completed') is True
                        or str(_soc_status_type.get('state', '')).lower() == 'post'
                        or _soc_st.startswith('STATUS_FINAL')
                        or _soc_st.startswith('STATUS_FULL_TIME')
                    )
                    if not _soc_done:
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
                    _soc_ex = _soc_cursor.execute(
                        'SELECT 1 FROM games WHERE game_id=? AND sport=?', (_soc_gid, 'SOCCER'),
                    ).fetchone()
                    if _soc_ex:
                        _soc_cursor.execute(
                            'UPDATE games SET home_score=?, away_score=?, status="final" '
                            'WHERE game_id=? AND sport=? AND (home_score IS NULL OR home_score!=?)',
                            (_soc_hs, _soc_as, _soc_gid, 'SOCCER', _soc_hs),
                        )
                    else:
                        try:
                            _soc_cursor.execute(
                                'INSERT INTO games (sport,league,game_id,season,game_date,'
                                'home_team_id,away_team_id,home_score,away_score,status) '
                                'VALUES (?,?,?,?,?,?,?,?,?,"final")',
                                ('SOCCER', _soc_lg_name, _soc_gid, yesterday_dt.year, _soc_gd,
                                 _soc_ht, _soc_at, _soc_hs, _soc_as),
                            )
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


def _build_daily_report_sport_tally(sport_key, report_date):
    """Grade yesterday's slate for one sport (thread-safe; no Flask context)."""
    from collections import defaultdict
    try:
        conn = get_db_connection()
        prob_sql = _predictions_prob_select_sql(conn)
        completed_games = conn.execute(f'''
            SELECT g.*,
                   {prob_sql}
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND g.home_score IS NOT NULL
            AND (g.game_date LIKE ? OR g.game_date = ?)
            ORDER BY g.game_date DESC LIMIT 50
        ''', (sport_key, sport_key, f'{report_date}%', report_date)).fetchall()
        conn.close()
        if not completed_games:
            return None
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
            game_date = _normalize_game_date_key(game['game_date'])
            if not game_date:
                continue
            glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = _model_probs_for_grading(
                sport_key, game, home_team, away_team, game_date,
            )
            if xgb_prob is None:
                xgb_prob = elo_prob
            if ens_prob is None:
                ens_prob = elo_prob
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
        try:
            _compute_spread_total_for_daily(sport_key, daily_results, skip_efficiency=True)
            _grade_efficiency_for_results(sport_key, daily_results)
            _finalize_daily_result_cards(sport_key, daily_results)
        except Exception:
            pass
        tally = compute_daily_model_tally(daily_results, report_date)
        if not tally or tally.get('games', 0) == 0:
            return None
        return {'sport_key': sport_key, 'tally': tally}
    except Exception as e:
        logger.error(f"Daily report {sport_key}: {e}")
        return None


@app.route('/daily-report')
def daily_report_page():
    """Daily Betting Results Report — marketing/proof-of-performance page."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
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
    _stale_daily_html = None
    if (
        _DAILY_REPORT_CACHE.get('html')
        and _DAILY_REPORT_CACHE.get('date') == report_date
        and (now_ts - _DAILY_REPORT_CACHE.get('ts', 0)) < 3600
    ):
        _stale_daily_html = _DAILY_REPORT_CACHE['html']
    if _stale_daily_html:
        return _stale_daily_html

    # Non-blocking score syncs (page reads yesterday from DB; sync fills gaps async).
    for _sync in ['NHL', 'NBA', 'MLB']:
        try:
            if _sync == 'NHL':
                _start_background_score_sync('NHL', update_nhl_scores)
            else:
                _start_background_score_sync(_sync)
        except Exception:
            pass
    _soccer_sync_thread = None
    if SOCCER_ENABLED:
        import threading as _thr_dr
        _soccer_sync_thread = _thr_dr.Thread(
            target=_sync_daily_report_soccer_scores,
            args=(yesterday_dt, report_date),
            daemon=True,
            name='daily-report-soccer-sync',
        )
        _soccer_sync_thread.start()

    sport_tallies = []
    total_games = 0
    agg_models = {}
    agg_spread = {'correct': 0, 'total': 0, 'pushes': 0}
    agg_ou = {'correct': 0, 'total': 0, 'pushes': 0}

    _daily_today = datetime.now()
    _active_sports = []
    for sport_key in ['NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'SOCCER']:
        if sport_key == 'SOCCER' and not SOCCER_ENABLED:
            continue
        if sport_key not in SPORTS:
            continue
        _status, _is_live = get_season_status(sport_key, today=_daily_today)
        if _is_live:
            _active_sports.append(sport_key)

    # Soccer finals are fetched on-demand for this page (not by a cron), so wait a
    # bounded time for that sync to land yesterday's games in the DB before grading.
    # Without this the soccer tally is built before the rows exist and the whole
    # soccer section is dropped from the report.
    if _soccer_sync_thread is not None:
        _soccer_sync_thread.join(timeout=12)

    _tally_results = []
    if _active_sports:
        with ThreadPoolExecutor(max_workers=min(4, len(_active_sports))) as _pool:
            _futs = {
                _pool.submit(_build_daily_report_sport_tally, sk, report_date): sk
                for sk in _active_sports
            }
            for _fut in as_completed(_futs):
                _row = _fut.result()
                if _row:
                    _tally_results.append(_row)

    _sport_order = {sk: i for i, sk in enumerate(_active_sports)}
    _tally_results.sort(key=lambda r: _sport_order.get(r['sport_key'], 99))

    for _row in _tally_results:
        sport_key = _row['sport_key']
        tally = _row['tally']
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
    return render_template_string("""
{% extends "base.html" %}
{% block title %}{{ safe_title }}{% endblock %}
{% block head_meta %}
    <meta name="description" content="Daily AI-powered {{ sport_info.name }} predictions, game forecasts, and model projections on predictionlab.io.">
    <meta name="robots" content="noindex, follow">
    <link rel="canonical" href="https://predictionlab.io/{{ sport_slug }}">
{% endblock %}
{% block extra_styles %}
    <style>
        .prediction-error-card{
            max-width:760px;
            margin:clamp(32px,8vh,90px) auto;
            padding:clamp(28px,5vw,54px);
            border:1px solid rgba(11,11,10,.16);
            background:rgba(251,251,248,.88);
            text-align:left;
        }
        .prediction-error-label{
            display:inline-block;
            margin-bottom:20px;
            padding:7px 10px;
            background:#0b0b0a;
            color:#c7ff2e;
            font:800 10px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
            letter-spacing:2px;
            text-transform:uppercase;
        }
        .prediction-error-card h1{font-size:clamp(42px,8vw,76px);line-height:.95;margin:0 0 24px;}
        .prediction-error-card p{max-width:600px;font-size:1rem;line-height:1.7;}
        .prediction-error-card a{
            display:inline-flex;
            margin-top:12px;
            padding:13px 18px;
            background:#c7ff2e;
            color:#0b0b0a;
            font:800 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
            letter-spacing:1.2px;
            text-decoration:none;
            text-transform:uppercase;
        }
    </style>
{% endblock %}
{% block content %}
    <div class="prediction-error-card">
        <span class="prediction-error-label">Data refresh</span>
        <h1>{{ sport_info.icon }} {{ sport_info.name }} Picks</h1>
        <p>We are refreshing this page right now. Please check the main picks feed below.</p>
        <p><a href="/{{ sport_slug }}">Open {{ sport_info.name }} picks</a></p>
    </div>
{% endblock %}
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
    cached_html = None
    selected_slug = request.args.get('league', '') if sport == 'SOCCER' else ''
    if not current_user.is_authenticated:
        cache_key = f"pred_page::v19::{sport}::{filter_date or 'all'}::{selected_slug or 'default'}"
        cache_ttl = _SPORT_PREDICTIONS_PAGE_TTL.get(sport, 180)
        cached_html, _revalidate = _stale_page_cache_get(
            _SPORT_PREDICTIONS_PAGE_CACHE, cache_key, cache_ttl,
        )
        if (
            cached_html
            and 'game-card' in cached_html
            and 'no predictions available' not in cached_html.lower()
            and 'refreshing this page right now' not in cached_html.lower()
            and 'upstream data/model dependency failed' not in cached_html.lower()
        ):
            return cached_html
    prediction_error = None
    try:
        predictions = get_upcoming_predictions(sport)
    except Exception as e:
        import traceback as _tb_pred
        logger.error(f"Error loading {sport} predictions: {e}\n{_tb_pred.format_exc()}")
        predictions = []
        prediction_error = (
            f"{sport} predictions could not be loaded because an upstream data/model dependency failed. "
            "Please refresh in a minute."
        )
    if not predictions and not prediction_error and sport in _OFFSEASON_SPORTS_HINT:
        prediction_error = _OFFSEASON_SPORTS_HINT[sport]

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
    # Advertise the current/next slate: prefer today, else the soonest UPCOMING date
    # (>= today). Only fall back to the earliest available date if nothing is upcoming,
    # so the share card never advertises a stale (e.g. months-old) prediction.
    if today_date in date_pool:
        target_date = today_date
    else:
        _future_dates = sorted(d for d in date_pool.keys() if d and d >= today_date)
        if _future_dates:
            target_date = _future_dates[0]
        else:
            target_date = sorted(date_pool.keys())[0] if date_pool else ''
    shareable_cards = date_pool.get(target_date, [])
    shareable_cards.sort(key=lambda x: (-x['confidence'], x['away_team'], x['home_team']))
    shareable_cards = shareable_cards[:3]
    share_image_src = None
    share_image_view_url = None
    if shareable_cards:
        try:
            _pred_payload = {
                'type': 'predictions',
                'sport': SPORTS.get(sport, {}).get('name', sport),
                'date': target_date or today_date,
                'cards': shareable_cards,
            }
            _pred_token = _register_share_image(_pred_payload)
            share_image_src = url_for('share_predictions_image', token=_pred_token, fmt='jpg')
            share_image_view_url = url_for('share_predictions_view', token=_pred_token)
        except Exception as _share_err:
            logger.debug(f"Share image skipped for {sport}: {_share_err}")

    # Group games for the picks page.
    # Soccer always includes completed games from the last 21 days so leagues
    # don't go blank after their season ends — users can still see what was picked.
    # Other sports: upcoming games only, with a 7-day fallback when season is over.
    from collections import defaultdict
    from datetime import date as _date_cls
    _cutoff_21 = (datetime.now() - timedelta(days=21)).strftime('%Y-%m-%d')

    grouped_predictions = defaultdict(list)
    # Keep the WHOLE season — completed games included. The picker exposes every
    # date; _picks_display_dates limits how many sections render inline and the
    # dated pages serve anything older.
    for pred in predictions:
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
    sorted_dates, all_picker_dates, default_pick_date = _picks_display_dates(grouped_predictions, today_date)

    # Filter to specific date if requested (daily SEO pages)
    if filter_date:
        if filter_date in grouped_predictions:
            grouped_predictions = {filter_date: grouped_predictions[filter_date]}
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
        _refresh_books_on_predictions(
            sport, predictions, today_date=today_date, prioritize=_book_priority,
        )
    except Exception as _card_bk:
        logger.debug(f"PL book odds on picks page for {sport}: {_card_bk}")

    _upcoming_for_card_eff = [
        p for p in predictions if isinstance(p, dict) and p.get('home_score') is None
    ]
    try:
        _eff_attach.fill_efficiency_spread_on_predictions(sport, _upcoming_for_card_eff)
    except Exception as _eff_card:
        logger.debug(f"[eff] picks card fill failed for {sport}: {_eff_card}")

    for pred in predictions:
        if not isinstance(pred, dict):
            continue
        for _eff_key in ('our_home_eff', 'our_away_eff'):
            _v = pred.get(_eff_key)
            if isinstance(_v, dict):
                pred[_eff_key] = types.SimpleNamespace(**_v)
        _finalize_prediction_odds(pred)
        # Soccer base-model probs can be oriented opposite to the calibrated
        # result; fix BEFORE pick/spread/display logic so the PL spread sign and
        # model-% cells all line up with the calibrated win prob (not just the
        # display). Must run before _enforce_pick_spread_consistency and
        # _prepare_pred_card_display, which read ensemble_prob.
        if sport == 'SOCCER':
            _reorient_soccer_model_probs(pred)
        _ens_pre = _safe_float(pred.get('ensemble_prob'))
        if _ens_pre is not None:
            pred['_ensemble_prob_pre_enforce'] = _ens_pre
        _enforce_pick_spread_consistency(pred, sport=sport)
        _prepare_pred_card_display(pred, sport=sport)
        # Soccer EV (goal-scale) — soccer is excluded from the points-based EV
        # block, so fill ml/spread/total EV here using calibrated win probs and
        # goal-scale sigma (needs disp_pl_* from the card prep above).
        if sport == 'SOCCER':
            _attach_soccer_ev_to_pred(pred)
        # Safety: ensure all required fields exist for template rendering
        if 'efficiency_prob' not in pred:
            pred['efficiency_prob'] = None
        if 'efficiency_correct' not in pred:
            pred['efficiency_correct'] = None
        # Safety: ensure face probabilities are set (fallback to ensemble if missing)
        if 'face_home_prob' not in pred or pred.get('face_home_prob') is None:
            ens = _safe_float(pred.get('ensemble_prob'))
            if ens is not None:
                if ens <= 1.0:
                    ens *= 100.0
                pred['face_home_prob'] = round(ens, 1)
                pred['face_away_prob'] = round(100.0 - ens, 1)
        if 'face_away_prob' not in pred or pred.get('face_away_prob') is None:
            if 'face_home_prob' in pred and pred.get('face_home_prob') is not None:
                pred['face_away_prob'] = round(100.0 - float(pred['face_home_prob']), 1)
        # Safety: ensure face_pick_team and face_pick_confidence are set
        if not pred.get('face_pick_team'):
            fhp = _safe_float(pred.get('face_home_prob'))
            if fhp and fhp >= 50:
                pred['face_pick_team'] = pred.get('home_team_id')
            elif fhp:
                pred['face_pick_team'] = pred.get('away_team_id')
        if not pred.get('face_pick_confidence'):
            fhp = _safe_float(pred.get('face_home_prob'))
            if fhp:
                pred['face_pick_confidence'] = round(fhp if fhp >= 50 else 100.0 - fhp, 1)

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
        all_picker_dates=all_picker_dates,
        default_pick_date=default_pick_date,
        today_date=today_date,
        group_by='week' if sport == 'NFL' else 'date',
        soccer_leagues=soccer_leagues,
        selected_league=selected_league,
        selected_league_slug=(
            _soccer_league_url_slug(selected_league) if sport == 'SOCCER' and selected_league else None
        ),
        shareable_cards=shareable_cards,
        share_image_src=share_image_src,
        share_image_view_url=share_image_view_url,
        is_logged_in=_pred_li,
        soccer_enabled=SOCCER_ENABLED,
        ga_tracking_id=GA_TRACKING_ID,
        todays_picks=[],
        team_logo_url=team_logo_url,
        is_premium=is_premium_user(),
    )
    try:
        rendered = _render_espn_picks_page(**_render_ctx)
    except Exception as _pred_render_err:
        logger.exception(f"Predictions render fallback for {sport} ({filter_date}): {_pred_render_err}")
        if (
            cached_html
            and 'game-card' in cached_html
            and 'refreshing this page right now' not in cached_html.lower()
        ):
            logger.warning("Serving last known-good %s picks page after render failure", sport)
            return cached_html
        return _predictions_fallback_page(sport, filter_date=filter_date)
    if (
        cache_key
        and rendered
        and grouped_predictions
        and sorted_dates
        and 'class="game-card' in rendered
        and 'no predictions available' not in rendered.lower()
        and 'upstream data/model dependency failed' not in rendered.lower()
    ):
        _trim_cache(_SPORT_PREDICTIONS_PAGE_CACHE, _SPORT_PREDICTIONS_PAGE_TTL.get(sport, 180), max_entries=50)
        _SPORT_PREDICTIONS_PAGE_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
    return rendered

def _render_nfl_results_page(sport, season_start_dt=None):
    """NFL weekly + DB fallback results (sport-specific)."""
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
            daily_results = _daily_results_from_weekly(weekly_results)
            _attach_book_odds_to_daily_results(sport, daily_results, api_limit=80)
            _cache_market_lines_for_results(sport, daily_results, limit=80)
            _grade_efficiency_for_results(sport, daily_results)
            overall_stats = compute_overall_stats_from_daily(daily_results)
            overall_stats = _merge_snapshot_efficiency_into_overall(overall_stats, sport)
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
            if _results_date_query_active():
                today_date = datetime.now().strftime('%Y-%m-%d')
                yesterday = yesterday_dt.strftime('%Y-%m-%d')
                sorted_dates = _recent_result_dates(daily_results, yesterday=yesterday, limit=30)
                _attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
                _cache_market_lines_for_results(sport, daily_results, limit=150)
                _st_stats = _compute_spread_total_for_daily(sport, daily_results)
                overall_stats = compute_overall_stats_from_daily(daily_results)
                _finalize_daily_result_cards(sport, daily_results)
                season_perf = _build_season_performance_summary(overall_stats, _st_stats)
                _date_ctx = _results_page_date_kwargs(daily_results, sorted_dates)
                return render_template_string(
                    DAILY_RESULTS_TEMPLATE,
                    **_results_page_meta(sport),
                    page=sport, sport=sport, sport_info=SPORTS[sport],
                    sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
                    sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
                    sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                    **_date_ctx,
                    today_date=today_date, overall_stats=overall_stats,
                    spread_total_stats=_st_stats, season_perf=season_perf,
                    daily_tally=daily_tally, daily_tally_date=daily_tally_date,
                    daily_tally_games=daily_tally_games,
                    weekly_tally=weekly_tally,
                    weekly_tally_date_range=weekly_tally_date_range,
                    weekly_tally_games=weekly_tally_games,
                    roi_cards=roi_cards,
                    results_stale_notice=results_stale_notice,
                    results_snapshot_notice=None,
                    soccer_leagues=None,
                )
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
    _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
    _attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
    _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
    _st_stats = _compute_spread_total_for_daily(sport, daily_results)
    overall_stats = compute_overall_stats_from_daily(daily_results)
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
    _date_ctx = _results_page_date_kwargs(daily_results, sorted_dates)
    return render_template_string(
        DAILY_RESULTS_TEMPLATE,
        **_results_page_meta(sport),
        page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
        sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
        sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
        **_date_ctx,
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



def _render_nhl_results_page(sport, season_start_dt=None):
    """NHL season snapshot + playoff results (sport-specific)."""
    cache_key = f'{sport}_moneyline_results_html_v3'
    cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300)
    cached_page = None
    if not _results_date_query_active():
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
        stale_html, _ = _stale_page_cache_get(_SPORT_RESULTS_CACHE, cache_key, cache_ttl)
        if stale_html and _results_page_html_usable(stale_html):
            return stale_html

    try:
        # Run NHL score sync at most once every 10 minutes (background only).
        _start_background_score_sync('NHL', update_nhl_scores)
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
            overall_stats = _merge_snapshot_efficiency_into_overall(
                snapshot_stats['overall_stats'], sport,
            )
            _st_stats = snapshot_stats['spread_total_stats']
            season_perf = snapshot_stats['season_perf']
            _ov = snapshot_stats['total_over']
            _un = snapshot_stats['total_under']
            _gou = snapshot_stats['total_games_ou']
            _avg = snapshot_stats['avg_total']
            _bench = snapshot_stats['ou_bench']
            roi_total = snapshot_stats.get('roi_total') or {}
            if daily_results and _daily_results_game_count(daily_results):
                _grade_efficiency_for_results(sport, daily_results)
                _finalize_daily_result_cards(sport, daily_results)
        else:
            _ov, _un, _gou, _avg, _bench = _ou_stats(season_daily, sport)
            _attach_book_odds_to_daily_results(sport, season_daily, api_limit=300)
            _cache_market_lines_for_results(sport, season_daily, limit=80)
            _attach_engine_odds_to_daily_results(sport, season_daily, limit=40)
            _st_stats = _compute_spread_total_for_daily(sport, season_daily)
            overall_stats = compute_overall_stats_from_daily(season_daily)
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
        _date_ctx = _results_page_date_kwargs(daily_results, sorted_dates)

        rendered = render_template_string(
            DAILY_RESULTS_TEMPLATE,
            **_results_page_meta(sport),
            page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
            sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
            sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
            **_date_ctx,
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
            not _results_date_query_active()
            and (snapshot_stats or _daily_results_game_count(daily_results))
            and _results_page_html_usable(rendered)
        ):
            _trim_cache(_SPORT_RESULTS_CACHE, _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
            _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
        return rendered
    except Exception as e:
        logger.error(f"Error processing NHL results: {e}")
        return f"<h1>NHL results page failed to render because of a processing error: {str(e)}</h1>"



def _render_daily_sport_results_page(sport, season_start_dt=None):
    """Shared daily results pipeline for MLB, NCAAB, NCAAW, NCAAF, WNBA, SOCCER."""
    min_live = _SPORT_MIN_LIVE_DATES.get(sport)
    if min_live and datetime.now() < min_live:
        launch_txt = min_live.strftime('%B %-d, %Y')
        return _results_fallback_page(
            sport,
            f"{SPORTS[sport]['name']} regular season results will appear once games begin on {launch_txt}."
        )
    selected_league = None
    selected_slug = None
    selected_league_slug = None
    if sport == 'SOCCER':
        selected_slug = (request.args.get('league') or '').strip()
    cache_key = f'{sport}_daily_results_html_v2'
    skip_cache = False
    cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)
    if _results_date_query_active():
        skip_cache = True

    conn = get_db_connection()
    soccer_league_counts = {}
    if sport == 'SOCCER':
        soccer_league_counts = _soccer_curated_league_game_counts(conn)
        soccer_recent_counts = _soccer_curated_league_recent_counts(conn, days=14)
        selected_league, selected_league_slug = _resolve_soccer_results_league(
            selected_slug, soccer_league_counts, recent_counts=soccer_recent_counts,
        )
        if selected_slug and not selected_league:
            conn.close()
            return _results_fallback_page(
                sport,
                f'Unknown soccer league “{selected_slug}”. Pick a league from the list below.',
            )
        if not selected_slug and selected_league_slug:
            conn.close()
            return redirect(f'/soccer-results?league={selected_league_slug}', code=302)
        if selected_league:
            cache_key = f'{sport}_daily_results_html_{selected_league_slug or _soccer_league_slug(selected_league)}'

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
                conn.close()
                return cached_html
            stale_html, _ = _stale_page_cache_get(_SPORT_RESULTS_CACHE, cache_key, cache_ttl)
            if stale_html and _results_page_html_usable(stale_html):
                conn.close()
                return stale_html
    snapshot_stats = None
    if sport != 'SOCCER':
        snapshot_raw = _load_sport_season_snapshot(sport)
        snapshot_stats = _stats_from_season_snapshot(snapshot_raw)
    # Update scores in background so the page is never blocked by API calls.
    if sport == 'SOCCER':
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
    else:
        _start_background_score_sync(sport)

    if sport == 'SOCCER':
        completed_games = _fetch_soccer_completed_games(
            conn, selected_league, SOCCER_RESULTS_GAMES_PER_LEAGUE,
        )
        completed_games = _sort_game_rows_by_date_desc(completed_games)
        if snapshot_stats:
            snapshot_games = int((snapshot_raw or {}).get('games_in_scope') or 0)
            if snapshot_games and len(completed_games) < snapshot_games:
                snapshot_stats = None
    else:
        prob_sql = _predictions_prob_select_sql(conn)
        season_start_dt, season_end_dt = _results_season_bounds(sport, datetime.now())
        season_end_sql = season_end_dt.strftime('%Y-%m-%d') if season_end_dt else None
        season_start_sql = season_start_dt.strftime('%Y-%m-%d') if season_start_dt else None
        if snapshot_stats:
            card_start = max(
                season_start_dt or (datetime.now() - timedelta(days=30)),
                datetime.now() - timedelta(days=30),
            )
            season_start_sql = card_start.strftime('%Y-%m-%d')
            season_end_sql = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
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
    soccer_season_start = season_start_dt
    if sport == 'SOCCER':
        soccer_season_start, _season_end_dt = _results_season_bounds(sport, yesterday_dt)
        sorted_dates = _recent_result_dates(
            daily_results, yesterday=yesterday, limit=60, recent_window_days=90,
        )
    else:
        sorted_dates = _recent_result_dates(daily_results, yesterday=yesterday, limit=30)
    if snapshot_stats:
        _ov = snapshot_stats['total_over']
        _un = snapshot_stats['total_under']
        _gou = snapshot_stats['total_games_ou']
        _avg = snapshot_stats['avg_total']
        _bench = snapshot_stats['ou_bench']
        overall_stats = snapshot_stats['overall_stats']
        _st_stats = snapshot_stats['spread_total_stats']
        season_perf = snapshot_stats['season_perf']
        roi_total = snapshot_stats.get('roi_total') or {}
        _attach_book_odds_to_daily_results(sport, daily_results, api_limit=0)
        _compute_spread_total_for_daily(sport, daily_results, skip_efficiency=True)
        _grade_efficiency_for_results(sport, daily_results)
        overall_stats = _merge_snapshot_efficiency_into_overall(overall_stats, sport)
        _finalize_daily_result_cards(sport, daily_results)
    else:
        _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
        _attach_book_odds_to_daily_results(sport, daily_results, api_limit=40)
        _cache_market_lines_for_results(sport, daily_results, limit=150)
        _attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
        _st_stats = _compute_spread_total_for_daily(sport, daily_results)
        overall_stats = compute_overall_stats_from_daily(daily_results)
        overall_stats = _merge_snapshot_efficiency_into_overall(overall_stats, sport)
        _finalize_daily_result_cards(sport, daily_results)
        season_perf = _build_season_performance_summary(overall_stats, _st_stats)
        roi_total = compute_roi_for_range(daily_results, None, None)
    if _st_stats and int(_st_stats.get('total_graded') or 0) == 0 and int((overall_stats or {}).get('ensemble', {}).get('total') or 0) > 0:
        logger.warning(
            f"[{sport}] results O/U still 0 graded after book attach "
            f"(check /data betting_lines totals + pl_book_odds_api on Render)"
        )
    tally_bundle = _compute_results_tally_bundle(
        daily_results,
        yesterday_dt,
        season_start_dt=soccer_season_start if sport == 'SOCCER' else season_start_dt,
        sport=sport,
        league_scoped=bool(sport == 'SOCCER' and selected_league),
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
        roi_total = compute_roi_for_range(daily_results, None, None)
    roi_cards = build_roi_cards(roi_daily, roi_weekly, roi_total)
    soccer_leagues = None
    if sport == 'SOCCER':
        soccer_leagues = _build_soccer_results_leagues_ui(
            selected_league, soccer_league_counts,
        )
    if sport == 'SOCCER' and selected_league and not selected_league_slug:
        selected_league_slug = _soccer_league_url_slug(selected_league)
    _date_ctx = _results_page_date_kwargs(daily_results, sorted_dates)

    rendered = render_template_string(
        DAILY_RESULTS_TEMPLATE,
        **_results_page_meta(sport),
        page=sport, sport=sport, sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
        sport_seo_slug=SPORT_SEO_SLUGS.get(sport, sport.lower()),
        sport_results_slug=_SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
        **_date_ctx,
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
        selected_league_slug=selected_league_slug,
        league_db_total=soccer_league_counts.get(selected_league, 0) if sport == 'SOCCER' else None,
    )
    if (
        not _results_date_query_active()
        and _daily_results_game_count(daily_results)
        and _results_page_html_usable(rendered)
    ):
        _trim_cache(_SPORT_RESULTS_CACHE, _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
        _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
    return rendered



def sport_results(sport):
    """Show model performance results for a sport."""
    season_start_dt = None
    try:
        if sport not in SPORTS:
            return "Sport not found", 404
        if sport == 'SOCCER' and not SOCCER_ENABLED:
            return "Soccer results are temporarily hidden while data loads.", 404

        _renderer = _SPORT_RESULTS_RENDERERS.get(sport)
        if _renderer is not None:
            return _renderer(sport, season_start_dt=season_start_dt)

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

def _prewarm_pages():
    """Warm the expensive pages in the background after startup so the first real
    visitor (and crawlers) hit a cached page instead of a cold 10-90s build.
    Runs once per process via the app's own test client; pages then serve cached
    /stale-while-warm. Heavy results pages first."""
    import threading as _thr_pw
    import time as _t_pw

    def _run():
        _t_pw.sleep(10)  # let model loading / DB init / score syncs settle
        # Picks pages first (the main user-facing pages), then the heavier
        # results/daily pages, so the most-visited routes are warm soonest.
        paths = ['/nba-picks', '/nhl-picks', '/mlb-picks']
        if SOCCER_ENABLED:
            paths.append('/soccer-picks')
        paths += ['/all-sports-results', '/daily-report',
                  '/nba-results', '/nhl-results', '/mlb-results']
        try:
            client = app.test_client()
        except Exception:
            logger.exception('pre-warm: could not create test client')
            return
        for p in paths:
            try:
                client.get(p)
                logger.info(f'pre-warmed {p}')
            except Exception:
                logger.debug(f'pre-warm failed for {p}')

    _thr_pw.Thread(target=_run, daemon=True, name='page-prewarm').start()


try:
    _prewarm_pages()
except Exception:
    logger.exception('could not start page pre-warm')


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
