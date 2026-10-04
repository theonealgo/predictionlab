#!/usr/bin/env python3
"""
Calculate ATS Rankings from Real Game Results
Analyzes completed games with betting lines to determine:
1. ATS records (spread coverage)
2. Over/Under records
3. Moneyline win percentage
4. Auto-updates system teams in ats_system.py
"""

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from colorama import Fore, Style, init
import re

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"


def get_db_connection():
    """Get database connection"""
    return sqlite3.connect(DB_PATH)


def calculate_ats_records(sport, min_games=5):
    """
    Calculate ATS records from real betting lines and game results
    
    Returns DataFrame with:
    - team: Team name
    - ats_record: W-L-P format
    - ats_wins, ats_losses, ats_pushes
    - cover_pct: ATS win percentage
    - avg_ats_margin: Average points covered by
    """
    print(f"\n{Fore.CYAN}Calculating {sport} ATS Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    
    # Get completed games with betting lines
    query = """
        SELECT 
            g.game_id,
            g.game_date,
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score,
            bl.spread,
            bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ?
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND bl.spread IS NOT NULL
          AND date(g.game_date) >= date('now', '-90 days')
        ORDER BY g.game_date DESC
    """
    
    df = pd.read_sql_query(query, conn, params=(sport,))
    conn.close()
    
    if df.empty:
        print(f"  {Fore.YELLOW}⚠ No games with betting lines found{Style.RESET_ALL}")
        return pd.DataFrame()
    
    print(f"  Analyzing {len(df)} completed games with betting lines")
    
    # Calculate actual margin and ATS result
    df['actual_margin'] = df['home_score'] - df['away_score']
    df['ats_margin'] = df['actual_margin'] - df['spread']
    
    # Determine ATS result (home team perspective)
    df['home_ats_result'] = df['ats_margin'].apply(
        lambda x: 'push' if abs(x) < 0.5 else ('win' if x > 0 else 'loss')
    )
    
    # Home team ATS records
    home_ats = df.groupby('home_team_id').agg({
        'home_ats_result': lambda x: (
            f"{(x=='win').sum()}-{(x=='loss').sum()}-{(x=='push').sum()}"
        ),
        'game_id': 'count'
    }).rename(columns={'home_ats_result': 'ats_record', 'game_id': 'games'})
    
    home_stats = df.groupby('home_team_id').apply(
        lambda x: pd.Series({
            'ats_wins': (x['home_ats_result'] == 'win').sum(),
            'ats_losses': (x['home_ats_result'] == 'loss').sum(),
            'ats_pushes': (x['home_ats_result'] == 'push').sum(),
            'avg_ats_margin': x['ats_margin'].mean()
        })
    )
    
    home_records = pd.concat([home_ats, home_stats], axis=1)
    home_records.index.name = 'team'
    home_records = home_records.reset_index()
    
    # Away team ATS records (inverse of home)
    df['away_ats_result'] = df['home_ats_result'].apply(
        lambda x: 'push' if x == 'push' else ('win' if x == 'loss' else 'loss')
    )
    df['away_ats_margin'] = -df['ats_margin']
    
    away_ats = df.groupby('away_team_id').agg({
        'away_ats_result': lambda x: (
            f"{(x=='win').sum()}-{(x=='loss').sum()}-{(x=='push').sum()}"
        ),
        'game_id': 'count'
    }).rename(columns={'away_ats_result': 'ats_record', 'game_id': 'games'})
    
    away_stats = df.groupby('away_team_id').apply(
        lambda x: pd.Series({
            'ats_wins': (x['away_ats_result'] == 'win').sum(),
            'ats_losses': (x['away_ats_result'] == 'loss').sum(),
            'ats_pushes': (x['away_ats_result'] == 'push').sum(),
            'avg_ats_margin': x['away_ats_margin'].mean()
        })
    )
    
    away_records = pd.concat([away_ats, away_stats], axis=1)
    away_records.index.name = 'team'
    away_records = away_records.reset_index()
    
    # Combine home and away
    all_teams = pd.concat([home_records[['team', 'ats_wins', 'ats_losses', 'ats_pushes', 'avg_ats_margin']],
                           away_records[['team', 'ats_wins', 'ats_losses', 'ats_pushes', 'avg_ats_margin']]])
    
    team_ats = all_teams.groupby('team').agg({
        'ats_wins': 'sum',
        'ats_losses': 'sum',
        'ats_pushes': 'sum',
        'avg_ats_margin': 'mean'
    }).reset_index()
    
    # Calculate cover percentage
    team_ats['total_games'] = team_ats['ats_wins'] + team_ats['ats_losses']
    team_ats['cover_pct'] = (team_ats['ats_wins'] / team_ats['total_games'] * 100).round(1)
    team_ats['ats_record'] = (team_ats['ats_wins'].astype(str) + '-' + 
                              team_ats['ats_losses'].astype(str) + '-' + 
                              team_ats['ats_pushes'].astype(str))
    
    # Filter teams with minimum games
    team_ats = team_ats[team_ats['total_games'] >= min_games]
    
    # Sort by cover percentage
    team_ats = team_ats.sort_values('cover_pct', ascending=False)
    
    print(f"  {Fore.GREEN}✓ Calculated ATS records for {len(team_ats)} teams{Style.RESET_ALL}")
    
    return team_ats


def calculate_over_under_records(sport, min_games=5):
    """Calculate Over/Under records from real betting totals"""
    print(f"\n{Fore.CYAN}Calculating {sport} Over/Under Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    
    query = """
        SELECT 
            g.game_id,
            g.game_date,
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score,
            bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ?
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND bl.total IS NOT NULL
          AND date(g.game_date) >= date('now', '-90 days')
    """
    
    df = pd.read_sql_query(query, conn, params=(sport,))
    conn.close()
    
    if df.empty:
        print(f"  {Fore.YELLOW}⚠ No games with totals found{Style.RESET_ALL}")
        return pd.DataFrame()
    
    print(f"  Analyzing {len(df)} completed games with totals")
    
    # Calculate actual total and O/U result
    df['actual_total'] = df['home_score'] + df['away_score']
    df['ou_diff'] = df['actual_total'] - df['total']
    
    df['ou_result'] = df['ou_diff'].apply(
        lambda x: 'push' if abs(x) < 0.5 else ('over' if x > 0 else 'under')
    )
    
    # Calculate for each team (both home and away games)
    results = []
    
    for team_col in ['home_team_id', 'away_team_id']:
        team_ou = df.groupby(team_col).apply(
            lambda x: pd.Series({
                'over_count': (x['ou_result'] == 'over').sum(),
                'under_count': (x['ou_result'] == 'under').sum(),
                'push_count': (x['ou_result'] == 'push').sum(),
                'avg_total': x['actual_total'].mean()
            })
        ).reset_index()
        team_ou.columns = ['team', 'over_count', 'under_count', 'push_count', 'avg_total']
        results.append(team_ou)
    
    # Combine
    all_teams_ou = pd.concat(results)
    team_ou = all_teams_ou.groupby('team').agg({
        'over_count': 'sum',
        'under_count': 'sum',
        'push_count': 'sum',
        'avg_total': 'mean'
    }).reset_index()
    
    team_ou['total_games'] = team_ou['over_count'] + team_ou['under_count']
    team_ou['over_pct'] = (team_ou['over_count'] / team_ou['total_games'] * 100).round(1)
    team_ou['ou_record'] = (team_ou['over_count'].astype(str) + '-' + 
                            team_ou['under_count'].astype(str) + '-' + 
                            team_ou['push_count'].astype(str))
    
    # Filter minimum games
    team_ou = team_ou[team_ou['total_games'] >= min_games]
    
    print(f"  {Fore.GREEN}✓ Calculated O/U records for {len(team_ou)} teams{Style.RESET_ALL}")
    
    return team_ou


def calculate_moneyline_records(sport, min_games=5):
    """Calculate straight-up win percentage for moneyline"""
    print(f"\n{Fore.CYAN}Calculating {sport} Moneyline Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    
    query = """
        SELECT 
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score
        FROM games g
        WHERE g.sport = ?
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND date(g.game_date) >= date('now', '-90 days')
    """
    
    df = pd.read_sql_query(query, conn, params=(sport,))
    conn.close()
    
    if df.empty:
        print(f"  {Fore.YELLOW}⚠ No completed games found{Style.RESET_ALL}")
        return pd.DataFrame()
    
    print(f"  Analyzing {len(df)} completed games")
    
    # Home team results
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    home_ml = df.groupby('home_team_id')['home_win'].agg(['sum', 'count'])
    home_ml.columns = ['wins', 'games']
    home_ml.index.name = 'team'
    home_ml = home_ml.reset_index()
    
    # Away team results
    df['away_win'] = (df['away_score'] > df['home_score']).astype(int)
    away_ml = df.groupby('away_team_id')['away_win'].agg(['sum', 'count'])
    away_ml.columns = ['wins', 'games']
    away_ml.index.name = 'team'
    away_ml = away_ml.reset_index()
    
    # Combine
    all_ml = pd.concat([home_ml, away_ml])
    team_ml = all_ml.groupby('team').agg({'wins': 'sum', 'games': 'sum'}).reset_index()
    
    team_ml['losses'] = team_ml['games'] - team_ml['wins']
    team_ml['win_pct'] = (team_ml['wins'] / team_ml['games'] * 100).round(1)
    team_ml['ml_record'] = team_ml['wins'].astype(str) + '-' + team_ml['losses'].astype(str)
    
    # Filter minimum games
    team_ml = team_ml[team_ml['games'] >= min_games]
    team_ml = team_ml.sort_values('win_pct', ascending=False)
    
    print(f"  {Fore.GREEN}✓ Calculated ML records for {len(team_ml)} teams{Style.RESET_ALL}")
    
    return team_ml


def update_system_teams_file(sport, ats_df, ou_df, ml_df):
    """
    Update SYSTEM_TEAMS in ats_system.py with calculated rankings
    
    Criteria:
    - Spread: Top teams with >60% ATS cover rate
    - Moneyline: Teams with >58% win rate
    - Over: Teams with >60% over rate
    - Under: Teams with >60% under rate (inverse - <40% over rate)
    """
    print(f"\n{Fore.CYAN}Updating {sport} System Teams{Style.RESET_ALL}")
    
    new_teams = {
        'spread': [],
        'moneyline': [],
        'over': [],
        'under': []
    }
    
    # Spread: Top ATS performers (>60% cover rate)
    if not ats_df.empty:
        spread_teams = ats_df[ats_df['cover_pct'] >= 60.0]['team'].tolist()
        new_teams['spread'] = spread_teams[:15]  # Top 15
        print(f"  Spread: {len(new_teams['spread'])} teams (>60% ATS)")
    
    # Moneyline: Winners (>58% win rate)
    if not ml_df.empty:
        ml_teams = ml_df[ml_df['win_pct'] >= 58.0]['team'].tolist()
        new_teams['moneyline'] = ml_teams[:15]  # Top 15
        print(f"  Moneyline: {len(new_teams['moneyline'])} teams (>58% wins)")
    
    # Over: High-scoring teams (>60% over rate)
    if not ou_df.empty:
        over_teams = ou_df[ou_df['over_pct'] >= 60.0].sort_values('over_pct', ascending=False)['team'].tolist()
        new_teams['over'] = over_teams[:12]  # Top 12
        print(f"  Over: {len(new_teams['over'])} teams (>60% overs)")
    
    # Under: Low-scoring teams (<40% over rate = >60% under rate)
    if not ou_df.empty:
        under_teams = ou_df[ou_df['over_pct'] <= 40.0].sort_values('over_pct')['team'].tolist()
        new_teams['under'] = under_teams[:10]  # Top 10
        print(f"  Under: {len(new_teams['under'])} teams (<40% overs)")
    
    return new_teams


def main():
    """Calculate all ATS rankings and update system teams"""
    print(f"{Fore.CYAN}{'='*70}")
    print(f"ATS Rankings Calculator - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    sports = ['NBA', 'NFL', 'NHL', 'NCAAF']
    all_system_teams = {}
    
    for sport in sports:
        print(f"\n{Fore.YELLOW}{'='*70}")
        print(f"{sport}")
        print(f"{'='*70}{Style.RESET_ALL}")
        
        # Calculate records
        ats_df = calculate_ats_records(sport, min_games=5)
        ou_df = calculate_over_under_records(sport, min_games=5)
        ml_df = calculate_moneyline_records(sport, min_games=5)
        
        # Display top performers
        if not ats_df.empty:
            print(f"\n  {Fore.GREEN}Top 10 ATS Covers:{Style.RESET_ALL}")
            for idx, row in ats_df.head(10).iterrows():
                print(f"    {row['team'][:30]:30} {row['ats_record']:8} {row['cover_pct']:5.1f}%  +{row['avg_ats_margin']:+.1f}")
        
        if not ml_df.empty:
            print(f"\n  {Fore.GREEN}Top 10 Moneyline:{Style.RESET_ALL}")
            for idx, row in ml_df.head(10).iterrows():
                print(f"    {row['team'][:30]:30} {row['ml_record']:8} {row['win_pct']:5.1f}%")
        
        if not ou_df.empty:
            print(f"\n  {Fore.GREEN}Top 10 Overs:{Style.RESET_ALL}")
            over_sorted = ou_df.sort_values('over_pct', ascending=False)
            for idx, row in over_sorted.head(10).iterrows():
                print(f"    {row['team'][:30]:30} {row['ou_record']:8} {row['over_pct']:5.1f}%")
            
            print(f"\n  {Fore.GREEN}Top 10 Unders:{Style.RESET_ALL}")
            under_sorted = ou_df.sort_values('over_pct')
            for idx, row in under_sorted.head(10).iterrows():
                print(f"    {row['team'][:30]:30} {row['ou_record']:8} {100-row['over_pct']:5.1f}% under")
        
        # Generate system teams
        system_teams = update_system_teams_file(sport, ats_df, ou_df, ml_df)
        all_system_teams[sport] = system_teams
    
    print(f"\n{Fore.GREEN}{'='*70}")
    print(f"✓ ATS Rankings Complete")
    print(f"{'='*70}{Style.RESET_ALL}\n")
    
    # Save to file for manual review
    with open('ats_rankings_output.txt', 'w') as f:
        f.write(f"ATS Rankings - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*70 + "\n\n")
        
        for sport, teams in all_system_teams.items():
            f.write(f"\n{sport}:\n")
            f.write(f"  Spread ({len(teams['spread'])}): {teams['spread']}\n")
            f.write(f"  Moneyline ({len(teams['moneyline'])}): {teams['moneyline']}\n")
            f.write(f"  Over ({len(teams['over'])}): {teams['over']}\n")
            f.write(f"  Under ({len(teams['under'])}): {teams['under']}\n")
    
    print(f"Results saved to: {Fore.CYAN}ats_rankings_output.txt{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
