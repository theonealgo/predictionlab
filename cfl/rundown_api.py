import http.client
import json
import ssl
import logging
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class RundownAPI:
    """Client for The Rundown API via RapidAPI to fetch betting odds"""
    
    BASE_URL = "therundown-therundown-v1.p.rapidapi.com"
    HEADERS = {
        'x-rapidapi-key': "5eee166535msh22626074ec6c874p14f687jsn40784b838787",
        'x-rapidapi-host': "therundown-therundown-v1.p.rapidapi.com"
    }
    
    SPORT_IDS = {
        'NCAAF': 1,
        'NFL': 2,
        'MLB': 3,
        'NBA': 4,
        'NCAAB': 5,
        'NHL': 6,
        'WNBA': 8
    }
    
    def __init__(self):
        # Create unverified context to avoid SSL errors on some environments
        self.context = ssl._create_unverified_context()

    def get_sport_id(self, sport_name):
        return self.SPORT_IDS.get(sport_name)

    def get_odds(self, sport_name, date_str):
        """
        Fetch odds for a given sport and date.
        date_str should be YYYY-MM-DD.
        """
        sport_id = self.get_sport_id(sport_name)
        if not sport_id:
            logger.error(f"Unknown sport for Rundown API: {sport_name}")
            return []

        conn = http.client.HTTPSConnection(self.BASE_URL, context=self.context)
        
        try:
            # Request odds for the specific date
            # Endpoint: /sports/{sport_id}/events/{date}
            # Note: The API might require ISO format or specific timezone handling, 
            # but YYYY-MM-DD usually works for the 'date' parameter in path or query.
            # Based on testing, /sports/{id}/events/{date} works.
            
            endpoint = f"/sports/{sport_id}/events/{date_str}"
            logger.info(f"Fetching odds from Rundown: {endpoint}")
            
            conn.request("GET", endpoint, headers=self.HEADERS)
            res = conn.getresponse()
            data = res.read()
            
            if res.status != 200:
                logger.error(f"Rundown API Error {res.status}: {data}")
                return []
                
            response_json = json.loads(data.decode("utf-8"))
            events = response_json.get('events', [])
            
            return self._parse_events(events)
            
        except Exception as e:
            logger.error(f"Error fetching odds from Rundown: {e}")
            return []
        finally:
            conn.close()

    def _parse_events(self, events):
        """Parse raw events into normalized format"""
        parsed_games = []
        
        for event in events:
            try:
                game_id = event.get('event_id')
                game_date = event.get('event_date')
                
                # Teams
                teams = event.get('teams_normalized', [])
                if len(teams) < 2:
                    continue
                    
                home_team = next((t for t in teams if t.get('is_home')), None)
                away_team = next((t for t in teams if not t.get('is_home')), None)
                
                if not home_team or not away_team:
                    continue
                    
                home_name = home_team.get('name') + " " + home_team.get('mascot')
                away_name = away_team.get('name') + " " + away_team.get('mascot')
                
                # Extract Lines
                lines = event.get('lines', {})
                spread = None
                total = None
                
                # Prioritize Pinnacle (3) then others
                # Filter out lines with 0.0001 (placeholders)
                valid_lines = []
                for affiliate_id, line in lines.items():
                    # Check for valid spread/total
                    ps = line.get('point_spread_home')
                    tot = line.get('total_over')
                    
                    if ps is not None and abs(ps) > 0.1 and tot is not None and tot > 1:
                         valid_lines.append((affiliate_id, line))
                
                # Sort to find preferred affiliate if available, or just pick first valid
                # Affiliate 3 is Pinnacle
                chosen_line = None
                for aff_id, line in valid_lines:
                    if aff_id == '3':
                        chosen_line = line
                        break
                
                if not chosen_line and valid_lines:
                    chosen_line = valid_lines[0][1]
                
                if chosen_line:
                    # Rundown gives spread for home team (e.g. -5 means Home -5)
                    # We usually want the spread value relative to home.
                    spread = chosen_line.get('point_spread_home')
                    total = chosen_line.get('total_over') # or total_under, usually same
                    
                parsed_games.append({
                    'game_date': game_date,
                    'home_team': home_name,
                    'away_team': away_name,
                    'vegas_spread': spread,
                    'vegas_total': total
                })
                
            except Exception as e:
                logger.error(f"Error parsing event: {e}")
                continue
                
        return parsed_games

if __name__ == "__main__":
    # Test
    api = RundownAPI()
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"Fetching NBA odds for {today}...")
    odds = api.get_odds('NBA', today)
    print(json.dumps(odds, indent=2))
