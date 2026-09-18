"""Blank NFL HTML must never count as a usable cached picks page."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import NHL77FINAL as nhl  # noqa: E402


def test_blank_nfl_banner_is_not_usable_html():
    html = '''
    <html><body class="sport-nfl">
    <div class="no-data">No predictions available for NFL</div>
    </body></html>
    '''
    assert not nhl._picks_page_html_usable("NFL", html)


def test_usable_nfl_html_needs_game_cards():
    html = '''
    <html><head><link href="research-theme.css"></head>
    <body class="sport-nfl" data-sport="NFL">
    <style>.team-slot {width:1px}</style>
    <div class="picks-view-controls"></div>
    <div class="game-card"></div>
    </body></html>
    '''
    assert nhl._picks_page_html_usable("NFL", html)


def test_page_cache_helper_uses_v39_not_v28():
    import inspect
    src = inspect.getsource(nhl._cached_usable_picks_html)
    assert "v39" in src
    assert "v28" not in src


def test_weekly_disk_cache_outlives_a_deploy():
    assert nhl._PREDICTIONS_DISK_MAX_AGE >= 14 * 24 * 3600
