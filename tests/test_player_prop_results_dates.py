"""Tests for player prop results date anchoring (Last Night / Last 7 Days)."""
from datetime import date, timedelta


def _props_normalize_display_date(for_date, today_et):
    yesterday_et = today_et - timedelta(days=1)
    if for_date:
        try:
            display = date.fromisoformat(for_date)
        except Exception:
            display = yesterday_et
    else:
        display = yesterday_et
    if display >= today_et:
        display = yesterday_et
    return display, yesterday_et


def test_today_clamps_to_yesterday():
    today = date(2026, 6, 4)
    display, yesterday = _props_normalize_display_date("2026-06-04", today)
    assert display == date(2026, 6, 3)
    assert yesterday == date(2026, 6, 3)


def test_missing_date_defaults_yesterday():
    today = date(2026, 6, 4)
    display, yesterday = _props_normalize_display_date(None, today)
    assert display == yesterday == date(2026, 6, 3)


def test_historical_date_preserved():
    today = date(2026, 6, 4)
    display, yesterday = _props_normalize_display_date("2026-05-20", today)
    assert display == date(2026, 5, 20)
    assert yesterday == date(2026, 6, 3)


def test_week_window_ends_yesterday():
    today = date(2026, 6, 4)
    _, yesterday = _props_normalize_display_date(None, today)
    week_start = yesterday - timedelta(days=6)
    assert week_start == date(2026, 5, 28)
    assert yesterday == date(2026, 6, 3)
