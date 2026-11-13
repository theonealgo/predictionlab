"""Last Night / Last 7 / Last 30 tallies on picks pages (above the SEO footer).

Does not change /{sport}-results. Cached so picks stay fast.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from html import escape

_CACHE: dict[str, tuple[float, str]] = {}
_CHART: dict[str, tuple[float, str]] = {}
_TTL = 180.0

_MODELS = (
    ("glicko2", "Grinder2"),
    ("trueskill", "Takedown"),
    ("elo", "Edge"),
    ("xgboost", "XSharp"),
    ("ensemble", "Sharp Consensus"),
    ("efficiency", "Efficiency"),
)

_SPREAD_ROWS = (
    ("pl_spread_covered", "pl_spread_graded", "pl_spread_pct", "Prediction Lab"),
    ("spread_covered", "spread_graded", "spread_pct", "XSharp"),
)
_TOTAL_ROWS = (
    ("pl_total_correct", "pl_total_graded", "pl_total_pct", "Prediction Lab"),
    ("total_correct", "total_graded", "total_pct", "XSharp"),
)


def _yesterday_et():
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.now()
    return (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)


def _rec(tally: dict | None, key: str) -> str:
    m = (tally or {}).get(key) or {}
    total = int(m.get("total") or 0)
    correct = int(m.get("correct") or 0)
    if total <= 0:
        return "—"
    acc = m.get("accuracy")
    if acc is None:
        acc = round(100.0 * correct / total, 1)
    return f"{acc}% · {correct}-{total - correct}"


def _st_rec(st: dict | None, win_key: str, n_key: str, pct_key: str) -> str:
    n = int((st or {}).get(n_key) or 0)
    w = int((st or {}).get(win_key) or 0)
    if n <= 0:
        return "—"
    acc = (st or {}).get(pct_key)
    if acc is None:
        acc = round(100.0 * w / n, 1)
    return f"{acc}% · {w}-{n - w}"


def _published_results_html(sport_u: str) -> str:
    """The results page already graded these tiles. Picks repeats that record."""
    from pathlib import Path

    here = Path(__file__).resolve().parent
    for base in (here, here.parent.parent):
        path = base / ".cache" / f"served_{sport_u}_.html"
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
    return ""


def _published_tiles(html: str, heading: str) -> dict | None:
    """Games count and per-row records from one results-page tile section."""
    if not html or not heading:
        return None
    m = re.search(rf"<h2[^>]*>[^<]*{re.escape(heading)}[^<]*</h2>", html)
    if not m:
        return None
    after = html[m.end() : m.end() + 12000]
    nxt = re.search(r"<h2\b", after)
    seg = after[: nxt.start()] if nxt else after
    games_m = re.search(r"\((\d+)\s+games?\)", m.group(0))
    rows: dict[str, str] = {}
    for name, acc, rec in re.findall(
        r'class="daily-model">([^<]*)</div>\s*<div class="daily-acc"[^>]*>([^<]*)</div>'
        r'\s*<div class="daily-rec">([^<]*)</div>',
        seg,
    ):
        label = re.sub(r"^[^A-Za-z]+", "", name).strip()
        rec = re.sub(r"\s+", "", rec)
        acc = re.sub(r"\s+", "", acc)
        if not label or not re.search(r"\d+-\d+", rec):
            continue
        rows[label] = f"{acc} · {rec}" if acc.endswith("%") else rec
    if not rows:
        return None
    return {
        "games": int(games_m.group(1)) if games_m else 0,
        "rows": rows,
        "dates": re.findall(r"\d{4}-\d{2}-\d{2}", m.group(0)),
    }


def _slice_daily(daily: dict, start, end) -> dict:
    import NHL77FINAL as N

    out = {}
    for dk, bucket in (daily or {}).items():
        if N._date_in_range(dk, start, end):
            out[dk] = bucket
    return out


def _spread_total_stats(N, daily: dict, start, end, tally: dict | None) -> dict:
    st = {}
    if hasattr(N, "_recount_spread_total_stats"):
        try:
            st = N._recount_spread_total_stats(_slice_daily(daily, start, end)) or {}
        except Exception:
            st = {}
    st = dict(st or {})
    if int(st.get("pl_spread_graded") or 0) == 0:
        sp = (tally or {}).get("spread") or {}
        if int(sp.get("total") or 0) > 0:
            st["pl_spread_graded"] = int(sp["total"])
            st["pl_spread_covered"] = int(sp.get("correct") or 0)
            st["pl_spread_pct"] = sp.get("accuracy")
    if int(st.get("pl_total_graded") or 0) == 0:
        ou = (tally or {}).get("total_ou") or {}
        if int(ou.get("total") or 0) > 0:
            st["pl_total_graded"] = int(ou["total"])
            st["pl_total_correct"] = int(ou.get("correct") or 0)
            st["pl_total_pct"] = ou.get("accuracy")
    if (
        int(st.get("total_graded") or 0) == 0
        and int(st.get("pl_total_graded") or 0) == 0
    ):
        ou = (tally or {}).get("total_ou") or {}
        if int(ou.get("total") or 0) > 0:
            st["total_graded"] = int(ou["total"])
            st["total_correct"] = int(ou.get("correct") or 0)
            st["total_pct"] = ou.get("accuracy")
    return st


def _fill_efficiency_and_grades(sport: str, daily: dict) -> None:
    try:
        from sports.team_efficiency_attach import (
            home_prob_pct_to_spread,
            spread_to_home_prob_pct,
        )
    except Exception:
        home_prob_pct_to_spread = None
        spread_to_home_prob_pct = None
    sport_u = (sport or "").strip().upper()
    for bucket in (daily or {}).values():
        for g in bucket.get("games") or []:
            if not isinstance(g, dict):
                continue
            if g.get("efficiency_prob") is None and spread_to_home_prob_pct:
                sp = g.get("efficiency_spread") or g.get("our_spread")
                if sp is None and g.get("ens_prob") is not None and home_prob_pct_to_spread:
                    try:
                        sp = home_prob_pct_to_spread(float(g["ens_prob"]), sport_u)
                    except (TypeError, ValueError):
                        sp = None
                if sp is not None:
                    try:
                        g["efficiency_prob"] = spread_to_home_prob_pct(float(sp), sport_u)
                    except (TypeError, ValueError):
                        pass
            hw = g.get("home_win")
            if hw is None:
                continue
            for pk, ck in (
                ("glicko2_prob", "glicko2_correct"),
                ("trueskill_prob", "trueskill_correct"),
                ("elo_prob", "elo_correct"),
                ("xgb_prob", "xgb_correct"),
                ("ens_prob", "ens_correct"),
                ("efficiency_prob", "efficiency_correct"),
            ):
                if g.get(ck) is not None or g.get(pk) is None:
                    continue
                try:
                    g[ck] = (float(g[pk]) >= 50.0) == bool(hw)
                except (TypeError, ValueError):
                    pass


def _rows_html(pairs) -> str:
    return "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(val)}</td></tr>"
        for label, val in pairs
    )


def _market_table(title: str, pairs) -> str:
    return (
        f'<h4 class="picks-recent-mkt">{escape(title)}</h4>'
        f"<table><tbody>{_rows_html(pairs)}</tbody></table>"
    )


def _nba_latest_final_date(N) -> str | None:
    """Latest NBA game that already has a final score. Does not create games."""
    try:
        conn = N.get_db_connection()
        row = conn.execute(
            """
            SELECT date(game_date) AS d
            FROM games
            WHERE sport = 'NBA'
              AND COALESCE(league, '') != 'NBA Preseason'
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            ORDER BY date(game_date) DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        return str(row["d"] or "")[:10] or None
    except Exception:
        return str(row[0] or "")[:10] or None


def _nba_scored_night(N, date_key: str):
    """Grade one stored NBA final the same way the results page does.

    Moneyline uses the stored snapshot (frozen Grinder2/Takedown only when
    those legs were blank). Efficiency uses the H2H spread already on the
    results card. Spread and total stay ungraded when the results page has
    no ATS/O/U record.
    """
    dt = N.parse_date(date_key)
    if dt is None:
        return None
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = dt.replace(hour=23, minute=59, second=59, microsecond=0)
    daily = N._banner_daily_results_for_range(
        "NBA", start, end, prefer_weekly=False,
    ) or {}
    games = []
    for bucket in daily.values():
        games.extend(bucket.get("games") or [])
    if not games:
        return None
    if hasattr(N, "_nba_grade_missing_g2_td"):
        for game in games:
            N._nba_grade_missing_g2_td(game)
    try:
        N._attach_h2h_projection_to_daily_results("NBA", daily, n=10)
        if hasattr(N, "_attach_published_lines"):
            N._attach_published_lines("NBA", daily)
    except Exception:
        pass
    if hasattr(N, "_grade_efficiency_ml_from_spread"):
        try:
            N._grade_efficiency_ml_from_spread("NBA", daily)
        except Exception:
            pass
    label = next((dk for dk, bucket in daily.items() if bucket.get("games")), date_key)
    tally = N.compute_daily_model_tally(daily, label, sport="NBA")
    if not tally or int(tally.get("games") or 0) <= 0:
        return None
    return label, tally, daily


def _build_block(sport: str) -> str:
    import NHL77FINAL as N

    sport_u = (sport or "").strip().upper()
    yest = _yesterday_et().replace(tzinfo=None)
    start = yest - timedelta(days=29)
    daily = N._banner_daily_results_for_range(
        sport_u, start, yest, prefer_weekly=False,
    ) or {}
    if not int((N.compute_model_tally_for_range(daily, start, yest, sport=sport_u) or {}).get("games") or 0):
        # Off-season: the windows end on the last NBA night with finals
        # (the results page shows the same boards).
        last = N.parse_date(_nba_latest_final_date(N) or "")
        if last is not None:
            yest = last.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=None)
            start = yest - timedelta(days=29)
            daily = N._banner_daily_results_for_range(
                sport_u, start, yest, prefer_weekly=False,
            ) or {}
    if hasattr(N, "_nba_grade_missing_g2_td"):
        for bucket in daily.values():
            for game in bucket.get("games") or []:
                N._nba_grade_missing_g2_td(game)
    try:
        N._attach_h2h_projection_to_daily_results("NBA", daily, n=10)
        if hasattr(N, "_attach_published_lines"):
            N._attach_published_lines("NBA", daily)
    except Exception:
        pass
    if hasattr(N, "_grade_efficiency_ml_from_spread"):
        try:
            N._grade_efficiency_ml_from_spread("NBA", daily)
        except Exception:
            pass
    _fill_efficiency_and_grades(sport_u, daily)
    if daily and hasattr(N, "_compute_spread_total_for_daily"):
        try:
            N._compute_spread_total_for_daily(
                sport_u, daily, skip_efficiency=True,
                skip_h2h=True,
            )
        except Exception:
            pass
    ln = N.compute_daily_model_tally(daily, yest.strftime("%Y-%m-%d"), sport=sport_u)
    if not ln:
        dated = []
        for dk in daily:
            dt = N.parse_date(dk)
            if dt is not None:
                dated.append((dt, dk))
        dated.sort(reverse=True)
        if dated and (yest.date() - dated[0][0].date()).days <= 4:
            ln = N.compute_daily_model_tally(daily, dated[0][1], sport=sport_u)
            ln_label = dated[0][1]
        else:
            ln_label = yest.strftime("%Y-%m-%d")
            ln = N._empty_results_model_tally() if hasattr(N, "_empty_results_model_tally") else {}
    else:
        ln_label = yest.strftime("%Y-%m-%d")
    ln_src = daily
    scored = _nba_scored_night(N, ln_label) if ln and int(ln.get("games") or 0) else None
    if scored:
        ln_label, ln, ln_src = scored
    last7_start = (yest - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    last7 = N.compute_model_tally_for_range(
        daily, last7_start, yest, sport=sport_u,
    )
    last30 = N.compute_model_tally_for_range(daily, start, yest, sport=sport_u)
    ln_dt = N.parse_date(ln_label) or yest
    ln_day_start = ln_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    ln_day_end = ln_dt.replace(hour=23, minute=59, second=59, microsecond=0)
    if _slice_daily(daily, ln_day_start, ln_day_end):
        ln_src = daily
    windows = (
        (f"Last Night — {ln_label}", ln, ln_day_start, ln_day_end, ln_src),
        ("Last 7 days", last7, last7_start, yest, daily),
        ("Last 30 days", last30, start, yest, daily),
    )
    cols = []
    chart_stats = []
    published = _published_results_html(sport_u)
    for title, tally, w_start, w_end, src in windows:
        games = int((tally or {}).get("games") or 0)
        st = _spread_total_stats(N, src, w_start, w_end, tally)
        chart_stats.append(st)
        ml_pairs = [(label, _rec(tally, key)) for key, label in _MODELS]
        sp_pairs = [
            (label, _st_rec(st, wk, nk, pk))
            for wk, nk, pk, label in _SPREAD_ROWS
        ]
        ou_pairs = [
            (label, _st_rec(st, wk, nk, pk))
            for wk, nk, pk, label in _TOTAL_ROWS
        ]
        if "Last Night" in title:
            heading = "Last Night"
        elif "Last 7" in title:
            heading = "Last 7 Days"
        else:
            heading = ""
        tiles = _published_tiles(published, heading) if heading and published else None
        if tiles and (tiles.get("dates") or [""])[-1] != w_end.strftime("%Y-%m-%d"):
            tiles = None
        if tiles and (not tiles["games"] or tiles["games"] == games):
            # Same window and games as the results page: copy its grades exactly.
            ml_pairs = [
                (label, tiles["rows"].get(label) or val)
                for (label, val) in ml_pairs
            ]
            if tiles["rows"].get("Spread"):
                sp_pairs[0] = ("Prediction Lab", tiles["rows"]["Spread"])
        cols.append(
            f'<div class="picks-recent-col">'
            f"<h3>{escape(title)}</h3>"
            f'<p class="picks-recent-n">{games} games</p>'
            f'{_market_table("Moneyline", ml_pairs)}'
            f'{_market_table("Spread", sp_pairs)}'
            f'{_market_table("Totals", ou_pairs)}'
            f'<a class="picks-recent-more" href="/{sport_u.lower()}-results">Full results →</a>'
            f"</div>"
        )
    _CHART[sport_u] = (time.time(), _totals_chart_html(chart_stats))
    return (
        '<section id="picks-recent-results" class="picks-recent-results" '
        'aria-label="Recent results">'
        "<h2>Recent results</h2>"
        '<p class="picks-recent-sub">Last Night, Last 7, and Last 30 — same moneyline, '
        "spread, and total grades as the results page.</p>"
        f'<div class="picks-recent-grid">{"".join(cols)}</div>'
        "<style>"
        ".picks-recent-results{max-width:1200px;margin:28px auto 0;padding:18px 16px;"
        "background:#fff;border:1px solid rgba(15,23,42,.14);border-radius:14px;color:#0f172a}"
        ".picks-recent-results h2{margin:0 0 6px;font-size:1.15rem}"
        ".picks-recent-sub{margin:0 0 14px;color:#64748b;font-size:.86rem}"
        ".picks-recent-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}"
        ".picks-recent-col{border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;background:#f8fafc}"
        ".picks-recent-col h3{margin:0 0 4px;font-size:.92rem}"
        ".picks-recent-n{margin:0 0 8px;color:#64748b;font-size:.75rem}"
        ".picks-recent-mkt{margin:10px 0 4px;font-size:.68rem;letter-spacing:.04em;"
        "text-transform:uppercase;color:#64748b;font-weight:700}"
        ".picks-recent-col table{width:100%;border-collapse:collapse;font-size:.8rem}"
        ".picks-recent-col th{text-align:left;padding:3px 4px;color:#334155;font-weight:700}"
        ".picks-recent-col td{text-align:right;padding:3px 4px;font-variant-numeric:tabular-nums}"
        ".picks-recent-more{display:inline-block;margin-top:8px;font-size:.78rem;font-weight:700;color:#00529B;text-decoration:none}"
        "@media(max-width:800px){.picks-recent-grid{grid-template-columns:1fr}}"
        "</style>"
        "</section>"
    )


def _totals_chart_html(stats: list) -> str:
    """Prediction Lab and XSharp totals for Last Night, Last 7, and Last 30."""
    rows = []
    for win_key, n_key, pct_key, label in _TOTAL_ROWS:
        cells = "".join(
            f"<td>{escape(_st_rec(st, win_key, n_key, pct_key))}</td>" for st in stats
        )
        rows.append(f'<tr><td class="bucket">{escape(label)}</td>{cells}</tr>')
    return (
        '<div class="pl-consensus-records" id="pl-totals-three-way">'
        "<h2>Prediction Lab · XSharp — Totals</h2>"
        "<table><thead><tr>"
        "<th>Model</th><th>Last night</th><th>Past 7 days</th><th>Past 30 days</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def results_totals_chart(sport: str) -> str:
    """Same totals the picks Recent results shows, as the results page table."""
    sport_u = (sport or "").strip().upper()
    hit = _CHART.get(sport_u)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        _CACHE[sport_u] = (time.time(), _build_block(sport_u))
    except Exception:
        return ""
    hit = _CHART.get(sport_u)
    return hit[1] if hit else ""


def inject_picks_recent_results(html: str, sport: str) -> str:
    from card_pagination import active as _cards_api_window
    if _cards_api_window():
        return html
    if not html or 'id="picks-recent-results"' in html:
        return html
    if "How These AI Picks Are Generated" not in html and "seo-picks-footer" not in html:
        if "game-card" not in html and "pick-card" not in html:
            if "golf-board" not in html:
                return html
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return html
    now = time.time()
    hit = _CACHE.get(sport_u)
    if hit and now - hit[0] < _TTL:
        block = hit[1]
    else:
        try:
            block = _build_block(sport_u)
        except Exception:
            return html
        _CACHE[sport_u] = (now, block)
    import re

    html2, n = re.subn(
        r'(<div class="seo-picks-footer"[^>]*>)',
        block + r"\1",
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return html2
    html2, n = re.subn(
        r'(<h2[^>]*>\s*How These AI Picks Are Generated\s*</h2>)',
        block + r"\1",
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return html2
    for needle in ("</main>", "</body>"):
        i = html.lower().rfind(needle)
        if i >= 0:
            return html[:i] + block + html[i:]
    return html + block
