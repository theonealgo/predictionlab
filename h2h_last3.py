"""H2H Last 10 line on every team-sport card.

Projected total = Team A's average points over its 3 most recent finals plus
Team B's. Projected spread = home average minus away average, shown as the
favored team and the line. Combined record = the 6 games' final totals counted
Over / Under against the game's posted Vegas total, or the XSharp total when no
Vegas total is posted. The projected total is never the comparison line.
"""
from __future__ import annotations

import html as _html
import re
import sqlite3
import time
from pathlib import Path

_MEMO: dict = {}
_MEMO_TTL = 600.0

_GID_RE = re.compile(r'data-game-id="([A-Za-z]+_[^"]+)"')
_SF_RE = re.compile(
    r'(class="sf-label">\s*H2H Last 10\s*</span>\s*<span class="sf-val">)([\s\S]*?)(</span>)',
    re.I,
)
_CHIP_RE = re.compile(
    r'(<div class="line-chip-label">\s*H2H Last 10\s*</div>\s*<div class="line-chip-val">)([^<]*)(</div>)',
    re.I,
)
_TOTAL_ROW_RE = re.compile(
    r'<td class="market-k">\s*Total\s*</td>\s*<td class="val-books">([\s\S]*?)</td>\s*'
    r'<td class="val-pl">[\s\S]*?</td>\s*<td class="val-xs">([\s\S]*?)</td>',
    re.I,
)


def _db_path() -> Path:
    import sys

    for name in ("NHL77FINAL", "__main__"):
        mod = sys.modules.get(name)
        db = getattr(mod, "DATABASE", None) if mod is not None else None
        if db and Path(db).is_file():
            return Path(db)
    if Path("/data/sports_predictions_original.db").is_file():
        return Path("/data/sports_predictions_original.db")
    return Path(__file__).resolve().parent / "sports_predictions_original.db"


def _half(x: float) -> float:
    return round(x * 2) / 2


def _num(x: float) -> str:
    return f"{x:g}"


def _line(raw: str | None) -> float | None:
    text = re.sub(r"<[^>]*>[^<]*</[^>]+>|<[^>]*>", " ", raw or "")
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _last3(conn, sport: str, team: str, before: str) -> list[tuple[float, float]]:
    rows = conn.execute(
        "SELECT home_team_id, home_score, away_score FROM games "
        "WHERE sport = ? AND home_score IS NOT NULL AND away_score IS NOT NULL "
        "AND (home_team_id = ? OR away_team_id = ?) AND date(game_date) < date(?) "
        "ORDER BY date(game_date) DESC, game_date DESC LIMIT 3",
        (sport, team, team, before),
    ).fetchall()
    out = []
    for home, hs, as_ in rows:
        try:
            hs, as_ = float(hs), float(as_)
        except (TypeError, ValueError):
            continue
        out.append((hs, as_) if home == team else (as_, hs))
    return out


def _numbers(gid: str):
    """(home, away, home avg, away avg, game totals) or None when either team has no finals."""
    hit = _MEMO.get(gid)
    if hit and time.time() - hit[0] < _MEMO_TTL:
        return hit[1]
    out = None
    try:
        conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT sport, home_team_id, away_team_id, date(game_date) FROM games WHERE game_id = ?",
                (gid,),
            ).fetchone()
            if row and row[1] and row[2] and row[3]:
                sport, home, away, day = row
                hg = _last3(conn, sport, home, day)
                ag = _last3(conn, sport, away, day)
                # Early season: use the finals a team has (up to 3).
                if hg and ag:
                    out = (
                        home,
                        away,
                        sum(s for s, _ in hg) / len(hg),
                        sum(s for s, _ in ag) / len(ag),
                        [s + o for s, o in hg + ag],
                    )
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if len(_MEMO) > 20000:
        _MEMO.clear()
    _MEMO[gid] = (time.time(), out)
    return out


def _short(full: str, shown: list[str], idx: int) -> str:
    if idx < len(shown) and shown[idx].strip():
        return shown[idx].strip()
    parts = (full or "").split()
    return parts[-1] if parts else full


def h2h_text(gid: str, card: str) -> str | None:
    """The H2H Last 10 line for one card, or None when either team has no finals."""
    nums = _numbers(gid)
    if not nums:
        return None
    home, away, h_avg, a_avg, totals = nums
    names = [_html.unescape(n) for n in re.findall(r'class="team-name">\s*([^<]+?)\s*<', card)]
    away_s, home_s = _short(away, names, 0), _short(home, names, 1)
    total = _num(_half(h_avg + a_avg))
    diff = _half(abs(h_avg - a_avg))
    if diff == 0:
        spread = "PK"
    else:
        spread = f"{home_s if h_avg > a_avg else away_s} -{_num(diff)}"
    text = f"{total} · {spread}"
    vegas = xs = None
    row = _TOTAL_ROW_RE.search(card)
    if row:
        vegas, xs = _line(row.group(1)), _line(row.group(2))
    if vegas is None:
        m = re.search(r'data-books-total="([^"]*)"', card)
        vegas = _line(m.group(1)) if m else None
    if xs is None:
        m = re.search(r'data-xs-total="([^"]*)"', card)
        xs = _line(m.group(1)) if m else None
    ref = vegas if vegas is not None else xs
    if ref is not None:
        over = sum(1 for t in totals if t > ref)
        under = sum(1 for t in totals if t < ref)
        text += f" · Combined record: {over}-{under} Over"
    return text


def apply_h2h_last3(page: str) -> str:
    """Rewrite every H2H Last 10 value (card face, chart chip, data-h2h) on a page."""
    if not page or "H2H Last 10" not in page or "data-game-id" not in page:
        return page
    starts = [(m.start(), m.group(1)) for m in _GID_RE.finditer(page)]
    if not starts:
        return page
    cards: dict[str, str] = {}
    for i, (pos, gid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(page)
        cards[gid] = cards.get(gid, "") + page[pos:end]
    texts: dict[str, str | None] = {}

    def _for(pos: int) -> str | None:
        gid = None
        for p, g in starts:
            if p > pos:
                break
            gid = g
        if gid is None:
            return None
        if gid not in texts:
            texts[gid] = h2h_text(gid, cards.get(gid, ""))
        return texts[gid]

    def _sub(regex: re.Pattern, s: str) -> str:
        out, last = [], 0
        for m in regex.finditer(s):
            val = _for(m.start())
            if val is None:
                continue
            out.append(s[last:m.start()])
            out.append(m.group(1) + _html.escape(val, quote=False) + m.group(3))
            last = m.end()
        if not out:
            return s
        out.append(s[last:])
        return "".join(out)

    page = _sub(_SF_RE, page)
    page = _sub(_CHIP_RE, page)

    def _attr(m: re.Match) -> str:
        tag = m.group(0)
        g = _GID_RE.search(tag)
        if not g:
            return tag
        if g.group(1) not in texts:
            texts[g.group(1)] = h2h_text(g.group(1), cards.get(g.group(1), ""))
        val = texts[g.group(1)]
        if val is None:
            return tag
        esc = _html.escape(val, quote=True)
        tag = re.sub(r'\bdata-h2h="[^"]*"', f'data-h2h="{esc}"', tag, count=1)
        tag = re.sub(r'\bdata-h2h-l3="[^"]*"', f'data-h2h-l3="{esc}"', tag, count=1)
        return tag

    return re.sub(r'<div\b[^>]*\bdata-h2h="[^"]*"[^>]*>', _attr, page)
