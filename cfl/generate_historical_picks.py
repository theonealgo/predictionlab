#!/usr/bin/env python3
"""
Generate picks for historical dates that were missed
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

def generate_picks_for_date_range(sport, start_date, end_date):
    """Generate picks for a specific date range (including final games)"""
    
    conn = sqlite3.connect('sports_predictions_original.db')
    cursor = conn.cursor()
    
    # Get games in date range (including final games)
    cursor.execute("""
        SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
               bl.spread, bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? AND g.game_date >= ? AND g.game_date <= ?
        ORDER BY g.game_date ASC
    """, (sport, start_date, end_date))
    
    games = cursor.fetchall()
    conn.close()
    
    if not games:
        return 0, 0, 0
    
    ats = ATSSystem()
    
    ml_picks = []
    spread_picks = []
    total_picks = []
    
    for game in games:
        game_id, date, home, away, spread, total = game
        
        # Get team records
        home_rec = ats.get_team_records(sport, home)
        away_rec = ats.get_team_records(sport, away)
        
        home_win_pct = home_rec['win_pct']
        away_win_pct = away_rec['win_pct']
        home_ats = home_rec['ats_pct']
        away_ats = away_rec['ats_pct']
        
        # Moneyline picks (>61% or <31%)
        if home_win_pct > 0.61:
            ml_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': home})
        elif away_win_pct > 0.61:
            ml_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': away})
        elif home_win_pct < 0.31 and home_win_pct > 0:
            ml_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': away})
        elif away_win_pct < 0.31 and away_win_pct > 0:
            ml_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': home})
        
        # Spread picks
        if spread and home_ats > 0.61:
            spread_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': home, 'model_spread': spread})
        elif spread and away_ats > 0.61:
            spread_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': away, 'model_spread': -spread if spread else 0})
        elif spread and home_ats < 0.31 and home_ats > 0:
            spread_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': away, 'model_spread': -spread if spread else 0})
        elif spread and away_ats < 0.31 and away_ats > 0:
            spread_picks.append({'game_id': game_id, 'game_date': date, 'pick_team': home, 'model_spread': spread})
        
        # Total picks (>60% over or <40% over)
        if total:
            home_over = home_rec['over_wins']
            home_under = home_rec['under_wins']
            away_over = away_rec['over_wins']
            away_under = away_rec['under_wins']
            
            home_total_games = home_over + home_under
            away_total_games = away_over + away_under
            
            home_over_pct = home_over / home_total_games if home_total_games > 0 else 0
            away_over_pct = away_over / away_total_games if away_total_games > 0 else 0
            
            # Both teams over 60% = OVER
            if home_over_pct > 0.60 and away_over_pct > 0.60:
                total_picks.append({'game_id': game_id, 'game_date': date, 'pick_type': 'OVER', 'model_total': total})
            # Both teams under 40% = UNDER
            elif home_over_pct < 0.40 and away_over_pct < 0.40:
                total_picks.append({'game_id': game_id, 'game_date': date, 'pick_type': 'UNDER', 'model_total': total})
    
    # Save picks
    ml_saved = save_picks_to_db(ml_picks, 'MONEYLINE', sport)
    spread_saved = save_picks_to_db(spread_picks, 'SPREAD', sport)
    total_saved = save_picks_to_db(total_picks, 'TOTAL', sport)
    
    return ml_saved, spread_saved, total_saved

def main():
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Generating Historical Picks")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    # Generate picks for Nov 16-18
    for sport in ['NBA', 'NHL', 'NFL']:
        print(f"\n{Fore.YELLOW}{sport}:{Style.RESET_ALL}")
        ml, spread, total = generate_picks_for_date_range(sport, '2025-11-16', '2025-11-18')
        print(f"  {Fore.GREEN}✓ Moneyline: {ml} picks saved{Style.RESET_ALL}")
        print(f"  {Fore.GREEN}✓ Spread: {spread} picks saved{Style.RESET_ALL}")
        print(f"  {Fore.GREEN}✓ Totals: {total} picks saved{Style.RESET_ALL}")
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete - Historical picks generated")
    print(f"{'='*60}{Style.RESET_ALL}\n")

if __name__ == "__main__":
    main()
