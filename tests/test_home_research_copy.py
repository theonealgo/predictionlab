from pathlib import Path

from react_shell import _page_meta
from site_words import rewrite_html

ROOT = Path(__file__).resolve().parents[1]
DISCLAIMER = (
    "PredictionLab is a sports analytics and quantitative data research platform. "
    "We do not provide sports betting services or take wagers."
)


def test_home_metadata_is_research_focused():
    title, description = _page_meta("/")
    assert "Quantitative sports forecasting model" in title
    assert "Statistical probability engine" in description
    assert "Straight-Up (SU)" in description
    assert not any(word in (title + description).lower() for word in ("picks", "odds", "handicapping"))


def test_disclaimer_is_exact_and_survives_display_wording():
    footer = (ROOT / "templates/partials/site_directory_footer.html").read_text()
    assert DISCLAIMER in footer
    assert DISCLAIMER in rewrite_html(footer)
    assert "1-800-GAMBLER" in footer and "21+" in footer


def test_llm_discovery_is_research_focused_and_describes_the_paywall():
    text = (ROOT / "llms.txt").read_text()
    assert DISCLAIMER in text
    assert "Quantitative sports forecasting model" in text
    assert "statistical probability engine" in text
    assert "require an active premium membership" in text
    assert "sports betting picks" not in text.lower()
    assert "/nhl-predictions" in text


def test_home_season_panel_no_longer_renders_the_units_field():
    source = (ROOT / "web/src/pages/HomePage.jsx").read_text()
    summary = (ROOT / "templates/partials/all_sports_summary.html").read_text()
    assert "<AllSportsResults />" in source
    assert "Season record · Straight up" in summary
    assert "{item.units}" not in source
    assert "Accuracy = wins" in summary
    assert "/api/web/season" not in source


def test_other_sport_metadata_is_not_rebranded_by_the_home_change():
    title, description = _page_meta("/nhl-predictions")
    assert title == "NHL Predictions Today: AI Win Probabilities | Prediction Lab"
    assert "Quantitative sports forecasting model" not in title
