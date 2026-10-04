#!/usr/bin/env python3
"""
NHL API Integration
Fetches live schedule and scores from NHL's official API
"""

import requests
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class NHLAPI:
    """Wrapper for ESPN NHL API"""
    
    def __init__(self):
        self.base_url = 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl'
    
    def get_schedule(self, date=None):
        """
        Get NHL schedule for a specific date
        
        Args:
            date: YYYY-MM-DD string, defaults to today
        
        Returns:
            List of games with teams, time, scores
        """
        if date is None:
            date = datetime.now().strftime('%Y%m%d')
        else:
            # Convert YYYY-MM-DD to YYYYMMDD
            date = date.replace('-', '')
        
        try:
            url = f'{self.base_url}/scoreboard?dates={date}'
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            games = []
            
            # Parse game data
            for event in data.get('events', []):
                competition = event['competitions'][0]
                
                # Find home and away teams
                home_team = next((t for t in competition['competitors'] if t['homeAway'] == 'home'), None)
                away_team = next((t for t in competition['competitors'] if t['homeAway'] == 'away'), None)
                
                if not home_team or not away_team:
                    continue
                
                # Only read scores for completed games.
                # ESPN returns '0' for scheduled games; treating that as a score
                # causes the template to show them as FINAL 0-0 and suppresses
                # spread/total predictions for upcoming games.
                status_name = event['status']['type']['name']
                is_final = status_name in [
                    'STATUS_FINAL', 'STATUS_FINAL_OT',
                    'STATUS_FINAL_OT2', 'STATUS_FINAL_SO',
                ]
                if is_final:
                    home_score = int(home_team['score']) if home_team.get('score') and str(home_team['score']).isdigit() else None
                    away_score = int(away_team['score']) if away_team.get('score') and str(away_team['score']).isdigit() else None
                else:
                    home_score = None
                    away_score = None

                # Convert UTC to Eastern Time for proper date grouping
                event_date_utc = event['date']
                try:
                    utc_dt = datetime.strptime(event_date_utc, '%Y-%m-%dT%H:%M%SZ')
                    et_dt = utc_dt - timedelta(hours=5)  # UTC to ET
                    game_date_et = et_dt.strftime('%Y-%m-%d')
                except:
                    game_date_et = event_date_utc.split('T')[0]
                
                _status_type = (event.get('status') or {}).get('type') or {}
                game_data = {
                    'game_id': f"NHL_{event['id']}",
                    'game_date': game_date_et,
                    'game_time': event['date'],
                    'status_type': _status_type,
                    'away_team_id': away_team['team']['displayName'],  # Use full name for DB matching
                    'home_team_id': home_team['team']['displayName'],  # Use full name for DB matching
                    'away_team_abbr': away_team['team']['abbreviation'],
                    'home_team_abbr': home_team['team']['abbreviation'],
                    'away_team_name': away_team['team']['displayName'],
                    'home_team_name': home_team['team']['displayName'],
                    'status': event['status']['type']['name'],
                    'away_score': away_score,
                    'home_score': home_score,
                    'venue': competition.get('venue', {}).get('fullName', ''),
                    'season': 2025
                }
                games.append(game_data)
            
            logger.info(f"Fetched {len(games)} NHL games for {date}")
            return games
            
        except Exception as e:
            logger.error(f"Error fetching NHL schedule for {date}: {e}")
            return []
    
    def get_recent_and_upcoming_games(self, days_back=7, days_forward=7):
        """
        Get games from the past N days and next N days
        
        Returns:
            Combined list of recent and upcoming games
        """
        all_games = []
        
        # Get past games
        for i in range(days_back, 0, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            games = self.get_schedule(date)
            all_games.extend(games)
        
        # Get today's games
        today_games = self.get_schedule()
        all_games.extend(today_games)
        
        # Get upcoming games
        for i in range(1, days_forward + 1):
            date = (datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d')
            games = self.get_schedule(date)
            all_games.extend(games)
        
        logger.info(f"Fetched {len(all_games)} total NHL games ({days_back}d back, {days_forward}d forward)")
        return all_games
    
    def get_team_stats(self, team_abbrev):
        """Get current season stats for a team"""
        try:
            url = f'{self.base_url}/club-stats/{team_abbrev}/now'
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching team stats for {team_abbrev}: {e}")
            return None


if __name__ == '__main__':
    # Test the API
    logging.basicConfig(level=logging.INFO)
    
    api = NHLAPI()
    
    print("\n=== Today's NHL Games ===")
    games = api.get_schedule()
    
    for game in games:
        score_str = ""
        if game['home_score'] is not None:
            score_str = f" - {game['away_score']}-{game['home_score']} FINAL"
        
        print(f"{game['away_team_id']} @ {game['home_team_id']}{score_str}")
        print(f"  Game ID: {game['game_id']}")
        print(f"  Status: {game['status']}")
        print()
