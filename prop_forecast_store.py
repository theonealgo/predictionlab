"""Prospective PL model ledger. Never modifies the application's original tables."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def ledger_path(original_db):
    return Path(original_db).resolve().with_name("prop_forecasts_v2.sqlite")


def _aware(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def capture(original_db, sections, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Publication time must be aware")
    records = []
    inputs = {}
    for section in sections:
        league = section["league"]
        for row in section.get("items") or []:
            start = _aware(row.get("event_start"))
            if not start or start <= now or not str(row.get("event_id") or "").isdigit() \
                    or not str(row.get("player_id") or "").isdigit() or not row.get("input_hash"):
                continue
            for forecast in row.get("model_forecasts") or []:
                side = forecast.get("side")
                probs = forecast.get("probabilities") or {}
                try:
                    values = [float(probs[key]) for key in ("over", "under", "push")]
                    line = float(row["line"])
                    if not math.isfinite(line) or any(not math.isfinite(p) or not 0 <= p <= 1 for p in values) \
                            or abs(sum(values)-1) > 1e-6 or side not in ("OVER", "UNDER"):
                        continue
                    expected = "OVER" if values[0] > values[1] else "UNDER" if values[1] > values[0] else None
                    if side != expected:
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                key = [league, row["event_id"], row["player_id"], row["prop_type"], line,
                       row.get("line_type", "market"), forecast["model_id"], forecast["model_version"]]
                identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()
                record = {**row, "id": identity, "league": league, "model_id": forecast["model_id"],
                          "model_name": forecast["model_name"], "model_version": forecast["model_version"],
                          "pick": side, "probability": 100*probs[side.lower()],
                          "probabilities": probs, "captured_at": now.isoformat(),
                          "pick_date": start.astimezone(ET).date().isoformat()}
                if row.get("input_values") is not None:
                    inputs[row["input_hash"]] = json.dumps(row["input_values"], allow_nan=False)
                for key in ("input_values", "model_forecasts", "model_unavailable", "model_confidence"):
                    record.pop(key, None)
                records.append((identity, league, record["pick_date"], json.dumps(record, allow_nan=False)))
    if not records:
        return 0
    path = ledger_path(original_db)
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS forecasts(
            id TEXT PRIMARY KEY, league TEXT NOT NULL, pick_date TEXT NOT NULL, payload TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS forecast_day ON forecasts(league,pick_date)")
        conn.execute("CREATE TABLE IF NOT EXISTS forecast_inputs(input_hash TEXT PRIMARY KEY,input_json TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS settlements(
            id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_id TEXT NOT NULL,
            payload TEXT NOT NULL, source_hash TEXT NOT NULL,
            UNIQUE(forecast_id,source_hash))""")
        conn.executemany("INSERT OR IGNORE INTO forecast_inputs VALUES(?,?)", list(inputs.items()))
        conn.executemany("INSERT OR IGNORE INTO forecasts VALUES(?,?,?,?)", records)
        conn.commit()
    return len(records)


def read(original_db, day, league=None, end_day=None):
    path = ledger_path(original_db)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
        conn.execute("PRAGMA query_only=ON")
        sql = """SELECT json_remove(f.payload,'$.input_values','$.model_forecasts','$.model_unavailable',
                                    '$.model_confidence'),s.payload FROM forecasts f LEFT JOIN settlements s
                   ON s.id=(SELECT MAX(s2.id) FROM settlements s2 WHERE s2.forecast_id=f.id)
                 WHERE f.pick_date BETWEEN ? AND ?"""
        params = [day, end_day or day]
        if league:
            sql += " AND f.league=?"
            params.append(league)
        rows = []
        for payload, settlement in conn.execute(sql, params):
            row = json.loads(payload)
            result = json.loads(settlement) if settlement else {
                "actual": None, "result": "Pending", "reason": "Awaiting a verified final player stat."}
            row.update(result)
            row.update(recorded_result=None, event=row.get("event_id"))
            row.pop("input_values", None)
            rows.append(row)
    return rows
