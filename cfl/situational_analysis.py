#!/usr/bin/env python3
"""
Situational Analysis - Non-stat factors that impact game outcomes
Includes: rest days, travel, back-to-back, revenge games, injuries, etc.
"""

import sqlite3
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class SituationalAnalyzer:
    """Analyze situational factors that impact game outcomes"""
    
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
    
    def analyze_game(self, sport, home_team, away_team, game_date):
        """
        Comprehensive situational analysis for a game
        
        Returns dict with adjustment factors for model probabilities
        """
        factors = {
            'home_rest_days': self.get_rest_days(sport, home_team, game_date),
            'away_rest_days': self.get_rest_days(sport, away_team, game_date),
            'home_back_to_back': False,
            'away_back_to_back': False,
            'home_travel_distance': 0,
            'away_travel_distance': 0,
            'home_recent_form': self.get_recent_form(sport, home_team, game_date, last_n=5),
            'away_recent_form': self.get_recent_form(sport, away_team, game_date, last_n=5),
            'revenge_game': False,
            'situational_edge': 0.0  # Net adjustment (-0.1 to +0.1)
        }
        
        # Back-to-back detection
        if factors['home_rest_days'] == 0:
            factors['home_back_to_back'] = True
        if factors['away_rest_days'] == 0:
            factors['away_back_to_back'] = True
        
        # Calculate situational edge
        edge = 0.0
        
        # Rest advantage (NHL/NBA specific - critical factor)
        if sport in ['NHL', 'NBA']:
            rest_diff = factors['home_rest_days'] - factors['away_rest_days']
            
            # Back-to-back penalty is severe
            if factors['away_back_to_back'] and not factors['home_back_to_back']:
                edge += 0.08  # Home team has major edge
            elif factors['home_back_to_back'] and not factors['away_back_to_back']:
                edge -= 0.08  # Away team has major edge
            
            # General rest advantage
            if rest_diff >= 2:
                edge += 0.04  # Home team rested
            elif rest_diff <= -2:
                edge -= 0.04  # Away team rested
        
        # Recent form (momentum)
        form_diff = factors['home_recent_form'] - factors['away_recent_form']
        if abs(form_diff) >= 0.3:  # Significant form difference
            edge += form_diff * 0.05  # Hot team gets small boost
        
        # NHL-specific: Home ice is more valuable
        if sport == 'NHL':
            edge += 0.02  # Base home ice advantage
        
        # NFL-specific: Home field more valuable
        if sport == 'NFL':
            edge += 0.025
        
        # Cap the total edge
        factors['situational_edge'] = max(-0.10, min(0.10, edge))
        
        return factors
    
    def get_rest_days(self, sport, team, game_date):
        """Get number of rest days since last game"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            
            # Parse game_date if string
            if isinstance(game_date, str):
                game_date = datetime.strptime(game_date.split()[0], '%Y-%m-%d')
            
            # Find previous game
            prev_game = conn.execute('''
                SELECT game_date
                FROM games
                WHERE sport = ?
                  AND (home_team_id = ? OR away_team_id = ?)
                  AND date(game_date) < date(?)
                ORDER BY date(game_date) DESC
                LIMIT 1
            ''', (sport, team, team, game_date.strftime('%Y-%m-%d'))).fetchone()
            
            conn.close()
            
            if prev_game:
                prev_date = datetime.strptime(prev_game['game_date'].split()[0], '%Y-%m-%d')
                rest_days = (game_date - prev_date).days - 1
                return max(0, rest_days)
            else:
                return 7  # Season start, assume well-rested
                
        except Exception as e:
            logger.error(f"Error getting rest days: {e}")
            return 2  # Default assumption
    
    def get_recent_form(self, sport, team, game_date, last_n=5):
        """
        Get recent form as win percentage in last N games
        
        Returns float 0.0 to 1.0
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            
            # Parse game_date if string
            if isinstance(game_date, str):
                game_date = datetime.strptime(game_date.split()[0], '%Y-%m-%d')
            
            # Get last N games before this date
            games = conn.execute('''
                SELECT home_team_id, away_team_id, home_score, away_score
                FROM games
                WHERE sport = ?
                  AND (home_team_id = ? OR away_team_id = ?)
                  AND date(game_date) < date(?)
                  AND home_score IS NOT NULL
                ORDER BY date(game_date) DESC
                LIMIT ?
            ''', (sport, team, team, game_date.strftime('%Y-%m-%d'), last_n)).fetchall()
            
            conn.close()
            
            if not games:
                return 0.5  # Neutral
            
            wins = 0
            for game in games:
                if game['home_team_id'] == team:
                    if game['home_score'] > game['away_score']:
                        wins += 1
                else:  # away team
                    if game['away_score'] > game['home_score']:
                        wins += 1
            
            return wins / len(games)
            
        except Exception as e:
            logger.error(f"Error getting recent form: {e}")
            return 0.5
    
    def get_travel_distance(self, team_a, team_b):
        """
        Estimate travel distance between team cities (miles)
        Simplified version - in production, use actual coordinates
        """
        # NHL/NBA/NFL city coordinates (lat, lon)
        CITY_COORDS = {
            # NHL teams (sample)
            'TOR': (43.64, -79.38), 'MTL': (45.50, -73.57), 'BOS': (42.36, -71.06),
            'NYR': (40.71, -74.01), 'NYI': (40.71, -74.01), 'NJD': (40.73, -74.17),
            'PHI': (39.95, -75.17), 'PIT': (40.44, -79.99), 'WSH': (38.91, -77.02),
            'CAR': (35.78, -78.64), 'FLA': (26.16, -80.32), 'TBL': (27.95, -82.45),
            'CBJ': (39.96, -83.00), 'DET': (42.33, -83.05), 'BUF': (42.89, -78.88),
            'CHI': (41.88, -87.62), 'MIN': (44.98, -93.26), 'WPG': (49.89, -97.14),
            'STL': (38.63, -90.20), 'NSH': (36.16, -86.78), 'DAL': (32.79, -96.81),
            'COL': (39.74, -104.99), 'ARI': (33.45, -112.07), 'VGK': (36.17, -115.14),
            'LAK': (34.05, -118.24), 'ANA': (33.81, -117.88), 'SJS': (37.33, -121.90),
            'SEA': (47.61, -122.33), 'VAN': (49.28, -123.11), 'CGY': (51.05, -114.07),
            'EDM': (53.55, -113.47),
        }
        
        if team_a not in CITY_COORDS or team_b not in CITY_COORDS:
            return 500  # Default moderate distance
        
        lat1, lon1 = CITY_COORDS[team_a]
        lat2, lon2 = CITY_COORDS[team_b]
        
        # Haversine formula (simplified)
        import math
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        distance = 3959 * c  # Radius of Earth in miles
        
        return round(distance)


if __name__ == '__main__':
    # Test situational analysis
    logging.basicConfig(level=logging.INFO)
    
    analyzer = SituationalAnalyzer()
    
    # Test NHL game
    factors = analyzer.analyze_game('NHL', 'TOR', 'BOS', '2025-11-07')
    
    print("\n=== Situational Analysis ===")
    print(f"Home (TOR) rest days: {factors['home_rest_days']}")
    print(f"Away (BOS) rest days: {factors['away_rest_days']}")
    print(f"Home back-to-back: {factors['home_back_to_back']}")
    print(f"Away back-to-back: {factors['away_back_to_back']}")
    print(f"Home recent form: {factors['home_recent_form']:.1%}")
    print(f"Away recent form: {factors['away_recent_form']:.1%}")
    print(f"Situational edge: {factors['situational_edge']:+.3f}")
