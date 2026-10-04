"""Blank / stale MLB picks must FAIL chrome + ship checkers (not soft-pass)."""

from __future__ import annotations

from qa.chrome_checker import (
    _et_today_str,
    _has_pick_cards,
    _picks_no_predictions_banner,
)
from qa.ship_parity import ShipParityAuditor


def _ship() -> ShipParityAuditor:
    return ShipParityAuditor.__new__(ShipParityAuditor)


def test_has_pick_cards_ignores_script_only_markup():
    html = """
    <html><body>
      <div class="no-data">No predictions available for MLB</div>
      <script>
        const tpl = '<div data-pick-card class="pick-card">H2H Last 10</div>';
      </script>
    </body></html>
    """
    assert _has_pick_cards(html) is False
    assert _ship()._has_pick_cards(html) is False


def test_no_predictions_banner_detects_mlb_empty():
    html = '<div class="no-data">No predictions available for MLB</div>'
    assert _picks_no_predictions_banner(html, "MLB") is True
    assert _ship()._picks_no_predictions_banner(html, "MLB") is True
    assert _picks_no_predictions_banner(html, "NFL") is False


def test_stale_slate_has_cards_but_not_today():
    today = _et_today_str()
    html = """
    <div class="date-section" id="date-2026-09-12">
      <div data-pick-card class="pick-card">CWS @ CLE</div>
    </div>
    """
    assert _has_pick_cards(html) is True
    assert f'id="date-{today}"' not in html


def test_weekly_sport_without_today_is_not_treated_as_blank():
    """NFL/NCAAF/CFL often have cards but no ET-today section — not a blank miss."""
    html = """
    <div class="date-section" id="date-2026-09-18">
      <div data-pick-card class="pick-card">KC @ BUF</div>
    </div>
    """
    assert _has_pick_cards(html) is True
    assert _picks_no_predictions_banner(html, "NFL") is False
