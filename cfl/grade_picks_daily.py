#!/usr/bin/env python3
"""
Daily pick grading automation.
Fetches final scores from ESPN API and grades all ungraded picks.
Run this daily to keep results up-to-date.
"""
import requests
import sqlite3
from datetime import datetime, timedelta

DB = 'sports_predictions_original.db'

def update_scores_from_espn(sport, league, date_str):
    """Fetch final scores from ESPN and update games table."""
    url = f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates={date_str}'
    try:
        data = requests.get(url, timeout=10).json()
    except Exception as e:
        print(f'Error fetching {league.upper()} for {date_str}: {e}')
        return 0
    
    con = sqlite3.connect(DB)
    cur = con.cursor()
    updated = 0
    
    for evt in data.get('events', []):
        if evt['status']['type']['state'] != 'post':
            continue
        
        comps = evt['competitions'][0]['competitors']
        home_comp = next(c for c in comps if c['homeAway'] == 'home')
        away_comp = next(c for c in comps if c['homeAway'] == 'away')
        
        home_team = home_comp['team']['displayName']
        away_team = away_comp['team']['displayName']
        home_score = int(home_comp['score'])
        away_score = int(away_comp['score'])
        game_date = datetime.strptime(date_str, '%Y%m%d').strftime('%Y-%m-%d')
        
        cur.execute("""
            UPDATE games 
            SET home_score=?, away_score=?, status='final' 
            WHERE sport=? AND home_team_id=? AND away_team_id=? AND game_date=? AND status='scheduled'
        """, (home_score, away_score, league.upper(), home_team, away_team, game_date))
        updated += cur.rowcount
    
    con.commit()
    con.close()
    return updated

def grade_picks():
    """Grade all ungraded picks with finalized games."""
    def grade_pick(pt, team, val, home, away, h, a, spread, total):
        margin = h - a
        if pt == 'MONEYLINE':
            return 'WIN' if ((team == home and h > a) or (team == away and a > h)) else 'LOSS'
        if pt == 'SPREAD' and spread is not None:
            cover = (margin + val) if team == home else (-margin + val)
            if abs(cover) < 0.5:
                return 'PUSH'
            return 'WIN' if cover > 0 else 'LOSS'
        if pt in ('OVER', 'UNDER') and total is not None:
            tot = h + a
            if abs(tot - total) < 0.5:
                return 'PUSH'
            return 'WIN' if (pt == 'OVER' and tot > total) or (pt == 'UNDER' and tot < total) else 'LOSS'
        return None
    
    con = sqlite3.connect(DB)
    cur = con.cursor()
    graded = 0
    
    # Get all final games with ungraded picks
    cur.execute("""
        SELECT DISTINCT g.game_id, g.sport, g.home_team_id, g.away_team_id, g.home_score, g.away_score, bl.spread, bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.status = 'final' 
        AND g.home_score IS NOT NULL 
        AND g.away_score IS NOT NULL
        AND EXISTS (SELECT 1 FROM system_picks WHERE game_id = g.game_id AND result IS NULL)
    """)
    
    for gid, sport, home, away, h, a, spread, total in cur.fetchall():
        cur.execute("""
            SELECT pick_type, pick_team, pick_value 
            FROM system_picks 
            WHERE game_id = ? AND result IS NULL
        """, (gid,))
        
        for pt, team, val in cur.fetchall():
            res = grade_pick(pt, team, val, home, away, h, a, spread, total)
            if res:
                cur.execute("""
                    UPDATE system_picks 
                    SET result = ? 
                    WHERE game_id = ? AND pick_type = ? AND pick_team IS ? AND result IS NULL
                """, (res, gid, pt, team))
                graded += 1
    
    con.commit()
    con.close()
    return graded

def main():
    print(f'=== Daily Grading Run: {datetime.now().strftime("%Y-%m-%d %H:%M")} ===')
    
    # Update scores for last 7 days (to catch any delayed finals)
    sports_config = [
        ('basketball', 'nba'),
        ('hockey', 'nhl'),
        ('football', 'nfl'),
        ('basketball', 'mens-college-basketball'),  # NCAAB
        ('football', 'college-football'),  # NCAAF
    ]
    
    total_updated = 0
    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
        for sport, league in sports_config:
            updated = update_scores_from_espn(sport, league, date)
            total_updated += updated
    
    print(f'Updated {total_updated} game scores')
    
    # Grade all ungraded picks
    graded = grade_picks()
    print(f'Graded {graded} picks')
    print('=== Done ===')

if __name__ == '__main__':
    main()
