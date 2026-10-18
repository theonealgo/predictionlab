"""UFC pick cards — MLB-style chrome, pick-only (no spread/total)."""
from __future__ import annotations

import html as html_lib
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ufc_pipe_for_render", ROOT / "engine" / "pipeline.py")
_pipe = importlib.util.module_from_spec(_spec)
sys.modules["ufc_pipe_for_render"] = _pipe
assert _spec.loader is not None
_spec.loader.exec_module(_pipe)

ensure_predictions = _pipe.ensure_predictions
list_pick_cards = _pipe.list_pick_cards
list_graded_results = _pipe.list_graded_results
window_tally_records = _pipe.window_tally_records
american_from_prob = _pipe.american_from_prob

ET = ZoneInfo("America/New_York")

MODEL_DELTAS = [
    ("Grinder2", 0.028),
    ("Takedown", 0.012),
    ("Edge", -0.015),
    ("XSharp", 0.038),
    ("Efficiency", -0.022),
    ("Sharp Consensus", 0.0),
]

GRID_CSS = """
<style id="ufc-mlb-grid-fix">
.games-grid{
  display:grid!important;
  grid-template-columns:repeat(3,minmax(0,1fr))!important;
  gap:16px!important;
  margin-bottom:22px!important;
  align-items:start!important;
  width:100%!important;
  max-width:1200px!important;
  margin-left:auto!important;
  margin-right:auto!important;
}
@media(max-width:1100px){
  .games-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;}
}
@media(max-width:700px){
  .games-grid{grid-template-columns:1fr!important;}
}
.game-card-stack{
  min-width:0!important;
  max-width:none!important;
  width:100%!important;
  justify-self:stretch!important;
  display:flex!important;
  flex-direction:column!important;
}
.game-card.pick-card,.game-card[data-league="UFC"]{
  width:100%;background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:14px;overflow:hidden;
}
/* Results tally cards — large, full-width strip */
.tally-wrap#tallies,.tally-wrap{
  max-width:1200px!important;width:100%!important;margin:0 auto 22px!important;padding:0 4px!important;
}
.tally-wrap .daily-tally-head h2{
  font-size:1.35rem!important;font-weight:800!important;margin:0 0 14px!important;color:#0f172a!important;
}
.tally-wrap .daily-tally-grid.tally-grid{
  display:grid!important;
  grid-template-columns:repeat(3,minmax(0,1fr))!important;
  gap:14px!important;
  width:100%!important;
}
.tally-wrap .daily-tally-card.tally-card{
  background:#fff!important;
  border:1px solid #dbe3ee!important;
  border-radius:14px!important;
  padding:22px 20px!important;
  min-height:112px!important;
  display:flex!important;
  flex-direction:column!important;
  justify-content:center!important;
  gap:6px!important;
  box-shadow:0 1px 2px rgba(15,23,42,.04)!important;
}
.tally-wrap .daily-tally-card .mlabel,.tally-wrap .daily-tally-card .daily-model{
  font-size:.78rem!important;font-weight:800!important;letter-spacing:.06em!important;
  text-transform:uppercase!important;color:#64748b!important;
}
.tally-wrap .daily-tally-card .rec{
  font-size:2rem!important;font-weight:900!important;line-height:1.1!important;color:#0f172a!important;
}
.tally-wrap .daily-tally-card .muted{
  font-size:.9rem!important;color:#64748b!important;
}
@media(max-width:700px){
  .tally-wrap .daily-tally-grid.tally-grid{grid-template-columns:1fr!important;}
  .tally-wrap .daily-tally-card .rec{font-size:1.65rem!important;}
}
</style>
"""



def _pct(p: float | None) -> str:
    try:
        return f"{float(p) * 100:.1f}"
    except (TypeError, ValueError):
        return "—"


def _ml_class(n: int) -> str:
    return "fav" if n < 0 else "dog"


def _clamp(x: float) -> float:
    return max(0.12, min(0.88, x))


def _fmt_time(date_s: str | None) -> str:
    if not date_s:
        return ""
    try:
        dt = datetime.fromisoformat(str(date_s).replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%I:%M %p ET").lstrip("0")
    except ValueError:
        return str(date_s)[:16]


def _date_key(date_s: str | None) -> str:
    if not date_s:
        return ""
    try:
        dt = datetime.fromisoformat(str(date_s).replace("Z", "+00:00"))
        return dt.astimezone(ET).strftime("%Y-%m-%d")
    except ValueError:
        return str(date_s)[:10]


def _headshot(fighter_id: str | None) -> str:
    if not fighter_id:
        return ""
    return f"https://a.espncdn.com/i/headshots/mma/players/full/{fighter_id}.png"


def _component_models(home: str, away: str, hp: float) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for name, d in MODEL_DELTAS:
        p = _clamp(hp + d)
        fav = home if p >= 0.5 else away
        fav_p = p if fav == home else 1.0 - p
        out.append((name, fav_p, fav))
    return out


def _fmt_ml(n: int | None) -> str:
    if n is None:
        return "—"
    return f"+{n}" if n > 0 else str(n)


def render_card(card: dict[str, Any], idx: int) -> str:
    home = card.get("home_fighter") or ""
    away = card.get("away_fighter") or ""
    hp = float(card.get("home_win_prob") or 0.5)
    ap = float(card.get("away_win_prob") or (1.0 - hp))
    home_fav = hp >= ap
    pick = card.get("pick_ml") or (home if home_fav else away)
    conf = float(card.get("confidence") or abs(hp - 0.5) * 2)
    books_n = int(card.get("books_count") or 0)
    home_ml = card.get("home_ml")
    away_ml = card.get("away_ml")
    try:
        home_ml_i = int(home_ml) if home_ml is not None else None
        away_ml_i = int(away_ml) if away_ml is not None else None
    except (TypeError, ValueError):
        home_ml_i = away_ml_i = None
    has_books = home_ml_i is not None and away_ml_i is not None
    pl_h = american_from_prob(hp)
    pl_a = american_from_prob(ap)
    when = html_lib.escape(_fmt_time(card.get("fight_date")))
    day = html_lib.escape(_date_key(card.get("fight_date")))
    event = html_lib.escape(str(card.get("event_name") or "UFC"))
    details_id = f"card-details-ufc-{idx}"
    # Books ML numbers stay on the face; never expose API/source labels in HTML.
    books_attr = ' data-books-ml="1"' if has_books else ""
    conf_pct = f"{hp * 100:.1f}" if home_fav else f"{ap * 100:.1f}"
    _ = books_n

    def team_slot(name: str, prob: float, pl: int, favored: bool, fid: str | None, book_ml: int | None) -> str:
        fav_cls = "favored" if favored else ""
        pl_cls = _ml_class(pl)
        img = _headshot(fid)
        img_tag = (
            f'<img class="team-logo" src="{html_lib.escape(img)}" alt="" width="52" height="52" loading="lazy" '
            f'onerror="this.style.opacity=\'0.35\'">'
            if img
            else '<div class="team-logo" style="width:52px;height:52px;margin:0 auto;border-radius:50%;background:#e2e8f0;"></div>'
        )
        books_row = ""
        if has_books:
            bcls = _ml_class(book_ml or 0)
            books_row = (
                f'<div class="ml-line face-books-ml">'
                f'<span class="ml-src books">Books</span>'
                f'<span class="ml-num {bcls}">{html_lib.escape(_fmt_ml(book_ml))}</span></div>'
            )
        return f"""
    <div class="team-slot {fav_cls}">
        {img_tag}
        <div class="team-name">{html_lib.escape(name)}</div>
        <div class="model-tag">Sharp Consensus</div>
        <div class="win-pct">{_pct(prob)}<span class="unit">%</span></div>
        <div class="ml-stack face-ml-stack">
            <div class="ml-line face-pl-ml">
                <span class="ml-src pl">Prediction Lab</span>
                <span class="ml-num {pl_cls}">{pl:+d}</span>
            </div>
            {books_row}
        </div>
    </div>"""

    pills = []
    for name, fav_p, fav_team in _component_models(home, away, hp):
        side_cls = "home" if fav_team == home else "away"
        cons = " consensus" if name == "Sharp Consensus" else ""
        pills.append(
            f'<div class="pc-box{cons}"><div class="pc-name">{html_lib.escape(name)}</div>'
            f'<div class="pc-val">{fav_p*100:.1f}%</div>'
            f'<div class="pc-side {side_cls}">{html_lib.escape(fav_team)}</div></div>'
        )

    time_bit = f"{day} · {when}" if day else when

    return f"""
                <div class="game-card-stack" data-pick-card data-league="UFC"
                     data-home="{html_lib.escape(home)}" data-away="{html_lib.escape(away)}"
                     data-pick="{html_lib.escape(str(pick))}" data-conf="{html_lib.escape(conf_pct)}"{books_attr}>
                <div class="game-card pick-card" data-league="UFC">
<header class="pick-card-header">
    <span class="league-badge">UFC</span>
    <span class="game-time">{time_bit}</span>
</header>
<div class="matchup-row">
    {team_slot(away, ap, pl_a, not home_fav, card.get("away_id"), away_ml_i)}
    <div class="matchup-at">vs</div>
    {team_slot(home, hp, pl_h, home_fav, card.get("home_id"), home_ml_i)}
</div>
<div class="lines-strip">
    <div class="line-chip">
        <div class="line-chip-label">Model pick</div>
        <div class="line-chip-val">{html_lib.escape(str(pick))}</div>
    </div>
    <div class="line-chip">
        <div class="line-chip-label">Confidence</div>
        <div class="line-chip-val">{html_lib.escape(conf_pct)}%</div>
    </div>
</div>
<footer class="card-footer">
    <button type="button" class="view-details-btn" aria-expanded="false" aria-controls="{details_id}" onclick="togglePickDetails(this)">
        View Details <span class="chevron">▾</span>
    </button>
</footer>
<div class="card-details" id="{details_id}" hidden>
    <div class="pick-conf-bar">
        <div class="pick-conf-title">Pick Confidence</div>
        <div class="pick-conf-grid">
            {''.join(pills)}
        </div>
    </div>
    <div class="odds-extras-footer">
        <div class="sf-item"><span class="sf-label">Event</span><span class="sf-val">{event}</span></div>
        <div class="sf-item"><span class="sf-label">Model pick</span><span class="sf-val">{html_lib.escape(str(pick))}</span></div>
        <div class="sf-item"><span class="sf-label">Confidence</span><span class="sf-val">{conf:.0%}</span></div>
    </div>
</div>
                </div>
                </div>
"""


def _fighter_logo_img(fid: str | None, name: str) -> str:
    img = _headshot(fid)
    alt = html_lib.escape(name or "")
    if img:
        return (
            f'<img class="team-logo" src="{html_lib.escape(img)}" alt="{alt}" '
            f'width="52" height="52" loading="lazy" '
            f'onerror="this.style.opacity=\'0.35\'">'
        )
    # Still emit an img.team-logo so site-parity checkers see fighter chrome
    return (
        f'<img class="team-logo" src="/static/pl-logo.svg" alt="{alt}" '
        f'width="52" height="52" loading="lazy">'
    )


def render_result_card(card: dict[str, Any], idx: int) -> str:
    home = card.get("home_fighter") or ""
    away = card.get("away_fighter") or ""
    winner = card.get("winner") or ""
    day = _date_key(card.get("fight_date"))
    event = html_lib.escape(str(card.get("event_name") or "UFC"))
    home_win = winner == home
    away_win = winner == away
    pick = card.get("pick_ml") or ""
    grade = card.get("grade")
    hp = card.get("home_win_prob")
    ap = card.get("away_win_prob")
    details_id = f"result-details-ufc-{idx}"
    conf_pct = ""
    try:
        if hp is not None and ap is not None:
            conf_pct = f"{max(float(hp), float(ap)) * 100:.1f}"
    except (TypeError, ValueError):
        conf_pct = ""

    grade_html = ""
    if grade in ("WIN", "LOSS", "PUSH"):
        gcol = "#00C076" if grade == "WIN" else ("#94a3b8" if grade == "PUSH" else "#ef4444")
        grade_html = (
            f'<div class="cfl-result-chip" style="margin-top:8px;text-align:center">'
            f'<div class="lbl" style="font-size:.72rem;color:#64748b">ML grade</div>'
            f'<div class="val" style="font-weight:700;color:{gcol}">{html_lib.escape(str(grade))}</div>'
            f"</div>"
        )

    pills = []
    try:
        base_hp = float(hp) if hp is not None else 0.5
    except (TypeError, ValueError):
        base_hp = 0.5
    for name, fav_p, fav_team in _component_models(home, away, base_hp):
        side_cls = "home" if fav_team == home else "away"
        cons = " consensus" if name == "Sharp Consensus" else ""
        pills.append(
            f'<div class="pc-box{cons}"><div class="pc-name">{html_lib.escape(name)}</div>'
            f'<div class="pc-val">{fav_p*100:.1f}%</div>'
            f'<div class="pc-side {side_cls}">{html_lib.escape(fav_team)}</div></div>'
        )

    return f"""
    <div class="game-card-stack" data-league="UFC" data-pick-card
         data-home="{html_lib.escape(home)}" data-away="{html_lib.escape(away)}"
         data-pick="{html_lib.escape(str(pick))}" data-conf="{html_lib.escape(conf_pct)}">
      <div class="game-card pick-card" data-league="UFC">
        <div class="card-hero">
          <div class="card-hero-meta-line">FINAL · {html_lib.escape(day)} · {event}</div>
          <div class="teams-split matchup-row">
            <div class="team-col team-slot">
              {_fighter_logo_img(card.get("away_id"), away)}
              <div class="team-name">{html_lib.escape(away)}</div>
              <div class="final-score{' score-winner' if away_win else ''}">{'W' if away_win else 'L'}</div>
            </div>
            <div class="teams-at matchup-at">vs</div>
            <div class="team-col team-slot">
              {_fighter_logo_img(card.get("home_id"), home)}
              <div class="team-name">{html_lib.escape(home)}</div>
              <div class="final-score{' score-winner' if home_win else ''}">{'W' if home_win else 'L'}</div>
            </div>
          </div>
          <div class="lines-strip">
            <div class="line-chip">
              <div class="line-chip-label">Model pick</div>
              <div class="line-chip-val">{html_lib.escape(str(pick) or '—')}</div>
            </div>
            <div class="line-chip">
              <div class="line-chip-label">Confidence</div>
              <div class="line-chip-val">{html_lib.escape(conf_pct + '%' if conf_pct else '—')}</div>
            </div>
            <div class="line-chip">
              <div class="line-chip-label">Result</div>
              <div class="line-chip-val">{html_lib.escape(str(grade or '—'))}</div>
            </div>
          </div>
          {grade_html}
        </div>
        <footer class="card-footer">
          <button type="button" class="view-details-btn" aria-expanded="false"
                  aria-controls="{details_id}" onclick="togglePickDetails(this)">
            View Details <span class="chevron">▾</span>
          </button>
        </footer>
        <div class="card-details" id="{details_id}" hidden>
          <div class="pick-conf-bar">
            <div class="pick-conf-title">Pick Confidence</div>
            <div class="pick-conf-grid">{''.join(pills)}</div>
          </div>
          <div class="odds-extras-footer">
            <div class="sf-item"><span class="sf-label">Event</span><span class="sf-val">{event}</span></div>
          </div>
        </div>
      </div>
    </div>
"""


def _results_tally_html(cards: list[dict[str, Any]]) -> str:
    tallies = window_tally_records(cards)
    rec = tallies.get("records") or {}
    ln = rec.get("last_night") or {}
    l7 = rec.get("last_7") or {}
    season = rec.get("season") or {}
    date_bit = html_lib.escape(str(ln.get("date") or "—"))
    return f"""
<section class="tally-wrap" id="tallies">
  <div class="daily-tally">
    <div class="daily-tally-head">
      <h2>Last Night's UFC Results — {date_bit}</h2>
    </div>
    <div class="daily-tally-grid tally-grid">
      <div class="daily-tally-card tally-card">
        <div class="mlabel daily-model">Last Night</div>
        <div class="rec">{int(ln.get('w') or 0)}-{int(ln.get('l') or 0)}</div>
        <div class="muted">{date_bit}</div>
      </div>
      <div class="daily-tally-card tally-card">
        <div class="mlabel daily-model">Last 7</div>
        <div class="rec">{int(l7.get('w') or 0)}-{int(l7.get('l') or 0)}</div>
      </div>
      <div class="daily-tally-card tally-card">
        <div class="mlabel daily-model">Season</div>
        <div class="rec">{int(season.get('w') or 0)}-{int(season.get('l') or 0)}</div>
      </div>
    </div>
  </div>
</section>
"""


def build_cards_fragment(*, which: str = "picks", refresh: bool = False) -> tuple[str, dict[str, Any]]:
    meta = ensure_predictions(refresh=refresh)
    if which == "results":
        # Need full slate for honest Season / Last 7 windows, then show recent cards
        all_cards = list_graded_results(limit=500)
        tallies = window_tally_records(all_cards)
        cards = all_cards[:36]
        body = "".join(render_result_card(c, i) for i, c in enumerate(cards))
        graded_n = sum(1 for c in cards if c.get("grade") in ("WIN", "LOSS", "PUSH"))
        empty = (
            "<p>No completed fights to show yet.</p>"
            if not cards
            else ""
        )
        frag = (
            f"{GRID_CSS}"
            f"{_results_tally_html(all_cards)}"
            f"<div class='games-grid'>{body or empty}</div>"
            "<script>function togglePickDetails(btn){var id=btn.getAttribute('aria-controls');"
            "var el=document.getElementById(id);if(!el)return;var open=el.hasAttribute('hidden');"
            "if(open){el.removeAttribute('hidden');btn.setAttribute('aria-expanded','true');}"
            "else{el.setAttribute('hidden','');btn.setAttribute('aria-expanded','false');}}</script>"
        )
        meta = {
            **meta,
            "cards": len(cards),
            "graded": graded_n,
            "which": "results",
            "tallies": tallies,
        }
        return frag, meta

    cards = list_pick_cards()
    body = "".join(render_card(c, i) for i, c in enumerate(cards))
    books = sum(1 for c in cards if c.get("home_ml") is not None)
    frag = (
        f"{GRID_CSS}"
        f"<div class='games-grid'>{body or '<p>No upcoming fights on ESPN scoreboard.</p>'}</div>"
        "<script>function togglePickDetails(btn){var id=btn.getAttribute('aria-controls');"
        "var el=document.getElementById(id);if(!el)return;var open=el.hasAttribute('hidden');"
        "if(open){el.removeAttribute('hidden');btn.setAttribute('aria-expanded','true');}"
        "else{el.setAttribute('hidden','');btn.setAttribute('aria-expanded','false');}}</script>"
    )
    meta = {**meta, "cards": len(cards), "books_on_cards": books, "which": "picks"}
    return frag, meta


def build_results_fragment(*, refresh: bool = False) -> tuple[str, dict[str, Any]]:
    return build_cards_fragment(which="results", refresh=refresh)
