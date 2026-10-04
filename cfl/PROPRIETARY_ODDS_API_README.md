# Proprietary Sports Odds API

## Overview
This API generates synthetic market-style spreads and totals using **only ESPN public data** and statistical modeling. No sportsbook or bookmaker odds are consumed or referenced.

## Key Features
- ✅ Uses only ESPN game data (scores, team stats, schedules)
- ✅ Generates model spreads and totals similar to Vegas opening lines
- ✅ Calculates confidence scores based on variance and sample size
- ✅ Provides edge analysis between predictions and model odds
- ✅ REST API with caching (6-hour default)
- ✅ No licensing risk - completely proprietary calculations

## How It Works

### Spread Calculation
1. Calculate expected scores using offensive vs defensive efficiency
2. Apply sport-specific home advantage (NBA: 3.0 pts, NFL: 2.5 pts, NHL: 0.3 goals)
3. Expected margin = Home expected score - Away expected score
4. Round to nearest 0.5

### Total Calculation
1. Calculate expected scores for both teams
2. Adjust for pace (fast/slow teams)
3. Sum expected scores
4. Round to nearest 0.5

### Confidence Score (0-1)
- Based on standard deviation of recent performances
- Higher confidence = lower variance + more games played
- Typical confidence: 0.6-0.9 for established teams

## API Endpoints

### 1. Get Upcoming Games Odds
```http
GET /api/odds/<sport>/upcoming?days=7
```

**Response:**
```json
{
  "sport": "NBA",
  "games": [
    {
      "game_id": "401584809",
      "game_date": "2026-01-15",
      "home_team": "Los Angeles Lakers",
      "away_team": "Boston Celtics",
      "model_spread": -3.5,
      "model_total": 225.5,
      "confidence_score": 0.782,
      "implied_margin_distribution": 12.4,
      "implied_total_distribution": 18.2,
      "generated_at": "2026-01-14T15:30:00"
    }
  ],
  "count": 15,
  "source": "proprietary_model",
  "data_source": "ESPN_public_API"
}
```

### 2. Get Single Game Odds
```http
GET /api/odds/game/<game_id>
```

### 3. Calculate Edge for Prediction
```http
POST /api/odds/edge

Content-Type: application/json
{
  "game_id": "401584809",
  "predicted_spread": -6.5,
  "predicted_total": 232.0
}
```

**Response:**
```json
{
  "game_id": "401584809",
  "model_odds": {
    "model_spread": -3.5,
    "model_total": 225.5,
    "confidence_score": 0.782
  },
  "prediction": {
    "spread": -6.5,
    "total": 232.0
  },
  "edge_analysis": {
    "spread_edge": 2.35,
    "total_edge": 5.09,
    "spread_direction": "favor_home",
    "total_direction": "over",
    "has_spread_edge": false,
    "has_total_edge": true,
    "confidence": 0.782,
    "recommendation": "EDGE_TOTAL"
  }
}
```

### 4. Clear Cache
```http
POST /api/odds/cache/clear
```

## Integration with Existing System

### Score Predictor Integration
The proprietary odds can be used to improve your existing `score_predictor.py`:

```python
from proprietary_odds_api import ProprietaryOddsAPI

odds_api = ProprietaryOddsAPI()

# Get model odds for a game
model_odds = odds_api.generate_odds(game_id, home_team, away_team, sport)

# Use model odds as a baseline
# Blend with your ensemble predictions
final_spread = 0.6 * your_predicted_spread + 0.4 * model_odds['model_spread']
final_total = 0.6 * your_predicted_total + 0.4 * model_odds['model_total']
```

### Edge Calculation for All Predictions
```python
# For each prediction, calculate edge
edge = odds_api.calculate_edge(
    predicted_spread=pred_spread,
    model_spread=model_odds['model_spread'],
    predicted_total=pred_total,
    model_total=model_odds['model_total'],
    confidence=model_odds['confidence_score']
)

# Only show picks with significant edge
if edge['has_spread_edge'] or edge['has_total_edge']:
    # This is a recommended pick
    display_pick(pred, edge)
```

## Edge Thresholds
- **Spread Edge**: 3.0 points (significant difference)
- **Total Edge**: 5.0 points (significant difference)
- Edge is weighted by confidence score

## Recommendations
- `NO_SIGNIFICANT_EDGE`: No strong edge detected
- `EDGE_SPREAD`: Significant edge on the spread
- `EDGE_TOTAL`: Significant edge on the total
- `STRONG_EDGE_BOTH`: Significant edge on both

## Data Sources
All calculations use only:
- ESPN game scores (historical and live)
- Team offensive/defensive ratings (derived from scores)
- Pace metrics (derived from total points)
- Home/away splits
- Rest days and schedule data

**No external odds feeds or sportsbook data are used.**

## Cache Behavior
- Default cache duration: 6 hours
- Cache key: `{game_id}_{home_team}_{away_team}_{sport}`
- Automatically refreshes after cache expiration
- Manual cache clear available via API endpoint

## Technical Details

### Sport-Specific Home Advantages
- NBA: 3.0 points
- NFL: 2.5 points
- NHL: 0.3 goals
- MLB: 0.15 runs
- NCAAB: 3.5 points
- NCAAF: 3.0 points
- WNBA: 2.5 points

### Lookback Window
- Default: Last 10 games per team
- Configurable via `get_team_stats(lookback_games=N)`

### Confidence Calculation
```python
# Normalized standard deviations
norm_margin_std = margin_std / 15.0  # NBA baseline
norm_total_std = total_std / 20.0

# Sample size factor
sample_factor = min(games_played / 10.0, 1.0)

# Combined confidence
variance_factor = 1.0 - (norm_margin_std + norm_total_std) / 2
confidence = (variance_factor * 0.7 + sample_factor * 0.3)
```

## Use Cases

### 1. Improve Spread/Total Accuracy
Use model odds as a calibration baseline to reduce prediction bias.

### 2. Edge Detection
Identify where your predictions differ significantly from the statistical model.

### 3. Confidence Scoring
Use the confidence score to weight predictions or filter low-confidence games.

### 4. Market Reference
Provide users with a "synthetic market line" without licensing sportsbook data.

### 5. Backtesting
Compare historical predictions against model odds to identify systematic biases.

## License & Legal
This is a **proprietary synthetic model** that:
- Does NOT use any sportsbook or bookmaker data
- Does NOT require licensing from odds providers
- Uses only publicly available ESPN data
- Generates completely independent statistical projections

Safe for public consumption and commercial use.

## Testing
Run the standalone test:
```bash
python3 proprietary_odds_api.py
```

This will generate sample odds for upcoming NBA games and test edge calculation.

## Questions?
This API is designed to be a self-contained, license-free odds generation system. All calculations are transparent and based solely on statistical analysis of public game data.
