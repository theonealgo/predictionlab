import sqlite3
from urllib.parse import parse_qs, urlsplit

from public_name_search import find_names, local_entities, normalized

SLUGS = {"NFL": "nfl-picks", "MLB": "mlb-picks", "NBA": "nba-picks",
         "SOCCER": "soccer-picks", "TENNIS": "tennis-picks"}
ORDER = tuple(SLUGS)


def fixture_rows():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE games(sport,home_team_id,away_team_id);
        CREATE TABLE predictions(sport,home_team_id,away_team_id);
        CREATE TABLE player_prop_picks(league,player_name,team,line,projection);
        CREATE TABLE player_prop_results(league,player_name);
        INSERT INTO games VALUES('MLB','Chicago White Sox','Athletics');
        INSERT INTO predictions VALUES('SOCCER','Vancouver Whitecaps','Other');
        INSERT INTO predictions VALUES('TENNIS','Bruce Whitehead','Other Player');
        INSERT INTO player_prop_picks VALUES('NFL','Rachaad White','Team','PRIVATE_LINE','PRIVATE_PROJECTION');
        INSERT INTO player_prop_results VALUES('NFL','Rachaad White');
        INSERT INTO player_prop_results VALUES('NBA','Derrick White');
    """)
    rows = local_entities(connection)
    connection.close()
    return rows


def no_provider(query, bucket):
    return [], False


def test_white_finds_rachaad_before_whitehead_and_keeps_teams():
    rows, partial = find_names("White", fixture_rows(), SLUGS, ORDER, provider=no_provider)
    labels = [row["label"] for row in rows]
    assert labels[0] == "Rachaad White"
    assert {"Derrick White", "Chicago White Sox", "Vancouver Whitecaps", "Bruce Whitehead"} <= set(labels)
    assert not partial
    query = parse_qs(urlsplit(rows[0]["href"]).query)
    assert query == {"league": ["NFL"], "player": ["Rachaad White"]}
    assert sum(row["label"] == "Rachaad White" for row in rows) == 1


def test_search_output_contains_no_paid_prop_values():
    rows, _ = find_names("white", fixture_rows(), SLUGS, ORDER, provider=no_provider)
    assert all(set(row) == {"label", "sport", "kind", "href"} for row in rows)
    assert "PRIVATE" not in str(rows)


def test_public_provider_covers_player_not_on_any_local_slate():
    def provider(query, bucket):
        return [{"label": "New White", "sport": "NFL", "kind": "player"}], False
    rows, _ = find_names("New White", [], SLUGS, ORDER, provider=provider)
    assert rows[0]["label"] == "New White"


def test_broad_roster_matches_do_not_crowd_out_existing_whitecaps():
    def provider(query, bucket):
        return [{"label": f"Player {number} White", "sport": "NFL", "kind": "player"}
                for number in range(40)], False
    rows, _ = find_names("white", fixture_rows(), SLUGS, ORDER, provider=provider)
    labels = [row["label"] for row in rows]
    assert "Rachaad White" in labels and "Chicago White Sox" in labels
    assert "Vancouver Whitecaps" in labels and "Bruce Whitehead" in labels


def test_provider_failure_keeps_real_local_matches_and_marks_partial():
    rows, partial = find_names("white", fixture_rows(), SLUGS, ORDER,
                              provider=lambda q, b: ([], True))
    assert partial and any(row["label"] == "Rachaad White" for row in rows)


def test_missing_optional_tables_does_not_break_available_names():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE games(sport,home_team_id,away_team_id)")
    connection.execute("INSERT INTO games VALUES('NFL','Team One','Team Two')")
    rows = local_entities(connection)
    connection.close()
    assert {row["label"] for row in rows} == {"Team One", "Team Two"}


def test_case_accent_normalization_and_short_queries():
    assert normalized("JOSÉ   White") == "jose white"
    rows, _ = find_names("jose", [{"label": "José White", "sport": "NFL", "kind": "player"}],
                         SLUGS, ORDER, provider=no_provider)
    assert rows[0]["label"] == "José White"
    assert find_names("w", [], SLUGS, ORDER, provider=no_provider) == ([], False)
