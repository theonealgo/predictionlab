"""Major date-window failures: dropdown, one month of results, pick horizon, API day."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa"))

from chart_shape import picks_date_bounds, slate_date_window_issues  # noqa: E402
from espn_slate_checker import (  # noqa: E402
    api_date_mismatch_issues,
    api_missing_day_issues,
    picks_window,
)


TODAY = date(2026, 9, 29)


def _page(dates: list[str], *, picker: bool = True, cards: bool = True) -> str:
    opts = "".join(f'<option value="{d}">{d}</option>' for d in dates)
    sections = "".join(
        f'<div id="date-{d}" class="date-section">'
        f'<div class="game-card" data-pick-card data-date="{d}" '
        f'data-away="Away" data-home="Home"></div></div>'
        for d in dates
    )
    picker_html = (
        f'<select id="datePicker">{opts}</select>'
        f'<script>const allDates = {dates!r};</script>'
        if picker
        else ""
    )
    body = sections if cards else ""
    return picker_html + body


def test_daily_picks_reject_dates_outside_three_days():
    html = _page(["2026-09-20", "2026-09-29", "2026-10-06"])
    issues = slate_date_window_issues(html, "MLB", page="picks", today=TODAY)
    assert any("2026-09-20" in i and "2026-10-06" in i for i in issues)
    assert any("3 days before and 3 days after" in i for i in issues)


def test_weekly_picks_allow_three_weeks_and_reject_beyond():
    start, end = picks_date_bounds("NCAAF", TODAY)
    assert start.isoformat() == "2026-09-26"
    assert end.isoformat() == "2026-10-20"
    ok = _page(["2026-09-26", "2026-10-03", "2026-10-20"])
    assert slate_date_window_issues(ok, "NFL", page="picks", today=TODAY) == []
    far = _page(["2026-09-26", "2026-10-24"])
    issues = slate_date_window_issues(far, "NCAAF", page="picks", today=TODAY)
    assert any("2026-10-24" in i and "3 weeks after" in i for i in issues)


def test_missing_dropdown_is_a_date_error():
    html = _page(["2026-09-29"], picker=False)
    issues = slate_date_window_issues(html, "NCAAF", page="picks", today=TODAY)
    assert any("no dropdown date picker" in i for i in issues)


def test_card_date_must_match_its_section():
    html = (
        '<select id="datePicker"></select>'
        '<div id="date-2026-09-29">'
        '<div data-pick-card data-date="2026-09-28" data-away="A" data-home="B"></div>'
        "</div>"
    )
    issues = slate_date_window_issues(html, "MLB", page="picks", today=TODAY)
    assert any("2026-09-28" in i and "2026-09-29" in i for i in issues)


def test_results_must_include_today_and_one_month_back():
    short = _page(["2026-09-20", "2026-09-29"])
    issues = slate_date_window_issues(short, "NCAAF", page="results", today=TODAY)
    assert any("back to 2026-08-30" in i for i in issues)
    month = _page(["2026-08-29", "2026-09-12", "2026-09-29"])
    assert slate_date_window_issues(month, "NCAAF", page="results", today=TODAY) == []
    no_today = _page(["2026-08-29", "2026-09-28"])
    issues = slate_date_window_issues(no_today, "MLB", page="results", today=TODAY)
    assert any("does not include today" in i for i in issues)


def test_api_date_mismatch_and_missing_edge_day():
    page = [("2026-10-03", "Ohio State", "Michigan")]
    espn = {
        "2026-10-03": [],
        "2026-10-04": [("Ohio State", "Michigan")],
    }
    issues = api_date_mismatch_issues(page, espn, sport="NCAAF")
    assert any("2026-10-03" in i and "2026-10-04" in i for i in issues)
    missing = api_missing_day_issues(
        page,
        {"2026-09-26": [("Alabama", "Georgia")], "2026-09-29": [], "2026-10-20": []},
        ["2026-09-26", "2026-09-29", "2026-10-20"],
    )
    assert any("2026-09-26" in i and "page has none" in i for i in missing)
    assert picks_window("NFL", "2026-09-29") == ("2026-09-26", "2026-10-20")
    assert picks_window("MLB", "2026-09-29") == ("2026-09-26", "2026-10-02")
