"""Records counted straight from graded results cards (added 2026-10-07).

Shared helper for team sports whose picks "Recent results" strip has to show
the same Last Night / Last 7 / Last 30 grades as the results page. Every
number comes from a FINAL card's own marks; nothing is invented.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import escape, unescape

SIX = ("Grinder2", "Takedown", "Edge", "XSharp", "Sharp Consensus", "Efficiency")


def _num(text: str) -> float | None:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def _mark(card: str, label: str) -> str | None:
    m = re.search(
        rf'class="sf-label">{label}</span>\s*<span class="sf-val">([\s\S]*?)</span>\s*</div>',
        card,
    )
    if not m:
        return None
    val = m.group(1)
    if re.search(r"\bpush\b", re.sub(r"<[^>]+>", " ", val), re.I):
        return "PUSH"
    if "pick-ok" in val or "✅" in val:
        return "WIN"
    if "pick-no" in val or "❌" in val:
        return "LOSS"
    return None


def _line(card: str, kinds: tuple[str, ...]) -> list[str]:
    for kind in kinds:
        m = re.search(
            rf'<td class="market-k">{kind}</td>\s*<td class="val-books">([^<]*)</td>\s*'
            r'<td class="val-pl">([^<]*)</td>\s*<td class="val-xs">([^<]*)</td>',
            card,
        )
        if m:
            return [unescape(x).strip() for x in m.groups()]
    return ["", "", ""]


def card_rows(html: str, spread_kinds: tuple[str, ...] = ("Spread", "Puck Line", "Run Line")) -> list[dict]:
    """One row per FINAL card: date and every grade the card prints."""
    rows: list[dict] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        for card in re.split(r'(?=<div class="game-card\b)', content)[1:]:
            if 'class="game-time">FINAL<' not in card:
                continue
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)\s*<', card)
            names = re.findall(r'class="team-name">([^<]+)</div>', card)
            if len(scores) < 2 or len(names) < 2:
                continue
            a_sc, h_sc = int(scores[0]), int(scores[1])
            away, home = unescape(names[0]).strip(), unescape(names[1]).strip()
            models: dict[str, str] = {}
            for name, inner in re.findall(
                r'class="pc-name">([^<]+)</div>\s*<div class="pc-val"[^>]*>[^<]*</div>\s*'
                r'<div class="pc-side[^"]*"[^>]*>([^<]*)</div>',
                card,
            ):
                name = name.strip()
                if name in SIX and name not in models:
                    if "✅" in inner:
                        models[name] = "WIN"
                    elif "❌" in inner:
                        models[name] = "LOSS"
            actual = a_sc + h_sc
            xs_total = None
            book_t = _num(_line(card, ("Total",))[0])
            xs_proj = re.search(
                r'class="proj-model xs">[^<]*</span>\s*<span class="proj-val">([^<]+)</span>', card
            )
            if xs_proj and book_t is not None:
                nums = re.findall(r"(\d+(?:\.\d+)?)", xs_proj.group(1))
                if len(nums) >= 2:
                    proj = float(nums[-2]) + float(nums[-1])
                    if proj != book_t and actual == book_t:
                        xs_total = "PUSH"
                    elif proj > book_t:
                        xs_total = "WIN" if actual > book_t else "LOSS"
                    elif proj < book_t:
                        xs_total = "WIN" if actual < book_t else "LOSS"

            def _home_line(text: str) -> float | None:
                text = (text or "").strip()
                if text.upper() in ("PK", "PICK", "PICK'EM", "EVEN"):
                    return 0.0
                m = re.match(r"(.+?)\s+([+-]\d+(?:\.\d+)?)$", text)
                if not m:
                    return None
                team, val = m.group(1).strip().lower(), float(m.group(2))
                if team == home.lower() or team.endswith(" " + home.lower()):
                    return val
                if team == away.lower() or team.endswith(" " + away.lower()):
                    return -val
                return None

            xs_spread = None
            books_l, _pl_l, xs_l = _line(card, spread_kinds)
            hb, hx = _home_line(books_l), _home_line(xs_l)
            if hb is not None and hx is not None and hb != hx:
                xs_home = hx < hb
                cover = (h_sc - a_sc) + hb
                if cover == 0:
                    xs_spread = "PUSH"
                else:
                    xs_spread = "WIN" if (cover > 0) == xs_home else "LOSS"
            rows.append(
                {
                    "date": dk,
                    "models": models,
                    "pl_spread": _mark(card, "Spread pick"),
                    "pl_total": _mark(card, "Total pick"),
                    "xs_spread": xs_spread,
                    "xs_total": xs_total,
                }
            )
    return rows


def card_windows(html: str) -> dict:
    """Last night, the printed Last 7 window and 30 days, from the cards."""
    rows = card_rows(html)
    if not rows:
        return {}
    night = re.search(r"Last Night's [^<]{0,120}?(\d{4}-\d{2}-\d{2})", html or "", flags=re.I)
    ln_key = night.group(1) if night else max(r["date"] for r in rows)
    try:
        ln_d = date.fromisoformat(ln_key)
    except ValueError:
        return {}
    week = re.search(
        r"Last 7 Days [^<]{0,160}?(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", html or "", flags=re.I
    )
    lo7, hi7 = (week.group(1), week.group(2)) if week else ((ln_d - timedelta(days=6)).isoformat(), ln_key)
    lo30 = (ln_d - timedelta(days=29)).isoformat()

    def _wl(grades: list) -> list[int]:
        return [grades.count("WIN"), grades.count("LOSS"), grades.count("PUSH")]

    out: dict = {"ln_key": ln_key}
    for key, (lo, hi) in {"ln": (ln_key, ln_key), "l7": (lo7, hi7), "l30": (lo30, ln_key)}.items():
        sel = [r for r in rows if lo <= r["date"] <= hi]
        out[key] = {
            "games": len(sel),
            "models": {n: _wl([r["models"].get(n) for r in sel]) for n in SIX},
            "pl_spread": _wl([r["pl_spread"] for r in sel]),
            "xs_spread": _wl([r["xs_spread"] for r in sel]),
            "pl_total": _wl([r["pl_total"] for r in sel]),
            "xs_total": _wl([r["xs_total"] for r in sel]),
        }
    return out


def _rec(rec: list[int]) -> str:
    return f"{rec[0]}-{rec[1]}" + (f"-{rec[2]}" if rec[2] else "")


def _face(rec: list[int]) -> str:
    w, l, _p = rec
    if w + l == 0:
        return "—"
    return f"{round(100.0 * w / (w + l), 1)}% · {_rec(rec)}"


def recent_columns(wins: dict, results_href: str) -> str:
    def _table(title: str, pairs) -> str:
        body = "".join(f"<tr><th>{escape(n)}</th><td>{_face(r)}</td></tr>" for n, r in pairs)
        return f'<h4 class="picks-recent-mkt">{escape(title)}</h4><table><tbody>{body}</tbody></table>'

    cols = []
    for title, key in ((f"Last Night — {wins['ln_key']}", "ln"), ("Last 7 days", "l7"), ("Last 30 days", "l30")):
        win = wins[key]
        cols.append(
            '<div class="picks-recent-col">'
            f"<h3>{escape(title)}</h3>"
            f'<p class="picks-recent-n">{win["games"]} games</p>'
            + _table("Moneyline", [(n, win["models"][n]) for n in SIX])
            + _table("Spread", [("Prediction Lab", win["pl_spread"]), ("XSharp", win["xs_spread"])])
            + _table("Totals", [("Prediction Lab", win["pl_total"]), ("XSharp", win["xs_total"])])
            + f'<a class="picks-recent-more" href="{escape(results_href)}">Full results →</a>'
            "</div>"
        )
    return "".join(cols)


def replace_recent_columns(picks_html: str, cards_html: str, results_href: str) -> str:
    """Swap the Recent results columns for ones counted from the results cards."""
    if not picks_html or 'id="picks-recent-results"' not in picks_html or not cards_html:
        return picks_html
    wins = card_windows(cards_html)
    if not wins:
        return picks_html
    sec = re.search(r'<section id="picks-recent-results"[\s\S]*?</section>', picks_html)
    if not sec:
        return picks_html
    block = sec.group(0)
    grid = re.search(r'(<div class="picks-recent-grid">)([\s\S]*?)(</div>\s*<style>)', block)
    if not grid:
        return picks_html
    fresh = block[: grid.start(2)] + recent_columns(wins, results_href) + block[grid.end(2):]
    return picks_html[: sec.start()] + fresh + picks_html[sec.end():]


# ── Copy the results page's own printed records onto picks Recent results ──

def _tally_block(html: str, title_re: str) -> dict[str, str]:
    head = re.search(title_re, html or "", flags=re.I)
    if not head:
        return {}
    nxt = html.find('<div class="daily-tally"', head.end())
    chunk = html[head.end(): nxt if nxt > 0 else head.end() + 8000]
    out: dict[str, str] = {}
    for name, rec in re.findall(
        r'<div class="daily-model">[^<A-Za-z]*([A-Za-z][^<]*?)</div>\s*<div class="daily-acc"[^>]*>[^<]*</div>\s*'
        r'<div class="daily-rec">([^<]*)</div>',
        chunk,
    ):
        m = re.search(r"\d+-\d+(?:-\d+)?", rec)
        if m:
            out[name.strip()] = m.group(0)
    return out


def _plxs_table(html: str, table_id: str) -> dict[str, list[str]]:
    at = (html or "").find(f'id="{table_id}"')
    if at < 0:
        return {}
    end = html.find("</table>", at)
    out: dict[str, list[str]] = {}
    for label, rest in re.findall(
        r'<tr>\s*<td class="(?:bucket|signal)">([^<]+)</td>([\s\S]*?)</tr>', html[at:end]
    ):
        cells = re.findall(r"<td(?:\s[^>]*)?>([\s\S]*?)</td>", rest)
        vals = []
        for cell in cells[:3]:
            m = re.search(r"\d+-\d+(?:-\d+)?", re.sub(r"<[^>]+>", " ", cell))
            vals.append(m.group(0) if m else "")
        if len(vals) == 3:
            out[label.strip()] = vals
    return out


def _face_text(rec: str) -> str:
    parts = [int(x) for x in rec.split("-")]
    w, l = parts[0], parts[1]
    if w + l == 0:
        return rec  # graded window with no decided pick (e.g. 0-0)
    return f"{round(100.0 * w / (w + l), 1)}% · {rec}"


def copy_recent_from_results(
    picks_html: str,
    results_html: str,
    sport_label: str,
    spread_table: str = "pl-spread-records",
    totals_table: str = "pl-totals-records",
    headings: bool = False,
) -> str:
    """Picks Recent results show the records the results page prints, cell for cell.

    headings=True also copies the Last Night date and the game counts.
    """
    if not picks_html or 'id="picks-recent-results"' not in picks_html or not results_html:
        return picks_html
    ln = _tally_block(results_html, rf"<h2>\s*Last Night's {sport_label} Results[^<]*</h2>")
    l7 = _tally_block(results_html, rf"<h2>\s*Last 7 Days {sport_label} Results[^<]*</h2>")
    sp = _plxs_table(results_html, spread_table)
    to = _plxs_table(results_html, totals_table)
    ln_head = re.search(
        rf"Last Night's {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*\((\d+)\s+games?\)",
        results_html,
    )
    l7_head = re.search(rf"Last 7 Days {sport_label} Results[^<(]*\((\d+)\s+games?\)", results_html)
    if not ln and not l7 and not sp and not to:
        return picks_html
    sec = re.search(r'<section id="picks-recent-results"[\s\S]*?</section>', picks_html)
    if not sec:
        return picks_html
    block = sec.group(0)
    parts = re.split(r'(?=<div class="picks-recent-col">)', block)
    for i, col in enumerate(parts):
        title = re.search(r"<h3>([^<]+)</h3>", col)
        if not title:
            continue
        t = title.group(1).lower()
        idx = 0 if "last night" in t else 1 if "last 7" in t else 2 if "last 30" in t else None
        if idx is None:
            continue
        tally = ln if idx == 0 else l7 if idx == 1 else {}
        want: dict[tuple[str, str], str] = {}
        for name in SIX:
            if tally.get(name):
                want[("Moneyline", name)] = tally[name]
        if tally.get("Spread"):
            want[("Spread", "Prediction Lab")] = tally["Spread"]
        if tally.get("Over/Under"):
            want[("Totals", "Prediction Lab")] = tally["Over/Under"]
        for market, table in (("Spread", sp), ("Totals", to)):
            for model in ("Prediction Lab", "XSharp"):
                vals = table.get(model)
                if vals and vals[idx] and (market, model) not in want:
                    want[(market, model)] = vals[idx]

        def _mkt(m: re.Match[str]) -> str:
            market = m.group(1).strip()

            def _row(r: re.Match[str]) -> str:
                rec = want.get((market, r.group(1).strip()))
                if not rec:
                    return r.group(0)
                return f"<tr><th>{r.group(1)}</th><td>{_face_text(rec)}</td></tr>"

            body = re.sub(r"<tr><th>([^<]+)</th><td>[^<]*</td></tr>", _row, m.group(2))
            return m.group(0).replace(m.group(2), body, 1)

        col = re.sub(
            r'<h4 class="picks-recent-mkt">([^<]+)</h4>\s*<table><tbody>([\s\S]*?)</tbody>', _mkt, col
        )
        if headings and idx == 0 and ln_head:
            col = re.sub(r"<h3>[^<]*</h3>", f"<h3>Last Night — {ln_head.group(1)}</h3>", col, count=1)
            col = re.sub(r'(<p class="picks-recent-n">)\d+', rf"\g<1>{ln_head.group(2)}", col, count=1)
        if headings and idx == 1 and l7_head:
            col = re.sub(r'(<p class="picks-recent-n">)\d+', rf"\g<1>{l7_head.group(1)}", col, count=1)
        parts[i] = col
    fresh = "".join(parts)
    return picks_html[: sec.start()] + fresh + picks_html[sec.end():]


def _cell_like(sample: str, rec: list[int]) -> str:
    """W-L cell in the same style as the table's other cells."""
    w, l, p = rec
    text = f"{w}-{l}" + (f"-{p}" if p else "")
    if w + l == 0:
        return text if p else "0-0"
    pct = round(100 * w / (w + l))
    if "cons-bar" not in sample:
        return text
    color = "#067647" if pct >= 55 else ("#ca8a04" if pct >= 50 else "#D93025")
    return (
        f"{text} <span style='color:{color};font-weight:700'>({pct}%)</span>"
        f"<div class='cons-bar' aria-hidden='true'><i style='width:{max(4, pct)}%;background:{color}'></i></div>"
    )


def _copy_row(html: str, src: str, table_id: str, label: str) -> str:
    """Copy one <tr> (by bucket label) of a table from src into html."""
    pat = rf'<tr><td class="bucket">{re.escape(label)}</td>[\s\S]*?</tr>'
    sa, da = src.find(f'id="{table_id}"'), html.find(f'id="{table_id}"')
    if sa < 0 or da < 0:
        return html
    se, de = src.find("</table>", sa), html.find("</table>", da)
    sm = re.search(pat, src[sa:se])
    dm = re.search(pat, html[da:de])
    if not sm or not dm:
        return html
    return html[: da + dm.start()] + sm.group(0) + html[da + dm.end():]


def set_pl_rows_from_cards(html: str, cards_html: str, xs_from: str | None = None) -> str:
    """Prediction Lab rows of the PL & XSharp spread/totals tables = the card marks.

    The Prediction Lab pick and its ✅/❌ are printed on every FINAL card, so
    those rows are counted from the cards. XSharp rows stay as printed.
    """
    if not html or not cards_html:
        return html
    wins = card_windows(cards_html)
    if not wins:
        return html
    for table_id, key in (("pl-spread-records", "pl_spread"), ("pl-totals-records", "pl_total")):
        if xs_from:
            # Chart tabs: the XSharp row is the cards page's row, cell for cell.
            html = _copy_row(html, xs_from, table_id, "XSharp")
        at = html.find(f'id="{table_id}"')
        if at < 0:
            continue
        end = html.find("</table>", at)
        seg = html[at:end]
        m = re.search(r'<tr><td class="bucket">Prediction Lab</td>([\s\S]*?)</tr>', seg)
        if not m:
            continue
        cells = re.findall(r"<td(?:\s[^>]*)?>[\s\S]*?</td>", m.group(1))
        if len(cells) < 3:
            continue
        recs = [wins[w][key] for w in ("ln", "l7", "l30")]
        if not any(sum(r) for r in recs):
            continue
        row = '<tr><td class="bucket">Prediction Lab</td>' + "".join(
            f"<td>{_cell_like(cells[i], recs[i])}</td>" for i in range(3)
        ) + "</tr>"
        seg = seg[: m.start()] + row + seg[m.end():]
        # Re-read the 30-day line under the table from the rows now printed.
        rows = _plxs_table(seg + "</table>", table_id)
        pl30 = (rows.get("Prediction Lab") or ["", "", ""])[2]
        xs30 = (rows.get("XSharp") or ["", "", ""])[2]
        label = "spread" if key == "pl_spread" else "totals"
        read = _calibration_line(pl30, xs30, label)
        html = html[:at] + seg + html[end:]
        end = at + len(seg)
        near = re.search(r'<p class="cons-read">[\s\S]*?</p>', html[end:end + 800])
        if near:
            s0, e0 = end + near.start(), end + near.end()
            html = html[:s0] + read + html[e0:]
    return html


def _pct_of(rec: str) -> float | None:
    m = re.match(r"(\d+)-(\d+)", rec or "")
    if not m:
        return None
    w, l = int(m.group(1)), int(m.group(2))
    return 100.0 * w / (w + l) if (w + l) else None


def _calibration_line(pl_rec: str, xs_rec: str, label: str) -> str:
    """Same wording as mlb_consensus_hub._plxs_calibration_line."""
    pl_pct, xs_pct = _pct_of(pl_rec), _pct_of(xs_rec)
    bits = [
        "Prediction Lab —" if pl_pct is None else f"Prediction Lab {pl_pct:.0f}%",
        "XSharp —" if xs_pct is None else f"XSharp {xs_pct:.0f}%",
    ]
    if pl_pct is None and xs_pct is None:
        read = f"Not enough graded {label} games in the last 30 days."
    elif pl_pct is not None and xs_pct is not None:
        if pl_pct + 1.0 < 50 and xs_pct + 1.0 < 50:
            read = f"Both models are under .500 on {label} over the last 30 days."
        elif abs(pl_pct - xs_pct) < 1.5:
            read = f"Prediction Lab and XSharp are even on {label} over the last 30 days."
        elif pl_pct > xs_pct:
            read = f"Prediction Lab is ahead of XSharp on {label} over the last 30 days."
        else:
            read = f"XSharp is ahead of Prediction Lab on {label} over the last 30 days."
    else:
        read = f"Last 30 days on {label}."
    return f'<p class="cons-read">{read} {" · ".join(bits)}</p>'


def _cons_key(label: str) -> str:
    text = unescape(label or "").lower()
    text = text.replace("/ no consensus", "")
    text = re.sub(r"[–—-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def consensus_past7(results_html: str) -> dict[str, str]:
    """Row label -> Past 7 W-L printed on the consensus table."""
    start = (results_html or "").find("Consensus Based Betting Records")
    if start < 0:
        return {}
    end = results_html.find("</table>", start)
    out: dict[str, str] = {}
    for label, rest in re.findall(
        r'<tr>\s*<td class="bucket">([^<]+)</td>([\s\S]*?)</tr>', results_html[start:end]
    ):
        cells = re.findall(r"<td(?:\s[^>]*)?>([\s\S]*?)</td>", rest)
        if len(cells) >= 2:
            m = re.search(r"\d+-\d+(?:-\d+)?", re.sub(r"<[^>]+>", " ", cells[1]))
            out[_cons_key(label)] = m.group(0) if m else "0-0"
    return out


def sync_consensus_chips(html: str, results_html: str) -> str:
    """Card chip "Consensus Record: <pattern> (W-L)" = that row's Past 7 on the table."""
    if not html or "Consensus Record:" not in html:
        return html
    past7 = consensus_past7(results_html)
    if not past7:
        return html

    def _chip(m: re.Match[str]) -> str:
        label = m.group(1).strip()
        key = _cons_key(label)
        rec = past7.get(key)
        if rec is None and key.endswith(" split"):
            rec = next((v for k, v in past7.items() if k.startswith(key)), None)
        if rec is None:
            rec = "0-0"
        return f"Consensus Record: {label} ({rec})"

    return re.sub(r"Consensus Record: ([^<(]+?)\s*\((\d+-\d+(?:-\d+)?)\)", _chip, html)


def _acc(rec: list[int]) -> str:
    w, l, _p = rec
    return f"{round(100.0 * w / (w + l), 1)}%" if (w + l) else "—"


def paint_tallies(html: str, sport_label: str, models: bool = True) -> str:
    """Last Night / Last 7 tally cards (six models, Spread, Over/Under) = card marks.

    models=False only repaints the Spread and Over/Under cards.
    """
    if not html or "game-card" not in html:
        return html
    wins = card_windows(html)
    if not wins:
        return html
    for title_re, key in (
        (rf"<h2>\s*Last Night's {sport_label} Results[^<]*</h2>", "ln"),
        (rf"<h2>\s*Last 7 Days {sport_label} Results[^<]*</h2>", "l7"),
    ):
        head = re.search(title_re, html, flags=re.I)
        if not head:
            continue
        start = head.end()
        nxt = html.find('<div class="daily-tally"', start)
        if nxt < 0:
            nxt = min(len(html), start + 8000)
        chunk = html[start:nxt]
        win = wins[key]
        faces = dict(win["models"]) if models else {}
        faces["Spread"] = win["pl_spread"]
        faces["Over/Under"] = win["pl_total"]
        for name, rec in faces.items():
            if sum(rec) == 0:
                continue
            chunk = re.sub(
                rf'(<div class="daily-model">[^<]*?{re.escape(name)}</div>\s*<div class="daily-acc"[^>]*>)'
                rf'[^<]*(</div>\s*<div class="daily-rec">)[^<]*(</div>)',
                lambda m, r=rec: m.group(1) + _acc(r) + m.group(2) + _rec(r) + m.group(3),
                chunk,
                count=1,
            )
        html = html[:start] + chunk + html[nxt:]
    return html


def _pl_total_grades_on(html: str, day: str) -> list[str]:
    """Prediction Lab total lean (its total vs the book total) graded on one date."""
    grades: list[str] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        if dk != day:
            continue
        for card in re.split(r'(?=<div class="game-card\b)', content)[1:]:
            if 'class="game-time">FINAL<' not in card:
                continue
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)\s*<', card)
            books, pl, _xs = _line(card, ("Total",))
            b, p = _num(books), _num(pl)
            if len(scores) < 2 or b is None or p is None or p == b:
                continue
            actual = int(scores[0]) + int(scores[1])
            if actual == b:
                grades.append("PUSH")
            elif (p > b) == (actual > b):
                grades.append("WIN")
            else:
                grades.append("LOSS")
    return grades


def fix_impossible_last_night_totals(html: str, sport_label: str, totals_table: str) -> str:
    """A Last Night Over/Under record larger than last night's slate is a stale number.

    Recount that one record from the cards (Prediction Lab total vs the book total).
    """
    head = re.search(
        rf"Last Night's {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*\((\d+)\s+games?\)",
        html or "",
    )
    if not head:
        return html
    day, n = head.group(1), int(head.group(2))
    grades = _pl_total_grades_on(html, day)
    rec = [grades.count("WIN"), grades.count("LOSS"), grades.count("PUSH")]

    def _too_big(text: str) -> bool:
        m = re.search(r"(\d+)-(\d+)(?:-(\d+))?", text or "")
        return bool(m) and sum(int(x or 0) for x in m.groups()) > n

    # Tally card
    tally = re.search(
        rf"(<h2>\s*Last Night's {sport_label} Results[^<]*</h2>[\s\S]*?"
        r'<div class="daily-model">[^<]*Over/Under</div>\s*<div class="daily-acc"[^>]*>)([^<]*)'
        r'(</div>\s*<div class="daily-rec">)([^<]*)(</div>)',
        html,
    )
    if tally and _too_big(tally.group(4)):
        acc = _acc(rec) if (rec[0] + rec[1]) else "—"
        html = (
            html[: tally.start(2)] + acc + tally.group(3) + _rec(rec) + tally.group(5)
            + html[tally.end():]
        )
    # Totals table, Prediction Lab row, Last night cell
    at = html.find(f'id="{totals_table}"')
    if at >= 0:
        end = html.find("</table>", at)
        seg = html[at:end]
        m = re.search(r'(<tr><td class="bucket">Prediction Lab</td><td>)([^<]*)(</td>)', seg)
        if m and _too_big(m.group(2)):
            seg = seg[: m.start(2)] + _rec(rec) + seg[m.end(2):]
            html = html[:at] + seg + html[end:]
    return html


def copy_consensus_body(html: str, cards_html: str) -> str:
    """Chart view prints the same consensus rows as the cards page."""
    marker = "Consensus Based Betting Records"
    cs, hs = (cards_html or "").find(marker), (html or "").find(marker)
    if cs < 0 or hs < 0:
        return html
    ce, he = cards_html.find("PL vs Sportsbook", cs), html.find("PL vs Sportsbook", hs)
    ctb, cte = cards_html.find("<tbody>", cs), cards_html.find("</tbody>", cs)
    htb, hte = html.find("<tbody>", hs), html.find("</tbody>", hs)
    if min(ctb, cte, htb, hte) < 0:
        return html
    if (ce > 0 and cte > ce) or (he > 0 and hte > he):
        return html
    return html[:htb] + cards_html[ctb:cte] + html[hte:]


def ensure_pl_totals_row(html: str, src_table: str = "pl-totals-vs-books",
                         dst_table: str = "pl-totals-three-way") -> str:
    """Totals table gets the Prediction Lab row the page already prints in src_table."""
    src = _plxs_table(html, src_table).get("Prediction Lab")
    at = (html or "").find(f'id="{dst_table}"')
    if not src or at < 0:
        return html
    end = html.find("</table>", at)
    seg = html[at:end]
    cells = "".join(f"<td>{v or '0-0'}</td>" for v in src)
    row = f'<tr><td class="bucket">Prediction Lab</td>{cells}</tr>'
    m = re.search(r'<tr><td class="bucket">Prediction Lab</td>[\s\S]*?</tr>', seg)
    if m:
        seg = seg[: m.start()] + row + seg[m.end():]
    else:
        xm = re.search(r'<tr><td class="bucket">XSharp</td>', seg)
        if not xm:
            return html
        seg = seg[: xm.start()] + row + seg[xm.start():]
    return html[:at] + seg + html[end:]


def served_results_html(sport: str) -> str:
    """The last finished /<sport>-results cards page exactly as it was served."""
    from pathlib import Path

    path = Path(__file__).resolve().parent / ".cache" / f"served_{(sport or '').strip().upper()}_.html"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return text if 'class="game-card' in text else ""


def append_plxs_rows(html: str, src_html: str, src_table: str, dst_table: str) -> str:
    """Give a three-way totals/spread table the plain Prediction Lab and XSharp rows
    the page already prints in src_table (same numbers, plain W-L cells)."""
    rows = _plxs_table(src_html, src_table)
    at = (html or "").find(f'id="{dst_table}"')
    if not rows or at < 0:
        return html
    tb = html.find("<tbody>", at)
    te = html.find("</tbody>", tb)
    if tb < 0 or te < 0:
        return html
    body = html[tb + len("<tbody>"):te]
    for label in ("Prediction Lab", "XSharp"):
        vals = rows.get(label)
        if not vals or not any(vals):
            continue
        row = f'<tr><td class="bucket">{label}</td>' + "".join(
            f"<td>{v or '—'}</td>" for v in vals
        ) + "</tr>"
        pat = rf'<tr><td class="bucket">{re.escape(label)}</td>[\s\S]*?</tr>'
        if re.search(pat, body):
            body = re.sub(pat, row, body, count=1)
        else:
            body += row
    return html[: tb + len("<tbody>")] + body + html[te:]


_FOUR = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")
_FOUR_LABELS = {
    4: "4/4 Unanimous",
    3: "3/4 Strong consensus",
    2: "2/4 Split / no consensus",
}


def four_model_past7(html: str, cards_html: str, sport_label: str = "WNBA") -> str:
    """Past 7 column of the 4-model consensus table = the cards inside the
    page's own "Last 7 Days … A to B" window (2/4 splits are pushes)."""
    week = re.search(
        rf"Last 7 Days {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}}) to (\d{{4}}-\d{{2}}-\d{{2}})",
        cards_html or "",
    )
    if not week or "Consensus Based Betting Records" not in (html or ""):
        return html
    lo, hi = week.group(1), week.group(2)
    tallies: dict[str, list[str]] = {}
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', cards_html)
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        if not (lo <= dk <= hi):
            continue
        for card in re.split(r'(?=<div class="game-card\b)', content)[1:]:
            picks: dict[str, tuple[str, bool]] = {}
            for name, side in re.findall(
                r'class="pc-name">([^<]+)</div>\s*<div class="pc-val"[^>]*>[^<]*</div>\s*'
                r'<div class="pc-side[^"]*"[^>]*>([^<]*)</div>',
                card[:25000],
            ):
                name = name.strip()
                team = re.sub(r"[✅❌]", "", side).strip()
                if name in _FOUR and team and name not in picks and ("✅" in side or "❌" in side):
                    picks[name] = (team, "✅" in side)
            if len(picks) < 4:
                continue
            counts: dict[str, int] = {}
            for team, _ok in picks.values():
                counts[team] = counts.get(team, 0) + 1
            top = max(counts.values())
            label = _FOUR_LABELS.get(top)
            if not label:
                continue
            if top == 2:
                grade = "PUSH"
            else:
                maj = next(t for t, n in counts.items() if n == top)
                grade = "WIN" if next(ok for t, ok in picks.values() if t == maj) else "LOSS"
            tallies.setdefault(label.lower(), []).append(grade)
    start = html.find("Consensus Based Betting Records")
    tb, te = html.find("<tbody>", start), html.find("</tbody>", start)
    if tb < 0 or te < 0:
        return html

    def _cell(grades: list[str]) -> str:
        w, l, p = grades.count("WIN"), grades.count("LOSS"), grades.count("PUSH")
        if w + l + p == 0:
            return "0-0"
        rec = f"{w}-{l}" + (f"-{p}" if p else "")
        if w + l == 0:
            return rec
        pct = round(100 * w / (w + l))
        color = "#067647" if pct >= 55 else ("#ca8a04" if pct >= 50 else "#D93025")
        return (
            f"{rec} <span style='color:{color};font-weight:700'>({pct}%)</span>"
            f"<div class='cons-bar' aria-hidden='true'><i style='width:{max(4, pct)}%;background:{color}'></i></div>"
        )

    def _row(m: re.Match[str]) -> str:
        label = m.group(1)
        cells = re.findall(r"<td(?:\s[^>]*)?>[\s\S]*?</td>", m.group(2))
        if len(cells) < 3:
            return m.group(0)
        cells[1] = f"<td>{_cell(tallies.get(label.strip().lower(), []))}</td>"
        return f'<tr><td class="bucket">{label}</td>' + "".join(cells) + "</tr>"

    body = re.sub(r'<tr>\s*<td class="bucket">([^<]+)</td>([\s\S]*?)</tr>', _row, html[tb:te])
    return html[:tb] + body + html[te:]


_BOX_RE = re.compile(
    r'class="pc-name">([^<]+)</div>(?:\s*<button[^>]*>[^<]*</button>)?\s*'
    r'<div class="pc-val"[^>]*>[^<]*</div>\s*<div class="pc-side[^"]*"[^>]*>([^<]*)</div>'
)


def _variable_panel_games(cards_html: str) -> list[dict]:
    """One consensus row per graded card, against the models that card shows."""
    games: list[dict] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', cards_html or "")
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        for card in re.split(r'(?=<div class="game-card\b)', content)[1:]:
            picks: dict[str, tuple[str, bool]] = {}
            for name, side in _BOX_RE.findall(card[:40000]):
                name = name.strip()
                team = re.sub(r"[✅❌]", "", side).strip()
                if name not in SIX or name in picks or not team or team.upper() in ("N/A", "NA", "—", "-"):
                    continue
                if "✅" in side or "❌" in side:
                    picks[name] = (team, "✅" in side)
            panel = len(picks)
            if panel < 3:
                continue
            counts: dict[str, int] = {}
            for team, _ok in picks.values():
                counts[team] = counts.get(team, 0) + 1
            top = max(counts.values())
            if list(counts.values()).count(top) > 1:
                continue
            maj = next(t for t, n in counts.items() if n == top)
            dissent = [n for n in SIX if n in picks and picks[n][0] != maj]
            label = f"{panel}/{panel} unanimous" if not dissent else (
                f"{top}/{panel} — all but " + " and ".join(dissent)
            )
            ok = next(picks[n][1] for n in SIX if n in picks and picks[n][0] == maj)
            games.append({"date": dk, "label": label, "panel": panel, "maj": top,
                          "grade": "WIN" if ok else "LOSS"})
    return games


def variable_panel_consensus(html: str, cards_html: str, sport_label: str) -> str:
    """Consensus rows rebuilt from the cards, each card graded on the models it shows."""
    if not html or "Consensus Based Betting Records" not in html:
        return html
    games = _variable_panel_games(cards_html)
    if not games:
        return html
    night = re.search(rf"Last Night's {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}})", cards_html)
    week = re.search(rf"Last 7 Days {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}}) to (\d{{4}}-\d{{2}}-\d{{2}})", cards_html)
    ln_key = night.group(1) if night else max(g["date"] for g in games)
    try:
        ln_d = date.fromisoformat(ln_key)
    except ValueError:
        return html
    lo7, hi7 = (week.group(1), week.group(2)) if week else ((ln_d - timedelta(days=6)).isoformat(), ln_key)
    lo30 = (ln_d - timedelta(days=29)).isoformat()

    def _cell(grades: list[str]) -> str:
        w, l = grades.count("WIN"), grades.count("LOSS")
        if w + l == 0:
            return "0-0"
        pct = round(100 * w / (w + l))
        color = "#067647" if pct >= 55 else ("#ca8a04" if pct >= 50 else "#D93025")
        return (
            f"{w}-{l} <span style='color:{color};font-weight:700'>({pct}%)</span>"
            f"<div class='cons-bar' aria-hidden='true'><i style='width:{max(4, pct)}%;background:{color}'></i></div>"
        )

    labels = sorted(
        {(g["panel"], g["maj"], g["label"]) for g in games if lo30 <= g["date"] <= ln_key or lo7 <= g["date"] <= hi7},
        key=lambda t: (-t[0], -t[1], t[2]),
    )
    if not labels:
        return html
    rows = []
    for _p, _m, label in labels:
        ln = [g["grade"] for g in games if g["label"] == label and g["date"] == ln_key]
        p7 = [g["grade"] for g in games if g["label"] == label and lo7 <= g["date"] <= hi7]
        p30 = [g["grade"] for g in games if g["label"] == label and lo30 <= g["date"] <= ln_key]
        rows.append(
            f'<tr><td class="bucket">{escape(label)}</td>'
            f"<td>{_cell(ln)}</td><td>{_cell(p7)}</td><td>{_cell(p30)}</td></tr>"
        )
    start = html.find("Consensus Based Betting Records")
    tb, te = html.find("<tbody>", start), html.find("</tbody>", start)
    nxt = html.find("PL vs Sportsbook", start)
    if tb < 0 or te < 0 or (0 <= nxt < te):
        return html
    html = html[: tb + len("<tbody>")] + "".join(rows) + html[te:]
    return re.sub(
        r"Moneyline on the pregame majority among the \d+ live models\.",
        "Moneyline on the pregame majority among the live models shown on each card.",
        html,
        count=1,
    )


def blank_unavailable_model_values(html: str) -> str:
    """A model box that says its rating is not available shows N/A, not a number."""
    if not html or "is not available" not in html:
        return html
    return re.sub(
        r'(<div class="pc-val"[^>]*title="[^"]*is not available[^"]*"[^>]*>)[^<]*(</div>)',
        r"\1N/A\2",
        html,
    )


def _tally_full(html: str, title_re: str) -> tuple[int | None, dict[str, tuple[str, str]]]:
    """(games, {model: (acc, rec)}) from one cards-page daily-tally block."""
    head = re.search(title_re, html or "", flags=re.I)
    if not head:
        return None, {}
    games_m = re.search(r"\((\d+)\s+games?\)", head.group(0))
    nxt = html.find('<div class="daily-tally"', head.end())
    chunk = html[head.end(): nxt if nxt > 0 else head.end() + 8000]
    out: dict[str, tuple[str, str]] = {}
    for name, acc, rec in re.findall(
        r'<div class="daily-model">[^<A-Za-z]*([A-Za-z][^<]*?)</div>\s*<div class="daily-acc"[^>]*>([^<]*)</div>\s*'
        r'<div class="daily-rec">([^<]*)</div>',
        chunk,
    ):
        out[name.strip()] = (acc.strip(), rec.strip())
    return (int(games_m.group(1)) if games_m else None), out


def copy_tallies_to_chart(chart_html: str, cards_html: str, sport_label: str) -> str:
    """Chart "Last Night" / "Last 7" model cards = the cards page's tallies."""
    if not chart_html or 'id="tallies"' not in chart_html or not cards_html:
        return chart_html
    for chart_title, cards_re in (
        ("Last Night", rf"<h2>\s*Last Night's {sport_label} Results[^<]*</h2>"),
        ("Last 7", rf"<h2>\s*Last 7 Days {sport_label} Results[^<]*</h2>"),
    ):
        games, faces = _tally_full(cards_html, cards_re)
        if not faces:
            continue
        m = re.search(
            rf'<h2>{chart_title} <span class="tag">\(\d+ games?\)</span></h2>([\s\S]*?)</section>',
            chart_html,
        )
        if not m:
            continue
        part = m.group(1)
        for name, (acc, rec) in faces.items():
            part = re.sub(
                rf'(<div class="daily-model mlabel">{re.escape(name)}</div>\s*<div class="daily-acc acc">)[^<]*'
                r'(</div>\s*<div class="daily-rec rec">)[^<]*(</div>)',
                lambda mm, a=acc, r=rec: mm.group(1) + a + mm.group(2) + r + mm.group(3),
                part,
                count=1,
            )
        head = f'<h2>{chart_title} <span class="tag">({games} games)</span></h2>' if games is not None else None
        new = (head or m.group(0)[: m.start(1) - m.start()]) + part + "</section>"
        chart_html = chart_html[: m.start()] + new + chart_html[m.end():]
    return chart_html


def payload_tallies_from_cards(payload: dict, cards_html: str, sport_label: str) -> dict:
    """Chart API Last Night / Last 7 model records = the cards page's printed tallies."""
    if not isinstance(payload, dict) or not cards_html:
        return payload
    ml = ((payload.get("markets") or {}).get("moneyline") or {})
    tallies = ml.get("tallies") if isinstance(ml.get("tallies"), dict) else None
    if tallies is None:
        return payload
    for key, title_re in (
        ("last_night", rf"<h2>\s*Last Night's {sport_label} Results[^<]*</h2>"),
        ("last_7", rf"<h2>\s*Last 7 Days {sport_label} Results[^<]*</h2>"),
    ):
        games, faces = _tally_full(cards_html, title_re)
        block = tallies.get(key)
        if not faces or not isinstance(block, dict):
            continue
        models = dict(block.get("models") or {})
        for name in SIX:
            acc, rec = faces.get(name, ("", ""))
            m = re.match(r"(\d+)-(\d+)(?:-(\d+))?$", rec or "")
            if not m:
                models.pop(name, None)  # the card prints no record for this model
                continue
            w, l, p = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            cur = dict(models.get(name) or {})
            cur.update({
                "w": w, "l": l, "pushes": p, "n": w + l, "record": rec,
                "pct": round(100.0 * w / (w + l), 1) if (w + l) else None,
            })
            models[name] = cur
        block = dict(block)
        block["models"] = models
        if games is not None:
            block["games"] = games
            block["events"] = games
            block["graded"] = games
        tallies[key] = block
    return payload


_SHARE_PREVIEW_CSS = (
    '<style id="pl-share-preview-size">'
    "body a.social-image-link{display:block!important;width:min(360px,100%)!important;"
    "max-width:360px!important;min-height:0!important;margin:12px auto!important}"
    "body a.social-image-link img{width:100%!important;max-width:360px!important;"
    "height:auto!important;max-height:none!important}"
    "body .social-export-wrap{max-width:360px!important;margin-left:auto!important;margin-right:auto!important;"
    "padding-left:0!important;padding-right:0!important}"
    "</style>"
)


def cap_share_preview(html: str) -> str:
    """Share image preview is a small preview (same 360px cap as MLB), not a full-size poster."""
    if not html or "social-image-link" not in html or 'id="pl-share-preview-size"' in html:
        return html
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", lambda m: _SHARE_PREVIEW_CSS + "</body>", html, count=1, flags=re.I)
    return html + _SHARE_PREVIEW_CSS


def single_all_dates(html: str) -> str:
    """One page, one `const allDates`; a second declaration is a SyntaxError."""
    if not html or len(re.findall(r"\bconst allDates\s*=", html)) < 2:
        return html
    seen = []

    def _keep_first(m: re.Match[str]) -> str:
        seen.append(1)
        return m.group(0) if len(seen) == 1 else "window.__plAllDatesExtra ="

    return re.sub(r"\bconst allDates\s*=", _keep_first, html)


def _div_extent(html: str, open_at: int) -> int:
    depth = 0
    for m in re.finditer(r"<div\b|</div>", html[open_at:]):
        if m.group(0) == "</div>":
            depth -= 1
            if depth == 0:
                return open_at + m.end()
        else:
            depth += 1
    return -1


def drop_empty_pl_vs_books(html: str) -> str:
    """No sportsbook odds in any window: say so instead of a table of 0-0 rows."""
    at = (html or "").find('id="pl-books-pl-records"')
    if at < 0:
        return html
    start = html.rfind("<div", 0, at)
    end = _div_extent(html, start)
    if start < 0 or end < 0:
        return html
    block = html[start:end]
    books = re.search(r'<td class="signal">Books favorite</td>((?:<td>[^<]*</td>){3})', block)
    if not books or re.sub(r"<[^>]+>", " ", books.group(1)).split() != ["0-0", "0-0", "0-0"]:
        return html
    note = (
        '<div class="pl-consensus-records pl-books-pl-records" id="pl-books-pl-records">'
        "<h2>PL vs Sportsbook</h2>"
        '<p class="sub">No sportsbook moneylines were posted for these matches, so the '
        "Prediction Lab favorite cannot be compared with a sportsbook favorite yet.</p></div>"
    )
    return html[:start] + note + html[end:]


_DATE_PICKER_JS = (
    '<script id="pl-date-picker-one-date">(function(){'
    "var s=document.getElementById('datePicker');if(!s)return;"
    "s.addEventListener('change',function(){var id=s.value;setTimeout(function(){"
    "document.querySelectorAll('.date-section').forEach(function(x){var on=x.id==='date-'+id;"
    "x.classList.toggle('visible',on);x.classList.toggle('seo-hidden',!on);"
    "x.style.display=on?'':'none';});"
    "document.querySelectorAll('.date-bubble').forEach(function(b){"
    "b.classList.toggle('active',b.getAttribute('data-date')===id);});},0);});"
    "})();</script>"
)


def date_picker_selects_one_date(html: str) -> str:
    """Choosing a date in the dropdown shows that date's cards and only that date."""
    if not html or 'id="datePicker"' not in html or 'id="pl-date-picker-one-date"' in html:
        return html
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", lambda m: _DATE_PICKER_JS + "</body>", html, count=1, flags=re.I)
    return html + _DATE_PICKER_JS


_ESPN_FULL_HEADSHOT = re.compile(
    r'src="https://a\.espncdn\.com/i/headshots/mma/players/full/(\d+)\.png"'
)


def small_headshots(html: str) -> str:
    """52px fighter slots load a 104px ESPN thumbnail instead of the full 200KB headshot."""
    if not html:
        return html
    return _ESPN_FULL_HEADSHOT.sub(
        lambda m: (
            'src="https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/'
            f'{m.group(1)}.png&amp;w=104&amp;h=104&amp;scale=crop"'
        ),
        html,
    )


# ── One header menu, one share strip, one footer on every page ──────────────

_TV_DEFAULT_TRUE = {"MLB", "SOCCER", "NCAAF", "CFL", "TENNIS", "UFC", "GOLF"}
_TV_SPORTS = (
    ("NBA", "nba"), ("MLB", "mlb"), ("NHL", "nhl"), ("NFL", "nfl"), ("SOCCER", "soccer"),
    ("NCAAB", "ncaab"), ("NCAAF", "ncaaf"), ("NCAAW", "ncaaw"), ("WNBA", "wnba"),
    ("CFL", "cfl"), ("TENNIS", "tennis"), ("UFC", "ufc"), ("GOLF", "golf"),
)
_TV_LABEL = {"SOCCER": "Soccer", "TENNIS": "Tennis", "GOLF": "Golf"}


def canonical_tv_menus(in_season: dict, soccer_enabled: bool = True) -> str:
    """The hamburger menu object exactly as templates/base.html renders it."""

    def _live(code: str) -> bool:
        if code in in_season:
            return bool(in_season.get(code))
        return code in _TV_DEFAULT_TRUE

    def _items(kind: str) -> str:
        rows = []
        order = _TV_SPORTS
        if kind == "results":
            # base.html lists NFL second under Results & Tracking.
            first = [x for x in _TV_SPORTS if x[0] in ("NBA",)]
            order = first + [x for x in _TV_SPORTS if x[0] == "NFL"] + [
                x for x in _TV_SPORTS if x[0] not in ("NBA", "NFL")
            ]
        for code, slug in order:
            if code == "SOCCER" and not soccer_enabled:
                continue
            label = _TV_LABEL.get(code, code)
            live = ",live:1" if _live(code) else ""
            rows.append(f"{{l:'{label}',h:'/{slug}-{kind}'{live}}}")
        return rows

    p = _items("picks")
    r = _items("results")
    return (
        "var TV_MENUS={\n"
        "  picks:{title:'Picks & Predictions',items:[\n    " + ",\n    ".join(p) + "\n  ]},\n"
        "  props:{title:'Props & Models',items:[{l:'Player Props',h:'/player-props'},{l:'Model Performance',h:'/performance'},{l:'Model vs Sportsbooks',h:'/our-model-vs-sportsbooks'},{l:'AI Picks Today',h:'/ai-sports-betting-picks-today'},{l:'Tutorial',h:'/tutorial'}]},\n"
        "  results:{title:'Results & Tracking',items:[\n    {l:'All Sports Results',h:'/all-sports-results'},\n    "
        + ",\n    ".join(r)
        + ",\n    {l:'Daily Results',h:'/daily-report'},{l:'Download CSV',h:'/results/downloads'},{l:'Model Performance',h:'/performance'},{l:'Edge Performance',h:'/edge-performance'}\n  ]},\n"
        "  community:{title:'Community',items:[{l:'X / Twitter',h:'https://x.com/predictionlab_io',ext:true},{l:'Instagram',h:'https://instagram.com/predictionlab.io',ext:true},{l:'TikTok',h:'https://www.tiktok.com/@predictionlab',ext:true},{l:'Reddit',h:'https://reddit.com/r/sportsbetting',ext:true},{l:'Telegram',h:'https://t.me/predictionlab',ext:true}]},\n"
        "  company:{title:'Company',items:[{l:'Join Premium',h:'/plans',cls:'highlight'},{l:'Plans & Pricing',h:'/plans'},{l:'Blog',h:'/blog'},{l:'Affiliate Program',h:'/affiliate'},{l:'FAQ',h:'/faq'},{l:'Tutorial',h:'/tutorial'},{l:'What Are AI Picks',h:'/what-are-ai-sports-betting-picks'},{l:'Contact',h:'/contact'},{l:'Privacy',h:'/privacy'},{l:'Terms',h:'/terms'},{l:'Refund Policy',h:'/refund-policy'},{l:'Responsible Gaming',h:'/responsible-gaming'}]}\n"
        "};"
    )


_TV_SUB_CANON = None


def _canonical_tv_sub() -> str:
    global _TV_SUB_CANON
    if _TV_SUB_CANON is None:
        from pathlib import Path

        try:
            text = (Path(__file__).resolve().parent / "templates" / "base.html").read_text(encoding="utf-8")
            m = re.search(r"^function tvSub\(key\)\{.*\}$", text, flags=re.M)
            _TV_SUB_CANON = m.group(0) if m else ""
        except Exception:
            _TV_SUB_CANON = ""
    return _TV_SUB_CANON


def _tv_menus_span(html: str) -> tuple[int, int]:
    """Start/end of the `var TV_MENUS={...};` statement (brace matched)."""
    at = html.find("var TV_MENUS={")
    if at < 0:
        return -1, -1
    i = at + len("var TV_MENUS=")
    depth = 0
    in_str = None
    while i < len(html):
        ch = html[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if end < len(html) and html[end] == ";":
                    end += 1
                return at, end
        i += 1
    return -1, -1


def normalize_site_chrome(html: str, path: str, in_season: dict, soccer_enabled: bool = True) -> str:
    """Same hamburger menu, 9-icon share strip and footer links on every page."""
    if not html or "<html" not in html[:3000].lower():
        return html
    # 1) Hamburger menu items
    s, e = _tv_menus_span(html)
    if s >= 0:
        canon = canonical_tv_menus(in_season, soccer_enabled)
        if html[s:e] != canon:
            html = html[:s] + canon + html[e:]
        sub = _canonical_tv_sub()
        if sub:
            html = re.sub(r"function tvSub\(key\)\{[^\n]*\}", lambda m: sub, html, count=1)
    # 2) Footer soccer link (bare path redirects)
    fs = html.find('<footer class="site-directory-footer"')
    if fs >= 0:
        fe = html.find("</footer>", fs)
        if fe > fs:
            # Footer sport links stay the plain /<sport>-picks paths (the
            # checker counts 13 of them); undo any ?region=all variant.
            seg = html[fs:fe].replace(
                '<a href="/soccer-picks?region=all">Soccer AI Picks</a>',
                '<a href="/soccer-picks">Soccer AI Picks</a>',
            )
            html = html[:fs] + seg + html[fe:]
        # 3) Share strip: present, once, with all 9 icons, right above the footer
        try:
            from served_layout_fixes import _share_shell
        except Exception:
            _share_shell = None
        if _share_shell is not None:
            strips = list(re.finditer(r'<div class="share-strip"', html))
            if not strips:
                fs = html.find('<footer class="site-directory-footer"')
                html = html[:fs] + _share_shell("", path or "/") + html[fs:]
            else:
                st = strips[-1].start()
                en = _div_extent(html, st)
                if en > st:
                    icons = len(re.findall(r'class="share-icon"', html[st:en]))
                    if icons < 9:
                        html = html[:st] + _share_shell("", path or "/") + html[en:]
    # 4) Share strip styles. Chart views shipped the strip without its CSS, so
    #    each icon drew at the full page width (owner 2026-10-08). Same rules as
    #    the cards pages.
    if '<div class="share-strip"' in html and ".share-icon img" not in html:
        css = (
            '<style id="pl-share-strip-css">'
            ".share-strip{max-width:1200px;margin:0 auto 10px;padding:10px 16px;display:flex;"
            "align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;"
            "background:rgba(244,247,249,0.7);border:1px solid rgba(15,23,42,0.1);border-radius:12px}"
            ".share-strip-label{font-size:0.82em;font-weight:800;color:#0f172a;letter-spacing:0.2px}"
            ".share-icons{display:flex;align-items:center;gap:8px;flex-wrap:wrap}"
            ".share-icon{width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;"
            "border-radius:999px;border:1px solid rgba(15,23,42,0.14);background:#fff}"
            ".share-icon img{width:16px;height:16px;display:block}"
            ".share-icon .txt{display:none}"
            ".share-icon:hover{border-color:#00529B;background:rgba(0,82,155,0.08)}"
            "</style>"
        )
        at = html.lower().find("</head>")
        if at > 0:
            html = html[:at] + css + html[at:]
    return html


def ensure_split_row(html: str, cards_html: str, sport_label: str) -> str:
    """Even 3/6 splits get one push row so the consensus rows cover every game."""
    if not html or "Consensus Based Betting Records" not in html or not cards_html:
        return html
    night = re.search(rf"Last Night's {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}})", cards_html)
    week = re.search(rf"Last 7 Days {sport_label} Results\s*[—–-]\s*(\d{{4}}-\d{{2}}-\d{{2}}) to (\d{{4}}-\d{{2}}-\d{{2}})", cards_html)
    if not night or not week:
        return html
    ln, lo7, hi7 = night.group(1), week.group(1), week.group(2)
    lo30 = (date.fromisoformat(ln) - timedelta(days=29)).isoformat()
    splits: list[str] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', cards_html)
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        for card in re.split(r'(?=<div class="game-card\b)', content)[1:]:
            picks: dict[str, str] = {}
            for name, side_cls, side in re.findall(
                r'class="pc-name">([^<]+)</div>(?:\s*<button[^>]*>[^<]*</button>)?\s*'
                r'<div class="pc-val"[^>]*>[^<]*</div>\s*<div class="pc-side([^"]*)"[^>]*>([^<]*)</div>',
                card[:40000],
            ):
                name = name.strip()
                team = re.sub(r"[✅❌]", "", side).strip()
                if not team:
                    cm = re.search(r"\b(home|away)\b", side_cls)
                    team = cm.group(1).upper() if cm else ""
                if name in SIX and name not in picks and team and team.upper() not in ("N/A", "NA", "—", "-"):
                    if "✅" in side or "❌" in side:
                        picks[name] = team
            if len(picks) == 6:
                counts: dict[str, int] = {}
                for t in picks.values():
                    counts[t] = counts.get(t, 0) + 1
                if sorted(counts.values()) == [3, 3]:
                    splits.append(dk)
    n_ln = sum(1 for d in splits if d == ln)
    n_7 = sum(1 for d in splits if lo7 <= d <= hi7)
    n_30 = sum(1 for d in splits if lo30 <= d <= ln)
    if not (n_ln or n_7 or n_30):
        return html
    cell = lambda n: f"0-0-{n}" if n else "0-0"
    row = (
        '<tr><td class="bucket">3/6 Split / no consensus</td>'
        f"<td>{cell(n_ln)}</td><td>{cell(n_7)}</td><td>{cell(n_30)}</td></tr>"
    )
    start = html.find("Consensus Based Betting Records")
    tb, te = html.find("<tbody>", start), html.find("</tbody>", start)
    nxt = html.find("PL vs Sportsbook", start)
    if tb < 0 or te < 0 or (0 <= nxt < te):
        return html
    body = html[tb:te]
    body = re.sub(r'<tr><td class="bucket">3/6[^<]*</td>[\s\S]*?</tr>', "", body)
    html = html[:tb] + body + row + html[te:]
    return html.replace(
        "Even splits are omitted.", "Even splits (3/6) are included and graded as pushes.", 1
    )


def open_market_panel(html: str, market: str) -> str:
    """On ?market=spread / totals charts the matching records panel is the open one."""
    if market not in ("spread", "totals") or 'data-market-panel="' + market + '"' not in (html or ""):
        return html
    html = html.replace('<div data-market-panel="moneyline">', '<div data-market-panel="moneyline" hidden>', 1)
    html = html.replace(f'<div data-market-panel="{market}" hidden>', f'<div data-market-panel="{market}">', 1)
    html = re.sub(
        r'<button type="button" class="market-tab active" data-market="moneyline">',
        '<button type="button" class="market-tab" data-market="moneyline">',
        html,
        count=1,
    )
    html = html.replace(
        f'<button type="button" class="market-tab" data-market="{market}">',
        f'<button type="button" class="market-tab active" data-market="{market}">',
        1,
    )
    return html


def show_model_pick_names(html: str, name_coin_flips: bool = False) -> str:
    """Results cards: each model box names the team it picked (e.g. "Dodgers ✅"),
    not just a check mark. The team comes from the box's home/away side.

    name_coin_flips also names a graded 50% box by the side it was graded on.
    Run it only after the consensus split rows are counted: those rows treat
    a 50% box as no pick."""
    if not html or "pc-side" not in html:
        return html

    def _card(m: re.Match[str]) -> str:
        card = m.group(0)
        names = [unescape(n).strip() for n in re.findall(r'class="team-name">([^<]+)</div>', card)[:2]]
        if len(names) < 2:
            return card
        away, home = names[0], names[1]

        def _box(b: re.Match[str]) -> str:
            # A 50% box made no real pick (the stored value is a coin flip);
            # it keeps its original mark and is not given a team name.
            if not name_coin_flips and re.fullmatch(r"50(?:\.0+)?%", b.group(2).strip()):
                return b.group(0)
            team = home if b.group(4) == "home" else away
            return f"{b.group(1)}{b.group(3)}{escape(team)} {b.group(5)}{b.group(6)}"

        return re.sub(
            r'(<div class="pc-val"[^>]*>([^<]*)</div>\s*)'
            r'(<div class="pc-side\s+(home|away)\b[^"]*"[^>]*>)\s*([✅❌])\s*(</div>)',
            _box,
            card,
        )

    return re.sub(r'<div class="game-card\b[\s\S]*?(?=<div class="game-card\b|<div id="date-|$)', _card, html)


_EDGE_CHIP_RE = re.compile(
    r'\s*<div class="line-chip edge-chip[^"]*">\s*<div class="line-chip-label">Edge\b[\s\S]*?</div>'
    r'\s*<div class="line-chip-val">[^<]*</div>\s*</div>'
)


def remove_edge_chip(html: str) -> str:
    """MLB cards: no Edge chip in the lines strip (owner request 2026-10-08)."""
    return _EDGE_CHIP_RE.sub("", html or "")


def set_plxs_total_rows_from_cards(html: str, cards_html: str,
                                   tables: tuple = ("pl-totals-records", "pl-totals-three-way")) -> str:
    """Prediction Lab and XSharp totals rows = the ✅/❌ totals marks on the FINAL cards.

    Owner fail list 2026-10-08: the totals chart and the picks Recent results
    disagreed because the XSharp row was printed from another source.
    """
    if not html or not cards_html:
        return html
    wins = card_windows(cards_html)
    if not wins:
        return html
    for table_id in tables:
        at = html.find(f'id="{table_id}"')
        if at < 0:
            continue
        end = html.find("</table>", at)
        if end < 0:
            continue
        seg = html[at:end]
        for name, key in (("Prediction Lab", "pl_total"), ("XSharp", "xs_total")):
            m = re.search(rf'<tr>\s*<td class="bucket">{re.escape(name)}</td>([\s\S]*?)</tr>', seg)
            if not m:
                continue
            cells = re.findall(r"<td(?:\s[^>]*)?>[\s\S]*?</td>", m.group(1))
            if len(cells) < 3:
                continue
            recs = [wins[w][key] for w in ("ln", "l7", "l30")]
            if not any(sum(r) for r in recs):
                continue
            new_cells = []
            for i in range(3):
                if "·" in cells[i]:
                    w, l, p = recs[i]
                    rec = f"{w}-{l}" + (f"-{p}" if p else "")
                    new_cells.append(f"<td>{_face_text(f'{w}-{l}') if (w + l) else rec}</td>")
                else:
                    new_cells.append(f"<td>{_cell_like(cells[i], recs[i])}</td>")
            row = f'<tr><td class="bucket">{name}</td>' + "".join(new_cells) + "</tr>"
            seg = seg[: m.start()] + row + seg[m.end():]
        html = html[:at] + seg + html[end:]
    return html


def ensure_totals_three_way(html: str, src_table: str = "pl-totals-records") -> str:
    """Add the 'Prediction Lab · XSharp — Totals' chart when the page has only the
    Prediction Lab & XSharp totals table. Rows are copied from that table as printed."""
    if not html or 'id="pl-totals-three-way"' in html:
        return html
    rows = _plxs_table(html, src_table)
    if not rows:
        return html
    body = []
    for name in ("Prediction Lab", "XSharp"):
        cells = rows.get(name)
        if not cells:
            continue
        body.append(
            f'<tr><td class="bucket">{name}</td>'
            + "".join(f"<td>{_face_text(c) if c else '—'}</td>" for c in cells)
            + "</tr>"
        )
    if not body:
        return html
    table = (
        '<div class="pl-consensus-records" id="pl-totals-three-way">'
        "<h2>Prediction Lab · XSharp — Totals</h2><table><thead><tr><th>Model</th>"
        "<th>Last night</th><th>Past 7 days</th><th>Past 30 days</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )
    at = html.find(f'id="{src_table}"')
    end = html.find("</table>", at)
    close = html.find("</div>", end) if end > 0 else -1
    if close < 0:
        return html
    close += len("</div>")
    return html[:close] + table + html[close:]


def fix_small_efficiency_percent(html: str) -> str:
    """Efficiency below 1% was read as a 0-1 fraction by the card template
    (0.6% home showed as 60%). Print the stored percent for the side it favors."""
    if not html or "data-m-efficiency" not in html:
        return html
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html)
    out = [parts[0]]
    for stack in parts[1:]:
        m = re.search(r'data-m-efficiency="([0-9.]+)"', stack[:3000])
        home = re.search(r'data-home="([^"]*)"', stack[:3000])
        away = re.search(r'data-away="([^"]*)"', stack[:3000])
        try:
            pct = float(m.group(1)) if m else None
        except ValueError:
            pct = None
        if pct is None or pct >= 1.0 or not home or not away:
            out.append(stack)
            continue
        fav_home = pct >= 50.0
        shown = pct if fav_home else 100.0 - pct
        cls = "home" if fav_home else "away"

        box = re.search(
            r'(<div class="pc-name">\s*Efficiency\s*</div>(?:\s*<button\b[^>]*>[\s\S]*?</button>)?\s*<div class="pc-val"[^>]*>)'
            r'[^<]*(</div>\s*<div class="pc-side) ?(home|away)?("[^>]*>)([^<]*)</div>',
            stack,
        )
        if not box:
            out.append(stack)
            continue
        side_now = box.group(3) or ""
        text_now = box.group(5)
        mark = " ✅" if "✅" in text_now else (" ❌" if "❌" in text_now else "")
        team_now = re.sub(r"[✅❌]", "", text_now).strip()
        if side_now == cls:
            team = team_now
        else:
            # The box named the other side; use that side's short name from the face.
            names = re.findall(r'class="team-name[^"]*">([^<]+)<', stack)
            team = (names[1] if cls == "home" else names[0]).strip() if len(names) >= 2 else ""
            if not team:
                out.append(stack)
                continue
            mark = ""  # grading mark belonged to the other side's pick
        repl = f'{box.group(1)}{shown:.1f}%{box.group(2)} {cls}{box.group(4)}{team}{mark}</div>'
        out.append(stack[:box.start()] + repl + stack[box.end():])
    return "".join(out)


# ── MLB results chart: one Results/Grading table per completed game (owner 2026-10-08) ──
_GRADE_MODELS = ("Grinder2", "Takedown", "Edge", "XSharp", "Efficiency", "Sharp Consensus")


def _gtext(s: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _short_team(full: str, away_short: str, home_short: str, away_full: str, home_full: str) -> str:
    f = (full or "").strip()
    for short, long_ in ((away_short, away_full), (home_short, home_full)):
        if short and (f.lower().startswith(long_.lower()) if long_ else False):
            return short + f[len(long_):]
    for short in (away_short, home_short):
        if short and short.lower() in f.lower():
            idx = f.lower().find(short.lower())
            return short + f[idx + len(short):]
    return f


def _mlb_grade_game(card: str, day: str) -> dict | None:
    slots = re.findall(r'<div class="team-slot[^"]*">([\s\S]*?)</div>\s*</div>\s*</div>', card[:12000])
    if len(slots) < 2:
        return None
    teams = []
    for s in slots[:2]:
        name = re.search(r'class="team-name">([^<]+)<', s)
        score = re.search(r'class="final-score[^"]*">\s*(\d+)\s*<', s)
        books = re.search(r'face-books-ml">[\s\S]*?class="ml-num[^"]*">\s*([^<]+?)\s*<', s)
        pl = re.search(r'face-pl-ml">[\s\S]*?class="ml-num[^"]*">\s*([^<]+?)\s*<', s)
        if not name or not score:
            return None
        teams.append({
            "name": _gtext(name.group(1)),
            "score": int(score.group(1)),
            "books": _gtext(books.group(1)) if books else "",
            "pl": _gtext(pl.group(1)) if pl else "",
        })
    away, home = teams
    if away["score"] == home["score"]:
        return None

    def _row(label: str) -> list[str]:
        m = re.search(
            rf'<td class="market-k">\s*{label}\s*</td>\s*<td class="val-books">([\s\S]*?)</td>\s*'
            r'<td class="val-pl">([\s\S]*?)</td>\s*<td class="val-xs">([\s\S]*?)</td>',
            card,
        )
        return [_gtext(x) for x in m.groups()] if m else ["", "", ""]

    rl = _row("Run Line")
    tot = _row("Total")
    picks = {}
    for name, side in re.findall(
        r'class="pc-name">([^<]+)</div>(?:\s*<button[^>]*>[^<]*</button>)?\s*'
        r'<div class="pc-val"[^>]*>[^<]*</div>\s*<div class="pc-side[^"]*"[^>]*>([^<]*)</div>',
        card,
    ):
        name = name.strip()
        team = re.sub(r"[✅❌]", "", side).strip()
        if name in _GRADE_MODELS and name not in picks:
            picks[name] = team
    gid = re.search(r'data-game-id="([^"]+)"', card[:600])
    return {
        "day": day, "gid": gid.group(1) if gid else "",
        "away": away, "home": home, "rl": rl, "tot": tot, "picks": picks,
    }


def _ml_odds_num(s: str):
    m = re.search(r"[+-]?\d+", s or "")
    return int(m.group(0)) if m else None


def _mlb_grading_table(g: dict) -> str:
    import html as _h
    a, h = g["away"], g["home"]
    winner = a if a["score"] > h["score"] else h
    margin = abs(a["score"] - h["score"])
    total = a["score"] + h["score"]
    esc = _h.escape

    def _model_cell(team: str) -> str:
        if not team or team.upper() in ("N/A", "NA", "—", "-"):
            return "—"
        ok = team.lower() == winner["name"].lower()
        mark = '<span class="g-ok">✓</span>' if ok else '<span class="g-no">✕</span>'
        return f"{mark}{esc(team)}"

    books_ml = "—"
    if a["books"] or h["books"]:
        books_ml = f'{esc(a["name"])} {esc(a["books"] or "—")} / {esc(h["name"])} {esc(h["books"] or "—")}'
    pl_pick = "—"
    an, hn = _ml_odds_num(a["pl"]), _ml_odds_num(h["pl"])
    if an is not None and hn is not None and an != hn:
        pl_pick = esc(a["name"] if an < hn else h["name"])

    def _line(v: str) -> str:
        v = _short_team(v, a["name"], h["name"], "", "")
        return esc(v) if v and v not in ("—", "-") else "—"

    ml_cells = "".join(f"<td>{_model_cell(g['picks'].get(m, ''))}</td>" for m in _GRADE_MODELS)
    dash_cells = "".join("<td>—</td>" for _ in _GRADE_MODELS)
    rows = (
        f'<tr><th scope="row">Moneyline</th><td>{esc(winner["name"])}</td><td>{books_ml}</td>'
        f"<td>{pl_pick}</td><td>—</td>{ml_cells}</tr>"
        f'<tr><th scope="row">Run Line</th><td>{esc(winner["name"])} by {margin}</td>'
        f"<td>{_line(g['rl'][0])}</td><td>{_line(g['rl'][1])}</td><td>{_line(g['rl'][2])}</td>{dash_cells}</tr>"
        f'<tr><th scope="row">Total</th><td>{total} runs</td>'
        f"<td>{_line(g['tot'][0])}</td><td>{_line(g['tot'][1])}</td><td>{_line(g['tot'][2])}</td>{dash_cells}</tr>"
    )
    head = (
        "<tr><th>Market</th><th>Final Result</th><th>Books</th><th>PL</th><th>XSharp</th>"
        + "".join(f"<th>{'XSharp Model' if m == 'XSharp' else m}</th>" for m in _GRADE_MODELS)
        + "</tr>"
    )
    title = f'{esc(a["name"])} {a["score"]} @ {esc(h["name"])} {h["score"]}'
    return (
        f'<div class="mlb-grade-game" data-game-id="{esc(g["gid"])}" data-date="{g["day"]}">'
        f'<div class="mlb-grade-title"><span>{g["day"]}</span> Game: {title}</div>'
        f'<div class="table-wrap"><table class="mlb-grade-table"><thead>{head}</thead>'
        f"<tbody>{rows}</tbody></table></div></div>"
    )


def mlb_grading_section(cards_html: str) -> str:
    """Per-game Results table from the MLB results cards. Grades the stored model
    picks against the final score. Run Line / Total model cells are dashes:
    the models publish no per-model run line or total pick."""
    if not cards_html or 'class="game-card' not in cards_html:
        return ""
    games = []
    seen = set()
    for card in re.split(r'(?=<div class="game-card pick-card)', cards_html)[1:]:
        if ">FINAL<" not in card[:1500] and "FINAL</span>" not in card[:1500]:
            continue
        dm = re.search(r'card-details-(\d{4}-\d{2}-\d{2})', card)
        if not dm:
            continue
        g = _mlb_grade_game(card, dm.group(1))
        if not g:
            continue
        key = g["gid"] or (g["day"], g["away"]["name"], g["home"]["name"])
        if key in seen:
            continue
        seen.add(key)
        games.append(g)
    if not games:
        return ""
    games.sort(key=lambda g: g["day"], reverse=True)
    style = (
        "<style>#mlb-grading .mlb-grade-game{margin:0 0 14px}"
        "#mlb-grading .mlb-grade-title{font-weight:700;font-size:.92rem;margin:0 0 4px}"
        "#mlb-grading .mlb-grade-title span{color:#64748b;font-weight:600;margin-right:6px}"
        "#mlb-grading table{width:100%;font-size:.78rem;border-collapse:collapse}"
        "#mlb-grading th,#mlb-grading td{padding:4px 6px;border:1px solid rgba(15,23,42,.12);white-space:nowrap;text-align:left}"
        "#mlb-grading .g-ok{color:#067647;font-weight:700;margin-right:2px}"
        "#mlb-grading .g-no{color:#D93025;font-weight:700;margin-right:2px}"
        "#mlb-grading .table-wrap{overflow-x:auto}"
        "#finals-wrap{display:none!important}</style>"
    )
    body = "".join(_mlb_grading_table(g) for g in games)
    return (
        f'<section id="mlb-grading" class="mlb-grading">{style}'
        f'<h2 class="sec-title">RESULTS <span class="tag">({len(games)} games)</span></h2>'
        f"{body}</section>"
    )


def place_mlb_grading_section(html: str, cards_html: str) -> str:
    """Put the grading tables where the Games records list sat (above the share strip)."""
    if not html or 'id="mlb-grading"' in html:
        return html
    section = mlb_grading_section(cards_html)
    if not section:
        return html
    at = html.find('<section id="finals-wrap">')
    if at < 0:
        return html
    return html[:at] + section + html[at:]


def remove_ssr_game_lists(html: str) -> str:
    """MLB chart view: the per-game Results tables are the only games chart.
    Drop the Moneyline / Spread / Totals games lists (owner 2026-10-08)."""
    if not html or 'id="mlb-grading"' not in html:
        return html
    return re.sub(
        r'<section\b[^>]*\bdata-ssr-market="[^"]*"[^>]*>[\s\S]*?</section>',
        "",
        html,
    )
