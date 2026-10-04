# Automated Model Retraining Setup

## Overview
This guide helps you set up automated weekly model retraining to keep your predictions accurate as the season progresses.

## What Gets Retrained
- **NHL Models**: XGBoost, CatBoost, Meta Ensemble with advanced features (back-to-back, goalie stats, form, special teams)
- **NBA Models**: XGBoost, CatBoost, Meta Ensemble with advanced features (rest days, pace, B2B detection, net rating)
- **NFL/MLB/NCAAF/NCAAB Models**: Basic retraining with available data

## Manual Retraining

To manually retrain all models right now:

```bash
python3 weekly_model_retrainer.py
```

This will:
1. Load all completed games from database
2. Engineer advanced features for each sport
3. Train XGBoost, CatBoost, and Meta models
4. Save updated models to `models/` directory
5. Print accuracy metrics for each model

## Automated Weekly Retraining (Cron Job)

### Setup on Mac/Linux

1. Open your crontab editor:
```bash
crontab -e
```

2. Add this line to run every Sunday at 11 PM:
```bash
0 23 * * 0 cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/bin/python3 weekly_model_retrainer.py >> logs/retraining.log 2>&1
```

3. Create logs directory if it doesn't exist:
```bash
mkdir -p /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\)/logs
```

4. Save and exit the crontab editor

5. Verify the cron job is installed:
```bash
crontab -l
```

### Cron Schedule Options

```bash
# Every Sunday at 11 PM
0 23 * * 0 <command>

# Every Monday at 3 AM
0 3 * * 1 <command>

# Every day at midnight
0 0 * * * <command>

# Twice per week (Sunday and Wednesday at 11 PM)
0 23 * * 0,3 <command>
```

### Alternative: Manual Weekly Reminder

If you prefer to run manually, set a weekly calendar reminder for Sunday nights:

```bash
# Run this command manually every Sunday:
cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"
python3 weekly_model_retrainer.py
```

## Monitoring Retraining

### Check Recent Retraining Logs
```bash
tail -n 100 logs/retraining.log
```

### Check Model File Dates
```bash
ls -lh models/*.pkl
```

The modification dates show when each model was last retrained.

### Verify Model Accuracy
The retraining script prints accuracy metrics:
- **XGBoost accuracy**: How well XGBoost performs
- **CatBoost accuracy**: How well CatBoost performs
- **Meta accuracy**: How well the ensemble performs

Target accuracies:
- NHL: 60-70% (improving with new features)
- NBA: 60-70% (improving with new features)
- NFL: 65-75%

## What Happens During Retraining

1. **Data Collection**: Fetches all completed games since season start
2. **Feature Engineering**: 
   - NHL: Goalie stats, back-to-back detection, form (L5/L10), special teams, shot quality
   - NBA: Rest days, pace, offensive/defensive ratings, B2B detection, home/road splits
3. **Model Training**: Trains XGBoost, CatBoost, and Meta ensemble
4. **Validation**: Tests on most recent 20% of games
5. **Model Saving**: Overwrites old models with new trained versions

## Troubleshooting

### Models Not Improving?
- Check if you have enough completed games (need 100+ per sport)
- Verify feature engineering modules are working: `python3 nhl_feature_engineering.py`
- Check for database connection issues

### Cron Job Not Running?
1. Check cron is running: `ps aux | grep cron`
2. Check logs: `tail -f logs/retraining.log`
3. Test command manually first
4. Verify Python path: `which python3`

### Low Accuracy After Retraining?
- Early season: Not enough data yet (wait for 50+ games)
- Feature engineering errors: Check logs for warnings
- Overfitting: Reduce model complexity in `weekly_model_retrainer.py`

## Best Practices

1. **Retrain Weekly**: More frequent than weekly can cause overfitting
2. **Monitor Logs**: Check logs after each retraining to catch issues early
3. **Backup Models**: Before retraining, backup current models:
   ```bash
   cp -r models models_backup_$(date +%Y%m%d)
   ```
4. **Test After Retraining**: Check model breakdown on Results page to see if accuracy improved

## Feature Improvements Implemented

### NHL (Target: 70%)
✅ Back-to-back game detection (-5% win prob)
✅ Fatigue (games in last 4 days)
✅ Recent form (L5/L10 with weighting)
✅ Goalie quality (save%, GAA)
✅ Special teams (PP%, PK%)
✅ Shot quality metrics

### NBA (Target: 70%)
✅ Rest days advantage (+2% per day)
✅ Road back-to-back penalty (-8% win prob)
✅ Weighted recent form (L5/L10)
✅ Pace and tempo matchups
✅ Net rating differential
✅ Home/away splits

### NFL (Maintain: 70%)
✅ Adaptive ensemble weighting (70% Elo, 15% XGB, 15% Cat)
🔄 Weather data (future)
🔄 Injury impact (future)
🔄 Turnover differential (future)

Legend: ✅ Implemented | 🔄 Planned
