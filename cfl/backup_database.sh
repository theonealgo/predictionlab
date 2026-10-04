#!/bin/bash
# Automated Database Backup Script
# Backs up system_picks data to prevent any accidental loss

cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"

BACKUP_DIR="database_backups"
mkdir -p "$BACKUP_DIR"

# Create timestamped backup
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/system_picks_backup_$TIMESTAMP.sql"

# Export system_picks table
sqlite3 sports_predictions_original.db ".dump system_picks" > "$BACKUP_FILE"

# Keep only last 30 backups (delete older ones)
ls -t "$BACKUP_DIR"/system_picks_backup_*.sql | tail -n +31 | xargs -r rm --

# Also create a daily backup of full database
DAILY_BACKUP="$BACKUP_DIR/full_db_$(date +%Y%m%d).db"
if [ ! -f "$DAILY_BACKUP" ]; then
    cp sports_predictions_original.db "$DAILY_BACKUP"
    echo "✓ Full database backed up to $DAILY_BACKUP"
fi

echo "✓ System picks backed up to $BACKUP_FILE"
echo "✓ $(sqlite3 sports_predictions_original.db "SELECT COUNT(*) FROM system_picks") total picks backed up"
