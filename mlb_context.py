"""
MLB contextual adjustment layer — totals-focused, ensemble-preserving.

Small deltas and confidence modifiers for the existing MLB decision layer in
NHL77FINAL.py. Does not replace Glicko-2 / TrueSkill / Elo / XGBoost / Ensemble.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MLB_PARK_RUN_FACTOR: Dict[str, float] = {
    'Colorado Rockies': 1.12,
    'Boston Red Sox': 1.04,
    'Cincinnati Reds': 1.03,
    'Chicago Cubs': 1.02,
    'Baltimore Orioles': 1.02,
    'Arizona Diamondbacks': 1.01,
    'San Francisco Giants': 0.96,
    'San Diego Padres': 0.97,
    'Oakland Athletics': 0.97,
    'Athletics': 0.97,
    'Miami Marlins': 0.97,
    'Seattle Mariners': 0.98,
}

MLB_ALTITUDE_BOOST: Dict[str, float] = {'Colorado Rockies': 0.35}

# Park total points (aligned with NHL77FINAL._MLB_PARK_FACTORS for grading)
MLB_PARK_POINTS: Dict[str, float] = {
    'Colorado Rockies': 1.2,
    'Boston Red Sox': 0.4,
    'Cincinnati Reds': 0.3,
    'Chicago Cubs': 0.2,
    'Baltimore Orioles': 0.2,
    'Arizona Diamondbacks': 0.1,
    'San Francisco Giants': -0.4,
    'San Diego Padres': -0.3,
    'Oakland Athletics': -0.3,
    'Athletics': -0.3,
    'Miami Marlins': -0.3,
    'Seattle Mariners': -0.2,
}

INJURY_OUT = frozenset({'Out', 'OUT', 'Inactive', 'IL', '60-Day IL', '15-Day IL'})
INJURY_DOUBT = frozenset({'Doubtful', 'Questionable', 'GTD', 'Day-To-Day', 'DTD'})
LINEUP_TIER1 = frozenset({'SS', 'CF', '1B', '3B', 'DH'})
LINEUP_TIER2 = frozenset({'2B', 'LF', 'RF', 'C'})

_BP_CACHE: Dict[str, Tuple[float, float, bool, float, int, float]] = {}
_BP_CACHE_TTL = 900


def _db_path() -> str:
    if os.path.isfile('/data/sports_predictions_original.db'):
        return '/data/sports_predictions_original.db'
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sports_predictions_original.db')


def damped_blend(
    season_val: Optional[float],
    recent_val: Optional[float],
    *,
    recent_weight: float = 0.28,
    outlier_cap: float = 1.35,
) -> Tuple[Optional[float], float]:
    if season_val is None and recent_val is None:
        return None, 0.0
    if recent_val is None:
        return season_val, 0.0
    if season_val is None:
        return recent_val, recent_weight
    diff = abs(recent_val - season_val)
    cap = outlier_cap * max(0.5, abs(season_val) * 0.08 + 0.5)
    w = recent_weight * max(0.15, 1.0 - diff / cap)
    return (1.0 - w) * season_val + w * recent_val, w


def lineup_tier(position: str) -> int:
    p = (position or '').upper()
    if p in LINEUP_TIER1:
        return 1
    if p in LINEUP_TIER2:
        return 2
    return 3


@dataclass
class BullpenContext:
    home_ml_delta: float = 0.0
    away_ml_delta: float = 0.0
    total_adj: float = 0.0
    variance_bump: float = 0.0
    home_fatigue_score: float = 0.0
    away_fatigue_score: float = 0.0
    home_relief_out: int = 0
    away_relief_out: int = 0


@dataclass
class LineupContext:
    home_ml_delta: float = 0.0
    away_ml_delta: float = 0.0
    total_adj: float = 0.0
    lineup_confirmed: bool = False
    confidence_penalty: float = 0.0
    home_tier1: int = 0
    home_tier2: int = 0
    away_tier1: int = 0
    away_tier2: int = 0


@dataclass
class WeatherParkContext:
    total_adj: float = 0.0
    ml_delta: float = 0.0
    wind_mph: Optional[float] = None
    precip_pct: Optional[float] = None
    park_factor: float = 1.0
    source: str = 'static'


@dataclass
class MarketTimingContext:
    early_market: bool = False
    confidence_multiplier: float = 1.0
    flags: List[str] = field(default_factory=list)


@dataclass
class MLBContextDiagnostics:
    weather_contrib: float = 0.0
    bullpen_contrib: float = 0.0
    lineup_contrib: float = 0.0
    pitcher_form_contrib: float = 0.0
    umpire_contrib: float = 0.0
    total_context_adj: float = 0.0
    ml_context_delta: float = 0.0
    totals_variance_bump: float = 0.0
    prediction_stability: float = 1.0
    calibration_drift: float = 0.0
    book_total: Optional[float] = None
    model_total: Optional[float] = None
    total_vs_close: Optional[float] = None
    market_timing: Dict[str, Any] = field(default_factory=dict)
    early_market: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MLBContextResult:
    home_ml_adj: float = 0.0
    away_ml_adj: float = 0.0
    total_adj: float = 0.0
    injury_confidence: float = 0.75
    home_tier: str = 'average'
    away_tier: str = 'average'
    diagnostics: MLBContextDiagnostics = field(default_factory=MLBContextDiagnostics)
    lineup_confirmed: bool = False
    early_market: bool = False
    home_tier1: int = 0
    home_tier2: int = 0
    away_tier1: int = 0
    away_tier2: int = 0
    home_relief_out: int = 0
    away_relief_out: int = 0


def _recent_pitcher_era(pitcher_name: str) -> Optional[float]:
    if not pitcher_name:
        return None
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ERA FROM player_game_logs
            WHERE sport='MLB' AND player_name=?
            ORDER BY game_date DESC LIMIT 3
            """,
            (pitcher_name,),
        ).fetchall()
        conn.close()
        vals = [float(r['ERA']) for r in rows if r['ERA'] is not None]
        return sum(vals) / len(vals) if vals else None
    except Exception:
        return None


def pitcher_quality_tier(
    era: Optional[float],
    xera: Optional[float] = None,
    whip: Optional[float] = None,
    kbb: Optional[float] = None,
    recent_era: Optional[float] = None,
) -> Tuple[str, float]:
    blended_era, _ = damped_blend(era, recent_era, recent_weight=0.28, outlier_cap=1.4)
    score = 0.0
    count = 0
    for v in (blended_era, xera):
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
        else:
            score += 0.3
    if kbb is not None:
        count += 1
        if kbb >= 4.0:
            score += 1.0
        elif kbb >= 3.0:
            score += 0.75
        elif kbb >= 2.2:
            score += 0.5
        else:
            score += 0.3
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


def _team_bullpen_fatigue(
    team: str,
    game_date: str,
    injury_list: List[dict],
) -> Tuple[float, float, bool, float, int]:
    gday = str(game_date)[:10]
    cache_key = f"{team}|{gday}"
    now = time.time()
    cached = _BP_CACHE.get(cache_key)
    if cached and (now - cached[5]) < _BP_CACHE_TTL:
        return cached[0], cached[1], cached[2], cached[3], cached[4]

    key_relief = 0
    for inj in injury_list or []:
        st = inj.get('status') or ''
        if st not in INJURY_OUT and st not in INJURY_DOUBT:
            continue
        if (inj.get('position') or '').upper() in {'RP', 'CP', 'CL'}:
            key_relief += 1

    ml_boost = min(0.025, key_relief * 0.008)
    total_adj = 0.0
    fatigue_score = min(1.0, key_relief * 0.12)
    is_b2b = False

    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date(game_date) AS d,
                   CASE WHEN home_team_id=? THEN away_score ELSE home_score END AS ra
            FROM games
            WHERE sport='MLB' AND (home_team_id=? OR away_team_id=?)
              AND home_score IS NOT NULL AND date(game_date) < date(?)
            ORDER BY date(game_date) DESC LIMIT 3
            """,
            (team, team, team, gday),
        ).fetchall()
        conn.close()
        if rows:
            dates = [datetime.strptime(r['d'], '%Y-%m-%d') for r in rows if r['d']]
            cur = datetime.strptime(gday, '%Y-%m-%d')
            if dates and (cur - dates[0]).days <= 1:
                is_b2b = True
            games_3d = sum(1 for d in dates if (cur - d).days <= 3)
            high_run = sum(1 for r in rows if float(r['ra'] or 0) >= 5)
            fatigue_score = min(1.0, fatigue_score + games_3d * 0.15 + high_run * 0.1)
            if is_b2b:
                ml_boost += 0.01
                total_adj += 0.35
            if games_3d >= 2:
                total_adj += 0.25
            if high_run >= 2:
                total_adj += 0.2
    except Exception as exc:
        logger.debug('[mlb_context] bullpen %s: %s', team, exc)

    _BP_CACHE[cache_key] = (ml_boost, total_adj, is_b2b, fatigue_score, key_relief, now)
    return ml_boost, total_adj, is_b2b, fatigue_score, key_relief


def compute_bullpen_context(
    home_team: str,
    away_team: str,
    game_date: str,
    home_injuries: List[dict],
    away_injuries: List[dict],
) -> BullpenContext:
    h_ml, h_tot, _, h_fat, h_out = _team_bullpen_fatigue(home_team, game_date, home_injuries)
    a_ml, a_tot, _, a_fat, a_out = _team_bullpen_fatigue(away_team, game_date, away_injuries)
    var_bump = 0.15 if (h_fat > 0.45 or a_fat > 0.45) else 0.0
    return BullpenContext(
        home_ml_delta=h_ml,
        away_ml_delta=a_ml,
        total_adj=h_tot + a_tot,
        variance_bump=var_bump,
        home_fatigue_score=h_fat,
        away_fatigue_score=a_fat,
        home_relief_out=h_out,
        away_relief_out=a_out,
    )


def _lineup_absence_boost(injury_list: List[dict]) -> Tuple[float, int, int]:
    t1 = t2 = 0
    for inj in injury_list or []:
        st = inj.get('status') or ''
        if st not in INJURY_OUT and st not in INJURY_DOUBT:
            continue
        pos = (inj.get('position') or '').upper()
        if pos in {'P', 'SP', 'RP', 'CP', 'CL'}:
            continue
        tier = lineup_tier(pos)
        if tier == 1:
            t1 += 1
        elif tier == 2:
            t2 += 1
    boost = t1 * 0.025 + t2 * 0.012
    if t1 >= 2:
        boost += 0.02
    return boost, t1, t2


def compute_lineup_context(
    pitch: Dict[str, Any],
    home_injuries: List[dict],
    away_injuries: List[dict],
) -> LineupContext:
    home_sp = (pitch.get('home_sp_name') or '').strip()
    away_sp = (pitch.get('away_sp_name') or '').strip()
    confirmed = (
        home_sp and away_sp
        and home_sp.upper() not in ('TBD', 'UNKNOWN')
        and away_sp.upper() not in ('TBD', 'UNKNOWN')
        and pitch.get('has_pitching_data', True)
    )
    penalty = 0.0 if confirmed else 0.22
    h_boost, h_t1, h_t2 = _lineup_absence_boost(home_injuries)
    a_boost, a_t1, a_t2 = _lineup_absence_boost(away_injuries)
    return LineupContext(
        home_ml_delta=a_boost,
        away_ml_delta=h_boost,
        total_adj=0.0,
        lineup_confirmed=confirmed,
        confidence_penalty=penalty,
        home_tier1=h_t1,
        home_tier2=h_t2,
        away_tier1=a_t1,
        away_tier2=a_t2,
    )


def _load_game_weather(game_id: str, team: str) -> Optional[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT temperature, wind_speed, wind_direction, precipitation_chance, humidity
            FROM weather WHERE game_id=? AND team_name=? LIMIT 1
            """,
            (game_id, team),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def weather_park_total_adjustment(
    home_team: str,
    away_team: Optional[str] = None,
    game_date: Optional[str] = None,
    game_id: Optional[str] = None,
) -> WeatherParkContext:
    """Totals-heavy park layer; minimal moneyline impact."""
    park = MLB_PARK_RUN_FACTOR.get(home_team, 1.0)
    total_adj = MLB_PARK_POINTS.get(home_team, 0.0)
    total_adj += MLB_ALTITUDE_BOOST.get(home_team, 0.0)
    ml_delta = (park - 1.0) * 0.15
    ctx = WeatherParkContext(total_adj=total_adj, ml_delta=ml_delta, park_factor=park, source='static')
    wx = _load_game_weather(game_id, home_team) if game_id else None
    if not wx:
        return ctx
    ctx.source = 'db_weather'
    wind = wx.get('wind_speed')
    precip = wx.get('precipitation_chance')
    temp = wx.get('temperature')
    ctx.wind_mph = float(wind) if wind is not None else None
    ctx.precip_pct = float(precip) if precip is not None else None
    if ctx.wind_mph and ctx.wind_mph >= 12:
        total_adj += 0.35
    if ctx.precip_pct and ctx.precip_pct >= 50:
        total_adj -= 0.45
    if temp is not None:
        t = float(temp)
        if t >= 88:
            total_adj += 0.2
        elif t <= 48:
            total_adj -= 0.15
    ctx.total_adj = total_adj
    ctx.ml_delta = ml_delta
    return ctx


_UMPIRE_TOTAL_BIAS: Dict[str, float] = {
    'angel hernandez': 0.12,
    'cb bucknor': 0.08,
}


def umpire_total_adjustment(umpire_name: Optional[str] = None) -> float:
    if not umpire_name or os.environ.get('MLB_UMPIRE_ENABLED', 'true').lower() == 'false':
        return 0.0
    key = umpire_name.strip().lower()
    return _UMPIRE_TOTAL_BIAS.get(key, 0.0)


def validate_market_timing(
    game_date: Optional[str],
    home_ml: Optional[float],
    away_ml: Optional[float],
    book_total: Optional[float] = None,
    model_total: Optional[float] = None,
) -> MarketTimingContext:
    flags: List[str] = []
    early = False
    conf_mult = 1.0
    if home_ml is None or away_ml is None:
        early = True
        flags.append('missing_moneylines')
        conf_mult *= 0.88
    if book_total is None:
        early = True
        flags.append('missing_book_total')
        conf_mult *= 0.92
    if book_total is not None and model_total is not None:
        gap = abs(float(model_total) - float(book_total))
        if gap >= 1.25:
            flags.append('model_book_total_gap')
        if gap >= 2.0:
            conf_mult *= 0.9
    if game_date:
        try:
            gd = datetime.strptime(str(game_date)[:10], '%Y-%m-%d')
            hours = (gd - datetime.now()).total_seconds() / 3600.0
            if hours > 18:
                early = True
                flags.append('early_projection_window')
                conf_mult *= 0.95
        except Exception:
            pass
    return MarketTimingContext(early_market=early, confidence_multiplier=conf_mult, flags=flags)


def _apply_pitcher_scratch(
    injury_list: List[dict],
    sp_name: Optional[str],
    side_quality: str,
) -> Tuple[float, float, bool]:
    if not sp_name:
        return 0.0, 0.0, False
    scratched = False
    sp_lower = sp_name.lower()
    for inj in injury_list or []:
        name = (inj.get('name') or '').lower()
        pos = (inj.get('position') or '').upper()
        status = inj.get('status') or ''
        if status not in INJURY_OUT and status not in INJURY_DOUBT:
            continue
        if 'P' not in pos and 'PITCH' not in (inj.get('reason') or '').upper():
            continue
        if sp_lower in name or name in sp_lower:
            scratched = True
            break
    if not scratched:
        return 0.0, 0.0, False
    if side_quality == 'elite':
        return 0.15, 1.5, True
    if side_quality == 'above_avg':
        return 0.09, 1.0, True
    if side_quality == 'average':
        return 0.05, 0.7, True
    return 0.02, 0.5, True


def apply_mlb_context_layers(
    *,
    home_team: str,
    away_team: str,
    game_date: Optional[str],
    game_id: Optional[str],
    pitch: Dict[str, Any],
    home_injuries: List[dict],
    away_injuries: List[dict],
    pre_blended: float,
    home_mkt: Optional[float],
    home_ml: Optional[float] = None,
    away_ml: Optional[float] = None,
    book_total: Optional[float] = None,
    model_total: Optional[float] = None,
    umpire_name: Optional[str] = None,
    injury_conf_default: float = 0.75,
) -> MLBContextResult:
    """Aggregate MLB contextual layers; preserves ensemble — returns deltas only."""
    home_recent = _recent_pitcher_era(pitch.get('home_sp_name'))
    away_recent = _recent_pitcher_era(pitch.get('away_sp_name'))
    home_tier, _ = pitcher_quality_tier(
        pitch.get('home_sp_era'),
        pitch.get('home_sp_xera'),
        pitch.get('home_sp_whip'),
        pitch.get('home_sp_kbb'),
        home_recent,
    )
    away_tier, _ = pitcher_quality_tier(
        pitch.get('away_sp_era'),
        pitch.get('away_sp_xera'),
        pitch.get('away_sp_whip'),
        pitch.get('away_sp_kbb'),
        away_recent,
    )

    away_boost, away_tot_bump, _ = _apply_pitcher_scratch(
        home_injuries, pitch.get('home_sp_name'), home_tier,
    )
    home_boost, home_tot_bump, _ = _apply_pitcher_scratch(
        away_injuries, pitch.get('away_sp_name'), away_tier,
    )

    bp = compute_bullpen_context(home_team, away_team, str(game_date or ''), home_injuries, away_injuries)
    lu = compute_lineup_context(pitch, home_injuries, away_injuries)
    wx = weather_park_total_adjustment(home_team, away_team, game_date, game_id)
    ump_adj = umpire_total_adjustment(umpire_name)
    mt = validate_market_timing(game_date, home_ml, away_ml, book_total, model_total)

    home_adj = home_boost + lu.home_ml_delta + bp.home_ml_delta
    away_adj = away_boost + lu.away_ml_delta + bp.away_ml_delta
    total_adj = (
        home_tot_bump + away_tot_bump + bp.total_adj + wx.total_adj + ump_adj
    )

    inj_conf = injury_conf_default
    if lu.lineup_confirmed:
        inj_conf = max(inj_conf, 0.72)
    else:
        inj_conf = min(inj_conf, 0.55)
    inj_conf = max(0.35, inj_conf - lu.confidence_penalty * 0.15)
    inj_conf *= mt.confidence_multiplier

    diag = MLBContextDiagnostics(
        weather_contrib=wx.total_adj,
        bullpen_contrib=bp.total_adj,
        lineup_contrib=lu.total_adj,
        pitcher_form_contrib=home_tot_bump + away_tot_bump,
        umpire_contrib=ump_adj,
        total_context_adj=total_adj,
        ml_context_delta=wx.ml_delta,
        totals_variance_bump=bp.variance_bump,
        prediction_stability=mt.confidence_multiplier,
        book_total=book_total,
        model_total=model_total,
        total_vs_close=(
            round(float(model_total) - float(book_total), 2)
            if book_total is not None and model_total is not None
            else None
        ),
        market_timing={'flags': mt.flags, 'confidence_multiplier': mt.confidence_multiplier},
        early_market=mt.early_market,
    )

    return MLBContextResult(
        home_ml_adj=home_adj,
        away_ml_adj=away_adj,
        total_adj=total_adj,
        injury_confidence=inj_conf,
        home_tier=home_tier,
        away_tier=away_tier,
        diagnostics=diag,
        lineup_confirmed=lu.lineup_confirmed,
        early_market=mt.early_market,
        home_tier1=lu.home_tier1,
        home_tier2=lu.home_tier2,
        away_tier1=lu.away_tier1,
        away_tier2=lu.away_tier2,
        home_relief_out=bp.home_relief_out,
        away_relief_out=bp.away_relief_out,
    )
