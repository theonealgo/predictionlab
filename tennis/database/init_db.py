"""Initialize and seed Tennis SQLite DB."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.db import connect, init_from_schema
from tennis.database.paths import DB_PATH, SCHEMA_PATH


def seed() -> Path:
    try:
        from tennis.espn_sync import sync_tennis_db

        synced = sync_tennis_db()
        if synced.get("ok") and int(synced.get("n") or 0) >= 8:
            return DB_PATH
    except Exception:
        pass
    init_from_schema(DB_PATH, SCHEMA_PATH.read_text(encoding="utf-8"))
    today = datetime.utcnow().date()
    players = [
        ("t1", "Jannik Sinner", 1780, 1785, 1700, 1720),
        ("t2", "Carlos Alcaraz", 1770, 1760, 1785, 1750),
        ("t3", "Novak Djokovic", 1740, 1745, 1730, 1760),
        ("t4", "Daniil Medvedev", 1680, 1700, 1600, 1650),
    ]
    with connect(DB_PATH) as conn:
        for t in ("grades", "predictions", "matches", "players", "model_runs"):
            conn.execute(f"DELETE FROM {t}")
        conn.executemany("INSERT INTO players VALUES (?,?,?,?,?,?)", players)
        matches = [
            ("m1", str(today - timedelta(days=1)), "ATP Cincinnati", "hard",
             "Jannik Sinner", "Daniil Medvedev", "Jannik Sinner", 2, 0, 12, 7, "final"),
            ("m3", str(today - timedelta(days=3)), "ATP Cincinnati", "hard",
             "Carlos Alcaraz", "Daniil Medvedev", "Carlos Alcaraz", 2, 1, 18, 14, "final"),
            ("m4", str(today - timedelta(days=5)), "ATP Madrid", "clay",
             "Novak Djokovic", "Jannik Sinner", "Novak Djokovic", 2, 0, 13, 9, "final"),
            ("m2", str(today + timedelta(days=1)), "ATP Cincinnati", "hard",
             "Carlos Alcaraz", "Novak Djokovic", None, None, None, None, None, "scheduled"),
        ]
        conn.executemany("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", matches)
        conn.execute(
            "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tp1", "m1", "2026-07-24T10:00:00", "ensemble", 0.68, 2.1, 0.4, 21.5, 0.45, 22.5, "UNDER", 0.72),
        )
        conn.execute(
            "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tp3", "m3", "2026-07-22T10:00:00", "ensemble", 0.61, 1.9, 1.1, 23.0, 0.40, 22.5, "OVER", 0.66),
        )
        conn.execute(
            "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tp4", "m4", "2026-07-20T10:00:00", "ensemble", 0.44, 1.2, 1.8, 22.0, 0.35, 21.5, "UNDER", 0.59),
        )
        conn.execute(
            "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tp2", "m2", "2026-07-26T10:00:00", "ensemble", 0.53, 1.6, 1.4, 24.0, 0.28, 23.5, "OVER", 0.58),
        )
        conn.execute(
            "INSERT INTO grades VALUES (?,?,?,?,?,?)",
            ("tg1", "tp1", 1, "WIN", "WIN", "2026-07-25T02:00:00"),
        )
        conn.execute(
            "INSERT INTO grades VALUES (?,?,?,?,?,?)",
            ("tg3", "tp3", 1, "WIN", "WIN", "2026-07-23T02:00:00"),
        )
        # m4: model leaned player_a (prob 0.44 → pick B) but Djokovic won — count as miss on A-prob rule
        conn.execute(
            "INSERT INTO grades VALUES (?,?,?,?,?,?)",
            ("tg4", "tp4", 0, "LOSS", "LOSS", "2026-07-21T02:00:00"),
        )
        conn.execute(
            "INSERT INTO model_runs VALUES (?,?,?,?,?,?)",
            ("run_t1", "2026-07-20T10:00:00", "ensemble", 0.64, 0.19, "demo seed"),
        )
        conn.commit()
    return DB_PATH


if __name__ == "__main__":
    print(seed())
