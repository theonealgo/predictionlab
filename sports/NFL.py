"""
NFL — Football predictions & results.

This file holds NFL-specific page rendering, score updating, and weekly performance calculations.
Shared grading, database access, and book odds stay in NHL77FINAL.py.
"""
from __future__ import annotations
from sports._sport_base import main, register_shortcut
from collections import defaultdict

SPORT = 'NFL'
PICKS_SLUG = 'nfl-picks'
RESULTS_SLUG = 'nfl-results'

def register_routes(app) -> None:
    register_shortcut(app, '/nfl', PICKS_SLUG)

def update_nfl_scores():
    """
    Fetches and updates NFL scores for the 2025 season.
    Also inserts new games (including playoffs) that don't exist in database.
    """
    try:
        main().logger.info("Fetching 2025 NFL schedule to update scores...")
        schedule = main().nfl.import_schedules([2025])
        
        if schedule.empty:
            main().logger.warning("No NFL schedule data found for the 2025 season.")
            return

        finished_games = schedule[schedule['result'].notna()].copy()

        if finished_games.empty:
            main().logger.info("No new finished NFL games with results found.")
            return

        main().logger.info(f"Found {len(finished_games)} finished NFL games to update.")
        
        # Team abbreviation to full name mapping for NFL
        nfl_abbr_to_full = {
            'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens',
            'BUF': 'Buffalo Bills', 'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears',
            'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns', 'DAL': 'Dallas Cowboys',
            'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
            'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars',
            'KC': 'Kansas City Chiefs', 'LV': 'Las Vegas Raiders', 'LAC': 'Los Angeles Chargers',
            'LAR': 'Los Angeles Rams', 'LA': 'Los Angeles Rams', 'MIA': 'Miami Dolphins',
            'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints',
            'NYG': 'New York Giants', 'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles',
            'PIT': 'Pittsburgh Steelers', 'SF': 'San Francisco 49ers', 'SEA': 'Seattle Seahawks',
            'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders'
        }
        
        conn = main().get_db_connection()
        cursor = conn.cursor()
        updates_count = 0
        inserts_count = 0

        for _, game in finished_games.iterrows():
            game_id = game['game_id']
            
            # Check if game exists
            existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = 'NFL'", (game_id,)).fetchone()
            
            if existing:
                # Update existing game
                cursor.execute("""
                    UPDATE games
                    SET home_score = ?, away_score = ?, status = 'final'
                    WHERE sport = 'NFL' AND game_id = ?
                """, (game['home_score'], game['away_score'], game_id))
                if cursor.rowcount > 0:
                    updates_count += 1
            else:
                # Insert new game (including playoffs)
                try:
                    home_team = nfl_abbr_to_full.get(game['home_team'], game['home_team'])
                    away_team = nfl_abbr_to_full.get(game['away_team'], game['away_team'])
                    game_date = str(game['gameday']) if main().pd.notna(game.get('gameday')) else str(game.get('game_date', ''))
                    
                    cursor.execute("""
                        INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                    """, ('NFL', 'NFL', game_id, 2025, game_date, home_team, away_team, game['home_score'], game['away_score']))
                    inserts_count += 1
                    main().logger.info(f"Inserted new NFL game: {away_team} @ {home_team} (Week {game.get('week', '?')})")
                except Exception as insert_error:
                    main().logger.error(f"Error inserting NFL game {game_id}: {insert_error}")

        conn.commit()
        conn.close()
        main().logger.info(f"Successfully updated {updates_count} and inserted {inserts_count} NFL game scores.")

    except Exception as e:
        main().logger.error(f"An error occurred while updating NFL scores: {e}")


def calculate_nfl_weekly_performance():
    """Calculate NFL model performance week by week using actual stored predictions
    
    Gets completed games and results from nfl_data_py API,
    then looks up predictions from database.
    """
    try:
        # Fetch 2025 NFL schedule with results from API - this is the source of truth
        schedule = main().nfl.import_schedules([2025])
        
        if schedule.empty:
            return None
        
        # Filter to completed games only (games with results)
        completed_games = schedule[schedule['result'].notna()].copy()
        
        if completed_games.empty:
            return None
        
        # Get database connection for predictions
        conn = main().get_db_connection()
        
        # Team abbreviation to full name mapping
        abbr_to_full = {
            'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens',
            'BUF': 'Buffalo Bills', 'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears',
            'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns', 'DAL': 'Dallas Cowboys',
            'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
            'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars',
            'KC': 'Kansas City Chiefs', 'LV': 'Las Vegas Raiders', 'LAC': 'Los Angeles Chargers',
            'LAR': 'Los Angeles Rams', 'LA': 'Los Angeles Rams', 'MIA': 'Miami Dolphins',
            'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints',
            'NYG': 'New York Giants', 'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles',
            'PIT': 'Pittsburgh Steelers', 'SF': 'San Francisco 49ers', 'SEA': 'Seattle Seahawks',
            'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders'
        }
        
        weekly_results = {}

        # Process each completed game from API
        for _, api_game in completed_games.iterrows():
            week = int(api_game['week'])
            game_id = api_game['game_id']

            # Look up stored predictions from database
            pred = conn.execute('''
                SELECT p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM predictions p
                WHERE p.game_id = ? AND p.sport = 'NFL'
            ''', (game_id,)).fetchone()

            # Get team full names
            home_team_full = abbr_to_full.get(api_game['home_team'], api_game['home_team'])
            away_team_full = abbr_to_full.get(api_game['away_team'], api_game['away_team'])

            if not pred or pred[0] is None:
                # No stored prediction (e.g. Super Bowl / playoff game never visited).
                # Fall back to live Elo so the game still shows in results.
                try:
                    _hr = main().get_elo(home_team_full)
                    _ar = main().get_elo(away_team_full)
                    elo_prob = main().expected_score(_hr, _ar) if _hr and _ar else 0.5
                except Exception:
                    elo_prob = 0.5
                xgb_prob = elo_prob
                ens_prob = elo_prob
            else:
                # Stored DB predictions
                elo_prob = float(pred[0]) if pred[0] else None
                xgb_prob = float(pred[1]) if pred[1] else elo_prob
                ens_prob = elo_prob  # start with elo as fallback

            # V2 model predictions
            v2 = main().get_v2_prediction('NFL', home_team_full, away_team_full, str(api_game['gameday']))
            glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
            trueskill_prob = v2.get('trueskill_prob') if v2 else None
            if v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
                ens_prob = main()._compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

            actual_home_win = api_game['home_score'] > api_game['away_score']

            if week not in weekly_results:
                weekly_results[week] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   >= 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob >= 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       >= 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       >= 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       >= 0.5) == actual_home_win if ens_prob       is not None else None

            for model, prob, correct in [
                ('glicko2',   glicko2_prob,   glicko2_correct),
                ('trueskill', trueskill_prob, trueskill_correct),
                ('elo',       elo_prob,       elo_correct),
                ('xgboost',   xgb_prob,       xgb_correct),
                ('ensemble',  ens_prob,       ens_correct),
            ]:
                if prob is not None:
                    weekly_results[week][model]['total'] += 1
                    if correct:
                        weekly_results[week][model]['correct'] += 1

            weekly_results[week]['games'].append({
                'game_id':          game_id,
                'date':             str(api_game['gameday']),
                'away':             away_team_full,
                'home':             home_team_full,
                'away_score':       int(api_game['away_score']),
                'home_score':       int(api_game['home_score']),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob       * 100, 1) if elo_prob       is not None else None,
                'xgb_prob':         round(xgb_prob       * 100, 1) if xgb_prob       is not None else None,
                'ens_prob':         round(ens_prob       * 100, 1) if ens_prob       is not None else None,
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       ens_correct,
            })

        conn.close()

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )

        return weekly_results
        
    except Exception as e:
        main().logger.error(f"Error calculating NFL weekly performance: {e}")
        return None


def render_sport_results_page(sport, season_start_dt=None):
    """NFL weekly + DB fallback results (sport-specific)."""
    weekly_results = None
    try:
        update_nfl_scores()
        # Also sync from ESPN to catch playoff games nfl_data_py might miss
        try:
            update_espn_scores('NFL')
        except Exception:
            pass
        weekly_results = calculate_nfl_weekly_performance()
    except Exception as nfl_sync_err:
        main().logger.exception(f"NFL sync/performance pipeline failed; falling back to DB-only render: {nfl_sync_err}")

    if weekly_results:
        try:
            daily_results = main()._daily_results_from_weekly(weekly_results)
            main()._attach_book_odds_to_daily_results(sport, daily_results, api_limit=80)
            main()._cache_market_lines_for_results(sport, daily_results, limit=80)
            main()._grade_efficiency_for_results(sport, daily_results)
            overall_stats = main().compute_overall_stats_from_daily(daily_results)
            overall_stats = main()._merge_snapshot_efficiency_into_overall(overall_stats, sport)
            yesterday_dt = main().datetime.now() - main().timedelta(days=1)
            tally_bundle = main()._compute_results_tally_bundle(
                daily_results, yesterday_dt, season_start_dt=season_start_dt,
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
            main()._attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
            roi_daily = main().compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
            roi_weekly = main().compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
            roi_total = main().compute_roi_for_range(daily_results, None, None)
            roi_cards = main().build_roi_cards(roi_daily, roi_weekly, roi_total)
            if main()._results_date_query_active():
                today_date = main().datetime.now().strftime('%Y-%m-%d')
                yesterday = yesterday_dt.strftime('%Y-%m-%d')
                sorted_dates = main()._recent_result_dates(daily_results, yesterday=yesterday, limit=30)
                main()._attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
                main()._cache_market_lines_for_results(sport, daily_results, limit=150)
                _st_stats = main()._compute_spread_total_for_daily(sport, daily_results)
                overall_stats = main().compute_overall_stats_from_daily(daily_results)
                main()._finalize_daily_result_cards(sport, daily_results)
                season_perf = main()._build_season_performance_summary(overall_stats, _st_stats)
                _date_ctx = main()._results_page_date_kwargs(daily_results, sorted_dates)
                return main().render_template_string(
                    main().DAILY_RESULTS_TEMPLATE,
                    **main()._results_page_meta(sport),
                    page=sport, sport=sport, sport_info=main().SPORTS[sport],
                    sport_bg_image=main().SPORT_BG_IMAGES.get(sport, ''),
                    sport_seo_slug=main().SPORT_SEO_SLUGS.get(sport, sport.lower()),
                    sport_results_slug=main()._SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                    **_date_ctx,
                    today_date=today_date, overall_stats=overall_stats,
                    spread_total_stats=_st_stats, season_perf=season_perf,
                    daily_tally=daily_tally, daily_tally_date=daily_tally_date,
                    daily_tally_games=daily_tally_games,
                    weekly_tally=weekly_tally,
                    weekly_tally_date_range=weekly_tally_date_range,
                    weekly_tally_games=weekly_tally_games,
                    roi_cards=roi_cards,
                    results_stale_notice=results_stale_notice,
                    results_snapshot_notice=None,
                    soccer_leagues=None,
                )
            return main().render_template_string(
                main().NFL_WEEKLY_RESULTS_TEMPLATE,
                **main()._results_page_meta(sport),
                page=sport,
                sport=sport,
                sport_info=main().SPORTS[sport], sport_bg_image=main().SPORT_BG_IMAGES.get(sport, ''),
                sport_seo_slug=main().SPORT_SEO_SLUGS.get(sport, sport.lower()),
                sport_results_slug=main()._SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
                weekly_results=weekly_results,
                overall_stats=overall_stats,
                daily_tally=daily_tally,
                daily_tally_date=daily_tally_date,
                daily_tally_games=daily_tally_games,
                weekly_tally=weekly_tally,
                weekly_tally_date_range=weekly_tally_date_range,
                weekly_tally_games=weekly_tally_games,
                roi_cards=roi_cards,
                results_stale_notice=results_stale_notice,
            )
        except Exception as nfl_tpl_err:
            main().logger.exception(
                "NFL weekly template render failed; falling back to DB daily cards: %s",
                nfl_tpl_err,
            )

    # Fallback path: render from existing DB data if the live NFL pipeline fails.
    conn = main().get_db_connection()
    completed_games = conn.execute('''
        SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
        FROM games g
        LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'NFL'
        WHERE g.sport = 'NFL' AND g.home_score IS NOT NULL
        ORDER BY g.game_date DESC
        LIMIT 100
    ''').fetchall()
    conn.close()
    if not completed_games:
        return main()._results_fallback_page(sport, "NFL moneyline results are temporarily unavailable because no completed NFL games are stored yet.")

    daily_results = defaultdict(lambda: {'games': []})
    today_date = main().datetime.now().strftime('%Y-%m-%d')
    for game in completed_games:
        home_score = main()._to_float_safe(game['home_score'])
        away_score = main()._to_float_safe(game['away_score'])
        if home_score is None or away_score is None:
            continue
        home_won = home_score > away_score
        _raw_date = main()._to_date_str(game['game_date'])
        game_date = main()._normalize_game_date_key(game['game_date']) or 'Unknown'
        elo_prob = main()._to_float_safe(game['elo_home_prob'], 0.5)
        xgb_prob = main()._to_float_safe(game['xgboost_home_prob'], elo_prob)
        ens_prob = main()._to_float_safe(game['win_probability'], elo_prob)
        game_info = {
            'game_id': game['game_id'],
            'date': game_date,
            'home': game['home_team_id'],
            'away': game['away_team_id'],
            'league': 'NFL',
            'home_score': int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
            'away_score': int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
            'home_win': home_won,
            'is_draw': False,
            'glicko2_prob': None,
            'trueskill_prob': None,
            'elo_prob': round(elo_prob * 100, 1),
            'xgb_prob': round(xgb_prob * 100, 1),
            'ens_prob': round(ens_prob * 100, 1),
            'glicko2_correct': None,
            'trueskill_correct': None,
            'elo_correct': (elo_prob >= 0.5) == home_won,
            'xgb_correct': (xgb_prob >= 0.5) == home_won,
            'ens_correct': (ens_prob >= 0.5) == home_won,
            'skip_grading': False,
        }
        daily_results[game_date]['games'].append(game_info)

    yesterday_dt = main().datetime.now() - main().timedelta(days=1)
    yesterday = yesterday_dt.strftime('%Y-%m-%d')
    sorted_dates = main()._recent_result_dates(daily_results, yesterday=yesterday, limit=30)
    _ov, _un, _gou, _avg, _bench = main()._ou_stats(daily_results, sport)
    main()._attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
    main()._attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
    _st_stats = main()._compute_spread_total_for_daily(sport, daily_results)
    overall_stats = main().compute_overall_stats_from_daily(daily_results)
    main()._finalize_daily_result_cards(sport, daily_results)
    season_perf = main()._build_season_performance_summary(overall_stats, _st_stats)
    tally_bundle = main()._compute_results_tally_bundle(
        daily_results, yesterday_dt, season_start_dt=season_start_dt,
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
    roi_total = main().compute_roi_for_range(daily_results, None, None)
    roi_cards = main().build_roi_cards(roi_daily, roi_weekly, roi_total)
    _date_ctx = main()._results_page_date_kwargs(daily_results, sorted_dates)
    return main().render_template_string(
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
        daily_tally=daily_tally,
        daily_tally_date=daily_tally_date,
        daily_tally_games=daily_tally_games,
        weekly_tally=weekly_tally,
        weekly_tally_date_range=weekly_tally_date_range,
        weekly_tally_games=weekly_tally_games,
        roi_cards=roi_cards,
        results_stale_notice=results_stale_notice,
        results_snapshot_notice=None,
        soccer_leagues=None
    )



