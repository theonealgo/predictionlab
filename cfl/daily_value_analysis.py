#!/usr/bin/env python3
"""
Daily Value Betting Analysis - All Sports
Runs comprehensive value analysis for NHL, NBA, and NFL
"""

import sys
from value_predictor import ValuePredictor
from datetime import datetime

def run_daily_analysis():
    """Run value analysis for all active sports"""
    
    predictor = ValuePredictor()
    
    print("\n" + "="*80)
    print(f"🎯 DAILY VALUE BETTING ANALYSIS - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*80)
    
    sports = ['NHL', 'NBA', 'NFL']
    all_results = {}
    
    for sport in sports:
        print(f"\n{'='*80}")
        print(f"🏒 {sport} ANALYSIS" if sport == 'NHL' else f"🏀 {sport} ANALYSIS" if sport == 'NBA' else f"🏈 {sport} ANALYSIS")
        print(f"{'='*80}")
        
        try:
            enhanced = predictor.enhance_predictions(sport)
            all_results[sport] = enhanced
            
            if not enhanced:
                print(f"❌ No upcoming {sport} games found.\n")
                continue
            
            predictor.print_recommendations(enhanced, sport)
            
            # Summary stats
            value_bets = [p for p in enhanced if p['recommendation'] != 'PASS']
            high_conf = [p for p in value_bets if p['confidence'] == 'HIGH']
            med_conf = [p for p in value_bets if p['confidence'] == 'MEDIUM']
            low_conf = [p for p in value_bets if p['confidence'] == 'LOW']
            
            print(f"📊 {sport} Summary:")
            print(f"   Total Games Analyzed: {len(enhanced)}")
            print(f"   Value Bets Found: {len(value_bets)}")
            print(f"   High Confidence: {len(high_conf)}")
            print(f"   Medium Confidence: {len(med_conf)}")
            print(f"   Low Confidence: {len(low_conf)}")
            print()
            
        except Exception as e:
            print(f"❌ Error analyzing {sport}: {e}\n")
            continue
    
    # Overall summary
    print("\n" + "="*80)
    print("📈 OVERALL SUMMARY")
    print("="*80)
    
    total_games = sum(len(all_results.get(s, [])) for s in sports)
    total_value_bets = sum(len([p for p in all_results.get(s, []) if p['recommendation'] != 'PASS']) for s in sports)
    
    print(f"Total Games: {total_games}")
    print(f"Total Value Bets: {total_value_bets}")
    print(f"Hit Rate Target: 55%+ (to be profitable at these edges)")
    print()
    
    # Top 5 plays across all sports
    all_value_bets = []
    for sport, results in all_results.items():
        for pred in results:
            if pred['recommendation'] != 'PASS':
                pred['sport'] = sport
                all_value_bets.append(pred)
    
    all_value_bets.sort(key=lambda x: x['edge'], reverse=True)
    
    if all_value_bets:
        print("🔥 TOP 5 VALUE PLAYS (All Sports):")
        print("="*80)
        for i, bet in enumerate(all_value_bets[:5], 1):
            sport_icon = '🏒' if bet['sport'] == 'NHL' else '🏀' if bet['sport'] == 'NBA' else '🏈'
            print(f"{i}. {sport_icon} {bet['sport']}: {bet['bet_team']} ({bet['edge']:.1f}% edge, {bet['confidence']} confidence)")
            print(f"   {bet['away_team']} @ {bet['home_team']}")
            
            if bet['bet_team'] == bet['home_team']:
                print(f"   Best Line: {bet['best_home_ml']}")
            else:
                print(f"   Best Line: {bet['best_away_ml']}")
            print()
    
    print("="*80)
    print("💡 Remember: These are VALUE bets, not guarantees. Manage your bankroll!")
    print("="*80 + "\n")


if __name__ == '__main__':
    run_daily_analysis()
