"""Prediction card copy output must stay in lockstep with visible card fields."""
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


def _base_card() -> dict:
    return {
        "away_team_id": "San Antonio Spurs",
        "home_team_id": "New York Knicks",
        "game_time": "7:30 PM ET",
        "face_model_label": "Sharp Consensus",
        "face_home_prob": 41.8,
        "face_away_prob": 58.2,
        "face_pick_team": "San Antonio Spurs",
        "face_pick_confidence": 58.2,
        "face_edge_pct": 6.4,
        "book_away_moneyline": +113,
        "book_home_moneyline": -136,
        "pl_model_away_ml": -114,
        "pl_model_home_ml": -103,
        "disp_book_spread": -2.5,
        "disp_pl_spread": -8.5,
        "disp_xs_spread": -4.5,
        "disp_book_total": 216.5,
        "disp_pl_total": 207,
        "disp_xs_total": 219.5,
        "pl_proj_away_pts": 99,
        "pl_proj_home_pts": 108,
        "xs_proj_away_pts": 107.5,
        "xs_proj_home_pts": 112,
        "ml_ev": 24.5,
        "spread_ev": 9.1,
        "total_ev": 6.8,
        "best_ev_market": "Spread",
        "best_ev_pick": "Spread",
        "spread_pick_label": "New York Knicks -2.5",
        "total_pick_label": "Under 216.5",
        "h2h_last10_total": 213.5,
        "h2h_last10_games": 10,
        "our_method": "efficiency",
        "spread_trend_record": "6-4",
        "total_trend_record": "7-3",
    }


def _conf_models() -> list[dict]:
    return [
        {"name": "Grinder2", "prob": 55.4, "key": "glicko2"},
        {"name": "Takedown", "prob": 58.3, "key": "trueskill"},
        {"name": "Edge", "prob": 50.0, "key": "elo"},
        {"name": "XSharp", "prob": 35.4, "key": "xgb"},
        {"name": "Efficiency", "prob": 23.9, "key": "efficiency"},
        {"name": "Sharp Consensus", "prob": 58.2, "key": "consensus"},
    ]


def _render_card(is_premium: bool) -> str:
    import NHL77FINAL as N

    with N.app.test_request_context("/nba-picks"):
        return render_template(
            "includes/game_card_body.html",
            card=_base_card(),
            conf_models=_conf_models(),
            sport="NBA",
            sport_info={"icon": "🏀", "name": "NBA"},
            is_results=False,
            is_final=False,
            is_premium=is_premium,
            spread_label="Spread",
            force_rl=False,
            card_details_id="card-details-test",
        )


def _payload(rendered: str) -> dict:
    match = re.search(
        r'<script type="application/json" class="prediction-card-data">(.*?)</script>',
        rendered,
        flags=re.S,
    )
    assert match, "prediction-card-data payload missing"
    return json.loads(html.unescape(match.group(1)))


def _copy_output(payload: dict) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available for serializer execution")
    template = (ROOT / "templates" / "espn_predictions_template.html").read_text(encoding="utf-8")
    match = re.search(r"function cleanCopyValue[\s\S]+?\n    function showDate", template)
    assert match, "copy serializer function block missing"
    serializer_js = match.group(0).rsplit("\n    function showDate", 1)[0]
    script = f"""
const sportName = 'NBA';
{serializer_js}
const payload = {json.dumps(payload)};
const card = {{ querySelector: () => ({{ textContent: JSON.stringify(payload) }}) }};
console.log(serializePredictionCard(card));
"""
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    return result.stdout


def test_generic_ev_label_never_renders_by_itself():
    rendered = _render_card(is_premium=True)
    assert not re.search(r">\s*EV\s*<", rendered)
    assert "Moneyline EV" in rendered
    assert "Spread EV" in rendered
    assert "Total EV" in rendered
    assert "Best EV Market" in rendered


def test_paid_copy_includes_every_visible_premium_card_field():
    rendered = _render_card(is_premium=True)
    copied = _copy_output(_payload(rendered))

    for visible_field in [
        "Moneyline EV",
        "Spread EV",
        "Total EV",
        "Best EV Market",
        "Recommended Premium Market",
        "Projected Score",
        "Pick Confidence",
        "Grinder2",
        "Takedown",
        "Edge",
        "XSharp",
        "Efficiency",
        "Sharp Consensus",
    ]:
        assert visible_field in rendered, f"{visible_field} should be visible on paid card"
        assert visible_field in copied, f"{visible_field} visible on card but missing from copy"

    assert "Odds &amp; Lines" in rendered
    assert "Odds & Lines" in copied

    for table_label in ["Books", "Prediction Lab", "XSharp", "Spread", "Total"]:
        assert table_label in rendered

    for copied_line in [
        "Books Spread",
        "Prediction Lab Spread",
        "XSharp Spread",
        "Books Total",
        "Prediction Lab Total",
        "XSharp Total",
    ]:
        assert copied_line in copied

    assert "New York Knicks -2.5" in copied
    assert "San Antonio Spurs 99" in copied
    assert "New York Knicks 108" in copied


def test_free_copy_and_payload_do_not_expose_premium_values():
    rendered = _render_card(is_premium=False)
    payload = _payload(rendered)
    copied = _copy_output(payload)

    assert payload["premium"] is None
    assert "Spread model available" in rendered
    assert "Total model available" in rendered
    assert "Projected score available" in rendered
    assert "Model-by-model confidence available" in rendered
    assert "EV market breakdown available" in rendered

    for premium_value in [
        "Spread EV",
        "Total EV",
        "Books Spread",
        "Prediction Lab Spread",
        "XSharp Spread",
        "Books Total",
        "Prediction Lab Total",
        "XSharp Total",
        "Projected Score",
        "Pick Confidence",
        "Grinder2",
        "Takedown",
        "XSharp",
        "New York Knicks -2.5",
        "Under 216.5",
        "216.5",
        "207",
        "219.5",
    ]:
        assert premium_value not in copied, f"free copy leaked premium field/value: {premium_value}"

    assert "Moneyline EV" in copied
    assert "AI Pick: San Antonio Spurs" in copied


def test_best_ev_market_has_matching_ev_value_in_paid_render_and_copy():
    rendered = _render_card(is_premium=True)
    copied = _copy_output(_payload(rendered))

    assert "Best EV Market" in rendered
    assert "Spread" in rendered
    assert "Spread EV" in rendered
    assert "Spread EV: +9.1%" in copied
    assert "Best EV Market: Spread" in copied


def test_picks_template_tolerates_missing_optional_efficiency_probability():
    template = (ROOT / "templates" / "espn_predictions_template.html").read_text(
        encoding="utf-8"
    )

    assert "'prob': pred.efficiency_prob|default(none)" in template
