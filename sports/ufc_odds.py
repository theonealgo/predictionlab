"""
UFC / MMA sportsbook odds adapter.
Source: The Odds API
Sport key: mma_mixed_martial_arts (confirmed active)
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"
SPORT_KEY = "mma_mixed_martial_arts"


def get_ufc_events():
    """
    Returns all upcoming UFC/MMA events with odds.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/sports/{SPORT_KEY}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            },
            timeout=10
        )

        if r.status_code != 200:
            print("UFC ODDS API ERROR:", r.status_code, r.text[:200])
            return []

        games = r.json()

        if not games:
            print("NO UFC EVENTS FOUND")
            return []

        print(f"UFC EVENTS FOUND: {len(games)}")
        return games

    except Exception as e:
        print("UFC EVENTS ERROR:", e)
        return []


def get_all_ufc_odds():
    """
    Returns all UFC fights with simplified odds summary.
    Useful for displaying a full card.
    """
    try:
        games = get_ufc_events()
        results = []

        for game in games:
            home_fighter = game.get("home_team")
            away_fighter = game.get("away_team")
            home_moneyline = None
            away_moneyline = None

            for book in game.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        if outcome["name"] == home_fighter:
                            home_moneyline = outcome.get("price")
                        elif outcome["name"] == away_fighter:
                            away_moneyline = outcome.get("price")
                if home_moneyline and away_moneyline:
                    break

            results.append({
                "game_id": game.get("id"),
                "home_fighter": home_fighter,
                "away_fighter": away_fighter,
                "commence_time": game.get("commence_time"),
                "home_moneyline": home_moneyline,
                "away_moneyline": away_moneyline,
                "source": "TheOddsAPI"
            })

        return results

    except Exception as e:
        print("GET ALL UFC ODDS ERROR:", e)
        return []


def build_ufc_odds(game_id, home_fighter, away_fighter, event_date=None):
    """
    Fetches moneyline odds for a specific UFC/MMA matchup.
    Returns None if fighters not found.
    """
    try:
        games = get_ufc_events()

        if not games:
            return None

        home_clean = home_fighter.lower()
        away_clean = away_fighter.lower()

        for game in games:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()

            if not (
                home_clean in home or home_clean in away
                or away_clean in home or away_clean in away
            ):
                continue

            result = {
                "game_id": game.get("id"),
                "home_fighter": game.get("home_team"),
                "away_fighter": game.get("away_team"),
                "commence_time": game.get("commence_time"),
                "home_moneyline": None,
                "away_moneyline": None,
                "bookmakers": [],
                "source": "TheOddsAPI"
            }

            for book in game.get("bookmakers", []):
                book_data = {"name": book.get("title")}

                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue

                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price")
                        if outcome["name"] == game.get("home_team"):
                            result["home_moneyline"] = price
                            book_data["home_moneyline"] = price
                        elif outcome["name"] == game.get("away_team"):
                            result["away_moneyline"] = price
                            book_data["away_moneyline"] = price

                result["bookmakers"].append(book_data)

            return result

        print(f"NO UFC MATCH FOUND: {home_fighter} vs {away_fighter}")
        return None

    except Exception as e:
        print("UFC ODDS ERROR:", e)
        return None