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


def _fmt_grade(correct: int, total: int) -> str:
    if total <= 0:
        return "—"
    acc = round(100.0 * correct / total, 1)
    return f"{acc:.1f}% · {correct}-{total - correct}"


def _ncaab_db_latest_final() -> str:
    """Latest completed NCAAB date already in the local games table."""
    import sqlite3
    from pathlib import Path

    db = Path(__file__).resolve().parent / "sports_predictions_original.db"
    if not db.is_file():
        return ""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            """
            SELECT MAX(date(game_date)) FROM games
            WHERE upper(sport) = 'NCAAB'
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            """
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return ""
    return str(row[0])[:10] if row and row[0] else ""


def _ncaab_strip_from_stored_cards() -> str | None:
    """Last Night / Last 7 / Last 30 from grades already printed on results cards.

    Off-season only. The calendar week is empty, so the windows end on the
    latest stored final. Counts come from the correct/wrong marks on those cards.
    """
    import re
    from pathlib import Path

    latest = _ncaab_db_latest_final()
    if not latest:
        return None
    try:
        latest_dt = datetime.strptime(latest, "%Y-%m-%d")
    except ValueError:
        return None
    yest = _yesterday_et().replace(tzinfo=None)
    if (yest.date() - latest_dt.date()).days <= 4:
        return None
    path = Path(__file__).resolve().parent / ".cache" / "served_NCAAB_.html"
    try:
        html = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    except OSError:
        html = ""
    if 'id="date-' not in html:
        return None
    parts = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html)
    seq = parts[1:]
    card_dates = seq[0::2]
    if not card_dates or max(card_dates) != latest:
        return None
    models = [label for _key, label in _MODELS]
    windows = (
        (f"Last Night — {latest}", latest_dt, latest_dt),
        ("Last 7 days", latest_dt - timedelta(days=6), latest_dt),
        ("Last 30 days", latest_dt - timedelta(days=29), latest_dt),
    )
    buckets = []
    for title, start, end in windows:
        ml = {name: [0, 0] for name in models}
        spread = [0, 0]
        total = [0, 0]
        games = 0
        for date, content in zip(card_dates, seq[1::2]):
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                continue
            if dt < start or dt > end:
                continue
            cards = re.split(r'<div class="game-card pick-card"', content)[1:]
            games += len(cards)
            for card in cards:
                for klass, name in re.findall(
                    r'<div class="pc-box([^"]*)"[^>]*>\s*<div class="pc-name">([^<]+)</div>',
                    card,
                ):
                    if name not in ml:
                        continue
                    low = klass.lower()
                    if "wrong" in low:
                        ml[name][1] += 1
                    elif "correct" in low:
                        ml[name][0] += 1
                        ml[name][1] += 1
                sm = re.search(
                    r"Spread pick</span>\s*<span class=\"sf-val\">[\s\S]*?(pick-ok|pick-no)",
                    card,
                )
                tm = re.search(
                    r"Total pick</span>\s*<span class=\"sf-val\">[\s\S]*?(pick-ok|pick-no)",
                    card,
                )
                if sm:
                    spread[1] += 1
                    if sm.group(1) == "pick-ok":
                        spread[0] += 1
                if tm:
                    total[1] += 1
                    if tm.group(1) == "pick-ok":
                        total[0] += 1
        buckets.append((title, games, ml, spread, total))
    if not buckets or buckets[0][1] <= 0:
        return None
    cols = []
    for title, games, ml, spread, total in buckets:
        ml_pairs = [(name, _fmt_grade(ml[name][0], ml[name][1])) for name in models]
        sp_pairs = [
            ("Prediction Lab", _fmt_grade(spread[0], spread[1])),
            ("XSharp", "—"),
        ]
        ou_pairs = [
            ("Prediction Lab", _fmt_grade(total[0], total[1])),
            ("XSharp", "—"),
        ]
        cols.append(
            f'<div class="picks-recent-col">'
            f"<h3>{escape(title)}</h3>"
            f'<p class="picks-recent-n">{games} games</p>'
            f'{_market_table("Moneyline", ml_pairs)}'
            f'{_market_table("Spread", sp_pairs)}'
            f'{_market_table("Totals", ou_pairs)}'
            f'<a class="picks-recent-more" href="/ncaab-results">Full results →</a>'
            f"</div>"
        )
    return _wrap_recent_section("".join(cols))


def _wrap_recent_section(cols_html: str) -> str:
    return (
        '<section id="picks-recent-results" class="picks-recent-results" '
        'aria-label="Recent results">'
        "<h2>Recent results</h2>"
        '<p class="picks-recent-sub">Last Night, Last 7, and Last 30 — same moneyline, '
        "spread, and total grades as the results page.</p>"
        f'<div class="picks-recent-grid">{cols_html}</div>'
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


def refresh_ncaab_offseason_strip(html: str) -> str:
    """Replace a baked empty Last Night with the latest stored final."""
    if not html or 'id="picks-recent-results"' not in html:
        return html
    now = time.time()
    latest = _ncaab_db_latest_final()
    hit = _CACHE.get("NCAAB")
    if (
        latest
        and hit
        and now - hit[0] < _TTL
        and f"Last Night — {latest}" in (hit[1] or "")
        and f"Last Night — {latest}</h3><p class=\"picks-recent-n\">0 games" not in (hit[1] or "")
    ):
        block = hit[1]
    else:
        try:
            block = _ncaab_strip_from_stored_cards()
        except Exception:
            block = None
        if not block:
            return html
        _CACHE["NCAAB"] = (now, block)
    import re

    html2, n = re.subn(
        r'<section id="picks-recent-results"[\s\S]*?</section>',
        lambda _m: block,
        html,
        count=1,
    )
    return html2 if n else html


def _build_block(sport: str) -> str:
    import NHL77FINAL as N

    sport_u = (sport or "").strip().upper()
    if sport_u == "NCAAB":
        stored = _ncaab_strip_from_stored_cards()
        if stored:
            return stored
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
    return _wrap_recent_section("".join(cols))


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
