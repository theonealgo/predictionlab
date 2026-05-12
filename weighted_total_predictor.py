#!/usr/bin/env python3
"""
Weighted Average Total System
Calculates projected game totals using last 5 games (non-OT) and trend analysis.

v2 changes:
  - Fetch last 5 completed non-OT games (was 3) for a more stable average
  - Over/under thresholds recalibrated for 10 checks (was 6):
      OVER  when combined_over_count >= 7  (was >= 4)
      UNDER when combined_over_count <= 3  (was <= 2)
  - Optional Vegas baseline blending: projected = 0.75*model + 0.25*vegas
"""

import requests
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional
import logging
import time

logger = logging.getLogger(__name__)

# ── Module-level ESPN request cache (15-min TTL) ───────────────────────────
_WT_CACHE: dict = {}
_WT_TTL = 900  # seconds


def _wt_cached_get(url: str, timeout: int = 3):
    """Cached requests.get with 15-minute TTL."""
    now = time.time()
    entry = _WT_CACHE.get(url)
    if entry and (now - entry['ts']) < _WT_TTL:
        return entry['data']
    try:
        r = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException:
        # Cache the miss briefly so we don't keep retrying a flaky endpoint.
        _WT_CACHE[url] = {'data': None, 'ts': now}
        return None
    if r.status_code == 200:
        data = r.json()
        _WT_CACHE[url] = {'data': data, 'ts': now}
        return data
    _WT_CACHE[url] = {'data': None, 'ts': now}
    return None


def prefetch_recent_scoreboards(sport: str = 'NBA', days: int = 14, max_workers: int = 8):
    """Warm `_WT_CACHE` for the last `days` ESPN scoreboards in parallel.

    Call this once before iterating predictions. Subsequent calls to
    fetch_team_last3_games() then run against an entirely warm cache, turning
    what was a sequential string of HTTP calls into local dict lookups.
    """
    from concurrent.futures import ThreadPoolExecutor

    espn_paths = {
        'NBA':   'basketball/nba',
        'WNBA':  'basketball/wnba',
        'NCAAB': 'basketball/mens-college-basketball',
        'NHL':   'hockey/nhl',
        'NFL':   'football/nfl',
        'NCAAF': 'football/college-football',
        'MLB':   'baseball/mlb',
    }
    path = espn_paths.get(sport)
    if not path:
        return

    today = datetime.now()
    urls = []
    for d in range(1, days + 1):
        date_str = (today - timedelta(days=d)).strftime('%Y%m%d')
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date_str}"
        # Skip URLs we already have in cache & still fresh.
        entry = _WT_CACHE.get(url)
        if entry and (time.time() - entry['ts']) < _WT_TTL:
            continue
        urls.append(url)

    if not urls:
        return

    def _fetch(u):
        try:
            _wt_cached_get(u, timeout=3)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_fetch, urls))


def fetch_team_last3_games(
    team_name: str,
    sport: str = 'NBA',
    max_lookback_days: int = 28,
    n: int = 5,
) -> List[Tuple[int, int]]:
    """
    Fetch last `n` completed non-OT games for a team via ESPN scoreboard.

    Args:
        team_name: Full team name (e.g. "Los Angeles Lakers")
        sport: One of NBA, NHL, NFL, MLB, WNBA, NCAAF, NCAAB
        max_lookback_days: How many days back to search (default 28 to find 5 games)
        n: Number of games to retrieve (default 5)

    Returns:
        List of tuples: [(team_score, opponent_score), ...]
        Returns empty list or partial results if < n games found
    """
    results = []
    today = datetime.now()

    espn_paths = {
        'NBA':   'basketball/nba',
        'WNBA':  'basketball/wnba',
        'NCAAB': 'basketball/mens-college-basketball',
        'NHL':   'hockey/nhl',
        'NFL':   'football/nfl',
        'NCAAF': 'football/college-football',
        'MLB':   'baseball/mlb',
    }
    path = espn_paths.get(sport, 'basketball/nba')

    for d in range(1, max_lookback_days + 1):
        if len(results) >= n:
            break
            
        date = today - timedelta(days=d)
        date_str = date.strftime('%Y%m%d')
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date_str}"
        
        try:
            data = _wt_cached_get(url, timeout=5)
            if data is None:
                continue
            
            for event in data.get('events', []):
                comp = event.get('competitions', [{}])[0]
                status = comp.get('status', {}).get('type', {})
                status_desc = status.get('description', '')
                status_state = status.get('state', '')

                # Exclude games not final yet
                if status_state != 'post':
                    continue

                # Exclude OT/extra games per sport
                desc_upper = (status_desc or '').upper()
                if sport in ['NBA', 'NHL', 'NFL', 'NCAAB', 'NCAAF'] and 'OT' in desc_upper:
                    continue
                if sport == 'NHL' and 'SO' in desc_upper:
                    continue
                if sport == 'MLB' and ('FINAL/' in desc_upper or 'EXTRAS' in desc_upper):
                    continue
                
                comps = comp.get('competitors', [])
                if len(comps) != 2:
                    continue
                    
                home = next((c for c in comps if c.get('homeAway') == 'home'), None)
                away = next((c for c in comps if c.get('homeAway') == 'away'), None)
                
                if not home or not away:
                    continue
                    
                home_name = home.get('team', {}).get('displayName')
                away_name = away.get('team', {}).get('displayName')
                
                try:
                    home_pts = int(home.get('score') or 0)
                    away_pts = int(away.get('score') or 0)
                except (ValueError, TypeError):
                    continue
                
                # Check if this game involves our team
                if team_name == home_name:
                    results.append((home_pts, away_pts))
                elif team_name == away_name:
                    results.append((away_pts, home_pts))
                
                if len(results) >= n:
                    return results[:n]

        except Exception as e:
            logger.debug(f"Error fetching games for date {date_str}: {e}")
            continue

    return results[:n]


def calculate_weighted_average_total(
    teamA_name: str,
    teamB_name: str,
    vegas_total: Optional[float] = None,
    max_lookback_days: int = 28,
    sport: str = 'NBA',
    n_games: int = 5,
) -> Dict:
    """
    Calculate projected game total using rolling average of last `n_games` games.

    v2 thresholds (10 checks total for n_games=5):
      OVER  if combined_over_count >= 7  (~70%+ of recent games went over)
      UNDER if combined_over_count <= 3  (~30%- of recent games went over)
      NO BET otherwise (4-6 range = mixed signal)

    Args:
        teamA_name:       Full name of team A (e.g. "Los Angeles Lakers")
        teamB_name:       Full name of team B (e.g. "Golden State Warriors")
        vegas_total:      Current Vegas O/U line (optional; blended 75/25 model/Vegas)
        max_lookback_days: Days to look back for games
        sport:            Sport key (NBA, NHL, NFL, MLB, WNBA, NCAAF, NCAAB)
        n_games:          Number of recent games to use (default 5)

    Returns:
        Dictionary with projected_total, over counts, recommended_bet, and metadata.
    """
    # Fetch last n games for both teams
    teamA_games = fetch_team_last3_games(teamA_name, sport, max_lookback_days, n=n_games)
    teamB_games = fetch_team_last3_games(teamB_name, sport, max_lookback_days, n=n_games)

    min_games = max(3, n_games - 2)  # need at least 3 games (or n-2 for flexibility)

    if len(teamA_games) < min_games:
        return {
            "error": f"Insufficient data for {teamA_name} (found {len(teamA_games)}/{n_games} games)",
            "projected_total": None, "teamA_over_count": 0, "teamB_over_count": 0,
            "combined_over_count": 0, "recommended_bet": "NO BET",
            "vegas_total": vegas_total, "difference_from_vegas": None,
            "teamA_last3_games": teamA_games, "teamB_last3_games": teamB_games,
            "teamA_avg": None, "teamB_avg": None,
        }

    if len(teamB_games) < min_games:
        return {
            "error": f"Insufficient data for {teamB_name} (found {len(teamB_games)}/{n_games} games)",
            "projected_total": None, "teamA_over_count": 0, "teamB_over_count": 0,
            "combined_over_count": 0, "recommended_bet": "NO BET",
            "vegas_total": vegas_total, "difference_from_vegas": None,
            "teamA_last3_games": teamA_games, "teamB_last3_games": teamB_games,
            "teamA_avg": None, "teamB_avg": None,
        }

    # ── Compute rolling averages (team score + opponent score per game) ────────
    def _team_avg(games):
        n = len(games)
        scoring_avg = sum(s for s, _ in games) / n
        opp_avg     = sum(o for _, o in games) / n
        return (scoring_avg + opp_avg) / 2   # game-pace proxy

    teamA_avg = _team_avg(teamA_games)
    teamB_avg = _team_avg(teamB_games)

    # Raw projected total (sum of pace proxies)
    raw_total = teamA_avg + teamB_avg

    # ── Vegas baseline blending ───────────────────────────────────────
    if vegas_total is not None:
        projected_total = 0.75 * raw_total + 0.25 * float(vegas_total)
    else:
        projected_total = raw_total

    # Use 6.5 as default comparison line for NHL when no Vegas line is available
    comparison_total = projected_total
    if sport == 'NHL' and vegas_total is None:
        comparison_total = 6.5

    # ── Trend check: how many of each team's last n games went over projected ─
    teamA_over_count = sum(
        1 for s, o in teamA_games if (s + o) > comparison_total
    )
    teamB_over_count = sum(
        1 for s, o in teamB_games if (s + o) > comparison_total
    )
    combined_over_count = teamA_over_count + teamB_over_count
    max_checks = len(teamA_games) + len(teamB_games)  # e.g. 10 for n_games=5

    # ── Recommendation logic (thresholds recalibrated for 10 checks) ────────
    over_threshold  = max(4, round(max_checks * 0.70))  # ≥70% = OVER
    under_threshold = max(2, round(max_checks * 0.30))  # ≤30% = UNDER
    if combined_over_count >= over_threshold:
        recommended_bet = "OVER"
    elif combined_over_count <= under_threshold:
        recommended_bet = "UNDER"
    else:
        recommended_bet = "NO BET"

    difference_from_vegas = (
        round(projected_total - vegas_total, 1)
        if vegas_total is not None else None
    )

    return {
        "projected_total":     round(projected_total, 1),
        "teamA_over_count":    teamA_over_count,
        "teamB_over_count":    teamB_over_count,
        "combined_over_count": combined_over_count,
        "recommended_bet":     recommended_bet,
        "vegas_total":         vegas_total,
        "difference_from_vegas": difference_from_vegas,
        "teamA_last3_games":   teamA_games,
        "teamB_last3_games":   teamB_games,
        "teamA_avg":           round(teamA_avg, 1),
        "teamB_avg":           round(teamB_avg, 1),
        "error":               None,
    }


def compute_team_avg_projection(
    home_team: str,
    away_team: str,
    sport: str = 'NBA',
    xsharp_total: Optional[float] = None,
    xsharp_spread: Optional[float] = None,
    n_games: int = 3,
    max_lookback_days: int = 28,
) -> Optional[Dict]:
    """Per-team last-N projection used for "Our Spread" / "Our Total".

    Algorithm (per the original spec):
      1. Pull each team's last `n_games` completed non-OT games via ESPN.
      2. teamA_avg = mean of that team's OWN scores across those games (pure
         scoring average — NOT a pace proxy).
      3. projected_total  = home_avg + away_avg
         projected_spread = home_avg - away_avg
            >0  → home favored by that much
            <0  → away favored by abs(value)
      4. Trend records vs the CURRENT XSharp lines for tonight's game:
            total_record   = "X-Y Over" — count of the 6 games whose total
                             went over `xsharp_total`.
            spread_record  = "X-Y ATS"  — count of the 6 games where the team
                             covered THEIR side of `xsharp_spread`. The home
                             team is on `xsharp_spread`'s home side; the away
                             team is on the inverted side.

    Returns None if either team has fewer than `n_games` recent games.
    """
    home_games = fetch_team_last3_games(home_team, sport, max_lookback_days, n=n_games)
    away_games = fetch_team_last3_games(away_team, sport, max_lookback_days, n=n_games)

    if len(home_games) < n_games or len(away_games) < n_games:
        return None

    # ── Pure team scoring average (not pace proxy) ─────────────────────────
    home_avg = sum(s for s, _ in home_games) / len(home_games)
    away_avg = sum(s for s, _ in away_games) / len(away_games)

    # Round to nearest 0.5, matching how the efficiency path (and books) display.
    def _half_round(v):
        return round(v * 2) / 2.0

    projected_total  = _half_round(home_avg + away_avg)
    projected_spread = _half_round(home_avg - away_avg)  # >0 home favored

    # ── Trend record vs CURRENT XSharp total ──────────────────────────────
    total_over = total_under = 0
    if xsharp_total is not None:
        for s, o in (home_games + away_games):
            game_total = s + o
            if game_total > float(xsharp_total):
                total_over += 1
            elif game_total < float(xsharp_total):
                total_under += 1
            # exact push: neither over nor under

    # ── Trend record vs CURRENT XSharp spread ─────────────────────────────
    # Convention: xsharp_spread > 0 ⇒ home favored by that amount.
    # Home team covers in a past game when home_margin > xsharp_spread.
    # Away team covers in a past game when away_margin > -xsharp_spread.
    spread_covers = spread_no_covers = 0
    if xsharp_spread is not None:
        xs = float(xsharp_spread)
        for s, o in home_games:
            margin = s - o  # home team's margin in their own past game
            if margin > xs:
                spread_covers += 1
            elif margin < xs:
                spread_no_covers += 1
        for s, o in away_games:
            margin = s - o  # away team's margin in their own past game
            if margin > -xs:
                spread_covers += 1
            elif margin < -xs:
                spread_no_covers += 1

    return {
        'home_avg':         round(home_avg, 1),
        'away_avg':         round(away_avg, 1),
        'projected_total':  round(projected_total, 1),
        'projected_spread': round(projected_spread, 1),
        'home_games':       home_games,
        'away_games':       away_games,
        'games_used':       len(home_games) + len(away_games),
        'total_record':     (total_over, total_under),
        'spread_record':    (spread_covers, spread_no_covers),
    }


if __name__ == "__main__":
    # Test the function
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python weighted_total_predictor.py 'Team A Name' 'Team B Name' [vegas_total]")
        sys.exit(1)
    
    teamA = sys.argv[1]
    teamB = sys.argv[2]
    vegas = float(sys.argv[3]) if len(sys.argv) > 3 else None
    
    result = calculate_weighted_average_total(teamA, teamB, vegas, sport='NBA')
    
    print("\n" + "="*60)
    print(f"Weighted Average Total Analysis")
    print("="*60)
    print(f"\n{teamA} vs {teamB}")
    
    if result.get('error'):
        print(f"\n❌ Error: {result['error']}")
    else:
        print(f"\n📊 Team A Average (last 3 games): {result['teamA_avg']}")
        print(f"📊 Team B Average (last 3 games): {result['teamB_avg']}")
        print(f"\n🎯 Projected Total: {result['projected_total']}")
        
        if result['vegas_total']:
            print(f"💰 Vegas Total: {result['vegas_total']}")
            print(f"📈 Difference: {result['difference_from_vegas']:+.1f}")
        
        print(f"\n📈 Over Count (Team A): {result['teamA_over_count']}/3")
        print(f"📈 Over Count (Team B): {result['teamB_over_count']}/3")
        print(f"📈 Combined Over Count: {result['combined_over_count']}/6")
        
        print(f"\n✅ Recommended Bet: {result['recommended_bet']}")
    
    print("\n" + "="*60)
