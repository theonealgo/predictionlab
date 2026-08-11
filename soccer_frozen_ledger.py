"""Frozen pre-kickoff soccer prediction ledger.

Immutable after predicted_at: prediction fields never regenerate.
Result fields (scores/actual) may be attached only after kickoff.

Additive table — does not alter Efficiency or existing predictions schema.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_DB_NAME = "soccer_frozen_ledger.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS soccer_frozen_predictions (
    game_id TEXT NOT NULL,
    model TEXT NOT NULL,
    game_date TEXT,
    league TEXT,
    home_team_id TEXT,
    away_team_id TEXT,
    predicted_at TEXT NOT NULL,
    kickoff_at TEXT,
    train_cutoff TEXT,
    train_window INTEGER,
    model_version TEXT,
    hw REAL,
    dp REAL,
    aw REAL,
    binary_home REAL,
    pick_3way TEXT,
    pick_2way TEXT,
    book_spread REAL,
    home_ml REAL,
    away_ml REAL,
    market_home_p REAL,
    model_conf REAL,
    model_mkt_gap REAL,
    home_score INTEGER,
    away_score INTEGER,
    actual TEXT,
    PRIMARY KEY (game_id, model)
);
"""


def _default_db_path() -> str:
    env = os.environ.get("SOCCER_FROZEN_LEDGER_DB")
    if env:
        return env
    # Prefer beside the sports DB when DATABASE is set by the app
    base = os.environ.get("DATABASE") or os.environ.get("SPORTS_DB")
    if base:
        return os.path.join(os.path.dirname(os.path.abspath(base)), DEFAULT_DB_NAME)
    here = os.path.dirname(os.path.abspath(__file__))
    # Local prod/work2 convention: sports_predictions_original.db at project root
    sports_db = os.path.join(here, "sports_predictions_original.db")
    if os.path.isfile(sports_db):
        return os.path.join(here, DEFAULT_DB_NAME)
    return os.path.join(here, "data", DEFAULT_DB_NAME)


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or _default_db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn


def _pick_3way(hw: float, dp: float, aw: float) -> str:
    return max((("home", hw), ("draw", dp), ("away", aw)), key=lambda x: x[1])[0]


def _pick_2way(hw: float, aw: float) -> str:
    return "home" if hw >= aw else "away"


def _threeway_from_binary_draw(binary: Optional[float], draw: Optional[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if binary is None or draw is None:
        return None, None, None
    bh = float(binary)
    dp = float(draw)
    if bh > 1.0:
        bh /= 100.0
    if dp > 1.0:
        dp /= 100.0
    hw = max(0.0, bh - 0.5 * dp)
    aw = max(0.0, 1.0 - hw - dp)
    s = hw + dp + aw
    if s <= 0:
        return None, None, None
    return hw / s, dp / s, aw / s


def rows_from_soccer_pred(
    *,
    game_id: str,
    game_date: Optional[str],
    league: Optional[str],
    home_team_id: Optional[str],
    away_team_id: Optional[str],
    soccer_pred: dict,
    kickoff_at: Optional[str] = None,
    train_cutoff: Optional[str] = None,
    train_window: Optional[int] = None,
    book_spread: Optional[float] = None,
    home_ml: Optional[float] = None,
    away_ml: Optional[float] = None,
    market_home_p: Optional[float] = None,
) -> List[dict]:
    """Build freeze rows for the four base models + consensus from live soccer_pred."""
    now = datetime.now(timezone.utc).isoformat()
    td_ver = soccer_pred.get("takedown_model_version") or "proto_v1"
    cons_ver = soccer_pred.get("consensus_model_version") or "equal_weight_v1"

    specs = [
        ("Grinder2", soccer_pred.get("poisson_xg_prob"), soccer_pred.get("glicko2_draw_prob"), "current"),
        ("Takedown", soccer_pred.get("markov_prob"), soccer_pred.get("trueskill_draw_prob"), td_ver),
        ("Edge", soccer_pred.get("elo_prob"), soccer_pred.get("elo_draw_prob"), "current"),
        ("XSharp", soccer_pred.get("poisson_reg_prob"), soccer_pred.get("xgb_draw_prob"), "current"),
        ("SharpConsensus", soccer_pred.get("ensemble_prob"), soccer_pred.get("ensemble_draw_prob") or soccer_pred.get("draw_prob"), cons_ver),
    ]
    out = []
    for model, binary, draw, ver in specs:
        # Prefer explicit 3-way when present (Takedown / Consensus)
        if model == "Takedown" and soccer_pred.get("takedown_home_win") is not None:
            hw = float(soccer_pred["takedown_home_win"])
            dp = float(soccer_pred["takedown_draw"])
            aw = float(soccer_pred["takedown_away_win"])
        elif model == "SharpConsensus" and soccer_pred.get("ensemble_home_win") is not None:
            hw = float(soccer_pred["ensemble_home_win"])
            dp = float(soccer_pred["ensemble_draw"])
            aw = float(soccer_pred["ensemble_away_win"])
        else:
            hw, dp, aw = _threeway_from_binary_draw(binary, draw)
        if hw is None or binary is None:
            continue
        bin_h = float(binary)
        if bin_h > 1.0:
            bin_h /= 100.0
        conf = max(hw, dp, aw)
        gap = (bin_h - float(market_home_p)) if market_home_p is not None else None
        out.append({
            "game_id": game_id,
            "model": model,
            "game_date": (str(game_date)[:10] if game_date else None),
            "league": league,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "predicted_at": now,
            "kickoff_at": kickoff_at or (str(game_date)[:10] if game_date else None),
            "train_cutoff": train_cutoff,
            "train_window": train_window,
            "model_version": ver,
            "hw": hw,
            "dp": dp,
            "aw": aw,
            "binary_home": bin_h,
            "pick_3way": _pick_3way(hw, dp, aw),
            "pick_2way": _pick_2way(hw, aw),
            "book_spread": book_spread,
            "home_ml": home_ml,
            "away_ml": away_ml,
            "market_home_p": market_home_p,
            "model_conf": conf,
            "model_mkt_gap": gap,
        })
    return out


def persist_pre_kickoff(
    rows: Sequence[dict],
    db_path: Optional[str] = None,
) -> int:
    """Insert freeze rows. Existing (game_id, model) rows are NEVER overwritten."""
    if not rows:
        return 0
    conn = connect(db_path)
    inserted = 0
    try:
        for r in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO soccer_frozen_predictions (
                    game_id, model, game_date, league, home_team_id, away_team_id,
                    predicted_at, kickoff_at, train_cutoff, train_window, model_version,
                    hw, dp, aw, binary_home, pick_3way, pick_2way,
                    book_spread, home_ml, away_ml, market_home_p, model_conf, model_mkt_gap
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r["game_id"], r["model"], r.get("game_date"), r.get("league"),
                    r.get("home_team_id"), r.get("away_team_id"),
                    r["predicted_at"], r.get("kickoff_at"), r.get("train_cutoff"),
                    r.get("train_window"), r.get("model_version"),
                    r.get("hw"), r.get("dp"), r.get("aw"), r.get("binary_home"),
                    r.get("pick_3way"), r.get("pick_2way"),
                    r.get("book_spread"), r.get("home_ml"), r.get("away_ml"),
                    r.get("market_home_p"), r.get("model_conf"), r.get("model_mkt_gap"),
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def attach_results(
    game_id: str,
    home_score: int,
    away_score: int,
    db_path: Optional[str] = None,
) -> int:
    """Fill result fields only. Never touches prediction columns."""
    if home_score == away_score:
        actual = "draw"
    elif home_score > away_score:
        actual = "home"
    else:
        actual = "away"
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE soccer_frozen_predictions
               SET home_score = ?, away_score = ?, actual = ?
             WHERE game_id = ?
               AND (home_score IS NULL OR actual IS NULL)
            """,
            (int(home_score), int(away_score), actual, game_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_frozen_models(
    game_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, dict]:
    """Return model -> freeze row for a game (prediction fields immutable)."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM soccer_frozen_predictions WHERE game_id = ?",
            (game_id,),
        ).fetchall()
        return {r["model"]: dict(r) for r in rows}
    finally:
        conn.close()


def frozen_to_soccer_pred_overlay(frozen: Dict[str, dict]) -> Optional[dict]:
    """Map ledger rows back onto soccer_pred-like keys for grading/display."""
    if not frozen:
        return None
    out: Dict[str, Any] = {}
    mapping = {
        "Grinder2": ("poisson_xg_prob", "glicko2_draw_prob"),
        "Takedown": ("markov_prob", "trueskill_draw_prob"),
        "Edge": ("elo_prob", "elo_draw_prob"),
        "XSharp": ("poisson_reg_prob", "xgb_draw_prob"),
        "SharpConsensus": ("ensemble_prob", "draw_prob"),
    }
    for model, (bin_k, draw_k) in mapping.items():
        row = frozen.get(model)
        if not row or row.get("binary_home") is None:
            continue
        out[bin_k] = float(row["binary_home"])
        if row.get("dp") is not None:
            out[draw_k] = float(row["dp"])
        if model == "Takedown":
            out["takedown_home_win"] = row.get("hw")
            out["takedown_draw"] = row.get("dp")
            out["takedown_away_win"] = row.get("aw")
            out["takedown_model_version"] = row.get("model_version")
            out["trueskill_draw_prob"] = row.get("dp")
        if model == "SharpConsensus":
            out["ensemble_home_win"] = row.get("hw")
            out["ensemble_draw"] = row.get("dp")
            out["ensemble_away_win"] = row.get("aw")
            out["ensemble_draw_prob"] = row.get("dp")
            out["consensus_model_version"] = row.get("model_version")
    if "ensemble_prob" not in out and not any(k in out for k in ("markov_prob", "poisson_xg_prob")):
        return None
    out["from_frozen_ledger"] = True
    return out
