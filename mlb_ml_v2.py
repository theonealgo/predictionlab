#!/usr/bin/env python3
"""MLB moneyline v2 — pre-game SP xFIP blend (isolation-proven 2026-08-13).

Grinder2/Takedown/Edge blend toward SP; XSharp is SP-only; Consensus is the
live 30/30/25/15 mix of those v2 legs. Efficiency / spread / totals are not
modified. Features for game t use only pitcher appearances with date < t.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _find_sp_db() -> Path | None:
    names = [
        HERE / "mlb_sp.sqlite",
        HERE / "data" / "mlb_sp.sqlite",
        HERE / "models" / "MLB_v2" / "mlb_sp.sqlite",
        HERE.parent / "data" / "mlb_sp.sqlite",
        HERE.parent / "models" / "MLB_v2" / "mlb_sp.sqlite",
        Path.home() / "Documents/Personal/mlb/data/mlb_sp.sqlite",
        Path.home() / "Documents/Personal/predictionlabfix_work/models/MLB_v2/mlb_sp.sqlite",
        Path("/Users/nimamesghali/Sports Sandbox/predictionlabfix_work2/models/MLB_v2/mlb_sp.sqlite"),
    ]
    for p in names:
        if p.exists():
            return p
    return None

# Frozen on 2024 H2 validation only (acc then log-loss). Test was not used to pick.
V2 = {
    "k": 0.4,
    "hfa": 0.05,
    "w_sp": {
        "Grinder2": 0.95,
        "Takedown": 0.95,
        "Edge": 0.90,
        "XSharp": 1.00,
    },
    "ensemble_w": {"glicko2": 0.30, "trueskill": 0.30, "xgb": 0.25, "elo": 0.15},
    "lg_xfip": 4.20,
    "lg_hrfb": 0.105,
    "fip_c": 3.10,
    "min_ip": 12.0,
    "prior_ip": 40.0,
    # IP gate frozen on 2024 H2; primary OOS 2026-06-16..2026-08-12.
    # Thin starter samples keep more team-rating residual (not last-7 fit).
    "ip_gate_floor": 0.35,
    "ip_gate_full": 80.0,
}

PARAMS_CANDIDATES = [
    HERE / "ml_v2_params.json",
    HERE.parent / "notes" / "ml_v2_params.json",
    HERE / "models" / "MLB_v2" / "ml_v2_params.json",
    HERE.parent / "models" / "MLB_v2" / "ml_v2_params.json",
]


def _as_frac(p: float | None) -> float | None:
    if p is None:
        return None
    try:
        x = float(p)
    except (TypeError, ValueError):
        return None
    if x > 1.5:
        x = x / 100.0
    if x < 0.02 or x > 0.98:
        return max(0.05, min(0.95, x))
    return x


def _logistic(z: float) -> float:
    z = max(-20.0, min(20.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def sp_win_prob(home_xfip: float | None, away_xfip: float | None,
                k: float = V2["k"], hfa: float = V2["hfa"]) -> float | None:
    if home_xfip is None or away_xfip is None:
        return None
    return max(0.05, min(0.95, _logistic(k * (away_xfip - home_xfip) + hfa)))


def _mix(v1: float | None, sp: float | None, w_sp: float) -> float | None:
    if sp is None:
        return v1
    if v1 is None:
        return sp
    return max(0.05, min(0.95, (1.0 - w_sp) * v1 + w_sp * sp))


def blend_named(g2: float | None, ts: float | None, elo: float | None,
                xgb: float | None, sp: float | None,
                w: dict[str, float] | None = None) -> tuple:
    ww = w or V2["w_sp"]
    g2n = _mix(_as_frac(g2), sp, ww["Grinder2"])
    tsn = _mix(_as_frac(ts), sp, ww["Takedown"])
    elon = _mix(_as_frac(elo), sp, ww["Edge"])
    xgbn = _mix(_as_frac(xgb), sp, ww["XSharp"])
    ew = V2["ensemble_w"]
    parts = []
    if g2n is not None:
        parts.append((g2n, ew["glicko2"]))
    if tsn is not None:
        parts.append((tsn, ew["trueskill"]))
    if xgbn is not None:
        parts.append((xgbn, ew["xgb"]))
    if elon is not None:
        parts.append((elon, ew["elo"]))
    ens = None
    if parts:
        tw = sum(x[1] for x in parts)
        ens = sum(p * wt for p, wt in parts) / tw
    return g2n, tsn, elon, xgbn, ens


def _sp_db_path() -> Path | None:
    return _find_sp_db()


_SP_CACHE: dict[str, dict] | None = None
_LOGS_BY_P: dict[int, list[tuple]] | None = None
_SCHED_INDEX: dict[tuple, list[tuple]] | None = None


def _connect() -> sqlite3.Connection | None:
    path = _sp_db_path()
    if path is None:
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _load_pregame() -> dict[str, dict]:
    global _SP_CACHE
    if _SP_CACHE is not None:
        return _SP_CACHE
    con = _connect()
    cache: dict[str, dict] = {}
    if con is None:
        _SP_CACHE = cache
        return cache
    try:
        for r in con.execute("SELECT * FROM sp_pregame"):
            cache[str(r["local_game_id"])] = dict(r)
    finally:
        con.close()
    _SP_CACHE = cache
    return cache


def norm_team(name: str) -> str:
    s = (name or "").strip()
    if s in {"Oakland Athletics", "Oakland A's", "A's", "Athletics"}:
        return "Athletics"
    return s


def _ip_to_outs(ip) -> int:
    if ip is None or ip == "":
        return 0
    s = str(ip).strip()
    try:
        if "." in s:
            inn, frac = s.split(".", 1)
            return int(inn) * 3 + int(frac or 0)
        return int(round(float(s) * 3))
    except (TypeError, ValueError):
        return 0


def ip_gate_scale(home_ip: float | None, away_ip: float | None,
                  hid: int | None, aid: int | None) -> float:
    """0..1 multiplier on w_sp. Missing both SPs → 0 (keep v1). Thin IP → floor."""
    if not hid or not aid:
        return 0.0
    try:
        ip = min(float(home_ip or 0.0), float(away_ip or 0.0))
    except (TypeError, ValueError):
        return float(V2["ip_gate_floor"])
    floor = float(V2["ip_gate_floor"])
    full = float(V2["ip_gate_full"])
    lo = float(V2["min_ip"])
    if ip < lo:
        return floor
    if ip >= full:
        return 1.0
    return floor + (1.0 - floor) * (ip - lo) / (full - lo)


def _prior_pitch_totals(pitcher_id: int, asof: str) -> tuple[float, int, int, int, int, int]:
    """Return (ip, hr, bb, hbp, k, fly) from logs with date < asof."""
    global _LOGS_BY_P
    if _LOGS_BY_P is None:
        con = _connect()
        logs: dict[int, list[tuple]] = {}
        if con is not None:
            try:
                for r in con.execute(
                    """SELECT pitcher_id, game_date, ip_outs, hr, bb, hbp, k, fly_outs
                       FROM pitcher_logs ORDER BY pitcher_id, game_date, game_pk"""
                ):
                    logs.setdefault(int(r["pitcher_id"]), []).append(
                        (r["game_date"], r["ip_outs"] or 0, r["hr"] or 0, r["bb"] or 0,
                         r["hbp"] or 0, r["k"] or 0, r["fly_outs"] or 0)
                    )
            finally:
                con.close()
        _LOGS_BY_P = logs
    prior = [row for row in _LOGS_BY_P.get(int(pitcher_id), []) if row[0] < asof]
    outs = hr = bb = hbp = k = fly = 0
    for row in prior:
        outs += row[1]
        hr += row[2]
        bb += row[3]
        hbp += row[4]
        k += row[5]
        fly += row[6] + row[2]
    return outs / 3.0, hr, bb, hbp, k, fly


def xfip_asof(pitcher_id: int | None, asof: str) -> float | None:
    """Rolling xFIP-equivalent from appearances strictly before asof."""
    got = xfip_ip_asof(pitcher_id, asof)
    return None if got is None else got[0]


def xfip_ip_asof(pitcher_id: int | None, asof: str) -> tuple[float, float] | None:
    """(xFIP, prior IP) from appearances strictly before asof."""
    if not pitcher_id or not asof:
        return None
    ip, hr, bb, hbp, k, fly = _prior_pitch_totals(int(pitcher_id), asof)
    if ip <= 0:
        return V2["lg_xfip"], 0.0
    raw = ((13.0 * (fly * V2["lg_hrfb"])) + (3.0 * (bb + hbp)) - (2.0 * k)) / ip + V2["fip_c"]
    xf = (ip * raw + V2["prior_ip"] * V2["lg_xfip"]) / (ip + V2["prior_ip"])
    return xf, ip


def _lookup_schedule_sp(game_date: str | None, home: str | None, away: str | None):
    if not game_date or not home or not away:
        return None, None
    d = str(game_date)[:10]
    global _SCHED_INDEX
    if _SCHED_INDEX is None:
        idx: dict[tuple, list[tuple]] = {}
        con = _connect()
        if con is not None:
            try:
                for r in con.execute(
                    """SELECT game_date, home_team, away_team, home_sp_id, away_sp_id
                       FROM schedule_games"""
                ):
                    key = (r["game_date"], norm_team(r["home_team"]), norm_team(r["away_team"]))
                    idx.setdefault(key, []).append((r["home_sp_id"], r["away_sp_id"]))
            finally:
                con.close()
        _SCHED_INDEX = idx
    cands = _SCHED_INDEX.get((d, norm_team(home), norm_team(away))) or []
    if not cands:
        return None, None
    return cands[0][0], cands[0][1]


def sp_context_for_game(game_id: str | None, game_date: str | None = None,
                        home: str | None = None, away: str | None = None):
    """Return (sp_home_win_prob, ip_gate_scale). Scale 0 → keep v1 legs."""
    if game_id:
        row = _load_pregame().get(str(game_id))
        if row:
            sp = sp_win_prob(row.get("home_xfip"), row.get("away_xfip"))
            sc = ip_gate_scale(
                row.get("home_ip"), row.get("away_ip"),
                row.get("home_sp_id"), row.get("away_sp_id"),
            )
            return sp, sc
    hid, aid = _lookup_schedule_sp(game_date, home, away)
    if not hid and not aid:
        return None, 0.0
    asof = str(game_date)[:10] if game_date else date.today().isoformat()
    h = xfip_ip_asof(hid, asof)
    a = xfip_ip_asof(aid, asof)
    if h is None or a is None:
        return None, 0.0
    return sp_win_prob(h[0], a[0]), ip_gate_scale(h[1], a[1], hid, aid)


def sp_prob_for_game(game_id: str | None, game_date: str | None = None,
                     home: str | None = None, away: str | None = None) -> float | None:
    sp, _sc = sp_context_for_game(game_id, game_date=game_date, home=home, away=away)
    return sp


def mix_named_ml_v2(
    game_id: str | None,
    g2: float | None,
    ts: float | None,
    elo: float | None,
    xgb: float | None,
    ens: float | None,
    *,
    game_date: str | None = None,
    home: str | None = None,
    away: str | None = None,
) -> tuple:
    """Return v2 (g2, ts, elo, xgb, ens) as 0–1. Falls back to v1 if no SP."""
    sp, scale = sp_context_for_game(game_id, game_date=game_date, home=home, away=away)
    if sp is None or scale <= 0:
        return _as_frac(g2), _as_frac(ts), _as_frac(elo), _as_frac(xgb), _as_frac(ens)
    ww = {k: float(v) * scale for k, v in V2["w_sp"].items()}
    return blend_named(g2, ts, elo, xgb, sp, w=ww)


def apply_v2_to_upcoming_dict(game_dict: dict[str, Any]) -> None:
    """Overwrite named ML percents on an upcoming MLB card. Spread/totals untouched."""
    g2, ts, elo, xgb, ens = mix_named_ml_v2(
        game_dict.get("game_id"),
        game_dict.get("glicko2_prob"),
        game_dict.get("trueskill_prob"),
        game_dict.get("elo_prob"),
        game_dict.get("xgb_prob"),
        game_dict.get("ensemble_prob"),
        game_date=game_dict.get("game_date"),
        home=game_dict.get("home_team_id"),
        away=game_dict.get("away_team_id"),
    )
    if g2 is not None:
        game_dict["glicko2_prob"] = round(g2 * 100.0, 1)
    if ts is not None:
        game_dict["trueskill_prob"] = round(ts * 100.0, 1)
    if elo is not None:
        game_dict["elo_prob"] = round(elo * 100.0, 1)
    if xgb is not None:
        game_dict["xgb_prob"] = round(xgb * 100.0, 1)
    if ens is not None:
        game_dict["ensemble_prob"] = round(ens * 100.0, 1)
        ht = game_dict.get("home_team_id")
        at = game_dict.get("away_team_id")
        game_dict["predicted_winner"] = ht if ens > 0.5 else at
        game_dict["model_win_pct"] = round((ens if ens >= 0.5 else 1.0 - ens) * 100.0, 1)
        game_dict["mlb_ml_v2"] = True
