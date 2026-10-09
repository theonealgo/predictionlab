"""Last Night / Last 7 / Last 30 tallies on picks pages (above the SEO footer).

Does not change /{sport}-results. Cached so picks stay fast.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from html import escape

_CACHE: dict[str, tuple[float, str]] = {}
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


# /cfl-picks is rendered from the in-process MLB predictions page, so the
# Recent results block already on that HTML is that page's Last 30. Short
# windows and the totals chart have to use this same graded set. Last 30's
# own bounds stay yesterday through 29 days earlier.
_CFL_PICKS_SHELL_SPORT = "MLB"
# Dates and grade strings for the CFL results board. Filled only when the
# short windows are taken from Last 30's graded set. Last 30 is not stored.
_CFL_WINDOW_META: dict = {}


def _col_games(html: str, title_prefix: str) -> int:
    import re

    for part in re.split(r'<div class="picks-recent-col">', html or "")[1:]:
        title = re.search(r"<h3>([^<]+)</h3>", part)
        if not title or not title.group(1).startswith(title_prefix):
            continue
        games = re.search(r'class="picks-recent-n">\s*(\d+)', part)
        return int(games.group(1)) if games else 0
    return 0


def _replace_recent_section(html: str, block: str) -> str:
    import re

    html2, n = re.subn(
        r'<section id="picks-recent-results"[\s\S]*?</section>',
        lambda _m: block,
        html,
        count=1,
        flags=re.I,
    )
    return html2 if n else html


def _build_block(sport: str, *, fill_short_from_graded: bool = False) -> str:
    import NHL77FINAL as N

    sport_u = (sport or "").strip().upper()
    yest = _yesterday_et().replace(tzinfo=None)
    start = yest - timedelta(days=29)
    daily = N._banner_daily_results_for_range(
        sport_u, start, yest, prefer_weekly=False,
    ) or {}
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
    l7_start = yest - timedelta(days=6)
    l7_end = yest
    last7 = N.compute_model_tally_for_range(
        daily, l7_start, l7_end, sport=sport_u,
    )
    last30 = N.compute_model_tally_for_range(daily, start, yest, sport=sport_u)
    ln_dt = N.parse_date(ln_label) or yest
    # Last 30 already graded these games. An empty calendar night or week
    # must use the newest graded date inside that same set, not show 0.
    if (
        fill_short_from_graded
        and int((last30 or {}).get("games") or 0) > 0
        and (
            int((ln or {}).get("games") or 0) <= 0
            or int((last7 or {}).get("games") or 0) <= 0
        )
    ):
        dated = []
        for dk, bucket in (daily or {}).items():
            if not N._gradable_result_games((bucket or {}).get("games") or []):
                continue
            dt = N.parse_date(dk)
            if dt is None or not N._date_in_range(dk, start, yest):
                continue
            dated.append((dt, str(dk)))
        dated.sort()
        if dated:
            ln_dt, ln_label = dated[-1]
            ln = N.compute_daily_model_tally(daily, ln_label, sport=sport_u)
            l7_start = ln_dt - timedelta(days=6)
            if l7_start < start:
                l7_start = start
            l7_end = ln_dt
            last7 = N.compute_model_tally_for_range(
                daily, l7_start, l7_end, sport=sport_u,
            )
    windows = (
        (f"Last Night — {ln_label}", ln, ln_dt, ln_dt),
        ("Last 7 days", last7, l7_start, l7_end),
        ("Last 30 days", last30, start, yest),
    )
    cols = []
    short_meta = {}
    for title, tally, w_start, w_end in windows:
        games = int((tally or {}).get("games") or 0)
        st = _spread_total_stats(N, daily, w_start, w_end, tally)
        ml_pairs = [(label, _rec(tally, key)) for key, label in _MODELS]
        sp_pairs = [
            (label, _st_rec(st, wk, nk, pk))
            for wk, nk, pk, label in _SPREAD_ROWS
        ]
        ou_pairs = [
            (label, _st_rec(st, wk, nk, pk))
            for wk, nk, pk, label in _TOTAL_ROWS
        ]
        low = title.lower()
        if fill_short_from_graded and (low.startswith("last night") or low.startswith("last 7")):
            key = "last night" if low.startswith("last night") else "last 7"
            short_meta[key] = {
                "games": games,
                "start": w_start.strftime("%Y-%m-%d"),
                "end": w_end.strftime("%Y-%m-%d"),
                "moneyline": {label: val for label, val in ml_pairs},
                "spread_pl": dict(sp_pairs).get("Prediction Lab", "—"),
                "total_pl": dict(ou_pairs).get("Prediction Lab", "—"),
            }
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
    if fill_short_from_graded and short_meta:
        _CFL_WINDOW_META.clear()
        _CFL_WINDOW_META.update(short_meta)
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


def _cfl_filled_block() -> str:
    now = time.time()
    hit = _CACHE.get("CFL-SHELL")
    if hit and now - hit[0] < _TTL:
        return hit[1]
    block = _build_block(_CFL_PICKS_SHELL_SPORT)
    block = _overlay_cfl_results_windows(block)
    _CACHE["CFL-SHELL"] = (now, block)
    return block


def _tally_face(entry) -> str:
    """Same W-L the results board prints for one model or market."""
    if not isinstance(entry, dict):
        return ""
    total = int(entry.get("total") or 0)
    correct = int(entry.get("correct") or 0)
    if total <= 0:
        return ""
    acc = entry.get("accuracy")
    if acc is None:
        acc = round(100.0 * correct / total, 1)
    try:
        acc_txt = f"{float(acc):.1f}"
    except (TypeError, ValueError):
        acc_txt = str(acc)
    rec = f"{correct}-{total - correct}"
    pushes = int(entry.get("pushes") or 0)
    if pushes:
        rec = f"{rec}-{pushes}"
    return f"{acc_txt}% · {rec}"


def _cfl_results_windows() -> dict:
    """Last Night and Last 7 from the CFL results board, not a longer slate."""
    from collections import defaultdict
    from datetime import datetime, timedelta

    import NHL77FINAL as N
    from cfl_live import _cfl_card_to_daily_game
    from mlb_team_shell import _cfl_cards

    cards, render = _cfl_cards("results")
    daily: dict = defaultdict(lambda: {"games": []})
    for card in cards:
        game = _cfl_card_to_daily_game(card, render)
        if game:
            daily[game["date"]]["games"].append(game)
    if not daily:
        return {}
    yesterday_dt = datetime.now() - timedelta(days=1)
    bundle = N._compute_results_tally_bundle(daily, yesterday_dt, sport="CFL") or {}

    def pack(tally, title: str, games: int) -> dict:
        ou = _tally_face((tally or {}).get("total_ou"))
        return {
            "title": title,
            "games": games,
            "moneyline": {
                label: _tally_face((tally or {}).get(key))
                for key, label in _MODELS
            },
            "spread_pl": _tally_face((tally or {}).get("spread")),
            "total": ou,
        }

    night_games = int(bundle.get("daily_tally_games") or 0)
    week_games = int(bundle.get("weekly_tally_games") or 0)
    night_date = bundle.get("daily_tally_date") or ""
    return {
        "last night": pack(
            bundle.get("daily_tally"),
            f"Last Night — {night_date}" if night_date else "Last Night",
            night_games,
        ),
        "last 7": pack(bundle.get("weekly_tally"), "Last 7 days", week_games),
    }


def _patch_market(column: str, market: str, updates: dict) -> str:
    import re

    parts = re.split(
        r'(<h4 class="picks-recent-mkt">(?:Moneyline|Spread|Totals)</h4>)',
        column,
    )
    out = []
    i = 0
    while i < len(parts):
        piece = parts[i]
        if piece == f'<h4 class="picks-recent-mkt">{market}</h4>' and i + 1 < len(parts):
            body = parts[i + 1]
            for label, value in updates.items():
                if not value:
                    continue
                body = re.sub(
                    rf"(<tr><th>{re.escape(label)}</th><td>)[^<]*(</td>)",
                    lambda m, v=value: m.group(1) + escape(v) + m.group(2),
                    body,
                    count=1,
                )
            out.append(piece + body)
            i += 2
            continue
        out.append(piece)
        i += 1
    return "".join(out)


def _patch_short_column(column: str, window: dict) -> str:
    import re

    games = int(window.get("games") or 0)
    if games <= 0:
        return column
    title = window.get("title") or ""
    if title:
        column = re.sub(r"<h3>[^<]*</h3>", f"<h3>{escape(title)}</h3>", column, count=1)
    column = re.sub(
        r'(class="picks-recent-n">)\s*\d+',
        rf"\g<1>{games}",
        column,
        count=1,
    )
    column = _patch_market(column, "Moneyline", window.get("moneyline") or {})
    column = _patch_market(column, "Spread", {"Prediction Lab": window.get("spread_pl") or ""})
    total = window.get("total") or ""
    column = _patch_market(
        column,
        "Totals",
        {"Prediction Lab": total, "XSharp": total},
    )
    return column


def _overlay_cfl_results_windows(html: str) -> str:
    """Point Last Night and Last 7 at the CFL results board. Leave Last 30."""
    import re

    try:
        windows = _cfl_results_windows()
    except Exception:
        return html
    if not windows:
        return html
    parts = re.split(r'(<div class="picks-recent-col">)', html)
    out = [parts[0]]
    i = 1
    while i < len(parts):
        delim = parts[i]
        column = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        title = re.search(r"<h3>([^<]+)</h3>", column)
        low = (title.group(1) if title else "").lower()
        if low.startswith("last night"):
            column = _patch_short_column(column, windows.get("last night") or {})
        elif low.startswith("last 7"):
            column = _patch_short_column(column, windows.get("last 7") or {})
        out.append(delim + column)
    return "".join(out)


def _repair_cfl_recent_section(html: str) -> str:
    """Keep Last 30. Last Night and Last 7 use the CFL results board."""
    try:
        block = _cfl_filled_block()
    except Exception:
        return html
    old_n = _col_games(html, "Last 30")
    new_n = _col_games(block, "Last 30")
    if new_n <= 0 or (old_n and new_n != old_n):
        return html
    return _replace_recent_section(html, block)


def cfl_totals_chart_html() -> str:
    """Last 30 totals chart cells from the same grades as Recent results."""
    import re

    try:
        block = _cfl_filled_block()
    except Exception:
        return ""
    pairs = []
    for part in re.split(r'<div class="picks-recent-col">', block)[1:]:
        body = re.search(
            r'<h4 class="picks-recent-mkt">Totals</h4>\s*<table><tbody>(.*?)</tbody>',
            part,
            flags=re.S,
        )
        rows = {}
        if body:
            rows = dict(re.findall(r"<th>([^<]+)</th><td>([^<]*)</td>", body.group(1)))
        pairs.append((rows.get("Prediction Lab", "—"), rows.get("XSharp", "—")))
    if len(pairs) < 3:
        return ""

    def row(name: str, idx: int) -> str:
        cells = "".join(f"<td>{escape(pairs[i][idx] or '—')}</td>" for i in range(3))
        return f'<tr><td class="bucket">{name}</td>{cells}</tr>'

    return (
        '<table id="pl-totals-three-way">'
        "<thead><tr><th>Model</th><th>Last night</th><th>Past 7 days</th>"
        "<th>Past 30 days</th></tr></thead><tbody>"
        + row("Prediction Lab", 0)
        + row("XSharp", 1)
        + "</tbody></table>"
    )


def inject_picks_recent_results(html: str, sport: str) -> str:
    sport_u = (sport or "").strip().upper()
    if sport_u == "CFL" and html and 'id="picks-recent-results"' in html:
        return _repair_cfl_recent_section(html)
    if not html or 'id="picks-recent-results"' in html:
        return html
    if "How These AI Picks Are Generated" not in html and "seo-picks-footer" not in html:
        if "game-card" not in html and "pick-card" not in html:
            if "golf-board" not in html:
                return html
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return html
    if sport_u == "CFL":
        try:
            block = _cfl_filled_block()
        except Exception:
            return html
    else:
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
