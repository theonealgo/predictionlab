# Prediction Fixer - Quick Guide

## Access
**URL:** `http://localhost:5004/admin/fixer`

## The Problem It Solves
- Missing predictions (showing N/A%)
- Outdated scores (games showing 0-0)
- Results stuck on old dates
- Any prediction/score data issues

## How to Use

### 1. Scan for Issues
Click **"🔍 Scan for Issues"**
- Auto-scans on page load
- Shows all games with missing predictions
- Displays stats by sport

### 2. Update Scores (NEW!)
Click **"📥 Update Scores"**
- Fetches latest scores from ESPN/NHL APIs
- Updates last 7 days of games
- Fixes games stuck at 0-0
- **Use this if results are stuck on old dates**

### 3. Fix All Predictions
Click **"✨ Fix All Sports"**
- Generates predictions for all games with missing data
- Saves with locked=1 to prevent changes
- Auto-rescans after completion

## When to Use Each Button

### Use "Update Scores" when:
- ✅ Results page stuck on old date (e.g., showing Jan 5 when today is Jan 8)
- ✅ Games showing 0-0 scores
- ✅ Scores not updating automatically
- ✅ After games finish but scores haven't updated

### Use "Fix All Sports" when:
- ✅ Games showing N/A% for predictions
- ✅ Models showing 0.0% accuracy
- ✅ Missing prediction data
- ✅ After adding new games to database

### Use "Scan for Issues" when:
- ✅ You want to check current status
- ✅ After running other fixes to verify
- ✅ Diagnosing what's wrong

## Today's Fix (Jan 8, 2026)

**Problem:** NBA results stuck on Jan 5
**Root Cause:** Jan 6-7 games had scores of 0-0 in database
**Solution:** Clicked "📥 Update Scores" → Updated 17 games → Now shows through Jan 7 ✅

## Workflow

```
Daily Morning Routine:
1. Visit http://localhost:5004/admin/fixer
2. Click "📥 Update Scores" (fetches overnight scores)
3. Click "🔍 Scan for Issues" (check for any problems)
4. If issues found, click "✨ Fix All Sports"
5. Done! Results will be up to date.
```

## Technical Details

### Update Scores Button
- Fetches from ESPN API (NBA, NCAA, MLB, WNBA)
- Fetches from NHL API (NHL)
- Fetches from nfl_data_py (NFL)
- Updates games with 0-0 or NULL scores
- Checks last 7 days

### Fix All Button
- Generates predictions using current Elo ratings
- Uses stored model weights (XGBoost, CatBoost, Meta)
- Saves with locked=1 flag
- Only fixes games with missing predictions

## Common Issues & Solutions

### Issue: NBA/NHL results not showing latest date
**Solution:** Click "📥 Update Scores"

### Issue: Games showing N/A%
**Solution:** Click "✨ Fix All Sports"

### Issue: Team name mismatch (e.g., "LA Clippers" vs "Los Angeles Clippers")
**Solution:** Fixer detects and handles automatically via fallback matching

### Issue: Game IDs don't match (e.g., NBA_SD vs NBA_401)
**Solution:** Fixer uses date+teams fallback matching

## Automation

For fully automated fixes, the daily cron job handles:
- Prediction generation (9 AM daily)
- Score updates (via API calls)
- Backfilling gaps

But the fixer page lets you force updates anytime!

## Port Info
Current Flask port: **5004**
- Changes automatically if port busy
- Check terminal output for actual port

---

**Last Updated:** January 8, 2026
**Status:** ✅ Working - NBA now shows through Jan 7
