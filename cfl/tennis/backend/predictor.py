"""Tennis match prediction service."""
from __future__ import annotations

from typing import Any, Dict, List

from shared.db import connect
from tennis.database.paths import DB_PATH
from tennis.database.init_db import seed
from tennis.ml import TennisModel


class TennisPredictor:
    def __init__(self) -> None:
        if not DB_PATH.exists():
            seed()
        self.model = TennisModel()
        self.model.fit_demo()

    def list_predictions(self) -> List[dict]:
        with connect(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT p.*, m.player_a, m.player_b, m.surface, m.tournament,
                       m.match_date, m.status, m.winner
                FROM predictions p
                JOIN matches m ON m.match_id = p.match_id
                ORDER BY m.match_date DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def predict_match(self, a: str, b: str, surface: str = "hard") -> Dict[str, Any]:
        feats = [30.0, 25.0, 0.4, 0.2, 0.1, 0.05, 3]
        probs = self.model.predict_row(feats)
        win_a = probs.get("ensemble", 0.55)
        return {
            "player_a": a,
            "player_b": b,
            "surface": surface,
            "win_prob_a": round(win_a, 4),
            "proj_sets_a": 2.0 if win_a > 0.55 else 1.4,
            "proj_sets_b": 0.7 if win_a > 0.55 else 1.6,
            "proj_games_total": 23.5,
            "straight_sets_prob": round(max(0.15, win_a - 0.2), 3),
            "ou_games_line": 22.5,
            "ou_pick": "OVER" if win_a < 0.6 else "UNDER",
            "confidence": round(abs(win_a - 0.5) * 2, 3),
            "note": "No ESPN book odds UI for Tennis sandbox.",
        }
