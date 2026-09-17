"""CFL face chip = Consensus Historical Record; strip MLB shell leftovers."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "iso_hub"))

from cfl_live import _dedupe_cfl_results_chrome, _strip_mlb_content_from_cfl
from team_results_charts import _inject_cfl_consensus_hist_chips


def _card(home: str, away: str, *, g2: str, td: str, edge: str, xs: str, sc: str, eff: str) -> str:
    def box(name: str, val: str) -> str:
        return (
            f'<div class="pc-box"><div class="pc-name">{name}</div>'
            f'<div class="pc-val">{val}</div>'
            f'<div class="pc-side home">{home}</div></div>'
        )

    return (
        f'<div class="game-card-stack" data-pick-card data-home="{home}" data-away="{away}">'
        '<div class="game-card pick-card is-expanded">'
        '<div class="lines-strip">'
        '<div class="line-chip h2h-face-chip">'
        '<div class="line-chip-label">H2H Last 10</div>'
        '<div class="line-chip-val">First meeting</div></div>'
        '<div class="line-chip h2h-face-chip">'
        '<div class="line-chip-label">H2H Last 10</div>'
        '<div class="line-chip-val">48.2 (10 games)</div></div>'
        '<div class="line-chip"><div class="line-chip-label">Spread Confidence</div>'
        '<div class="line-chip-val">62</div></div></div>'
        '<div class="pick-conf-grid">'
        + box("Grinder2", g2)
        + box("Takedown", td)
        + box("Edge", edge)
        + box("XSharp", xs)
        + box("Efficiency", eff)
        + box("Sharp Consensus", sc)
        + "</div>"
        '<div class="sf-item"><span class="sf-label">H2H Last 10</span> '
        '<span class="sf-val">48.2 (10 games)</span></div>'
        "</div></div>"
    )


def test_cfl_face_replaces_duplicate_h2h_with_consensus_hist():
    html = _card(
        "Montreal Alouettes",
        "Winnipeg Blue Bombers",
        g2="65.1%",
        td="63.4%",
        edge="60.4%",
        xs="66.1%",
        sc="61.6%",
        eff="59.1%",
    )
    out = _inject_cfl_consensus_hist_chips(html)
    assert "consensus-hist-chip" in out
    assert "Consensus Historical Record" in out
    assert out.count("h2h-face-chip") == 0
    # Details H2H kept.
    assert out.count("H2H Last 10") == 1


def test_strip_mlb_no_data_and_routes():
    html = (
        '<div class="no-data">No predictions available for MLB</div>'
        '<div class="pl-view-toggle">'
        '<a href="/mlb-results">Cards</a>'
        '<a href="/mlb-results?view=chart">Chart</a></div>'
        '<meta property="og:title" content="MLB Predictions Today | predictionlab.io">'
    )
    out = _strip_mlb_content_from_cfl(html)
    assert "No predictions available for MLB" not in out
    assert 'href="/mlb-results"' not in out
    assert 'href="/cfl-results"' in out
    assert 'content="CFL Predictions Today' in out


def test_dedupe_cfl_results_chrome():
    html = (
        '<h1 class="page-title">CFL Results A</h1>'
        '<div class="section-tabs"><a href="/cfl-picks">Predictions</a></div>'
        '<div class="pl-view-toggle"><a href="/cfl-results">Cards</a></div>'
        '<style>.pl-view-toggle{display:flex}</style>'
        '<h1 class="page-title">CFL Results B</h1>'
        '<div class="section-tabs"><a href="/cfl-picks">Predictions</a></div>'
        '<div class="pl-view-toggle"><a href="/mlb-results">Cards</a></div>'
    )
    out = _dedupe_cfl_results_chrome(html)
    assert out.count('class="section-tabs"') == 1
    assert out.count('class="pl-view-toggle"') == 1
    assert out.count("page-title") == 1
    assert "CFL Results B" not in out


def test_dedupe_pl2_headers_keeps_one():
    from cfl_live import _dedupe_pl2_headers, _strip_all_pl2_headers

    html = (
        '<body>'
        '<header class="pl2-header"><a class="pl2-brand">ONE</a></header>'
        '<main>x</main>'
        '<header class="pl2-header foo"><a class="pl2-brand">TWO</a></header>'
        '</body>'
    )
    out = _dedupe_pl2_headers(html)
    assert out.count("pl2-header") == 1
    assert "ONE" in out and "TWO" not in out
    stripped = _strip_all_pl2_headers(html)
    assert "pl2-header" not in stripped
    assert "<main>x</main>" in stripped
