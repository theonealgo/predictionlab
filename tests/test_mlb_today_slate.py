"""MLB picks must not serve a yesterday-only slate when ET today exists."""

from mlb_ui_fixup import (
    mlb_et_today_str,
    mlb_html_has_et_today,
    mlb_slate_has_et_today,
)


def test_yesterday_only_slate_is_stale():
    today = mlb_et_today_str()
    preds = [
        {"game_date": "2026-09-16", "home_team_id": "Cleveland Guardians"},
        {"game_date": "2026-09-16", "home_team_id": "Minnesota Twins"},
    ]
    if any(p["game_date"] == today for p in preds):
        preds = [{"game_date": "2020-01-01", "home_team_id": "X"}]
    assert mlb_slate_has_et_today(preds) is False


def test_today_in_slate_is_fresh():
    today = mlb_et_today_str()
    preds = [
        {"game_date": "2026-09-16", "home_team_id": "Cleveland Guardians"},
        {"game_date": today, "home_team_id": "Pittsburgh Pirates"},
    ]
    assert mlb_slate_has_et_today(preds) is True


def test_html_without_today_section_is_stale():
    today = mlb_et_today_str()
    html = """
    <div class="date-section" id="date-2026-09-16">
      <div class="game-card-stack"></div>
    </div>
    """
    if f'id="date-{today}"' in html:
        return
    assert mlb_html_has_et_today(html) is False


def test_html_with_today_section_is_fresh():
    today = mlb_et_today_str()
    html = f'<div class="date-section" id="date-{today}"></div>'
    assert mlb_html_has_et_today(html) is True
