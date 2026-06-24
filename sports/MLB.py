"""
MLB — Professional baseball predictions & results.

This file holds MLB-specific logic extracted from NHL77FINAL.py:
score updates, model grading pipeline, and the full /mlb-results page.

Login, navigation, database, book odds, and multi-sport routing stay in NHL77FINAL.py.
Import helpers lazily via main() to avoid circular imports at module load.
"""
from __future__ import annotations

import time as _time
from collections import defaultdict
from datetime import datetime, timedelta

from sports._sport_base import main, register_shortcut

SPORT = 'MLB'
PICKS_SLUG = 'mlb-picks'
RESULTS_SLUG = 'mlb-results'


def register_routes(app) -> None:
    """Register MLB-only Flask shortcuts (SEO slugs stay in main seo_picks_page)."""
    register_shortcut(app, '/mlb', PICKS_SLUG)


def update_mlb_scores() -> None:
    """Fetch and update MLB scores via ESPN (last 7 days)."""
    main().update_espn_scores(SPORT)


def mlb_model_probs_for_grading(game_row, home_team, away_team, game_date_key):
    """MLB wrapper for shared _model_probs_for_grading."""
    return main()._model_probs_for_grading(SPORT, game_row, home_team, away_team, game_date_key)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    """Render /mlb-results (called from sport_results when sport == MLB).

    Extracted from _render_daily_sport_results_page in NHL77FINAL.py with all
    SOCCER-specific branches removed.  MLB always skips the HTML cache and
    uses date-bounded season queries for the completed-games DB fetch.
    """
    m = main()
    if sport != SPORT:
        return None

    # ---- pre-season gate ------------------------------------------------
    min_live = m._SPORT_MIN_LIVE_DATES.get(sport)
    if min_live and datetime.now() < min_live:
        launch_txt = min_live.strftime('%B %-d, %Y')
        return m._results_fallback_page(
            sport,
            f"{m.SPORTS[sport]['name']} regular season results will appear "
            f"once games begin on {launch_txt}.",
        )

    # ---- cache setup (MLB always skips cache) ---------------------------
    cache_key = f'{sport}_daily_results_html_v2'
    skip_cache = True                       # MLB always rebuilds fresh
    cache_ttl = m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)

    if m._results_date_query_active():
        skip_cache = True

    # ---- open DB connection & season snapshot ---------------------------
    conn = m.get_db_connection()

    snapshot_stats = None
    snapshot_raw = m._load_sport_season_snapshot(sport)
    snapshot_stats = m._stats_from_season_snapshot(snapshot_raw)

    # ---- background score sync ------------------------------------------
    m._start_background_score_sync(sport)

    # ---- fetch completed games from DB ----------------------------------
    prob_sql = m._predictions_prob_select_sql(conn)
    season_start_dt_q, season_end_dt = m._results_season_bounds(sport, datetime.now())
    if season_start_dt is None:
        season_start_dt = season_start_dt_q
    season_end_sql = season_end_dt.strftime('%Y-%m-%d') if season_end_dt else None
    season_start_sql = season_start_dt.strftime('%Y-%m-%d') if season_start_dt else None

    if snapshot_stats:
        card_start = max(
            season_start_dt or (datetime.now() - timedelta(days=30)),
            datetime.now() - timedelta(days=30),
        )
        season_start_sql = card_start.strftime('%Y-%m-%d')
        season_end_sql = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # MLB uses date-bounded query (same as the sport in ('MLB', 'SOCCER') branch)
    if season_start_sql and season_end_sql:
        completed_games = conn.execute(f'''
            SELECT g.*,
                   {prob_sql}
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND g.home_score IS NOT NULL
              AND date(g.game_date) >= ?
              AND date(g.game_date) <= ?
            ORDER BY g.game_date DESC
        ''', (sport, sport, season_start_sql, season_end_sql)).fetchall()
    else:
        completed_games = conn.execute(f'''
            SELECT g.*,
                   {prob_sql}
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND g.home_score IS NOT NULL
            ORDER BY g.game_date DESC
            LIMIT 3000
        ''', (sport, sport)).fetchall()

    completed_games = m._sort_game_rows_by_date_desc(completed_games)
    conn.close()

    # ---- empty results guard --------------------------------------------
    if not completed_games:
        offseason_msg = (
            f" The {m.SPORTS[sport]['name']} season has ended. "
            "Results from the 2025 season will be available next year."
        )
        return m._results_fallback_page(
            sport,
            f"No {m.SPORTS[sport]['name']} results data available yet.{offseason_msg}",
        )

    # ---- process completed games into daily results ---------------------
    daily_results = defaultdict(lambda: {'games': []})
    today_date = datetime.now().strftime('%Y-%m-%d')

    for game in completed_games:
        try:
            home_score = m._to_float_safe(game['home_score'])
            away_score = m._to_float_safe(game['away_score'])
            if home_score is None or away_score is None:
                continue
            home_won = home_score > away_score
            is_draw = False

            home_team = game['home_team_id']
            away_team = game['away_team_id']
            game_date = m._normalize_game_date_key(game['game_date'])

            league_name = None
            try:
                if isinstance(game, dict):
                    league_name = game.get('league')
                else:
                    league_name = game['league'] if 'league' in game.keys() else None
            except Exception:
                pass
            if league_name is None:
                league_name = sport

            # Stored DB probs + frozen v2 backfill
            glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = (
                mlb_model_probs_for_grading(game, home_team, away_team, game_date)
            )

            game_info = {
                'game_id':         game['game_id'],
                'date':            game_date or 'Unknown',
                'home':            home_team,
                'away':            away_team,
                'league':          league_name or sport,
                'home_score':      int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
                'away_score':      int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
                'home_win':        home_won,
                'is_draw':         is_draw,
                'glicko2_prob':    round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':  round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':        round(elo_prob  * 100, 1) if elo_prob  is not None else None,
                'xgb_prob':        round(xgb_prob  * 100, 1) if xgb_prob  is not None else None,
                'ens_prob':        round(ens_prob  * 100, 1) if ens_prob  is not None else None,
                'model_data_note': None,
            }

            # Apply ML grading (non-soccer: draw_dec=None)
            m._soccer_sport._apply_soccer_ml_grading(
                game_info,
                draw_dec=None,
                glicko2_prob=glicko2_prob,
                trueskill_prob=trueskill_prob,
                elo_prob=elo_prob,
                xgb_prob=xgb_prob,
                ens_prob=ens_prob,
                home_won=home_won,
                is_draw=is_draw,
            )

            daily_results[game_info['date']]['games'].append(game_info)
        except Exception as _row_err:
            _gid = None
            try:
                _gid = game['game_id']
            except Exception:
                pass
            m.logger.warning(f"Skipping {sport} results row (game_id={_gid}): {_row_err}")
            continue

    # ---- date sorting & stats -------------------------------------------
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

    if (
        _st_stats
        and int(_st_stats.get('total_graded') or 0) == 0
        and int((overall_stats or {}).get('ensemble', {}).get('total') or 0) > 0
    ):
        m.logger.warning(
            f"[{sport}] results O/U still 0 graded after book attach "
            f"(check /data betting_lines totals + pl_book_odds_api on Render)"
        )

    # ---- tally bundles & ROI --------------------------------------------
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

    # ---- render template ------------------------------------------------
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

    # ---- cache the rendered page ----------------------------------------
    if (
        not m._results_date_query_active()
        and m._daily_results_game_count(daily_results)
        and m._results_page_html_usable(rendered)
    ):
        m._trim_cache(m._SPORT_RESULTS_CACHE, m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
        m._SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}

    return rendered

# === Extracted MLB Logic ===

def _apply_mlb_spread_fade_batch(sport, items) -> None:
    main()._apply_model_fades_batch(sport, items)

def _mlb_pitcher_quality_tier(era, xera=None, whip=None, kbb=None, recent_form=None):
    """Return pitcher tier + score using available run-prevention indicators."""
    score = 0.0
    count = 0
    for v in (era, xera):
        if v is None:
            continue
        count += 1
        if v <= 3.15:
            score += 1.0
        elif v <= 3.7:
            score += 0.75
        elif v <= 4.3:
            score += 0.5
        elif v <= 4.9:
            score += 0.3
        else:
            score += 0.1
    if whip is not None:
        count += 1
        if whip <= 1.10:
            score += 1.0
        elif whip <= 1.22:
            score += 0.75
        elif whip <= 1.32:
            score += 0.5
        elif whip <= 1.45:
            score += 0.3
        else:
            score += 0.1
    if kbb is not None:
        count += 1
        if kbb >= 4.0:
            score += 1.0
        elif kbb >= 3.0:
            score += 0.75
        elif kbb >= 2.2:
            score += 0.5
        elif kbb >= 1.6:
            score += 0.3
        else:
            score += 0.1
    if recent_form is not None:
        count += 1
        if recent_form <= 2.8:
            score += 1.0
        elif recent_form <= 3.5:
            score += 0.75
        elif recent_form <= 4.2:
            score += 0.5
        elif recent_form <= 5.0:
            score += 0.3
        else:
            score += 0.1
    avg = (score / count) if count else 0.5
    if avg >= 0.86:
        return 'elite', avg
    if avg >= 0.67:
        return 'above_avg', avg
    if avg >= 0.45:
        return 'average', avg
    if avg >= 0.30:
        return 'below_avg', avg
    return 'replacement', avg

def _mlb_recent_pitcher_form(pitcher_name):
    """Approximate last-3-start form from recent game logs in local DB."""
    if not pitcher_name:
        return None
    try:
        conn = main().get_db_connection()
        rows = conn.execute(
            '''
            SELECT ERA
            FROM player_game_logs
            WHERE sport='MLB' AND player_name=?
            ORDER BY game_date DESC
            LIMIT 3
            ''',
            (pitcher_name,),
        ).fetchall()
        conn.close()
        vals = []
        for r in rows:
            try:
                vals.append(float(r['ERA']))
            except Exception:
                continue
        if not vals:
            return None
        return sum(vals) / len(vals)
    except Exception:
        return None

def _mlb_lineup_tier(position):
    p = str(position or '').upper()
    if p in {'SS', 'CF', '1B', '3B', 'DH'}:
        return 1
    if p in {'2B', 'LF', 'RF', 'C'}:
        return 2
    return 3

def _mlb_bullpen_fatigue_boost(team_name, game_date):
    """Estimate bullpen fatigue from prior game timing + runs allowed."""
    if not team_name or not game_date:
        return 0.0, 0.0, False
    gday = str(game_date)[:10]
    cache_key = f"{team_name}|{gday}"
    cached = _MLB_BULLPEN_FATIGUE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        conn = main().get_db_connection()
        row = conn.execute(
            '''
            SELECT date(game_date) AS d,
                   CASE WHEN home_team_id=? THEN away_score ELSE home_score END AS runs_allowed
            FROM games
            WHERE sport='MLB'
              AND (home_team_id=? OR away_team_id=?)
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND date(game_date) < date(?)
            ORDER BY date(game_date) DESC
            LIMIT 1
            ''',
            (team_name, team_name, team_name, gday),
        ).fetchone()
        conn.close()
        if not row or not row['d']:
            if len(_MLB_BULLPEN_FATIGUE_CACHE) > 500:
                for _k in list(_MLB_BULLPEN_FATIGUE_CACHE)[:200]:
                    _MLB_BULLPEN_FATIGUE_CACHE.pop(_k, None)
            _MLB_BULLPEN_FATIGUE_CACHE[cache_key] = (0.0, 0.0, False)
            return 0.0, 0.0, False
        prev = datetime.strptime(row['d'], '%Y-%m-%d')
        cur = datetime.strptime(gday, '%Y-%m-%d')
        is_b2b = (cur - prev).days <= 1
        runs_allowed = float(row['runs_allowed']) if row['runs_allowed'] is not None else 4.0
        boost = 0.0
        total_adj = 0.0
        if is_b2b and runs_allowed >= 6:
            boost = 0.02
            total_adj = 0.5
        elif is_b2b:
            boost = 0.01
            total_adj = 0.5
        if len(_MLB_BULLPEN_FATIGUE_CACHE) > 500:
            for _k in list(_MLB_BULLPEN_FATIGUE_CACHE)[:200]:
                _MLB_BULLPEN_FATIGUE_CACHE.pop(_k, None)
        _MLB_BULLPEN_FATIGUE_CACHE[cache_key] = (boost, total_adj, is_b2b)
        return boost, total_adj, is_b2b
    except Exception:
        return 0.0, 0.0, False

def _set_mlb_spread_pick_label(card: dict) -> None:
    """Run-line pick label from faded model spread (same sign as disp_pl_spread)."""
    sp = main()._safe_float(card.get('disp_pl_spread'))
    if sp is None:
        sp = main()._safe_float(main()._best_pl_spread(card))
    if sp is None:
        sp = main()._first_pred_float(card, ('our_spread', 'xgb_spread'))
    if sp is None:
        return
    h = card.get('home_team_id') or card.get('home')
    a = card.get('away_team_id') or card.get('away')
    if not (h and a):
        return
    run_line = 1.5
    pick_team = h if sp > 0 else a
    card['spread_pick_label'] = f"{pick_team} {-run_line:+.1f}"

def _apply_mlb_ou_calibration(daily_results):
    """Cold-streak calibration for MLB totals.

    Runs after total projections are set but before display. Adjusts ONLY live
    (ungraded) O/U picks — graded results, moneyline and spread are never touched.
    """
    try:
        from sports.mlb_ou_calibration import (
            compute_ou_performance, apply_mlb_ou_calibration,
        )
    except Exception as _imp_e:
        logger.error(f"[MLB O/U] calibration import failed: {_imp_e}")
        return None

    graded = []   # (date, correct) for graded, non-push MLB totals
    live = []     # live cards to calibrate
    for date_key, dd in daily_results.items():
        for g in dd.get('games', []):
            pick = g.get('total_pick')
            if pick not in ('OVER', 'UNDER'):
                continue
            correct = g.get('total_correct')
            game_date = g.get('game_date') or date_key
            if correct is not None:
                graded.append((game_date, bool(correct)))
                continue
            # Live card: normalise the inputs the calibration layer needs.
            proj = main()._safe_float(g.get('xgb_total_adj'))
            if proj is None:
                proj = main()._safe_float(g.get('our_total'))
            mkt = main()._safe_float(g.get('market_total'))
            edge = (proj - mkt) if (proj is not None and mkt is not None) else 0.0
            g['total_edge'] = round(edge, 2)
            if g.get('total_confidence') is None:
                g['total_confidence'] = round(min(72.0, 55.0 + abs(edge) * 6.0), 1)
            live.append(g)

    if not live:
        return None

    perf = compute_ou_performance(graded)
    summary = apply_mlb_ou_calibration(live, perf)
    main().logger.info(
        "[MLB O/U] calibration mode=%s flips=%s cautioned=%s last30=%s/%s season=%s/%s recent7=%s",
        summary.get('mode'), summary.get('flips'), summary.get('cautioned'),
        perf.get('last30_rate'), perf.get('last30_n'),
        perf.get('season_rate'), perf.get('season_n'), perf.get('recent7_rate'),
    )
    return summary

