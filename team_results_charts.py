"""Publish-layer team-sports template: real H2H, Cards|Chart, consensus, Copy All.

Does not rewrite MLB / tennis UI / UFC picks.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

RESULTS_SLUGS = {
    "NBA": "nba-results",
    "NFL": "nfl-results",
    "NHL": "nhl-results",
    "NCAAB": "ncaab-results",
    "NCAAF": "ncaaf-results",
    "NCAAW": "ncaaw-results",
    "WNBA": "wnba-results",
    "CFL": "cfl-results",
    "SOCCER": "soccer-results",
    "TENNIS": "tennis-results",
}

PICKS_H2H_SPORTS = frozenset(RESULTS_SLUGS) - {"SOCCER"}
MARKER = "<!-- team-results-charts -->"
COPY_MARK = "pl-copy-all-slate"
_CHART_SOURCE_HTML: dict[str, str] = {}


def set_results_chart_source(sport: str, html: str) -> None:
    """Cards HTML used to rebuild consensus when ?view=chart is a thin shell."""
    if html and "<" in html:
        _CHART_SOURCE_HTML[_sport_key(sport)] = html

_DIR_WORDS = frozenset(
    {
        "new",
        "north",
        "south",
        "east",
        "west",
        "central",
        "northern",
        "southern",
        "eastern",
        "western",
        "middle",
    }
)
_STOP = frozenset({"the", "university", "univ", "of", "and"})
_QUALIFIERS = frozenset(
    {"state", "tech", "am", "st", "ohio", "oh", "a", "m", "am", "a&m"}
)


def _sport_key(sport: str) -> str:
    s = (sport or "").strip()
    if s.lower() == "soccer":
        return "SOCCER"
    return s.upper()


def _db_path() -> Path:
    root = Path(__file__).resolve().parent
    return root / "sports_predictions_original.db"


def _parse_game_date(raw: str) -> str:
    s = str(raw or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    from datetime import datetime as _dt

    for fmt in (
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ):
        try:
            return _dt.strptime(s[:19] if len(s) >= 16 else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _nfl_current_season_year() -> int:
    try:
        from sports.NFL import nfl_season_year

        return int(nfl_season_year())
    except Exception:
        now = datetime.now()
        return now.year - 1 if now.month <= 2 else now.year


def _nfl_finals_from_db(limit: int = 180, sport: str = "NFL") -> list[dict]:
    """Completed games + model sides for the shared 6-model consensus inject."""
    path = _db_path()
    if not path.is_file():
        return []
    sport_u = (sport or "NFL").strip().upper()
    season = _nfl_current_season_year() if sport_u == "NFL" else None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        if sport_u == "NFL" and season is not None:
            rows = conn.execute(
                """
                SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                       g.home_score, g.away_score,
                       p.elo_home_prob, p.xgboost_home_prob, p.win_probability,
                       p.glicko_home_prob, p.trueskill_home_prob, p.logistic_home_prob
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ? AND g.home_score IS NOT NULL
                  AND g.away_score IS NOT NULL
                  AND (
                        CAST(g.season AS TEXT) = ?
                        OR g.game_date LIKE ?
                  )
                ORDER BY g.game_date DESC
                LIMIT ?
                """,
                (sport_u, sport_u, str(season), f"{season}%", int(limit)),
            ).fetchall()
        else:
            rows = []
            season = None
        if len(rows) < 3:
            rows = conn.execute(
                """
                SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                       g.home_score, g.away_score,
                       p.elo_home_prob, p.xgboost_home_prob, p.win_probability,
                       p.glicko_home_prob, p.trueskill_home_prob, p.logistic_home_prob
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ? AND g.home_score IS NOT NULL
                  AND g.away_score IS NOT NULL
                ORDER BY g.game_date DESC
                LIMIT ?
                """,
                (sport_u, sport_u, int(limit)),
            ).fetchall()
        conn.close()
    except Exception:
        return []
    out: list[dict] = []
    prob_models = (
        ("Edge", "elo_home_prob"),
        ("XSharp", "xgboost_home_prob"),
        ("Sharp Consensus", "win_probability"),
        ("Grinder2", "glicko_home_prob"),
        ("Takedown", "trueskill_home_prob"),
        ("Efficiency", "logistic_home_prob"),
    )
    for r in rows:
        try:
            hs = float(r["home_score"])
            aws = float(r["away_score"])
        except (TypeError, ValueError):
            continue
        dk = _parse_game_date(r["game_date"])
        if not dk:
            continue
        home = str(r["home_team_id"] or "").strip()
        away = str(r["away_team_id"] or "").strip()
        if not home or not away:
            continue
        home_won = hs > aws
        models: dict = {}
        for name, col in prob_models:
            try:
                prob = float(r[col]) if r[col] is not None else None
            except (TypeError, ValueError):
                prob = None
            if prob is None:
                continue
            pick = home if prob >= 0.5 else away
            models[name] = {
                "pick": pick,
                "prob": prob,
                "correct": (pick == home) == home_won,
            }
        if len(models) < 3:
            continue
        face = (models.get("Sharp Consensus") or models.get("Edge") or next(iter(models.values())))
        out.append(
            {
                "game_date": dk,
                "league": sport_u,
                "away_team_id": away,
                "home_team_id": home,
                "away_score": int(aws) if abs(aws - round(aws)) < 1e-6 else aws,
                "home_score": int(hs) if abs(hs - round(hs)) < 1e-6 else hs,
                "final": True,
                "face_pick": face.get("pick"),
                "face_prob": face.get("prob"),
                "correct": face.get("correct"),
                "models": models,
                "game_id": str(r["game_id"] or ""),
            }
        )
    return out


_NFL_TABLE_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _nfl_finals_from_results_html(html: str) -> list[dict]:
    """Per-game G2/TD/Edge/XSharp/SC sides from the week-by-week results tables."""
    html = html or ""
    out: list[dict] = []
    for body in re.findall(
        r'<table class="games-table">[\s\S]*?<tbody>([\s\S]*?)</tbody>',
        html,
        flags=re.I,
    ):
        for tr in re.findall(r"<tr>([\s\S]*?)</tr>", body, flags=re.I):
            tds = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr, flags=re.I)
            if len(tds) < 8:
                continue
            dk = _parse_game_date(re.sub(r"<[^>]+>", "", tds[0]).strip())
            teams = re.findall(
                r'<span class="(winner|loser)">([^<]+)</span>',
                tds[1],
                flags=re.I,
            )
            score_m = re.search(
                r"(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)",
                re.sub(r"<[^>]+>", "", tds[2]),
            )
            if not dk or len(teams) < 2 or not score_m:
                continue
            away, home = teams[0][1].strip(), teams[1][1].strip()
            try:
                aws, hs = float(score_m.group(1)), float(score_m.group(2))
            except (TypeError, ValueError):
                continue
            actual = away if teams[0][0].lower() == "winner" else home
            other = home if actual == away else away
            models: dict = {}
            for name, cell in zip(_NFL_TABLE_MODELS, tds[3:9]):
                plain = re.sub(r"<[^>]+>", " ", cell)
                if "✅" in cell or "prob-correct" in cell:
                    pick, ok = actual, True
                elif "❌" in cell or "prob-wrong" in cell:
                    pick, ok = other, False
                else:
                    continue
                num = re.search(r"(\d+(?:\.\d+)?)", plain)
                models[name] = {
                    "pick": pick,
                    "prob": float(num.group(1)) if num else None,
                    "correct": ok,
                    "side": "away" if pick == away else "home",
                }
            if len(models) < 3:
                continue
            face = models.get("Sharp Consensus") or next(iter(models.values()))
            out.append(
                {
                    "game_date": dk,
                    "league": "NFL",
                    "away_team_id": away,
                    "home_team_id": home,
                    "away_score": int(aws) if abs(aws - round(aws)) < 1e-6 else aws,
                    "home_score": int(hs) if abs(hs - round(hs)) < 1e-6 else hs,
                    "final": True,
                    "face_pick": face.get("pick"),
                    "face_prob": face.get("prob"),
                    "correct": face.get("correct"),
                    "models": models,
                    "game_id": f"nfl-{dk}-{away}-{home}",
                }
            )
    return out


def _merge_nfl_efficiency(html_rows: list[dict], db_rows: list[dict]) -> list[dict]:
    """Attach DB Efficiency onto week-table finals when the same game exists."""
    if not html_rows:
        return db_rows
    by_key = {}
    for row in db_rows or []:
        key = (
            str(row.get("game_date") or "")[:10],
            _team_core(str(row.get("home_team_id") or "")),
            _team_core(str(row.get("away_team_id") or "")),
        )
        eff = (row.get("models") or {}).get("Efficiency")
        if eff:
            by_key[key] = eff
            by_key[(key[0], key[2], key[1])] = eff
    for row in html_rows:
        key = (
            str(row.get("game_date") or "")[:10],
            _team_core(str(row.get("home_team_id") or "")),
            _team_core(str(row.get("away_team_id") or "")),
        )
        eff = by_key.get(key)
        if eff:
            models = dict(row.get("models") or {})
            models.setdefault("Efficiency", eff)
            row["models"] = models
    return html_rows


def _nfl_ml_favorite_side(home_ml, away_ml) -> str | None:
    try:
        home = float(home_ml)
        away = float(away_ml)
    except (TypeError, ValueError):
        return None
    if home == away:
        return None
    return "HOME" if home < away else "AWAY"


def _merge_nfl_book_sides(rows: list[dict]) -> list[dict]:
    """Attach book / PL moneylines so PL vs Sportsbook has favorites.

    Week-table finals use synthetic game_ids, and the hub date lookup is
    MLB-only — so book sides never get onto NFL rows unless we copy them here.
    """
    if not rows:
        return rows
    path = _db_path()
    if not path.is_file():
        return rows
    by_key: dict[tuple[str, str, str], dict] = {}
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            """
            SELECT game_id, game_date, home_team, away_team,
                   home_moneyline, away_moneyline
            FROM betting_lines
            WHERE sport = 'NFL'
              AND home_moneyline IS NOT NULL
              AND away_moneyline IS NOT NULL
            """
        ):
            dk = _parse_game_date(r["game_date"]) or str(r["game_date"] or "")[:10]
            hk, ak = _nfl_team_key(r["home_team"]), _nfl_team_key(r["away_team"])
            if dk and hk and ak:
                by_key[(dk, hk, ak)] = {
                    "game_id": str(r["game_id"] or ""),
                    "book_home_moneyline": r["home_moneyline"],
                    "book_away_moneyline": r["away_moneyline"],
                }
        for r in conn.execute(
            """
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   p.lock_card_json
            FROM games g
            JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'NFL'
            WHERE g.sport = 'NFL'
              AND p.lock_card_json IS NOT NULL
            """
        ):
            dk = _parse_game_date(r["game_date"])
            hk, ak = _nfl_team_key(r["home_team_id"]), _nfl_team_key(r["away_team_id"])
            if not dk or not hk or not ak:
                continue
            try:
                snap = json.loads(r["lock_card_json"] or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                snap = {}
            if not isinstance(snap, dict):
                continue
            slot = by_key.setdefault((dk, hk, ak), {})
            if r["game_id"] and not slot.get("game_id"):
                slot["game_id"] = str(r["game_id"])
            for src, dest in (
                ("book_home_moneyline", "book_home_moneyline"),
                ("book_away_moneyline", "book_away_moneyline"),
                ("pl_model_home_ml", "pl_model_home_ml"),
                ("pl_model_away_ml", "pl_model_away_ml"),
            ):
                if slot.get(dest) is None and snap.get(src) is not None:
                    slot[dest] = snap.get(src)
        conn.close()
    except Exception:
        return rows
    for row in rows:
        dk = str(row.get("game_date") or "")[:10]
        hk = _nfl_team_key(str(row.get("home_team_id") or ""))
        ak = _nfl_team_key(str(row.get("away_team_id") or ""))
        slot = by_key.get((dk, hk, ak)) if dk and hk and ak else None
        if not slot:
            if row.get("pl_fav_side") not in ("HOME", "AWAY"):
                pick = str(row.get("face_pick") or "").strip()
                home = str(row.get("home_team_id") or "").strip()
                away = str(row.get("away_team_id") or "").strip()
                pk, hkk, akk = (
                    _nfl_team_key(pick),
                    _nfl_team_key(home),
                    _nfl_team_key(away),
                )
                if pk and hkk and pk == hkk:
                    row["pl_fav_side"] = "HOME"
                elif pk and akk and pk == akk:
                    row["pl_fav_side"] = "AWAY"
            continue
        if slot.get("game_id") and str(row.get("game_id") or "").startswith("nfl-"):
            row["game_id"] = slot["game_id"]
        for key in (
            "book_home_moneyline",
            "book_away_moneyline",
            "pl_model_home_ml",
            "pl_model_away_ml",
        ):
            if row.get(key) is None and slot.get(key) is not None:
                row[key] = slot[key]
        if row.get("book_fav_side") not in ("HOME", "AWAY"):
            side = _nfl_ml_favorite_side(
                row.get("book_home_moneyline"), row.get("book_away_moneyline")
            )
            if side:
                row["book_fav_side"] = side
        if row.get("pl_fav_side") not in ("HOME", "AWAY"):
            side = _nfl_ml_favorite_side(
                row.get("pl_model_home_ml"), row.get("pl_model_away_ml")
            )
            if side:
                row["pl_fav_side"] = side
            else:
                pick = str(row.get("face_pick") or "").strip()
                home = str(row.get("home_team_id") or "").strip()
                away = str(row.get("away_team_id") or "").strip()
                pk, hkk, akk = _nfl_team_key(pick), _nfl_team_key(home), _nfl_team_key(away)
                if pk and hkk and pk == hkk:
                    row["pl_fav_side"] = "HOME"
                elif pk and akk and pk == akk:
                    row["pl_fav_side"] = "AWAY"
    return rows


def _nfl_implied_efficiency(row: dict, sport: str = "NFL") -> dict | None:
    """Published Efficiency: PL-spread-implied ML, not a new model."""
    models = row.get("models") or {}
    if models.get("Efficiency"):
        return None
    face = models.get("Sharp Consensus") or models.get("Edge")
    if not face:
        return None
    try:
        from sports.team_efficiency_attach import (
            home_prob_pct_to_spread,
            spread_to_home_prob_pct,
        )
    except Exception:
        return None
    try:
        raw = float(face.get("prob"))
    except (TypeError, ValueError):
        return None
    pct = raw * 100.0 if raw <= 1.0 else raw
    sport_u = (sport or "NFL").strip().upper()
    sigma_sport = "NFL" if sport_u == "CFL" else sport_u
    try:
        sp = home_prob_pct_to_spread(pct, sigma_sport)
        eff_pct = spread_to_home_prob_pct(float(sp), sigma_sport)
    except (TypeError, ValueError):
        return None
    if eff_pct is None:
        return None
    home = str(row.get("home_team_id") or "")
    away = str(row.get("away_team_id") or "")
    if not home or not away:
        return None
    pick = home if eff_pct >= 50.0 else away
    hs, aws = row.get("home_score"), row.get("away_score")
    correct = None
    try:
        if hs is not None and aws is not None:
            correct = (pick == home) == (float(hs) > float(aws))
    except (TypeError, ValueError):
        pass
    return {
        "pick": pick,
        "prob": float(eff_pct) / 100.0,
        "correct": correct,
        "side": "home" if pick == home else "away",
    }


def _nfl_attach_efficiency(rows: list[dict]) -> list[dict]:
    for row in rows or []:
        models = dict(row.get("models") or {})
        if models.get("Efficiency"):
            row["models"] = models
            continue
        eff = _nfl_implied_efficiency(
            {**row, "models": models},
            sport=str(row.get("league") or "NFL"),
        )
        if eff:
            models["Efficiency"] = eff
            row["models"] = models
    return rows


_NFL_SIX_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _nfl_row_has_six(row: dict) -> bool:
    models = row.get("models") or {}
    return all(
        isinstance(models.get(name), dict)
        and (models[name].get("pick") or models[name].get("side"))
        for name in _NFL_SIX_MODELS
    )


def _nfl_keep_six_model_rows(rows: list[dict]) -> list[dict]:
    """Keep games that have all 6 sides so the panel does not shrink to 4/5."""
    six = [row for row in rows or [] if _nfl_row_has_six(row)]
    if six:
        return six
    year = str(_nfl_current_season_year())
    season = [
        row
        for row in rows or []
        if str(row.get("game_date") or "").startswith(year)
    ]
    return season or list(rows or [])


def _six_model_consensus_finals(html: str, sport: str) -> list[dict]:
    """Shared 6-model consensus rows (NFL / NCAAF / CFL)."""
    sport_u = (sport or "").strip().upper()
    rows = _nfl_finals_from_db(limit=400, sport=sport_u)
    if not rows:
        rows = _nfl_finals_from_results_html(html)
    if not rows and html and ("game-card" in html or "data-pick-card" in html):
        try:
            from mlb_consensus_hub import _extract_game_rows

            rows = _extract_game_rows(html)
        except Exception:
            rows = []
    for row in rows or []:
        if not row.get("league"):
            row["league"] = sport_u
    return _nfl_keep_six_model_rows(_nfl_attach_efficiency(rows))


def _nfl_consensus_finals(html: str) -> list[dict]:
    """NFL 6-model sides for the MLB-shaped consensus table.

    Use the current-season DB slate so Past 7 / Past 30 have dissent
    rows. One visible last-night card is not enough — that collapsed
    the table to a single 6/6 row. Attach published Efficiency
    (PL-spread-implied) when a game is missing it.
    """
    rows = _nfl_finals_from_db(limit=400)
    if not rows:
        rows = _nfl_finals_from_results_html(html)
    if not rows and html and ("game-card" in html or "data-pick-card" in html):
        try:
            from mlb_consensus_hub import _extract_game_rows

            rows = _extract_game_rows(html)
        except Exception:
            rows = []
    return _nfl_keep_six_model_rows(
        _nfl_attach_efficiency(_merge_nfl_book_sides(rows))
    )


def _nfl_line_to_pick(home: str, away: str, home_centric) -> str | None:
    """Home-centric line → 'Team -X.X' pick text for hub spread grading."""
    try:
        line = float(home_centric)
    except (TypeError, ValueError):
        return None
    if abs(line) < 1e-9:
        return None
    if line > 0:
        return f"{home} {-line:+g}"
    return f"{away} {line:+g}"


def _nfl_json_float(snap: dict, *keys):
    for key in keys:
        try:
            val = snap.get(key)
        except AttributeError:
            return None
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _nfl_spread_total_finals(sport: str = "NFL") -> list[dict]:
    """Scored games with locked PL/XSharp spread + total vs the book."""
    path = _db_path()
    if not path.is_file():
        return []
    sport_u = (sport or "NFL").strip().upper()
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.lock_pl_spread, p.lock_xs_spread,
                   p.lock_pl_total, p.lock_xs_total, p.lock_card_json
            FROM games g
            JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ?
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND (
                    p.lock_pl_spread IS NOT NULL
                 OR p.lock_xs_spread IS NOT NULL
                 OR p.lock_pl_total IS NOT NULL
                 OR (p.lock_card_json IS NOT NULL AND length(p.lock_card_json) > 20)
              )
            ORDER BY g.game_date DESC
            """,
            (sport_u, sport_u),
        ).fetchall()
        conn.close()
    except Exception:
        return []
    try:
        from mlb_consensus_hub import _apply_pl_xs_grades
    except Exception:
        _apply_pl_xs_grades = None  # type: ignore
    out: list[dict] = []
    for r in rows:
        try:
            hs = float(r["home_score"])
            aws = float(r["away_score"])
        except (TypeError, ValueError):
            continue
        dk = _parse_game_date(r["game_date"])
        home = str(r["home_team_id"] or "").strip()
        away = str(r["away_team_id"] or "").strip()
        if not dk or not home or not away:
            continue
        snap: dict = {}
        raw = r["lock_card_json"]
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    snap = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                snap = {}
        pl_sp = r["lock_pl_spread"]
        if pl_sp is None:
            pl_sp = _nfl_json_float(snap, "our_spread", "disp_pl_spread")
        xs_sp = r["lock_xs_spread"]
        if xs_sp is None:
            xs_sp = _nfl_json_float(snap, "xgb_spread", "xsharp_spread")
        pl_tot = r["lock_pl_total"]
        if pl_tot is None:
            pl_tot = _nfl_json_float(snap, "our_total", "xgb_total")
        xs_tot = r["lock_xs_total"]
        if xs_tot is None:
            xs_tot = _nfl_json_float(snap, "xgb_total", "our_total")
        book_tot = _nfl_json_float(snap, "book_total", "market_total")
        pl_pick = _nfl_line_to_pick(home, away, pl_sp)
        xs_pick = _nfl_line_to_pick(home, away, xs_sp)
        spread = {}
        if pl_pick:
            spread["pl_pick"] = pl_pick
        if xs_pick:
            spread["xs_pick"] = xs_pick
        totals = {}
        if pl_tot is not None:
            totals["pl_line"] = float(pl_tot)
        if xs_tot is not None:
            totals["xs_line"] = float(xs_tot)
        if book_tot is not None:
            totals["book_line"] = float(book_tot)
        if not spread and not totals:
            continue
        row = {
            "game_date": dk,
            "league": sport_u,
            "away_team_id": away,
            "home_team_id": home,
            "away_score": int(aws) if abs(aws - round(aws)) < 1e-6 else aws,
            "home_score": int(hs) if abs(hs - round(hs)) < 1e-6 else hs,
            "final": True,
            "spread": spread,
            "totals": totals,
            "game_id": str(r["game_id"] or ""),
        }
        if _apply_pl_xs_grades:
            try:
                _apply_pl_xs_grades(row)
            except Exception:
                pass
        sp = row.get("spread") if isinstance(row.get("spread"), dict) else {}
        tot = row.get("totals") if isinstance(row.get("totals"), dict) else {}
        if sp.get("pl_grade") in ("WIN", "LOSS", "PUSH") and not sp.get("grade"):
            sp["grade"] = sp["pl_grade"]
        if tot.get("pl_grade") in ("WIN", "LOSS", "PUSH") and not tot.get("grade"):
            tot["grade"] = tot["pl_grade"]
        if (
            sp.get("grade") not in ("WIN", "LOSS", "PUSH")
            and sp.get("xs_grade") not in ("WIN", "LOSS", "PUSH")
            and tot.get("grade") not in ("WIN", "LOSS", "PUSH")
            and tot.get("xs_grade") not in ("WIN", "LOSS", "PUSH")
        ):
            continue
        out.append(row)
    return out


def stamp_nfl_lock_market_grades(daily_results: dict) -> None:
    """Copy locked PL/XSharp ATS grades onto daily cards. Does not invent lines."""
    if not daily_results:
        return
    finals = _nfl_spread_total_finals("NFL")
    if not finals:
        return
    by_key: dict[tuple[str, str, str], dict] = {}
    for row in finals:
        dk = str(row.get("game_date") or "")[:10]
        hk = _nfl_team_key(str(row.get("home_team_id") or ""))
        ak = _nfl_team_key(str(row.get("away_team_id") or ""))
        if dk and hk and ak:
            by_key[(dk, hk, ak)] = row
    for dd in daily_results.values():
        for g in dd.get("games") or []:
            if not isinstance(g, dict) or g.get("skip_grading"):
                continue
            dk = str(g.get("date") or "")[:10]
            hk = _nfl_team_key(str(g.get("home") or g.get("home_team_id") or ""))
            ak = _nfl_team_key(str(g.get("away") or g.get("away_team_id") or ""))
            row = by_key.get((dk, hk, ak))
            if not row:
                continue
            sp = row.get("spread") if isinstance(row.get("spread"), dict) else {}
            tot = row.get("totals") if isinstance(row.get("totals"), dict) else {}
            pl_g = sp.get("pl_grade") or sp.get("grade")
            if pl_g in ("WIN", "LOSS", "PUSH"):
                if pl_g == "PUSH":
                    g["spread_pick"] = "PUSH"
                    g["pl_spread_correct"] = None
                    g["spread_correct"] = None
                else:
                    g["pl_spread_correct"] = pl_g == "WIN"
                    g["spread_correct"] = g["pl_spread_correct"]
                    if sp.get("pl_pick"):
                        g["spread_pick"] = sp["pl_pick"]
            tot_g = tot.get("pl_grade") or tot.get("grade")
            if tot_g in ("WIN", "LOSS", "PUSH"):
                if tot_g == "PUSH":
                    g["total_pick"] = "PUSH"
                    g["total_correct"] = None
                    g["pl_total_correct"] = None
                else:
                    g["total_correct"] = tot_g == "WIN"
                    g["pl_total_correct"] = g["total_correct"]
                    try:
                        book = float(tot.get("book_line"))
                        pl = float(tot.get("pl_line"))
                        g["total_pick"] = "OVER" if pl >= book else "UNDER"
                    except (TypeError, ValueError):
                        g["total_pick"] = g.get("total_pick") or ("OVER" if g["total_correct"] else "UNDER")


_EMPTY_MARKET_STUB = "No graded games for this market on the current results slate."


def _replace_empty_market_panel(html: str, market: str, block: str) -> str:
    """Swap the hub empty-market stub for a real PL/XSharp chart."""
    if not html or not block or market not in ("spread", "totals"):
        return html
    marker = f'data-market-panel="{market}"'
    i = html.find(marker)
    if i < 0:
        return html
    stub_at = html.find(_EMPTY_MARKET_STUB, i)
    if stub_at < 0:
        return html
    start = html.rfind('<div class="pl-consensus-records"', i, stub_at + 1)
    if start < 0:
        start = html.rfind("<div", i, stub_at + 1)
    if start < 0:
        return html
    end = html.find("</div>", stub_at)
    if end < 0:
        return html
    end += len("</div>")
    return html[:start] + block + html[end:]


def _fill_nfl_spread_total_charts(html: str, sport: str = "NFL") -> str:
    """Empty Spread/Totals stubs — grade locked PL/XSharp lines."""
    if not html or _EMPTY_MARKET_STUB not in html:
        return html
    if 'data-market-panel="spread"' not in html and 'data-market-panel="totals"' not in html:
        return html
    sport_u = (sport or "NFL").strip().upper()
    finals = _nfl_spread_total_finals(sport_u)
    if not finals:
        return html
    try:
        from mlb_consensus_hub import build_pl_xs_records_html
    except Exception:
        return html
    dates = sorted(
        {
            str(g.get("game_date") or "")[:10]
            for g in finals
            if str(g.get("game_date") or "")[:10]
        }
    )
    page_dates = re.findall(r"Last night \((\d{4}-\d{2}-\d{2})\)", html)
    page_dates += re.findall(
        r"Last Night's [^—<&]{0,40}(?:—|&mdash;|–|&ndash;)\s*(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    page_dates = [d for d in page_dates if d]
    ln_key = max(page_dates) if page_dates else (dates[-1] if dates else None)
    spread = build_pl_xs_records_html(
        finals, "spread", last_night_key=ln_key, sport=sport_u.lower()
    )
    totals = build_pl_xs_records_html(
        finals, "totals", last_night_key=ln_key, sport=sport_u.lower()
    )
    if spread:
        html = _replace_empty_market_panel(html, "spread", spread)
    if totals:
        html = _replace_empty_market_panel(html, "totals", totals)
    return html


def _nfl_ats_mark(grade: str) -> str:
    if grade == "WIN":
        return '<span class="ats-ok">✅</span>'
    if grade == "LOSS":
        return '<span class="ats-no">❌</span>'
    if grade == "PUSH":
        return "<span>PUSH</span>"
    return ""


def _nfl_short_team(name: str) -> str:
    raw = (name or "").strip()
    return _NFL_ABBR_BY_NAME.get(raw) or (raw.split()[-1] if raw else "")


def _nfl_last_night_key(html: str, finals: list[dict]) -> str | None:
    tally = re.findall(
        r"Last Night's[\s\S]{0,80}?(\d{4}-\d{2}-\d{2})",
        html or "",
        flags=re.I,
    )
    if tally:
        return max(tally)
    dates = sorted(
        {
            str(g.get("game_date") or "")[:10]
            for g in finals
            if str(g.get("game_date") or "")[:10]
        }
    )
    return dates[-1] if dates else None


def _nfl_ats_card_html(game: dict, market: str) -> str:
    away = _nfl_short_team(str(game.get("away_team_id") or game.get("away") or ""))
    home = _nfl_short_team(str(game.get("home_team_id") or game.get("home") or ""))
    hs = game.get("home_score")
    aws = game.get("away_score")
    score = f"{aws}–{hs}" if aws is not None and hs is not None else "—"
    dk = str(game.get("game_date") or "")[:10]
    rows = []
    if market == "spread":
        sp = game.get("spread") if isinstance(game.get("spread"), dict) else {}
        for label, pick_key, grade_key in (
            ("Prediction Lab", "pl_pick", "pl_grade"),
            ("XSharp", "xs_pick", "xs_grade"),
        ):
            pick = sp.get(pick_key)
            grade = sp.get(grade_key)
            if not pick or grade not in ("WIN", "LOSS", "PUSH"):
                continue
            rows.append(
                f'<div class="ats-line"><span>{escape(label)}</span> '
                f"<b>{escape(str(pick))}</b> {_nfl_ats_mark(str(grade))}</div>"
            )
    else:
        tot = game.get("totals") if isinstance(game.get("totals"), dict) else {}
        actual = None
        try:
            if hs is not None and aws is not None:
                actual = float(hs) + float(aws)
        except (TypeError, ValueError):
            actual = None
        for label, line_key, grade_key in (
            ("Prediction Lab", "pl_line", "pl_grade"),
            ("XSharp", "xs_line", "xs_grade"),
        ):
            line = tot.get(line_key)
            grade = tot.get(grade_key)
            if line is None or grade not in ("WIN", "LOSS", "PUSH"):
                continue
            try:
                line_s = f"{float(line):g}"
            except (TypeError, ValueError):
                line_s = str(line)
            extra = f" · actual {actual:g}" if actual is not None else ""
            rows.append(
                f'<div class="ats-line"><span>{escape(label)}</span> '
                f"<b>{escape(line_s)}</b> {_nfl_ats_mark(str(grade))}"
                f"<small>{escape(extra)}</small></div>"
            )
    if not rows:
        return ""
    title = "Spread" if market == "spread" else "Total"
    return (
        f'<article class="nfl-ats-card" data-ats-market="{escape(market)}">'
        f'<div class="ats-date">{escape(dk)}</div>'
        f'<div class="ats-match">{escape(away)} @ {escape(home)}</div>'
        f'<div class="ats-score">{escape(score)}</div>'
        f'<div class="ats-kicker">{escape(title)} result</div>'
        f"{''.join(rows)}"
        "</article>"
    )


def _nfl_ats_section(finals: list[dict], market: str, last_night: str | None) -> str:
    games = [
        g
        for g in finals
        if str(g.get("game_date") or "")[:10] == last_night
    ] if last_night else []
    if not games:
        dates = sorted(
            {
                str(g.get("game_date") or "")[:10]
                for g in finals
                if str(g.get("game_date") or "")[:10]
            }
        )
        if dates:
            games = [
                g
                for g in finals
                if str(g.get("game_date") or "")[:10] == dates[-1]
            ]
    cards = [c for c in (_nfl_ats_card_html(g, market) for g in games) if c]
    if not cards:
        return ""
    hid = " hidden" if market != "spread" else ""
    heading = "Spread results" if market == "spread" else "Totals results"
    sid = "nfl-spread-cards" if market == "spread" else "nfl-totals-cards"
    return (
        f'<section class="nfl-ats-cards" id="{sid}"{hid} '
        f'data-ats-market="{escape(market)}">'
        f"<h3>{escape(heading)}</h3>"
        f'<div class="nfl-ats-grid">{"".join(cards)}</div>'
        "</section>"
    )


def _add_html_class(html: str, tag: str, cls: str) -> str:
    m = re.search(rf"<{tag}\b[^>]*>", html or "", flags=re.I)
    if not m:
        return html
    raw = m.group(0)
    if re.search(rf'\bclass="[^"]*\b{re.escape(cls)}\b', raw, flags=re.I):
        return html
    if re.search(r'\bclass="', raw, flags=re.I):
        raw2 = re.sub(r'\bclass="([^"]*)"', rf'class="\1 {cls}"', raw, count=1, flags=re.I)
    else:
        raw2 = raw[:-1] + f' class="{cls}">'
    return html[: m.start()] + raw2 + html[m.end() :]


def _extract_balanced_div_local(html: str, start: int) -> str:
    if start < 0:
        return ""
    i = html.find(">", start)
    if i < 0:
        return ""
    i += 1
    depth = 1
    low = html.lower()
    while i < len(html) and depth:
        nxt_open = low.find("<div", i)
        nxt_close = low.find("</div>", i)
        if nxt_close < 0:
            return html[start:]
        if nxt_open >= 0 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
    return html[start:i]


def _strip_class_divs(html: str, cls: str, *, limit: int = 10) -> str:
    pat = re.compile(
        rf'<div\b[^>]*\bclass="[^"]*\b{re.escape(cls)}\b[^"]*"',
        flags=re.I,
    )
    for _ in range(limit):
        m = pat.search(html or "")
        if not m:
            break
        block = _extract_balanced_div_local(html, m.start())
        if not block:
            break
        html = html.replace(block, "", 1)
    return html


def _strip_heading_wrapper(html: str, heading: str) -> str:
    m = re.search(
        rf"<h[1-6][^>]*>[\s\S]*?{re.escape(heading)}[\s\S]*?</h[1-6]>",
        html or "",
        flags=re.I,
    )
    if not m:
        return html
    start = html.rfind("<div", 0, m.start())
    if start < 0:
        return html[: m.start()] + html[m.end() :]
    block = _extract_balanced_div_local(html, start)
    return html.replace(block, "", 1) if block else html


def _is_nfl_mlb_chart_page(html: str) -> bool:
    """True when /nfl-results?view=chart already used the MLB team_results shell."""
    html = html or ""
    return (
        "team-results.js" in html
        or 'data-ssr-chart="1"' in html
        or 'id="ssr-finals"' in html
        or 'id="ssr-finals"' in html.replace("'", '"')
    )


def _strip_nfl_card_board_for_chart(html: str) -> str:
    """Remove Cards-only boards so ?view=chart is the MLB consensus table.

    Only strip known card-board classes and the Season banner open tag.
    Never walk up to a parent — that deleted the consensus table.
    Never strip daily-tally / daily-tally-grid — those are the chart
    Last Night / Last 7 / Season model cards (\\b matches the hyphen).
    """
    html = html or ""
    if _is_nfl_mlb_chart_page(html):
        return html
    for cls in (
        "date-nav",
        "date-section",
        "game-card",
        "week-section",
        "nfl-ml-board",
        "nfl-ats-cards",
        "model-grid",
    ):
        html = _strip_class_divs(html, cls)
    m = re.search(
        r'<div style="background:#ffffff;border:1px solid rgba\(15,23,42,0\.16\);'
        r'border-radius:14px;padding:22px;margin-bottom:16px;overflow:hidden;">'
        r'\s*<h2[^>]*>[^<]*Season Performance',
        html,
        flags=re.I,
    )
    if m:
        block = _extract_balanced_div_local(html, m.start())
        if block and "Consensus Based Betting Records" not in block:
            html = html.replace(block, "", 1)
    html = re.sub(
        r"<h3[^>]*>[\s\S]*?Moneyline Accuracy by Model[\s\S]*?</h3>",
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"<h2[^>]*>[\s\S]*?Model Performance \(Flat Unit Tracking\)[\s\S]*?</h2>",
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"<p[^>]*>[\s\S]*?Percentages are unit ROI[\s\S]*?</p>",
        "",
        html,
        count=1,
        flags=re.I,
    )
    return html


def _apply_nfl_cards_chart_split(html: str, view: str = "") -> str:
    """Daily MLB-style cards; chart hides the card board. Tighten the 6 tally boxes."""
    if not html:
        return html
    if not any(
        s in html
        for s in (
            "Last Night's NFL Results",
            "Week by Week Results",
            "nfl-results",
            "NFL Results",
        )
    ):
        return html
    daily = (
        "game-card" in html
        or "data-pick-card" in html
        or 'id="date-' in html
    )
    css = """
<style id="nfl-results-layout-css">
.daily-tally-grid{grid-template-columns:repeat(6,minmax(0,1fr))!important;gap:8px}
.daily-tally-card{padding:8px 6px}
.daily-acc{font-size:1.15em}
.model-grid{grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}
.nfl-results-chart .daily-tally,
.nfl-results-chart .date-nav,
.nfl-results-chart .date-section,
.nfl-results-chart .game-card,
.nfl-results-chart [data-pick-card],
.nfl-results-chart .model-grid,
.nfl-results-chart .roi-grid,
.nfl-results-chart .week-section,
.nfl-results-chart .nfl-ml-board,
.nfl-results-chart .nfl-ats-cards{display:none!important}
</style>
"""
    extra = css
    if not daily:
        html = re.sub(
            r'(<div style="background:#ffffff;border:1px solid rgba\(15,23,42,0.16\);'
            r'border-radius:15px;padding:25px;margin-bottom:25px;">)'
            r'(\s*<h2[^>]*>[^<]*Overall Model Performance)',
            r'<div class="nfl-ml-board" style="background:#ffffff;border:1px solid rgba(15,23,42,0.16);'
            r'border-radius:15px;padding:25px;margin-bottom:25px;">\2',
            html,
            count=1,
            flags=re.I,
        )
        finals = _nfl_spread_total_finals()
        ln = _nfl_last_night_key(html, finals)
        extra = (
            css
            + (_nfl_ats_section(finals, "spread", ln) or "")
            + (_nfl_ats_section(finals, "totals", ln) or "")
        )
    if extra.strip():
        if MARKER in html:
            html = html.replace(MARKER, extra + MARKER, 1)
        elif re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(r"</body\s*>", extra + "</body>", html, count=1, flags=re.I)
        else:
            html = html + extra
    if (view or "").strip().lower() == "chart":
        if not _is_nfl_mlb_chart_page(html):
            html = _strip_nfl_card_board_for_chart(html)
        html = _add_html_class(html, "body", "nfl-results-chart")
    return html


def _strip_empty_xsharp_bucket_rows(html: str) -> str:
    """Drop XSharp chart rows that are only — / 0-0 (no graded games)."""

    def _drop(m: re.Match) -> str:
        row = m.group(0)
        cells = re.findall(r"<td>([\s\S]*?)</td>", row)
        plains = [re.sub(r"<[^>]+>", "", c) for c in cells]
        if plains and all(_cell_is_empty_rec(p) for p in plains):
            return ""
        return row

    return re.sub(
        r"<tr>\s*<td class=\"bucket\">\s*XSharp\s*</td>[\s\S]*?</tr>",
        _drop,
        html or "",
        flags=re.I,
    )


def _tokens(name: str) -> list[str]:
    s = html_lib.unescape(name or "").lower()
    s = s.replace("a&m", "am").replace("a & m", "am")
    s = s.replace("st.", "state").replace("&", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return [w for w in s.split() if w and w not in _STOP]


def _school_key(name: str) -> tuple[str, ...]:
    """Stable school/club key: 'Kansas Jayhawks' and 'Kansas' both → (kansas,).

    Shared metros keep the nickname so Giants ≠ Jets and Rams ≠ Chargers.
    """
    words = _tokens(name)
    if not words:
        return ()
    key = [words[0]]
    i = 1
    if words[0] in _DIR_WORDS and len(words) >= 2:
        key.append(words[1])
        i = 2
    elif words[0] == "los" and len(words) >= 2 and words[1] == "angeles":
        key.append("angeles")
        i = 2
    while i < len(words) and words[i] in _QUALIFIERS:
        key.append(words[i])
        i += 1
    if tuple(key) in {("new", "york"), ("los", "angeles")} and i < len(words):
        key.append(words[i])
        i += 1
    return tuple(key)


def _team_core(name: str) -> tuple[str, ...]:
    return _school_key(name)


def _has_chart_control(html: str) -> bool:
    html = html or ""
    if "view=chart" in html:
        return True
    if re.search(r"setPicksView\(\s*['\"]chart['\"]\s*\)", html):
        return True
    if re.search(r'id=["\']pvChartBtn["\']', html):
        return True
    return False


def _view_toggle(sport: str, active: str) -> str:
    slug = RESULTS_SLUGS.get(_sport_key(sport), "")
    if not slug:
        return ""
    cards = "active" if active != "chart" else ""
    chart = "active" if active == "chart" else ""
    return (
        f'<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {cards}" href="/{slug}">Cards</a>'
        f'<a class="pl-view-btn {chart}" href="/{slug}?view=chart">Chart</a>'
        f"</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 16px 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _inject_toggle(html: str, sport: str, view: str) -> str:
    if _has_chart_control(html):
        return html
    block = _view_toggle(sport, view)
    if not block:
        return html
    if re.search(r'<div class="section-tabs">[\s\S]*?</div>', html):
        return re.sub(
            r'(<div class="section-tabs">[\s\S]*?</div>)',
            r"\1" + block,
            html,
            count=1,
        )
    if re.search(r"<main\b", html, flags=re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + block, html, count=1, flags=re.I)
    return block + html


_REC_IN_BLOCK = re.compile(
    r'class="[^"]*(?:daily-model|model-label)[^"]*"[^>]*>\s*(?:[⭐🎯📊🤖🏆⚡📈🎲]\s*)?'
    r"(Grinder2|Takedown|Edge|XSharp|Sharp Consensus|Efficiency|Spread|Over/Under)\s*</div>"
    r'[\s\S]{0,420}?class="[^"]*(?:daily-rec|model-rec)[^"]*"[^>]*>\s*'
    r"(\d+\s*[-–]\s*\d+(?:\s*[-–]\s*\d+)?)",
    flags=re.I,
)


def _recs_from_block(block: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for m in _REC_IN_BLOCK.finditer(block or ""):
        name = m.group(1)
        if name.lower() == "over/under":
            name = "Over/Under"
        elif name.lower() == "spread":
            name = "Spread"
        found[name] = re.sub(r"\s+", "", m.group(2)).replace("–", "-")
    return found


def _scrape_window_records(html: str) -> dict[str, dict[str, str]]:
    """Last night / past 7 / season W-L already rendered on the results page."""
    windows: dict[str, dict[str, str]] = {"ln": {}, "d7": {}, "d30": {}}
    html = html or ""
    h2s = list(re.finditer(r"<h2\b[^>]*>([\s\S]*?)</h2>", html, flags=re.I))
    for i, m in enumerate(h2s):
        title = re.sub(r"<[^>]+>", " ", m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        end = h2s[i + 1].start() if i + 1 < len(h2s) else min(len(html), m.end() + 8000)
        recs = _recs_from_block(html[m.end() : end])
        if not recs:
            continue
        low = title.lower()
        if "last night" in low:
            windows["ln"] = recs
        elif "last 7" in low or "past 7" in low:
            windows["d7"] = recs
        elif "season" in low or "moneyline accuracy" in low:
            windows["d30"].update(recs)
    # Season model cards sit under 🏆 Season Performance with model-label.
    season_m = re.search(
        r"Season Performance[\s\S]{0,8000}",
        html,
        flags=re.I,
    )
    if season_m:
        windows["d30"].update(_recs_from_block(season_m.group(0)))
    return windows


def _cell(value: str) -> str:
    v = (value or "").strip() or "—"
    return escape(v)


def _records_table(
    *,
    title: str,
    subtitle: str,
    rows: list[tuple[str, str, str, str]],
    table_id: str,
) -> str:
    body = []
    for label, ln, d7, d30 in rows:
        body.append(
            "<tr>"
            f'<td class="bucket">{escape(label)}</td>'
            f"<td>{_cell(ln)}</td><td>{_cell(d7)}</td><td>{_cell(d30)}</td>"
            "</tr>"
        )
    return f"""
    <div class="pl-consensus-records pl-live-records" id="{escape(table_id)}">
      <h2>{escape(title)}</h2>
      <p class="sub">{escape(subtitle)}</p>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th style="text-align:left">Agreement</th>
              <th>Last night</th>
              <th>Past 7 days</th>
              <th>Past 30 days</th>
            </tr>
          </thead>
          <tbody>
            {''.join(body)}
          </tbody>
        </table>
      </div>
      <style>
        .pl-consensus-records{{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:14px;
          padding:18px;margin:16px auto 20px;max-width:1100px}}
        .pl-consensus-records h2{{margin:0 0 6px;font-size:1.15rem;color:#0f172a;text-align:center}}
        .pl-consensus-records .sub{{margin:0 0 14px;color:#64748b;font-size:.88rem;text-align:center}}
        .pl-consensus-records table{{width:100%;border-collapse:collapse;font-size:.9rem}}
        .pl-consensus-records th,.pl-consensus-records td{{padding:10px 8px;border-bottom:1px solid #e2e8f0;text-align:center}}
        .pl-consensus-records th{{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#64748b}}
        .pl-consensus-records td.bucket{{text-align:left;font-weight:700;color:#0f172a}}
      </style>
    </div>
    """


def _pick(win: dict[str, str], *names: str) -> str:
    for n in names:
        if win.get(n):
            return win[n]
    return ""


def _build_charts_from_page(html: str) -> str:
    w = _scrape_window_records(html)
    ln, d7, d30 = w["ln"], w["d7"], w["d30"]
    models = (
        "XSharp",
        "Edge",
        "Sharp Consensus",
        "Grinder2",
        "Takedown",
        "Efficiency",
    )
    ml_rows = []
    for name in models:
        a, b, c = _pick(ln, name), _pick(d7, name), _pick(d30, name)
        if a or b or c:
            ml_rows.append((name, a, b, c))
    if not ml_rows:
        return ""
    pl_spread = (
        _pick(ln, "Spread"),
        _pick(d7, "Spread"),
        _pick(d30, "Spread"),
    )
    if not any(pl_spread):
        pl_spread = (
            _pick(ln, "Over/Under"),
            _pick(d7, "Over/Under"),
            _pick(d30, "Over/Under"),
        )
    books_rows = [
        ("Prediction Lab",) + pl_spread,
        (
            "XSharp",
            _pick(ln, "XSharp"),
            _pick(d7, "XSharp"),
            _pick(d30, "XSharp"),
        ),
    ]
    ml = _records_table(
        title="Consensus Based Betting Records",
        subtitle="Model moneyline records from last night, the past 7 days, and the season slate.",
        rows=ml_rows,
        table_id="pl-consensus-records",
    )
    books = _records_table(
        title="Books · Prediction Lab · XSharp",
        subtitle="Prediction Lab (published spread) and XSharp versus the sportsbook line.",
        rows=books_rows,
        table_id="pl-spread-three-way",
    )
    ou = _ou_chart_from_windows(w)
    return ml + books + ou


def _ou_chart_from_windows(w: dict[str, dict[str, str]]) -> str:
    ln, d7, d30 = w["ln"], w["d7"], w["d30"]
    ou_pl = (
        _pick(ln, "Over/Under"),
        _pick(d7, "Over/Under"),
        _pick(d30, "Over/Under"),
    )
    if not any(ou_pl):
        return ""
    return _records_table(
        title="Prediction Lab · XSharp — Totals",
        subtitle="Prediction Lab and XSharp versus the sportsbook total.",
        rows=[
            ("Prediction Lab",) + ou_pl,
            (
                "XSharp",
                _pick(ln, "XSharp"),
                _pick(d7, "XSharp"),
                _pick(d30, "XSharp"),
            ),
        ],
        table_id="pl-totals-three-way",
    )


def _build_ou_chart_from_page(html: str) -> str:
    return _ou_chart_from_windows(_scrape_window_records(html))


def _cell_is_empty_rec(v: str) -> bool:
    t = re.sub(r"\s+", "", v or "")
    return t in ("", "—", "–", "0-0", "0-0-0", "0")


def _bucket_row_empty(html: str, bucket: str) -> bool:
    row = re.search(
        rf'<td class="bucket">\s*{re.escape(bucket)}\s*</td>\s*'
        r"<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>",
        html or "",
        flags=re.I,
    )
    if not row:
        return False
    return all(_cell_is_empty_rec(row.group(i)) for i in (1, 2, 3))


_FOUR_MODEL_MEANINGS = (
    ("4/4", "Unanimous"),
    ("3/4", "Strong consensus"),
    ("2/4", "Split / no consensus"),
    ("1/4", "Strong disagreement"),
)
_FOUR_MODEL_SPORTS = frozenset({"WNBA", "NBA"})
_FOUR_MODEL_NAMES = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")


def _four_model_games_from_cards(html: str) -> list[dict]:
    """One row per results card: date, 4/4|3/4|2/4|1/4, WIN/LOSS/PUSH."""
    html = html or ""
    games: list[dict] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html)
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        parts = re.split(r'(<div class="game-card\b[^"]*"[^>]*>)', content, flags=re.I)
        idx = 1
        while idx < len(parts):
            body = parts[idx + 1] if idx + 1 < len(parts) else ""
            idx += 2
            boxes = re.findall(
                r'class="pc-name">([^<]+)</div>\s*'
                r'<div class="pc-val">([^<]+)</div>\s*'
                r'<div class="pc-side[^"]*"[^>]*>([^<]+)</div>',
                body[:25000],
            )
            picks: dict[str, tuple[str, bool | None]] = {}
            for name, _pct, side in boxes:
                name = re.sub(r"[^A-Za-z ]+", "", name).strip()
                if name not in _FOUR_MODEL_NAMES:
                    continue
                ok = True if "✅" in side else False if "❌" in side else None
                picks[name] = (re.sub(r"[✅❌]", "", side).strip(), ok)
            sides = [picks[n][0] for n in _FOUR_MODEL_NAMES if n in picks and picks[n][0]]
            if len(sides) < 2:
                continue
            counts = Counter(sides)
            top_n = counts.most_common(1)[0][1]
            n = len(sides)
            if n >= 4:
                level = "4/4" if top_n == 4 else "3/4" if top_n == 3 else "2/4"
            elif n == 3:
                level = "3/4" if top_n == 3 else "2/4"
            else:
                level = "2/4"
            if level == "2/4" and top_n * 2 == n:
                grade = "PUSH"
            else:
                maj = counts.most_common(1)[0][0]
                maj_ok = next(
                    (
                        picks[nm][1]
                        for nm in _FOUR_MODEL_NAMES
                        if nm in picks and picks[nm][0] == maj
                    ),
                    None,
                )
                if maj_ok is True:
                    grade = "WIN"
                elif maj_ok is False:
                    grade = "LOSS"
                else:
                    continue
            if level == "3/4" and top_n == 1:
                level = "1/4"
            games.append({"date": dk, "level": level, "grade": grade})
    return games


def _wl_cell_from_grades(grades: list[str]) -> str:
    w = sum(1 for g in grades if g == "WIN")
    l = sum(1 for g in grades if g == "LOSS")
    p = sum(1 for g in grades if g == "PUSH")
    decided = w + l
    if decided == 0 and p == 0:
        return "0-0"
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    if decided == 0:
        return rec
    pct = int(round(100.0 * w / decided))
    color = "#00C076" if pct >= 55 else "#D93025" if pct < 47 else "#ca8a04"
    return (
        f"{rec} <span style='color:{color};font-weight:700'>({pct}%)</span>"
        f"<div class='cons-bar' aria-hidden='true'>"
        f"<i style='width:{pct}%;background:{color}'></i></div>"
    )


def _plain_rec_cell(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"&mdash;|&ndash;|&nbsp;", " ", text, flags=re.I)
    return re.sub(r"\s+", "", text).replace("—", "").replace("–", "")


def _rec_cell_is_empty(raw: str) -> bool:
    plain = _plain_rec_cell(raw)
    return plain in ("", "0-0", "-", "—", "–")


def _rec_cell_has_result(raw: str) -> bool:
    if _rec_cell_is_empty(raw):
        return False
    return bool(re.search(r"\d+-\d+", _plain_rec_cell(raw)))


def _last_night_is_recent(html: str, *, days: int = 14) -> bool:
    m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", html or "")
    if not m:
        return False
    try:
        ln = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return False
    return (datetime.now().date() - ln).days <= days


def four_model_chart_mismatches(html: str, cards_html: str | None = None) -> list[str]:
    """FAIL reasons: chart 2/4 is 0-0 while cards split, or Past 7 drops last night."""
    html = html or ""
    cards = cards_html or html
    issues: list[str] = []
    games = _four_model_games_from_cards(cards)
    m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", html)
    ln_key = m.group(1) if m else ""
    if games and ln_key:
        ln_games = [g for g in games if g["date"] == ln_key]
        split_n = sum(1 for g in ln_games if g["level"] == "2/4")
        row = re.search(
            r"2/4 Split / no consensus</td>\s*<td>([\s\S]*?)</td>",
            html,
            flags=re.I,
        )
        # Only when the page actually publishes a 2/4 row (4-model sports).
        if split_n and row and _rec_cell_is_empty(row.group(1)):
            issues.append(
                f"Cards have {split_n} last-night 2/4 split game(s) but the chart shows 0-0"
            )
    for row in re.finditer(
        r'<td class="(?:bucket|signal)">([^<]+)</td>\s*'
        r"<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>",
        html,
        flags=re.I,
    ):
        label = re.sub(r"\s+", " ", row.group(1)).strip()
        if _rec_cell_has_result(row.group(2)) and _rec_cell_is_empty(row.group(3)):
            issues.append(
                f"{label}: last night has a record but Past 7 days is empty "
                "(last night must count in Past 7)"
            )
    if (
        len(re.findall(r"Spread pick", cards, flags=re.I)) >= 2
        and "No graded games for this market" in html
        and _last_night_is_recent(html)
        and any(
            _rec_cell_has_result(m.group(2))
            for m in re.finditer(
                r'<td class="(?:bucket|signal)">([^<]+)</td>\s*<td>([\s\S]*?)</td>',
                html,
                flags=re.I,
            )
        )
    ):
        issues.append("Cards have spread picks but the Spread chart says no graded games")
    return issues


def _apply_four_model_consensus_meanings(html: str, cards_html: str | None = None) -> str:
    """Label 4-model consensus rows with Unanimous / Strong / Split / Disagreement."""
    if not html or "Consensus Based Betting Records" not in html:
        return html
    if (
        "among the 4 live models" not in html
        and "4/4 unanimous" not in html.lower()
        and "2/4 Split / no consensus" not in html
    ):
        return html
    start = html.find("Consensus Based Betting Records")
    end = html.find("PL vs Sportsbook", start)
    if start < 0:
        return html
    if end < 0:
        end = start + 12000
    block = html[start:end]
    block = re.sub(r"4/4 unanimous", "4/4 Unanimous", block, flags=re.I)
    block = re.sub(
        r"3/4\s*(?:—|-)\s*all but",
        "3/4 Strong consensus — all but",
        block,
        flags=re.I,
    )
    block = re.sub(
        r"3/4\s*(?:—|-)\s*one dissent",
        "3/4 Strong consensus",
        block,
        flags=re.I,
    )
    block = re.sub(
        r"2/4\s*(?:—|-)?\s*split",
        "2/4 Split / no consensus",
        block,
        flags=re.I,
    )
    block = re.sub(
        r"1/4\s*(?:—|-)?\s*(?:strong )?disagreement",
        "1/4 Strong disagreement",
        block,
        flags=re.I,
    )
    if "cons-meaning" not in block:
        legend = (
            '<p class="cons-meaning">'
            "<span><b>4/4</b> Unanimous</span>"
            "<span><b>3/4</b> Strong consensus</span>"
            "<span><b>2/4</b> Split / no consensus</span>"
            "<span><b>1/4</b> Strong disagreement</span>"
            "</p>"
            "<style>.cons-meaning{display:flex;flex-wrap:wrap;justify-content:center;"
            "gap:8px 16px;margin:0 0 12px;color:#64748b;font-size:.8rem}"
            ".cons-meaning b{color:#0f172a;margin-right:4px}</style>"
        )
        block = re.sub(
            r'(<p class="sub">[\s\S]*?</p>)',
            r"\1" + legend,
            block,
            count=1,
        )
    rec = re.search(
        r'(<table>[\s\S]*?Last night[\s\S]*?<tbody>)([\s\S]*?)(</tbody>)',
        block,
        flags=re.I,
    )
    if rec:
        body = rec.group(2)
        empty = "<td>0-0</td><td>0-0</td><td>0-0</td>"
        extras = []
        if not re.search(r"3/4 Strong consensus|3/4\s", body):
            extras.append(
                f'<tr><td class="bucket">3/4 Strong consensus</td>{empty}</tr>'
            )
        if "2/4 Split / no consensus" not in body:
            extras.append(
                f'<tr><td class="bucket">2/4 Split / no consensus</td>{empty}</tr>'
            )
        if "1/4 Strong disagreement" not in body:
            extras.append(
                f'<tr><td class="bucket">1/4 Strong disagreement</td>{empty}</tr>'
            )
        if extras:
            block = (
                block[: rec.start(3)]
                + "".join(extras)
                + block[rec.start(3) :]
            )
        games = _four_model_games_from_cards(cards_html or html)
        ln_m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", block)
        if games and ln_m:
            ln_key = ln_m.group(1)
            try:
                ln_d = datetime.strptime(ln_key, "%Y-%m-%d").date()
            except ValueError:
                ln_d = None
            if ln_d is not None:
                cut7 = (ln_d - timedelta(days=6)).isoformat()
                cut30 = (ln_d - timedelta(days=29)).isoformat()
                filled: list[str] = []
                for key, meaning in _FOUR_MODEL_MEANINGS:
                    ln_g = [
                        g["grade"]
                        for g in games
                        if g["date"] == ln_key and g["level"] == key
                    ]
                    d7_g = [
                        g["grade"]
                        for g in games
                        if cut7 <= g["date"] <= ln_key and g["level"] == key
                    ]
                    d30_g = [
                        g["grade"]
                        for g in games
                        if cut30 <= g["date"] <= ln_key and g["level"] == key
                    ]
                    filled.append(
                        "<tr>"
                        f'<td class="bucket">{key} {meaning}</td>'
                        f"<td>{_wl_cell_from_grades(ln_g)}</td>"
                        f"<td>{_wl_cell_from_grades(d7_g)}</td>"
                        f"<td>{_wl_cell_from_grades(d30_g)}</td>"
                        "</tr>"
                    )
                rec2 = re.search(
                    r'(<table>[\s\S]*?Last night[\s\S]*?<tbody>)([\s\S]*?)(</tbody>)',
                    block,
                    flags=re.I,
                )
                if rec2:
                    block = block[: rec2.start(2)] + "".join(filled) + block[rec2.end(2) :]
                block = block.replace(
                    "Even splits are omitted.",
                    "2/4 even splits are counted as pushes (W-L-P).",
                )
    return html[:start] + block + html[end:]


def _set_row_data_col(block: str, label: str, col_idx: int, cell_html: str) -> str:
    """Replace data column col_idx (0=last night, 1=Past 7, 2=Past 30) for a row."""

    def repl(m: re.Match[str]) -> str:
        tds = re.findall(r"<td(?:\s[^>]*)?>[\s\S]*?</td>", m.group(0))
        if len(tds) < 4:
            return m.group(0)
        target = col_idx + 1
        if target >= len(tds):
            return m.group(0)
        tds[target] = f"<td>{cell_html}</td>"
        return "<tr>" + "".join(tds) + "</tr>"

    return re.sub(
        rf'<tr>\s*<td class="(?:bucket|signal)">\s*{re.escape(label)}\s*</td>'
        r"\s*<td>[\s\S]*?</td>\s*<td>[\s\S]*?</td>\s*<td>[\s\S]*?</td>\s*</tr>",
        repl,
        block,
        count=1,
        flags=re.I,
    )


def _apply_last_night_windows(html: str, cards_html: str | None = None) -> str:
    """Past 7 / Past 30 for unlocked sports are last-night-relative, not today."""
    html = html or ""
    ln_m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", html)
    if not ln_m:
        return html
    ln_key = ln_m.group(1)
    try:
        ln_d = datetime.strptime(ln_key, "%Y-%m-%d").date()
    except ValueError:
        return html
    cut7 = (ln_d - timedelta(days=6)).isoformat()
    cards = cards_html or html
    rows: list = []
    pl_vs_rows: list = []
    try:
        from mlb_consensus_hub import (
            _consensus_record_cell,
            _extract_game_rows,
            _grade_items,
            _pl_vs_books_rows_from_finals,
            _pl_vs_books_slices,
            _plxs_items_from_finals,
        )
        rows = _extract_game_rows(cards, limit=2000, prefer_date=ln_key)
        # Card extract often has no book ML. NFL lock snapshots do —
        # use those for PL vs Sportsbook so Last Night Books is not 0-0.
        pl_vs_rows = rows
        if re.search(r"Last Night's NFL Results", cards or html, flags=re.I):
            nfl_rows = _nfl_consensus_finals(cards or html)
            if nfl_rows:
                pl_vs_rows = nfl_rows
        elif re.search(r"Last Night's CFL Results", cards or html, flags=re.I):
            try:
                from team_tabbed_results import build_cfl_payload

                cfl_rows = (build_cfl_payload() or {}).get("finals") or []
            except Exception:
                cfl_rows = []
            if cfl_rows:
                pl_vs_rows = cfl_rows
                rows = cfl_rows
        elif not rows:
            rows = _nfl_consensus_finals(cards or html)
            pl_vs_rows = rows
    except Exception:
        _consensus_record_cell = None  # type: ignore
        _grade_items = None  # type: ignore
        _pl_vs_books_rows_from_finals = None  # type: ignore
        _pl_vs_books_slices = None  # type: ignore
        _plxs_items_from_finals = None  # type: ignore

    def in7(d: str) -> bool:
        return cut7 <= (d or "")[:10] <= ln_key

    def on_ln(d: str) -> bool:
        return (d or "")[:10] == ln_key

    pv_all = (
        _pl_vs_books_rows_from_finals(pl_vs_rows)
        if pl_vs_rows and _pl_vs_books_rows_from_finals
        else []
    )
    pv7 = [a for a in pv_all if in7(a.get("game_date") or "")]
    pv_ln = [a for a in pv_all if on_ln(a.get("game_date") or "")]
    if pv7 or pv_ln:
        slices7 = _pl_vs_books_slices(pv7) if pv7 else None
        slices_ln = _pl_vs_books_slices(pv_ln) if pv_ln else None
        start = html.find("PL vs Sportsbook")
        end = html.find("Prediction Lab & XSharp", start) if start >= 0 else -1
        if start >= 0:
            if end < 0:
                end = start + 8000
            block = html[start:end]
            mapping = (
                ("Books favorite", "books"),
                ("PL favorite", "pl"),
                ("PL vs Books disagree", "books_pl_disagree"),
                ("PL and Books agree", "books_pl_agree"),
            )
            for label, key in mapping:
                if slices_ln:
                    graded_ln = _grade_items(slices_ln[key])
                    if graded_ln:
                        cell = _consensus_record_cell(
                            graded_ln, bar=True, empty="0-0"
                        )
                        block = _set_row_data_col(block, label, 0, cell)
                if slices7:
                    graded7 = _grade_items(slices7[key])
                    if graded7:
                        cell = _consensus_record_cell(
                            graded7, bar=True, empty="0-0"
                        )
                        block = _set_row_data_col(block, label, 1, cell)
            html = html[:start] + block + html[end:]

    for market, marker in (
        ("spread", 'id="pl-spread-records"'),
        ("totals", 'id="pl-totals-records"'),
    ) if rows and _plxs_items_from_finals and _consensus_record_cell else ():
        mi = html.find(marker)
        if mi < 0:
            continue
        start = html.rfind("<div", 0, mi + 1)
        if start < 0:
            start = mi
        nxt = html.find('id="pl-', mi + len(marker))
        end = nxt if nxt > 0 else min(len(html), start + 8000)
        # Don't swallow the next chart's opening tag.
        if nxt > 0:
            end = html.rfind("<div", start + 1, nxt)
            if end <= start:
                end = nxt
        block = html[start:end]
        all_items = (
            _plxs_items_from_finals(rows, market)
            if rows and _plxs_items_from_finals
            else []
        )
        items7 = [a for a in all_items if in7(a.get("game_date") or "")]
        items_ln = [a for a in all_items if on_ln(a.get("game_date") or "")]
        for model in ("Prediction Lab", "XSharp"):
            if items_ln:
                cell = _consensus_record_cell(
                    [a for a in items_ln if a.get("model") == model],
                    bar=True,
                    empty="0-0",
                )
                block = _set_row_data_col(block, model, 0, cell)
            if items7:
                cell = _consensus_record_cell(
                    [a for a in items7 if a.get("model") == model],
                    bar=True,
                    empty="0-0",
                )
                block = _set_row_data_col(block, model, 1, cell)
        html = html[:start] + block + html[end:]

    # Last night is inside Past 7. If a row still shows a real last-night
    # W-L and an empty Past 7, copy last night into that cell.
    def _copy_ln_row(m: re.Match[str]) -> str:
        cls, label, ln_cell, d7_cell, d30_cell = (
            m.group(1),
            m.group(2),
            m.group(3),
            m.group(4),
            m.group(5),
        )
        if _rec_cell_has_result(ln_cell) and _rec_cell_is_empty(d7_cell):
            return (
                f'<td class="{cls}">{label}</td>'
                f"<td>{ln_cell}</td><td>{ln_cell}</td><td>{d30_cell}</td>"
            )
        return m.group(0)

    html = re.sub(
        r'<td class="(bucket|signal)">([^<]+)</td>\s*'
        r"<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>",
        _copy_ln_row,
        html,
        flags=re.I,
    )
    return html


def _has_signed_off_consensus_charts(html: str) -> bool:
    """MLB-shaped charts: dissent combos + PL vs Sportsbook with color bars."""
    html = html or ""
    if "Consensus Based Betting Records" not in html:
        return False
    if not re.search(r"unanimous|all but|Strong consensus", html, flags=re.I):
        return False
    if "cons-bar" not in html:
        return False
    if "PL vs Sportsbook" not in html:
        return False
    if "Books favorite" not in html or "PL favorite" not in html:
        return False
    return True


def _charts_are_empty_stub(html: str) -> bool:
    html = html or ""
    if _has_signed_off_consensus_charts(html):
        return False
    if "Model moneyline records on this results slate" in html:
        return True
    if "Books · Prediction Lab · XSharp" in html and _bucket_row_empty(
        html, "Prediction Lab"
    ):
        return True
    row = re.search(
        r'<td class="bucket">\s*XSharp\s*</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>',
        html,
        flags=re.I,
    )
    if not row:
        return False
    return all(_cell_is_empty_rec(row.group(i)) for i in (1, 2, 3))


def _strip_injected_charts(html: str) -> str:
    html = html or ""
    html = re.sub(
        r'<div class="pl-consensus-records[^"]*"[^>]*>[\s\S]*?<style>[\s\S]*?</style>\s*</div>',
        "",
        html,
        flags=re.I,
        count=6,
    )
    return html


def _ensure_books_title(html: str) -> str:
    html = html or ""
    if "Books · Prediction Lab · XSharp" in html:
        return html
    html = html.replace("Prediction Lab & XSharp — Spread", "Books · Prediction Lab · XSharp")
    html = html.replace("Prediction Lab & XSharp — Run Line", "Books · Prediction Lab · XSharp")
    html = html.replace("Prediction Lab & XSharp — Puck Line", "Books · Prediction Lab · XSharp")
    return html


def _insert_charts(html: str, block: str) -> str:
    if not block:
        return html
    m = re.search(
        r'(<(?:div|section)\b[^>]*(?:id="tallies"|class="(?:daily-tally|games-grid|week-section|tally-wrap)\b))',
        html,
        flags=re.I,
    )
    if m:
        return html[: m.start(1)] + block + html[m.start(1) :]
    m = re.search(r'(<nav class="market-tabs")', html, flags=re.I)
    if m:
        return html[: m.start(1)] + block + html[m.start(1) :]
    m = re.search(r'(<div class="pl-view-toggle")', html, flags=re.I)
    if m:
        end = html.find("</style>", m.start())
        if end > 0:
            return html[: end + len("</style>")] + block + html[end + len("</style>") :]
    if re.search(r"<main\b", html, flags=re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + block, html, count=1, flags=re.I)
    return html + block


def _six_model_chart_broken(html: str) -> bool:
    """True when consensus is not the MLB 6/6 dissent table."""
    html = html or ""
    if re.search(r"among the [345] live models|[345] live models", html, flags=re.I):
        return True
    if "2/4 Split / no consensus" in html or "1/4 Strong disagreement" in html:
        return True
    if re.search(r"\b3/3\b", html) and not re.search(r"\b6/6\b", html):
        return True
    if re.search(r"\b4/4\b", html) and not re.search(r"\b6/6\b", html):
        return True
    if re.search(r"\b5/5\b", html) and not re.search(r"\b6/6\b", html):
        return True
    return False


def _sync_last_night_dates(html: str) -> str:
    """One Last Night date across moneyline / spread / totals headings."""
    html = html or ""
    dates = re.findall(r"Last night \((\d{4}-\d{2}-\d{2})\)", html)
    dates += re.findall(
        r"Last Night's [^—<&]{0,40}(?:—|&mdash;|–|&ndash;)\s*(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    dates = [d for d in dates if d]
    if len(set(dates)) < 2:
        return html
    tally = re.findall(
        r"Last Night's [^—<&]{0,40}(?:—|&mdash;|–|&ndash;)\s*(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    canon = tally[0] if tally else max(dates)
    html = re.sub(r"Last night \(\d{4}-\d{2}-\d{2}\)", f"Last night ({canon})", html)
    html = re.sub(
        r"(Last Night's [^—<&]{0,40}(?:—|&mdash;|–|&ndash;)\s*)\d{4}-\d{2}-\d{2}",
        rf"\g<1>{canon}",
        html,
        flags=re.I,
    )
    return html


def _hide_blank_ml_tally_cards(html: str, names: tuple[str, ...]) -> str:
    """Drop G2 / Takedown / Efficiency tally tiles that are only dashes."""
    if not html or not names:
        return html
    names_pat = "|".join(re.escape(n) for n in names)
    html = re.sub(
        rf'<div class="daily-tally-card[^"]*"[\s\S]{{0,120}}>'
        rf'\s*<div class="daily-model">[^<]*?(?:{names_pat})\s*</div>\s*'
        rf'<div class="daily-acc"[^>]*>\s*(?:—|&mdash;|&ndash;)\s*</div>\s*'
        rf'<div class="daily-rec">\s*(?:—|&mdash;|&ndash;)\s*</div>\s*'
        rf"</div>",
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        rf'<div class="model-card[^"]*"[\s\S]{{0,120}}>'
        rf'\s*<div class="model-label">[^<]*?(?:{names_pat})\s*</div>\s*'
        rf'<div class="model-acc"[^>]*>\s*(?:—|&mdash;|&ndash;)\s*</div>\s*'
        rf'<div class="model-rec">\s*(?:—|&mdash;|&ndash;)\s*</div>\s*'
        rf"</div>",
        "",
        html,
        flags=re.I,
    )
    return html


def _ncaaf_edge_fifty_to_na(html: str) -> str:
    """Unattached Edge 50% is a placeholder — show N/A, do not invent a lean."""

    def repl(m: re.Match[str]) -> str:
        val = m.group(2).strip()
        if val in ("50%", "50.0%", "50.00%"):
            return f"{m.group(1)}N/A</div>"
        return m.group(0)

    return re.sub(
        r'(<div class="pc-name">\s*Edge\s*</div>\s*<div class="pc-val">)([^<]+)</div>',
        repl,
        html or "",
        flags=re.I,
    )


_NCAAF_ELO_K = 30.0
_NCAAF_ELO_CACHE: tuple[float, dict[str, float]] | None = None
_NCAAF_ELO_TTL = 600.0


def ncaaf_elo_is_placeholder(val) -> bool:
    """True when Edge was stored as an untrained 50/50, not a real lean."""
    try:
        n = float(val)
    except (TypeError, ValueError):
        return False
    if abs(n) <= 1.0 + 1e-9:
        n *= 100.0
    return abs(n - 50.0) < 0.051


def _ncaaf_elo_lookup(ratings: dict[str, float], name: str) -> tuple[float, bool]:
    if name in ratings:
        return ratings[name], True
    key = _cfb_team_key(name)
    if not key:
        return 1500.0, False
    for team, rating in ratings.items():
        if _cfb_team_key(team) == key:
            return rating, True
    return 1500.0, False


def ncaaf_elo_ratings() -> dict[str, float]:
    """Walk-forward NCAAF Elo (K=30) from completed games. Same trainer as picks."""
    global _NCAAF_ELO_CACHE
    now = time.time()
    if _NCAAF_ELO_CACHE and (now - _NCAAF_ELO_CACHE[0]) < _NCAAF_ELO_TTL:
        return _NCAAF_ELO_CACHE[1]
    ratings: dict[str, float] = {}
    path = _db_path()
    if path.is_file():
        try:
            con = sqlite3.connect(str(path))
            rows = con.execute(
                """
                SELECT home_team_id, away_team_id, home_score, away_score
                FROM games
                WHERE sport = 'NCAAF' AND home_score IS NOT NULL AND away_score IS NOT NULL
                ORDER BY date(game_date), game_id
                """
            ).fetchall()
            con.close()
        except Exception:
            rows = []
        for home, away, hs, aws in rows:
            hr = ratings.get(home, 1500.0)
            ar = ratings.get(away, 1500.0)
            expected = 1.0 / (1.0 + 10.0 ** ((ar - hr) / 400.0))
            try:
                actual = 1.0 if float(hs) > float(aws) else 0.0
            except (TypeError, ValueError):
                continue
            ratings[home] = hr + _NCAAF_ELO_K * (actual - expected)
            ratings[away] = ar + _NCAAF_ELO_K * ((1.0 - actual) - (1.0 - expected))
    _NCAAF_ELO_CACHE = (now, ratings)
    return ratings


def ncaaf_live_elo_home_pct(home: str, away: str) -> float | None:
    """Home win % from trained NCAAF Elo. None when both teams are unrated."""
    if not home or not away:
        return None
    ratings = ncaaf_elo_ratings()
    hr, h_known = _ncaaf_elo_lookup(ratings, home)
    ar, a_known = _ncaaf_elo_lookup(ratings, away)
    if not h_known and not a_known:
        return None
    pct = 100.0 / (1.0 + 10.0 ** ((ar - hr) / 400.0))
    return round(pct, 1)


def _ncaaf_decode(text: str) -> str:
    raw = html_lib.unescape(html_lib.unescape(text or ""))
    return raw.replace("&nbsp;", " ").strip()


def _ncaaf_parse_proj(text: str, home: str, away: str) -> tuple[float, float] | None:
    raw = _ncaaf_decode(text)
    m = re.match(
        r"^(.+?)\s+(-?\d+(?:\.\d+)?)\s+[–—-]\s+(.+?)\s+(-?\d+(?:\.\d+)?)\s*$",
        raw,
    )
    if not m:
        return None
    n1, p1, n2, p2 = m.group(1).strip(), float(m.group(2)), m.group(3).strip(), float(m.group(4))
    hkey, akey = _cfb_team_key(home), _cfb_team_key(away)
    if hkey and _cfb_team_key(n2) == hkey:
        return p2, p1
    if hkey and _cfb_team_key(n1) == hkey:
        return p1, p2
    if akey and _cfb_team_key(n1) == akey:
        return p2, p1
    return p2, p1


def _ncaaf_fmt_half(value: float) -> str:
    n = round(float(value) * 2.0) / 2.0
    if n == int(n):
        return str(int(n))
    return f"{n:.1f}"


def _ncaaf_fmt_named_spread(home: str, away: str, home_centric: float) -> str:
    if home_centric >= 0:
        team, mag = home, home_centric
    else:
        team, mag = away, -home_centric
    return f"{team} -{_ncaaf_fmt_half(mag)}"


def _ncaaf_short(name: str) -> str:
    parts = (name or "").split()
    return parts[-1] if parts else name


def _fill_ncaaf_card_gaps(html: str) -> str:
    """Fill blank Odds & Lines and placeholder Edge 50% from published proj / Elo."""
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        hm = re.search(r'data-home="([^"]*)"', open_tag, flags=re.I)
        am = re.search(r'data-away="([^"]*)"', open_tag, flags=re.I)
        home = _ncaaf_decode(hm.group(1) if hm else "")
        away = _ncaaf_decode(am.group(1) if am else "")
        pl_proj = ""
        xs_proj = ""
        pm = re.search(r'data-pl-proj="([^"]*)"', open_tag, flags=re.I)
        xm = re.search(r'data-xs-proj="([^"]*)"', open_tag, flags=re.I)
        if pm:
            pl_proj = pm.group(1)
        if xm:
            xs_proj = xm.group(1)
        if not pl_proj:
            tm = re.search(
                r'<span class="proj-model pl">[\s\S]*?</span>\s*<span class="proj-val">([^<]+)',
                rest,
                flags=re.I,
            )
            if tm:
                pl_proj = tm.group(1)
        if not xs_proj:
            tm = re.search(
                r'<span class="proj-model xs">[\s\S]*?</span>\s*<span class="proj-val">([^<]+)',
                rest,
                flags=re.I,
            )
            if tm:
                xs_proj = tm.group(1)
        pl_pts = _ncaaf_parse_proj(pl_proj, home, away) if pl_proj else None
        xs_pts = _ncaaf_parse_proj(xs_proj, home, away) if xs_proj else None
        if xs_pts is None:
            xs_pts = pl_pts
        if xs_pts is not None and home and away:
            xs_spread = _ncaaf_fmt_named_spread(home, away, xs_pts[0] - xs_pts[1])
            rest = re.sub(
                r'(<td class="market-k">\s*Spread\s*</td>\s*'
                r'<td class="val-books">[\s\S]*?</td>\s*'
                r'<td class="val-pl">[\s\S]*?</td>\s*'
                r'<td class="val-xs">)\s*(?:—|&mdash;|&ndash;|–|-)\s*(</td>)',
                lambda m, _s=xs_spread: f"{m.group(1)}{escape(_s)}{m.group(2)}",
                rest,
                count=1,
                flags=re.I,
            )
            if re.search(r'data-xs-spread=""', open_tag, flags=re.I):
                open_tag = re.sub(
                    r'data-xs-spread=""',
                    f'data-xs-spread="{escape(xs_spread, quote=True)}"',
                    open_tag,
                    count=1,
                    flags=re.I,
                )
        if pl_pts is not None:
            pl_total = _ncaaf_fmt_half(pl_pts[0] + pl_pts[1])
            rest = re.sub(
                r'(<td class="market-k">\s*Total\s*</td>\s*'
                r'<td class="val-books">[\s\S]*?</td>\s*'
                r'<td class="val-pl">)\s*(?:—|&mdash;|&ndash;|–|-)\s*(</td>)',
                lambda m, _t=pl_total: f"{m.group(1)}{_t}{m.group(2)}",
                rest,
                count=1,
                flags=re.I,
            )
        if xs_pts is not None:
            xs_total = _ncaaf_fmt_half(xs_pts[0] + xs_pts[1])
            rest = re.sub(
                r'(<td class="market-k">\s*Total\s*</td>\s*'
                r'<td class="val-books">[\s\S]*?</td>\s*'
                r'<td class="val-pl">[\s\S]*?</td>\s*'
                r'<td class="val-xs">)\s*(?:—|&mdash;|&ndash;|–|-)\s*(</td>)',
                lambda m, _t=xs_total: f"{m.group(1)}{_t}{m.group(2)}",
                rest,
                count=1,
                flags=re.I,
            )
        edge_m = re.search(
            r'<div class="pc-name">\s*Edge\s*</div>\s*<div class="pc-val"[^>]*>\s*([^<]+)',
            rest,
            flags=re.I,
        )
        edge_raw = (edge_m.group(1) if edge_m else "").strip().rstrip("%")
        attr_m = re.search(r'data-m-edge="([^"]*)"', open_tag, flags=re.I)
        if attr_m and not edge_raw:
            edge_raw = attr_m.group(1)
        if ncaaf_elo_is_placeholder(edge_raw) and home and away:
            live = ncaaf_live_elo_home_pct(home, away)
            if live is None or ncaaf_elo_is_placeholder(live):
                pl_line = ""
                lm = re.search(r'data-pl-spread="([^"]*)"', open_tag, flags=re.I)
                if lm:
                    pl_line = _ncaaf_decode(lm.group(1))
                if not pl_line:
                    tm = re.search(
                        r'<td class="market-k">\s*Spread\s*</td>\s*'
                        r'<td class="val-books">[\s\S]*?</td>\s*'
                        r'<td class="val-pl">([^<]+)',
                        rest,
                        flags=re.I,
                    )
                    if tm:
                        pl_line = _ncaaf_decode(tm.group(1))
                hc = _nfl_home_centric_spread(pl_line, home, away) if pl_line else None
                if hc is not None:
                    try:
                        from sports.team_efficiency_attach import spread_to_home_prob_pct

                        live = float(spread_to_home_prob_pct(hc, "NCAAF"))
                    except Exception:
                        live = None
            if live is not None and not ncaaf_elo_is_placeholder(live):
                if live >= 50:
                    face, side, cls, title = live, _ncaaf_short(home), "home", home
                else:
                    face, side, cls, title = round(100.0 - live, 1), _ncaaf_short(away), "away", away
                if face == int(face):
                    face_s = f"{int(face)}.0"
                else:
                    face_s = f"{face:.1f}"
                rest = re.sub(
                    r'(<div class="pc-name">\s*Edge\s*</div>\s*)'
                    r'<div class="pc-val"[^>]*>\s*50(?:\.0+)?%\s*</div>\s*'
                    r'<div class="pc-side[^"]*"[^>]*>[\s\S]*?</div>',
                    (
                        r"\1"
                        f'<div class="pc-val">{face_s}%</div>'
                        f'<div class="pc-side {cls}" title="{escape(title)}">'
                        f"{escape(side)}</div>"
                    ),
                    rest,
                    count=1,
                    flags=re.I,
                )
                if attr_m:
                    open_tag = re.sub(
                        r'data-m-edge="[^"]*"',
                        f'data-m-edge="{live:.1f}"',
                        open_tag,
                        count=1,
                        flags=re.I,
                    )
        out.append(open_tag + rest)
    return "".join(out)


def _hide_blank_books_ml_lines(html: str) -> str:
    """Hide Books face lines that have no posted number."""
    return re.sub(
        r'<div class="ml-line[^"]*">\s*'
        r'<span class="ml-src books">[^<]*(?:<span\b[^>]*>[^<]*</span>[^<]*)?</span>\s*'
        r'<span class="ml-num[^"]*">\s*(?:—|&mdash;|&ndash;)\s*</span>\s*'
        r"</div>",
        "",
        html or "",
        flags=re.I,
    )


def apply_team_results_template(html: str, sport: str, view: str = "") -> str:
    sport_u = _sport_key(sport)
    if sport_u not in RESULTS_SLUGS or not html or "<" not in html:
        return html
    view_l = (view or "").strip().lower()
    html = _inject_toggle(html, sport_u, view_l)
    six_wrong_panel = sport_u in ("NFL", "NCAAF", "CFL") and _six_model_chart_broken(html)
    cfl_needs_inject = False
    if sport_u == "CFL":
        rec = re.search(
            r"Books favorite[\s\S]{0,160}?\b(\d{1,3}-\d{1,3})\b",
            html or "",
            flags=re.I,
        )
        cfl_needs_inject = (not rec) or rec.group(1) == "0-0"
        if "No graded games for this market" in (html or ""):
            cfl_needs_inject = True
    if six_wrong_panel or not _has_signed_off_consensus_charts(html) or cfl_needs_inject:
        source = html
        if view_l == "chart":
            cards = _CHART_SOURCE_HTML.get(sport_u) or ""
            if cards and ("daily-model" in cards or "Season Performance" in cards):
                source = cards
        try:
            from mlb_consensus_hub import inject_consensus_records_html

            six_finals = None
            if sport_u == "NFL":
                six_finals = _nfl_consensus_finals(html)
            elif sport_u == "CFL":
                try:
                    from team_tabbed_results import build_cfl_payload

                    six_finals = (build_cfl_payload() or {}).get("finals") or []
                except Exception:
                    six_finals = []
                if not six_finals:
                    six_finals = _six_model_consensus_finals(html, sport_u)
            elif sport_u == "NCAAF":
                six_finals = _six_model_consensus_finals(html, sport_u)
            html = inject_consensus_records_html(
                html,
                sport=sport_u.lower(),
                chart_view=view_l == "chart",
                fallback_html=source if source != html else None,
                finals=six_finals or None,
            )
        except Exception:
            pass
    if sport_u in ("NFL", "NCAAF"):
        html = _fill_nfl_spread_total_charts(html, sport_u)
        html = _sync_last_night_dates(html)
    cards_src = html
    if view_l == "chart":
        cached = _CHART_SOURCE_HTML.get(sport_u) or ""
        if cached and ("game-card" in cached or "pick-conf-grid" in cached):
            cards_src = cached
    if sport_u == "WNBA":
        html = _apply_four_model_consensus_meanings(html, cards_html=cards_src)
    if sport_u in ("WNBA", "NFL", "CFL"):
        html = _apply_last_night_windows(html, cards_html=cards_src)
    if sport_u != "NFL":
        html = _strip_empty_xsharp_bucket_rows(html)
    if not _has_signed_off_consensus_charts(html) and _charts_are_empty_stub(html):
        html = _strip_injected_charts(html)
        source = html
        if view_l == "chart":
            cards = _CHART_SOURCE_HTML.get(sport_u) or ""
            if cards and ("daily-model" in cards or "Season Performance" in cards):
                source = cards
        extra = _build_charts_from_page(source)
        if extra:
            html = _insert_charts(html, extra)
    if sport_u == "SOCCER":
        if "Prediction Lab · XSharp — Totals" not in html and "Prediction Lab & XSharp — Totals" not in html:
            cards = _CHART_SOURCE_HTML.get(sport_u) or ""
            ou_source = (
                cards
                if cards and ("Over/Under" in cards or "daily-model" in cards)
                else html
            )
            ou = _build_ou_chart_from_page(ou_source)
            if ou:
                html = _insert_charts(html, ou)
        try:
            from soccer_ui_fixup import ensure_soccer_results_ship_bits

            html = ensure_soccer_results_ship_bits(html)
        except Exception:
            html = html.replace(
                "Prediction Lab & XSharp — Totals",
                "Prediction Lab · XSharp — Totals",
            )
    if sport_u == "NCAAF":
        html = _ncaaf_top_date_nav(html)
        html = _hide_blank_ml_tally_cards(html, ("Grinder2", "Takedown", "Efficiency"))
        if view_l == "chart":
            html = _strip_nfl_card_board_for_chart(html)
            html = _add_html_class(html, "body", "ncaaf-results-chart")
    if sport_u == "CFL":
        html = _hide_blank_ml_tally_cards(html, ("Grinder2", "Takedown", "Efficiency"))
    if sport_u == "NFL":
        html = _apply_nfl_cards_chart_split(html, view_l)
    if sport_u == "WNBA" and "H2H Last 10" in html:
        html = _apply_h2h_faces(html, sport_u)
    if MARKER not in html:
        if re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(r"</body\s*>", MARKER + "\n</body>", html, count=1, flags=re.I)
        else:
            html = html + MARKER
    try:
        from site_chrome import ensure_locked_site_chrome

        html = ensure_locked_site_chrome(html)
    except Exception:
        pass
    return html


def _ncaaf_top_date_nav(html: str) -> str:
    """Put the NCAAF results date strip at the top with visible day bubbles."""
    if not html or 'id="date-' not in html:
        return html
    dated: list[str] = []
    for m in re.finditer(
        r'<div id="date-(\d{4}-\d{2}-\d{2})"[^>]*>([\s\S]*?)(?=<div id="date-|\Z)',
        html,
        flags=re.I,
    ):
        body = m.group(2) or ""
        if "game-card" in body or "data-pick-card" in body:
            dated.append(m.group(1))
    if len(dated) < 2:
        dated = list(dict.fromkeys(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html)))
    if len(dated) < 2:
        return html
    dates = sorted(dict.fromkeys(dated))
    show = dates[-8:]
    active = show[-1]
    bubbles = []
    for dk in show:
        try:
            dt = datetime.strptime(dk, "%Y-%m-%d")
            label = f"{dt.strftime('%a, %b')} {dt.day}"
        except ValueError:
            label = dk
        cls = "date-bubble"
        if dk == active:
            cls += " active"
        bubbles.append(
            f'<button type="button" class="{cls}" data-date="{dk}">{escape(label)}</button>'
        )
    bar = (
        '<nav class="ncaaf-top-dates" id="ncaafTopDates" aria-label="Results dates">'
        + "".join(bubbles)
        + "</nav>"
        "<style id=\"ncaaf-top-dates-css\">"
        ".ncaaf-top-dates{display:flex;gap:8px;overflow-x:auto;max-width:1100px;"
        "margin:8px auto 16px;padding:8px 12px;}"
        ".ncaaf-top-dates .date-bubble{background:#fff;border:2px solid rgba(15,23,42,0.18);"
        "border-radius:22px;padding:8px 14px;min-width:96px;font-weight:600;font-size:0.84rem;"
        "color:#0f172a;cursor:pointer;white-space:nowrap;}"
        ".ncaaf-top-dates .date-bubble.active{background:#f59e0b;border-color:#d97706;}"
        "</style>"
        "<script id=\"ncaaf-top-dates-js\">"
        "(function(){var bar=document.getElementById('ncaafTopDates');if(!bar)return;"
        "function go(d){if(!d)return;"
        "bar.querySelectorAll('.date-bubble').forEach(function(x){x.classList.remove('active');});"
        "var b=bar.querySelector('[data-date=\"'+d+'\"]');if(b)b.classList.add('active');"
        "if(typeof showDate==='function'){showDate(d);}"
        "else{"
        "document.querySelectorAll('.date-section').forEach(function(s){s.classList.remove('visible');});"
        "var sec=document.getElementById('date-'+d);if(sec)sec.classList.add('visible');"
        "}"
        "}"
        "bar.addEventListener('click',function(e){"
        "var b=e.target.closest('[data-date]');if(!b)return;"
        "go(b.getAttribute('data-date'));"
        "});"
        "var latest=bar.getAttribute('data-latest')||'" + escape(active) + "';"
        "function boot(){go(latest);}"
        "if(document.readyState==='complete'){setTimeout(boot,0);}"
        "else{window.addEventListener('load',function(){setTimeout(boot,0);});}"
        "})();"
        "</script>"
    )
    bar = bar.replace(
        '<nav class="ncaaf-top-dates" id="ncaafTopDates"',
        f'<nav class="ncaaf-top-dates" id="ncaafTopDates" data-latest="{escape(active)}"',
        1,
    )
    if 'id="ncaafTopDates"' in html:
        html = re.sub(
            r'<nav class="ncaaf-top-dates"[\s\S]*?</script>',
            bar,
            html,
            count=1,
            flags=re.I,
        )
    else:
        placed = False
        for pat in (
            r'(<h1\b[^>]*>[\s\S]*?</h1>)',
            r'(<div class="pl-view-toggle"[\s\S]*?</div>)',
        ):
            html2, n = re.subn(pat, r"\1" + bar, html, count=1, flags=re.I)
            if n:
                html = html2
                placed = True
                break
        if not placed:
            html = bar + html
    # Keep the bottom week slider, but the top strip is what people use.
    return html


_NFL_ABBR_BY_NAME = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}
_NFL_NAME_BY_ABBR = {abbr: name for name, abbr in _NFL_ABBR_BY_NAME.items()}
_NFL_NICK_TO_ABBR = {
    "cardinals": "ARI",
    "falcons": "ATL",
    "ravens": "BAL",
    "bills": "BUF",
    "panthers": "CAR",
    "bears": "CHI",
    "bengals": "CIN",
    "browns": "CLE",
    "cowboys": "DAL",
    "broncos": "DEN",
    "lions": "DET",
    "packers": "GB",
    "texans": "HOU",
    "colts": "IND",
    "jaguars": "JAX",
    "chiefs": "KC",
    "raiders": "LV",
    "chargers": "LAC",
    "rams": "LAR",
    "dolphins": "MIA",
    "vikings": "MIN",
    "patriots": "NE",
    "saints": "NO",
    "giants": "NYG",
    "jets": "NYJ",
    "eagles": "PHI",
    "steelers": "PIT",
    "49ers": "SF",
    "seahawks": "SEA",
    "buccaneers": "TB",
    "titans": "TEN",
    "commanders": "WAS",
    "redskins": "WAS",
}
_NFL_ESPN_SLUG = {
    "ARI": "ari",
    "ATL": "atl",
    "BAL": "bal",
    "BUF": "buf",
    "CAR": "car",
    "CHI": "chi",
    "CIN": "cin",
    "CLE": "cle",
    "DAL": "dal",
    "DEN": "den",
    "DET": "det",
    "GB": "gb",
    "HOU": "hou",
    "IND": "ind",
    "JAX": "jax",
    "KC": "kc",
    "LV": "lv",
    "LAC": "lac",
    "LAR": "lar",
    "MIA": "mia",
    "MIN": "min",
    "NE": "ne",
    "NO": "no",
    "NYG": "nyg",
    "NYJ": "nyj",
    "PHI": "phi",
    "PIT": "pit",
    "SF": "sf",
    "SEA": "sea",
    "TB": "tb",
    "TEN": "ten",
    "WAS": "wsh",
}
_NFL_ESPN_TTL = 900.0
_NFL_ESPN_CACHE: dict[str, tuple[float, list]] = {}
_NCAAF_ESPN_DISK = Path(__file__).resolve().parent / "data" / "ncaaf_espn_schedule_cache.json"
_NCAAF_ESPN_DISK_TTL = 86400.0
_NCAAF_ESPN_DISK_LOADED = False


def _ncaaf_espn_disk_load() -> None:
    global _NCAAF_ESPN_DISK_LOADED
    if _NCAAF_ESPN_DISK_LOADED:
        return
    _NCAAF_ESPN_DISK_LOADED = True
    try:
        raw = json.loads(_NCAAF_ESPN_DISK.read_text(encoding="utf-8"))
    except Exception:
        return
    now = time.time()
    if not isinstance(raw, dict):
        return
    for url, row in raw.items():
        if not isinstance(row, dict):
            continue
        ts = float(row.get("ts") or 0)
        events = row.get("events")
        if ts and (now - ts) < _NCAAF_ESPN_DISK_TTL and isinstance(events, list):
            _NFL_ESPN_CACHE[str(url)] = (ts, events)


def _ncaaf_espn_disk_save() -> None:
    try:
        payload = {
            url: {"ts": ts, "events": events}
            for url, (ts, events) in _NFL_ESPN_CACHE.items()
            if "college-football" in url
        }
        _NCAAF_ESPN_DISK.parent.mkdir(parents=True, exist_ok=True)
        _NCAAF_ESPN_DISK.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _nfl_team_key(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    up = s.upper()
    if up in _NFL_NAME_BY_ABBR:
        return up
    if s in _NFL_ABBR_BY_NAME:
        return _NFL_ABBR_BY_NAME[s]
    low = s.lower()
    for full, abbr in _NFL_ABBR_BY_NAME.items():
        if full.lower() == low:
            return abbr
    if "washington" in low and ("football" in low or "redskin" in low or "commander" in low):
        return "WAS"
    if "raider" in low:
        return "LV"
    if "charger" in low:
        return "LAC"
    if "ram" in low and "rams" in low:
        return "LAR"
    nick = _NFL_NICK_TO_ABBR.get(low.split()[-1])
    return nick or ""


def _cfb_team_key(name: str) -> tuple[str, ...]:
    """School key that keeps Miami (OH) distinct from Miami (FL)."""
    s = html_lib.unescape(name or "").lower()
    if re.search(r"miami\s*\(\s*oh", s) or "redhawks" in s:
        return ("miami", "oh")
    return _school_key(name)


def _meeting_keys(sport: str, home: str, away: str):
    if (sport or "").upper() == "NFL":
        return _nfl_team_key(home), _nfl_team_key(away)
    if (sport or "").upper() == "NCAAF":
        return _cfb_team_key(home), _cfb_team_key(away)
    return _team_core(home), _team_core(away)


def _load_meetings(sport: str) -> dict:
    path = _db_path()
    if not path.is_file():
        return {}
    out: dict = defaultdict(list)
    try:
        con = sqlite3.connect(str(path))
        rows = con.execute(
            """
            SELECT home_team_id, away_team_id, home_score, away_score, game_date
            FROM games
            WHERE sport = ? AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY date(game_date) DESC
            """,
            (sport,),
        ).fetchall()
        con.close()
    except Exception:
        return {}
    for home, away, hs, aws, dt in rows:
        ch, ca = _meeting_keys(sport, home, away)
        if not ch or not ca:
            continue
        rec = (home, away, float(hs), float(aws), str(dt or "")[:10])
        out[(ch, ca)].append(rec)
    return out


def _h2h_text(meetings: dict, home: str, away: str, sport: str = "") -> str:
    ch, ca = _meeting_keys(sport, home, away)
    if not ch or not ca or ch == ca:
        return ""
    rows = list(meetings.get((ch, ca), [])) + list(meetings.get((ca, ch), []))
    # de-dupe by date+scores
    seen = set()
    uniq = []
    for rec in rows:
        key = (rec[4], rec[2], rec[3], rec[0], rec[1])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(rec)
    uniq.sort(key=lambda r: r[4], reverse=True)
    uniq = uniq[:10]
    if not uniq:
        return ""
    totals = []
    for db_home, _db_away, hs, aws, _dt in uniq:
        # upcoming home team's points in that meeting
        if _team_core(db_home) == ch:
            totals.append(hs + aws)
        else:
            totals.append(hs + aws)
    avg = round(sum(totals) / len(totals), 1)
    if avg == int(avg):
        avg_s = str(int(avg))
    else:
        avg_s = f"{avg:.1f}"
    n = len(uniq)
    return f"{avg_s} ({n} game{'s' if n != 1 else ''})"


def _is_fake_h2h(val: str) -> bool:
    plain = re.sub(r"<[^>]+>", "", val or "")
    plain = re.sub(r"&mdash;|&ndash;|—|–", "", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return True
    if re.fullmatch(r"first meeting", plain, flags=re.I):
        return False
    if re.fullmatch(r"0+(?:\.0+)?", plain):
        return True
    if re.fullmatch(r"0+(?:\.0+)?\s*\(\s*0\s*games?\)", plain, flags=re.I):
        return True
    return False


def _ensure_h2h_attr_first_meeting(html: str) -> str:
    """Cards with no real H2H history show First meeting, not a dash."""

    def _tag(m: re.Match[str]) -> str:
        tag = m.group(0)
        cur = re.search(r'data-h2h="([^"]*)"', tag, flags=re.I)
        if cur and not _is_fake_h2h(cur.group(1)):
            return tag
        if cur:
            return re.sub(
                r'data-h2h="[^"]*"', 'data-h2h="First meeting"', tag, count=1
            )
        return tag[:-1] + ' data-h2h="First meeting">'

    return re.sub(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", _tag, html or "", flags=re.I)


def _fill_blank_h2h_chips(html: str) -> str:
    """Replace leftover dash / 0 H2H faces with First meeting."""

    def _sf(m: re.Match[str]) -> str:
        cur = m.group(2)
        if _is_fake_h2h(cur):
            return f"{m.group(1)}First meeting</span>"
        return m.group(0)

    def _face(m: re.Match[str]) -> str:
        cur = m.group(2)
        if _is_fake_h2h(cur):
            return f"{m.group(1)}First meeting{m.group(3)}"
        return m.group(0)

    html = re.sub(
        r'(H2H Last 10</span>\s*<span class="sf-val">)([\s\S]*?)</span>',
        _sf,
        html or "",
        flags=re.I,
    )
    return re.sub(
        r'(<div class="line-chip h2h-face-chip">\s*'
        r'<div class="line-chip-label">H2H Last 10</div>\s*'
        r'<div class="line-chip-val">)([\s\S]*?)(</div>)',
        _face,
        html,
        flags=re.I,
    )


def _fill_na_from_published(html: str) -> str:
    """Replace leftover N/A model faces with a % already on that card."""
    if not html or "N/A" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        tag = open_m.group(1)
        rest = stack[open_m.end() :]
        published = []
        for raw in re.findall(
            r'class="pc-val"[^>]*>\s*([\s\S]*?)</div>', rest, flags=re.I
        ):
            val = re.sub(r"<[^>]+>", "", raw).strip()
            if re.fullmatch(r"\d+(?:\.\d+)?%", val):
                published.append(val)
        if not published:
            conf = re.search(r'data-conf="([^"]*)"', tag, flags=re.I)
            raw = (conf.group(1) if conf else "").strip()
            if re.fullmatch(r"\d+(?:\.\d+)?", raw):
                published.append(f"{float(raw):.1f}%")
            elif "%" in raw and re.search(r"\d", raw):
                published.append(raw)
        if not published:
            out.append(tag + rest)
            continue
        fill = published[0]
        rest = re.sub(
            r'(<div class="pc-val"[^>]*>)\s*N/A\s*(</div>)',
            lambda m, _fill=fill: f"{m.group(1)}{_fill}{m.group(2)}",
            rest,
            flags=re.I,
        )
        out.append(tag + rest)
    return "".join(out)


def _apply_h2h_faces(html: str, sport: str) -> str:
    """Fill H2H Last 10 from meetings; First meeting when there is no history."""
    sport_u = _sport_key(sport)
    if not html or "H2H Last 10" not in html:
        return html
    meetings = _load_meetings(sport_u) if sport_u in PICKS_H2H_SPORTS else {}

    def _card_repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        home = html_lib.unescape(m.group("home") or "")
        away = html_lib.unescape(m.group("away") or "")
        val = _h2h_text(meetings, home, away, sport_u) if meetings else ""
        if not val:
            val = "First meeting"
        if re.search(r'data-h2h="', tag, flags=re.I):
            tag = re.sub(r'data-h2h="[^"]*"', f'data-h2h="{escape(val)}"', tag, count=1)
        else:
            tag = tag[:-1] + f' data-h2h="{escape(val)}">'
        return tag

    html = re.sub(
        r"<div\b(?=[^>]*\bdata-pick-card\b)(?=[^>]*\bdata-home=\"(?P<home>[^\"]*)\")"
        r"(?=[^>]*\bdata-away=\"(?P<away>[^\"]*)\")[^>]*>",
        _card_repl,
        html,
        flags=re.I,
    )
    if sport_u == "NFL":
        html = _fill_nfl_espn_h2h(html)
        html = _fill_placeholder_edge(html, "NFL")
        html = _fill_efficiency_na(html, "NFL")
        html = _fill_na_from_published(html)
    elif sport_u == "CFL":
        html = _fill_placeholder_edge(html, "CFL")
        html = _fill_efficiency_na(html, "CFL")
        html = _fill_na_from_published(html)
    elif sport_u == "NCAAF":
        html = _fill_ncaaf_espn_h2h(html)
        html = _fill_ncaaf_card_gaps(html)
        html = _fill_efficiency_na(html, "NCAAF")
        html = _fill_na_from_published(html)
    html = _ensure_h2h_attr_first_meeting(html)
    html = _sync_h2h_chips(html)
    html = _inject_face_h2h_chips(html)
    html = _sync_h2h_face_chips(html)
    html = _fill_blank_h2h_chips(html)
    return html


def apply_team_picks_h2h(html: str, sport: str) -> str:
    """Fill H2H Last 10 from completed meetings (alias-aware). Never invent 0."""
    sport_u = _sport_key(sport)
    if sport_u == "WNBA":
        try:
            from wnba_ui_fixup import fill_wnba_card_gaps, fill_wnba_placeholder_probs

            html = fill_wnba_placeholder_probs(html)
            html = fill_wnba_card_gaps(html)
        except Exception:
            pass
    if sport_u not in PICKS_H2H_SPORTS or not html or "H2H Last 10" not in html:
        html = apply_team_picks_copy_all(html, sport_u)
        if sport_u == "CFL":
            html = _hide_blank_books_ml_lines(html)
        return html
    html = _apply_h2h_faces(html, sport_u)
    html = apply_team_picks_copy_all(html, sport_u)
    if sport_u == "CFL":
        html = _hide_blank_books_ml_lines(html)
    return html


def _sync_h2h_chips(html: str) -> str:
    vals = re.findall(r'data-h2h="([^"]*)"', html)
    if not vals:
        return html
    it = iter(vals)

    def _chip(m: re.Match[str]) -> str:
        try:
            nxt = next(it)
        except StopIteration:
            return m.group(0)
        if not nxt or _is_fake_h2h(nxt):
            return f"{m.group(1)}—</span>"
        return f"{m.group(1)}{html_lib.unescape(nxt)}</span>"

    return re.sub(
        r'(H2H Last 10</span>\s*<span class="sf-val">)([\s\S]*?)</span>',
        _chip,
        html,
        flags=re.I,
    )


def _sync_h2h_face_chips(html: str) -> str:
    vals = re.findall(r'data-h2h="([^"]*)"', html)
    if not vals:
        return html
    it = iter(vals)

    def _face(m: re.Match[str]) -> str:
        raw = next(it, "")
        val = "—" if _is_fake_h2h(raw) else html_lib.unescape(raw)
        return f"{m.group(1)}{escape(val)}{m.group(3)}"

    return re.sub(
        r'(<div class="line-chip h2h-face-chip">\s*'
        r'<div class="line-chip-label">H2H Last 10</div>\s*'
        r'<div class="line-chip-val">)([\s\S]*?)(</div>)',
        _face,
        html,
        flags=re.I,
    )


def _nfl_espn_events(slug: str, season: int) -> list:
    url = (
        "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/"
        f"teams/{slug}/schedule?season={int(season)}"
    )
    now = time.time()
    hit = _NFL_ESPN_CACHE.get(url)
    if hit and (now - hit[0]) < _NFL_ESPN_TTL:
        return hit[1]
    events: list = []
    try:
        import requests

        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if getattr(r, "status_code", None) == 403:
            alt = url.replace("://site.web.api.espn.com", "://site.api.espn.com", 1)
            r = requests.get(alt, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json() if r.content else {}
        if isinstance(data, dict):
            events = list(data.get("events") or [])
    except Exception:
        events = []
    _NFL_ESPN_CACHE[url] = (now, events)
    return events


def _nfl_espn_score(raw) -> float | None:
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _nfl_espn_h2h_text(home: str, away: str) -> str:
    hk, ak = _nfl_team_key(home), _nfl_team_key(away)
    if not hk or not ak or hk == ak:
        return ""
    slug = _NFL_ESPN_SLUG.get(ak) or _NFL_ESPN_SLUG.get(hk)
    if not slug:
        return ""
    want = {hk, ak}
    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    year = datetime.now().year
    for season in range(year, year - 8, -1):
        for ev in _nfl_espn_events(slug, season):
            if not isinstance(ev, dict):
                continue
            comps = (ev.get("competitions") or [{}])[0] or {}
            status = (comps.get("status") or {}).get("type") or {}
            if not status.get("completed"):
                continue
            sides: list[tuple[str, float]] = []
            for c in comps.get("competitors") or []:
                if not isinstance(c, dict):
                    continue
                team = c.get("team") or {}
                name = (
                    team.get("displayName")
                    or team.get("name")
                    or team.get("abbreviation")
                    or ""
                ).strip()
                score = _nfl_espn_score(c.get("score"))
                if name and score is not None:
                    sides.append((name, score))
            if len(sides) != 2:
                continue
            keys = {_nfl_team_key(n) for n, _ in sides}
            if keys != want:
                continue
            dk = str(ev.get("date") or "")[:10]
            if not dk or dk in seen:
                continue
            seen.add(dk)
            rows.append((dk, sides[0][1] + sides[1][1]))
        if len(rows) >= 10:
            break
    rows.sort(key=lambda r: r[0], reverse=True)
    totals = [tot for _dt, tot in rows[:10]]
    if not totals:
        return ""
    avg = round(sum(totals) / len(totals), 1)
    avg_s = str(int(avg)) if avg == int(avg) else f"{avg:.1f}"
    n = len(totals)
    return f"{avg_s} ({n} game{'s' if n != 1 else ''})"


def _fill_nfl_espn_h2h(html: str) -> str:
    """When the local slate has no meetings, use ESPN completed schedules."""
    if not html or "data-pick-card" not in html:
        return html
    slugs: set[str] = set()
    for m in re.finditer(
        r"<div\b(?=[^>]*\bdata-pick-card\b)(?=[^>]*\bdata-home=\"(?P<home>[^\"]*)\")"
        r"(?=[^>]*\bdata-away=\"(?P<away>[^\"]*)\")[^>]*>",
        html,
        flags=re.I,
    ):
        cur = re.search(r'data-h2h="([^"]*)"', m.group(0), flags=re.I)
        if cur and not _is_fake_h2h(cur.group(1)):
            continue
        away = html_lib.unescape(m.group("away") or "").strip()
        slug = _NFL_ESPN_SLUG.get(_nfl_team_key(away))
        if slug:
            slugs.add(slug)
    if slugs:
        year = datetime.now().year
        jobs = [(slug, season) for slug in slugs for season in range(year, year - 6, -1)]
        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda p: _nfl_espn_events(*p), jobs))
        except Exception:
            pass
    cache: dict[tuple[str, str], str] = {}

    def _lookup(home: str, away: str) -> str:
        key = (home, away)
        if key in cache:
            return cache[key]
        try:
            val = _nfl_espn_h2h_text(home, away)
        except Exception:
            val = ""
        cache[key] = val
        cache[(away, home)] = val
        return val

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        cur = re.search(r'data-h2h="([^"]*)"', open_tag, flags=re.I)
        if cur and not _is_fake_h2h(cur.group(1)):
            out.append(open_tag + rest)
            continue
        home_m = re.search(r'data-home="([^"]*)"', open_tag, flags=re.I)
        away_m = re.search(r'data-away="([^"]*)"', open_tag, flags=re.I)
        home = html_lib.unescape((home_m.group(1) if home_m else "").strip())
        away = html_lib.unescape((away_m.group(1) if away_m else "").strip())
        val = _lookup(home, away) if home and away else ""
        if val:
            esc = escape(val)
            if re.search(r'data-h2h="', open_tag, flags=re.I):
                open_tag = re.sub(
                    r'data-h2h="[^"]*"', f'data-h2h="{esc}"', open_tag, count=1
                )
            else:
                open_tag = open_tag[:-1] + f' data-h2h="{esc}">'
        out.append(open_tag + rest)
    return "".join(out)


_NCAAF_ID_MAP: dict[str, str] | None = None


def _ncaaf_id_map() -> dict[str, str]:
    global _NCAAF_ID_MAP
    if _NCAAF_ID_MAP is not None:
        return _NCAAF_ID_MAP
    path = Path(__file__).resolve().parent / "data" / "ncaaf_team_espn_ids.json"
    out: dict[str, str] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            if key and val:
                out[re.sub(r"\s+", " ", str(key).strip().lower())] = str(val).strip()
    _NCAAF_ID_MAP = out
    return out


def _ncaaf_espn_id(name: str) -> str:
    raw = html_lib.unescape(name or "").strip()
    if not raw:
        return ""
    ids = _ncaaf_id_map()
    cands = [raw.lower(), re.sub(r"\s+", " ", raw.lower())]
    words = raw.split()
    if len(words) >= 3:
        cands.append(" ".join(words[:-1]).lower())
    if len(words) >= 2:
        cands.append(words[0].lower())
    for cand in cands:
        sid = ids.get(cand)
        if sid:
            return sid
    want = _cfb_team_key(raw)
    if not want:
        return ""
    for key, sid in ids.items():
        if _cfb_team_key(key) == want:
            return sid
    return ""


def _ncaaf_espn_events(team_id: str, season: int, *, fetch: bool = True) -> list:
    url = (
        "https://site.web.api.espn.com/apis/site/v2/sports/football/"
        f"college-football/teams/{team_id}/schedule?season={int(season)}"
    )
    now = time.time()
    _ncaaf_espn_disk_load()
    hit = _NFL_ESPN_CACHE.get(url)
    if hit and (now - hit[0]) < _NCAAF_ESPN_DISK_TTL:
        return hit[1]
    if not fetch:
        return hit[1] if hit else []
    events: list = []
    try:
        import requests

        r = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        if getattr(r, "status_code", None) == 403:
            alt = url.replace("://site.web.api.espn.com", "://site.api.espn.com", 1)
            r = requests.get(alt, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json() if r.content else {}
        if isinstance(data, dict):
            events = list(data.get("events") or [])
    except Exception:
        events = []
    _NFL_ESPN_CACHE[url] = (now, events)
    return events


def _ncaaf_espn_h2h_text(home: str, away: str) -> str:
    hk, ak = _cfb_team_key(home), _cfb_team_key(away)
    if not hk or not ak or hk == ak:
        return ""
    tid = _ncaaf_espn_id(away) or _ncaaf_espn_id(home)
    if not tid:
        return ""
    want = {hk, ak}
    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    year = datetime.now().year
    for season in range(year, year - 16, -1):
        for ev in _ncaaf_espn_events(tid, season, fetch=True):
            if not isinstance(ev, dict):
                continue
            comps = (ev.get("competitions") or [{}])[0] or {}
            status = (comps.get("status") or {}).get("type") or {}
            if not status.get("completed"):
                continue
            sides: list[tuple[str, float]] = []
            for c in comps.get("competitors") or []:
                if not isinstance(c, dict):
                    continue
                team = c.get("team") or {}
                name = (
                    team.get("displayName")
                    or team.get("name")
                    or team.get("shortDisplayName")
                    or ""
                ).strip()
                score = _nfl_espn_score(c.get("score"))
                if name and score is not None:
                    sides.append((name, score))
            if len(sides) != 2:
                continue
            keys = {_cfb_team_key(n) for n, _ in sides}
            if keys != want:
                continue
            dk = str(ev.get("date") or "")[:10]
            if not dk or dk in seen:
                continue
            seen.add(dk)
            rows.append((dk, sides[0][1] + sides[1][1]))
        if len(rows) >= 10:
            break
    rows.sort(key=lambda r: r[0], reverse=True)
    totals = [tot for _dt, tot in rows[:10]]
    if not totals:
        return ""
    avg = round(sum(totals) / len(totals), 1)
    avg_s = str(int(avg)) if avg == int(avg) else f"{avg:.1f}"
    n = len(totals)
    return f"{avg_s} ({n} game{'s' if n != 1 else ''})"


def _fill_ncaaf_espn_h2h(html: str) -> str:
    """When the local NCAAF slate has no meetings, use ESPN schedules."""
    if not html or "data-pick-card" not in html:
        return html
    ids: set[str] = set()
    for m in re.finditer(
        r"<div\b(?=[^>]*\bdata-pick-card\b)(?=[^>]*\bdata-home=\"(?P<home>[^\"]*)\")"
        r"(?=[^>]*\bdata-away=\"(?P<away>[^\"]*)\")[^>]*>",
        html,
        flags=re.I,
    ):
        cur = re.search(r'data-h2h="([^"]*)"', m.group(0), flags=re.I)
        if cur and not _is_fake_h2h(cur.group(1)):
            continue
        away = html_lib.unescape(m.group("away") or "").strip()
        tid = _ncaaf_espn_id(away)
        if tid:
            ids.add(tid)
    _ncaaf_espn_disk_load()
    if ids:
        year = datetime.now().year
        jobs = [(tid, season) for tid in ids for season in range(year, year - 12, -1)]
        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda p: _ncaaf_espn_events(p[0], p[1], fetch=True), jobs))
            _ncaaf_espn_disk_save()
        except Exception:
            pass
    cache: dict[tuple[str, str], str] = {}

    def _lookup(home: str, away: str) -> str:
        key = (home, away)
        if key in cache:
            return cache[key]
        try:
            val = _ncaaf_espn_h2h_text(home, away)
        except Exception:
            val = ""
        cache[key] = val
        cache[(away, home)] = val
        return val

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        cur = re.search(r'data-h2h="([^"]*)"', open_tag, flags=re.I)
        if cur and not _is_fake_h2h(cur.group(1)):
            out.append(open_tag + rest)
            continue
        home_m = re.search(r'data-home="([^"]*)"', open_tag, flags=re.I)
        away_m = re.search(r'data-away="([^"]*)"', open_tag, flags=re.I)
        home = html_lib.unescape((home_m.group(1) if home_m else "").strip())
        away = html_lib.unescape((away_m.group(1) if away_m else "").strip())
        val = _lookup(home, away) if home and away else ""
        if val:
            esc = escape(val)
            if re.search(r'data-h2h="', open_tag, flags=re.I):
                open_tag = re.sub(
                    r'data-h2h="[^"]*"', f'data-h2h="{esc}"', open_tag, count=1
                )
            else:
                open_tag = open_tag[:-1] + f' data-h2h="{esc}">'
            open_tag = re.sub(r'\s*data-h2h-reason="[^"]*"', "", open_tag)
        elif home and away:
            if re.search(r'data-h2h="', open_tag, flags=re.I):
                open_tag = re.sub(
                    r'data-h2h="[^"]*"', 'data-h2h="First meeting"', open_tag, count=1
                )
            else:
                open_tag = open_tag[:-1] + ' data-h2h="First meeting">'
            if re.search(r'data-h2h-reason="', open_tag, flags=re.I):
                open_tag = re.sub(
                    r'data-h2h-reason="[^"]*"',
                    'data-h2h-reason="No prior completed meetings"',
                    open_tag,
                    count=1,
                )
            else:
                open_tag = open_tag[:-1] + ' data-h2h-reason="No prior completed meetings">'
        out.append(open_tag + rest)
    return "".join(out)


def _nfl_home_centric_spread(pick: str, home: str, away: str) -> float | None:
    text = html_lib.unescape(pick or "").strip()
    m = re.match(r"^(.+?)\s*([+-]\d+(?:\.\d+)?)\s*$", text)
    if not m:
        return None
    team, line = m.group(1).strip(), float(m.group(2))
    tk, hk, ak = _nfl_team_key(team), _nfl_team_key(home), _nfl_team_key(away)
    if tk and hk and tk == hk:
        return -line
    if tk and ak and tk == ak:
        return line
    ck, ch, ca = _cfb_team_key(team), _cfb_team_key(home), _cfb_team_key(away)
    if ck and ch and ck == ch:
        return -line
    if ck and ca and ck == ca:
        return line
    tlow, hlow, alow = team.lower(), home.lower(), away.lower()
    if tlow in hlow or hlow in tlow or team.split()[-1].lower() == home.split()[-1].lower():
        return -line
    if tlow in alow or alow in tlow or team.split()[-1].lower() == away.split()[-1].lower():
        return line
    return None


def _fill_placeholder_edge(html: str, sport: str) -> str:
    """Replace placeholder Edge 50% from the published PL spread. Display only."""
    if not html or "Edge" not in html or "data-pick-card" not in html:
        return html
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct
    except Exception:
        return html
    sport_u = _sport_key(sport)

    def _short(name: str) -> str:
        parts = (name or "").split()
        return parts[-1] if parts else name

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        edge_m = re.search(
            r'<div class="pc-name">\s*Edge\s*</div>\s*<div class="pc-val"[^>]*>\s*([^<]+)',
            rest,
            flags=re.I,
        )
        edge_raw = (edge_m.group(1) if edge_m else "").strip().rstrip("%")
        attr_m = re.search(r'data-m-edge="([^"]*)"', open_tag, flags=re.I)
        if attr_m and not edge_raw:
            edge_raw = attr_m.group(1)
        if not ncaaf_elo_is_placeholder(edge_raw):
            out.append(open_tag + rest)
            continue
        home_m = re.search(r'data-home="([^"]*)"', open_tag, flags=re.I)
        away_m = re.search(r'data-away="([^"]*)"', open_tag, flags=re.I)
        home = html_lib.unescape((home_m.group(1) if home_m else "").strip())
        away = html_lib.unescape((away_m.group(1) if away_m else "").strip())
        pl = ""
        pl_m = re.search(r'data-pl-spread="([^"]*)"', open_tag, flags=re.I)
        if pl_m:
            pl = html_lib.unescape(pl_m.group(1) or "").strip()
        if not pl:
            tm = re.search(
                r'<td class="market-k">\s*Spread\s*</td>\s*'
                r'<td class="val-books">[\s\S]*?</td>\s*'
                r'<td class="val-pl">([^<]+)',
                rest,
                flags=re.I,
            )
            if tm:
                pl = html_lib.unescape(tm.group(1) or "").strip()
        hc = _nfl_home_centric_spread(pl, home, away) if pl and home and away else None
        live = None
        if hc is not None:
            try:
                live = float(spread_to_home_prob_pct(hc, sport_u))
            except Exception:
                live = None
        if live is None or ncaaf_elo_is_placeholder(live):
            out.append(open_tag + rest)
            continue
        if live >= 50:
            face, side, cls, title = live, _short(home), "home", home
        else:
            face, side, cls, title = round(100.0 - live, 1), _short(away), "away", away
        if face == int(face):
            face_s = f"{int(face)}.0"
        else:
            face_s = f"{face:.1f}"
        rest = re.sub(
            r'(<div class="pc-name">\s*Edge\s*</div>\s*)'
            r'<div class="pc-val"[^>]*>\s*50(?:\.0+)?%\s*</div>\s*'
            r'<div class="pc-side[^"]*"[^>]*>[\s\S]*?</div>',
            (
                r"\1"
                f'<div class="pc-val">{face_s}%</div>'
                f'<div class="pc-side {cls}" title="{escape(title)}">'
                f"{escape(side)}</div>"
            ),
            rest,
            count=1,
            flags=re.I,
        )
        if attr_m:
            open_tag = re.sub(
                r'data-m-edge="[^"]*"',
                f'data-m-edge="{live:.1f}"',
                open_tag,
                count=1,
                flags=re.I,
            )
        out.append(open_tag + rest)
    return "".join(out)


def _fill_efficiency_na(html: str, sport: str) -> str:
    """Fill Efficiency N/A from the published PL spread. Display only."""
    if not html or "Efficiency" not in html:
        return html
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct
    except Exception:
        return html
    sport_u = _sport_key(sport)

    def _short(name: str) -> str:
        parts = (name or "").split()
        return parts[-1] if parts else name

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        if not re.search(
            r'pc-name">\s*Efficiency\s*</div>\s*<div class="pc-val"[^>]*>\s*N/A',
            rest,
            flags=re.I,
        ):
            out.append(open_tag + rest)
            continue
        home_m = re.search(r'data-home="([^"]*)"', open_tag, flags=re.I)
        away_m = re.search(r'data-away="([^"]*)"', open_tag, flags=re.I)
        home = html_lib.unescape((home_m.group(1) if home_m else "").strip())
        away = html_lib.unescape((away_m.group(1) if away_m else "").strip())
        pl = ""
        pl_m = re.search(r'data-pl-spread="([^"]*)"', open_tag, flags=re.I)
        if pl_m:
            pl = html_lib.unescape(pl_m.group(1) or "").strip()
        if not pl:
            tm = re.search(
                r'<td class="market-k">\s*Spread\s*</td>\s*'
                r'<td class="val-books">[\s\S]*?</td>\s*'
                r'<td class="val-pl">([^<]+)',
                rest,
                flags=re.I,
            )
            if tm:
                pl = html_lib.unescape(tm.group(1) or "").strip()
        hc = _nfl_home_centric_spread(pl, home, away) if pl and home and away else None
        if hc is None:
            out.append(open_tag + rest)
            continue
        try:
            home_pct = float(spread_to_home_prob_pct(hc, sport_u))
        except Exception:
            out.append(open_tag + rest)
            continue
        if home_pct >= 50:
            face, side, cls = home_pct, _short(home), "home"
        else:
            face, side, cls = round(100.0 - home_pct, 1), _short(away), "away"
        if face == int(face):
            face_s = str(int(face))
        else:
            face_s = f"{face:.1f}"
        rest = re.sub(
            r'(<div class="pc-name">\s*Efficiency\s*</div>\s*)'
            r'<div class="pc-val"[^>]*>\s*N/A\s*</div>\s*'
            r'<div class="pc-side"[^>]*>\s*N/A\s*</div>',
            (
                r"\1"
                f'<div class="pc-val">{face_s}%</div>'
                f'<div class="pc-side {cls}" title="'
                f'{escape(home if cls == "home" else away)}">'
                f"{escape(side)}</div>"
            ),
            rest,
            count=1,
            flags=re.I,
        )
        pct_s = f"{home_pct:.1f}"
        if re.search(r'data-m-efficiency="', open_tag, flags=re.I):
            open_tag = re.sub(
                r'data-m-efficiency="[^"]*"',
                f'data-m-efficiency="{pct_s}"',
                open_tag,
                count=1,
            )
        else:
            open_tag = open_tag[:-1] + f' data-m-efficiency="{pct_s}">'
        out.append(open_tag + rest)
    return "".join(out)


def _inject_face_h2h_chips(html: str) -> str:
    """Put H2H Last 10 on the card face (lines strip), not only inside details."""
    if not html or "H2H Last 10" not in html:
        return html
    vals = re.findall(r'data-h2h="([^"]*)"', html)
    if not vals:
        return html
    it = iter(vals)

    def _strip(m: re.Match[str]) -> str:
        raw = next(it, "")
        val = "—" if _is_fake_h2h(raw) else raw
        if "h2h-face-chip" in m.group(0):
            return m.group(0)
        chip = (
            '<div class="line-chip h2h-face-chip">'
            '<div class="line-chip-label">H2H Last 10</div>'
            f'<div class="line-chip-val">{escape(val)}</div></div>'
        )
        return m.group(1) + chip

    return re.sub(r'(<div class="lines-strip">)', _strip, html, flags=re.I)


def apply_team_picks_copy_all(html: str, sport: str) -> str:
    """Copy All = every loaded pick card with models, lines, and H2H."""
    sport_u = _sport_key(sport)
    if not html:
        return html
    if "refreshing this page right now" in html.lower():
        return html
    if 'id="pvCopyBtn"' not in html:
        btn = (
            '<button type="button" class="pv-copy" id="pvCopyBtn" '
            'onclick="copyVisiblePicks(this)">📋 Copy All</button>'
        )
        if "picks-view-controls" in html:
            html = html.replace(
                '<div class="picks-view-controls">',
                '<div class="picks-view-controls">' + btn,
                1,
            )
        elif '<div class="date-nav">' in html:
            bar = (
                '<div class="picks-view-controls" style="display:flex;justify-content:flex-end;'
                'max-width:1200px;margin:0 auto 12px;padding:0 4px;">'
                f"{btn}</div>"
                "<style>.pv-copy{border:1px solid #00529B;background:#fff;color:#00529B;"
                "border-radius:999px;padding:6px 14px;font-size:0.8em;font-weight:800;cursor:pointer;}"
                ".pv-copy.copied{background:#00C076;border-color:#00C076;color:#fff;}</style>"
            )
            html = html.replace('<div class="date-nav">', bar + '<div class="date-nav">', 1)
    if COPY_MARK in html:
        return html
    script = r"""
<script id="pl-copy-all-slate">
(function(){
  function dash(v){ v=(v==null?"":String(v)).trim(); return v||"—"; }
  function txt(el){ return el ? String(el.textContent||"").replace(/\s+/g," ").trim() : ""; }
  function copyVisiblePicks(btn){
    var secs=[].slice.call(document.querySelectorAll(".date-section"));
    if(!secs.length) secs=[document];
    var stacks=[];
    secs.forEach(function(sec){
      sec.querySelectorAll("[data-pick-card]").forEach(function(st){ stacks.push(st); });
    });
    if(!stacks.length){
      document.querySelectorAll("[data-pick-card]").forEach(function(st){ stacks.push(st); });
    }
    var icon=(typeof sportIcon!=="undefined"?sportIcon:"");
    var name=(typeof sportName!=="undefined"?sportName:"Picks");
    var lines=[icon+" "+name+" AI Picks — all loaded games ("+stacks.length+")"];
    stacks.forEach(function(st, i){
      var away=st.getAttribute("data-away")||"";
      var home=st.getAttribute("data-home")||"";
      var time=st.getAttribute("data-time")||"";
      var pick=st.getAttribute("data-pick")||"";
      var conf=st.getAttribute("data-conf")||"";
      var result=st.getAttribute("data-result")||"";
      var h2h=dash(st.getAttribute("data-h2h"));
      var date="";
      var sec=st.closest(".date-section");
      if(sec&&sec.id) date=(sec.id||"").replace("date-","");
      lines.push("");
      lines.push("—— Game "+(i+1)+(date?" · "+date:"")+" ——");
      var head=away+" @ "+home;
      if(time) head+=" ("+time+")";
      lines.push(head);
      if(pick) lines.push("Pick: "+pick+(conf?" ("+conf+"%)":"")+(result==="WON"?" ✅":result==="LOST"?" ❌":""));
      if(h2h!=="—") lines.push("H2H Last "+"10: "+h2h);
      var plxg=st.getAttribute("data-plxg");
      if(plxg) lines.push("PL Expected Goals: "+plxg);
      st.querySelectorAll(".pc-box").forEach(function(box){
        var n=txt(box.querySelector(".pc-name"));
        var v=txt(box.querySelector(".pc-val"));
        var side=txt(box.querySelector(".pc-side"));
        if(n) lines.push(n+": "+(v||"—")+(side?" · "+side:""));
      });
      var books=dash(st.getAttribute("data-books-spread"));
      var pl=dash(st.getAttribute("data-pl-spread"));
      var xs=dash(st.getAttribute("data-xs-spread"));
      if(books!=="—"||pl!=="—"||xs!=="—") lines.push("Spread — Books: "+books+" | PL: "+pl+" | XSharp: "+xs);
      var tot=dash(st.getAttribute("data-books-total"));
      var plt=dash(st.getAttribute("data-pl-total")||st.getAttribute("data-pl-proj"));
      var xst=dash(st.getAttribute("data-xs-total")||st.getAttribute("data-xs-proj"));
      if(tot!=="—" && tot.toLowerCase().indexOf("o/u")<0) tot="O/U "+tot;
      if(tot!=="—"||plt!=="—"||xst!=="—") lines.push("Total — Books: "+tot+" | PL: "+plt+" | XSharp: "+xst);
      st.querySelectorAll(".ml-line").forEach(function(ml){
        var src=txt(ml.querySelector(".ml-src"));
        var num=txt(ml.querySelector(".ml-num"));
        if(src||num) lines.push((src||"ML")+": "+(num||"—"));
      });
      st.querySelectorAll(".line-chip").forEach(function(ch){
        var lab=txt(ch.querySelector(".line-chip-label"));
        var val=txt(ch.querySelector(".line-chip-val"));
        if(lab&&val) lines.push(lab+": "+val);
      });
    });
    lines.push("");
    lines.push("via predictionlab.io");
    var text=lines.join("\n");
    var done=function(){
      if(!btn) return;
      var o=btn.textContent;
      btn.textContent="✓ Copied";
      btn.classList.add("copied");
      setTimeout(function(){ btn.textContent=o; btn.classList.remove("copied"); },1500);
    };
    if(navigator.clipboard&&window.isSecureContext){
      navigator.clipboard.writeText(text).then(done).catch(function(){
        if(typeof _fallbackCopy==="function") _fallbackCopy(text, done);
        else done();
      });
    } else if(typeof _fallbackCopy==="function"){
      _fallbackCopy(text, done);
    } else {
      done();
    }
  }
  window.copyVisiblePicks=copyVisiblePicks;
})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", script + "\n</body>", 1)
    return html + script
