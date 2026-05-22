"""
PL Book Odds API — market lines as close to sportsbooks as possible.

Source: ESPN Core API (provider DraftKings when available). Does not use PL model
probabilities or XSharp for pricing.

Sign convention (matches ESPN/DraftKings):
  spread < 0  → home team favored by |spread| points
  spread > 0  → away team favored
  moneyline   → American odds integers (+170 / -205), never win %
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

CORE_API_SPORT_PATHS = {
    'NBA': ('basketball', 'nba'),
    'WNBA': ('basketball', 'wnba'),
    'NHL': ('hockey', 'nhl'),
    'NFL': ('football', 'nfl'),
    'MLB': ('baseball', 'mlb'),
    'NCAAB': ('basketball', 'mens-college-basketball'),
    'NCAAF': ('football', 'college-football'),
}


def _normalize_team_key(team_name: str) -> str:
    if not team_name:
        return ''
    txt = unicodedata.normalize('NFKD', str(team_name))
    txt = txt.encode('ascii', 'ignore').decode('ascii')
    txt = txt.lower().replace('&', 'and')
    txt = re.sub(r'[^a-z0-9]+', '', txt)
    return txt


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int_american(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _espn_event_id(game_id: str) -> Optional[str]:
    if not game_id:
        return None
    raw = str(game_id).split('_')[-1]
    return raw if raw.isdigit() else None


def _fetch_core_odds_payload(sport: str, event_id: str) -> Optional[list]:
    path = CORE_API_SPORT_PATHS.get(sport)
    if not path:
        return None
    sport_slug, league_slug = path
    url = (
        f"https://sports.core.api.espn.com/v2/sports/{sport_slug}/leagues/{league_slug}/"
        f"events/{event_id}/competitions/{event_id}/odds"
    )
    try:
        data = requests.get(url, timeout=10).json()
    except Exception as exc:
        logger.debug('PL book odds fetch failed %s: %s', event_id, exc)
        return None
    items = data.get('items') if isinstance(data, dict) else []
    return items or None


def _parse_core_item(item: dict) -> Optional[dict[str, Any]]:
    spread = _to_float(item.get('spread'))
    total = _to_float(item.get('overUnder'))
    if spread is None and total is None:
        return None
    prov = item.get('provider') or {}
    return {
        'provider': prov.get('name') or 'unknown',
        'provider_id': prov.get('id'),
        'spread': spread,
        'total': total,
        'home_moneyline': _to_int_american((item.get('homeTeamOdds') or {}).get('moneyLine')),
        'away_moneyline': _to_int_american((item.get('awayTeamOdds') or {}).get('moneyLine')),
        'is_live': 'live' in (prov.get('name') or '').lower(),
    }


def fetch_all_core_providers(
    sport: str,
    event_id: str,
    *,
    include_live: bool = False,
) -> list[dict[str, Any]]:
    """All pregame books ESPN exposes for one event (for accuracy testing)."""
    items = _fetch_core_odds_payload(sport, event_id)
    if not items:
        return []
    out = []
    for item in items:
        row = _parse_core_item(item)
        if not row:
            continue
        if row.get('is_live') and not include_live:
            continue
        out.append(row)
    return out


def _fetch_core_odds_item(sport: str, event_id: str) -> Optional[dict]:
    items = _fetch_core_odds_payload(sport, event_id)
    if not items:
        return None

    # Prefer DraftKings (provider id 100) when present
    dk = None
    fallback = None
    for item in items:
        prov = item.get('provider') or {}
        name = (prov.get('name') or '').lower()
        if item.get('spread') is None and item.get('overUnder') is None:
            continue
        if 'live' in name and dk is None and fallback is None:
            continue
        if name == 'draftkings' or str(prov.get('id')) == '100':
            dk = item
            break
        if fallback is None and 'live' not in name:
            fallback = item
    return dk or fallback


def diff_book_lines(a: dict, b: dict) -> dict[str, Optional[float]]:
    """Point/line deltas between two home-centric book rows."""

    def _d(x, y):
        if x is None or y is None:
            return None
        return round(float(x) - float(y), 2)

    return {
        'spread_delta': _d(a.get('spread'), b.get('spread')),
        'total_delta': _d(a.get('total'), b.get('total')),
        'home_ml_delta': _d(a.get('home_moneyline'), b.get('home_moneyline')),
        'away_ml_delta': _d(a.get('away_moneyline'), b.get('away_moneyline')),
    }


def _spread_favorite_team(home_team: str, away_team: str, spread: float) -> str:
    """Return team_id/name of the favorite from home-centric spread."""
    if spread < 0:
        return home_team
    if spread > 0:
        return away_team
    return ''


def _format_spread_line(home_team: str, away_team: str, spread: float) -> dict:
    """DraftKings-style spread strings for both sides."""
    if spread is None or abs(spread) < 0.01:
        return {
            'favorite_team': None,
            'home_spread_line': 'PK',
            'away_spread_line': 'PK',
        }
    fav = _spread_favorite_team(home_team, away_team, spread)
    mag = abs(spread)
    if fav == home_team:
        return {
            'favorite_team': home_team,
            'home_spread_line': f'-{mag:g}',
            'away_spread_line': f'+{mag:g}',
        }
    return {
        'favorite_team': away_team,
        'home_spread_line': f'+{mag:g}',
        'away_spread_line': f'-{mag:g}',
    }


def _ml_from_spread_fallback(spread: float, vig: float = 0.045) -> tuple[Optional[int], Optional[int]]:
    """Rough American ML from spread when book ML missing (NBA ~1 pt ≈ 3% win prob)."""
    try:
        s = float(spread)
    except (TypeError, ValueError):
        return None, None
    # Home-centric: negative spread → home favored
    home_p = 0.5 - (s * 0.03)
    home_p = max(0.05, min(0.95, home_p))
    away_p = 1.0 - home_p
    total = home_p + away_p
    home_p /= total
    away_p /= total
    vf = 1.0 + vig
    home_p = min(home_p * vf, 0.99)
    away_p = min(away_p * vf, 0.99)

    def _p_to_am(p):
        if p >= 0.5:
            return -int(round((p / (1 - p)) * 100))
        return int(round(((1 - p) / p) * 100))

    return _p_to_am(home_p), _p_to_am(away_p)


def build_pl_book_odds(
    sport: str,
    game_id: str,
    home_team: str,
    away_team: str,
    game_date: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Return book-style odds for one game.

    Response uses American moneyline integers only (no win %).
    favorite_team always matches spread sign.
    """
    event_id = _espn_event_id(game_id)
    if not event_id:
        return None

    item = _fetch_core_odds_item(sport, event_id)
    if not item:
        return None

    spread = _to_float(item.get('spread'))
    total = _to_float(item.get('overUnder'))
    if spread is None and total is None:
        return None

    home_ml = _to_int_american((item.get('homeTeamOdds') or {}).get('moneyLine'))
    away_ml = _to_int_american((item.get('awayTeamOdds') or {}).get('moneyLine'))

    if spread is not None and (home_ml is None or away_ml is None):
        h_fb, a_fb = _ml_from_spread_fallback(spread)
        home_ml = home_ml if home_ml is not None else h_fb
        away_ml = away_ml if away_ml is not None else a_fb

    lines = _format_spread_line(home_team, away_team, spread or 0.0)
    fav = lines.get('favorite_team')
    fav_ml = None
    dog_ml = None
    if fav == home_team:
        fav_ml, dog_ml = home_ml, away_ml
    elif fav == away_team:
        fav_ml, dog_ml = away_ml, home_ml

    provider = (item.get('provider') or {}).get('name') or 'ESPN Core API'

    return {
        'sport': sport,
        'game_id': game_id,
        'game_date': game_date,
        'home_team': home_team,
        'away_team': away_team,
        'spread': spread,
        'total': total,
        'home_moneyline': home_ml,
        'away_moneyline': away_ml,
        'favorite_team': fav,
        'favorite_moneyline': fav_ml,
        'underdog_moneyline': dog_ml,
        'home_spread_line': lines.get('home_spread_line'),
        'away_spread_line': lines.get('away_spread_line'),
        'spread_price_home': _to_int_american((item.get('homeTeamOdds') or {}).get('spreadOdds')),
        'spread_price_away': _to_int_american((item.get('awayTeamOdds') or {}).get('spreadOdds')),
        'total_over_price': _to_int_american(item.get('overOdds')),
        'total_under_price': _to_int_american(item.get('underOdds')),
        'provider': provider,
        'source': 'pl_book_odds_api',
        'as_of': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def fetch_pl_book_odds_for_date(sport: str, game_date: str, games: list[dict]) -> list[dict]:
    """Build book odds for a list of {game_id, home_team_id, away_team_id, game_date}."""
    out = []
    for g in games:
        row = build_pl_book_odds(
            sport,
            g.get('game_id', ''),
            g.get('home_team_id', ''),
            g.get('away_team_id', ''),
            g.get('game_date') or game_date,
        )
        if row:
            out.append(row)
    return out
