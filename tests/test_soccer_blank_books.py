"""Soccer: hide blank Books ML; copy published PL onto blank XSharp."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_ui_fixup import apply_soccer_picks_fixups  # noqa: E402


CARD = """
<div class="game-card-stack" data-pick-card
    data-home="Arsenal" data-away="Chelsea"
    data-pl-spread="Arsenal -0.5" data-xs-spread=""
    data-m-edge="50.0">
  <div class="ml-line face-books-ml">
    <span class="ml-src books">Books</span>
    <span class="ml-num dog">+310</span>
  </div>
  <div class="ml-line face-books-ml">
    <span class="ml-src books">Books</span>
    <span class="ml-num">—</span>
  </div>
  <div class="ml-line face-pl-ml">
    <span class="ml-src pl">Prediction Lab</span>
    <span class="ml-num fav">-140</span>
  </div>
  <table>
    <tr>
      <td class="market-k">Spread</td>
      <td class="val-books">Arsenal -0.5</td>
      <td class="val-pl">Arsenal -0.5</td>
      <td class="val-xs">—</td>
    </tr>
    <tr>
      <td class="market-k">Total</td>
      <td class="val-books">2.5</td>
      <td class="val-pl">2.5</td>
      <td class="val-xs">—</td>
    </tr>
  </table>
  <div class="pc-name">Edge</div>
  <div class="pc-val">50.0%</div>
  <div class="pc-name">XSharp</div>
  <div class="pc-val">61.0%</div>
  <div class="pc-name">Sharp Consensus</div>
  <div class="pc-val">58.0%</div>
</div>
"""


def test_hide_blank_books_and_fill_xs_from_pl():
    out = apply_soccer_picks_fixups(CARD + CARD)
    assert out.count('class="ml-src books"') == 2
    assert "+310" in out
    assert out.count('ml-num">—') == 0
    assert "Prediction Lab" in out and "-140" in out
    assert out.count("val-xs\">Arsenal -0.5</td>") == 2
    assert out.count("val-xs\">2.5</td>") == 2
    assert "50.0%" not in out
    assert "61.0%" in out
    assert out.count("pc-val\">58.0%") + out.count("pc-val\">61.0%") >= 4
