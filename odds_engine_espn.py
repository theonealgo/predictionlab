"""
Standalone ESPN-Based Odds Engine
==================================
Generates sportsbook-style moneyline, spread, and total odds using only
ESPN team stats and historical performance. Zero dependency on prediction models.

Usage:
    from odds_engine_espn import get_odds, get_all_team_stats
    odds = get_odds('NBA', 'Boston Celtics', 'Miami Heat')
"""

import time
import math
import logging
import requests
from scipy.stats import norm

logger = logging.getLogger(__name__)

# ─── ESPN API configuration ──────────────────────────────────────────────────

ESPN_SPORT_PATHS = {
    'NBA':   ('basketball', 'nba'),
    'NHL':   ('hockey',     'nhl'),
    'MLB':   ('baseball',   'mlb'),
    'NFL':   ('football',   'nfl'),
    'NCAAB': ('basketball', 'mens-college-basketball'),
    'NCAAF': ('football',   'college-football'),
    'NCAAW': ('basketball', 'womens-college-basketball'),
    'WNBA':  ('basketball', 'wnba'),
    'SOCCER': ('soccer',    'eng.1'),  # EPL as default; per-league fetching handled by Flask
}

# ─── Sport-specific constants ─────────────────────────────────────────────────

HOME_FIELD_ADV = {
    'NBA': 2.0, 'NHL': 0.25, 'MLB': 0.15, 'NFL': 2.5,
    'NCAAB': 3.0, 'NCAAF': 3.0, 'NCAAW': 3.0, 'WNBA': 2.5,
    'SOCCER': 0.3,
}

# Standard deviation of score differential (controls how extreme probabilities get)
SCORE_STD = {
    'NBA': 11.0, 'NHL': 1.3, 'MLB': 1.8, 'NFL': 9.5,
    'NCAAB': 11.0, 'NCAAF': 14.0, 'NCAAW': 10.5, 'WNBA': 8.5,
    'SOCCER': 1.1,
}

# Typical scoring ranges for sanity-clamping totals
TOTAL_RANGE = {
    'NBA':   (195, 250),
    'NHL':   (4.5, 8.0),
    'MLB':   (6.5, 11.0),
    'NFL':   (35, 58),
    'NCAAB': (120, 170),
    'NCAAF': (35, 70),
    'NCAAW': (110, 160),
    'WNBA':  (140, 190),
    'SOCCER': (1.5, 5.0),
}

DEFAULT_VIG = 0.04

# Sportsbook-realistic caps
ML_CAP_FAV = -1500     # no favorite stronger than -1500
ML_CAP_DOG = 1000      # no underdog bigger than +1000
SPREAD_CAP = {         # max spread per sport
    'NBA': 14.0, 'NHL': 2.5, 'MLB': 2.5, 'NFL': 14.5,
    'NCAAB': 18.0, 'NCAAF': 24.0, 'NCAAW': 18.0, 'WNBA': 13.0,
    'SOCCER': 2.5,
}

# ─── Cache ────────────────────────────────────────────────────────────────────

_TEAM_STATS_CACHE: dict = {}
_CACHE_TTL = 900  # 15 minutes


def _normalize_name(name: str) -> str:
    """Normalize team name for fuzzy matching."""
    import re
    import unicodedata
    if not name:
        return ''
    txt = unicodedata.normalize('NFKD', str(name))
    txt = txt.encode('ascii', 'ignore').decode('ascii')
    txt = txt.lower().replace('&', 'and')
    txt = re.sub(r'[^a-z0-9]+', '', txt)
    return txt


# ─── STEP 1: Fetch all team stats from ESPN ───────────────────────────────────

def _fetch_all_teams(sport: str) -> dict:
    """Fetch every team's season stats from ESPN for a given sport.
    
    Returns dict mapping normalized team name -> stats dict.
    """
    path = ESPN_SPORT_PATHS.get(sport)
    if not path:
        return {}
    
    sport_slug, league_slug = path
    
    # Get team list
    teams_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/{league_slug}/teams"
    try:
        resp = requests.get(teams_url, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.warning(f"[odds_engine] Failed to fetch {sport} teams: {e}")
        return {}
    
    teams_list = (
        data.get('sports', [{}])[0]
            .get('leagues', [{}])[0]
            .get('teams', [])
    )
    
    all_stats = {}
    
    for entry in teams_list:
        team = entry.get('team', {})
        team_id = team.get('id')
        display_name = team.get('displayName', '')
        abbreviation = team.get('abbreviation', '')
        
        if not team_id:
            continue
        
        # Fetch individual team stats (has PPG, PA, home/away)
        team_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/{league_slug}/teams/{team_id}"
        try:
            tresp = requests.get(team_url, timeout=8)
            tdata = tresp.json().get('team', {})
        except Exception:
            continue
        
        record_items = tdata.get('record', {}).get('items', [])
        overall = next((i for i in record_items if i.get('type') == 'total'), {})
        home_rec = next((i for i in record_items if i.get('type') == 'home'), {})
        away_rec = next((i for i in record_items if i.get('type') == 'road'), {})
        
        def _stat(record_item, key, default=None):
            for s in record_item.get('stats', []):
                if s.get('name') == key:
                    return s.get('value', default)
            return default
        
        ppg = _stat(overall, 'avgPointsFor')
        pag = _stat(overall, 'avgPointsAgainst')
        gp = _stat(overall, 'gamesPlayed', 0)
        win_pct = _stat(overall, 'winPercent', 0.5)
        differential = _stat(overall, 'differential', 0)
        wins = _stat(overall, 'wins', 0)
        losses = _stat(overall, 'losses', 0)
        
        home_win_pct = _stat(home_rec, 'winPercent', win_pct)
        away_win_pct = _stat(away_rec, 'winPercent', win_pct)
        
        if ppg is None or pag is None:
            continue
        
        stats = {
            'team_id': team_id,
            'name': display_name,
            'abbreviation': abbreviation,
            'ppg': float(ppg),
            'pag': float(pag),
            'net_rating': float(ppg) - float(pag),
            'games_played': int(gp) if gp else 0,
            'win_pct': float(win_pct) if win_pct else 0.5,
            'wins': int(wins) if wins else 0,
            'losses': int(losses) if losses else 0,
            'home_win_pct': float(home_win_pct) if home_win_pct else 0.5,
            'away_win_pct': float(away_win_pct) if away_win_pct else 0.5,
            'differential': float(differential) if differential else 0.0,
        }
        
        # Store under multiple keys for fuzzy lookup
        norm_name = _normalize_name(display_name)
        all_stats[norm_name] = stats
        all_stats[_normalize_name(abbreviation)] = stats
        # Also store short name (e.g., "Celtics")
        short = display_name.split()[-1] if display_name else ''
        if short:
            all_stats[_normalize_name(short)] = stats
    
    return all_stats


def get_all_team_stats(sport: str) -> dict:
    """Get cached team stats for a sport."""
    cache_key = sport
    now = time.time()
    cached = _TEAM_STATS_CACHE.get(cache_key)
    if cached and (now - cached['ts']) < _CACHE_TTL:
        return cached['data']
    
    logger.info(f"[odds_engine] Fetching {sport} team stats from ESPN...")
    stats = _fetch_all_teams(sport)
    # Cache even if empty so we don't keep re-fetching
    _TEAM_STATS_CACHE[cache_key] = {'data': stats, 'ts': now}
    if stats:
        logger.info(f"[odds_engine] Loaded {len(set(id(v) for v in stats.values()))} {sport} teams")
    return stats


def _find_team(all_stats: dict, team_name: str) -> dict | None:
    """Find team stats by name with fuzzy matching."""
    norm = _normalize_name(team_name)
    if norm in all_stats:
        return all_stats[norm]
    # Try partial match
    for key, val in all_stats.items():
        if norm in key or key in norm:
            return val
    return None


# ─── STEP 2 & 3: Power Rating + Score Projection ─────────────────────────────

def _compute_league_avg(all_stats: dict) -> float:
    """Compute league average PPG from all teams (deduplicated)."""
    seen = set()
    total_ppg = 0.0
    count = 0
    for stats in all_stats.values():
        tid = stats.get('team_id')
        if tid in seen:
            continue
        seen.add(tid)
        total_ppg += stats['ppg']
        count += 1
    return total_ppg / count if count > 0 else 100.0


def _project_scores(sport: str, home_stats: dict, away_stats: dict,
                    league_avg: float = None) -> tuple:
    """Project expected scores using league-relative offensive/defensive ratings.

    Uses FULL adjustments (not halved) so a +10 offense vs +5 bad defense
    produces a +15 projection, not +7.5.

    Returns (home_score, away_score).
    """
    if league_avg is None:
        league_avg = (home_stats['ppg'] + away_stats['ppg']) / 2

    hfa = HOME_FIELD_ADV.get(sport, 2.0)

    # Dampen PPG/PA for tanking teams (win% < .350) to prevent inflated spreads
    # in bad-vs-bad matchups like IND@CHI. Good teams keep raw stats.
    TANK_THRESHOLD = 0.350
    DAMPEN_FACTOR = 0.30  # blend 30% toward league average

    def _dampen(ppg, pag, win_pct):
        if win_pct < TANK_THRESHOLD:
            return (
                ppg * (1 - DAMPEN_FACTOR) + league_avg * DAMPEN_FACTOR,
                pag * (1 - DAMPEN_FACTOR) + league_avg * DAMPEN_FACTOR,
            )
        return ppg, pag

    h_ppg, h_pag = _dampen(home_stats['ppg'], home_stats['pag'], home_stats['win_pct'])
    a_ppg, a_pag = _dampen(away_stats['ppg'], away_stats['pag'], away_stats['win_pct'])

    # Offensive/defensive ratings relative to league average
    home_off_adj = h_ppg - league_avg
    home_def_adj = h_pag - league_avg
    away_off_adj = a_ppg - league_avg
    away_def_adj = a_pag - league_avg

    # Score projection: league avg + own offense adj + opponent's defensive weakness
    home_score = league_avg + home_off_adj + away_def_adj + hfa
    away_score = league_avg + away_off_adj + home_def_adj

    # Win% power adjustment — quadratic, 120x multiplier, capped at ±8
    # Higher cap lets elite teams (.760) produce proper extreme spreads
    # Selective dampening above prevents tankers from over-inflating
    home_dev = home_stats['win_pct'] - 0.5
    away_dev = away_stats['win_pct'] - 0.5
    home_power = home_dev * abs(home_dev) * 120.0
    away_power = away_dev * abs(away_dev) * 120.0
    home_power = math.copysign(min(abs(home_power), 8.0), home_power)
    away_power = math.copysign(min(abs(away_power), 8.0), away_power)
    home_score += home_power
    away_score += away_power

    return max(home_score, 0.5), max(away_score, 0.5)


# ─── STEP 4-7: Spread, Total, Probability, American Odds ─────────────────────

def _spread_to_win_prob(spread: float, sport: str) -> float:
    """Convert point spread to win probability using normal CDF.
    
    spread > 0 means home team is favored.
    Returns home team win probability (0.01 to 0.99).
    """
    std = SCORE_STD.get(sport, 10.0)
    # CDF of the spread: probability that home team wins
    prob = float(norm.cdf(spread / std))
    # Clamp to allow extreme but not impossible probabilities
    return max(0.01, min(0.99, prob))


def _prob_to_american(p: float) -> int | None:
    """Convert probability (0-1) to American odds."""
    if p is None or p <= 0.01 or p >= 0.99:
        if p and p >= 0.99:
            return -9900
        if p and p <= 0.01:
            return 9900
        return None
    if p >= 0.5:
        return -round((p / (1 - p)) * 100)
    return round(((1 - p) / p) * 100)


def _apply_vig(home_prob: float, away_prob: float, vig: float = DEFAULT_VIG) -> tuple:
    """Apply sportsbook vig to probabilities.
    
    Returns (home_vig_prob, away_vig_prob) where combined > 1.0.
    """
    total = home_prob + away_prob
    ph = home_prob / total if total > 0 else 0.5
    pa = away_prob / total if total > 0 else 0.5
    vig_factor = 1 + vig
    return (
        min(ph * vig_factor, 0.99),
        min(pa * vig_factor, 0.99),
    )


def _round_to_half(x: float) -> float:
    """Round to nearest 0.5."""
    return round(x * 2) / 2


# ─── MAIN API ─────────────────────────────────────────────────────────────────

def get_odds(sport: str, home_team: str, away_team: str) -> dict | None:
    """Generate sportsbook-style odds for a matchup.
    
    Args:
        sport: 'NBA', 'NHL', 'MLB', 'NFL', 'NCAAB', etc.
        home_team: Full team name (e.g., 'Boston Celtics')
        away_team: Full team name (e.g., 'Miami Heat')
    
    Returns dict with:
        moneyline_home, moneyline_away, spread, spread_price_home,
        spread_price_away, total, total_over_price, total_under_price,
        win_prob_home, win_prob_away, expected_home_score, expected_away_score,
        home_ppg, home_pag, away_ppg, away_pag, source
    """
    all_stats = get_all_team_stats(sport)
    if not all_stats:
        return None
    
    home_s = _find_team(all_stats, home_team)
    away_s = _find_team(all_stats, away_team)
    
    if not home_s or not away_s:
        logger.debug(f"[odds_engine] Team not found: home={home_team} away={away_team}")
        return None
    
    # Project scores
    league_avg = _compute_league_avg(all_stats)
    home_score, away_score = _project_scores(sport, home_s, away_s, league_avg)
    
    # Spread: favorite gets minus, underdog gets plus
    raw_spread = home_score - away_score  # positive = home favored
    max_spd = SPREAD_CAP.get(sport, 20.0)
    spread_mag = min(_round_to_half(abs(raw_spread)), max_spd)
    if raw_spread >= 0:
        spread_home = -spread_mag
        spread_away = spread_mag
    else:
        spread_home = spread_mag
        spread_away = -spread_mag

    # Total: blend projected total with league average to avoid extremes
    raw_total = home_score + away_score
    league_total = league_avg * 2 if league_avg else raw_total
    blended_total = raw_total * 0.75 + league_total * 0.25  # dampen toward league avg
    lo, hi = TOTAL_RANGE.get(sport, (0, 999))
    clamped_total = max(lo, min(hi, blended_total))
    total = _round_to_half(clamped_total)

    # Win probability from capped spread
    win_prob_home = _spread_to_win_prob(raw_spread, sport)
    win_prob_away = 1.0 - win_prob_home

    # Apply vig
    home_vig, away_vig = _apply_vig(win_prob_home, win_prob_away)

    # Moneyline: favorite is negative, underdog is positive — capped
    ml_home = _prob_to_american(home_vig)
    ml_away = _prob_to_american(away_vig)
    if ml_home is not None:
        ml_home = max(ml_home, ML_CAP_FAV) if ml_home < 0 else min(ml_home, ML_CAP_DOG)
    if ml_away is not None:
        ml_away = max(ml_away, ML_CAP_FAV) if ml_away < 0 else min(ml_away, ML_CAP_DOG)

    # Spread prices (standard -110/-110)
    spread_vig_h, spread_vig_a = _apply_vig(0.5, 0.5)
    spread_price_home = _prob_to_american(spread_vig_h)
    spread_price_away = _prob_to_american(spread_vig_a)

    # Total prices (standard -110/-110)
    total_price_over = _prob_to_american(spread_vig_h)
    total_price_under = _prob_to_american(spread_vig_a)

    return {
        'moneyline_home': ml_home,
        'moneyline_away': ml_away,
        'spread_home': spread_home,
        'spread_away': spread_away,
        'spread': spread_home,  # legacy: home team perspective
        'spread_price_home': spread_price_home,
        'spread_price_away': spread_price_away,
        'total': total,
        'total_over_price': total_price_over,
        'total_under_price': total_price_under,
        'win_prob_home': round(win_prob_home, 4),
        'win_prob_away': round(win_prob_away, 4),
        'expected_home_score': round(home_score, 1),
        'expected_away_score': round(away_score, 1),
        'home_ppg': round(home_s['ppg'], 1),
        'home_pag': round(home_s['pag'], 1),
        'away_ppg': round(away_s['ppg'], 1),
        'away_pag': round(away_s['pag'], 1),
        'home_record': f"{home_s['wins']}-{home_s['losses']}",
        'away_record': f"{away_s['wins']}-{away_s['losses']}",
        'source': 'espn_engine',
    }


# ─── Convenience: get odds for all games on today's scoreboard ────────────────

def get_todays_odds(sport: str) -> list:
    """Get odds for all games on today's ESPN scoreboard."""
    path = ESPN_SPORT_PATHS.get(sport)
    if not path:
        return []
    
    sport_slug, league_slug = path
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/{league_slug}/scoreboard"
    
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.warning(f"[odds_engine] Scoreboard fetch failed: {e}")
        return []
    
    results = []
    for event in data.get('events', []):
        comp = event.get('competitions', [{}])[0]
        competitors = comp.get('competitors', [])
        if len(competitors) != 2:
            continue
        
        home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
        away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
        if not home or not away:
            continue
        
        home_name = home.get('team', {}).get('displayName', '')
        away_name = away.get('team', {}).get('displayName', '')
        
        status = comp.get('status', {}).get('type', {}).get('name', '')
        if status.startswith('STATUS_FINAL'):
            continue  # Skip completed games
        
        odds = get_odds(sport, home_name, away_name)
        if odds:
            odds['home_team'] = home_name
            odds['away_team'] = away_name
            odds['game_id'] = event.get('id', '')
            results.append(odds)
    
    return results


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    sport = sys.argv[1] if len(sys.argv) > 1 else 'NBA'
    print(f"\n{'='*70}")
    print(f"  ESPN Odds Engine — {sport} Today's Games")
    print(f"{'='*70}\n")
    
    games = get_todays_odds(sport)
    if not games:
        print("No games found.")
        sys.exit(0)
    
    for g in games:
        away = g['away_team']
        home = g['home_team']
        ml_h = g['moneyline_home']
        ml_a = g['moneyline_away']
        sp_h = g['spread_home']
        sp_a = g['spread_away']
        total = g['total']

        ml_h_str = f"{ml_h:+d}" if ml_h else "N/A"
        ml_a_str = f"{ml_a:+d}" if ml_a else "N/A"
        sp_h_str = f"{sp_h:+.1f}"
        sp_a_str = f"{sp_a:+.1f}"

        print(f"  {away:25s}  ML {ml_a_str:>7s}  Spread {sp_a_str:>6s}  ({g['away_record']}  {g['away_ppg']:.1f}/{g['away_pag']:.1f})")
        print(f"  {home:25s}  ML {ml_h_str:>7s}  Spread {sp_h_str:>6s}  ({g['home_record']}  {g['home_ppg']:.1f}/{g['home_pag']:.1f})")
        print(f"  Total: O/U {total:.1f}  |  Proj: {g['expected_away_score']:.1f} - {g['expected_home_score']:.1f}")
        print()
