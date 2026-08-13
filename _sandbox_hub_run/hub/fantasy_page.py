#!/usr/bin/env python3
"""Fantasy sandbox pages for Sports Sandbox Hub (:5081 only). Multi-sport + section IA."""
from __future__ import annotations

import html as html_lib
import importlib.util
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

FANTASY_ISO = Path.home() / "Documents/Personal/fantasy"
SPORTS = ("nfl", "mlb", "nba", "nhl")
SPORT_LABELS = {"nfl": "NFL", "mlb": "MLB", "nba": "NBA", "nhl": "NHL"}

# Path sections (not query tools)
SECTIONS = (
    ("rankings", "Rankings", "In-season player rankings"),
    ("start-sit", "Start/Sit", "Compare two players"),
    ("waivers", "Waiver Wire", "Pickup targets"),
    ("matchups", "Matchups", "Defense vs position"),
    ("draft", "Draft", "Season draft board"),
    ("sleepers", "Sleepers", "Model upside list"),
)
SECTION_KEYS = {s[0] for s in SECTIONS}
_PIPE = None


def _pipeline():
    global _PIPE
    if _PIPE is not None:
        return _PIPE
    path = FANTASY_ISO / "engine" / "pipeline.py"
    name = "fantasy_iso_pipeline"
    if name in sys.modules:
        _PIPE = sys.modules[name]
        return _PIPE
    root = str(FANTASY_ISO.resolve())
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    _PIPE = mod
    return mod


def _sync(sport: str = "nfl") -> dict[str, Any]:
    return _pipeline().sync_sport(sport, week=1)


def _esc(v: Any) -> str:
    return html_lib.escape(str(v if v is not None else ""))


def _chrome_header(*, active_sport: str | None = None, active_section: str | None = None) -> str:
    """Prediction Lab–style header + Fantasy hamburger mega-menu."""
    sport_btns = []
    for s in SPORTS:
        cls = " active" if s == active_sport else ""
        sport_btns.append(
            f'<button type="button" class="fan-mega-sport{cls}" data-sport="{s}" '
            f'aria-pressed="{"true" if s == active_sport else "false"}">{SPORT_LABELS[s]}</button>'
        )

    # Build per-sport section panels for the mega menu
    panels = []
    for s in SPORTS:
        links = []
        for key, label, blurb in SECTIONS:
            href = f"/fantasy/{s}/{key}"
            active = " active" if s == active_sport and key == active_section else ""
            links.append(
                f'<a class="fan-mega-link{active}" href="{href}">'
                f"<strong>{_esc(label)}</strong><span>{_esc(blurb)}</span></a>"
            )
        links.append(
            f'<a class="fan-mega-link" href="/fantasy/{s}"><strong>Sport home</strong>'
            f"<span>All {SPORT_LABELS[s]} tools</span></a>"
        )
        hidden = "" if s == (active_sport or "nfl") else " hidden"
        panels.append(
            f'<div class="fan-mega-panel{hidden}" data-panel="{s}">'
            f'<div class="fan-mega-col-title">Sections · {SPORT_LABELS[s]}</div>'
            f'{"".join(links)}</div>'
        )

    return f"""
<header class="pl2-header fan-header">
  <button type="button" class="pl2-burger hamburger" id="fanBurger" aria-label="Open menu" aria-expanded="false" aria-controls="fanDrawer">
    <span></span><span></span><span></span>
  </button>
  <a class="pl2-brand fan-brand" href="/">
    <b>Prediction Lab</b>
    <span>Fantasy · Sandbox</span>
  </a>
  <nav class="pl2-nav fan-desktop-nav" aria-label="Fantasy sports">
    <a href="/fantasy/">Hub</a>
    {"".join(f'<a href="/fantasy/{s}" class="{"is-active" if s == active_sport else ""}">{SPORT_LABELS[s]}</a>' for s in SPORTS)}
  </nav>
  <div class="pl2-header-actions">
    <a class="pl2-cta fan-cta" href="/fantasy/">Fantasy Home</a>
  </div>
</header>
<div class="tv-overlay" id="fanOverlay" hidden></div>
<aside class="tv-drawer fan-drawer" id="fanDrawer" aria-hidden="true">
  <div class="tv-drawer-header">
    <div class="tv-drawer-title">Fantasy</div>
    <div class="tv-header-btns">
      <button type="button" class="tv-close-btn" id="fanDrawerClose" aria-label="Close">×</button>
    </div>
  </div>
  <div class="fan-mega">
    <div class="fan-mega-sports" role="tablist" aria-label="Sports">
      {"".join(sport_btns)}
    </div>
    <div class="fan-mega-sections">
      <a class="fan-mega-link" href="/fantasy/"><strong>Fantasy Dashboard</strong><span>All sports overview</span></a>
      {"".join(panels)}
    </div>
  </div>
  <div class="tv-menu-list" style="border-top:1px solid var(--pl-line,#e2e8f0);padding-top:8px">
    <a class="tv-sub-link" href="/">← Sports Sandbox Hub</a>
    <a class="tv-sub-link" href="/cfl/">CFL</a>
    <a class="tv-sub-link" href="/golf/">Golf</a>
    <a class="tv-sub-link" href="/mlb/">MLB Picks</a>
  </div>
</aside>
"""


def _sport_tabs(sport: str) -> str:
    parts = [f'<a href="/fantasy/" class="fan-tab">Dashboard</a>']
    for s in SPORTS:
        cls = "fan-tab active" if s == sport else "fan-tab"
        parts.append(f'<a class="{cls}" href="/fantasy/{s}">{SPORT_LABELS[s]}</a>')
    return '<div class="fan-sport-tabs">' + "".join(parts) + "</div>"


def _section_nav(sport: str, section: str) -> str:
    parts = []
    for key, label, _ in SECTIONS:
        cls = " active" if key == section else ""
        parts.append(f'<a class="fan-sec{cls}" href="/fantasy/{sport}/{key}">{_esc(label)}</a>')
    return '<div class="fan-section-nav">' + "".join(parts) + "</div>"


def _rank_rows(items: list[dict[str, Any]], *, sport: str, mode: str = "inseason") -> str:
    out = []
    for i, r in enumerate(items):
        pid = _esc(r.get("player_id") or "")
        trend = _esc(r.get("form_trend") or "—")
        mrate = r.get("matchup_rating")
        mrate_s = f"{float(mrate):.1f}" if mrate is not None else "—"
        rank = r.get("draft_rank") or r.get("rank") or (i + 1)
        if mode == "draft":
            pts = float(r.get("season_projected") or r.get("projected_points") or 0)
            pts_label = f"{pts:.1f}"
            extra = f"<td>{float(r.get('weekly_projected') or r.get('projected_points') or 0):.1f}/wk</td>"
        elif mode == "sleepers":
            pts = float(r.get("projected_points") or 0)
            pts_label = f"{pts:.1f}"
            extra = (
                f"<td>{float(r.get('ceiling_points') or 0):.1f}</td>"
                f"<td>{float(r.get('upside_ratio') or 0):.2f}×</td>"
            )
        else:
            pts_label = f"{float(r.get('projected_points') or 0):.1f}"
            extra = f"<td>{trend}</td><td>{mrate_s}</td>"
        out.append(
            "<tr>"
            f"<td>{rank}</td>"
            f"<td><a href='/fantasy/player/{quote(pid)}?sport={sport}'><strong>"
            f"{_esc(r.get('name'))}</strong></a></td>"
            f"<td>{_esc(r.get('position') or '')}</td>"
            f"<td>{_esc(r.get('team') or '')}</td>"
            f"<td><strong>{pts_label}</strong></td>"
            f"{extra}"
            "</tr>"
        )
    cols = 7 if mode != "sleepers" else 7
    return "\n".join(out) or f"<tr><td colspan={cols}>No players</td></tr>"


def _rank_table(items: list[dict[str, Any]], *, sport: str, mode: str = "inseason") -> str:
    if mode == "draft":
        head = "<th>#</th><th>Player</th><th>Pos</th><th>Team</th><th>Season Proj</th><th>Weekly</th>"
    elif mode == "sleepers":
        head = "<th>#</th><th>Player</th><th>Pos</th><th>Team</th><th>Proj</th><th>Ceiling</th><th>Upside</th>"
    else:
        head = "<th>#</th><th>Player</th><th>Pos</th><th>Team</th><th>Proj</th><th>Form</th><th>Matchup</th>"
    return f"""
<table class="fan-table">
<thead><tr>{head}</tr></thead>
<tbody>{_rank_rows(items, sport=sport, mode=mode)}</tbody>
</table>"""


def _start_sit_form(sport: str, *, a: str = "", b: str = "", result_html: str = "") -> str:
    pipe = _pipeline()
    da, db = pipe.default_compare(sport)
    return f"""
<div class="fan-card">
  <h2>Start / Sit analyzer</h2>
  <p class="tiny">Compare two players by projected fantasy points for this sport.</p>
  {result_html}
  <form method="get" action="/fantasy/{sport}/start-sit" class="fan-form">
    <label>Player A<br/><input name="a" value="{_esc(a or da)}" placeholder="{_esc(da)}"/></label>
    <label>Player B<br/><input name="b" value="{_esc(b or db)}" placeholder="{_esc(db)}"/></label>
    <button type="submit" class="fan-btn">Compare</button>
  </form>
</div>
"""


def _section_body(sport: str, section: str, *, a: str | None = None, b: str | None = None) -> tuple[str, dict[str, Any]]:
    pipe = _pipeline()
    meta = _sync(sport)
    label = SPORT_LABELS[sport]
    scoring = _esc(pipe.scoring_note(sport))
    mpos = pipe.default_matchup_position(sport)
    extra: dict[str, Any] = {}

    if section == "rankings":
        ranks = pipe.list_rankings(sport=sport, week=1)
        body = f"""
<div class="fan-card">
  <h2>In-season rankings · {label}</h2>
  <p class="tiny">{scoring}</p>
  {_rank_table(ranks, sport=sport)}
</div>"""
        extra["rankings"] = len(ranks)

    elif section == "start-sit":
        result_html = ""
        if a or b:
            da, db = pipe.default_compare(sport)
            result = pipe.start_sit(a or da, b or db, sport=sport, week=1)
            if result.get("ok"):
                result_html = f"""
<div class="fan-result">
  <p><strong>Start:</strong> {_esc(result['start'].get('name'))}
     ({float(result['start'].get('projected_points') or 0):.1f})</p>
  <p><strong>Sit:</strong> {_esc(result['sit'].get('name'))}
     ({float(result['sit'].get('projected_points') or 0):.1f})</p>
  <p class="tiny">{_esc(result.get('explanation'))}</p>
</div>"""
            else:
                result_html = f"<p class='fan-err'>Start/Sit: {_esc(result.get('error'))}</p>"
        body = _start_sit_form(sport, a=a or "", b=b or "", result_html=result_html)

    elif section == "waivers":
        waivers = pipe.waiver_wire(sport=sport, week=1, limit=15)
        lis = "".join(
            f"<li><a href='/fantasy/player/{quote(str(w.get('player_id')))}?sport={sport}'>"
            f"{_esc(w.get('name'))}</a> "
            f"({_esc(w.get('position'))} · {float(w.get('projected_points') or 0):.1f} · "
            f"{_esc(w.get('form_trend') or '')})</li>"
            for w in waivers
        ) or "<li>None</li>"
        body = f"""
<div class="fan-card">
  <h2>Waiver wire · {label}</h2>
  <p class="tiny">Mid-tier projections by position — availability proxy from our board (not ownership %).</p>
  <ul class="fan-list">{lis}</ul>
</div>"""
        extra["waivers"] = len(waivers)

    elif section == "matchups":
        matchups = pipe.list_matchups(sport=sport, week=1, position=mpos)[:16]
        rows = "".join(
            "<tr>"
            f"<td>{_esc(m.get('team'))}</td>"
            f"<td>{_esc(m.get('opponent'))}</td>"
            f"<td>{_esc(m.get('position'))}</td>"
            f"<td>#{m.get('defense_rank')}</td>"
            f"<td>{float(m.get('fantasy_points_allowed') or 0):.1f}</td>"
            f"<td>{_esc(m.get('note') or '')}</td>"
            "</tr>"
            for m in matchups
        ) or "<tr><td colspan=6>No matchups</td></tr>"
        nhl = (
            "<p class='tiny'>NHL: F/D skater groups and G (goalie) tracked separately.</p>"
            if sport == "nhl"
            else ""
        )
        body = f"""
<div class="fan-card">
  <h2>Matchup analysis · {label} ({_esc(mpos)})</h2>
  <p class="tiny">Defense vs position — fantasy points allowed proxy (sandbox ranks).</p>
  {nhl}
  <table class="fan-table"><thead><tr>
    <th>Team</th><th>Opp</th><th>Pos</th><th>Rank</th><th>FPA</th><th>Note</th>
  </tr></thead><tbody>{rows}</tbody></table>
</div>"""
        extra["matchups"] = len(matchups)

    elif section == "draft":
        board = pipe.draft_board(sport=sport, week=1)
        body = f"""
<div class="fan-card">
  <h2>Draft rankings · {label}</h2>
  <p class="tiny">Season draft board from the same projection engine (weekly × season factor).
     Not ADP / expert consensus.</p>
  <p class="tiny">{scoring}</p>
  {_rank_table(board, sport=sport, mode="draft")}
</div>"""
        extra["draft"] = len(board)

    elif section == "sleepers":
        sleepers = pipe.sleeper_board(sport=sport, week=1, limit=15)
        body = f"""
<div class="fan-card">
  <h2>Sleepers · {label}</h2>
  <p class="tiny">Model-derived upside: mid-tier players ranked by ceiling ÷ projected.
     Not expert consensus.</p>
  {_rank_table(sleepers, sport=sport, mode="sleepers")}
</div>"""
        extra["sleepers"] = len(sleepers)

    else:
        body = "<div class='fan-card'><p>Unknown section</p></div>"

    return body, {**meta, **extra}


def render_dashboard() -> tuple[str, dict[str, Any]]:
    pipe = _pipeline()
    metas = {}
    cards = []
    total = 0
    for sport in SPORTS:
        meta = _sync(sport)
        metas[sport] = meta
        total += int(meta.get("players") or 0)
        ranks = pipe.list_rankings(sport=sport, week=1)[:6]
        label = SPORT_LABELS[sport]
        sec_links = " · ".join(
            f'<a href="/fantasy/{sport}/{k}">{lab}</a>' for k, lab, _ in SECTIONS[:5]
        )
        cards.append(
            f"""
<article class="fan-card">
  <h2><a href="/fantasy/{sport}">{label}</a></h2>
  <p class="tiny">{_esc(pipe.scoring_note(sport))}</p>
  {_rank_table(ranks, sport=sport)}
  <p class="fan-card-links">{sec_links}</p>
</article>"""
        )

    body = f"""
<div class="fan-wrap">
  <p class="fan-kicker">Fantasy Lab</p>
  <h1>Rankings, Start/Sit, Waivers &amp; more</h1>
  <p class="sub">NFL · MLB · NBA · NHL — open the hamburger for the full Fantasy menu (sports × sections).</p>
  {_sport_tabs("")}
  <div class="fan-grid">{"".join(cards)}</div>
  <p class="meta">players≈{total} · sandbox only · not betting picks</p>
</div>
"""
    return _page("Fantasy · Prediction Lab Sandbox", body, active_sport=None, active_section=None), {
        "ok": True,
        "players": total,
        "sports": metas,
    }


def render_sport_home(sport: str) -> tuple[str, dict[str, Any]]:
    sport = sport.lower().strip()
    if sport not in SPORTS:
        return _not_found()
    meta = _sync(sport)
    pipe = _pipeline()
    label = SPORT_LABELS[sport]
    tiles = []
    for key, lab, blurb in SECTIONS:
        tiles.append(
            f'<a class="fan-tile" href="/fantasy/{sport}/{key}">'
            f"<strong>{_esc(lab)}</strong><span>{_esc(blurb)}</span></a>"
        )
    ranks = pipe.list_rankings(sport=sport, week=1)[:12]
    body = f"""
<div class="fan-wrap">
  <p class="fan-kicker">{label} Fantasy</p>
  <h1>{label} tools</h1>
  <p class="sub">{_esc(pipe.scoring_note(sport))}</p>
  {_sport_tabs(sport)}
  {_section_nav(sport, "")}
  <div class="fan-tiles">{"".join(tiles)}</div>
  <div class="fan-card" style="margin-top:16px">
    <h2>Featured rankings</h2>
    {_rank_table(ranks, sport=sport)}
    <p class="fan-card-links"><a href="/fantasy/{sport}/rankings">Full rankings →</a></p>
  </div>
  <p class="meta">players={meta.get('players')} · source={_esc(meta.get('source'))}</p>
</div>
"""
    return _page(f"{label} Fantasy · Sandbox", body, active_sport=sport, active_section=None), {
        "ok": True,
        **meta,
    }


def render_section(
    sport: str,
    section: str,
    *,
    a: str | None = None,
    b: str | None = None,
) -> tuple[str, dict[str, Any]]:
    sport = sport.lower().strip()
    section = section.lower().strip()
    # legacy aliases
    aliases = {"startsit": "start-sit", "start_sit": "start-sit", "waiver": "waivers", "matchup": "matchups"}
    section = aliases.get(section, section)
    if sport not in SPORTS or section not in SECTION_KEYS:
        return _not_found()

    inner, meta = _section_body(sport, section, a=a, b=b)
    label = SPORT_LABELS[sport]
    sec_label = next(x[1] for x in SECTIONS if x[0] == section)
    body = f"""
<div class="fan-wrap">
  <p class="fan-kicker">{label} · {sec_label}</p>
  <h1>{sec_label}</h1>
  <p class="sub">Use sport tabs and section nav — or open the hamburger for the full Fantasy menu.</p>
  {_sport_tabs(sport)}
  {_section_nav(sport, section)}
  {inner}
  <p class="meta">players={meta.get('players')} · cached={meta.get('cached')}</p>
</div>
"""
    return _page(
        f"{label} {sec_label} · Fantasy",
        body,
        active_sport=sport,
        active_section=section,
    ), {"ok": True, **meta}


def render_sport(
    sport: str,
    *,
    tool: str | None = None,
    a: str | None = None,
    b: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Backward compatible: ?tool= maps to sections; bare sport → sport home."""
    if tool:
        aliases = {
            "startsit": "start-sit",
            "waivers": "waivers",
            "matchups": "matchups",
            "rankings": "rankings",
            "draft": "draft",
            "sleepers": "sleepers",
        }
        return render_section(sport, aliases.get(tool, tool), a=a, b=b)
    if a or b:
        return render_section(sport, "start-sit", a=a, b=b)
    return render_sport_home(sport)


def render_nfl(*, tool: str | None = None, a: str | None = None, b: str | None = None) -> tuple[str, dict[str, Any]]:
    return render_sport("nfl", tool=tool, a=a, b=b)


def render_player(player_id: str, *, sport: str | None = None) -> tuple[str, dict[str, Any]]:
    pipe = _pipeline()
    inferred = None
    for s in SPORTS:
        if player_id.startswith(f"{s}_"):
            inferred = s
            break
    sport = (sport or inferred or "nfl").lower()
    if sport in SPORTS:
        _sync(sport)
    p = pipe.get_player(player_id, sport=sport if sport in SPORTS else None)
    if not p:
        for s in SPORTS:
            p = pipe.get_player(player_id, sport=s)
            if p:
                sport = s
                break
    if not p:
        return _not_found("Player not found")

    psport = str(p.get("sport") or sport)
    body = f"""
<div class="fan-wrap">
  {_sport_tabs(psport)}
  <div class="fan-card">
    <h1>{_esc(p.get('name'))}</h1>
    <p>{_esc(p.get('position') or '')} · {_esc(p.get('team') or '')} · {psport.upper()}</p>
    <ul class="fan-list">
      <li>Projected: <strong>{float(p.get('projected_points') or 0):.1f}</strong></li>
      <li>Floor / Ceiling: {float(p.get('floor_points') or 0):.1f} / {float(p.get('ceiling_points') or 0):.1f}</li>
      <li>Form: {_esc(p.get('form_trend') or '—')}</li>
      <li>Matchup rating: {float(p.get('matchup_rating') or 0):.1f}/10</li>
      <li>Pos rank: #{p.get('rank') or '—'}</li>
      <li>ESPN id: {_esc(p.get('espn_id') or '')}</li>
    </ul>
    <p class="fan-card-links">
      <a href="/fantasy/{psport}/rankings">← Rankings</a> ·
      <a href="/fantasy/{psport}/start-sit?a={quote(str(p.get('name') or ''))}">Start/Sit</a>
    </p>
  </div>
</div>
"""
    return _page(
        f"{p.get('name')} · Fantasy",
        body,
        active_sport=psport if psport in SPORTS else None,
        active_section="rankings",
    ), {"ok": True, "player": p.get("name"), "sport": psport}


def _not_found(msg: str = "Not found") -> tuple[str, dict[str, Any]]:
    body = f"<div class='fan-wrap'><h1>{_esc(msg)}</h1><p><a href='/fantasy/'>← Fantasy</a></p></div>"
    return _page("Fantasy", body), {"ok": False, "status": 404}


def _page(
    title: str,
    body: str,
    *,
    active_sport: str | None = None,
    active_section: str | None = None,
) -> str:
    chrome = _chrome_header(active_sport=active_sport, active_section=active_section)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
<link rel="stylesheet" href="/static/css/hub.css"/>
<link rel="stylesheet" href="/static/css/research-theme.css"/>
<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>
<style>
:root {{
  --pl-ink: #0c1e3a;
  --pl-line: #e2e8f0;
  --pl-soft: #f1f5f9;
  --pl-neon: #d8f56a;
}}
body.fan.research-site {{
  background: linear-gradient(180deg, #f7fafc 0%, #eef3f8 100%);
  color: var(--pl-ink);
  margin: 0;
  font-family: "Segoe UI", system-ui, sans-serif;
}}
.fan-header {{
  position: sticky; top: 0; z-index: 2000;
  display: flex; align-items: center; gap: 14px;
  padding: 0 16px; height: 64px;
  background: rgba(255,255,255,.92); backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--pl-line);
}}
.fan-header .hamburger {{
  display: flex; flex-direction: column; justify-content: center; gap: 5px;
  width: 40px; height: 40px; border: 1px solid var(--pl-line); border-radius: 10px;
  background: #fff; cursor: pointer; padding: 0;
}}
.fan-header .hamburger span {{ display:block; width:18px; height:1.5px; background: var(--pl-ink); margin:0 auto; border-radius:2px; }}
.fan-brand {{ text-decoration:none; color: var(--pl-ink); display:flex; flex-direction:column; line-height:1.1; }}
.fan-brand b {{ font-size: .95rem; }}
.fan-brand span {{ font-size: .68rem; color:#64748b; font-weight:600; }}
.fan-desktop-nav {{ display:flex; gap:18px; margin-left: auto; }}
.fan-desktop-nav a {{ color:#475569; text-decoration:none; font-weight:700; font-size:.88rem; }}
.fan-desktop-nav a.is-active, .fan-desktop-nav a:hover {{ color: var(--pl-ink); }}
.fan-cta {{
  margin-left: 8px; padding: 8px 12px; border-radius: 2px; border: 1px solid var(--pl-ink);
  color: var(--pl-ink); text-decoration:none; font-weight:800; font-size:.78rem;
}}
#fanOverlay {{ display:none; position:fixed; inset:64px 0 0; background:rgba(15,23,42,.34); z-index:1998; }}
#fanOverlay.open {{ display:block; }}
.fan-drawer {{
  position:fixed; top:72px; left:16px; z-index:1999;
  width: min(420px, calc(100vw - 32px));
  max-height: min(720px, calc(100dvh - 96px));
  background:#fff; border:1px solid var(--pl-line); border-radius:14px;
  box-shadow:0 18px 44px rgba(15,23,42,.18);
  transform: translateY(-10px); opacity:0; pointer-events:none;
  transition: transform .2s, opacity .2s;
  display:flex; flex-direction:column; overflow:hidden;
}}
.fan-drawer.open {{ transform:translateY(0); opacity:1; pointer-events:auto; }}
.fan-mega {{ display:grid; grid-template-columns: 88px 1fr; min-height: 280px; flex:1; overflow:hidden; }}
.fan-mega-sports {{
  background: var(--pl-soft); border-right:1px solid var(--pl-line);
  display:flex; flex-direction:column; padding:8px; gap:4px; overflow-y:auto;
}}
.fan-mega-sport {{
  border:0; background:transparent; border-radius:10px; padding:12px 8px;
  font-weight:800; font-size:.78rem; color:#475569; cursor:pointer;
}}
.fan-mega-sport.active, .fan-mega-sport:hover {{ background:#fff; color:var(--pl-ink); box-shadow:0 1px 4px rgba(15,23,42,.08); }}
.fan-mega-sections {{ overflow-y:auto; padding:10px 8px 16px; }}
.fan-mega-col-title {{
  font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em;
  color:#94a3b8; padding:6px 12px 8px;
}}
.fan-mega-panel[hidden] {{ display:none; }}
.fan-mega-link {{
  display:flex; flex-direction:column; gap:2px; padding:10px 12px; margin:2px 4px;
  border-radius:10px; text-decoration:none; color:var(--pl-ink);
}}
.fan-mega-link:hover, .fan-mega-link.active {{ background: var(--pl-soft); }}
.fan-mega-link strong {{ font-size:.92rem; }}
.fan-mega-link span {{ font-size:.75rem; color:#64748b; font-weight:500; }}
.fan-wrap {{ max-width:1100px; margin:0 auto; padding:28px 16px 48px; }}
.fan-kicker {{ margin:0; font-size:.75rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:#64748b; }}
.fan-wrap h1 {{ margin:6px 0 8px; font-size:clamp(1.6rem, 3vw, 2.2rem); line-height:1.1; }}
.fan-wrap .sub {{ color:#64748b; margin:0 0 18px; max-width:52ch; }}
.fan-sport-tabs, .fan-section-nav {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 14px; }}
.fan-tab, .fan-sec {{
  padding:7px 12px; border-radius:999px; border:1px solid var(--pl-line); background:#fff;
  color:var(--pl-ink); font-weight:700; font-size:.84rem; text-decoration:none;
}}
.fan-tab.active, .fan-sec.active {{ background:var(--pl-ink); color:#fff; border-color:var(--pl-ink); }}
.fan-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
.fan-tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin-bottom:8px; }}
.fan-tile {{
  display:flex; flex-direction:column; gap:4px; padding:14px; border-radius:14px;
  background:#fff; border:1px solid var(--pl-line); text-decoration:none; color:var(--pl-ink);
  box-shadow:0 8px 24px rgba(15,23,42,.04);
}}
.fan-tile:hover {{ border-color:#94a3b8; }}
.fan-tile strong {{ font-size:.95rem; }}
.fan-tile span {{ font-size:.78rem; color:#64748b; }}
.fan-card {{
  background:#fff; border:1px solid rgba(15,23,42,.1); border-radius:16px;
  padding:16px; box-shadow:0 8px 24px rgba(15,23,42,.05);
}}
.fan-card h2 {{ margin:0 0 10px; font-size:1.05rem; }}
.fan-card h2 a {{ color:inherit; text-decoration:none; }}
.fan-table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
.fan-table th, .fan-table td {{ text-align:left; padding:8px 6px; border-bottom:1px solid #eef2f7; }}
.fan-table th {{ color:#64748b; font-size:.72rem; text-transform:uppercase; }}
.tiny {{ color:#64748b; font-size:.82rem; margin:0 0 10px; }}
.meta {{ margin-top:14px; font-size:.78rem; color:#94a3b8; }}
.fan-btn {{
  padding:8px 14px; border:0; border-radius:999px; background:var(--pl-ink); color:#fff; font-weight:700; cursor:pointer;
}}
.fan-form {{ display:flex; flex-wrap:wrap; gap:8px; align-items:end; }}
.fan-form input {{ padding:8px; border:1px solid #dbe3ee; border-radius:8px; min-width:140px; }}
.fan-list {{ line-height:1.75; margin:0; padding-left:1.1rem; }}
.fan-card-links {{ margin:10px 0 0; font-size:.88rem; }}
.fan-result {{ background:var(--pl-soft); border-radius:12px; padding:12px; margin-bottom:12px; }}
.fan-err {{ color:#b91c1c; }}
@media (max-width: 800px) {{
  .fan-desktop-nav {{ display:none; }}
}}
</style>
</head>
<body class="fan research-site" data-theme="light">
{chrome}
{body}
<script>
(function() {{
  var burger = document.getElementById('fanBurger');
  var drawer = document.getElementById('fanDrawer');
  var overlay = document.getElementById('fanOverlay');
  var closeBtn = document.getElementById('fanDrawerClose');
  function openMenu() {{
    drawer.classList.add('open');
    overlay.classList.add('open');
    overlay.hidden = false;
    drawer.setAttribute('aria-hidden','false');
    burger.setAttribute('aria-expanded','true');
  }}
  function closeMenu() {{
    drawer.classList.remove('open');
    overlay.classList.remove('open');
    overlay.hidden = true;
    drawer.setAttribute('aria-hidden','true');
    burger.setAttribute('aria-expanded','false');
  }}
  if (burger) burger.addEventListener('click', function() {{
    if (drawer.classList.contains('open')) closeMenu(); else openMenu();
  }});
  if (closeBtn) closeBtn.addEventListener('click', closeMenu);
  if (overlay) overlay.addEventListener('click', closeMenu);
  document.querySelectorAll('.fan-mega-sport').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var s = btn.getAttribute('data-sport');
      document.querySelectorAll('.fan-mega-sport').forEach(function(b) {{
        b.classList.toggle('active', b === btn);
        b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
      }});
      document.querySelectorAll('.fan-mega-panel').forEach(function(p) {{
        if (p.getAttribute('data-panel') === s) p.removeAttribute('hidden');
        else p.setAttribute('hidden', '');
      }});
    }});
  }});
  document.addEventListener('keydown', function(e) {{ if (e.key === 'Escape') closeMenu(); }});
}})();
</script>
</body></html>"""
