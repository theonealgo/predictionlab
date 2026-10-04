"""Compact Consensus Historical face for MLB/NFL/NCAAF/CFL."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

from chart_shape import compact_consensus_hist_face_issues, picks_clock_issues  # noqa: E402
from team_results_charts import _nfl_consensus_hist_label, _inject_consensus_hist_chips  # noqa: E402


def _card(val: str) -> str:
    return (
        '<div data-pick-card data-home="New York Mets" data-away="Philadelphia Phillies">'
        '<div class="lines-strip">'
        '<div class="line-chip consensus-hist-chip">'
        '<div class="line-chip-label">Consensus Historical Record</div>'
        f'<div class="line-chip-val">{val}</div></div></div>'
        '<div class="pick-conf-grid">'
        '<div class="pc-name">Edge</div><div class="pc-val">60%</div>'
        '<div class="pc-name">XSharp</div><div class="pc-val">55%</div>'
        "</div></div>"
    )


def test_compact_consensus_rejects_verbose():
    html = _card("Consensus Record: 27-14 (66%) — Last 7 Days · 6/6 unanimous") + _card(
        "Consensus Record: 3-4 (43%) — Last 7 Days · 3/6 Split / no consensus"
    )
    issues = compact_consensus_hist_face_issues(html, "MLB")
    assert issues and "verbose" in issues[0].lower()


def test_compact_consensus_accepts_nfl_style():
    html = _card("Consensus Record: 6/6 Unanimous (27-14)") + _card(
        "Consensus Record: 3/6 Split (0-0)"
    )
    assert not compact_consensus_hist_face_issues(html, "MLB")


def test_mlb_inject_uses_compact_label():
    card = (
        '<div data-pick-card data-home="New York Mets" data-away="Philadelphia Phillies">'
        '<div class="lines-strip">'
        '<div class="line-chip h2h-face-chip">'
        '<div class="line-chip-label">H2H Last 10</div>'
        '<div class="line-chip-val">x</div></div></div>'
        '<div class="pick-conf-grid">'
        + "".join(
            f'<div class="pc-box"><div class="pc-name">{n}</div>'
            f'<div class="pc-val">60%</div><div class="pc-side home">Mets</div></div>'
            for n in (
                "Grinder2",
                "Takedown",
                "Edge",
                "XSharp",
                "Sharp Consensus",
                "Efficiency",
            )
        )
        + "</div></div>"
    )
    # Empty lookup → compact empty label, not Last 7 Days
    out = _inject_consensus_hist_chips(
        card,
        sport="MLB",
        models=(
            "Grinder2",
            "Takedown",
            "Edge",
            "XSharp",
            "Sharp Consensus",
            "Efficiency",
        ),
    )
    assert "Last 7 Days" not in out
    assert "Consensus Historical Record" in out


def test_picks_clock_ignores_final():
    html = (
        '<span class="game-time">FINAL</span>'
        '<span class="game-time">FINAL</span>'
        '<span class="game-time">7:10 PM ET</span>'
        '<span class="game-time">7:40 PM ET</span>'
    )
    assert not picks_clock_issues(html)


def test_nfl_label_helper():
    assert (
        _nfl_consensus_hist_label(3, (), 0, 0, None, panel=6, pushes=0)
        == "Consensus Record: 3/6 Split (0-0)"
    )
