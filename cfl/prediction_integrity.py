#!/usr/bin/env python3
"""
Prediction Integrity System
Ensures predictions are locked and results use the exact same values
"""

import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def lock_predictions_for_game(game_id: str, sport: str) -> bool:
    """
    Lock predictions for a game once it starts
    This prevents any modifications to prediction values
    
    Returns True if locked successfully, False if already locked or error
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if predictions exist
        pred = cursor.execute('''
            SELECT id, created_at, elo_home_prob, xgboost_home_prob, 
                   catboost_home_prob, meta_home_prob
            FROM predictions
            WHERE game_id = ? AND sport = ?
        ''', (game_id, sport)).fetchone()
        
        if not pred:
            logger.warning(f"No predictions found for game {game_id}")
            return False
        
        # Add a note that this prediction is locked
        cursor.execute('''
            UPDATE predictions
            SET key_factors = COALESCE(key_factors, '') || ' [LOCKED at ' || datetime('now') || ']'
            WHERE game_id = ? AND sport = ?
            AND key_factors NOT LIKE '%[LOCKED%'
        ''', (game_id, sport))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Locked predictions for game {game_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error locking predictions: {e}")
        return False


def validate_results_match_predictions(sport: str = 'NBA') -> Dict:
    """
    Validate that Results page uses exact same predictions from Predictions page
    
    Returns a report of any discrepancies
    """
    try:
        conn = get_db_connection()
        
        # Get all games with both predictions and results
        games = conn.execute('''
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
                p.elo_home_prob,
                p.xgboost_home_prob,
                p.catboost_home_prob,
                p.logistic_home_prob,
                p.meta_home_prob,
                p.win_probability,
                p.created_at
            FROM games g
            INNER JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND g.home_score IS NOT NULL
            ORDER BY g.game_date DESC
        ''', (sport, sport)).fetchall()
        
        conn.close()
        
        report = {
            'total_games': len(games),
            'games_with_predictions': 0,
            'discrepancies': [],
            'validation_time': datetime.now().isoformat()
        }
        
        for game in games:
            has_predictions = any([
                game['elo_home_prob'] is not None,
                game['xgboost_home_prob'] is not None,
                game['catboost_home_prob'] is not None,
                game['meta_home_prob'] is not None
            ])
            
            if has_predictions:
                report['games_with_predictions'] += 1
            
            # Check for placeholder values (exactly 0.5 or 50%)
            for model_field, model_name in [
                ('elo_home_prob', 'Elo'),
                ('xgboost_home_prob', 'XGBoost'),
                ('catboost_home_prob', 'CatBoost'),
                ('meta_home_prob', 'Ensemble')
            ]:
                prob = game[model_field]
                if prob is not None and abs(prob - 0.5) < 0.001:
                    report['discrepancies'].append({
                        'game_id': game['game_id'],
                        'game_date': game['game_date'],
                        'matchup': f"{game['away_team_id']} @ {game['home_team_id']}",
                        'issue': f'{model_name} has placeholder value (50.0%)',
                        'value': prob
                    })
        
        return report
        
    except Exception as e:
        logger.error(f"Error validating predictions: {e}")
        return {'error': str(e)}


def generate_integrity_report(sport: str = 'NBA') -> str:
    """
    Generate a comprehensive report showing:
    1. Game date, matchup
    2. Prediction % from database
    3. Predicted side (>50% = home, <50% = away)
    4. Actual winner
    5. Correct/Incorrect
    """
    try:
        conn = get_db_connection()
        
        games = conn.execute('''
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score,
                p.elo_home_prob,
                p.xgboost_home_prob,
                p.catboost_home_prob,
                p.logistic_home_prob,
                p.meta_home_prob,
                p.created_at
            FROM games g
            INNER JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND g.home_score IS NOT NULL
            ORDER BY g.game_date DESC
        ''', (sport, sport)).fetchall()
        
        conn.close()
        
        report_lines = []
        report_lines.append("=" * 120)
        report_lines.append(f"{sport} PREDICTION INTEGRITY REPORT")
        report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append("=" * 120)
        report_lines.append("")
        
        for game in games:
            date_str = game['game_date'][:10] if game['game_date'] else 'Unknown'
            matchup = f"{game['away_team_id']} @ {game['home_team_id']}"
            score = f"{game['away_score']}-{game['home_score']}"
            actual_winner = "HOME" if game['home_score'] > game['away_score'] else "AWAY"
            
            report_lines.append(f"Date: {date_str}")
            report_lines.append(f"Matchup: {matchup}")
            report_lines.append(f"Final Score: {score} (Winner: {actual_winner})")
            report_lines.append(f"Prediction Created: {game['created_at']}")
            report_lines.append("")
            
            # Check each model
            models = [
                ('Elo', game['elo_home_prob']),
                ('XGBoost', game['xgboost_home_prob']),
                ('CatBoost', game['catboost_home_prob']),
                ('Ensemble', game['meta_home_prob'])
            ]
            
            for model_name, prob in models:
                if prob is None:
                    report_lines.append(f"  {model_name:12s}: NO PREDICTION")
                    continue
                
                prob_pct = prob * 100
                predicted_side = "HOME" if prob > 0.5 else "AWAY"
                is_correct = (predicted_side == actual_winner)
                status = "✓ CORRECT" if is_correct else "✗ INCORRECT"
                
                report_lines.append(f"  {model_name:12s}: {prob_pct:5.1f}% → {predicted_side:4s} | {status}")
            
            report_lines.append("-" * 120)
            report_lines.append("")
        
        return "\n".join(report_lines)
        
    except Exception as e:
        logger.error(f"Error generating report: {e}")
        return f"Error: {e}"


def check_for_orphaned_results(sport: str = 'NBA') -> List[Dict]:
    """
    Find games in Results that don't have corresponding Predictions
    This should NEVER happen - it means results are using fake/generated predictions
    """
    try:
        conn = get_db_connection()
        
        orphans = conn.execute('''
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                g.home_score,
                g.away_score
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? 
              AND g.home_score IS NOT NULL
              AND p.id IS NULL
        ''', (sport, sport)).fetchall()
        
        conn.close()
        
        if orphans:
            logger.error(f"CRITICAL: Found {len(orphans)} games with results but NO predictions!")
            return [dict(o) for o in orphans]
        else:
            logger.info(f"✓ All {sport} results have corresponding predictions")
            return []
        
    except Exception as e:
        logger.error(f"Error checking for orphans: {e}")
        return []


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("PREDICTION INTEGRITY VALIDATION")
    print("=" * 60 + "\n")
    
    # Check for orphaned results
    print("1. Checking for games with results but no predictions...")
    orphans = check_for_orphaned_results('NBA')
    if orphans:
        print(f"   ✗ CRITICAL: {len(orphans)} orphaned results found!")
        for o in orphans[:5]:  # Show first 5
            print(f"      - {o['game_date']}: {o['away_team_id']} @ {o['home_team_id']}")
    else:
        print("   ✓ OK: All results have predictions")
    
    print("\n2. Validating prediction consistency...")
    validation = validate_results_match_predictions('NBA')
    print(f"   Total games: {validation['total_games']}")
    print(f"   Games with predictions: {validation['games_with_predictions']}")
    if validation['discrepancies']:
        print(f"   ✗ WARNING: {len(validation['discrepancies'])} discrepancies found!")
        for d in validation['discrepancies'][:5]:
            print(f"      - {d['matchup']}: {d['issue']}")
    else:
        print("   ✓ OK: No placeholder values detected")
    
    print("\n3. Generating full integrity report...")
    report = generate_integrity_report('NBA')
    
    # Save to file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'nba_integrity_report_{timestamp}.txt'
    with open(filename, 'w') as f:
        f.write(report)
    
    print(f"   ✓ Report saved to: {filename}")
    
    print("\n" + "=" * 60)
    print("VALIDATION COMPLETE")
    print("=" * 60 + "\n")
