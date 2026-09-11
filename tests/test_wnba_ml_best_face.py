"""WNBA moneyline face must be the best published model, not Edge.

Owner: grade the best model (Efficiency 45-24), not Edge (28-41).
Chart summary and games table primary column must match.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wnba_ui_fixup import (
    apply_wnba_best_ml_face,
    fill_wnba_card_gaps,
    fill_wnba_placeholder_probs,
    patch_wnba_cards_ml_face,
    relabel_wnba_chart_ml_pick_header,
    wnba_best_ml_model,
    wnba_ml_table_still_grades_edge,
)


def _season_models():
    return {
        "Edge": {"w": 28, "l": 41, "n": 69, "pct": 40.6, "record": "28-41"},
        "XSharp": {"w": 28, "l": 41, "n": 69, "pct": 40.6, "record": "28-41"},
        "Sharp Consensus": {"w": 39, "l": 30, "n": 69, "pct": 56.5, "record": "39-30"},
        "Efficiency": {"w": 45, "l": 24, "n": 69, "pct": 65.2, "record": "45-24"},
    }


def _payload():
    models = _season_models()
    finals = [
        {
            "game_date": "2026-08-17",
            "away_team_id": "Dallas Wings",
            "home_team_id": "Golden State Valkyries",
            "face_pick": "Dallas Wings",
            "face_prob": 52.9,
            "correct": False,
            "models": {
                "Edge": {"pick": "Dallas Wings", "prob": 52.9, "correct": False},
                "XSharp": {"pick": "Dallas Wings", "prob": 57.9, "correct": False},
                "Sharp Consensus": {"pick": "Golden State Valkyries", "prob": 55.9, "correct": True},
                "Efficiency": {"pick": "Golden State Valkyries", "prob": 72.3, "correct": True},
            },
        },
        {
            "game_date": "2026-08-16",
            "away_team_id": "Chicago Sky",
            "home_team_id": "Seattle Storm",
            "face_pick": "Chicago Sky",
            "face_prob": 52.9,
            "correct": True,
            "models": {
                "Edge": {"pick": "Chicago Sky", "prob": 52.9, "correct": True},
                "XSharp": {"pick": "Chicago Sky", "prob": 62.9, "correct": True},
                "Sharp Consensus": {"pick": "Seattle Storm", "prob": 58.9, "correct": False},
                "Efficiency": {"pick": "Seattle Storm", "prob": 76.6, "correct": False},
            },
        },
        {
            "game_date": "2026-08-11",
            "away_team_id": "Phoenix Mercury",
            "home_team_id": "Los Angeles Sparks",
            "face_pick": "Phoenix Mercury",
            "face_prob": 73.8,
            "correct": True,
            "models": {
                "Efficiency": {"pick": "Phoenix Mercury", "prob": 73.8, "correct": True},
            },
        },
    ]
    pl = {"w": 68, "l": 31, "n": 99, "pct": 68.7, "record": "68-31"}
    return {
        "ok": True,
        "markets": {
            "moneyline": {
                "label": "Moneyline",
                "model_order": ["Edge", "XSharp", "Sharp Consensus", "Efficiency"],
                "tallies": {
                    "season": {"models": models, "games": 69},
                    "last_night": {"models": {"Efficiency": {"w": 1, "l": 0, "n": 1, "pct": 100.0, "record": "1-0"}}},
                    "last_7": {"models": {"Efficiency": {"w": 12, "l": 3, "n": 15, "pct": 80.0, "record": "12-3"}}},
                },
                "finals": finals,
            },
            "spread": {
                "label": "Spread",
                "tallies": {
                    "last_night": {"models": {"Prediction Lab": {"w": 1, "l": 0, "n": 1, "record": "1-0"}}},
                    "last_7": {"models": {"Prediction Lab": {"w": 14, "l": 4, "n": 18, "record": "14-4"}}},
                    "season": {"models": {"Prediction Lab": dict(pl)}},
                },
            },
        },
        "finals": finals,
    }


def test_best_model_is_efficiency_not_edge():
    best = wnba_best_ml_model(_season_models())
    assert best is not None
    assert best["name"] == "Efficiency"
    assert best["record"] == "45-24"


def test_apply_face_rewrites_owner_rows_and_keeps_aug11():
    out = apply_wnba_best_ml_face(_payload())
    assert out["ml_face_model"] == "Efficiency"
    rows = {f["game_date"] + f["away_team_id"]: f for f in out["markets"]["moneyline"]["finals"]}
    dallas = rows["2026-08-17Dallas Wings"]
    assert dallas["face_pick"] == "Golden State Valkyries"
    assert dallas["face_prob"] == 72.3
    assert dallas["correct"] is True
    chi = rows["2026-08-16Chicago Sky"]
    assert chi["face_pick"] == "Seattle Storm"
    assert chi["face_prob"] == 76.6
    assert chi["correct"] is False
    phx = rows["2026-08-11Phoenix Mercury"]
    assert phx["face_pick"] == "Phoenix Mercury"
    assert phx["correct"] is True
    # Spread last-fix records stay put
    sp = out["markets"]["spread"]["tallies"]
    assert sp["last_night"]["models"]["Prediction Lab"]["record"] == "1-0"
    assert sp["last_7"]["models"]["Prediction Lab"]["record"] == "14-4"
    assert sp["season"]["models"]["Prediction Lab"]["record"] == "68-31"


def test_ungraded_scored_row_fills_from_pl_proj():
    payload = {
        "markets": {
            "moneyline": {
                "tallies": {"season": {"models": _season_models()}},
                "finals": [
                    {
                        "game_date": "2026-08-27",
                        "away_team_id": "Washington Mystics",
                        "home_team_id": "Phoenix Mercury",
                        "away_score": 73,
                        "home_score": 80,
                        "face_pick": "—",
                        "face_prob": None,
                        "correct": None,
                        "pl_proj": "Washington Mystics 79.5 – Phoenix Mercury 85",
                        "models": {
                            "Edge": {"pick": "—", "prob": None, "correct": None},
                            "XSharp": {"pick": "", "prob": None, "correct": None},
                        },
                    }
                ],
            }
        }
    }
    payload["finals"] = payload["markets"]["moneyline"]["finals"]
    out = apply_wnba_best_ml_face(payload)
    row = out["markets"]["moneyline"]["finals"][0]
    assert row["face_pick"] == "Phoenix Mercury"
    assert row["correct"] is True
    assert out["markets"]["moneyline"]["ungraded"] == 0
    assert row["models"]["Prediction Lab"]["pick"] == "Phoenix Mercury"


def test_chart_header_not_edge_when_efficiency_better():
    html = "<thead><tr><th>Date</th><th>Edge pick</th><th>%</th><th>Result</th></tr></thead>"
    assert wnba_ml_table_still_grades_edge(html) is True
    out = relabel_wnba_chart_ml_pick_header(html, "Efficiency")
    assert wnba_ml_table_still_grades_edge(out) is False
    assert "<th>Efficiency pick</th>" in out


def test_cards_season_headline_moves_off_edge_and_sc():
    html = """
    <h3>Moneyline Accuracy by Model</h3>
    <div class="model-label">Edge</div><div class="model-acc">40.6%</div><div class="model-rec">28-41</div>
    <div class="model-label">XSharp</div><div class="model-acc">40.6%</div><div class="model-rec">28-41</div>
    <div class="model-label">Sharp Consensus</div><div class="model-acc">56.5%</div><div class="model-rec">39-30</div>
    <div class="model-label">Efficiency</div><div class="model-acc">65.2%</div><div class="model-rec">45-24</div>
    <div>🎯 Moneyline (Sharp Consensus)</div>
                    <div style="font-size:2em;font-weight:bold;color:#00C076;">56.5%</div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">39-30 <span>
    <div class="daily-tally-card highlight"><div class="daily-model">🏆 Sharp Consensus</div></div>
    <div class="daily-tally-card "><div class="daily-model">⚡ Efficiency</div></div>
    <div class="model-card highlight"><div class="model-label">🏆 Sharp Consensus</div></div>
    <div class="model-card "><div class="model-label">⚡ Efficiency</div></div>
    """
    out = patch_wnba_cards_ml_face(html)
    assert "Moneyline (Efficiency)" in out
    assert "45-24" in out
    assert "65.2%" in out
    assert re.search(r"65\.2%</div></div>", out) is None
    assert 'daily-tally-card highlight"><div class="daily-model">⚡ Efficiency' in out
    assert 'model-card highlight"><div class="model-label">⚡ Efficiency' in out
    assert 'daily-tally-card highlight"><div class="daily-model">🏆 Sharp Consensus' not in out


def test_cards_banner_cleans_double_close_when_already_efficiency():
    html = """
    <h3>Moneyline Accuracy by Model</h3>
    <div class="model-label">Edge</div><div class="model-acc">40.6%</div><div class="model-rec">28-41</div>
    <div class="model-label">Efficiency</div><div class="model-acc">65.2%</div><div class="model-rec">45-24</div>
    <div>🎯 Moneyline (Efficiency)</div>
                    <div style="font-size:2em;font-weight:bold;color:#00C076;">65.2%</div></div>
                    <div style="font-size:0.85em;opacity:0.9;color:#334155;">45-24 <span>
    """
    out = patch_wnba_cards_ml_face(html)
    assert "Moneyline (Efficiency)" in out
    assert re.search(r"65\.2%</div></div>", out) is None
    assert "65.2%</div>" in out
    assert "45-24" in out


def test_best_ml_model_stats_includes_efficiency():
    import NHL77FINAL as N

    overall = {
        "elo": {"correct": 28, "total": 69, "accuracy": 40.6},
        "xgboost": {"correct": 28, "total": 69, "accuracy": 40.6},
        "ensemble": {"correct": 39, "total": 69, "accuracy": 56.5},
        "efficiency": {"correct": 45, "total": 69, "accuracy": 65.2},
    }
    best = N._best_ml_model_stats(overall, sport="WNBA")
    assert best["label"] == "Efficiency"
    assert best["correct"] == 45
    assert best["total"] == 69
    perf = N._build_season_performance_summary(overall, {}, sport="WNBA")
    assert perf["ml_model_label"] == "Efficiency"
    assert perf["ml_correct"] == 45
    assert perf["ml_total"] == 69


def test_fill_wnba_placeholder_probs_from_pl_odds():
    html = """
    <div class="game-card-stack" data-pick-card
                    data-away="Connecticut Sun"
                    data-home="Atlanta Dream"
                    data-pick="Atlanta Dream"
                    data-conf="50.0"
                    data-m-edge="50.0"
                    data-m-xsharp="50.0"
                    data-m-consensus="50.0"
                    data-pl-spread="Atlanta Dream -7.5"
                    data-xs-spread="Connecticut Sun -16"
                    data-pl-proj="Connecticut Sun 72 – Atlanta Dream 79"
                    data-xs-proj="Connecticut Sun 90 – Atlanta Dream 74">
      <div class="matchup-row">
        <div class="team-slot ">
          <div class="team-name">Connecticut Sun</div>
          <div class="win-pct">50.0<span class="unit">%</span></div>
          <span class="ml-src pl">Prediction Lab</span>
          <span class="ml-num dog">+262</span>
        </div>
        <div class="team-slot ">
          <div class="team-name">Atlanta Dream</div>
          <div class="win-pct">50.0<span class="unit">%</span></div>
          <span class="ml-src pl">Prediction Lab</span>
          <span class="ml-num fav">-323</span>
        </div>
      </div>
      <div class="lines-strip"></div>
      <div class="pick-conf-grid">
        <div class="pc-box "><div class="pc-name">Edge</div><div class="pc-val">50.0%</div><div class="pc-side home">Atlanta Dream</div></div>
        <div class="pc-box "><div class="pc-name">XSharp</div><div class="pc-val">50.0%</div><div class="pc-side home">Atlanta Dream</div></div>
        <div class="pc-box consensus"><div class="pc-name">Sharp Consensus</div><div class="pc-val">50.0%</div><div class="pc-side home">Atlanta Dream</div></div>
      </div>
    </div>
    """
    out = fill_wnba_placeholder_probs(html)
    assert "50.0" not in re.findall(r'class="win-pct">([\d.]+)', out)
    assert "73.4" in out
    assert "26.6" in out
    assert "Connecticut Sun" in out and "92.7%" in out
    assert "Efficiency" in out
    assert 'data-conf="73.4"' in out


def test_fill_wnba_card_gaps_clock_logo_h2h(monkeypatch):
    html = """
    <script type="application/ld+json">
    {"@type":"SportsEvent","startDate":"2026-09-17T19:00:00-04:00"}
    </script>
    <div class="game-card-stack" data-pick-card
                    data-away="Phoenix Mercury"
                    data-home="Portland Fire"
                    data-time="Upcoming"
                    data-h2h="">
      <span class="game-time">Upcoming</span>
      <div class="team-slot ">
        <img class="team-logo" src="/static/pl-logo.svg" alt="" width="52" height="52" loading="lazy">
        <div class="team-name">Phoenix Mercury</div>
        <div class="model-tag">Sharp Consensus</div>
      </div>
      <div class="team-slot favored">
        <img class="team-logo" src="/static/pl-logo.svg" alt="" width="52" height="52" loading="lazy">
        <div class="team-name">Portland Fire</div>
        <div class="model-tag">Sharp Consensus</div>
      </div>
      <div class="sf-item"><span class="sf-label">H2H Last 10</span> <span class="sf-val">—</span></div>
    </div>
    """

    monkeypatch.setattr(
        "wnba_ui_fixup._wnba_espn_clocks",
        lambda _html: {("phoenixmercury", "portlandfire"): "10:00 PM ET"},
    )
    monkeypatch.setattr(
        "wnba_ui_fixup._wnba_espn_h2h_text",
        lambda home, away: "161.5 (2 games)",
    )
    out = fill_wnba_card_gaps(html)
    assert 'data-time="10:00 PM ET"' in out
    assert 'class="game-time">10:00 PM ET</span>' in out
    assert "Upcoming" not in out
    assert "wnba/500/por.png" in out
    assert "wnba/500/phx.png" in out
    assert "/static/pl-logo.svg" not in out
    assert 'data-h2h="161.5 (2 games)"' in out
    assert "161.5 (2 games)" in out
