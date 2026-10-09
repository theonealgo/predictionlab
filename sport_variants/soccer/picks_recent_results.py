"""Last Night / Last 7 / Last 30 tallies on picks pages (above the SEO footer).

Does not change /{sport}-results. Cached so picks stay fast.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

_CACHE: dict[str, tuple[float, str]] = {}
_TTL = 180.0
_SOCCER_TOTALS: dict[str, list[str]] = {}

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


def _soccer_stored_finals(N, start, end) -> int:
    """Completed soccer games stored for this calendar window.

    Last 30 is every final in the database, not the 15-league snapshot slice.
    """
    start_sql = start.strftime("%Y-%m-%d")
    end_sql = end.strftime("%Y-%m-%d")
    conn = N.get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM games
            WHERE sport = 'SOCCER'
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND date(game_date) >= ?
              AND date(game_date) <= ?
            """,
            (start_sql, end_sql),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"] if row else 0)


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


def _soccer_totals_path() -> Path:
    return Path(__file__).resolve().parent / ".cache" / "soccer_recent_totals.json"


def _soccer_recent_html_path() -> Path:
    return Path(__file__).resolve().parent / ".cache" / "soccer_picks_recent.html"


def _wl_pair(st: dict | None, win_key: str, n_key: str) -> str:
    n = int((st or {}).get(n_key) or 0)
    w = int((st or {}).get(win_key) or 0)
    if n <= 0:
        return ""
    return f"{w}-{n - w}"


def _remember_soccer_totals(rows: dict[str, list[str]]) -> None:
    """Same Prediction Lab / XSharp total W-L the Recent strip just graded."""
    global _SOCCER_TOTALS
    if not rows:
        return
    _SOCCER_TOTALS = rows
    path = _soccer_totals_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _load_soccer_totals() -> dict[str, list[str]]:
    if _SOCCER_TOTALS:
        return _SOCCER_TOTALS
    path = _soccer_totals_path()
    try:
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out = {}
    for name in ("Prediction Lab", "XSharp"):
        cells = payload.get(name)
        if isinstance(cells, list) and any(str(c or "").strip() for c in cells):
            out[name] = [str(c or "") for c in cells[:3]]
    return out


def soccer_totals_chart_html() -> str:
    """Results-page totals chart filled from the graded Recent totals."""
    rows = _load_soccer_totals()
    pl = rows.get("Prediction Lab") or []
    xs = rows.get("XSharp") or []
    if not any(pl) and not any(xs):
        return ""

    def cell(cells, idx):
        val = cells[idx] if idx < len(cells) else ""
        return escape(val) if val else "—"

    body = []
    for label, cells in (("Prediction Lab", pl), ("XSharp", xs)):
        if not any(cells):
            continue
        body.append(
            "<tr>"
            f'<td class="bucket">{escape(label)}</td>'
            f"<td>{cell(cells, 0)}</td>"
            f"<td>{cell(cells, 1)}</td>"
            f"<td>{cell(cells, 2)}</td>"
            "</tr>"
        )
    if not body:
        return ""
    return (
        '<div class="pl-consensus-records pl-live-records" id="pl-totals-three-way">'
        "<h2>Prediction Lab · XSharp — Totals</h2>"
        '<p class="sub">Prediction Lab and XSharp versus the sportsbook total. '
        "Same graded games as Recent results.</p>"
        '<div style="overflow-x:auto"><table><thead><tr>'
        '<th style="text-align:left">Model</th>'
        "<th>Last night</th><th>Past 7 days</th><th>Past 30 days</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div></div>"
    )


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


def _build_block(sport: str) -> str:
    import NHL77FINAL as N

    sport_u = (sport or "").strip().upper()
    yest = _yesterday_et().replace(tzinfo=None)
    if sport_u == "SOCCER":
        # Noon made the first calendar day (2026-08-30) fall outside Last 30.
        yest = yest.replace(hour=0, minute=0, second=0, microsecond=0)
    start = yest - timedelta(days=29)
    banner_kw = {"prefer_weekly": False}
    if sport_u == "SOCCER":
        banner_kw["soccer_all_leagues"] = True
        banner_kw["row_limit"] = None
    daily = N._banner_daily_results_for_range(
        sport_u, start, yest, **banner_kw,
    ) or {}
    if sport_u == "SOCCER" and hasattr(N, "_align_soccer_picks_recent_grades"):
        # Same Efficiency and spread grades as /soccer-results. The banner
        # fill counts a different Efficiency sample (it skips draws).
        try:
            N._align_soccer_picks_recent_grades(daily)
        except Exception:
            _fill_efficiency_and_grades(sport_u, daily)
    else:
        _fill_efficiency_and_grades(sport_u, daily)
        if daily and hasattr(N, "_compute_spread_total_for_daily"):
            try:
                N._compute_spread_total_for_daily(
                    sport_u, daily, skip_efficiency=True,
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
    last7 = N.compute_model_tally_for_range(
        daily, yest - timedelta(days=6), yest, sport=sport_u,
    )
    last30 = N.compute_model_tally_for_range(daily, start, yest, sport=sport_u)
    ln_dt = N.parse_date(ln_label) or yest
    windows = (
        (f"Last Night — {ln_label}", ln, ln_dt, ln_dt),
        ("Last 7 days", last7, yest - timedelta(days=6), yest),
        ("Last 30 days", last30, start, yest),
    )
    cols = []
    for title, tally, w_start, w_end in windows:
        games = int((tally or {}).get("games") or 0)
        if sport_u == "SOCCER":
            games = _soccer_stored_finals(N, w_start, w_end)
        st = _spread_total_stats(N, daily, w_start, w_end, tally)
        if sport_u == "SOCCER":
            # Results "Spread" is PL, then XSharp when PL has no grade.
            sp = (tally or {}).get("spread") or {}
            if int(sp.get("total") or 0) > 0:
                st["pl_spread_graded"] = int(sp["total"])
                st["pl_spread_covered"] = int(sp.get("correct") or 0)
                st["pl_spread_pct"] = sp.get("accuracy")
        ml_pairs = [(label, _rec(tally, key)) for key, label in _MODELS]
        sp_pairs = [
            (label, _st_rec(st, wk, nk, pk))
            for wk, nk, pk, label in _SPREAD_ROWS
        ]
        ou_pairs = [
            (label, _st_rec(st, wk, nk, pk))
            for wk, nk, pk, label in _TOTAL_ROWS
        ]
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
    if sport_u == "SOCCER" and len(cols) == 3:
        pl_cells = []
        xs_cells = []
        for _title, tally, w_start, w_end in windows:
            st = _spread_total_stats(N, daily, w_start, w_end, tally)
            pl_cells.append(_wl_pair(st, "pl_total_correct", "pl_total_graded"))
            xs_cells.append(_wl_pair(st, "total_correct", "total_graded"))
        _remember_soccer_totals({
            "Prediction Lab": pl_cells,
            "XSharp": xs_cells,
        })
    html_block = (
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
    if sport_u == "SOCCER" and 'id="picks-recent-results"' in html_block:
        path = _soccer_recent_html_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".html.tmp")
            tmp.write_text(html_block, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass
    return html_block


def inject_picks_recent_results(html: str, sport: str) -> str:
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
        block = ""
        if sport_u == "SOCCER":
            path = _soccer_recent_html_path()
            try:
                if path.is_file() and now - path.stat().st_mtime < _TTL:
                    block = path.read_text(encoding="utf-8")
            except Exception:
                block = ""
        if not block:
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
