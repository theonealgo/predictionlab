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


def test_mlb_pick_conf_css_matches_nfl_3up():
    from mlb_ui_fixup import ensure_mlb_pick_conf_no_scroll

    html = "<html><head></head><body class=\"sport-mlb\"></body></html>"
    out = ensure_mlb_pick_conf_no_scroll(html)
    assert "minmax(520px" not in out
    assert "repeat(3,minmax(0,1fr))" in out
    wide = (
        '<html><head><style id="mlb-pick-conf-no-scroll">'
        "grid-template-columns:repeat(auto-fit,minmax(520px,1fr))!important;"
        "</style></head><body></body></html>"
    )
    fixed = ensure_mlb_pick_conf_no_scroll(wide)
    assert "minmax(520px" not in fixed
    assert fixed.count('id="mlb-pick-conf-no-scroll"') == 1


def test_shared_picks_grid_matches_nfl_3up():
    from team_results_charts import inject_shared_picks_card_grid_css

    html = "<html><head></head><body class=\"sport-nhl\"></body></html>"
    out = inject_shared_picks_card_grid_css(html)
    assert "pl-shared-picks-card-grid" in out
    assert "repeat(3,minmax(0,1fr))" in out
    assert "minmax(520px" not in out
    assert inject_shared_picks_card_grid_css(out).count("pl-shared-picks-card-grid") == 1

