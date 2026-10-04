#!/usr/bin/env python3
"""
Weighted Average Total System (NBA-first)
- Pure functions with no global state
- Expects caller to supply last-3 non-OT games per team (team_score, opp_score)
"""
from typing import List, Dict, Tuple, Optional

def _three_game_avg(team_games: List[Tuple[int, int]], use_opponent_component: bool = True) -> Optional[float]:
    """
    Compute a team's 3-game average scoring.
    team_games: list of (team_score, opponent_score) for last 3 games (no OT scoring)
    If fewer than 3, return None.
    If use_opponent_component=True, blend team scoring avg with opponents-allowed avg for stability.
    """
    if team_games is None or len(team_games) < 3:
        return None

    # Team scoring average across last 3
    team_points = sum(g[0] for g in team_games) / 3.0

    if not use_opponent_component:
        return float(team_points)

    # Opponents' points allowed proxy: opponents' points scored vs this team
    opp_points = sum(g[1] for g in team_games) / 3.0
    # Average the two for a more stable per-team baseline
    return float((team_points + opp_points) / 2.0)

def _trend_over_count(projected_total: float, team_games: List[Tuple[int, int]]) -> int:
    """
    Count how many of a team's last 3 game totals went over the projected_total.
    team_games: list of (team_score, opponent_score)
    Returns 0-3.
    """
    count = 0
    for team_pts, opp_pts in team_games[:3]:
        if (team_pts + opp_pts) > projected_total:
            count += 1
    return count

def compute_weighted_total_recommendation(
    teamA_name: str,
    teamB_name: str,
    teamA_last3: List[Tuple[int, int]],
    teamB_last3: List[Tuple[int, int]],
    vegas_total: Optional[float],
    use_opponent_component: bool = True,
) -> Dict:
    """
    Wrapper producing the final output dict.

    Inputs:
      - teamA_name, teamB_name
      - teamA_last3/teamB_last3: [(team_score, opp_score), ...] last 3 non-OT games each
      - vegas_total (float or None)

    Output:
      {
        "projected_total": float,
        "teamA_over_count": int,
        "teamB_over_count": int,
        "combined_over_count": int,
        "recommended_bet": "OVER"|"UNDER"|"NO BET"|"insufficient data",
        "vegas_total": float|None,
        "difference_from_vegas": float|None
      }
    """
    # Validate inputs
    if (teamA_last3 is None or len(teamA_last3) < 3) or (teamB_last3 is None or len(teamB_last3) < 3):
        return {
            "projected_total": None,
            "teamA_over_count": 0,
            "teamB_over_count": 0,
            "combined_over_count": 0,
            "recommended_bet": "insufficient data",
            "vegas_total": vegas_total,
            "difference_from_vegas": None,
        }

    a_avg = _three_game_avg(teamA_last3, use_opponent_component)
    b_avg = _three_game_avg(teamB_last3, use_opponent_component)

    if a_avg is None or b_avg is None:
        return {
            "projected_total": None,
            "teamA_over_count": 0,
            "teamB_over_count": 0,
            "combined_over_count": 0,
            "recommended_bet": "insufficient data",
            "vegas_total": vegas_total,
            "difference_from_vegas": None,
        }

    projected_total = (a_avg + b_avg) / 2.0

    # Trend check counts
    a_over = _trend_over_count(projected_total, teamA_last3)
    b_over = _trend_over_count(projected_total, teamB_last3)
    combined = a_over + b_over

    # Recommendation logic
    if combined >= 4:
        rec = "OVER"
    elif combined <= 2:
        rec = "UNDER"
    else:
        rec = "NO BET"

    diff = None if vegas_total is None else round(projected_total - float(vegas_total), 1)

    return {
        "projected_total": round(projected_total, 1),
        "teamA_over_count": int(a_over),
        "teamB_over_count": int(b_over),
        "combined_over_count": int(combined),
        "recommended_bet": rec,
        "vegas_total": None if vegas_total is None else float(vegas_total),
        "difference_from_vegas": diff,
    }
