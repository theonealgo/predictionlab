import sqlite3
import os

def run_cleanup():
    # 1. Identify the database file
    # Most of these setups use 'sports_predictions.db'. 
    # If yours is named differently, change the name below.
    db_name = 'sports_predictions.db'
    
    if not os.path.exists(db_name):
        print(f"❌ Error: Could not find '{db_name}' in this folder.")
        return

    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        
        print(f"Connected to {db_name}. Starting cleanup...")

        # FIX 1: The "Year Swap"
        # This finds any December games incorrectly labeled as 2026 
        # and moves them back to 2025.
        cursor.execute("""
            UPDATE games 
            SET game_date = REPLACE(game_date, '2026-12-', '2025-12-')
            WHERE game_date LIKE '2026-12-%'
        """)
        print(f"✅ Fixed {cursor.rowcount} games that had the wrong year (2026 -> 2025).")

        # FIX 2: The "Dec 17 Wall"
        # If games from mid-December are marked as 'completed' but have no scores,
        # we reset them so the scraper tries to get the scores again.
        cursor.execute("""
            UPDATE games 
            SET home_score = NULL, away_score = NULL
            WHERE (home_score = 0 AND away_score = 0)
            AND date(game_date) < date('now')
        """)
        print(f"✅ Reset {cursor.rowcount} games with empty scores to allow re-scraping.")

        # FIX 3: Cleanup Duplicate Predictions
        # Sometimes a bug creates two predictions for the same game.
        cursor.execute("""
            DELETE FROM predictions 
            WHERE rowid NOT IN (
                SELECT MIN(rowid) 
                FROM predictions 
                GROUP BY sport, game_date, home_team_id, away_team_id
            )
        """)
        print(f"✅ Removed {cursor.rowcount} duplicate predictions.")

        conn.commit()
        conn.close()
        print("\n✨ Database cleanup complete! You can now restart your main website.")

    except Exception as e:
        print(f"❌ An error occurred: {e}")

if __name__ == "__main__":
    run_cleanup()