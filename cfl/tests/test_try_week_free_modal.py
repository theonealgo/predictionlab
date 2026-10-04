"""Try a Week FREE CTAs keep existing checkout URLs and expose the PLAY26 modal."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LIVE_CTA_FILES = [
    ROOT / "templates/base.html",
    ROOT / "templates/includes/picks_nav_chrome.html",
    ROOT / "templates/espn_predictions_template.html",
    ROOT / "templates/homepage_preview.html",
    ROOT / "templates/includes/stripe_weekly_buy_button.html",
    ROOT / "templates/underdogs_layout.html",
    ROOT / "auth_system.py",
]

TEMPLATE_CHECKOUT = "/checkout/weekly"
LEGACY_STRIPE_WEEKLY = "https://buy.stripe.com/14A6oI4Ra66ReWLczTao802"


def test_live_cta_copy_is_try_a_week_free():
    for path in LIVE_CTA_FILES:
        text = path.read_text(encoding="utf-8")
        assert "Try a Week FREE" in text, path.name
        assert "pl-try-free" not in text
        assert "WeekFREE" not in text
        assert ">Try a Week</a>" not in text
        assert "Try This Week" not in text
        assert "Try a Week — $4.99" not in text


def test_template_ctas_keep_checkout_weekly():
    for path in LIVE_CTA_FILES:
        text = path.read_text(encoding="utf-8")
        assert TEMPLATE_CHECKOUT in text, path.name
        assert "js-try-week-cta" in text
        if path.name != "auth_system.py":
            assert LEGACY_STRIPE_WEEKLY not in text, path.name


def test_nhl77_base_template_keeps_existing_weekly_href():
    text = (ROOT / "NHL77FINAL.py").read_text(encoding="utf-8")
    start = text.index("BASE_TEMPLATE = ")
    end = text.index("# Static HTML footers", start)
    chunk = text[start:end]
    assert 'class="tv-premium-cta tv-premium-cta-weekly js-try-week-cta"' in chunk
    assert 'class="join-premium-btn join-premium-btn-weekly js-try-week-cta"' in chunk
    assert LEGACY_STRIPE_WEEKLY in chunk
    assert "Try a Week FREE" in chunk
    assert "pl-try-free" not in chunk
    assert '{% include "includes/try_week_free_modal.html" %}' in chunk


def test_modal_markup_and_play26():
    modal = (ROOT / "templates/includes/try_week_free_modal.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/try-week-free-modal.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/try-week-free-modal.css").read_text(encoding="utf-8")
    assert 'id="plTryWeekModal"' in modal
    assert 'role="dialog"' in modal
    assert "Try PredictionLab FREE for 7 Days" in modal
    assert ">PLAY26<" in modal
    assert modal.count("PLAY26") == 1
    assert "Copy Code" in modal
    assert "Continue to Checkout" in modal
    assert f'href="{TEMPLATE_CHECKOUT}"' in modal
    assert "TheOddsAPI" not in modal
    assert "sandbox" not in modal.lower()
    assert 'var CODE = "PLAY26"' in js
    assert "focusable" in js
    assert "Escape" in js
    assert "clipboard" in js
    assert ".pl-try-week-dialog" in css


def test_no_stripe_backend_edits_in_this_change():
    """Plans CTA copy lives in auth_system HTML; checkout routes stay /checkout/*."""
    src = (ROOT / "auth_system.py").read_text(encoding="utf-8")
    start = src.index("@auth_bp.route('/plans')")
    chunk = src[start : start + 12000]
    assert 'href="/checkout/weekly"' in chunk
    assert 'href="/checkout/monthly"' in chunk
    assert 'href="/checkout/yearly"' in chunk
    assert LEGACY_STRIPE_WEEKLY not in chunk
