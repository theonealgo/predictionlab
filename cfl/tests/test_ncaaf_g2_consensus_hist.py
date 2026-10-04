"""NCAAF unlock: G2/TD fill, Consensus Historical face, Model Performance win%."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

from chart_shape import (  # noqa: E402
    model_performance_wl_pct_issues,
    ncaaf_consensus_hist_face_issues,
)
from NHL77FINAL import build_roi_cards, _fill_ncaaf_missing_card_models  # noqa: E402


def test_build_roi_cards_win_pct_primary():
    weekly = {
        "total": {
            "roi_pct": 35.59,
            "units_won": 21.0,
            "wins": 40,
            "losses": 19,
            "pushes": 0,
        }
    }
    cards = build_roi_cards({}, weekly, {}, win_pct_primary=True)
    assert cards["total"]["weekly"]["roi"] == "67.8%"
    assert "40-19-0" in cards["total"]["weekly"]["detail"]
    assert "+21.00u" in cards["total"]["weekly"]["detail"]


def test_model_performance_wl_pct_issues_catches_roi():
    html = """
    <h2>Model Performance (Flat Unit Tracking)</h2>
    <div>7 Days</div><div>35.59%</div><div>40-19-0, +21.00u</div>
    <!--
    """
    issues = model_performance_wl_pct_issues(html, "NCAAF")
    assert issues and "67.8" in issues[0]


def test_ncaaf_consensus_hist_face_issues():
    bad = """
    <div data-pick-card>
      <div class="line-chip h2h-face-chip"><div class="line-chip-label">H2H Last 10</div></div>
      <div class="pick-conf-grid">
        <div class="pc-name">Grinder2</div><div class="pc-val">N/A</div>
        <div class="pc-name">Takedown</div><div class="pc-val">N/A</div>
        <div class="pc-name">Edge</div><div class="pc-val">67.4%</div>
        <div class="pc-name">XSharp</div><div class="pc-val">55.0%</div>
      </div>
    </div>
    <div data-pick-card>
      <div class="pick-conf-grid">
        <div class="pc-name">Edge</div><div class="pc-val">60%</div>
      </div>
    </div>
    """
    issues = ncaaf_consensus_hist_face_issues(bad)
    assert any("Consensus Historical" in i for i in issues)
    assert any("Grinder2/Takedown" in i for i in issues)


def test_fill_ncaaf_missing_card_models(monkeypatch):
    pred = {
        "home_team_id": "Pittsburgh Panthers",
        "away_team_id": "Syracuse Orange",
        "game_date": "2026-09-19",
        "glicko2_prob": None,
        "trueskill_prob": None,
    }

    def fake_v2(sport, home, away, date=None):
        return {"glicko2_prob": 0.8613, "trueskill_prob": 0.9734}

    monkeypatch.setattr("NHL77FINAL.get_v2_prediction", fake_v2)
    _fill_ncaaf_missing_card_models(pred)
    assert pred["glicko2_prob"] == 86.1
    assert pred["trueskill_prob"] == 97.3
