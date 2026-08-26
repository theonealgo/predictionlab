"""Soccer PL Expected Goals — soccer-only attach, other sports H2H untouched."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_pl_expected import clamp_goals, fit_until  # noqa: E402
from soccer_pl_xg import (  # noqa: E402
    apply_plxg_to_card,
    fill_soccer_plxg_fields,
    format_plxg_face,
    locked_config,
    predict_matchup,
)


def _g(home, away, hs, aws, day, league="Spanish LaLiga"):
    return {
        "game_id": f"{home}_{away}_{day}",
        "game_date": day,
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hs,
        "away_score": aws,
        "league": league,
    }


def test_locked_config_is_w15_avg_no_h2h():
    cfg = locked_config()
    assert cfg.window == 15
    assert cfg.half_life == 12.0
    assert cfg.formulation == "avg"
    assert cfg.h2h_weight == 0.0
    assert cfg.venue_split is True
    assert cfg.opp_adjust is True
    assert cfg.league_env is True


def test_face_format_home_away():
    from soccer_pl_expected import PLXGPrediction

    pred = PLXGPrediction(
        home=1.72,
        away=0.81,
        total=2.53,
        n_home=15,
        n_away=15,
        home_attack=1.5,
        home_defense=1.0,
        away_attack=1.0,
        away_defense=1.0,
        league_mu_home=1.4,
        league_mu_away=1.1,
        method="w15_hl12.0_avg",
    )
    assert format_plxg_face(pred) == "2.53 · Home 1.72 · Away 0.81"


def test_apply_sets_our_total_not_spread_or_h2h():
    from soccer_pl_expected import PLXGPrediction

    xg = PLXGPrediction(
        home=1.4,
        away=1.6,
        total=3.0,
        n_home=8,
        n_away=10,
        home_attack=1.4,
        home_defense=1.1,
        away_attack=1.5,
        away_defense=1.2,
        league_mu_home=1.4,
        league_mu_away=1.1,
        method="w15_hl12.0_avg",
    )
    card = {
        "home_team_id": "Málaga",
        "away_team_id": "Atlético Madrid",
        "our_spread": -0.5,
        "disp_pl_spread": -0.5,
        "h2h_last10_total": 8.0,
        "disp_pl_total": 2.5,
    }
    apply_plxg_to_card(card, xg)
    assert card["our_total"] == pytest.approx(3.0)
    assert card["our_spread"] == -0.5
    assert card["disp_pl_spread"] == -0.5
    assert card["h2h_last10_total"] == 8.0
    assert "Home" in card["soccer_pl_expected_face"]
    assert card["disp_pl_total"] == pytest.approx(3.0)


def test_malaga_atletico_is_current_form_not_stale_h2h():
    pred = predict_matchup(
        "Málaga", "Atlético Madrid", league="Spanish LaLiga", asof="2026-08-19"
    )
    assert pred is not None
    assert pred.n_home >= 1 and pred.n_away >= 1
    assert 1.2 <= pred.total <= 5.5
    # Stale 2013–18 H2H blowouts would sit well above current-form ~3.
    assert pred.total < 4.5
    assert pred.used_h2h is False


def test_fill_does_not_write_generic_h2h_columns():
    cards = [
        {
            "home_team_id": "Málaga",
            "away_team_id": "Atlético Madrid",
            "league": "Spanish LaLiga",
            "date": "2026-08-19",
            "h2h_last10_total": 7.2,
            "our_spread": 0.5,
        }
    ]
    fill_soccer_plxg_fields(cards)
    assert cards[0]["h2h_last10_total"] == 7.2
    assert cards[0]["our_spread"] == 0.5
    assert cards[0].get("soccer_pl_expected_total") is not None


def test_fit_until_no_leak():
    games = [
        _g("A", "B", 2, 0, "2026-01-01"),
        _g("A", "C", 4, 0, "2026-01-10"),
    ]
    st = fit_until(games, "2026-01-10")
    assert st.n_updated == 1


def test_clamp_soccer_scale():
    eh, ea = clamp_goals(8.0, 7.0)
    assert eh + ea <= 5.5 + 1e-9


def test_cold_start_uses_league_avg_never_none():
    pred = predict_matchup(
        "Güemes", "Sport Huancayo", league="Argentina Primera Nacional", asof="2026-08-19"
    )
    assert pred is not None
    assert 1.2 <= pred.total <= 5.5
    assert pred.home > 0 and pred.away > 0
    card = {"home_team_id": "Güemes", "away_team_id": "Sport Huancayo", "our_spread": -0.5}
    apply_plxg_to_card(card, pred)
    assert "Home" in card["soccer_pl_expected_face"]
    assert card["our_spread"] == -0.5
    assert card["soccer_pl_expected_missing_reason"] is None


def test_apply_none_xg_still_stamps_league_avg():
    card = {
        "home_team_id": "Malaysia",
        "away_team_id": "Palestine",
        "league": "International",
        "our_spread": 0.5,
    }
    apply_plxg_to_card(card, None)
    assert card["soccer_pl_expected_face"]
    assert "Home" in card["soccer_pl_expected_face"]
    assert card["our_spread"] == 0.5


def test_sanitize_proj_unescapes_beer_apostrophe():
    from soccer_ui_fixup import sanitize_soccer_proj_entities

    raw = (
        '<div data-pick-card data-home="Hapoel Be&amp;#39;er" '
        'data-pl-proj="Sabah FK 0.5 – Hapoel Be&amp;#39;er 2">'
    )
    out = sanitize_soccer_proj_entities(raw)
    assert "&#39;" not in out
    assert "39" not in out
    assert "Be'er" in out or "Beer" in out.replace("'", "")
    assert "0.5" in out and "2" in out


def test_apostrophe_entity_is_not_a_soccer_total():
    """Be&#39;er must not become 39 / 40.5."""
    raw = "Sabah 1.5 – Hapoel Be&#39;er Sheva 1.5"
    s = raw
    for _ in range(4):
        s = (
            s.replace("&amp;", "&")
            .replace("&apos;", "'")
            .replace("&#039;", "'")
            .replace("&#39;", "'")
        )
    assert "39" not in s
    nums = __import__("re").findall(r"(\d+(?:\.\d+)?)", s)
    assert nums[-2:] == ["1.5", "1.5"]
    assert float(nums[-2]) + float(nums[-1]) == pytest.approx(3.0)


def test_strip_soccer_h2h_labels():
    from soccer_pl_xg import strip_soccer_h2h_labels

    html = '<th class="ctr">H2H L10</th><span class="sf-label">H2H Last 10</span>'
    out = strip_soccer_h2h_labels(html)
    assert "H2H L10" not in out
    assert "H2H Last 10" not in out
    assert "PL-xG" in out
    assert "PL Expected Goals" in out


def test_strip_does_not_rewrite_data_plxg_or_books():
    from soccer_pl_xg import strip_soccer_h2h_labels

    html = (
        '<div class="game-card-stack" data-pick-card '
        'data-plxg="2.60 · Home 1.30 · Away 1.30" '
        'data-books-spread="Chacarita Juniors -0.5" '
        'data-books-total="1.5">'
        '<span class="ml-num dog">+340</span>'
        '<span class="ml-num fav">-130</span>'
        "</div>"
    )
    out = strip_soccer_h2h_labels(html)
    assert 'data-plxg="2.60 · Home 1.30 · Away 1.30"' in out
    assert "data-Predicted" not in out
    assert "Predicted Score" not in out
    assert 'data-books-spread="Chacarita Juniors -0.5"' in out
    assert 'data-books-total="1.5"' in out
    assert "+340" in out and "-130" in out


def test_enrich_preserves_books_ml_spread_total():
    from soccer_pl_xg import enrich_soccer_plxg_html
    from soccer_ui_fixup import apply_soccer_picks_fixups

    html = (
        '<html><body>'
        '<div class="game-card-stack" data-pick-card '
        'data-home="Chacarita Juniors" data-away="Güemes" '
        'data-home-full="Chacarita Juniors" data-away-full="Güemes" '
        'data-date="2026-08-19" data-league="Argentine Nacional B" '
        'data-plxg="2.60 · Home 1.30 · Away 1.30" '
        'data-books-spread="Chacarita Juniors -0.5" data-books-total="1.5">'
        '<div class="ml-line face-books-ml">'
        '<span class="ml-src books">Books</span>'
        '<span class="ml-num dog">+340</span></div>'
        '<div class="ml-line face-books-ml">'
        '<span class="ml-src books">Books</span>'
        '<span class="ml-num fav">-130</span></div>'
        '<div class="line-chip"><div class="line-chip-label">Books spread</div>'
        '<div class="line-chip-val">Chacarita Juniors -0.5</div></div>'
        '<div class="line-chip"><div class="line-chip-label">Books total</div>'
        '<div class="line-chip-val">O/U 1.5</div></div>'
        '<div class="odds-extras-footer">'
        '<div class="sf-item"><span class="sf-label">PL Expected Goals</span> '
        '<span class="sf-val">2.60 · Home 1.30 · Away 1.30</span></div>'
        "</div></div></body></html>"
    )
    out = apply_soccer_picks_fixups(enrich_soccer_plxg_html(html))
    assert "+340" in out and "-130" in out
    assert "Chacarita Juniors -0.5" in out
    assert "O/U 1.5" in out
    assert 'data-plxg="' in out
    assert "data-Predicted" not in out
    assert "Predicted Score" not in out
    assert "H2H Last 10" not in out
    assert "PL Expected Goals" in out
    assert "Home" in out and "Away" in out
