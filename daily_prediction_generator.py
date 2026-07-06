#!/usr/bin/env python3
"""
Daily Prediction Generator - PERMANENT FIX
===========================================
This script ensures predictions are ALWAYS saved for upcoming games
BEFORE they are played, preventing the missing prediction issue.

Run this daily via cron or scheduler:
0 9 * * * cd /path/to/SportStatsAPI && python3 daily_prediction_generator.py
"""

import sqlite3
from datetime import datetime, timedelta
import logging
from NHL77v1 import get_upcoming_predictions, get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def save_predictions_for_sport(sport, days_ahead=7):
    """
    Generate and save predictions for upcoming games in the next N days.
    This ensures predictions exist BEFORE games are played.
    """
    logger.info(f"Generating predictions for {sport} (next {days_ahead} days)...")
    
    try:
        # Get all predictions (includes upcoming games)
        all_predictions = get_upcoming_predictions(sport, days=365)
        
        # Filter to only upcoming games (no scores yet) in next N days
        today = datetime.now()
        cutoff = today + timedelta(days=days_ahead)
        
        upcoming = []
        for pred in all_predictions:
            # Skip if game has scores (already played)
            if pred.get('home_score') is not None:
                continue
            
            # Check if within date range
            try:
                game_date = datetime.strptime(pred['game_date'], '%Y-%m-%d')
                if today <= game_date <= cutoff:
                    upcoming.append(pred)
            except:
                continue
        
        logger.info(f"Found {len(upcoming)} upcoming {sport} games to save predictions for")
        
        if not upcoming:
            return 0
        
        # Save to database
        conn = get_db_connection()
        cursor = conn.cursor()
        saved_count = 0
        updated_count = 0
        
        for pred in upcoming:
            game_id = pred.get('game_id')
            if not game_id:
                logger.warning(f"Skipping prediction without game_id: {pred.get('away_team_id')} @ {pred.get('home_team_id')}")
                continue
            
            # Check if prediction already exists
            existing = cursor.execute(
                'SELECT id, locked FROM predictions WHERE game_id = ? AND sport = ?',
                (game_id, sport)
            ).fetchone()
            
            if existing:
                # Only update if not locked
                if existing['locked'] == 0:
                    cursor.execute('''
                        UPDATE predictions 
                        SET elo_home_prob = ?, xgboost_home_prob = ?, 
                            logistic_home_prob = ?, win_probability = ?
                        WHERE id = ?
                    ''', (
                        pred['elo_prob'] / 100.0,
                        pred['xgb_prob'] / 100.0,
                        pred['cat_prob'] / 100.0,
                        pred['ensemble_prob'] / 100.0,
                        existing['id']
                    ))
                    updated_count += 1
                    logger.debug(f"Updated prediction for {pred['away_team_id']} @ {pred['home_team_id']}")
            else:
                # Insert new prediction with locked=1
                try:
                    cursor.execute('''
                        INSERT INTO predictions (
                            game_id, sport, league, game_date, 
                            home_team_id, away_team_id,
                            elo_home_prob, xgboost_home_prob, 
                            logistic_home_prob, win_probability,
                            locked, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
                    ''', (
                        game_id, sport, sport, pred['game_date'],
                        pred['home_team_id'], pred['away_team_id'],
                        pred['elo_prob'] / 100.0,
                        pred['xgb_prob'] / 100.0,
                        pred['cat_prob'] / 100.0,
                        pred['ensemble_prob'] / 100.0
                    ))
                    saved_count += 1
                    logger.info(f"Saved NEW prediction: {pred['away_team_id']} @ {pred['home_team_id']} on {pred['game_date']}")
                except Exception as e:
                    logger.error(f"Error saving prediction for {game_id}: {e}")
        
        conn.commit()
        conn.close()
        
        logger.info(f"{sport}: Saved {saved_count} new predictions, updated {updated_count} existing predictions")
        return saved_count + updated_count
        
    except Exception as e:
        logger.error(f"Error generating predictions for {sport}: {e}")
        return 0


def backfill_missing_predictions():
    """
    Backfill predictions for any games that already have scores but no predictions.
    This fixes historical data gaps.
    """
    logger.info("Checking for games with scores but no predictions (backfill)...")
    
    sports = ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAB', 'NCAAF', 'WNBA']
    total_backfilled = 0
    
    for sport in sports:
        try:
            # Generate all predictions
            all_predictions = get_upcoming_predictions(sport, days=365)
            
            # Filter to completed games (have scores) without predictions in DB
            conn = get_db_connection()
            cursor = conn.cursor()
            
            backfill_count = 0
            for pred in all_predictions:
                # Must have scores and game_id
                if pred.get('home_score') is None or not pred.get('game_id'):
                    continue
                
                # Check if prediction exists (try game_id OR date+teams fallback)
                existing = cursor.execute('''
                    SELECT id FROM predictions 
                    WHERE sport = ? AND (
                        game_id = ? 
                        OR (
                            date(game_date) = date(?)
                            AND home_team_id = ?
                            AND away_team_id = ?
                        )
                    )
                ''', (sport, pred['game_id'], pred['game_date'], pred['home_team_id'], pred['away_team_id'])).fetchone()
                
                if not existing:
                    # Need to backfill this one
                    try:
                        cursor.execute('''
                            INSERT INTO predictions (
                                game_id, sport, league, game_date, 
                                home_team_id, away_team_id,
                                elo_home_prob, xgboost_home_prob, 
                                logistic_home_prob, win_probability,
                                locked, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
                        ''', (
                            pred['game_id'], sport, sport, pred['game_date'],
                            pred['home_team_id'], pred['away_team_id'],
                            pred['elo_prob'] / 100.0,
                            pred['xgb_prob'] / 100.0,
                            pred['cat_prob'] / 100.0,
                            pred['ensemble_prob'] / 100.0
                        ))
                        backfill_count += 1
                        logger.info(f"BACKFILLED {sport}: {pred['away_team_id']} @ {pred['home_team_id']} ({pred['game_date']})")
                    except Exception as e:
                        logger.error(f"Error backfilling {sport} {pred['game_id']}: {e}")
            
            conn.commit()
            conn.close()
            
            if backfill_count > 0:
                logger.info(f"{sport}: Backfilled {backfill_count} missing predictions")
                total_backfilled += backfill_count
                
        except Exception as e:
            logger.error(f"Error backfilling {sport}: {e}")
    
    return total_backfilled


if __name__ == '__main__':
    logger.info("="*60)
    logger.info("Daily Prediction Generator - Starting")
    logger.info("="*60)
    
    # ===== SECTION: Active sports for nightly generation =====
    # Include MLB + WNBA so the live summer sports get predictions logged
    # nightly (not only when someone opens the picks page). Their prior absence
    # left recent slates empty, which made the homepage "last night" card and
    # results drift to weeks-old games during the summer.
    active_sports = ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAB', 'NCAAF', 'WNBA']
    
    total_saved = 0
    
    # Generate predictions for upcoming games (next 7 days)
    for sport in active_sports:
        count = save_predictions_for_sport(sport, days_ahead=7)
        total_saved += count
    
    # Backfill any gaps (completed games without predictions)
    backfilled = backfill_missing_predictions()
    
    logger.info("="*60)
    logger.info(f"Daily Prediction Generator - Complete")
    logger.info(f"Total predictions saved/updated: {total_saved}")
    logger.info(f"Total predictions backfilled: {backfilled}")
    logger.info("="*60)
