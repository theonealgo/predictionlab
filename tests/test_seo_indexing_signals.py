"""SEO indexing signals: empty dated picks, sitemap crawl budget, blog redirects."""
import unittest
from datetime import datetime

from NHL77FINAL import (
    SEASON_CALENDAR,
    _PICKS_ROBOTS_INDEX,
    _PICKS_ROBOTS_NOINDEX,
    _picks_page_canonical_url,
    _picks_robots_meta,
    _sitemap_loc_is_canonical,
    app,
)


class TestPicksRobotsMeta(unittest.TestCase):
    def test_hub_always_indexable(self):
        self.assertEqual(_picks_robots_meta(filter_date=None, grouped_predictions={}), _PICKS_ROBOTS_INDEX)
        self.assertEqual(
            _picks_robots_meta(filter_date=None, grouped_predictions={'2026-07-29': [{'x': 1}]}),
            _PICKS_ROBOTS_INDEX,
        )

    def test_empty_dated_noindex(self):
        self.assertEqual(
            _picks_robots_meta(filter_date='2026-07-02', grouped_predictions={}),
            _PICKS_ROBOTS_NOINDEX,
        )
        self.assertEqual(
            _picks_robots_meta(filter_date='2026-07-02', grouped_predictions={'2026-07-02': []}),
            _PICKS_ROBOTS_NOINDEX,
        )

    def test_dated_golf_always_noindex(self):
        self.assertEqual(
            _picks_robots_meta(
                sport='GOLF',
                filter_date='2026-07-02',
                grouped_predictions={'2026-07-02': [{'x': 1}]},
            ),
            _PICKS_ROBOTS_NOINDEX,
        )

    def test_dated_with_games_indexable(self):
        self.assertEqual(
            _picks_robots_meta(
                sport='MLB',
                filter_date='2026-07-29',
                grouped_predictions={'2026-07-29': [{'home_team_id': 'A', 'away_team_id': 'B'}]},
            ),
            _PICKS_ROBOTS_INDEX,
        )


class TestPicksCanonical(unittest.TestCase):
    def test_empty_dated_points_at_hub(self):
        with app.test_request_context('/golf-picks-july-2-2026'):
            url = _picks_page_canonical_url(
                sport='GOLF',
                filter_date='2026-07-02',
                grouped_predictions={},
            )
        self.assertEqual(url, 'https://predictionlab.io/golf-picks')

    def test_dated_golf_canonical_hub_even_with_games(self):
        with app.test_request_context('/golf-picks-july-2-2026'):
            url = _picks_page_canonical_url(
                sport='GOLF',
                filter_date='2026-07-02',
                grouped_predictions={'2026-07-02': [{'x': 1}]},
            )
        self.assertEqual(url, 'https://predictionlab.io/golf-picks')

    def test_dated_with_games_self_canonical(self):
        with app.test_request_context('/mlb-picks-july-29-2026'):
            url = _picks_page_canonical_url(
                sport='MLB',
                filter_date='2026-07-29',
                grouped_predictions={'2026-07-29': [{'home_team_id': 'A'}]},
            )
        self.assertEqual(url, 'https://predictionlab.io/mlb-picks-july-29-2026')


class TestSitemapAndBlogRedirect(unittest.TestCase):
    def test_sitemap_filters_and_excludes_thin_dated_urls(self):
        client = app.test_client()
        resp = client.get('/sitemap.xml')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # Thin Trends blog is noindex — keep it out of the sitemap.
        self.assertNotIn('/blog</loc>', body)
        self.assertNotIn('/auth/', body)
        self.assertNotIn('/share/', body)
        self.assertNotIn('/sport/', body)
        self.assertIn('https://predictionlab.io/golf-picks</loc>', body)
        self.assertNotIn('golf-picks-july-', body)
        self.assertNotIn('tennis-picks-july-', body)
        self.assertNotIn('ufc-picks-july-', body)
        # Calendar sports still get a short dated window when live
        today = datetime.now()
        month = today.strftime('%B').lower()
        mlb_today = f'mlb-picks-{month}-{today.day}-{today.year}'
        if 'MLB' in SEASON_CALENDAR:
            if mlb_today in body or 'mlb-picks-' in body:
                self.assertIn(mlb_today, body)
        self.assertTrue(_sitemap_loc_is_canonical('https://predictionlab.io/mlb-picks'))
        self.assertFalse(_sitemap_loc_is_canonical('https://www.predictionlab.io/mlb-picks'))
        self.assertFalse(_sitemap_loc_is_canonical('https://predictionlab.io/sport/MLB/predictions'))

    def test_blog_slug_301_to_archive(self):
        client = app.test_client()
        resp = client.get(
            '/blog/lottery-results-today-google-trends-betting-angle-2026-06-17',
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers.get('Location'), 'https://predictionlab.io/blog')
        self.assertIn('noindex', (resp.headers.get('X-Robots-Tag') or '').lower())

    def test_robots_keeps_auth_and_share_blocked(self):
        client = app.test_client()
        resp = client.get('/robots.txt')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Disallow: /auth/', body)
        self.assertIn('Disallow: /share/', body)


if __name__ == '__main__':
    unittest.main()
