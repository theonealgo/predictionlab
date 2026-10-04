"""Shared ESPN fetch helpers for individual-sport modules (Tennis, UFC, Golf)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

INDIVIDUAL_SPORTS = frozenset({'TENNIS', 'UFC', 'GOLF'})

_ELO_K: dict[str, int] = {'TENNIS': 32, 'UFC': 40, 'GOLF': 20}

_SPORT_MODULES: dict[str, tuple] = {}

# Default lookback / forward when callers omit explicit windows.
_DEFAULT_WINDOWS: dict[str, tuple[int, int]] = {
    'UFC': (7, 60),
    'TENNIS': (3, 21),
    'GOLF': (7, 60),
}


def main():
    import sys
    running = sys.modules.get('__main__')
    if running and hasattr(running, 'get_db_connection') and hasattr(running, 'app'):
        return running
    import NHL77FINAL as m
    return m


def _competitor_name(comp: dict) -> str:
    athlete = comp.get('athlete') or {}
    for src in (athlete, comp):
        for key in ('displayName', 'fullName', 'shortName', 'name'):
            val = src.get(key)
            if val and str(val).strip() and str(val).strip().upper() != 'TBD':
                return str(val).strip()
    team = comp.get('team') or {}
    for key in ('displayName', 'name', 'abbreviation'):
        val = team.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return 'TBD'


def _athlete_id(comp: dict) -> str | None:
    athlete = comp.get('athlete') or {}
    for src in (athlete, comp.get('team') or {}, comp):
        val = src.get('id') if isinstance(src, dict) else None
        if val:
            return str(val)
    return None


def _register_athlete_identity(m, sport: str, name: str, comp: dict) -> None:
    athlete_id = _athlete_id(comp)
    if not athlete_id:
        return
    register = getattr(m, '_register_individual_athlete_logo', None)
    if callable(register):
        register(sport, name, athlete_id)


def _ordered_pair(competitors: list[dict]) -> tuple[dict, dict] | None:
    """Return (home, away) competitors using ESPN order when present."""
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
    if home and away:
        return home, away
    with_order = [c for c in competitors if c.get('order') is not None]
    if len(with_order) >= 2:
        with_order.sort(key=lambda c: int(c.get('order') or 0))
        return with_order[0], with_order[1]
    return competitors[0], competitors[1]


def _event_datetime(event: dict, comp: dict | None = None) -> str:
    for source in (
        (comp or {}).get('date'),
        (comp or {}).get('startDate'),
        event.get('date'),
    ):
        if source:
            return source
    return ''


def _is_final_status(status_name: str) -> bool:

    if not status_name:

        return False

    status = str(status_name).upper()

    return (

        status in (

            'STATUS_FINAL',

            'STATUS_FINAL_OT',

            'STATUS_FULL_TIME',

            'STATUS_FINAL_PEN',

        )

        or 'FINAL' in status

        or 'COMPLETE' in status

    )


def _sets_won(comp: dict) -> int | None:
    lines = comp.get('linescores') or []
    if not lines:
        return None
    if not any('winner' in ls or ls.get('value') is not None for ls in lines):
        return None
    return sum(1 for ls in lines if ls.get('winner'))


def _parse_match_scores(status_name: str, p1: dict, p2: dict) -> tuple[int | None, int | None]:
    """Return scores only for finished matches; never default missing values to 0."""
    if not _is_final_status(status_name):
        return None, None
    s1, s2 = p1.get('score'), p2.get('score')
    if s1 is not None and s2 is not None:
        try:
            return int(s1), int(s2)
        except (TypeError, ValueError):
            pass
    w1, w2 = _sets_won(p1), _sets_won(p2)
    if w1 is not None and w2 is not None and (w1 + w2) > 0:
        return w1, w2
    if p1.get('winner') is True:
        return 1, 0
    if p2.get('winner') is True:
        return 0, 1
    return None, None


def _fetch_scoreboard_events(
    m,
    espn_sport: str,
    espn_league: str,
    *,
    start_str: str | None = None,
    end_str: str | None = None,
) -> list[dict]:
    base = (
        f"https://site.api.espn.com/apis/site/v2/sports/{espn_sport}/"
        f"{espn_league}/scoreboard"
    )
    seen_event_ids: set[str] = set()
    events: list[dict] = []
    urls = [f"{base}?dates={start_str}-{end_str}&limit=200"] if start_str and end_str else []
    urls.append(f"{base}?limit=200")
    for idx, url in enumerate(urls):
        try:
            data = m._cached_get(url)
        except Exception as exc:
            m.logger.warning(f"Scoreboard fetch failed ({espn_sport}/{espn_league}): {exc}")
            continue
        batch = data.get('events', []) if isinstance(data, dict) else []
        for event in batch:
            eid = str(event.get('id', ''))
            if eid and eid in seen_event_ids:
                continue
            if eid:
                seen_event_ids.add(eid)
            events.append(event)
        if idx == 0 and start_str and end_str and events:
            break
    return events


def fetch_espn_api_games(
    sport: str,
    espn_configs: list[tuple[str, str, bool]],
    *,
    tennis_groupings: bool = False,
    days_back: int | None = None,
    days_forward: int | None = None,
) -> list[dict]:
    """ESPN scoreboard for athlete matchups (not team sports)."""
    m = main()
    _back, _forward = _DEFAULT_WINDOWS.get(sport, (3, 14))
    days_back = days_back if days_back is not None else _back
    days_forward = days_forward if days_forward is not None else _forward
    start_str = (datetime.now() - timedelta(days=days_back)).strftime('%Y%m%d')
    end_str = (datetime.now() + timedelta(days=days_forward)).strftime('%Y%m%d')
    api_games: list[dict] = []
    seen_keys: set[tuple] = set()
    for espn_sport, espn_league, is_tournament in espn_configs:
        try:
            events = _fetch_scoreboard_events(
                m, espn_sport, espn_league, start_str=start_str, end_str=end_str,
            )
            league_label = (
                espn_league.upper()
                if tennis_groupings
                else (m.SPORTS.get(sport, {}) or {}).get('name', sport)
            )
            for event in events:
                competitions = event.get('competitions') or []
                if tennis_groupings and not competitions:
                    for grp in (event.get('groupings') or []):
                        competitions.extend(grp.get('competitions') or [])
                if is_tournament:
                    comp = competitions[0] if competitions else {}
                    field = comp.get('competitors') or []
                    _raw_dt = _event_datetime(event, comp)
                    game_date = m._espn_event_date_to_local(_raw_dt) or datetime.now().strftime('%Y-%m-%d')
                    tournament = event.get('name', '') or league_label
                    status_name = (
                        (comp.get('status') or event.get('status') or {})
                        .get('type', {})
                        .get('name', 'scheduled')
                    )
                    is_final = _is_final_status(status_name)
                    for i in range(0, len(field) - 1, 2):
                        p1, p2 = field[i], field[i + 1]
                        home_name = _competitor_name(p1)
                        away_name = _competitor_name(p2)
                        if not home_name or not away_name or home_name == 'TBD' or away_name == 'TBD':
                            continue
                        _register_athlete_identity(m, sport, home_name, p1)
                        _register_athlete_identity(m, sport, away_name, p2)
                        home_score, away_score = None, None
                        if is_final:
                            try:
                                s1 = float(p1.get('score'))
                                s2 = float(p2.get('score'))
                            except (TypeError, ValueError):
                                s1 = s2 = None
                            if s1 is not None and s2 is not None and s1 != s2:
                                # Lower total is better in golf — convert to winner=1 / loser=0.
                                if s1 < s2:
                                    home_score, away_score = 1, 0
                                else:
                                    home_score, away_score = 0, 1
                        key = (game_date, home_name, away_name)
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        if sport in ("TENNIS", "UFC", "GOLF"):
                            game_id = str(event.get("id", ""))
                        else:
                            game_id = f"{sport}_{event.get('id', '')}_pair{i}"

                        api_games.append({
                            'game_id': game_id,
                            'home_team_id': home_name,
                            'away_team_id': away_name,
                            'home_athlete_id': _athlete_id(p1),
                            'away_athlete_id': _athlete_id(p2),
                            'game_date': game_date,
                            'event_date': _raw_dt or None,
                            'home_score': home_score,
                            'away_score': away_score,
                            'league': tournament,
                        })   
                    continue
                for comp in competitions:
                    competitors = comp.get('competitors') or []
                    pair = _ordered_pair(competitors)
                    if not pair:
                        continue
                    p1, p2 = pair
                    home_name = _competitor_name(p1)
                    away_name = _competitor_name(p2)
                    if not home_name or not away_name or home_name == 'TBD' or away_name == 'TBD':
                        continue
                    _register_athlete_identity(m, sport, home_name, p1)
                    _register_athlete_identity(m, sport, away_name, p2)
                    _raw_dt = _event_datetime(event, comp)
                    game_date = m._espn_event_date_to_local(_raw_dt) or datetime.now().strftime('%Y-%m-%d')
                    status_name = (
                        (comp.get('status') or event.get('status') or {})
                        .get('type', {})
                        .get('name', 'scheduled')
                    )
                    home_score, away_score = _parse_match_scores(status_name, p1, p2)
                    key = (game_date, home_name, away_name)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    api_games.append({
                        'game_id': f"{sport}_{comp.get('id', event.get('id', ''))}",
                        'parent_event_id': str(event.get("id", "")),
                        'home_team_id': home_name,
                        'away_team_id': away_name,
                        'home_athlete_id': _athlete_id(p1),
                        'away_athlete_id': _athlete_id(p2),
                        'game_date': game_date,
                        'event_date': _raw_dt or None,
                        'home_score': home_score,
                        'away_score': away_score,
                        'league': event.get('name', '') or league_label,
                    })
        except Exception as e:
            m.logger.warning(f"Error fetching {sport} scoreboard ({espn_league}): {e}")
    return api_games


def enrich_games_from_db(sport: str, api_games: list[dict]) -> None:
    """Attach stored prediction probs from DB (in-place)."""
    m = main()
    conn = m.get_db_connection()
    try:
        for game in api_games:
            pred = conn.execute('''
                SELECT elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                FROM predictions WHERE game_id = ? AND sport = ?
            ''', (game['game_id'], sport)).fetchone()
            if pred:
                game['stored_elo_prob'] = m._to_float_safe(pred['elo_home_prob'])
                game['stored_xgb_prob'] = m._to_float_safe(pred['xgboost_home_prob'])
                game['stored_ensemble_prob'] = m._to_float_safe(pred['win_probability'])
    finally:
        conn.close()


def load_upcoming_games_with_dates(
    sport: str,
    espn_configs: list[tuple[str, str, bool]],
    *,
    tennis_groupings: bool = False,
    days_back: int | None = None,
    days_forward: int | None = None,
) -> list[tuple]:
    """Fetch ESPN games, enrich from DB, return sorted (date, game) tuples."""
    m = main()
    _back, _forward = _DEFAULT_WINDOWS.get(sport, (3, 14))
    days_back = days_back if days_back is not None else _back
    days_forward = days_forward if days_forward is not None else _forward
    api_games = fetch_espn_api_games(
        sport,
        espn_configs,
        tennis_groupings=tennis_groupings,
        days_back=days_back,
        days_forward=days_forward,
    )
    enrich_games_from_db(sport, api_games)
    dated = [
        (m.parse_date(g['game_date']), g)
        for g in api_games
        if m.parse_date(g['game_date'])
    ]
    dated.sort(key=lambda x: x[0])
    return dated


def _sport_module(sport: str):
    if sport not in _SPORT_MODULES:
        if sport == 'TENNIS':
            from sports import TENNIS as mod
            _SPORT_MODULES[sport] = (mod, True)
        elif sport == 'UFC':
            from sports import UFC as mod
            _SPORT_MODULES[sport] = (mod, False)
        elif sport == 'GOLF':
            from sports import GOLF as mod
            _SPORT_MODULES[sport] = (mod, True)
        else:
            raise KeyError(sport)
    return _SPORT_MODULES[sport]


def individual_sport_season_bounds(ref_dt=None):
    """Calendar-year window for tennis / UFC / golf season stats."""
    ref_dt = ref_dt or datetime.now()
    start = datetime(ref_dt.year, 1, 1)
    end = min(datetime(ref_dt.year, 12, 31, 23, 59, 59), ref_dt)
    return start, end


def _fetch_completed_games(sport: str, start_dt, end_dt) -> list[dict]:
    """Completed matchups from ESPN (+ stored DB probs when present)."""
    m = main()
    mod, tennis_groupings = _sport_module(sport)
    days_back = 365
    if start_dt:
        days_back = max(14, min((datetime.now() - start_dt).days + 21, 400))
    api_games = fetch_espn_api_games(
        sport,
        mod.ESPN_CONFIGS,
        tennis_groupings=tennis_groupings,
        days_back=days_back,
        days_forward=0,
    )
    enrich_games_from_db(sport, api_games)
    completed: list[dict] = []
    for game in api_games:
        hs, aw = game.get('home_score'), game.get('away_score')
        if hs is None or aw is None:
            continue
        if start_dt and end_dt:
            gd_key = m._normalize_game_date_key(game.get('game_date'))
            if not m._date_in_range(gd_key, start_dt, end_dt):
                continue
        completed.append(game)
    completed.sort(
        key=lambda g: (
            m.parse_date(g.get('game_date') or '') or datetime.min,
            g.get('game_id') or '',
        )
    )
    return completed


def build_graded_daily_results(sport: str, start_dt, end_dt):
    """Walk-forward Elo grading for individual-sport moneyline results."""
    if sport not in INDIVIDUAL_SPORTS:
        return None
    m = main()
    completed = _fetch_completed_games(sport, start_dt, end_dt)
    if not completed:
        return None

    k_factor = _ELO_K.get(sport, 20)
    elo_ratings: dict[str, float] = {}

    def get_elo(team: str) -> float:
        return elo_ratings.get(team, 1500.0)

    def expected_score(rating_a: float, rating_b: float) -> float:
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))

    daily_results = defaultdict(lambda: {'games': []})

    for game in completed:
        home = game['home_team_id']
        away = game['away_team_id']
        home_score = float(game['home_score'])
        away_score = float(game['away_score'])
        home_won = home_score > away_score
        game_date = m._normalize_game_date_key(game['game_date'])

        glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = m._model_probs_for_grading(
            sport, game, home, away, game_date,
        )

        stored_elo = m._to_float_safe(game.get('stored_elo_prob'))
        if stored_elo is not None:
            elo_prob = stored_elo
        elif elo_prob is None:
            elo_prob = expected_score(get_elo(home), get_elo(away))

        stored_xgb = m._to_float_safe(game.get('stored_xgb_prob'))
        if stored_xgb is not None:
            xgb_prob = stored_xgb
        stored_ens = m._to_float_safe(game.get('stored_ensemble_prob'))
        if stored_ens is not None:
            ens_prob = stored_ens

        if glicko2_prob is None and elo_prob is not None:
            glicko2_prob = elo_prob
        if trueskill_prob is None and elo_prob is not None:
            trueskill_prob = elo_prob
        if ens_prob is None and elo_prob is not None:
            ens_prob = m._compute_ensemble_prob(
                glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=elo_prob,
            )

        game_info = {
            'game_id': game['game_id'],
            'date': game_date or 'Unknown',
            'home': home,
            'away': away,
            'league': game.get('league') or sport,
            'home_score': int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
            'away_score': int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
            'home_win': home_won,
            'is_draw': False,
            'glicko2_prob': round(glicko2_prob * 100, 1) if glicko2_prob is not None else None,
            'trueskill_prob': round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
            'elo_prob': round(elo_prob * 100, 1) if elo_prob is not None else None,
            'xgb_prob': round(xgb_prob * 100, 1) if xgb_prob is not None else None,
            'ens_prob': round(ens_prob * 100, 1) if ens_prob is not None else None,
            'efficiency_prob': None,
            'efficiency_pick': None,
            'efficiency_correct': None,
        }
        m._soccer_sport._apply_soccer_ml_grading(
            game_info,
            draw_dec=None,
            glicko2_prob=glicko2_prob,
            trueskill_prob=trueskill_prob,
            elo_prob=elo_prob,
            xgb_prob=xgb_prob,
            ens_prob=ens_prob,
            home_won=home_won,
            is_draw=False,
        )
        daily_results[game_info['date']]['games'].append(game_info)

        hr, ar = get_elo(home), get_elo(away)
        expected_home = expected_score(hr, ar)
        actual_home = 1.0 if home_won else 0.0
        elo_ratings[home] = hr + k_factor * (actual_home - expected_home)
        elo_ratings[away] = ar + k_factor * ((1 - actual_home) - (1 - expected_home))

    if not m._daily_results_game_count(daily_results):
        return None
    return daily_results
