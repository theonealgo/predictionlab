#!/usr/bin/env python3
"""
System Teams Updater
====================
Analyzes recent ATS and over/under performance to recommend
which teams should be in your system teams lists.

Use this periodically to keep your picks aligned with current trends.
"""

import argparse
from ats_system import ATSSystem


def analyze_sport(ats, sport, lookback_days=90, min_games=10):
    """
    Analyze a sport and recommend system teams based on performance.
    
    Args:
        ats: ATSSystem instance
        sport: Sport to analyze
        lookback_days: Days to look back for analysis
        min_games: Minimum games to qualify
    
    Returns:
        dict with recommended teams for each bet type
    """
    print(f"\n{'='*80}")
    print(f"{sport} - Last {lookback_days} Days")
    print(f"{'='*80}\n")
    
    # Get ATS records
    ats_records = ats.calculate_ats_records(sport, lookback_days=lookback_days)
    
    # Filter teams with enough games
    ats_records = ats_records[ats_records['total_games'] >= min_games]
    
    if ats_records.empty:
        print(f"⚠️  Not enough data for {sport} (need {min_games}+ games)")
        return None
    
    # Get top ATS teams (>55% cover rate)
    top_ats = ats_records[ats_records['ats_win_pct'] >= 0.55].sort_values('ats_win_pct', ascending=False)
    
    print(f"📊 TOP ATS TEAMS (≥55% cover rate, {min_games}+ games)")
    print(f"{'-'*80}")
    if not top_ats.empty:
        print(top_ats[['team', 'ats_wins', 'ats_losses', 'ats_win_pct', 'total_games']].head(15).to_string(index=False))
        spread_teams = top_ats['team'].head(10).tolist()
    else:
        print("No teams meet criteria")
        spread_teams = []
    
    # Moneyline: teams with good win rate
    # Use same ATS records but look at win percentage in actual games
    # For simplicity, we'll use top ATS teams as proxy for ML teams
    ml_teams = top_ats['team'].head(12).tolist()
    
    # Get over/under records
    ou_records = ats.calculate_over_under_records(sport, lookback_days=lookback_days)
    ou_records = ou_records[ou_records['total_games'] >= min_games]
    
    if not ou_records.empty:
        # Top OVER teams (>60% over rate)
        over_teams_df = ou_records[ou_records['over_pct'] >= 0.60].sort_values('over_pct', ascending=False)
        
        print(f"\n🔥 TOP OVER TEAMS (≥60% over rate)")
        print(f"{'-'*80}")
        if not over_teams_df.empty:
            print(over_teams_df[['team', 'over_count', 'under_count', 'over_pct', 'avg_total']].head(10).to_string(index=False))
            over_teams = over_teams_df['team'].head(9).tolist()
        else:
            print("No teams meet criteria")
            over_teams = []
        
        # Top UNDER teams (<40% over rate = >60% under rate)
        under_teams_df = ou_records[ou_records['over_pct'] <= 0.40].sort_values('over_pct', ascending=True)
        
        print(f"\n❄️  TOP UNDER TEAMS (≥60% under rate)")
        print(f"{'-'*80}")
        if not under_teams_df.empty:
            print(under_teams_df[['team', 'over_count', 'under_count', 'over_pct', 'avg_total']].head(10).to_string(index=False))
            under_teams = under_teams_df['team'].head(4).tolist()
        else:
            print("No teams meet criteria")
            under_teams = []
    else:
        over_teams = []
        under_teams = []
    
    # Generate recommendations
    print(f"\n{'='*80}")
    print(f"RECOMMENDED SYSTEM TEAMS FOR {sport}")
    print(f"{'='*80}\n")
    
    print("Copy and paste into ats_system.py:\n")
    print(f"'{sport}': {{")
    print(f"    'spread': {spread_teams},")
    print(f"    'moneyline': {ml_teams},")
    print(f"    'over': {over_teams},")
    print(f"    'under': {under_teams}")
    print(f"}},\n")
    
    return {
        'spread': spread_teams,
        'moneyline': ml_teams,
        'over': over_teams,
        'under': under_teams
    }


def main():
    parser = argparse.ArgumentParser(description='Update system teams based on recent performance')
    parser.add_argument('--sports', nargs='+', default=['NBA', 'NHL', 'NFL'],
                       help='Sports to analyze (default: NBA NHL NFL)')
    parser.add_argument('--lookback', type=int, default=90,
                       help='Days to look back (default: 90)')
    parser.add_argument('--min-games', type=int, default=10,
                       help='Minimum games to qualify (default: 10)')
    
    args = parser.parse_args()
    
    ats = ATSSystem()
    
    print("\n" + "="*80)
    print("SYSTEM TEAMS ANALYSIS & RECOMMENDATIONS")
    print("="*80)
    print(f"\nAnalyzing last {args.lookback} days")
    print(f"Minimum {args.min_games} games to qualify")
    
    all_recommendations = {}
    
    for sport in args.sports:
        recommendations = analyze_sport(ats, sport, args.lookback, args.min_games)
        if recommendations:
            all_recommendations[sport] = recommendations
    
    # Summary
    print(f"\n{'='*80}")
    print("COMPLETE SYSTEM_TEAMS DICTIONARY")
    print(f"{'='*80}\n")
    
    print("SYSTEM_TEAMS = {")
    for sport, teams in all_recommendations.items():
        print(f"    '{sport}': {{")
        print(f"        'spread': {teams['spread']},")
        print(f"        'moneyline': {teams['moneyline']},")
        print(f"        'over': {teams['over']},")
        print(f"        'under': {teams['under']}")
        print(f"    }},")
    print("}\n")
    
    print(f"{'='*80}")
    print("USAGE:")
    print(f"{'='*80}")
    print("1. Copy the dictionary above")
    print("2. Replace SYSTEM_TEAMS in ats_system.py")
    print("3. Re-run get_ats_picks.py to get updated picks")
    print(f"{'='*80}\n")


if __name__ == '__main__':
    main()
