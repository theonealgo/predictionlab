"""Soccer results/picks ⓘ controls must expose real tooltip text."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_ui_fixup import apply_soccer_info_tooltips, apply_soccer_results_fixups


_SEASON_SNIPPET = """
<!DOCTYPE html><html><head><title>Soccer Results</title></head><body>
<div style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">
    <h2>🏆 Season Performance — AFC Champions League Elite</h2>
    <div class="roi-grid">
        <div>
            <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎯 Moneyline (Sharp Consensus)</div>
            <div style="font-size:2em;font-weight:bold;color:#00C076;">100.0%</div>
            <div style="font-size:0.85em;opacity:0.9;color:#334155;">6-0 <span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>
        </div>
        <div>
            <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">📈 Spread (Prediction Lab)</div>
            <div style="font-size:2em;font-weight:bold;color:#D93025;">16.7%</div>
            <div style="font-size:0.85em;opacity:0.9;color:#334155;">1-5 <span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>
        </div>
        <div>
            <div style="font-size:0.8em;opacity:0.85;margin-bottom:4px;color:#334155;">🎲 O/U (XSharp)</div>
            <div style="font-size:2em;font-weight:bold;color:#00C076;">66.7%</div>
            <div style="font-size:0.85em;opacity:0.9;color:#334155;">2-1 <span title="Number of Games" style="cursor:help;opacity:0.7;">ⓘ</span></div>
        </div>
    </div>
    <div class="pick-conf-title">Pick Confidence</div>
    <div class="pc-name">Efficiency</div>
</body></html>
"""

_BTN = re.compile(
    r"<button\b[^>]*\bclass=\"[^\"]*\bpl-info-btn\b[^\"]*\"[^>]*>",
    flags=re.I,
)


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return (m.group(1) if m else "").strip()


def test_soccer_results_info_icons_have_nonempty_tooltip_text():
    out = apply_soccer_results_fixups(_SEASON_SNIPPET, league="afc-champions-league-elite")
    buttons = _BTN.findall(out)
    assert len(buttons) >= 3, f"expected season ⓘ buttons, got {len(buttons)}"
    tips = []
    for btn in buttons:
        tip = _attr(btn, "data-tip") or _attr(btn, "data-pl-info-tip") or _attr(btn, "aria-label")
        assert tip, f"empty tooltip on {btn}"
        assert "Number of Games" not in tip
        assert len(tip) >= 24
        tips.append(tip)
    joined = " ".join(tips)
    assert "moneyline" in joined.lower()
    assert "spread" in joined.lower()
    assert "over/under" in joined.lower() or "total" in joined.lower()
    assert "pl-info-tips.js" in out
    assert "pl-info-tips.css" in out
    # Decorative native-title-only spans must be gone.
    assert not re.search(r'<span[^>]*title="Number of Games"[^>]*>\s*ⓘ', out)


def test_soccer_info_tooltips_idempotent():
    once = apply_soccer_info_tooltips(_SEASON_SNIPPET, kind="results")
    twice = apply_soccer_info_tooltips(once, kind="results")
    assert twice.count('id="pl-info-tips-css"') == 1
    assert twice.count("pl-info-tips.js") == 1
    assert len(_BTN.findall(twice)) == len(_BTN.findall(once))
