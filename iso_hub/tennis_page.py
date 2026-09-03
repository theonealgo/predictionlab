"""Tennis picks + results from sandbox isolation DB (not empty/fake live sidecar).

TENNIS UI LOCKED 2026-09-02 — do not change HTML/CSS/layout unless owner says
UNLOCK TENNIS (see notes/TENNIS_LOCKED.md, .cursor/rules/tennis-ui-locked.mdc).

Sandbox only — never push / never talk about live deploy.
Layout chrome matches MLB/UFC (pick cards, daily-tally, games-grid); markets are moneyline-only.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tennis.database.paths import DB_PATH
from tennis.database.init_db import seed
from tennis.espn_sync import (
    HEADSHOT_CACHE,
    flag_url_for,
    headshot_url_for,
    load_headshot_cache,
    model_sides,
    sync_tennis_db,
)

# Verified ESPN tennis headshot ids (most scoreboard athlete ids 404 on this CDN).
_TENNIS_HEADSHOT_IDS: dict[str, str] = {
    "jannik sinner": "3623",
    "carlos alcaraz": "3782",
    "novak djokovic": "296",
    "daniil medvedev": "2383",
}
_HEADSHOT_TMPL = "https://a.espncdn.com/i/headshots/tennis/players/full/{}.png"
_FALLBACK_LOGO = "/static/pl-logo.svg"
_ESPN_SEARCH_CACHE: dict[str, str] = {}

GRID_CSS = """
<style id="tennis-mlb-grid-fix">
body[data-sandbox-sport="tennis"] .games-grid,
body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) > .games-grid,
body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) .games-grid{
  display:grid!important;
  grid-template-columns:repeat(3, minmax(0, 1fr))!important;
  gap:14px!important;
  margin-bottom:22px!important;
  align-items:start!important;
}
@media (max-width:1100px){
  body[data-sandbox-sport="tennis"] .games-grid,
  body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) > .games-grid,
  body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) .games-grid{
    grid-template-columns:repeat(2, minmax(0, 1fr))!important;
  }
}
@media (max-width:700px){
  body[data-sandbox-sport="tennis"] .games-grid,
  body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) > .games-grid,
  body[data-sandbox-sport="tennis"] .date-section:not(.chart-mode) .games-grid{
    grid-template-columns:1fr!important;
  }
}
body[data-sandbox-sport="tennis"] .game-card-stack{
  min-width:0!important;max-width:none!important;width:100%!important;
  justify-self:stretch!important;display:flex!important;flex-direction:column!important;
}
body[data-sandbox-sport="tennis"] .date-section.chart-mode > .games-grid,
body[data-sandbox-sport="tennis"] .date-section.chart-mode .games-grid{display:none!important;}
body[data-sandbox-sport="tennis"] .date-section.chart-mode > .chart-table-wrap,
body[data-sandbox-sport="tennis"] .date-section.chart-mode .chart-table-wrap{display:block;}
/* Left-align Tennis Predictions heading + Predictions|Results + Cards|Chart */
body[data-sandbox-sport="tennis"] .sport-predictions-heading,
body[data-sandbox-sport="tennis"] h2.sport-predictions-heading{
  text-align:left!important;width:100%;
}
body[data-sandbox-sport="tennis"] .section-tabs,
body[data-sandbox-sport="tennis"] .pl-view-toggle,
body[data-sandbox-sport="tennis"] .picks-view-controls,
body[data-sandbox-sport="tennis"] .picks-view-toggle,
body[data-sandbox-sport="tennis"] #pvToggle{
  display:flex!important;justify-content:flex-start!important;align-items:center!important;
  flex-wrap:wrap!important;width:100%!important;max-width:none!important;
  margin-left:0!important;margin-right:0!important;
}
body[data-sandbox-sport="tennis"] .pv-toggle{
  display:inline-flex!important;margin-left:0;margin-right:0;
}
/* Pick Confidence: 3×2 — 6-up in a 3-col card is unreadable */
body[data-sandbox-sport="tennis"] .tennis-models.pick-conf-grid,
body[data-sandbox-sport="tennis"] .pick-conf-grid{
  display:grid!important;
  grid-template-columns:repeat(3, minmax(0, 1fr))!important;
  gap:6px!important;
}
body[data-sandbox-sport="tennis"] .pc-box{
  padding:6px 7px!important;
  min-width:0!important;
}
body[data-sandbox-sport="tennis"] .pc-name{font-size:0.68rem!important;}
body[data-sandbox-sport="tennis"] .pc-val{font-size:0.92rem!important;}
body[data-sandbox-sport="tennis"] .pc-side{
  font-size:0.72rem!important;
  white-space:nowrap!important;
  overflow:hidden!important;
  text-overflow:ellipsis!important;
}
header.pl2-header, header.pl2-header a, header.pl2-header a:hover{color:inherit}
</style>
"""


def _display_tournament(name: str | None) -> str:
    """Product-facing event label — never isolation/sandbox names (AGENTS rule 9)."""
    t = " ".join(str(name or "").strip().split())
    if not t or re.search(r"\bsandbox\b", t, flags=re.I):
        return "ATP Tour"
    return t


def _today_et() -> datetime.date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _conn() -> sqlite3.Connection:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size < 1000:
        seed()
    else:
        try:
            probe = sqlite3.connect(str(DB_PATH))
            n_final = probe.execute("SELECT COUNT(*) FROM matches WHERE status='final'").fetchone()[0]
            n_up = probe.execute(
                "SELECT COUNT(*) FROM matches WHERE status IN ('scheduled','live')"
            ).fetchone()[0]
            probe.close()
        except Exception:
            n_final = n_up = 0
        # Demo slate is tiny; also resync when picks slate is empty.
        if n_final < 8 or n_up < 2:
            try:
                sync_tennis_db(force=True)
            except Exception:
                pass
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _remember_headshot_id(name: str, aid: str) -> None:
    key = " ".join(name.strip().lower().split())
    if not key or not aid:
        return
    _TENNIS_HEADSHOT_IDS[key] = str(aid)
    try:
        cur = load_headshot_cache()
        if cur.get(key) != str(aid):
            cur[key] = str(aid)
            HEADSHOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            HEADSHOT_CACHE.write_text(json.dumps(cur, indent=0, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _espn_search_headshot(name: str) -> str | None:
    """Resolve an ATP/WTA headshot via ESPN search (cached; best-effort)."""
    key = " ".join(name.strip().lower().split())
    if not key:
        return None
    if key in _ESPN_SEARCH_CACHE:
        return _ESPN_SEARCH_CACHE[key] or None
    href = None
    try:
        import ssl
        from urllib.parse import quote
        from urllib.request import Request, urlopen

        url = (
            "https://site.web.api.espn.com/apis/common/v3/search"
            f"?query={quote(name)}&limit=5&type=player"
        )
        req = Request(url, headers={"User-Agent": "sports-sandbox-hub"})
        ctx = ssl._create_unverified_context()
        with urlopen(req, timeout=2.5, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        want = set(key.split())
        for it in data.get("items") or []:
            hs = (it.get("headshot") or {}).get("href") or ""
            sport = str(it.get("sport") or "").lower()
            league = str(it.get("defaultLeagueSlug") or it.get("league") or "").lower()
            tennisish = (
                "tennis" in sport
                or league in ("atp", "wta")
                or "headshots/tennis" in hs
            )
            if not tennisish:
                continue
            disp = " ".join(str(it.get("displayName") or "").lower().split())
            if disp != key:
                dparts = set(disp.split())
                if not want.issubset(dparts) and not dparts.issubset(want):
                    continue
            href = hs.strip() or None
            aid = it.get("id")
            if not href and aid:
                href = _HEADSHOT_TMPL.format(aid)
            if aid:
                _remember_headshot_id(name, str(aid))
            if href:
                break
    except Exception:
        href = None
    _ESPN_SEARCH_CACHE[key] = href or ""
    return href


def tennis_player_logo(name: str | None, *, search: bool = True) -> str:
    """Player chrome: verified ESPN headshot, else country flag, else site logo."""
    key = " ".join(str(name or "").strip().lower().split())
    if not key:
        return _FALLBACK_LOGO
    # Only use hard-verified headshot ids — scoreboard athlete ids usually 404.
    aid = _TENNIS_HEADSHOT_IDS.get(key)
    if aid:
        return _HEADSHOT_TMPL.format(aid)
    flag = flag_url_for(name)
    if flag:
        return flag
    if search:
        found = _espn_search_headshot(str(name))
        # Search often returns ids that still 404; prefer flag if we have one.
        if found and key in _TENNIS_HEADSHOT_IDS:
            return found
        flag2 = flag_url_for(name)
        if flag2:
            return flag2
    return _FALLBACK_LOGO


def american_from_prob(p: float) -> int:
    p = min(0.99, max(0.01, float(p)))
    if p >= 0.5:
        return int(round(-100 * p / (1.0 - p)))
    return int(round(100 * (1.0 - p) / p))


def tennis_picks_writeup_html() -> str:
    """MLB/UFC-style SEO intro (title + paragraph + Predictions heading). No IP/vendor notes."""
    return (
        '<div class="header">'
        '<h1 id="pageHeading">🎾 Tennis AI Picks, Predictions and Model Probabilities</h1>'
        "</div>\n"
        "<!-- SEO text block -->\n"
        '<div class="sport-picks-writeup" style="margin-bottom:16px;padding:14px 16px;'
        "background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);"
        'border-radius:10px;font-size:0.85em;color:#475569;line-height:1.7;">\n'
        "Our Tennis picks today are generated using a specialized AI prediction system that "
        "analyzes player form, surface performance, serve and return metrics, recent match "
        "results, and head-to-head matchups. By evaluating ranking trends, tournament context, "
        "and key performance indicators, our model identifies high-value opportunities across "
        "Tennis moneyline and match outcome predictions.\n"
        "</div>\n"
        '<h2 class="sport-predictions-heading" style="color:#0f172a;font-size:1.2rem;'
        'margin:0 0 12px;text-align:left;">📊 Tennis Predictions</h2>\n'
    )


def tennis_section_tabs_html(which: str) -> str:
    """Mirror MLB/UFC in-page tabs: 📊 Predictions | 🎯 Results."""
    pa = "active" if which == "picks" else ""
    ra = "active" if which == "results" else ""
    picks_href = "/tennis-picks"
    results_href = "/tennis-results"
    return (
        '<div class="section-tabs" role="navigation" aria-label="Sport pages">'
        f'<a href="{picks_href}" class="tab {pa}">📊 Predictions</a>'
        f'<a href="{results_href}" class="tab {ra}">🎯 Results</a>'
        "</div>"
        "<style>.section-tabs{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap;"
        "justify-content:flex-start;width:100%}"
        ".section-tabs .tab{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.section-tabs .tab.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _parse_match_dt(raw: str | None) -> tuple[str, str]:
    """Return (YYYY-MM-DD, time label)."""
    s = str(raw or "").strip()
    if not s:
        return "", ""
    try:
        if "T" in s or "+" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(
                ZoneInfo("America/New_York")
            )
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        pass
    return s[:10], ""


def build_tennis_results_payload() -> dict[str, Any]:
    """Cards + window tallies for Normal results and Chart API."""
    con = _conn()
    try:
        matches = [
            dict(r)
            for r in con.execute(
                """
                SELECT m.*, p.model_name, p.win_prob_a, p.ou_pick, p.ou_games_line,
                       p.confidence, g.winner_correct, g.ou_result, g.result AS grade_result
                FROM matches m
                LEFT JOIN predictions p ON p.match_id = m.match_id
                LEFT JOIN grades g ON g.pred_id = p.pred_id
                ORDER BY date(substr(m.match_date,1,10)) DESC, m.match_id
                """
            )
        ]
    finally:
        con.close()

    finals = [
        m
        for m in matches
        if (m.get("status") or "").lower() == "final" and m.get("winner")
    ]
    upcoming = [
        m
        for m in matches
        if (m.get("status") or "").lower() in ("scheduled", "live", "upcoming")
        and str(m.get("player_a") or "").strip().upper() not in ("", "TBD", "NONE")
        and str(m.get("player_b") or "").strip().upper() not in ("", "TBD", "NONE")
    ]
    # Picks should show soonest first (results finals stay newest-first).
    upcoming.sort(
        key=lambda m: (
            str(m.get("match_date") or "")[:16],
            str(m.get("match_id") or ""),
        )
    )

    today = _today_et()
    last_night = today - timedelta(days=1)

    def _window_hits(days: int | None = None, exact: datetime.date | None = None) -> tuple[int, int]:
        w = l = 0
        for m in finals:
            try:
                d = datetime.strptime(str(m.get("match_date") or "")[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if exact is not None and d != exact:
                continue
            if days is not None and exact is None:
                if d < today - timedelta(days=days) or d > today:
                    continue
            ok = m.get("winner_correct")
            if ok is None:
                continue
            if int(ok) == 1:
                w += 1
            else:
                l += 1
        return w, l

    ln_w, ln_l = _window_hits(exact=last_night) if last_night else (0, 0)
    if last_night:
        l7_w = l7_l = 0
        for m in finals:
            try:
                d = datetime.strptime(str(m.get("match_date") or "")[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if last_night - timedelta(days=6) <= d <= last_night:
                ok = m.get("winner_correct")
                if ok is None:
                    continue
                if int(ok) == 1:
                    l7_w += 1
                else:
                    l7_l += 1
    else:
        l7_w = l7_l = 0
    season_w = season_l = 0
    for m in finals:
        ok = m.get("winner_correct")
        if ok is None:
            continue
        if int(ok) == 1:
            season_w += 1
        else:
            season_l += 1

    cards = []
    for m in finals:
        a = m.get("player_a") or ""
        b = m.get("player_b") or ""
        winner = m.get("winner") or ""
        ok = m.get("winner_correct")
        sets_a = m.get("sets_a")
        sets_b = m.get("sets_b")
        day, _ = _parse_match_dt(m.get("match_date"))
        cards.append(
            {
                "match_id": m.get("match_id"),
                "game_date": day,
                "tournament": _display_tournament(m.get("tournament")),
                "surface": m.get("surface") or "",
                "player_a": a,
                "player_b": b,
                "home": a,
                "away": b,
                "home_team_id": a,
                "away_team_id": b,
                "home_logo": tennis_player_logo(a, search=False),
                "away_logo": tennis_player_logo(b, search=False),
                "winner": winner,
                "sets_a": sets_a,
                "sets_b": sets_b,
                "score": f"{sets_a}-{sets_b}" if sets_a is not None and sets_b is not None else "",
                "games": f"{m.get('games_a')}-{m.get('games_b')}"
                if m.get("games_a") is not None
                else "",
                "model": m.get("model_name") or "Prediction Lab",
                "pick": a if (m.get("win_prob_a") or 0) >= 0.5 else b,
                "prob": round(
                    100.0
                    * max(float(m.get("win_prob_a") or 0.5), 1.0 - float(m.get("win_prob_a") or 0.5)),
                    1,
                ),
                "win_prob_a": float(m.get("win_prob_a") or 0.5),
                "models": {
                    name: {
                        "pick": fav,
                        "prob": pct,
                        "correct": (fav == winner) if winner else None,
                        "side": "home" if fav == a else "away",
                    }
                    for name, fav, pct in model_sides(a, b, float(m.get("win_prob_a") or 0.5))
                },
                "correct": True if ok == 1 else False if ok == 0 else None,
                "ou_pick": m.get("ou_pick"),
                "ou_result": m.get("ou_result"),
                "final": True,
            }
        )

    return {
        "ok": True,
        "sport": "tennis",
        "source": "isolation_db",
        "today": today.strftime("%Y-%m-%d"),
        "finals": cards,
        "upcoming": [
            {
                "match_id": m.get("match_id"),
                "game_date": _parse_match_dt(m.get("match_date"))[0],
                "start_iso": m.get("match_date"),
                "tournament": _display_tournament(m.get("tournament")),
                "player_a": m.get("player_a"),
                "player_b": m.get("player_b"),
                "home": m.get("player_a"),
                "away": m.get("player_b"),
                "home_logo": tennis_player_logo(m.get("player_a"), search=False),
                "away_logo": tennis_player_logo(m.get("player_b"), search=False),
                "win_prob_a": float(m.get("win_prob_a") or 0.5),
                "final": False,
            }
            for m in upcoming
        ],
        "labels": ["Last Night", "Last 7", "Season"],
        "values": [ln_w, l7_w, season_w],
        "records": {
            "last_night": {
                "w": ln_w,
                "l": ln_l,
                "date": last_night.isoformat() if last_night else None,
            },
            "last_7": {"w": l7_w, "l": l7_l},
            "season": {"w": season_w, "l": season_l},
        },
    }


def build_tennis_picks_payload() -> dict[str, Any]:
    data = build_tennis_results_payload()
    upcoming = list(data.get("upcoming") or [])
    # Logos come from scoreboard flag cache (no live ESPN search on page render).
    for row in upcoming:
        row["home_logo"] = tennis_player_logo(row.get("player_a"), search=False)
        row["away_logo"] = tennis_player_logo(row.get("player_b"), search=False)
    return {
        "ok": True,
        "sport": "tennis",
        "source": "isolation_db",
        "today": data.get("today"),
        "upcoming": upcoming,
        "cards": upcoming,
    }


def _render_pick_card(c: dict[str, Any], idx: int, *, expanded: bool = False) -> str:
    a = str(c.get("player_a") or c.get("home") or "")
    b = str(c.get("player_b") or c.get("away") or "")
    pa = float(c.get("win_prob_a") or 0.5)
    pb = 1.0 - pa
    a_fav = pa >= pb
    pick = a if a_fav else b
    conf = max(pa, pb) * 100.0
    pl_a = american_from_prob(pa)
    pl_b = american_from_prob(pb)
    day, when = _parse_match_dt(c.get("start_iso") or c.get("game_date"))
    tourney = _display_tournament(c.get("tournament"))
    la = c.get("home_logo") or tennis_player_logo(a, search=False)
    lb = c.get("away_logo") or tennis_player_logo(b, search=False)
    details_id = f"card-details-tennis-{idx}"

    def slot(name: str, prob: float, pl: int, favored: bool, logo: str) -> str:
        fav_cls = "favored" if favored else ""
        pl_cls = "fav" if pl < 0 else "dog"
        return f"""
    <div class="team-slot {fav_cls}">
        <img class="team-logo" src="{html_lib.escape(logo)}" alt="{html_lib.escape(name)}"
             width="52" height="52" loading="lazy" onerror="this.src='/static/pl-logo.svg'">
        <div class="team-name">{html_lib.escape(name)}</div>
        <div class="model-tag">Sharp Consensus</div>
        <div class="win-pct">{prob*100:.1f}<span class="unit">%</span></div>
        <div class="ml-stack face-ml-stack">
            <div class="ml-line face-pl-ml">
                <span class="ml-src pl">Prediction Lab</span>
                <span class="ml-num {pl_cls}">{pl:+d}</span>
            </div>
        </div>
    </div>"""

    pills = []
    model_attrs: dict[str, str] = {}
    for name, fav, pct in model_sides(a, b, pa):
        side_cls = "home" if fav == a else "away"
        cons = " consensus" if name == "Sharp Consensus" else ""
        pills.append(
            f'<div class="pc-box{cons}"><div class="pc-name">{html_lib.escape(name)}</div>'
            f'<div class="pc-val">{pct:.1f}%</div>'
            f'<div class="pc-side {side_cls}">{html_lib.escape(fav)}</div></div>'
        )
        key = {
            "Grinder2": "grinder2",
            "Takedown": "takedown",
            "Edge": "edge",
            "XSharp": "xsharp",
            "Efficiency": "efficiency",
            "Sharp Consensus": "consensus",
        }.get(name)
        if key:
            model_attrs[key] = f"{pct:.1f}"
    time_bit = " · ".join(x for x in (day, when) if x)
    attr_bits = " ".join(f'data-m-{k}="{v}"' for k, v in model_attrs.items())
    stack_cls = "game-card-stack is-expanded" if expanded else "game-card-stack"
    aria_exp = "true" if expanded else "false"
    btn_label = (
        'Less details <span class="chevron">▾</span>'
        if expanded
        else 'View Details <span class="chevron">▾</span>'
    )
    details_style = "display:block" if expanded else "display:none"
    details_hidden = "" if expanded else " hidden"

    return f"""
<div class="{stack_cls}" data-pick-card data-league="TENNIS"
     data-home="{html_lib.escape(a)}" data-away="{html_lib.escape(b)}"
     data-pick="{html_lib.escape(pick)}" data-conf="{conf:.1f}"
     {attr_bits}>
  <div class="game-card pick-card" data-league="TENNIS">
    <header class="pick-card-header">
      <span class="league-badge">🎾 Tennis</span>
      <span class="game-time">{html_lib.escape(time_bit)}</span>
    </header>
    <div class="matchup-row">
      {slot(b, pb, pl_b, not a_fav, lb)}
      <div class="matchup-at">vs</div>
      {slot(a, pa, pl_a, a_fav, la)}
    </div>
    <div class="lines-strip">
      <div class="line-chip"><div class="line-chip-label">Model pick</div>
        <div class="line-chip-val">{html_lib.escape(pick)}</div></div>
      <div class="line-chip"><div class="line-chip-label">Confidence</div>
        <div class="line-chip-val">{conf:.1f}%</div></div>
    </div>
    <footer class="card-footer">
      <button type="button" class="view-details-btn" aria-expanded="{aria_exp}"
              aria-controls="{details_id}" onclick="togglePickDetails(this)">
        {btn_label}
      </button>
    </footer>
    <div class="card-details" id="{details_id}"{details_hidden} style="{details_style}">
      <div class="pick-conf-bar">
        <div class="pick-conf-title">Pick Confidence</div>
        <div class="pick-conf-grid">{''.join(pills)}</div>
      </div>
      <div class="odds-extras-footer">
        <div class="sf-item"><span class="sf-label">Event</span>
          <span class="sf-val">{html_lib.escape(tourney)}</span></div>
        <div class="sf-item"><span class="sf-label">Model pick</span>
          <span class="sf-val">{html_lib.escape(pick)}</span></div>
        <div class="sf-item"><span class="sf-label">Confidence</span>
          <span class="sf-val">{conf:.1f}%</span></div>
      </div>
    </div>
  </div>
</div>
"""


def _render_result_card(c: dict[str, Any], idx: int) -> str:
    """Same markup as predictions cards — graded finals reuse the pick chrome."""
    row = {
        "player_a": c.get("player_a") or c.get("home") or "",
        "player_b": c.get("player_b") or c.get("away") or "",
        "home": c.get("player_a") or c.get("home") or "",
        "away": c.get("player_b") or c.get("away") or "",
        "win_prob_a": float(c.get("win_prob_a") or 0.5),
        "tournament": c.get("tournament"),
        "home_logo": c.get("home_logo"),
        "away_logo": c.get("away_logo"),
        "game_date": c.get("game_date"),
        "start_iso": c.get("start_iso") or c.get("game_date"),
    }
    out = _render_pick_card(row, idx)
    day = html_lib.escape(str(c.get("game_date") or "").strip() or "Final")
    # Keep identical chrome; only the time label marks the match as final.
    out = re.sub(
        r'(<span class="game-time">)[^<]*(</span>)',
        rf"\1FINAL · {day}\2",
        out,
        count=1,
    )
    return out


def render_tennis_picks_html(payload: dict[str, Any] | None = None) -> str:
    data = payload or build_tennis_picks_payload()
    cards = list(data.get("upcoming") or data.get("cards") or [])
    tabs = tennis_section_tabs_html("picks")
    writeup = tennis_picks_writeup_html()

    by_day: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        day = str(c.get("game_date") or _parse_match_dt(c.get("start_iso"))[0] or "upcoming")
        by_day.setdefault(day, []).append(c)
    days = sorted(by_day.keys())
    today = str(data.get("today") or _today_et().isoformat())
    if today in by_day:
        days = [today] + [d for d in days if d != today]
    elif days:
        # Prefer soonest upcoming day as visible.
        days = sorted(days)

    if not days:
        body_cards = '<div class="no-data">No upcoming Tennis matches in the current slate.</div>'
    else:
        parts = []
        idx = 0
        for i, day in enumerate(days):
            visible = "visible" if i == 0 else "seo-hidden"
            today_badge = (
                ' <span style="background:#00C076;color:white;padding:3px 10px;border-radius:4px;'
                'font-size:0.68em;margin-left:8px;">TODAY</span>'
                if day == today
                else ""
            )
            stacks = []
            for c in by_day[day]:
                # MLB picks cards ship open ("Less details") — match that.
                stacks.append(_render_pick_card(c, idx, expanded=True))
                idx += 1
            # chart-table-wrap required for Cards|Chart inject / punchlist structure
            parts.append(
                f'<div class="date-section {visible}" id="date-{html_lib.escape(day)}">'
                f'<div class="date-header">📅 {html_lib.escape(day)}{today_badge}</div>'
                f'<div class="games-grid">{"".join(stacks)}</div>'
                f'<div class="chart-table-wrap" hidden></div>'
                f"</div>"
            )
        body_cards = "".join(parts)

    body = f"""
<main>
  <div class="container">
    {writeup}
    {tabs}
    {body_cards}
  </div>
</main>
{GRID_CSS}
<script>
function togglePickDetails(btn){{
  if(!btn) return;
  var id=btn.getAttribute('aria-controls');
  var panel=id?document.getElementById(id):null;
  if(!panel) return;
  var open=btn.getAttribute('aria-expanded')==='true';
  btn.setAttribute('aria-expanded', open?'false':'true');
  if(open){{
    panel.setAttribute('hidden','');
    panel.style.display='none';
    btn.innerHTML='View Details <span class="chevron">▾</span>';
  }} else {{
    panel.removeAttribute('hidden');
    panel.style.display='block';
    btn.innerHTML='Less details <span class="chevron">▾</span>';
  }}
  var stack=btn.closest('.game-card-stack');
  if(stack) stack.classList.toggle('is-expanded', !open);
}}
</script>
"""
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tennis Picks | Prediction Lab</title>
  <link rel="stylesheet" href="/static/css/research-theme.css" />
  <link rel="stylesheet" href="/static/css/picks-nav-overrides.css" />
  <link rel="stylesheet" href="/static/css/mlb-pick-cards.css" />
  <link rel="stylesheet" href="/static/css/sports-chrome.css" />
  <style id="tennis-chrome-isolate">
    header.pl2-header, header.pl2-header a, header.pl2-header a:hover {{ color: inherit; }}
  </style>
</head>
<body class="research-site" data-theme="light" data-sandbox-sport="tennis" data-sandbox-sports-chrome="1">
{body}
</body>
</html>"""
    return page


def render_tennis_results_html(payload: dict[str, Any] | None = None) -> str:
    """MLB-chrome tennis results: daily-tally + games-grid cards with player logos."""
    data = payload or build_tennis_results_payload()
    finals = data.get("finals") or []
    rec = data.get("records") or {}
    ln = rec.get("last_night") or {}
    l7 = rec.get("last_7") or {}
    season = rec.get("season") or {}

    def esc(s: Any) -> str:
        return html_lib.escape(str(s if s is not None else ""))

    # Logos from flag cache — avoid live ESPN search (slow / flaky on page render).
    for c in finals[:80]:
        c["home_logo"] = tennis_player_logo(c.get("player_a"), search=False)
        c["away_logo"] = tennis_player_logo(c.get("player_b"), search=False)

    date_bit = esc(ln.get("date") or "—")
    tabs = tennis_section_tabs_html("results")
    tally = f"""
    <section class="tally-wrap" id="tallies">
      <div class="daily-tally">
        <div class="daily-tally-head">
          <h2>Last Night's Tennis Results — {date_bit}</h2>
        </div>
        <div class="daily-tally-grid tally-grid">
          <div class="daily-tally-card tally-card">
            <div class="daily-model">Last Night</div>
            <div class="rec">{esc(ln.get('w', 0))}-{esc(ln.get('l', 0))}</div>
            <div class="muted">{date_bit}</div>
          </div>
          <div class="daily-tally-card tally-card">
            <div class="daily-model">Last 7</div>
            <div class="rec">{esc(l7.get('w', 0))}-{esc(l7.get('l', 0))}</div>
          </div>
          <div class="daily-tally-card tally-card">
            <div class="daily-model">Season</div>
            <div class="rec">{esc(season.get('w', 0))}-{esc(season.get('l', 0))}</div>
          </div>
        </div>
      </div>
    </section>
    """

    if not finals:
        cards_html = '<div class="no-data">No completed matches in sandbox slate yet.</div>'
    else:
        cards_html = "".join(
            _render_result_card(c, i) for i, c in enumerate(finals[:80])
        )

    consensus = ""
    try:
        try:
            from tennis_consensus import build_tennis_consensus_html

            consensus = build_tennis_consensus_html(
                [
                    {
                        "game_date": c.get("game_date"),
                        "home_team_id": c.get("player_a"),
                        "away_team_id": c.get("player_b"),
                        "home": c.get("player_a"),
                        "away": c.get("player_b"),
                        "home_score": c.get("sets_a"),
                        "away_score": c.get("sets_b"),
                        "winner": c.get("winner"),
                        "models": c.get("models") or {},
                    }
                    for c in finals
                ],
                last_night_key=str(ln.get("date") or ""),
            )
        except Exception:
            from team_tabbed_results import build_consensus_records_html

            consensus = build_consensus_records_html(
                [
                    {
                        "game_date": c.get("game_date"),
                        "home_team_id": c.get("player_a"),
                        "away_team_id": c.get("player_b"),
                        "home": c.get("player_a"),
                        "away": c.get("player_b"),
                        "home_score": c.get("sets_a"),
                        "away_score": c.get("sets_b"),
                        "winner": c.get("winner"),
                        "models": c.get("models") or {},
                    }
                    for c in finals
                ],
                last_night_key=str(ln.get("date") or ""),
                sport="tennis",
            )
    except Exception:
        consensus = ""

    perf = ""
    try:
        from share_chrome import build_ml_sport_performance_html
        from team_tabbed_results import build_tennis_payload

        perf = build_ml_sport_performance_html(build_tennis_payload(), sport="tennis")
    except Exception:
        perf = ""

    if perf:
        # Full model tallies replace the thin W-L strip.
        tally = ""

    body = f"""
<main>
  <div class="container">
    {tabs}
    {tally}
    {perf}
    {consensus}
    <section id="finals-wrap">
      <h2 class="sec-title">Completed matches <span class="tag">({min(len(finals), 80)})</span></h2>
      <div class="cards games-grid" id="finals">{cards_html}</div>
    </section>
  </div>
</main>
{GRID_CSS}
<script>
function togglePickDetails(btn){{
  if(!btn) return;
  var id=btn.getAttribute('aria-controls');
  var panel=id?document.getElementById(id):null;
  if(!panel) return;
  var open=btn.getAttribute('aria-expanded')==='true';
  btn.setAttribute('aria-expanded', open?'false':'true');
  if(open){{
    panel.setAttribute('hidden','');
    panel.style.display='none';
    btn.innerHTML='View Details <span class="chevron">▾</span>';
  }} else {{
    panel.removeAttribute('hidden');
    panel.style.display='block';
    btn.innerHTML='Less details <span class="chevron">▾</span>';
  }}
  var stack=btn.closest('.game-card-stack');
  if(stack) stack.classList.toggle('is-expanded', !open);
}}
</script>
"""
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tennis Results | Prediction Lab</title>
  <link rel="stylesheet" href="/static/css/research-theme.css" />
  <link rel="stylesheet" href="/static/css/picks-nav-overrides.css" />
  <link rel="stylesheet" href="/static/css/mlb-pick-cards.css" />
  <link rel="stylesheet" href="/static/css/sports-chrome.css" />
  <link rel="stylesheet" href="/static/css/team-results.css" />
  <style id="tennis-chrome-isolate">
    header.pl2-header, header.pl2-header a, header.pl2-header a:hover {{ color: inherit; }}
    body[data-sandbox-sport="tennis"] .section-tabs,
    body[data-sandbox-sport="tennis"] .pl-view-toggle {{
      display: flex !important;
      justify-content: flex-start !important;
      flex-wrap: wrap !important;
      width: 100%;
      margin-left: 0 !important;
      margin-right: 0 !important;
    }}
    body[data-sandbox-sport="tennis"] .sport-predictions-heading {{
      text-align: left !important;
    }}
    /* Tally boxes: full-width strip (team-results.css was shrinking .rec to ~0.75rem) */
    body[data-sandbox-sport="tennis"] .tally-wrap,
    body[data-sandbox-sport="tennis"] .daily-tally {{
      max-width: none !important;
      width: 100% !important;
      margin-left: 0 !important;
      margin-right: 0 !important;
    }}
    body[data-sandbox-sport="tennis"] .daily-tally-head h2 {{
      text-align: left !important;
      font-size: 1.05rem !important;
      font-weight: 800 !important;
      margin: 0 0 10px !important;
      color: #0f172a !important;
    }}
    body[data-sandbox-sport="tennis"] .daily-tally-grid,
    body[data-sandbox-sport="tennis"] .tally-grid {{
      display: grid !important;
      grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
      gap: 12px !important;
      width: 100% !important;
      justify-content: stretch !important;
    }}
    body[data-sandbox-sport="tennis"] .daily-tally-card,
    body[data-sandbox-sport="tennis"] .tally-card {{
      background: #fff !important;
      border: 1px solid #dbe3ee !important;
      border-radius: 12px !important;
      padding: 14px 12px !important;
      min-height: 84px !important;
      width: 100% !important;
      max-width: none !important;
      display: flex !important;
      flex-direction: column !important;
      justify-content: center !important;
      gap: 4px !important;
      text-align: left !important;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
    }}
    body[data-sandbox-sport="tennis"] .daily-tally-card .daily-model,
    body[data-sandbox-sport="tennis"] .tally-card .mlabel {{
      font-size: 0.72rem !important;
      font-weight: 800 !important;
      letter-spacing: 0.04em !important;
      text-transform: uppercase !important;
      color: #64748b !important;
    }}
    body[data-sandbox-sport="tennis"] .daily-tally-card .rec,
    body[data-sandbox-sport="tennis"] .tally-card .rec {{
      font-size: 1.15rem !important;
      font-weight: 800 !important;
      line-height: 1.15 !important;
      color: #0f172a !important;
      margin: 0 !important;
    }}
    body[data-sandbox-sport="tennis"] .daily-tally-card .muted {{
      font-size: 0.78rem !important;
      color: #64748b !important;
    }}
    /* Results cards: denser type matching chart view (team-results / picks-chart). */
    body[data-sandbox-sport="tennis"] .game-card-stack .team-name {{
      font-size: 0.82em !important;
      line-height: 1.2 !important;
    }}
    body[data-sandbox-sport="tennis"] .game-card-stack .win-pct {{
      font-size: 1.15em !important;
    }}
    body[data-sandbox-sport="tennis"] .game-card-stack .pick-card-header,
    body[data-sandbox-sport="tennis"] .game-card-stack .league-badge,
    body[data-sandbox-sport="tennis"] .game-card-stack .game-time {{
      font-size: 0.72em !important;
    }}
    body[data-sandbox-sport="tennis"] .game-card-stack .line-chip-val,
    body[data-sandbox-sport="tennis"] .game-card-stack .model-pick-label,
    body[data-sandbox-sport="tennis"] .game-card-stack .odds-line {{
      font-size: 0.78em !important;
    }}
    @media (max-width: 700px) {{
      body[data-sandbox-sport="tennis"] .daily-tally-grid,
      body[data-sandbox-sport="tennis"] .tally-grid {{
        grid-template-columns: 1fr !important;
      }}
      body[data-sandbox-sport="tennis"] .daily-tally-card .rec,
      body[data-sandbox-sport="tennis"] .tally-card .rec {{
        font-size: 1.05rem !important;
      }}
    }}
    body[data-sandbox-sport="tennis"] .pc-box.correct {{
      background: #ecfdf3; border-color: #bbf7d0;
    }}
    body[data-sandbox-sport="tennis"] .pc-box.wrong {{
      background: #fef2f2; border-color: #fecaca;
    }}
    body[data-sandbox-sport="tennis"] .pl-consensus-records {{
      max-width: none;
      margin-left: 0;
      margin-right: 0;
    }}
  </style>
</head>
<body class="research-site" data-theme="light" data-sandbox-sport="tennis" data-sandbox-sports-chrome="1">
{body}
</body>
</html>"""
    return page


def _purge_non_tennis_stacks(html: str) -> str:
    """Remove leftover sidecar pick stacks (e.g. data-league=\"US Open\")."""
    parts: list[str] = []
    pos = 0
    while True:
        m = re.search(r'<div class="game-card-stack\b', html[pos:], flags=re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        tag_end = html.find(">", abs_start)
        if tag_end < 0:
            parts.append(html[pos:])
            break
        open_tag = html[abs_start : tag_end + 1]
        i = tag_end + 1
        depth = 1
        j = i
        end = -1
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
                    end = next_close + 6
                    break
                j = next_close + 6
        if end < 0:
            parts.append(html[pos:])
            break
        keep = (
            'data-league="TENNIS"' in open_tag
            or "data-league='TENNIS'" in open_tag
            or 'data-league="Tennis"' in open_tag
        )
        if keep:
            parts.append(html[pos:end])
        else:
            parts.append(html[pos:abs_start])
        pos = end
    return "".join(parts)


def _purge_sidecar_date_sections(html: str) -> str:
    """Drop leftover sidecar date-sections that still carry US Open / 50% junk."""
    out = []
    pos = 0
    for m in re.finditer(
        r'<div\b[^>]*\bclass=["\'][^"\']*\bdate-section\b[^"\']*["\'][^>]*>',
        html,
        flags=re.I,
    ):
        out.append(html[pos : m.start()])
        # balanced close
        i = m.end()
        depth = 1
        j = i
        end = None
        while j < len(html) and depth > 0:
            nxt = re.search(r"<div\b|</div>", html[j:], flags=re.I)
            if not nxt:
                break
            tok = nxt.group(0).lower()
            j2 = j + nxt.start()
            if tok.startswith("<div"):
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    end = j2 + len(nxt.group(0))
                    break
            j = j2 + len(nxt.group(0))
        if end is None:
            out.append(html[m.start() :])
            return "".join(out)
        sec = html[m.start() : end]
        # Keep isolation sections (date-YYYY-MM-DD + TENNIS stacks or empty chart wrap).
        if 'data-league="TENNIS"' in sec or "data-league='TENNIS'" in sec:
            out.append(sec)
        pos = end
    out.append(html[pos:])
    return "".join(out)


def _replace_container(html: str, body: str) -> str:
    """Balanced replace of first .container (UFC pattern) — do not leave sidecar body."""
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
                    return html[:i] + "\n" + body + "\n" + html[next_close:]
                j = next_close + 6
    m = re.search(r"(<main\b[^>]*>)([\s\S]*?)(</main>)", html, flags=re.I)
    if m:
        return html[: m.start(2)] + f'<div class="container">\n{body}\n</div>' + html[m.end(2) :]
    if re.search(r"<footer\b", html, re.I):
        return re.sub(
            r"(<footer\b)",
            f'<div class="container">\n{body}\n</div>\n' + r"\1",
            html,
            count=1,
            flags=re.I,
        )
    return html + f'<div class="container">{body}</div>'


def render_tennis_with_chrome(chrome_html: str, which: str = "picks") -> tuple[str, dict[str, Any]]:
    """UFC-style: isolation cards + optional sidecar chrome shell."""
    from shared_chrome import ensure_canonical_chrome

    if which == "results":
        payload = build_tennis_results_payload()
        frag_page = render_tennis_results_html(payload)
        title = "Tennis Results | Prediction Lab"
        meta = {
            "cards": len(payload.get("finals") or []),
            "finals": len(payload.get("finals") or []),
            "source": "isolation_db",
        }
    else:
        payload = build_tennis_picks_payload()
        frag_page = render_tennis_picks_html(payload)
        title = "Tennis Picks | Prediction Lab"
        meta = {
            "cards": len(payload.get("upcoming") or []),
            "upcoming": len(payload.get("upcoming") or []),
            "source": "isolation_db",
        }

    inner = frag_page
    mm = re.search(r"<main\b[^>]*>([\s\S]*?)</main>", frag_page, re.I)
    if mm:
        inner = mm.group(1)
    extra = ""
    gm = re.search(r'(<style id="tennis-mlb-grid-fix">[\s\S]*?</style>)', frag_page, re.I)
    if gm:
        extra += gm.group(1)
    sm = re.search(r"(<script>[\s\S]*?togglePickDetails[\s\S]*?</script>)", frag_page, re.I)
    if sm:
        extra += sm.group(1)

    page_path = "/tennis/" if which == "picks" else "/tennis/results"

    if not chrome_html or "<body" not in chrome_html.lower():
        page = frag_page
        page = ensure_canonical_chrome(page, "tennis", which=which)
        # Results keep Best Performing / LN-L7-Season above cards (chart-parity).
        if which == "results":
            try:
                from share_chrome import build_ml_sport_performance_html
                from team_tabbed_results import build_tennis_payload

                perf = build_ml_sport_performance_html(build_tennis_payload(), sport="tennis")
                if perf and "tennis-perf-analytics" not in page:
                    page = re.sub(
                        r'(</div>\s*<style>[\s\S]*?\.section-tabs[\s\S]*?</style>)',
                        r"\1\n" + perf,
                        page,
                        count=1,
                        flags=re.I,
                    )
            except Exception:
                pass
        from mlb_page_template import apply_mlb_picks_template

        page = apply_mlb_picks_template(page, sport="tennis", which=which)
        return page, meta

    html = chrome_html
    html = re.sub(r"(<title>)(.*?)(</title>)", rf"\1{title}\3", html, count=1, flags=re.I | re.S)
    html = _replace_container(html, inner + extra)
    html = _purge_sidecar_date_sections(html)
    html = _purge_non_tennis_stacks(html)
    # Sidecar also dumps SEO preview lists / jump links — hide common leftovers.
    html = re.sub(
        r'<nav\b[^>]*aria-label=["\']Tennis previews["\'][^>]*>[\s\S]*?</nav>',
        "",
        html,
        flags=re.I,
    )
    html = html.replace("/tennis-picks", "/tennis/")
    html = html.replace("/tennis-results", "/tennis/results")
    if "tennis-chrome-isolate" not in html:
        html = html.replace(
            "</head>",
            "<style id=\"tennis-chrome-isolate\">"
            "header.pl2-header, header.pl2-header a, header.pl2-header a:hover{color:inherit}"
            "</style></head>",
            1,
        )
    if 'id="tennis-mlb-grid-fix"' not in html and GRID_CSS not in html:
        html = html.replace("</head>", GRID_CSS + "</head>", 1)
    html = ensure_canonical_chrome(html, "tennis", which=which)
    from mlb_page_template import apply_mlb_picks_template

    html = apply_mlb_picks_template(html, sport="tennis", which=which)
    return html, meta
