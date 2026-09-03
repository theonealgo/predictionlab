from pathlib import Path
SPORT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = SPORT_ROOT / "database" / "tennis.sqlite"
SCHEMA_PATH = SPORT_ROOT / "database" / "schema.sql"
