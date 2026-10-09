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


def _blank_rec(val: str) -> bool:
    raw = re.sub(r"\s+", "", val or "").lower()
    return raw in {"", "—", "–", "-", "n/a", "na"}


def _grade_mark(text: str) -> str:
    if "✅" in (text or ""):
        return "W"
    if "❌" in (text or ""):
        return "L"
    return ""


def _record_from_marks(marks) -> str | None:
    decided = [m for m in marks if m in {"W", "L"}]
    if not decided:
        return None
    wins = sum(1 for m in decided if m == "W")
    losses = len(decided) - wins
    acc = round(100.0 * wins / len(decided), 1)
    return f"{acc}% · {wins}-{losses}"


def _wl_pair(text: str) -> str:
    match = re.search(r"(\d+)\s*[-–—]\s*(\d+)", text or "")
    if not match:
        return ""
    return f"{int(match.group(1))}-{int(match.group(2))}"


def _load_wnba_results_cards() -> str:
    try:
        import NHL77FINAL as N

        hit = (getattr(N, "_SPORT_RESULTS_CACHE", {}) or {}).get(
            "WNBA_daily_results_html_v5"
        )
        html = hit.get("html") if isinstance(hit, dict) else ""
        if html and "game-card" in html:
            return html
    except Exception:
        pass
    try:
        from isolate_checker_fixes import lookup_served_results, results_serve_key

        html = lookup_served_results(results_serve_key("WNBA")) or ""
        if "game-card" in html:
            return html
    except Exception:
        pass
    return ""


def parse_wnba_result_cards(html: str) -> list[dict]:
    """Graded WNBA result cards: model, spread, and total marks actually printed."""
    cards = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    pending = iter(chunks[1:])
    for dk in pending:
        content = next(pending, "")
        parts = re.split(r'(<div class="game-card\b)', content)
        idx = 1
        while idx < len(parts):
            body = parts[idx] + (parts[idx + 1] if idx + 1 < len(parts) else "")
            idx += 2
            teams = re.findall(r'class="team-name">([^<]+)</div>', body)
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)', body)
            if len(teams) < 2:
                continue
            models: dict[str, str] = {}
            edge = {"side": "", "pct": "", "mark": ""}
            model_bits = []
            boxes = re.findall(
                r'class="pc-name">\s*([^<]+)</div>'
                r'[\s\S]{0,500}?class="pc-val">\s*([^<]*)</div>'
                r'[\s\S]{0,400}?class="pc-side[^"]*"[^>]*>([^<]*)</div>',
                body,
            )
            for name, pct, side in boxes:
                name = re.sub(r"\s+", " ", name).strip()
                side_txt = re.sub(r"[✅❌]", "", side).strip()
                mark = _grade_mark(side)
                if mark:
                    models[name] = mark
                if name == "Edge":
                    edge = {"side": side_txt, "pct": (pct or "").strip(), "mark": mark}
                if side_txt:
                    flag = "✓" if mark == "W" else "✗" if mark == "L" else ""
                    model_bits.append(f"{name} {side_txt} {flag}".strip())

            def _sf(label: str, body: str = body) -> str:
                found = re.search(
                    rf"{label}</span>\s*<span class=\"sf-val\">([\s\S]*?)</span>",
                    body,
                )
                return _grade_mark(found.group(1) if found else "")

            cards.append({
                "date": dk,
                "away": teams[0].strip(),
                "home": teams[1].strip(),
                "away_score": scores[0] if scores else "",
                "home_score": scores[1] if len(scores) > 1 else "",
                "models": models,
                "spread": _sf("Spread pick"),
                "total": _sf("Total pick"),
                "edge": edge,
                "model_bits": model_bits,
            })
    return cards


def _latest_graded_night(cards: list[dict], end: str) -> str:
    """Yesterday, or the newest graded card within 4 days when that day is empty.

    The results page uses the same fallback. An empty yesterday stays empty
    when nothing in that window was graded.
    """
    if any(c.get("date") == end for c in cards):
        return end
    dates = sorted({c["date"] for c in cards if c.get("date") and c["date"] < end})
    if not dates:
        return end
    latest = dates[-1]
    try:
        gap = (
            datetime.strptime(end, "%Y-%m-%d")
            - datetime.strptime(latest, "%Y-%m-%d")
        ).days
    except ValueError:
        return end
    if 0 < gap <= 4:
        return latest
    return end


def _wnba_card_windows(cards: list[dict]) -> dict:
    """Last Night and Last 7 from the same graded cards as the results page."""
    yest = _yesterday_et().replace(tzinfo=None)
    end = yest.strftime("%Y-%m-%d")
    start7 = (yest - timedelta(days=6)).strftime("%Y-%m-%d")
    night = _latest_graded_night(cards, end)
    out = {}
    for key, start, stop in (
        ("last night", night, night),
        ("last 7", start7, end),
    ):
        chosen = [c for c in cards if start <= c["date"] <= stop]
        if not chosen:
            continue
        money = {}
        for _key, label in _MODELS:
            rec = _record_from_marks(c["models"].get(label) for c in chosen)
            if rec:
                money[label] = rec
        out[key] = {
            "games": len(chosen),
            "as_of": stop if key == "last night" else end,
            "moneyline": money,
            "spread": _record_from_marks(c["spread"] for c in chosen),
            "total": _record_from_marks(c["total"] for c in chosen),
        }
    return out


def _window_key(title: str) -> str:
    low = (title or "").lower()
    if "last night" in low:
        return "last night"
    if "last 7" in low:
        return "last 7"
    if "last 30" in low:
        return "last 30"
    return ""


def _align_wnba_recent(title, games, ml_pairs, sp_pairs, ou_pairs, card_windows):
    """Use the results-page grades for Last Night and Last 7.

    A model with no printed grade is left off the strip. A filled grade is kept.
    """
    key = _window_key(title)
    card = (card_windows or {}).get(key) or {}
    if key in {"last night", "last 7"} and card:
        games = int(card.get("games") or games)
        money = card.get("moneyline") or {}
        if money:
            ml_pairs = [(label, money[label]) for _k, label in _MODELS if label in money]
        if card.get("spread"):
            sp_pairs = [
                (label, card["spread"] if label == "Prediction Lab" else val)
                for label, val in sp_pairs
            ]
        if card.get("total"):
            ou_pairs = [(label, card["total"]) for label, _val in ou_pairs]
    ml_pairs = [(label, val) for label, val in ml_pairs if not _blank_rec(val)]
    return games, ml_pairs, sp_pairs, ou_pairs


def wnba_chart_total_cells(cards_html: str = "") -> dict:
    """Totals W-L the chart must show: same grades as the picks strip."""
    cards = parse_wnba_result_cards(cards_html or _load_wnba_results_cards())
    windows = _wnba_card_windows(cards)
    cells = {
        "Prediction Lab": ["", "", ""],
        "XSharp": ["", "", ""],
    }
    for key, idx in (("last night", 0), ("last 7", 1)):
        rec = _wl_pair((windows.get(key) or {}).get("total") or "")
        if rec:
            cells["Prediction Lab"][idx] = rec
            cells["XSharp"][idx] = rec
    try:
        import NHL77FINAL as N

        yest = _yesterday_et().replace(tzinfo=None)
        start = yest - timedelta(days=29)
        daily = N._banner_daily_results_for_range(
            "WNBA", start, yest, prefer_weekly=False,
        ) or {}
        _fill_efficiency_and_grades("WNBA", daily)
        if daily and hasattr(N, "_compute_spread_total_for_daily"):
            try:
                N._compute_spread_total_for_daily(
                    "WNBA", daily, skip_efficiency=True,
                )
            except Exception:
                pass
        tally = N.compute_model_tally_for_range(daily, start, yest, sport="WNBA")
        st = _spread_total_stats(N, daily, start, yest, tally)
        for win_key, n_key, pct_key, label in _TOTAL_ROWS:
            pair = _wl_pair(_st_rec(st, win_key, n_key, pct_key))
            if pair:
                cells[label][2] = pair
    except Exception:
        pass
    return cells


def install_wnba_totals_chart(html: str, cards_html: str = "") -> str:
    """Replace the empty totals shell with the graded Prediction Lab and XSharp rows."""
    block = wnba_totals_chart_html(cards_html or html)
    if not block or not html:
        return html
    html2, n = re.subn(
        r'<div class="pl-consensus-records pl-xsharp-totals" id="pl-xsharp-totals">[\s\S]*?</div>',
        block,
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return html2
    if 'id="pl-totals-three-way"' in html:
        return re.sub(
            r'<section\b[^>]*id="pl-totals-three-way"[\s\S]*?</section>',
            block,
            html,
            count=1,
            flags=re.I,
        )
    return html + block


def wnba_totals_chart_html(cards_html: str = "") -> str:
    cells = wnba_chart_total_cells(cards_html)
    rows = []
    for label in ("Prediction Lab", "XSharp"):
        ln, d7, d30 = cells.get(label) or ("", "", "")
        if not any((ln, d7, d30)):
            continue
        rows.append(
            "<tr>"
            f'<td class="bucket">{escape(label)}</td>'
            f"<td>{escape(ln or '—')}</td>"
            f"<td>{escape(d7 or '—')}</td>"
            f"<td>{escape(d30 or '—')}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<section class="pl-consensus-records" id="pl-totals-three-way">'
        "<h2>Prediction Lab · XSharp — Totals</h2>"
        "<table><thead><tr><th>Signal</th><th>Last night</th>"
        "<th>Past 7 days</th><th>Past 30 days</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def wnba_finals_rows(cards_html: str = "") -> tuple[int, str]:
    cards = [
        c for c in parse_wnba_result_cards(cards_html or _load_wnba_results_cards())
        if c.get("away_score") and c.get("home_score")
    ]
    cards.sort(key=lambda c: c["date"], reverse=True)
    rows = []
    for card in cards:
        edge = card.get("edge") or {}
        mark = edge.get("mark") or ""
        result = "Correct" if mark == "W" else "Wrong" if mark == "L" else "—"
        pct = edge.get("pct") or "—"
        if pct not in {"", "—"} and not str(pct).endswith("%"):
            pct = f"{pct}%"
        score = f"{card['away_score']}–{card['home_score']}"
        rows.append(
            "<tr>"
            f"<td>{escape(card['date'])}</td><td>WNBA</td>"
            f"<td>{escape(card['away'])} @ {escape(card['home'])}</td>"
            f"<td>{escape(score)}</td>"
            f"<td>{escape(edge.get('side') or '—')}</td>"
            f"<td>{escape(pct)}</td>"
            f"<td>{escape(result)}</td>"
            f"<td>{escape(' '.join(card.get('model_bits') or []) or '—')}</td>"
            "</tr>"
        )
    return len(rows), "".join(rows)


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
    card_windows = {}
    if sport_u == "WNBA":
        card_windows = _wnba_card_windows(parse_wnba_result_cards(_load_wnba_results_cards()))
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
        if sport_u == "WNBA":
            games, ml_pairs, sp_pairs, ou_pairs = _align_wnba_recent(
                title, games, ml_pairs, sp_pairs, ou_pairs, card_windows,
            )
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


def _set_recent_row(col: str, market: str, label: str, value: str) -> str:
    """Replace one printed row. A missing row is left off; nothing is invented."""
    if not value:
        return col
    pattern = (
        rf'(<h4 class="picks-recent-mkt">{re.escape(market)}</h4>'
        rf'\s*<table><tbody>)([\s\S]*?)(</tbody>)'
    )

    def _repl(match: re.Match[str]) -> str:
        body = match.group(2)
        row = (
            rf'(<tr><th>{re.escape(label)}</th><td>)([^<]*)(</td></tr>)'
        )
        body2, n = re.subn(row, rf"\g<1>{escape(value)}\g<3>", body, count=1)
        if not n:
            return match.group(0)
        return match.group(1) + body2 + match.group(3)

    return re.sub(pattern, _repl, col, count=1)


def _paint_recent_column(col: str, window: dict) -> str:
    """Last Night / Last 7 cells from the graded result cards."""
    games = int(window.get("games") or 0)
    if games <= 0:
        return col
    col = re.sub(
        r'(<p class="picks-recent-n">)\d+',
        rf"\g<1>{games}",
        col,
        count=1,
    )
    as_of = str(window.get("as_of") or "")
    if as_of and "Last Night" in col:
        col = re.sub(
            r'(<h3>Last Night — )\d{4}-\d{2}-\d{2}',
            rf"\g<1>{as_of}",
            col,
            count=1,
        )
    money = window.get("moneyline") or {}
    for _key, label in _MODELS:
        if label in money:
            col = _set_recent_row(col, "Moneyline", label, money[label])
    if window.get("spread"):
        col = _set_recent_row(col, "Spread", "Prediction Lab", window["spread"])
    if window.get("total"):
        col = _set_recent_row(col, "Totals", "Prediction Lab", window["total"])
        col = _set_recent_row(col, "Totals", "XSharp", window["total"])
    return col


_WNBA_WINDOW_CACHE: tuple[float, dict] = (0.0, {})


def align_frozen_wnba_recent(html: str) -> str:
    """Point the saved Recent results strip at the result-card grades.

    Does not rebuild the block, so it does not train a spread model or scan
    30 days on the picks request. Last 30 stays as printed.
    """
    if not html or 'id="picks-recent-results"' not in html:
        return html
    now = time.time()
    cached_at, windows = _WNBA_WINDOW_CACHE
    if not windows or now - cached_at >= 60:
        cards = parse_wnba_result_cards(_load_wnba_results_cards())
        windows = _wnba_card_windows(cards) if cards else {}
        if windows:
            globals()["_WNBA_WINDOW_CACHE"] = (now, windows)
    if not windows:
        return html
    parts = re.split(r'(<div class="picks-recent-col">)', html, maxsplit=0)
    # re.split keeps the delimiter when the pattern is a group.
    if len(parts) < 3:
        return html
    out = [parts[0]]
    idx = 1
    while idx < len(parts):
        sep = parts[idx]
        body = parts[idx + 1] if idx + 1 < len(parts) else ""
        idx += 2
        key = _window_key(re.search(r"<h3>([^<]+)</h3>", body).group(1) if re.search(r"<h3>([^<]+)</h3>", body) else "")
        window = windows.get(key) if key in {"last night", "last 7"} else None
        if window:
            # Only the column itself, up to the next column, is in body
            # because the separator was split out. Paint the whole body.
            body = _paint_recent_column(sep + body, window)[len(sep):]
        out.append(sep + body)
    return "".join(out)


def inject_picks_recent_results(html: str, sport: str) -> str:
    sport_u = (sport or "").strip().upper()
    if (
        html
        and sport_u == "WNBA"
        and 'id="picks-recent-results"' in html
    ):
        # Rebuilding this block trains a spread model on the request and
        # pushes the first /wnba-picks hit over 5s. Rewrite the printed
        # Last Night and Last 7 cells from the graded cards instead.
        try:
            html = align_frozen_wnba_recent(html)
        except Exception:
            pass
        try:
            from wnba_ui_fixup import _rewrite_wnba_books_na_hover
            html = _rewrite_wnba_books_na_hover(html)
        except Exception:
            pass
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
