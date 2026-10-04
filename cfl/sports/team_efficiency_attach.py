"""Team efficiency projections for picks and results (all ESPN box-score sports)."""
from __future__ import annotations

import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Sports with ESPN summary/box-score paths in team_efficiency._ESPN_PATHS
EFFICIENCY_SPORTS = frozenset({'NBA', 'WNBA', 'NCAAB', 'NHL', 'NFL', 'NCAAF', 'MLB'})

# Results ML grading for all sports that show spread lines (ESPN or PL/ensemble fallback)
EFFICIENCY_GRADING_SPORTS = EFFICIENCY_SPORTS | frozenset({'SOCCER', 'NCAAW'})

_SPREAD_SIGMA = {
    'NBA': 12.0, 'WNBA': 11.0, 'NCAAB': 10.0, 'NCAAW': 10.0,
    'NFL': 14.0, 'NCAAF': 16.0, 'NHL': 1.2, 'MLB': 1.5, 'SOCCER': 1.0,
}

LABEL_EFFICIENCY = '⚡ Efficiency'


def spread_to_home_prob_pct(spread: float, sport: str = 'NBA') -> float:
    """Home win % (0–100) implied by an efficiency spread."""
    sigma = _SPREAD_SIGMA.get(sport, 12.0)
    return round(50.0 + 50.0 * math.erf(float(spread) / (sigma * math.sqrt(2))), 1)


def _snapshot_build_mode() -> bool:
    return os.environ.get('PL_SNAPSHOT_BUILD') == '1'


def _efficiency_precompute_budget(team_count: int, *, weekly_batch: bool = False) -> tuple[float, int]:
    """ESPN fetch budget — longer for snapshot builds and week-batched season grading."""
    if _snapshot_build_mode():
        return (min(240.0, 20.0 + team_count * 0.35), 20)
    if weekly_batch:
        return (min(120.0, 15.0 + team_count * 0.30), 16)
    return (12.0, 12)


def _efficiency_use_weekly_batches(game_count: int) -> bool:
    """Grade large result sets week-by-week (point-in-time ORtg) instead of one 12s burst."""
    return _snapshot_build_mode() or game_count > 50


def home_prob_pct_to_spread(prob_pct: float, sport: str = 'NBA') -> float:
    """Inverse of spread_to_home_prob_pct — spread implied by home win %."""
    target = float(prob_pct)
    sigma = _SPREAD_SIGMA.get(sport, 12.0)
    lo, hi = -45.0, 45.0
    for _ in range(48):
        mid = (lo + hi) / 2.0
        if spread_to_home_prob_pct(mid, sport) < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 1)


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
                if pred.get('our_spread') is not None:
                    pred['efficiency_spread'] = pred['our_spread']
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
                if pred.get('our_spread') is not None:
                    pred['efficiency_spread'] = pred['our_spread']
    except Exception as exc:
        logger.error(f"[eff] attach predictions failed for {sport}: {exc}", exc_info=True)


def _implied_efficiency_spread_from_prediction(pred: dict, sport: str) -> Optional[float]:
    """Spread for Efficiency pick row when ESPN box scores are unavailable."""
    sp = _safe_float(pred.get('efficiency_spread'))
    if sp is not None:
        return sp
    if pred.get('our_method') in ('efficiency', 'team-avg-fallback'):
        sp = _safe_float(pred.get('our_spread'))
        if sp is not None:
            return sp
    ens = pred.get('ensemble_prob')
    if ens is not None:
        try:
            return home_prob_pct_to_spread(float(ens), sport)
        except (TypeError, ValueError):
            pass
    return None


def fill_efficiency_spread_on_predictions(sport: str, predictions) -> None:
    """Set efficiency_spread on pick cards (ESPN attach + ensemble fallback)."""
    if sport not in EFFICIENCY_GRADING_SPORTS or not predictions:
        return
    m = __import__('NHL77FINAL', fromlist=['_round_to_half'])
    for pred in predictions:
        if pred.get('home_score') is not None:
            continue
        if _safe_float(pred.get('efficiency_spread')) is not None:
            continue
        sp = _implied_efficiency_spread_from_prediction(pred, sport)
        if sp is None:
            continue
        pred['efficiency_spread'] = m._round_to_half(sp)


def _efficiency_lookback_days() -> int:
    return 21 if _snapshot_build_mode() else 14


def _parse_result_game_date(date_val):
    if not date_val:
        return None
    try:
        import NHL77FINAL as m
        return m.parse_date(str(date_val)[:10])
    except Exception:
        return None


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
        if not _snapshot_build_mode():
            prefetch_recent_scoreboards(sport=sport, days=14)
        games = []
        for dd in daily_results.values():
            for g in dd.get('games', []):
                h, a = g.get('home'), g.get('away')
                if h and a:
                    games.append(g)
        if not games:
            return
        lookback = _efficiency_lookback_days()
        weekly_batch = _efficiency_use_weekly_batches(len(games))

        def _attach_with_eff_map(game_rows, eff_map):
            for g in game_rows:
                h, a = g.get('home'), g.get('away')
                he, ae = eff_map.get(h), eff_map.get(a)
                if not (he and ae):
                    continue
                proj = compute_efficiency_projection_from(
                    he, ae, sport=sport,
                    xsharp_total=g.get('xgb_total'),
                    xsharp_spread=g.get('xgb_spread'),
                )
                eff_sp = proj.get('projected_spread')
                if eff_sp is not None:
                    g['efficiency_spread'] = m._round_to_half(eff_sp)
                if g.get('our_spread') is None and eff_sp is not None:
                    g['our_spread'] = m._round_to_half(eff_sp)
                if g.get('our_total') is None and proj.get('projected_total') is not None:
                    g['our_total'] = m._round_to_half(proj['projected_total'])
                if g.get('our_home_pts') is None and proj.get('home_pts') is not None:
                    g['our_home_pts'] = round(float(proj['home_pts']))
                if g.get('our_away_pts') is None and proj.get('away_pts') is not None:
                    g['our_away_pts'] = round(float(proj['away_pts']))
                g['our_method'] = 'efficiency'

        if weekly_batch:
            by_week = {}
            for g in games:
                gd = _parse_result_game_date(g.get('date'))
                if not gd:
                    continue
                wk = gd.strftime('%Y-%W')
                by_week.setdefault(wk, {'as_of': gd, 'teams': set(), 'games': []})
                if gd > by_week[wk]['as_of']:
                    by_week[wk]['as_of'] = gd
                by_week[wk]['teams'].add(g['home'])
                by_week[wk]['teams'].add(g['away'])
                by_week[wk]['games'].append(g)
            for bundle in by_week.values():
                tc = len(bundle['teams'])
                budget_sec, max_workers = _efficiency_precompute_budget(
                    tc, weekly_batch=True,
                )
                eff_map = precompute_team_efficiencies(
                    list(bundle['teams']), sport=sport, n_games=5,
                    max_lookback_days=lookback,
                    total_budget_seconds=budget_sec,
                    max_workers=max_workers,
                    as_of=bundle['as_of'],
                )
                _attach_with_eff_map(bundle['games'], eff_map)
        else:
            teams = {t for g in games for t in (g.get('home'), g.get('away')) if t}
            budget_sec, max_workers = _efficiency_precompute_budget(len(teams))
            eff_map = precompute_team_efficiencies(
                list(teams), sport=sport, n_games=5,
                max_lookback_days=lookback,
                total_budget_seconds=budget_sec,
                max_workers=max_workers,
            )
            _attach_with_eff_map(games, eff_map)
    except Exception as exc:
        logger.debug(f"[eff] daily results attach failed for {sport}: {exc}")


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _implied_efficiency_spread_from_game(g: dict, sport: str) -> Optional[float]:
    """PL/ensemble-implied spread when ESPN efficiency box scores are unavailable."""
    sp = _safe_float(g.get('efficiency_spread'))
    if sp is not None:
        return sp
    sp = _safe_float(g.get('our_spread'))
    if sp is not None:
        return sp
    ens = g.get('ens_prob')
    if ens is not None:
        try:
            return home_prob_pct_to_spread(float(ens), sport)
        except (TypeError, ValueError):
            pass
    return None


def fill_efficiency_spread_fallback(sport: str, daily_results) -> None:
    """Set efficiency_spread from PL lines when ESPN did not (SOCCER, NCAAW, gaps on ESPN sports).

    Same implied-spread logic as NHL77FINAL._fill_pl_model_lines_for_results; UI label stays
    Efficiency — grading fallback only, not a separate model layer.
    """
    if sport not in EFFICIENCY_GRADING_SPORTS or not daily_results:
        return
    m = __import__('NHL77FINAL', fromlist=['_round_to_half'])
    for dd in daily_results.values():
        for g in dd.get('games', []):
            if g.get('skip_grading'):
                continue
            if _safe_float(g.get('efficiency_spread')) is not None:
                continue
            sp = _implied_efficiency_spread_from_game(g, sport)
            if sp is None:
                continue
            g['efficiency_spread'] = m._round_to_half(sp)
            if g.get('efficiency_spread_source') is None:
                g['efficiency_spread_source'] = (
                    g.get('pl_spread_source')
                    or ('ensemble_implied' if g.get('ens_prob') is not None else 'pl_spread')
                )


def grade_efficiency_for_daily_results(sport: str, daily_results) -> None:
    """ESPN efficiency (if available) + PL fallback spread + ML grading."""
    if sport in EFFICIENCY_SPORTS:
        attach_efficiency_to_daily_results(sport, daily_results)
    fill_efficiency_spread_fallback(sport, daily_results)
    apply_efficiency_ml_grading(sport, daily_results)


def _efficiency_spread_for_grading(g: dict, sport: str = '') -> Optional[float]:
    """Spread used for Team Efficiency ML grading on a result game."""
    # ============================================================
    # MLB LOCK — DO NOT MODIFY
    # MLB was previously fixed and verified.
    # DO NOT change this logic unless the user explicitly says:
    # "UNLOCK MLB"
    # Changes to other sports must NOT modify MLB behavior.
    # Efficiency ML uses the stored PL / H2H our_spread (favorite wins).
    # Do NOT substitute the faded run-line pick / spread_pick.
    # ============================================================
    sp = _safe_float(g.get('efficiency_spread'))
    if sp is not None:
        return sp
    if str(sport or '').upper() == 'MLB':
        for key in ('_unfaded_our_spread', 'our_spread'):
            our_sp = _safe_float(g.get(key))
            if our_sp is not None:
                return our_sp
        return None
    if g.get('our_method') in ('efficiency', 'team-avg-fallback'):
        our_sp = _safe_float(g.get('our_spread'))
        if our_sp is not None:
            return our_sp
    our_sp = _safe_float(g.get('our_spread'))
    if our_sp is not None and g.get('pl_spread_source') == 'ensemble_implied':
        return our_sp
    return None


def apply_efficiency_ml_grading(sport: str, daily_results) -> None:
    """Set efficiency_prob, efficiency_pick, and efficiency_correct on graded games.

    MLB LOCK: favorite from stored our_spread wins ML. Do not use spread_pick.
    ONLY modify MLB if the user explicitly says "UNLOCK MLB".
    """
    if sport not in EFFICIENCY_GRADING_SPORTS or not daily_results:
        return
    for dd in daily_results.values():
        for g in dd.get('games', []):
            if g.get('skip_grading'):
                g['efficiency_prob'] = None
                g['efficiency_pick'] = None
                g['efficiency_correct'] = None
                continue
            sp = _efficiency_spread_for_grading(g, sport)
            if sp is None:
                g['efficiency_prob'] = None
                g['efficiency_pick'] = None
                g['efficiency_correct'] = None
                continue
            if g.get('efficiency_spread') is None:
                g['efficiency_spread'] = sp
            try:
                prob = spread_to_home_prob_pct(float(sp), sport)
            except (TypeError, ValueError):
                g['efficiency_prob'] = None
                g['efficiency_pick'] = None
                g['efficiency_correct'] = None
                continue
            g['efficiency_prob'] = prob
            g['efficiency_pick'] = 'home' if prob >= 50.0 else 'away'
            home_won = g.get('home_win')
            if home_won is None and g.get('home_score') is not None and g.get('away_score') is not None:
                home_won = g['home_score'] > g['away_score']
            if home_won is None:
                g['efficiency_correct'] = None
            else:
                g['efficiency_correct'] = (prob >= 50.0) == home_won
