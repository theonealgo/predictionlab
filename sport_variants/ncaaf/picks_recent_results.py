"""Last Night / Last 7 / Last 30 tallies on picks pages (above the SEO footer).

Does not change /{sport}-results. Cached so picks stay fast.
"""
from __future__ import annotations

import re
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


def _published_results_html(sport_u: str) -> str:
    """The results page already graded these tiles. Picks repeats that record."""
    from pathlib import Path

    path = Path(__file__).resolve().parent / ".cache" / f"served_{sport_u}_.html"
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return ""


def _published_pl_rec(html: str, heading: str, market: str) -> str:
    if not html or not heading:
        return ""
    i = html.find(heading)
    if i < 0:
        return ""
    chunk = html[i : i + 12000]
    m = re.search(
        rf'class="daily-model">[^<]*{re.escape(market)}[^<]*</div>'
        r'[\s\S]{0,800}?class="daily-acc"[^>]*>\s*([^<]+)'
        r'[\s\S]{0,400}?class="daily-rec">\s*([^<]+)',
        chunk,
        flags=re.I,
    )
    if not m:
        return ""
    acc = re.sub(r"\s+", "", m.group(1))
    rec = re.sub(r"\s+", "", m.group(2))
    if not re.search(r"\d+-\d+", rec):
        return ""
    if acc.endswith("%"):
        return f"{acc} · {rec}"
    return rec


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
    return {"games": int(games_m.group(1)) if games_m else 0, "rows": rows}


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


def _num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _cover_flag(spread, margin):
    s, m = _num(spread), _num(margin)
    if s is None or m is None or abs(s) < 0.05 or abs(m - s) < 0.05:
        return None
    return (m > s) if s > 0 else (m < s)


def _ou_flag(model_total, book_total, actual):
    t, b, a = _num(model_total), _num(book_total), _num(actual)
    if t is None or b is None or a is None:
        return None
    if abs(a - b) < 0.05 or abs(t - b) < 0.05:
        return None
    return (t > b) == (a > b)


def _pct(wins, graded):
    if graded <= 0:
        return None
    return round(100.0 * wins / graded, 1)


def _spread_total_stats(N, daily: dict, start, end, tally: dict | None) -> dict:
    """Grade Prediction Lab and XSharp from their own lines.

    A missing line stays blank. It is not filled from the other model.
    """
    del N, tally
    pl_sw = pl_sn = xs_sw = xs_sn = 0
    pl_ow = pl_on = xs_ow = xs_on = 0
    for bucket in (_slice_daily(daily, start, end) or {}).values():
        for g in bucket.get("games") or []:
            if not isinstance(g, dict) or g.get("skip_grading"):
                continue
            hs, aws = g.get("home_score"), g.get("away_score")
            if hs is None or aws is None:
                continue
            margin = float(hs) - float(aws)
            actual = float(hs) + float(aws)
            book = g.get("disp_book_total")
            if book is None:
                book = g.get("book_total")
            if book is None:
                book = g.get("market_total")
            pairs = (
                ("pl", g.get("disp_pl_spread") if g.get("disp_pl_spread") is not None else g.get("our_spread"),
                 g.get("disp_pl_total") if g.get("disp_pl_total") is not None else g.get("our_total")),
                ("xs", g.get("disp_xs_spread") if g.get("disp_xs_spread") is not None else g.get("xgb_spread"),
                 g.get("disp_xs_total") if g.get("disp_xs_total") is not None else g.get("xgb_total")),
            )
            for kind, spread, total in pairs:
                hit = _cover_flag(spread, margin)
                if hit is not None:
                    if kind == "pl":
                        pl_sn += 1
                        pl_sw += int(hit)
                    else:
                        xs_sn += 1
                        xs_sw += int(hit)
                ou = _ou_flag(total, book, actual)
                if ou is not None:
                    if kind == "pl":
                        pl_on += 1
                        pl_ow += int(ou)
                    else:
                        xs_on += 1
                        xs_ow += int(ou)
    return {
        "pl_spread_covered": pl_sw,
        "pl_spread_graded": pl_sn,
        "pl_spread_pct": _pct(pl_sw, pl_sn),
        "spread_covered": xs_sw,
        "spread_graded": xs_sn,
        "spread_pct": _pct(xs_sw, xs_sn),
        "pl_total_correct": pl_ow,
        "pl_total_graded": pl_on,
        "pl_total_pct": _pct(pl_ow, pl_on),
        "total_correct": xs_ow,
        "total_graded": xs_on,
        "total_pct": _pct(xs_ow, xs_on),
    }


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
    # Results tiles grade after the published lines are on the card.
    # Prepare first and the picks box counts a different set of totals.
    if sport_u == "NCAAF" and hasattr(N, "_finalize_daily_result_cards"):
        try:
            N._finalize_daily_result_cards(sport_u, daily)
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
        if sport_u == "NCAAF":
            published = _published_results_html(sport_u)
            if "Last Night" in title:
                heading = "Last Night"
            elif "Last 7" in title:
                heading = "Last 7 Days"
            else:
                heading = ""
            tiles = _published_tiles(published, heading) if heading and published else None
            if tiles:
                # The results page already graded this window. Copy it exactly.
                games = tiles["games"] or games
                ml_pairs = [
                    (label, tiles["rows"].get(label) or val)
                    for (label, val) in ml_pairs
                ]
                if tiles["rows"].get("Spread"):
                    sp_pairs[0] = ("Prediction Lab", tiles["rows"]["Spread"])
                if tiles["rows"].get("Over/Under"):
                    ou_pairs[0] = ("Prediction Lab", tiles["rows"]["Over/Under"])
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


def _recent_block(sport_u: str) -> str:
    now = time.time()
    hit = _CACHE.get(sport_u)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    block = _build_block(sport_u)
    _CACHE[sport_u] = (now, block)
    return block


def inject_picks_recent_results(html: str, sport: str) -> str:
    if not html:
        return html
    has_block = 'id="picks-recent-results"' in html
    if not has_block:
        if "How These AI Picks Are Generated" not in html and "seo-picks-footer" not in html:
            if "game-card" not in html and "pick-card" not in html:
                if "golf-board" not in html:
                    return html
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return html
    try:
        block = _recent_block(sport_u)
    except Exception:
        return html
    if has_block:
        html2, n = re.subn(
            r'<section id="picks-recent-results"[\s\S]*?</section>',
            lambda _m: block,
            html,
            count=1,
            flags=re.I,
        )
        if n:
            return html2
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
