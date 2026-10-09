"""NCAAW chart payload: graded finals read from the cards already served on /ncaaw-results.

No grades are calculated here. Marks are the ones printed on each card.
"""
from __future__ import annotations

import re


def _graded_row_from_card(card: str, date: str) -> dict | None:
    names = [
        re.sub(r"\s+", " ", n).strip()
        for n in re.findall(r'class="team-name">([^<]+)', card)
    ]
    scores = [
        re.sub(r"\s+", " ", s).strip()
        for s in re.findall(r'class="final-score[^"]*">\s*([^<]*?)\s*<', card)
    ]
    if len(names) < 2 or len(scores) < 2:
        return None
    away, home = names[0], names[1]
    try:
        away_score = int(float(scores[0]))
        home_score = int(float(scores[1]))
    except ValueError:
        return None
    edge = re.search(
        r'<div class="pc-box([^"]*)"[^>]*>\s*<div class="pc-name">Edge</div>'
        r'[\s\S]*?<div class="pc-val">([^<]*)</div>\s*'
        r'<div class="pc-side[^"]*">([^<]*)</div>',
        card,
        flags=re.I,
    )
    if not edge:
        return None
    klass = edge.group(1).lower()
    if "wrong" in klass:
        result = "Wrong"
    elif "correct" in klass:
        result = "Correct"
    else:
        return None
    pick = re.sub(r"[✅❌]", "", edge.group(3)).strip()
    prob = re.sub(r"\s+", "", edge.group(2)).strip()
    if not pick:
        return None
    models = []
    for box in re.finditer(
        r'<div class="pc-box([^"]*)"[^>]*>\s*<div class="pc-name">([^<]+)</div>'
        r'[\s\S]*?<div class="pc-side[^"]*">([^<]*)</div>',
        card,
        flags=re.I,
    ):
        name = box.group(2).strip()
        side = re.sub(r"[✅❌]", "", box.group(3)).strip()
        if not name or not side:
            continue
        mark = "✓" if "correct" in box.group(1).lower() else "✗" if "wrong" in box.group(1).lower() else ""
        models.append(f"{name} {side} {mark}".strip())
    return {
        "date": date,
        "away": away,
        "home": home,
        "score": f"{away_score}–{home_score}",
        "pick": pick,
        "prob": prob,
        "result": result,
        "models": " ".join(models),
    }


_NCAAW_MODEL_ORDER = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _ncaaw_team(row: dict, side: str) -> str:
    if side == "home":
        return str(row.get("home") or row.get("home_team") or row.get("home_team_id") or "")
    return str(row.get("away") or row.get("away_team") or row.get("away_team_id") or "")


def _ncaaw_model_map(card: str) -> dict:
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


def _ncaaw_chart_finals(html: str) -> list[dict]:
    """Graded chart rows from the cards already on /ncaaw-results.

    NCAAW cards use team-slot, not team-col, so the shared extractor drops them.
    Spread and total marks are the checkmarks already printed on each card.
    """
    from mlb_results_ui import _extract_spread_totals

    finals: list[dict] = []
    parts = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    seq = parts[1:]
    for date, content in zip(seq[0::2], seq[1::2]):
        bits = re.split(r'(<div class="game-card pick-card")', content)
        idx = 1
        while idx < len(bits):
            card = bits[idx] + (bits[idx + 1] if idx + 1 < len(bits) else "")
            idx += 2
            graded = _graded_row_from_card(card, date)
            if not graded:
                continue
            spread, totals = _extract_spread_totals(card)
            gid_m = re.search(r'data-game-id="([^"]*)"', card, flags=re.I)
            h2h_m = re.search(
                r'H2H Last 10</span>\s*<span class="sf-val">([^<]+)',
                card,
                flags=re.I,
            )
            try:
                away_score, home_score = [
                    int(x) for x in graded["score"].split("–")
                ]
            except ValueError:
                away_score = home_score = None
            prob = graded.get("prob") or ""
            face_prob = None
            pm = re.search(r"(\d+(?:\.\d+)?)", prob)
            if pm:
                try:
                    face_prob = float(pm.group(1))
                except ValueError:
                    face_prob = None
            finals.append(
                {
                    "game_date": graded["date"],
                    "league": "NCAAW",
                    "away": graded["away"],
                    "home": graded["home"],
                    "away_team_id": graded["away"],
                    "home_team_id": graded["home"],
                    "away_score": away_score,
                    "home_score": home_score,
                    "face_pick": graded["pick"],
                    "face_prob": face_prob,
                    "correct": graded["result"] == "Correct",
                    "spread": spread,
                    "totals": totals,
                    "h2h10": (h2h_m.group(1).strip() if h2h_m else ""),
                    "models": _ncaaw_model_map(card),
                    "game_id": (gid_m.group(1).strip() if gid_m else "") or None,
                }
            )
    return finals


def _ncaaw_attach_finals(payload: dict | None, finals: list[dict]) -> dict:
    if not isinstance(payload, dict):
        payload = {"ok": bool(finals), "markets": {}}
    payload["finals"] = finals
    payload["ok"] = bool(finals) or bool(payload.get("ok"))
    markets = payload.get("markets")
    if not isinstance(markets, dict):
        markets = {}
        payload["markets"] = markets
    markets.setdefault("moneyline", {})["finals"] = finals
    markets.setdefault("spread", {})["finals"] = [g for g in finals if g.get("spread")] or finals
    markets.setdefault("totals", {})["finals"] = [g for g in finals if g.get("totals")] or finals
    return payload
