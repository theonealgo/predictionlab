#!/usr/bin/env python3
"""
NBA Hybrid API Integration
Fetches NBA schedule from SportsData.io and scores from ESPN API
"""

import requests
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class NBASportsDataAPI:
    """Wrapper for SportsData.io NBA API"""
    
    def __init__(self, api_key: str = "33fcde62021645849486b8bdbff4eb29"):
        self.base_url = "https://api.sportsdata.io/v3/nba/scores/json"
        self.api_key = api_key
        self.headers = {
            'Ocp-Apim-Subscription-Key': self.api_key
        }
        
        # Team abbreviation to full name mapping
        self.team_map = {
            'ATL': 'Atlanta Hawks', 'BOS': 'Boston Celtics', 'BKN': 'Brooklyn Nets',
            'CHA': 'Charlotte Hornets', 'CHI': 'Chicago Bulls', 'CLE': 'Cleveland Cavaliers',
            'DAL': 'Dallas Mavericks', 'DEN': 'Denver Nuggets', 'DET': 'Detroit Pistons',
            'GSW': 'Golden State Warriors', 'GS': 'Golden State Warriors', 'HOU': 'Houston Rockets', 'IND': 'Indiana Pacers',
            'LAC': 'Los Angeles Clippers', 'LAL': 'Los Angeles Lakers', 'MEM': 'Memphis Grizzlies',
            'MIA': 'Miami Heat', 'MIL': 'Milwaukee Bucks', 'MIN': 'Minnesota Timberwolves',
            'NOP': 'New Orleans Pelicans', 'NO': 'New Orleans Pelicans', 'NYK': 'New York Knicks', 'NY': 'New York Knicks', 'OKC': 'Oklahoma City Thunder',
            'ORL': 'Orlando Magic', 'PHI': 'Philadelphia 76ers', 'PHO': 'Phoenix Suns', 'PHX': 'Phoenix Suns',
            'POR': 'Portland Trail Blazers', 'SAC': 'Sacramento Kings', 'SAS': 'San Antonio Spurs', 'SA': 'San Antonio Spurs',
            'TOR': 'Toronto Raptors', 'UTA': 'Utah Jazz', 'WAS': 'Washington Wizards'
        }
    
    def get_games_by_date(self, date: str) -> Optional[List[Dict]]:
        """
        Get NBA games for a specific date
        
        Args:
            date: Date string in format YYYY-MM-DD (e.g., "2025-11-06")
        
        Returns:
            List of game dictionaries or None if error
        """
        try:
            url = f"{self.base_url}/GamesByDate/{date}"
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching NBA games for {date}: {e}")
            return None
    
    def get_games_for_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """
        Get all NBA games for a date range
        
        Args:
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
        
        Returns:
            List of standardized game dictionaries
        """
        all_games = []
        current_date = start_date
        
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            
            games = self.get_games_by_date(date_str)
            if games:
                for game in games:
                    parsed_game = self._parse_game(game, date_str)
                    if parsed_game:
                        all_games.append(parsed_game)
            
            current_date += timedelta(days=1)
        
        return all_games
    
    def _parse_game(self, game: Dict, query_date: str) -> Optional[Dict]:
        """
        Parse a game from the API response into a standardized game dictionary
        
        Args:
            game: Game dictionary from API
            query_date: The date we queried for (YYYY-MM-DD format)
        
        Returns:
            Standardized game dictionary or None if parsing fails
        """
        try:
            # Get team names
            away_abbr = game.get('AwayTeam')
            home_abbr = game.get('HomeTeam')
            
            away_team = self.team_map.get(away_abbr, away_abbr)
            home_team = self.team_map.get(home_abbr, home_abbr)
            
            # Get scores (None if not played yet)
            away_score = game.get('AwayTeamScore')
            home_score = game.get('HomeTeamScore')
            
            # Get status
            status = game.get('Status', 'Scheduled')
            is_final = status in ['Final', 'F/OT', 'F/2OT', 'F/3OT']
            
            return {
                'game_id': f"NBA_SD_{game.get('GameID')}",
                'game_date': query_date,
                'home_team_id': home_team,
                'away_team_id': away_team,
                'home_score': int(home_score) if home_score is not None else None,
                'away_score': int(away_score) if away_score is not None else None,
                'sport': 'NBA',
                'league': 'NBA',
                'season': 2025,
                'status': 'final' if is_final else 'scheduled',
                'raw_status': status,
                # Betting odds
                'home_moneyline': game.get('HomeTeamMoneyLine'),
                'away_moneyline': game.get('AwayTeamMoneyLine'),
                'spread': game.get('PointSpread'),
                'total': game.get('OverUnder')
            }
        
        except Exception as e:
            logger.error(f"Error parsing game: {e}")
            return None
    
    def get_espn_scores_for_date(self, date_str: str) -> Dict[str, Dict]:
        """
        Get actual game scores from ESPN API (free, no rate limits)
        
        Args:
            date_str: Date in YYYY-MM-DD format
        
        Returns:
            Dictionary mapping (away_team, home_team) -> {away_score, home_score, status}
        """
        scores = {}
        try:
            # ESPN uses YYYYMMDD format
            espn_date = date_str.replace('-', '')
            url = f'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={espn_date}'
            
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                events = data.get('events', [])
                
                for event in events:
                    competitions = event.get('competitions', [])
                    if not competitions:
                        continue
                    
                    comp = competitions[0]
                    competitors = comp.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    # Determine home/away
                    home_team = None
                    away_team = None
                    home_score = None
                    away_score = None
                    
                    for competitor in competitors:
                        team_name = competitor['team']['displayName']
                        score = competitor.get('score', '0')
                        
                        if competitor.get('homeAway') == 'home':
                            home_team = team_name
                            home_score = int(score) if score else None
                        else:
                            away_team = team_name
                            away_score = int(score) if score else None
                    
                    status_desc = event.get('status', {}).get('type', {}).get('description', 'Scheduled')
                    is_final = 'Final' in status_desc
                    
                    if away_team and home_team:
                        # Normalize Clippers name
                        if away_team == 'LA Clippers':
                            away_team = 'Los Angeles Clippers'
                        if home_team == 'LA Clippers':
                            home_team = 'Los Angeles Clippers'
                        
                        scores[(away_team, home_team)] = {
                            'away_score': away_score,
                            'home_score': home_score,
                            'status': 'final' if is_final else 'scheduled'
                        }
        
        except Exception as e:
            logger.error(f"Error fetching ESPN scores for {date_str}: {e}")
        
        return scores
    
    def get_recent_and_upcoming_games(self, days_back: int = 7, days_forward: int = 7, fetch_scores: bool = True) -> List[Dict]:
        """
        Get recent and upcoming NBA games with scores from ESPN
        
        Args:
            days_back: Number of days in the past to fetch
            days_forward: Number of days in the future to fetch
            fetch_scores: If True, fetches actual scores from ESPN API
        
        Returns:
            List of game dictionaries with accurate scores
        """
        today = datetime.now()
        start_date = today - timedelta(days=days_back)
        end_date = today + timedelta(days=days_forward)
        
        games = self.get_games_for_date_range(start_date, end_date)
        
        if fetch_scores:
            # Fetch scores from ESPN for each date
            dates = set(g['game_date'] for g in games)
            espn_scores_by_date = {}
            
            for date_str in dates:
                espn_scores_by_date[date_str] = self.get_espn_scores_for_date(date_str)
            
            # Update games with ESPN scores
            for game in games:
                date_str = game['game_date']
                key = (game['away_team_id'], game['home_team_id'])
                
                if date_str in espn_scores_by_date and key in espn_scores_by_date[date_str]:
                    espn_data = espn_scores_by_date[date_str][key]
                    game['away_score'] = espn_data['away_score']
                    game['home_score'] = espn_data['home_score']
                    game['status'] = espn_data['status']
        
        return games


if __name__ == '__main__':
    # Test the API
    api = NBASportsDataAPI()
    
    print("Testing SportsData NBA API...")
    print("=" * 60)
    
    # Test single date (today)
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\nFetching games for {today}...")
    games = api.get_games_by_date(today)
    
    if games:
        print(f"Found {len(games)} games")
        for game in games:
            parsed = api._parse_game(game, today)
            if parsed:
                print(f"  {parsed['away_team_id']} @ {parsed['home_team_id']}")
                print(f"    Score: {parsed['away_score']} - {parsed['home_score']}")
                print(f"    Status: {parsed['status']}")
                print(f"    Spread: {parsed['spread']}, O/U: {parsed['total']}")
    
    # Test date range
    print("\n" + "=" * 60)
    print("Fetching games for last 3 days + next 3 days...")
    recent_games = api.get_recent_and_upcoming_games(days_back=3, days_forward=3)
    print(f"Found {len(recent_games)} total games")
    
    from collections import Counter
    dates = Counter(g['game_date'] for g in recent_games)
    print(f'\nGames by date:')
    for date in sorted(dates.keys()):
        print(f'  {date}: {dates[date]} games')
    
    print("\n✓ API test complete!")
