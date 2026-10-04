# ATS System - Quick Start Guide

## 🚀 What You Got

I've added a complete **ATS (Against The Spread)** betting system to your sports prediction platform that:

✅ **Tracks ATS records** - Which teams consistently cover spreads  
✅ **Tracks over/under trends** - Which teams go over/under totals  
✅ **Generates spread picks** - Teams likely to cover  
✅ **Generates moneyline picks** - Teams likely to win straight-up  
✅ **Generates total picks** - Games likely to go over/under  
✅ **Filters by system teams** - Only recommends proven performers  

---

## 📋 New Files

1. **`ats_system.py`** - Core ATS system (tracks records, generates picks)
2. **`get_ats_picks.py`** - Quick CLI tool to get today's picks
3. **`update_system_teams.py`** - Helper to update system teams based on recent performance
4. **`ATS_SYSTEM_README.md`** - Complete documentation
5. **`QUICK_START_ATS.md`** - This file

---

## ⚡ Quick Commands

### Get Today's Picks
```bash
python3 get_ats_picks.py
```

### Export to CSV
```bash
python3 get_ats_picks.py --csv picks.csv
```

### Show ATS Trends
```bash
python3 get_ats_picks.py --show-ats
```

### Next 14 Days
```bash
python3 get_ats_picks.py --days 14 --csv week_ahead.csv
```

### Update System Teams (Monthly)
```bash
python3 update_system_teams.py --lookback 90
```

---

## 📊 Output Example

```
================================================================================
NBA BETTING PICKS - ATS SYSTEM
================================================================================

💰 MONEYLINE PICKS (12)
--------------------------------------------------------------------------------
  📅 2025-11-11: Phoenix Suns @ Cleveland Cavaliers
     ✅ PICK: Cleveland Cavaliers ML
     Win Prob: 86.4% | Confidence: 73%

📊 SPREAD PICKS (10)
--------------------------------------------------------------------------------
  📅 2025-11-11: Sacramento Kings @ Los Angeles Lakers
     ✅ PICK: Los Angeles Lakers +4.4
     Confidence: 44%

🎯 OVER/UNDER PICKS (42)
--------------------------------------------------------------------------------
  📅 2025-11-11: Utah Jazz @ Golden State Warriors
     ✅ PICK: OVER (Model Total: 220.0)
     System Team: Golden State Warriors | Confidence: 65%
```

---

## 🎯 System Teams (Who Gets Picked)

Your picks are filtered to only recommend **proven system teams**:

### NBA
- **Spread**: Lakers, Pistons, Trail Blazers, 76ers, Bulls, Spurs, Bucks
- **Moneyline**: Thunder, Pistons, Lakers, Nuggets, Spurs, Bulls, Bucks, Cavaliers
- **Over**: Rockets, Timberwolves, Lakers, Trail Blazers, 76ers, Nets, Kings, Knicks, Warriors
- **Under**: Mavericks, Pacers, Grizzlies

### NHL
- **Spread**: Blackhawks, Penguins, Kraken, Bruins, Sharks, Hurricanes
- **Moneyline**: Devils, Ducks, Hurricanes, Jets, Canadiens, Utah, Red Wings, Penguins, Rangers, Islanders, Flyers
- **Over**: Maple Leafs, Islanders, Canucks, Senators, Canadiens, Flames, Blues, Hurricanes, Ducks
- **Under**: Lightning, Blackhawks, Rangers, Predators

### NFL
- **Spread**: Rams, Seahawks, Patriots, Colts, Panthers, Dolphins
- **Moneyline**: Broncos, Colts, Patriots, Bills, Eagles, Seahawks, Buccaneers, Rams, Packers, 49ers, Chargers
- **Over**: Bengals, Vikings, Seahawks, Jets, Dolphins, 49ers, Cowboys, Ravens, Titans
- **Under**: Saints, Broncos, Chiefs, Raiders

---

## 🔄 How to Use (Workflow)

### Daily Routine
```bash
# 1. Generate today's picks
python3 get_ats_picks.py --csv today.csv --days 3

# 2. Open CSV and review picks
open today.csv

# 3. Place bets on high-confidence picks

# 4. Track results in your betting ledger
# (Use your existing CSV tracking system)
```

### Weekly Review
```bash
# Check which teams are hot/cold recently
python3 get_ats_picks.py --show-ats --lookback 30
```

### Monthly Update
```bash
# Update system teams based on recent 90-day performance
python3 update_system_teams.py --lookback 90

# Copy the output and update ats_system.py SYSTEM_TEAMS
```

---

## 📈 Integration with Existing System

The ATS system works **on top of** your existing models:

```
Your Current Models:
├── Elo Model
├── XGBoost Model
├── CatBoost Model
└── Meta Ensemble
         ↓
    Win Probabilities
         ↓
    ATS System ← NEW
         ↓
├── Converts to spreads
├── Identifies over/under trends
├── Filters by system teams
└── Generates betting picks
```

**No changes needed** to your existing prediction pipeline!

---

## 💡 Pro Tips

1. **Focus on High Confidence**: Filter for picks with >60% confidence for better hit rate

2. **Combine with Value System**: Use alongside `value_predictor.py` to find +EV bets
   ```bash
   # Compare model picks to market odds
   python3 value_predictor.py
   ```

3. **Update System Teams Monthly**: Teams get hot/cold - update every 30-90 days
   ```bash
   python3 update_system_teams.py --lookback 90
   ```

4. **Track Your Results**: Use your existing betting CSV system to calculate ROI

5. **Adjust Lookback Period**: Use shorter periods (30-60 days) for recent trends in fast-moving seasons

---

## 📝 CSV Export Columns

| Column | Description | Example |
|--------|-------------|---------|
| Sport | NBA/NHL/NFL | NBA |
| Date | Game date | 2025-11-11 |
| Matchup | Away @ Home | Lakers @ Warriors |
| Bet Type | MONEYLINE/SPREAD/TOTAL | SPREAD |
| Pick | Team to bet | Golden State Warriors |
| Pick Type | HOME_ML/AWAY_SPREAD/OVER | HOME_SPREAD |
| Details | Prob/Spread/Total | Spread: +5.5 |
| Confidence | % confidence | 73% |

---

## 🔧 Customization

### Change System Teams
Edit `ats_system.py`, line 33:
```python
SYSTEM_TEAMS = {
    'NBA': {
        'spread': ['Your', 'Teams', 'Here'],
        ...
    }
}
```

### Change Confidence Thresholds
Edit `ats_system.py`:
- Line 463: Moneyline threshold (currently 55%)
- Line 386: Spread threshold (currently 3 points)

### Change Sport Averages
Edit `ats_system.py`, line 319:
```python
avg_totals = {
    'NBA': 220,  # Adjust these
    'NHL': 6,
    'NFL': 45,
}
```

---

## ❓ Troubleshooting

### "No picks generated"
- Check games exist: `SELECT COUNT(*) FROM games WHERE sport='NBA' AND status != 'final'`
- Verify predictions exist: `SELECT COUNT(*) FROM predictions WHERE sport='NBA'`
- Ensure team names match exactly (case-sensitive)

### "Model spread seems wrong"
- This is **intentional** - model spreads ≠ Vegas lines
- The goal is to find value where your model disagrees with market
- Compare with Vegas using `value_predictor.py`

### "Database locked error"
- Close Flask app or other processes accessing database
- Only one process can write to SQLite at a time

---

## 📚 More Info

For detailed documentation, see:
- **`ATS_SYSTEM_README.md`** - Complete technical documentation
- **`ats_system.py`** - Source code with inline comments

---

## 🎯 Next Steps

1. **Test it**: Run `python3 get_ats_picks.py --csv test.csv`
2. **Review picks**: Open `test.csv` and review
3. **Start tracking**: Place small bets and track results
4. **Optimize**: After 2-4 weeks, update system teams based on performance

---

**Good luck! 🚀**
