#!/usr/bin/env python3
"""
Efficiency-based projection for NBA (and extendable to other sports).

Implements the same formula sportsbooks use to set opening lines:

    poss      = (home_pace + away_pace) / 2 / 100
    home_pts  = ((home_ortg + away_drtg) / 2) * (avg_pace / 100) + home_court_adv
    away_pts  = ((away_ortg + home_drtg) / 2) * (avg_pace / 100)
    spread    = home_pts - away_pts          # >0  home favored
    total     = home_pts + away_pts

Per-team ORtg / DRtg / Pace are computed from each team's recent box scores
fetched from ESPN's free `summary` endpoint:

    https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gameId}

Possessions per game (Dean Oliver's standard estimator):

    poss = FGA + 0.475 * FTA - OREB + TOV

Pace is normalized to 48 minutes:

    pace = poss * (48 / minutes_played)

Caching:
    Module-level URL cache (15-min TTL) shared with weighted_total_predictor.
    Per-team efficiency aggregate cache (10-min TTL).

All HTTP is parallelized via ThreadPoolExecutor.
"""

import time
import logging
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Share the scoreboard URL cache with weighted_total_predictor so we don't
# double-fetch the same date pages.
try:
    from weighted_total_predictor import _WT_CACHE as _URL_CACHE, _WT_TTL as _URL_TTL, _WT_CACHE_MAX as _URL_CACHE_MAX
except Exception:
    _URL_CACHE: dict = {}
    _URL_TTL = 900
    _URL_CACHE_MAX = 300

# Per-team aggregate cache (ORtg/DRtg/Pace) — bounded to 200 entries
_TEAM_EFF_CACHE: dict = {}
_TEAM_EFF_TTL = 600   # 10 minutes
_TEAM_EFF_MAX = 200   # ~30 NBA teams × a few lookback configs = well under this

# Home court advantage by sport (points). NBA ~2.5, NFL ~2.0, NHL ~0.2 etc.
_HOME_COURT_ADV = {
    'NBA': 2.5, 'WNBA': 2.0, 'NCAAB': 3.5, 'NCAAW': 3.0,
    'NFL': 2.0, 'NCAAF': 2.5, 'NHL': 0.2, 'MLB': 0.2,
}

_ESPN_PATHS = {
    'NBA':   'basketball/nba',
    'WNBA':  'basketball/wnba',
    'NCAAB': 'basketball/mens-college-basketball',
    'NHL':   'hockey/nhl',
    'NFL':   'football/nfl',
    'NCAAF': 'football/college-football',
    'MLB':   'baseball/mlb',
}


def _cached_get(url: str, timeout: int = 3):
    """Cached GET with 15-min TTL and bounded size; returns parsed JSON or None."""
    now = time.time()
    entry = _URL_CACHE.get(url)
    if entry and (now - entry['ts']) < _URL_TTL:
        return entry['data']
    try:
        r = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.debug(f"[eff] fetch failed for {url}: {e}")
        _URL_CACHE[url] = {'data': None, 'ts': now}
        return None
    if r.status_code != 200:
        _URL_CACHE[url] = {'data': None, 'ts': now}
        return None
    try:
        data = r.json()
    except Exception:
        _URL_CACHE[url] = {'data': None, 'ts': now}
        return None
    _URL_CACHE[url] = {'data': data, 'ts': now}
    # Evict oldest 20% of entries when over the size cap.
    if len(_URL_CACHE) > _URL_CACHE_MAX:
        evict_count = max(1, len(_URL_CACHE) // 5)
        for _old_url in sorted(_URL_CACHE, key=lambda u: _URL_CACHE[u]['ts'])[:evict_count]:
            _URL_CACHE.pop(_old_url, None)
    return data


def _parse_display_stat(dv) -> Optional[float]:
    """Parse ESPN displayValue — plain number or made-attempted (e.g. '40-88' → 88)."""
    if dv is None:
        return None
    text = str(dv).strip()
    if not text:
        return None
    if '-' in text:
        parts = text.split('-')
        if len(parts) >= 2:
            try:
                return float(parts[-1].strip())
            except (TypeError, ValueError):
                return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _stat_value(stats_list, *candidate_names) -> Optional[float]:
    """Pull a numeric stat from ESPN's `statistics` array, tolerating multiple
    field names. Returns None if nothing matches.

    ESPN sometimes uses camelCase ('fieldGoalsAttempted'), compound names
    ('fieldGoalsMade-fieldGoalsAttempted' → display '40-88'), or plain
    `displayValue` strings when `value` is null.
    """
    if not stats_list:
        return None
    candidates_lower = {n.lower() for n in candidate_names}
    compound_attempted = {
        'fieldgoalsattempted': 'fieldgoalsmade-fieldgoalsattempted',
        'fga': 'fieldgoalsmade-fieldgoalsattempted',
        'freethrowsattempted': 'freethrowsmade-freethrowsattempted',
        'fta': 'freethrowsmade-freethrowsattempted',
    }
    for s in stats_list:
        if not isinstance(s, dict):
            continue
        name = (s.get('name') or s.get('abbreviation') or s.get('label') or '').lower()
        if name in candidates_lower:
            v = s.get('value')
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
            parsed = _parse_display_stat(s.get('displayValue'))
            if parsed is not None:
                return parsed
    for want in candidates_lower:
        compound = compound_attempted.get(want)
        if not compound:
            continue
        for s in stats_list:
            if not isinstance(s, dict):
                continue
            name = (s.get('name') or '').lower()
            if name == compound:
                parsed = _parse_display_stat(s.get('displayValue'))
                if parsed is not None:
                    return parsed
    return None


def _extract_team_boxscore(summary_data, team_name: str) -> Optional[Dict]:
    """Pull (pts, opp_pts, fga, fta, oreb, tov, minutes_played) for `team_name`
    out of an ESPN summary payload. Returns None if anything's missing."""
    if not summary_data:
        return None
    box = summary_data.get('boxscore') or {}
    teams = box.get('teams') or []
    if len(teams) != 2:
        return None

    def _name(t):
        return ((t.get('team') or {}).get('displayName')
                or (t.get('team') or {}).get('name')
                or '').strip()

    # Identify which boxscore entry is our team.
    our_idx = next(
        (i for i, t in enumerate(teams) if _name(t) == team_name),
        None,
    )
    if our_idx is None:
        return None
    our = teams[our_idx]
    opp = teams[1 - our_idx]

    our_stats = our.get('statistics') or []
    fga = _stat_value(our_stats, 'fieldGoalsAttempted', 'FGA')
    fta = _stat_value(our_stats, 'freeThrowsAttempted', 'FTA')
    oreb = _stat_value(our_stats, 'offensiveRebounds', 'OREB')
    tov = _stat_value(our_stats, 'turnovers', 'TO', 'TOV')

    # Pull final scores from the header (more reliable than boxscore stats).
    header = summary_data.get('header') or {}
    comps = (header.get('competitions') or [{}])[0].get('competitors') or []
    our_pts = opp_pts = None
    for c in comps:
        tn = ((c.get('team') or {}).get('displayName')
              or (c.get('team') or {}).get('name')
              or '').strip()
        try:
            score = int(c.get('score') or 0)
        except (TypeError, ValueError):
            continue
        if tn == team_name:
            our_pts = score
        else:
            opp_pts = score

    # Was the game OT? We'll keep OT games but normalize pace to actual minutes.
    status = ((header.get('competitions') or [{}])[0].get('status') or {})
    status_type = status.get('type') or {}
    if not status_type.get('completed'):
        return None
    period = status.get('period') or 4
    # NBA regulation = 4 quarters * 12 = 48 min. OT period = 5 min each.
    minutes_played = 48 + max(0, (period - 4)) * 5 if period >= 4 else 48

    if None in (fga, fta, oreb, tov, our_pts, opp_pts):
        return None

    return {
        'pts': our_pts,
        'opp_pts': opp_pts,
        'fga': fga,
        'fta': fta,
        'oreb': oreb,
        'tov': tov,
        'minutes_played': minutes_played,
    }


def _list_team_recent_game_ids(team_name: str, sport: str,
                                max_lookback_days: int = 21,
                                n: int = 5,
                                as_of: Optional[datetime] = None) -> List[Tuple[str, int]]:
    """Return list of (event_id, minutes_played_guess) for `team_name`'s last
    `n` completed games before `as_of`. minutes_played_guess is 48 for regulation, +5 per OT.
    """
    path = _ESPN_PATHS.get(sport)
    if not path:
        return []
    end = as_of or datetime.now()
    out: List[Tuple[str, int]] = []

    for d in range(1, max_lookback_days + 1):
        if len(out) >= n:
            break
        date_str = (end - timedelta(days=d)).strftime('%Y%m%d')
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date_str}"
        data = _cached_get(url, timeout=3)
        if not data:
            continue
        for event in data.get('events', []):
            comp = (event.get('competitions') or [{}])[0]
            status = comp.get('status', {}).get('type', {})
            if status.get('state') != 'post':
                continue
            comps = comp.get('competitors') or []
            if len(comps) != 2:
                continue
            names = [((c.get('team') or {}).get('displayName') or '').strip() for c in comps]
            if team_name not in names:
                continue
            event_id = event.get('id')
            if not event_id:
                continue
            # Guess minutes played from period count if available.
            period = (comp.get('status') or {}).get('period') or 4
            mins = 48 + max(0, (period - 4)) * 5 if period >= 4 else 48
            out.append((str(event_id), mins))
            if len(out) >= n:
                break
    return out[:n]


def _aggregate_efficiency(games_box: List[Dict]) -> Optional[Dict[str, float]]:
    """Compute aggregate ORtg/DRtg/Pace plus style metrics from per-game box scores.

    Style metrics (ft_rate, oreb_rate, tov_rate) let the PL projection diverge
    from XSharp, which only sees rolling scoring averages and Elo — not shooting
    profile or turnover tendencies.
    """
    if not games_box:
        return None
    n = len(games_box)
    pts = sum(g['pts'] for g in games_box)
    opp = sum(g['opp_pts'] for g in games_box)
    fga_total = sum(g['fga'] for g in games_box)
    fta_total = sum(g['fta'] for g in games_box)
    oreb_total = sum(g['oreb'] for g in games_box)
    tov_total = sum(g['tov'] for g in games_box)
    poss = 0.0
    pace_mins = 0.0
    per_game_paces: List[float] = []
    per_game_pts: List[float] = []
    for g in games_box:
        p = g['fga'] + 0.475 * g['fta'] - g['oreb'] + g['tov']  # Dean Oliver possessions
        mins = max(1.0, g['minutes_played'] or 48)
        poss += p
        pace_mins += mins
        per_game_paces.append(p * (48.0 / mins))
        per_game_pts.append(float(g['pts']))
    if poss <= 0:
        return None
    ortg = pts / poss * 100.0
    drtg = opp / poss * 100.0
    pace = (poss / max(1.0, pace_mins)) * 48.0

    # Pace variance: std dev of per-game paces (measures tempo consistency).
    if len(per_game_paces) >= 2:
        _mean_p = sum(per_game_paces) / len(per_game_paces)
        pace_std = round(
            (sum((p - _mean_p) ** 2 for p in per_game_paces) / (len(per_game_paces) - 1)) ** 0.5, 2
        )
    else:
        pace_std = 0.0

    # Scoring volatility: std dev of per-game points scored.
    if len(per_game_pts) >= 2:
        _mean_s = sum(per_game_pts) / len(per_game_pts)
        ppg_std = round(
            (sum((s - _mean_s) ** 2 for s in per_game_pts) / (len(per_game_pts) - 1)) ** 0.5, 2
        )
    else:
        ppg_std = 0.0

    # Style metrics — give PL model signals XSharp cannot see.
    ft_rate = round(fta_total / max(fga_total, 1), 3)    # free-throw generation
    oreb_rate = round(oreb_total / max(fga_total, 1), 3)  # offensive rebounding pressure
    tov_rate = round(tov_total / max(poss, 1), 3)         # turnover rate per possession

    return {
        'ortg': round(ortg, 2),
        'drtg': round(drtg, 2),
        'pace': round(pace, 2),
        'pace_std': pace_std,
        'ppg_std': ppg_std,
        'games': n,
        'ppg': round(pts / n, 2),
        'opp_ppg': round(opp / n, 2),
        'ft_rate': ft_rate,
        'oreb_rate': oreb_rate,
        'tov_rate': tov_rate,
    }


def compute_team_efficiency(team_name: str, sport: str = 'NBA',
                            n_games: int = 5,
                            max_lookback_days: int = 21,
                            max_workers: int = 8,
                            as_of: Optional[datetime] = None) -> Optional[Dict[str, float]]:
    """Top-level: fetch box scores for `team_name`'s last `n_games` before `as_of`
    and return aggregate {ortg, drtg, pace, games, ppg, opp_ppg}. Cached 10 min."""
    as_of = as_of or datetime.now()
    cache_key = (sport, team_name, n_games, as_of.strftime('%Y-%m-%d'))
    entry = _TEAM_EFF_CACHE.get(cache_key)
    now = time.time()
    if entry and (now - entry['ts']) < _TEAM_EFF_TTL:
        return entry['data']

    path = _ESPN_PATHS.get(sport)
    if not path:
        return None

    ids = _list_team_recent_game_ids(team_name, sport, max_lookback_days, n_games, as_of=as_of)
    if not ids:
        _TEAM_EFF_CACHE[cache_key] = {'data': None, 'ts': now}
        return None

    # Fetch summaries in parallel
    summary_urls = [
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary?event={gid}"
        for gid, _ in ids
    ]

    def _fetch(u):
        return _cached_get(u, timeout=4)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        summaries = list(ex.map(_fetch, summary_urls))

    games_box = []
    for s in summaries:
        bx = _extract_team_boxscore(s, team_name)
        if bx:
            games_box.append(bx)

    agg = _aggregate_efficiency(games_box)
    _TEAM_EFF_CACHE[cache_key] = {'data': agg, 'ts': now}
    # Evict oldest entries when over size cap.
    if len(_TEAM_EFF_CACHE) > _TEAM_EFF_MAX:
        evict_count = max(1, len(_TEAM_EFF_CACHE) // 5)
        for _old_key in sorted(_TEAM_EFF_CACHE, key=lambda k: _TEAM_EFF_CACHE[k]['ts'])[:evict_count]:
            _TEAM_EFF_CACHE.pop(_old_key, None)
    return agg


def compute_efficiency_projection_from(home_eff: Dict, away_eff: Dict,
                                       sport: str = 'NBA',
                                       home_court_adv: Optional[float] = None,
                                       xsharp_total: Optional[float] = None,
                                       xsharp_spread: Optional[float] = None) -> Dict:
    """Project given PRE-COMPUTED efficiency dicts. No network. Synchronous."""
    if home_court_adv is None:
        home_court_adv = _HOME_COURT_ADV.get(sport, 2.5)

    avg_pace = (home_eff['pace'] + away_eff['pace']) / 2.0

    # Playoff intensity modifier: NBA playoff games run ~2% slower pace.
    playoff_modifier = False
    if sport == 'NBA':
        month = datetime.now().month
        if month in (4, 5, 6):
            avg_pace *= 0.98
            playoff_modifier = True

    poss_mult = avg_pace / 100.0

    home_pts = ((home_eff['ortg'] + away_eff['drtg']) / 2.0) * poss_mult + home_court_adv
    away_pts = ((away_eff['ortg'] + home_eff['drtg']) / 2.0) * poss_mult

    # ── Style-based adjustments (NBA only) ──────────────────────────────────
    # These give PL a signal XSharp's rolling-score averages cannot see:
    #   FT rate  → foul-drawing teams generate more points per possession
    #   OREB rate → second-chance volume elevates effective scoring above ORtg
    #   TOV rate  → turnover-heavy teams lose possessions → fewer points
    # Baselines are 2024-25 NBA medians.
    if sport == 'NBA':
        _FT_BASE   = 0.280   # median fta / fga
        _OREB_BASE = 0.230   # median oreb / fga
        _TOV_BASE  = 0.140   # median tov / poss

        def _style_adj(eff, base_ft, base_oreb, base_tov):
            ft_delta   = min(0.12, max(-0.12, eff.get('ft_rate', base_ft) - base_ft))
            oreb_delta = min(0.10, max(-0.10, eff.get('oreb_rate', base_oreb) - base_oreb))
            tov_delta  = min(0.06, max(-0.06, eff.get('tov_rate', base_tov) - base_tov))
            adj = ft_delta * 15.0 + oreb_delta * 20.0 - tov_delta * 20.0
            return max(-5.0, min(5.0, adj))  # hard cap ±5 pts

        home_pts += _style_adj(home_eff, _FT_BASE, _OREB_BASE, _TOV_BASE)
        away_pts += _style_adj(away_eff, _FT_BASE, _OREB_BASE, _TOV_BASE)

    projected_total  = home_pts + away_pts
    projected_spread = home_pts - away_pts  # >0 home favored

    def _half_round(v):
        return round(v * 2) / 2.0

    # ── Variance + volatility → confidence tier ─────────────────────────────
    h_pace_std = home_eff.get('pace_std', 0.0) or 0.0
    a_pace_std = away_eff.get('pace_std', 0.0) or 0.0
    avg_pace_std = (h_pace_std + a_pace_std) / 2.0

    h_ppg_std = home_eff.get('ppg_std', 0.0) or 0.0
    a_ppg_std = away_eff.get('ppg_std', 0.0) or 0.0
    avg_ppg_std = (h_ppg_std + a_ppg_std) / 2.0

    min_games = min(home_eff.get('games', 5), away_eff.get('games', 5))
    # Volatility score 0–10 (higher = more uncertain)
    volatility_score = round(min(10.0, (avg_pace_std / 1.5 + avg_ppg_std / 2.5) / 2.0), 1)

    if avg_pace_std < 3.0 and avg_ppg_std < 8.0 and min_games >= 4:
        variance_tier = 'Low'
        confidence_tier = 'High'
    elif avg_pace_std < 6.0 and avg_ppg_std < 14.0 and min_games >= 3:
        variance_tier = 'Med'
        confidence_tier = 'Med'
    else:
        variance_tier = 'High'
        confidence_tier = 'Low'

    out = {
        'home_eff': home_eff,
        'away_eff': away_eff,
        'home_pts': round(home_pts, 1),
        'away_pts': round(away_pts, 1),
        'projected_total':  _half_round(projected_total),
        'projected_spread': _half_round(projected_spread),
        'avg_pace': round(avg_pace, 2),
        'home_court_adv': home_court_adv,
        'variance_tier': variance_tier,
        'confidence_tier': confidence_tier,
        'volatility_score': volatility_score,
        'avg_pace_std': round(avg_pace_std, 2),
        'avg_ppg_std': round(avg_ppg_std, 2),
        'playoff_modifier': playoff_modifier,
    }
    if xsharp_total is not None:
        out['delta_vs_xsharp_total'] = round(projected_total - float(xsharp_total), 1)
    if xsharp_spread is not None:
        out['delta_vs_xsharp_spread'] = round(projected_spread - float(xsharp_spread), 1)
    return out


def precompute_team_efficiencies(team_names: List[str], sport: str = 'NBA',
                                  n_games: int = 5,
                                  max_lookback_days: int = 14,
                                  total_budget_seconds: float = 10.0,
                                  max_workers: int = 16,
                                  as_of: Optional[datetime] = None) -> Dict[str, Optional[Dict]]:
    """Compute ORtg/DRtg/Pace for every team in `team_names` in parallel,
    with a HARD wall-clock budget. Teams that don't finish in time get None.

    Returns: {team_name: efficiency_dict_or_None}. Logs timing + per-team
    success/failure so the caller can see exactly what ESPN returned.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTimeout

    start = time.time()
    out: Dict[str, Optional[Dict]] = {}
    unique = list(dict.fromkeys(t for t in team_names if t))  # dedupe, preserve order

    if not unique:
        return out

    workers = min(max_workers, max(2, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(
                compute_team_efficiency, t, sport, n_games, max_lookback_days, 4, as_of,
            ): t
            for t in unique
        }
        try:
            for fut in as_completed(futures, timeout=total_budget_seconds):
                team = futures[fut]
                try:
                    out[team] = fut.result(timeout=0.1)
                except Exception as e:
                    logger.debug(f"[eff] {team} compute failed: {e}")
                    out[team] = None
        except FTimeout:
            logger.warning(
                f"[eff] precompute hit {total_budget_seconds}s budget — "
                f"{len(out)}/{len(unique)} teams finished"
            )
            # Mark the ones still in flight as None so callers fall back.
            for fut, team in futures.items():
                if team not in out:
                    out[team] = None
                    fut.cancel()

    elapsed = time.time() - start
    success = sum(1 for v in out.values() if v)
    logger.info(
        f"[eff] precomputed {success}/{len(unique)} {sport} teams in {elapsed:.2f}s"
    )
    return out


def compute_efficiency_projection(home_team: str, away_team: str,
                                  sport: str = 'NBA',
                                  n_games: int = 5,
                                  home_court_adv: Optional[float] = None,
                                  xsharp_total: Optional[float] = None,
                                  xsharp_spread: Optional[float] = None) -> Optional[Dict]:
    """Compute the efficiency-based projected spread and total.

    Returns None when efficiency stats can't be computed for either team
    (caller should fall back to a simpler estimate).

    Sign convention:
        projected_spread > 0  ⇒  home team favored by that many points.
    """
    if home_court_adv is None:
        home_court_adv = _HOME_COURT_ADV.get(sport, 2.5)

    # Fetch both teams in parallel
    with ThreadPoolExecutor(max_workers=2) as ex:
        home_fut = ex.submit(compute_team_efficiency, home_team, sport, n_games)
        away_fut = ex.submit(compute_team_efficiency, away_team, sport, n_games)
        home_eff = home_fut.result()
        away_eff = away_fut.result()

    if not home_eff or not away_eff:
        return None

    avg_pace = (home_eff['pace'] + away_eff['pace']) / 2.0

    # Playoff intensity modifier for NBA.
    playoff_modifier = False
    if sport == 'NBA':
        month = datetime.now().month
        if month in (4, 5, 6):
            avg_pace *= 0.98
            playoff_modifier = True

    poss_mult = avg_pace / 100.0

    home_pts = ((home_eff['ortg'] + away_eff['drtg']) / 2.0) * poss_mult + home_court_adv
    away_pts = ((away_eff['ortg'] + home_eff['drtg']) / 2.0) * poss_mult

    # Style-based adjustments (NBA only) — FT rate, OREB rate, TOV rate.
    if sport == 'NBA':
        _FT_BASE   = 0.280
        _OREB_BASE = 0.230
        _TOV_BASE  = 0.140

        def _style_adj(eff, base_ft, base_oreb, base_tov):
            ft_delta   = min(0.12, max(-0.12, eff.get('ft_rate', base_ft) - base_ft))
            oreb_delta = min(0.10, max(-0.10, eff.get('oreb_rate', base_oreb) - base_oreb))
            tov_delta  = min(0.06, max(-0.06, eff.get('tov_rate', base_tov) - base_tov))
            adj = ft_delta * 15.0 + oreb_delta * 20.0 - tov_delta * 20.0
            return max(-5.0, min(5.0, adj))

        home_pts += _style_adj(home_eff, _FT_BASE, _OREB_BASE, _TOV_BASE)
        away_pts += _style_adj(away_eff, _FT_BASE, _OREB_BASE, _TOV_BASE)

    projected_total  = home_pts + away_pts
    projected_spread = home_pts - away_pts  # >0 home favored

    def _half_round(v):
        return round(v * 2) / 2.0

    # Variance and confidence tier (pace_std + ppg_std).
    h_pace_std = home_eff.get('pace_std', 0.0) or 0.0
    a_pace_std = away_eff.get('pace_std', 0.0) or 0.0
    avg_pace_std = (h_pace_std + a_pace_std) / 2.0

    h_ppg_std = home_eff.get('ppg_std', 0.0) or 0.0
    a_ppg_std = away_eff.get('ppg_std', 0.0) or 0.0
    avg_ppg_std = (h_ppg_std + a_ppg_std) / 2.0

    min_games = min(home_eff.get('games', 5), away_eff.get('games', 5))
    volatility_score = round(min(10.0, (avg_pace_std / 1.5 + avg_ppg_std / 2.5) / 2.0), 1)

    if avg_pace_std < 3.0 and avg_ppg_std < 8.0 and min_games >= 4:
        variance_tier = 'Low'
        confidence_tier = 'High'
    elif avg_pace_std < 6.0 and avg_ppg_std < 14.0 and min_games >= 3:
        variance_tier = 'Med'
        confidence_tier = 'Med'
    else:
        variance_tier = 'High'
        confidence_tier = 'Low'

    out = {
        'home_eff': home_eff,
        'away_eff': away_eff,
        'home_pts': round(home_pts, 1),
        'away_pts': round(away_pts, 1),
        'projected_total':  _half_round(projected_total),
        'projected_spread': _half_round(projected_spread),
        'avg_pace': round(avg_pace, 2),
        'home_court_adv': home_court_adv,
        'variance_tier': variance_tier,
        'confidence_tier': confidence_tier,
        'volatility_score': volatility_score,
        'avg_pace_std': round(avg_pace_std, 2),
        'avg_ppg_std': round(avg_ppg_std, 2),
        'playoff_modifier': playoff_modifier,
    }

    # Compare against XSharp lines if provided
    if xsharp_total is not None:
        out['delta_vs_xsharp_total'] = round(projected_total - float(xsharp_total), 1)
    if xsharp_spread is not None:
        out['delta_vs_xsharp_spread'] = round(projected_spread - float(xsharp_spread), 1)

    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python team_efficiency.py 'Home Team' 'Away Team' [sport]")
        sys.exit(1)
    home = sys.argv[1]
    away = sys.argv[2]
    sport = sys.argv[3] if len(sys.argv) > 3 else 'NBA'
    r = compute_efficiency_projection(home, away, sport=sport, n_games=5)
    if not r:
        print(f"❌ Could not compute projection for {home} vs {away}")
        sys.exit(2)
    print(f"\n{home}  ORtg={r['home_eff']['ortg']}  DRtg={r['home_eff']['drtg']}  Pace={r['home_eff']['pace']}")
    print(f"{away}  ORtg={r['away_eff']['ortg']}  DRtg={r['away_eff']['drtg']}  Pace={r['away_eff']['pace']}")
    print(f"\nProjected score: {home} {r['home_pts']} - {r['away_pts']} {away}")
    print(f"Projected spread: {'%+.1f' % r['projected_spread']} (home)")
    print(f"Projected total:  {r['projected_total']}")
