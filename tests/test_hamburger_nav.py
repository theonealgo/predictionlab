"""Static checks: hamburger TV_MENUS must stay consistent across all nav sources."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAV_CFG = json.loads((ROOT / "qa" / "expected_navigation.json").read_text())

NAV_SOURCES = [
    ROOT / "templates" / "includes" / "picks_nav_chrome.html",
    ROOT / "templates" / "base.html",
    ROOT / "templates" / "underdogs_layout.html",
    ROOT / "NHL77FINAL.py",
]

_PICKS_RE = re.compile(r"picks:\{title:'Picks[^}]+items:\[([^\]]+)\]")
_PROPS_RE = re.compile(r"props:\{title:'Props[^}]+items:\[([^\]]+)\]")
_TOOLS_RE = re.compile(r"tools:\{title:'Tools[^}]+items:\[([^\]]+)\]")
_RESULTS_RE = re.compile(r"results:\{title:'Results[^}]+items:\[([^\]]+)\]")


def _section_hrefs(items_blob: str) -> list[str]:
    return re.findall(r"h:'([^']+)'", items_blob)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_nav_sources_define_tv_menus():
    for path in NAV_SOURCES:
        assert "var TV_MENUS=" in _read(path), f"Missing TV_MENUS in {path.name}"


def test_results_menu_has_all_sports_results():
    required = NAV_CFG["menu_sections"]["results"]["required"]
    for path in NAV_SOURCES:
        text = _read(path)
        m = _RESULTS_RE.search(text)
        assert m, f"No results menu in {path.name}"
        hrefs = _section_hrefs(m.group(1))
        for req in required:
            assert req in hrefs, f"{path.name} results menu missing {req}"


def test_picks_menu_has_no_per_sport_results():
    for path in NAV_SOURCES:
        text = _read(path)
        m = _PICKS_RE.search(text)
        assert m, f"No picks menu in {path.name}"
        hrefs = _section_hrefs(m.group(1))
        bad = [h for h in hrefs if h.endswith("-results")]
        assert not bad, f"{path.name} picks menu must not link to results pages: {bad}"


def test_props_menu_has_no_daily_results():
    for path in NAV_SOURCES:
        text = _read(path)
        m = _PROPS_RE.search(text)
        assert m, f"No props menu in {path.name}"
        items = m.group(1)
        hrefs = _section_hrefs(items)
        assert hrefs == ["/player-props"], f"{path.name} props menu must only contain Player Props"


def test_tools_menu_has_non_prop_model_pages():
    required = {"/performance", "/ai-sports-betting-picks-today", "/our-model-vs-sportsbooks", "/tutorial"}
    for path in NAV_SOURCES:
        text = _read(path)
        m = _TOOLS_RE.search(text)
        assert m, f"No tools menu in {path.name}"
        hrefs = set(_section_hrefs(m.group(1)))
        missing = required - hrefs
        assert not missing, f"{path.name} tools menu missing {sorted(missing)}"


def test_nhl_embedded_tv_menus_match():
    """Both embedded TV_MENUS copies in NHL77FINAL.py must agree."""
    text = _read(ROOT / "NHL77FINAL.py")
    blocks = re.findall(
        r"var TV_MENUS=\{picks:\{title:'Picks & Predictions'[^;]+;",
        text,
    )
    assert len(blocks) == 2, f"Expected 2 TV_MENUS blocks, found {len(blocks)}"
    assert blocks[0] == blocks[1], "NHL77FINAL.py TV_MENUS copies differ"
