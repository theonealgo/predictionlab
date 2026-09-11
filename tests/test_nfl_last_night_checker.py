"""Checker must fail the Aug 29 NFL last-night board the owner pasted."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT))

from chart_shape import (
    blank_moneyline_model_issues,
    consensus_empty_vs_tally_issues,
    last_night_window_mismatch_issues,
    pl_vs_books_empty_vs_tally_issues,
    results_math_issues,
    six_model_chart_issues,
)

# Copied from the live Last Night / consensus / PL vs Sportsbook board.
OWNER_NFL_AUG29 = """
Last Night's NFL Results — 2026-08-29 (2 games)
Share
MONEYLINE
⭐ Grinder2
—
—
🎯 Takedown
—
—
📊 Edge
50.0%
1-1
🤖 XSharp
50.0%
1-1
🏆 Sharp Consensus
50.0%
1-1
⚡ Efficiency
—
—
📈 Spread
50.0%
1-1
🎲 Over/Under
100.0%
2-0
Last 7 Days NFL Results — 2026-08-23 to 2026-08-29 (17 games)
MONEYLINE
⭐ Grinder2
—
—
🎯 Takedown
—
—
📊 Edge
52.9%
9-8
🤖 XSharp
58.8%
10-7
🏆 Sharp Consensus
58.8%
10-7
⚡ Efficiency
—
—
Moneyline Accuracy by Model
⭐ Grinder2
—
—
🎯 Takedown
—
—
📊 Edge
59.0%
59-41
🤖 XSharp
56.0%
56-44
🏆 Sharp Consensus
55.0%
55-45
⚡ Efficiency
—
—
Consensus Based Betting Records
Moneyline on the pregame majority among the 4 live models. Each row is one dissent combination (model(s) that broke from the majority). 0-0 means that combination had no graded games in the window. Even splits are omitted.
Agreement	Last night (2026-08-29)	Past 7 days	Past 30 days
4/4 unanimous	0-0	0-0	0-0
3/4 — all but Edge	0-0	0-0	0-0
3/4 — all but Efficiency	0-0	0-0	0-0
3/4 — all but XSharp	0-0	0-0	0-0
PL vs Sportsbook
Compares the Prediction Lab favorite with the sportsbook favorite.
Signal	Last night (2026-08-29)	Past 7 days	Past 30 days
Books favorite	0-2 (0%)
7-9-1 (44%)
15-22-1 (41%)
PL favorite	1-1 (50%)
10-6-1 (62%)
20-17-1 (54%)
PL vs Books disagree	1-0 (100%)
6-3-1 (67%)
11-6-1 (65%)
PL and Books agree	0-1 (0%)
4-3 (57%)
9-11 (45%)
"""


def test_owner_aug29_board_fails():
    six = six_model_chart_issues(OWNER_NFL_AUG29, "NFL")
    assert any("4-model" in x or "4/4" in x for x in six)
    blank = blank_moneyline_model_issues(OWNER_NFL_AUG29)
    assert blank
    assert any("Grinder2" in x and "Takedown" in x and "Efficiency" in x for x in blank)
    empty = consensus_empty_vs_tally_issues(OWNER_NFL_AUG29)
    assert empty
    assert any("0-0" in x and "2 games" in x for x in empty)
    # PL vs books has real last-night records on this paste.
    assert pl_vs_books_empty_vs_tally_issues(OWNER_NFL_AUG29) == []
    assert last_night_window_mismatch_issues(OWNER_NFL_AUG29) == []
    math = results_math_issues(OWNER_NFL_AUG29)
    assert any("Grinder2" in x for x in math)
    assert any("0-0" in x for x in math)
