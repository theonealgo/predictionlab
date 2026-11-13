"""Restore saved rating fields; calculate first missing fields only before kickoff."""
from datetime import datetime
from html import escape, unescape
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

FIELDS = (("Grinder2", "glicko_home_prob", "glicko2_prob", "grinder2"),
          ("Takedown", "trueskill_home_prob", "trueskill_prob", "takedown"))


def model_values(predictor, home, away):
    return {
        "glicko2_prob": round(predictor.glicko2.win_probability(
            home, away, predictor.config["home_advantage"]*10)[0], 4),
        "trueskill_prob": round(predictor.trueskill.win_probability(home, away), 4),
    }


def pct(value):
    try:
        number = float(value)
        number = number*100 if abs(number) <= 1 else number
        return round(number, 1) if math.isfinite(number) and 0 <= number <= 100 else None
    except (ValueError, TypeError):
        return None


def before_start(card, game_date):
    match = re.search(r'class="game-time"[^>]*>(.*?)</', card["html"], re.S)
    clock = re.search(r"\b(\d{1,2}:\d{2})\s*([AP]M)\s*ET\b", match[1]) if match else None
    if not clock or card.get("isFinal") or re.search(r"\bFINAL\b", match[1], re.I):
        return False
    start = datetime.strptime(f"{game_date} {clock[1]} {clock[2]}", "%Y-%m-%d %I:%M %p").replace(
        tzinfo=ZoneInfo("America/New_York"))
    return start > datetime.now(ZoneInfo("America/New_York"))


def fill(card, values, saved):
    html = card["html"]
    for name, column, key, attribute in FIELDS:
        value = pct(values.get(column))
        if value is None:
            html = re.sub(r'title="'+name+r' rating is not available[^"]*"',
                          'title="No pregame '+name+' forecast was saved for this game."', html)
            continue
        side = "home" if value >= 50 else "away"
        label = card.get(side) or ""
        short = re.search(r'<div class="pc-side '+side+r'"[^>]*>([^<]+)</div>', html)
        if short:
            label = unescape(short[1])
        confidence = max(value, round(100-value, 1))
        def replace(match):
            if re.search(r"\d", match[2]):
                return match[0]
            # Saved historical probabilities may display, but no new grade is invented.
            return (match[1]+f'<div class="pc-val">{confidence:g}%</div>'
                    +f'<div class="pc-side {side}">{escape(label)}</div>')
        html = re.sub(
            r'(<div class="pc-box[^"]*">\s*<div class="pc-name">'+name+r'</div>\s*)'
            r'<div class="pc-val"[^>]*>(.*?)</div>\s*<div class="pc-side[^"]*"[^>]*>.*?</div>',
            replace, html, count=1, flags=re.S)
        html = re.sub(r'(data-m-'+attribute+r'=")([^"]*)(")',
                      lambda m: m[1]+f"{value:g}"+m[3] if not m[2].strip() else m[0], html)
    return {**card, "html": html}


def refresh(payload, sport, database, model_fn=None):
    if sport not in ("NCAAB", "NCAAW") or not payload.get("cards"):
        return payload
    cards = []
    with sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1) as conn:
        conn.row_factory = sqlite3.Row
        for card in payload["cards"]:
            match = re.search(r'data-game-id="([^"]+)"', card["html"])
            row = conn.execute(
                "SELECT game_date,lock_card_json,glicko_home_prob,trueskill_home_prob,"
                "actual_home_score,actual_away_score FROM predictions WHERE sport=? AND game_id=?",
                (sport, match[1])).fetchone() if match else None
            if not row:
                cards.append(fill(card, {}, False))
                continue
            values = dict(row)
            snapshot = json.loads(row["lock_card_json"] or "{}")
            for _, column, key, _ in FIELDS:
                if snapshot.get(key) is not None:
                    values[column] = snapshot[key]
            pregame = (row["actual_home_score"] is None and row["actual_away_score"] is None
                       and before_start(card, row["game_date"][:10]))
            if pregame and model_fn and any(values[column] is None for _, column, _, _ in FIELDS):
                result = model_fn(sport, card["home"], card["away"], row["game_date"]) or {}
                for _, column, key, _ in FIELDS:
                    if values[column] is None and pct(result.get(key)) is not None:
                        values[column] = result[key]
                if before_start(card, row["game_date"][:10]) and any(values[column] != row[column] for _, column, _, _ in FIELDS):
                    with sqlite3.connect(database, timeout=.1) as writer:
                        writer.row_factory = sqlite3.Row
                        current = writer.execute(
                            "SELECT lock_card_json FROM predictions WHERE sport=? AND game_id=?",
                            (sport, match[1])).fetchone()
                        snapshot = json.loads(current["lock_card_json"] or "{}")
                        for _, column, key, _ in FIELDS:
                            if snapshot.get(key) is None and pct(values[column]) is not None:
                                value = float(values[column])
                                snapshot[key] = value/100 if abs(value) > 1 else value
                        snapshot["_rating_fields_filled_at"] = datetime.now(ZoneInfo("UTC")).isoformat()
                        writer.execute(
                            "UPDATE predictions SET glicko_home_prob=COALESCE(glicko_home_prob,?),"
                            "trueskill_home_prob=COALESCE(trueskill_home_prob,?),lock_card_json=? "
                            "WHERE sport=? AND game_id=? AND actual_home_score IS NULL AND actual_away_score IS NULL",
                            (float(values["glicko_home_prob"])/100 if values["glicko_home_prob"] is not None and abs(float(values["glicko_home_prob"])) > 1 else values["glicko_home_prob"],
                             float(values["trueskill_home_prob"])/100 if values["trueskill_home_prob"] is not None and abs(float(values["trueskill_home_prob"])) > 1 else values["trueskill_home_prob"],
                             json.dumps(snapshot), sport, match[1]))
            cards.append(fill(card, values, bool(row)))
    return {**payload, "cards": cards}
