#!/usr/bin/env python3
"""
Team Trends API - Internal JSON Endpoint
==========================================
Returns ML, ATS, and O/U records for all teams in a sport.
No scraping - calculated from our own ESPN data.
"""

import sqlite3
from flask import jsonify

DB_PATH = "sports_predictions_original.db"


def get_team_trends(sport):
    """
    Get team trends for a sport in SportsBettingDime format.
    Returns JSON with team records.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            team_name,
            wins, losses, win_pct,
            ats_wins, ats_losses, ats_pct,
            over_wins, under_wins, over_pct
        FROM team_records
        WHERE sport = ?
        ORDER BY ats_pct DESC
    """, (sport,))
    
    teams = cursor.fetchall()
    conn.close()
    
    if not teams:
        return jsonify({'sport': sport, 'teams': [], 'message': 'No data available'})
    
    # Format as table
    trends = []
    for i, (name, w, l, ml_pct, ats_w, ats_l, ats_pct, over_w, under_w, over_pct) in enumerate(teams, 1):
        trends.append({
            'rank': i,
            'team': name,
            'ml': f"{w}-{l}",
            'ml_pct': round(ml_pct * 100, 1),
            'ats': f"{ats_w}-{ats_l}",
            'ats_pct': round(ats_pct * 100, 1),
            'ou': f"{over_w}-{under_w}",
            'over_pct': round(over_pct * 100, 1)
        })
    
    return jsonify({
        'sport': sport,
        'total_teams': len(teams),
        'teams': trends,
        'source': 'Internal calculation from ESPN data'
    })


# Add this route to NHL77FINAL.py or create standalone Flask app:
"""
@app.route('/api/trends/<sport>')
def api_team_trends(sport):
    return get_team_trends(sport)
"""
