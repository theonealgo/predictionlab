"""Soccer picks face chip: Consensus Historical Record (not duplicate H2H)."""

from team_results_charts import (
    _SOCCER_CONSENSUS_MODELS,
    _inject_soccer_consensus_hist_chips,
    set_results_chart_source,
)


def _card(*, g2=72.0, td=68.0, edge=65.0, xs=75.0, sc=67.0, eff=69.0) -> str:
    def box(name: str, pct: float) -> str:
        return (
            f'<div class="pc-name">{name}</div>'
            f'<div class="pc-val home">{pct:.1f}%</div>'
            f'<div class="pc-side">Ajax Amsterdam</div>'
        )

    return (
        '<div class="game-card game-card-stack" data-pick-card '
        'data-home-full="Ajax Amsterdam" data-away-full="Willem II" '
        'data-date="2026-09-19" data-h2h="6 (1 game)">'
        '<div class="lines-strip">'
        '<div class="line-chip h2h-face-chip">'
        '<div class="line-chip-label">H2H Last 10</div>'
        '<div class="line-chip-val">6 (1 game)</div></div>'
        "</div>"
        + box("Grinder2", g2)
        + box("Takedown", td)
        + box("Edge", edge)
        + box("XSharp", xs)
        + box("Sharp Consensus", sc)
        + box("Efficiency", eff)
        + '<div class="odds-extras-footer">'
        '<div class="sf-item"><span class="sf-label">H2H Last 10</span> '
        '<span class="sf-val">6 (1 game)</span></div></div>'
        "</div>"
    )


def test_soccer_face_replaces_h2h_with_consensus_hist():
    set_results_chart_source("SOCCER", "<html></html>")
    html = _card() + _card(g2=40.0)  # second card has dissent
    out = _inject_soccer_consensus_hist_chips(html)
    assert "Consensus Historical Record" in out
    assert out.count("consensus-hist-chip") >= 2
    assert "h2h-face-chip" not in out
    # Details H2H stays once per card
    assert out.count("H2H Last 10") == 2
    assert "Unanimous:" in out or "All but" in out or "Split:" in out or "—" in out


def test_soccer_consensus_models_are_six():
    assert len(_SOCCER_CONSENSUS_MODELS) == 6
