import plistlib
from pathlib import Path


def test_installed_sitechecker_runs_nightly_at_8pm():
    plist_path = (
        Path(__file__).resolve().parents[1]
        / "qa/io.predictionlab.sitechecker.plist"
    )
    data = plistlib.loads(plist_path.read_bytes())

    assert data["StartCalendarInterval"] == {"Hour": 20, "Minute": 0}
