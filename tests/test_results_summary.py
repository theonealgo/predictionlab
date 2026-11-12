import json
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from jinja2 import ChoiceLoader, DictLoader

import all_sports_summary as summary
import paywall

ROOT = Path(__file__).resolve().parents[1]


def model_grid(record="47-34", label="Grinder2"):
    return (
        '<h2>Season Performance</h2><h3>Moneyline Accuracy by Model</h3>'
        '<div class="model-grid"><div class="model-card">'
        f'<div class="model-label">{label}</div><div>{record}</div></div></div>'
    )


def market_table(identifier="pl-spread-records", headings=None):
    headings = headings or ["Model", "Last night (2026-11-09)", "Past 7 days", "Past 30 days"]
    return (
        f'<div id="{identifier}"><table><tr>'
        + "".join(f"<th>{heading}</th>" for heading in headings)
        + '</tr><tr><td>Prediction Lab</td><td>1-0</td><td>3-2</td><td>12-8-2 (60%)</td></tr>'
        '<tr><td>XSharp</td><td>0-1</td><td>2-3</td><td>9-11</td></tr></table></div>'
    )


@pytest.mark.parametrize("label,key", [
    ("Grinder2", "glicko2"), ("Takedown", "trueskill"), ("Edge", "elo"),
    ("XSharp", "xgboost"), ("Sharp Consensus", "ensemble"),
    ("Market Consensus", "ensemble"), ("⚡ Efficiency <small>Season</small>", "efficiency"),
])
def test_model_cards_read_record_without_positional_text_guessing(label, key):
    board = summary.parse_board(model_grid("47-34", label), "CFL")
    assert board["ml"][key]["record"] == "47-34"
    assert board["ml"][key]["pct"] == 58.0


def test_individual_season_board_does_not_take_recent_record():
    def tally(title, record):
        return (
            f'<div class="daily-tally"><h2>{title}</h2><div class="daily-tally-card">'
            '<div class="daily-model">Efficiency <small>Season</small></div>'
            f'<div class="daily-rec">{record} · +20.0u</div></div></div>'
        )
    board = summary.parse_board(tally("Last 7 days", "8-2") +
                                tally("Season Performance (568 matches)", "308-260"), "TENNIS")
    assert board["ml"]["efficiency"]["record"] == "308-260"
    assert board["ml"]["efficiency"]["pct"] == 54.2


def test_market_scope_is_selected_by_header_not_last_available_number():
    board = summary.parse_board(market_table(), "NHL")
    cell = board["spread"]["pl"]
    assert cell["record"] == "12-8-2" and cell["pct"] == 60.0
    assert cell["n"] == 20 and cell["pushes"] == 2
    assert board["through"] == "2026-11-09"
    no_season = summary.parse_board(market_table(headings=["Model", "Night", "Week", "Season"]), "NHL")
    assert no_season["spread"] == {}


@pytest.mark.parametrize("identifier,market", [
    ("pl-spread-records", "spread"), ("pl-spread-vs-books", "spread"),
    ("pl-totals-records", "ou"), ("pl-totals-vs-books", "ou"),
])
def test_both_published_market_table_styles(identifier, market):
    assert summary.parse_board(market_table(identifier), "NCAAF")[market]["xgboost"]["record"] == "9-11"


def test_no_record_is_not_a_zero_game_claim():
    assert summary.record_cell("Not available")["status"] == "unavailable"
    assert summary.record_cell("0-0")["status"] == "empty"
    assert summary.record_cell("0-0-2")["pct"] is None
    assert summary.record_cell("0-0-2")["pushes"] == 2


def test_cfl_real_records_replace_missing_dashboard_cells_and_follow_file_changes(tmp_path):
    path = tmp_path / "served_CFL___free.html"
    path.write_text(model_grid("47-34"))
    cfl = next(row for row in summary.summary_rows(tmp_path) if row["sport"] == "CFL")
    assert cfl["ml"]["glicko2"]["record"] == "47-34"
    assert cfl["ml"]["trueskill"]["record"] == "N/A"
    path.write_text(model_grid("148-34"))
    cfl = next(row for row in summary.summary_rows(tmp_path) if row["sport"] == "CFL")
    assert cfl["ml"]["glicko2"]["record"] == "148-34"


def test_free_canonical_board_not_arbitrary_chart_or_historical_snapshot(tmp_path):
    (tmp_path / "served_NHL_.html").write_text(model_grid("99-1"))
    (tmp_path / "served_NHL___free.html").write_text(model_grid("47-34"))
    (tmp_path / "served_NHL_chart_.html").write_text(model_grid("1-99"))
    row = next(row for row in summary.summary_rows(tmp_path) if row["sport"] == "NHL")
    assert row["ml"]["glicko2"]["record"] == "47-34"


def test_unsupported_sports_do_not_get_invented_market_results(tmp_path):
    rows = {row["sport"]: row for row in summary.summary_rows(tmp_path)}
    assert rows["TENNIS"]["spread"]["pl"]["status"] == "not_applicable"
    assert rows["GOLF"]["ml"]["ensemble"]["pct"] is None


def test_final_published_board_wins_over_newer_intermediate_html(tmp_path):
    folder = tmp_path / "results_summary"
    folder.mkdir()
    (folder / "NBA.json").write_text(json.dumps({
        "version": summary.VERSION, "board": summary.parse_board(model_grid("68-43"), "NBA"),
    }))
    (tmp_path / "served_NBA___free.html").write_text(model_grid("66-44"))
    row = next(row for row in summary.summary_rows(tmp_path) if row["sport"] == "NBA")
    assert row["ml"]["glicko2"]["record"] == "68-43"


def test_invalid_derived_cache_falls_back_without_claiming_zero_games(tmp_path):
    folder = tmp_path / "results_summary"
    folder.mkdir()
    (folder / "CFL.json").write_text('{"version":1,"board":null}')
    (tmp_path / "served_CFL_.html").write_text(model_grid("47-34"))
    row = next(row for row in summary.summary_rows(tmp_path) if row["sport"] == "CFL")
    assert row["ml"]["glicko2"]["record"] == "47-34"


def test_response_observer_does_not_change_content_and_ignores_filtered_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(summary, "_PARSED", {})
    app = Flask(__name__)
    app.after_request(summary.remember_response)
    app.add_url_rule("/cfl-results", view_func=lambda: model_grid("47-34"))
    with app.test_client() as client:
        response = client.get("/cfl-results")
        assert response.get_data(as_text=True) == model_grid("47-34")
        path = tmp_path / "results_summary/CFL.json"
        before = path.read_bytes()
        client.get("/cfl-results?date=2026-11-09")
        assert path.read_bytes() == before
        assert json.loads(before)["board"]["ml"]["glicko2"]["record"] == "47-34"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "predictionlab.io"])
def test_results_and_summary_public_without_unlocking_paid_tools(tmp_path, monkeypatch, host):
    monkeypatch.setattr(summary, "_cache_dir", lambda: tmp_path)
    (tmp_path / "served_CFL_.html").write_text(model_grid("47-34"))
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.jinja_loader = ChoiceLoader([
        DictLoader({"premium_required.html": "<h1>Premium required</h1>"}),
        app.jinja_loader,
    ])
    app.secret_key = "isolated-results-test"
    login = LoginManager(app)
    login.user_loader(lambda key: None)
    paywall.install_access_policy(app)
    app.after_request(paywall.enforce_response)
    app.add_url_rule("/all-sports-results", "results", view_func=lambda: summary.render_fragment())
    app.add_url_rule("/api/web/results-summary", "summary", view_func=lambda: jsonify(html=summary.render_fragment()))
    app.add_url_rule("/performance", "performance", view_func=lambda: "PRIVATE")
    app.add_url_rule("/ladder-picks", "ladder", view_func=lambda: "PRIVATE")
    app.add_url_rule("/player-props-api/props", "props", view_func=lambda: jsonify(secret="PRIVATE"))
    with app.test_client() as client:
        for path in ("/all-sports-results", "/api/web/results-summary"):
            response = client.get(path, base_url=f"http://{host}")
            assert response.status_code == 200
            assert b"47-34" in response.data
            assert b"No games yet" not in response.data
        for path in ("/performance", "/ladder-picks", "/player-props-api/props"):
            response = client.get(path, base_url=f"http://{host}")
            assert response.status_code in (401, 403)
            assert b"PRIVATE" not in response.data


def test_main_page_no_longer_uses_the_independent_record_banner():
    source = (ROOT / "web/src/pages/HomePage.jsx").read_text()
    assert "<AllSportsResults />" in source
    assert "pl-right pl-mobile-hidden" in source
    assert source.index("<AllSportsResults />") > source.index("</main>")
    assert "/api/web/season" not in source
    assert 'aria-label="Show predictions or results"' in source


def test_legacy_all_sports_premium_rule_removed():
    import ast
    tree = ast.parse((ROOT / "NHL77FINAL.py").read_text())
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "_PREMIUM_ONLY_PAGES"
                              for t in node.targets))
    pages = ast.literal_eval(assignment.value)
    assert "/all-sports-results" not in pages
    assert "/performance" in pages and "/ladder-picks" in pages
