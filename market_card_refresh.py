"""Market-only repair of cached paid cards; no model or grading writes."""
from contextlib import closing
from html import escape, unescape
import logging
import math
from pathlib import Path
import re
import sqlite3

SPORTS = {"NHL", "NFL", "NCAAB", "NCAAW", "NBA"}
ML = re.compile(r'(<div class="ml-line face-books-ml">\s*<span[^>]*>.*?</span>\s*<span class="ml-num[^"]*">)(.*?)(</span>)(\s*<button\b.*?</button>)?', re.S)
ROWS = re.compile(r'<tr\b[^>]*>.*?</tr>', re.S)
CELL = re.compile(r'(<td class="val-books">)(.*?)(</td>)', re.S)
PL_CELL = re.compile(r'(<td class="val-pl">)(.*?)(</td>)', re.S)
LABEL = re.compile(r'<td class="market-k">(.*?)</td>', re.S)
PL_ATTR = re.compile(r'data-pl-spread="([^"]*)"')
PL_TOTAL_ATTR = re.compile(r'data-pl-total="([^"]*)"')
XS_ATTR = re.compile(r'data-xs-spread="([^"]*)"')
XS_TOTAL_ATTR = re.compile(r'data-xs-total="([^"]*)"')
XS_CELL = re.compile(r'(<td class="val-xs">)(.*?)(</td>)', re.S)


def text(value):
    value = re.sub(r'<button\b.*?</button>', '', value, flags=re.S)
    return " ".join(unescape(re.sub(r'<[^>]+>', '', value)).split())


def blank(value):
    return text(value) in ("", "N/A", "—", "–", "-", "--")


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def real_source(value):
    source = str(value or "").lower()
    return not any(word in source for word in ("synthetic", "model", "internal", "engine")) and any(
        word in source for word in ("espn", "pl_book_odds", "draftkings", "odds_api"))


def fill_pl_cells(html):
    """Copy data-pl-spread / totals into blank Prediction Lab cells (SPA has no page JS)."""
    pl_spread = text((PL_ATTR.search(html) or [None, ""])[1])
    pl_total = text((PL_TOTAL_ATTR.search(html) or [None, ""])[1])

    def market(match):
        line = match[0]
        label = LABEL.search(line)
        if not label:
            return line
        name = text(label[1]).lower()
        value = pl_total if name == "total" else pl_spread if name in ("spread", "puck line", "run line") else ""
        if not value:
            return line
        cell = PL_CELL.search(line)
        if cell and blank(cell[2]):
            line = PL_CELL.sub(lambda cell: cell[1] + escape(value) + cell[3], line, count=1)
        return line
    return ROWS.sub(market, html)


def fill_xs_cells(html):
    """Copy data-xs-spread / totals into blank XSharp cells (SPA has no page JS)."""
    xs_spread = text((XS_ATTR.search(html) or [None, ""])[1])
    xs_total = text((XS_TOTAL_ATTR.search(html) or [None, ""])[1])

    def market(match):
        line = match[0]
        label = LABEL.search(line)
        if not label:
            return line
        name = text(label[1]).lower()
        value = xs_total if name == "total" else xs_spread if name in ("spread", "puck line", "run line") else ""
        if not value:
            return line
        cell = XS_CELL.search(line)
        if cell and blank(cell[2]):
            line = XS_CELL.sub(lambda cell: cell[1] + escape(value) + cell[3], line, count=1)
        return line
    return ROWS.sub(market, html)


def _fmt_named_spread(home, away, spread):
    if spread is None:
        return ""
    try:
        spread = float(spread)
    except (TypeError, ValueError):
        return ""
    if abs(spread) < 1e-9:
        return "Pick'em"
    team = home if spread < 0 else away
    if not team:
        return f"{spread:g}"
    return f"{team} -{abs(spread):g}"


def fill_xs_from_efficiency(html, sport, home=None, away=None):
    """When XSharp attrs/cells are blank, fill from efficiency projection."""
    if sport not in {"NCAAB", "NCAAW", "NBA", "WNBA"}:
        return html
    xs_spread_attr = text((XS_ATTR.search(html) or [None, ""])[1])
    xs_total_attr = text((XS_TOTAL_ATTR.search(html) or [None, ""])[1])
    spread_cell = re.search(
        r'<td class="market-k">(?:Spread|Puck Line|Run Line)</td>[\s\S]*?<td class="val-xs">([^<]*)</td>',
        html,
        flags=re.I,
    )
    total_cell = re.search(
        r'<td class="market-k">Total</td>[\s\S]*?<td class="val-xs">([^<]*)</td>',
        html,
        flags=re.I,
    )
    need_spread = blank(xs_spread_attr) or (spread_cell is not None and blank(spread_cell.group(1)))
    need_total = blank(xs_total_attr) or (total_cell is not None and blank(total_cell.group(1)))
    if not need_spread and not need_total:
        return html
    home = home or text((re.search(r'data-home="([^"]*)"', html) or [None, ""])[1])
    away = away or text((re.search(r'data-away="([^"]*)"', html) or [None, ""])[1])
    if not home or not away:
        names = re.findall(r'class="team-name"[^>]*>([^<]+)<', html)
        if len(names) >= 2 and not away:
            away = text(names[0])
        if len(names) >= 2 and not home:
            home = text(names[1])
    if not home or not away:
        return html
    sp = tot = None
    try:
        from team_efficiency import compute_efficiency_projection
        proj = compute_efficiency_projection(home, away, sport=sport, n_games=5)
    except Exception:
        proj = None
    if proj:
        sp = proj.get("projected_spread")
        tot = proj.get("projected_total")
    if sp is None and need_spread:
        # Early-season teams with no ESPN box history: imply from XSharp win %.
        xp = None
        m_prob = re.search(
            r'(?:data-xs-prob|data-xgb-prob)="([^"]+)"|'
            r'XSharp</[^>]+>\s*<[^>]+>(\d{1,2}(?:\.\d+)?)\s*%',
            html,
            flags=re.I,
        )
        if m_prob:
            xp = number(m_prob.group(1) or m_prob.group(2))
        if xp is not None:
            if xp <= 1.0:
                xp *= 100.0
            # Card face % is the favorite side; convert to home-centric via who is favored.
            try:
                from sports.team_efficiency_attach import home_prob_pct_to_spread
                # Prefer stored home win % when both sides are present in Pick Confidence.
                both = re.findall(
                    r'<div class="pc-name">\s*XSharp\s*</div>\s*<div class="pc-pct[^"]*">\s*([^<]+)',
                    html,
                    flags=re.I,
                )
                home_pct = None
                if both:
                    # face boards list one XSharp % for the picked side — use attr if any
                    home_pct = xp
                # If HTML only has one %, treat as home win % when home is the ML favorite.
                home_ml = re.search(
                    r'data-pl-spread="([^"]*)"',
                    html,
                    flags=re.I,
                )
                # Prefer DB-style: xgb_prob on cards is often home-centric in attributes.
                m_home = re.search(r'data-xgb-home-prob="([^"]+)"', html, flags=re.I)
                if m_home:
                    home_pct = number(m_home.group(1))
                    if home_pct is not None and home_pct <= 1.0:
                        home_pct *= 100.0
                if home_pct is None:
                    home_pct = xp
                sp = home_prob_pct_to_spread(home_pct, sport)
            except Exception:
                sp = None
    if tot is None and need_total:
        pl_tot = text((PL_TOTAL_ATTR.search(html) or [None, ""])[1])
        tot = number(pl_tot)
        if tot is None:
            pl_cell = re.search(
                r'<td class="market-k">Total</td>[\s\S]*?<td class="val-pl">([^<]*)</td>',
                html,
                flags=re.I,
            )
            if pl_cell:
                tot = number(text(pl_cell.group(1)))
    if sp is None and tot is None:
        return html
    named = _fmt_named_spread(home, away, sp)
    if named and need_spread:
        if blank(xs_spread_attr):
            html = re.sub(
                r'(data-xs-spread=")([^"]*)(")',
                lambda m: m[1] + escape(named, quote=True) + m[3],
                html,
                count=1,
            )
    if tot is not None and need_total:
        tot_s = f"{float(tot):g}"
        if blank(xs_total_attr):
            if re.search(r'data-xs-total="', html):
                html = re.sub(
                    r'(data-xs-total=")([^"]*)(")',
                    lambda m: m[1] + escape(tot_s, quote=True) + m[3],
                    html,
                    count=1,
                )
            else:
                html = html.replace("data-pick-card", f'data-xs-total="{escape(tot_s, quote=True)}" data-pick-card', 1)
        # Always write the Total row cell when still blank.
        def _total_row(match):
            row = match.group(0)
            cell = XS_CELL.search(row)
            if cell and blank(cell.group(2)):
                row = XS_CELL.sub(lambda c: c.group(1) + escape(tot_s) + c.group(3), row, count=1)
            return row
        html = re.sub(
            r'<tr>\s*<td class="market-k">Total</td>[\s\S]*?</tr>',
            _total_row,
            html,
            flags=re.I,
        )
    return fill_xs_cells(html)


def repair_card(card, row, allow_final=False):
    html = card.get("html") or ""
    if not allow_final and re.search(r'class="game-time"[^>]*>[^<]*\bFINAL\b', html, re.I):
        return card
    original = html
    prices = [number(row.get("away_moneyline")), number(row.get("home_moneyline"))]
    if len(ML.findall(html)) == 2:
        position = iter(prices)
        def moneyline(match):
            price = next(position)
            if price is None or abs(price) < 100 or not blank(match[2]):
                return match[0]
            return match[1] + f"{price:+g}" + match[3]
        html = ML.sub(moneyline, html)
    spread, total = number(row.get("spread")), number(row.get("total"))
    displays = {}
    if spread is not None:
        team = card.get("home") if spread < 0 else card.get("away")
        displays["spread"] = f"{team} -{abs(spread):g}" if spread and team else "Pick'em" if spread == 0 else None
    if total is not None:
        displays["total"] = f"{total:g}"
    def market(match):
        line = match[0]
        label = LABEL.search(line)
        if not label:
            return line
        name = text(label[1]).lower()
        kind = "total" if name == "total" else "spread" if name in ("spread", "puck line", "run line") else None
        if not kind:
            return line
        cell = CELL.search(line)
        if cell and not blank(cell[2]):
            displays[kind] = text(cell[2])
        elif displays.get(kind):
            line = CELL.sub(lambda cell: cell[1] + escape(displays[kind]) + cell[3], line, count=1)
        return line
    html = ROWS.sub(market, html)
    for kind, value in displays.items():
        if value:
            html = re.sub(r'(data-books-' + kind + r'=")([^"]*)(")',
                          lambda match: match[1] + escape(value, quote=True) + match[3] if blank(match[2]) else match[0], html)
    html = fill_pl_cells(html)
    html = fill_xs_cells(html)
    return {**card, "html": html} if html != original else card


def refresh(payload, sport, database, allow_final=False, fetch_missing=True):
    if sport not in SPORTS or not payload.get("cards"):
        return payload
    ids = {}
    for card in payload["cards"]:
        match = re.search(r'data-game-id="([^"]+)"', card.get("html") or "")
        if match:
            ids[id(card)] = match[1]
    if not ids:
        return payload
    latest = {}
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            keys = list(set(ids.values()))
            for offset in range(0, len(keys), 400):
                part = keys[offset:offset+400]
                for row in conn.execute(
                    "SELECT game_id,spread,total,home_moneyline,away_moneyline,source FROM betting_lines "
                    f"WHERE sport=? AND game_id IN ({','.join('?' for _ in part)}) ORDER BY fetched_at DESC",
                    [sport]+part,
                ):
                    if real_source(row["source"]):
                        latest.setdefault(row["game_id"], dict(row))
    except sqlite3.Error:
        logging.getLogger(__name__).warning("Stored market refresh unavailable for %s", sport)
        return payload
    if fetch_missing and sport in {"NCAAB", "NCAAW", "NBA"}:
        needs = []
        for card in payload["cards"]:
            gid = ids.get(id(card))
            row = latest.get(gid) or {}
            if gid and not card.get("isFinal") and any(number(row.get(k)) is None for k in
                    ("spread", "total", "home_moneyline", "away_moneyline")):
                needs.append({"game_id": gid, "home_team_id": card.get("home"),
                              "away_team_id": card.get("away"), "game_date": card.get("date"),
                              "home_score": None})
        if needs:
            try:
                from NHL77FINAL import _start_background_book_refresh
                _start_background_book_refresh(sport, needs[:5])
            except Exception:
                logging.getLogger(__name__).warning("Background market refresh unavailable for %s", sport)
    xs_probs = {}
    if sport in {"NCAAB", "NCAAW", "NBA", "WNBA"}:
        try:
            with closing(sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                keys = list(set(ids.values()))
                for offset in range(0, len(keys), 400):
                    part = keys[offset:offset + 400]
                    for row in conn.execute(
                        "SELECT game_id, xgboost_home_prob, lock_xs_spread, lock_xs_total, "
                        "lock_pl_total FROM predictions "
                        f"WHERE game_id IN ({','.join('?' for _ in part)})",
                        part,
                    ):
                        xs_probs[row["game_id"]] = dict(row)
        except sqlite3.Error:
            logging.getLogger(__name__).warning("XSharp lock lookup unavailable for %s", sport)
    cards = []
    for card in payload["cards"]:
        if ids.get(id(card)) in latest:
            card = repair_card(card, latest[ids[id(card)]], allow_final=allow_final)
        else:
            html = fill_pl_cells(card.get("html") or "")
            html = fill_xs_cells(html)
            if html != (card.get("html") or ""):
                card = {**card, "html": html}
        gid = ids.get(id(card))
        row = xs_probs.get(gid) or {}
        html = card.get("html") or ""
        # Prefer stored XSharp locks when HTML blank.
        if row.get("lock_xs_spread") is not None and blank(text((XS_ATTR.search(html) or [None, ""])[1])):
            named = _fmt_named_spread(card.get("home"), card.get("away"), row["lock_xs_spread"])
            if named:
                html = re.sub(
                    r'(data-xs-spread=")([^"]*)(")',
                    lambda m: m[1] + escape(named, quote=True) + m[3],
                    html,
                    count=1,
                )
        if row.get("lock_xs_total") is not None and blank(text((XS_TOTAL_ATTR.search(html) or [None, ""])[1])):
            tot_s = f"{float(row['lock_xs_total']):g}"
            html = re.sub(
                r'(data-xs-total=")([^"]*)(")',
                lambda m: m[1] + escape(tot_s, quote=True) + m[3],
                html,
                count=1,
            )
        html = fill_xs_cells(html)
        if row.get("xgboost_home_prob") is not None and 'data-xgb-home-prob="' not in html:
            hp = float(row["xgboost_home_prob"])
            hp = hp * 100.0 if hp <= 1.0 else hp
            html = html.replace(
                "data-pick-card",
                f'data-xgb-home-prob="{hp:g}" data-pick-card',
                1,
            )
        html = fill_xs_from_efficiency(
            html,
            sport,
            home=card.get("home"),
            away=card.get("away"),
        )
        if html != (card.get("html") or ""):
            card = {**card, "html": html}
        reason = "No historical sportsbook line was recorded." if allow_final else "No posted sportsbook line is available yet."
        html = CELL.sub(lambda m: m[1]+'<span title="'+reason+'">N/A</span>'+m[3]
                        if blank(m[2]) else m[0], card.get("html") or "")
        cards.append({**card, "html": html} if sport in {"NCAAB", "NCAAW", "NBA"} else card)
    return {**payload, "cards": cards}
