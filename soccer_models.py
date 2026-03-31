from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
from collections import defaultdict
from datetime import datetime
import math


def _parse_game_date(game: dict) -> Optional[datetime]:
    raw = game.get('game_date') or game.get('date')
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8', errors='ignore')
        except Exception:
            return None
    raw_str = str(raw)
    if len(raw_str) >= 10:
        raw_str = raw_str[:10]
    try:
        return datetime.fromisoformat(raw_str)
    except Exception:
        return None


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))
    except Exception:
        return 0.0


def _win_draw_loss_from_lambdas(lam_home: float, lam_away: float, max_goals: int = 8) -> Tuple[float, float, float]:
    home_win = 0.0
    draw = 0.0
    away_win = 0.0
    for h in range(max_goals + 1):
        p_h = _poisson_pmf(h, lam_home)
        for a in range(max_goals + 1):
            p = p_h * _poisson_pmf(a, lam_away)
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
    total = home_win + draw + away_win
    if total > 0:
        home_win /= total
        draw /= total
        away_win /= total
    return home_win, draw, away_win


def _extract_scores(game: dict) -> Tuple[Optional[float], Optional[float]]:
    return game.get('home_score'), game.get('away_score')


def _extract_teams(game: dict) -> Tuple[Optional[str], Optional[str]]:
    home = game.get('home_team_id') or game.get('home_team')
    away = game.get('away_team_id') or game.get('away_team')
    return home, away


def _summarize_team_stats(games: List[dict]) -> Tuple[Dict[str, dict], float, float, float]:
    totals = defaultdict(lambda: {'scored': 0.0, 'allowed': 0.0, 'games': 0})
    total_goals = 0.0
    total_home_goals = 0.0
    total_away_goals = 0.0
    game_count = 0

    for game in games:
        home, away = _extract_teams(game)
        home_score, away_score = _extract_scores(game)
        if home is None or away is None:
            continue
        if home_score is None or away_score is None:
            continue
        try:
            hs = float(home_score)
            aw = float(away_score)
        except Exception:
            continue
        totals[home]['scored'] += hs
        totals[home]['allowed'] += aw
        totals[home]['games'] += 1
        totals[away]['scored'] += aw
        totals[away]['allowed'] += hs
        totals[away]['games'] += 1
        total_goals += hs + aw
        total_home_goals += hs
        total_away_goals += aw
        game_count += 1

    if game_count == 0:
        return {}, 0.0, 0.0, 0.0
    league_avg = total_goals / (2 * game_count)
    home_gpg = total_home_goals / game_count
    away_gpg = total_away_goals / game_count
    return totals, league_avg, home_gpg, away_gpg


class PoissonXGModel:
    def __init__(self):
        self.team_stats: Dict[str, dict] = {}
        self.league_avg = 1.2
        self.home_adv_goals = 0.2
        self.min_team_games = 3

    def fit(self, games: List[dict]):
        team_stats, league_avg, home_gpg, away_gpg = _summarize_team_stats(games)
        if team_stats:
            self.team_stats = team_stats
        if league_avg > 0:
            self.league_avg = league_avg
        diff = home_gpg - away_gpg
        self.home_adv_goals = max(0.0, min(0.6, diff))

    def _team_rates(self, team: str) -> Tuple[float, float]:
        stats = self.team_stats.get(team)
        if not stats or stats['games'] < self.min_team_games:
            return self.league_avg, self.league_avg
        return stats['scored'] / stats['games'], stats['allowed'] / stats['games']

    def predict(self, home: str, away: str) -> dict:
        home_off, home_def = self._team_rates(home)
        away_off, away_def = self._team_rates(away)
        exp_home = max(0.1, (home_off + away_def) / 2 + self.home_adv_goals)
        exp_away = max(0.1, (away_off + home_def) / 2)
        home_win, draw, away_win = _win_draw_loss_from_lambdas(exp_home, exp_away)
        return {
            'expected_home': exp_home,
            'expected_away': exp_away,
            'home_win': home_win,
            'draw': draw,
            'away_win': away_win,
        }


class PoissonRegressionModel:
    def __init__(self):
        self.attack: Dict[str, float] = {}
        self.defense: Dict[str, float] = {}
        self.league_avg = 1.2
        self.home_adv_factor = 0.1

    def fit(self, games: List[dict], iterations: int = 15):
        team_stats, league_avg, home_gpg, away_gpg = _summarize_team_stats(games)
        teams = list(team_stats.keys())
        if not teams:
            return
        self.league_avg = max(0.2, league_avg)
        if away_gpg > 0:
            self.home_adv_factor = max(-0.1, min(0.35, (home_gpg / away_gpg) - 1.0))
        else:
            self.home_adv_factor = 0.1
        self.attack = {team: 1.0 for team in teams}
        self.defense = {team: 1.0 for team in teams}

        for _ in range(iterations):
            for team in teams:
                goals_scored = team_stats[team]['scored']
                exp_goals = 0.0
                for game in games:
                    home, away = _extract_teams(game)
                    if home == team:
                        opp = away
                        exp_goals += self.league_avg * (1 + self.home_adv_factor) * self.defense.get(opp, 1.0)
                    elif away == team:
                        opp = home
                        exp_goals += self.league_avg * self.defense.get(opp, 1.0)
                if exp_goals > 0:
                    self.attack[team] = max(0.2, min(3.0, goals_scored / exp_goals))

            for team in teams:
                goals_allowed = team_stats[team]['allowed']
                exp_allowed = 0.0
                for game in games:
                    home, away = _extract_teams(game)
                    if home == team:
                        opp = away
                        exp_allowed += self.league_avg * self.attack.get(opp, 1.0)
                    elif away == team:
                        opp = home
                        exp_allowed += self.league_avg * (1 + self.home_adv_factor) * self.attack.get(opp, 1.0)
                if exp_allowed > 0:
                    self.defense[team] = max(0.2, min(3.0, goals_allowed / exp_allowed))

    def predict_expected(self, home: str, away: str) -> Tuple[float, float]:
        home_attack = self.attack.get(home, 1.0)
        away_attack = self.attack.get(away, 1.0)
        home_def = self.defense.get(home, 1.0)
        away_def = self.defense.get(away, 1.0)
        exp_home = max(0.1, self.league_avg * (1 + self.home_adv_factor) * home_attack * away_def)
        exp_away = max(0.1, self.league_avg * away_attack * home_def)
        return exp_home, exp_away

    def predict(self, home: str, away: str) -> dict:
        exp_home, exp_away = self.predict_expected(home, away)
        home_win, draw, away_win = _win_draw_loss_from_lambdas(exp_home, exp_away)
        return {
            'expected_home': exp_home,
            'expected_away': exp_away,
            'home_win': home_win,
            'draw': draw,
            'away_win': away_win,
        }


class MarkovChainModel:
    def __init__(self, minutes: int = 90, max_goals: int = 8):
        self.minutes = minutes
        self.max_goals = max_goals

    def _goal_distribution(self, expected_goals: float) -> List[float]:
        p = expected_goals / max(self.minutes, 1)
        p = max(0.0001, min(0.2, p))
        dist = [0.0] * (self.max_goals + 1)
        dist[0] = 1.0
        for _ in range(self.minutes):
            new = [0.0] * (self.max_goals + 1)
            for g in range(self.max_goals + 1):
                stay = dist[g] * (1 - p)
                score = dist[g - 1] * p if g > 0 else 0.0
                new[g] += stay + score
            new[self.max_goals] += dist[self.max_goals] * p
            dist = new
        return dist

    def predict(self, expected_home: float, expected_away: float) -> dict:
        home_dist = self._goal_distribution(expected_home)
        away_dist = self._goal_distribution(expected_away)
        home_win = 0.0
        draw = 0.0
        away_win = 0.0
        for h, ph in enumerate(home_dist):
            for a, pa in enumerate(away_dist):
                p = ph * pa
                if h > a:
                    home_win += p
                elif h == a:
                    draw += p
                else:
                    away_win += p
        total = home_win + draw + away_win
        if total > 0:
            home_win /= total
            draw /= total
            away_win /= total
        exp_home = sum(idx * prob for idx, prob in enumerate(home_dist))
        exp_away = sum(idx * prob for idx, prob in enumerate(away_dist))
        return {
            'expected_home': exp_home,
            'expected_away': exp_away,
            'home_win': home_win,
            'draw': draw,
            'away_win': away_win,
        }


class SoccerEloModel:
    def __init__(self, k_factor: float = 22.0):
        self.k_factor = k_factor
        self.ratings: Dict[str, float] = defaultdict(lambda: 1500.0)
        self.draw_rate = 0.25
        self.home_adv = 50.0

    def fit(self, games: List[dict]):
        if not games:
            return
        draws = 0
        home_wins = 0
        away_wins = 0
        total = 0
        for game in games:
            home_score, away_score = _extract_scores(game)
            if home_score is None or away_score is None:
                continue
            total += 1
            if home_score > away_score:
                home_wins += 1
            elif home_score < away_score:
                away_wins += 1
            else:
                draws += 1
        if total > 0:
            self.draw_rate = max(0.12, min(0.42, draws / total))
            adv_raw = (home_wins - away_wins) / total
            self.home_adv = max(10.0, min(90.0, adv_raw * 200.0))

        games_sorted = sorted(games, key=lambda g: _parse_game_date(g) or datetime.min)
        for game in games_sorted:
            home, away = _extract_teams(game)
            if not home or not away:
                continue
            home_score, away_score = _extract_scores(game)
            if home_score is None or away_score is None:
                continue
            actual = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
            exp_home, draw_prob, _ = self.predict(home, away)
            expected = exp_home + draw_prob * 0.5
            delta = self.k_factor * (actual - expected)
            self.ratings[home] += delta
            self.ratings[away] -= delta

    def _draw_prob(self, diff: float) -> float:
        closeness = max(0.0, 1 - min(1.0, abs(diff) / 400.0))
        base = self.draw_rate
        return max(0.1, min(0.45, base * (0.6 + 0.8 * closeness)))

    def predict(self, home: str, away: str) -> Tuple[float, float, float]:
        home_rating = self.ratings.get(home, 1500.0) + self.home_adv
        away_rating = self.ratings.get(away, 1500.0)
        diff = home_rating - away_rating
        expected_home = 1 / (1 + 10 ** (-diff / 400))
        draw_prob = self._draw_prob(diff)
        home_win = expected_home * (1 - draw_prob)
        away_win = (1 - expected_home) * (1 - draw_prob)
        return home_win, draw_prob, away_win


@dataclass
class SoccerModelBundle:
    ready: bool
    reason: Optional[str]
    games_count: int
    league_name: Optional[str]
    poisson_xg: Optional[PoissonXGModel] = None
    poisson_reg: Optional[PoissonRegressionModel] = None
    markov: Optional[MarkovChainModel] = None
    elo: Optional[SoccerEloModel] = None

    def predict(self, home: str, away: str) -> Optional[dict]:
        if not self.ready or not self.poisson_xg or not self.poisson_reg or not self.markov or not self.elo:
            return None
        xg = self.poisson_xg.predict(home, away)
        reg = self.poisson_reg.predict(home, away)
        elo_home, elo_draw, elo_away = self.elo.predict(home, away)
        markov = self.markov.predict(reg['expected_home'], reg['expected_away'])

        def _to_home_win(prob_dict: dict) -> float:
            return prob_dict['home_win'] + 0.5 * prob_dict['draw']

        xg_home = _to_home_win(xg)
        reg_home = _to_home_win(reg)
        markov_home = _to_home_win(markov)
        elo_home_win = elo_home + 0.5 * elo_draw

        weights = {
            'xg': (xg_home, 0.25),
            'reg': (reg_home, 0.25),
            'markov': (markov_home, 0.20),
            'elo': (elo_home_win, 0.30),
        }
        total_weight = sum(w for _, w in weights.values())
        ensemble = sum(p * w for p, w in weights.values()) / total_weight if total_weight > 0 else None

        return {
            'poisson_xg_prob': xg_home,
            'poisson_reg_prob': reg_home,
            'markov_prob': markov_home,
            'elo_prob': elo_home_win,
            'ensemble_prob': ensemble,
            'expected_home_score': reg['expected_home'],
            'expected_away_score': reg['expected_away'],
            'draw_prob': (xg['draw'] + reg['draw'] + markov['draw'] + elo_draw) / 4,
        }


def build_soccer_model_bundle(
    games: List[dict],
    min_games: int = 12,
    league_name: Optional[str] = None
) -> SoccerModelBundle:
    cleaned = []
    seen = set()
    for game in games:
        home, away = _extract_teams(game)
        home_score, away_score = _extract_scores(game)
        if not home or not away:
            continue
        if home_score is None or away_score is None:
            continue
        game_date = _parse_game_date(game)
        date_key = game_date.strftime('%Y-%m-%d') if game_date else ''
        game_id = game.get('game_id') or ''
        key = (game_id, date_key, home, away, home_score, away_score)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(game)

    games_count = len(cleaned)
    league_label = league_name or 'Soccer'
    if games_count < min_games:
        reason = (
            f"N/A — soccer models need at least {min_games} completed games for {league_label}; "
            f"only {games_count} available."
        )
        return SoccerModelBundle(
            ready=False,
            reason=reason,
            games_count=games_count,
            league_name=league_name,
        )

    poisson_xg = PoissonXGModel()
    poisson_xg.fit(cleaned)
    poisson_reg = PoissonRegressionModel()
    poisson_reg.fit(cleaned)
    markov = MarkovChainModel()
    elo = SoccerEloModel()
    elo.fit(cleaned)

    return SoccerModelBundle(
        ready=True,
        reason=None,
        games_count=games_count,
        league_name=league_name,
        poisson_xg=poisson_xg,
        poisson_reg=poisson_reg,
        markov=markov,
        elo=elo,
    )
