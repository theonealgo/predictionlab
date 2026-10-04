# NBA Prediction Integrity - Critical Issues & Fix Plan

## CRITICAL ISSUES FOUND

### 1. **14 Games Have Results But NO Predictions**
- Games from 2025-11-03 and other dates pulled from API
- These games have game_ids like `NBA_95`, `NBA_96` (simple numbers)
- The predictions table has NO matching records for these games
- **This means the Results page is generating "fake" predictions on-the-fly!**

### 2. **Root Cause: API Games vs Stored Predictions**
- The hybrid API (SportsData + ESPN) pulls live NBA games
- These API games get temporary game_IDs when displayed on Predictions page  
- But predictions are never SAVED to the database with these game_IDs
- When Results page runs, it can't find predictions for these games
- So it generates new predictions (which violates integrity!)

## THE CORRECT SOLUTION

### Phase 1: Lock All Existing Predictions ✅
```bash
python3 prediction_integrity.py
```
This validates current predictions and generates an integrity report.

### Phase 2: Pre-Generate Predictions for API Games (REQUIRED)
We need a script that:
1. Pulls NBA games from the hybrid API
2. For each game WITHOUT a prediction:
   - Generate predictions using trained models
   - INSERT into predictions table with correct game_ID
   - Mark with timestamp (created_at)
3. Never modify existing predictions (check created_at)

### Phase 3: Modify Results Page to ONLY Use Database
The `calculate_nba_weekly_performance()` function must:
- ✅ Already fixed: No longer calls slow NBA API
- ✅ Uses database scores (updated by update_nba_scores)
- ❌ PROBLEM: Still generates predictions for orphaned games
- ✅ SOLUTION: Only show games that have predictions in database

### Phase 4: Daily Automation
```bash
# Run daily at 9 AM (before games start)
0 9 * * * cd /path/to/project && python3 generate_nba_predictions.py

# Run after games end (update scores)
0 2 * * * cd /path/to/project && python3 -c "from NHL77FINAL import update_nba_scores; update_nba_scores()"

# Run integrity check
0 3 * * * cd /path/to/project && python3 prediction_integrity.py
```

## IMMEDIATE ACTIONS NEEDED

1. **Create `generate_nba_predictions.py`** - Generates predictions for new API games
2. **Modify `calculate_nba_weekly_performance()`** - Only show games with predictions
3. **Add validation** - Results page shows warning if orphaned games detected
4. **Daily cron** - Automate prediction generation and validation

## VALIDATION CHECKLIST

- [ ] No orphaned results (games with scores but no predictions)
- [ ] No placeholder predictions (exactly 50%)  
- [ ] All predictions have created_at timestamp
- [ ] Results page uses exact same % from predictions table
- [ ] Predictions are never modified after creation
- [ ] Daily integrity report generated

## FILES CREATED

1. **prediction_integrity.py** - Validation and reporting system
2. **NBA_INTEGRITY_FIX_PLAN.md** - This document
3. **nba_integrity_report_TIMESTAMP.txt** - Generated reports

## NEXT STEPS

Ask the user:
1. Do you want me to create the prediction generation script?
2. Should we modify the Results page to hide orphaned games?
3. Do you want to set up the daily cron jobs?
