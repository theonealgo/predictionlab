def sport_results(sport):
    if sport not in SPORTS:
        return "Sport not found", 404
    
    check_date = _traffic_now()
    
    try:
        predictions = get_upcoming_predictions(sport.upper())
        
        # Convert to simple JSON format for frontend
        picks = []
        for pred in predictions:
            game_id = pred['game_id']
            game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            
            if not game or game['home_score'] is None or game['away_score'] is None:
                continue
            
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
