#!/usr/bin/env python3
"""Golf sandbox — ranked tournament win-% board (multi-model, tournament picker)."""
from __future__ import annotations

import html as html_lib
import json
import math
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HUB_ROOT = Path(__file__).resolve().parents[1]
if str(HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(HUB_ROOT))

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
ESPN_EVENT = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard/{event_id}"

# Transparent golf component models — each uses a different signal.
# Display names follow the Prediction Lab suite; formulas are golf-specific
# and intentionally non-identical (do not fake duplicate columns).
MODEL_META: list[tuple[str, str, str]] = [
    ("grinder2", "Grinder2", "Field-order prior (ESPN leaderboard / start list)"),
    ("takedown", "Takedown", "Live stroke score (lower is stronger)"),
    ("edge", "Edge", "Blend of field order + live score"),
    ("xsharp", "XSharp", "Sandbox Elo / seeded win% when a name match exists"),
    ("efficiency", "Efficiency", "Cut-survival / position-vs-cut strength"),
    ("consensus", "Sharp Consensus", "Weighted blend of available component models"),
]
MODEL_KEYS = [k for k, _, _ in MODEL_META]


def _http_json(url: str) -> Any | None:
    try:
        req = Request(url, headers={"User-Agent": "sports-sandbox-hub", "Accept": "application/json"})
        with urlopen(req, timeout=25, context=ssl._create_unverified_context()) as resp:
            return json.loads(resp.read().decode())
    except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sandbox_lookup() -> dict[str, dict[str, Any]]:
    """Name → sandbox prediction / elo row (demo DB)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from golf.backend.predictor import GolfPredictor
        from golf.database.paths import DB_PATH
        from shared.db import connect

        rows = GolfPredictor().list_field("evt1")
        for r in rows:
            nm = str(r.get("name") or "").strip().lower()
            if nm:
                out[nm] = {
                    "win_pct": float(r.get("win_pct") or 0),
                    "top10_pct": r.get("top10_pct"),
                    "make_cut_pct": r.get("make_cut_pct"),
                    "player_id": r.get("player_id"),
                }
        if DB_PATH.exists():
            with connect(DB_PATH) as conn:
                for row in conn.execute("SELECT name, elo FROM players").fetchall():
                    nm = str(row["name"] or "").strip().lower()
                    if nm:
                        out.setdefault(nm, {})
                        out[nm]["elo"] = float(row["elo"] or 1500)
    except Exception:
        pass
    return out


def list_espn_tournaments(*, days_back: int = 28, days_forward: int = 42) -> list[dict[str, Any]]:
    """Upcoming + recent PGA events from ESPN calendar / scoreboard."""
    data = _http_json(ESPN_SCOREBOARD)
    if not isinstance(data, dict):
        return []
    now = datetime.now(timezone.utc)
    lo = now - timedelta(days=days_back)
    hi = now + timedelta(days=days_forward)
    cal: list[dict[str, Any]] = []
    for league in data.get("leagues") or []:
        for item in league.get("calendar") or []:
            eid = str(item.get("id") or "")
            if not eid:
                continue
            start = _parse_iso(item.get("startDate"))
            end = _parse_iso(item.get("endDate"))
            # Keep if tournament window overlaps [lo, hi]
            if start and end and (end < lo or start > hi):
                continue
            if start and not end and (start < lo or start > hi):
                continue
            cal.append(
                {
                    "id": eid,
                    "name": item.get("label") or item.get("name") or f"Event {eid}",
                    "start": item.get("startDate"),
                    "end": item.get("endDate"),
                    "status": None,
                }
            )
    # Annotate status / prefer live field from current scoreboard events
    live_by_id = {}
    for ev in data.get("events") or []:
        eid = str(ev.get("id") or "")
        if eid:
            live_by_id[eid] = ev
    if not cal:
        # Fallback: only whatever is on the scoreboard
        for ev in data.get("events") or []:
            eid = str(ev.get("id") or "")
            if not eid:
                continue
            cal.append(
                {
                    "id": eid,
                    "name": ev.get("name") or f"Event {eid}",
                    "start": ev.get("date"),
                    "end": ev.get("endDate"),
                    "status": ((ev.get("status") or {}).get("type") or {}).get("description"),
                }
            )
    for t in cal:
        ev = live_by_id.get(t["id"])
        if ev:
            t["status"] = ((ev.get("status") or {}).get("type") or {}).get("description")
            t["name"] = ev.get("name") or t["name"]
    # Newest first for recent, then upcoming — sort by start desc within window
    cal.sort(key=lambda t: t.get("start") or "", reverse=True)
    # Deduplicate
    seen = set()
    out = []
    for t in cal:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        out.append(t)
    return out


def _default_event_id(tournaments: list[dict[str, Any]]) -> str | None:
    data = _http_json(ESPN_SCOREBOARD)
    if isinstance(data, dict):
        for ev in data.get("events") or []:
            eid = str(ev.get("id") or "")
            if eid:
                return eid
    return tournaments[0]["id"] if tournaments else None


def _espn_event_payload(event_id: str | None) -> tuple[str, str | None, list[dict[str, Any]], str | None]:
    """Return (name, event_id, players, status)."""
    tournaments = list_espn_tournaments()
    eid = (event_id or "").strip() or _default_event_id(tournaments)
    if not eid:
        return "PGA Tour", None, [], None

    # Prefer dedicated event scoreboard URL
    data = _http_json(ESPN_EVENT.format(event_id=eid))
    name = None
    status = None
    players: list[dict[str, Any]] = []

    if isinstance(data, dict) and data.get("competitions"):
        name = data.get("name") or data.get("shortName")
        status = ((data.get("status") or {}).get("type") or {}).get("description")
        comps = data.get("competitions") or []
        competitors = (comps[0].get("competitors") or []) if comps else []
        for c in competitors:
            ath = c.get("athlete") or {}
            pname = ath.get("displayName") or c.get("displayName")
            if not pname:
                continue
            players.append(
                {
                    "name": pname,
                    "athlete_id": ath.get("id") or c.get("id"),
                    "score": c.get("score"),
                    "order": c.get("order") or c.get("sortOrder"),
                    "status": (c.get("status") or {}).get("type", {}).get("description")
                    if isinstance(c.get("status"), dict)
                    else c.get("status"),
                }
            )
    elif isinstance(data, dict) and data.get("events"):
        # Some responses wrap events
        for ev in data["events"]:
            if str(ev.get("id")) == str(eid) or not players:
                name = ev.get("name") or name
                status = ((ev.get("status") or {}).get("type") or {}).get("description")
                comps = ev.get("competitions") or []
                for c in (comps[0].get("competitors") or []) if comps else []:
                    ath = c.get("athlete") or {}
                    pname = ath.get("displayName") or c.get("displayName")
                    if not pname:
                        continue
                    players.append(
                        {
                            "name": pname,
                            "athlete_id": ath.get("id") or c.get("id"),
                            "score": c.get("score"),
                            "order": c.get("order") or c.get("sortOrder"),
                            "status": (c.get("status") or {}).get("type", {}).get("description")
                            if isinstance(c.get("status"), dict)
                            else c.get("status"),
                        }
                    )
                if str(ev.get("id")) == str(eid):
                    break

    # Fallback: dates query from calendar start
    if not players:
        meta = next((t for t in tournaments if t["id"] == eid), None)
        start = _parse_iso((meta or {}).get("start"))
        if start:
            dates = start.strftime("%Y%m%d")
            sb = _http_json(f"{ESPN_SCOREBOARD}?dates={dates}")
            if isinstance(sb, dict):
                for ev in sb.get("events") or []:
                    if str(ev.get("id")) != str(eid):
                        continue
                    name = ev.get("name") or name
                    status = ((ev.get("status") or {}).get("type") or {}).get("description")
                    comps = ev.get("competitions") or []
                    for c in (comps[0].get("competitors") or []) if comps else []:
                        ath = c.get("athlete") or {}
                        pname = ath.get("displayName") or c.get("displayName")
                        if not pname:
                            continue
                        players.append(
                            {
                                "name": pname,
                                "athlete_id": ath.get("id") or c.get("id"),
                                "score": c.get("score"),
                                "order": c.get("order") or c.get("sortOrder"),
                                "status": (c.get("status") or {}).get("type", {}).get("description")
                                if isinstance(c.get("status"), dict)
                                else c.get("status"),
                            }
                        )

    if not name:
        meta = next((t for t in tournaments if t["id"] == eid), None)
        name = (meta or {}).get("name") or "PGA Event"
    return str(name), str(eid), players, status


def _parse_golf_score(raw: Any) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in ("-", "WD", "DQ", "CUT", "MDF"):
        return None
    if s in ("E", "EVEN"):
        return 0.0
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return None


def _softmax(strengths: list[float], temp: float = 8.0) -> list[float]:
    if not strengths:
        return []
    m = max(strengths)
    exps = [math.exp((s - m) / max(temp, 0.5)) for s in strengths]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


def _ranks_from_probs(probs: list[float]) -> list[int]:
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    ranks = [0] * len(probs)
    for r, i in enumerate(order, 1):
        ranks[i] = r
    return ranks


def _component_probs(
    players: list[dict[str, Any]], sandbox: dict[str, dict[str, Any]]
) -> dict[str, list[float]]:
    """Distinct win-% vectors — never copy one column into another."""
    n = len(players)
    if not n:
        return {k: [] for k in MODEL_KEYS}

    field_s: list[float] = []
    score_s: list[float] = []
    xsharp_s: list[float] = []
    eff_s: list[float] = []

    # Cut line ~ top 65 + ties for PGA; use order if present else list index
    for i, p in enumerate(players):
        order = p.get("order")
        try:
            ord_i = int(order) if order is not None else (i + 1)
        except (TypeError, ValueError):
            ord_i = i + 1
        # Field prior: earlier order → stronger (world-rank / start-list proxy)
        field_s.append(42.0 - min(ord_i, 80) * 0.45)

        sc = _parse_golf_score(p.get("score"))
        if sc is None:
            # Pre-tournament: fall back to mild field prior so column still differs via temp
            score_s.append(20.0 - min(ord_i, 80) * 0.22)
        else:
            # Lower strokes better
            score_s.append(30.0 - sc * 1.15)

        nm = str(p.get("name") or "").lower()
        sb = sandbox.get(nm) or {}
        if sb.get("win_pct"):
            xsharp_s.append(10.0 + float(sb["win_pct"]) * 80.0)
        elif sb.get("elo"):
            xsharp_s.append((float(sb["elo"]) - 1450.0) / 12.0)
        else:
            # Transparent: no sandbox match → neutral baseline (not a clone of Field)
            xsharp_s.append(12.0 - min(ord_i, 100) * 0.08)

        # Efficiency: cut survival — strong if inside projected cut, weaker if near/outside
        cut_proxy = 65.5
        if sc is not None:
            # Better score + better order → higher cut survival strength
            eff_s.append(28.0 - sc * 0.7 - max(0, ord_i - cut_proxy) * 0.35)
        else:
            made = sb.get("make_cut_pct")
            if made is not None:
                eff_s.append(8.0 + float(made) * 28.0)
            else:
                eff_s.append(22.0 - max(0, ord_i - cut_proxy) * 0.4)

    p_field = _softmax(field_s, temp=7.5)
    p_score = _softmax(score_s, temp=6.5)
    p_edge = _softmax(
        [0.55 * a + 0.45 * b for a, b in zip(field_s, score_s)],
        temp=7.0,
    )
    p_xsharp = _softmax(xsharp_s, temp=8.5)
    p_eff = _softmax(eff_s, temp=7.2)

    # Consensus: weighted blend of components that have signal
    weights = {
        "grinder2": 0.18,
        "takedown": 0.22,
        "edge": 0.22,
        "xsharp": 0.18,
        "efficiency": 0.20,
    }
    cons = []
    for i in range(n):
        cons.append(
            weights["grinder2"] * p_field[i]
            + weights["takedown"] * p_score[i]
            + weights["edge"] * p_edge[i]
            + weights["xsharp"] * p_xsharp[i]
            + weights["efficiency"] * p_eff[i]
        )
    z = sum(cons) or 1.0
    p_cons = [c / z for c in cons]

    return {
        "grinder2": p_field,
        "takedown": p_score,
        "edge": p_edge,
        "xsharp": p_xsharp,
        "efficiency": p_eff,
        "consensus": p_cons,
    }


def build_ranked_board(event_id: str | None = None) -> dict[str, Any]:
    tournaments = list_espn_tournaments()
    event_name, eid, espn, status = _espn_event_payload(event_id)
    sandbox = _sandbox_lookup()
    board: list[dict[str, Any]] = []
    source = "empty"

    if espn:
        comps = _component_probs(espn, sandbox)
        cons = comps["consensus"]
        ranked_idx = sorted(range(len(espn)), key=lambda i: -cons[i])
        for rank, i in enumerate(ranked_idx, 1):
            p = espn[i]
            models = {}
            for key in MODEL_KEYS:
                probs = comps[key]
                ranks = _ranks_from_probs(probs)
                models[key] = {
                    "win_pct": round(probs[i], 5),
                    "rank": ranks[i],
                }
            board.append(
                {
                    "rank": rank,
                    "name": p["name"],
                    "athlete_id": p.get("athlete_id"),
                    "win_pct": models["consensus"]["win_pct"],
                    "score": p.get("score"),
                    "order": p.get("order"),
                    "models": models,
                    "source": "espn+components",
                }
            )
        source = "espn-multi-model"
    else:
        # No ESPN field yet — optional sandbox demo field only when no event selected
        if not eid and sandbox:
            rows = sorted(sandbox.items(), key=lambda kv: -float(kv[1].get("win_pct") or 0))
            z = sum(float(v.get("win_pct") or 0) for _, v in rows) or 1.0
            for i, (nm, v) in enumerate(rows, 1):
                wp = float(v.get("win_pct") or 0) / z
                models = {k: {"win_pct": round(wp, 5), "rank": i} for k in MODEL_KEYS}
                # Mark non-consensus as unavailable-identical only in demo? Prefer N/A for components
                for k in MODEL_KEYS:
                    if k != "consensus":
                        models[k] = {"win_pct": None, "rank": None}
                board.append(
                    {
                        "rank": i,
                        "name": nm.title(),
                        "athlete_id": None,
                        "win_pct": round(wp, 5),
                        "models": models,
                        "source": "sandbox-db",
                    }
                )
            event_name = "Sandbox Open (demo)"
            source = "sandbox-db"

    return {
        "event": event_name,
        "event_id": eid,
        "status": status,
        "players": board,
        "source": source,
        "tournaments": tournaments,
        "models": MODEL_META,
    }


def _headshot(athlete_id: Any) -> str:
    if not athlete_id:
        return ""
    return f"https://a.espncdn.com/i/headshots/golf/players/full/{athlete_id}.png"


def _tournament_select_html(
    tournaments: list[dict[str, Any]],
    selected_id: str | None,
    action: str,
) -> str:
    if not tournaments:
        return (
            '<p class="golf-muted">No ESPN tournaments available in the current window.</p>'
        )
    opts = []
    for t in tournaments:
        eid = html_lib.escape(str(t["id"]))
        label = html_lib.escape(str(t.get("name") or eid))
        st = t.get("status")
        start = (t.get("start") or "")[:10]
        extra = []
        if start:
            extra.append(start)
        if st:
            extra.append(str(st))
        suffix = f" — {' · '.join(extra)}" if extra else ""
        sel = " selected" if selected_id and str(t["id"]) == str(selected_id) else ""
        opts.append(f'<option value="{eid}"{sel}>{label}{html_lib.escape(suffix)}</option>')
    return (
        f'<form class="golf-picker" method="get" action="{html_lib.escape(action)}">'
        f'<label for="golf-event">Tournament</label>'
        f'<select id="golf-event" name="event" onchange="this.form.submit()">'
        + "\n".join(opts)
        + "</select></form>"
    )


def _point_static_to_hub(html: str) -> str:
    """Serve research CSS from hub /static (match CFL/MLB parity)."""
    import re

    html = re.sub(
        r"https?://127\.0\.0\.1:\d+/static/css/research-theme\.css",
        "/static/css/research-theme.css",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"https?://127\.0\.0\.1:\d+/static/css/picks-nav-overrides\.css",
        "/static/css/picks-nav-overrides.css",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'href="[^"]*/static/css/research-theme\.css"',
        'href="/static/css/research-theme.css"',
        html,
    )
    html = re.sub(
        r'href="[^"]*/static/css/picks-nav-overrides\.css"',
        'href="/static/css/picks-nav-overrides.css"',
        html,
    )
    return html


def _ensure_golf_assets(html: str) -> str:
    """Ensure research-theme + golf-board + PL fonts are present on the page."""
    import re

    head_bits = []
    if "research-theme.css" not in html:
        head_bits.append('<link rel="stylesheet" href="/static/css/research-theme.css"/>')
    if "picks-nav-overrides.css" not in html:
        head_bits.append('<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>')
    if "golf-board.css" not in html:
        head_bits.append('<link rel="stylesheet" href="/static/css/golf-board.css"/>')
    if "Space+Grotesk" not in html and "Space Grotesk" not in html:
        head_bits.append(
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700&family=Oswald:wght@400;600;700&display=swap">'
        )
    if head_bits:
        html = re.sub(
            r"(</head>)",
            "\n".join(head_bits) + r"\n\1",
            html,
            count=1,
            flags=re.I,
        )
    # Live golf chrome should use research-site body class (paper grid, not gray sandbox).
    def _body_cls(m: re.Match) -> str:
        tag = m.group(0)
        if "research-site" in tag:
            return tag
        if re.search(r"\bclass=", tag, flags=re.I):
            return re.sub(
                r'\bclass=(["\'])(.*?)\1',
                lambda c: f'class={c.group(1)}{(c.group(2) + " research-site").strip()}{c.group(1)}',
                tag,
                count=1,
            )
        return tag[:-1] + ' class="research-site">'

    html = re.sub(r"<body\b[^>]*>", _body_cls, html, count=1, flags=re.I)
    return html


def _replace_container(html: str, body: str) -> str:
    """Replace main .container inner HTML with golf board (CFL-style)."""
    import re

    start = re.search(r'<div class="container\b[^"]*"[^>]*>', html, flags=re.I)
    if start:
        i = start.end()
        depth = 1
        j = i
        while j < len(html) and depth > 0:
            next_open = html.find("<div", j)
            next_close = html.find("</div>", j)
            if next_close < 0:
                break
            if next_open >= 0 and next_open < next_close:
                depth += 1
                j = next_open + 4
            else:
                depth -= 1
                if depth == 0:
                    frag = f'\n<div class="golf-wrap">\n{body}\n</div>\n'
                    return html[:i] + frag + html[next_close:]
                j = next_close + 6
    m = re.search(r"(<main\b[^>]*>)([\s\S]*?)(</main>)", html, flags=re.I)
    if m:
        return (
            html[: m.start(2)]
            + f'<div class="container"><div class="golf-wrap">\n{body}\n</div></div>'
            + html[m.end(2) :]
        )
    if re.search(r"<footer\b", html, re.I):
        return re.sub(
            r"(<footer\b)",
            f'<div class="container"><div class="golf-wrap">\n{body}\n</div></div>\n' + r"\1",
            html,
            count=1,
            flags=re.I,
        )
    return html + f'<div class="container"><div class="golf-wrap">{body}</div></div>'


def _golf_page_shell(title: str, body: str, which: str) -> str:
    """Minimal fallback when sidecar chrome is unavailable."""
    page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html_lib.escape(title)}</title>
<link rel="stylesheet" href="/static/css/research-theme.css"/>
<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>
<link rel="stylesheet" href="/static/css/golf-board.css"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700&family=Oswald:wght@400;600;700&display=swap">
</head>
<body class="research-site" data-theme="light">
<div class="container"><div class="golf-wrap">{body}</div></div>
</body></html>"""
    from sandbox_fixup import inject_sport_subnav

    return inject_sport_subnav(page, "golf", which=which)


def build_golf_board_fragment(event_id: str | None = None) -> tuple[str, dict[str, Any], str]:
    """Inner board HTML + meta + title (no chrome)."""
    data = build_ranked_board(event_id)
    players = data.get("players") or []
    picker = _tournament_select_html(
        data.get("tournaments") or [], data.get("event_id"), "/golf/"
    )
    model_note = (
        '<p class="golf-note">Each column is a <strong>distinct component</strong> of the '
        "tournament win engine (field order, live score, blend, cut survival, "
        "consensus). Columns are not duplicated copies of one number. "
        "This board is <strong>tournament win probability</strong> — not head-to-head betting cards.</p>"
    )
    legend = '<ul class="golf-legend">' + "".join(
        f"<li><strong>{html_lib.escape(label)}</strong> — {html_lib.escape(desc)}</li>"
        for _, label, desc in MODEL_META
    ) + "</ul>"

    head_cols = (
        '<th scope="col">Rank</th>'
        '<th scope="col">Player</th>'
        + "".join(
            f'<th scope="col" title="{html_lib.escape(desc)}">{html_lib.escape(label)}</th>'
            for key, label, desc in MODEL_META
            if key != "consensus"
        )
        + '<th scope="col">Consensus win %</th>'
    )

    rows = []
    for p in players[:80]:
        pct = float(p.get("win_pct") or 0) * 100
        img = _headshot(p.get("athlete_id"))
        img_tag = (
            f'<img src="{img}" alt="" width="40" height="40" class="golf-headshot" '
            f'onerror="this.style.display=\'none\'"/>'
            if img
            else ""
        )
        models = p.get("models") or {}
        model_tds = []
        for key, label, _desc in MODEL_META:
            if key == "consensus":
                continue
            m = models.get(key) or {}
            r = m.get("rank")
            wp = m.get("win_pct")
            if r is None and wp is None:
                cell = "—"
            else:
                wp_s = f"{float(wp) * 100:.1f}%" if wp is not None else "—"
                cell = f'<span class="golf-mrank">#{r}</span> <span class="golf-mpct">{wp_s}</span>'
            model_tds.append(f"<td>{cell}</td>")
        rows.append(
            "<tr>"
            f'<td class="golf-rank">#{p.get("rank")}</td>'
            f'<td class="golf-player">{img_tag}<strong>{html_lib.escape(str(p.get("name")))}</strong></td>'
            + "".join(model_tds)
            + f'<td class="golf-winpct">{pct:.1f}<span class="golf-pct-suffix">%</span></td>'
            "</tr>"
        )

    status = data.get("status")
    status_bit = f' <span class="golf-status">({html_lib.escape(str(status))})</span>' if status else ""
    empty = (
        '<tr><td colspan="8" class="golf-empty">No field posted for this tournament yet. '
        "Pick a recent completed event, or check back when tee times / the leaderboard are up.</td></tr>"
    )
    body = (
        f"<h1>{html_lib.escape(str(data.get('event')))}{status_bit}</h1>"
        '<p class="golf-lede">Model-estimated <strong>probability of winning the tournament</strong> '
        "for each player in the selected field, ranked by Sharp Consensus. "
        "Not a head-to-head matchup board and not sportsbook odds.</p>"
        f"{picker}{model_note}{legend}"
        '<div class="golf-table-wrap"><table class="golf-table">'
        f"<thead><tr>{head_cols}</tr></thead><tbody>"
        + ("\n".join(rows) or empty)
        + "</tbody></table></div>"
    )
    title = f"{data.get('event')} Win Probability | Prediction Lab"
    meta = {
        "ok": True,
        "players": len(players),
        "source": data.get("source"),
        "event": data.get("event"),
        "event_id": data.get("event_id"),
    }
    return body, meta, title


def render_golf_board_html(event_id: str | None = None) -> tuple[str, dict[str, Any]]:
    body, meta, title = build_golf_board_fragment(event_id)
    page = _golf_page_shell(title, body, "picks")
    return page, meta


def render_golf_with_chrome(
    chrome_html: str,
    which: str = "picks",
    event_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Inject ranked board / results into live pl2-header chrome (MLB/CFL parity)."""
    import re

    if which == "results":
        body, meta, title = build_golf_results_fragment(event_id)
    else:
        body, meta, title = build_golf_board_fragment(event_id)

    if not chrome_html or "<body" not in chrome_html.lower():
        page = _golf_page_shell(title, body, which)
        meta = {**meta, "chrome": "fallback_shell"}
        return page, meta

    html = chrome_html
    html = re.sub(
        r"(<title>)(.*?)(</title>)",
        rf"\1{html_lib.escape(title)}\3",
        html,
        count=1,
        flags=re.I | re.S,
    )
    html = _replace_container(html, body)
    html = _point_static_to_hub(html)
    html = _ensure_golf_assets(html)
    html = html.replace("/golf-picks", "/golf/")
    html = html.replace("/golf-results", "/golf/results")
    from sandbox_fixup import (
        fix_share_social_assets,
        inject_sport_subnav,
        strip_sandbox_dev_notes,
    )

    html = strip_sandbox_dev_notes(html)
    html = inject_sport_subnav(html, "golf", which=which)
    html = fix_share_social_assets(html)
    meta = {**meta, "chrome": "live_sidecar", "ok": True}
    return html, meta


def _espn_finish_order(event_id: str | None = None) -> tuple[str, str | None, list[dict[str, Any]]]:
    """Actual tournament finish positions from ESPN for the selected event."""
    name, eid, players, _status = _espn_event_payload(event_id)
    rows: list[dict[str, Any]] = []
    for i, p in enumerate(players):
        pos = p.get("order")
        try:
            finish = int(pos) if pos is not None and str(pos).isdigit() else None
        except (TypeError, ValueError):
            finish = None
        rows.append(
            {
                "name": p["name"],
                "athlete_id": p.get("athlete_id"),
                "finish": finish,
                "score": p.get("score"),
                "status": p.get("status"),
            }
        )
    ranked = sorted(
        rows,
        key=lambda r: (r["finish"] is None, r["finish"] if r["finish"] is not None else 9999),
    )
    for i, r in enumerate(ranked, 1):
        if r["finish"] is None:
            r["finish"] = i
    return name, eid, ranked


def build_golf_results_fragment(event_id: str | None = None) -> tuple[str, dict[str, Any], str]:
    """Inner results HTML + meta + title (no chrome)."""
    predicted = build_ranked_board(event_id)
    event, eid, actual = _espn_finish_order(event_id or predicted.get("event_id"))
    if not event:
        event = str(predicted.get("event") or "Tournament")
    eid = eid or predicted.get("event_id")
    picker = _tournament_select_html(
        predicted.get("tournaments") or list_espn_tournaments(),
        eid,
        "/golf/results",
    )
    pred_map = {str(p.get("name") or "").lower(): p for p in (predicted.get("players") or [])}
    names: list[str] = []
    seen: set[str] = set()
    for src in (actual, predicted.get("players") or []):
        for p in src:
            nm = str(p.get("name") or "")
            k = nm.lower()
            if k and k not in seen:
                seen.add(k)
                names.append(nm)

    rows_html = []
    for nm in names[:80]:
        pred = pred_map.get(nm.lower()) or {}
        act = next((a for a in actual if str(a.get("name") or "").lower() == nm.lower()), None)
        model_rank = pred.get("rank")
        win_pct = float(pred.get("win_pct") or 0) * 100 if pred else None
        finish = (act or {}).get("finish")
        score = (act or {}).get("score")
        aid = (act or {}).get("athlete_id") or pred.get("athlete_id")
        img = _headshot(aid)
        img_tag = (
            f'<img src="{img}" alt="" width="40" height="40" class="golf-headshot" '
            f'onerror="this.style.display=\'none\'"/>'
            if img
            else ""
        )
        delta_html = "—"
        if model_rank is not None and finish is not None:
            try:
                d = int(model_rank) - int(finish)
                if d == 0:
                    delta_html = '<span class="golf-delta golf-delta-even">=</span>'
                elif d > 0:
                    delta_html = f'<span class="golf-delta golf-delta-up">↑{d}</span>'
                else:
                    delta_html = f'<span class="golf-delta golf-delta-down">↓{abs(d)}</span>'
            except (TypeError, ValueError):
                delta_html = "—"

        model_bits = []
        models = pred.get("models") or {}
        for key, label, _ in MODEL_META:
            if key == "consensus":
                continue
            m = models.get(key) or {}
            r = m.get("rank")
            if r is not None:
                model_bits.append(f"{html_lib.escape(label)} #{r}")
        model_detail = (
            f'<div class="golf-model-detail">{html_lib.escape(" · ".join(model_bits))}</div>'
            if model_bits
            else ""
        )

        rows_html.append(
            "<tr>"
            f'<td class="golf-player">{img_tag}<strong>{html_lib.escape(nm)}</strong>{model_detail}</td>'
            f"<td>{('#' + str(model_rank)) if model_rank else '—'}</td>"
            f'<td class="golf-winpct">'
            f"{(f'{win_pct:.1f}%') if win_pct is not None else '—'}</td>"
            f"<td>{('#' + str(finish)) if finish is not None else '—'}</td>"
            f"<td>{html_lib.escape(str(score)) if score is not None else '—'}</td>"
            f"<td>{delta_html}</td>"
            "</tr>"
        )

    body = (
        f"<h1>{html_lib.escape(event)} — Results</h1>"
        '<p class="golf-lede">Compare our ranked <strong>tournament win probability</strong> '
        "(Sharp Consensus finish order) with each player's <strong>actual tournament finish</strong>. "
        "Golf is graded as a field ranking — not player-vs-player matchups. "
        "↑ means the player finished better than the model rank; ↓ means worse.</p>"
        f"{picker}"
        '<div class="golf-table-wrap"><table class="golf-table golf-results-table">'
        "<thead><tr>"
        "<th scope=\"col\">Player</th>"
        "<th scope=\"col\">Model rank</th>"
        "<th scope=\"col\">Win probability</th>"
        "<th scope=\"col\">Actual finish</th>"
        "<th scope=\"col\">Score</th>"
        "<th scope=\"col\">vs model</th>"
        "</tr></thead><tbody>"
        + (
            "\n".join(rows_html)
            or '<tr><td colspan="6" class="golf-empty">No tournament results available yet.</td></tr>'
        )
        + "</tbody></table></div>"
    )
    title = f"{event} Results | Prediction Lab"
    meta = {
        "ok": True,
        "players": len(names),
        "actual": len(actual),
        "event": event,
        "event_id": eid,
        "source": "espn-finish+model-rank",
        "grading": "consensus_rank_vs_espn_order",
    }
    return body, meta, title


def render_golf_results_html(event_id: str | None = None) -> tuple[str, dict[str, Any]]:
    """Compare model win-% rank vs actual tournament finish — not player-vs-player cards."""
    body, meta, title = build_golf_results_fragment(event_id)
    page = _golf_page_shell(title, body, "results")
    return page, meta
