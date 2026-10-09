"""
NCAAB — Men's college basketball predictions & results.

This file holds NCAAB-specific logic extracted from NHL77FINAL.py:
score syncing, the full /ncaab-results rendering pipeline, and route shortcuts.

Login, navigation, database, book odds, and multi-sport routing stay in NHL77FINAL.py.
Import helpers lazily via main() to avoid circular imports at module load.
"""
from __future__ import annotations

import re
import time as _time
from collections import defaultdict
from datetime import datetime, timedelta

from sports._sport_base import main, register_shortcut

SPORT = 'NCAAB'
PICKS_SLUG = 'ncaab-picks'
RESULTS_SLUG = 'ncaab-results'


def register_routes(app) -> None:
    """Register NCAAB-only Flask shortcuts (SEO slugs stay in main seo_picks_page)."""
    register_shortcut(app, '/ncaab', PICKS_SLUG)


def update_ncaab_scores() -> None:
    """Fetch and update NCAAB scores via ESPN (last 7 days)."""
    main().update_espn_scores(SPORT)


def ensure_graded_games_chart(html: str) -> str:
    """Chart view lists the graded finals already on the results cards.

    Does not add games that are not on the page, and does not change the
    Season record block.
    """
    if not html or 'id="ncaab-graded-games"' in html:
        return html
    rows = _graded_rows_from_result_cards(html)
    if not rows:
        return html
    table = _graded_games_chart_html(rows)
    spot = re.search(
        r'<h2[^>]*>\s*Consensus Based Betting Records',
        html,
        flags=re.I,
    )
    if spot:
        return html[: spot.start()] + table + html[spot.start() :]
    if "</main>" in html.lower():
        return re.sub(r"</main>", table + "</main>", html, count=1, flags=re.I)
    return html + table


def _graded_rows_from_result_cards(html: str) -> list[dict]:
    rows: list[dict] = []
    parts = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    seq = parts[1:]
    for date, content in zip(seq[0::2], seq[1::2]):
        bits = re.split(r'(<div class="game-card pick-card")', content)
        idx = 1
        while idx < len(bits):
            card = bits[idx] + (bits[idx + 1] if idx + 1 < len(bits) else "")
            idx += 2
            row = _graded_row_from_card(card, date)
            if row:
                rows.append(row)
    return rows


def _graded_row_from_card(card: str, date: str) -> dict | None:
    names = [
        re.sub(r"\s+", " ", n).strip()
        for n in re.findall(r'class="team-name">([^<]+)', card)
    ]
    scores = [
        re.sub(r"\s+", " ", s).strip()
        for s in re.findall(r'class="final-score[^"]*">\s*([^<]*?)\s*<', card)
    ]
    if len(names) < 2 or len(scores) < 2:
        return None
    away, home = names[0], names[1]
    try:
        away_score = int(float(scores[0]))
        home_score = int(float(scores[1]))
    except ValueError:
        return None
    edge = re.search(
        r'<div class="pc-box([^"]*)"[^>]*>\s*<div class="pc-name">Edge</div>'
        r'[\s\S]*?<div class="pc-val">([^<]*)</div>\s*'
        r'<div class="pc-side[^"]*">([^<]*)</div>',
        card,
        flags=re.I,
    )
    if not edge:
        return None
    klass = edge.group(1).lower()
    if "wrong" in klass:
        result = "Wrong"
    elif "correct" in klass:
        result = "Correct"
    else:
        return None
    pick = re.sub(r"[✅❌]", "", edge.group(3)).strip()
    prob = re.sub(r"\s+", "", edge.group(2)).strip()
    if not pick:
        return None
    models = []
    for box in re.finditer(
        r'<div class="pc-box([^"]*)"[^>]*>\s*<div class="pc-name">([^<]+)</div>'
        r'[\s\S]*?<div class="pc-side[^"]*">([^<]*)</div>',
        card,
        flags=re.I,
    ):
        name = box.group(2).strip()
        side = re.sub(r"[✅❌]", "", box.group(3)).strip()
        if not name or not side:
            continue
        mark = "✓" if "correct" in box.group(1).lower() else "✗" if "wrong" in box.group(1).lower() else ""
        models.append(f"{name} {side} {mark}".strip())
    return {
        "date": date,
        "away": away,
        "home": home,
        "score": f"{away_score}–{home_score}",
        "pick": pick,
        "prob": prob,
        "result": result,
        "models": " ".join(models),
    }


def _graded_games_chart_html(rows: list[dict]) -> str:
    from html import escape

    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{escape(row['date'])}</td>"
            f"<td>NCAAB</td>"
            f"<td>{escape(row['away'])} @ {escape(row['home'])}</td>"
            f"<td>{escape(row['score'])}</td>"
            f"<td>{escape(row['pick'])}</td>"
            f"<td>{escape(row['prob'])}</td>"
            f"<td>{escape(row['result'])}</td>"
            f"<td>{escape(row['models'])}</td>"
            "</tr>"
        )
    return (
        '<section id="ncaab-graded-games" class="pl-consensus-records">'
        '<h2 class="sec-title">Moneyline games '
        f'<span class="tag">({len(rows)})</span></h2>'
        '<div class="table-wrap"><table class="results-table" id="ssr-finals">'
        "<thead><tr><th>Date</th><th>League</th><th>Match</th><th>Score</th>"
        "<th>Edge pick</th><th>%</th><th>Result</th><th>Models</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )


def render_sport_results_page(sport: str, *, season_start_dt=None):
    """Render /ncaab-results (called from sport_results when sport == NCAAB)."""
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
    roi_cards = m.build_roi_cards(roi_daily, roi_weekly, roi_total)

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


def _ncaab_results_cards_html() -> str:
    """Cards HTML for the chart payload. Never the chart URL itself."""
    m = main()
    for key in (
        "NCAAB_daily_results_html_v3",
        "NCAAB_daily_results_html_v2",
    ):
        cached = m._SPORT_RESULTS_CACHE.get(key)
        if isinstance(cached, dict):
            html = cached.get("html") or ""
            if html and html.count("game-card") >= 3:
                return html
    root = __import__("pathlib").Path(__file__).resolve().parent.parent / ".cache"
    for name in ("served_NCAAB_.html", "chart_src_NCAAB.html"):
        path = root / name
        try:
            if path.is_file():
                html = path.read_text(encoding="utf-8", errors="replace")
                if html.count("game-card") >= 3:
                    return html
        except OSError:
            continue
    try:
        from flask import has_request_context

        app = getattr(m, "app", None)
        if app is not None and has_request_context():
            with app.test_request_context("/ncaab-results"):
                html = m.sport_results(SPORT) or ""
                if html and html.count("game-card") >= 3:
                    return html
    except Exception:
        pass
    return ""


_NCAAB_MODEL_ORDER = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _ncaab_team(row: dict, side: str) -> str:
    if side == "home":
        return str(row.get("home") or row.get("home_team") or row.get("home_team_id") or "")
    return str(row.get("away") or row.get("away_team") or row.get("away_team_id") or "")


def _ncaab_model_map(card: str) -> dict:
    """Per-model pick, side, and grade already printed on the result card."""
    from mlb_results_ui import _extract_card_models

    models = _extract_card_models(card) or {}
    for box in re.finditer(
        r'<div class="pc-name">([^<]+)</div>[\s\S]*?<div class="pc-side\s+([^"\s]+)',
        card or "",
        flags=re.I,
    ):
        name = box.group(1).strip()
        side = box.group(2).strip().lower()
        if name in models and side in ("home", "away"):
            models[name]["side"] = side
    return models


def _ncaab_models_label(models: dict | None) -> str:
    """Model result text: name, side picked, and correct/wrong mark."""
    if not isinstance(models, dict) or not models:
        return ""
    names = [n for n in _NCAAB_MODEL_ORDER if n in models]
    names.extend(n for n in models if n not in names)
    bits = []
    for name in names:
        block = models.get(name) or {}
        pick = str(block.get("pick") or "").strip()
        if not pick:
            continue
        if block.get("correct") is True:
            mark = "✓"
        elif block.get("correct") is False:
            mark = "✗"
        else:
            mark = ""
        bits.append(f"{name} {pick} {mark}".strip())
    return " ".join(bits)


def _ncaab_games_section(market: str, title: str, finals: list) -> str:
    """Same graded-games table the team-results chart already paints."""
    from html import escape

    rows = []
    shown = list(finals or [])
    if market == "moneyline":
        for game in shown:
            home = _ncaab_team(game, "home")
            away = _ncaab_team(game, "away")
            hs, aws = game.get("home_score"), game.get("away_score")
            score = f"{aws}–{hs}" if hs is not None and aws is not None else "—"
            face = game.get("face_pick") or "—"
            fp = game.get("face_prob")
            fp_s = f"{fp}%" if fp is not None else "—"
            ok = game.get("correct")
            res = "Correct" if ok is True else "Wrong" if ok is False else "—"
            models = _ncaab_models_label(game.get("models"))
            date = escape(str(game.get("game_date") or "")[:10])
            gid = escape(str(game.get("game_id") or ""))
            rows.append(
                f'<tr data-date="{date}" data-game-id="{gid}">'
                f"<td>{date}</td>"
                f"<td>{escape(str(game.get('league') or 'NCAAB'))}</td>"
                f"<td>{escape(away)} @ {escape(home)}</td>"
                f"<td>{escape(score)}</td>"
                f"<td>{escape(str(face))}</td>"
                f"<td>{escape(fp_s)}</td>"
                f"<td>{escape(res)}</td>"
                f'<td class="mono-models">{escape(models or "—")}</td>'
                "</tr>"
            )
        head = (
            "<thead><tr><th>Date</th><th>League</th><th>Match</th><th>Score</th>"
            "<th>Edge pick</th><th>%</th><th>Result</th><th>Models</th></tr></thead>"
        )
        colspan = 8
        section_id = "ssr-finals"
    else:
        key = "spread" if market == "spread" else "totals"
        for game in shown:
            home = _ncaab_team(game, "home")
            away = _ncaab_team(game, "away")
            hs, aws = game.get("home_score"), game.get("away_score")
            score = f"{aws}–{hs}" if hs is not None and aws is not None else "—"
            block = game.get(key) if isinstance(game.get(key), dict) else {}
            book = block.get("book") or block.get("book_line") or "—"
            pl = block.get("pl_pick") or block.get("pl_line") or block.get("pick") or "—"
            xs = block.get("xs_pick") or block.get("xs_line") or "—"
            ok = block.get("correct")
            push = block.get("push")
            if ok is None and not push and key == "spread" and hs is not None and aws is not None:
                # No stored grade: grade the published PL line against the final score.
                import re as _re
                _m = _re.match(r"^(.*?)\s*([+-]\d+(?:\.\d+)?)\s*$", str(block.get("pl_pick") or ""))
                if _m:
                    _team, _line = _m.group(1).strip().lower(), float(_m.group(2))
                    try:
                        _hn, _an = str(home).strip().lower(), str(away).strip().lower()
                        _side = None
                        if _team and (_team == _hn or _team in _hn or _hn in _team):
                            _side = float(hs) - float(aws)
                        elif _team and (_team == _an or _team in _an or _an in _team):
                            _side = float(aws) - float(hs)
                        if _side is not None:
                            _cover = _side + _line
                            if abs(_cover) < 1e-9:
                                push = True
                            else:
                                ok = _cover > 0
                    except (TypeError, ValueError):
                        pass
            res = (
                "Push" if push
                else "Correct" if ok is True
                else "Wrong" if ok is False
                else "—"
            )
            h2h = game.get("h2h10") or game.get("h2h_l10") or "—"
            rows.append(
                "<tr>"
                f"<td>{escape(str(game.get('game_date') or '')[:10])}</td>"
                f"<td>{escape(away)} @ {escape(home)}</td>"
                f"<td>{escape(score)}</td>"
                f"<td>{escape(str(book))}</td>"
                f"<td>{escape(str(h2h))}</td>"
                f"<td>{escape(str(pl))}</td>"
                f"<td>{escape(str(xs))}</td>"
                f"<td>{escape(res)}</td>"
                "</tr>"
            )
        head = (
            "<thead><tr><th>Date</th><th>Match</th><th>Score</th>"
            "<th>Book</th><th>H2H L10</th><th>PL</th><th>XSharp</th><th>Result</th></tr></thead>"
        )
        colspan = 8
        section_id = "ssr-finals-spread" if market == "spread" else "ssr-finals-totals"
    body = "".join(rows) or f'<tr><td colspan="{colspan}" class="muted">No finals.</td></tr>'
    return (
        f'<section id="{section_id}" data-ssr-market="{market}">'
        f'<h2 class="sec-title">{escape(title)} <span class="tag">({len(shown)})</span></h2>'
        '<div class="table-wrap"><table class="results-table">'
        f"{head}<tbody>{body}</tbody></table></div></section>"
    )


def _ncaab_ensure_market_lists(html: str, payload: dict | None) -> str:
    """Chart page keeps Moneyline, Spread, and Totals graded lists together."""
    markets = {}
    if isinstance(payload, dict):
        markets = payload.get("markets") or {}
    titles = (
        ("moneyline", "Moneyline games"),
        ("spread", "Spread games"),
        ("totals", "Totals records"),
    )
    missing = []
    for key, title in titles:
        if title in (html or ""):
            continue
        block = markets.get(key) if isinstance(markets.get(key), dict) else {}
        finals = list((block or {}).get("finals") or [])
        if not finals and isinstance(payload, dict) and key == "moneyline":
            finals = list(payload.get("finals") or [])
        missing.append(_ncaab_games_section(key, title, finals))
    if not missing:
        return html
    block = "".join(missing)
    spot = re.search(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
        html or "",
        flags=re.I,
    )
    if spot:
        at = spot.end()
        return html[:at] + block + html[at:]
    if re.search(r"</main>", html or "", flags=re.I):
        return re.sub(r"</main>", block + "</main>", html, count=1, flags=re.I)
    return (html or "") + block


def _ncaab_chart_finals(html: str) -> list[dict]:
    """Graded chart rows from the cards already on /ncaab-results.

    NCAAB cards use team-slot, not team-col, so the shared extractor drops them.
    Spread and total marks are the checkmarks already printed on each card.
    """
    from mlb_results_ui import _extract_spread_totals

    finals: list[dict] = []
    parts = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    seq = parts[1:]
    for date, content in zip(seq[0::2], seq[1::2]):
        bits = re.split(r'(<div class="game-card pick-card")', content)
        idx = 1
        while idx < len(bits):
            card = bits[idx] + (bits[idx + 1] if idx + 1 < len(bits) else "")
            idx += 2
            graded = _graded_row_from_card(card, date)
            if not graded:
                continue
            spread, totals = _extract_spread_totals(card)
            gid_m = re.search(r'data-game-id="([^"]*)"', card, flags=re.I)
            h2h_m = re.search(
                r'H2H Last 10</span>\s*<span class="sf-val">([^<]+)',
                card,
                flags=re.I,
            )
            try:
                away_score, home_score = [
                    int(x) for x in graded["score"].split("–")
                ]
            except ValueError:
                away_score = home_score = None
            prob = graded.get("prob") or ""
            face_prob = None
            pm = re.search(r"(\d+(?:\.\d+)?)", prob)
            if pm:
                try:
                    face_prob = float(pm.group(1))
                except ValueError:
                    face_prob = None
            finals.append(
                {
                    "game_date": graded["date"],
                    "league": "NCAAB",
                    "away": graded["away"],
                    "home": graded["home"],
                    "away_team_id": graded["away"],
                    "home_team_id": graded["home"],
                    "away_score": away_score,
                    "home_score": home_score,
                    "face_pick": graded["pick"],
                    "face_prob": face_prob,
                    "correct": graded["result"] == "Correct",
                    "spread": spread,
                    "totals": totals,
                    "h2h10": (h2h_m.group(1).strip() if h2h_m else ""),
                    "models": _ncaab_model_map(card),
                    "game_id": (gid_m.group(1).strip() if gid_m else "") or None,
                }
            )
    return finals


def _ncaab_attach_finals(payload: dict | None, finals: list[dict]) -> dict:
    if not isinstance(payload, dict):
        payload = {"ok": bool(finals), "markets": {}}
    payload["finals"] = finals
    payload["ok"] = bool(finals) or bool(payload.get("ok"))
    markets = payload.get("markets")
    if not isinstance(markets, dict):
        markets = {}
        payload["markets"] = markets
    markets.setdefault("moneyline", {})["finals"] = finals
    markets.setdefault("spread", {})["finals"] = [g for g in finals if g.get("spread")] or finals
    markets.setdefault("totals", {})["finals"] = [g for g in finals if g.get("totals")] or finals
    return payload


def render_ncaab_results_chart_page():
    """Team-results chart for /ncaab-results?view=chart.

    Cards|Chart toggle, season tallies, consensus, and the Moneyline,
    Spread, and Totals graded game lists. Cards view is unchanged.
    """
    m = main()
    from mlb_results_ui import markets_from_live_html, render_team_results_chart_page

    market = ""
    try:
        from flask import request

        market = (request.args.get("market") or "").strip().lower()
    except Exception:
        market = ""
    cards = _ncaab_results_cards_html()
    payload = None
    if cards:
        try:
            from team_results_charts import set_results_chart_source

            set_results_chart_source("NCAAB", cards)
            payload = markets_from_live_html(cards, "ncaab")
        except Exception as exc:
            m.logger.exception("NCAAB chart payload failed: %s", exc)
        payload = _ncaab_attach_finals(payload, _ncaab_chart_finals(cards))
    html = render_team_results_chart_page("ncaab", payload=payload, market=market)
    html = _ncaab_ensure_market_lists(html or "", payload)
    html = _ncaab_refresh_moneyline_rows(html or "", payload)
    html = _ncaab_stamp_last_night_date(html or "")
    html = select_latest_results_date(html or "")
    html = _ncaab_fill_results_list(html or "")
    return publicize_ncaab_share_hrefs(html or "")


def _ncaab_refresh_moneyline_rows(html: str, payload: dict | None) -> str:
    """Replace the moneyline placeholder cell. Spread and Totals lists stay."""
    markets = (payload or {}).get("markets") if isinstance(payload, dict) else {}
    block = markets.get("moneyline") if isinstance(markets, dict) else {}
    finals = list((block or {}).get("finals") or [])
    if not finals and isinstance(payload, dict):
        finals = list(payload.get("finals") or [])
    if not finals:
        return html
    section = _ncaab_games_section("moneyline", "Moneyline games", finals)
    replaced, n = re.subn(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
        lambda _m: section,
        html or "",
        count=1,
        flags=re.I,
    )
    if n:
        return replaced
    return (html or "") + section


def select_latest_results_date(html: str) -> str:
    """The date strip opens on the latest stored final, not the oldest option."""
    if not html or 'id="datePicker"' not in html:
        return html
    match = re.search(r'(<select id="datePicker">)(.*?)(</select>)', html, flags=re.S)
    if not match:
        return html
    dates = re.findall(r'value="(\d{4}-\d{2}-\d{2})"', match.group(2))
    if not dates:
        return html
    latest = max(dates)
    inner = re.sub(r"\sselected\b", "", match.group(2))
    inner = inner.replace(
        f'<option value="{latest}">',
        f'<option value="{latest}" selected>',
        1,
    )
    return html[: match.start(2)] + inner + html[match.end(2) :]


def _ncaab_stamp_last_night_date(html: str) -> str:
    """Game-count check reads Last Night's … — YYYY-MM-DD (N games), not a table cell."""
    m = re.search(
        r"<h2>\s*Last Night\s*<span class=\"tag\">\((\d+)\s*games?\)</span>\s*</h2>",
        html or "",
        flags=re.I,
    )
    if not m:
        return html
    date_m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", html or "", flags=re.I)
    if not date_m:
        return html
    heading = (
        "<h2>Last Night's NCAA Basketball Results — "
        f"{date_m.group(1)} ({m.group(1)} games)</h2>"
    )
    return html[: m.start()] + heading + html[m.end() :]


def _ncaab_fill_results_list(html: str) -> str:
    """Copy moneyline rows into #results-body, the games list beside the tallies."""
    sec = re.search(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][^>]*\bdata-ssr-market=["\']moneyline["\'][\s\S]*?</section>',
        html or "",
        flags=re.I,
    )
    if not sec:
        return html
    block = sec.group(0)
    body = re.search(r"<tbody>([\s\S]*?)</tbody>", block, flags=re.I)
    head = re.search(r"<thead>([\s\S]*?)</thead>", block, flags=re.I)
    if not body:
        return html
    html = re.sub(
        r'(<tbody\b[^>]*\bid=["\']results-body["\'][^>]*>)[\s\S]*?(</tbody>)',
        lambda m, inner=body.group(1): m.group(1) + inner + m.group(2),
        html,
        count=1,
        flags=re.I,
    )
    if head:
        html = re.sub(
            r'(<thead\b[^>]*\bid=["\']results-head["\'][^>]*>)[\s\S]*?(</thead>)',
            lambda m, inner=head.group(1): m.group(1) + inner + m.group(2),
            html,
            count=1,
            flags=re.I,
        )
    n_rows = body.group(1).count("<tr")
    html = re.sub(
        r'(<span\b[^>]*\bid=["\']game-count["\'][^>]*>)[\s\S]*?(</span>)',
        lambda m, n_rows=n_rows: f"{m.group(1)}({n_rows}){m.group(2)}",
        html,
        count=1,
        flags=re.I,
    )
    return html


def _ncaab_div_span(html: str, marker: str, limit: int) -> tuple[int, int] | None:
    """Outer div that contains marker, searching only before limit."""
    idx = (html or "").rfind(marker, 0, limit)
    if idx < 0:
        return None
    start = html.rfind("<div", 0, idx + 1)
    if start < 0:
        return None
    depth = 0
    i = start
    lower = html.lower()
    while i < len(html):
        if lower.startswith("<div", i):
            depth += 1
            i += 4
            continue
        if lower.startswith("</div", i):
            depth -= 1
            if depth == 0:
                end = html.find(">", i)
                if end < 0:
                    return None
                return start, end + 1
            i += 5
            continue
        i += 1
    return None


def _ncaab_stored_totals_pl() -> list[str]:
    """Prediction Lab totals grades already printed on picks Recent results.

    Last Night, Last 7, Last 30. Empty strings when that window is not stored.
    """
    block = ""
    try:
        from picks_recent_results import _ncaab_strip_from_stored_cards

        block = _ncaab_strip_from_stored_cards() or ""
    except Exception:
        block = ""
    if "picks-recent-mkt" not in block:
        return ["", "", ""]
    windows = ("last night", "last 7", "last 30")
    grades = ["", "", ""]
    for part in re.split(r'<div class="picks-recent-col">', block)[1:]:
        head = re.search(r"<h3>(.*?)</h3>", part, flags=re.I | re.S)
        title = re.sub(r"<[^>]+>", "", head.group(1) if head else "")
        title = re.sub(r"\s+", " ", title).strip().lower()
        slot = next((i for i, name in enumerate(windows) if name in title), None)
        if slot is None:
            continue
        tot = re.search(
            r'picks-recent-mkt">Totals</h4>\s*<table[\s\S]*?</table>',
            part,
            flags=re.I,
        )
        if not tot:
            continue
        for row in re.findall(r"<tr>([\s\S]*?)</tr>", tot.group(0), flags=re.I):
            cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row, flags=re.I)
            if len(cells) < 2:
                continue
            label = re.sub(r"<[^>]+>", "", cells[0])
            label = re.sub(r"\s+", " ", label).strip().lower()
            if label != "prediction lab":
                continue
            text = re.sub(r"<[^>]+>", "", cells[1])
            text = re.sub(r"\s+", " ", text).strip()
            if re.search(r"\d+-\d+", text):
                grades[slot] = text
    return grades


def _ncaab_fill_totals_chart(html: str) -> str:
    """Copy stored Prediction Lab totals grades into the totals chart row."""
    from html import escape

    if not html or 'id="pl-totals-records"' not in html:
        return html
    grades = _ncaab_stored_totals_pl()
    if not any(grades):
        return html
    start = html.find('id="pl-totals-records"')
    div_start = html.rfind("<div", 0, start + 1)
    if div_start < 0:
        return html
    span = _ncaab_div_span(html, 'id="pl-totals-records"', len(html))
    if not span:
        return html
    block = html[span[0] : span[1]]
    row = re.search(
        r'(<td class="bucket">\s*Prediction Lab\s*</td>\s*'
        r'<td\b[^>]*>)([\s\S]*?)(</td>\s*'
        r'<td\b[^>]*>)([\s\S]*?)(</td>\s*'
        r'<td\b[^>]*>)([\s\S]*?)(</td>)',
        block,
        flags=re.I,
    )
    if not row:
        return html
    parts = []
    last = 0
    for idx, grade in enumerate(grades):
        inner_i = 2 + idx * 2
        parts.append(block[last : row.start(inner_i)])
        shown = grade if grade else re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", row.group(inner_i))).strip()
        parts.append(escape(shown) if grade else row.group(inner_i))
        last = row.end(inner_i)
    parts.append(block[last:])
    block = "".join(parts)
    if all(grades):
        note = (
            f"Prediction Lab Last Night {grades[0]}. "
            f"Last 7 {grades[1]}. Last 30 {grades[2]}. XSharp —."
        )
        block = re.sub(
            r'<p class="cons-read">[\s\S]*?</p>',
            f'<p class="cons-read">{escape(note)}</p>',
            block,
            count=1,
            flags=re.I,
        )
    return html[: span[0]] + block + html[span[1] :]


_NCAAB_LAYOUT_CSS = """
<style id="ncaab-owner-fixes">
.pl-consensus-records table,
.pl-books-pl-records table,
#pl-totals-records table,
#pl-spread-records table,
#three-way-totals table{
  display:table!important;
  width:100%!important;
  border-collapse:separate!important;
  border-spacing:0!important;
}
.pl-consensus-records th,
.pl-consensus-records td,
.pl-books-pl-records th,
.pl-books-pl-records td,
#pl-totals-records th,
#pl-totals-records td,
#pl-spread-records th,
#pl-spread-records td,
#three-way-totals th,
#three-way-totals td{
  display:table-cell!important;
  padding:10px 16px!important;
  vertical-align:middle!important;
}
nav.market-tabs,
.pl-results-market-tabs,
#market-tabs{
  display:flex!important;
  flex-wrap:wrap!important;
  gap:10px!important;
  align-items:center!important;
}
a.market-tab,
button.market-tab{
  display:inline-flex!important;
  align-items:center!important;
  margin:0!important;
  padding:8px 14px!important;
  white-space:nowrap!important;
}
.pick-conf-grid{
  display:grid!important;
  grid-template-columns:repeat(3,minmax(0,1fr))!important;
  gap:6px!important;
}
.pc-box{
  min-width:0!important;
  height:auto!important;
  overflow:visible!important;
  display:flex!important;
  flex-direction:column!important;
}
.pc-name{
  display:block!important;
  min-width:0!important;
  width:100%!important;
  white-space:normal!important;
  overflow:visible!important;
  text-overflow:clip!important;
  height:auto!important;
  max-height:none!important;
  overflow-wrap:break-word!important;
  word-break:normal!important;
  line-height:1.2!important;
  font-size:11px!important;
}
</style>
"""


def _ncaab_layout_css(html: str) -> str:
    html = re.sub(
        r'<style id="ncaab-owner-fixes">[\s\S]*?</style>',
        "",
        html or "",
        count=1,
    )
    idx = html.lower().rfind("</body>")
    if idx < 0:
        return html + _NCAAB_LAYOUT_CSS
    return html[:idx] + _NCAAB_LAYOUT_CSS + html[idx:]


def _ncaab_share_above_footer(html: str) -> str:
    """Results image, then the share bar, then the site footer."""
    if not html or "site-directory-footer" not in html:
        return html
    footer_at = html.rfind('class="site-directory-footer"')
    footer_tag = html.rfind("<footer", 0, footer_at + 1)
    if footer_tag < 0:
        return html
    image = _ncaab_div_span(html, 'data-results-share="1"', footer_tag)
    share = _ncaab_div_span(html, 'class="share-strip"', footer_tag)
    if not image or not share:
        return html
    if image[1] <= share[0] and share[1] <= footer_tag:
        between = html[image[1] : share[0]] + html[share[1] : footer_tag]
        if between.strip() == "":
            return html
    spans = sorted((image, share), key=lambda pair: pair[0], reverse=True)
    image_html = html[image[0] : image[1]]
    share_html = html[share[0] : share[1]]
    for start, end in spans:
        html = html[:start] + html[end:]
        if footer_tag >= end:
            footer_tag -= end - start
        elif footer_tag > start:
            footer_tag = start
    return html[:footer_tag] + image_html + share_html + html[footer_tag:]


def apply_ncaab_page_fixes(html: str) -> str:
    """NCAAB results/picks only. Stored grades, separated columns, share order."""
    html = _ncaab_fill_totals_chart(html or "")
    html = _ncaab_share_above_footer(html)
    return _ncaab_layout_css(html)


def publicize_ncaab_share_hrefs(html: str) -> str:
    """NCAAB share hrefs use https://predictionlab.io, the origin already in the app."""
    if not html or "share-icon" not in html:
        return html

    def _public(href: str) -> str:
        href = re.sub(
            r"https?%3A%2F%2F(?:127\.0\.0\.1|localhost)(?:%3A\d+)?",
            "https%3A%2F%2Fpredictionlab.io",
            href,
            flags=re.I,
        )
        href = re.sub(
            r"https?%3A//(?:127\.0\.0\.1|localhost)(?:%3A\d+)?",
            "https%3A//predictionlab.io",
            href,
            flags=re.I,
        )
        return re.sub(
            r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?",
            "https://predictionlab.io",
            href,
            flags=re.I,
        )

    def _repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        hm = re.search(r'\bhref="([^"]*)"', tag, flags=re.I)
        if not hm:
            return tag
        fixed = _public(hm.group(1))
        if fixed == hm.group(1):
            return tag
        return tag[: hm.start(1)] + fixed + tag[hm.end(1) :]

    return re.sub(
        r'<a\b[^>]*\bclass="[^"]*\bshare-icon\b[^"]*"[^>]*>',
        _repl,
        html,
        flags=re.I,
    )


# === Extracted Dead Code Logic ===

def _apply_ncaab_spread_fade(d: dict) -> None:
    _apply_spread_fade(d)

