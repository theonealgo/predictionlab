from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def test_seed_and_list():
    from tennis.database.init_db import seed
    path = seed()
    assert path.exists()
