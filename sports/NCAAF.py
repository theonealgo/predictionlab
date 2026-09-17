"""
NCAAF — College football predictions & results.

This file holds NCAAF-specific logic extracted from NHL77FINAL.py:
score syncing, the full /ncaaf-results rendering pipeline, and route shortcuts.

Login, navigation, database, book odds, and multi-sport routing stay in NHL77FINAL.py.
Import helpers lazily via main() to avoid circular imports at module load.
"""
from __future__ import annotations

import html as html_mod
import re
import time as _time
from collections import defaultdict
from datetime import datetime, timedelta

from sports._sport_base import main, register_shortcut

SPORT = 'NCAAF'
PICKS_SLUG = 'ncaaf-picks'
RESULTS_SLUG = 'ncaaf-results'


def register_routes(app) -> None:
    """Register NCAAF-only Flask shortcuts (SEO slugs stay in main seo_picks_page)."""
    register_shortcut(app, '/ncaaf', PICKS_SLUG)


def update_ncaaf_scores() -> None:
    """Fetch and update NCAAF scores via ESPN (last 7 days)."""
    main().update_espn_scores(SPORT)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    """Render /ncaaf-results (called from sport_results when sport == NCAAF)."""
    m = main()
    if sport != SPORT:
        return None

    # ── Launch gate ─────────────────────────────────────────────────────
    min_live = m._SPORT_MIN_LIVE_DATES.get(sport)
    if min_live and datetime.now() < min_live:
        launch_txt = min_live.strftime('%B %-d, %Y')
        return m._results_fallback_page(
            sport,
            f"{m.SPORTS[sport]['name']} regular season results will appear once games begin on {launch_txt}."
        )

    # ── Cache check ─────────────────────────────────────────────────────
    cache_key = f'{sport}_daily_results_html_v2'
    cache_ttl = m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 240)
    skip_cache = m._results_date_query_active()

    if not skip_cache:
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
            stale_html, _ = m._stale_page_cache_get(m._SPORT_RESULTS_CACHE, cache_key, cache_ttl)
            if stale_html and m._results_page_html_usable(stale_html):
                return stale_html

    # ── Season snapshot ─────────────────────────────────────────────────
    snapshot_raw = m._load_sport_season_snapshot(sport)
    snapshot_stats = m._stats_from_season_snapshot(snapshot_raw)

    # ── Background score sync ───────────────────────────────────────────
    m._start_background_score_sync(sport)

    # ── DB query for completed games ────────────────────────────────────
    conn = m.get_db_connection()
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

    # Non-MLB/non-SOCCER path: fetch all with LIMIT 3000, filter in Python
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
            if m._date_in_range(m._normalize_game_date_key(g['game_date']), season_start_dt, season_end_dt)
        ]

    completed_games = m._sort_game_rows_by_date_desc(completed_games)
    conn.close()

    if not completed_games:
        return m._results_fallback_page(
            sport,
            f"No {m.SPORTS[sport]['name']} results data available yet."
        )

    # ── Process games into daily results ────────────────────────────────
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

            league_name = sport

            # Stored DB probs + frozen v2 backfill
            glicko2_prob, trueskill_prob, elo_prob, xgb_prob, ens_prob = m._model_probs_for_grading(
                sport, game, home_team, away_team, game_date,
            )

            game_info = {
                'game_id':         game['game_id'],
                'date':            game_date or 'Unknown',
                'home':            home_team,
                'away':            away_team,
                'league':          league_name,
                'home_score':      int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
                'away_score':      int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
                'home_win':        home_won,
                'is_draw':         is_draw,
                'glicko2_prob':    round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':  round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':        round(elo_prob  * 100, 1) if elo_prob is not None else None,
                'xgb_prob':        round(xgb_prob  * 100, 1) if xgb_prob is not None else None,
                'ens_prob':        round(ens_prob  * 100, 1) if ens_prob is not None else None,
                'model_data_note': None,
            }

            # Apply ML grading (handles generic non-soccer case with draw_dec=None)
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

    # ── Dates & stats ───────────────────────────────────────────────────
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

    if _st_stats and int(_st_stats.get('total_graded') or 0) == 0 and int((overall_stats or {}).get('ensemble', {}).get('total') or 0) > 0:
        m.logger.warning(
            f"[{sport}] results O/U still 0 graded after book attach "
            f"(check /data betting_lines totals + pl_book_odds_api on Render)"
        )

    # ── Tallies & ROI ───────────────────────────────────────────────────
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
    roi_cards = m.build_roi_cards(roi_daily, roi_weekly, roi_total, win_pct_primary=True)

    # ── Render template ─────────────────────────────────────────────────
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
        soccer_leagues=None,
        results_stale_notice=results_stale_notice,
        results_snapshot_notice=None,
        selected_league=None,
        selected_league_slug=None,
        league_db_total=None,
    )

    # ── Cache rendered HTML ─────────────────────────────────────────────
    if (
        not m._results_date_query_active()
        and m._daily_results_game_count(daily_results)
        and m._results_page_html_usable(rendered)
    ):
        m._trim_cache(m._SPORT_RESULTS_CACHE, m._SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300), max_entries=50)
        m._SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}

    return rendered


def render_ncaaf_results_chart_page():
    """MLB team-results template for /ncaaf-results?view=chart."""
    m = main()
    from mlb_results_ui import markets_from_live_html, render_team_results_chart_page

    market = ""
    try:
        from flask import request

        market = (request.args.get("market") or "").strip().lower()
    except Exception:
        market = ""
    cards = ""
    for key in (
        "NCAAF_daily_results_html_v4",
        "NCAAF_daily_results_html_v3",
        "NCAAF_daily_results_html_v2",
    ):
        cached = m._SPORT_RESULTS_CACHE.get(key)
        if isinstance(cached, dict):
            html = cached.get("html")
            if html and len(html) > 500:
                cards = html
                break
    payload = None
    if cards:
        try:
            from team_results_charts import set_results_chart_source

            set_results_chart_source("NCAAF", cards)
            payload = markets_from_live_html(cards, "ncaaf")
        except Exception as exc:
            m.logger.exception("NCAAF chart payload failed: %s", exc)
    html = render_team_results_chart_page("ncaaf", payload=payload, market=market)
    if html and "ncaaf-results-chart" not in html:
        html = re.sub(
            r"<body\b([^>]*)>",
            r'<body class="ncaaf-results-chart"\1>',
            html,
            count=1,
            flags=re.I,
        )
    html = _ncaaf_chart_best_width(html)
    html = _ncaaf_chart_sou_compare(html, payload, market)
    return html


_NCAAF_CHART_BEST_CSS = """<style id="ncaaf-chart-best-width">
body.ncaaf-results-chart section.pl-analytics{width:100%!important}
body.ncaaf-results-chart section.pl-analytics .tally-grid{
  display:grid!important;
  grid-template-columns:repeat(3,minmax(0,1fr))!important;
  width:100%!important;
}
</style>"""


def _ncaaf_chart_best_width(html: str) -> str:
    if not html or "ncaaf-chart-best-width" in html:
        return html
    if re.search(r"</head\s*>", html, flags=re.I):
        return re.sub(r"</head\s*>", _NCAAF_CHART_BEST_CSS + "</head>", html, count=1, flags=re.I)
    return _NCAAF_CHART_BEST_CSS + html


def _ncaaf_line_compare(row: dict, market: str) -> str:
    aw, hs = row.get("away_score"), row.get("home_score")
    block = row.get(market) if isinstance(row.get(market), dict) else {}
    if aw is None or hs is None:
        return "—"
    if market == "totals":
        actual = float(aw) + float(hs)
        bits = [f"Act {actual:g}"]
        bookn = block.get("book_line")
        pln = block.get("pl_line")
        def _vs(n, label):
            if n is None:
                return
            try:
                n = float(n)
            except (TypeError, ValueError):
                return
            hit = "Over" if actual > n else "Under" if actual < n else "Push"
            bits.append(f"{label} {n:g} {hit}")
        _vs(bookn, "Books")
        _vs(pln, "PL")
        return " · ".join(bits)
    book = html_mod.unescape(str(block.get("book") or block.get("book_line") or "—"))
    pl = html_mod.unescape(str(block.get("pl_pick") or block.get("pl_line") or block.get("pick") or "—"))
    return f"Act {aw}–{hs} · Books {book} · PL {pl}"


def _ncaaf_chart_sou_compare(html: str, payload: dict | None, market: str) -> str:
    """Add Actual vs lines on NCAAF spread/totals SSR tables."""
    if not html or (market or "").lower() not in ("spread", "totals"):
        return html
    mk = market.lower()
    split = re.split(r'(<section id="ssr-finals"[^>]*>)', html, maxsplit=1, flags=re.I)
    if len(split) < 3:
        return html
    prefix, start, rest = split[0], split[1], split[2]
    end_m = re.search(r"</section>", rest, flags=re.I)
    if not end_m:
        return html
    section, after = rest[: end_m.end()], rest[end_m.end() :]
    section = re.sub(
        r"<th>H2H L10</th>",
        "<th>Actual vs lines</th>",
        section,
        count=1,
        flags=re.I,
    )
    section = re.sub(
        r"<th>Book</th>",
        "<th>Books</th>",
        section,
        count=1,
        flags=re.I,
    )
    finals = []
    if isinstance(payload, dict):
        markets = payload.get("markets") or {}
        block = markets.get(mk) or {}
        finals = block.get("finals") or payload.get("finals") or []
    by_match = {}
    for f in finals:
        if not isinstance(f, dict):
            continue
        away = html_mod.unescape(html_mod.unescape(str(f.get("away_team_id") or f.get("away") or "")))
        home = html_mod.unescape(html_mod.unescape(str(f.get("home_team_id") or f.get("home") or "")))
        if away and home:
            by_match[f"{away} @ {home}"] = _ncaaf_line_compare(f, mk)

    def _row(m: re.Match[str]) -> str:
        cells = m.group(0)
        tds = re.findall(r"<td>[\s\S]*?</td>", cells)
        if len(tds) != 8:
            return cells
        match_key = html_mod.unescape(html_mod.unescape(re.sub(r"<[^>]+>", "", tds[1]).strip()))
        score = re.sub(r"<[^>]+>", "", tds[2]).strip()
        book = html_mod.unescape(html_mod.unescape(re.sub(r"<[^>]+>", "", tds[3]).strip()))
        pl = html_mod.unescape(html_mod.unescape(re.sub(r"<[^>]+>", "", tds[5]).strip()))
        cmp = by_match.get(match_key) or ""
        if not cmp or cmp == "—":
            if mk == "totals":
                nums = re.findall(r"\d+", score.replace("–", "-"))
                if len(nums) >= 2:
                    actual = int(nums[0]) + int(nums[1])
                    bits = [f"Act {actual}"]
                    for raw, label in ((book, "Books"), (pl, "PL")):
                        nm = re.search(r"(\d+(?:\.\d+)?)", raw or "")
                        if not nm:
                            continue
                        n = float(nm.group(1))
                        hit = "Over" if actual > n else "Under" if actual < n else "Push"
                        bits.append(f"{label} {n:g} {hit}")
                    cmp = " · ".join(bits)
                else:
                    cmp = "—"
            else:
                cmp = f"Act {score} · Books {book} · PL {pl}"
        cmp = (
            cmp.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        tds[4] = f"<td>{cmp}</td>"
        return "<tr>" + "".join(tds) + "</tr>"

    section = re.sub(r"<tr>\s*<td>[\s\S]*?</tr>", _row, section)
    return prefix + start + section + after


# === Extracted Dead Code Logic ===
