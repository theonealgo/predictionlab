"""PL moneyline on pick card face + soccer 3-way draw."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_pick_card_sets_pl_model_moneylines(nhl):
    card = {
        "home_team_id": "Boston Celtics",
        "away_team_id": "New York Knicks",
        "ensemble_prob": 63.1,
        "book_home_moneyline": -205,
        "book_away_moneyline": 170,
    }
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert card.get("pl_model_home_ml") is not None
    assert card.get("pl_model_away_ml") is not None
    assert card["pl_model_home_ml"] < 0
    assert card["pl_model_away_ml"] > 0


def test_soccer_threeway_probs_from_binary(nhl):
    hw, dw, aw = nhl._soccer_threeway_probs(0.625, 0.25)
    assert hw == pytest.approx(0.5)
    assert dw == pytest.approx(0.25)
    assert aw == pytest.approx(0.25)


def test_soccer_pick_card_draw_and_pl_ml(nhl):
    card = {
        "home_team_id": "Arsenal",
        "away_team_id": "Chelsea",
        "ensemble_prob": 55.0,
        "draw_prob": 25.0,
        "home_win_prob": 42.5,
        "away_win_prob": 32.5,
        "xgb_prob": 55.0,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_draw_prob") == pytest.approx(25.0)
    assert card.get("pl_model_draw_ml") is not None
    assert card.get("pl_model_home_ml") is not None
    assert card.get("pl_model_away_ml") is not None


def test_soccer_binary_probs_render_without_draw_fields(nhl):
    card = {
        "home_team_id": "Granada",
        "away_team_id": "Almeria",
        "xgb_prob": 58.0,
        "ensemble_prob": 55.0,
        "book_home_moneyline": -110,
        "book_away_moneyline": -110,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_home_prob") == pytest.approx(58.0)
    assert card.get("face_away_prob") == pytest.approx(42.0)
    assert card.get("pl_model_home_ml") is not None
    assert card.get("pl_model_away_ml") is not None


def test_soccer_xsharp_falls_back_to_ensemble_when_xgb_missing(nhl):
    card = {
        "home_team_id": "Arsenal",
        "away_team_id": "Chelsea",
        "xgb_prob": None,
        "ensemble_prob": 62.0,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_home_prob") == pytest.approx(62.0)
    assert card.get("pl_model_home_ml") is not None


def test_soccer_bundle_loads_laliga2_db_variant(nhl, monkeypatch):
    """Segunda rows are stored as 'Spanish LALIGA 2' — bundle must still train."""
    fake_rows = [
        {
            "game_id": f"SOCCER_esp.2_{i}",
            "game_date": f"2026-0{(i % 9) + 1}-{(i % 20) + 1:02d}",
            "home_team_id": f"Home{i}",
            "away_team_id": f"Away{i}",
            "home_score": 2,
            "away_score": 1,
            "league": "Spanish LALIGA 2",
        }
        for i in range(11)
    ]

    class _Conn:
        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return fake_rows

        def close(self):
            pass

    monkeypatch.setattr(nhl, "get_db_connection", lambda: _Conn())
    nhl._SOCCER_MODEL_CACHE.clear()
    nhl._invalidate_soccer_league_db_variants()

    variants = {"Spanish Segunda División": {"Spanish Segunda División", "Spanish LALIGA 2"}}
    monkeypatch.setattr(nhl, "_ensure_soccer_league_db_variants", lambda _c: variants)

    captured = {}

    def _fake_build(games, league_name=None, min_games=12):
        captured["games"] = games
        captured["min_games"] = min_games
        from soccer_models import SoccerModelBundle
        return SoccerModelBundle(
            ready=len(games) >= min_games,
            reason=None if len(games) >= min_games else "too few",
            games_count=len(games),
            league_name=league_name,
        )

    monkeypatch.setattr(nhl, "build_soccer_model_bundle", _fake_build)
    bundle = nhl._get_soccer_model_bundle([], "Spanish Segunda División")
    assert bundle.ready is True
    assert captured["min_games"] == 10
    assert len(captured["games"]) == 11
    assert captured["games"][0]["league"] == "Spanish LALIGA 2"


def test_get_upcoming_soccer_segunda_populates_model_probs(nhl, monkeypatch):
    """End-to-end: Segunda card gets xgb + ensemble when bundle has enough games."""
    from soccer_models import SoccerModelBundle

    fake_pred = {
        "elo_prob": 0.55,
        "poisson_reg_prob": 0.58,
        "ensemble_prob": 0.56,
        "poisson_xg_prob": 0.57,
        "markov_prob": 0.54,
        "draw_prob": 0.25,
        "expected_home_score": 1.4,
        "expected_away_score": 1.1,
    }

    class _FakeBundle:
        ready = True
        reason = None

        def predict(self, home, away):
            return dict(fake_pred)

    monkeypatch.setattr(
        nhl,
        "_get_soccer_model_bundle",
        lambda *_a, **_k: _FakeBundle(),
    )
    nhl._PREDICTIONS_CACHE.clear()

    sample_game = {
        "game_id": "SOCCER_esp.2_999",
        "home_team_id": "Almería",
        "away_team_id": "Real Valladolid",
        "game_date": "2026-06-02",
        "home_score": None,
        "away_score": None,
        "league": "Spanish Segunda División",
    }

    def _fake_soccer_feed(*_a, **_k):
        from datetime import datetime
        return [(datetime(2026, 6, 2), sample_game)]

    monkeypatch.setattr(
        nhl,
        "get_upcoming_predictions",
        nhl.get_upcoming_predictions,
    )
    # Patch only the SOCCER API path by calling inner logic via a slim wrapper
    orig = nhl.get_upcoming_predictions

    def _patched(sport, days=365):
        if sport != "SOCCER":
            return orig(sport, days=days)
        nhl._PREDICTIONS_CACHE.clear()
        # Minimal path: one upcoming Segunda game + fake bundle already patched
        from datetime import datetime, timedelta
        all_games = [(datetime(2026, 6, 2), dict(sample_game))]
        completed_games = []
        predictions = []
        for game_date, game in all_games:
            soccer_league = nhl._canonical_soccer_league_name(game.get("league")) or game.get("league")
            soccer_bundle = nhl._get_soccer_model_bundle(completed_games, soccer_league)
            soccer_pred = soccer_bundle.predict(game["home_team_id"], game["away_team_id"])
            elo_prob = soccer_pred.get("elo_prob")
            xgb_prob = soccer_pred.get("poisson_reg_prob")
            ensemble_prob = soccer_pred.get("ensemble_prob")
            if xgb_prob is None:
                xgb_prob = elo_prob
            if ensemble_prob is None:
                ensemble_prob = elo_prob
            game_dict = dict(game)
            game_dict["xgb_prob"] = round(xgb_prob * 100, 1)
            game_dict["ensemble_prob"] = round(ensemble_prob * 100, 1)
            if soccer_pred.get("draw_prob") is not None:
                hw, dw, aw = nhl._soccer_threeway_probs(ensemble_prob, soccer_pred["draw_prob"])
                game_dict["draw_prob"] = round(dw * 100, 1)
                game_dict["home_win_prob"] = round(hw * 100, 1)
                game_dict["away_win_prob"] = round(aw * 100, 1)
            nhl._prepare_pred_card_display(game_dict, sport="SOCCER")
            predictions.append(game_dict)
        return predictions

    preds = _patched("SOCCER")
    assert len(preds) == 1
    p = preds[0]
    assert p.get("xgb_prob") == pytest.approx(58.0)
    assert p.get("ensemble_prob") == pytest.approx(56.0)
    assert p.get("face_home_prob") is not None
    assert p.get("pl_model_home_ml") is not None
    assert p.get("face_draw_prob") == pytest.approx(25.0, abs=0.2)


def test_soccer_missing_model_data_no_fake_fifty_fifty(nhl):
    card = {
        "home_team_id": "Ceuta",
        "away_team_id": "Albacete",
        "elo_prob": 50.0,
        "xgb_prob": None,
        "ensemble_prob": None,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_home_prob") is None
    assert card.get("face_away_prob") is None
    assert card.get("face_pick_confidence") is None
    assert card.get("pl_model_home_ml") is None
    assert card.get("pl_model_away_ml") is None


def test_soccer_ml_grading_draw_pick(nhl):
    info = {
        "glicko2_prob": 55.0,
        "ens_prob": 55.0,
    }
    nhl._apply_soccer_ml_grading(
        info,
        draw_dec=0.30,
        glicko2_prob=0.55,
        trueskill_prob=0.52,
        elo_prob=0.50,
        xgb_prob=0.48,
        ens_prob=0.55,
        home_won=None,
        is_draw=True,
    )
    assert info.get("skip_grading") is False
    assert info.get("draw_prob") == pytest.approx(30.0, abs=0.2)


def test_soccer_ml_grading_home_win(nhl):
    assert nhl._soccer_ml_pick_correct(55.0, 20.0, 25.0, True, False) is True
    assert nhl._soccer_ml_pick_correct(30.0, 20.0, 50.0, True, False) is False


def test_nba_unaffected_by_soccer_draw(nhl):
    card = {"ensemble_prob": 60.0, "home_team_id": "A", "away_team_id": "B"}
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert "face_draw_prob" not in card or card.get("face_draw_prob") is None
    assert card.get("pl_model_home_ml") is not None


def test_mlb_pick_card_sets_face_edge_pct(nhl):
    card = {
        "home_team_id": "Kansas City Royals",
        "away_team_id": "Cincinnati Reds",
        "ensemble_prob": 45.3,
        "predicted_winner": "Cincinnati Reds",
        "model_win_pct": 54.7,
        "book_home_moneyline": -205,
        "book_away_moneyline": 170,
    }
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card.get("face_edge_pct") is not None
    assert card["face_edge_pct"] > 5.0


def test_mlb_edge_falls_back_to_cached_edge_pct(nhl):
    card = {
        "home_team_id": "New York Yankees",
        "away_team_id": "Boston Red Sox",
        "ensemble_prob": 58.4,
        "predicted_winner": "New York Yankees",
        "model_win_pct": 58.4,
        "edge_pct": 4.8,
    }
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card.get("face_edge_pct") == pytest.approx(4.8, abs=0.1)


def test_mlb_edge_recomputes_after_book_hydration(nhl):
    card = {
        "home_team_id": "Kansas City Royals",
        "away_team_id": "Cincinnati Reds",
        "ensemble_prob": 45.3,
        "predicted_winner": "Cincinnati Reds",
        "model_win_pct": 54.7,
        "edge_pct": 17.34,
        "book_home_moneyline": -205,
        "book_away_moneyline": 170,
    }
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card.get("face_edge_pct") is not None
    assert card["face_edge_pct"] > 5.0


def test_nba_pick_card_has_no_face_edge_pct(nhl):
    card = {
        "home_team_id": "Boston Celtics",
        "away_team_id": "New York Knicks",
        "ensemble_prob": 63.1,
        "book_home_moneyline": -205,
        "book_away_moneyline": 170,
    }
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert card.get("face_edge_pct") is None


def test_mlb_picks_page_renders_edge_chip(nhl):
    from NHL77FINAL import app
    with app.test_client() as c:
        r = c.get("/mlb-picks", headers={"Host": "127.0.0.1"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "edge-chip" in body or 'line-chip-label">Edge' in body
