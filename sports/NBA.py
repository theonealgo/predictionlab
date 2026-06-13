"""
NBA — Professional basketball predictions & results.

This file holds NBA-specific logic extracted from NHL77FINAL.py:
weekly grading, efficiency projections on picks, and the full /nba-results page.

Login, navigation, database, book odds, and multi-sport routing stay in NHL77FINAL.py.
Import helpers lazily via _main() to avoid circular imports at module load.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

SPORT = 'NBA'
PICKS_SLUG = 'nba-picks'
RESULTS_SLUG = 'nba-results'


def _main():
    import NHL77FINAL as main
    return main


def register_routes(app) -> None:
    """Register NBA-only Flask shortcuts (SEO slugs stay in main seo_picks_page)."""
    from flask import redirect

    @app.route('/nba', endpoint='shortcut_nba')
    def nba_shortcut():
        return redirect(f'/{PICKS_SLUG}', code=301)


def update_nba_scores() -> None:
    """Fetch and update NBA scores via ESPN (last 7 days)."""
    _main().update_espn_scores(SPORT)


def nba_model_probs_for_grading(game_row, home_team, away_team, game_date_key):
    """NBA wrapper for shared _model_probs_for_grading."""
    return _main()._model_probs_for_grading(SPORT, game_row, home_team, away_team, game_date_key)


def calculate_nba_weekly_performance():
    """NBA model performance week-by-week using stored + frozen predictions."""
    m = _main()

    def to_float(val):
        if val is None:
            return None
        if isinstance(val, (float, int)):
            return float(val)
        if isinstance(val, bytes):
            try:
                import struct
                if len(val) == 8:
                    return struct.unpack('d', val)[0]
                if len(val) == 4:
                    return struct.unpack('f', val)[0]
            except Exception:
                pass
            return None
        try:
            return float(val)
        except Exception:
            return None

    try:
        conn = m.get_db_connection()
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        prob_sql = m._predictions_prob_select_sql(conn)

        games = conn.execute(f'''
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.predicted_total,
                   {prob_sql}
            FROM games g
            LEFT JOIN predictions p
              ON p.sport = 'NBA' AND (
                   p.game_id = g.game_id
                   OR (
                        date(p.game_date) = date(g.game_date)
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                   )
              )
            WHERE g.sport = 'NBA'
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) <= ?
            ORDER BY g.game_date
        ''', (yesterday,)).fetchall()
        conn.close()

        if not games:
            return None

        first_game_date = m.parse_date(games[0]['game_date'])
        season_start = first_game_date if first_game_date else datetime(2025, 10, 21)
        weekly_results = {}

        for game in games:
            game_date = m.parse_date(game['game_date'])
            if not game_date:
                continue

            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game['home_score']
            away_score = game['away_score']

            if home_score is None or away_score is None:
                continue

            days_since_start = (game_date - season_start).days
            week = (days_since_start // 7) + 1

            game_date_key = m._normalize_game_date_key(game['game_date'])
            glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = nba_model_probs_for_grading(
                game, home_team, away_team, game_date_key,
            )
            if xgb_prob is None:
                xgb_prob = elo_prob

            actual_home_win = home_score > away_score

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

            _pt = to_float(game['predicted_total'])
            weekly_results[week]['games'].append({
                'game_id':         game['game_id'],
                'date':             game['game_date'].split()[0],
                'away':             away_team,
                'home':             home_team,
                'away_score':       int(away_score),
                'home_score':       int(home_score),
                'home_win':         actual_home_win,
                'predicted_total':  m._round_to_half(_pt) if _pt is not None else None,
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob  * 100, 1) if elo_prob  is not None else None,
                'xgb_prob':         round(xgb_prob  * 100, 1) if xgb_prob  is not None else None,
                'ens_prob':         round(ens_prob  * 100, 1) if ens_prob  is not None else None,
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       ens_correct,
                'efficiency_prob':   None,
                'efficiency_pick':   None,
                'efficiency_correct': None,
            })

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )

        return weekly_results

    except Exception as e:
        m.logger.error(f"Error calculating NBA weekly performance: {e}")
        return None


def attach_nba_efficiency_to_daily_results(sport, daily_results) -> None:
    """Backward-compatible alias — delegates to shared team efficiency attach."""
    from sports.team_efficiency_attach import attach_efficiency_to_daily_results
    attach_efficiency_to_daily_results(sport, daily_results)


def attach_nba_prediction_projections(predictions) -> None:
    """Backward-compatible alias — delegates to shared team efficiency attach."""
    from sports.team_efficiency_attach import attach_efficiency_to_predictions
    attach_efficiency_to_predictions(SPORT, predictions)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    """Render /nba-results (called from sport_results when sport == NBA)."""
    import time as _time

    m = _main()
    if sport != SPORT:
        return None

    cache_key = f'{sport}_daily_results_html_v8'
    cache_ttl = m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)
    if not m._results_date_query_active():
        # Serve stale-while-warm (up to 5x TTL) like the picks pages so a visitor
        # never blocks on the expensive full-season rebuild once it's been warmed.
        cached_html, _needs_reval = m._stale_page_cache_get(
            m._SPORT_RESULTS_CACHE, cache_key, cache_ttl,
        )
        if cached_html and m._results_page_html_usable(cached_html):
            return cached_html
    try:
        try:
            update_nba_scores()
        except Exception as e:
            m.logger.error(f"NBA score sync failed (continuing with existing data): {e}")
        weekly_results = calculate_nba_weekly_performance()
        m.logger.info(
            f"NBA weekly_results: {weekly_results is not None}, "
            f"weeks: {list(weekly_results.keys()) if weekly_results else 'None'}"
        )
        if not weekly_results:
            return m._results_fallback_page(
                sport,
                "NBA results could not be loaded because no completed NBA games were available for grading yet.",
            )

        daily_results = defaultdict(lambda: {'games': []})
        today_date = datetime.now().strftime('%Y-%m-%d')

        for week, week_data in weekly_results.items():
            for game in week_data['games']:
                date_key = game['date']
                daily_results[date_key]['games'].append(game)

        yesterday_dt = datetime.now() - timedelta(days=1)
        yesterday = yesterday_dt.strftime('%Y-%m-%d')
        sorted_dates = m._recent_result_dates(daily_results, yesterday=yesterday, limit=7)
        if not sorted_dates:
            return m._results_fallback_page(
                sport,
                "NBA results could not be loaded because no completed games were available for grading yet.",
            )

        _ov, _un, _gou, _avg, _bench = m._ou_stats(daily_results, sport)
        m._attach_book_odds_to_daily_results(sport, daily_results, api_limit=300)
        m._cache_market_lines_for_results(sport, daily_results, limit=150)
        m._attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
        _st_stats = m._compute_spread_total_for_daily(sport, daily_results, skip_efficiency=True)
        m._grade_efficiency_for_results(SPORT, daily_results)
        overall_stats = m.compute_overall_stats_from_daily(daily_results)
        overall_stats = m._merge_snapshot_efficiency_into_overall(overall_stats, SPORT)
        m._finalize_daily_result_cards(sport, daily_results)
        season_perf = m._build_season_performance_summary(overall_stats, _st_stats)
        tally_bundle = m._compute_results_tally_bundle(
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
        roi_daily = m.compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
        roi_weekly = m.compute_roi_for_range(daily_results, weekly_start_dt, weekly_end_dt)
        roi_total = m.compute_roi_for_range(daily_results, None, None)
        roi_cards = m.build_roi_cards(roi_daily, roi_weekly, roi_total)
        _date_ctx = m._results_page_date_kwargs(daily_results, sorted_dates)
        rendered = m.render_template_string(
            m.DAILY_RESULTS_TEMPLATE,
            **m._results_page_meta(sport),
            page=sport, sport=sport, sport_info=m.SPORTS[sport], sport_bg_image=m.SPORT_BG_IMAGES.get(sport, ''),
            sport_seo_slug=m.SPORT_SEO_SLUGS.get(sport, sport.lower()),
            sport_results_slug=m._SPORT_RESULTS_SLUGS.get(sport, sport.lower() + '-results'),
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
        )
        if (
            not m._results_date_query_active()
            and m._daily_results_game_count(daily_results)
            and m._results_page_html_usable(rendered)
        ):
            m._trim_cache(m._SPORT_RESULTS_CACHE, m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
            m._SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
        return rendered
    except Exception as e:
        m.logger.error(f"Error processing NBA results: {e}")
        return f"<h1>NBA results page failed to render because of a processing error: {str(e)}</h1>"
