"""Soccer results: per-league home-win / draw / away-win table."""

from soccer_ui_fixup import inject_soccer_league_outcome_records


def test_soccer_league_outcome_records_from_cards():
    card = (
        '<div class="game-card" data-pick-card data-league="English Premier League" '
        'data-date="2026-09-14">'
        # away @ home scores
        '<div class="final-score">0</div><div class="final-score">1</div>'
        "</div>"
        '<div class="game-card" data-pick-card data-league="English Premier League" '
        'data-date="2026-09-14">'
        '<div class="final-score">2</div><div class="final-score">2</div>'
        "</div>"
        '<div class="game-card" data-pick-card data-league="Spanish LaLiga" '
        'data-date="2026-09-14">'
        '<div class="final-score">3</div><div class="final-score">0</div>'
        "</div>"
        '<div id="date-2026-09-14"></div>'
    )
    out = inject_soccer_league_outcome_records(card)
    assert 'id="soccer-league-outcome-records"' in out
    assert "English Premier League" in out
    assert "Spanish LaLiga" in out
    assert "Home wins" in out
    assert "Draws" in out
    assert "Away wins" in out
    # EPL: 1 home win, 1 draw; LaLiga: 1 away win
    assert out.count("<tr>") >= 3  # header + 2 leagues
