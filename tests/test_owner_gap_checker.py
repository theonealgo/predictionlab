"""Owner-reported gaps the checker must fail on."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qa"))

from datetime import date  # noqa: E402

from page_speed import (  # noqa: E402
    is_results_path,
    is_slow,
    results_wont_open_message,
    speed_fail_message,
)
from chart_shape import (  # noqa: E402
    best_performing_width_issues,
    efficiency_copied_na_issues,
    ncaaf_chart_api_issues,
    share_ad_card_issues,
    team_chart_sou_table_issues,
    blank_moneyline_model_issues,
    card_blank_market_line_issues,
    card_missing_model_value_issues,
    mlb_xsharp_totals_issues,
    team_xsharp_totals_issues,
    nba_last_season_gap_issues,
    nfl_chart_api_issues,
    nfl_chart_not_mlb_issues,
    nfl_chart_same_as_cards_issues,
    nfl_chart_window_tally_issues,
    duplicate_h2h_issues,
    nfl_duplicate_h2h_issues,
    nfl_missing_efficiency_issues,
    results_missing_efficiency_issues,
    team_chart_api_issues,
    nfl_preseason_results_issues,
    empty_last_night_spread_tally_issues,
    nfl_spread_result_card_issues,
    nfl_stale_season_perf_issues,
    nfl_stale_season_week_issues,
    nhl_chart_view_missing_issues,
    six_model_chart_issues,
    results_card_parameter_issues,
    team_chart_leftover_board_issues,
    team_chart_same_as_cards_issues,
    team_chart_template_issues,
    team_chart_window_tally_issues,
    team_picks_template_issues,
    team_results_tally_model_issues,
    team_results_template_issues,
    tennis_chart_same_as_cards_issues,
    six_model_consensus_from_cards_issues,
    pl_vs_books_partition_issues,
    best_performing_today_issues,
)


def test_page_over_5s_is_slow():
    assert is_slow(5.01)
    assert not is_slow(4.9)
    msg = speed_fail_message("/ncaaf-results", 12.3)
    assert "12.3s" in msg
    assert "5s" in msg
    assert "Won't open" not in msg
    assert is_results_path("/nfl-results")
    slow = results_wont_open_message("/nfl-results", 32.6)
    assert "32.6s" in slow
    assert "Won't open" not in slow


def test_site_checker_reports_slow_page_as_fail():
    from site_checker import (  # noqa: E402
        FAIL,
        AuditReport,
        _SLOW_PAGES_REPORTED,
        _report_slow_page,
    )

    _SLOW_PAGES_REPORTED.clear()
    report = AuditReport()
    _report_slow_page(report, "/ncaaf-results", 5.01, "http://127.0.0.1:5052/ncaaf-results")
    fails = [c for c in report.checks if c.status == FAIL]
    assert fails
    assert fails[0].label == "speed /ncaaf-results"
    assert "5s" in fails[0].message
    _report_slow_page(report, "/ncaaf-results", 9.0, "http://127.0.0.1:5052/ncaaf-results")
    assert len([c for c in report.checks if c.label.startswith("speed")]) == 1

    _SLOW_PAGES_REPORTED.clear()
    fast = AuditReport()
    _report_slow_page(fast, "/ncaaf-picks", 4.99)
    assert not fast.checks
    _SLOW_PAGES_REPORTED.clear()


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


def test_xsharp_totals_flags_any_team_sport():
    issues = team_xsharp_totals_issues("<html>XSharp</html>", "<html>XSharp</html>", "CFL")
    assert any("CFL" in i and "Totals" in i for i in issues)


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


def test_duplicate_h2h_flags_any_team_sport():
    html = """
    <div data-pick-card>
      <div class="line-chip h2h-face-chip">
        <div class="line-chip-label">H2H Last 10</div>
        <div class="line-chip-val">First meeting</div>
      </div>
      <div class="sf-item">
        <span class="sf-label">H2H Last 10</span>
        <span class="sf-val">First meeting</span>
      </div>
    </div>
    """
    issues = duplicate_h2h_issues(html, "NHL")
    assert issues
    assert any("NHL" in i for i in issues)


def test_team_chart_api_missing_ok_fails_for_mlb():
    issues = team_chart_api_issues(None, "mlb")
    assert any("mlb/api/picks" in i for i in issues)


def test_results_missing_efficiency_flags_cfl():
    html = """
    Last Night's CFL Results
    Grinder2 80% Takedown 80% Edge 60% XSharp 80% Sharp Consensus 80%
    Last 7 Days CFL Results
    """
    issues = results_missing_efficiency_issues(html, "CFL")
    assert issues
    assert any("Efficiency" in i for i in issues)


def test_mlb_chart_window_tally_is_checked():
    html = "Consensus Based Betting Records"
    issues = team_chart_window_tally_issues(html, "MLB")
    assert issues
    assert any("Last Night" in i or "Season" in i or "model card" in i for i in issues)


def test_nfl_duplicate_h2h_fails():
    html = """
    <div data-pick-card>
      <div class="lines-strip">
        <div class="line-chip h2h-face-chip">
          <div class="line-chip-label">H2H Last 10</div>
          <div class="line-chip-val">First meeting</div>
        </div>
      </div>
      <div class="sf-item">
        <span class="sf-label">H2H Last 10</span>
        <span class="sf-val">First meeting</span>
      </div>
    </div>
    """
    issues = nfl_duplicate_h2h_issues(html)
    assert issues
    assert any("details" in i.lower() for i in issues)


def test_nfl_face_only_h2h_passes():
    html = """
    <div data-pick-card>
      <div class="lines-strip">
        <div class="line-chip h2h-face-chip">
          <div class="line-chip-label">H2H Last 10</div>
          <div class="line-chip-val">First meeting</div>
        </div>
      </div>
    </div>
    """
    assert not nfl_duplicate_h2h_issues(html)


def test_nfl_preseason_results_date_fails():
    html = """
    NFL Results nfl-results
    <div id="date-2026-08-14" class="date-section"></div>
    <div id="date-2026-09-10" class="date-section"></div>
    """
    issues = nfl_preseason_results_issues(html)
    assert issues
    assert any("2026-08-14" in i for i in issues)


def test_nfl_regular_only_results_pass():
    html = """
    NFL Results nfl-results
    <div id="date-2026-09-10" class="date-section"></div>
    """
    assert not nfl_preseason_results_issues(html)


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


def test_ncaaf_results_missing_efficiency_tally_fails():
    html = """
    Last Night's NCAA Football Results — 2026-09-11 (5 games)
    Grinder2 80.0% Takedown 80.0% Edge 60.0% XSharp 80.0% Sharp Consensus 80.0%
    Last 7 Days NCAA Football Results
    Grinder2 Takedown Edge XSharp Sharp Consensus
    Season Performance
    Moneyline Accuracy by Model
    Grinder2 Takedown Edge XSharp Sharp Consensus
    <div class="date-nav"></div>
    """
    issues = team_results_tally_model_issues(html, "NCAAF")
    assert issues
    assert any("Efficiency" in i for i in issues)


def test_ncaaf_results_card_efficiency_na_fails():
    card = (
        '<div class="game-card"><div class="odds-pricing-title">Odds &amp; Lines</div>'
        '<div class="pick-conf-grid">'
        '<div class="pc-name">Grinder2</div><div class="pc-val">80%</div>'
        '<div class="pc-name">Takedown</div><div class="pc-val">80%</div>'
        '<div class="pc-name">Edge</div><div class="pc-val">60%</div>'
        '<div class="pc-name">XSharp</div><div class="pc-val">90%</div>'
        '<div class="pc-name">Sharp Consensus</div><div class="pc-val">85%</div>'
        '<div class="pc-name">Efficiency</div><div class="pc-val">N/A</div>'
        '</div><span>H2H Last 10</span></div>'
    )
    issues = results_card_parameter_issues(card + card, "NCAAF")
    assert issues
    assert any("Efficiency" in i for i in issues)


def test_efficiency_copied_percent_fails():
    html = (
        '<div class="pc-box"><div class="pc-name">Efficiency</div>'
        '<div class="pc-val">81.8%</div>'
        '<div class="pc-side">N/A</div></div>'
    )
    issues = efficiency_copied_na_issues(html)
    assert issues


def test_share_card_needs_two_picks():
    assert share_ad_card_issues('<div class="social-export-wrap" data-share-picks="1"></div>')
    assert not share_ad_card_issues('<div class="social-export-wrap" data-share-picks="2"></div>')


def test_ncaaf_spread_table_still_moneyline_fails():
    html = (
        '<section id="ssr-finals" data-ssr-market="moneyline">'
        "<h2>Moneyline games</h2><th>Edge pick</th></section>"
    )
    issues = team_chart_sou_table_issues(html, "spread")
    assert issues
    assert any("moneyline" in i.lower() for i in issues)


def test_ncaaf_spread_table_with_compare_passes():
    html = (
        '<section id="ssr-finals" data-ssr-market="spread">'
        "<th>Books</th><th>Actual vs lines</th><td>Act 19–29 · Books Hawai'i -7.5</td>"
        "</section>"
    )
    assert not team_chart_sou_table_issues(html, "spread")


def test_best_performing_width_fails_without_css():
    html = '<section class="tally pl-analytics"><h2>Best Performing Model</h2></section>'
    assert best_performing_width_issues(html)
    html2 = html + '<style id="ncaaf-chart-best-width"></style>'
    assert not best_performing_width_issues(html2)


def test_ncaaf_chart_api_missing_spread_fails():
    issues = ncaaf_chart_api_issues({"ok": True, "markets": {"moneyline": {"tallies": {}}}})
    assert any("spread" in i for i in issues)


def test_ncaaf_chart_leftover_cards_board_fails():
    html = """
    <nav id="ncaafTopDates"></nav>
    Model Performance (Flat Unit Tracking)
    Consensus Based Betting Records
    <div class="game-card">a</div><div class="game-card">b</div><div class="game-card">c</div>
    """
    issues = team_chart_leftover_board_issues(html, "NCAAF")
    assert issues
    same = team_chart_same_as_cards_issues(html, html, "NCAAF")
    assert same
    windows = team_chart_window_tally_issues(html, "NCAAF")
    assert any("Last Night" in i or "model card" in i for i in windows)


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


_SIX_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _pc_box(name: str, side: str, ok: bool) -> str:
    mark = " ✅" if ok else " ❌"
    return (
        f'<div class="pc-box"><div class="pc-name">{name}</div>'
        f'<div class="pc-val">60%</div>'
        f'<div class="pc-side">{side}{mark}</div></div>'
    )


def _six_card(*, majority: str, won: bool, dissent: tuple[str, ...] = ()) -> str:
    other = "Away" if majority == "Home" else "Home"
    boxes = []
    for name in _SIX_MODELS:
        if name in dissent:
            boxes.append(_pc_box(name, other, not won))
        else:
            boxes.append(_pc_box(name, majority, won))
    return f'<div class="game-card">{"".join(boxes)}</div>'


def _date_section(day: str, cards: list[str]) -> str:
    return f'<div id="date-{day}" class="date-section">{"".join(cards)}</div>'


def _cons_table(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td class="bucket">{label}</td><td>{rec}</td><td>0-0</td><td>0-0</td></tr>'
        for label, rec in rows
    )
    return f"""
    Consensus Based Betting Records
    <th>Last night (2026-09-13)</th>
    <tbody>{body}</tbody>
    """


def test_consensus_buckets_mismatch_owner_nfl_slate():
    cards = (
        [_six_card(majority="Home", won=True) for _ in range(4)]
        + [_six_card(majority="Home", won=False)]
        + [_six_card(majority="Home", won=False, dissent=("Edge",)) for _ in range(3)]
        + [_six_card(majority="Home", won=True, dissent=("Edge",)) for _ in range(2)]
        + [_six_card(majority="Home", won=True, dissent=("Efficiency",))]
        + [_six_card(majority="Home", won=False, dissent=("Efficiency",))]
        + [
            _six_card(majority="Home", won=False, dissent=("Edge", "Efficiency"))
            for _ in range(3)
        ]
    )
    section = _date_section("2026-09-13", cards)
    wrong = section + _cons_table(
        [
            ("6/6 unanimous", "3-1"),
            ("5/6 — all but Edge", "2-3"),
            ("5/6 — all but Efficiency", "1-1"),
            ("4/6 — all but Edge and Efficiency", "0-2"),
        ]
    )
    issues = six_model_consensus_from_cards_issues(wrong, wrong, "NFL")
    assert issues
    assert any("6/6 unanimous" in i and "3-1" in i and "4-1" in i for i in issues)
    assert any("all but Edge" in i and "2-3" in i and "3-2" in i for i in issues)
    assert any("Edge and Efficiency" in i and "0-2" in i and "3-0" in i for i in issues)

    right = section + _cons_table(
        [
            ("6/6 unanimous", "4-1"),
            ("5/6 — all but Edge", "3-2"),
            ("5/6 — all but Efficiency", "1-1"),
            ("4/6 — all but Edge and Efficiency", "3-0"),
        ]
    )
    assert not six_model_consensus_from_cards_issues(right, right, "NFL")


def test_mlb_chart_unanimous_wl_not_matching_cards_fails():
    """Cards 6/6 last night 2-3 must not silently show 0-0 / a different sample on chart."""
    cards = _date_section(
        "2026-09-13",
        [_six_card(majority="Home", won=True) for _ in range(2)]
        + [_six_card(majority="Home", won=False) for _ in range(3)],
    )
    chart = _cons_table([("6/6 unanimous", "0-0")])
    issues = six_model_consensus_from_cards_issues(chart, cards, "MLB")
    assert issues
    assert any("2-3" in i and "0-0" in i for i in issues)


def test_pl_vs_books_partition_missing_game_fails():
    html = """
    Last Night's NFL Results — 2026-09-13 (13 games)
    PL vs Sportsbook
    Books favorite
    10-3
    PL favorite
    6-7
    PL vs Books disagree
    1-5
    PL and Books agree
    5-1
    """
    issues = pl_vs_books_partition_issues(html, "NFL")
    assert issues
    assert any("12" in i and "13" in i for i in issues)


def test_best_performing_today_on_yesterdays_slate_fails():
    html = """
    <section class="tally pl-analytics"><h2>Best Performing Model</h2>
    <div class="tally-card"><div class="mlabel">Today</div><div class="rec">9-4</div></div>
    </section>
    <th>Last night (2026-09-13)</th>
    """
    issues = best_performing_today_issues(html, "NFL", today=date(2026, 9, 14))
    assert issues
    assert any("Today" in i and "2026-09-13" in i for i in issues)


def test_nfl_card_aggregates_rewrites_wrong_consensus_and_today():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from team_results_charts import apply_nfl_card_aggregates

    cards = (
        [_six_card(majority="Home", won=True) for _ in range(4)]
        + [_six_card(majority="Home", won=False)]
        + [_six_card(majority="Home", won=False, dissent=("Edge",)) for _ in range(3)]
        + [_six_card(majority="Home", won=True, dissent=("Edge",)) for _ in range(2)]
        + [_six_card(majority="Home", won=True, dissent=("Efficiency",))]
        + [_six_card(majority="Home", won=False, dissent=("Efficiency",))]
        + [
            _six_card(majority="Home", won=False, dissent=("Edge", "Efficiency"))
            for _ in range(3)
        ]
    )
    html = (
        _date_section("2026-09-13", cards)
        + _cons_table(
            [
                ("6/6 unanimous", "3-1"),
                ("5/6 — all but Edge", "2-3"),
                ("5/6 — all but Efficiency", "1-1"),
                ("4/6 — all but Edge and Efficiency", "0-2"),
            ]
        )
        + '<section class="tally pl-analytics"><h2>Best Performing Model</h2>'
        '<div class="tally-card"><div class="mlabel">Today</div></div></section>'
    )
    out = apply_nfl_card_aggregates(html)
    assert not six_model_consensus_from_cards_issues(out, out, "NFL")
    assert "4-1" in out
    assert "3-2" in out
    assert "3-0" in out
    assert re.search(r'class="mlabel">\s*Last Night\s*<', out)
