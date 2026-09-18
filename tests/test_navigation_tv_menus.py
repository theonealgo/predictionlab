"""Hamburger TV_MENUS must parse; a failed regex is not a missing Sports menu."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
sys.path.insert(0, str(QA))
sys.path.insert(0, str(ROOT))

from site_checker import NavigationAuditor  # noqa: E402


SAMPLE = """
<script>
var TV_MENUS={
  picks:{title:'Picks & Predictions',items:[
    {l:'NBA',h:'/nba-picks'},
    {l:'NFL',h:'/nfl-picks',live:1},
    {l:'MLB',h:'/mlb-picks',live:1}
  ]},
  props:{title:'Props & Models',items:[{l:'Player Props',h:'/player-props'},{l:'Model Performance',h:'/performance'}]},
  results:{title:'Results & Tracking',items:[
    {l:'All Sports Results',h:'/all-sports-results'}
  ]},
  company:{title:'Company',items:[{l:'Plans & Pricing',h:'/plans'},{l:'Blog',h:'/blog'}]}
};
</script>
"""


def test_parse_tv_menus_multiline_object():
    nav = NavigationAuditor.__new__(NavigationAuditor)
    sections = nav._parse_menu_sections(SAMPLE)
    hrefs = {h for items in sections.values() for _, h in items}
    assert "/nfl-picks" in hrefs
    assert "/player-props" in hrefs
    assert "/plans" in hrefs
    assert "picks" in sections
    assert "tools" not in sections


def test_old_oneline_regex_does_not_see_real_menus():
    import re
    assert not re.search(r"var TV_MENUS=(\{.*?\}\});", SAMPLE)
