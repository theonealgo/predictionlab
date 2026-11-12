import copy
import gzip
import json
from pathlib import Path

import pytest
from flask import Flask, jsonify, make_response, request
from flask_login import LoginManager, current_user
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

import auth_system
import paywall

ROOT = Path(__file__).resolve().parents[1]
MEMBER_CARD = """<div class="game-card-stack" data-pick-card data-game-id="NBA_test"
 data-away="Away" data-home="Home" data-date="2026-11-10" data-league="NBA"
 data-pick="Home" data-conf="60.1" data-books-spread="PRIVATE_LINE"
 data-m-grinder2="PRIVATE_MODEL" data-pl-proj="PRIVATE_PROJECTION">
<header class="pick-card-header"><span class="league-badge">NBA</span><span class="game-time">8:00 PM ET</span></header>
<div class="matchup-row">
<div class="team-slot"><img class="team-logo" src="/static/pl-logo.svg"><div class="team-name">Away</div><div class="win-pct">39.9<span>%</span></div>
<div class="face-books-ml">PRIVATE_MARKET_PRICE</div><div class="face-pl-ml">PRIVATE_MODEL_PRICE</div></div>
<div class="team-slot"><div class="team-name">Home</div><div class="win-pct">60.1<span>%</span></div></div>
</div><div class="card-details"><div class="pc-box">PRIVATE_MODEL</div><span class="proj-val">PRIVATE_PROJECTION</span></div></div>"""
PAYLOAD = {
    "cards": [{"html": MEMBER_CARD, "home": "Home", "away": "Away", "date": "2026-11-10"}],
    "total": 1, "css": "PRIVATE_CSS_PAYLOAD", "shareImage": "PRIVATE_SHARE_IMAGE",
    "extra": "PRIVATE_EXTRA",
}
FEATURES = [
    "/performance", "/performance/audit.csv", "/api/performance-data", "/ladder",
    "/ladder-picks", "/ladder-picks/status.json", "/edge-performance",
    "/our-model-vs-sportsbooks", "/player-props", "/player-props-api/leagues",
    "/player-props-api/props", "/player-props-api/results",
    "/player-props-api/streaks", "/player-props-api/diagnostics",
    "/nfl/api/picks", "/sport/NFL/spreads", "/sport/NFL/ats",
]


class FixtureAdmin(auth_system.User):
    @property
    def is_admin(self):
        return True


@pytest.fixture
def app():
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.config.update(SECRET_KEY="isolated-fixture-key", TESTING=True)
    login = LoginManager(app)
    fixtures = {
        "free": auth_system.User("free", "free@example.invalid"),
        "expired": auth_system.User("expired", "expired@example.invalid",
                                    is_premium=True, premium_expires="2000-01-01"),
        "paid": auth_system.User("paid", "paid@example.invalid",
                                 is_premium=True, premium_expires="2099-01-01"),
        "admin": FixtureAdmin("admin", "fixture-admin@example.invalid"),
    }
    login.user_loader(lambda key: fixtures.get(key))
    app.jinja_loader = ChoiceLoader([
        DictLoader({
            "premium_required.html": "<h1>Premium required</h1>{% if not logged_in %}<a href='/login'>Log in</a>{% endif %}",
            "free_predictions.html": "<html><head></head><body>{% for card in cards %}{{ card }}{% endfor %}</body></html>",
        }),
        FileSystemLoader(ROOT / "templates"),
    ])
    paywall.install_access_policy(app)
    app.after_request(paywall.enforce_response)

    for i, path in enumerate(FEATURES):
        app.add_url_rule(path, f"feature_{i}", lambda: "PRIVATE_FEATURE_DATA")

    @app.route("/api/web/me")
    def me():
        return jsonify(loggedIn=bool(current_user.is_authenticated),
                       premium=bool(auth_system.is_premium_user()))

    @app.route("/api/web/sports/nba-picks/cards")
    def cards():
        response = jsonify(copy.deepcopy(PAYLOAD))
        if request.headers.get("Accept-Encoding") == "gzip":
            response.set_data(gzip.compress(response.get_data()))
            response.headers["Content-Encoding"] = "gzip"
            response.vary.add("Accept-Encoding")
        response.set_cookie("fixture", "preserved")
        return response

    @app.route("/nba-picks")
    def predictions():
        return "<html><body>" + MEMBER_CARD + "<script>const hidden='PRIVATE_SCRIPT';</script></body></html>"

    @app.route("/nba-results")
    def results():
        final = MEMBER_CARD.replace("8:00 PM ET", "FINAL").replace("NBA_test", "NBA_final")
        return "<html><body><h1>Results</h1>" + final + MEMBER_CARD + "</body></html>"

    @app.route("/api/web/golf")
    def golf():
        return jsonify(events=[{"id": "7", "name": "Tournament", "secret": "PRIVATE_EVENT"}],
                       selected="7", picks=[{"note": "PRIVATE_GOLF_PROJECTION"}],
                       field=[{"rank": "1", "player": "Golfer", "photo": "", "win": "12%",
                               "top10": "PRIVATE_TOP10", "top20": "PRIVATE_TOP20"}])

    return app


def client_for(app, role, host):
    client = app.test_client()
    base = "http://" + host
    if role != "guest":
        with client.session_transaction(base_url=base) as session:
            session["_user_id"] = role
            session["_fresh"] = True
    return client, base


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "predictionlab.io"])
@pytest.mark.parametrize("role", ["guest", "free", "expired", "paid", "admin"])
def test_no_hostname_entitlement_and_feature_routes(app, host, role):
    client, base = client_for(app, role, host)
    paid = role in ("paid", "admin")
    assert client.get("/api/web/me", base_url=base).json == {
        "loggedIn": role != "guest", "premium": paid,
    }
    for path in FEATURES:
        response = client.get(path, base_url=base)
        if paid:
            assert response.status_code == 200, (role, host, path)
            assert b"PRIVATE_FEATURE_DATA" in response.data
        else:
            assert response.status_code in (401, 403), (role, host, path)
            assert b"PRIVATE_FEATURE_DATA" not in response.data
            if role != "guest":
                assert b"Log in" not in response.data


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "predictionlab.io"])
@pytest.mark.parametrize("role", ["guest", "free", "expired", "paid", "admin"])
@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_cached_member_payload_never_leaks_to_nonmember(app, host, role, encoding):
    client, base = client_for(app, role, host)
    response = client.get("/api/web/sports/nba-picks/cards", base_url=base,
                          headers={"Accept-Encoding": encoding})
    body = gzip.decompress(response.data) if response.headers.get("Content-Encoding") == "gzip" else response.data
    data = json.loads(body)
    assert response.status_code == 200
    assert "private" in response.headers["Cache-Control"] and "no-store" in response.headers["Cache-Control"]
    assert "Cookie" in response.headers["Vary"]
    assert response.headers.getlist("Set-Cookie")
    if role in ("paid", "admin"):
        assert data == PAYLOAD
    else:
        assert b"PRIVATE_" not in body
        assert data["shareImage"] == ""
        assert "39.9" in data["cards"][0]["html"] and "60.1" in data["cards"][0]["html"]
        assert "face-books-ml" not in data["cards"][0]["html"]
        assert "data-books-spread" not in data["cards"][0]["html"]
        assert "pc-box" not in data["cards"][0]["html"]
        assert data["cards"][0]["home"] == "Home"


@pytest.mark.parametrize("role", ["guest", "free", "expired"])
def test_direct_html_has_no_private_scripts_and_headers_cannot_bypass(app, role):
    client, base = client_for(app, role, "127.0.0.1")
    response = client.get("/nba-picks", base_url=base, headers={
        "X-PL-Results-Rebuild": "1", "X-PL-Premium": "true", "X-PL-Internal": "1",
    })
    assert response.status_code == 200
    assert b"PRIVATE_" not in response.data
    assert b"39.9" in response.data and b"60.1" in response.data


def test_completed_results_free_but_upcoming_prices_not_exposed(app):
    client, base = client_for(app, "guest", "localhost")
    response = client.get("/nba-results", base_url=base)
    assert response.status_code == 200
    assert b"FINAL" in response.data
    # Only the completed card retains its historical fields.
    assert response.data.count(b"PRIVATE_MARKET_PRICE") == 1
    assert response.data.count(b"PRIVATE_PROJECTION") == 2  # attribute plus historical reading value
    assert response.data.count(b"pl-free-card") == 1


def test_golf_keeps_primary_win_forecasts_only(app):
    client, base = client_for(app, "guest", "localhost")
    response = client.get("/api/web/golf", base_url=base)
    assert response.status_code == 200 and response.json["locked"]
    assert b"PRIVATE_" not in response.data
    assert response.json["field"][0]["win"] == "12%"
    assert "top10" not in response.json["field"][0]


def test_free_card_retains_actual_final_scores_but_not_prices():
    source = MEMBER_CARD.replace(
        '<div class="team-name">Away</div>', '<div class="team-name">Away</div><div class="final-score">101</div>'
    ).replace('<div class="team-name">Home</div>', '<div class="team-name">Home</div><div class="final-score">110</div>')
    safe = paywall.free_card(source)["html"]
    assert '<div class="final-score">101</div>' in safe
    assert '<div class="final-score">110</div>' in safe
    assert 'FINAL' in safe and 'PRIVATE_' not in safe


def test_empty_future_score_placeholders_do_not_expose_a_card():
    source = MEMBER_CARD.replace('<div class="team-name">Away</div>', '<div class="team-name">Away</div><div class="final-score"></div>')
    source = source.replace('<div class="team-name">Home</div>', '<div class="team-name">Home</div><div class="final-score"></div>')
    assert paywall._completed_card(source, {}) is False


@pytest.mark.parametrize("path", ["/nba-picks?market=spread", "/nba-picks?view=chart",
                                 "/api/web/sports/nba-picks/cards?market=totals"])
def test_private_prediction_views_are_gated(app, path):
    client, base = client_for(app, "guest", "localhost")
    response = client.get(path, base_url=base)
    assert response.status_code in (401, 403)
    assert b"PRIVATE_" not in response.data


def test_internal_source_context_is_not_membership(app):
    client, base = client_for(app, "guest", "localhost")
    with paywall.page_source():
        assert client.get("/performance", base_url=base).status_code == 403
        assert client.get("/api/web/me", base_url=base).json["premium"] is False
    assert b"PRIVATE_" not in client.get("/nba-picks", base_url=base).data


@pytest.mark.parametrize("expiry,active", [
    ("2000-01-01T00:00:00Z", False),
    ("2000-01-01T00:00:00+00:00", False),
    ("2000-01-01T00:00:00-05:00", False),
    ("2099-01-01T00:00:00Z", True),
    ("2099-01-01T00:00:00-05:00", True),
    ("not-a-valid-expiry", False),
    (None, True),
])
def test_expiry_does_not_grant_access_on_timezone_or_parse_error(expiry, active):
    user = auth_system.User("fixture", "expiry-fixture@example.invalid",
                            is_premium=True, premium_expires=expiry)
    assert user.premium_active is active
