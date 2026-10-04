#!/usr/bin/env python3
"""
Regenerate System Picks
Uses ATS system to generate and save picks for all sports
"""

import sqlite3
from ats_system import ATSSystem
from colorama import Fore, Style, init

init(autoreset=True)

def save_picks_to_db(picks, pick_type, sport):
    """Save picks to system_picks table"""
    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()
    
    saved_count = 0
    
    for pick in picks:
        game_id = pick['game_id']
        game_date = pick['game_date']
        pick_team = pick.get('pick_team')
        
        if pick_type == 'MONEYLINE':
            pick_value = None
        elif pick_type == 'SPREAD':
            pick_value = pick.get('model_spread')
        else:  # OVER/UNDER
            pick_type_actual = pick.get('pick_type', 'OVER')
            pick_value = pick.get('model_total')
            
            # For totals, insert with OVER or UNDER as pick_type
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO system_picks 
                    (sport, game_id, game_date, pick_type, pick_team, pick_value)
                    VALUES (?, ?, ?, ?, NULL, ?)
                """, (sport, game_id, game_date, pick_type_actual, pick_value))
                saved_count += 1
            except Exception as e:
                print(f"  {Fore.RED}Error saving total pick: {e}{Style.RESET_ALL}")
            continue
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO system_picks 
                (sport, game_id, game_date, pick_type, pick_team, pick_value)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sport, game_id, game_date, pick_type, pick_team, pick_value))
            saved_count += 1
        except Exception as e:
            print(f"  {Fore.RED}Error saving pick: {e}{Style.RESET_ALL}")
    
    conn.commit()
    conn.close()
    return saved_count

def main():
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Regenerating System Picks")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    ats = ATSSystem()
    
    for sport in ['NBA', 'NFL', 'NHL', 'NCAAF', 'NCAAB']:
        print(f"\n{Fore.YELLOW}{sport}:{Style.RESET_ALL}")
        
        # Generate picks for next 7 days
        ml_picks = ats.generate_moneyline_picks(sport, days_ahead=7)
        spread_picks = ats.generate_spread_picks(sport, days_ahead=7)
        total_picks = ats.generate_total_picks(sport, days_ahead=7)
        
        # Save to database
        ml_saved = save_picks_to_db(ml_picks, 'MONEYLINE', sport)
        spread_saved = save_picks_to_db(spread_picks, 'SPREAD', sport)
        total_saved = save_picks_to_db(total_picks, 'TOTAL', sport)
        
        print(f"  {Fore.GREEN}✓ Moneyline: {ml_saved} picks saved{Style.RESET_ALL}")
        print(f"  {Fore.GREEN}✓ Spread: {spread_saved} picks saved{Style.RESET_ALL}")
        print(f"  {Fore.GREEN}✓ Totals: {total_saved} picks saved{Style.RESET_ALL}")
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete - All picks regenerated")
    print(f"{'='*60}{Style.RESET_ALL}\n")

if __name__ == "__main__":
    main()
