"""WNBA chart: Moneyline, Spread and Totals graded game lists built from the payload finals.

Marks are the ones stored on each card. Nothing is invented.
"""
from __future__ import annotations

import re


_WNBA_MODEL_ORDER = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _wnba_team(row: dict, side: str) -> str:
    if side == "home":
        return str(row.get("home") or row.get("home_team") or row.get("home_team_id") or "")
    return str(row.get("away") or row.get("away_team") or row.get("away_team_id") or "")


def _wnba_model_map(card: str) -> dict:
    """Per-model pick, side, and grade already printed on the result card."""
    from mlb_results_ui import _extract_card_models

    models = _extract_card_models(card) or {}
    for box in re.finditer(
        r'<div class="pc-name">([^<]+)</div>[\s\S]*?<div class="pc-side\s+([^"\s]+)',
        card or "",
        flags=re.I,
    ):
        name = box.group(1).strip()
        side = box.group(2).strip().lower()
        if name in models and side in ("home", "away"):
            models[name]["side"] = side
    return models


def _wnba_models_label(models: dict | None) -> str:
    """Model result text: name, side picked, and correct/wrong mark."""
    if not isinstance(models, dict) or not models:
        return ""
    names = [n for n in _WNBA_MODEL_ORDER if n in models]
    names.extend(n for n in models if n not in names)
    bits = []
    for name in names:
        block = models.get(name) or {}
        pick = str(block.get("pick") or "").strip()
        if not pick:
            continue
        if block.get("correct") is True:
            mark = "✓"
        elif block.get("correct") is False:
            mark = "✗"
        else:
            mark = ""
        bits.append(f"{name} {pick} {mark}".strip())
    return " ".join(bits)


_FACE_ML_RE = re.compile(
    r'<div class="ml-line face-(books|pl)-ml">\s*<span class="ml-src[^"]*">[^<]*</span>\s*'
    r'<span class="ml-num[^"]*">\s*([^<]*?)\s*</span>',
    re.I,
)


def wnba_face_odds(cards: str) -> dict:
    """Books and Prediction Lab moneylines printed on each result card, by game id."""
    out: dict = {}
    marks = list(re.finditer(r'data-game-id="([^"]+)"', cards or ""))
    for idx, mark in enumerate(marks):
        gid = mark.group(1)
        if gid in out:
            continue
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(cards)
        books, pl = [], []
        for hit in _FACE_ML_RE.finditer(cards[mark.end():end]):
            (books if hit.group(1).lower() == "books" else pl).append(hit.group(2).strip())
        if len(books) >= 2 or len(pl) >= 2:
            out[gid] = {"books": books[:2], "pl": pl[:2]}
    return out


def _wnba_ml_pair(vals: list) -> str:
    vals = [v for v in (vals or []) if v and v not in ("—", "-")]
    return " / ".join(vals[:2]) if len(vals) >= 2 else "—"


def _wnba_games_section(market: str, title: str, finals: list, odds: dict | None = None) -> str:
    """Same graded-games table the team-results chart already paints."""
    from html import escape

    rows = []
    shown = list(finals or [])
    if market == "moneyline":
        for game in shown:
            home = _wnba_team(game, "home")
            away = _wnba_team(game, "away")
            hs, aws = game.get("home_score"), game.get("away_score")
            score = f"{aws}–{hs}" if hs is not None and aws is not None else "—"
            face_missing = not game.get("face_pick")
            face = game.get("face_pick") or "N/A"
            fp = game.get("face_prob")
            fp_s = f"{fp}%" if fp is not None else "N/A"
            ok = game.get("correct")
            res = "Correct" if ok is True else "Wrong" if ok is False else "N/A"
            why = (
                ' title="Edge stored 50% for this game, so it made no pick to grade."'
                if face_missing else ""
            )
            models = _wnba_models_label(game.get("models"))
            date = escape(str(game.get("game_date") or "")[:10])
            gid = escape(str(game.get("game_id") or ""))
            rows.append(
                f'<tr data-date="{date}" data-game-id="{gid}">'
                f"<td>{date}</td>"
                f"<td>{escape(str(game.get('league') or 'WNBA'))}</td>"
                f"<td>{escape(away)} @ {escape(home)}</td>"
                f"<td>{escape(score)}</td>"
                f"<td>{escape(_wnba_ml_pair((odds or {}).get(str(game.get('game_id') or ''), {}).get('books')))}</td>"
                f"<td>{escape(_wnba_ml_pair((odds or {}).get(str(game.get('game_id') or ''), {}).get('pl')))}</td>"
                f"<td{why}>{escape(str(face))}</td>"
                f"<td{why}>{escape(fp_s)}</td>"
                f"<td{why}>{escape(res)}</td>"
                f'<td class="mono-models">{escape(models or "—")}</td>'
                "</tr>"
            )
        head = (
            "<thead><tr><th>Date</th><th>League</th><th>Match</th><th>Score</th>"
            "<th>Books ML (away / home)</th><th>PL ML (away / home)</th>"
            "<th>Edge pick</th><th>%</th><th>Result</th><th>Models</th></tr></thead>"
        )
        colspan = 10
        section_id = "ssr-finals"
    else:
        key = "spread" if market == "spread" else "totals"
        for game in shown:
            home = _wnba_team(game, "home")
            away = _wnba_team(game, "away")
            hs, aws = game.get("home_score"), game.get("away_score")
            score = f"{aws}–{hs}" if hs is not None and aws is not None else "—"
            block = game.get(key) if isinstance(game.get(key), dict) else {}
            book = block.get("book") or block.get("book_line") or "—"
            pl = block.get("pl_pick") or block.get("pl_line") or block.get("pick") or "—"
            xs = block.get("xs_pick") or block.get("xs_line") or "—"
            ok = block.get("correct")
            push = block.get("push")
            if ok is None and not push and key == "spread" and hs is not None and aws is not None:
                # No stored grade: grade the published PL line against the final score.
                import re as _re
                _m = _re.match(r"^(.*?)\s*([+-]\d+(?:\.\d+)?)\s*$", str(block.get("pl_pick") or ""))
                if _m:
                    _team, _line = _m.group(1).strip().lower(), float(_m.group(2))
                    try:
                        _hn, _an = str(home).strip().lower(), str(away).strip().lower()
                        _side = None
                        if _team and (_team == _hn or _team in _hn or _hn in _team):
                            _side = float(hs) - float(aws)
                        elif _team and (_team == _an or _team in _an or _an in _team):
                            _side = float(aws) - float(hs)
                        if _side is not None:
                            _cover = _side + _line
                            if abs(_cover) < 1e-9:
                                push = True
                            else:
                                ok = _cover > 0
                    except (TypeError, ValueError):
                        pass
            res = (
                "Push" if push
                else "Correct" if ok is True
                else "Wrong" if ok is False
                else "—"
            )
            h2h = game.get("h2h10") or game.get("h2h_l10") or "—"
            rows.append(
                "<tr>"
                f"<td>{escape(str(game.get('game_date') or '')[:10])}</td>"
                f"<td>{escape(away)} @ {escape(home)}</td>"
                f"<td>{escape(score)}</td>"
                f"<td>{escape(str(book))}</td>"
                f"<td>{escape(str(h2h))}</td>"
                f"<td>{escape(str(pl))}</td>"
                f"<td>{escape(str(xs))}</td>"
                f"<td>{escape(res)}</td>"
                "</tr>"
            )
        head = (
            "<thead><tr><th>Date</th><th>Match</th><th>Score</th>"
            "<th>Book</th><th>H2H L10</th><th>PL</th><th>XSharp</th><th>Result</th></tr></thead>"
        )
        colspan = 8
        section_id = "ssr-finals-spread" if market == "spread" else "ssr-finals-totals"
    body = "".join(rows) or f'<tr><td colspan="{colspan}" class="muted">No finals.</td></tr>'
    return (
        f'<section id="{section_id}" data-ssr-market="{market}">'
        f'<h2 class="sec-title">{escape(title)} <span class="tag">({len(shown)})</span></h2>'
        '<div class="table-wrap"><table class="results-table">'
        f"{head}<tbody>{body}</tbody></table></div></section>"
    )
