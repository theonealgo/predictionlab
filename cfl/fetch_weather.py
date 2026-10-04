#!/usr/bin/env python3
"""
Fetch Weather Data for Outdoor Sports
Gets weather forecasts for NFL/NCAAF games to inform betting decisions
"""

import sqlite3
import requests
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

# Free weather API (OpenWeatherMap - requires API key)
# For testing, we'll use a simple structure
WEATHER_API_KEY = "your_api_key_here"  # User needs to add their own key

# Stadium locations for NFL teams
NFL_STADIUMS = {
    'Buffalo Bills': {'lat': 42.7738, 'lon': -78.7870, 'outdoor': True},
    'Miami Dolphins': {'lat': 25.9580, 'lon': -80.2389, 'outdoor': False},  # Retractable
    'New England Patriots': {'lat': 42.0909, 'lon': -71.2643, 'outdoor': True},
    'New York Jets': {'lat': 40.8135, 'lon': -74.0745, 'outdoor': False},  # Indoor
    'Baltimore Ravens': {'lat': 39.2780, 'lon': -76.6227, 'outdoor': True},
    'Cincinnati Bengals': {'lat': 39.0954, 'lon': -84.5160, 'outdoor': True},
    'Cleveland Browns': {'lat': 41.5061, 'lon': -81.6995, 'outdoor': True},
    'Pittsburgh Steelers': {'lat': 40.4468, 'lon': -80.0158, 'outdoor': True},
    'Houston Texans': {'lat': 29.6847, 'lon': -95.4107, 'outdoor': False},  # Retractable
    'Indianapolis Colts': {'lat': 39.7601, 'lon': -86.1639, 'outdoor': False},  # Dome
    'Jacksonville Jaguars': {'lat': 30.3239, 'lon': -81.6373, 'outdoor': True},
    'Tennessee Titans': {'lat': 36.1665, 'lon': -86.7713, 'outdoor': True},
    'Denver Broncos': {'lat': 39.7439, 'lon': -105.0201, 'outdoor': True},
    'Kansas City Chiefs': {'lat': 39.0489, 'lon': -94.4839, 'outdoor': True},
    'Las Vegas Raiders': {'lat': 36.0908, 'lon': -115.1833, 'outdoor': False},  # Dome
    'Los Angeles Chargers': {'lat': 33.9535, 'lon': -118.3390, 'outdoor': True},
    'Dallas Cowboys': {'lat': 32.7473, 'lon': -97.0945, 'outdoor': False},  # Retractable
    'New York Giants': {'lat': 40.8135, 'lon': -74.0745, 'outdoor': False},  # Indoor
    'Philadelphia Eagles': {'lat': 39.9008, 'lon': -75.1675, 'outdoor': True},
    'Washington Commanders': {'lat': 38.9076, 'lon': -76.8645, 'outdoor': True},
    'Chicago Bears': {'lat': 41.8623, 'lon': -87.6167, 'outdoor': True},
    'Detroit Lions': {'lat': 42.3400, 'lon': -83.0456, 'outdoor': False},  # Dome
    'Green Bay Packers': {'lat': 44.5013, 'lon': -88.0622, 'outdoor': True},
    'Minnesota Vikings': {'lat': 44.9738, 'lon': -93.2577, 'outdoor': False},  # Dome
    'Atlanta Falcons': {'lat': 33.7554, 'lon': -84.4008, 'outdoor': False},  # Retractable
    'Carolina Panthers': {'lat': 35.2258, 'lon': -80.8528, 'outdoor': True},
    'New Orleans Saints': {'lat': 29.9511, 'lon': -90.0812, 'outdoor': False},  # Dome
    'Tampa Bay Buccaneers': {'lat': 27.9759, 'lon': -82.5033, 'outdoor': True},
    'Arizona Cardinals': {'lat': 33.5276, 'lon': -112.2626, 'outdoor': False},  # Retractable
    'Los Angeles Rams': {'lat': 33.9535, 'lon': -118.3390, 'outdoor': True},
    'San Francisco 49ers': {'lat': 37.4032, 'lon': -121.9698, 'outdoor': True},
    'Seattle Seahawks': {'lat': 47.5952, 'lon': -122.3316, 'outdoor': True}
}


def create_weather_table():
    """Create weather table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL,
            game_id TEXT NOT NULL,
            game_date TEXT NOT NULL,
            team_name TEXT NOT NULL,
            is_outdoor BOOLEAN,
            temperature REAL,
            feels_like REAL,
            conditions TEXT,
            wind_speed REAL,
            wind_direction TEXT,
            precipitation_chance REAL,
            humidity REAL,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(game_id, team_name)
        )
    """)
    
    conn.commit()
    conn.close()


def fetch_weather_for_game(game_date, lat, lon):
    """Fetch weather forecast for a specific location and date"""
    if WEATHER_API_KEY == "your_api_key_here":
        # Return dummy data if no API key
        return {
            'temperature': 65,
            'feels_like': 62,
            'conditions': 'Clear',
            'wind_speed': 8,
            'wind_direction': 'NW',
            'precipitation_chance': 0,
            'humidity': 50
        }
    
    try:
        url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=imperial"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Find forecast closest to game time
        # This is simplified - real implementation would match exact game time
        forecast = data['list'][0] if data['list'] else {}
        
        return {
            'temperature': forecast.get('main', {}).get('temp', 0),
            'feels_like': forecast.get('main', {}).get('feels_like', 0),
            'conditions': forecast.get('weather', [{}])[0].get('main', ''),
            'wind_speed': forecast.get('wind', {}).get('speed', 0),
            'wind_direction': 'N',  # Simplified
            'precipitation_chance': forecast.get('pop', 0) * 100,
            'humidity': forecast.get('main', {}).get('humidity', 0)
        }
    
    except Exception as e:
        return None


def fetch_weather_for_upcoming_games(sport):
    """Fetch weather for upcoming outdoor games"""
    print(f"\n{Fore.CYAN}Fetching {sport} Weather Data{Style.RESET_ALL}")
    
    if sport not in ['NFL', 'NCAAF']:
        print(f"  {Fore.YELLOW}Weather not applicable for indoor sport{Style.RESET_ALL}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get upcoming games (next 7 days)
    today = datetime.now().strftime('%Y-%m-%d')
    week_later = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    
    cursor.execute("""
        SELECT game_id, game_date, home_team_id
        FROM games
        WHERE sport = ?
        AND game_date >= ?
        AND game_date <= ?
        AND status != 'final'
    """, (sport, today, week_later))
    
    games = cursor.fetchall()
    weather_count = 0
    
    for game_id, game_date, home_team in games:
        # Check if outdoor stadium
        if sport == 'NFL' and home_team in NFL_STADIUMS:
            stadium = NFL_STADIUMS[home_team]
            
            if not stadium['outdoor']:
                print(f"  {home_team}: Indoor stadium, skipping")
                continue
            
            weather = fetch_weather_for_game(game_date, stadium['lat'], stadium['lon'])
            
            if weather:
                cursor.execute("""
                    INSERT OR REPLACE INTO weather
                    (sport, game_id, game_date, team_name, is_outdoor, 
                     temperature, feels_like, conditions, wind_speed, wind_direction,
                     precipitation_chance, humidity, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (sport, game_id, game_date, home_team, True,
                      weather['temperature'], weather['feels_like'], weather['conditions'],
                      weather['wind_speed'], weather['wind_direction'],
                      weather['precipitation_chance'], weather['humidity']))
                
                weather_count += 1
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated weather for {weather_count} outdoor games{Style.RESET_ALL}")


def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Weather Data Fetcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    create_weather_table()
    
    for sport in ['NFL', 'NCAAF']:
        fetch_weather_for_upcoming_games(sport)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Weather data updated for outdoor sports")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
