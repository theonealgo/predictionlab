"""
Rating Engines Module
=====================

Glicko-2 replaces Elo as primary rating system because:
1. Tracks rating uncertainty (σ) - knows when it's confident vs uncertain
2. Tracks volatility (φ) - adapts to streaky vs consistent teams
3. Better calibrated win probabilities

Elo is demoted to a feature generator, not a standalone predictor.
Margin-adjusted ratings capture HOW teams win, not just IF they win.
"""

import math
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, List
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class Glicko2Player:
    """Glicko-2 rating for a team"""
    mu: float = 1500.0        # Rating mean (Glicko scale)
    sigma: float = 350.0      # Rating deviation (uncertainty)
    phi: float = 0.06         # Volatility (how erratic performance is)
    
    # Glicko-2 internal scale
    SCALE_FACTOR = 173.7178
    TAU = 0.5  # System constant (lower = more conservative volatility changes)
    
    @property
    def glicko2_mu(self) -> float:
        """Convert to Glicko-2 internal scale"""
        return (self.mu - 1500) / self.SCALE_FACTOR
    
    @property
    def glicko2_sigma(self) -> float:
        """Convert sigma to Glicko-2 internal scale"""
        return self.sigma / self.SCALE_FACTOR
    
    def to_glicko1(self, g2_mu: float, g2_sigma: float) -> Tuple[float, float]:
        """Convert from Glicko-2 internal scale back to Glicko-1"""
        return (
            g2_mu * self.SCALE_FACTOR + 1500,
            g2_sigma * self.SCALE_FACTOR
        )


class Glicko2Rating:
    """
    Glicko-2 Rating System
    ----------------------
    
    Why Glicko-2 over Elo:
    - Uncertainty tracking: Knows when ratings are reliable vs uncertain
    - Volatility: Adapts K-factor dynamically per team
    - Better calibration: Win probabilities are more accurate
    
    The uncertainty (sigma) is especially valuable because:
    - New teams start with high uncertainty
    - Teams coming off breaks have increased uncertainty
    - Consistently performing teams have low uncertainty
    """
    
    TAU = 0.5
    EPSILON = 0.000001
    
    def __init__(self, initial_rating: float = 1500.0, initial_rd: float = 350.0):
        self.initial_rating = initial_rating
        self.initial_rd = initial_rd
        self.ratings: Dict[str, Glicko2Player] = {}
        
    def get_rating(self, team_id: str) -> Glicko2Player:
        """Get or create rating for a team"""
        if team_id not in self.ratings:
            self.ratings[team_id] = Glicko2Player(
                mu=self.initial_rating,
                sigma=self.initial_rd,
                phi=0.06
            )
        return self.ratings[team_id]
    
    def _g(self, sigma: float) -> float:
        """Glicko g function - reduces impact of uncertain opponents"""
        return 1.0 / math.sqrt(1.0 + 3.0 * sigma**2 / math.pi**2)
    
    def _E(self, mu: float, mu_opp: float, sigma_opp: float) -> float:
        """Expected score given ratings"""
        return 1.0 / (1.0 + math.exp(-self._g(sigma_opp) * (mu - mu_opp)))
    
    def win_probability(self, team_a: str, team_b: str, 
                        home_advantage: float = 0.0) -> Tuple[float, float, float]:
        """
        Calculate win probability for team_a vs team_b
        
        Returns: (prob_a_wins, uncertainty, disagreement_potential)
        - prob_a_wins: Probability team A wins
        - uncertainty: Combined rating uncertainty (higher = less confident)
        - disagreement_potential: How much the prediction could be wrong
        """
        rating_a = self.get_rating(team_a)
        rating_b = self.get_rating(team_b)
        
        # Apply home advantage to rating
        mu_a = rating_a.glicko2_mu + (home_advantage / Glicko2Player.SCALE_FACTOR)
        mu_b = rating_b.glicko2_mu
        
        sigma_a = rating_a.glicko2_sigma
        sigma_b = rating_b.glicko2_sigma
        
        # Combined uncertainty
        combined_sigma = math.sqrt(sigma_a**2 + sigma_b**2)
        
        # Expected score with uncertainty factored in
        prob = self._E(mu_a, mu_b, combined_sigma)
        
        # Uncertainty measure (0-1, higher = less confident)
        uncertainty = min(1.0, combined_sigma / 2.0)
        
        # Disagreement potential (how volatile both teams are)
        disagreement = (rating_a.phi + rating_b.phi) / 0.12
        
        return prob, uncertainty, disagreement
    
    def _compute_v(self, mu: float, opponents: List[Tuple[float, float, float]]) -> float:
        """Compute estimated variance of rating based on game outcomes"""
        v = 0.0
        for mu_j, sigma_j, _ in opponents:
            g_sigma = self._g(sigma_j)
            E = self._E(mu, mu_j, sigma_j)
            v += g_sigma**2 * E * (1 - E)
        return 1.0 / v if v > 0 else float('inf')
    
    def _compute_delta(self, mu: float, v: float, 
                       opponents: List[Tuple[float, float, float]]) -> float:
        """Compute estimated improvement in rating"""
        delta = 0.0
        for mu_j, sigma_j, score in opponents:
            g_sigma = self._g(sigma_j)
            E = self._E(mu, mu_j, sigma_j)
            delta += g_sigma * (score - E)
        return v * delta
    
    def _new_volatility(self, sigma: float, phi: float, v: float, delta: float) -> float:
        """Compute new volatility using iterative algorithm"""
        a = math.log(phi**2)
        
        def f(x):
            ex = math.exp(x)
            num = ex * (delta**2 - sigma**2 - v - ex)
            denom = 2 * (sigma**2 + v + ex)**2
            return num / denom - (x - a) / self.TAU**2
        
        # Iterative algorithm to find new volatility
        A = a
        if delta**2 > sigma**2 + v:
            B = math.log(delta**2 - sigma**2 - v)
        else:
            k = 1
            while f(a - k * self.TAU) < 0:
                k += 1
            B = a - k * self.TAU
        
        fA, fB = f(A), f(B)
        
        while abs(B - A) > self.EPSILON:
            C = A + (A - B) * fA / (fB - fA)
            fC = f(C)
            if fC * fB < 0:
                A, fA = B, fB
            else:
                fA = fA / 2
            B, fB = C, fC
        
        return math.exp(A / 2)
    
    def update_ratings(self, team_a: str, team_b: str, 
                       score_a: float, home_advantage: float = 0.0):
        """
        Update ratings after a game
        
        Args:
            team_a: Home team ID
            team_b: Away team ID
            score_a: 1 if team_a won, 0 if lost, 0.5 for draw
            home_advantage: Rating points added to home team (e.g., 50 for NHL)
        """
        rating_a = self.get_rating(team_a)
        rating_b = self.get_rating(team_b)
        
        # Convert to Glicko-2 scale
        mu_a = rating_a.glicko2_mu + (home_advantage / Glicko2Player.SCALE_FACTOR)
        mu_b = rating_b.glicko2_mu
        sigma_a = rating_a.glicko2_sigma
        sigma_b = rating_b.glicko2_sigma
        phi_a = rating_a.phi
        phi_b = rating_b.phi
        
        # Update team A
        v_a = self._compute_v(mu_a, [(mu_b, sigma_b, score_a)])
        delta_a = self._compute_delta(mu_a, v_a, [(mu_b, sigma_b, score_a)])
        new_phi_a = self._new_volatility(sigma_a, phi_a, v_a, delta_a)
        sigma_star_a = math.sqrt(sigma_a**2 + new_phi_a**2)
        new_sigma_a = 1.0 / math.sqrt(1.0/sigma_star_a**2 + 1.0/v_a)
        new_mu_a = (mu_a - home_advantage/Glicko2Player.SCALE_FACTOR) + \
                   new_sigma_a**2 * self._g(sigma_b) * (score_a - self._E(mu_a, mu_b, sigma_b))
        
        # Update team B (inverse result)
        score_b = 1 - score_a
        v_b = self._compute_v(mu_b, [(mu_a, sigma_a, score_b)])
        delta_b = self._compute_delta(mu_b, v_b, [(mu_a, sigma_a, score_b)])
        new_phi_b = self._new_volatility(sigma_b, phi_b, v_b, delta_b)
        sigma_star_b = math.sqrt(sigma_b**2 + new_phi_b**2)
        new_sigma_b = 1.0 / math.sqrt(1.0/sigma_star_b**2 + 1.0/v_b)
        new_mu_b = mu_b + new_sigma_b**2 * self._g(sigma_a) * (score_b - self._E(mu_b, mu_a, sigma_a))
        
        # Convert back and update
        mu1, sigma1 = rating_a.to_glicko1(new_mu_a, new_sigma_a)
        mu2, sigma2 = rating_b.to_glicko1(new_mu_b, new_sigma_b)
        
        self.ratings[team_a] = Glicko2Player(mu=mu1, sigma=sigma1, phi=new_phi_a)
        self.ratings[team_b] = Glicko2Player(mu=mu2, sigma=sigma2, phi=new_phi_b)
    
    def decay_ratings(self, inactive_periods: int = 1):
        """Increase uncertainty for all teams (e.g., between seasons)"""
        for team_id in self.ratings:
            rating = self.ratings[team_id]
            # Increase sigma for inactivity
            new_sigma = math.sqrt(rating.sigma**2 + (rating.phi * 100)**2 * inactive_periods)
            new_sigma = min(350.0, new_sigma)  # Cap at initial RD
            self.ratings[team_id] = Glicko2Player(
                mu=rating.mu, sigma=new_sigma, phi=rating.phi
            )
    
    def get_features(self, team_a: str, team_b: str) -> Dict[str, float]:
        """
        Extract features for ML models
        
        This is how Glicko-2 feeds into XGBoost/CatBoost/LightGBM:
        - Rating difference (mu_a - mu_b)
        - Combined uncertainty
        - Volatility indicators
        """
        rating_a = self.get_rating(team_a)
        rating_b = self.get_rating(team_b)
        
        return {
            'glicko2_rating_diff': rating_a.mu - rating_b.mu,
            'glicko2_mu_a': rating_a.mu,
            'glicko2_mu_b': rating_b.mu,
            'glicko2_sigma_a': rating_a.sigma,
            'glicko2_sigma_b': rating_b.sigma,
            'glicko2_combined_uncertainty': math.sqrt(rating_a.sigma**2 + rating_b.sigma**2),
            'glicko2_phi_a': rating_a.phi,
            'glicko2_phi_b': rating_b.phi,
            'glicko2_combined_volatility': (rating_a.phi + rating_b.phi) / 2,
            'glicko2_confidence': 1.0 - min(1.0, math.sqrt(rating_a.sigma**2 + rating_b.sigma**2) / 700),
        }
    
    def save(self, filepath: str):
        """Save ratings to file"""
        import pickle
        save_dict = {
            'initial_rating': self.initial_rating,
            'initial_rd': self.initial_rd,
            'ratings': self.ratings,
        }
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
    
    @classmethod
    def load(cls, filepath: str) -> 'Glicko2Rating':
        """Load ratings from file"""
        import pickle
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        g2 = cls(save_dict['initial_rating'], save_dict['initial_rd'])
        g2.ratings = save_dict['ratings']
        return g2


class MarginRating:
    """
    Margin-Adjusted Rating System
    -----------------------------
    
    Why margin matters:
    - A team winning 35-7 is demonstrating more dominance than 14-10
    - Captures offensive AND defensive quality
    - Better predicts future point totals and spreads
    
    This is NOT a replacement for Glicko-2, but a complementary signal.
    """
    
    # Sport-specific margin caps to prevent blowouts from over-influencing
    MARGIN_CAPS = {
        'NFL': 21,
        'NBA': 25,
        'NHL': 5,
        'MLB': 6,
        'NCAAF': 28,
        'NCAAB': 25,
        'WNBA': 20,
    }
    
    # K-factors for different sports
    K_FACTORS = {
        'NFL': 20,
        'NBA': 15,
        'NHL': 10,
        'MLB': 8,
        'NCAAF': 20,
        'NCAAB': 15,
        'WNBA': 15,
    }
    
    def __init__(self, sport: str, initial_rating: float = 0.0):
        self.sport = sport
        self.initial_rating = initial_rating
        self.ratings: Dict[str, float] = {}
        self.margin_cap = self.MARGIN_CAPS.get(sport, 15)
        self.k_factor = self.K_FACTORS.get(sport, 15)
        
        # Track offensive and defensive components separately
        self.offensive_ratings: Dict[str, float] = {}
        self.defensive_ratings: Dict[str, float] = {}
        
    def get_rating(self, team_id: str) -> Tuple[float, float, float]:
        """Get (overall, offensive, defensive) ratings"""
        if team_id not in self.ratings:
            self.ratings[team_id] = self.initial_rating
            self.offensive_ratings[team_id] = 0.0
            self.defensive_ratings[team_id] = 0.0
        
        return (
            self.ratings[team_id],
            self.offensive_ratings.get(team_id, 0.0),
            self.defensive_ratings.get(team_id, 0.0)
        )
    
    def _cap_margin(self, margin: float) -> float:
        """Cap margin to prevent blowouts from over-influencing"""
        return max(-self.margin_cap, min(self.margin_cap, margin))
    
    def update_ratings(self, home_team: str, away_team: str,
                       home_score: float, away_score: float,
                       home_advantage: float = 0.0):
        """
        Update ratings based on actual margin of victory
        """
        # Initialize if needed
        for team in [home_team, away_team]:
            if team not in self.ratings:
                self.ratings[team] = self.initial_rating
                self.offensive_ratings[team] = 0.0
                self.defensive_ratings[team] = 0.0
        
        # Calculate actual margin (positive = home team won)
        actual_margin = home_score - away_score
        capped_margin = self._cap_margin(actual_margin)
        
        # Expected margin based on current ratings + home advantage
        expected_margin = (self.ratings[home_team] - self.ratings[away_team]) + home_advantage
        
        # Margin error
        margin_error = capped_margin - expected_margin
        
        # Update overall ratings (towards actual margin)
        self.ratings[home_team] += self.k_factor * margin_error / 100
        self.ratings[away_team] -= self.k_factor * margin_error / 100
        
        # Update offensive ratings (based on points scored vs league avg)
        league_avg_points = self._get_league_avg_points()
        home_off_adj = (home_score - league_avg_points) / league_avg_points
        away_off_adj = (away_score - league_avg_points) / league_avg_points
        
        self.offensive_ratings[home_team] = 0.9 * self.offensive_ratings[home_team] + 0.1 * home_off_adj
        self.offensive_ratings[away_team] = 0.9 * self.offensive_ratings[away_team] + 0.1 * away_off_adj
        
        # Update defensive ratings (based on points allowed vs league avg)
        self.defensive_ratings[home_team] = 0.9 * self.defensive_ratings[home_team] - 0.1 * away_off_adj
        self.defensive_ratings[away_team] = 0.9 * self.defensive_ratings[away_team] - 0.1 * home_off_adj
    
    def _get_league_avg_points(self) -> float:
        """Get league average points per team per game"""
        avgs = {
            'NFL': 23,
            'NBA': 115,
            'NHL': 3.0,
            'MLB': 4.5,
            'NCAAF': 28,
            'NCAAB': 72,
            'WNBA': 80,
        }
        return avgs.get(self.sport, 50)
    
    def predict_margin(self, home_team: str, away_team: str,
                       home_advantage: float = 0.0) -> Tuple[float, float, float]:
        """
        Predict expected margin, home score, away score
        
        Returns: (expected_margin, predicted_home_score, predicted_away_score)
        """
        home_overall, home_off, home_def = self.get_rating(home_team)
        away_overall, away_off, away_def = self.get_rating(away_team)
        
        # Expected margin from overall ratings
        expected_margin = (home_overall - away_overall) + home_advantage
        
        # Predicted scores using offensive/defensive components
        league_avg = self._get_league_avg_points()
        
        # Home score = home offense vs away defense
        home_score = league_avg * (1 + home_off) * (1 - away_def) + home_advantage * 0.3
        
        # Away score = away offense vs home defense
        away_score = league_avg * (1 + away_off) * (1 - home_def)
        
        return expected_margin, home_score, away_score
    
    def get_features(self, team_a: str, team_b: str) -> Dict[str, float]:
        """Extract features for ML models"""
        overall_a, off_a, def_a = self.get_rating(team_a)
        overall_b, off_b, def_b = self.get_rating(team_b)
        
        return {
            'margin_rating_diff': overall_a - overall_b,
            'margin_rating_a': overall_a,
            'margin_rating_b': overall_b,
            'offensive_rating_a': off_a,
            'offensive_rating_b': off_b,
            'defensive_rating_a': def_a,
            'defensive_rating_b': def_b,
            'offensive_matchup': off_a - def_b,  # A's offense vs B's defense
            'defensive_matchup': off_b - def_a,  # B's offense vs A's defense
            'net_rating_a': off_a + def_a,
            'net_rating_b': off_b + def_b,
        }
    
    def save(self, filepath: str):
        """Save ratings to file"""
        import pickle
        save_dict = {
            'sport': self.sport,
            'initial_rating': self.initial_rating,
            'ratings': self.ratings,
            'offensive_ratings': self.offensive_ratings,
            'defensive_ratings': self.defensive_ratings,
        }
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
    
    @classmethod
    def load(cls, filepath: str) -> 'MarginRating':
        """Load ratings from file"""
        import pickle
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        mr = cls(save_dict['sport'], save_dict['initial_rating'])
        mr.ratings = save_dict['ratings']
        mr.offensive_ratings = save_dict['offensive_ratings']
        mr.defensive_ratings = save_dict['defensive_ratings']
        return mr


class TrueSkillRating:
    """
    TrueSkill Rating System (Microsoft Research)
    ---------------------------------------------
    
    Why TrueSkill alongside Glicko-2:
    - Better for multiplayer/dynamic scenarios
    - Different uncertainty model (Gaussian belief propagation)
    - Converges faster than Glicko-2 for new teams
    - Uses mu (mean) and sigma (uncertainty) like Glicko-2
    
    Key difference from Glicko-2:
    - TrueSkill uses factor graphs and belief propagation
    - Glicko-2 uses analytical update formulas
    - TrueSkill is more computationally expensive but handles ties better
    """
    
    def __init__(self, mu: float = 25.0, sigma: float = 8.333, beta: float = 4.167, tau: float = 0.0833):
        """
        Initialize TrueSkill system
        
        Default values:
        - mu = 25 (initial skill mean)
        - sigma = 25/3 ≈ 8.333 (initial uncertainty)
        - beta = sigma/2 ≈ 4.167 (performance variation)
        - tau = sigma/100 ≈ 0.0833 (dynamics factor)
        """
        self.initial_mu = mu
        self.initial_sigma = sigma
        self.beta = beta
        self.tau = tau  # How much ratings drift between games
        
        # Store ratings as (mu, sigma) tuples
        self.ratings: Dict[str, Tuple[float, float]] = {}
        
    def get_rating(self, team_id: str) -> Tuple[float, float]:
        """Get (mu, sigma) for a team"""
        if team_id not in self.ratings:
            self.ratings[team_id] = (self.initial_mu, self.initial_sigma)
        return self.ratings[team_id]
    
    def _v_function(self, x: float, epsilon: float = 0.0) -> float:
        """V function for TrueSkill updates (truncated Gaussian)"""
        from scipy.stats import norm
        denom = norm.cdf(x - epsilon)
        if denom < 1e-10:
            return -x + epsilon
        return norm.pdf(x - epsilon) / denom
    
    def _w_function(self, x: float, epsilon: float = 0.0) -> float:
        """W function for TrueSkill updates"""
        v = self._v_function(x, epsilon)
        return v * (v + x - epsilon)
    
    def win_probability(self, team_a: str, team_b: str) -> float:
        """
        Calculate probability team_a beats team_b
        
        P(A > B) = Φ((mu_a - mu_b) / sqrt(2*beta^2 + sigma_a^2 + sigma_b^2))
        """
        from scipy.stats import norm
        mu_a, sigma_a = self.get_rating(team_a)
        mu_b, sigma_b = self.get_rating(team_b)
        
        c = math.sqrt(2 * self.beta**2 + sigma_a**2 + sigma_b**2)
        return norm.cdf((mu_a - mu_b) / c)
    
    def update_ratings(self, winner: str, loser: str, draw: bool = False):
        """
        Update ratings after a match
        
        Uses simplified TrueSkill update (1v1 case)
        """
        from scipy.stats import norm
        
        mu_w, sigma_w = self.get_rating(winner)
        mu_l, sigma_l = self.get_rating(loser)
        
        # Add dynamics factor (rating drift)
        sigma_w = math.sqrt(sigma_w**2 + self.tau**2)
        sigma_l = math.sqrt(sigma_l**2 + self.tau**2)
        
        # Calculate c (combined variance)
        c = math.sqrt(2 * self.beta**2 + sigma_w**2 + sigma_l**2)
        
        # Calculate t (normalized skill difference)
        t = (mu_w - mu_l) / c
        
        if draw:
            # Draw case (not common in most sports)
            epsilon = 0.0  # Draw margin
            v = self._v_function(t, epsilon) - self._v_function(-t, epsilon)
            w = self._w_function(t, epsilon) + self._w_function(-t, epsilon)
        else:
            # Win/Loss case
            v = self._v_function(t)
            w = self._w_function(t)
        
        # Update winner
        new_mu_w = mu_w + (sigma_w**2 / c) * v
        new_sigma_w = sigma_w * math.sqrt(1 - (sigma_w**2 / c**2) * w)
        
        # Update loser
        new_mu_l = mu_l - (sigma_l**2 / c) * v
        new_sigma_l = sigma_l * math.sqrt(1 - (sigma_l**2 / c**2) * w)
        
        # Clamp sigma to prevent collapse
        new_sigma_w = max(1.0, new_sigma_w)
        new_sigma_l = max(1.0, new_sigma_l)
        
        self.ratings[winner] = (new_mu_w, new_sigma_w)
        self.ratings[loser] = (new_mu_l, new_sigma_l)
    
    def update_with_margin(self, winner: str, loser: str, margin: float, max_margin: float = 10.0):
        """
        Update ratings with margin of victory boost
        
        Larger margins = more confident update
        """
        from scipy.stats import norm
        
        mu_w, sigma_w = self.get_rating(winner)
        mu_l, sigma_l = self.get_rating(loser)
        
        # Margin multiplier (capped, logarithmic)
        margin_factor = 1.0 + 0.5 * math.log(1 + min(margin, max_margin))
        
        sigma_w = math.sqrt(sigma_w**2 + self.tau**2)
        sigma_l = math.sqrt(sigma_l**2 + self.tau**2)
        
        c = math.sqrt(2 * self.beta**2 + sigma_w**2 + sigma_l**2)
        t = (mu_w - mu_l) / c
        
        v = self._v_function(t)
        w = self._w_function(t)
        
        # Apply margin factor to skill update (not uncertainty)
        new_mu_w = mu_w + margin_factor * (sigma_w**2 / c) * v
        new_sigma_w = sigma_w * math.sqrt(1 - (sigma_w**2 / c**2) * w)
        
        new_mu_l = mu_l - margin_factor * (sigma_l**2 / c) * v
        new_sigma_l = sigma_l * math.sqrt(1 - (sigma_l**2 / c**2) * w)
        
        new_sigma_w = max(1.0, new_sigma_w)
        new_sigma_l = max(1.0, new_sigma_l)
        
        self.ratings[winner] = (new_mu_w, new_sigma_w)
        self.ratings[loser] = (new_mu_l, new_sigma_l)
    
    def get_features(self, team_a: str, team_b: str) -> Dict[str, float]:
        """Extract TrueSkill features for ML models"""
        mu_a, sigma_a = self.get_rating(team_a)
        mu_b, sigma_b = self.get_rating(team_b)
        
        # Conservative skill estimate (mu - 3*sigma)
        conservative_a = mu_a - 3 * sigma_a
        conservative_b = mu_b - 3 * sigma_b
        
        return {
            'trueskill_mu_a': mu_a,
            'trueskill_mu_b': mu_b,
            'trueskill_mu_diff': mu_a - mu_b,
            'trueskill_sigma_a': sigma_a,
            'trueskill_sigma_b': sigma_b,
            'trueskill_combined_sigma': math.sqrt(sigma_a**2 + sigma_b**2),
            'trueskill_conservative_a': conservative_a,
            'trueskill_conservative_b': conservative_b,
            'trueskill_conservative_diff': conservative_a - conservative_b,
            'trueskill_win_prob': self.win_probability(team_a, team_b),
        }
    
    def save(self, filepath: str):
        """Save ratings to file"""
        import pickle
        save_dict = {
            'initial_mu': self.initial_mu,
            'initial_sigma': self.initial_sigma,
            'beta': self.beta,
            'tau': self.tau,
            'ratings': self.ratings,
        }
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
    
    @classmethod
    def load(cls, filepath: str) -> 'TrueSkillRating':
        """Load ratings from file"""
        import pickle
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        ts = cls(
            mu=save_dict['initial_mu'],
            sigma=save_dict['initial_sigma'],
            beta=save_dict['beta'],
            tau=save_dict['tau']
        )
        ts.ratings = save_dict['ratings']
        return ts


class EloFeatureGenerator:
    """
    Elo Rating as Feature Generator (DEMOTED from predictor)
    --------------------------------------------------------
    
    Why demote Elo:
    - No uncertainty tracking (treats all predictions equally confident)
    - Fixed K-factor doesn't adapt to team volatility
    - Probabilities are poorly calibrated
    
    Elo is still useful as a FEATURE because:
    - Simple, interpretable baseline
    - Captures overall team strength
    - Decades of evidence it works as a component
    """
    
    K_FACTORS = {
        'NFL': 20,
        'NBA': 20,
        'NHL': 10,
        'MLB': 5,
        'NCAAF': 20,
        'NCAAB': 20,
        'WNBA': 20,
    }
    
    def __init__(self, sport: str, initial_rating: float = 1500.0):
        self.sport = sport
        self.initial_rating = initial_rating
        self.ratings: Dict[str, float] = {}
        self.k_factor = self.K_FACTORS.get(sport, 15)
        
    def get_rating(self, team_id: str) -> float:
        return self.ratings.get(team_id, self.initial_rating)
    
    def expected_score(self, rating_a: float, rating_b: float) -> float:
        """Standard Elo expected score"""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))
    
    def update_ratings(self, team_a: str, team_b: str, score_a: float):
        """Update ratings after game"""
        rating_a = self.get_rating(team_a)
        rating_b = self.get_rating(team_b)
        
        expected_a = self.expected_score(rating_a, rating_b)
        
        self.ratings[team_a] = rating_a + self.k_factor * (score_a - expected_a)
        self.ratings[team_b] = rating_b + self.k_factor * ((1 - score_a) - (1 - expected_a))
    
    def get_features(self, team_a: str, team_b: str) -> Dict[str, float]:
        """Extract Elo features for ML models"""
        rating_a = self.get_rating(team_a)
        rating_b = self.get_rating(team_b)
        
        return {
            'elo_rating_diff': rating_a - rating_b,
            'elo_rating_a': rating_a,
            'elo_rating_b': rating_b,
            'elo_expected_score': self.expected_score(rating_a, rating_b),
        }
    
    def save(self, filepath: str):
        """Save ratings to file"""
        import pickle
        save_dict = {
            'sport': self.sport,
            'initial_rating': self.initial_rating,
            'ratings': self.ratings,
        }
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
    
    @classmethod
    def load(cls, filepath: str) -> 'EloFeatureGenerator':
        """Load ratings from file"""
        import pickle
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        elo = cls(save_dict['sport'], save_dict['initial_rating'])
        elo.ratings = save_dict['ratings']
        return elo
