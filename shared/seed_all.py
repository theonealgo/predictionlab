"""Seed demo SQLite DBs for every sport so hub pages are non-empty."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    # Make sure demo + isolation DBs are writable before seed/request writes.
    try:
        from shared.db import ensure_writable_sqlite, prepare_hub_sqlite_dbs

        prepare_hub_sqlite_dbs()
    except Exception as e:
        print(f"seed prepare warning: {e}")

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
            # Prefer writing through ensure_writable when init_db exposes DB_PATH.
            try:
                from shared.db import ensure_writable_sqlite

                db_path = getattr(mod, "DB_PATH", None)
                if db_path is None:
                    paths_mod = __import__(
                        modpath.rsplit(".", 1)[0] + ".paths",
                        fromlist=["DB_PATH"],
                    )
                    db_path = getattr(paths_mod, "DB_PATH", None)
                if db_path is not None:
                    ensure_writable_sqlite(db_path)
            except Exception:
                pass
            path = mod.seed()
            print(f"seeded {name}: {path}")
        except Exception as e:
            print(f"seed skip {name}: {e}")


if __name__ == "__main__":
    main()
