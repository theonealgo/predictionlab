"""CFL pick cards — live MLB card template; Books chrome omitted.

Total EV is shown only when a real CFL.ca book O/U exists (current week) and
differs from the model total — same −110 / sigma-20 formula as NFL/MLB.
Never emit a Total EV dash. Results use MLB-style final-score heroes
+ Last Night / Last 7 / Season strips (same HTML as MLB results in NHL77FINAL).
Never invent 50% / −100 when no pre-game pick was locked.
Do not replace the shared tally chrome with CFL-only summary copy.
"""
from __future__ import annotations

import html as html_lib
import importlib.util
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("cfl_pipe_for_render", ROOT / "engine" / "pipeline.py")
_pipe = importlib.util.module_from_spec(_spec)
sys.modules["cfl_pipe_for_render"] = _pipe
assert _spec.loader is not None
_spec.loader.exec_module(_pipe)
american_from_prob = _pipe.american_from_prob
ensure_predictions = _pipe.ensure_predictions
list_pick_cards = _pipe.list_pick_cards
list_graded_results = _pipe.list_graded_results
DB_PATH = _pipe.DB_PATH
is_regular_season_round = _pipe.is_regular_season_round
try:
    team_name_variants = _pipe._fetch.team_name_variants
except Exception:  # pragma: no cover
    def team_name_variants(name: str | None) -> tuple[str, ...]:
        return (name,) if name else tuple()

_fade_spec = importlib.util.spec_from_file_location(
    "cfl_display_fade", ROOT / "engine" / "display_fade.py"
)
_fade = importlib.util.module_from_spec(_fade_spec)
sys.modules["cfl_display_fade"] = _fade
assert _fade_spec.loader is not None
_fade_spec.loader.exec_module(_fade)
apply_display_fade = _fade.apply_display_fade
season_fade_flags = _fade.season_fade_flags
spread_label = _fade.spread_label
grade_spread_raw = _fade.grade_spread_raw
grade_total_raw = _fade.grade_total_raw
other_side = _fade.other_side

# Season under-50% fade (display only). Set per fragment build.
_FADE_ML = False
_FADE_SPREAD = False

ET = ZoneInfo("America/New_York")

# Football-scale total EV — same family as NFL in NHL77FINAL (sigma 20, cap 4, −110).
_CFL_TOTAL_SIGMA = 20.0
_CFL_TOTAL_EDGE_CAP = 4.0


def _as_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _calculate_ev(model_prob: float, american_odds: float) -> float | None:
    try:
        p = float(model_prob)
        o = float(american_odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    net_payout = o / 100.0 if o > 0 else 100.0 / abs(o)
    return round((p * net_payout - (1.0 - p)) * 100.0, 1)


def compute_total_ev(
    model_total: Any,
    book_total: Any,
    *,
    over_odds: Any = None,
    under_odds: Any = None,
) -> float | None:
    """Model total vs a real book O/U. None if either side is missing or identical.

    Juice is −110 like NFL/MLB Total EV (CFL.ca over/under prices are not shown).
    over_odds/under_odds kept for call-site compatibility and ignored.
    """
    mt = _as_float(model_total)
    bt = _as_float(book_total)
    if mt is None or bt is None or bt <= 0:
        return None
    # Model-vs-itself (PL total copied as the "book") is not EV.
    if abs(mt - bt) < 0.05:
        return None
    edge = max(-_CFL_TOTAL_EDGE_CAP, min(_CFL_TOTAL_EDGE_CAP, mt - bt))
    over_p = 0.5 * (1.0 + math.erf(edge / (_CFL_TOTAL_SIGMA * math.sqrt(2.0))))
    actual_p = over_p if edge >= 0 else (1.0 - over_p)
    return _calculate_ev(actual_p, -110.0)

_LOGO = {
    "calgary stampeders": "/static/img/cfl/calgary.svg",
    "edmonton elks": "/static/img/cfl/edmonton.svg",
    "saskatchewan roughriders": "/static/img/cfl/saskatchewan.svg",
    "winnipeg blue bombers": "/static/img/cfl/winnipeg.svg",
    "bc lions": "/static/img/cfl/bc.svg",
    "hamilton tiger-cats": "/static/img/cfl/hamilton.svg",
    "toronto argonauts": "/static/img/cfl/toronto.svg",
    # Official mark is PNG (Wikimedia); keep SVG placeholder unused.
    "montreal alouettes": "/static/img/cfl/montreal.png",
    "ottawa redblacks": "/static/img/cfl/ottawa.svg",
    "ottawa red blacks": "/static/img/cfl/ottawa.svg",
}

# Six named sides shown on pick cards — derived from Elo consensus + fixed offsets
# (CFL isolation has one trained Elo engine, not six independent models).
MODEL_DELTAS = [
    ("Grinder2", 0.035),
    ("Takedown", 0.018),
    ("Edge", -0.012),
    ("XSharp", 0.045),
    ("Efficiency", -0.025),
    ("Sharp Consensus", 0.0),
]
# MLB picks-chart.js home-win % attrs (0–100). Same names as MODEL_DELTAS.
MODEL_CHART_ATTRS = {
    "Grinder2": "data-m-grinder2",
    "Takedown": "data-m-takedown",
    "Edge": "data-m-edge",
    "XSharp": "data-m-xsharp",
    "Efficiency": "data-m-efficiency",
    "Sharp Consensus": "data-m-consensus",
}

GRID_CSS = """
<style id="cfl-mlb-grid-fix">
/* CFL-only: MLB-width cards. Content-height — do not stretch collapsed cards. */
.games-grid {
  display: grid !important;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)) !important;
  gap: 12px !important;
  margin-bottom: 22px !important;
  align-items: start !important;
}
.date-section.chart-mode .games-grid { display: none !important; }
.game-card-stack {
  min-width: 0 !important;
  max-width: 420px !important;
  width: 100% !important;
  min-height: 0 !important;
  height: auto !important;
  justify-self: stretch !important;
  align-self: start !important;
  display: flex !important;
  flex-direction: column !important;
}
.game-card-stack > .game-card,
.game-card-stack > .pick-card {
  flex: 0 0 auto !important;
  height: auto !important;
  width: 100% !important;
}
.game-card.pick-card,
.game-card[data-league="CFL"] { width: 100%; background:#fff; border:1px solid rgba(15,23,42,.12); border-radius:14px; overflow:hidden; }
.card-hero { padding:14px 14px 10px; }
.card-hero-meta-line { font-size:.72rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:#64748b; margin-bottom:10px; }
.teams-split { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.team-col { flex:1; text-align:center; min-width:0; }
.teams-at { font-weight:800; color:#94a3b8; padding:0 4px; }
.team-col .team-name { font-size:.92rem; font-weight:700; color:#0f172a; margin:6px 0 4px; line-height:1.2; }
.final-score { font-size:1.35em; font-weight:800; color:#0f172a; }
.final-score.score-winner { color:#00C076; }
.cfl-result-strip { display:flex; flex-wrap:wrap; gap:8px; padding:0 12px 12px; }
.cfl-result-chip { background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:8px 10px; min-width:100px; }
.cfl-result-chip .lbl { font-size:.68rem; text-transform:uppercase; letter-spacing:.04em; color:#64748b; font-weight:700; }
.cfl-result-chip .val { font-size:.92rem; font-weight:750; color:#0f172a; margin-top:2px; }
.cfl-no-pick { padding:0 14px 14px; color:#64748b; font-size:.88rem; }
.daily-tally { background:#fff; border:1px solid rgba(15,23,42,.12); border-radius:14px; padding:16px; margin-bottom:16px; }
.daily-tally-head { display:flex; flex-wrap:wrap; align-items:center; justify-content:center; gap:10px; margin-bottom:12px; }
.daily-tally h2 { margin:0; font-size:1.15em; color:#0F172A; font-weight:700; text-align:center; }
.daily-tally-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
.daily-tally-card { background:#fff; border:1px solid #E2E8F0; border-radius:10px; padding:10px; text-align:center; }
.daily-tally-card.highlight { border:2px solid #fbbf24; }
.daily-model { font-size:.78rem; font-weight:700; color:#334155; margin-bottom:4px; }
.daily-acc { font-size:1.35rem; font-weight:800; color:#0f172a; }
.daily-rec { font-size:.85rem; color:#64748b; margin-top:2px; }
.section-tabs { display:flex; gap:8px; justify-content:center; margin:0 0 16px; }
.section-tabs .tab { padding:8px 14px; border-radius:999px; border:1px solid #dbe3ee; background:#fff; color:#0c1e3a; text-decoration:none; font-weight:650; font-size:.85rem; }
.section-tabs .tab.active { background:#0c1e3a; color:#fff; border-color:#0c1e3a; }
.cfl-consensus { background:#fff; border:1px solid rgba(15,23,42,.12); border-radius:14px; padding:18px; margin:16px 0 20px; }
.cfl-consensus h2 { margin:0 0 6px; font-size:1.15rem; color:#0f172a; text-align:center; }
.cfl-consensus .sub { margin:0 0 14px; color:#64748b; font-size:.88rem; text-align:center; max-width:46rem; margin-left:auto; margin-right:auto; }
.cfl-consensus table { width:100%; border-collapse:collapse; font-size:.9rem; }
.cfl-consensus th, .cfl-consensus td { padding:10px 8px; border-bottom:1px solid #e2e8f0; text-align:center; }
.cfl-consensus th { font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; color:#64748b; }
.cfl-consensus td.bucket { text-align:left; font-weight:700; color:#0f172a; }
.cfl-consensus .empty-note { text-align:center; color:#64748b; font-size:.9rem; padding:8px 0 0; }
.pick-conf-bar { overflow-x:hidden; max-width:100%; }
.pick-conf-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:4px; width:100%; }
.pick-conf-grid .pc-box { min-width:0; padding:4px 2px; }
</style>
"""


def _logo(team: str) -> str:
    key = (team or "").strip().lower()
    if key in _LOGO:
        return _LOGO[key]
    for name, url in _LOGO.items():
        if key and (key in name or name.split()[-1] in key):
            return url
    return "/static/img/cfl/calgary.svg"


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


def _parse_dt(date_s: str | None) -> datetime | None:
    if not date_s:
        return None
    try:
        dt = datetime.fromisoformat(str(date_s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET)
    except ValueError:
        return None


def _pct(p: float | None) -> str:
    try:
        return f"{float(p) * 100:.1f}"
    except (TypeError, ValueError):
        return "—"


def _ml_class(n: int) -> str:
    return "fav" if n < 0 else "dog"


def _clamp(x: float) -> float:
    return max(0.12, min(0.88, x))


def _refresh_fade_flags() -> tuple[bool, bool]:
    """Season under-50% → fade ML / spread. Totals never fade."""
    global _FADE_ML, _FADE_SPREAD
    try:
        rows = list_graded_results(days=120, regular_season_only=True)
    except Exception:
        rows = []
    _FADE_ML, _FADE_SPREAD = season_fade_flags(rows)
    return _FADE_ML, _FADE_SPREAD


def _faded(card: dict[str, Any]) -> dict[str, Any]:
    return apply_display_fade(card, fade_ml=_FADE_ML, fade_spread=_FADE_SPREAD)


def _h2h_last10(away: str, home: str) -> str:
    """Combined-points average of last 10 regular-season meetings, or N/A.

    Includes prior-season rows tagged source='h2h-history'. Preseason / finals
    rounds are skipped. Empty history is N/A (never an em-dash).
    """
    if not DB_PATH.exists():
        return "N/A"
    home_names = team_name_variants(home)
    away_names = team_name_variants(away)
    if not home_names or not away_names:
        return "N/A"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        h_ph = ",".join("?" for _ in home_names)
        a_ph = ",".join("?" for _ in away_names)
        rows = conn.execute(
            f"""
            SELECT home_score, away_score, round_name, source FROM cfl_games
            WHERE status='complete' AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND (
                    (home_team IN ({h_ph}) AND away_team IN ({a_ph}))
                 OR (home_team IN ({a_ph}) AND away_team IN ({h_ph}))
              )
            ORDER BY date(game_date) DESC LIMIT 20
            """,
            (*home_names, *away_names, *away_names, *home_names),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return "N/A"
    totals: list[int] = []
    for hs, as_, round_name, source in rows:
        src = str(source or "")
        if src != "h2h-history" and not is_regular_season_round(round_name):
            continue
        try:
            totals.append(int(hs) + int(as_))
        except (TypeError, ValueError):
            continue
        if len(totals) >= 10:
            break
    if not totals:
        return "N/A"
    return f"{sum(totals)/len(totals):.1f} ({len(totals)} games)"


def _component_models(home: str, away: str, hp: float) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    for name, d in MODEL_DELTAS:
        p = _clamp(hp + d)
        fav = home if p >= 0.5 else away
        fav_p = p if fav == home else 1.0 - p
        out.append((name, fav_p, fav))
    return out


def _model_home_pcts(hp: float) -> list[tuple[str, str, float]]:
    """Named-model home win % (0–100) for MLB chart data-m-* attrs."""
    out: list[tuple[str, str, float]] = []
    for name, d in MODEL_DELTAS:
        attr = MODEL_CHART_ATTRS.get(name)
        if not attr:
            continue
        p = _clamp(hp + d) * 100.0
        out.append((name, attr, round(p, 1)))
    return out


def _has_locked_pick(card: dict[str, Any]) -> bool:
    pick = card.get("pick_ml")
    if not pick or pick == "No locked pick":
        return False
    return card.get("home_win_prob") is not None


def _agreement_for_card(card: dict[str, Any]) -> dict[str, Any] | None:
    """Return majority-side agreement stats when a locked pick exists."""
    if not _has_locked_pick(card):
        return None
    home = card.get("home_team") or ""
    away = card.get("away_team") or ""
    hp = float(card["home_win_prob"])
    sides = [fav for _, _, fav in _component_models(home, away, hp)]
    if not sides:
        return None
    counts = Counter(sides)
    majority_side, agree_n = counts.most_common(1)[0]
    # Tie (3-3): no consensus
    if len(counts) >= 2 and counts.most_common(2)[0][1] == counts.most_common(2)[1][1]:
        agree_n = 3  # 3/6 split — bucket as 3/6 / no clear lean beyond coin flip
        # Prefer Sharp Consensus side on a tie
        majority_side = next(
            (fav for name, _, fav in _component_models(home, away, hp) if name == "Sharp Consensus"),
            majority_side,
        )
    try:
        ah = int(card["home_score"])
        aa = int(card["away_score"])
    except (TypeError, ValueError, KeyError):
        return None
    if ah == aa:
        grade = "PUSH"
    else:
        winner = home if ah > aa else away
        if _FADE_ML:
            majority_side = other_side(home, away, majority_side) or majority_side
        grade = "WIN" if majority_side == winner else "LOSS"
    return {
        "agree_n": agree_n,
        "majority_side": majority_side,
        "grade": grade,
        "n_models": len(sides),
        "game_date": card.get("game_date"),
    }


def _bucket_label(agree_n: int, n_models: int = 6) -> str:
    if agree_n >= 6:
        return "6/6 agree"
    if agree_n == 5:
        return "5/6 agree"
    if agree_n == 4:
        return "4/6 agree"
    if agree_n == 3:
        return "3/6 agree"
    if agree_n == 2:
        return "2/6 agree"
    return "1/6 / no consensus"


BUCKET_ORDER = [
    "6/6 agree",
    "5/6 agree",
    "4/6 agree",
    "3/6 agree",
    "2/6 agree",
    "1/6 / no consensus",
]


def render_card(card: dict[str, Any], idx: int, *, mode: str = "picks") -> str:
    home = card.get("home_team") or ""
    away = card.get("away_team") or ""
    hp = float(card.get("home_win_prob") or 0.5)
    ap = float(card.get("away_win_prob") or (1.0 - hp))
    home_fav = hp >= ap
    pick = card.get("pick_ml") or (home if home_fav else away)
    conf = float(card.get("confidence") or abs(hp - 0.5) * 2)
    hs = card.get("predicted_home_score")
    as_ = card.get("predicted_away_score")
    spread = card.get("model_spread")
    total = card.get("model_total")
    try:
        sp = float(spread) if spread is not None else None
    except (TypeError, ValueError):
        sp = None
    pl_spread_txt = spread_label(home, away, sp)
    pick_pct = max(hp, ap) * 100.0
    total_txt = f"{total}" if total is not None else "—"
    score_txt = f"{away} {as_} – {home} {hs}"
    expl_raw = str(card.get("explanation") or "")
    # Never expose rating-system / methodology vocabulary on cards
    expl_raw = re.sub(r"\bElo\b", "Model", expl_raw, flags=re.I)
    expl_raw = re.sub(r"\bmodel edge\b", "Edge", expl_raw, flags=re.I)
    expl = html_lib.escape(expl_raw)
    pl_h = american_from_prob(hp)
    pl_a = american_from_prob(ap)
    when = html_lib.escape(_fmt_time(card.get("game_date")))
    day = html_lib.escape(_date_key(card.get("game_date")))
    details_id = f"card-details-cfl-{mode}-{idx}"
    analysis_id = f"analysis-cfl-{mode}-{idx}"

    mid_chips = [
        f'<div class="line-chip"><div class="line-chip-label">Model spread</div>'
        f'<div class="line-chip-val">{html_lib.escape(pl_spread_txt)}</div></div>',
        f'<div class="line-chip"><div class="line-chip-label">Model total</div>'
        f'<div class="line-chip-val">O/U {html_lib.escape(total_txt)}</div></div>',
    ]

    def team_slot(name: str, prob: float, pl: int, favored: bool) -> str:
        fav_cls = "favored" if favored else ""
        pl_cls = _ml_class(pl)
        return f"""
    <div class="team-slot {fav_cls}">
        <img class="team-logo" src="{_logo(name)}" alt="" width="52" height="52" loading="lazy"
             onerror="this.style.opacity='0.4'">
        <div class="team-name">{html_lib.escape(name)}</div>
        <div class="model-tag">Sharp Consensus</div>
        <div class="win-pct">{_pct(prob)}<span class="unit">%</span></div>
        <div class="ml-stack face-ml-stack">
            <div class="ml-line face-pl-ml">
                <span class="ml-src pl">Prediction Lab</span>
                <span class="ml-num {pl_cls}">{pl:+d}</span>
            </div>
        </div>
    </div>"""

    h2h = _h2h_last10(away, home)
    time_bit = f"{day} · {when}" if day else when

    extra_attrs = []
    if total is not None:
        tot_s = html_lib.escape(str(total))
        extra_attrs.append(f'data-pl-total="{tot_s}"')
        extra_attrs.append(f'data-xs-total="{tot_s}"')
    extra_attrs.append(f'data-pl-proj="{html_lib.escape(score_txt)}"')
    book_total = card.get("book_total")
    total_ev = compute_total_ev(
        total,
        book_total,
        over_odds=card.get("book_over_odds"),
        under_odds=card.get("book_under_odds"),
    )
    if total_ev is not None:
        extra_attrs.append(f'data-total-ev="{total_ev:.1f}"')
    # Chart model series: home-win % already used on results tallies. Skip if
    # no locked probability (do not invent 50%).
    if card.get("home_win_prob") is not None:
        for _name, attr, home_pct in _model_home_pcts(hp):
            extra_attrs.append(f'{attr}="{home_pct:.1f}"')
    extra_attr_s = (" " + " ".join(extra_attrs)) if extra_attrs else ""

    tev_html = ""
    if total_ev is not None:
        tev_col = "#15803d" if total_ev > 0 else "#b91c1c"
        tev_sign = "+" if total_ev > 0 else ""
        tev_html = (
            f'<div class="sf-item">'
            f'<span class="sf-label">Total EV</span>'
            f'<span class="sf-val" style="color:{tev_col};font-weight:700;">'
            f"{tev_sign}{total_ev:.1f}%</span></div>"
        )

    conf_html = ""
    if card.get("home_win_prob") is not None:
        boxes = []
        for name, fav_p, fav in _component_models(home, away, hp):
            cons = " consensus" if name == "Sharp Consensus" else ""
            side_cls = "home" if fav == home else "away"
            boxes.append(
                f'<div class="pc-box{cons}"><div class="pc-name">{html_lib.escape(name)}</div>'
                f'<div class="pc-val">{fav_p * 100:.1f}%</div>'
                f'<div class="pc-side {side_cls}">{html_lib.escape(fav)}</div></div>'
            )
        conf_html = (
            '<div class="pick-conf-bar">'
            '<div class="pick-conf-title">Pick Confidence</div>'
            f'<div class="pick-conf-grid">{"".join(boxes)}</div>'
            "</div>"
        )

    return f"""
                <div class="game-card-stack" data-pick-card data-league="CFL" data-home="{html_lib.escape(home)}" data-away="{html_lib.escape(away)}" data-pick="{html_lib.escape(str(pick))}" data-conf="{pick_pct:.1f}" data-time="{time_bit}" data-pl-spread="{html_lib.escape(pl_spread_txt)}" data-xs-spread="{html_lib.escape(pl_spread_txt)}" data-xs-proj="{html_lib.escape(score_txt)}" data-h2h="{html_lib.escape(h2h)}"{extra_attr_s}>
                <div class="game-card pick-card" data-league="CFL">
<header class="pick-card-header">
    <span class="league-badge">🏈 CFL</span>
    <span class="game-time">{time_bit}</span>
</header>

<div class="matchup-row">
    {team_slot(away, ap, pl_a, not home_fav)}
    <div class="matchup-at">@</div>
    {team_slot(home, hp, pl_h, home_fav)}
</div>

<div class="lines-strip">
    {''.join(mid_chips)}
</div>

<footer class="card-footer">
    <button type="button" class="view-details-btn" aria-expanded="false" aria-controls="{details_id}" onclick="togglePickDetails(this)">
        View Details <span class="chevron">▾</span>
    </button>
</footer>

<div class="card-details" id="{details_id}" hidden>
    <div class="odds-pricing-section">
        <div class="odds-pricing-title">Odds &amp; Lines</div>
        <table class="odds-pricing-table">
            <thead>
                <tr>
                    <th>Market</th>
                    <th class="col-pl">Prediction Lab</th>
                    <th class="col-xs">XSharp</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td class="market-k">Spread</td>
                    <td class="val-pl">{html_lib.escape(pl_spread_txt)}</td>
                    <td class="val-xs">{html_lib.escape(pl_spread_txt)}</td>
                </tr>
                <tr>
                    <td class="market-k">Total</td>
                    <td class="val-pl">{html_lib.escape(total_txt)}</td>
                    <td class="val-xs">{html_lib.escape(total_txt)}</td>
                </tr>
            </tbody>
        </table>
        <div class="proj-score-box">
            <div class="proj-score-title">Projected Score</div>
            <div class="proj-row">
                <span class="proj-model pl">Prediction Lab</span>
                <span class="proj-val">{html_lib.escape(score_txt)}</span>
            </div>
            <div class="proj-row">
                <span class="proj-model xs">XSharp</span>
                <span class="proj-val">{html_lib.escape(score_txt)}</span>
            </div>
        </div>
    </div>

    {conf_html}

    <div class="odds-extras-footer">
        <div class="sf-item">
            <span class="sf-label">H2H Last 10</span>
            <span class="sf-val">{html_lib.escape(h2h)}</span>
        </div>
        <div class="sf-item">
            <span class="sf-label">Model pick</span>
            <span class="sf-val">{html_lib.escape(str(pick))}</span>
        </div>
        {tev_html}
    </div>

    <div class="analysis-toggle" onclick="(function(el){{var p=document.getElementById('{analysis_id}');if(!p)return;var o=p.style.display==='block';p.style.display=o?'none':'block';el.textContent=o?'Analysis ▾':'Analysis ▴';}})(this)">Analysis ▾</div>
    <div id="{analysis_id}" class="analysis-panel" style="display:none;padding:10px 12px;font-size:0.85em;line-height:1.45;color:#334155">{expl}</div>
</div>
                </div>
                </div>
"""


def render_result_card(card: dict[str, Any], idx: int) -> str:
    """MLB-style final-score result card. No fake 50%/−100 when pick wasn't locked."""
    home = card.get("home_team") or ""
    away = card.get("away_team") or ""
    try:
        actual_home = int(card["home_score"])
        actual_away = int(card["away_score"])
    except (TypeError, ValueError, KeyError):
        return ""
    day = _date_key(card.get("game_date"))
    when = _fmt_time(card.get("game_date"))
    meta = "FINAL"
    if day:
        meta = f"FINAL · {day}" + (f" · {when}" if when else "")
    away_win = actual_away > actual_home
    home_win = actual_home > actual_away
    locked = _has_locked_pick(card)
    grade = card.get("grade")
    details_id = f"cfl-result-details-{idx}"

    if locked:
        hp = float(card["home_win_prob"])
        ap = float(card.get("away_win_prob") if card.get("away_win_prob") is not None else (1.0 - hp))
        pick = str(card.get("pick_ml") or "")
        pl_h = american_from_prob(hp)
        pl_a = american_from_prob(ap)
        gcol = "#00C076" if grade == "WIN" else ("#94a3b8" if grade == "PUSH" else "#ef4444")
        grade_txt = html_lib.escape(str(grade or "—"))
        spread = card.get("model_spread")
        total = card.get("model_total")
        try:
            sp = float(spread) if spread is not None else None
        except (TypeError, ValueError):
            sp = None
        pl_spread_txt = spread_label(home, away, sp)
        total_txt = f"{total}" if total is not None else "—"
        hs = card.get("predicted_home_score")
        as_ = card.get("predicted_away_score")
        score_txt = f"{away} {as_} – {home} {hs}" if hs is not None and as_ is not None else "—"

        body_extra = f"""
<div class="cfl-result-strip">
  <div class="cfl-result-chip"><div class="lbl">Model pick</div><div class="val">{html_lib.escape(pick)}</div></div>
  <div class="cfl-result-chip"><div class="lbl">ML grade</div><div class="val" style="color:{gcol}">{grade_txt}</div></div>
  <div class="cfl-result-chip"><div class="lbl">Away model</div><div class="val">{_pct(ap)}% ({pl_a:+d})</div></div>
  <div class="cfl-result-chip"><div class="lbl">Home model</div><div class="val">{_pct(hp)}% ({pl_h:+d})</div></div>
</div>
<footer class="card-footer">
  <button type="button" class="view-details-btn" aria-expanded="false" aria-controls="{details_id}" onclick="togglePickDetails(this)">
    View Details <span class="chevron">▾</span>
  </button>
</footer>
<div class="card-details" id="{details_id}" hidden>
  <div class="odds-pricing-section">
    <div class="odds-pricing-title">Pre-game model lines</div>
    <table class="odds-pricing-table">
      <thead><tr><th>Market</th><th class="col-pl">Prediction Lab</th><th class="col-xs">XSharp</th></tr></thead>
      <tbody>
        <tr><td class="market-k">Spread</td><td class="val-pl">{html_lib.escape(pl_spread_txt)}</td><td class="val-xs">{html_lib.escape(pl_spread_txt)}</td></tr>
        <tr><td class="market-k">Total</td><td class="val-pl">{html_lib.escape(total_txt)}</td><td class="val-xs">{html_lib.escape(total_txt)}</td></tr>
      </tbody>
    </table>
    <div class="proj-score-box">
      <div class="proj-score-title">Projected Score</div>
      <div class="proj-row"><span class="proj-model pl">Prediction Lab</span><span class="proj-val">{html_lib.escape(score_txt)}</span></div>
    </div>
  </div>
</div>
"""
    else:
        body_extra = (
            '<p class="cfl-no-pick">Final score only — no pre-game pick was locked.</p>'
        )

    return f"""
<div class="game-card-stack" data-pick-card data-league="CFL">
  <div class="game-card" data-league="CFL">
    <div class="card-hero">
      <div class="card-hero-meta-line">{html_lib.escape(meta)}</div>
      <div class="teams-split">
        <div class="team-col away">
          <img class="team-logo" src="{_logo(away)}" alt="" width="48" height="48" loading="lazy"
               onerror="this.style.opacity='0.4'">
          <div class="team-name">{html_lib.escape(away)}</div>
          <div class="final-score {'score-winner' if away_win else ''}">{actual_away}</div>
        </div>
        <div class="teams-at">@</div>
        <div class="team-col home">
          <img class="team-logo" src="{_logo(home)}" alt="" width="48" height="48" loading="lazy"
               onerror="this.style.opacity='0.4'">
          <div class="team-name">{html_lib.escape(home)}</div>
          <div class="final-score {'score-winner' if home_win else ''}">{actual_home}</div>
        </div>
      </div>
    </div>
    {body_extra}
  </div>
</div>
"""


def _one_games_grid(cards: list[dict[str, Any]], *, mode: str) -> str:
    if not cards:
        return "<p style='padding:1rem;color:#64748b'>No games to show.</p>"
    if mode == "results":
        body = "\n".join(c for c in (render_result_card(card, i) for i, card in enumerate(cards)) if c)
    else:
        body = "\n".join(render_card(c, i, mode=mode) for i, c in enumerate(cards))
    return f'<div class="games-grid">\n{body}\n</div>'


# Same six face labels as NHL77FINAL MLB Last Night / Last 7 tallies.
_TALLY_MODEL_CARDS = [
    ("⭐ Grinder2", "Grinder2"),
    ("🎯 Takedown", "Takedown"),
    ("📊 Edge", "Edge"),
    ("🤖 XSharp", "XSharp"),
    ("🏆 Sharp Consensus", "Sharp Consensus"),
    ("⚡ Efficiency", "Efficiency"),
]


def _wl_acc(w: int, l: int, p: int = 0) -> tuple[float | None, str]:
    n = w + l
    acc = round(100.0 * w / n, 1) if n else None
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    return acc, rec


def _model_ml_wl(rows: list[dict[str, Any]], model_name: str) -> tuple[int, int, int]:
    w = l = p = 0
    for r in rows:
        if not _has_locked_pick(r):
            continue
        home = r.get("home_team") or ""
        away = r.get("away_team") or ""
        try:
            hp = float(r["home_win_prob"])
            ah = int(r["home_score"])
            aa = int(r["away_score"])
        except (TypeError, ValueError, KeyError):
            continue
        fav = next((f for n, _, f in _component_models(home, away, hp) if n == model_name), None)
        if not fav:
            continue
        if _FADE_ML:
            fav = other_side(home, away, fav) or fav
        if ah == aa:
            p += 1
            continue
        winner = home if ah > aa else away
        if fav == winner:
            w += 1
        else:
            l += 1
    return w, l, p


def _sou_wl(rows: list[dict[str, Any]], market: str) -> tuple[int, int, int]:
    w = l = p = 0
    for r in rows:
        if market == "spread":
            ok, push = grade_spread_raw(r)
            if _FADE_SPREAD and ok is not None:
                ok = not ok
        else:
            ok, push = grade_total_raw(r)
        if push:
            p += 1
        elif ok is True:
            w += 1
        elif ok is False:
            l += 1
    return w, l, p


def _acc_color(acc: float | None, *, spread_ou: bool) -> str:
    if acc is None:
        return "#94a3b8"
    if not spread_ou:
        return "#0f172a"
    if acc >= 52:
        return "#00C076"
    if acc >= 48:
        return "#fbbf24"
    return "#D93025"


def _tally_stat_html(acc: float | None, rec: str, *, color: bool = False) -> str:
    if acc is None:
        return (
            '<div class="daily-acc" style="color:#94a3b8;">—</div>'
            '<div class="daily-rec">—</div>'
        )
    c = _acc_color(acc, spread_ou=color)
    return (
        f'<div class="daily-acc" style="color:{c};">{acc:.1f}%</div>'
        f'<div class="daily-rec">{html_lib.escape(rec)}</div>'
    )


def _tally_block(title: str, rows: list[dict[str, Any]], *, empty_note: str) -> str:
    """MLB Last Night / Last 7 daily-tally chrome. Do not invent CFL-only copy."""
    games = len(rows)
    if games == 0:
        return f"""
        <div class="daily-tally" data-empty-results="1" style="text-align:center;">
          <div class="daily-tally-head"><h2>{title}</h2></div>
          <p style="margin:0;color:#64748b;font-size:.92rem">{html_lib.escape(empty_note)}</p>
        </div>
        """
    cards = []
    for label, name in _TALLY_MODEL_CARDS:
        w, l, p = _model_ml_wl(rows, name)
        acc, rec = _wl_acc(w, l, p)
        hl = " highlight" if name == "Sharp Consensus" else ""
        cards.append(
            f'<div class="daily-tally-card{hl}">'
            f'<div class="daily-model">{html_lib.escape(label)}</div>'
            f"{_tally_stat_html(acc, rec)}"
            f"</div>"
        )
    sw, sl, sp = _sou_wl(rows, "spread")
    tw, tl, tp = _sou_wl(rows, "total")
    s_acc, s_rec = _wl_acc(sw, sl, sp)
    t_acc, t_rec = _wl_acc(tw, tl, tp)
    s_body = (
        _tally_stat_html(s_acc, s_rec, color=True)
        if (sw + sl + sp)
        else '<div class="daily-acc" style="color:#94a3b8;">—</div><div class="daily-rec">no spread data</div>'
    )
    t_body = (
        _tally_stat_html(t_acc, t_rec, color=True)
        if (tw + tl + tp)
        else '<div class="daily-acc" style="color:#94a3b8;">—</div><div class="daily-rec">no O/U data</div>'
    )
    return f"""
        <div class="daily-tally">
          <div class="daily-tally-head">
            <h2>{title} ({games} games)</h2>
          </div>
          <div style="font-size:0.78em;text-align:center;opacity:0.7;margin-bottom:6px;">MONEYLINE</div>
          <div class="daily-tally-grid">
            {''.join(cards)}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
            <div class="daily-tally-card" style="border:1px solid rgba(139,92,246,0.4);">
              <div class="daily-model">📈 Spread</div>
              {s_body}
            </div>
            <div class="daily-tally-card" style="border:1px solid rgba(251,191,36,0.4);">
              <div class="daily-model">🎲 Over/Under</div>
              {t_body}
            </div>
          </div>
        </div>
        """


def _season_performance_block(rows: list[dict[str, Any]]) -> str:
    """MLB Season Performance banner + Moneyline Accuracy by Model grid."""
    sw, sl, sp = _model_ml_wl(rows, "Sharp Consensus")
    s_acc, s_rec = _wl_acc(sw, sl, sp)
    spw, spl, spp = _sou_wl(rows, "spread")
    sp_acc, sp_rec = _wl_acc(spw, spl, spp)
    tw, tl, tp = _sou_wl(rows, "total")
    t_acc, t_rec = _wl_acc(tw, tl, tp)

    def face(label: str, acc: float | None, rec: str, graded: int, *, spread_ou: bool) -> str:
        if graded <= 0 or acc is None:
            val = '<div style="font-size:1.5em;color:#94a3b8;">—</div>'
            sub = '<div style="font-size:0.85em;color:#64748b;">not graded yet</div>'
        else:
            c = _acc_color(acc, spread_ou=spread_ou)
            val = f'<div style="font-size:2em;font-weight:bold;color:{c};">{acc:.1f}%</div>'
            sub = (
                f'<div style="font-size:0.85em;opacity:0.9;color:#334155;">{html_lib.escape(rec)} '
                f'<span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>'
            )
        return (
            '<div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">'
            f'<div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">{label}</div>'
            f"{val}{sub}</div>"
        )

    ml_face = face("🎯 Moneyline (Sharp Consensus)", s_acc, s_rec, sw + sl, spread_ou=False)
    # Season ML face uses MLB color bands (>=55 green, >=50 yellow).
    if sw + sl > 0 and s_acc is not None:
        ml_c = "#00C076" if s_acc >= 55 else ("#fbbf24" if s_acc >= 50 else "#D93025")
        ml_face = (
            '<div style="background:#f8fafc;border:1px solid rgba(15,23,42,0.12);border-radius:9px;padding:14px;text-align:center;">'
            '<div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎯 Moneyline (Sharp Consensus)</div>'
            f'<div style="font-size:2em;font-weight:bold;color:{ml_c};">{s_acc:.1f}%</div>'
            f'<div style="font-size:0.85em;opacity:0.9;color:#334155;">{html_lib.escape(s_rec)} '
            f'<span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div></div>'
        )
    sp_face = face("📈 Spread (Prediction Lab)", sp_acc, sp_rec, spw + spl, spread_ou=True)
    ou_face = face("🎲 O/U (Prediction Lab)", t_acc, t_rec, tw + tl, spread_ou=True)

    model_cards = []
    for label, name in _TALLY_MODEL_CARDS:
        w, l, p = _model_ml_wl(rows, name)
        acc, rec = _wl_acc(w, l, p)
        hl = " highlight" if name == "Sharp Consensus" else ""
        model_cards.append(
            f'<div class="daily-tally-card{hl}">'
            f'<div class="daily-model">{html_lib.escape(label)}</div>'
            f"{_tally_stat_html(acc, rec)}"
            f"</div>"
        )
    return f"""
        <div class="daily-tally" style="text-align:center;">
          <h2 style="text-align:center;margin:0 0 6px 0;font-size:1.5em;color:#0f172a;">🏆 Season Performance</h2>
          <div class="roi-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px;">
            {ml_face}{sp_face}{ou_face}
          </div>
        </div>
        <h3 style="text-align:center;font-size:1.15em;margin:0 0 12px;color:#0f172a;">Moneyline Accuracy by Model</h3>
        <div class="daily-tally-grid" style="margin-bottom:16px;">
          {''.join(model_cards)}
        </div>
        <!-- ── Type Toggle ── -->
        """


def _bucket_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(ET)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    last7_cut = now - timedelta(days=7)

    by_day: dict[str, list] = {}
    for r in rows:
        dk = _date_key(r.get("game_date"))
        if dk:
            by_day.setdefault(dk, []).append(r)

    dates_sorted = sorted(by_day.keys())
    # CFL does not play every night. Last Night = yesterday if they played,
    # else the most recent completed slate (never a blank "no games" box).
    if yesterday in by_day:
        last_night_key = yesterday
    elif dates_sorted:
        last_night_key = dates_sorted[-1]
    else:
        last_night_key = None
    last_night = by_day.get(last_night_key or "", [])

    last7: list[dict[str, Any]] = []
    for r in rows:
        dt = _parse_dt(r.get("game_date"))
        if dt and dt >= last7_cut:
            last7.append(r)

    return {
        "last_night_key": last_night_key,
        "last_night": last_night,
        "last7": last7,
        "season": rows,
    }


def _period_filter(agreements: list[dict[str, Any]], *, days: int | None, last_night_key: str | None) -> list[dict[str, Any]]:
    if days is None and last_night_key:
        return [a for a in agreements if _date_key(a.get("game_date")) == last_night_key]
    if days is None:
        return []
    cut = datetime.now(ET) - timedelta(days=days)
    out = []
    for a in agreements:
        dt = _parse_dt(a.get("game_date"))
        if dt and dt >= cut:
            out.append(a)
    return out


def _record_cell(items: list[dict[str, Any]]) -> str:
    w = sum(1 for i in items if i.get("grade") == "WIN")
    l = sum(1 for i in items if i.get("grade") == "LOSS")
    p = sum(1 for i in items if i.get("grade") == "PUSH")
    decided = w + l
    if decided == 0 and p == 0:
        return "—"
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    if decided == 0:
        return html_lib.escape(rec)
    pct = 100.0 * w / decided
    return f"{html_lib.escape(rec)} <span style='color:#64748b'>({pct:.0f}%)</span>"


def build_consensus_section(rows: list[dict[str, Any]], *, last_night_key: str | None) -> str:
    """Consensus Based Betting Records — majority-side ML W-L by agreement bucket."""
    agreements = []
    for r in rows:
        a = _agreement_for_card(r)
        if a and a.get("grade") in ("WIN", "LOSS", "PUSH"):
            agreements.append(a)

    ln = _period_filter(agreements, days=None, last_night_key=last_night_key)
    d7 = _period_filter(agreements, days=7, last_night_key=None)
    d30 = _period_filter(agreements, days=30, last_night_key=None)

    def by_bucket(items: list[dict[str, Any]]) -> dict[str, list]:
        out = {b: [] for b in BUCKET_ORDER}
        for a in items:
            label = _bucket_label(int(a.get("agree_n") or 0), int(a.get("n_models") or 6))
            out.setdefault(label, []).append(a)
        return out

    ln_b, d7_b, d30_b = by_bucket(ln), by_bucket(d7), by_bucket(d30)

    rows_html = []
    for label in BUCKET_ORDER:
        rows_html.append(
            "<tr>"
            f'<td class="bucket">{html_lib.escape(label)}</td>'
            f"<td>{_record_cell(ln_b.get(label, []))}</td>"
            f"<td>{_record_cell(d7_b.get(label, []))}</td>"
            f"<td>{_record_cell(d30_b.get(label, []))}</td>"
            "</tr>"
        )

    empty = ""
    if not agreements:
        empty = (
            '<p class="empty-note">No locked pre-game picks to grade yet — '
            "records appear after upcoming slate picks settle.</p>"
        )

    ln_hdr = f"Last night ({last_night_key})" if last_night_key else "Last night"
    return f"""
    <div class="cfl-consensus" id="cfl-consensus-records">
      <h2>Consensus Based Betting Records</h2>
      <p class="sub">
        Moneyline record when model sides agree.
      </p>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th style="text-align:left">Agreement</th>
              <th>{html_lib.escape(ln_hdr)}</th>
              <th>Past 7 days</th>
              <th>Past 30 days</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
      {empty}
    </div>
    """


def build_cards_fragment(*, which: str = "picks", refresh: bool = False) -> tuple[str, dict[str, Any]]:
    meta = ensure_predictions(refresh=refresh)
    _refresh_fade_flags()
    raw_cards = list_pick_cards()
    attach = getattr(_pipe, "attach_book_totals", None)
    if callable(attach):
        raw_cards = attach(raw_cards)
    cards = [_faded(c) for c in raw_cards]
    frag = f"""
            {GRID_CSS}
            <div class="section-tabs">
              <a href="/cfl/" class="tab active">📊 Predictions</a>
              <a href="/cfl/results" class="tab">🎯 Results</a>
            </div>
            {_one_games_grid(cards, mode="picks")}
            {TOGGLE_DETAILS_JS}
    """
    return frag, {"ok": True, "cards": len(cards), "db": str(meta.get("db")), "which": which}


def build_results_fragment(*, refresh: bool = False) -> tuple[str, dict[str, Any]]:
    meta = ensure_predictions(refresh=refresh)
    # Current CFL.ca regular season (Week N). Preseason stays in DB for Elo only.
    raw = list_graded_results(days=120, regular_season_only=True)
    # Season under-50% fade: invert displayed ML + spread. Totals never fade.
    # Tallies grade unfaded rows then invert via _FADE_ML / _FADE_SPREAD.
    _refresh_fade_flags()
    rows = [_faded(c) for c in raw]
    buckets = _bucket_results(raw)
    ln_key = buckets["last_night_key"]
    ln_title = (
        f"Last Night's CFL Results — {ln_key}"
        if ln_key
        else "Last Night's CFL Results"
    )
    last7 = buckets["last7"]
    if last7:
        days = sorted({_date_key(r.get("game_date")) for r in last7 if _date_key(r.get("game_date"))})
        range_txt = f"{days[0]} to {days[-1]}" if days else ""
    else:
        range_txt = ""
    l7_title = f"Last 7 Days CFL Results — {range_txt}".strip(" —")
    ln_empty = f"No completed games for {ln_key}." if ln_key else "No completed games yet."
    l7_empty = "—"
    tallies = (
        _tally_block(ln_title, buckets["last_night"], empty_note=ln_empty)
        + _tally_block(l7_title, last7, empty_note=l7_empty)
        + _season_performance_block(buckets["season"])
    )
    consensus = build_consensus_section(raw, last_night_key=buckets["last_night_key"])

    graded = sum(1 for r in rows if r.get("grade") in ("WIN", "LOSS", "PUSH"))
    wins = sum(1 for r in rows if r.get("grade") == "WIN")
    losses = sum(1 for r in rows if r.get("grade") == "LOSS")
    tally = f"{wins}-{losses}" if graded else "n/a"

    if not rows:
        body = """
            <div class="daily-tally" data-empty-results="1" style="margin:1.5rem 0;padding:1.25rem;text-align:center">
              <h2 style="margin:0 0 .5rem;color:#0f172a">No completed games yet</h2>
              <p style="margin:0;color:#64748b">CFL results and model records appear here after games are final and graded.</p>
            </div>
        """
    else:
        body = f"""
            {tallies}
            {consensus}
            <h3 style="text-align:center;font-size:1.1rem;margin:8px 0 12px;color:#0f172a;">Recent Finals</h3>
            {_one_games_grid(rows, mode="results")}
        """

    frag = f"""
            {GRID_CSS}
            <h1 class="page-title" id="pageHeading">🏈 CFL Results, Performance and Model Accuracy</h1>
            <div class="section-tabs">
              <a href="/cfl/" class="tab">📊 Predictions</a>
              <a href="/cfl/results" class="tab active">🎯 Results</a>
            </div>
            {body}
            {TOGGLE_DETAILS_JS}
    """
    return frag, {
        "ok": True,
        "cards": len(rows),
        "graded": graded,
        "record": tally,
        "which": "results",
        "db": str(meta.get("db")),
        "last_night_games": len(buckets["last_night"]),
    }


def build_slate_html(*, which: str = "picks", refresh: bool = False) -> tuple[str, dict[str, Any]]:
    if which == "results":
        return build_results_fragment(refresh=refresh)
    return build_cards_fragment(which=which, refresh=refresh)


CARD_CSS = GRID_CSS

TOGGLE_DETAILS_JS = """
<script id="cfl-iso-toggle-pick-details">
function _sandboxDetailsEl(btn){
  if(!btn) return null;
  var id=btn.getAttribute('aria-controls');
  var el=id?document.getElementById(id):null;
  if(el) return el;
  var stack=btn.closest('.game-card-stack,.game-card,.pick-card');
  el=stack?stack.querySelector('.card-details'):null;
  if(el) return el;
  var foot=btn.closest('.card-footer');
  return foot&&foot.parentNode?foot.parentNode.querySelector('.card-details'):null;
}
function _sandboxForcePickDetails(btn, open){
  var el=_sandboxDetailsEl(btn);
  if(!el) return;
  var card=btn.closest('.pick-card,.game-card,.game-card-stack');
  if(open){
    el.removeAttribute('hidden'); el.hidden=false;
    el.style.removeProperty('display');
    btn.setAttribute('aria-expanded','true');
    if(card) card.classList.add('is-expanded');
  } else {
    el.setAttribute('hidden',''); el.hidden=true;
    btn.setAttribute('aria-expanded','false');
    if(card) card.classList.remove('is-expanded');
  }
}
function togglePickDetails(btn){
  if(!btn) return;
  var el=_sandboxDetailsEl(btn);
  if(!el) return;
  var isOpen=!(el.hasAttribute('hidden') || el.hidden===true);
  _sandboxForcePickDetails(btn, !isOpen);
}
document.addEventListener('pointerdown', function(e){
  var t=e.target;
  if(!t || !t.closest) return;
  var btn=t.closest('.view-details-btn');
  if(!btn) return;
  btn.setAttribute('data-want-open', btn.getAttribute('aria-expanded')==='true' ? '0' : '1');
}, true);
document.addEventListener('click', function(e){
  var t=e.target;
  if(!t || !t.closest) return;
  var btn=t.closest('.view-details-btn');
  if(!btn) return;
  e.preventDefault();
  var want=btn.getAttribute('data-want-open')!=='0';
  setTimeout(function(){ _sandboxForcePickDetails(btn, want); }, 0);
}, true);
</script>
"""
