"""Seed demo SQLite DBs for every sport so hub pages are non-empty."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    seeds = [
        ("CFL", "cfl.database.init_db"),
        ("GOLF", "golf.database.init_db"),
        ("UFC", "ufc.database.init_db"),
        ("TENNIS", "tennis.database.init_db"),
        ("MLB", "mlb.database.init_db"),
        ("SOCCER", "soccer.database.init_db"),
        ("WNBA", "wnba.database.init_db"),
    ]
    for name, modpath in seeds:
        try:
            mod = __import__(modpath, fromlist=["seed"])
            path = mod.seed()
            print(f"seeded {name}: {path}")
        except Exception as e:
            print(f"seed skip {name}: {e}")


if __name__ == "__main__":
    main()
