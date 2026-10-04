# PREDICTION SYSTEM RULES

## How The System Works

### Data Collection (Daily)
1. Fetch current betting lines from ESPN API (spreads, totals)
2. Calculate team records from completed games:
   - Moneyline record (W-L)
   - ATS record (covers vs doesn't cover)
   - Over/Under record (games going over vs under)

### Prediction Thresholds

**PICK teams that meet these criteria:**
- **Moneyline**: Win% > 61% → Pick to WIN
- **ATS**: ATS% > 61% → Pick to COVER spread
- **Totals**: Combined O/U% ≥ 60% → Pick OVER or UNDER

**FADE teams that meet these criteria:**
- **Moneyline**: Win% < 31% (but > 0%) → Pick OPPONENT to win
- **ATS**: ATS% < 31% (but > 0%) → Pick OPPONENT to cover
- **Totals**: Combined O/U% ≥ 60% for under → Pick UNDER

### Important Rules

1. **One pick per game per bet type** - Don't make conflicting picks
2. **Negative spread = favorite** - If picking favorite on spread (-X), MUST pick them on ML too
3. **Positive spread = underdog** - Can pick different teams on ML vs spread
4. **Totals use combined records** - Add both teams' over/under records together
5. **No picks for 0% teams** - Teams with exactly 0% get no pick (not a fade)

## Data Sources

### ESPN API
- Fetch daily betting lines (spread, total)
- Fetch game scores for grading picks
- Update game status (scheduled → final)

### Team Records (Updated Daily)
Import from CSV files or calculate from completed games:
- `nfl_complete_trends.csv`
- `nba_complete_trends.csv`
- `nhl_complete_trends.csv`

### CSV Format
```csv
team_name,ml_wins,ml_losses,ats_wins,ats_losses,over_wins,under_wins
Seattle Seahawks,7,3,9,1,6,4
```

## Database Schema

### team_records table
- `sport`: NFL, NBA, NHL, etc.
- `team_name`: Full team name
- `wins, losses`: Moneyline record
- `win_pct`: Calculated win percentage
- `ats_wins, ats_losses`: ATS record
- `ats_pct`: Calculated ATS percentage
- `over_wins, under_wins`: O/U record

### system_picks table
- `sport, game_id, game_date`
- `pick_type`: MONEYLINE, SPREAD, OVER, UNDER
- `pick_team`: Team being picked (NULL for totals)
- `pick_value`: Spread or total value
- `result`: WIN, LOSS, PUSH (graded after game)

## Daily Workflow

### Morning (9 AM)
1. Run `calculate_team_records.py` - Update all team records
2. Run `fetch_espn_odds.py` - Get latest betting lines
3. Run `update_all_schedules.py` - Update game schedules
4. Run `fetch_injuries.py` - Get injury data
5. Run `fetch_weather.py` - Get weather for outdoor games
6. Run `track_picks_results.py` - Save today's picks based on thresholds

### Evening (11 PM)
1. Run `track_results.py` - Grade yesterday's picks
2. Updates `system_picks.result` column with WIN/LOSS/PUSH
3. Updates `pick_results` table with units won/lost
4. Updates `weekly_summary` with cumulative W-L records

## Example System Teams (Current as of Nov 17-19)

### NFL
**ML >61%**: New England (9-2), LA Rams (8-2), Philadelphia (8-2), Indianapolis (8-2)
**ATS >61%**: Seattle (9-1)
**Over ≥60%**: Cincinnati (7-3), Minnesota (7-3), Baltimore (7-3)
**Under ≥60%**: Denver (3-8), Kansas City (3-7), Houston (3-7)

### NBA
**ML >61%**: Oklahoma City (14-1), Detroit (12-2), Denver (10-3)
**ATS >61%**: Philadelphia (10-3)
**Over ≥60%**: Toronto (16-3), Houston (10-2), Portland (10-3)
**Under ≥60%**: Indiana (5-9), Dallas (6-9), Memphis (5-9)

### NHL
**ML >61%**: Colorado (13-6), New Jersey (13-5), Carolina (12-6)
**ATS >61%**: Pittsburgh (15-4), Chicago (15-3), Boston (15-5), Seattle (14-4)
**Over ≥60%**: Toronto (16-3), Vancouver (15-5)
**Under ≥60%**: Chicago (6-12), NY Rangers (7-13)

## Notes

- Team records should be imported daily from updated CSV files
- Don't rely solely on calculated records from database games with betting lines
- The CSV files represent the true season-long performance
- Betting lines must exist for games to generate spread/total picks
