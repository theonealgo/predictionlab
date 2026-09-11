"""NCAAF placeholder Edge 50% and blank Odds & Lines fill from published proj/Elo."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from team_results_charts import (  # noqa: E402
    _fill_ncaaf_card_gaps,
    _ncaaf_fmt_named_spread,
    _ncaaf_parse_proj,
    ncaaf_live_elo_home_pct,
)


FAMU_CARD = """
<div class="game-card-stack" data-pick-card
    data-away="Florida A&amp;M Rattlers"
    data-home="Miami Hurricanes"
    data-pl-spread="Miami Hurricanes -5.5"
    data-xs-spread=""
    data-m-edge="50.0"
    data-pl-proj="Florida A&amp;amp;M Rattlers 21.5 – Miami Hurricanes 26.5"
    data-xs-proj="Florida A&amp;amp;M Rattlers 21.5 – Miami Hurricanes 26.5">
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
  <div class="pc-name">Edge</div>
  <div class="pc-val">50.0%</div>
  <div class="pc-side home">Miami Hurricanes</div>
</div>
"""


def test_parse_famu_proj():
    pts = _ncaaf_parse_proj(
        "Florida A&amp;amp;M Rattlers 21.5 – Miami Hurricanes 26.5",
        "Miami Hurricanes",
        "Florida A&M Rattlers",
    )
    assert pts == (26.5, 21.5)
    assert _ncaaf_fmt_named_spread("Miami Hurricanes", "Florida A&M Rattlers", 5.0) == (
        "Miami Hurricanes -5"
    )


def test_fill_famu_blank_lines_and_edge():
    html = _fill_ncaaf_card_gaps(FAMU_CARD + FAMU_CARD)
    assert "val-xs\">Miami Hurricanes -5</td>" in html
    assert "val-pl\">48</td>" in html
    assert html.count("val-xs\">48</td>") == 2
    assert 'data-xs-spread="Miami Hurricanes -5"' in html
    assert "50.0%" not in html
    live = ncaaf_live_elo_home_pct("Miami Hurricanes", "Florida A&M Rattlers")
    assert live is not None and abs(live - 50.0) >= 1.0
    assert f"{live:.1f}%" in html
    assert f'data-m-edge="{live:.1f}"' in html
