"""
WNBA — Women's professional basketball predictions & results.

Route shortcuts and full results-page rendering for the WNBA, extracted
from the generic _render_daily_sport_results_page pipeline in NHL77FINAL.py.

Login, navigation, database, book odds, and multi-sport routing stay in
NHL77FINAL.py.  Import helpers lazily via main() to avoid circular imports
at module load.

██████████████████████████████████████████████████████████████████████████████
██  LOCKED — DO NOT MODIFY WITHOUT PASSWORD                               ██
██  This file is password-locked by the project owner.                    ██
██  Password required: Camaro98#                                          ██
██  Any AI agent or collaborator must receive this password directly      ██
██  from the owner before making ANY changes to this file.                ██
██████████████████████████████████████████████████████████████████████████████
"""
from __future__ import annotations

import time as _time
from collections import defaultdict
from datetime import datetime, timedelta

from sports._sport_base import main, register_shortcut

SPORT = 'WNBA'
PICKS_SLUG = 'wnba-picks'
RESULTS_SLUG = 'wnba-results'


def register_routes(app) -> None:
    """Register WNBA-only Flask shortcuts."""
    register_shortcut(app, '/wnba', PICKS_SLUG)


def update_wnba_scores() -> None:
    """Fetch and update WNBA scores via ESPN."""
    main().update_espn_scores(SPORT)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    """Render /wnba-results — full generic results pipeline."""
    if sport != SPORT:
        return None

    m = main()

    # ── launch gate ──────────────────────────────────────────────
    min_live = m._SPORT_MIN_LIVE_DATES.get(sport)
    if min_live and datetime.now() < min_live:
        launch_txt = min_live.strftime('%B %-d, %Y')
        return m._results_fallback_page(
            sport,
            f"{m.SPORTS[sport]['name']} regular season results will appear "
            f"once games begin on {launch_txt}.",
        )

    # ── cache check ──────────────────────────────────────────────
    cache_key = f'{sport}_daily_results_html_v2'
    cache_ttl = m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)

    if not m._results_date_query_active():
        cached_page = m._SPORT_RESULTS_CACHE.get(cache_key)
        if isinstance(cached_page, dict):
            cached_ts = cached_page.get('ts')
            cached_html = cached_page.get('html')
            if (
                cached_ts is not None
                and cached_html
                and (_time.time() - cached_ts) < cache_ttl
                and m._results_page_html_usable(cached_html)
            ):
                return cached_html
            stale_html, _ = m._stale_page_cache_get(
                m._SPORT_RESULTS_CACHE, cache_key, cache_ttl,
            )
            if stale_html and m._results_page_html_usable(stale_html):
                return stale_html

    # ── season snapshot ──────────────────────────────────────────
    snapshot_raw = m._load_sport_season_snapshot(sport)
    snapshot_stats = m._stats_from_season_snapshot(snapshot_raw)

    # ── background score sync ────────────────────────────────────
    m._start_background_score_sync(sport)

    # ── DB query for completed games ─────────────────────────────
    conn = m.get_db_connection()
    prob_sql = m._predictions_prob_select_sql(conn)
    season_start_dt, season_end_dt = m._results_season_bounds(sport, datetime.now())
    season_end_sql = season_end_dt.strftime('%Y-%m-%d') if season_end_dt else None
    season_start_sql = season_start_dt.strftime('%Y-%m-%d') if season_start_dt else None

    if snapshot_stats:
        card_start = max(
            season_start_dt or (datetime.now() - timedelta(days=30)),
            datetime.now() - timedelta(days=30),
        )
        season_start_sql = card_start.strftime('%Y-%m-%d')
        season_end_sql = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # Non-MLB/non-SOCCER: LIMIT 3000, filter in Python
    completed_games = conn.execute(f'''
        SELECT g.*,
               {prob_sql}
        FROM games g
        LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
        WHERE g.sport = ? AND g.home_score IS NOT NULL
        ORDER BY g.game_date DESC
        LIMIT 3000
    ''', (sport, sport)).fetchall()

    if season_start_dt and season_end_dt:
        completed_games = [
            g for g in completed_games
            if m._date_in_range(
                m._normalize_game_date_key(g['game_date']),
                season_start_dt, season_end_dt,
            )
        ]

    completed_games = m._sort_game_rows_by_date_desc(completed_games)
    conn.close()

    if not completed_games:
        offseason_msg = (
            f" The {m.SPORTS[sport]['name']} season has ended. Results from "
            f"the 2025 season will be available next year."
        )
        return m._results_fallback_page(
            sport,
            f"No {m.SPORTS[sport]['name']} results data available yet.{offseason_msg}",
        )

    # ── process completed games into daily results ───────────────
    daily_results = defaultdict(lambda: {'games': []})
    today_date = datetime.now().strftime('%Y-%m-%d')

    for game in completed_games:
        try:
            home_score = m._to_float_safe(game['home_score'])
            away_score = m._to_float_safe(game['away_score'])
            if home_score is None or away_score is None:
                continue
            home_won = home_score > away_score

            home_team = game['home_team_id']
            away_team = game['away_team_id']
            game_date = m._normalize_game_date_key(game['game_date'])

            glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = (
                m._model_probs_for_grading(
                    sport, game, home_team, away_team, game_date,
                )
            )

            game_info = {
                'game_id':        game['game_id'],
                'date':           game_date or 'Unknown',
                'home':           home_team,
                'away':           away_team,
                'league':         sport,
                'home_score':     int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
                'away_score':     int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
                'home_win':       home_won,
                'is_draw':        False,
                'glicko2_prob':   round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob': round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':       round(elo_prob  * 100, 1) if elo_prob  is not None else None,
                'xgb_prob':       round(xgb_prob  * 100, 1) if xgb_prob  is not None else None,
                'ens_prob':       round(ens_prob  * 100, 1) if ens_prob  is not None else None,
                'model_data_note': None,
            }

            # Generic binary ML grading (no draw_dec for basketball)
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
            # All-Star / TEAM COOP–SPOON etc. — keep on page but exclude from tallies
            if m._is_exhibition_matchup(home_team, away_team):
                game_info['skip_grading'] = True
                game_info['exhibition'] = True

            daily_results[game_info['date']]['games'].append(game_info)
        except Exception as _row_err:
            _gid = None
            try:
                _gid = game['game_id']
            except Exception:
                pass
            m.logger.warning(
                f"Skipping {sport} results row (game_id={_gid}): {_row_err}"
            )
            continue

    # ── sorted dates & stats ─────────────────────────────────────
    yesterday_dt = datetime.now() - timedelta(days=1)
    yesterday = yesterday_dt.strftime('%Y-%m-%d')
    sorted_dates = m._recent_result_dates(daily_results, yesterday=yesterday, limit=30)

    if snapshot_stats:
        _ov = snapshot_stats['total_over']
        _un = snapshot_stats['total_under']
        _gou = snapshot_stats['total_games_ou']
        _avg = snapshot_stats['avg_total']
        _bench = snapshot_stats['ou_bench']
        overall_stats = snapshot_stats['overall_stats']
        _st_stats = snapshot_stats['spread_total_stats']
        season_perf = snapshot_stats['season_perf']
        roi_total = snapshot_stats.get('roi_total') or {}
        m._attach_book_odds_to_daily_results(sport, daily_results, api_limit=0)
        m._compute_spread_total_for_daily(sport, daily_results, skip_efficiency=True)
        m._grade_efficiency_for_results(sport, daily_results)
        overall_stats = m._merge_snapshot_efficiency_into_overall(overall_stats, sport)
        m._finalize_daily_result_cards(sport, daily_results)
    else:
        _ov, _un, _gou, _avg, _bench = m._ou_stats(daily_results, sport)
        m._attach_book_odds_to_daily_results(sport, daily_results, api_limit=40)
        m._cache_market_lines_for_results(sport, daily_results, limit=150)
        m._attach_engine_odds_to_daily_results(sport, daily_results, limit=40)
        _st_stats = m._compute_spread_total_for_daily(sport, daily_results)
        overall_stats = m.compute_overall_stats_from_daily(daily_results)
        overall_stats = m._merge_snapshot_efficiency_into_overall(overall_stats, sport)
        m._finalize_daily_result_cards(sport, daily_results)
        season_perf = m._build_season_performance_summary(overall_stats, _st_stats)
        roi_total = m.compute_roi_for_range(daily_results, None, None)

    # ── tallies & ROI ────────────────────────────────────────────
    tally_bundle = m._compute_results_tally_bundle(
        daily_results,
        yesterday_dt,
        season_start_dt=season_start_dt,
        sport=sport,
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
    if not snapshot_stats:
        roi_total = m.compute_roi_for_range(daily_results, None, None)
    roi_cards = m.build_roi_cards(roi_daily, roi_weekly, roi_total)

    # ── render ───────────────────────────────────────────────────
    _date_ctx = m._results_page_date_kwargs(daily_results, sorted_dates)

    rendered = m.render_template_string(
        m.DAILY_RESULTS_TEMPLATE,
        **m._results_page_meta(sport),
        page=sport, sport=sport, sport_info=m.SPORTS[sport],
        sport_bg_image=m.SPORT_BG_IMAGES.get(sport, ''),
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

    # ── cache rendered HTML ──────────────────────────────────────
    if (
        not m._results_date_query_active()
        and m._daily_results_game_count(daily_results)
        and m._results_page_html_usable(rendered)
    ):
        m._trim_cache(
            m._SPORT_RESULTS_CACHE,
            m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300),
            max_entries=50,
        )
        m._SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}

    return rendered


# === Extracted Dead Code Logic ===

def _apply_wnba_snapshot_flip(data):
    """Flip Grinder2/Takedown/XSharp records in a WNBA season snapshot.

    These three grade <50% on WNBA moneyline, so they are flipped everywhere in
    live grading (see _model_probs_for_grading). The frozen snapshot was built
    pre-flip, so flip its records too — otherwise the WNBA results panel (which
    renders from the snapshot) would still show the old losing records. Operates
    on the freshly-loaded dict (no cache), so there is no double-flip risk."""
    try:
        overall = data.get('overall_stats') or {}
        for k in ('glicko2', 'trueskill', 'xgboost'):
            m = overall.get(k)
            if isinstance(m, dict):
                tot = int(m.get('total') or 0)
                if tot > 0:
                    corr = tot - int(m.get('correct') or 0)
                    m['correct'] = corr
                    m['accuracy'] = round(corr / tot * 100, 1)
    except Exception:
        pass
    return data

