"""Team efficiency projections for picks and results (all ESPN box-score sports)."""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Sports with ESPN summary/box-score paths in team_efficiency._ESPN_PATHS
EFFICIENCY_SPORTS = frozenset({'NBA', 'WNBA', 'NCAAB', 'NHL', 'NFL', 'NCAAF', 'MLB'})

_SPREAD_SIGMA = {
    'NBA': 12.0, 'WNBA': 11.0, 'NCAAB': 10.0, 'NCAAW': 10.0,
    'NFL': 14.0, 'NCAAF': 16.0, 'NHL': 1.2, 'MLB': 1.5,
}

LABEL_EFFICIENCY = '⚡ Efficiency'


def spread_to_home_prob_pct(spread: float, sport: str = 'NBA') -> float:
    """Home win % (0–100) implied by an efficiency spread."""
    sigma = _SPREAD_SIGMA.get(sport, 12.0)
    return round(50.0 + 50.0 * math.erf(float(spread) / (sigma * math.sqrt(2))), 1)


def attach_efficiency_to_predictions(sport: str, predictions) -> None:
    """Attach efficiency-based our_spread/our_total on live pick cards."""
    if sport not in EFFICIENCY_SPORTS or not predictions:
        return
    m = __import__('NHL77FINAL', fromlist=['_round_to_half'])
    try:
        from team_efficiency import (
            precompute_team_efficiencies,
            compute_efficiency_projection_from,
        )
        from weighted_total_predictor import (
            compute_team_avg_projection,
            prefetch_recent_scoreboards,
        )
    except ImportError:
        return
    try:
        prefetch_recent_scoreboards(sport=sport, days=14)
        unique_teams = []
        seen = set()
        for pred in predictions:
            for t in (pred.get('home_team_id'), pred.get('away_team_id')):
                if t and t not in seen:
                    seen.add(t)
                    unique_teams.append(t)
        if not unique_teams:
            return
        eff_map = precompute_team_efficiencies(
            unique_teams, sport=sport, n_games=5,
            max_lookback_days=14, total_budget_seconds=10.0, max_workers=16,
        )
        for pred in predictions:
            ht = pred.get('home_team_id')
            at = pred.get('away_team_id')
            if not (ht and at):
                continue
            xs_total = pred.get('xgb_total')
            xs_spread = pred.get('xgb_spread')
            home_eff = eff_map.get(ht)
            away_eff = eff_map.get(at)
            if home_eff and away_eff:
                proj = compute_efficiency_projection_from(
                    home_eff, away_eff, sport=sport,
                    xsharp_total=xs_total, xsharp_spread=xs_spread,
                )
                pred['our_spread'] = m._round_to_half(proj['projected_spread'])
                pred['our_total'] = m._round_to_half(proj['projected_total'])
                if pred['our_spread'] is not None and pred['our_total'] is not None:
                    _h, _a = m._scores_from_spread_total(pred['our_spread'], pred['our_total'])
                    if _h is not None:
                        pred['our_home_pts'] = _h
                        pred['our_away_pts'] = _a
                    else:
                        pred['our_home_pts'] = (
                            m._round_to_half(proj['home_pts']) if proj.get('home_pts') is not None else None
                        )
                        pred['our_away_pts'] = (
                            m._round_to_half(proj['away_pts']) if proj.get('away_pts') is not None else None
                        )
                else:
                    pred['our_home_pts'] = (
                        m._round_to_half(proj['home_pts']) if proj.get('home_pts') is not None else None
                    )
                    pred['our_away_pts'] = (
                        m._round_to_half(proj['away_pts']) if proj.get('away_pts') is not None else None
                    )
                pred['our_home_eff'] = home_eff
                pred['our_away_eff'] = away_eff
                pred['our_pace'] = proj.get('avg_pace')
                pred['our_method'] = 'efficiency'
                pred['pl_variance_tier'] = proj.get('variance_tier')
                pred['pl_confidence_tier'] = proj.get('confidence_tier')
                if xs_total is not None and pred['our_total'] is not None:
                    _delta = abs(float(pred['our_total']) - float(xs_total))
                    if _delta <= 0.5:
                        pred['consensus_total'] = m._round_to_half(
                            (float(pred['our_total']) + float(xs_total)) / 2.0
                        )
                    else:
                        pred['consensus_total'] = m._round_to_half(
                            0.6 * float(pred['our_total']) + 0.4 * float(xs_total)
                        )
                    pred['pl_model_delta'] = round(float(pred['our_total']) - float(xs_total), 1)
                continue
            try:
                fb = compute_team_avg_projection(
                    home_team=ht, away_team=at, sport=sport,
                    xsharp_total=xs_total, xsharp_spread=xs_spread,
                    n_games=3, max_lookback_days=14,
                )
            except Exception as _fb_e:
                fb = None
                logger.debug(f"[team-avg fallback] {ht} vs {at}: {_fb_e}")
            if fb:
                pred['our_total'] = fb['projected_total']
                pred['our_spread'] = fb.get('projected_spread')
                pred['our_method'] = 'team-avg-fallback'
    except Exception as exc:
        logger.debug(f"[eff] attach predictions failed for {sport}: {exc}")


def attach_efficiency_to_daily_results(sport: str, daily_results) -> None:
    """Attach PL spread/total from efficiency on completed games (results grading)."""
    if sport not in EFFICIENCY_SPORTS or not daily_results:
        return
    m = __import__('NHL77FINAL', fromlist=['_round_to_half'])
    try:
        from team_efficiency import precompute_team_efficiencies, compute_efficiency_projection_from
        from weighted_total_predictor import prefetch_recent_scoreboards
    except ImportError:
        return
    try:
        prefetch_recent_scoreboards(sport=sport, days=14)
        teams, games = set(), []
        for dd in daily_results.values():
            for g in dd.get('games', []):
                h, a = g.get('home'), g.get('away')
                if h and a:
                    teams.add(h)
                    teams.add(a)
                    games.append(g)
        if not teams:
            return
        eff_map = precompute_team_efficiencies(
            list(teams), sport=sport, n_games=5,
            max_lookback_days=14, total_budget_seconds=12.0, max_workers=12,
        )
        for g in games:
            h, a = g.get('home'), g.get('away')
            he, ae = eff_map.get(h), eff_map.get(a)
            if not (he and ae):
                continue
            proj = compute_efficiency_projection_from(
                he, ae, sport=sport,
                xsharp_total=g.get('xgb_total'),
                xsharp_spread=g.get('xgb_spread'),
            )
            if g.get('our_spread') is None and proj.get('projected_spread') is not None:
                g['our_spread'] = m._round_to_half(proj['projected_spread'])
            if g.get('our_total') is None and proj.get('projected_total') is not None:
                g['our_total'] = m._round_to_half(proj['projected_total'])
            if g.get('our_home_pts') is None and proj.get('home_pts') is not None:
                g['our_home_pts'] = round(float(proj['home_pts']))
            if g.get('our_away_pts') is None and proj.get('away_pts') is not None:
                g['our_away_pts'] = round(float(proj['away_pts']))
            g['our_method'] = 'efficiency'
    except Exception as exc:
        logger.debug(f"[eff] daily results attach failed for {sport}: {exc}")


def apply_efficiency_ml_grading(sport: str, daily_results) -> None:
    """Set efficiency_prob + efficiency_correct on graded result games."""
    if not daily_results:
        return
    for dd in daily_results.values():
        for g in dd.get('games', []):
            if g.get('skip_grading'):
                g['efficiency_prob'] = None
                g['efficiency_correct'] = None
                continue
            sp = g.get('our_spread')
            if sp is None:
                g['efficiency_prob'] = None
                g['efficiency_correct'] = None
                continue
            try:
                prob = spread_to_home_prob_pct(float(sp), sport)
            except (TypeError, ValueError):
                g['efficiency_prob'] = None
                g['efficiency_correct'] = None
                continue
            g['efficiency_prob'] = prob
            home_won = g.get('home_win')
            if home_won is None and g.get('home_score') is not None and g.get('away_score') is not None:
                home_won = g['home_score'] > g['away_score']
            if home_won is None:
                g['efficiency_correct'] = None
            else:
                g['efficiency_correct'] = (prob >= 50.0) == home_won
