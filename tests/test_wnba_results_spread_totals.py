"""WNBA Spread/Totals must not clone empty moneyline tiles.

The owner paste: season headline 68-31 while Edge/XSharp/SC/Efficiency
under SPREAD and TOTALS are all 0-0 · no picks. Those names are ML models.
WNBA does not publish Grinder2 / Takedown.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wnba_ui_fixup import (
    render_wnba_chart_market_tallies,
    wnba_results_html_has_empty_ml_sou_tiles,
    wnba_sou_tile_names,
)


def _broken_chart_html() -> str:
    return """
    <p>Spread · Season 68.7% (68-31) · 33 recent graded games shown</p>
    <div class="bet-type-banner" data-market="spread">SPREAD</div>
    <section class="tally"><h2>Last Night</h2>
      <div class="mlabel">Edge</div><div class="acc muted">—</div><div class="rec">0-0 · no picks</div>
      <div class="mlabel">XSharp</div><div class="acc muted">—</div><div class="rec">0-0 · no picks</div>
      <div class="mlabel">Sharp Consensus</div><div class="acc muted">—</div><div class="rec">0-0 · no picks</div>
      <div class="mlabel">Efficiency</div><div class="acc muted">—</div><div class="rec">0-0 · no picks</div>
    </section>
    """


def _face_payload() -> dict:
    def face(w, l, games=None, **extra):
        n = w + l
        pct = round(100.0 * w / n, 1) if n else None
        rec = f"{w}-{l}"
        row = {"w": w, "l": l, "n": n, "pct": pct, "record": rec}
        return {
            "games": games if games is not None else n,
            "w": w,
            "l": l,
            "n": n,
            "pct": pct,
            "record": rec,
            "models": {"Prediction Lab": row},
            "model_order": ["Prediction Lab"],
            **extra,
        }

    return {
        "ok": True,
        "markets": {
            "spread": {
                "label": "Spread",
                "model_order": ["Prediction Lab"],
                "tallies": {
                    "last_night": face(1, 0, games=1, date="2026-08-17"),
                    "last_7": face(14, 4, games=18),
                    "season": face(68, 31, games=99),
                },
            },
            "totals": {
                "label": "Totals",
                "model_order": ["Prediction Lab"],
                "tallies": {
                    "last_night": face(1, 0, games=1, date="2026-08-17"),
                    "last_7": face(16, 2, games=18),
                    "season": face(64, 35, games=99),
                },
            },
        },
    }


def test_owner_spread_grid_is_the_forbidden_pattern():
    assert wnba_results_html_has_empty_ml_sou_tiles(_broken_chart_html()) is True


def test_sou_tile_names_never_clone_empty_ml():
    models = {
        "Prediction Lab": {"n": 99, "w": 68, "l": 31, "record": "68-31", "pct": 68.7},
        "Edge": {"n": 0, "w": 0, "l": 0, "record": "0-0"},
        "XSharp": {"n": 0, "w": 0, "l": 0, "record": "0-0"},
        "Sharp Consensus": {"n": 0, "w": 0, "l": 0, "record": "0-0"},
        "Efficiency": {"n": 0, "w": 0, "l": 0, "record": "0-0"},
    }
    # Broken JS used to pass ML order after dropping Prediction Lab.
    names = wnba_sou_tile_names(
        ["Edge", "XSharp", "Sharp Consensus", "Efficiency"],
        models,
        market="spread",
    )
    assert names == ["Prediction Lab"]
    assert "Edge" not in names
    names2 = wnba_sou_tile_names(["Prediction Lab"], models, market="totals")
    assert names2 == ["Prediction Lab"]


def test_rendered_spread_totals_not_empty_ml_when_season_ats_exists():
    payload = _face_payload()
    spread = render_wnba_chart_market_tallies(payload, "spread")
    totals = render_wnba_chart_market_tallies(payload, "totals")
    assert "68-31" in spread
    assert "64-35" in totals
    assert "Prediction Lab" in spread and "Prediction Lab" in totals
    assert wnba_results_html_has_empty_ml_sou_tiles(spread) is False
    assert wnba_results_html_has_empty_ml_sou_tiles(totals) is False
    for name in ("Edge", "XSharp", "Sharp Consensus", "Efficiency"):
        assert f">{name}<" not in spread
        assert f">{name}<" not in totals
        assert f"{name}" not in spread or "0-0 · no picks" not in spread
    assert "0-0 · no picks" not in spread
    assert "0-0 · no picks" not in totals
    assert "Grinder2" not in spread and "Takedown" not in spread
    assert "Grinder2" not in totals and "Takedown" not in totals
    assert "14-4" in spread and "16-2" in totals
    assert "1-0" in spread and "1-0" in totals


def test_wnba_results_cards_html_has_no_g2_td_or_empty_ml_sou():
    """Live cards HTML (if present) must stay G2/TD-free and not clone ML onto ATS."""
    import NHL77FINAL as N

    client = N.app.test_client()
    rv = client.get("/wnba-results")
    assert rv.status_code == 200
    html = rv.get_data(as_text=True)
    assert "Grinder2" not in html
    assert "Takedown" not in html
    assert wnba_results_html_has_empty_ml_sou_tiles(html) is False
    assert "68-31" in html or "Spread" in html
