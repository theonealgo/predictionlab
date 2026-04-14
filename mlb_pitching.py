#!/usr/bin/env python3
"""
MLB Pitching Enhancement Layer
===============================
Fetches probable starting pitcher data from ESPN and computes
pitching-based win probability adjustments for MLB predictions.

This module is MLB-ONLY and does not affect any other sport.

Usage:
    from mlb_pitching import get_mlb_pitching_adjustment
    adj = get_mlb_pitching_adjustment(home_team, away_team)
    # adj = {'home_sp_era': 3.25, 'away_sp_era': 4.50, 'pitching_prob': 0.58, ...}
"""

import requests
import logging
import time
import math

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_PITCHING_CACHE = {}
_CACHE_TTL = 900  # 15 minutes

# ── League average ERA for normalization ──────────────────────────────────────
LEAGUE_AVG_ERA = 4.10  # 2025 MLB average
LEAGUE_AVG_WHIP = 1.28


def _fetch_todays_probables():
    """Fetch all probable starters for today's MLB games from ESPN scoreboard."""
    cache_key = 'mlb_probables'
    cached = _PITCHING_CACHE.get(cache_key)
    if cached and (time.time() - cached['ts']) < _CACHE_TTL:
        return cached['data']

    try:
        r = requests.get(
            'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
            timeout=8
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.debug(f"[MLB pitching] Scoreboard fetch failed: {e}")
        return {}

    # Build lookup: {team_name: {era, whip, name, wins, losses, ...}}
    probables = {}
    for event in data.get('events', []):
        comp = event.get('competitions', [{}])[0]
        for competitor in comp.get('competitors', []):
            team_name = competitor.get('team', {}).get('displayName', '')
            if not team_name:
                continue
            for prob in competitor.get('probables', []):
                athlete = prob.get('athlete', {})
                stats_list = prob.get('statistics', [])
                record = prob.get('record', '')

                # Extract stats into dict
                stats = {}
                for s in stats_list:
                    name = s.get('name', '')
                    val = s.get('displayValue', '')
                    try:
                        stats[name] = float(val)
                    except (ValueError, TypeError):
                        stats[name] = val

                era = stats.get('ERA')
                try:
                    era = float(era) if era is not None else None
                except (ValueError, TypeError):
                    era = None

                pitcher_info = {
                    'name': athlete.get('fullName') or athlete.get('displayName') or 'Unknown',
                    'id': athlete.get('id'),
                    'era': era,
                    'wins': stats.get('wins', 0),
                    'losses': stats.get('losses', 0),
                    'record': record,
                }
                probables[team_name] = pitcher_info

    _PITCHING_CACHE[cache_key] = {'ts': time.time(), 'data': probables}
    logger.info(f"[MLB pitching] Fetched {len(probables)} probable starters")
    return probables


def _era_to_quality(era):
    """Convert ERA to a quality score (0.0 = terrible, 1.0 = elite).
    
    Scale:
        ERA 1.50 or below → 1.0 (elite)
        ERA 3.00 → 0.75
        ERA 4.10 (league avg) → 0.50
        ERA 5.50 → 0.25
        ERA 7.00+ → 0.0 (terrible)
    """
    if era is None:
        return 0.5  # Unknown pitcher → league average
    # Linear interpolation between anchor points
    era = max(1.0, min(8.0, era))
    # Inverted: lower ERA = higher quality
    quality = 1.0 - (era - 1.0) / 7.0
    return max(0.0, min(1.0, quality))


def _pitching_to_win_prob(home_quality, away_quality):
    """Convert pitcher quality differential to win probability.
    
    Uses logistic function centered at 0.5.
    Quality differential of 0.25 (one solid starter vs one bad) → ~62% win prob.
    """
    diff = home_quality - away_quality  # positive = home pitcher is better
    # Logistic with scale factor 3.0 (steeper curve for pitching impact)
    prob = 1.0 / (1.0 + math.exp(-diff * 3.0))
    return max(0.15, min(0.85, prob))


def get_mlb_pitching_adjustment(home_team, away_team):
    """Get pitching-based win probability adjustment for an MLB game.
    
    Returns dict with:
        - home_sp_name, away_sp_name: pitcher names
        - home_sp_era, away_sp_era: pitcher ERAs
        - home_quality, away_quality: 0-1 quality scores
        - pitching_prob: pitching-only home win probability (0-1)
        - has_pitching_data: True if at least one pitcher found
    
    If no pitching data available, returns neutral (0.5) probability.
    """
    probables = _fetch_todays_probables()

    home_sp = probables.get(home_team, {})
    away_sp = probables.get(away_team, {})

    home_era = home_sp.get('era')
    away_era = away_sp.get('era')

    home_quality = _era_to_quality(home_era)
    away_quality = _era_to_quality(away_era)

    pitching_prob = _pitching_to_win_prob(home_quality, away_quality)

    has_data = bool(home_sp) or bool(away_sp)

    return {
        'home_sp_name': home_sp.get('name', 'TBD'),
        'away_sp_name': away_sp.get('name', 'TBD'),
        'home_sp_era': home_era,
        'away_sp_era': away_era,
        'home_quality': round(home_quality, 3),
        'away_quality': round(away_quality, 3),
        'pitching_prob': pitching_prob,
        'has_pitching_data': has_data,
    }


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    probables = _fetch_todays_probables()
    print(f"\nToday's probable starters ({len(probables)} teams):")
    for team, info in sorted(probables.items()):
        era_str = f"{info['era']:.2f}" if info.get('era') is not None else "N/A"
        q = _era_to_quality(info.get('era'))
        print(f"  {team:30s}  {info['name']:25s}  ERA: {era_str:>5s}  Quality: {q:.2f}")
