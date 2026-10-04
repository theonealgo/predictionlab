# NBA Weighted Average Total System

## Overview
The Weighted Average Total System is a specialized prediction method for NBA game totals that uses recent team performance (last 3 games) combined with trend analysis to generate betting recommendations.

## Implementation Date
December 11, 2025

## Backup Location
Full application backup created at:
`/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)_backup_20251211_083842`

## How It Works

### 1. Data Collection
- Fetches the last 3 completed games for each team from ESPN API
- **Excludes overtime games** to get regulation scoring only
- Looks back up to 21 days to find games

### 2. Team Average Calculation
For each team:
1. Calculate the team's average scoring over last 3 games
2. Calculate the opponents' average scoring in those 3 games
3. Average these two values together for a more stable team average
   - Formula: `team_avg = (team_scoring_avg + opponents_avg) / 2`

### 3. Projected Total Calculation
- **Projected Total = Team A Average + Team B Average**
- This represents the expected combined score for the matchup

### 4. Trend Analysis (Over/Under Confirmation)
For each team's last 3 games:
- Count how many games had a total score exceeding the projected total
- Maximum 3 per team, 6 combined

### 5. Betting Recommendation
Based on combined over count:
- **≥4 out of 6** → Recommend **OVER**
- **≤2 out of 6** → Recommend **UNDER**
- **3 out of 6** → **NO BET** (insufficient edge)

## Integration Points

### Files Modified
1. **NHL77FINAL.py** (lines 2863-3025)
   - Modified `/sport/<sport>/spreads` route
   - Added weighted total calculation for NBA games
   - Updated HTML template to display weighted total analysis

### Files Created
1. **weighted_total_predictor.py**
   - Standalone module for calculating weighted totals
   - Can be used independently via command line
   - Includes error handling for insufficient data

## Usage

### Via Web Interface
Navigate to: `http://localhost:5000/sport/NBA/spreads`

The NBA spreads page now includes:
- ⭐ Weighted Avg Total section (highlighted in gold)
- Team averages for both teams
- Over/Under trend counts
- 💡 Recommendation (OVER/UNDER/NO BET)
- Comparison with Vegas total (if available)

### Via Command Line
```bash
python3 weighted_total_predictor.py "Team A Name" "Team B Name" [vegas_total]
```

Example:
```bash
python3 weighted_total_predictor.py "Los Angeles Lakers" "Golden State Warriors" 225.5
```

## Output Format

### JSON Response Structure
```python
{
    "projected_total": float,           # Calculated game total
    "teamA_over_count": int,            # Home team over count (0-3)
    "teamB_over_count": int,            # Away team over count (0-3)
    "combined_over_count": int,         # Combined count (0-6)
    "recommended_bet": str,             # "OVER" | "UNDER" | "NO BET"
    "vegas_total": float or None,       # Vegas line if provided
    "difference_from_vegas": float,     # Difference from Vegas
    "teamA_last3_games": list,          # [(team_pts, opp_pts), ...]
    "teamB_last3_games": list,          # [(team_pts, opp_pts), ...]
    "teamA_avg": float,                 # Team A weighted average
    "teamB_avg": float,                 # Team B weighted average
    "error": str or None                # Error message if data insufficient
}
```

## Error Handling

### Insufficient Data
If either team has fewer than 3 completed games:
- Returns error message explaining which team lacks data
- Displays error on web interface
- Recommends "NO BET"

### API Failures
- Gracefully handles ESPN API timeouts
- Logs warnings for debugging
- Continues processing other games

## Visual Indicators (Web UI)

### Colors
- **Gold (#fbbf24)**: Weighted total section header
- **Green (#10b981)**: OVER recommendation
- **Red (#f87171)**: UNDER recommendation  
- **Purple (#a78bfa)**: NO BET recommendation

### Display Elements
- Team abbreviations (first 3 letters)
- Individual and combined over counts
- Difference from Vegas line (with +/- indicator)

## Testing

Test with current NBA teams:
```bash
python3 weighted_total_predictor.py "Boston Celtics" "Miami Heat" 220.0
python3 weighted_total_predictor.py "Denver Nuggets" "Phoenix Suns" 230.5
```

## Sport-Specific Notes

- **NBA Only**: This system is specifically calibrated for NBA basketball
- **Other Sports**: Continue using standard statistical model
- **Future**: Could be adapted for other sports with appropriate calibration

## Performance Considerations

- API calls are cached per day in main application
- Lookback period of 21 days balances data freshness with API load
- Each game adds weighted total calculation (minimal overhead)

## Maintenance

### To Update Algorithm
Edit `weighted_total_predictor.py`:
- Modify `calculate_weighted_average_total()` function
- Adjust thresholds in recommendation logic (lines 201-206)
- Update lookback days parameter if needed

### To Disable Feature
In `NHL77FINAL.py`, remove or comment out lines 2878-2891 in the `/sport/<sport>/spreads` route.

## Dependencies
- requests
- datetime
- typing (for type hints)
- Flask (for web integration)

All standard Python 3.11 libraries - no additional packages required.
