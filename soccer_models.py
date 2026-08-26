from __future__ import annotations

"""Soccer model bundle: Grinder2 / Takedown / Edge / XSharp / Sharp Consensus.

Published slots (restore product intent — not one shared λ):
  Grinder2        = Elo 1X2  (SoccerEloModel)
  Takedown        = form / GD / soft-xG  (TakedownIndependent)
  Edge            = book no-vig 1X2 when moneylines exist, else EdgeEloPlus
  XSharp          = Poisson attack/defense λ 1X2  (PoissonRegressionModel)
  Sharp Consensus = probability-weighted average of the independent bases
                    (never Efficiency; never copy a missing neighbor)

Efficiency is NOT part of this module and must never be edited here.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
from collections import defaultdict
from datetime import datetime
import math
import os
import re
import unicodedata


def _fold_team_name(name: str) -> str:
    txt = unicodedata.normalize('NFKD', str(name or ''))
    txt = ''.join(c for c in txt if not unicodedata.combining(c))
    txt = txt.lower().replace('&', 'and')
    txt = re.sub(r'[^a-z0-9]+', '', txt)
    return txt


# ESPN scoreboard short names → common DB / long names (folded).
_TEAM_NAME_ALIASES = {
    'atleticomg': ('atleticomineiro', 'clubeatleticomineiro', 'cam'),
    'atleticomineiro': ('atleticomg', 'clubeatleticomineiro'),
    'atleticopr': ('atleticoparanaense', 'athletico', 'athleticopr'),
    'athletico': ('atleticopr', 'atleticoparanaense'),
    'gremio': ('gremioportoalegre',),
    'inter': ('internacional', 'scinternacional'),
    'internacional': ('inter', 'scinternacional'),
}


_TEAM_NAME_TRIM = (
    'wanderers', 'united', 'city', 'town', 'hotspur', 'rovers', 'athletic',
    'atletico', 'deportivo', 'club', 'cf', 'fc', 'sc', 'ac', 'fk', 'afc',
)


def _team_core(name: Optional[str]) -> str:
    fold = _fold_team_name(name)
    if not fold:
        return ''
    changed = True
    while changed and len(fold) > 5:
        changed = False
        for suf in _TEAM_NAME_TRIM:
            if fold.endswith(suf) and len(fold) - len(suf) >= 5:
                fold = fold[: -len(suf)]
                changed = True
                break
        for pre in _TEAM_NAME_TRIM:
            if fold.startswith(pre) and len(fold) - len(pre) >= 5:
                fold = fold[len(pre):]
                changed = True
                break
    return fold


def _resolve_team_key(name: Optional[str], known: Dict[str, object]) -> Optional[str]:
    """Map a scoreboard name onto a fitted team key when spellings differ."""
    if not name:
        return name
    if name in known:
        return name
    fold = _fold_team_name(name)
    if not fold:
        return name
    core = _team_core(name)
    for key in known:
        kf = _fold_team_name(key)
        if kf == fold:
            return key
        kc = _team_core(key)
        if core and kc and core == kc:
            return key
        if fold and kf and len(fold) >= 6 and len(kf) >= 6 and (fold in kf or kf in fold):
            return key
    for alias in _TEAM_NAME_ALIASES.get(fold, ()):
        for key in known:
            if _fold_team_name(key) == alias or _team_core(key) == alias:
                return key
    return name


def _resolve_any(name: Optional[str], *dicts: Optional[Dict[str, object]]) -> Optional[str]:
    if not name:
        return name
    for d in dicts:
        if not d:
            continue
        resolved = _resolve_team_key(name, d)
        if resolved and resolved in d:
            return resolved
    return name


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


# Soccer score scale: club matches are ~1–4 goals/side, totals ~1.5–5.5.
# Tiny cup samples can otherwise peg attack at 3.0 and explode λ to ~10.
_SOCCER_GOAL_MIN = 0.15
_SOCCER_GOAL_MAX = 3.5
_SOCCER_TOTAL_MIN = 1.2
_SOCCER_TOTAL_MAX = 5.5


# ---------------------------------------------------------------------------
# Version flags / kill-switches
# TAKEDOWN_MODEL_VERSION: proto_v1 (default) | legacy | markov
# CONSENSUS_MODEL_VERSION: equal_weight_v1 (default; diversity-weighted rejected)
# ---------------------------------------------------------------------------
TAKEDOWN_MODEL_VERSION_DEFAULT = 'proto_v1'
CONSENSUS_MODEL_VERSION_DEFAULT = 'equal_weight_v1'
TAKEDOWN_MODEL_VERSION = (
    os.environ.get('TAKEDOWN_MODEL_VERSION') or TAKEDOWN_MODEL_VERSION_DEFAULT
).strip().lower()
CONSENSUS_MODEL_VERSION = (
    os.environ.get('CONSENSUS_MODEL_VERSION') or CONSENSUS_MODEL_VERSION_DEFAULT
).strip().lower()


def takedown_uses_legacy() -> bool:
    return TAKEDOWN_MODEL_VERSION in ('legacy', 'markov', 'markov_legacy', 'cur', 'current')


def _clamp_soccer_goals(exp_home: float, exp_away: float) -> Tuple[float, float]:
    """Keep projected goals on a soccer scale (not baseball runs)."""
    eh = max(_SOCCER_GOAL_MIN, min(_SOCCER_GOAL_MAX, float(exp_home)))
    ea = max(_SOCCER_GOAL_MIN, min(_SOCCER_GOAL_MAX, float(exp_away)))
    total = eh + ea
    if total > _SOCCER_TOTAL_MAX and total > 0:
        scale = _SOCCER_TOTAL_MAX / total
        eh, ea = eh * scale, ea * scale
    elif total < _SOCCER_TOTAL_MIN and total > 0:
        scale = _SOCCER_TOTAL_MIN / total
        eh, ea = eh * scale, ea * scale
    return eh, ea


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


def _dc_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1.0 + lam_h * rho
    if h == 1 and a == 0:
        return 1.0 + lam_a * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _win_draw_loss_dc(
    lam_h: float, lam_a: float, rho: float = 0.0, max_goals: int = 8
) -> Tuple[float, float, float]:
    hw = dw = aw = 0.0
    for h in range(max_goals + 1):
        ph = _poisson_pmf(h, lam_h)
        for a in range(max_goals + 1):
            pa = _poisson_pmf(a, lam_a)
            tau = _dc_tau(h, a, lam_h, lam_a, rho) if rho != 0.0 else 1.0
            p = max(0.0, ph * pa * tau)
            if h > a:
                hw += p
            elif h == a:
                dw += p
            else:
                aw += p
    s = hw + dw + aw
    if s <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return hw / s, dw / s, aw / s


def _extract_scores(game: dict) -> Tuple[Optional[float], Optional[float]]:
    return game.get('home_score'), game.get('away_score')


def _extract_teams(game: dict) -> Tuple[Optional[str], Optional[str]]:
    home = game.get('home_team_id') or game.get('home_team') or game.get('home')
    away = game.get('away_team_id') or game.get('away_team') or game.get('away')
    return home, away


def _league_key(game: dict) -> str:
    return str(game.get('league') or 'UNK')


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
        exp_home = (home_off + away_def) / 2 + self.home_adv_goals
        exp_away = (away_off + home_def) / 2
        exp_home, exp_away = _clamp_soccer_goals(exp_home, exp_away)
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
                    # Tight cap: cup/small samples otherwise peg attack at 3.0 → ~10 goals.
                    self.attack[team] = max(0.4, min(1.75, goals_scored / exp_goals))

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
                    self.defense[team] = max(0.4, min(1.75, goals_allowed / exp_allowed))

    def predict_expected(self, home: str, away: str) -> Tuple[float, float]:
        known_h = home in self.attack
        known_a = away in self.attack
        home_attack = self.attack.get(home, 1.0)
        away_attack = self.attack.get(away, 1.0)
        home_def = self.defense.get(home, 1.0)
        away_def = self.defense.get(away, 1.0)
        exp_home = self.league_avg * (1 + self.home_adv_factor) * home_attack * away_def
        exp_away = self.league_avg * away_attack * home_def
        eh, ea = _clamp_soccer_goals(exp_home, exp_away)
        # Both unknown → league-average dummy (the 1–2 / total 3 card bug).
        if not known_h and not known_a:
            return eh, ea
        return eh, ea

    def has_team_rates(self, home: str, away: str) -> bool:
        return bool(self.attack) and (home in self.attack or away in self.attack)

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
    """LEGACY Takedown (Markov thinning of Poisson-reg λ). Kept for rollback only."""

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


# Alias for explicit rollback imports / tests
MarkovChainModelLegacy = MarkovChainModel


class TakedownIndependent:
    """Independent Takedown_proto (not XSharp λ / not Markov thinning).

    Signals: form (L8), soft-xG shrink to league, independent GD rating, DC draw.
    Market residual path is intentionally omitted from production (Takedown_mktres REJECT).
    """

    def __init__(self, form_n: int = 8):
        self.form_n = form_n
        self.hist: Dict[str, List[dict]] = defaultdict(list)
        self.gd_rating: Dict[str, float] = defaultdict(lambda: 0.0)
        self.soft_att: Dict[str, float] = {}
        self.soft_def: Dict[str, float] = {}
        self.league_mu: Dict[str, float] = {}
        self.global_mu = 1.25
        self.home_adv = 0.22
        self.draw_rate = 0.26
        self.asof: Optional[datetime] = None

    def fit(self, games: List[dict], asof: Optional[datetime] = None):
        self.asof = asof or (_parse_game_date(games[-1]) if games else None)
        self.hist = defaultdict(list)
        self.gd_rating = defaultdict(lambda: 0.0)
        games_sorted = sorted(games, key=lambda g: _parse_game_date(g) or datetime.min)

        lg = defaultdict(lambda: {'g': 0.0, 'n': 0})
        draws = tot = 0
        for g in games_sorted:
            hs, aws = _extract_scores(g)
            home, away = _extract_teams(g)
            if hs is None or aws is None or not home or not away:
                continue
            try:
                hs_f, aws_f = float(hs), float(aws)
            except Exception:
                continue
            lk = _league_key(g)
            lg[lk]['g'] += hs_f + aws_f
            lg[lk]['n'] += 2
            tot += 1
            if hs_f == aws_f:
                draws += 1
            exp = 1.0 / (1.0 + math.exp(-(self.gd_rating[home] - self.gd_rating[away] + 0.18)))
            actual = 1.0 if hs_f > aws_f else (0.0 if hs_f < aws_f else 0.5)
            gd = hs_f - aws_f
            k = 0.35 * (1.0 + 0.4 * math.tanh(abs(gd) / 2.0))
            delta = k * (actual - exp)
            delta += 0.04 * math.tanh(gd - (self.gd_rating[home] - self.gd_rating[away]))
            self.gd_rating[home] += delta
            self.gd_rating[away] -= delta
            self.hist[home].append({
                'gf': hs_f, 'ga': aws_f,
                'pts': 3 if hs_f > aws_f else (1 if hs_f == aws_f else 0),
                'home': True, 'league': lk,
            })
            self.hist[away].append({
                'gf': aws_f, 'ga': hs_f,
                'pts': 3 if aws_f > hs_f else (1 if hs_f == aws_f else 0),
                'home': False, 'league': lk,
            })

        if tot:
            self.draw_rate = max(0.14, min(0.40, draws / tot))
        tot_g = tot_n = 0.0
        for lk, v in lg.items():
            if v['n']:
                self.league_mu[lk] = v['g'] / v['n']
                tot_g += v['g']
                tot_n += v['n']
        self.global_mu = (tot_g / tot_n) if tot_n else 1.25

        for t, matches in self.hist.items():
            if not matches:
                continue
            recent = matches[-20:]
            gf = sum(m['gf'] for m in recent) / len(recent)
            ga = sum(m['ga'] for m in recent) / len(recent)
            lk = recent[-1].get('league') or 'UNK'
            mu = self.league_mu.get(lk, self.global_mu)
            alpha = min(0.55, len(recent) / 40.0)
            self.soft_att[t] = alpha * gf + (1 - alpha) * mu
            self.soft_def[t] = alpha * ga + (1 - alpha) * mu

    def _form(self, team: str) -> Tuple[float, float]:
        ms = self.hist.get(team) or []
        if not ms:
            return 0.0, 0.0
        recent = ms[-self.form_n:]
        wsum = pts = gd = 0.0
        for i, m in enumerate(recent):
            w = 0.6 + 0.4 * (i + 1) / len(recent)
            pts += m['pts'] * w
            gd += (m['gf'] - m['ga']) * w
            wsum += w
        ppg = (pts / wsum) / 3.0
        gpg = gd / wsum
        return ppg, gpg

    def predict(self, home: str, away: str, league: Optional[str] = None) -> dict:
        mu = self.league_mu.get(league or 'UNK', self.global_mu)
        sa_h = self.soft_att.get(home, mu)
        sd_h = self.soft_def.get(home, mu)
        sa_a = self.soft_att.get(away, mu)
        sd_a = self.soft_def.get(away, mu)
        lam_h = 0.5 * (sa_h + sd_a) * (1 + self.home_adv)
        lam_a = 0.5 * (sa_a + sd_h)
        fph, fgh = self._form(home)
        fpa, fga = self._form(away)
        lam_h += 0.18 * fgh + 0.12 * (fph - 0.45)
        lam_a += 0.18 * fga + 0.12 * (fpa - 0.45)
        gdiff = self.gd_rating.get(home, 0.0) - self.gd_rating.get(away, 0.0)
        lam_h += 0.22 * math.tanh(gdiff)
        lam_a -= 0.22 * math.tanh(gdiff)
        lam_h, lam_a = _clamp_soccer_goals(lam_h, lam_a)
        closeness = math.exp(-abs(gdiff) * 1.2)
        rho = -0.04 - 0.06 * closeness
        hw, dw, aw = _win_draw_loss_dc(lam_h, lam_a, rho=rho)
        dw = 0.7 * dw + 0.3 * self.draw_rate * (0.7 + 0.6 * closeness)
        s = hw + dw + aw
        hw, dw, aw = hw / s, dw / s, aw / s
        return {
            'expected_home': lam_h,
            'expected_away': lam_a,
            'home_win': hw,
            'draw': dw,
            'away_win': aw,
            'gd_diff': gdiff,
            'form_home': fph,
            'form_away': fpa,
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


def _american_implied(odds: Any) -> Optional[float]:
    if odds is None or odds == "":
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    return (-o / (-o + 100.0)) if o < 0 else (100.0 / (o + 100.0))


def market_threeway(home_ml: Any, away_ml: Any) -> Optional[Tuple[float, float, float]]:
    """No-vig 2-way book ML plus a closeness draw. Independent of λ / Elo."""
    ih = _american_implied(home_ml)
    ia = _american_implied(away_ml)
    if ih is None or ia is None:
        return None
    s = ih + ia
    if s <= 0:
        return None
    ih, ia = ih / s, ia / s
    closeness = max(0.0, 1.0 - min(1.0, abs(ih - ia) / 0.50))
    dw = max(0.12, min(0.42, 0.26 * (0.55 + 0.90 * closeness)))
    hw = ih * (1.0 - dw)
    aw = ia * (1.0 - dw)
    tot = hw + dw + aw
    if tot <= 0:
        return None
    return hw / tot, dw / tot, aw / tot


class EdgeEloPlus:
    """Existing Edge formula when books are missing (prototypes/models.EdgeEloPlus)."""

    def __init__(self, k_base: float = 28.0, home_adv: float = 65.0, mov_scale: float = 1.0):
        self.k_base = k_base
        self.home_adv = home_adv
        self.mov_scale = mov_scale
        self.rating_home: Dict[str, float] = defaultdict(lambda: 1500.0)
        self.rating_away: Dict[str, float] = defaultdict(lambda: 1500.0)
        self.rating: Dict[str, float] = defaultdict(lambda: 1500.0)
        self.draw_rate = 0.26
        self.league_mean: Dict[str, float] = defaultdict(lambda: 1500.0)
        self.games_played: Dict[str, int] = defaultdict(int)

    def fit(self, games: List[dict], asof: Optional[datetime] = None):
        games_sorted = sorted(games, key=lambda g: _parse_game_date(g) or datetime.min)
        draws = hw = aw = tot = 0
        league_sums: Dict[str, List[float]] = defaultdict(list)
        for g in games_sorted:
            hs, aws = _extract_scores(g)
            if hs is None or aws is None:
                continue
            tot += 1
            if hs > aws:
                hw += 1
            elif hs < aws:
                aw += 1
            else:
                draws += 1
        if tot > 0:
            self.draw_rate = max(0.14, min(0.40, draws / tot))
            adv_raw = (hw - aw) / tot
            self.home_adv = max(35.0, min(100.0, 50.0 + adv_raw * 180.0))
        asof_dt = asof or (_parse_game_date(games_sorted[-1]) if games_sorted else None)
        for g in games_sorted:
            home, away = _extract_teams(g)
            hs, aws = _extract_scores(g)
            if hs is None or aws is None or not home or not away:
                continue
            lk = _league_key(g)
            for tid in (home, away):
                if self.games_played[tid] == 0:
                    init = self.league_mean.get(lk, 1500.0) - 40.0
                    self.rating[tid] = init
                    self.rating_home[tid] = init
                    self.rating_away[tid] = init
            age = 1.0
            gd = _parse_game_date(g)
            if gd is not None and asof_dt is not None:
                days = max(0.0, (asof_dt - gd).total_seconds() / 86400.0)
                age = 0.5 ** (days / 120.0)
            k = self.k_base * (0.7 + 0.6 * age)
            rh = 0.55 * self.rating_home[home] + 0.45 * self.rating[home] + self.home_adv
            ra = 0.55 * self.rating_away[away] + 0.45 * self.rating[away]
            diff = rh - ra
            exp = 1.0 / (1.0 + 10 ** (-diff / 400.0))
            actual = 1.0 if hs > aws else (0.0 if hs < aws else 0.5)
            mov_m = 1.0 + 0.55 * math.tanh(math.log1p(abs(hs - aws)) * self.mov_scale)
            k_eff = k * mov_m * (0.85 + 0.45 * abs(actual - exp))
            delta = k_eff * (actual - exp)
            self.rating[home] += delta
            self.rating[away] -= delta
            self.rating_home[home] += delta * 1.15
            self.rating_away[away] -= delta * 1.15
            self.rating_away[home] += delta * 0.35
            self.rating_home[away] -= delta * 0.35
            self.games_played[home] += 1
            self.games_played[away] += 1
            league_sums[lk].extend([self.rating[home], self.rating[away]])
        for lk, vals in league_sums.items():
            if vals:
                self.league_mean[lk] = sum(vals) / len(vals)

    def predict(self, home: str, away: str, league: Optional[str] = None) -> Tuple[float, float, float]:
        lk = league or "UNK"
        rh0 = self.league_mean.get(lk, 1500.0) - 40.0
        ra0 = self.league_mean.get(lk, 1500.0) - 40.0
        rh = 0.55 * self.rating_home.get(home, rh0) + 0.45 * self.rating.get(home, rh0) + self.home_adv
        ra = 0.55 * self.rating_away.get(away, ra0) + 0.45 * self.rating.get(away, ra0)
        diff = rh - ra
        p_home_binary = 1.0 / (1.0 + 10 ** (-diff / 400.0))
        closeness = max(0.0, 1.0 - min(1.0, abs(diff) / 380.0))
        dp = max(0.12, min(0.42, self.draw_rate * (0.55 + 0.9 * closeness)))
        hw = p_home_binary * (1.0 - dp)
        aw = (1.0 - p_home_binary) * (1.0 - dp)
        return hw, dp, aw

    def asian_handicap(self, home: str, away: str, league: Optional[str] = None) -> float:
        """PL AH from EdgeElo+ ratings — not Poisson λ."""
        lk = league or "UNK"
        rh0 = self.league_mean.get(lk, 1500.0) - 40.0
        ra0 = self.league_mean.get(lk, 1500.0) - 40.0
        rh = 0.55 * self.rating_home.get(home, rh0) + 0.45 * self.rating.get(home, rh0) + self.home_adv
        ra = 0.55 * self.rating_away.get(away, ra0) + 0.45 * self.rating.get(away, ra0)
        gd = max(-2.5, min(2.5, ((rh - ra) / 400.0) * 1.25))
        return round(gd * 2) / 2.0


def _binary_home(hw: float, dw: float) -> float:
    return float(hw) + 0.5 * float(dw)


def _equal_weight_consensus_3way(components: List[Tuple[float, float, float]]) -> Tuple[float, float, float]:
    """Equal-weight average of distinct (hw, dp, aw) packs. Not diversity-weighted."""
    if not components:
        return 1 / 3, 1 / 3, 1 / 3
    n = len(components)
    hw = sum(c[0] for c in components) / n
    dp = sum(c[1] for c in components) / n
    aw = sum(c[2] for c in components) / n
    s = hw + dp + aw
    if s <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return hw / s, dp / s, aw / s


def weighted_consensus_3way(
    components: List[Tuple[float, float, float]],
    weights: Optional[List[float]] = None,
) -> Tuple[float, float, float]:
    """Probability-weighted 3-way average. Weights must be prior-OOS only."""
    if not components:
        return 1 / 3, 1 / 3, 1 / 3
    if not weights or len(weights) != len(components):
        return _equal_weight_consensus_3way(components)
    wts = [max(1e-6, float(w)) for w in weights]
    sw = sum(wts)
    wts = [w / sw for w in wts]
    hw = sum(c[0] * w for c, w in zip(components, wts))
    dp = sum(c[1] * w for c, w in zip(components, wts))
    aw = sum(c[2] * w for c, w in zip(components, wts))
    s = hw + dp + aw
    if s <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return hw / s, dp / s, aw / s


@dataclass
class SoccerModelBundle:
    ready: bool
    reason: Optional[str]
    games_count: int
    league_name: Optional[str]
    poisson_xg: Optional[PoissonXGModel] = None
    poisson_reg: Optional[PoissonRegressionModel] = None
    markov: Optional[MarkovChainModel] = None  # legacy Takedown only
    takedown: Optional[TakedownIndependent] = None  # proto Takedown
    elo: Optional[SoccerEloModel] = None
    edge_plus: Optional[EdgeEloPlus] = None
    takedown_model_version: str = TAKEDOWN_MODEL_VERSION_DEFAULT
    consensus_model_version: str = CONSENSUS_MODEL_VERSION_DEFAULT

    def predict(
        self,
        home: str,
        away: str,
        league: Optional[str] = None,
        consensus_weights: Optional[List[float]] = None,
        home_ml: Any = None,
        away_ml: Any = None,
    ) -> Optional[dict]:
        if not self.ready or not self.elo:
            return None
        home = _resolve_any(
            home,
            getattr(self.elo, 'ratings', None),
            getattr(self.poisson_reg, 'attack', None),
            getattr(self.edge_plus, 'rating', None),
            getattr(self.takedown, 'soft_att', None),
        )
        away = _resolve_any(
            away,
            getattr(self.elo, 'ratings', None),
            getattr(self.poisson_reg, 'attack', None),
            getattr(self.edge_plus, 'rating', None),
            getattr(self.takedown, 'soft_att', None),
        )
        use_legacy = takedown_uses_legacy() or (
            self.takedown_model_version in ('legacy', 'markov', 'markov_legacy', 'cur', 'current')
        )

        # XSharp = Poisson λ 1X2 (never flipped, never shared onto other slots).
        # Unknown clubs still get league-average λ so the chart is not "—";
        # xg_dummy keeps dummy 1–2 scorelines off the card.
        xs_dummy = True
        if self.poisson_reg is not None:
            reg = self.poisson_reg.predict(home, away)
            xs_dummy = not self.poisson_reg.has_team_rates(home, away)
        else:
            reg = {
                'expected_home': None, 'expected_away': None,
                'home_win': None, 'draw': None, 'away_win': None,
            }
        # Grinder2 = Elo 1X2. Both-unknown 1500 priors are not a real pick.
        g2_unknown = home not in self.elo.ratings and away not in self.elo.ratings
        if g2_unknown:
            elo_home = elo_draw = elo_away = None
        else:
            elo_home, elo_draw, elo_away = self.elo.predict(home, away)
        # Edge = books when present, else its own EdgeEloPlus (not SoccerElo)
        mkt = market_threeway(home_ml, away_ml)
        if mkt is not None:
            ed_home, ed_draw, ed_away = mkt
            edge_src = 'market'
        elif self.edge_plus is not None:
            ed_home, ed_draw, ed_away = self.edge_plus.predict(
                home, away, league=league or self.league_name
            )
            edge_src = 'elo_plus'
        else:
            ed_home = ed_draw = ed_away = None
            edge_src = None

        td = None
        td_version = self.takedown_model_version
        td_unknown = False
        if self.takedown is not None:
            td_unknown = home not in self.takedown.hist and away not in self.takedown.hist
        if use_legacy and self.markov and reg.get('expected_home') is not None and not xs_dummy:
            td = self.markov.predict(reg['expected_home'], reg['expected_away'])
            td_version = 'legacy_markov'
        elif not use_legacy and self.takedown and not td_unknown:
            lg = league or self.league_name
            td = self.takedown.predict(home, away, league=lg)
            td_version = 'proto_v1'

        # Per-component 3-way (Grinder2=Elo / Takedown / Edge / XSharp=λ)
        g2_hw, g2_dp, g2_aw = elo_home, elo_draw, elo_away
        if td:
            td_hw, td_dp, td_aw = td['home_win'], td['draw'], td['away_win']
        else:
            td_hw = td_dp = td_aw = None
        ed_hw, ed_dp, ed_aw = ed_home, ed_draw, ed_away
        xs_hw, xs_dp, xs_aw = reg.get('home_win'), reg.get('draw'), reg.get('away_win')
        # Keep league-average λ 1X2 for XSharp. Do not copy Grinder2 into this slot.

        g2_bin = _binary_home(g2_hw, g2_dp) if g2_hw is not None else None
        td_bin = _binary_home(td_hw, td_dp) if td_hw is not None else None
        ed_bin = _binary_home(ed_hw, ed_dp) if ed_hw is not None else None
        xs_bin = _binary_home(xs_hw, xs_dp) if xs_hw is not None else None

        # Sharp Consensus: average of *available* independent bases only.
        packs = []
        if g2_hw is not None:
            packs.append((g2_hw, g2_dp, g2_aw))
        if td_hw is not None:
            packs.append((td_hw, td_dp, td_aw))
        if xs_hw is not None:
            packs.append((xs_hw, xs_dp, xs_aw))
        pack_w = None
        if ed_hw is not None:
            packs.insert(min(2, len(packs)), (ed_hw, ed_dp, ed_aw))
            pack_w = consensus_weights
        elif consensus_weights and len(consensus_weights) == 4:
            pack_w = [consensus_weights[0], consensus_weights[1], consensus_weights[3]]
        if not packs:
            return None
        if pack_w and len(pack_w) == len(packs):
            c_hw, c_dp, c_aw = weighted_consensus_3way(packs, pack_w)
            cons_ver = 'oos_invll_v1'
        else:
            c_hw, c_dp, c_aw = _equal_weight_consensus_3way(packs)
            cons_ver = self.consensus_model_version or CONSENSUS_MODEL_VERSION_DEFAULT
        ensemble = _binary_home(c_hw, c_dp)

        eh = ea = None
        dc_hw = dc_dp = dc_aw = dc_bin = None
        if reg.get('expected_home') is not None and not xs_dummy:
            eh, ea = _clamp_soccer_goals(reg['expected_home'], reg['expected_away'])
            dc_hw, dc_dp, dc_aw = _win_draw_loss_dc(eh, ea, rho=-0.08)
            dc_bin = _binary_home(dc_hw, dc_dp)

        pl_spread = None
        if self.edge_plus is not None:
            try:
                pl_spread = self.edge_plus.asian_handicap(
                    home, away, league=league or self.league_name
                )
            except Exception:
                pl_spread = None

        return {
            'poisson_xg_prob': g2_bin,
            'poisson_reg_prob': xs_bin,
            'markov_prob': td_bin,  # Takedown slot (UI/DB key unchanged)
            'elo_prob': ed_bin,
            'ensemble_prob': ensemble,
            'expected_home_score': eh,
            'expected_away_score': ea,
            # Consensus draw (equal-weight of component draws)
            'draw_prob': c_dp,
            # Per-component draws for grading / 3-way reconstruction
            'glicko2_draw_prob': g2_dp,
            'trueskill_draw_prob': td_dp,
            'elo_draw_prob': ed_dp,
            'xgb_draw_prob': xs_dp,
            'ensemble_draw_prob': c_dp,
            'takedown_model_version': td_version,
            'consensus_model_version': cons_ver,
            'takedown_home_win': td_hw,
            'takedown_draw': td_dp,
            'takedown_away_win': td_aw,
            'glicko2_home_win': g2_hw,
            'glicko2_away_win': g2_aw,
            'xgb_home_win': xs_hw,
            'xgb_away_win': xs_aw,
            'elo_home_win': ed_hw,
            'elo_away_win': ed_aw,
            'ensemble_home_win': c_hw,
            'ensemble_draw': c_dp,
            'ensemble_away_win': c_aw,
            'dixon_coles_home_win': dc_hw,
            'dixon_coles_draw': dc_dp,
            'dixon_coles_away_win': dc_aw,
            'dixon_coles_prob': dc_bin,
            'edge_source': edge_src,
            'grinder2_formula': 'elo',
            'xsharp_formula': 'poisson_lambda',
            'takedown_formula': td_version,
            'pl_spread': pl_spread,
            'xg_dummy': bool(xs_dummy),
            'g2_unknown': bool(g2_unknown),
            'td_unknown': bool(td_unknown),
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
    td_ver = TAKEDOWN_MODEL_VERSION
    cons_ver = CONSENSUS_MODEL_VERSION
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
            takedown_model_version=td_ver,
            consensus_model_version=cons_ver,
        )

    poisson_xg = PoissonXGModel()
    poisson_xg.fit(cleaned)
    poisson_reg = PoissonRegressionModel()
    poisson_reg.fit(cleaned)
    elo = SoccerEloModel()
    elo.fit(cleaned)
    edge_plus = EdgeEloPlus()
    asof_fit = _parse_game_date(cleaned[-1]) if cleaned else None
    edge_plus.fit(cleaned, asof=asof_fit)

    markov = None
    takedown = None
    if takedown_uses_legacy():
        markov = MarkovChainModel()
    else:
        takedown = TakedownIndependent()
        asof = _parse_game_date(cleaned[-1]) if cleaned else None
        takedown.fit(cleaned, asof=asof)
        # Always keep legacy instance available for emergency in-process switch tests
        markov = MarkovChainModel()

    return SoccerModelBundle(
        ready=True,
        reason=None,
        games_count=games_count,
        league_name=league_name,
        poisson_xg=poisson_xg,
        poisson_reg=poisson_reg,
        markov=markov,
        takedown=takedown,
        elo=elo,
        edge_plus=edge_plus,
        takedown_model_version=td_ver,
        consensus_model_version=cons_ver,
    )


def two_way_side(hw: Any, dw: Any = 0.0, aw: Any = None) -> Optional[str]:
    """Independent 2-way H/A pick from a model's own 1X2. Never Draw. Never flip."""
    try:
        h = float(hw)
        a = float(aw) if aw is not None else None
        d = float(dw or 0.0)
    except (TypeError, ValueError):
        return None
    if h > 1.0 or (a is not None and a > 1.0) or d > 1.0:
        h, d = h / 100.0, d / 100.0
        if a is not None:
            a = a / 100.0
    if a is None:
        return "home" if h >= 0.5 else "away"
    den = h + a
    if den <= 1e-12:
        return None
    return "home" if (h / den) >= 0.5 else "away"


def diagnose_independence(rows: List[dict], *, warn_rate: float = 0.80) -> dict:
    """Admin/dev diagnostic: unique picks and distributions. Not user HTML.

    Each row may include pick sides under keys G2/TD/ED/XS (or home/away strings
    in glicko2_side/trueskill_side/elo_side/xgb_side).
    """
    n = 0
    all_base_same = 0
    uniq_counts: Dict[int, int] = defaultdict(int)
    pair_agree = defaultdict(int)
    labels = ("G2", "TD", "ED", "XS")
    alt = {
        "G2": ("glicko2_side", "grinder2_side"),
        "TD": ("trueskill_side", "takedown_side"),
        "ED": ("elo_side", "edge_side"),
        "XS": ("xgb_side", "xsharp_side"),
    }
    for row in rows or []:
        sides = []
        for lab in labels:
            val = row.get(lab)
            if not val:
                for k in alt[lab]:
                    val = row.get(k)
                    if val:
                        break
            if val in ("home", "away", "H", "A"):
                sides.append("H" if val in ("home", "H") else "A")
            else:
                sides.append(None)
        present = [s for s in sides if s]
        if len(present) < 2:
            continue
        n += 1
        u = len(set(present))
        uniq_counts[u] += 1
        if u == 1 and len(present) >= 3:
            all_base_same += 1
        pairs = (("G2", 0, "XS", 3), ("G2", 0, "ED", 2), ("TD", 1, "XS", 3), ("ED", 2, "XS", 3))
        for a, i, b, j in pairs:
            if sides[i] and sides[j]:
                pair_agree[f"{a}={b}"] += int(sides[i] == sides[j])
                pair_agree[f"{a}={b}_n"] += 1
    rate = (all_base_same / n) if n else 0.0
    warn = bool(n >= 8 and rate >= warn_rate)
    return {
        "n_comparable": n,
        "all_base_identical": all_base_same,
        "all_base_identical_rate": round(rate, 4),
        "unique_pick_counts": dict(uniq_counts),
        "pair_agree": dict(pair_agree),
        "warn": warn,
        "warn_message": (
            f"all-base identical rate {rate:.1%} on {n} games — collapse likely"
            if warn
            else None
        ),
    }


def overlay_independent_ml_on_games(
    games: List[dict],
    train_games: List[dict],
    *,
    book_lookup: Optional[Dict[str, dict]] = None,
    min_games: int = 12,
) -> dict:
    """Past-only recompute of soccer ML legs. Mutates game dicts. Spread/O/U untouched.

    Weekly refit: train on games strictly before each week start.
    Missing model → leave None (NO PREDICTION), never copy a neighbor.
    """
    book_lookup = book_lookup or {}
    dated: Dict[str, List[dict]] = defaultdict(list)
    for g in games or []:
        gd = _parse_game_date(g)
        if not gd:
            continue
        dated[gd.strftime("%Y-%m-%d")].append(g)
    train_sorted = sorted(
        [t for t in (train_games or []) if _parse_game_date(t)],
        key=lambda t: _parse_game_date(t) or datetime.min,
    )
    weeks = sorted({d[:10] for d in dated})
    diag_rows = []
    last_cut = None
    bundle = None
    for day in weeks:
        cut = day
        if bundle is None or last_cut is None or (datetime.fromisoformat(day) - datetime.fromisoformat(last_cut)).days >= 14:
            prior = [
                t
                for t in train_sorted
                if (_parse_game_date(t) or datetime.min).strftime("%Y-%m-%d") < cut
            ]
            if len(prior) > 2000:
                prior = prior[-2000:]
            bundle = build_soccer_model_bundle(prior, min_games=min_games)
            last_cut = cut
        for g in dated[day]:
            home = g.get("home") or g.get("home_team_id") or g.get("home_team")
            away = g.get("away") or g.get("away_team_id") or g.get("away_team")
            gid = str(g.get("game_id") or "")
            bl = book_lookup.get(gid) or {}
            pred = None
            if bundle and bundle.ready and home and away:
                pred = bundle.predict(
                    home,
                    away,
                    league=g.get("league"),
                    home_ml=bl.get("home") or bl.get("home_moneyline") or g.get("book_home_moneyline"),
                    away_ml=bl.get("away") or bl.get("away_moneyline") or g.get("book_away_moneyline"),
                )
            if not pred:
                continue
            g2 = pred.get("poisson_xg_prob")
            td = pred.get("markov_prob")
            ed = pred.get("elo_prob")
            xs = pred.get("poisson_reg_prob")
            sc = pred.get("ensemble_prob")

            def _pct(p):
                return round(float(p) * 100.0, 1) if p is not None else None

            g["glicko2_prob"] = _pct(g2)
            g["trueskill_prob"] = _pct(td)
            g["elo_prob"] = _pct(ed)
            g["xgb_prob"] = _pct(xs)
            g["ens_prob"] = _pct(sc)
            g["ensemble_prob"] = _pct(sc)
            g["glicko2_draw_prob"] = pred.get("glicko2_draw_prob")
            g["trueskill_draw_prob"] = pred.get("trueskill_draw_prob")
            g["elo_draw_prob"] = pred.get("elo_draw_prob")
            g["xgb_draw_prob"] = pred.get("xgb_draw_prob")
            g["draw_prob"] = (
                round(float(pred["draw_prob"]) * 100.0, 1)
                if pred.get("draw_prob") is not None
                else g.get("draw_prob")
            )
            eh, ea = pred.get("expected_home_score"), pred.get("expected_away_score")
            if eh is not None and ea is not None and not pred.get("xg_dummy"):
                g["v2_expected_home"] = eh
                g["v2_expected_away"] = ea
                g["naive_home_score"] = round(float(eh), 2)
                g["naive_away_score"] = round(float(ea), 2)
            if pred.get("pl_spread") is not None:
                g["pl_ah_spread"] = pred["pl_spread"]
            g["_soccer_ml_independent"] = True
            diag_rows.append(
                {
                    "G2": two_way_side(pred.get("glicko2_home_win"), pred.get("glicko2_draw_prob"), pred.get("glicko2_away_win")),
                    "TD": two_way_side(pred.get("takedown_home_win"), pred.get("takedown_draw"), pred.get("takedown_away_win")),
                    "ED": two_way_side(pred.get("elo_home_win"), pred.get("elo_draw_prob"), pred.get("elo_away_win")),
                    "XS": two_way_side(pred.get("xgb_home_win"), pred.get("xgb_draw_prob"), pred.get("xgb_away_win")),
                }
            )
    return diagnose_independence(diag_rows)
