#!/usr/bin/env python3
"""
Test Threshold-Based Pick Generation
=====================================
Quick test to verify the new threshold logic works correctly.
"""

from ats_system import ATSSystem
from colorama import Fore, Style, init

init(autoreset=True)


def test_picks(sport='NBA'):
    """Test pick generation for a sport"""
    print(f"\n{Fore.CYAN}{'='*70}")
    print(f"Testing {sport} Threshold-Based Picks")
    print(f"{'='*70}{Style.RESET_ALL}\n")
    
    ats = ATSSystem()
    
    # Test ML picks
    print(f"{Fore.YELLOW}MONEYLINE PICKS (win% >61% or <31%):{Style.RESET_ALL}")
    ml_picks = ats.generate_moneyline_picks(sport, days_ahead=1)
    if ml_picks:
        for pick in ml_picks:
            team_rec = ats.get_team_records(sport, pick['pick_team'])
            print(f"  {pick['pick_team']:30s} {pick['pick_type']:10s} (Win%: {team_rec['win_pct']:.1%})")
    else:
        print(f"  {Fore.RED}No ML picks found{Style.RESET_ALL}")
    
    # Test ATS picks
    print(f"\n{Fore.YELLOW}SPREAD PICKS (ATS% >61% or <31%):{Style.RESET_ALL}")
    ats_picks = ats.generate_spread_picks(sport, days_ahead=1)
    if ats_picks:
        for pick in ats_picks:
            team_rec = ats.get_team_records(sport, pick['pick_team'])
            print(f"  {pick['pick_team']:30s} {pick['pick_type']:12s} (ATS%: {team_rec['ats_pct']:.1%})")
    else:
        print(f"  {Fore.RED}No spread picks found{Style.RESET_ALL}")
    
    # Test Total picks
    print(f"\n{Fore.YELLOW}TOTAL PICKS (Combined O/U% ≥60%):{Style.RESET_ALL}")
    total_picks = ats.generate_total_picks(sport, days_ahead=1)
    if total_picks:
        for pick in total_picks:
            home = pick['home_team']
            away = pick['away_team']
            home_rec = ats.get_team_records(sport, home)
            away_rec = ats.get_team_records(sport, away)
            
            # Calculate combined percentage
            if pick['pick_type'] == 'OVER':
                combined_w = home_rec['over_wins'] + away_rec['over_wins']
                combined_l = home_rec['under_wins'] + away_rec['under_wins']
            else:
                combined_w = home_rec['under_wins'] + away_rec['under_wins']
                combined_l = home_rec['over_wins'] + away_rec['over_wins']
            
            combined_pct = combined_w / (combined_w + combined_l) if (combined_w + combined_l) > 0 else 0
            
            print(f"  {home} vs {away}")
            print(f"    {pick['pick_type']:6s} {pick['model_total']} (Combined: {combined_pct:.1%}, {combined_w}-{combined_l})")
    else:
        print(f"  {Fore.RED}No total picks found{Style.RESET_ALL}")
    
    print(f"\n{Fore.GREEN}{'='*70}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    test_picks('NBA')
