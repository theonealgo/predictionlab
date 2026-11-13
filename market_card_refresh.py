"""Market-only repair of cached paid cards; no model or grading writes."""
from contextlib import closing
from html import escape, unescape
import logging
import math
from pathlib import Path
import re
import sqlite3

SPORTS = {"NHL", "NFL", "NCAAB"}
ML = re.compile(r'(<div class="ml-line face-books-ml">\s*<span[^>]*>.*?</span>\s*<span class="ml-num[^"]*">)(.*?)(</span>)(\s*<button\b.*?</button>)?', re.S)
ROWS = re.compile(r'<tr\b[^>]*>.*?</tr>', re.S)
CELL = re.compile(r'(<td class="val-books">)(.*?)(</td>)', re.S)
LABEL = re.compile(r'<td class="market-k">(.*?)</td>', re.S)


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


def repair_card(card, row):
    html = card.get("html") or ""
    if re.search(r'class="game-time"[^>]*>[^<]*\bFINAL\b', html, re.I):
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
    return {**card, "html": html} if html != original else card


def refresh(payload, sport, database):
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
    return {**payload, "cards": [repair_card(card, latest[ids[id(card)]])
                                if ids.get(id(card)) in latest else card for card in payload["cards"]]}
