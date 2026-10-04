#!/usr/bin/env python3
"""
Fix Results - Grade all ungraded picks and fix daily summaries
"""
import sqlite3
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"


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


def grade_all_ungraded_picks():
    """Grade all picks that are missing results"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Grading All Ungraded Picks")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for sport in ['NBA', 'NFL', 'NHL']:
        print(f"{Fore.YELLOW}{sport}:{Style.RESET_ALL}")
        
        # Get all picks for completed games that don't have results
        cursor.execute("""
            SELECT sp.id, sp.game_id, sp.pick_type, sp.pick_team, sp.pick_value,
                   g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                   g.game_date, bl.spread, bl.total
            FROM system_picks sp
            JOIN games g ON sp.game_id = g.game_id
            LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
            WHERE sp.sport = ? AND g.status = 'final' 
            AND sp.result IS NULL
            AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
            ORDER BY g.game_date DESC
        """, (sport,))
        
        picks = cursor.fetchall()
        graded = 0
        
        for pick_id, game_id, pick_type, pick_team, pick_value, home, away, h_score, a_score, game_date, spread, total in picks:
            result = grade_pick(pick_type, pick_team, home, away, h_score, a_score, spread, total)
            
            if result:
                cursor.execute("""
                    UPDATE system_picks
                    SET result = ?
                    WHERE id = ?
                """, (result, pick_id))
                graded += 1
                print(f"  {game_date} - {game_id}: {pick_type} {pick_team} = {result}")
        
        if graded == 0:
            print(f"  ✓ No ungraded picks")
        else:
            print(f"  {Fore.GREEN}✓ Graded {graded} picks{Style.RESET_ALL}")
        print()
    
    conn.commit()
    conn.close()


def recalculate_daily_summaries():
    """Recalculate daily summary counts from actual results"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Recalculating Daily Summaries")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all dates with results in last 14 days
    cursor.execute("""
        SELECT DISTINCT game_date, sport
        FROM system_picks
        WHERE result IS NOT NULL
        AND date(game_date) >= date('now', '-14 days')
        ORDER BY game_date DESC, sport
    """)
    
    date_sports = cursor.fetchall()
    
    for game_date, sport in date_sports:
        # Count actual W-L by pick type
        cursor.execute("""
            SELECT pick_type, result, COUNT(*) as count
            FROM system_picks
            WHERE sport = ? AND game_date = ? AND result IS NOT NULL
            GROUP BY pick_type, result
        """, (sport, game_date))
        
        results = cursor.fetchall()
        
        ml_w = ml_l = ml_p = 0
        sp_w = sp_l = sp_p = 0
        tot_w = tot_l = tot_p = 0
        
        for pick_type, result, count in results:
            if pick_type == 'MONEYLINE':
                if result == 'WIN': ml_w += count
                elif result == 'LOSS': ml_l += count
                else: ml_p += count
            elif pick_type == 'SPREAD':
                if result == 'WIN': sp_w += count
                elif result == 'LOSS': sp_l += count
                else: sp_p += count
            elif pick_type in ['OVER', 'UNDER']:
                if result == 'WIN': tot_w += count
                elif result == 'LOSS': tot_l += count
                else: tot_p += count
        
        print(f"{game_date} {sport}: ML {ml_w}-{ml_l}-{ml_p} | Spread {sp_w}-{sp_l}-{sp_p} | Total {tot_w}-{tot_l}-{tot_p}")
    
    conn.close()


def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Results Fix Script - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    grade_all_ungraded_picks()
    recalculate_daily_summaries()
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Results Fix Complete")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
