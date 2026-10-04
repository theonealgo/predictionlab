#!/bin/bash
#
# Setup Daily Prediction Generator Cron Job
# ==========================================
# This script sets up automatic daily prediction generation
# to prevent missing predictions for games.
#

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
GENERATOR_SCRIPT="$SCRIPT_DIR/daily_prediction_generator.py"
LOG_FILE="$SCRIPT_DIR/logs/daily_predictions.log"

# Create logs directory
mkdir -p "$SCRIPT_DIR/logs"

# Cron job to run daily at 9 AM
CRON_SCHEDULE="0 9 * * *"
CRON_COMMAND="cd $SCRIPT_DIR && /usr/bin/python3 $GENERATOR_SCRIPT >> $LOG_FILE 2>&1"

echo "=============================================="
echo "Daily Prediction Generator - Cron Setup"
echo "=============================================="
echo ""
echo "Script location: $GENERATOR_SCRIPT"
echo "Log file: $LOG_FILE"
echo "Schedule: Daily at 9:00 AM"
echo ""

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "daily_prediction_generator.py"; then
    echo "⚠️  Cron job already exists. Removing old entry..."
    crontab -l 2>/dev/null | grep -v "daily_prediction_generator.py" | crontab -
fi

# Add new cron job
echo "✓ Adding new cron job..."
(crontab -l 2>/dev/null; echo "$CRON_SCHEDULE $CRON_COMMAND") | crontab -

echo ""
echo "✓ Cron job installed successfully!"
echo ""
echo "To verify, run: crontab -l"
echo "To remove, run: crontab -e (then delete the line)"
echo ""
echo "Testing the script now..."
echo "=============================================="

# Run the script once to test
cd "$SCRIPT_DIR"
python3 "$GENERATOR_SCRIPT"

echo ""
echo "=============================================="
echo "Setup complete! Predictions will be generated"
echo "automatically every day at 9:00 AM."
echo "=============================================="
