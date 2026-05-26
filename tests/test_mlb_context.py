"""Unit tests for MLB contextual pipeline (mlb_context.py)."""
import unittest
from unittest.mock import patch

from mlb_context import (
    damped_blend,
    pitcher_quality_tier,
    compute_bullpen_context,
    compute_lineup_context,
    weather_park_total_adjustment,
    validate_market_timing,
    umpire_total_adjustment,
    apply_mlb_context_layers,
    MLB_PARK_POINTS,
)


class TestDampedBlend(unittest.TestCase):
    def test_blend_midpoint(self):
        val, w = damped_blend(4.0, 5.0, recent_weight=0.5, outlier_cap=2.0)
        self.assertIsNotNone(val)
        self.assertGreater(w, 0)

    def test_outlier_dampens_weight(self):
        _, w_small = damped_blend(3.0, 8.0, recent_weight=0.5, outlier_cap=1.0)
        _, w_large = damped_blend(3.0, 4.0, recent_weight=0.5, outlier_cap=10.0)
        self.assertLess(w_small, w_large)


class TestPitcherQuality(unittest.TestCase):
    def test_elite_tier(self):
        tier, score = pitcher_quality_tier(2.8, whip=1.05, kbb=4.5, recent_era=2.9)
        self.assertEqual(tier, 'elite')
        self.assertGreaterEqual(score, 0.86)


class TestBullpenAggregate(unittest.TestCase):
    @patch('mlb_context._team_bullpen_fatigue')
    def test_team_aggregated_totals(self, mock_fatigue):
        mock_fatigue.side_effect = [
            (0.01, 0.5, True, 0.6, 1),
            (0.02, 0.35, False, 0.4, 2),
        ]
        ctx = compute_bullpen_context('A', 'B', '2025-05-20', [], [])
        self.assertAlmostEqual(ctx.total_adj, 0.85)
        self.assertEqual(ctx.home_relief_out, 1)
        self.assertEqual(ctx.away_relief_out, 2)


class TestLineupContext(unittest.TestCase):
    def test_unconfirmed_penalty(self):
        pitch = {'home_sp_name': 'TBD', 'away_sp_name': 'Smith', 'has_pitching_data': True}
        ctx = compute_lineup_context(pitch, [], [])
        self.assertFalse(ctx.lineup_confirmed)
        self.assertGreater(ctx.confidence_penalty, 0)


class TestWeatherPark(unittest.TestCase):
    def test_coors_static_points(self):
        ctx = weather_park_total_adjustment('Colorado Rockies')
        self.assertGreater(ctx.total_adj, 1.0)
        self.assertAlmostEqual(MLB_PARK_POINTS['Colorado Rockies'], 1.2)

    def test_minimal_ml_delta(self):
        ctx = weather_park_total_adjustment('Boston Red Sox')
        self.assertLess(abs(ctx.ml_delta), 0.1)


class TestMarketTiming(unittest.TestCase):
    def test_missing_lines_early_flag(self):
        ctx = validate_market_timing('2099-01-01', None, -110, book_total=8.5, model_total=9.0)
        self.assertTrue(ctx.early_market)
        self.assertIn('missing_moneylines', ctx.flags)


class TestUmpire(unittest.TestCase):
    def test_minor_weight(self):
        self.assertLessEqual(abs(umpire_total_adjustment('Angel Hernandez')), 0.15)

    def test_disabled(self):
        with patch.dict('os.environ', {'MLB_UMPIRE_ENABLED': 'false'}):
            self.assertEqual(umpire_total_adjustment('Angel Hernandez'), 0.0)


class TestApplyLayers(unittest.TestCase):
    @patch('mlb_context._team_bullpen_fatigue', return_value=(0.0, 0.0, False, 0.0, 0))
    @patch('mlb_context._recent_pitcher_era', return_value=None)
    def test_returns_diagnostics(self, *_mocks):
        pitch = {
            'home_sp_name': 'Ace',
            'away_sp_name': 'Bob',
            'home_sp_era': 3.5,
            'away_sp_era': 4.1,
            'has_pitching_data': True,
        }
        res = apply_mlb_context_layers(
            home_team='Boston Red Sox',
            away_team='New York Yankees',
            game_date='2025-05-22',
            game_id=None,
            pitch=pitch,
            home_injuries=[],
            away_injuries=[],
            pre_blended=0.52,
            home_mkt=0.5,
            home_ml=-110,
            away_ml=100,
            book_total=8.5,
            model_total=9.0,
        )
        self.assertIn('weather_contrib', res.diagnostics.to_dict())
        self.assertIsInstance(res.diagnostics.early_market, bool)


if __name__ == '__main__':
    unittest.main()
