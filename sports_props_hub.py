"""Read-only props presentation; never rebuild historical predictions or grades."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo
from odds_engine.model.prop_math import VERSION

LEAGUES = ("NBA", "NFL", "NHL", "MLB", "NCAAB", "NCAAW", "NCAAF", "WNBA", "CFL", "SOCCER", "UFC", "TENNIS", "GOLF")
MODELS = (("glicko2", "Grinder2"), ("trueskill", "Takedown"), ("elo", "Edge"),
          ("xgboost", "XSharp"), ("ensemble", "Sharp Consensus"), ("efficiency", "Efficiency"),
          ("pl_projection", "PL Projection"), ("pl_count", "PL Count"))
REAL_LINES = {"espn_props", "the_odds_api", "odds_api"}
PL_LINES = {"pl_model"}
ET = ZoneInfo("America/New_York")


def yesterday():
    return (datetime.now(ET).date() - timedelta(days=1)).isoformat()


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def preview(league, entry, limit=3, day=None):
    """Only show current real market lines; legacy confidence offsets are not models."""
    rows = []
    stamp = finite((entry or {}).get("ts"))
    try:
        current = stamp is not None and datetime.fromtimestamp(stamp, ET).date() == datetime.now(ET).date()
    except (ValueError, OverflowError, OSError):
        current = False
    payload = (entry or {}).get("payload") or {}
    target_day = day or datetime.now(ET).date().isoformat()
    players = {str(p.get("player_id")): p for p in payload.get("players") or [] if isinstance(p, dict)} \
        if isinstance(payload, dict) else {}
    dated = False
    if current and isinstance(payload, dict):
        for row in payload.get("props") or []:
            if not isinstance(row, dict):
                continue
            line = finite(row.get("line"))
            if row.get("line_source") not in REAL_LINES | PL_LINES or line is None or not row.get("player_name"):
                continue
            player = players.get(str(row.get("player_id")), {})
            if day:
                player = players.get(str(row.get("player_id")), {})
                stamp = row.get("event_start") or (player.get("next_game") or {}).get("start_time")
                try:
                    start = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    if start.tzinfo is None:
                        continue
                    if start.astimezone(ET).date().isoformat() != target_day:
                        continue
                except (ValueError, TypeError):
                    continue
            forecasts = []
            if row.get("schema_version") == VERSION:
                for forecast in row.get("model_forecasts") or []:
                    if forecast.get("model_id") not in {identity for identity, name in MODELS}:
                        continue
                    probs = forecast.get("probabilities") or {}
                    values = [finite(probs.get(key)) for key in ("over", "under", "push")]
                    if any(value is None or not 0 <= value <= 1 for value in values) or abs(sum(values)-1) > 1e-6:
                        continue
                    side = "OVER" if values[0] > values[1] else "UNDER" if values[1] > values[0] else None
                    forecasts.append({"model_id": forecast["model_id"], "model_name": forecast["model_name"],
                                      "model_version": forecast["model_version"], "probabilities": probs,
                                      "side": side, "probability": 100*max(values[0], values[1]),
                                      "projection": forecast.get("projection"), "sample": forecast.get("sample")})
            dated = True
            rows.append({"player_id": row.get("player_id"), "player_name": row["player_name"],
                         "team": row.get("team"), "prop_type": row.get("prop_type"), "line": line,
                         "event_id": row.get("event_id"), "event_start": row.get("event_start"),
                         "opponent": (player.get("next_game") or {}).get("opponent"),
                         "line_type": "pl" if row.get("line_source") in PL_LINES else "market",
                         "pl_line": row.get("pl_line"), "model_forecasts": forecasts,
                         "model_unavailable": row.get("model_unavailable") or [],
                         "input_hash": row.get("input_hash"),
                         "input_values": row.get("input_values"),
                         "pick": row.get("picked_side") if forecasts else None,
                         "pick_probability": row.get("pick_probability") if forecasts else None,
                         "strong_pick": bool(row.get("strong_pick")) if forecasts else False,
                         "model_reason": None if forecasts else row.get("model_reason") or
                         "Forecasts are updating or verified recent stats are unavailable."})
    exact = payload.get("requested_date") == target_day if isinstance(payload, dict) else False
    known = current and exact and payload.get("availability") in {"ready", "no_fixtures", "no_verified_props", "slate_started"}
    items = rows
    if limit is not None:
        if payload.get("player_pool_version"):
            counts, items = Counter(), []
            for row in rows:
                if counts[row["prop_type"]] < limit:
                    items.append(row)
                    counts[row["prop_type"]] += 1
        else:
            items = rows[:limit]
    return {"league": league, "date": target_day, "items": items,
            "total": len(rows) if dated or known else None, "count_kind": "available",
            "status": payload.get("availability") if exact else ("ready" if dated else "not_loaded"),
            "refresh_notice": payload.get("refresh_notice"),
            "next_date": payload.get("next_date") if exact else None,
            "available": bool(rows), "reason": None if rows else
            (payload.get("availability_reason") if exact else None) or
            "Current props for this date have not loaded yet."}


def historical_catalog(db_path, day, selected=None, limit=3):
    """Counts are recorded props for this exact night, not model votes or game counts."""
    if selected and selected not in LEAGUES:
        raise ValueError("Unsupported sport")
    data = read_results(db_path, day=day)
    sections, counts = [], []
    for league in LEAGUES:
        records = [row for row in data["items"] if row["league"] == league]
        modern = [row for row in records if row.get("model_id")]
        if modern:
            offers = {}
            for row in modern:
                key = (row["event_id"], row["player_id"], row["prop_type"], row["line"], row.get("line_type"))
                item = offers.setdefault(key, {"player_id": row["player_id"], "player_name": row["player_name"],
                    "team": row["team"], "prop_type": row["prop_type"], "line": row["line"],
                    "line_type": row.get("line_type"), "event_id": row["event_id"],
                    "event_start": row.get("event_start"), "game_id": row.get("game_id"),
                    "game_label": row.get("game_label"), "model_forecasts": [], "model_reason": None})
                item["model_forecasts"].append({"model_id": row["model_id"], "model_name": row["model_name"],
                    "side": row["pick"], "probability": row["probability"], "probabilities": row["probabilities"]})
            items = list(offers.values())
        else:
            items = [{"player_id": row["id"], "player_name": row["player_name"], "team": row["team"],
                      "prop_type": row["prop_type"], "line": row["line"], "recorded_side": row["pick"],
                      "line_type": row.get("line_type"), "event_id": row.get("event_id"),
                      "model_forecasts": [], "model_reason": "Separate model predictions were not recorded."}
                     for row in records]
        counts.append({"league": league, "count": len(items) if data["records_available"] or modern else None, "kind": "recorded"})
        if selected and league != selected:
            continue
        sections.append({"league": league, "date": day, "items": items[:limit] if limit is not None else items,
                         "total": len(items) if data["records_available"] or modern else None,
                         "count_kind": "recorded", "available": bool(items),
                         "reason": None if items else ("No props were recorded for this date." if data["records_available"]
                                                     else "Saved prop records are unavailable."),
                         "building": False})
    return {"leagues": list(LEAGUES), "date": day, "counts": counts, "sections": sections}


def season_start(league, day):
    anchor = datetime.strptime(day, "%Y-%m-%d").date()
    if league in {"NBA", "NHL", "NCAAB", "NCAAW", "NFL", "NCAAF", "SOCCER"}:
        return f"{anchor.year if anchor.month >= 7 else anchor.year-1}-07-01"
    return f"{anchor.year}-01-01"


def outcome_summary(rows):
    counts = Counter(row.get("result") for row in rows)
    wins, losses = counts["Win"], counts["Loss"]
    return {"recorded": len(rows), "wins": wins, "losses": losses, "pushes": counts["Push"],
            "pending": counts["Pending"], "voids": counts["Void"], "unavailable": counts["N/A"],
            "decided": wins+losses, "win_rate": 100*wins/(wins+losses) if wins+losses else None}


def model_type_summary(rows):
    grouped = {}
    for row in rows:
        if not row.get("model_id"):
            continue
        key = (row["league"], row["model_id"], row["prop_type"])
        grouped.setdefault(key, []).append(row)
    return [{"league": league, "model_id": model, "model_name": records[0]["model_name"],
             "prop_type": prop, **outcome_summary(records)}
            for (league, model, prop), records in sorted(grouped.items())]


def read_results(db_path, league=None, day=None, window="night"):
    """Read existing tables through mode=ro. Legacy records never become model-specific wins."""
    day = day or yesterday()
    if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
        raise ValueError("Invalid date")
    if league and league not in LEAGUES:
        raise ValueError("Unsupported sport")
    if window not in {"night", "7", "30", "season"}:
        raise ValueError("Unsupported result window")
    starts = {sport: (season_start(sport, day) if window == "season" else
                     (datetime.strptime(day, "%Y-%m-%d").date() -
                      timedelta(days=int(window)-1 if window in {"7", "30"} else 0)).isoformat())
              for sport in ([league] if league else LEAGUES)}
    first_day = min(starts.values())
    rows = []
    path = Path(db_path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "player_prop_picks" in tables:
            pick_columns = {record[1] for record in conn.execute("PRAGMA table_info(player_prop_picks)")}
            source_column = "p.line_source" if "line_source" in pick_columns else "NULL AS line_source"
            result_columns = "r.actual, r.result AS recorded_result" if "player_prop_results" in tables else \
                "NULL AS actual, NULL AS recorded_result"
            join = """ LEFT JOIN player_prop_results r
                       ON r.league=p.league AND r.result_date=p.pick_date
                      AND r.player_name=p.player_name AND r.prop_type=p.prop_type
                      AND r.pick=p.pick AND r.line=p.line""" if "player_prop_results" in tables else ""
            sql = f"""SELECT p.id, p.league, p.pick_date, p.player_name, p.team, p.prop_type,
                             p.pick, p.line, p.created_at, {source_column}, {result_columns}
                      FROM player_prop_picks p {join} WHERE p.pick_date BETWEEN ? AND ?"""
            params = [first_day, day]
            if league:
                sql += " AND p.league=?"
                params.append(league)
            sql += " ORDER BY p.league,p.player_name,p.prop_type,p.id"
            for record in conn.execute(sql, params):
                row = dict(record)
                if row["league"] not in starts or row["pick_date"] < starts[row["league"]]:
                    continue
                row.update(model_id=None, model_name="Not recorded", probability=None, result="N/A",
                           line_type="market" if row["line_source"] in REAL_LINES else
                           "pl" if row["line_source"] in PL_LINES else "legacy",
                           reason="A separate pregame model selection and probability were not recorded.",
                           event=None)
                rows.append(row)
    from prop_forecast_store import read as read_model_forecasts, first_recorded_dates
    recorded_dates = first_recorded_dates(db_path, league)
    original_forecasts = {}
    for row in sorted(read_model_forecasts(db_path, first_day, league, end_day=day),
                      key=lambda item: item.get("captured_at") or ""):
        if row["league"] not in starts or row["pick_date"] < starts[row["league"]]:
            continue
        key = (row["league"], row["event_id"], row["player_id"], row["prop_type"],
               row["model_id"], row.get("line_type"))
        original_forecasts.setdefault(key, row)
    rows.extend(original_forecasts.values())
    try:
        from prop_actuals import read_actuals
    except ImportError:
        recovered = {}
    else:
        recovered = read_actuals(db_path, first_day, league, end_day=day)
    for row in rows:
        if row.get("model_id"):
            continue
        actual = recovered.get(row["id"]) or recovered.get(str(row["id"]))
        if actual:
            row.update(actual=actual.get("actual"), selection_result=actual.get("selection_result", "N/A"),
                       actual_reason=actual.get("reason"), event_id=actual.get("event_id"),
                       player_id=actual.get("player_id"), event_start=actual.get("event_start"),
                       pregame_verified=bool(actual.get("pregame_verified")),
                       actual_label=actual.get("actual_label"),
                       actual_source="Official final statistics")
    markets = Counter(row["prop_type"] for row in rows)
    models = []
    for key, name in MODELS:
        selected = [row for row in rows if row.get("model_id") == key]
        if key == "efficiency" or (key.startswith("pl_") and not selected):
            continue
        wins = sum(row["result"] == "Win" for row in selected)
        losses = sum(row["result"] == "Loss" for row in selected)
        pushes = sum(row["result"] == "Push" for row in selected)
        models.append({"id": key, "name": name, "first_recorded_date": recorded_dates.get(key),
                       "wins": wins, "losses": losses, "pushes": pushes,
                       "decided": wins+losses, "win_rate": 100*wins/(wins+losses) if wins+losses else None,
                       "reason": ("Efficiency is a game-winner model, not a player-stat model." if key == "efficiency"
                                  else "Awaiting decided results." if selected else "No separately recorded model results.")})
    return {"league": league, "result_date": day, "start_date": first_day, "window": window,
            "season_starts": starts if window == "season" else None, "count": len(rows), "items": rows,
            "models": models,
            "by_model_prop_type": model_type_summary(rows),
            "by_prop_type": [{"prop_type": key, "recorded": n, "win_rate": None}
                             for key, n in sorted(markets.items())],
            "readonly": True, "records_available": "player_prop_picks" in tables,
            "notice": "Model records use the first saved pregame selection for each player, prop, game and line type. Older unassigned selections are excluded."}
