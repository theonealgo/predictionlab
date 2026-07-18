# NHL77FINAL.py

import requests
from datetime import datetime, timedelta
import json
import logging
from flask import request, redirect, url_for, render_template_string
from app import app, logger, get_db_connection, _traffic_now, log_site_visit
from sports import NHL as _nhl_sport
from prediction_system_v2.base_models import calculate_efficiency

# Constants and configurations
NHL_API_URL = "https://api.example.com/nhl"
NHL_SEASON_START = datetime(2023, 10, 1)
NHL_SEASON_END = datetime(2024, 6, 30)

# Helper functions
def _fetch_nhl_games(check_date):
    """Fetch NHL games from the API."""
    url = f"{NHL_API_URL}/games"
    params = {
        'date': check_date.strftime('%Y-%m-%d')
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    events = data.get('events', [])
    
    for event in events:
        competition = event.get('competitions', [{}])[0]
        competitors = competition.get('competitors', [])
        
        if len(competitors) != 2:
            continue
        
        home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
        away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
        
        if not home or not away:
            continue
        
        home_team = home.get('team', {}).get('displayName', '')
        away_team = away.get('team', {}).get('displayName', '')
        
        status_info = event.get('status', {}).get('type', {})
        status_name = status_info.get('name', 'scheduled')
        
        if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_OT2']:
            continue
        
        api_games_raw.append({
            'home_team_name': home_team,
            'away_team_name': away_team,
            'game_date': check_date.strftime('%Y-%m-%d'),
        })
    api_games = api_games_raw
    return api_games

@app.route('/sport/<sport>/results')
def sport_results(sport):
    """Show results for a sport."""
    if sport not in SPORTS:
        return "Sport not found", 404
    
    check_date = _traffic_now()
    
    try:
        predictions = get_upcoming_predictions(sport.upper())
        
        # Convert to simple JSON format for frontend
        picks = []
        for pred in predictions:
            picks.append({
                'date': pred['game_date'],
                'matchup': f"{pred['away_team_id']} @ {pred['home_team_id']}",
                'homeTeam': pred['home_team_id'],
                'awayTeam': pred['away_team_id'],
                'pick': pred['predicted_winner'],
                'winPercent': pred['ensemble_prob'],
                'edge': pred.get('elo_prob'),
                'xsharp': pred.get('xgb_prob'),
                'grinder2': pred.get('glicko2_prob'),
                'takedown': pred.get('trueskill_prob')
            })
        
        return render_template_string(
            RESULTS_TEMPLATE,
            page=sport,
            sport=sport,
            sport_info=SPORTS[sport], sport_bg_image=SPORT_BG_IMAGES.get(sport, ''),
            picks=picks,
            count=len(picks)
        )
    except Exception as e:
        logger.error(f"Error in results endpoint for {sport}: {e}")
        return jsonify({'error': str(e)}), 500

# Other routes and functions...
