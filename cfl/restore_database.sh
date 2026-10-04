#!/bin/bash
# Database Restore Script
# Restores system_picks from a backup file

cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"

BACKUP_DIR="database_backups"

if [ $# -eq 0 ]; then
    echo "Usage: ./restore_database.sh <backup_file>"
    echo ""
    echo "Available backups:"
    ls -lht "$BACKUP_DIR"/system_picks_backup_*.sql | head -10
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "WARNING: This will restore system_picks from:"
echo "  $BACKUP_FILE"
echo ""
read -p "Are you sure? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Restore cancelled"
    exit 0
fi

# Create safety backup before restore
SAFETY_BACKUP="$BACKUP_DIR/pre_restore_backup_$(date +%Y%m%d_%H%M%S).sql"
sqlite3 sports_predictions_original.db ".dump system_picks" > "$SAFETY_BACKUP"
echo "✓ Safety backup created: $SAFETY_BACKUP"

# Restore from backup
sqlite3 sports_predictions_original.db <<EOF
DROP TABLE IF EXISTS system_picks;
.read $BACKUP_FILE
EOF

echo "✓ Database restored from $BACKUP_FILE"
echo "✓ $(sqlite3 sports_predictions_original.db "SELECT COUNT(*) FROM system_picks") picks restored"
