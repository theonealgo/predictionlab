"""Owner-reported gaps the checker must fail on."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qa"))

from datetime import date  # noqa: E402

from chart_shape import (  # noqa: E402
    blank_moneyline_model_issues,
    card_blank_market_line_issues,
    card_missing_model_value_issues,
    mlb_xsharp_totals_issues,
    nba_last_season_gap_issues,
    nfl_chart_api_issues,
    nfl_chart_not_mlb_issues,
    nfl_chart_same_as_cards_issues,
    nfl_chart_window_tally_issues,
    nfl_missing_efficiency_issues,
    empty_last_night_spread_tally_issues,
    nfl_spread_result_card_issues,
    nfl_stale_season_perf_issues,
    nfl_stale_season_week_issues,
    nhl_chart_view_missing_issues,
    six_model_chart_issues,
    team_chart_template_issues,
    team_picks_template_issues,
    team_results_template_issues,
    tennis_chart_same_as_cards_issues,
)


def test_mlb_missing_xsharp_totals_fails():
    cards = "<html>Consensus Based Betting Records</html>"
    chart = "<html>Best Performing Model Last Night</html>"
    issues = mlb_xsharp_totals_issues(cards, chart)
    assert issues
    assert any("XSharp — Totals" in i for i in issues)


def test_mlb_xsharp_totals_present_passes():
    cards = "<html>Prediction Lab · XSharp — Totals XSharp</html>"
    chart = "<html>Prediction Lab · XSharp — Totals XSharp</html>"
    assert not mlb_xsharp_totals_issues(cards, chart)


def test_tennis_identical_pages_fail():
    html = "<html>cards only</html>"
    assert tennis_chart_same_as_cards_issues(html, html)


def test_nhl_chart_missing_fails():
    assert nhl_chart_view_missing_issues("<html>cards</html>", "<html>cards</html>")


def test_nba_empty_season_fails():
    html = "<h1>NBA Results</h1>" + (" 0-0 " * 10)
    assert nba_last_season_gap_issues(html)


def test_ncaaf_missing_model_box_fails():
    card = (
        '<div data-pick-card><div class="pick-conf-grid">'
        '<div class="pc-box"><div class="pc-name">Edge</div>'
        '<div class="pc-val">55%</div></div></div></div>'
    )
    html = card + card
    assert card_missing_model_value_issues(html, "NCAAF")


def test_ncaaf_na_pc_val_fails_even_when_class_has_extra_tokens():
    card = (
        '<div data-pick-card><div class="pick-conf-grid">'
        '<div class="pc-box extra"><div class="pc-name">Edge</div>'
        '<div class="pc-val" style="color:#64748b;">N/A</div></div>'
        '<div class="pc-box"><div class="pc-name">XSharp</div>'
        '<div class="pc-val">98.3%</div></div>'
        '<div class="pc-box"><div class="pc-name">Sharp Consensus</div>'
        '<div class="pc-val">89%</div></div>'
        '<div class="pc-box"><div class="pc-name">Efficiency</div>'
        '<div class="pc-val">63%</div></div>'
        '<div class="pc-box"><div class="pc-name">Grinder2</div>'
        '<div class="pc-val">90%</div></div>'
        '<div class="pc-box"><div class="pc-name">Takedown</div>'
        '<div class="pc-val">99%</div></div>'
        "</div></div>"
    )
    issues = card_missing_model_value_issues(card + card, "NCAAF")
    assert issues
    assert any("N/A" in i or "blank" in i for i in issues)


def test_ncaaf_blank_spread_total_and_edge_fifty_fail():
    card = """
    <div data-pick-card data-league="NCAAF">
      <div class="pc-name">Edge</div><div class="pc-val">50.0%</div>
      <div class="pc-name">XSharp</div><div class="pc-val">98.3%</div>
      <div class="pc-name">Sharp Consensus</div><div class="pc-val">89.3%</div>
      <div class="pc-name">Efficiency</div><div class="pc-val">63.4%</div>
      <div class="pc-name">Grinder2</div><div class="pc-val">90.7%</div>
      <div class="pc-name">Takedown</div><div class="pc-val">99.9%</div>
      <table>
        <tr><td class="market-k">Spread</td>
            <td class="val-books">Miami Hurricanes -55.5</td>
            <td class="val-pl">Miami Hurricanes -5.5</td>
            <td class="val-xs">—</td></tr>
        <tr><td class="market-k">Total</td>
            <td class="val-books">61.5</td>
            <td class="val-pl">—</td>
            <td class="val-xs">—</td></tr>
      </table>
    </div>
    """
    html = card + card
    miss = card_missing_model_value_issues(html, "NCAAF")
    lines = card_blank_market_line_issues(html, "NCAAF")
    assert any("50%" in i or "blank" in i for i in miss)
    assert any("XSharp spread" in i for i in lines)
    assert any("Prediction Lab total" in i for i in lines)
    assert any("XSharp total" in i for i in lines)


def test_mlb_na_grinder_and_blank_xsharp_proj_fail():
    card = """
    <div data-pick-card data-sport="MLB">
      <div class="pick-conf-grid">
        <div class="pc-box"><div class="pc-name">Edge</div><div class="pc-val">51.1%</div></div>
        <div class="pc-box"><div class="pc-name">XSharp</div><div class="pc-val">51.1%</div></div>
        <div class="pc-box"><div class="pc-name">Sharp Consensus</div><div class="pc-val">51.1%</div></div>
        <div class="pc-box"><div class="pc-name">Efficiency</div><div class="pc-val">63.1%</div></div>
        <div class="pc-box"><div class="pc-name">Grinder2</div><div class="pc-val" style="color:#64748b;">N/A</div></div>
        <div class="pc-box"><div class="pc-name">Takedown</div><div class="pc-val" style="color:#64748b;">N/A</div></div>
      </div>
      <table>
        <tr><td class="market-k">Run Line</td>
            <td class="val-books">Chicago Cubs -1.5</td>
            <td class="val-pl">Chicago Cubs -1.5</td>
            <td class="val-xs">—</td></tr>
        <tr><td class="market-k">Total</td>
            <td class="val-books">8</td>
            <td class="val-pl">10.5</td>
            <td class="val-xs">—</td></tr>
      </table>
      <div class="proj-row"><span class="proj-model xs">XSharp</span>
           <span class="proj-val">—</span></div>
    </div>
    """
    html = card + card
    miss = card_missing_model_value_issues(html, "MLB")
    lines = card_blank_market_line_issues(html, "MLB")
    assert any("N/A" in i or "blank" in i for i in miss)
    assert any("XSharp spread" in i for i in lines)
    assert any("XSharp total" in i for i in lines)
    assert any("projected score" in i for i in lines)


OWNER_NFL_WEEK22 = """
🏈 Week 22
1 Games
⭐ Grinder2
100.0%
1-0
🎯 Takedown
100.0%
1-0
📊 Edge
0.0%
0-1
🤖 XSharp
100.0%
1-0
🏆 Sharp Consensus
100.0%
1-0
Date	Matchup	Score	Grinder2	Takedown	Edge	XSharp	Sharp Consensus
2026-02-08	Seattle Seahawks @ New England Patriots	29 - 13	✅ 52.7%	✅ 72.9%	❌ 50.0%	✅ 77.1%	✅ 64.4%
"""


def test_nfl_week22_super_bowl_in_september_fails():
    issues = nfl_stale_season_week_issues(OWNER_NFL_WEEK22, today=date(2026, 9, 10))
    assert issues
    assert any("Week 22" in i and "2026-02-08" in i for i in issues)


def test_nfl_week22_in_february_is_ok():
    assert not nfl_stale_season_week_issues(OWNER_NFL_WEEK22, today=date(2026, 2, 9))


def test_nfl_season_year_september_is_current():
    from datetime import datetime

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sports.NFL import nfl_season_year

    assert nfl_season_year(datetime(2026, 9, 10)) == 2026
    assert nfl_season_year(datetime(2026, 2, 8)) == 2025


def test_nfl_last_season_snapshot_perf_fails():
    html = """
    NFL Results nfl-results
    Last Night's NFL Results
    <h2>Season Performance</h2>
    <div>Moneyline (XSharp)</div>
    <div>72.3%</div>
    <div>206-79</div>
    <div class="game-card">SEA</div>
    <div class="game-card">NE</div>
    <div class="game-card">KC</div>
    """
    issues = nfl_stale_season_perf_issues(html, today=date(2026, 9, 10))
    assert issues
    assert any("206-79" in i or "285" in i for i in issues)


def test_nfl_preseason_in_season_perf_fails():
    html = """
    NFL Results nfl-results
    Last Night's NFL Results
    <h2>Season Performance</h2>
    <div>Moneyline (Takedown)</div>
    <div>56.4%</div>
    <div>22-17</div>
    <div class="game-card">2026-08-14 preseason</div>
    <div class="game-card">2026-09-09 SEA</div>
    <div class="game-card">2026-09-09 NE</div>
    """
    issues = nfl_stale_season_perf_issues(html, today=date(2026, 9, 10))
    assert issues
    assert any("preseason" in i.lower() for i in issues)


def test_nfl_this_season_perf_passes():
    html = """
    NFL Results nfl-results
    Last Night's NFL Results
    <h2>Season Performance</h2>
    <div>Moneyline (XSharp)</div>
    <div>100.0%</div>
    <div>1-0</div>
    <div class="game-card">2026-08-14 preseason</div>
    <div class="game-card">2026-09-09 SEA</div>
    <div class="game-card">2026-09-09 NE</div>
    """
    assert not nfl_stale_season_perf_issues(html, today=date(2026, 9, 10))


def test_nfl_week1_in_september_passes():
    html = """
    NFL Results
    <div class="week-title">Week 1</div>
    2026-09-07 Dallas Cowboys @ Philadelphia Eagles
    """
    assert not nfl_stale_season_week_issues(html, today=date(2026, 9, 10))


OWNER_NFL_NO_EFFICIENCY = """
Last Night's NFL Results — 2026-09-09 (1 games)
⭐ Grinder2
100.0%
1-0
🎯 Takedown
100.0%
1-0
📊 Edge
100.0%
1-0
🤖 XSharp
100.0%
1-0
🏆 Sharp Consensus
100.0%
1-0
<h1>NFL - Week by Week Results</h1>
<table class="games-table"><thead><tr>
<th>Date</th><th>Matchup</th><th>Score</th>
<th>Grinder2</th><th>Takedown</th><th>Edge</th>
<th>XSharp</th><th>Sharp Consensus</th>
</tr></thead></table>
"""


def test_ncaaf_last_night_without_efficiency_tile_passes():
    html = """
    Last Night's NCAA Football Results — 2026-09-10 (1 games)
    MONEYLINE
    Grinder2
    100.0%
    1-0
    Takedown
    100.0%
    1-0
    Edge
    100.0%
    1-0
    XSharp
    100.0%
    1-0
    Sharp Consensus
    100.0%
    1-0
    """
    assert not blank_moneyline_model_issues(html)


def test_cfl_footer_edge_price_is_not_a_tally_fail():
    html = """
    Last Night's CFL Results — 2026-09-07 (2 games)
    MONEYLINE
    Grinder2
    50.0%
    1-1
    Takedown
    50.0%
    1-1
    Edge
    50.0%
    1-1
    XSharp
    50.0%
    1-1
    Sharp Consensus
    50.0%
    1-1
    Efficiency
    50.0%
    1-1
    Last Night's CFL Results
    Understanding These Results
    Premium Edge — $4.99/wk
    Monthly Plan — $19.99
    """
    assert not blank_moneyline_model_issues(html)


def test_nfl_last_night_missing_g2_td_eff_tiles_fails():
    html = """
    Last Night's NFL Results — 2026-09-10 (2 games)
    📊 Edge
    50.0%
    1-1
    🤖 XSharp
    0.0%
    0-2
    🏆 Sharp Consensus
    0.0%
    0-2
    """
    issues = blank_moneyline_model_issues(html)
    assert issues
    assert any("Grinder2" in i and "Takedown" in i and "Efficiency" in i for i in issues)


def test_nfl_results_without_efficiency_fail():
    issues = nfl_missing_efficiency_issues(OWNER_NFL_NO_EFFICIENCY)
    assert issues
    assert any("omit Efficiency" in i for i in issues)
    dashed = OWNER_NFL_NO_EFFICIENCY.replace(
        "🏆 Sharp Consensus\n100.0%\n1-0\n",
        "🏆 Sharp Consensus\n100.0%\n1-0\n⚡ Efficiency\n—\n—\n",
    )
    tally = blank_moneyline_model_issues(dashed)
    assert tally
    assert any("Efficiency" in i for i in tally)


def test_nfl_identical_cards_and_chart_fail():
    html = '<html>NFL week board <div class="week-section">Week 1</div></html>'
    assert nfl_chart_same_as_cards_issues(html, html)
    assert nfl_chart_same_as_cards_issues(
        html + " cards",
        html + ' <body class="nfl-x"> <div class="week-section">',
    )


def test_nfl_chart_hide_class_passes():
    cards = (
        '<html>Last Night\'s NFL Results <div class="date-section">'
        '<div class="game-card">NE @ SEA</div></div></html>'
    )
    chart = """
    <html><body class="nfl-results-chart">
    Consensus Based Betting Records
    Moneyline on the pregame majority among the 6 live models.
    <th>Agreement</th><th>Last night (2026-09-09)</th><th>Past 7 days</th><th>Past 30 days</th>
    <td class="bucket">6/6 unanimous</td>
    <td class="bucket">5/6 — all but Edge</td>
    <div class="cons-bar"><i></i></div>
    <button>Moneyline</button><button>Spread</button><button>Totals</button>
    </body></html>
    """
    assert not nfl_chart_same_as_cards_issues(cards, chart)
    assert not nfl_chart_not_mlb_issues(chart)


def test_nfl_spread_tab_without_cards_fails():
    html = """
    Last Night's NFL Results — 2026-09-09
    <button>Spread</button>
    <div class="week-section">moneyline table only</div>
    """
    issues = nfl_spread_result_card_issues(html)
    assert issues
    assert any("Spread" in i for i in issues)


def test_nfl_five_model_consensus_fails():
    html = """
    Consensus Based Betting Records
    Moneyline on the pregame majority among the 5 live models.
    <th>Agreement</th><th>Last night (2026-09-09)</th><th>Past 7 days</th><th>Past 30 days</th>
    <td class="bucket">5/5 unanimous</td>
    <td>5/5 — all but Edge</td>
    <div class="cons-bar"></div>
    <button>Moneyline</button><button>Spread</button><button>Totals</button>
    """
    issues = six_model_chart_issues(html, "NFL")
    assert issues
    assert any("5-model" in i or "5/5" in i or "not 6-model" in i for i in issues)


def test_nfl_unanimous_only_consensus_fails():
    html = """
    Consensus Based Betting Records
    Moneyline on the pregame majority among the 6 live models.
    <th>Agreement</th><th>Last night (2026-09-09)</th><th>Past 7 days</th><th>Past 30 days</th>
    <td class="bucket">6/6 unanimous</td>
    <div class="cons-bar"><i></i></div>
    <button>Moneyline</button><button>Spread</button><button>Totals</button>
    """
    issues = six_model_chart_issues(html, "NFL")
    assert issues
    assert any("all-but" in i or "agreement row" in i for i in issues)


def test_nfl_chart_empty_last_night_fails():
    html = """
    <body class="nfl-results-chart">
    <h2>Last Night <span>(1 games)</span></h2>
    <h2>Last 7 <span>(2 games)</span></h2>
    <h2>Season <span>(2 games)</span></h2>
    Consensus Based Betting Records
    </body>
    """
    issues = nfl_chart_window_tally_issues(html)
    assert issues
    assert any("model card" in i for i in issues)


def test_nfl_chart_api_missing_last_night_fails():
    payload = {
        "ok": True,
        "markets": {
            "moneyline": {"tallies": {"last_night": {"models": {}}, "last_7": {"models": {}}}},
            "spread": {"tallies": {}},
            "totals": {"tallies": {}},
        },
    }
    issues = nfl_chart_api_issues(payload)
    assert issues
    assert any("Last Night" in i for i in issues)


def test_nfl_chart_with_last_night_boxes_fails():
    html = """
    <body class="nfl-results-chart">
    Last Night's NFL Results — 2026-09-09
    Season Performance
    Consensus Based Betting Records
    Moneyline on the pregame majority among the 6 live models.
    <th>Agreement</th><th>Last night (2026-09-09)</th><th>Past 7 days</th><th>Past 30 days</th>
    <td class="bucket">6/6 unanimous</td>
    <td class="bucket">5/6 — all but Edge</td>
    <div class="cons-bar"><i></i></div>
    <button>Moneyline</button><button>Spread</button><button>Totals</button>
    </body>
    """
    issues = nfl_chart_not_mlb_issues(html)
    assert issues
    assert any("Last Night" in i for i in issues)


def test_nfl_mlb_shaped_six_model_consensus_passes():
    html = """
    Consensus Based Betting Records
    Moneyline on the pregame majority among the 6 live models.
    <th>Agreement</th><th>Last night (2026-09-09)</th><th>Past 7 days</th><th>Past 30 days</th>
    <td class="bucket">6/6 unanimous</td>
    <td class="bucket">5/6 — all but Edge</td>
    <div class="cons-bar"><i></i></div>
    <button>Moneyline</button><button>Spread</button><button>Totals</button>
    """
    assert not six_model_chart_issues(html, "NFL")


def test_nfl_results_with_efficiency_pass():
    html = OWNER_NFL_NO_EFFICIENCY.replace(
        "🏆 Sharp Consensus\n100.0%\n1-0\n",
        "🏆 Sharp Consensus\n100.0%\n1-0\n⚡ Efficiency\n100.0%\n1-0\n",
    ).replace(
        "<th>XSharp</th><th>Sharp Consensus</th>",
        "<th>XSharp</th><th>Sharp Consensus</th><th>Efficiency</th>",
    )
    assert not nfl_missing_efficiency_issues(html)


def test_nfl_daily_game_card_spread_passes():
    html = """
    Last Night's NFL Results — 2026-09-09
    <button>Spread</button>
    <div class="game-card" data-pick-card>NE @ SEA spread_correct</div>
    """
    assert not nfl_spread_result_card_issues(html)


def test_nfl_one_card_results_fail_shared_template():
    html = """
    <html>Last Night's NFL Results
    <div class="daily-tally"></div>
    Season Performance
    Consensus Based Betting Records
    <div class="date-nav"></div>
    <div id="date-2026-09-09" class="date-section">
      <div class="game-card">NE @ SEA</div>
    </div>
    </html>
    """
    issues = team_results_template_issues(html, "NFL", today=date(2026, 9, 10))
    assert issues
    assert any("last night only" in i or "game card" in i for i in issues)


def test_nfl_multiday_results_pass_shared_template():
    html = """
    <html>Last Night's NFL Results
    <div class="daily-tally"></div>
    Season Performance
    Consensus Based Betting Records
    <div class="date-nav"></div>
    <div id="date-2026-09-09" class="date-section"><div class="game-card">a</div></div>
    <div id="date-2026-08-29" class="date-section"><div class="game-card">b</div></div>
    <div id="date-2026-08-28" class="date-section"><div class="game-card">c</div></div>
    </html>
    """
    assert not team_results_template_issues(html, "NFL", today=date(2026, 9, 10))


def test_team_chart_with_last_night_board_fails():
    html = """
    Last Night's NFL Results
    Season Performance
    Consensus Based Betting Records
    """
    issues = team_chart_template_issues(html, "NFL")
    assert issues
    assert any("Last Night" in i or "Season Performance" in i for i in issues)


def test_team_picks_missing_grid_fails():
    assert team_picks_template_issues("<html>no cards</html>", "NFL")


def test_nfl_last_night_empty_spread_tally_fails():
    html = """
    Last Night's NFL Results — 2026-09-09 (1 games)
    <div class="daily-rec">no spread data</div>
    <div class="daily-rec">no O/U data</div>
    Model Performance (Flat Unit Tracking)
    No graded picks in range.
    """
    issues = empty_last_night_spread_tally_issues(html)
    assert any("no spread data" in i for i in issues)
    assert any("no O/U data" in i for i in issues)
