#!/usr/bin/env python3
"""
Track Pick Results Daily
Automatically grades picks and updates weekly summaries
"""

import sqlite3
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"


def get_week_number(date_str):
    """Get week number of the year"""
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    return date_obj.isocalendar()[1]


def grade_picks_for_date(sport, date_str):
    """Grade all picks for a specific date"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"\n{Fore.CYAN}Grading {sport} picks for {date_str}{Style.RESET_ALL}")
    
    # Get completed games for this date
    cursor.execute("""
        SELECT g.game_id, g.home_team_id, g.away_team_id, 
               g.home_score, g.away_score,
               bl.spread, bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? AND g.game_date = ? AND g.status = 'final'
        AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
    """, (sport, date_str))
    
    games = cursor.fetchall()
    
    if not games:
        print(f"  No completed games found")
        conn.close()
        return
    
    week_num = get_week_number(date_str)
    graded = 0
    
    for game in games:
        game_id, home, away, h_score, a_score, spread, total = game
        
        # Get picks for this game from system_picks table
        cursor.execute("""
            SELECT pick_type, pick_team, pick_value
            FROM system_picks
            WHERE game_id = ? AND sport = ?
        """, (game_id, sport))
        
        picks = cursor.fetchall()
        
        for pick_type, pick_team, pick_value in picks:
            result = grade_pick(pick_type, pick_team, home, away, h_score, a_score, spread, total)
            
            if result:
                # Update system_picks with result
                cursor.execute("""
                    UPDATE system_picks
                    SET result = ?
                    WHERE game_id = ? AND sport = ? AND pick_type = ?
                """, (result, game_id, sport, pick_type))
                
                graded += 1
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Graded {graded} picks{Style.RESET_ALL}")


def grade_pick(pick_type, pick_team, home, away, h_score, a_score, spread, total):
    """Grade a single pick"""
    actual_margin = h_score - a_score
    actual_total = h_score + a_score
    
    if pick_type == 'MONEYLINE':
        if pick_team == home:
            return 'WIN' if h_score > a_score else 'LOSS'
        else:
            return 'WIN' if a_score > h_score else 'LOSS'
    
    elif pick_type == 'SPREAD':
        if not spread:
            return None
        if pick_team == home:
            cover_margin = actual_margin - spread
        else:
            cover_margin = -actual_margin - spread
        
        if abs(cover_margin) < 0.5:
            return 'PUSH'
        return 'WIN' if cover_margin > 0 else 'LOSS'
    
    elif pick_type in ['OVER', 'UNDER']:
        if not total:
            return None
        if abs(actual_total - total) < 0.5:
            return 'PUSH'
        
        if pick_type == 'OVER':
            return 'WIN' if actual_total > total else 'LOSS'
        else:
            return 'WIN' if actual_total < total else 'LOSS'
    
    return None


def calculate_units(result, odds):
    """Calculate units won/lost based on result and odds"""
    if result == 'PUSH':
        return 0
    if result == 'LOSS':
        return -1
    
    # For wins
    if not odds or odds == 0:
        return 1  # Default even money
    
    if odds > 0:
        return odds / 100
    else:
        return 100 / abs(odds)




def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Daily Results Tracker - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    # Grade yesterday's picks (games have finished and been updated)
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    for sport in ['NBA', 'NFL', 'NHL']:
        grade_picks_for_date(sport, yesterday)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Results tracking complete")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
