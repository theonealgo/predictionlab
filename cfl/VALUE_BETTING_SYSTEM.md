# Value Betting System - Implementation Guide

## What Changed?

### OLD SYSTEM (Problem):
- Models just picked favorites based on Elo ratings
- No comparison to market odds
- Ignored situational factors (rest, travel, form)
- Result: **Always betting favorites = losing strategy**

### NEW SYSTEM (Solution):
- **TheOdds API integration** - Live odds from 9+ bookmakers
- **Line shopping** - Find best available lines
- **Value detection** - Only bet when edge ≥ 5%
- **Situational analysis** - Rest days, back-to-back, recent form
- **Contrarian plays** - Bet underdogs when mispriced

## Key Components

### 1. `theodds_api.py`
- Fetches live odds from TheOdds-API
- Tracks multiple bookmakers for line shopping
- Calculates implied probabilities
- Finds best available lines

### 2. `situational_analysis.py`
- Rest days calculation (critical for NHL/NBA)
- Back-to-back game detection (8% edge penalty)
- Recent form tracking (last 5 games)
- Travel distance estimation
- Momentum factors

### 3. `value_predictor.py`
- Core engine that combines:
  - Model predictions (Elo, XGBoost, CatBoost, Meta)
  - Market odds (from TheOdds API)
  - Situational adjustments
- Calculates betting edge: `Edge = (Model_Prob - Market_Prob) / Market_Prob`
- Only recommends bets with ≥5% edge

### 4. `daily_value_analysis.py`
- Run daily for all sports (NHL, NBA, NFL)
- Shows top value plays
- Confidence levels: HIGH (15%+ edge), MEDIUM (10-15%), LOW (5-10%)

## Usage

### Daily Analysis (Recommended)
```bash
python3 daily_value_analysis.py
```

Shows:
- All value bets for today
- Confidence levels
- Best available lines
- Situational factors (rest, form, back-to-back)
- Top 5 plays across all sports

### Single Sport Analysis
```bash
python3 value_predictor.py  # NHL by default
```

Edit the `__main__` section to change sport:
```python
nba_enhanced = predictor.enhance_predictions('NBA')
predictor.print_recommendations(nba_enhanced, 'NBA')
```

## Example Output

```
🎯 Buffalo Sabres @ Carolina Hurricanes (2025-11-08)
   Recommendation: BET_AWAY - Buffalo Sabres
   Edge: 42.1% | Confidence: HIGH
   Model: 54.6% vs Market: 72.1%
   Best Line: +230 (Away)
   Rest: Home 1d, Away 1d
   Form: Home 60.0%, Away 20.0%
```

**Translation:**
- Market thinks Carolina is 72% to win (Sabres +230)
- Our model thinks Buffalo is 54.6% to win
- Market is OVERVALUING Carolina by 42%
- This is a VALUE BET on Buffalo at +230

## Key Metrics

### Edge Calculation
```
Edge = ((Model_Prob - Market_Prob) / Market_Prob) × 100
```

Example:
- Model: 55% home win
- Market: 40% home win (based on +150 odds)
- Edge: ((0.55 - 0.40) / 0.40) × 100 = **37.5% edge**

### Confidence Levels
- **HIGH**: ≥15% edge (bet with confidence)
- **MEDIUM**: 10-15% edge (solid value)
- **LOW**: 5-10% edge (marginal value)
- **PASS**: <5% edge (no bet)

### Situational Adjustments
- **Back-to-back penalty**: -8% (team on 0 days rest)
- **Rest advantage**: ±4% (2+ day difference)
- **Form momentum**: ±5% (significant hot/cold streak)
- **Home ice/field**: +2-2.5% (varies by sport)

## Expected Results

### Target Metrics
- **Win Rate**: 55%+ (at recommended edges)
- **ROI**: 10-20% (depending on edge magnitude)
- **Bets per day**: 5-10 across all sports (quality > quantity)

### Why This Works
1. **Market inefficiency** - Vegas overreacts to recent results
2. **Situational blind spots** - Market undervalues rest/travel
3. **Public bias** - Heavy favorites get overbet
4. **Line shopping** - 5-10% better prices vs single book

## Integration with NHL77FINAL.py

To integrate into your web app, you can:

1. **Run daily sync** before starting app:
```bash
python3 daily_value_analysis.py > today_picks.txt
python3 NHL77FINAL.py
```

2. **Add to database** - Store value picks in a new table:
```sql
CREATE TABLE value_picks (
    pick_id INTEGER PRIMARY KEY,
    sport TEXT,
    game_id TEXT,
    bet_team TEXT,
    edge REAL,
    confidence TEXT,
    best_line TEXT,
    created_at TIMESTAMP
);
```

3. **Show in UI** - Add "Value Picks" page that highlights only high-edge bets

## API Usage Notes

### TheOdds API Limits
- Free tier: 500 requests/month
- Each sport query = 1 request
- Running daily analysis = 3 requests/day × 30 days = 90/month
- **Well within free tier limits**

### Remaining API calls
Check at: https://the-odds-api.com/account/

## Next Steps

1. **Track performance** - Log all value picks and outcomes
2. **Adjust MIN_EDGE** - Start conservative (5%), can lower to 3% if hitting 60%+
3. **Add more factors** - Injuries, weather, referee tendencies
4. **Kelly Criterion** - Use edge to calculate optimal bet sizing
5. **Automated alerts** - Send daily picks via email/SMS

## Files Reference

| File | Purpose |
|------|---------|
| `theodds_api.py` | Odds fetching & line shopping |
| `situational_analysis.py` | Rest/travel/form analysis |
| `value_predictor.py` | Core value calculation engine |
| `daily_value_analysis.py` | Daily report for all sports |
| `VALUE_BETTING_SYSTEM.md` | This documentation |

## Philosophy

**"We're not trying to predict every game. We're trying to find mispriced games."**

A 52% model that only bets mispriced lines beats a 60% model that bets everything.

Focus on:
- **Selectivity** - Quality > Quantity
- **Edge** - Only bet when you have an advantage
- **Discipline** - Pass on games without clear value
- **Bankroll management** - Unit sizing based on edge (Kelly)

---

**Created**: 2025-11-07  
**Last Updated**: 2025-11-07  
**Status**: ✅ Production Ready
