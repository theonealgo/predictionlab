"""
Tennis sportsbook odds adapter.
Source: The Odds API
Dynamically discovers active tennis tournament keys.
Checks both active and inactive tournaments for live odds.
Returns None gracefully when no tournaments have odds available.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"


def get_tennis_sport_key():
    """
    Returns the first tennis sport key with live odds.
    Checks ALL tennis keys (active and inactive) since the API
    sometimes has odds for tournaments marked inactive.
    Returns None if no tournaments have odds right now.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": ODDS_API_KEY, "all": "true"},
            timeout=10
        )

        if r.status_code != 200:
            print("SPORT LIST ERROR:", r.status_code, r.text[:200])
            return None

        sports = r.json()

        # Get ALL tennis keys, both active and inactive, exclude outrights
        tennis_sports = [
            s.get("key") for s in sports
            if "tennis" in s.get("key", "").lower()
            and "winner" not in s.get("key", "").lower()
        ]

        if not tennis_sports:
            print("NO TENNIS SPORTS FOUND IN API")
            return None

        print(f"CHECKING {len(tennis_sports)} TENNIS TOURNAMENTS FOR LIVE ODDS...")

        for key in tennis_sports:
            try:
                r = requests.get(
                    f"{BASE_URL}/sports/{key}/odds",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "us",
                        "markets": "h2h",
                        "oddsFormat": "american",
                    },
                    timeout=10
                )

                if r.status_code != 200:
                    continue

                games = r.json()

                if games:
                    print(f"USING TENNIS SPORT: {key} | EVENTS: {len(games)}")
                    return key

            except Exception:
                continue

        print("NO ACTIVE TENNIS ODDS FOUND — between tournaments right now.")
        return None

    except Exception as e:
        print("TENNIS SPORT LOOKUP ERROR:", e)
        return None


def get_all_tennis_odds():
    """
    Returns all tennis matches with simplified odds summary.
    """
    try:
        sport_key = get_tennis_sport_key()

        if not sport_key:
            return []

        r = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            },
            timeout=10
        )

        if r.status_code != 200:
            print("TENNIS ODDS API ERROR:", r.status_code, r.text[:200])
            return []

        games = r.json()
        results = []

        for game in games:
            home_player = game.get("home_team")
            away_player = game.get("away_team")
            home_moneyline = None
            away_moneyline = None

            for book in game.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        if outcome["name"] == home_player:
                            home_moneyline = outcome.get("price")
                        elif outcome["name"] == away_player:
                            away_moneyline = outcome.get("price")
                if home_moneyline and away_moneyline:
                    break

            results.append({
                "game_id": game.get("id"),
                "home_player": home_player,
                "away_player": away_player,
                "commence_time": game.get("commence_time"),
                "home_moneyline": home_moneyline,
                "away_moneyline": away_moneyline,
                "source": "TheOddsAPI"
            })

        return results

    except Exception as e:
        print("GET ALL TENNIS ODDS ERROR:", e)
        return []


def build_tennis_odds(game_id, home_player, away_player, event_date=None):
    """
    Fetches moneyline odds for a specific tennis matchup.
    Returns None if no active tournament or player not found.
    """
    try:
        sport_key = get_tennis_sport_key()

        if not sport_key:
            return None

        r = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "h2h",
                "oddsFormat": "american",
            },
            timeout=10
        )

        if r.status_code != 200:
            print("ODDS API ERROR:", r.status_code, r.text[:200])
            return None

        games = r.json()
        home_clean = home_player.lower()
        away_clean = away_player.lower()

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
                "home_player": game.get("home_team"),
                "away_player": game.get("away_team"),
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

        print(f"NO MATCH FOUND: {home_player} vs {away_player}")
        return None

    except Exception as e:
        print("TENNIS ODDS ERROR:", e)
        return None