"""Homepage must not chain /api/homepage-live onto first paint / LCP."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/homepage_preview.html"


def test_live_fetch_is_gated_off_the_critical_path():
    html = TEMPLATE.read_text(encoding="utf-8")
    fetch_at = html.find("fetch('/api/homepage-live'")
    assert fetch_at != -1
    gate = html.rfind("{% if not homepage_defer_live %}", 0, fetch_at)
    endif = html.find("{% endif %}", fetch_at)
    assert gate != -1, "homepage-live fetch must sit behind homepage_defer_live"
    assert endif != -1
    assert gate < fetch_at < endif


def test_fast_landing_sets_defer_live():
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parents[1] / "NHL77FINAL.py").read_text(encoding="utf-8")
    assert "homepage_defer_live=True" in src
    assert "stale-while-revalidate=1800" in src
