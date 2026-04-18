#!/usr/bin/env python3
"""
h2h_total_projector.py
----------------------
Projected Total based on the last 10 head-to-head games between Team A and Team B.

Spec:
  1. Input last-10 H2H game scores (Team A points, Team B points).
  2. Compute average points for Team A and Team B across the 10 games.
  3. Projected Total = avg_A + avg_B.
  4. Prompt for current xsharp (Vegas) Over/Under line.
  5. Compare Projected Total to the line -> OVER/UNDER.
  6. Trend: for each of the 10 games check if combined score was OVER/UNDER the Vegas line.
  7. Output Projected Total and trend record (e.g., "5-5 Over against 219.5").

Usage (interactive):
  python3 h2h_total_projector.py

Usage (scripted, pass scores + line via CLI):
  python3 h2h_total_projector.py \
      --team-a "Phoenix Suns" --team-b "Golden State Warriors" \
      --scores 111,96 97,101 116,119 99,98 107,118 95,133 130,105 105,109 113,105 112,113 \
      --line 219.5
"""

from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass
from typing import List, Tuple


# ────────────────────────────────────────────────────────────────────────────
# Data structures
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class H2HGame:
    team_a_score: float
    team_b_score: float

    @property
    def total(self) -> float:
        return self.team_a_score + self.team_b_score


@dataclass
class H2HReport:
    team_a: str
    team_b: str
    games: List[H2HGame]
    vegas_line: float

    @property
    def avg_a(self) -> float:
        return sum(g.team_a_score for g in self.games) / len(self.games) if self.games else 0.0

    @property
    def avg_b(self) -> float:
        return sum(g.team_b_score for g in self.games) / len(self.games) if self.games else 0.0

    @property
    def projected_total(self) -> float:
        return self.avg_a + self.avg_b

    @property
    def pick(self) -> str:
        if self.projected_total > self.vegas_line:
            return "OVER"
        if self.projected_total < self.vegas_line:
            return "UNDER"
        return "PUSH"

    def trend_counts(self) -> Tuple[int, int, int]:
        """Return (overs, unders, pushes) against the Vegas line."""
        overs = sum(1 for g in self.games if g.total > self.vegas_line)
        unders = sum(1 for g in self.games if g.total < self.vegas_line)
        pushes = sum(1 for g in self.games if g.total == self.vegas_line)
        return overs, unders, pushes

    def trend_label(self) -> str:
        """Format matches user spec: 'overs-unders Over' against the line.
        Record is always read as (overs)-(unders); the trailing 'Over' is
        the descriptor for what the first number counts.
        """
        overs, unders, pushes = self.trend_counts()
        record = f"{overs}-{unders}"
        if pushes:
            record = f"{overs}-{unders}-{pushes}"
        return f"{record} Over"


# ────────────────────────────────────────────────────────────────────────────
# Projection
# ────────────────────────────────────────────────────────────────────────────
def project(report: H2HReport) -> dict:
    overs, unders, pushes = report.trend_counts()
    return {
        "team_a": report.team_a,
        "team_b": report.team_b,
        "n_games": len(report.games),
        "avg_team_a": round(report.avg_a, 2),
        "avg_team_b": round(report.avg_b, 2),
        "projected_total": round(report.projected_total, 2),
        "vegas_line": report.vegas_line,
        "model_pick": report.pick,
        "overs_vs_line": overs,
        "unders_vs_line": unders,
        "pushes_vs_line": pushes,
        "trend": report.trend_label(),
    }


# ────────────────────────────────────────────────────────────────────────────
# Rendering helpers
# ────────────────────────────────────────────────────────────────────────────
def render(report: H2HReport) -> str:
    res = project(report)
    lines = []
    lines.append(f"H2H Total Projection — {res['team_a']} vs {res['team_b']}")
    lines.append("=" * 60)
    lines.append(f"Games analysed:      {res['n_games']}")
    lines.append(f"{res['team_a']:<30}avg {res['avg_team_a']}")
    lines.append(f"{res['team_b']:<30}avg {res['avg_team_b']}")
    lines.append(f"Projected Total:     {res['projected_total']}")
    lines.append(f"xsharp (Vegas) line: {res['vegas_line']}")
    lines.append(f"Model pick:          {res['model_pick']}")
    lines.append("")
    lines.append("Per-game totals vs line:")
    for i, g in enumerate(report.games, start=1):
        flag = "OVER" if g.total > report.vegas_line else ("UNDER" if g.total < report.vegas_line else "PUSH")
        lines.append(
            f"  {i:>2}. {report.team_a} {int(g.team_a_score):>3} — "
            f"{int(g.team_b_score):>3} {report.team_b}   "
            f"total {g.total:>5.1f}   {flag}"
        )
    lines.append("")
    lines.append(
        f"Trend: In the last {res['n_games']} games these teams are "
        f"{res['trend']} against the xsharp line of {res['vegas_line']}."
    )
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Interactive input
# ────────────────────────────────────────────────────────────────────────────
def _ask_float(prompt: str) -> float:
    while True:
        raw = input(prompt).strip()
        try:
            return float(raw)
        except ValueError:
            print("  (enter a number)")


def run_interactive() -> int:
    print("H2H Total Projector — last 10 head-to-head games")
    team_a = input("Team A name: ").strip() or "Team A"
    team_b = input("Team B name: ").strip() or "Team B"

    games: List[H2HGame] = []
    print(f"Enter the 10 H2H scores (oldest or newest first — order does not matter).")
    for i in range(1, 11):
        a = _ask_float(f"  Game {i:>2}  {team_a} score: ")
        b = _ask_float(f"  Game {i:>2}  {team_b} score: ")
        games.append(H2HGame(a, b))

    line = _ask_float("Current xsharp (Vegas) Over/Under line: ")

    report = H2HReport(team_a=team_a, team_b=team_b, games=games, vegas_line=line)
    print()
    print(render(report))
    return 0


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def _parse_score_pair(raw: str) -> H2HGame:
    try:
        a, b = raw.split(",")
        return H2HGame(float(a), float(b))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"Expected pair like '111,96', got {raw!r}"
        )


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Project total score from last 10 H2H games and compare vs xsharp line."
    )
    parser.add_argument("--team-a", default="Team A", help="Team A name")
    parser.add_argument("--team-b", default="Team B", help="Team B name")
    parser.add_argument(
        "--scores",
        nargs="*",
        metavar="A,B",
        type=_parse_score_pair,
        help="Space-separated pairs of scores, e.g. 111,96 97,101 ...",
    )
    parser.add_argument(
        "--line",
        type=float,
        help="xsharp / Vegas Over-Under line to compare against.",
    )
    args = parser.parse_args(argv)

    if not args.scores and args.line is None:
        return run_interactive()

    if not args.scores or args.line is None:
        parser.error("--scores and --line are both required when using CLI mode.")

    report = H2HReport(
        team_a=args.team_a,
        team_b=args.team_b,
        games=list(args.scores),
        vegas_line=args.line,
    )
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
