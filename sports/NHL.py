"""
NHL — Hockey predictions & results.

This file holds NHL-specific page rendering, score updating, and weekly performance calculations.
Shared grading, database access, and book odds stay in NHL77FINAL.py.
"""
from __future__ import annotations
from sports._sport_base import main, register_shortcut
from collections import defaultdict
from datetime import datetime, timedelta
import json as _json
import os as _os
_NHL_RESULTS_REGULAR_SEASON_MD = ((10, 1), (4, 30))  # Oct-Apr regular season (excludes playoffs)
import requests
import time as _time

SPORT = 'NHL'
PICKS_SLUG = 'nhl-picks'
RESULTS_SLUG = 'nhl-results'


def register_routes(app) -> None:
    register_shortcut(app, '/nhl', PICKS_SLUG)


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

        conn = main().get_db_connection()
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
        main().logger.info(f"Fetching NHL scores from API (last {lookback_days} days)...")
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
                                    main().logger.info(f"Inserted new NHL game: {away_team} @ {home_team} ({date_str})")
                                except Exception as insert_error:
                                    main().logger.error(f"Error inserting NHL game {game_id}: {insert_error}")

                            if cursor.rowcount > 0:
                                updates_count += 1

            except Exception as date_error:
                # Skip silently to avoid log spam
                pass

            current_date += timedelta(days=1)

        conn.commit()
        conn.close()
        main().logger.info(f"Successfully updated {updates_count} NHL game scores.")

    except Exception as e:
        main().logger.error(f"An error occurred while updating NHL scores: {e}")


def calculate_nhl_weekly_performance():
    """Calculate NHL model performance for the current regular season (Oct-Apr).

    Used by legacy callers; the results page uses _banner_daily_results_for_range.
    V2 inference runs only for games in the last 21 days to avoid worker timeouts.
    """
    try:
        import time as _t
        from datetime import datetime, timedelta
        _wall_start = _t.time()
        _WALL_BUDGET = 25.0  # seconds -- must finish before Render's 30s timeout

        conn = main().get_db_connection()

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
            game_date = main().parse_date(game['game_date'])
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
                main().logger.info(f"[NHL results] wall budget hit after {included_games} games")
                break

            glicko2_prob = None
            trueskill_prob = None
            v2 = None
            days_ago = (datetime.now() - game_date).days if game_date else 999
            if days_ago <= 21:
                try:
                    v2 = main().get_v2_prediction('NHL', game['home_team_id'], game['away_team_id'], game['game_date'])
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
                meta_prob = main()._compute_ensemble_prob(
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
        main().logger.error(f"Error calculating NHL weekly performance: {e}")
        return None


def render_sport_results_page(sport, season_start_dt=None):
    """NHL season snapshot + playoff results (sport-specific)."""
    cache_key = f'{sport}_moneyline_results_html_v3'
    cache_ttl = main()._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300)
    cached_page = None
    if not main()._results_date_query_active():
        cached_page = main()._SPORT_RESULTS_CACHE.get(cache_key)
    if isinstance(cached_page, dict):
        cached_ts = cached_page.get('ts')
        cached_html = cached_page.get('html')
        if (
            cached_ts is not None
            and cached_html
            and (_time.time() - cached_ts) < cache_ttl
            and main()._results_page_html_usable(cached_html)
        ):
            return cached_html
        stale_html, _ = main()._stale_page_cache_get(main()._SPORT_RESULTS_CACHE, cache_key, cache_ttl)
        if stale_html and main()._results_page_html_usable(stale_html):
            return stale_html

    try:
        # Run NHL score sync at most once every 10 minutes (background only).
        main()._start_background_score_sync('NHL', update_nhl_scores)
    except Exception as e:
        main().logger.error(f"NHL score sync failed (continuing with existing data): {e}")

    now_dt = datetime.now()
    yesterday_dt = now_dt - timedelta(days=1)
    regular_complete = _nhl_regular_season_complete(now_dt)
    results_snapshot_notice = None
    snapshot_raw = (
        _load_nhl_season_snapshot(now_dt, 'regular') if regular_complete else None
    )
    snapshot_stats = main()._stats_from_nhl_snapshot(snapshot_raw)
    if regular_complete and snapshot_raw and not snapshot_stats:
        main().logger.warning('NHL season snapshot present but stats extraction failed')

    season_start_dt, season_end_dt = main()._results_season_bounds('NHL', yesterday_dt)
    season_end_eff = min(season_end_dt, yesterday_dt) if season_end_dt else yesterday_dt
    season_daily = None
    if not snapshot_stats:
        season_daily = main()._banner_daily_results_for_range(
            sport, season_start_dt, season_end_eff,
        )
        if not season_daily:
            if regular_complete:
                season_daily = defaultdict(lambda: {'games': []})
                if _os.path.isfile(_nhl_snapshot_json_path(now_dt, 'regular')):
                    snapshot_stats = main()._stats_from_nhl_snapshot(
                        _load_nhl_season_snapshot(now_dt, 'regular'),
                    )
                if not snapshot_stats:
                    results_snapshot_notice = (
                        'Frozen 2025-26 regular-season stats are not available on this '
                        'server yet. Season summary will appear after deploy; playoff '
                        'game cards load below when games are graded.'
                    )
            else:
                return main()._results_fallback_page(
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
            playoff_daily = main()._banner_daily_results_for_range(
                sport, pf_start, pf_end_eff, playoffs=True,
            )
        if playoff_daily and main()._daily_results_game_count(playoff_daily):
            pf_st = _attach_nhl_display_grading(sport, playoff_daily)
            pf_overall = main().compute_overall_stats_from_daily(playoff_daily)
            playoff_perf = main()._build_season_performance_summary(
                pf_overall,
                pf_st,
                scope_label='NHL playoffs (live)',
                games_in_scope=main()._daily_results_game_count(playoff_daily),
            )

    if playoff_daily and main()._daily_results_game_count(playoff_daily):
        daily_results = playoff_daily
    elif snapshot_stats:
        card_start = max(season_start_dt, yesterday_dt - timedelta(days=30))
        daily_results = main()._banner_daily_results_for_range(
            sport, card_start, season_end_eff,
            playoffs=False, skip_v2=True,
        )
        if not daily_results or not main()._daily_results_game_count(daily_results):
            daily_results = defaultdict(lambda: {'games': []})
        else:
            _attach_nhl_display_grading(sport, daily_results)
    else:
        display_start_dt = yesterday_dt - timedelta(days=30)
        daily_results = main()._subset_daily_results(season_daily, display_start_dt, yesterday_dt)
        if not main()._daily_results_game_count(daily_results):
            daily_results = season_daily

    today_date = now_dt.strftime('%Y-%m-%d')

    try:
        yesterday = yesterday_dt.strftime('%Y-%m-%d')
        sorted_dates = main()._recent_result_dates(daily_results, yesterday=yesterday, limit=7)

        if snapshot_stats:
            overall_stats = main()._merge_snapshot_efficiency_into_overall(
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
            if daily_results and main()._daily_results_game_count(daily_results):
                main()._grade_efficiency_for_results(sport, daily_results)
                main()._finalize_daily_result_cards(sport, daily_results)
        else:
            _ov, _un, _gou, _avg, _bench = main()._ou_stats(season_daily, sport)
            main()._attach_book_odds_to_daily_results(sport, season_daily, api_limit=300)
            main()._cache_market_lines_for_results(sport, season_daily, limit=80)
            main()._attach_engine_odds_to_daily_results(sport, season_daily, limit=40)
            _st_stats = main()._compute_spread_total_for_daily(sport, season_daily)
            overall_stats = main().compute_overall_stats_from_daily(season_daily)
            main()._finalize_daily_result_cards(sport, season_daily)
            season_perf = main()._build_season_performance_summary(
                overall_stats,
                _st_stats,
                scope_label='NHL regular season (Oct-Apr)',
                games_expected=main().SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL'),
                games_in_scope=_nhl_results_games_in_scope(season_daily),
            )
            roi_total = main().compute_roi_for_range(season_daily, None, None)

        if (
            not snapshot_stats
            and daily_results is not season_daily
            and main()._daily_results_game_count(daily_results)
        ):
            _attach_nhl_display_grading(sport, daily_results)

        week_start = yesterday_dt - timedelta(days=6)
        week_end = min(yesterday_dt, season_end_eff)
        tally_daily = main()._banner_daily_results_for_range(
            sport, week_start, week_end, playoffs=False, skip_v2=True,
        )
        if tally_daily and main()._daily_results_game_count(tally_daily):
            _attach_nhl_display_grading(sport, tally_daily)
        elif season_daily and main()._daily_results_game_count(season_daily):
            tally_daily = season_daily
        else:
            tally_daily = daily_results

        tally_bundle = main()._compute_results_tally_bundle(
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
        roi_daily = main().compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
        roi_weekly = main().compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
        if not snapshot_stats:
            roi_total = main().compute_roi_for_range(season_daily, None, None)
        roi_cards = main().build_roi_cards(roi_daily, roi_weekly, roi_total)
        _date_ctx = main()._results_page_date_kwargs(daily_results, sorted_dates)

        rendered = main().render_template_string(
            main().DAILY_RESULTS_TEMPLATE,
            **main()._results_page_meta(sport),
            page=sport, sport=sport, sport_info=main().SPORTS[sport], sport_bg_image=main().SPORT_BG_IMAGES.get(sport, ''),
            sport_seo_slug=main().SPORT_SEO_SLUGS.get(sport, sport.lower()),
            sport_results_slug=main()._SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
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
            not main()._results_date_query_active()
            and (snapshot_stats or main()._daily_results_game_count(daily_results))
            and main()._results_page_html_usable(rendered)
        ):
            main()._trim_cache(main()._SPORT_RESULTS_CACHE, main()._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
            main()._SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
        return rendered
    except Exception as e:
        main().logger.error(f"Error processing NHL results: {e}")
        return f"<h1>NHL results page failed to render because of a processing error: {str(e)}</h1>"


# === Extracted Dead Code Logic ===

def _apply_nhl_spread_fade(d: dict) -> None:
    main()._apply_spread_fade(d)


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
    game_date = main()._normalize_game_date_key(raw_date)
    if not game_date:
        return None
    hk = main()._normalize_team_key_for_sport('NHL', home)
    ak = main()._normalize_team_key_for_sport('NHL', away)
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
    return hk in main()._NHL_FRANCHISE_TEAM_KEYS and ak in main()._NHL_FRANCHISE_TEAM_KEYS


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
        key=lambda r: main().parse_date(main()._normalize_game_date_key(r['game_date'])) or datetime.min,
    )
    if not apply_season_cap:
        return deduped
    cap = main().SPORT_REGULAR_SEASON_GAMES_PER_TEAM.get('NHL', 82)
    team_counts = {}
    kept = []
    for row in deduped:
        _, hk, ak = _nhl_results_match_key(row)
        if team_counts.get(hk, 0) >= cap or team_counts.get(ak, 0) >= cap:
            continue
        team_counts[hk] = team_counts.get(hk, 0) + 1
        team_counts[ak] = team_counts.get(ak, 0) + 1
        kept.append(row)
    league_max = main().SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL')
    if league_max and len(kept) > league_max:
        kept = kept[:league_max]
    return kept


def _nhl_results_games_in_scope(daily_results):
    """Graded NHL regular-season games for banner subtitle (never above league max)."""
    count = main()._daily_results_game_count(daily_results)
    league_max = main().SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL')
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
    for base in (main()._V2_BASE, main()._BASE_DIR, _os.path.dirname(_os.path.abspath(__file__))):
        path = _os.path.join(base, 'data', 'season_snapshots', fname)
        if _os.path.isfile(path):
            return path
    return _os.path.join(main()._V2_BASE, 'data', 'season_snapshots', fname)


def _load_nhl_season_snapshot(ref_dt=None, phase='regular'):
    """Load committed season JSON (no import side-effects — works on Render)."""
    path = _nhl_snapshot_json_path(ref_dt, phase)
    if not _os.path.isfile(path):
        main().logger.debug('NHL season snapshot missing: %s', path)
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            data = _json.load(fh)
    except (OSError, _json.JSONDecodeError) as exc:
        main().logger.warning('NHL season snapshot read failed (%s): %s', path, exc)
        return None
    if not isinstance(data, dict) or data.get('sport') != 'NHL':
        return None
    return data


def _attach_nhl_display_grading(sport, daily_results):
    """Book lines + spread/O/U for a small display slice (not full season)."""
    m = main()
    if not daily_results or not m._daily_results_game_count(daily_results):
        return None
    m._attach_book_odds_to_daily_results(sport, daily_results, api_limit=80)
    m._cache_market_lines_for_results(sport, daily_results, limit=40)
    m._attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
    st = m._compute_spread_total_for_daily(sport, daily_results)
    m._finalize_daily_result_cards(sport, daily_results)
    return st


def _render_nhl_results_page(sport, season_start_dt=None):
    """NHL season snapshot + playoff results (sport-specific)."""
    cache_key = f'{sport}_moneyline_results_html_v3'
    cache_ttl = main()._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300)
    cached_page = None
    if not main()._results_date_query_active():
        cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
    if isinstance(cached_page, dict):
        cached_ts = cached_page.get('ts')
        cached_html = cached_page.get('html')
        if (
            cached_ts is not None
            and cached_html
            and (main()._time.time() - cached_ts) < cache_ttl
            and _results_page_html_usable(cached_html)
        ):
            return cached_html
        stale_html, _ = _stale_page_cache_get(_SPORT_RESULTS_CACHE, cache_key, cache_ttl)
        if stale_html and _results_page_html_usable(stale_html):
            return stale_html

    try:
        # Run NHL score sync at most once every 10 minutes (background only).
        main()._start_background_score_sync('NHL', update_nhl_scores)
    except Exception as e:
        main().logger.error(f"NHL score sync failed (continuing with existing data): {e}")

    now_dt = main().datetime.now()
    yesterday_dt = now_dt - timedelta(days=1)
    regular_complete = _nhl_regular_season_complete(now_dt)
    results_snapshot_notice = None
    snapshot_raw = (
        _load_nhl_season_snapshot(now_dt, 'regular') if regular_complete else None
    )
    snapshot_stats = _stats_from_nhl_snapshot(snapshot_raw)
    if regular_complete and snapshot_raw and not snapshot_stats:
        main().logger.warning('NHL season snapshot present but stats extraction failed')

    season_start_dt, season_end_dt = main()._results_season_bounds('NHL', yesterday_dt)
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
                return main()._results_fallback_page(
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
            page=sport, sport=sport, sport_info=main().SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
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
            not main()._results_date_query_active()
            and (snapshot_stats or _daily_results_game_count(daily_results))
            and _results_page_html_usable(rendered)
        ):
            _trim_cache(_SPORT_RESULTS_CACHE, main()._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
            _SPORT_RESULTS_CACHE[cache_key] = {'ts': main()._time.time(), 'html': rendered}
        return rendered
    except Exception as e:
        main().logger.error(f"Error processing NHL results: {e}")
        return f"<h1>NHL results page failed to render because of a processing error: {str(e)}</h1>"

