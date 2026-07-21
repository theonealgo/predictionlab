"""
Golf sportsbook odds adapter.
Source: The Odds API

Currently active golf keys:
  - golf_masters_tournament_winner  (active: True)
  - golf_the_open_championship_winner (active: True)

Note: Golf odds from The Odds API are outright winner markets,
not head-to-head matchup odds. This adapter returns tournament
winner odds for all available golfers.
"""

import os
import requests

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "18cfd484126cfef3f271472d619e2319")
BASE_URL = "https://api.the-odds-api.com/v4"

# Active golf tournament keys
GOLF_KEYS = [
    "golf_masters_tournament_winner",
    "golf_the_open_championship_winner",
    "golf_pga_championship_winner",
    "golf_us_open_winner",
]


def get_active_golf_key():
    """
    Returns the first active golf tournament key with live odds.
    Returns None if no tournaments are currently active.
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

        active_golf = [
            s.get("key") for s in sports
            if "golf" in s.get("key", "").lower()
            and s.get("active") is True
        ]

        if not active_golf:
            print("NO ACTIVE GOLF TOURNAMENTS RIGHT NOW")
            return None

        print(f"ACTIVE GOLF TOURNAMENTS: {active_golf}")

        # Check which ones actually have odds
        for key in active_golf:
            try:
                r = requests.get(
                    f"{BASE_URL}/sports/{key}/odds",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "us",
                        "markets": "outrights",
                        "oddsFormat": "american",
                    },
                    timeout=10
                )

                if r.status_code != 200:
                    continue

                data = r.json()
                if data:
                    print(f"USING GOLF KEY: {key}")
                    return key

            except Exception:
                continue

        print("NO ACTIVE GOLF ODDS FOUND")
        return None

    except Exception as e:
        print("GOLF KEY LOOKUP ERROR:", e)
        return None


def get_all_golf_odds():
    """
    Returns all golfers with their tournament winner odds.
    Golf uses outright winner markets, not head-to-head.
    """
    try:
        sport_key = get_active_golf_key()

        if not sport_key:
            return []

        r = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "outrights",
                "oddsFormat": "american",
            },
            timeout=10
        )

        if r.status_code != 200:
            print("GOLF ODDS API ERROR:", r.status_code, r.text[:200])
            return []

        data = r.json()

        if not data:
            print("NO GOLF ODDS DATA RETURNED")
            return []

        results = []

        # Golf outrights: each "game" is the tournament
        # bookmakers > markets > outcomes = each golfer
        for tournament in data:
            tournament_name = tournament.get("sport_title", sport_key)

            for book in tournament.get("bookmakers", []):
                book_name = book.get("title")

                for market in book.get("markets", []):
                    if market.get("key") != "outrights":
                        continue

                    for outcome in market.get("outcomes", []):
                        results.append({
                            "tournament": tournament_name,
                            "golfer": outcome.get("name"),
                            "odds": outcome.get("price"),
                            "bookmaker": book_name,
                            "source": "TheOddsAPI"
                        })

        print(f"GOLF ODDS FOUND: {len(results)} entries")
        return results

    except Exception as e:
        print("GET ALL GOLF ODDS ERROR:", e)
        return []


def build_golf_odds(golfer_name):
    """
    Returns odds for a specific golfer across all bookmakers.
    """
    try:
        all_odds = get_all_golf_odds()

        if not all_odds:
            return None

        golfer_clean = golfer_name.lower()

        golfer_odds = [
            entry for entry in all_odds
            if golfer_clean in entry.get("golfer", "").lower()
        ]

        if not golfer_odds:
            print(f"NO ODDS FOUND FOR GOLFER: {golfer_name}")
            return None

        # Return best odds (highest positive or least negative)
        best = max(golfer_odds, key=lambda x: x.get("odds", -99999))

        return {
            "golfer": golfer_name,
            "best_odds": best.get("odds"),
            "best_bookmaker": best.get("bookmaker"),
            "tournament": best.get("tournament"),
            "all_books": golfer_odds,
            "source": "TheOddsAPI"
        }

    except Exception as e:
        print("BUILD GOLF ODDS ERROR:", e)
        return None