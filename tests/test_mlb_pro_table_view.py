"""MLB Pro Table View — render, premium gating, explicit EV labels, copy parity.

Covers the MLB-only "Pro Table View": one row per game with both teams' Books
and Prediction Lab odds, logos, win prob, model-by-model picks, EV breakdown,
and free/premium access rules. Uses the SAME card data + copy serializer as the
existing cards, so nothing is fabricated.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from flask import render_template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "templates" / "espn_predictions_template.html"


def _mlb_pred(best_ev: str = "Moneyline", **override) -> dict:
    pred = {
        "away_team_id": "Seattle Mariners",
        "home_team_id": "Baltimore Orioles",
        "game_time": "6:35 PM ET",
        "face_model_label": "Sharp Consensus",
        "face_away_prob": 48.7,
        "face_home_prob": 51.3,
        "face_pick_team": "Baltimore Orioles",
        "face_pick_confidence": 51.3,
        # Both teams' odds — away is the favorite at the book, home the dog.
        "book_away_moneyline": -136,
        "book_home_moneyline": +113,
        "pl_model_away_ml": -103,
        "pl_model_home_ml": -114,
        "disp_book_spread": -1.5,
        "disp_pl_spread": -2.0,
        "disp_xs_spread": -1.0,
        "disp_book_total": 8.5,
        "disp_pl_total": 9.2,
        "disp_xs_total": 8.8,
        "pl_proj_away_pts": 4.1,
        "pl_proj_home_pts": 4.8,
        "xs_proj_away_pts": 4.3,
        "xs_proj_home_pts": 4.9,
        "glicko2_prob": 55.4,
        "trueskill_prob": 58.3,
        "elo_prob": 50.0,
        "xgb_prob": 35.4,
        "efficiency_prob": 23.9,
        "ensemble_prob": 51.3,
        "ml_ev": 24.5,
        "spread_ev": 9.1,
        "total_ev": 6.8,
        "best_ev_market": best_ev,
        "h2h_last10_total": 8.5,
        "h2h_last10_games": 5,
        "our_method": "efficiency",
        "spread_trend_record": "6-4",
        "total_trend_record": "7-3",
    }
    pred.update(override)
    return pred


def _render_table(is_premium: bool, preds=None) -> str:
    import NHL77FINAL as N

    with N.app.test_request_context("/mlb-picks"):
        return render_template(
            "includes/mlb_pro_table.html",
            _date_preds=preds if preds is not None else [_mlb_pred()],
            date="2026-06-08",
            sport="MLB",
            is_premium=is_premium,
        )


def _render_mlb_card(is_premium: bool) -> str:
    import NHL77FINAL as N

    conf_models = [
        {"name": "Grinder2", "prob": 55.4, "key": "glicko2"},
        {"name": "Takedown", "prob": 58.3, "key": "trueskill"},
        {"name": "Edge", "prob": 50.0, "key": "elo"},
        {"name": "XSharp", "prob": 35.4, "key": "xgb"},
        {"name": "Efficiency", "prob": 23.9, "key": "efficiency"},
        {"name": "Sharp Consensus", "prob": 51.3, "key": "consensus"},
    ]
    with N.app.test_request_context("/mlb-picks"):
        return render_template(
            "includes/game_card_body.html",
            card=_mlb_pred(best_ev="Spread"),
            conf_models=conf_models,
            sport="MLB",
            sport_info={"icon": "⚾", "name": "MLB"},
            is_results=False,
            is_final=False,
            is_premium=is_premium,
            spread_label="Run Line",
            force_rl=True,
            card_details_id="card-details-mlb-test",
        )


def _payload(rendered: str) -> dict:
    match = re.search(
        r'<script type="application/json" class="prediction-card-data">(.*?)</script>',
        rendered,
        flags=re.S,
    )
    assert match, "prediction-card-data payload missing"
    return json.loads(html.unescape(match.group(1)))


def _copy_output(payload: dict, sport_name: str = "MLB") -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available for serializer execution")
    template = TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"function cleanCopyValue[\s\S]+?\n    function showDate", template)
    assert match, "copy serializer function block missing"
    serializer_js = match.group(0).rsplit("\n    function showDate", 1)[0]
    script = f"""
const sportName = '{sport_name}';
{serializer_js}
const payload = {json.dumps(payload)};
const card = {{ querySelector: () => ({{ textContent: JSON.stringify(payload) }}) }};
console.log(serializePredictionCard(card));
"""
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    return result.stdout


# ── Render + structure ────────────────────────────────────────────────────
def test_pro_table_view_renders_on_mlb_page():
    out = _render_table(is_premium=True, preds=[_mlb_pred(), _mlb_pred()])
    assert 'class="mlbpt-table"' in out
    assert "MLB Picks — 2026-06-08" in out
    assert out.count('class="mlbpt-row"') == 2  # one row per game
    # Removed columns: AI Moneyline Pick (redundant with consensus %) and per-row Copy.
    assert "AI Moneyline Pick" not in out
    assert ">Copy</th>" not in out


def test_page_template_wires_toggle_and_include_for_team_sports():
    src = TEMPLATE.read_text(encoding="utf-8")
    assert "Pro Table View" in src
    assert "Cards View" in src
    assert "setPicksView(" in src
    assert "{% include 'includes/mlb_pro_table.html' %}" in src
    # Toggle + include are gated to team-vs-team sports via _pro_table_ok.
    assert "_pro_table_ok" in src
    assert "{% if _pro_table_ok" in src


def test_table_generalizes_to_non_mlb_sport():
    # NBA: spread word is "Spread" (not Run Line), title uses the sport, and the
    # run-line force is OFF so a real spread renders.
    out = _render_table(is_premium=True, preds=[_mlb_pred(disp_book_spread=-5.5)])
    # re-render under NBA by passing sport explicitly
    import NHL77FINAL as N
    from flask import render_template
    with N.app.test_request_context("/nba-picks"):
        nba = render_template("includes/mlb_pro_table.html",
                              _date_preds=[_mlb_pred(disp_book_spread=-5.5)],
                              date="2026-06-08", sport="NBA", is_premium=True)
    assert "Books Spread / Total" in nba          # not "Run Line"
    assert "NBA Picks — 2026-06-08" in nba
    assert "Run Line" not in nba


# ── Away / Home cells: logos, names, win prob, both teams' odds ────────────
def test_away_home_cells_show_logos_names_winprob_and_odds():
    out = _render_table(is_premium=False)  # free user still sees these
    assert "teamlogos/mlb" in out                  # real ESPN team logos
    assert "Mariners" in out and "Orioles" in out  # team names
    assert "48.7%" in out and "51.3%" in out        # win probabilities
    # Picked side shows the Sharp Consensus label, the other shows Win Prob.
    assert "Sharp Consensus" in out
    assert "Win Prob" in out


def test_both_teams_books_odds_present():
    out = _render_table(is_premium=False)
    assert "Books:</span> -136" in out   # away
    assert "Books:</span> +113" in out   # home


def test_prediction_lab_odds_are_locked_for_free_users():
    # ANONYMOUS ACCESS CONTRACT: PL odds are premium even though book ML is free.
    out = _render_table(is_premium=False)
    assert "PL:</span> -103" not in out
    assert "PL:</span> -114" not in out
    assert out.count('title="Premium">Premium</span>') >= 2


# ── EV labels ──────────────────────────────────────────────────────────────
def test_ev_column_consolidates_ml_spread_total_and_drops_best_ev():
    out = _render_table(is_premium=True)
    # One consolidated "EV" column header; the old separate headers are gone.
    assert ">EV</th>" in out
    assert "Moneyline EV" not in out
    assert "Spread EV" not in out
    assert "Total EV" not in out
    # Best EV Market is removed entirely (not tracked in results).
    assert "Best EV Market" not in out
    assert "mlbpt-badge-ev" not in out
    # All three EV values still render (labeled ML / Spread / Total inside the cell).
    assert "+24.5%" in out   # moneyline EV
    assert "+9.1%" in out    # spread EV
    assert "+6.8%" in out    # total EV


def test_books_run_line_and_total_share_one_column_with_short_names():
    out = _render_table(is_premium=True)
    # Merged header.
    assert "Books Run Line / Total" in out
    assert ">Books Total</th>" not in out
    # Run line uses the short team name (e.g. "Mariners -1.5"), not the full city+name.
    assert "Mariners -1.5" in out
    assert "Seattle Mariners -1.5" not in out
    # Total stacked in the same cell (labeled "Total", not "O/U").
    assert "Total 8.5" in out
    assert "O/U" not in out


# ── Access rules: free locks premium, paid sees everything ─────────────────
def test_free_user_cannot_see_premium_columns():
    # ANONYMOUS ACCESS CONTRACT: model percentages are locked.
    out = _render_table(is_premium=False)
    assert "mlbpt-lock" in out                  # premium cells locked
    assert "+9.1%" not in out                   # spread EV hidden
    assert "+6.8%" not in out                   # total EV hidden
    assert "+24.5%" not in out                  # moneyline EV hidden
    assert "Spread:" not in out                 # PL/XSharp lines hidden
    # Free fields still present.
    assert "Mariners -1.5" in out               # sportsbook spread is free
    assert "Total: Premium" in out               # sportsbook total is premium


def test_paid_user_sees_full_table():
    out = _render_table(is_premium=True)
    assert "mlbpt-lock" not in out              # nothing locked
    assert "mlbpt-mteam" in out                 # model-by-model picks shown
    assert "Spread:" in out and "Total:" in out and "mlbpt-scoreline" in out
    # All six models present in the header.
    for model in ["Grinder2", "Takedown", "Edge", "XSharp", "Efficiency", "Sharp Consensus"]:
        assert model in out


# ── Copy: both teams' odds, free vs paid parity (reuses card serializer) ────
def test_mlb_card_payload_carries_both_teams_books_and_pl_odds():
    payload = _payload(_render_mlb_card(is_premium=True))
    ml = payload["moneyline"]
    assert ml["awayBooks"] == -136 and ml["homeBooks"] == 113
    assert ml["awayPredictionLab"] == -103 and ml["homePredictionLab"] == -114


def test_paid_copy_includes_both_teams_odds_and_premium_fields():
    copied = _copy_output(_payload(_render_mlb_card(is_premium=True)))
    # Both teams' Books AND Prediction Lab odds appear in the copy output.
    assert "Books: -136" in copied and "Books: +113" in copied
    assert "Prediction Lab: -103" in copied and "Prediction Lab: -114" in copied
    for field in ["Moneyline EV", "Spread EV", "Total EV", "Best EV Market", "Sharp Consensus"]:
        assert field in copied


def test_free_copy_only_has_free_moneyline_fields():
    # COPY CONTRACT: embedded JSON cannot leak values hidden by the paywall.
    copied = _copy_output(_payload(_render_mlb_card(is_premium=False)))
    # Both teams' free moneyline odds present.
    assert "Books: -136" in copied and "Books: +113" in copied
    assert "Prediction Lab: -103" not in copied
    assert "Prediction Lab: -114" not in copied
    assert "Moneyline EV" not in copied
    # Premium fields must NOT leak.
    for premium in ["Spread EV", "Total EV", "Projected Score", "Pick Confidence"]:
        assert premium not in copied


# ── Existing card view unaffected ──────────────────────────────────────────
def test_existing_mlb_card_view_still_renders():
    out = _render_mlb_card(is_premium=True)
    assert "matchup-row" in out                 # card matchup still renders
    assert "Mariners" in out and "Orioles" in out
    assert "AI pick" in out
