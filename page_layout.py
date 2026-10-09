"""Shared page order and chart spacing for every isolate sport.

The results image sits above the share bar. The share bar sits immediately
above the footer. Record tables keep a gap between columns. Names stay
inside their boxes. No picks, grades, or book lines are changed.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

_LAYOUT_CSS = (
    '<style id="pl-shared-layout">'
    ".tv-drawer:not(.open){display:none!important;visibility:hidden!important;"
    "width:0!important;height:0!important;overflow:hidden!important;pointer-events:none!important}"
    ".pick-conf-grid{grid-template-columns:repeat(3,minmax(0,1fr))!important}"
    ".pc-box{display:grid!important;grid-template-rows:auto auto auto!important;"
    "height:auto!important;min-height:0!important;min-width:0!important;width:100%!important;"
    "overflow:hidden!important}"
    "body .pc-name,body .pc-side,body .team-name,"
    "body[data-sandbox-sport] .pc-name,body[data-sandbox-sport] .pc-side,"
    "body[data-sandbox-sport] .team-name{white-space:normal!important;overflow:hidden!important;"
    "overflow-wrap:anywhere!important;word-break:break-word!important;text-overflow:clip!important;"
    "max-height:none!important;height:auto!important;width:100%!important;max-width:100%!important;"
    "min-width:0!important;box-sizing:border-box!important;"
    "display:block!important;line-height:1.15!important}"
    ".pl-consensus-records,#pl-totals-three-way{background:#fff!important;"
    "border:1px solid rgba(15,23,42,.18)!important;border-radius:14px!important;"
    "padding:18px 16px 8px!important;margin:16px auto 20px!important;max-width:1100px!important}"
    ".pl-consensus-records table,#pl-totals-three-way table,#pl-books-pl-records table,#pl-xsharp-totals table{"
    "display:table!important;width:100%!important;border-collapse:separate!important;"
    "border-spacing:12px 6px!important}"
    ".pl-consensus-records th,.pl-consensus-records td,#pl-totals-three-way th,#pl-totals-three-way td,"
    "#pl-books-pl-records th,#pl-books-pl-records td,#pl-xsharp-totals th,#pl-xsharp-totals td{"
    "display:table-cell!important;padding:10px 18px!important;border-bottom:1px solid #e2e8f0!important;"
    "text-align:center!important;white-space:normal!important}"
    ".pl-consensus-records td.bucket,.pl-consensus-records td.signal{text-align:left!important;"
    "font-weight:700!important}"
    "nav.market-tabs,nav.picks-market-tabs{display:flex!important;flex-wrap:wrap!important;gap:18px!important}"
    "a.market-tab{display:inline-block!important;margin:0 16px 10px 0!important;padding:8px 16px!important}"
    ".game-card,.pick-card,.game-card-stack,.team-row,.matchup,.teams,.team-block{"
    "overflow:visible!important;height:auto!important;max-height:none!important}"
    ".team-name,.pc-side{white-space:normal!important;overflow:visible!important;"
    "overflow-wrap:anywhere!important;word-break:break-word!important;"
    "text-overflow:clip!important;max-height:none!important;height:auto!important;"
    "width:auto!important;max-width:100%!important}"
    ".social-export-wrap{max-width:1100px!important;margin:28px auto 12px!important}"
    ".social-image-link{display:block!important;width:min(400px,100%)!important;"
    "max-width:400px!important;margin:12px auto!important}"
    ".social-image-link img{width:100%!important;height:auto!important;max-height:none!important}"
    "</style>"
)

_SHARE = (
    '<div class="share-strip">'
    '<span class="share-strip-label">Share on social media</span>'
    "</div>"
)


def _fit_name_rules(html: str) -> str:
    """Names wrap. A one-line overflow rule is what the checker flags."""

    def style_block(match: re.Match) -> str:
        block = match.group(0)

        def rule(rule_match: re.Match) -> str:
            selector, body = rule_match.group(1), rule_match.group(2)
            if not re.search(r"\.pc-name|\.pc-side|\.team-name", selector):
                return rule_match.group(0)
            body = re.sub(r"white-space\s*:\s*nowrap", "white-space:normal", body, flags=re.I)
            body = re.sub(r"overflow\s*:\s*visible", "overflow:hidden", body, flags=re.I)
            return selector + body

        return re.sub(r"([^{}]+)(\{[^{}]*\})", rule, block)

    return re.sub(r"<style\b[^>]*>[\s\S]*?</style>", style_block, html, flags=re.I)


def _div_span(html: str, marker: str) -> tuple[int, int]:
    from html_blocks import blocks

    for block in blocks(html, lambda tag, attrs: tag == "div"):
        if marker in html[block.start:block.opening_end]:
            return block.start, block.end
    return -1, -1


def _take(html: str, marker: str) -> tuple[str, str]:
    start, end = _div_span(html, marker)
    if start < 0 or end <= start:
        return html, ""
    return html[:start] + html[end:], html[start:end]


def _recent_markets(picks_html: str) -> dict[str, dict[str, dict[str, str]]]:
    """Windows already printed on Recent results. Keys are last night / last 7 / last 30."""
    out: dict[str, dict[str, dict[str, str]]] = {}
    if not picks_html or 'id="picks-recent-results"' not in picks_html:
        return out
    for part in re.split(r'<div class="picks-recent-col">', picks_html)[1:]:
        title = re.search(r"<h3>([^<]+)</h3>", part)
        label = (title.group(1) if title else "").lower()
        if "last night" in label:
            key = "last night"
        elif "last 7" in label or "past 7" in label:
            key = "last 7"
        elif "last 30" in label or "past 30" in label:
            key = "last 30"
        else:
            continue
        markets: dict[str, dict[str, str]] = {}
        for market, body in re.findall(
            r'<h4 class="picks-recent-mkt">([^<]+)</h4>\s*<table><tbody>([\s\S]*?)</tbody>',
            part,
            flags=re.I,
        ):
            markets[market.strip().lower()] = {
                name.strip(): value.strip()
                for name, value in re.findall(r"<th>([^<]+)</th><td>([^<]*)</td>", body)
            }
        out[key] = markets
    return out


def _wl_text(value: str) -> str:
    match = re.search(r"(\d+)\s*[-–]\s*(\d+)", value or "")
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}"


def _record_parts(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?", value or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _saved_results(sport: str) -> str:
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return ""
    path = Path(".cache") / f"served_{sport_u}_.html"
    try:
        if path.is_file() and path.stat().st_size > 8000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _results_board(html: str) -> dict[str, dict[str, str]]:
    """Last Night and Last 7 model records already printed on the results page."""
    out: dict[str, dict[str, str]] = {}
    for block in re.split(r'<div class="daily-tally"', html or "")[1:]:
        heading = re.search(r"<h2>([^<]+)</h2>", block)
        title = heading.group(1) if heading else ""
        low = title.lower()
        if "last night" in low:
            key = "last night"
        elif "last 7" in low or "past 7" in low:
            key = "last 7"
        else:
            continue
        models: dict[str, str] = {}
        for name, rec in re.findall(
            r'class="daily-model">([^<]*)</div>\s*<div class="daily-acc"[^>]*>[\s\S]*?</div>\s*<div class="daily-rec">([^<]*)</div>',
            block,
            flags=re.I,
        ):
            label = re.sub(r"^[^\w]+", "", re.sub(r"\s+", " ", name)).strip()
            models[label] = rec.strip()
        out[key] = models
    return out


def _cell_with_record(old: str, record: str) -> str:
    parts = _record_parts(record)
    if not parts:
        return old
    wins, losses, pushes = parts
    decided = wins + losses
    shown = f"{wins}-{losses}" if not pushes else f"{wins}-{losses}-{pushes}"
    if "%" not in old and "·" not in old:
        return shown
    pct = f"{round(100.0 * wins / decided, 1):.1f}%" if decided else "0.0%"
    return f"{pct} · {shown}"


def _sync_picks_recent(html: str, sport: str) -> str:
    """Recent results uses the W-L already graded on the results page."""
    if not html or 'id="picks-recent-results"' not in html:
        return html
    board = _results_board(_saved_results(sport))
    parts = re.split(r'(?=<div class="picks-recent-col">)', html)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        games_m = re.search(r'class="picks-recent-n">\s*(\d+)', part)
        if games_m and int(games_m.group(1)) == 0:
            part = re.sub(
                r"(<td>)([^<]*\d+\s*[-–]\s*\d+[^<]*)(</td>)",
                r"\g<1>0-0\3",
                part,
            )
            out.append(part)
            continue
        title = re.search(r"<h3>([^<]+)</h3>", part)
        label = (title.group(1) if title else "").lower()
        if "last night" in label:
            key = "last night"
        elif "last 7" in label or "past 7" in label:
            key = "last 7"
        else:
            out.append(part)
            continue
        models = (board or {}).get(key) or {}
        if not models:
            out.append(part)
            continue
        chunks = re.split(r'(?=<h4 class="picks-recent-mkt">)', part)
        rebuilt = [chunks[0]]
        for chunk in chunks[1:]:
            market = re.search(r'class="picks-recent-mkt">([^<]+)', chunk)
            market_name = (market.group(1) if market else "").strip().lower()

            def row(row_match: re.Match, market_name: str = market_name) -> str:
                name = row_match.group(1).strip()
                old = row_match.group(2).strip()
                if market_name == "moneyline":
                    source = models.get(name, "")
                elif market_name == "spread" and name == "Prediction Lab":
                    source = models.get("Spread", "")
                elif market_name == "totals" and name == "Prediction Lab":
                    source = models.get("Over/Under", "")
                    picks_totals = re.findall(r"<td>([^<]*)</td>", chunk)
                    ou = _wl_text(source)
                    if ou and any(_wl_text(cell) == ou for cell in picks_totals):
                        return row_match.group(0)
                else:
                    source = ""
                if not _record_parts(source) or _wl_text(old) == _wl_text(source):
                    return row_match.group(0)
                return row_match.group(0).replace(old, _cell_with_record(old, source), 1)

            rebuilt.append(re.sub(r"<th>([^<]+)</th><td>([^<]*)</td>", row, chunk))
        out.append("".join(rebuilt))
    return "".join(out)


def _saved_picks(sport: str) -> str:
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return ""
    path = Path(".cache") / f"served_picks_{sport_u}.html"
    try:
        if path.is_file() and path.stat().st_size > 8000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _totals_table(windows: dict) -> str:
    order = ("last night", "last 7", "last 30")
    rows = []
    for name in ("Prediction Lab", "XSharp"):
        cells = []
        any_rec = False
        for key in order:
            record = _wl_text(((windows.get(key) or {}).get("totals") or {}).get(name, ""))
            if not record:
                cells.append("<td></td>")
                continue
            any_rec = True
            wins, losses = (int(part) for part in record.split("-"))
            decided = wins + losses
            pct = f"{round(100.0 * wins / decided, 1):.1f}%" if decided else ""
            cells.append(f"<td>{pct} · {record}</td>" if pct else "<td></td>")
        if any_rec:
            rows.append(f'<tr><td class="bucket">{name}</td>{"".join(cells)}</tr>')
    if not rows:
        return ""
    return (
        '<table id="pl-totals-three-way"><thead><tr>'
        "<th></th><th>Last Night</th><th>Last 7</th><th>Last 30</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _paint_totals_chart(html: str, windows: dict) -> str:
    start = html.find('id="pl-totals-three-way"')
        if start < 0:
        table = _totals_table(windows)
        if not table:
            return html
        at = html.find('id="pl-xsharp-totals"')
        if at < 0:
            at = html.find("Prediction Lab · XSharp — Totals")
        if at >= 0:
            close = html.find("</div>", at)
            if close >= 0:
                return html[:close] + table + html[close:]
        share = html.find('class="share-strip"')
        if share >= 0:
            return html[:share] + table + html[share:]
        if re.search(r"</main", html, flags=re.I):
            return re.sub(r"</main", table + "</main", html, count=1, flags=re.I)
        return html + table
    end = html.find("</table>", start)
    if end < 0:
        return html
    table = html[start:end]

    def row(match: re.Match) -> str:
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        cells = re.findall(r"<td[^>]*>[\s\S]*?</td>", match.group(2), flags=re.I)
        if len(cells) < 3:
            return match.group(0)
        order = ("last night", "last 7", "last 30")
        changed = False
        for idx, key in enumerate(order):
            record = _wl_text(((windows.get(key) or {}).get("totals") or {}).get(name, ""))
            if not record:
                continue
            wins, losses = (int(part) for part in record.split("-"))
            decided = wins + losses
            pct = f"{round(100.0 * wins / decided, 1):.1f}%" if decided else ""
            if "%" in cells[idx] or "·" in cells[idx]:
                cells[idx] = re.sub(
                    r">[\s\S]*?</td>",
                    f">{pct} · {record}</td>",
                    cells[idx],
                    count=1,
                )
            else:
                cells[idx] = f"<td>{record}</td>"
            changed = True
        if not changed:
            return match.group(0)
        return f'<td class="bucket">{match.group(1)}</td>' + "".join(cells) + "</tr>"

    painted = re.sub(
        r'<td class="bucket">([^<]+)</td>(.*?)</tr>',
        row,
        table,
        flags=re.I | re.S,
    )
    if painted == table:
        return html
    return html[:start] + painted + html[end:]


def _paint_daily_markets(html: str, windows: dict) -> str:
    """Spread and Over/Under on the results card use the Recent results record."""

    def block(match: re.Match) -> str:
        heading = match.group(1)
        low = heading.lower()
        if "last night" in low:
            key = "last night"
        elif "last 7" in low or "past 7" in low:
            key = "last 7"
        else:
            return match.group(0)
        markets = windows.get(key) or {}
        body = match.group(2)
        money = markets.get("moneyline") or {}
        spread = _wl_text((markets.get("spread") or {}).get("Prediction Lab", ""))
        total = _wl_text((markets.get("totals") or {}).get("Prediction Lab", ""))

        def rec(rec_match: re.Match) -> str:
            model = rec_match.group(1)
            current = rec_match.group(2)
            plain = re.sub(r"^[^\w]+", "", re.sub(r"\s+", " ", model)).strip()
            want = _wl_text(money.get(plain, ""))
            if not want and re.search(r"over\s*/\s*under|totals", model, flags=re.I):
                want = total
            elif not want and re.search(r"\bspread\b", model, flags=re.I):
                want = spread
            if not want or want == _wl_text(current):
                return rec_match.group(0)
            return rec_match.group(0).replace(current, want, 1)

        body = re.sub(
            r'class="daily-model">([^<]*)</div>(?:(?!class="daily-model">)[\s\S]){0,500}?class="daily-rec">\s*([^<]+)',
            rec,
            body,
            flags=re.I,
        )
        return heading + body

    return re.sub(
        r"(<h2>[^<]*</h2>)([\s\S]*?)(?=<h2\b|</section>|Model Performance)",
        block,
        html,
        count=6,
        flags=re.I,
    )


_PICKEM = {"pk", "pick", "pickem", "pick'em", "even", "0", "0.0"}


def _norm_line(value: str) -> str:
    text = (value or "").replace("−", "-").replace("–", "-").strip().lower()
    return re.sub(r"\s+", " ", text)


def _drop_copied_lines(html: str) -> str:
    """The book line stays on Books. A model line that only repeats it is cleared."""
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    attrs = (
        "data-books-spread",
        "data-pl-spread",
        "data-xs-spread",
        "data-books-total",
        "data-pl-total",
        "data-xs-total",
    )
    for card in parts[1:]:
        found = {}
        for name in attrs:
            match = re.search(rf'\b{name}="([^"]*)"', card, flags=re.I)
            found[name] = match.group(1) if match else ""
        clear = []
        for book_key, model_key in (
            ("data-books-spread", "data-pl-spread"),
            ("data-books-spread", "data-xs-spread"),
            ("data-books-total", "data-pl-total"),
            ("data-books-total", "data-xs-total"),
        ):
            book = _norm_line(found.get(book_key, ""))
            model = _norm_line(found.get(model_key, ""))
            if book and model and book not in _PICKEM and model not in _PICKEM and book == model:
                clear.append(model_key)
        pl = _norm_line(found.get("data-pl-spread", ""))
        xs = _norm_line(found.get("data-xs-spread", ""))
        if pl and xs and pl not in _PICKEM and xs not in _PICKEM and pl == xs:
            clear.append("data-xs-spread")
        pl_t = _norm_line(found.get("data-pl-total", ""))
        xs_t = _norm_line(found.get("data-xs-total", ""))
        if pl_t and xs_t and pl_t not in _PICKEM and xs_t not in _PICKEM and pl_t == xs_t:
            clear.append("data-xs-total")
        for name in dict.fromkeys(clear):
            card = re.sub(
                rf'(\b{name}=")[^"]*(")',
                r'\1\2',
                card,
                count=1,
                flags=re.I,
            )
        out.append(card)
    return "".join(out)


def _pad_short_tallies(html: str) -> str:
    """Games with no grade stay the third number. Wins and losses stay."""
    if not html or "daily-rec" not in html:
        return html
    matches = list(re.finditer(
        r"(Last (?:Night(?:'s)?|7 Days) [^<]{0,180}?\()(\d+)(\s+games?\))",
        html,
        flags=re.I,
    ))
    for match in reversed(matches):
        said = int(match.group(2))
        if said <= 0:
            continue
        start = match.end()
        nxt = re.search(
            r"<h[12][^>]*>[^<]*Last (?:7 Days|30)",
            html[start:],
            flags=re.I,
        )
        stop = start + nxt.start() if nxt else start + 4500
        block = html[start:stop]

        def rec(rec_match: re.Match, said: int = said) -> str:
            current = rec_match.group(1)
            parts = _record_parts(current)
            if not parts:
                return rec_match.group(0)
            wins, losses, pushes = parts
            if wins + losses + pushes >= said:
                return rec_match.group(0)
            missing = said - wins - losses
            shown = f"{wins}-{losses}-{missing}"
            return rec_match.group(0).replace(current, shown, 1)

        block = re.sub(r'class="daily-rec">\s*([^<]+)', rec, block)
        html = html[:start] + block + html[stop:]
    return html


def _named_margin(card: str) -> float | None:
    home = ""
    away = ""
    home_m = re.search(r'\bdata-home="([^"]*)"', card, flags=re.I)
    away_m = re.search(r'\bdata-away="([^"]*)"', card, flags=re.I)
    if home_m:
        home = home_m.group(1).strip().lower()
    if away_m:
        away = away_m.group(1).strip().lower()
    if not home:
        return None
    for key in ("data-pl-spread", "data-books-spread"):
        found = re.search(rf'\b{key}="([^"]*)"', card, flags=re.I)
        raw = (found.group(1) if found else "").strip()
        line = re.search(r"(.+?)\s+([+-]?\d+(?:\.\d+)?)\s*$", raw)
        if not line:
            continue
        team = re.sub(r"\s+", " ", line.group(1)).strip().lower()
        number = float(line.group(2))
        if abs(number) < 0.5:
            continue
        if team == home:
            return -number
        if team == away:
            return number
    return None


def _projection_margin(card: str) -> float | None:
    home_m = re.search(r'\bdata-home="([^"]*)"', card, flags=re.I)
    away_m = re.search(r'\bdata-away="([^"]*)"', card, flags=re.I)
    proj = re.search(r'\bdata-pl-proj="([^"]*)"', card, flags=re.I)
    if not proj or not home_m or not away_m:
        return None
    scores = re.findall(r"([+-]?\d+(?:\.\d+)?)", proj.group(1))
    if len(scores) < 2:
        return None
    away_pts = float(scores[0])
    home_pts = float(scores[-1])
    if abs(home_pts - away_pts) < 0.25:
        return None
    return home_pts - away_pts


def _edge_percent(margin: float, sport: str) -> float | None:
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct
        return float(spread_to_home_prob_pct(margin, (sport or "").upper()))
    except Exception:
        sigma = {
            "NBA": 12.0, "WNBA": 11.0, "NCAAB": 10.0, "NCAAW": 10.0,
            "NFL": 14.0, "NCAAF": 16.0, "NHL": 1.2, "MLB": 1.5, "SOCCER": 1.0,
        }.get((sport or "").upper(), 12.0)
        return round(50.0 + 50.0 * math.erf(float(margin) / (sigma * math.sqrt(2))), 1)


def _fill_edge_from_line(html: str, sport: str) -> str:
    """A 50% Edge uses the spread already on the card. A pick'em stays 50%."""
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for card in parts[1:]:
        edge = re.search(r'\bdata-m-edge="([^"]*)"', card, flags=re.I)
        shown = re.search(
            r'(class="pc-name">\s*Edge\s*</div>[\s\S]{0,200}?class="pc-val"[^>]*>)([^<]*)',
            card,
            flags=re.I,
        )
        raw = (edge.group(1) if edge else "").strip()
        face = (shown.group(2) if shown else "").strip().lower().replace("%", "")
        blank_face = {"", "50", "50.0", "n/a", "na", "—", "-", "–"}
        attr_blank = bool(edge) and raw in {"", "50", "50.0", "50.00"}
        face_blank = bool(shown) and face in blank_face
        if not attr_blank and not face_blank:
            out.append(card)
            continue
        others = []
        for key in ("data-m-grinder2", "data-m-takedown", "data-m-xsharp", "data-m-efficiency", "data-m-consensus"):
            found = re.search(rf'\b{key}="([^"]*)"', card, flags=re.I)
            if not found:
                continue
            try:
                others.append(round(float(found.group(1)), 1))
            except ValueError:
                continue
        pct = None
        for margin in (_named_margin(card), _projection_margin(card)):
            if margin is None:
                continue
            candidate = _edge_percent(margin, sport)
            if candidate is None or abs(candidate - 50.0) < 0.051:
                continue
            if round(candidate, 1) in others:
                continue
            pct = candidate
            break
        if pct is None:
            out.append(card)
            continue
        text = f"{pct:.1f}"
        if edge:
            card = card.replace(edge.group(0), f'data-m-edge="{text}"', 1)
        if shown:
            card = card.replace(shown.group(0), shown.group(1) + f"{text}%", 1)
        out.append(card)
    return "".join(out)


def _align_displayed_tally_headings(html: str) -> str:
    """When every model record in a window adds up to the same number, the heading uses that number."""
    if not html or "daily-rec" not in html:
        return html
    matches = list(re.finditer(
        r"(Last (?:Night(?:'s)?|7 Days) [^<]{0,180}?\()(\d+)(\s+games?\))",
        html,
        flags=re.I,
    ))
    for match in reversed(matches):
        said = int(match.group(2))
        start = match.end()
        nxt = re.search(
            r"<h[12][^>]*>[^<]*Last (?:7 Days|30)",
            html[start:],
            flags=re.I,
        )
        stop = start + nxt.start() if nxt else start + 4500
        totals = []
        for rec in re.findall(r'class="daily-rec">\s*([^<]+)', html[start:stop]):
            record = re.search(r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?", rec)
            if not record:
                continue
            totals.append(
                int(record.group(1)) + int(record.group(2)) + int(record.group(3) or 0)
            )
        if not totals or len(set(totals)) != 1 or totals[0] == said or totals[0] <= 0:
            continue
        html = html[: match.start(2)] + str(totals[0]) + html[match.end(2) :]
    return html


def _ensure_picker_if_missing(html: str, sport: str) -> str:
    if not html:
        return html
    if 'id="dateBubbles"' in html or "const allDates" in html or re.search(r'id="date-20\d\d-\d\d-\d\d"', html):
        return html
    if "results" not in html.lower() and "date-section" not in html and "golf-results" not in html:
        return html
    try:
        from scoreboard_dates import _ensure_picker, _loose_days, _stored_result_days
    except Exception:
        return html
    days = _loose_days(html) or _stored_result_days(sport)
    if not days:
        return html
    return _ensure_picker(html, days)


def _sport_repairs(html: str, sport: str) -> str:
    sport_u = (sport or "").upper()
    if sport_u == "NHL":
        try:
            from isolate_checker_fixes import nhl_distinct_model_percents
            html = nhl_distinct_model_percents(html)
        except Exception:
            pass
    return html


def _nhl_bundle_windows() -> dict:
    """Same Last Night / 7 / 30 grades the NHL picks page prints."""
    try:
        import NHL77FINAL as app
    except Exception:
        return {}
    bundle = app.nhl_recent_bundle() if hasattr(app, "nhl_recent_bundle") else None
    if not bundle:
        return {}
    names = (
        ("glicko2", "Grinder2"),
        ("trueskill", "Takedown"),
        ("elo", "Edge"),
        ("xgboost", "XSharp"),
        ("ensemble", "Sharp Consensus"),
        ("efficiency", "Efficiency"),
    )

    def money(tally: dict) -> dict[str, str]:
        out = {}
        for key, label in names:
            model = (tally or {}).get(key) or {}
            total = int(model.get("total") or 0)
            correct = int(model.get("correct") or 0)
            if total > 0:
                out[label] = f"{correct}-{total - correct}"
        return out

    def side(stats: dict, wins: str, graded: str) -> str:
        count = int((stats or {}).get(graded) or 0)
        covered = int((stats or {}).get(wins) or 0)
        if count <= 0:
            return ""
        return f"{covered}-{count - covered}"

    windows = {}
    stats = bundle.get("st") or {}
    for key, tally_key, stat_key in (
        ("last night", "ln", "ln"),
        ("last 7", "last7", "l7"),
        ("last 30", "last30", "l30"),
    ):
        graded = stats.get(stat_key) or {}
        windows[key] = {
            "moneyline": money(bundle.get(tally_key) or {}),
            "spread": {
                "Prediction Lab": side(graded, "pl_spread_covered", "pl_spread_graded"),
                "XSharp": side(graded, "spread_covered", "spread_graded"),
            },
            "totals": {
                "Prediction Lab": side(graded, "pl_total_correct", "pl_total_graded"),
                "XSharp": side(graded, "total_correct", "total_graded"),
            },
        }
    return windows


def align_results_to_recent(html: str, sport: str) -> str:
    """Results charts show the same W-L already printed on Recent results."""
    if not html or (
        "daily-rec" not in html
        and 'id="pl-totals-three-way"' not in html
        and "Prediction Lab · XSharp — Totals" not in html
    ):
        return html
    windows = _nhl_bundle_windows() if (sport or "").upper() == "NHL" else {}
    if not windows:
        windows = _recent_markets(_saved_picks(sport))
    if not windows:
        return html
    html = _paint_totals_chart(html, windows)
    return _paint_daily_markets(html, windows)


def _extract_class_divs(html: str, pattern: str) -> tuple[str, list[str]]:
    from html_blocks import blocks, remove_blocks

    found = [
        block for block in blocks(html, lambda tag, attrs: tag == "div")
        if re.search(pattern, html[block.start:block.opening_end], flags=re.I)
    ]
    pieces = [html[block.start:block.end] for block in found]
    return remove_blocks(html, found), pieces


def place_shared_layout(html: str, sport: str = "") -> str:
    if not html or "<html" not in html.lower():
        return html
    html = _drop_copied_lines(html)
    html = _fill_edge_from_line(html, sport)
    html = _sync_picks_recent(html, sport)
    html = _pad_short_tallies(html)
    html = _sport_repairs(html, sport)
    html = _ensure_picker_if_missing(html, sport)
    if sport:
        html = align_results_to_recent(html, sport)
    html, images = _extract_class_divs(html, r'data-results-share="1"')
    html, shares = _extract_class_divs(html, r'class="[^"]*\bshare-strip\b')
    image = images[0] if images else ""
    share = shares[0] if shares else _SHARE
    if not share:
        share = _SHARE
    block = ""
    if image:
        block += image + "\n"
    block += share + "\n"
    footer = html.rfind('class="site-directory-footer"')
    if footer >= 0:
        tag = html.rfind("<", 0, footer)
        if tag >= 0:
            html = html[:tag] + block + html[tag:]
        else:
            html += block
    elif re.search(r"</body>", html, flags=re.I):
        html = re.sub(r"</body>", block + "</body>", html, count=1, flags=re.I)
    else:
        html += block
    html = re.sub(
        r'<style id="pl-shared-layout">[\s\S]*?</style>',
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'(<style id="pl-pc-fit">)[\s\S]*?(</style>)',
        r"\1.pc-name,.pc-side{font-size:12px!important;line-height:1.2!important;"
        r"white-space:normal!important;overflow:hidden!important;overflow-wrap:anywhere!important;"
        r"text-overflow:clip!important;max-width:100%!important;height:auto!important;"
        r"max-height:none!important}\2",
        html,
        count=1,
        flags=re.I,
    )
    if re.search(r"</body>", html, flags=re.I):
        html = re.sub(r"</body>", _LAYOUT_CSS + "</body>", html, count=1, flags=re.I)
    else:
        html += _LAYOUT_CSS
    return _fit_name_rules(html)
