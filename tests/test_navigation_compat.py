from flask import Flask, request
import pytest
from werkzeug.datastructures import MultiDict

from navigation_compat import canonical_result_link, install


@pytest.mark.parametrize("market", ["spread", "totals", "moneyline"])
@pytest.mark.parametrize("sport", ["mlb", "ncaaw", "ncaab"])
def test_legacy_link_redirects_once_to_supported_chart(market, sport):
    app = Flask(__name__)
    install(app)

    @app.route(f"/{sport}-results", methods=["GET", "POST"])
    def results():
        assert request.args.get("view") == "chart"
        return "Existing chart"

    client = app.test_client()
    response = client.get(f"/{sport}-results?market={market}")
    assert response.status_code == 301
    assert response.headers["Location"] == f"/{sport}-results?market={market}&view=chart"
    assert client.get(response.headers["Location"]).status_code == 200


def test_explicit_views_and_other_sports_unchanged():
    assert canonical_result_link("/mlb-results", MultiDict({"view": "cards", "market": "spread"})) is None
    assert canonical_result_link("/mlb-results", MultiDict({"view": "chart", "market": "totals"})) is None
    assert canonical_result_link("/nba-results", MultiDict({"market": "totals"})) is None
    assert canonical_result_link("/mlb-results", MultiDict({"market": "invalid"})) is None
    assert canonical_result_link("/mlb-results", MultiDict({"market": "totals"}), method="POST") is None


def test_existing_filters_are_preserved_and_encoded():
    args = MultiDict([("market", "spread"), ("date", "2026-10-30"), ("team", "A & B")])
    target = canonical_result_link("/mlb-results", args)
    assert target == "/mlb-results?market=spread&date=2026-10-30&team=A+%26+B&view=chart"
