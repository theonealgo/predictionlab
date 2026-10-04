from pathlib import Path

SPORT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SPORT_ROOT / "database" / "cfl_sandbox.db"
SCHEMA_PATH = SPORT_ROOT / "database" / "schema.sql"
CACHE_DIR = SPORT_ROOT / "database" / "cache"
