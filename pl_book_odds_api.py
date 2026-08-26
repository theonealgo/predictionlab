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
import os
import re
import time
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
    'NCAAW': ('basketball', 'womens-college-basketball'),
    'SOCCER': ('soccer', None),

    # Individual sports use scoreboard embedded odds
    'TENNIS': ('tennis', None),
    'UFC': ('mma', None),
    'GOLF': ('golf', None),
}

# Display / DB league name → ESPN core league slug (soccer only).
SOCCER_LEAGUE_ESPN_SLUGS = {
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
    'AFC Asian Cup': 'afc.asian.cup',
}

_SOCCER_LEAGUE_ALIASES = {
    'premier league': 'English Premier League',
    'epl': 'English Premier League',
    'eng.1': 'eng.1',
    'champions league': 'UEFA Champions League',
    'uefa champions league': 'UEFA Champions League',
    'mls': 'Major League Soccer',
    'la liga': 'Spanish LaLiga',
    'laliga': 'Spanish LaLiga',
    'bundesliga': 'German Bundesliga',
    'serie a': 'Italian Serie A',
    'ligue 1': 'French Ligue 1',
    'afc champions league elite': 'AFC Champions League Elite',
    'afc champions league': 'AFC Champions League Elite',
    'acl elite': 'AFC Champions League Elite',
    'afc.champions': 'afc.champions',
}

SOCCER_PROBE_SLUGS = [
    # Tier 1 leagues (have DraftKings odds on ESPN Core API)
    'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1',
    'uefa.champions', 'uefa.europa', 'uefa.europa.conf',
    'usa.1', 'mex.1', 'ned.1', 'por.1', 'afc.champions',
    'conmebol.libertadores', 'concacaf.champions',
    # Tier 2 / lower divisions (odds availability varies)
    'eng.2', 'esp.2', 'ger.2', 'ita.2', 'fra.2',
    'usa.open', 'mex.2',
]

# Catalog display name → existing soccer keys on the same odds API tennis/golf use.
# Only leagues that API actually lists. Cups it does not list (Copa do Brasil,
# Copa Colombia, Copa Chile, …) stay ESPN-only.
SOCCER_ODDS_API_KEYS = {
    'English Premier League': 'soccer_epl',
    'EFL Championship': 'soccer_efl_champ',
    'EFL Cup': 'soccer_england_efl_cup',
    'FA Cup': 'soccer_fa_cup',
    'English League One': 'soccer_england_league1',
    'English League Two': 'soccer_england_league2',
    'Spanish LaLiga': 'soccer_spain_la_liga',
    'Spanish Segunda División': 'soccer_spain_segunda_division',
    'Spanish Copa del Rey': 'soccer_spain_copa_del_rey',
    'German Bundesliga': 'soccer_germany_bundesliga',
    'German 2. Bundesliga': 'soccer_germany_bundesliga2',
    'German Cup': 'soccer_germany_dfb_pokal',
    'Italian Serie A': 'soccer_italy_serie_a',
    'Italian Serie B': 'soccer_italy_serie_b',
    'Coppa Italia': 'soccer_italy_coppa_italia',
    'French Ligue 1': 'soccer_france_ligue_one',
    'French Ligue 2': 'soccer_france_ligue_two',
    'Coupe de France': 'soccer_france_coupe_de_france',
    'Dutch Eredivisie': 'soccer_netherlands_eredivisie',
    'Portuguese Primeira Liga': 'soccer_portugal_primeira_liga',
    'Scottish Premiership': 'soccer_spl',
    'Belgian Pro League': 'soccer_belgium_first_div',
    'Austrian Bundesliga': 'soccer_austria_bundesliga',
    'Danish Superliga': 'soccer_denmark_superliga',
    'Swedish Allsvenskan': 'soccer_sweden_allsvenskan',
    'Norwegian Eliteserien': 'soccer_norway_eliteserien',
    'Greek Super League': 'soccer_greece_super_league',
    'Turkish Super Lig': 'soccer_turkey_super_league',
    'Russian Premier League': 'soccer_russia_premier_league',
    'UEFA Champions League': 'soccer_uefa_champs_league',
    'UEFA Champions League Qualifying': 'soccer_uefa_champs_league_qualification',
    'UEFA Europa League': 'soccer_uefa_europa_league',
    'UEFA Europa Conference League': 'soccer_uefa_europa_conference_league',
    'UEFA Nations League': 'soccer_uefa_nations_league',
    'UEFA European Championship': 'soccer_uefa_european_championship',
    'Major League Soccer': 'soccer_usa_mls',
    'Liga MX': 'soccer_mexico_ligamx',
    'Leagues Cup': 'soccer_concacaf_leagues_cup',
    'Concacaf Gold Cup': 'soccer_concacaf_gold_cup',
    'Argentine Liga Profesional de Fútbol': 'soccer_argentina_primera_division',
    'Brazilian Serie A': 'soccer_brazil_campeonato',
    'Brazilian Serie B': 'soccer_brazil_serie_b',
    'Chilean Primera División': 'soccer_chile_campeonato',
    'Copa Libertadores': 'soccer_conmebol_copa_libertadores',
    'CONMEBOL Sudamericana': 'soccer_conmebol_copa_sudamericana',
    'Copa América': 'soccer_conmebol_copa_america',
    'Chinese Super League': 'soccer_china_superleague',
    'Japanese J.League': 'soccer_japan_j_league',
    'Australian A-League Men': 'soccer_australia_aleague',
    'Saudi Pro League': 'soccer_saudi_arabia_pro_league',
    'FIFA World Cup': 'soccer_fifa_world_cup',
    'FIFA World Cup Qualifiers (UEFA)': 'soccer_fifa_world_cup_qualifiers_europe',
    'FIFA World Cup Qualifiers (CONMEBOL)': 'soccer_fifa_world_cup_qualifiers_south_america',
    'FIFA Club World Cup': 'soccer_fifa_club_world_cup',
    'FIFA Women\'s World Cup': 'soccer_fifa_world_cup_womens',
    'Africa Cup of Nations': 'soccer_africa_cup_of_nations',
}

_ODDS_API_SOCCER_CACHE: dict[str, dict[str, Any]] = {}
_ODDS_API_SOCCER_TTL = 300.0

def build_pl_book_odds():

    if sport == "TENNIS":
        from sports.odds.tennis_odds import build_tennis_odds

        return build_tennis_odds(
            game_id,
            home,
            away,
            game_date,
        )

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


def _soccer_slug_and_event_from_game_id(game_id: str) -> tuple[Optional[str], Optional[str]]:
    """Parse SOCCER_<espn.slug.may_have_underscores>_<eventid>.

    Slugs such as bra.copa_do_brazil and ita.coppa_italia must stay intact.
    Splitting on every '_' used to send Core API `bra.copa` (empty).
    """
    raw = str(game_id or '').strip()
    if not raw:
        return None, None
    if '_' not in raw:
        return None, raw if raw.isdigit() else None
    head, tail = raw.split('_', 1)
    if head.upper() != 'SOCCER':
        return None, tail.split('_')[-1] if tail.split('_')[-1].isdigit() else None
    if tail.isdigit():
        return None, tail
    event = tail.rsplit('_', 1)
    if len(event) == 2 and event[1].isdigit():
        slug = event[0]
        return (slug if slug else None), event[1]
    return (tail if '.' in tail else None), None


def _espn_event_id(game_id: str) -> Optional[str]:
    if not game_id:
        return None
    _slug, event_id = _soccer_slug_and_event_from_game_id(game_id)
    if event_id:
        return event_id
    raw = str(game_id).split('_')[-1]
    return raw if raw.isdigit() else None


def _soccer_slug_from_league_name(league_name: Optional[str]) -> Optional[str]:
    if not league_name:
        return None
    key = str(league_name).strip()
    if not key:
        return None
    low = key.lower()
    if re.match(r'^[a-z][a-z0-9._-]*$', low) and '.' in low:
        return low
    canon = _SOCCER_LEAGUE_ALIASES.get(low, key)
    if re.match(r'^[a-z][a-z0-9._-]*$', str(canon).lower()) and '.' in str(canon):
        return str(canon).lower()
    mapped = SOCCER_LEAGUE_ESPN_SLUGS.get(canon) or SOCCER_LEAGUE_ESPN_SLUGS.get(key)
    if mapped:
        return mapped
    try:
        from soccer_league_catalog import soccer_espn_slug
        return soccer_espn_slug(canon) or soccer_espn_slug(key)
    except Exception:
        return None


def _soccer_league_slugs_to_try(game_id: str, league_name: Optional[str] = None) -> list[str]:
    """Ordered ESPN league slugs for a soccer event (most likely first)."""
    slugs: list[str] = []
    mapped = _soccer_slug_from_league_name(league_name)
    if mapped:
        slugs.append(mapped)
    parsed_slug, _event_id = _soccer_slug_and_event_from_game_id(game_id)
    if parsed_slug and '.' in parsed_slug and parsed_slug not in slugs:
        slugs.append(parsed_slug)
    known = bool(slugs)
    # Prefer the catalog/game_id slug. Do not fan out to EPL/etc. when we
    # already know the competition — that made cup pages stall and miss books.
    if not known:
        for slug in SOCCER_PROBE_SLUGS:
            if slug not in slugs:
                slugs.append(slug)
    return slugs[:2] if known else slugs[:5]



def _core_moneyline(team_odds) -> Optional[int]:
    if not isinstance(team_odds, dict):
        return None
    for key in ('moneyLine', 'moneyline'):
        ml = _to_int_american(team_odds.get(key))
        if ml is not None:
            return ml
        nested = team_odds.get(key)
        if isinstance(nested, dict):
            ml = _to_int_american(nested.get('odds') or nested.get('close') or nested.get('current'))
            if ml is not None:
                return ml
    current = team_odds.get('current') or team_odds.get('close') or {}
    if isinstance(current, dict):
        return _to_int_american(current.get('moneyLine') or current.get('moneyline') or current.get('odds'))
    return None


def _parse_core_item(item: dict) -> Optional[dict[str, Any]]:
    spread = _to_float(item.get('spread'))
    total = _to_float(item.get('overUnder'))
    home_ml = _core_moneyline(item.get('homeTeamOdds') or {})
    away_ml = _core_moneyline(item.get('awayTeamOdds') or {})
    # Soccer cups often post 3-way ML with no AH / total. Keep those rows.
    if spread is None and total is None and home_ml is None and away_ml is None:
        return None
    prov = item.get('provider') or {}
    return {
        'provider': prov.get('name') or 'unknown',
        'provider_id': prov.get('id'),
        'spread': spread,
        'total': total,
        'home_moneyline': home_ml,
        'away_moneyline': away_ml,
        'is_live': 'live' in (prov.get('name') or '').lower(),
    }
def _fetch_scoreboard_competition_odds(
    sport: str,
    event_id: str,
) -> list:
    """
    Fetch embedded ESPN odds for individual sports.
    Tennis/UFC/Golf use different scoreboard paths.
    """

    urls = []

    if sport == "TENNIS":
        urls.extend([
            f"https://site.api.espn.com/apis/site/v2/sports/tennis/atp/summary?event={event_id}",
            f"https://site.api.espn.com/apis/site/v2/sports/tennis/wta/summary?event={event_id}",
        ])

    elif sport == "UFC":
        urls.append(
            f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/summary?event={event_id}"
        )

    elif sport == "GOLF":
        urls.append(
            f"https://site.api.espn.com/apis/site/v2/sports/golf/summary?event={event_id}"
        )

    for url in urls:
        try:
            r = requests.get(url, timeout=5)

            if r.status_code != 200:
                continue

            data = r.json()

            competitions = data.get("competitions") or []

            for comp in competitions:
                odds = comp.get("odds")

                if odds:
                    return odds

        except Exception as exc:
            logger.debug(
                "Individual sport odds fetch failed %s: %s",
                url,
                exc,
            )

    return []
def _fetch_core_odds_payload(
    sport: str,
    event_id: str,
    *,
    game_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> Optional[list]:
    """
    Fetch sportsbook odds.

    ESPN Core API:
        MLB/NBA/NHL/NFL/NCAA/SOCCER
        -> DraftKings provider odds

    ESPN scoreboard competition odds:
        Tennis/UFC/Golf
        -> competition embedded odds
    """

    path = CORE_API_SPORT_PATHS.get(sport)

    if not path:
        return None

    sport_slug, league_slug = path

    # Individual sports do not expose Core odds endpoints.
    # Pull embedded competition odds instead.
    if sport in ("TENNIS", "UFC", "GOLF"):
        return _fetch_scoreboard_competition_odds(
            sport,
            event_id,
        )

    league_slugs = [league_slug] if league_slug else []

    if sport == "SOCCER":
        league_slugs = _soccer_league_slugs_to_try(
            game_id or "",
            league_name,
        )

    for _league_slug in league_slugs:

        if not _league_slug:
            continue

        url = (
            f"https://sports.core.api.espn.com/v2/sports/"
            f"{sport_slug}/leagues/{_league_slug}/"
            f"events/{event_id}/competitions/{event_id}/odds"
        )

        try:
            response = requests.get(
                url,
                timeout=5,
            )

            data = response.json()

        except Exception as exc:
            logger.debug(
                "PL book odds fetch failed %s/%s: %s",
                _league_slug,
                event_id,
                exc,
            )
            continue


        items = (
            data.get("items")
            if isinstance(data, dict)
            else []
        )

        if sport == "NCAAW":
            logger.warning(
                "NCAAW ODDS DEBUG league=%s event=%s items=%s",
                _league_slug,
                event_id,
                len(items) if items else 0,
            )

        if items:
            return items


    return None
def fetch_all_core_providers(
    sport: str,
    event_id: str,
    *,
    include_live: bool = False,
    game_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    """All pregame books ESPN exposes for one event (for accuracy testing)."""
    items = _fetch_core_odds_payload(
        sport, event_id, game_id=game_id, league_name=league_name,
    )
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


def _fetch_core_odds_item(
    sport: str,
    event_id: str,
    *,
    game_id: Optional[str] = None,
    league_name: Optional[str] = None,
) -> Optional[dict]:
    items = _fetch_core_odds_payload(
        sport, event_id, game_id=game_id, league_name=league_name,
    )
    if not items:
        return None

    # Prefer DraftKings (provider id 100) when present
    dk = None
    fallback = None
    for item in items:
        prov = item.get('provider') or {}
        name = (prov.get('name') or '').lower()
        if (
            item.get('spread') is None
            and item.get('overUnder') is None
            and not ((item.get('homeTeamOdds') or {}).get('moneyLine')
                     or (item.get('awayTeamOdds') or {}).get('moneyLine'))
        ):
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


def _odds_api_soccer_key(league_name: Optional[str]) -> Optional[str]:
    if not league_name:
        return None
    key = str(league_name).strip()
    mapped = SOCCER_ODDS_API_KEYS.get(key)
    if mapped:
        return mapped
    try:
        from soccer_league_catalog import _SOCCER_LEAGUE_CANONICAL
        canon = _SOCCER_LEAGUE_CANONICAL.get(key.lower())
        if canon:
            return SOCCER_ODDS_API_KEYS.get(canon)
    except Exception:
        pass
    return None


def _soccer_team_keys_match(a: str, b: str) -> bool:
    ka, kb = _normalize_team_key(a), _normalize_team_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    aliases = {
        'atleticomg': {'atleticomineiro'},
        'atleticomineiro': {'atleticomg'},
        'rbbragantino': {'redbullbragantino', 'bragantino'},
        'redbullbragantino': {'rbbragantino', 'bragantino'},
        'gremio': {'gremiofbpa'},
        'vascodagama': {'vasco'},
        'vasco': {'vascodagama'},
    }
    if kb in aliases.get(ka, ()) or ka in aliases.get(kb, ()):
        return True
    shorter, longer = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return len(shorter) >= 8 and shorter in longer


def _fetch_odds_api_soccer_events(sport_key: str) -> list[dict]:
    now = time.time()
    cached = _ODDS_API_SOCCER_CACHE.get(sport_key)
    if cached and (now - float(cached.get('ts') or 0)) < _ODDS_API_SOCCER_TTL:
        return list(cached.get('events') or [])
    api_key = os.getenv('ODDS_API_KEY') or os.getenv('THEODDS_API_KEY') or ''
    if not api_key:
        # Same default already used by theodds_api.py / sports tennis+golf adapters.
        api_key = '18cfd484126cfef3f271472d619e2319'
    if not api_key:
        return []
    try:
        resp = requests.get(
            f'https://api.the-odds-api.com/v4/sports/{sport_key}/odds',
            params={
                'apiKey': api_key,
                'regions': 'us,uk,eu',
                'markets': 'h2h,spreads,totals',
                'oddsFormat': 'american',
                'dateFormat': 'iso',
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug('soccer odds-api %s status %s', sport_key, resp.status_code)
            _ODDS_API_SOCCER_CACHE[sport_key] = {'ts': now, 'events': []}
            return []
        events = resp.json() if isinstance(resp.json(), list) else []
    except Exception as exc:
        logger.debug('soccer odds-api %s failed: %s', sport_key, exc)
        return []
    _ODDS_API_SOCCER_CACHE[sport_key] = {'ts': now, 'events': events}
    return events


def _pick_odds_api_book(event: dict) -> Optional[dict]:
    books = event.get('bookmakers') or []
    prefer = ('draftkings', 'fanduel', 'betmgm', 'caesars', 'williamhill', 'pinnacle')
    chosen = None
    for bk in books:
        key = (bk.get('key') or bk.get('title') or '').lower()
        if any(p in key for p in prefer):
            chosen = bk
            break
        if chosen is None:
            chosen = bk
    return chosen


def _odds_api_market_outcomes(book: dict, market_key: str) -> list[dict]:
    for mkt in book.get('markets') or []:
        if (mkt.get('key') or '') == market_key:
            return list(mkt.get('outcomes') or [])
    return []


def _build_row_from_odds_api(
    sport: str,
    game_id: str,
    home_team: str,
    away_team: str,
    game_date: Optional[str],
    league_name: Optional[str],
) -> Optional[dict[str, Any]]:
    sport_key = _odds_api_soccer_key(league_name)
    if not sport_key:
        return None
    events = _fetch_odds_api_soccer_events(sport_key)
    if not events:
        return None
    want_date = (game_date or '')[:10]
    match = None
    for ev in events:
        ev_home = ev.get('home_team') or ''
        ev_away = ev.get('away_team') or ''
        home_ok = (
            _soccer_team_keys_match(home_team, ev_home)
            or _soccer_team_keys_match(away_team, ev_away)
        )
        away_ok = (
            _soccer_team_keys_match(away_team, ev_away)
            or _soccer_team_keys_match(home_team, ev_away)
        )
        sides_ok = (
            (_soccer_team_keys_match(home_team, ev_home) and _soccer_team_keys_match(away_team, ev_away))
            or (_soccer_team_keys_match(home_team, ev_away) and _soccer_team_keys_match(away_team, ev_home))
        )
        if not sides_ok:
            continue
        commence = str(ev.get('commence_time') or '')[:10]
        if want_date and commence and abs(
            (datetime.strptime(want_date, '%Y-%m-%d') - datetime.strptime(commence, '%Y-%m-%d')).days
        ) > 1:
            continue
        match = ev
        if home_ok and away_ok:
            break
    if not match:
        return None
    book = _pick_odds_api_book(match)
    if not book:
        return None
    ev_home = match.get('home_team') or ''
    home_is_event_home = _soccer_team_keys_match(home_team, ev_home)
    home_ml = away_ml = spread = total = None
    for out in _odds_api_market_outcomes(book, 'h2h'):
        name = out.get('name') or ''
        price = _to_int_american(out.get('price'))
        if price is None:
            continue
        if str(name).lower() in ('draw', 'tie'):
            continue
        if _soccer_team_keys_match(name, home_team):
            home_ml = price
        elif _soccer_team_keys_match(name, away_team):
            away_ml = price
        elif _soccer_team_keys_match(name, ev_home):
            if home_is_event_home:
                home_ml = price
            else:
                away_ml = price
        else:
            if home_is_event_home:
                away_ml = away_ml if away_ml is not None else price
            else:
                home_ml = home_ml if home_ml is not None else price
    for out in _odds_api_market_outcomes(book, 'spreads'):
        point = _to_float(out.get('point'))
        if point is None:
            continue
        if _soccer_team_keys_match(out.get('name') or '', home_team) or (
            home_is_event_home and _soccer_team_keys_match(out.get('name') or '', ev_home)
        ):
            spread = point
            break
        if _soccer_team_keys_match(out.get('name') or '', away_team):
            spread = -point
            break
    for out in _odds_api_market_outcomes(book, 'totals'):
        if str(out.get('name') or '').lower() == 'over':
            total = _to_float(out.get('point'))
            if total is not None:
                break
    if spread is None and total is None and home_ml is None and away_ml is None:
        return None
    if spread is not None and (home_ml is None or away_ml is None):
        h_fb, a_fb = _ml_from_spread_fallback(spread)
        home_ml = home_ml if home_ml is not None else h_fb
        away_ml = away_ml if away_ml is not None else a_fb
    lines = _format_spread_line(home_team, away_team, spread or 0.0)
    fav = lines.get('favorite_team') if spread is not None else None
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
        'favorite_moneyline': home_ml if fav == home_team else (away_ml if fav == away_team else None),
        'underdog_moneyline': away_ml if fav == home_team else (home_ml if fav == away_team else None),
        'home_spread_line': lines.get('home_spread_line') if spread is not None else None,
        'away_spread_line': lines.get('away_spread_line') if spread is not None else None,
        'provider': 'sportsbook',
        'source': 'pl_book_odds_api',
        'as_of': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }


def build_pl_book_odds(
    sport: str,
    game_id: str,
    home_team: str,
    away_team: str,
    game_date: Optional[str] = None,
    league_name: Optional[str] = None,
    espn_event_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Return book-style odds for one game.

    Response uses American moneyline integers only (no win %).
    favorite_team always matches spread sign.
    """
    event_id = str(espn_event_id) if espn_event_id else _espn_event_id(game_id)
    item = None
    if event_id and str(event_id).isdigit():
        item = _fetch_core_odds_item(
            sport, event_id, game_id=game_id, league_name=league_name,
        )
    if not item and sport == 'SOCCER':
        return _build_row_from_odds_api(
            sport, game_id, home_team, away_team, game_date, league_name,
        )
    if not item:
        return None

    spread = _to_float(item.get('spread'))
    total = _to_float(item.get('overUnder'))
    home_ml = _core_moneyline(item.get('homeTeamOdds') or {})
    away_ml = _core_moneyline(item.get('awayTeamOdds') or {})
    if home_ml is None:
        home_ml = _to_int_american((item.get('homeTeamOdds') or {}).get('moneyLine'))
    if away_ml is None:
        away_ml = _to_int_american((item.get('awayTeamOdds') or {}).get('moneyLine'))
    if spread is None and total is None and home_ml is None and away_ml is None:
        if sport == 'SOCCER':
            return _build_row_from_odds_api(
                sport, game_id, home_team, away_team, game_date, league_name,
            )
        return None

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
            league_name=g.get('league') or g.get('league_name'),
        )
        if row:
            out.append(row)
    return out
