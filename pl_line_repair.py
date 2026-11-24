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


def _snap(row):
    try:
        snap = json.loads(row["lock_card_json"] or "{}")
    except (TypeError, ValueError):
        snap = {}
    return snap if isinstance(snap, dict) else {}


def _finite(value):
    try:
        if value is None:
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def stored_spread(row):
    snap = _snap(row)
    for value in (row["lock_pl_spread"], snap.get("disp_pl_spread"), snap.get("our_spread")):
        number = _finite(value)
        if number is not None:
            return number
    return None


def stored_total(row):
    snap = _snap(row)
    for value in (row["lock_pl_total"], snap.get("disp_pl_total"), snap.get("our_total")):
        number = _finite(value)
        if number is not None and number > 0:
            return number
    return None


def stored_projected(row):
    """Prediction Lab projected goals from the odds-engine snapshot (never invent)."""
    snap = _snap(row)
    home = _finite(snap.get("v2_expected_home") or snap.get("pl_proj_home_pts")
                   or snap.get("our_home_pts") or row["expected_home_score"])
    away = _finite(snap.get("v2_expected_away") or snap.get("pl_proj_away_pts")
                   or snap.get("our_away_pts") or row["expected_away_score"])
    if home is not None and away is not None:
        return round(home * 2) / 2, round(away * 2) / 2
    spread = stored_spread(row)
    total = stored_total(row)
    if spread is None or total is None:
        return None
    # Home-centric spread: positive means home is the underdog (gives points).
    home = (total - spread) / 2
    away = total - home
    return round(home * 2) / 2, round(away * 2) / 2


def _proj_label(home_name, away_name, home_pts, away_pts):
    return f"{away_name} {away_pts:g} – {home_name} {home_pts:g}"


def fill_html(card, spread, projected=None, total=None):
    from market_card_refresh import ROWS, LABEL, blank, text
    html = card["html"]
    if re.search(r'class="game-time"[^>]*>[^<]*\bFINAL\b', html, re.I):
        return card
    home_name = card.get("home") or ""
    away_name = card.get("away") or ""
    if spread is not None:
        team = home_name if spread > 0 else away_name
        if team or spread == 0:
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
    if total is not None:
        tot_s = f"{float(total):g}"
        def total_line(match):
            source = match[0]
            heading = LABEL.search(source)
            if not heading or text(heading[1]).lower() != "total":
                return source
            return re.sub(r'(<td class="val-pl">)(.*?)(</td>)',
                          lambda cell: cell[1]+escape(tot_s)+cell[3] if blank(cell[2]) else cell[0],
                          source, flags=re.S)
        html = ROWS.sub(total_line, html)
    if projected and home_name and away_name:
        home_pts, away_pts = projected
        proj = _proj_label(home_name, away_name, home_pts, away_pts)
        def proj_row(match):
            if not blank(match[2]):
                return match[0]
            return match[1] + escape(proj) + match[3]
        html = re.sub(
            r'(<span class="proj-model pl">Prediction Lab</span>\s*<span class="proj-val">)(.*?)(</span>)',
            proj_row, html, count=1, flags=re.S)
        html = re.sub(
            r'(data-pl-proj=")([^"]*)(")',
            lambda match: match[1]+escape(proj, quote=True)+match[3] if blank(match[2]) else match[0],
            html, count=1)
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
    """Read saved values only; never wait for a provider or model here.

    Always fill blank Prediction Lab projected scores / puck lines from the
    stored odds-engine snapshot so Projected Score never shows "—" when we
    already have expected goals or a PL total+spread.
    """
    from contextlib import closing
    identities = {}
    for card in payload.get("cards") or []:
        match = re.search(r'data-game-id="([^"]+)"', card.get("html") or "")
        if match and not card.get("isFinal"):
            identities[id(card)] = match[1]
    if not identities:
        return payload
    values, totals, projected, missing = {}, {}, {}, []
    try:
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as conn:
            conn.row_factory = sqlite3.Row
            for identity in set(identities.values()):
                row = conn.execute(
                    "SELECT lock_pl_spread,lock_pl_total,expected_home_score,expected_away_score,"
                    "lock_card_json FROM predictions WHERE sport='NHL' AND game_id=?",
                    (identity,)).fetchone()
                if not row:
                    missing.append(identity)
                    continue
                value = stored_spread(row)
                if value is None:
                    missing.append(identity)
                else:
                    values[identity] = value
                tot = stored_total(row)
                if tot is not None:
                    totals[identity] = tot
                proj = stored_projected(row)
                if proj is not None:
                    projected[identity] = proj
        running = start(database, missing)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("Saved NHL PL refresh unavailable")
        return payload

    def patched(card):
        identity = identities.get(id(card))
        if not identity:
            return card
        if identity not in values and identity not in projected and identity not in totals:
            return card
        return fill_html(
            card,
            values.get(identity),
            projected=projected.get(identity),
            total=totals.get(identity),
        )

    return {**payload, "refreshing": bool(payload.get("refreshing") or running),
            "cards": [patched(card) for card in payload["cards"]]}


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
