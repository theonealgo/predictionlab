#!/bin/bash
cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"
echo "yes" | python3 generate_nba_predictions_to_db.py >> prediction_generation.log 2>&1
