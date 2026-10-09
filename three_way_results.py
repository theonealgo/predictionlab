"""Books / Prediction Lab / XSharp agreement tables for results charts.

Grades only the sides and lines printed on the result cards. A market with
no printed side stays 0-0.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

_LABELS = ("PL = Books", "PL = XSharp", "Books = XSharp", "All 3 agree")
_MARKETS = ("moneyline", "spread", "totals")
_TITLES = {
    "moneyline": "Books · Prediction Lab · XSharp — Moneyline",
    "spread": "Books · Prediction Lab · XSharp — Spread",
    "totals": "Books · Prediction Lab · XSharp — Totals",
}


def _yesterday() -> date:
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.now()
    return (now - timedelta(days=1)).date()


def _same_team(side: str, team: str) -> bool:
    a = re.sub(r"[^a-z0-9]", "", (side or "").lower())
    b = re.sub(r"[^a-z0-9]", "", (team or "").lower())
    return bool(a and b and (a in b or b in a))


def _latest_final(db_path: Path, sport: str, yesterday: date) -> tuple[str, int] | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            """
            SELECT date(game_date), COUNT(*) FROM games
            WHERE upper(sport) = ? AND home_score IS NOT NULL
              AND date(game_date) <= ?
            GROUP BY date(game_date)
            ORDER BY date(game_date) DESC
            LIMIT 1
            """,
            ((sport or "").upper(), yesterday.isoformat()),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    return str(row[0])[:10], int(row[1] or 0)


def _games_from_cards(cards_html: str) -> list[dict]:
    html = cards_html or ""
    games: list[dict] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html)
    if len(chunks) > 1:
        it = iter(chunks[1:])
        for dk in it:
            content = next(it, "")
            games.extend(_cards_in(content, dk))
        return games
    # Tennis and similar cards carry the date on the header, not a date section.
    parts = re.split(r'(<div\b[^>]*\bdata-pick-card\b[^>]*>)', html, flags=re.I)
    idx = 1
    while idx < len(parts):
        open_tag = parts[idx]
        body = parts[idx + 1] if idx + 1 < len(parts) else ""
        idx += 2
        dated = re.search(r"FINAL\s*·\s*(\d{4}-\d{2}-\d{2})", open_tag + body)
        if not dated:
            continue
        games.extend(_cards_in(open_tag + body, dated.group(1)))
    return games


def _cards_in(content: str, dk: str) -> list[dict]:
    found: list[dict] = []
    parts = re.split(r'(<div class="game-card\b[^"]*"[^>]*>)', content, flags=re.I)
    blocks = []
    if len(parts) > 1:
        idx = 1
        while idx < len(parts):
            blocks.append(parts[idx] + (parts[idx + 1] if idx + 1 < len(parts) else ""))
            idx += 2
    else:
        blocks = [content]
    for body in blocks:
        teams = [
            re.sub(r"\s+", " ", name).strip()
            for name in re.findall(r'class="team-name[^"]*"[^>]*>\s*([^<]+)', body)
        ]
        scores = re.findall(r'class="final-score[^"]*">\s*(\d+)', body)
        if len(teams) < 2 or len(scores) < 2:
            continue
        away, home = teams[0], teams[1]
        try:
            aa, hs = int(scores[0]), int(scores[1])
        except ValueError:
            continue
        prices: dict[str, dict[str, str]] = {}
        for bit in re.split(r'class="team-name[^"]*"[^>]*>', body)[1:3]:
            name_m = re.match(r"\s*([^<]+)", bit)
            if not name_m:
                continue
            prices[re.sub(r"\s+", " ", name_m.group(1)).strip()] = {
                src.strip(): raw.strip()
                for src, raw in re.findall(
                    r'ml-src ([^"]+)">[^<]*</span>\s*<span class="ml-num[^"]*">([^<]+)',
                    bit,
                )
            }

        def _ml_side(src: str, prices: dict = prices) -> str:
            vals: dict[str, int] = {}
            for name, mp in prices.items():
                raw = (mp.get(src) or "").replace("−", "-").replace("+", "").strip()
                if raw in {"", "—", "–", "-", "N/A"}:
                    continue
                try:
                    vals[name] = int(float(raw))
                except ValueError:
                    continue
            if not vals:
                return ""
            return min(vals, key=lambda n: vals[n])

        def _proj_side(which: str, body: str = body, away: str = away, home: str = home) -> str:
            match = re.search(
                rf'proj-model {which}">[^<]*</span>\s*<span class="proj-val">([^<]+)',
                body,
            )
            if not match:
                return ""
            txt = match.group(1)

            def _pts(name: str) -> float | None:
                hit = re.search(
                    re.escape(name) + r"[^\d]{0,24}(\d+(?:\.\d+)?)",
                    txt,
                    flags=re.I,
                )
                return float(hit.group(1)) if hit else None

            ap, hp = _pts(away), _pts(home)
            if ap is None or hp is None or ap == hp:
                return ""
            return away if ap > hp else home

        def _line_row(labels: tuple[str, ...], body: str = body) -> dict[str, str]:
            joined = "|".join(labels)
            tr = re.search(
                rf'class="market-k">(?:{joined})</td>([\s\S]*?)</tr>',
                body,
                flags=re.I,
            )
            if not tr:
                return {}
            return {
                key: re.sub(r"\s+", " ", val).strip()
                for key, val in re.findall(
                    r'class="val-(books|pl|xs)">([^<]*)',
                    tr.group(1),
                )
            }

        found.append(
            {
                "date": dk,
                "away": away,
                "home": home,
                "aa": aa,
                "hs": hs,
                "book_ml": _ml_side("books"),
                "pl_ml": _ml_side("pl"),
                "xs_ml": _proj_side("xs"),
                "spread": _line_row(("Spread", "Puck Line", "Run Line")),
                "total": _line_row(("Total",)),
            }
        )
    return found


def _spread_side(cell: str, away: str, home: str) -> tuple[str, float | None]:
    text = (cell or "").replace("−", "-").replace("–", "-").strip()
    if not text or text.upper() in {"PK", "PICK", "PICK'EM", "PICKEM", "EVEN", "N/A", "—", "-"}:
        return "", None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*$", text)
    if not match:
        return "", None
    try:
        line = float(match.group(1))
    except ValueError:
        return "", None
    who = text[: match.start()].strip()
    if _same_team(who, home):
        return home, line
    if _same_team(who, away):
        return away, line
    return "", None


def _cover(team: str, line: float, away: str, home: str, aa: int, hs: int) -> str:
    margin = (hs - aa) if team == home else (aa - hs) if team == away else None
    if margin is None:
        return ""
    diff = margin + line
    if abs(diff) < 1e-6:
        return "PUSH"
    return "WIN" if diff > 0 else "LOSS"


def _total_lean(raw: str, book: float) -> str:
    text = (raw or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return ""
    val = float(text)
    if val > book:
        return "over"
    if val < book:
        return "under"
    return ""


def _shared(grades: list[str]) -> str:
    decided = [g for g in grades if g in {"WIN", "LOSS"}]
    if not decided or len(decided) != len([g for g in grades if g]):
        return ""
    if all(g == "WIN" for g in decided):
        return "WIN"
    if all(g == "LOSS" for g in decided):
        return "LOSS"
    return ""


def _wl(grades: list[str]) -> str:
    wins = sum(1 for g in grades if g == "WIN")
    losses = sum(1 for g in grades if g == "LOSS")
    if wins + losses == 0:
        return "0-0"
    return f"{wins}-{losses}"


def _records(games: list[dict], ln_key: str, yesterday: date) -> dict:
    cut7_cal = (yesterday - timedelta(days=6)).isoformat()
    yday = yesterday.isoformat()
    in_calendar = [g for g in games if cut7_cal <= g["date"] <= yday]
    try:
        ln_d = date.fromisoformat(ln_key)
        cut7_ln = (ln_d - timedelta(days=6)).isoformat()
    except ValueError:
        cut7_ln = ln_key
    use_calendar = bool(in_calendar)
    bucket = {
        market: {label: {"ln": [], "d7": [], "d30": []} for label in _LABELS}
        for market in _MARKETS
    }

    def _add(market: str, label: str, dk: str, grade: str) -> None:
        if grade not in {"WIN", "LOSS"}:
            return
        if dk == ln_key:
            bucket[market][label]["ln"].append(grade)
        if use_calendar and cut7_cal <= dk <= yday:
            bucket[market][label]["d7"].append(grade)
        elif not use_calendar and cut7_ln <= dk <= ln_key:
            bucket[market][label]["d7"].append(grade)
        if dk <= yday:
            bucket[market][label]["d30"].append(grade)

    pairs = (
        ("PL = Books", ("pl", "book")),
        ("PL = XSharp", ("pl", "xs")),
        ("Books = XSharp", ("book", "xs")),
    )
    for g in games:
        dk, away, home, aa, hs = g["date"], g["away"], g["home"], g["aa"], g["hs"]
        ml = {"book": g["book_ml"], "pl": g["pl_ml"], "xs": g["xs_ml"]}
        ml_grade: dict[str, str] = {}
        if aa != hs:
            winner = home if hs > aa else away
            for key, side in ml.items():
                if side:
                    ml_grade[key] = "WIN" if side == winner else "LOSS"
        for label, keys in pairs:
            sides = [ml.get(k) or "" for k in keys]
            if sides[0] and sides[0] == sides[1]:
                _add("moneyline", label, dk, _shared([ml_grade.get(k) or "" for k in keys]))
        if all(ml.values()) and len(set(ml.values())) == 1:
            _add(
                "moneyline",
                "All 3 agree",
                dk,
                _shared([ml_grade.get(k) or "" for k in ("pl", "book", "xs")]),
            )

        spread_side: dict[str, str] = {}
        spread_grade: dict[str, str] = {}
        for key, src in (("book", "books"), ("pl", "pl"), ("xs", "xs")):
            team, line = _spread_side((g.get("spread") or {}).get(src) or "", away, home)
            if not team or line is None:
                continue
            spread_side[key] = team
            spread_grade[key] = _cover(team, line, away, home, aa, hs)
        for label, keys in pairs:
            sides = [spread_side.get(k) or "" for k in keys]
            if sides[0] and sides[0] == sides[1]:
                _add("spread", label, dk, _shared([spread_grade.get(k) or "" for k in keys]))
        if len(spread_side) == 3 and len(set(spread_side.values())) == 1:
            _add(
                "spread",
                "All 3 agree",
                dk,
                _shared([spread_grade.get(k) or "" for k in ("pl", "book", "xs")]),
            )

        totals = g.get("total") or {}
        book_raw = (totals.get("books") or "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?", book_raw):
            continue
        book_line = float(book_raw)
        leans = {
            "pl": _total_lean(totals.get("pl") or "", book_line),
            "xs": _total_lean(totals.get("xs") or "", book_line),
        }
        actual = aa + hs
        if actual == book_line or not leans["pl"] or leans["pl"] != leans["xs"]:
            continue
        over_hit = actual > book_line
        grade = "WIN" if (leans["pl"] == "over") == over_hit else "LOSS"
        _add("totals", "PL = XSharp", dk, grade)
    return bucket


def render_three_way_section(sport: str, cards_html: str, db_path: Path, market: str = "") -> str:
    yesterday = _yesterday()
    latest = _latest_final(db_path, sport, yesterday)
    games = _games_from_cards(cards_html)
    card_dates = sorted({g["date"] for g in games if g["date"] <= yesterday.isoformat()})
    named = re.findall(
        r"Last night \((\d{4}-\d{2}-\d{2})\)", cards_html or "", flags=re.I
    )
    if latest:
        ln_key = latest[0]
    elif card_dates:
        ln_key = card_dates[-1]
    elif named:
        ln_key = named[0]
    else:
        ln_key = yesterday.isoformat()
    records = _records(games, ln_key, yesterday)
    want = (market or "").strip().lower()
    order = list(_MARKETS)
    if want in order:
        order = [want] + [name for name in order if name != want]
    blocks = []
    for name in order:
        rows = []
        for label in _LABELS:
            cells = records[name][label]
            rows.append(
                "<tr>"
                f'<td class="bucket">{escape(label)}</td>'
                f"<td>{_wl(cells['ln'])}</td>"
                f"<td>{_wl(cells['d7'])}</td>"
                f"<td>{_wl(cells['d30'])}</td>"
                "</tr>"
            )
        blocks.append(
            f'<section class="pl-consensus-records" id="three-way-{name}">'
            f"<h2>{escape(_TITLES[name])}</h2>"
            "<table><thead><tr><th>Signal</th>"
            f"<th>Last night ({escape(ln_key)})</th>"
            "<th>Past 7 days</th><th>Past 30 days</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></section>"
        )
    return "".join(blocks)
