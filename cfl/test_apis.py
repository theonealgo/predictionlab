#!/usr/bin/env python3
"""Test different APIs to fetch NBA game results"""
import requests
from datetime import datetime, timedelta

# API Keys
ODDS_API_KEY = "fa5c07106b4e80c3f7cd7e418fe3a5cc"
SPORTSGAMEODDS_API_KEY = "62845a06a5eae9f4e616fb175e83fce3"
SPORTSDATA_API_KEY = "33fcde62021645849486b8bdbff4eb29"
RUNDOWN_API_KEY = "dedd47f5e1msh0048bd3771f326cp143f82jsn8612be64ffaa"
THEODDS_API_KEY = "18cfd484126cfef3f271472d619e2319"
BALLDONTLIE_API_KEY = "541f8875-e848-49ec-8334-c43f545841a7"
CFBD_API_KEY = "SH+fJUcVIMlSjIJwbqVuSTYU5vN/33MarjANm0toIz9gRmEVObWX6zM2wzU63pwh"

def test_odds_api():
    """Test The Odds API (the-odds-api.com)"""
    print("\n=== Testing The Odds API ===")
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/scores/"
    params = {
        "apiKey": ODDS_API_KEY,
        "daysFrom": 3
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ SUCCESS - Found {len(data)} games")
            if len(data) > 0:
                print(f"Sample game: {data[0]}")
            return True
        else:
            print(f"❌ FAILED - {response.text}")
            return False
    except Exception as e:
        print(f"❌ ERROR - {str(e)}")
        return False

def test_balldontlie():
    """Test Balldontlie.io API"""
    print("\n=== Testing Balldontlie.io ===")
    # Test with games from yesterday and today
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    url = f"https://api.balldontlie.io/v1/games"
    headers = {"Authorization": BALLDONTLIE_API_KEY}
    params = {
        "start_date": yesterday,
        "end_date": "2025-11-04"
    }
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            games = data.get('data', [])
            print(f"✅ SUCCESS - Found {len(games)} games")
            if len(games) > 0:
                print(f"Sample game: {games[0]}")
            return True
        else:
            print(f"❌ FAILED - {response.text}")
            return False
    except Exception as e:
        print(f"❌ ERROR - {str(e)}")
        return False

def test_sportsdata_io():
    """Test SportsData.io API"""
    print("\n=== Testing SportsData.io ===")
    # Try to get scores for a specific date
    url = f"https://api.sportsdata.io/v3/nba/scores/json/GamesByDate/2025-11-04"
    headers = {"Ocp-Apim-Subscription-Key": SPORTSDATA_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ SUCCESS - Found {len(data)} games")
            if len(data) > 0:
                print(f"Sample game: {data[0]}")
            return True
        else:
            print(f"❌ FAILED - {response.text}")
            return False
    except Exception as e:
        print(f"❌ ERROR - {str(e)}")
        return False

def test_rundown_rapidapi():
    """Test Rundown API via RapidAPI"""
    print("\n=== Testing Rundown API (RapidAPI) ===")
    url = "https://therundown-therundown-v1.p.rapidapi.com/sports/2/events"
    headers = {
        "X-RapidAPI-Key": RUNDOWN_API_KEY,
        "X-RapidAPI-Host": "therundown-therundown-v1.p.rapidapi.com"
    }
    params = {"date": "2025-11-04"}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            events = data.get('events', [])
            print(f"✅ SUCCESS - Found {len(events)} games")
            if len(events) > 0:
                print(f"Sample game: {events[0]}")
            return True
        else:
            print(f"❌ FAILED - {response.text}")
            return False
    except Exception as e:
        print(f"❌ ERROR - {str(e)}")
        return False

def test_nba_api():
    """Test nba_api Python library"""
    print("\n=== Testing nba_api (stats.nba.com) ===")
    try:
        from nba_api.stats.endpoints import scoreboardv2
        from datetime import date
        
        # Get scoreboard for November 4, 2025
        scoreboard = scoreboardv2.ScoreboardV2(game_date="2025-11-04")
        games = scoreboard.get_data_frames()[0]
        
        print(f"✅ SUCCESS - Found {len(games)} games")
        if len(games) > 0:
            print(f"Sample game:\n{games.iloc[0]}")
        return True
    except Exception as e:
        print(f"❌ ERROR - {str(e)}")
        return False

if __name__ == "__main__":
    print("Testing APIs for NBA game results (up to November 4, 2025)\n")
    
    results = {
        "The Odds API": test_odds_api(),
        "Balldontlie.io": test_balldontlie(),
        "SportsData.io": test_sportsdata_io(),
        "Rundown API": test_rundown_rapidapi(),
        "nba_api": test_nba_api()
    }
    
    print("\n" + "="*50)
    print("SUMMARY:")
    print("="*50)
    for api_name, success in results.items():
        status = "✅ WORKS" if success else "❌ FAILED"
        print(f"{api_name}: {status}")
