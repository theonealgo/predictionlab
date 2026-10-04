#!/usr/bin/env python3
"""
Daily ATS Records Updater
==========================
Fetches real ATS records, over/under trends, and standings from APIs.
Updates the ATS system with current data every day.

Data Sources:
- NFL: nfl_data_py library (schedules, scores, vegas lines)
- NBA: SportsData.io API (spreads, totals, moneylines)
- NHL: ESPN/NHL API (scores) + manual spread tracking
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import json
from typing import Dict, List, Tuple

# Import your existing APIs
try:
    import nfl_data_py as nfl
    NFL_AVAILABLE = True
except:
    NFL_AVAILABLE = False

try:
    from nba_sportsdata_api import NBASportsDataAPI
    NBA_AVAILABLE = True
except:
    NBA_AVAILABLE = False

try:
    from nhl_api import NHLAPI
    NHL_AVAILABLE = True
except:
    NHL_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DailyATSUpdater:
    """
    Fetches daily ATS records, over/under trends, and team standings from APIs.
    Updates database with real betting line data.
    """
    
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
        self._create_betting_lines_table()
    
    def _create_betting_lines_table(self):
        """Create table to store actual betting lines"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS betting_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL,
                game_id TEXT NOT NULL,
                game_date DATE NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                spread REAL,
                total REAL,
                home_moneyline INTEGER,
                away_moneyline INTEGER,
                source TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sport, game_id)
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_betting_lines_sport_date 
            ON betting_lines(sport, game_date)
        """)
        
        conn.commit()
        conn.close()
        logger.info("✓ Betting lines table ready")
    
    def update_nfl_ats_records(self, season=2025) -> Dict:
        """
        Fetch NFL data and calculate real ATS records.
        Uses nfl_data_py to get schedules with results and lines.
        """
        if not NFL_AVAILABLE:
            logger.warning("nfl_data_py not available")
            return {}
        
        logger.info(f"Fetching NFL {season} schedule and results...")
        
        try:
            # Get schedule with scores
            schedule = nfl.import_schedules([season])
            
            if schedule.empty:
                logger.warning(f"No NFL schedule data for {season}")
                return {}
            
            # Only process games with results
            completed = schedule[schedule['result'].notna()].copy()
            
            if completed.empty:
                logger.warning("No completed NFL games yet")
                return {}
            
            logger.info(f"Found {len(completed)} completed NFL games")
            
            # Calculate ATS records
            ats_records = self._calculate_nfl_ats(completed)
            ou_records = self._calculate_nfl_over_under(completed)
            
            # Store betting lines in database
            self._store_nfl_betting_lines(schedule)
            
            return {
                'ats': ats_records,
                'over_under': ou_records,
                'total_games': len(completed)
            }
        
        except Exception as e:
            logger.error(f"Error updating NFL ATS records: {e}")
            return {}
    
    def _calculate_nfl_ats(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate NFL ATS records from schedule data.
        Assumes spread is from home team perspective (negative = home favored).
        """
        # Make copy to avoid warnings
        df = df.copy()
        
        # Calculate actual margin (home - away)
        df['actual_margin'] = df['home_score'] - df['away_score']
        
        # Spread is usually negative if home favored
        # ATS cover: If home favored by -7, they need to win by >7
        # If spread is -7 and they win by 10, they covered (10 > 7)
        df['spread'] = df.get('spread_line', 0).fillna(0)
        
        # Home team covers if actual_margin + spread > 0
        # Example: spread=-7, actual_margin=10 -> 10-7=3 > 0 = cover
        df['home_ats_result'] = np.where(
            df['spread'] == 0, 'push',  # No line
            np.where(
                abs(df['actual_margin'] + df['spread']) < 0.5, 'push',
                np.where(df['actual_margin'] + df['spread'] > 0, 'cover', 'no_cover')
            )
        )
        
        # Aggregate by home team
        home_ats = df.groupby('home_team').apply(
            lambda x: pd.Series({
                'covers': (x['home_ats_result'] == 'cover').sum(),
                'no_covers': (x['home_ats_result'] == 'no_cover').sum(),
                'pushes': (x['home_ats_result'] == 'push').sum(),
                'home_games': len(x)
            }), include_groups=False
        ).reset_index()
        
        # Away team ATS (inverse logic)
        df['away_ats_result'] = np.where(
            df['home_ats_result'] == 'push', 'push',
            np.where(df['home_ats_result'] == 'cover', 'no_cover', 'cover')
        )
        
        away_ats = df.groupby('away_team').apply(
            lambda x: pd.Series({
                'covers': (x['away_ats_result'] == 'cover').sum(),
                'no_covers': (x['away_ats_result'] == 'no_cover').sum(),
                'pushes': (x['away_ats_result'] == 'push').sum(),
                'away_games': len(x)
            }), include_groups=False
        ).reset_index()
        away_ats.rename(columns={'away_team': 'home_team'}, inplace=True)
        
        # Combine
        combined = pd.merge(
            home_ats, away_ats,
            on='home_team', how='outer',
            suffixes=('_home', '_away')
        ).fillna(0)
        
        combined['total_covers'] = combined['covers_home'] + combined['covers_away']
        combined['total_no_covers'] = combined['no_covers_home'] + combined['no_covers_away']
        combined['total_pushes'] = combined['pushes_home'] + combined['pushes_away']
        combined['total_games'] = combined['home_games'] + combined['away_games']
        
        # ATS record format: covers-no_covers-pushes
        combined['total_covers'] = combined['total_covers'].fillna(0).astype(int)
        combined['total_no_covers'] = combined['total_no_covers'].fillna(0).astype(int)
        combined['total_pushes'] = combined['total_pushes'].fillna(0).astype(int)
        
        combined['ats_record'] = (
            combined['total_covers'].astype(str) + '-' +
            combined['total_no_covers'].astype(str) + '-' +
            combined['total_pushes'].astype(str)
        )
        
        combined['ats_pct'] = combined['total_covers'] / (
            combined['total_covers'] + combined['total_no_covers']
        ).replace(0, 1)
        
        result = combined[['home_team', 'ats_record', 'ats_pct', 'total_games']].copy()
        result.columns = ['team', 'ats_record', 'ats_pct', 'games']
        result = result.sort_values('ats_pct', ascending=False)
        
        return result
    
    def _calculate_nfl_over_under(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate NFL over/under records"""
        df = df.copy()
        
        df['total_points'] = df['home_score'] + df['away_score']
        df['total_line'] = df.get('total_line', 45).fillna(45)  # Default if missing
        
        df['ou_result'] = np.where(
            abs(df['total_points'] - df['total_line']) < 0.5, 'push',
            np.where(df['total_points'] > df['total_line'], 'over', 'under')
        )
        
        # Count by team (both home and away)
        home_ou = df.groupby('home_team')['ou_result'].value_counts().unstack(fill_value=0)
        away_ou = df.groupby('away_team')['ou_result'].value_counts().unstack(fill_value=0)
        
        combined_ou = home_ou.add(away_ou, fill_value=0)
        combined_ou['total_games'] = combined_ou.sum(axis=1)
        combined_ou['over_pct'] = combined_ou.get('over', 0) / (
            combined_ou.get('over', 0) + combined_ou.get('under', 0)
        ).replace(0, 1)
        
        combined_ou['ou_record'] = (
            combined_ou.get('over', 0).astype(int).astype(str) + '-' +
            combined_ou.get('under', 0).astype(int).astype(str) + '-' +
            combined_ou.get('push', 0).astype(int).astype(str)
        )
        
        result = combined_ou[['ou_record', 'over_pct', 'total_games']].reset_index()
        result.columns = ['team', 'ou_record', 'over_pct', 'games']
        result = result.sort_values('over_pct', ascending=False)
        
        return result
    
    def _store_nfl_betting_lines(self, schedule: pd.DataFrame):
        """Store NFL betting lines in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for _, game in schedule.iterrows():
            cursor.execute("""
                INSERT OR REPLACE INTO betting_lines 
                (sport, game_id, game_date, home_team, away_team, spread, total, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'NFL',
                game.get('game_id', ''),
                game.get('gameday', ''),
                game.get('home_team', ''),
                game.get('away_team', ''),
                game.get('spread_line'),
                game.get('total_line'),
                'nfl_data_py'
            ))
        
        conn.commit()
        conn.close()
    
    def update_nba_ats_records(self, days_back=30) -> Dict:
        """
        Fetch NBA data and calculate real ATS records.
        Uses SportsData.io API which includes actual betting lines.
        """
        if not NBA_AVAILABLE:
            logger.warning("NBA API not available")
            return {}
        
        logger.info(f"Fetching NBA games from last {days_back} days...")
        
        try:
            api = NBASportsDataAPI()
            
            # Get recent games (past month)
            games = api.get_recent_and_upcoming_games(days_back=days_back, days_forward=0)
            
            if not games:
                logger.warning("No NBA games fetched")
                return {}
            
            # Only process finished games with betting lines
            completed = [g for g in games if g['status'] == 'final' and g['spread'] is not None]
            
            logger.info(f"Found {len(completed)} completed NBA games with betting lines")
            
            if not completed:
                return {}
            
            # Convert to DataFrame
            df = pd.DataFrame(completed)
            
            # Calculate ATS records
            ats_records = self._calculate_nba_ats(df)
            ou_records = self._calculate_nba_over_under(df)
            
            # Store betting lines
            self._store_nba_betting_lines(games)
            
            return {
                'ats': ats_records,
                'over_under': ou_records,
                'total_games': len(completed)
            }
        
        except Exception as e:
            logger.error(f"Error updating NBA ATS records: {e}")
            return {}
    
    def _calculate_nba_ats(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate NBA ATS records from games with actual spreads"""
        df = df.copy()
        
        # Calculate actual margin
        df['actual_margin'] = df['home_score'] - df['away_score']
        
        # ATS logic (spread is from home perspective)
        df['home_ats_result'] = np.where(
            abs(df['actual_margin'] + df['spread']) < 0.5, 'push',
            np.where(df['actual_margin'] + df['spread'] > 0, 'cover', 'no_cover')
        )
        
        # Home team ATS
        home_ats = df.groupby('home_team_id').apply(
            lambda x: pd.Series({
                'covers': (x['home_ats_result'] == 'cover').sum(),
                'no_covers': (x['home_ats_result'] == 'no_cover').sum(),
                'pushes': (x['home_ats_result'] == 'push').sum()
            }), include_groups=False
        ).reset_index()
        
        # Away team ATS
        df['away_ats_result'] = np.where(
            df['home_ats_result'] == 'push', 'push',
            np.where(df['home_ats_result'] == 'cover', 'no_cover', 'cover')
        )
        
        away_ats = df.groupby('away_team_id').apply(
            lambda x: pd.Series({
                'covers': (x['away_ats_result'] == 'cover').sum(),
                'no_covers': (x['away_ats_result'] == 'no_cover').sum(),
                'pushes': (x['away_ats_result'] == 'push').sum()
            }), include_groups=False
        ).reset_index()
        away_ats.rename(columns={'away_team_id': 'home_team_id'}, inplace=True)
        
        # Combine
        combined = pd.merge(
            home_ats, away_ats, on='home_team_id', how='outer', suffixes=('_home', '_away')
        ).fillna(0)
        
        combined['total_covers'] = combined['covers_home'] + combined['covers_away']
        combined['total_no_covers'] = combined['no_covers_home'] + combined['no_covers_away']
        combined['total_pushes'] = combined['pushes_home'] + combined['pushes_away']
        
        combined['ats_record'] = (
            combined['total_covers'].astype(int).astype(str) + '-' +
            combined['total_no_covers'].astype(int).astype(str) + '-' +
            combined['total_pushes'].astype(int).astype(str)
        )
        
        combined['ats_pct'] = combined['total_covers'] / (
            combined['total_covers'] + combined['total_no_covers']
        ).replace(0, 1)
        
        result = combined[['home_team_id', 'ats_record', 'ats_pct']].copy()
        result.columns = ['team', 'ats_record', 'ats_pct']
        result = result.sort_values('ats_pct', ascending=False)
        
        return result
    
    def _calculate_nba_over_under(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate NBA over/under records"""
        df = df.copy()
        
        df['total_points'] = df['home_score'] + df['away_score']
        
        df['ou_result'] = np.where(
            abs(df['total_points'] - df['total']) < 0.5, 'push',
            np.where(df['total_points'] > df['total'], 'over', 'under')
        )
        
        # Count by team
        home_ou = df.groupby('home_team_id')['ou_result'].value_counts().unstack(fill_value=0)
        away_ou = df.groupby('away_team_id')['ou_result'].value_counts().unstack(fill_value=0)
        
        combined_ou = home_ou.add(away_ou, fill_value=0)
        combined_ou['over_pct'] = combined_ou.get('over', 0) / (
            combined_ou.get('over', 0) + combined_ou.get('under', 0)
        ).replace(0, 1)
        
        combined_ou['ou_record'] = (
            combined_ou.get('over', 0).astype(int).astype(str) + '-' +
            combined_ou.get('under', 0).astype(int).astype(str) + '-' +
            combined_ou.get('push', 0).astype(int).astype(str)
        )
        
        result = combined_ou[['ou_record', 'over_pct']].reset_index()
        result.columns = ['team', 'ou_record', 'over_pct']
        result = result.sort_values('over_pct', ascending=False)
        
        return result
    
    def _store_nba_betting_lines(self, games: List[Dict]):
        """Store NBA betting lines in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for game in games:
            if game.get('spread') is None:
                continue
            
            cursor.execute("""
                INSERT OR REPLACE INTO betting_lines 
                (sport, game_id, game_date, home_team, away_team, spread, total, 
                 home_moneyline, away_moneyline, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'NBA',
                game['game_id'],
                game['game_date'],
                game['home_team_id'],
                game['away_team_id'],
                game['spread'],
                game['total'],
                game.get('home_moneyline'),
                game.get('away_moneyline'),
                'sportsdata_io'
            ))
        
        conn.commit()
        conn.close()
    
    def print_report(self, sport: str, data: Dict):
        """Print formatted ATS report"""
        print(f"\n{'='*80}")
        print(f"{sport} ATS REPORT - {datetime.now().strftime('%Y-%m-%d')}")
        print(f"{'='*80}\n")
        
        if not data:
            print(f"❌ No data available for {sport}\n")
            return
        
        print(f"📊 Total Games Analyzed: {data.get('total_games', 0)}\n")
        
        # ATS Records
        if 'ats' in data and not data['ats'].empty:
            print("🎯 ATS RECORDS (Top 10 Covers)")
            print("-" * 80)
            top_ats = data['ats'].head(10)
            for _, row in top_ats.iterrows():
                print(f"  {row['team']:<30} {row['ats_record']:<15} ({row['ats_pct']:.1%})")
            
            print("\n❌ ATS RECORDS (Bottom 5 - Worst Covers)")
            print("-" * 80)
            bottom_ats = data['ats'].tail(5)
            for _, row in bottom_ats.iterrows():
                print(f"  {row['team']:<30} {row['ats_record']:<15} ({row['ats_pct']:.1%})")
        
        # Over/Under
        if 'over_under' in data and not data['over_under'].empty:
            print("\n🔥 OVER TEAMS (Top 10)")
            print("-" * 80)
            top_over = data['over_under'].head(10)
            for _, row in top_over.iterrows():
                print(f"  {row['team']:<30} {row['ou_record']:<15} ({row['over_pct']:.1%})")
            
            print("\n❄️  UNDER TEAMS (Bottom 5)")
            print("-" * 80)
            bottom_under = data['over_under'].tail(5)
            for _, row in bottom_under.iterrows():
                print(f"  {row['team']:<30} {row['ou_record']:<15} ({row['over_pct']:.1%})")
        
        print(f"\n{'='*80}\n")


def auto_update_system_teams(all_sports_data: Dict):
    """Automatically update SYSTEM_TEAMS in ats_system.py based on API data"""
    print("\n" + "="*80)
    print("AUTO-UPDATING SYSTEM TEAMS IN ats_system.py")
    print("="*80)
    
    new_system_teams = {}
    
    for sport, data in all_sports_data.items():
        if not data or 'ats' not in data or data['ats'].empty:
            continue
        
        ats_df = data['ats']
        ou_df = data.get('over_under', pd.DataFrame())
        
        # Top ATS teams for spread picks (>55% cover rate, min 8 games)
        if 'ats_pct' in ats_df.columns:
            # Add games column if missing
            if 'games' not in ats_df.columns and 'total_games' in ats_df.columns:
                ats_df['games'] = ats_df['total_games']
            elif 'games' not in ats_df.columns:
                ats_df['games'] = 10  # Assume enough games if column missing
            
            top_ats = ats_df[
                (ats_df['ats_pct'] >= 0.55) & 
                (ats_df['games'] >= 8)
            ].head(15)['team'].tolist()
        else:
            top_ats = []
        
        # Top ML teams (same as top ATS)
        top_ml = top_ats[:12] if len(top_ats) > 0 else []
        
        # Over/Under teams
        top_over = []
        top_under = []
        if not ou_df.empty and 'over_pct' in ou_df.columns:
            # Top over teams (>60% over rate)
            top_over = ou_df[ou_df['over_pct'] >= 0.60].head(9)['team'].tolist()
            # Top under teams (<40% over rate)
            top_under = ou_df[ou_df['over_pct'] <= 0.40].head(5)['team'].tolist()
        
        new_system_teams[sport] = {
            'spread': top_ats,
            'moneyline': top_ml,
            'over': top_over,
            'under': top_under
        }
        
        print(f"\n{sport}:")
        print(f"  Spread: {len(top_ats)} teams")
        print(f"  Moneyline: {len(top_ml)} teams")
        print(f"  Over: {len(top_over)} teams")
        print(f"  Under: {len(top_under)} teams")
    
    # Read current ats_system.py
    ats_file = 'ats_system.py'
    try:
        with open(ats_file, 'r') as f:
            content = f.read()
        
        # Find SYSTEM_TEAMS dict and replace it
        import re
        pattern = r'SYSTEM_TEAMS = \{[^}]+(?:\{[^}]+\}[^}]*)+\}'
        
        # Build new SYSTEM_TEAMS string
        new_dict = f"""SYSTEM_TEAMS = {{
        # Auto-updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        for sport, teams in new_system_teams.items():
            new_dict += f"        '{sport}': {{\n"
            new_dict += f"            'spread': {teams['spread']},\n"
            new_dict += f"            'moneyline': {teams['moneyline']},\n"
            new_dict += f"            'over': {teams['over']},\n"
            new_dict += f"            'under': {teams['under']}\n"
            new_dict += f"        }},\n"
        new_dict += "    }"
        
        # Replace in content
        new_content = re.sub(pattern, new_dict, content, flags=re.DOTALL)
        
        # Write back
        with open(ats_file, 'w') as f:
            f.write(new_content)
        
        print(f"\n✅ Updated {ats_file} with new system teams!")
        
    except Exception as e:
        print(f"\n⚠️  Could not auto-update {ats_file}: {e}")
        print("You can manually update using: python3 update_system_teams.py")


def main():
    """Run daily ATS update for all sports"""
    updater = DailyATSUpdater()
    
    print("\n" + "="*80)
    print("DAILY ATS RECORDS UPDATE")
    print("="*80)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    all_sports_data = {}
    
    # Update NFL
    if NFL_AVAILABLE:
        print("\n🏈 Updating NFL ATS Records...")
        nfl_data = updater.update_nfl_ats_records()
        updater.print_report("NFL", nfl_data)
        all_sports_data['NFL'] = nfl_data
    else:
        print("\n⚠️  NFL: nfl_data_py not available")
    
    # Update NBA
    if NBA_AVAILABLE:
        print("\n🏀 Updating NBA ATS Records...")
        nba_data = updater.update_nba_ats_records(days_back=30)
        updater.print_report("NBA", nba_data)
        all_sports_data['NBA'] = nba_data
    else:
        print("\n⚠️  NBA: SportsData API not available")
    
    # NHL (manual tracking for now - no built-in spread API)
    print("\n🏒 NHL: Using model-based ATS tracking (no API spread data)")
    
    print("\n" + "="*80)
    print("✓ Daily update complete!")
    print("="*80 + "\n")
    
    # Auto-update system teams based on API data
    auto_update_system_teams(all_sports_data)
    
    print("\n" + "="*80)
    print("Next steps:")
    print("  1. System teams auto-updated in ats_system.py")
    print("  2. Run: python3 get_ats_picks.py --csv today.csv")
    print("  3. Review picks and place bets")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
