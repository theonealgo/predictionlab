"""WNBA unlock: Consensus Historical face + season undercount checker."""
from qa.chart_shape import (
    wnba_consensus_hist_face_issues,
    wnba_season_games_undercount_issues,
)
from team_results_charts import (
    _WNBA_CONSENSUS_MODELS,
    _card_dissent_for_models,
    _inject_wnba_consensus_hist_chips,
    _wnba_consensus_hist_label,
)


def _card(face: str, details_h2h: bool = True, models: str = "") -> str:
    details = (
        '<div class="sf-item"><span class="sf-label">H2H Last 10</span>'
        '<span class="sf-val">1-0</span></div>'
        if details_h2h
        else ""
    )
    return (
        f'<div data-pick-card data-home="Atlanta Dream" data-away="Connecticut Sun">'
        f'<div class="lines-strip">{face}</div>'
        f'<div class="pick-conf-grid">{models}</div>'
        f"{details}</div>"
    )


def _pc(name: str, pct: str, side: str, cls: str) -> str:
    return (
        f'<div class="pc-box"><div class="pc-name">{name}</div>'
        f'<div class="pc-val">{pct}</div>'
        f'<div class="pc-side {cls}">{side}</div></div>'
    )


def test_wnba_consensus_hist_face_issues():
    bad = _card(
        '<div class="line-chip h2h-face-chip">'
        '<div class="line-chip-label">H2H Last 10</div>'
        '<div class="line-chip-val">150.8 (10 games)</div></div>'
    ) * 2
    issues = wnba_consensus_hist_face_issues(bad)
    assert any("Consensus Historical" in i for i in issues)
    assert any("H2H Last 10 on the face" in i for i in issues)

    good = _card(
        '<div class="line-chip consensus-hist-chip">'
        '<div class="line-chip-label">Consensus Historical Record</div>'
        '<div class="line-chip-val">Unanimous: 3-0</div></div>',
        models=(
            _pc("Edge", "60%", "Atlanta Dream", "home")
            + _pc("XSharp", "60%", "Atlanta Dream", "home")
            + _pc("Sharp Consensus", "60%", "Atlanta Dream", "home")
            + _pc("Efficiency", "60%", "Atlanta Dream", "home")
        ),
    ) * 2
    assert wnba_consensus_hist_face_issues(good) == []


def test_wnba_card_dissent_uses_side_not_face_pct():
    """Away pick at 92% must not count as a home lean (fake 4/4)."""
    models = (
        _pc("Edge", "73.8%", "Atlanta Dream", "home")
        + _pc("XSharp", "92.7%", "Connecticut Sun", "away")
        + _pc("Sharp Consensus", "73.4%", "Atlanta Dream", "home")
        + _pc("Efficiency", "75.2%", "Atlanta Dream", "home")
    )
    card = _card(
        '<div class="line-chip consensus-hist-chip">'
        '<div class="line-chip-label">Consensus Historical Record</div>'
        '<div class="line-chip-val">placeholder</div></div>',
        models=models,
    )
    dissent = _card_dissent_for_models(card, _WNBA_CONSENSUS_MODELS)
    assert dissent == (3, ("XSharp",))
    label = _wnba_consensus_hist_label(3, ("XSharp",), 0, 0, None, panel=4)
    assert label == "All but XSharp: 0-0"

    fixed = _inject_wnba_consensus_hist_chips(card + card)
    assert "All but XSharp:" in fixed
    assert "3/4" not in fixed
    assert "4/4" not in fixed
    assert "Last 7" not in fixed


def test_wnba_consensus_hist_rejects_wrong_pattern():
    models = (
        _pc("Edge", "73.8%", "Atlanta Dream", "home")
        + _pc("XSharp", "92.7%", "Connecticut Sun", "away")
        + _pc("Sharp Consensus", "73.4%", "Atlanta Dream", "home")
        + _pc("Efficiency", "75.2%", "Atlanta Dream", "home")
    )
    wrong = _card(
        '<div class="line-chip consensus-hist-chip">'
        '<div class="line-chip-label">Consensus Historical Record</div>'
        '<div class="line-chip-val">All but XSharp (3/4): 0-0</div></div>',
        models=models,
    ) * 2
    issues = wnba_consensus_hist_face_issues(wrong)
    assert any("wrong Consensus Historical pattern" in i for i in issues)


def test_wnba_season_games_undercount_issues():
    stale = """
    <h2>Season Performance</h2>
    <h3>Moneyline Accuracy by Model</h3>
    <div class="model-rec">33-34</div>
    <div class="model-rec">30-37</div>
    <div class="model-rec">39-28</div>
    <div class="model-rec">40-27</div>
    """
    issues = wnba_season_games_undercount_issues(stale)
    assert issues and "67" in issues[0]

    ok = """
    <h3>Moneyline Accuracy by Model</h3>
    <div class="model-rec">61-69</div>
    <div class="model-rec">62-68</div>
    <div class="model-rec">69-61</div>
    <div class="model-rec">69-61</div>
    """
    assert wnba_season_games_undercount_issues(ok) == []


def test_wnba_results_graded_clarity_issues():
    from qa.chart_shape import wnba_results_graded_clarity_issues

    bare = """
    <h2>Model Performance (Flat Unit Tracking)</h2>
    <p>Percentages are unit ROI</p>
    <h2>Season Performance</h2>
    """
    issues = wnba_results_graded_clarity_issues(bare)
    assert any("graded decisions" in i for i in issues)

    good = """
    <h2>Model Performance (Flat Unit Tracking)</h2>
    <p>Records count graded decisions — not every completed final</p>
    <h2>Season Performance</h2>
    <p>W-L tiles count graded model decisions</p>
    """
    assert wnba_results_graded_clarity_issues(good) == []
