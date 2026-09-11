"""NFL placeholder Edge 50% fill from the published PL spread."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from team_results_charts import _fill_placeholder_edge  # noqa: E402


JAX_CARD = """
<div class="game-card-stack" data-pick-card
    data-away="Cleveland Browns"
    data-home="Jacksonville Jaguars"
    data-pl-spread="Jacksonville Jaguars -12.5"
    data-m-edge="50.0">
  <table>
    <tr><td class="market-k">Spread</td>
        <td class="val-books">Jacksonville Jaguars -7.5</td>
        <td class="val-pl">Jacksonville Jaguars -12.5</td>
        <td class="val-xs">Jacksonville Jaguars -12.5</td></tr>
  </table>
  <div class="pc-name">Edge</div>
  <div class="pc-val">50.0%</div>
  <div class="pc-side home">Jacksonville Jaguars</div>
</div>
"""


def test_nfl_edge_from_pl_spread():
    html = _fill_placeholder_edge(JAX_CARD + JAX_CARD, "NFL")
    assert "50.0%" not in html
    assert 'data-m-edge="50.0"' not in html
    assert html.count("pc-val") == 2
    assert "Jaguars" in html
