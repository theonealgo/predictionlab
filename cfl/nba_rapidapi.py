#!/usr/bin/env python3
"""
NBA RapidAPI Integration
Fetches NBA schedule and results from nba-api-free-data.p.rapidapi.com
"""

import requests
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class NBAScheduleAPI:
    """Wrapper for NBA RapidAPI schedule/results endpoint"""
    
    def __init__(self, api_key: str = "5eee166535msh22626074ec6c874p14f687jsn40784b838787"):
        self.base_url = "https://nba-api-free-data.p.rapidapi.com"
        self.headers = {
            'x-rapidapi-key': api_key,
            'x-rapidapi-host': "nba-api-free-data.p.rapidapi.com"
        }
    
    def get_scoreboard_by_date(self, date: str) -> Optional[Dict]:
        """
        Get NBA scoreboard for a specific date
        
        Args:
            date: Date string in format YYYYMMDD (e.g., "20250120")
        
        Returns:
            Dictionary with scoreboard data or None if error
        """
        try:
            url = f"{self.base_url}/nba-scoreboard-by-date"
            params = {"date": date}
            
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching NBA scoreboard for {date}: {e}")
            return None
    
    def get_games_for_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """
        Get all NBA games for a date range
        
        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
        
        Returns:
            List of game dictionaries
        """
        all_games = []
        current_date = start_date
        
        while current_date <= end_date:
            date_str = current_date.strftime("%Y%m%d")
            query_date = current_date.strftime("%Y-%m-%d")
            
            scoreboard = self.get_scoreboard_by_date(date_str)
            if scoreboard and scoreboard.get('status') == 'success':
                events = scoreboard.get('response', {}).get('Events', [])
                
                for event in events:
                    game = self._parse_event(event, query_date)
                    if game:
                        all_games.append(game)
            
            current_date += timedelta(days=1)
            
            # Small delay to avoid rate limits (0.2 seconds between requests)
            time.sleep(0.2)
        
        return all_games
    
    def _parse_event(self, event: Dict, query_date: str = None) -> Optional[Dict]:
        """
        Parse an event from the API response into a standardized game dictionary
        
        Args:
            event: Event dictionary from API
            query_date: The date we queried for (YYYY-MM-DD format), overrides UTC timestamp parsing
        
        Returns:
            Standardized game dictionary or None if parsing fails
        """
        try:
            # Handle both possible response formats
            if 'competitions' in event:
                competitions = event.get('competitions', {})
                competitors = competitions.get('competitors', [])
            else:
                return None
            
            if len(competitors) != 2:
                return None
            
            # Determine home/away teams
            home_team = None
            away_team = None
            
            for competitor in competitors:
                team_info = competitor.get('team', {})
                team_name = team_info.get('displayName', '')
                score = competitor.get('score')
                
                if competitor.get('homeAway') == 'home':
                    home_team = team_name
                    home_score = int(score) if score is not None else None
                else:
                    away_team = team_name
                    away_score = int(score) if score is not None else None
            
            # Get game status
            status = event.get('status', {})
            status_type = status.get('type', {}).get('name', 'scheduled')
            
            # Use query_date if provided (correct approach for scheduled games)
            # The API returns games by local calendar date, not UTC date
            if query_date:
                db_date = query_date
            else:
                # Fallback: parse UTC timestamp (less accurate for display)
                date_str = event.get('date', '')
                game_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                db_date = game_date.strftime('%Y-%m-%d')
            
            return {
                'game_id': f"NBA_RAPID_{event.get('id')}",
                'game_date': db_date,
                'home_team_id': home_team,
                'away_team_id': away_team,
                'home_score': home_score,
                'away_score': away_score,
                'sport': 'NBA',
                'league': 'NBA',
                'season': 2025,
                'status': 'final' if 'final' in status_type.lower() else 'scheduled',
                'raw_status': status_type
            }
        
        except Exception as e:
            logger.error(f"Error parsing event: {e}")
            return None
    
    def get_recent_and_upcoming_games(self, days_back: int = 7, days_forward: int = 7) -> List[Dict]:
        """
        Get recent and upcoming NBA games
        
        Args:
            days_back: Number of days in the past to fetch
            days_forward: Number of days in the future to fetch
        
        Returns:
            List of game dictionaries
        """
        today = datetime.now()
        start_date = today - timedelta(days=days_back)
        end_date = today + timedelta(days=days_forward)
        
        return self.get_games_for_date_range(start_date, end_date)


def normalize_team_name(name: str) -> str:
    """Normalize team names to match database format"""
    # Handle LA Clippers -> Los Angeles Clippers
    if name == 'LA Clippers':
        return 'Los Angeles Clippers'
    return name


if __name__ == '__main__':
    # Test the API
    api = NBAScheduleAPI()
    
    print("Testing NBA RapidAPI...")
    print("=" * 60)
    
    # Test single date
    print("\nFetching games for January 20, 2025...")
    games = api.get_scoreboard_by_date("20250120")
    
    if games and games.get('status') == 'success':
        events = games.get('response', {}).get('Events', [])
        print(f"Found {len(events)} games")
        
        for event in events[:3]:  # Show first 3
            game = api._parse_event(event)
            if game:
                print(f"  {game['away_team_id']} @ {game['home_team_id']}")
                print(f"    Score: {game['away_score']} - {game['home_score']}")
                print(f"    Status: {game['status']}")
    
    # Test date range
    print("\n" + "=" * 60)
    print("Fetching games for last 3 days + next 3 days...")
    recent_games = api.get_recent_and_upcoming_games(days_back=3, days_forward=3)
    print(f"Found {len(recent_games)} total games")
    
    print("\n✓ API test complete!")
