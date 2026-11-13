"""Fill only absent, pregame PL spread fields; never copy book/XSharp lines."""
from datetime import datetime, timezone
from html import escape
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time

_LOCK = threading.Lock()
_JOBS = {}
_LAST = {}


def stored_spread(row):
    try:
        snap = json.loads(row["lock_card_json"] or "{}")
    except (TypeError, ValueError):
        snap = {}
    if not isinstance(snap, dict):
        snap = {}
    for value in (row["lock_pl_spread"], snap.get("disp_pl_spread"), snap.get("our_spread")):
        try:
            if value is not None and math.isfinite(float(value)):
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def fill_html(card, spread):
    from market_card_refresh import ROWS, LABEL, blank, text
    html = card["html"]
    if re.search(r'class="game-time"[^>]*>[^<]*\bFINAL\b', html, re.I):
        return card
    team = card.get("home") if spread > 0 else card.get("away")
    if not team:
        return card
    label = f"{team} -{abs(spread):g}" if spread else "Pick'em"
    def line(match):
        source = match[0]
        heading = LABEL.search(source)
        if not heading or text(heading[1]).lower() not in ("puck line", "spread"):
            return source
        return re.sub(r'(<td class="val-pl">)(.*?)(</td>)',
                      lambda cell: cell[1]+escape(label)+cell[3] if blank(cell[2]) else cell[0],
                      source, flags=re.S)
    html = ROWS.sub(line, html)
    html = re.sub(r'(data-pl-spread=")([^"]*)(")',
                  lambda match: match[1]+escape(label, quote=True)+match[3] if blank(match[2]) else match[0], html)
    from sports.team_efficiency_attach import spread_to_home_prob_pct
    home_probability = spread_to_home_prob_pct(spread, "NHL")
    confidence = max(home_probability, round(100-home_probability, 1))
    side = "home" if home_probability >= 50 else "away"
    side_label = re.search(r'<div class="pc-side '+side+r'"[^>]*>([^<]+)</div>', html)
    team_label = text(side_label[1]) if side_label else card.get(side)
    filled = False
    def efficiency(match):
        nonlocal filled
        if not blank(match[2]) or not blank(match[3]):
            return match[0]
        filled = True
        return (match[1]+f'<div class="pc-val">{confidence:g}%</div>'
                +f'<div class="pc-side {side}">{escape(team_label or "")}</div>')
    html = re.sub(
        r'(<div class="pc-box[^"]*">\s*<div class="pc-name">Efficiency</div>\s*)'
        r'<div class="pc-val"[^>]*>(.*?)</div>\s*<div class="pc-side[^"]*"[^>]*>(.*?)</div>',
        efficiency, html, count=1, flags=re.S)
    if filled:
        html = re.sub(r'(data-m-efficiency=")([^"]*)(")',
                      lambda match: match[1]+f"{home_probability:g}"+match[3] if blank(match[2]) else match[0], html)
    return {**card, "html": html}


def start(database, identities):
    if not identities:
        return
    identity = str(Path(database).resolve())
    with _LOCK:
        process = _JOBS.get(identity)
        if process and process.poll() is None:
            return True
        if time.monotonic()-_LAST.get(identity, -1000) < 300:
            return False
        from served_cache import cache_dir
        cache = cache_dir() / "pl-line-repair"
        cache.mkdir(parents=True, exist_ok=True)
        with (cache / "worker.log").open("ab") as log:
            _JOBS[identity] = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--database", identity,
                 "--cache", str(cache), "--ids", json.dumps(sorted(set(identities)))],
                stdout=subprocess.DEVNULL, stderr=log)
        _LAST[identity] = time.monotonic()
        return True


def refresh(payload, database):
    """Read saved values only; never wait for a provider or model here."""
    from contextlib import closing
    identities = {}
    for card in payload.get("cards") or []:
        match = re.search(r'data-game-id="([^"]+)"', card.get("html") or "")
        if match and not card.get("isFinal"):
            identities[id(card)] = match[1]
    if not identities:
        return payload
    values, missing = {}, []
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as conn:
            conn.row_factory = sqlite3.Row
            for identity in set(identities.values()):
                row = conn.execute(
                    "SELECT lock_pl_spread,lock_card_json FROM predictions WHERE sport='NHL' AND game_id=?",
                    (identity,)).fetchone()
                if row:
                    value = stored_spread(row)
                    if value is None:
                        missing.append(identity)
                    else:
                        values[identity] = value
        running = start(database, missing)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("Saved NHL PL refresh unavailable")
        return payload
    return {**payload, "refreshing": bool(payload.get("refreshing") or running),
            "cards": [fill_html(card, values[identities[id(card)]])
                      if identities.get(id(card)) in values else card for card in payload["cards"]]}


def run(database, cache, identities):
    import fcntl
    import signal
    import requests
    import odds_engine_espn as engine
    with (Path(cache) / "publication.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        os.nice(15)
        signal.alarm(90)
        changed = 0
        with sqlite3.connect(database, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            for identity in identities:
                row = conn.execute("SELECT * FROM predictions WHERE sport='NHL' AND game_id=?", (identity,)).fetchone()
                if (not row or stored_spread(row) is not None
                        or row["actual_home_score"] is not None or row["actual_away_score"] is not None):
                    continue
                event_id = str(identity).split("_")[-1]
                response = requests.get(
                    "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary",
                    params={"event": event_id}, timeout=15)
                response.raise_for_status()
                event = response.json().get("header") or {}
                if str(event.get("id")) != event_id:
                    continue
                comp = (event.get("competitions") or [{}])[0]
                state = ((comp.get("status") or event.get("status") or {}).get("type") or {}).get("state")
                stamp = datetime.fromisoformat(str(comp.get("date") or event.get("date")).replace("Z", "+00:00"))
                teams = {item["homeAway"]: item.get("team", {}).get("displayName") for item in comp.get("competitors") or []}
                if state != "pre" or stamp <= datetime.now(timezone.utc) or teams != {
                        "home": row["home_team_id"], "away": row["away_team_id"]}:
                    continue
                # Provider I/O and PL calculation run with no write transaction held.
                result = engine.get_odds("NHL", row["home_team_id"], row["away_team_id"])
                if not result or result.get("spread_home") is None or stamp <= datetime.now(timezone.utc):
                    continue
                spread = -float(result["spread_home"])
                if not math.isfinite(spread):
                    continue
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT * FROM predictions WHERE sport='NHL' AND game_id=?", (identity,)).fetchone()
                if not current or stored_spread(current) is not None or stamp <= datetime.now(timezone.utc):
                    conn.rollback()
                    continue
                snap = json.loads(current["lock_card_json"] or "{}")
                if not isinstance(snap, dict):
                    conn.rollback()
                    continue
                snap.update(our_spread=spread, disp_pl_spread=spread,
                            _pl_spread_filled_at=datetime.now(timezone.utc).isoformat(),
                            _pl_spread_fill_source="espn_engine")
                cursor = conn.execute(
                    "UPDATE predictions SET lock_pl_spread=?,lock_card_json=? WHERE sport='NHL' AND game_id=? "
                    "AND lock_pl_spread IS NULL AND actual_home_score IS NULL AND actual_away_score IS NULL",
                    (spread, json.dumps(snap), identity))
                conn.commit()
                changed += cursor.rowcount
        return changed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--ids", required=True)
    args = parser.parse_args()
    run(args.database, args.cache, json.loads(args.ids))
