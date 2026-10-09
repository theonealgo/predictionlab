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


def _build_block(sport: str) -> str:
    import NHL77FINAL as N

    sport_u = (sport or "").strip().upper()
    yest = _yesterday_et().replace(tzinfo=None)
    if sport_u == "MLB":
        # The window compare is a datetime. Noon on the first day drops that
        # day's finals. 2026-08-30 has 14 stored finals.
        yest = yest.replace(hour=0, minute=0, second=0, microsecond=0)
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


def _mlb_results_windows(results_html: str) -> dict:
    """Last Night and Last 7 records already graded on the results page."""
    import re

    out: dict = {}
    for block in re.split(r'<div class="daily-tally"', results_html or "")[1:]:
        heading = re.search(r"<h2>([^<]+)</h2>", block)
        title = heading.group(1) if heading else ""
        low = title.lower()
        if "last night" in low:
            key = "last night"
        elif "last 7" in low or "past 7" in low:
            key = "last 7"
        else:
            continue
        games = re.search(r"\((\d+)\s+games?", title)
        models = {}
        for name, rec in re.findall(
            r'class="daily-model">([^<]*)</div>\s*'
            r'<div class="daily-acc"[^>]*>[\s\S]*?</div>\s*'
            r'<div class="daily-rec">([^<]*)</div>',
            block,
            flags=re.I,
        ):
            label = re.sub(r"^[^\w]+", "", name).strip()
            models[label] = (rec or "").strip()
        out[key] = {
            "games": int(games.group(1)) if games else 0,
            "models": models,
        }
    return out


def _mlb_recent_cell(record: str) -> str:
    """Display cell for a graded W-L. Empty when the results page has no grade."""
    import re

    pair = re.search(r"(\d+)\s*[-–]\s*(\d+)", record or "")
    if not pair:
        return ""
    wins, losses = int(pair.group(1)), int(pair.group(2))
    if wins + losses <= 0:
        return ""
    pct = round(100.0 * wins / (wins + losses), 1)
    return f"{pct}% · {wins}-{losses}"


def _mlb_cards_results_html() -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parent / ".cache" / "served_MLB_.html"
    try:
        if path.is_file() and path.stat().st_size > 1000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _replace_recent_cell(column: str, market: str, label: str, value: str) -> str:
    """Replace one Recent results cell. A missing grade does not clear the cell."""
    import re

    if not value:
        return column

    def _market(match: re.Match) -> str:
        title = match.group(1)
        if title.strip().lower() != market:
            return match.group(0)
        body = match.group(2)

        def _row(row: re.Match) -> str:
            if row.group(1).strip() != label:
                return row.group(0)
            return f"<tr><th>{row.group(1)}</th><td>{value}</td></tr>"

        body = re.sub(
            r"<tr><th>([^<]+)</th><td>[^<]*</td></tr>",
            _row,
            body,
        )
        return (
            f'<h4 class="picks-recent-mkt">{title}</h4>'
            f"<table><tbody>{body}</tbody>"
        )

    return re.sub(
        r'<h4 class="picks-recent-mkt">([^<]+)</h4>\s*<table><tbody>([\s\S]*?)</tbody>',
        _market,
        column,
        count=0,
    )


def _mlb_recent_block() -> str:
    now = time.time()
    hit = _CACHE.get("MLB")
    if hit and now - hit[0] < _TTL and hit[1]:
        return hit[1]
    block = _build_block("MLB")
    _CACHE["MLB"] = (now, block)
    return block


def _swap_mlb_last30_column(html: str) -> str:
    """Replace the printed Last 30 column with the stored-finals window."""
    import re

    if 'id="picks-recent-results"' not in (html or ""):
        return html
    try:
        fresh = _mlb_recent_block()
    except Exception:
        return html
    cols = re.findall(r'<div class="picks-recent-col">[\s\S]*?</div>', fresh)
    old = re.findall(r'<div class="picks-recent-col">[\s\S]*?</div>', html)
    if len(cols) < 3 or len(old) < 3:
        return html
    if "last 30" not in cols[2].lower():
        return html
    return html.replace(old[2], cols[2], 1)


def align_mlb_recent_results_strip(html: str, results_html: str | None = None) -> str:
    """Make the MLB Recent results strip use the results page's graded W-L.

    Last 30 is the stored finals in the calendar window. A window with no
    graded record is not cleared.
    """
    import re

    if not html or 'id="picks-recent-results"' not in html:
        return html
    html = _swap_mlb_last30_column(html)
    windows = _mlb_results_windows(results_html or _mlb_cards_results_html())
    if not windows:
        return html
    parts = re.split(r'(<div class="picks-recent-col">)', html)
    if len(parts) < 3:
        return html
    changed = False
    out = [parts[0]]
    i = 1
    while i < len(parts):
        opener = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        title = re.search(r"<h3>([^<]+)</h3>", body)
        low = (title.group(1) if title else "").lower()
        if "last night" in low:
            key = "last night"
        elif "last 7" in low or "past 7" in low:
            key = "last 7"
        else:
            out.append(opener + body)
            continue
        window = windows.get(key) or {}
        models = window.get("models") or {}
        original = body
        for name in (
            "Grinder2",
            "Takedown",
            "Edge",
            "XSharp",
            "Sharp Consensus",
            "Efficiency",
        ):
            body = _replace_recent_cell(
                body, "moneyline", name, _mlb_recent_cell(models.get(name, ""))
            )
        body = _replace_recent_cell(
            body, "spread", "Prediction Lab", _mlb_recent_cell(models.get("Spread", ""))
        )
        total = _mlb_recent_cell(models.get("Over/Under", ""))
        for name in ("Prediction Lab", "XSharp"):
            body = _replace_recent_cell(body, "totals", name, total)
        games = int(window.get("games") or 0)
        if games > 0:
            body2, n = re.subn(
                r'(class="picks-recent-n">)\s*\d+',
                rf"\g<1>{games}",
                body,
                count=1,
            )
            if n:
                body = body2
        if body != original:
            changed = True
        out.append(opener + body)
    if not changed:
        return html
    return "".join(out)


def _mlb_last30_totals_from_picks(picks_html: str) -> dict[str, str]:
    """Last 30 totals already printed on Recent results. Do not invent them."""
    import re

    found = {"Prediction Lab": "", "XSharp": ""}
    if 'id="picks-recent-results"' not in (picks_html or ""):
        return found
    for part in re.split(r'<div class="picks-recent-col">', picks_html)[1:]:
        title = re.search(r"<h3>([^<]+)</h3>", part)
        low = (title.group(1) if title else "").lower()
        if "last 30" not in low and "past 30" not in low:
            continue
        market = re.search(
            r'<h4 class="picks-recent-mkt">Totals</h4>\s*<table><tbody>([\s\S]*?)</tbody>',
            part,
            flags=re.I,
        )
        if not market:
            continue
        for name, value in re.findall(r"<th>([^<]+)</th><td>([^<]*)</td>", market.group(1)):
            if name.strip() in found and _mlb_recent_cell(value):
                found[name.strip()] = _mlb_recent_cell(value)
    return found


def fill_mlb_totals_chart_from_grades(
    html: str, picks_html: str | None = None
) -> str:
    """Add Prediction Lab and XSharp totals rows from graded records.

    Existing agreement rows are left in place. A cell that already has a W-L
    is not replaced with a blank.
    """
    import re
    from pathlib import Path

    if not html or 'id="pl-totals-three-way"' not in html:
        return html
    windows = _mlb_results_windows(html) or _mlb_results_windows(_mlb_cards_results_html())
    if picks_html is None:
        path = Path(__file__).resolve().parent / ".cache" / "served_picks_MLB.html"
        try:
            picks_html = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        except OSError:
            picks_html = ""
    last30 = _mlb_last30_totals_from_picks(picks_html or "")
    graded = {}
    for name in ("Prediction Lab", "XSharp"):
        cells = ["", "", ""]
        for idx, key in ((0, "last night"), (1, "last 7")):
            rec = ((windows.get(key) or {}).get("models") or {}).get("Over/Under", "")
            cells[idx] = _mlb_recent_cell(rec)
        cells[2] = last30.get(name) or ""
        if any(cells):
            graded[name] = cells
    if not graded:
        return html
    start = html.find('id="pl-totals-three-way"')
    tbody_end = html.find("</tbody>", start)
    table_end = html.find("</table>", start)
    if tbody_end < 0 or table_end < 0 or tbody_end > table_end:
        return html
    table = html[start:tbody_end]
    extra = []
    for name, cells in graded.items():
        row_re = re.compile(
            rf'(<tr>\s*<td class="bucket">\s*{re.escape(name)}\s*</td>)([\s\S]*?)(</tr>)',
            re.I,
        )
        match = row_re.search(table)
        if not match:
            painted = "".join(f"<td>{cell}</td>" if cell else "<td></td>" for cell in cells)
            if not any(cells):
                continue
            extra.append(
                f'<tr><td class="bucket">{name}</td>{painted}</tr>'
            )
            continue
        existing = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", match.group(2), flags=re.I)
        merged = []
        for idx in range(3):
            current = existing[idx] if idx < len(existing) else ""
            plain = re.sub(r"<[^>]+>", " ", current or "")
            has_grade = re.search(r"(\d+)\s*[-–]\s*(\d+)", plain)
            if has_grade and int(has_grade.group(1)) + int(has_grade.group(2)) > 0:
                merged.append(current)
            elif cells[idx]:
                merged.append(cells[idx])
            else:
                merged.append(current)
        painted = "".join(f"<td>{cell}</td>" for cell in merged)
        table = (
            table[: match.start()]
            + match.group(1)
            + painted
            + match.group(3)
            + table[match.end() :]
        )
    if extra:
        table += "".join(extra)
    if table == html[start:tbody_end]:
        return html
    return html[:start] + table + html[tbody_end:]


def inject_picks_recent_results(html: str, sport: str) -> str:
    sport_u = (sport or "").strip().upper()
    if not html:
        return html
    if 'id="picks-recent-results"' in html:
        if sport_u == "MLB":
            return align_mlb_recent_results_strip(html)
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

    def _finish(page: str) -> str:
        if sport_u == "MLB":
            return align_mlb_recent_results_strip(page)
        return page

    html2, n = re.subn(
        r'(<div class="seo-picks-footer"[^>]*>)',
        block + r"\1",
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return _finish(html2)
    html2, n = re.subn(
        r'(<h2[^>]*>\s*How These AI Picks Are Generated\s*</h2>)',
        block + r"\1",
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return _finish(html2)
    for needle in ("</main>", "</body>"):
        i = html.lower().rfind(needle)
        if i >= 0:
            return _finish(html[:i] + block + html[i:])
    return _finish(html + block)
