#!/usr/bin/env python3
"""
TheOdds API Integration - Live Odds & Line Shopping
Fetches real-time betting odds from multiple bookmakers
"""

import requests
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TheOddsAPI:
    """Wrapper for TheOdds-API to get live betting odds"""
    
    def __init__(self, api_key='18cfd484126cfef3f271472d619e2319'):
        self.api_key = api_key
        self.base_url = 'https://api.the-odds-api.com/v4'
        
    def get_odds(self, sport, markets='h2h,spreads,totals'):
        """
        Get live odds for a sport
        
        Args:
            sport: 'americanfootball_nfl', 'icehockey_nhl', 'basketball_nba'
            markets: Comma-separated markets (h2h=moneyline, spreads, totals)
        
        Returns:
            List of games with odds from multiple bookmakers
        """
        try:
            url = f'{self.base_url}/sports/{sport}/odds'
            params = {
                'apiKey': self.api_key,
                'regions': 'us',  # US bookmakers
                'markets': markets,
                'oddsFormat': 'american',
                'dateFormat': 'iso'
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            games = response.json()
            logger.info(f"Fetched odds for {len(games)} {sport} games")
            return games
            
        except Exception as e:
            logger.error(f"Error fetching odds from TheOdds API: {e}")
            return []
    
    def parse_odds_for_game(self, game_data):
        """
        Parse odds data for a single game to find best lines
        
        Returns dict with:
        - best_home_ml, best_away_ml (moneyline)
        - best_home_spread, best_away_spread
        - best_over, best_under (totals)
        - avg_home_ml, avg_away_ml (consensus)
        - bookmaker_count
        """
        home_team = game_data.get('home_team')
        away_team = game_data.get('away_team')
        
        result = {
            'home_team': home_team,
            'away_team': away_team,
            'game_id': game_data.get('id'),
            'commence_time': game_data.get('commence_time'),
            'bookmakers': []
        }
        
        # Track all odds for averaging
        home_ml_odds = []
        away_ml_odds = []
        home_spreads = []
        away_spreads = []
        totals = []
        
        bookmakers = game_data.get('bookmakers', [])
        
        for book in bookmakers:
            book_name = book.get('key')
            markets = book.get('markets', [])
            
            book_data = {'name': book_name}
            
            for market in markets:
                market_key = market.get('key')
                outcomes = market.get('outcomes', [])
                
                if market_key == 'h2h':  # Moneyline
                    for outcome in outcomes:
                        if outcome['name'] == home_team:
                            book_data['home_ml'] = outcome['price']
                            home_ml_odds.append(outcome['price'])
                        elif outcome['name'] == away_team:
                            book_data['away_ml'] = outcome['price']
                            away_ml_odds.append(outcome['price'])
                
                elif market_key == 'spreads':  # Point spread
                    for outcome in outcomes:
                        if outcome['name'] == home_team:
                            book_data['home_spread'] = outcome['point']
                            book_data['home_spread_odds'] = outcome['price']
                            home_spreads.append((outcome['point'], outcome['price']))
                        elif outcome['name'] == away_team:
                            book_data['away_spread'] = outcome['point']
                            book_data['away_spread_odds'] = outcome['price']
                            away_spreads.append((outcome['point'], outcome['price']))
                
                elif market_key == 'totals':  # Over/Under
                    for outcome in outcomes:
                        if outcome['name'] == 'Over':
                            book_data['over_total'] = outcome['point']
                            book_data['over_odds'] = outcome['price']
                        elif outcome['name'] == 'Under':
                            book_data['under_total'] = outcome['point']
                            book_data['under_odds'] = outcome['price']
                        totals.append(outcome['point'])
            
            result['bookmakers'].append(book_data)
        
        # Calculate best lines and averages
        if home_ml_odds:
            result['best_home_ml'] = max(home_ml_odds)  # Best odds for bettor
            result['avg_home_ml'] = sum(home_ml_odds) / len(home_ml_odds)
            result['home_implied_prob'] = self._american_to_probability(result['avg_home_ml'])
        
        if away_ml_odds:
            result['best_away_ml'] = max(away_ml_odds)
            result['avg_away_ml'] = sum(away_ml_odds) / len(away_ml_odds)
            result['away_implied_prob'] = self._american_to_probability(result['avg_away_ml'])
        
        if home_spreads:
            # Find best spread (most favorable for home team = most positive)
            result['best_home_spread'] = max(home_spreads, key=lambda x: x[0])
        
        if totals:
            result['consensus_total'] = sum(totals) / len(totals)
        
        result['bookmaker_count'] = len(bookmakers)
        
        return result
    
    def _american_to_probability(self, odds):
        """Convert American odds to implied probability"""
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return abs(odds) / (abs(odds) + 100)
    
    def get_nhl_odds(self):
        """Get NHL odds"""
        return self.get_odds('icehockey_nhl')
    
    def get_nfl_odds(self):
        """Get NFL odds"""
        return self.get_odds('americanfootball_nfl')
    
    def get_nba_odds(self):
        """Get NBA odds"""
        return self.get_odds('basketball_nba')
    
    def calculate_edge(self, model_prob, market_prob):
        """
        Calculate betting edge (Kelly Criterion input)
        
        Args:
            model_prob: Our model's probability (0-1)
            market_prob: Market implied probability (0-1)
        
        Returns:
            Edge as percentage (positive = value bet)
        """
        if market_prob == 0:
            return 0
        return ((model_prob - market_prob) / market_prob) * 100


if __name__ == '__main__':
    # Test the API
    logging.basicConfig(level=logging.INFO)
    
    api = TheOddsAPI()
    
    print("\n=== Testing NHL Odds ===")
    nhl_odds = api.get_nhl_odds()
    if nhl_odds:
        first_game = nhl_odds[0]
        parsed = api.parse_odds_for_game(first_game)
        print(f"\nGame: {parsed['away_team']} @ {parsed['home_team']}")
        print(f"Best Home ML: {parsed.get('best_home_ml')}")
        print(f"Best Away ML: {parsed.get('best_away_ml')}")
        print(f"Home Implied Prob: {parsed.get('home_implied_prob', 0):.1%}")
        print(f"Away Implied Prob: {parsed.get('away_implied_prob', 0):.1%}")
        print(f"Bookmakers: {parsed['bookmaker_count']}")
