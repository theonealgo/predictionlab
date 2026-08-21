"""Soccer H2H Last 10: fill from prior completed games, else ⓘ — never a silent dash."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_ui_fixup import (  # noqa: E402
    aliases_for,
    apply_soccer_picks_fixups,
    fill_soccer_h2h_display_fields,
    h2h_projection,
)


def test_alias_key_matches_atletico_spellings():
    from soccer_ui_fixup import _alias_key

    assert _alias_key("Club Atlético de Madrid") == _alias_key("Atletico Madrid")
    names = ["Atletico Madrid", "Club Atlético de Madrid", "Atlético Madrid"]
    found = aliases_for("Club Atlético de Madrid", names)
    assert "Atletico Madrid" in found or "Atlético Madrid" in found


def test_h2h_projection_excludes_this_kickoff(tmp_path):
    db = tmp_path / "h2h.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE games (sport TEXT, home_team_id TEXT, away_team_id TEXT, "
        "home_score REAL, away_score REAL, game_date TEXT)"
    )
    conn.executemany(
        "INSERT INTO games VALUES ('SOCCER',?,?,?,?,?)",
        [
            ("Independiente Rivadavia", "Fluminense", 1, 1, "2026-08-18"),
            ("Independiente Rivadavia", "Fluminense", 1, 1, "2026-05-07"),
            ("Fluminense", "Independiente Rivadavia", 1, 2, "2026-04-16"),
        ],
    )
    conn.commit()
    leaked = h2h_projection(conn, "Independiente Rivadavia", "Fluminense", min_games=1)
    assert leaked and leaked["games_used"] == 3
    prior = h2h_projection(
        conn, "Independiente Rivadavia", "Fluminense", min_games=1, before_date="2026-08-18"
    )
    assert prior and prior["games_used"] == 2
    assert prior["our_total"] == pytest.approx(2.5)
    none = h2h_projection(conn, "Banfield", "Midland", min_games=1, before_date="2026-08-18")
    assert none is None
    conn.close()


def test_fill_sets_missing_reason_on_first_meeting():
    pred = {
        "home_team_id": "Banfield",
        "away_team_id": "Midland",
        "home_score": 2,
        "away_score": 2,
        "date": "2026-08-18",
    }
    fill_soccer_h2h_display_fields([pred])
    assert pred.get("h2h_last10_total") is None
    assert pred.get("h2h_missing_reason")
    assert "prior" in pred["h2h_missing_reason"].lower() or "meeting" in pred["h2h_missing_reason"].lower()


def test_enrich_unknown_clubs_show_plxg_or_info():
    from soccer_pl_xg import enrich_soccer_plxg_html

    bare = (
        '<html><body>'
        '<div class="game-card-stack" data-pick-card '
        'data-home-full="Banfield" data-away-full="Midland" '
        'data-date="2026-08-18" data-time="FINAL" data-h2h="">'
        '<div class="odds-extras-footer"></div>'
        '</div>'
        '</body></html>'
    )
    out = enrich_soccer_plxg_html(bare)
    assert "PL Expected Goals" in out
    assert "H2H Last 10" not in out
    assert "Home" in out
    assert "—" not in re.search(r'class="sf-val">([^<]*)</span>', out).group(1)
    full = apply_soccer_picks_fixups(bare)
    assert "PL Expected Goals" in full
    assert "H2H Last 10" not in full
    assert "H2H L10" not in full


def test_enrich_fills_plxg_when_form_exists():
    from soccer_pl_xg import enrich_soccer_plxg_html

    bare = (
        '<html><body>'
        '<div class="game-card-stack" data-pick-card '
        'data-home-full="Independiente Rivadavia" data-away-full="Fluminense" '
        'data-date="2026-08-18" data-time="FINAL" data-h2h="">'
        '<div class="odds-extras-footer"></div>'
        '</div>'
        '</body></html>'
    )
    out = enrich_soccer_plxg_html(bare)
    assert "PL Expected Goals" in out
    assert "data-plxg=" in out
    assert "H2H Last 10" not in out
