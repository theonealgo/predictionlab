#!/usr/bin/env python3
"""
ATS (Against The Spread) Betting System
=========================================
Tracks team performance against spreads and over/under totals.
Generates picks based on historical ATS records and model predictions.
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Tuple, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ATSSystem:
    """
    Advanced ATS betting system that:
    1. Tracks teams' ATS records (spread coverage)
    2. Tracks teams' over/under records
    3. Generates spread and total predictions from model scores
    4. Filters picks using system teams (proven performers)
    """
    
    # System teams - proven ATS performers (Updated Nov 13, 2025)
    SYSTEM_TEAMS = {
        # Updated: 2025-11-14 with user's exact data
        'NBA': {
            'spread': ['Philadelphia 76ers', 'Chicago Bulls', 'Miami Heat', 'Los Angeles Lakers',
                      'Denver Nuggets', 'New York Knicks', 'Houston Rockets', 'Phoenix Suns',
                      'Detroit Pistons', 'Portland Trail Blazers', 'Utah Jazz'],
            'moneyline': ['Oklahoma City Thunder', 'San Antonio Spurs', 'Denver Nuggets',
                         'Detroit Pistons', 'Los Angeles Lakers', 'Miami Heat',
                         'Milwaukee Bucks', 'Cleveland Cavaliers', 'Minnesota Timberwolves'],
            # Over teams: 63%+ over rate (2025 current data)
            'over': ['Houston Rockets', 'New York Knicks', 'Portland Trail Blazers',
                    'Miami Heat', 'Washington Wizards', 'Los Angeles Lakers',
                    'Golden State Warriors', 'Chicago Bulls'],
            # Under teams: 60%+ under rate - FADE these teams (bet UNDER on their games)
            'under': ['Indiana Pacers', 'Boston Celtics', 'Dallas Mavericks', 'Memphis Grizzlies']
        },
        'NFL': {
            'spread': ['Seattle Seahawks', 'Los Angeles Rams', 'New England Patriots',
                      'Philadelphia Eagles', 'Indianapolis Colts', 'Detroit Lions', 'Carolina Panthers'],
            'moneyline': ['Denver Broncos', 'Indianapolis Colts', 'New England Patriots',
                         'Seattle Seahawks', 'Los Angeles Rams', 'Philadelphia Eagles',
                         'Los Angeles Chargers', 'Buffalo Bills', 'Tampa Bay Buccaneers',
                         'Chicago Bears', 'Detroit Lions'],
            # Over teams: 60%+ over rate (2025 actual data)
            'over': ['Cincinnati Bengals', 'Minnesota Vikings', 'Dallas Cowboys',
                    'San Francisco 49ers', 'Seattle Seahawks', 'Baltimore Ravens',
                    'Tennessee Titans', 'Miami Dolphins', 'Chicago Bears', 'New York Jets',
                    'Indianapolis Colts'],
            # Under teams: 60%+ under rate (2025 actual data)
            'under': ['Kansas City Chiefs', 'Denver Broncos', 'Las Vegas Raiders',
                     'New Orleans Saints', 'Atlanta Falcons', 'Houston Texans',
                     'Green Bay Packers', 'Los Angeles Rams', 'Buffalo Bills']
        },
        'NHL': {
            'spread': ['Pittsburgh Penguins', 'Chicago Blackhawks', 'Boston Bruins',
                      'Seattle Kraken', 'San Jose Sharks', 'Anaheim Ducks', 'Columbus Blue Jackets'],
            'moneyline': ['Colorado Avalanche', 'Anaheim Ducks', 'New Jersey Devils',
                         'Carolina Hurricanes', 'Montreal Canadiens', 'Dallas Stars',
                         'Pittsburgh Penguins', 'Boston Bruins'],
            'over': ['Toronto Maple Leafs', 'Vancouver Canucks', 'Ottawa Senators',
                    'New York Islanders', 'Calgary Flames', 'Montreal Canadiens',
                    'St. Louis Blues', 'Edmonton Oilers'],
            'under': ['Chicago Blackhawks', 'New York Rangers', 'Tampa Bay Lightning',
                     'Nashville Predators', 'Pittsburgh Penguins']
        },
        'NCAAF': {
            'spread': ['Texas Tech', 'Ohio State', 'Mississippi', 'North Texas',
                      'Pittsburgh', 'San Diego State', 'Utah', 'South Florida',
                      'Vanderbilt', 'Hawaii', 'Iowa', 'Boise State', 'Western Michigan',
                      'Toledo', 'Central Michigan'],
            'moneyline': ['Indiana', 'Ohio State', 'Texas A&M', 'Texas Tech', 'Mississippi',
                         'North Texas', 'Oregon', 'Georgia', 'Alabama', 'James Madison',
                         'BYU', 'Georgia Tech', 'Houston', 'Memphis', 'Virginia',
                         'Vanderbilt', 'San Diego State', 'Texas', 'USC', 'Navy',
                         'Utah', 'Tulane', 'Kennesaw State', 'Cincinnati', 'Louisville',
                         'Miami', 'South Florida', 'Michigan', 'UNLV', 'Oklahoma',
                         'Southern Miss', 'Western Kentucky', 'Pittsburgh', 'Notre Dame',
                         'SMU', 'Hawaii', 'UConn', 'Old Dominion', 'Nebraska',
                         'New Mexico', 'Jacksonville State', 'Boise State', 'Tennessee',
                         'Washington', 'Missouri', 'Illinois', 'East Carolina', 'TCU',
                         'Wake Forest', 'Minnesota', 'Arizona State', 'Iowa',
                         'Missouri State', 'Arizona', 'Fresno State', 'Coastal Carolina',
                         'Central Michigan', 'Toledo', 'Western Michigan', 'Troy',
                         'Iowa State', 'California', 'Ohio'],
            'over': [],
            'under': []
        }
    }
    
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
        # Sport-specific home field advantages (from rules)
        self.home_field_adv = {
            'NFL': 2.5,
            'NBA': 3.0,
            'WNBA': 3.0,
            'NCAAF': 3.0,
            'NCAAB': 3.0,
            'NHL': 0.3,
            'MLB': 0.15
        }
    
    def get_team_records(self, sport: str, team_name: str) -> dict:
        """Get team's current records from team_records table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT wins, losses, win_pct, ats_wins, ats_losses, ats_pct, 
                   over_wins, over_losses, under_wins, under_losses
            FROM team_records
            WHERE sport = ? AND team_name = ?
        """, (sport, team_name))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return {
                'wins': 0, 'losses': 0, 'win_pct': 0,
                'ats_wins': 0, 'ats_losses': 0, 'ats_pct': 0,
                'over_wins': 0, 'over_losses': 0, 'under_wins': 0, 'under_losses': 0
            }
        
        return {
            'wins': result[0],
            'losses': result[1],
            'win_pct': result[2],
            'ats_wins': result[3],
            'ats_losses': result[4],
            'ats_pct': result[5],
            'over_wins': result[6],
            'over_losses': result[7],
            'under_wins': result[8],
            'under_losses': result[9]
        }
    
    def normalize_team_name(self, full_name: str, sport: str) -> str:
        """Extract school name from full ESPN name (e.g., 'Ohio State Buckeyes' -> 'Ohio State')"""
        if sport != 'NCAAF':
            return full_name
        
        # Common mascot names to remove
        mascots = ['Aggies', 'Wildcats', 'Bulldogs', 'Tigers', 'Eagles', 'Falcons', 'Cardinals',
                   'Cougars', 'Huskies', 'Panthers', 'Bears', 'Lions', 'Trojans', 'Spartans',
                   'Buckeyes', 'Wolverines', 'Ducks', 'Beavers', 'Sun Devils', 'Bruins', 'Utes',
                   'Buffaloes', 'Golden Gophers', 'Hawkeyes', 'Cornhuskers', 'Badgers', 'Fighting Irish',
                   'Seminoles', 'Gators', 'Volunteers', 'Crimson Tide', 'Razorbacks', 'Gamecocks',
                   'Commodores', 'Rebels', 'Red Raiders', 'Horned Frogs', 'Sooners', 'Cowboys',
                   'Longhorns', 'Mountaineers', 'Cyclones', 'Jayhawks', 'Mean Green', 'Blue Raiders',
                   'Pirates', 'Golden Eagles', 'Owls', 'Bulls', 'Knights', 'Bearcats', 'Cougars',
                   'Midshipmen', 'Black Knights', 'Minutemen', 'Rainbow Warriors', 'Aztecs',
                   'Broncos', 'Hokies', 'Yellow Jackets', 'Tar Heels', 'Demon Deacons', 'Orange',
                   'Hurricanes', 'Nittany Lions', 'Terrapins', 'Scarlet Knights', 'Hoosiers',
                   'Golden Panthers', '49ers', 'Roadrunners', 'Chanticleers', 'Dukes', 'Monarchs',
                   'Bobcats', 'RedHawks', 'Huskies', 'Chippewas', 'Rockets', 'Thundering Herd',
                   'Hilltoppers', 'Ragin\' Cajuns', 'Warhawks', 'Jaguars', 'Gamecocks', 'Flames']
        
        for mascot in mascots:
            if full_name.endswith(' ' + mascot):
                return full_name[:-len(mascot)-1]
        
        return full_name
    
    def calculate_ats_records(self, sport: str, lookback_days: int = 365) -> pd.DataFrame:
        """
        Calculate each team's ATS record (wins/losses against the spread).
        
        Returns DataFrame with:
        - team: Team name
        - ats_wins: Games where team covered the spread
        - ats_losses: Games where team didn't cover
        - ats_pushes: Games that landed exactly on the spread
        - ats_win_pct: ATS winning percentage
        - total_games: Total games played
        """
        conn = sqlite3.connect(self.db_path)
        
        # Get completed games with scores
        query = f"""
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
                p.win_probability,
                p.elo_home_prob,
                p.xgboost_home_prob,
                p.meta_home_prob
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id
            WHERE g.sport = ?
              AND g.status = 'final'
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) >= date('now', '-{lookback_days} days')
            ORDER BY g.game_date DESC
        """
        
        df = pd.read_sql_query(query, conn, params=(sport,))
        conn.close()
        
        if df.empty:
            logger.warning(f"No completed games found for {sport}")
            return pd.DataFrame()
        
        # Calculate model-implied spread for each game
        # Spread = (Home Expected Score - Away Expected Score)
        # Using probability to derive expected margin
        df['model_spread'] = self._calculate_model_spread(df, sport)
        
        # Calculate actual margin
        df['actual_margin'] = df['home_score'] - df['away_score']
        
        # Calculate ATS result for home team
        # If home team won by more than model spread, they covered
        df['home_ats_result'] = np.where(
            np.abs(df['actual_margin'] - df['model_spread']) < 0.5, 'push',
            np.where(df['actual_margin'] > df['model_spread'], 'win', 'loss')
        )
        
        # Build team-level ATS records
        home_ats = df.groupby('home_team_id').apply(
            lambda x: pd.Series({
                'ats_wins': (x['home_ats_result'] == 'win').sum(),
                'ats_losses': (x['home_ats_result'] == 'loss').sum(),
                'ats_pushes': (x['home_ats_result'] == 'push').sum(),
                'total_games': len(x)
            })
        ).reset_index()
        home_ats.columns = ['team'] + list(home_ats.columns[1:])
        
        # Away team ATS (inverse of home)
        df['away_ats_result'] = np.where(
            df['home_ats_result'] == 'push', 'push',
            np.where(df['home_ats_result'] == 'win', 'loss', 'win')
        )
        
        away_ats = df.groupby('away_team_id').apply(
            lambda x: pd.Series({
                'ats_wins': (x['away_ats_result'] == 'win').sum(),
                'ats_losses': (x['away_ats_result'] == 'loss').sum(),
                'ats_pushes': (x['away_ats_result'] == 'push').sum(),
                'total_games': len(x)
            })
        ).reset_index()
        away_ats.columns = ['team'] + list(away_ats.columns[1:])
        
        # Combine home and away
        combined = pd.concat([home_ats, away_ats])
        team_ats = combined.groupby('team').agg({
            'ats_wins': 'sum',
            'ats_losses': 'sum',
            'ats_pushes': 'sum',
            'total_games': 'sum'
        }).reset_index()
        
        # Calculate ATS win percentage
        team_ats['ats_win_pct'] = team_ats['ats_wins'] / (
            team_ats['ats_wins'] + team_ats['ats_losses']
        )
        
        # Sort by ATS win percentage
        team_ats = team_ats.sort_values('ats_win_pct', ascending=False)
        
        return team_ats
    
    def calculate_over_under_records(self, sport: str, lookback_days: int = 365) -> pd.DataFrame:
        """
        Calculate each team's over/under record.
        
        Returns DataFrame with:
        - team: Team name
        - over_count: Games that went over the total
        - under_count: Games that went under the total
        - push_count: Games that hit exactly the total
        - over_pct: Over percentage
        - avg_total: Average actual total
        - avg_model_total: Average predicted total
        """
        conn = sqlite3.connect(self.db_path)
        
        query = f"""
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
                p.win_probability,
                p.elo_home_prob,
                p.xgboost_home_prob,
                p.meta_home_prob
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id
            WHERE g.sport = ?
              AND g.status = 'final'
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) >= date('now', '-{lookback_days} days')
            ORDER BY g.game_date DESC
        """
        
        df = pd.read_sql_query(query, conn, params=(sport,))
        conn.close()
        
        if df.empty:
            logger.warning(f"No completed games found for {sport}")
            return pd.DataFrame()
        
        # Calculate model total
        df['model_total'] = self._calculate_model_total(df, sport)
        
        # Calculate actual total
        df['actual_total'] = df['home_score'] + df['away_score']
        
        # Determine over/under result
        df['ou_result'] = np.where(
            np.abs(df['actual_total'] - df['model_total']) < 0.5, 'push',
            np.where(df['actual_total'] > df['model_total'], 'over', 'under')
        )
        
        # Aggregate by team (both home and away)
        results = []
        
        for team_col in ['home_team_id', 'away_team_id']:
            team_df = df.groupby(team_col).apply(
                lambda x: pd.Series({
                    'over_count': (x['ou_result'] == 'over').sum(),
                    'under_count': (x['ou_result'] == 'under').sum(),
                    'push_count': (x['ou_result'] == 'push').sum(),
                    'total_games': len(x),
                    'avg_total': x['actual_total'].mean(),
                    'avg_model_total': x['model_total'].mean()
                })
            ).reset_index()
            team_df.columns = ['team'] + list(team_df.columns[1:])
            results.append(team_df)
        
        # Combine
        combined = pd.concat(results)
        team_ou = combined.groupby('team').agg({
            'over_count': 'sum',
            'under_count': 'sum',
            'push_count': 'sum',
            'total_games': 'sum',
            'avg_total': 'mean',
            'avg_model_total': 'mean'
        }).reset_index()
        
        # Calculate over percentage
        team_ou['over_pct'] = team_ou['over_count'] / (
            team_ou['over_count'] + team_ou['under_count']
        )
        
        return team_ou
    
    def _calculate_model_spread(self, df: pd.DataFrame, sport: str) -> pd.Series:
        """
        Calculate model-implied spread from win probabilities.
        Uses the formula from rules: spread = expected_home_score - expected_away_score
        """
        # Use best available probability (meta > xgboost > elo)
        prob = df['meta_home_prob'].fillna(
            df['xgboost_home_prob'].fillna(
                df['elo_home_prob'].fillna(0.5)
            )
        )
        
        # Convert probability to point spread
        # Empirical formula: spread ≈ 25 * (prob - 0.5) for basketball
        # Adjust multiplier by sport
        multipliers = {
            'NBA': 25,
            'WNBA': 25,
            'NCAAB': 25,
            'NFL': 14,
            'NCAAF': 14,
            'NHL': 1.5,
            'MLB': 2.5
        }
        
        multiplier = multipliers.get(sport, 10)
        spread = multiplier * (prob - 0.5)
        
        return spread
    
    def _calculate_model_total(self, df: pd.DataFrame, sport: str) -> pd.Series:
        """
        Calculate model-implied total score.
        Uses sport-specific averages as baseline.
        """
        # Sport-specific average totals
        avg_totals = {
            'NBA': 220,
            'WNBA': 160,
            'NCAAB': 140,
            'NFL': 45,
            'NCAAF': 55,
            'NHL': 6,
            'MLB': 9
        }
        
        # Return baseline (could be enhanced with team-specific stats)
        return pd.Series([avg_totals.get(sport, 100)] * len(df))
    
    def generate_spread_picks(self, sport: str, days_ahead: int = 7) -> List[Dict]:
        """
        Generate spread picks using threshold-based system.
        Pick teams with ATS% > 61%, fade teams with ATS% < 31%.
        Uses real betting lines when available.
        """
        conn = sqlite3.connect(self.db_path)
        
        # Get ALL upcoming games
        query = f"""
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                bl.spread
            FROM games g
            LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
            WHERE g.sport = ?
              AND g.status != 'final'
              AND date(g.game_date) >= date('now')
              AND date(g.game_date) <= date('now', '+{days_ahead} days')
            ORDER BY g.game_date ASC
        """
        
        df = pd.read_sql_query(query, conn, params=(sport,))
        conn.close()
        
        if df.empty:
            return []
        
        picks = []
        
        for _, game in df.iterrows():
            home = game['home_team_id']
            away = game['away_team_id']
            spread = game['spread'] if pd.notna(game['spread']) else None
            
            # Get team records
            home_rec = self.get_team_records(sport, home)
            away_rec = self.get_team_records(sport, away)
            
            home_ats = home_rec['ats_pct']
            away_ats = away_rec['ats_pct']
            
            # Skip teams with no data (0% with no games)
            if home_ats == 0 and home_rec['ats_wins'] == 0 and home_rec['ats_losses'] == 0:
                home_ats = None
            if away_ats == 0 and away_rec['ats_wins'] == 0 and away_rec['ats_losses'] == 0:
                away_ats = None
            
            # Only pick ONE team per game based on thresholds
            # Priority: home >61% > away >61% > home <31% (fade) > away <31% (fade)
            if home_ats and home_ats > 0.61:
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': home,
                    'pick_type': 'HOME_SPREAD',
                    'model_spread': spread if spread else 0.0,
                    'confidence': home_ats,
                    'bet_type': 'SPREAD'
                })
            elif away_ats and away_ats > 0.61:
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': away,
                    'pick_type': 'AWAY_SPREAD',
                    'model_spread': -spread if spread else 0.0,
                    'confidence': away_ats,
                    'bet_type': 'SPREAD'
                })
            elif home_ats and home_ats < 0.31 and home_ats > 0:
                # Fade home (pick away) - only if they have games played
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': away,
                    'pick_type': 'AWAY_SPREAD',
                    'model_spread': -spread if spread else 0.0,
                    'confidence': 1 - home_ats,
                    'bet_type': 'SPREAD'
                })
            elif away_ats and away_ats < 0.31 and away_ats > 0:
                # Fade away (pick home) - only if they have games played
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': home,
                    'pick_type': 'HOME_SPREAD',
                    'model_spread': spread if spread else 0.0,
                    'confidence': 1 - away_ats,
                    'bet_type': 'SPREAD'
                })
        
        return picks
    
    def generate_moneyline_picks(self, sport: str, days_ahead: int = 7) -> List[Dict]:
        """
        Generate moneyline picks using threshold-based system.
        Pick teams with win% > 61%, fade teams with win% < 31%.
        """
        conn = sqlite3.connect(self.db_path)
        
        query = f"""
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id
            FROM games g
            WHERE g.sport = ?
              AND g.status != 'final'
              AND date(g.game_date) >= date('now')
              AND date(g.game_date) <= date('now', '+{days_ahead} days')
            ORDER BY g.game_date ASC
        """
        
        df = pd.read_sql_query(query, conn, params=(sport,))
        conn.close()
        
        if df.empty:
            return []
        
        picks = []
        
        for _, game in df.iterrows():
            home = game['home_team_id']
            away = game['away_team_id']
            
            # Get team records
            home_rec = self.get_team_records(sport, home)
            away_rec = self.get_team_records(sport, away)
            
            home_win_pct = home_rec['win_pct']
            away_win_pct = away_rec['win_pct']
            
            # Skip teams with no data (0% with no games)
            if home_win_pct == 0 and home_rec['wins'] == 0 and home_rec['losses'] == 0:
                home_win_pct = None
            if away_win_pct == 0 and away_rec['wins'] == 0 and away_rec['losses'] == 0:
                away_win_pct = None
            
            # Only pick ONE team per game based on thresholds
            # Priority: home >61% > away >61% > home <31% (fade) > away <31% (fade)
            if home_win_pct and home_win_pct > 0.61:
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': home,
                    'pick_type': 'HOME_ML',
                    'win_probability': home_win_pct,
                    'confidence': home_win_pct,
                    'bet_type': 'MONEYLINE'
                })
            elif away_win_pct and away_win_pct > 0.61:
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': away,
                    'pick_type': 'AWAY_ML',
                    'win_probability': away_win_pct,
                    'confidence': away_win_pct,
                    'bet_type': 'MONEYLINE'
                })
            elif home_win_pct and home_win_pct < 0.31 and home_win_pct > 0:
                # Fade home (pick away) - only if they have games played
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': away,
                    'pick_type': 'AWAY_ML',
                    'win_probability': 1 - home_win_pct,
                    'confidence': 1 - home_win_pct,
                    'bet_type': 'MONEYLINE'
                })
            elif away_win_pct and away_win_pct < 0.31 and away_win_pct > 0:
                # Fade away (pick home) - only if they have games played
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': home,
                    'pick_type': 'HOME_ML',
                    'win_probability': 1 - away_win_pct,
                    'confidence': 1 - away_win_pct,
                    'bet_type': 'MONEYLINE'
                })
        
        return picks
    
    def generate_total_picks(self, sport: str, days_ahead: int = 7) -> List[Dict]:
        """
        Generate over/under picks using threshold-based system.
        Combines both teams' O/U records and picks if combined % >= 60%.
        Uses real betting totals when available.
        """
        conn = sqlite3.connect(self.db_path)
        
        query = f"""
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                bl.total
            FROM games g
            LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
            WHERE g.sport = ?
              AND g.status != 'final'
              AND date(g.game_date) >= date('now')
              AND date(g.game_date) <= date('now', '+{days_ahead} days')
            ORDER BY g.game_date ASC
        """
        
        df = pd.read_sql_query(query, conn, params=(sport,))
        conn.close()
        
        if df.empty:
            return []
        
        # Sport averages for when no betting total exists
        avg_totals = {'NBA': 220, 'NFL': 45, 'NHL': 6, 'NCAAF': 55, 'MLB': 9}
        default_total = avg_totals.get(sport, 100)
        
        picks = []
        
        for _, game in df.iterrows():
            home = game['home_team_id']
            away = game['away_team_id']
            total = game['total'] if pd.notna(game['total']) else default_total
            
            # Get team records
            home_rec = self.get_team_records(sport, home)
            away_rec = self.get_team_records(sport, away)
            
            # Calculate combined over/under percentages
            home_over = home_rec['over_wins']
            home_under = home_rec['under_wins']
            away_over = away_rec['over_wins']
            away_under = away_rec['under_wins']
            
            combined_over_wins = home_over + away_over
            combined_over_losses = home_under + away_under
            combined_over_total = combined_over_wins + combined_over_losses
            
            combined_under_wins = home_under + away_under
            combined_under_losses = home_over + away_over
            combined_under_total = combined_under_wins + combined_under_losses
            
            over_pct = combined_over_wins / combined_over_total if combined_over_total > 0 else 0
            under_pct = combined_under_wins / combined_under_total if combined_under_total > 0 else 0
            
            # Only pick if combined % >= 60%
            if over_pct >= 0.60:
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': f"{home} vs {away}",
                    'pick_type': 'OVER',
                    'model_total': round(total, 1),
                    'confidence': over_pct,
                    'bet_type': 'TOTAL'
                })
            elif under_pct >= 0.60:
                picks.append({
                    'game_id': game['game_id'],
                    'game_date': game['game_date'],
                    'home_team': home,
                    'away_team': away,
                    'pick_team': f"{home} vs {away}",
                    'pick_type': 'UNDER',
                    'model_total': round(total, 1),
                    'confidence': under_pct,
                    'bet_type': 'TOTAL'
                })
        
        return picks
    
    def get_all_picks(self, sport: str, days_ahead: int = 7) -> Dict[str, List[Dict]]:
        """
        Get all betting picks (moneyline, spread, totals) for a sport.
        
        Returns dict with keys:
        - moneyline: List of ML picks
        - spread: List of spread picks
        - totals: List of over/under picks
        """
        logger.info(f"Generating all picks for {sport}...")
        
        return {
            'moneyline': self.generate_moneyline_picks(sport, days_ahead),
            'spread': self.generate_spread_picks(sport, days_ahead),
            'totals': self.generate_total_picks(sport, days_ahead)
        }
    
    def print_picks(self, picks: Dict[str, List[Dict]], sport: str):
        """Pretty print all betting picks"""
        print(f"\n{'='*80}")
        print(f"{sport} BETTING PICKS - ATS SYSTEM")
        print(f"{'='*80}\n")
        
        # Moneyline picks
        ml_picks = picks['moneyline']
        if ml_picks:
            print(f"💰 MONEYLINE PICKS ({len(ml_picks)})")
            print(f"{'-'*80}")
            for pick in ml_picks:
                print(f"  📅 {pick['game_date']}: {pick['away_team']} @ {pick['home_team']}")
                print(f"     ✅ PICK: {pick['pick_team']} ML")
                print(f"     Win Prob: {pick['win_probability']:.1%} | Confidence: {pick['confidence']:.0%}")
                print()
        
        # Spread picks
        spread_picks = picks['spread']
        if spread_picks:
            print(f"📊 SPREAD PICKS ({len(spread_picks)})")
            print(f"{'-'*80}")
            for pick in spread_picks:
                print(f"  📅 {pick['game_date']}: {pick['away_team']} @ {pick['home_team']}")
                print(f"     ✅ PICK: {pick['pick_team']} {pick['model_spread']:+.1f}")
                print(f"     Confidence: {pick['confidence']:.0%}")
                print()
        
        # Total picks
        total_picks = picks['totals']
        if total_picks:
            print(f"🎯 OVER/UNDER PICKS ({len(total_picks)})")
            print(f"{'-'*80}")
            for pick in total_picks:
                print(f"  📅 {pick['game_date']}: {pick['away_team']} @ {pick['home_team']}")
                print(f"     ✅ PICK: {pick['pick_type']} (Model Total: {pick['model_total']:.1f})")
                print(f"     System Team: {pick['pick_team']} | Confidence: {pick['confidence']:.0%}")
                print()
        
        total_count = len(ml_picks) + len(spread_picks) + len(total_picks)
        print(f"{'='*80}")
        print(f"Total Picks: {total_count}")
        print(f"{'='*80}\n")


def main():
    """Test the ATS system"""
    ats = ATSSystem()
    
    sports = ['NBA', 'NHL', 'NFL']
    
    for sport in sports:
        print(f"\n{'='*80}")
        print(f"{sport} ATS ANALYSIS")
        print(f"{'='*80}\n")
        
        # Show ATS records
        ats_records = ats.calculate_ats_records(sport, lookback_days=180)
        if not ats_records.empty:
            print(f"TOP 10 ATS TEAMS:")
            print(ats_records.head(10).to_string(index=False))
            print()
        
        # Show over/under records
        ou_records = ats.calculate_over_under_records(sport, lookback_days=180)
        if not ou_records.empty:
            print(f"\nTOP OVER TEAMS:")
            over_sorted = ou_records.sort_values('over_pct', ascending=False)
            print(over_sorted.head(10)[['team', 'over_pct', 'avg_total']].to_string(index=False))
            
            print(f"\nTOP UNDER TEAMS:")
            under_sorted = ou_records.sort_values('over_pct', ascending=True)
            print(under_sorted.head(10)[['team', 'over_pct', 'avg_total']].to_string(index=False))
            print()
        
        # Generate picks
        all_picks = ats.get_all_picks(sport, days_ahead=14)
        ats.print_picks(all_picks, sport)


if __name__ == '__main__':
    main()
