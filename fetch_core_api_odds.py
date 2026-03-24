#!/usr/bin/env python3
"""
Fetch betting lines from ESPN Core API (sports.core.api.espn.com).
Uses game IDs already stored in the local DB so it can backfill recent
historical lines instead of relying on the first page of league events.
"""
import sqlite3
import requests
from datetime import datetime
import re
import unicodedata
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

# Map sport to Core API sport/league slugs
SPORT_TO_API_PATH = {
    'NBA': ('basketball', 'nba'),
    'NHL': ('hockey', 'nhl'),
    'NFL': ('football', 'nfl'),
    'MLB': ('baseball', 'mlb'),
    'WNBA': ('basketball', 'wnba'),
    'NCAAB': ('basketball', 'mens-college-basketball'),
    'NCAAF': ('football', 'college-football'),
}


def _normalize_team_key(team_name):
    if not team_name:
        return ''
    txt = unicodedata.normalize('NFKD', str(team_name))
    txt = txt.encode('ascii', 'ignore').decode('ascii')
    txt = txt.lower().replace('&', 'and')
    txt = re.sub(r'[^a-z0-9]+', '', txt)
    alias_map = {
        'utahhockeyclub': 'utahmammoth',
    }
    txt = alias_map.get(txt, txt)
    return txt


def _resolve_espn_event_id_by_matchup(sport, game_day, home_team, away_team):
    """Resolve ESPN event ID by matching date + teams on ESPN scoreboard API."""
    sport_path = SPORT_TO_API_PATH.get(sport)
    if not sport_path or not game_day:
        return None

    try:
        base_dt = datetime.strptime(str(game_day), '%Y-%m-%d')
    except Exception:
        return None

    home_key = _normalize_team_key(home_team)
    away_key = _normalize_team_key(away_team)
    if not home_key or not away_key:
        return None

    sport_slug, league_slug = sport_path
    for day_offset in [0, -1, 1]:
        date_str = (base_dt.fromordinal(base_dt.toordinal() + day_offset)).strftime('%Y%m%d')
        scoreboard_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/"
            f"{league_slug}/scoreboard?dates={date_str}"
        )
        if sport == 'NCAAB':
            scoreboard_url += '&groups=50&limit=357'
        try:
            data = requests.get(scoreboard_url, timeout=10).json()
        except Exception:
            continue

        for event in data.get('events', []):
            competition = event.get('competitions', [{}])[0]
            competitors = competition.get('competitors', [])
            if len(competitors) != 2:
                continue
            home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
            away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
            if not home or not away:
                continue

            def _keys(competitor):
                team = competitor.get('team', {})
                values = {
                    team.get('displayName'),
                    team.get('shortDisplayName'),
                    team.get('name'),
                    team.get('nickname'),
                    team.get('location'),
                    team.get('abbreviation'),
                }
                return {_normalize_team_key(v) for v in values if v}

            if home_key in _keys(home) and away_key in _keys(away):
                event_id = str(event.get('id') or '').strip()
                if event_id:
                    return event_id

    return None

def _to_float(value):
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _fetch_core_odds(sport, event_id):
    sport_path = SPORT_TO_API_PATH.get(sport)
    if not sport_path:
        return None

    sport_slug, league_slug = sport_path
    odds_url = (
        f"http://sports.core.api.espn.com/v2/sports/{sport_slug}/leagues/{league_slug}/"
        f"events/{event_id}/competitions/{event_id}/odds"
    )

    try:
        response = requests.get(odds_url, timeout=10)
        response.raise_for_status()
        odds_data = response.json()
    except Exception:
        return None

    items = odds_data.get('items', [])
    if not items:
        return None

    chosen = None
    for item in items:
        if item.get('spread') is not None or item.get('overUnder') is not None:
            chosen = item
            break
    if chosen is None:
        chosen = items[0]

    spread = _to_float(chosen.get('spread'))
    total = _to_float(chosen.get('overUnder'))
    home_ml = chosen.get('homeTeamOdds', {}).get('moneyLine')
    away_ml = chosen.get('awayTeamOdds', {}).get('moneyLine')

    if spread is None and total is None:
        return None

    return {
        'spread': spread,
        'total': total,
        'home_ml': home_ml,
        'away_ml': away_ml,
    }


def fetch_odds_for_sport(sport, days_back=7, days_ahead=14, max_games=2000):
    """Fetch Core API odds for games in the local DB date window."""
    print(
        f"\n{Fore.CYAN}Fetching {sport} Core API Odds "
        f"(window: -{days_back}d to +{days_ahead}d){Style.RESET_ALL}"
    )

    if sport not in SPORT_TO_API_PATH:
        print(f"  {Fore.RED}No API path mapping for {sport}{Style.RESET_ALL}")
        return 0

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    added = 0
    attempted = 0
    missing_core_event_id = 0
    mapped_event_fallbacks = 0

    try:
        games = cursor.execute("""
            SELECT game_id, date(game_date) AS game_day, home_team_id, away_team_id
            FROM games
            WHERE sport = ?
              AND date(game_date) BETWEEN date('now', ?) AND date('now', ?)
            ORDER BY date(game_date) DESC
            LIMIT ?
        """, (sport, f'-{days_back} day', f'+{days_ahead} day', max_games)).fetchall()

        for game_id, game_day, home_team, away_team in games:
            raw_event_id = str(game_id).split('_')[-1] if game_id else ''
            if not raw_event_id or not raw_event_id.isdigit():
                missing_core_event_id += 1
                continue
            event_candidates = [raw_event_id]
            # Local schedule IDs for NHL are usually not ESPN event IDs.
            # Resolve by matchup/date for reliable odds ingestion.
            if sport == 'NHL' and not raw_event_id.startswith('401'):
                mapped_event_id = _resolve_espn_event_id_by_matchup(
                    sport, game_day, home_team, away_team
                )
                if mapped_event_id:
                    event_candidates = [mapped_event_id] + [
                        e for e in event_candidates if e != mapped_event_id
                    ]
                    mapped_event_fallbacks += 1

            attempted += 1
            odds = None
            for event_id in event_candidates:
                odds = _fetch_core_odds(sport, event_id)
                if odds:
                    break
            if not odds:
                continue

            cursor.execute("""
                INSERT OR REPLACE INTO betting_lines
                (sport, game_id, game_date, home_team, away_team, spread, total, home_moneyline, away_moneyline, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ESPN Core API')
            """, (
                sport,
                game_id,
                game_day,
                home_team,
                away_team,
                odds['spread'],
                odds['total'],
                odds['home_ml'],
                odds['away_ml'],
            ))
            added += 1

    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")

    conn.commit()
    conn.close()

    print(
        f"  {Fore.GREEN}✓ Added {added} lines{Style.RESET_ALL} "
        f"(attempted {attempted}, mapped {mapped_event_fallbacks}, skipped non-ESPN IDs {missing_core_event_id})"
    )
    return added


def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"ESPN Core API Odds Fetcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")

    total = 0
    windows = {
        'NBA': (60, 14),
        'NHL': (60, 14),
        'NFL': (30, 30),
        'NCAAB': (21, 14),
        'NCAAF': (30, 30),
    }
    for sport in ['NBA', 'NHL', 'NFL', 'NCAAB', 'NCAAF']:
        days_back, days_ahead = windows.get(sport, (14, 14))
        total += fetch_odds_for_sport(sport, days_back=days_back, days_ahead=days_ahead)

    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete - {total} betting lines fetched")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
