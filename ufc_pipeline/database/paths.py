from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "ufc_sandbox.db"
SCHEMA_PATH = ROOT / "database" / "schema.sql"
CACHE_DIR = ROOT / "database" / "cache"
