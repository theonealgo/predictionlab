#!/usr/bin/env python3
"""
Score Predictor using ESPN Team Stats
Calculates predicted scores by comparing team offense vs defense.

v2 improvements:
  - 3-way average formula (team offense, opp defense, league avg)
  - Regression to mean toward league average total
  - Optional Vegas total blending (70% model / 30% Vegas)
  - Hard clipping to sport-specific realistic bounds
  - All changes only affect predict_score(); all callers remain compatible
"""
import requests
import sqlite3
from datetime import datetime, timedelta
import logging
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Sport-specific total clipping bounds ──────────────────────────────────
TOTAL_BOUNDS = {
    'NBA':   (200, 255),
    'NFL':   (32,  68),
    'NHL':   (4.5, 6.5),
    'MLB':   (5.0, 16.0),
    'NCAAB': (120, 185),
    'NCAAF': (28,  90),
    'WNBA':  (155, 205),
}

# ── League mean totals for regression-to-mean ─────────────────────────────
LEAGUE_MEAN_TOTAL = {
    'NBA':   226.0,
    'NFL':    47.5,
    'NHL':     5.8,
    'MLB':     8.9,
    'NCAAB': 150.0,
    'NCAAF':  56.0,
    'WNBA':  176.0,
}

TOTAL_REGRESSION_ALPHA = 0.82  # 82% model, 18% league mean
VEGAS_BLEND_WEIGHT     = 0.30  # when vegas_total provided: 30% Vegas, 70% model


class ScorePredictor:
    """Predicts game scores using team offensive/defensive stats from ESPN"""

    # Home field advantage by sport
    HOME_ADVANTAGE = {
        'NBA':   3.0,
        'NFL':   2.5,
        'NHL':   0.3,
        'NCAAF': 3.0,
        'NCAAB': 3.0,
        'MLB':   0.15,
        'WNBA':  3.0,
    }
    
    # ESPN API endpoints
    ESPN_ENDPOINTS = {
        'NBA': 'basketball/nba',
        'NFL': 'football/nfl',
        'NHL': 'hockey/nhl',
        'NCAAF': 'football/college-football',
        'NCAAB': 'basketball/mens-college-basketball'
    }
    
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
        self.team_stats_cache = {}
    
    def get_team_slug(self, team_name, sport):
        """Convert team name to ESPN slug format"""
        # Remove common suffixes and convert to slug
        slug = team_name.lower()
        slug = slug.replace(' ', '-')
        # Remove mascots for college sports
        return slug
    
    def fetch_team_stats(self, sport):
        """Fetch all team stats for a sport from ESPN API"""
        endpoint = self.ESPN_ENDPOINTS.get(sport)
        if not endpoint:
            logger.error(f"No ESPN endpoint for sport: {sport}")
            return {}
        
        url = f"https://site.api.espn.com/apis/site/v2/sports/{endpoint}/teams"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            team_stats = {}
            teams = data['sports'][0]['leagues'][0]['teams']
            
            for team_obj in teams:
                team = team_obj['team']
                team_name = team['displayName']
                
                # Get team stats from record endpoint
                team_id = team['id']
                stats_url = f"https://site.api.espn.com/apis/site/v2/sports/{endpoint}/teams/{team_id}"
                
                try:
                    stats_response = requests.get(stats_url, timeout=10)
                    stats_data = stats_response.json()
                    
                    # Extract points per game and points allowed
                    record = stats_data.get('team', {}).get('record', {}).get('items', [])
                    
                    points_for = 0
                    points_against = 0
                    games_played = 0
                    
                    # Look for overall stats
                    for item in record:
                        stats = item.get('stats', [])
                        for stat in stats:
                            if stat.get('name') == 'pointsFor':
                                points_for = float(stat.get('value', 0))
                            elif stat.get('name') == 'pointsAgainst':
                                points_against = float(stat.get('value', 0))
                            elif stat.get('name') == 'gamesPlayed':
                                games_played = int(stat.get('value', 1))
                    
                    # Calculate per-game averages
                    if games_played > 0:
                        ppg = points_for / games_played
                        papg = points_against / games_played
                    else:
                        ppg = 0
                        papg = 0
                    
                    team_stats[team_name] = {
                        'offense': ppg,  # Points per game
                        'defense': papg   # Points allowed per game
                    }
                    
                except Exception as e:
                    logger.warning(f"Could not fetch stats for {team_name}: {e}")
                    continue
            
            return team_stats
            
        except Exception as e:
            logger.error(f"Error fetching team stats for {sport}: {e}")
            return {}
    
    def predict_score(self, home_team, away_team, sport, vegas_total=None):
        """
        Predict final score using offensive/defensive stats.

        v2 Formula (3-way average):
          home_raw = (home_offense + away_defense + league_avg_ppg) / 3 + home_adv
          away_raw = (away_offense + home_defense + league_avg_ppg) / 3

        Post-processing:
          1. Regression to mean: total = 0.82*raw + 0.18*league_mean_total
          2. Vegas blend (if provided): total = 0.70*model + 0.30*vegas
          3. Hard clip to TOTAL_BOUNDS
          4. Preserve home/away ratio from raw scores

        Args:
            home_team:   home team display name
            away_team:   away team display name
            sport:       sport key (NBA, NFL, NHL, MLB, NCAAF, NCAAB, WNBA)
            vegas_total: optional Vegas O/U line for blending (default None)
        """
        # Get team stats (use cache if available)
        cache_key = f"{sport}_{datetime.now().strftime('%Y-%m-%d')}"
        if cache_key not in self.team_stats_cache:
            self.team_stats_cache[cache_key] = self.fetch_team_stats(sport)

        team_stats = self.team_stats_cache[cache_key]

        if home_team not in team_stats or away_team not in team_stats:
            logger.warning(f"Stats not found for {home_team} vs {away_team}")
            return None, None, None, None

        home_off = team_stats[home_team]['offense']
        home_def = team_stats[home_team]['defense']
        away_off = team_stats[away_team]['offense']
        away_def = team_stats[away_team]['defense']

        home_adv = self.HOME_ADVANTAGE.get(sport, 0)

        # ── League average PPG from cached stats ──────────────────────────
        all_ppg = [v['offense'] for v in team_stats.values() if v.get('offense', 0) > 0]
        league_avg_ppg = float(np.mean(all_ppg)) if all_ppg else (LEAGUE_MEAN_TOTAL.get(sport, 100) / 2)

        # ── 3-way average raw scores ──────────────────────────────────────
        home_raw = (home_off + away_def + league_avg_ppg) / 3 + home_adv
        away_raw = (away_off + home_def + league_avg_ppg) / 3
        raw_total = home_raw + away_raw

        # ── Regression to mean ────────────────────────────────────────────
        league_mean_total = LEAGUE_MEAN_TOTAL.get(sport, raw_total)
        total = TOTAL_REGRESSION_ALPHA * raw_total + (1 - TOTAL_REGRESSION_ALPHA) * league_mean_total

        # ── Vegas blend (when available) ──────────────────────────────────
        if vegas_total is not None:
            total = (1.0 - VEGAS_BLEND_WEIGHT) * total + VEGAS_BLEND_WEIGHT * float(vegas_total)

        # ── Hard clip to sport bounds ─────────────────────────────────────
        tlo, thi = TOTAL_BOUNDS.get(sport, (0, 9999))
        total = max(tlo, min(thi, total))

        # ── Preserve home/away ratio from raw scores ──────────────────────
        denom = home_raw + away_raw
        total_ratio = home_raw / denom if denom > 0 else 0.5
        home_score = total * total_ratio
        away_score = total * (1 - total_ratio)

        spread = home_score - away_score

        return round(home_score, 1), round(away_score, 1), round(spread, 1), round(total, 1)
    
    def generate_spread_total_picks(self, sport, days_ahead=7):
        """Generate spread and total picks for upcoming games"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_date = datetime.now().strftime('%Y-%m-%d')
        end_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        
        # Get upcoming games with betting lines
        cursor.execute("""
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id,
                   bl.spread, bl.total
            FROM games g
            LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
            WHERE g.sport = ? AND g.game_date >= ? AND g.game_date <= ?
            AND g.status = 'scheduled'
            ORDER BY g.game_date
        """, (sport, start_date, end_date))
        
        games = cursor.fetchall()
        picks = []
        
        for game_id, game_date, home_team, away_team, vegas_spread, vegas_total in games:
            home_score, away_score, pred_spread, pred_total = self.predict_score(home_team, away_team, sport)
            
            if home_score is None:
                continue
            
            # Determine spread comparison
            spread_pick = home_team if pred_spread > 0 else away_team
            spread_comparison = None
            if vegas_spread is not None:
                # Vegas spread is relative to home team (negative = home favored)
                if abs(pred_spread) > abs(vegas_spread):
                    spread_comparison = 'Higher confidence in favorite'
                elif abs(pred_spread) < abs(vegas_spread):
                    spread_comparison = 'Lower confidence in favorite'
                else:
                    spread_comparison = 'Matches Vegas'
            
            # Determine total comparison
            total_pick = None
            total_comparison = None
            if vegas_total is not None:
                if pred_total > vegas_total:
                    total_pick = 'OVER'
                    total_comparison = f'+{pred_total - vegas_total:.1f}'
                elif pred_total < vegas_total:
                    total_pick = 'UNDER'
                    total_comparison = f'{pred_total - vegas_total:.1f}'
                else:
                    total_pick = 'PUSH'
                    total_comparison = 'Matches Vegas'
            
            picks.append({
                'game_id': game_id,
                'game_date': game_date,
                'home_team': home_team,
                'away_team': away_team,
                'predicted_home_score': home_score,
                'predicted_away_score': away_score,
                'predicted_spread': pred_spread,
                'predicted_total': pred_total,
                'vegas_spread': vegas_spread,
                'vegas_total': vegas_total,
                'spread_pick': spread_pick,
                'spread_comparison': spread_comparison,
                'total_pick': total_pick,
                'total_comparison': total_comparison
            })
        
        conn.close()
        return picks
    
    def generate_spread_total_picks_from_api_games(self, sport, api_games):
        """
        Generate spread and total picks from a list of API game dicts.
        This doesn't require games to exist in database.
        
        Args:
            sport: Sport name (NBA, NHL, etc.)
            api_games: List of dicts with keys: home_team_name (or team names), away_team_name, game_date
        
        Returns:
            List of pick dicts with spread/total analysis
        """
        picks = []
        
        for game in api_games:
            # Handle both variations of team name keys
            home_team = game.get('home_team_name') or game.get('home_team')
            away_team = game.get('away_team_name') or game.get('away_team')
            game_date = game.get('game_date')
            
            if not home_team or not away_team or not game_date:
                logger.warning(f"Skipping game with missing data: {game}")
                continue
            
            home_score, away_score, pred_spread, pred_total = self.predict_score(home_team, away_team, sport)
            
            if home_score is None:
                logger.debug(f"Could not predict score for {away_team} @ {home_team}")
                continue
            
            # Determine spread comparison (no Vegas data available from API)
            spread_pick = home_team if pred_spread > 0 else away_team
            
            # Try to get Vegas lines if available in the game dict
            vegas_spread = game.get('vegas_spread')
            vegas_total = game.get('vegas_total')
            
            spread_comparison = None
            if vegas_spread is not None:
                if abs(pred_spread) > abs(vegas_spread):
                    spread_comparison = 'Higher confidence in favorite'
                elif abs(pred_spread) < abs(vegas_spread):
                    spread_comparison = 'Lower confidence in favorite'
                else:
                    spread_comparison = 'Matches Vegas'
            
            # Determine total comparison
            total_pick = None
            total_comparison = None
            if vegas_total is not None:
                if pred_total > vegas_total:
                    total_pick = 'OVER'
                    total_comparison = f'+{pred_total - vegas_total:.1f}'
                elif pred_total < vegas_total:
                    total_pick = 'UNDER'
                    total_comparison = f'{pred_total - vegas_total:.1f}'
                else:
                    total_pick = 'PUSH'
                    total_comparison = 'Matches Vegas'
            
            picks.append({
                'game_date': game_date,
                'home_team': home_team,
                'away_team': away_team,
                'predicted_home_score': home_score,
                'predicted_away_score': away_score,
                'predicted_spread': pred_spread,
                'predicted_total': pred_total,
                'vegas_spread': vegas_spread,
                'vegas_total': vegas_total,
                'spread_pick': spread_pick,
                'spread_comparison': spread_comparison,
                'total_pick': total_pick,
                'total_comparison': total_comparison
            })
        
        return picks


if __name__ == '__main__':
    # Test the predictor
    predictor = ScorePredictor()
    
    for sport in ['NBA', 'NHL', 'NFL']:
        print(f"\n=== {sport} Score Predictions ===")
        picks = predictor.generate_spread_total_picks(sport, days_ahead=3)
        
        for pick in picks[:5]:  # Show first 5
            print(f"{pick['away_team']} @ {pick['home_team']}")
            print(f"  Predicted Score: {pick['predicted_away_score']}-{pick['predicted_home_score']}")
            print(f"  Spread: {pick['spread_pick']} {pick['predicted_spread']:+.1f}")
            print(f"  Total: {pick['total_pick']} {pick['predicted_total']}")
            print()
