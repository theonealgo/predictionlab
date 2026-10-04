#!/usr/bin/env python3
"""
Quick ATS Picks Generator
=========================
Generates betting picks (moneyline, spread, over/under) for all sports.
Optionally exports to CSV for easy viewing.
"""

import argparse
import pandas as pd
from datetime import datetime
from ats_system import ATSSystem


def export_picks_to_csv(all_picks, filename='ats_picks.csv'):
    """Export all picks to a CSV file"""
    rows = []
    
    for sport, picks_by_type in all_picks.items():
        for bet_type, picks in picks_by_type.items():
            for pick in picks:
                rows.append({
                    'Sport': sport,
                    'Date': pick['game_date'],
                    'Matchup': f"{pick['away_team']} @ {pick['home_team']}",
                    'Bet Type': pick['bet_type'],
                    'Pick': pick['pick_team'],
                    'Pick Type': pick['pick_type'],
                    'Details': _get_pick_details(pick),
                    'Confidence': f"{pick['confidence']:.0%}",
                })
    
    df = pd.DataFrame(rows)
    df = df.sort_values(['Sport', 'Date'])
    df.to_csv(filename, index=False)
    print(f"\n✅ Exported {len(rows)} picks to {filename}")
    return df


def _get_pick_details(pick):
    """Format pick-specific details"""
    if pick['bet_type'] == 'MONEYLINE':
        return f"Win Prob: {pick['win_probability']:.1%}"
    elif pick['bet_type'] == 'SPREAD':
        return f"Spread: {pick['model_spread']:+.1f}"
    elif pick['bet_type'] == 'TOTAL':
        return f"Total: {pick['model_total']:.1f}"
    return ""


def main():
    parser = argparse.ArgumentParser(description='Generate ATS betting picks')
    parser.add_argument('--sports', nargs='+', default=['NBA', 'NHL', 'NFL'],
                       help='Sports to generate picks for (default: NBA NHL NFL)')
    parser.add_argument('--days', type=int, default=7,
                       help='Days ahead to look for games (default: 7)')
    parser.add_argument('--csv', type=str, default=None,
                       help='Export picks to CSV file (e.g., picks.csv)')
    parser.add_argument('--show-ats', action='store_true',
                       help='Show ATS records and over/under trends')
    parser.add_argument('--lookback', type=int, default=180,
                       help='Days to look back for ATS/O-U records (default: 180)')
    
    args = parser.parse_args()
    
    ats = ATSSystem()
    all_picks = {}
    
    print("\n" + "="*80)
    print(f"ATS BETTING PICKS - Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*80)
    
    for sport in args.sports:
        print(f"\n{'='*80}")
        print(f"{sport}")
        print(f"{'='*80}")
        
        # Show ATS trends if requested
        if args.show_ats:
            print("\n📊 ATS RECORDS (Last 180 days)")
            print("-"*80)
            ats_records = ats.calculate_ats_records(sport, lookback_days=args.lookback)
            if not ats_records.empty:
                print(ats_records.head(10).to_string(index=False))
            
            print("\n📈 OVER/UNDER TRENDS")
            print("-"*80)
            ou_records = ats.calculate_over_under_records(sport, lookback_days=args.lookback)
            if not ou_records.empty:
                print("\nTop 5 OVER teams:")
                over_sorted = ou_records.sort_values('over_pct', ascending=False)
                print(over_sorted.head(5)[['team', 'over_pct', 'avg_total']].to_string(index=False))
                
                print("\nTop 5 UNDER teams:")
                under_sorted = ou_records.sort_values('over_pct', ascending=True)
                print(under_sorted.head(5)[['team', 'over_pct', 'avg_total']].to_string(index=False))
        
        # Generate picks
        picks = ats.get_all_picks(sport, days_ahead=args.days)
        all_picks[sport] = picks
        
        # Display summary
        ml_count = len(picks['moneyline'])
        spread_count = len(picks['spread'])
        total_count = len(picks['totals'])
        
        print(f"\n📋 PICKS SUMMARY:")
        print(f"   💰 Moneyline: {ml_count}")
        print(f"   📊 Spread: {spread_count}")
        print(f"   🎯 Totals: {total_count}")
        print(f"   Total: {ml_count + spread_count + total_count}")
        
        # Show picks
        if ml_count > 0:
            print(f"\n💰 MONEYLINE PICKS:")
            for pick in picks['moneyline'][:5]:  # Show first 5
                print(f"   {pick['game_date']}: {pick['pick_team']} ({pick['win_probability']:.0%})")
            if ml_count > 5:
                print(f"   ... and {ml_count - 5} more")
        
        if spread_count > 0:
            print(f"\n📊 SPREAD PICKS:")
            for pick in picks['spread'][:5]:
                print(f"   {pick['game_date']}: {pick['pick_team']} {pick['model_spread']:+.1f}")
            if spread_count > 5:
                print(f"   ... and {spread_count - 5} more")
        
        if total_count > 0:
            print(f"\n🎯 OVER/UNDER PICKS:")
            for pick in picks['totals'][:5]:
                print(f"   {pick['game_date']}: {pick['pick_type']} {pick['model_total']:.1f}")
            if total_count > 5:
                print(f"   ... and {total_count - 5} more")
    
    # Export to CSV if requested
    if args.csv:
        export_picks_to_csv(all_picks, args.csv)
    
    print(f"\n{'='*80}")
    print("✅ Done!")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
