"""NFL: 3/6 splits in consensus table + PL vs Books face chip (not Books spread)."""
from __future__ import annotations

from mlb_consensus_hub import (
    _consensus_agreements_from_finals,
    build_consensus_records_html,
)
from team_results_charts import (
    _inject_nfl_consensus_hist_chips,
    _inject_nfl_pl_vs_books_chips,
)


MODEL_ORDER = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _game(*, picks: dict[str, str], hs: int = 21, aa: int = 17, gid: str = "g1"):
    home, away = "Home Team", "Away Team"
    models = {
        name: {"pick": picks[name], "side": "home" if picks[name] == home else "away"}
        for name in MODEL_ORDER
    }
    return {
        "game_id": gid,
        "game_date": "2026-09-14",
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hs,
        "away_score": aa,
        "final": True,
        "models": models,
        "book_home_moneyline": -150,
        "book_away_moneyline": 130,
        "pl_model_home_ml": 120,
        "pl_model_away_ml": -140,
    }


def _split_picks():
    return {
        "Grinder2": "Home Team",
        "Takedown": "Away Team",
        "Edge": "Away Team",
        "XSharp": "Home Team",
        "Sharp Consensus": "Home Team",
        "Efficiency": "Away Team",
    }


def test_nfl_three_three_grades_push_and_appears_in_table():
    g = _game(picks=_split_picks())
    rows = _consensus_agreements_from_finals([g], sport="nfl")
    assert len(rows) == 1
    assert rows[0]["is_three_three"] is True
    assert rows[0]["grade"] == "PUSH"

    html = build_consensus_records_html([g], sport="nfl")
    assert "3/6 Split / no consensus" in html
    assert "Even splits (3/6) are included" in html
    assert "Even splits are omitted" not in html


def test_mlb_three_three_still_omitted():
    g = _game(picks=_split_picks())
    rows = _consensus_agreements_from_finals([g], sport="mlb")
    assert rows[0]["grade"] == "NO_BET"
    html = build_consensus_records_html([g], sport="mlb")
    assert "3/6 Split / no consensus" not in html
    assert "Even splits are omitted" in html


def test_nfl_face_replaces_books_spread_with_pl_vs_books(monkeypatch):
    card = """
    <div class="game-card-stack" data-pick-card data-home="Buffalo Bills" data-away="Detroit Lions">
      <div class="ml-line face-books-ml"><span class="ml-src books">Books</span>
        <span class="ml-num">+140</span></div>
      <div class="ml-line face-books-ml"><span class="ml-src books">Books</span>
        <span class="ml-num">-160</span></div>
      <div class="ml-line face-pl-ml"><span class="ml-src pl">Prediction Lab</span>
        <span class="ml-num">-120</span></div>
      <div class="ml-line face-pl-ml"><span class="ml-src pl">Prediction Lab</span>
        <span class="ml-num">+100</span></div>
      <div class="pick-conf-grid">
        <div class="pc-box"><div class="pc-name">Grinder2</div>
          <div class="pc-val">60%</div><div class="pc-side home">B</div></div>
        <div class="pc-box"><div class="pc-name">Takedown</div>
          <div class="pc-val">55%</div><div class="pc-side away">D</div></div>
        <div class="pc-box"><div class="pc-name">Edge</div>
          <div class="pc-val">58%</div><div class="pc-side away">D</div></div>
        <div class="pc-box"><div class="pc-name">XSharp</div>
          <div class="pc-val">51%</div><div class="pc-side home">B</div></div>
        <div class="pc-box"><div class="pc-name">Sharp Consensus</div>
          <div class="pc-val">50%</div><div class="pc-side home">B</div></div>
        <div class="pc-box"><div class="pc-name">Efficiency</div>
          <div class="pc-val">58%</div><div class="pc-side away">D</div></div>
      </div>
      <div class="lines-strip">
        <div class="line-chip"><div class="line-chip-label">Books spread</div>
          <div class="line-chip-val">Buffalo Bills -3</div></div>
        <div class="line-chip"><div class="line-chip-label">Books total</div>
          <div class="line-chip-val">O/U 52.5</div></div>
      </div>
    </div>
    """

    monkeypatch.setattr(
        "team_results_charts._six_model_consensus_finals",
        lambda *_a, **_k: [_game(picks=_split_picks())],
    )
    monkeypatch.setattr(
        "team_results_charts._consensus_d7_combo_lookup",
        lambda *_a, **_k: {(3, ()): (0, 0, None, 2)},
    )

    out = _inject_nfl_consensus_hist_chips(card)
    assert "Books spread" not in out
    assert "Books total" not in out
    assert "Consensus Historical Record" in out
    # Mock lookup is (0,0,None,2) → pushes only → 0-0-2
    assert "Consensus Record: 3/6 Split (0-0-2)" in out
    chip = out.split("consensus-hist-chip", 1)[1].split("</div></div>", 1)[0]
    assert "Last 7 Days" not in chip
    assert "/ no consensus" not in chip
    assert "PL vs Books" in out
    assert "Past 30 Days" in out
    assert "Books favorite" in out


def test_nfl_consensus_hist_label_compact():
    from team_results_charts import _nfl_consensus_hist_label

    assert (
        _nfl_consensus_hist_label(3, (), 0, 0, None, panel=6, pushes=0)
        == "Consensus Record: 3/6 Split (0-0)"
    )
    assert (
        _nfl_consensus_hist_label(6, (), 4, 1, 80.0, panel=6, pushes=0)
        == "Consensus Record: 6/6 Unanimous (4-1)"
    )


def test_nfl_picks_card_width_css_injected():
    from team_results_charts import _inject_nfl_picks_card_width_css

    html = "<html><head></head><body class=\"sport-nfl\"></body></html>"
    out = _inject_nfl_picks_card_width_css(html)
    assert 'id="nfl-picks-card-width"' in out
    assert "minmax(520px,1fr)" in out
    assert "word-break:normal" in out
    # idempotent
    assert out.count('id="nfl-picks-card-width"') == 1
    assert _inject_nfl_picks_card_width_css(out).count('id="nfl-picks-card-width"') == 1
