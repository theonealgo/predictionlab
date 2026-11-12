"""Recent-results calendar windows; no application imports or ledger writes."""
import copy
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 11, 9, 0, tzinfo=tz)


def load_recent():
    spec = importlib.util.spec_from_file_location("recent_calendar_fixture", ROOT / "picks_recent_results.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_recent(sport="NBA", empty=False):
    module = load_recent()
    counts = {"2026-11-04": 14, "2026-11-05": 2, "2026-11-06": 9,
              "2026-11-07": 7, "2026-11-08": 7, "2026-11-09": 8, "2026-11-10": 6}
    daily = {}
    for i in range(30):
        day = (datetime(2026, 11, 10) - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[day] = {"games": [{"immutable": day}] * counts.get(day, 1)}
    if empty:
        daily = {}
    frozen = copy.deepcopy(daily)

    def in_range(day, start, end):
        parsed = datetime.strptime(day, "%Y-%m-%d")
        return (not start or parsed >= start) and (not end or parsed <= end)

    def ranged(data, start, end, **kwargs):
        return {"games": sum(len(bucket["games"]) for day, bucket in data.items()
                             if in_range(day, start, end))}

    fake = SimpleNamespace(
        _banner_daily_results_for_range=lambda sport, start, end, **kw:
            {day: bucket for day, bucket in daily.items() if in_range(day, start, end)},
        compute_daily_model_tally=lambda data, day, **kw:
            {"games": len(data[day]["games"])} if day in data else None,
        compute_model_tally_for_range=ranged,
        _date_in_range=in_range,
        parse_date=lambda day: datetime.strptime(day, "%Y-%m-%d"),
        _empty_results_model_tally=lambda: {"games": 0},
    )
    with patch.dict(sys.modules, {"NHL77FINAL": fake}), patch.object(module, "datetime", Clock), \
            patch.object(module, "_fill_efficiency_and_grades", lambda *args: None):
        html, _ = module._build_block(sport)
    assert daily == frozen
    return [int(n) for n in re.findall(r'class="picks-recent-n">(\d+) games', html)]


class RecentCalendarTests(unittest.TestCase):
    def test_yesterday_is_midnight(self):
        module = load_recent()
        with patch.object(module, "datetime", Clock):
            yesterday = module._yesterday_et()
        self.assertEqual(yesterday.isoformat(), "2026-11-10T00:00:00-05:00")

    def test_nba_includes_all_fourteen_games_from_first_day(self):
        self.assertEqual(render_recent(), [6, 53, 76])

    def test_complete_calendar_windows_for_every_sport(self):
        for sport in ("NBA", "NHL", "NFL", "NCAAF", "NCAAB", "NCAAW", "MLB",
                      "WNBA", "CFL", "SOCCER", "TENNIS", "UFC", "GOLF"):
            with self.subTest(sport=sport):
                self.assertEqual(render_recent(sport), [6, 53, 76])

    def test_empty_dates_do_not_invent_results(self):
        self.assertEqual(render_recent(empty=True), [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
