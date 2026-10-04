# ATS (Against The Spread) Betting System

## Overview

The ATS System enhances your sports prediction models by adding **spread coverage**, **over/under**, and **moneyline** betting recommendations based on:

1. **Historical ATS Performance**: Tracks which teams consistently cover spreads
2. **Over/Under Trends**: Identifies teams that trend toward high/low-scoring games
3. **System Teams**: Filters picks to only recommend proven performers
4. **Model-Derived Spreads**: Converts win probabilities into point spreads and totals

---

## System Teams (Proven Performers)

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

## Quick Start

### Generate Picks for All Sports

```bash
python3 get_ats_picks.py
```

This will display:
- **Moneyline picks**: Teams likely to win straight-up
- **Spread picks**: Teams likely to cover the spread
- **Over/Under picks**: Games likely to go over/under the total

### Export to CSV

```bash
python3 get_ats_picks.py --csv my_picks.csv
```

Creates a CSV file with all picks for easy viewing in Excel/Google Sheets.

### Customize Options

```bash
# Show ATS records and trends
python3 get_ats_picks.py --show-ats

# Generate picks for specific sports
python3 get_ats_picks.py --sports NBA NHL

# Look ahead 14 days instead of 7
python3 get_ats_picks.py --days 14

# Use 90-day lookback for ATS records
python3 get_ats_picks.py --show-ats --lookback 90
```

---

## How It Works

### 1. Model Spread Calculation

The system converts win probabilities into point spreads using sport-specific multipliers:

```python
# Example: NBA
home_win_prob = 0.65  # 65% chance home team wins
spread = 25 * (0.65 - 0.5) = +3.75 points

# NFL uses multiplier of 14
# NHL uses multiplier of 1.5
# MLB uses multiplier of 2.5
```

### 2. ATS Record Tracking

For each completed game, the system:
- Calculates the model spread
- Compares to actual margin
- Records whether the team covered

**Example**:
- Model spread: Lakers -5.5
- Actual result: Lakers win by 8
- ATS Result: **Lakers COVERED** (won by more than spread)

### 3. Pick Generation Logic

#### Moneyline Picks
- Requires: Win probability > 55%
- Filters: Only system teams
- Confidence: Based on probability margin

#### Spread Picks
- Requires: Model spread > 3 points
- Filters: Only system teams
- Confidence: Based on spread magnitude

#### Over/Under Picks
- Requires: Either team is in over/under system list
- Uses: Model total (or sport average)
- Confidence: Medium (65%)

---

## Advanced Usage

### View ATS Records

```python
from ats_system import ATSSystem

ats = ATSSystem()

# Get NBA ATS records (last 180 days)
nba_ats = ats.calculate_ats_records('NBA', lookback_days=180)
print(nba_ats.head(10))

# Output:
#                    team  ats_wins  ats_losses  ats_pushes  total_games  ats_win_pct
# Houston Rockets         10         2           1           13          0.833
# Detroit Pistons          9         3           0           12          0.750
# ...
```

### Generate Specific Pick Types

```python
from ats_system import ATSSystem

ats = ATSSystem()

# Get only spread picks for NBA
spread_picks = ats.generate_spread_picks('NBA', days_ahead=7)

# Get only moneyline picks
ml_picks = ats.generate_moneyline_picks('NHL', days_ahead=14)

# Get only totals
total_picks = ats.generate_total_picks('NFL', days_ahead=3)
```

### Calculate Over/Under Trends

```python
# Get over/under records for NHL
nhl_ou = ats.calculate_over_under_records('NHL', lookback_days=180)

# Sort to find top "over" teams
top_over = nhl_ou.sort_values('over_pct', ascending=False)
print(top_over.head(5))

# Sort to find top "under" teams
top_under = nhl_ou.sort_values('over_pct', ascending=True)
print(top_under.head(5))
```

---

## Integration with Existing Models

The ATS system integrates seamlessly with your existing prediction pipeline:

1. **Reads from Database**: Uses `games` and `predictions` tables
2. **No Model Changes**: Works with existing Elo, XGBoost, CatBoost, Meta models
3. **Complementary**: Adds ATS layer on top of win probability predictions

### Database Schema

The system requires these tables:
- `games`: Game schedules and results
- `predictions`: Model predictions with probabilities

```sql
-- Example query the system uses
SELECT 
    g.game_id,
    g.home_team_id,
    g.away_team_id,
    p.meta_home_prob,
    p.xgboost_home_prob,
    p.elo_home_prob
FROM games g
LEFT JOIN predictions p ON g.game_id = p.game_id
WHERE g.sport = 'NBA' 
  AND g.status != 'final'
```

---

## CSV Output Format

The generated CSV file has these columns:

| Column | Description | Example |
|--------|-------------|---------|
| Sport | Sport abbreviation | NBA |
| Date | Game date | 2025-11-11 |
| Matchup | Away @ Home | Lakers @ Warriors |
| Bet Type | Type of bet | MONEYLINE, SPREAD, TOTAL |
| Pick | Team to bet on | Golden State Warriors |
| Pick Type | Specific pick | HOME_ML, AWAY_SPREAD, OVER |
| Details | Probability/spread/total | Win Prob: 72.5% |
| Confidence | Confidence level | 65% |

---

## Confidence Levels

### Moneyline
- **High (>80%)**: Win probability > 65%
- **Medium (50-80%)**: Win probability 60-65%
- **Low (25-50%)**: Win probability 55-60%

### Spread
- **High**: Model spread > 10 points
- **Medium**: Model spread 5-10 points  
- **Low**: Model spread 3-5 points

### Totals
- **Medium (65%)**: Fixed confidence for system team games

---

## Backtesting

To validate ATS performance:

```python
from ats_system import ATSSystem

ats = ATSSystem()

# Get ATS records for last 365 days
nba_ats = ats.calculate_ats_records('NBA', lookback_days=365)

# Check if your system teams are top performers
system_teams = ['Los Angeles Lakers', 'Detroit Pistons', ...]
system_performance = nba_ats[nba_ats['team'].isin(system_teams)]

print(f"Average ATS Win %: {system_performance['ats_win_pct'].mean():.1%}")
```

---

## Tips for Use

1. **Update System Teams**: As seasons progress, update the system teams list based on recent ATS performance
2. **Combine with Value System**: Use alongside `value_predictor.py` to find +EV bets
3. **Track Results**: Keep a betting log to measure ROI (see your betting CSV rules)
4. **Adjust Lookback**: Use shorter lookback periods (90 days) for more recent trends
5. **Filter by Confidence**: Focus on high-confidence picks for better hit rate

---

## Example Workflow

```bash
# 1. Generate picks and export to CSV
python3 get_ats_picks.py --csv today.csv --days 3

# 2. Review picks in CSV
open today.csv  # or Excel/Google Sheets

# 3. Check ATS trends before betting
python3 get_ats_picks.py --show-ats --sports NBA

# 4. Track results in your betting ledger
# (Use your existing CSV tracking system)
```

---

## Troubleshooting

### No Picks Generated

**Issue**: System returns 0 picks for a sport

**Solutions**:
1. Check if games exist: `SELECT COUNT(*) FROM games WHERE sport='NBA' AND status != 'final'`
2. Verify predictions exist: `SELECT COUNT(*) FROM predictions WHERE sport='NBA'`
3. Ensure system teams match database team names (case-sensitive)

### Model Spread Seems Wrong

**Issue**: Spreads don't match Vegas lines

**Explanation**: The system uses model probabilities, not Vegas lines. This is intentional - the goal is to find value where the model disagrees with the market.

### Database Errors

**Issue**: `database is locked` or connection errors

**Solution**: Close any other processes accessing the database (like the Flask app)

---

## Future Enhancements

Potential improvements:
- [ ] Add actual spread/total data from odds APIs
- [ ] Calculate value by comparing model to Vegas lines
- [ ] Add unit sizing recommendations
- [ ] Track pick performance over time
- [ ] Add team-specific scoring models for totals
- [ ] Integrate situational analysis (rest, travel, etc.)

---

## Files

- `ats_system.py`: Core ATS system class
- `get_ats_picks.py`: Quick CLI tool for generating picks
- `ATS_SYSTEM_README.md`: This documentation

---

## Support

For issues or questions, check:
1. Database schema matches expected format
2. Predictions exist for upcoming games
3. Team names in SYSTEM_TEAMS match database exactly
4. Python 3.11+ with pandas, numpy installed

---

**Good luck with your betting! 🎯**
