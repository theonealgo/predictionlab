"""PredictionLab Expected Goals (PL-xG) — soccer only.

Copied from isolation ``~/Documents/Personal/soccer/engine/pl_expected_goals.py``.
Does not write generic ``h2h_last10_*`` columns used by other sports.

Current-form attack/defense. No H2H in the production path.
Date T uses only matches with game_date < T (predict before update).

ESPN: boxscore has shots/SOT/possession after kickoff. No expectedGoals field
was present on inspected summaries. Injuries table in the sandbox DB is empty
and there is no stored pre-kickoff lineup snapshot — lineup/injury is off.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

GOAL_MIN = 0.15
GOAL_MAX = 3.5
TOTAL_MIN = 1.2
TOTAL_MAX = 5.5
GLOBAL_HOME_MU = 1.45
GLOBAL_AWAY_MU = 1.15
ELO_K = 22.0
ELO_HOME_ADV = 50.0
ELO_MEAN = 1500.0


def parse_date(raw: Any) -> Optional[datetime]:
    s = str(raw or "")[:10]
    if len(s) < 10:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def date_key(raw: Any) -> str:
    return str(raw or "")[:10]


def clamp_goals(eh: float, ea: float) -> Tuple[float, float]:
    eh = max(GOAL_MIN, min(GOAL_MAX, float(eh)))
    ea = max(GOAL_MIN, min(GOAL_MAX, float(ea)))
    tot = eh + ea
    if tot > TOTAL_MAX and tot > 0:
        eh, ea = eh * TOTAL_MAX / tot, ea * TOTAL_MAX / tot
    elif tot < TOTAL_MIN and tot > 0:
        eh, ea = eh * TOTAL_MIN / tot, ea * TOTAL_MIN / tot
    return eh, ea


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))
    except Exception:
        return 0.0


def dc_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1.0 + lam_h * rho
    if h == 1 and a == 0:
        return 1.0 + lam_a * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lam_h: float, lam_a: float, rho: float = 0.0, max_goals: int = 8
) -> List[List[float]]:
    grid = [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    z = 0.0
    for h in range(max_goals + 1):
        ph = poisson_pmf(h, lam_h)
        for a in range(max_goals + 1):
            pa = poisson_pmf(a, lam_a)
            tau = dc_tau(h, a, lam_h, lam_a, rho) if rho else 1.0
            p = max(0.0, ph * pa * tau)
            grid[h][a] = p
            z += p
    if z > 0:
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                grid[h][a] /= z
    return grid


def over_probs(lam_h: float, lam_a: float, rho: float = 0.0) -> Dict[str, float]:
    grid = score_matrix(lam_h, lam_a, rho=rho)
    out: Dict[str, float] = {}
    for line in (1.5, 2.5, 3.5):
        p_over = 0.0
        for h, row in enumerate(grid):
            for a, p in enumerate(row):
                if h + a > line:
                    p_over += p
        key = str(line).replace(".", "")
        out[f"p_over_{key}"] = p_over
        out[f"p_under_{key}"] = 1.0 - p_over
    return out


def shrink(raw: Optional[float], n: float, prior: float, k: float) -> float:
    if raw is None or n <= 0:
        return prior
    return (n * raw + k * prior) / (n + k)


@dataclass(frozen=True)
class PLXGConfig:
    name: str = "full"
    window: int = 10
    half_life: Optional[float] = 8.0
    venue_split: bool = True
    venue_shrink_k: float = 6.0
    opp_adjust: bool = True
    league_env: bool = True
    league_shrink_k: float = 20.0
    formulation: str = "mult"
    shot_blend: float = 0.0
    h2h_weight: float = 0.0
    team_shrink_k: float = 8.0

    def to_dict(self) -> dict:
        return asdict(self)


# Ablations A–F (components stacked). Locked path is E with h2h_weight=0.
ABLATIONS: Dict[str, PLXGConfig] = {
    "A_flat_goals": PLXGConfig(
        name="A_flat_goals",
        window=10,
        half_life=None,
        venue_split=False,
        opp_adjust=False,
        league_env=False,
        formulation="avg",
        h2h_weight=0.0,
    ),
    "B_recency": PLXGConfig(
        name="B_recency",
        window=10,
        half_life=8.0,
        venue_split=False,
        opp_adjust=False,
        league_env=False,
        formulation="avg",
        h2h_weight=0.0,
    ),
    "C_venue_split": PLXGConfig(
        name="C_venue_split",
        window=10,
        half_life=8.0,
        venue_split=True,
        opp_adjust=False,
        league_env=False,
        formulation="avg",
        h2h_weight=0.0,
    ),
    "D_opp_elo": PLXGConfig(
        name="D_opp_elo",
        window=10,
        half_life=8.0,
        venue_split=True,
        opp_adjust=True,
        league_env=False,
        formulation="mult",
        h2h_weight=0.0,
    ),
    "E_league_env": PLXGConfig(
        name="E_league_env",
        window=10,
        half_life=8.0,
        venue_split=True,
        opp_adjust=True,
        league_env=True,
        formulation="mult",
        h2h_weight=0.0,
    ),
    "F_h2h_research": PLXGConfig(
        name="F_h2h_research",
        window=10,
        half_life=8.0,
        venue_split=True,
        opp_adjust=True,
        league_env=True,
        formulation="mult",
        h2h_weight=0.08,
    ),
}


@dataclass
class MatchRec:
    date: str
    gf: float
    ga: float
    venue: str
    opp: str
    opp_elo: float
    league: str
    shots: Optional[float] = None
    sot: Optional[float] = None

    @property
    def shot_xg(self) -> Optional[float]:
        if self.shots is None and self.sot is None:
            return None
        shots = float(self.shots or 0.0)
        sot = float(self.sot or 0.0)
        return max(0.05, 0.08 * shots + 0.22 * sot)


class IncrementalElo:
    """Same K / home-adv defaults as soccer_models.SoccerEloModel."""

    def __init__(self, k: float = ELO_K, home_adv: float = ELO_HOME_ADV):
        self.k = k
        self.home_adv = home_adv
        self.ratings: Dict[str, float] = defaultdict(lambda: ELO_MEAN)

    def rating(self, team: str) -> float:
        return float(self.ratings[team])

    def expected_home_share(self, home: str, away: str) -> float:
        diff = self.rating(home) + self.home_adv - self.rating(away)
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def update(self, home: str, away: str, hs: float, aws: float) -> None:
        actual = 1.0 if hs > aws else 0.0 if hs < aws else 0.5
        exp = self.expected_home_share(home, away)
        delta = self.k * (actual - exp)
        self.ratings[home] += delta
        self.ratings[away] -= delta


@dataclass
class PLXGPrediction:
    home: float
    away: float
    total: float
    n_home: int
    n_away: int
    home_attack: float
    home_defense: float
    away_attack: float
    away_defense: float
    league_mu_home: float
    league_mu_away: float
    method: str
    used_h2h: bool = False
    p_over_15: float = 0.0
    p_over_25: float = 0.0
    p_over_35: float = 0.0
    p_under_15: float = 0.0
    p_under_25: float = 0.0
    p_under_35: float = 0.0

    def as_store(self) -> dict:
        return {
            "soccer_pl_expected_home": round(self.home, 3),
            "soccer_pl_expected_away": round(self.away, 3),
            "soccer_pl_expected_total": round(self.total, 3),
            "soccer_pl_expected_p_over_15": round(self.p_over_15, 4),
            "soccer_pl_expected_p_over_25": round(self.p_over_25, 4),
            "soccer_pl_expected_p_over_35": round(self.p_over_35, 4),
            "soccer_pl_expected_method": self.method,
            "soccer_pl_expected_n_home": self.n_home,
            "soccer_pl_expected_n_away": self.n_away,
        }


class PLXGState:
    """Chronological team form. Call predict() then update() for no leak."""

    def __init__(self, rho: float = -0.08):
        self.hist: Dict[str, List[MatchRec]] = defaultdict(list)
        self.elo = IncrementalElo()
        self.league_home: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=80))
        self.league_away: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=80))
        self.global_home: Deque[float] = deque(maxlen=400)
        self.global_away: Deque[float] = deque(maxlen=400)
        self.h2h: Dict[Tuple[str, str], List[Tuple[str, float, float]]] = defaultdict(list)
        self.rho = rho
        self.n_updated = 0

    def _league_mu(self, league: str, cfg: PLXGConfig) -> Tuple[float, float]:
        gh = (sum(self.global_home) / len(self.global_home)) if self.global_home else GLOBAL_HOME_MU
        ga = (sum(self.global_away) / len(self.global_away)) if self.global_away else GLOBAL_AWAY_MU
        if not cfg.league_env:
            return gh, ga
        lh = self.league_home.get(league or "")
        la = self.league_away.get(league or "")
        raw_h = (sum(lh) / len(lh)) if lh else None
        raw_a = (sum(la) / len(la)) if la else None
        n = float(len(lh) if lh else 0)
        return (
            shrink(raw_h, n, gh, cfg.league_shrink_k),
            shrink(raw_a, n, ga, cfg.league_shrink_k),
        )

    def _wmean(
        self,
        matches: List[MatchRec],
        *,
        window: int,
        half_life: Optional[float],
        venue: Optional[str],
        field: str,
        opp_adjust: bool,
        shot_blend: float,
    ) -> Tuple[Optional[float], int]:
        pool = [m for m in matches if venue is None or m.venue == venue]
        chunk = pool[-int(window) :] if window else pool
        if not chunk:
            return None, 0
        num = den = 0.0
        n = len(chunk)
        for i, m in enumerate(chunk):
            age = n - 1 - i
            w = 1.0 if not half_life or half_life <= 0 else 0.5 ** (age / half_life)
            gf = float(m.gf)
            if shot_blend > 0:
                sx = m.shot_xg
                if sx is not None:
                    gf = (1.0 - shot_blend) * gf + shot_blend * sx
            if field == "gf":
                val = gf
            else:
                val = float(m.ga)
                if shot_blend > 0 and m.shot_xg is not None:
                    # No opponent shots stored on this row; goals conceded only.
                    val = float(m.ga)
            if opp_adjust:
                rel = 10 ** ((float(m.opp_elo) - ELO_MEAN) / 400.0)
                rel = max(0.45, min(2.2, rel))
                if field == "gf":
                    val = val * rel
                else:
                    val = val / rel
            num += w * val
            den += w
        if den <= 0:
            return None, 0
        return num / den, n

    def _team_rates(self, team: str, cfg: PLXGConfig, mu_h: float, mu_a: float) -> Dict[str, float]:
        ms = self.hist.get(team) or []
        mu = 0.5 * (mu_h + mu_a)
        att, n_all = self._wmean(
            ms,
            window=cfg.window,
            half_life=cfg.half_life,
            venue=None,
            field="gf",
            opp_adjust=cfg.opp_adjust,
            shot_blend=cfg.shot_blend,
        )
        deff, _ = self._wmean(
            ms,
            window=cfg.window,
            half_life=cfg.half_life,
            venue=None,
            field="ga",
            opp_adjust=cfg.opp_adjust,
            shot_blend=cfg.shot_blend,
        )
        att = shrink(att, float(n_all), mu, cfg.team_shrink_k)
        deff = shrink(deff, float(n_all), mu, cfg.team_shrink_k)
        if not cfg.venue_split:
            return {
                "att": att,
                "defn": deff,
                "home_att": att,
                "home_def": deff,
                "away_att": att,
                "away_def": deff,
                "n": float(n_all),
                "n_home": float(n_all),
                "n_away": float(n_all),
            }
        ha, n_ha = self._wmean(
            ms, window=cfg.window, half_life=cfg.half_life, venue="H",
            field="gf", opp_adjust=cfg.opp_adjust, shot_blend=cfg.shot_blend,
        )
        hd, n_hd = self._wmean(
            ms, window=cfg.window, half_life=cfg.half_life, venue="H",
            field="ga", opp_adjust=cfg.opp_adjust, shot_blend=cfg.shot_blend,
        )
        aa, n_aa = self._wmean(
            ms, window=cfg.window, half_life=cfg.half_life, venue="A",
            field="gf", opp_adjust=cfg.opp_adjust, shot_blend=cfg.shot_blend,
        )
        ad, n_ad = self._wmean(
            ms, window=cfg.window, half_life=cfg.half_life, venue="A",
            field="ga", opp_adjust=cfg.opp_adjust, shot_blend=cfg.shot_blend,
        )
        return {
            "att": att,
            "defn": deff,
            "home_att": shrink(ha, float(n_ha), att, cfg.venue_shrink_k),
            "home_def": shrink(hd, float(n_hd), deff, cfg.venue_shrink_k),
            "away_att": shrink(aa, float(n_aa), att, cfg.venue_shrink_k),
            "away_def": shrink(ad, float(n_ad), deff, cfg.venue_shrink_k),
            "n": float(n_all),
            "n_home": float(n_ha),
            "n_away": float(n_aa),
        }

    def honest_h2h(
        self, home: str, away: str, n: int = 10, min_games: int = 1
    ) -> Optional[Dict[str, float]]:
        """Prior meetings only (rows are appended after predict)."""
        key = tuple(sorted((home, away)))
        rows = (self.h2h.get(key) or [])[-n:]
        if len(rows) < min_games:
            return None
        hs_list: List[float] = []
        as_list: List[float] = []
        for rec in rows:
            _dt, g1, g2, first = rec
            if first == home:
                hs_list.append(float(g1))
                as_list.append(float(g2))
            else:
                hs_list.append(float(g2))
                as_list.append(float(g1))
        if not hs_list:
            return None
        return {
            "games_used": float(len(hs_list)),
            "avg_home": sum(hs_list) / len(hs_list),
            "avg_away": sum(as_list) / len(as_list),
            "our_total": (sum(hs_list) + sum(as_list)) / len(hs_list),
        }

    def predict(
        self,
        home: str,
        away: str,
        *,
        league: str = "",
        cfg: Optional[PLXGConfig] = None,
        rho: Optional[float] = None,
    ) -> PLXGPrediction:
        cfg = cfg or PLXGConfig()
        mu_h, mu_a = self._league_mu(league, cfg)
        hr = self._team_rates(home, cfg, mu_h, mu_a)
        ar = self._team_rates(away, cfg, mu_h, mu_a)
        ha, hd = hr["home_att"], hr["home_def"]
        aa, ad = ar["away_att"], ar["away_def"]
        if cfg.formulation == "avg":
            eh = 0.5 * (ha + ad)
            ea = 0.5 * (aa + hd)
            if cfg.league_env:
                scale = (mu_h + mu_a) / max(0.4, eh + ea)
                scale = max(0.7, min(1.35, scale))
                eh *= 0.70 + 0.30 * scale
                ea *= 0.70 + 0.30 * scale
        else:
            rel_ha = ha / max(mu_h, 0.35)
            rel_ad = ad / max(mu_a, 0.35)
            rel_aa = aa / max(mu_a, 0.35)
            rel_hd = hd / max(mu_h, 0.35)
            eh = mu_h * rel_ha * rel_ad
            ea = mu_a * rel_aa * rel_hd
        used_h2h = False
        if cfg.h2h_weight and cfg.h2h_weight > 0:
            h2h = self.honest_h2h(home, away, n=10, min_games=2)
            if h2h:
                w = min(0.15, float(cfg.h2h_weight))
                eh = (1.0 - w) * eh + w * float(h2h["avg_home"])
                ea = (1.0 - w) * ea + w * float(h2h["avg_away"])
                used_h2h = True
        eh, ea = clamp_goals(eh, ea)
        used_rho = self.rho if rho is None else rho
        probs = over_probs(eh, ea, rho=used_rho)
        return PLXGPrediction(
            home=eh,
            away=ea,
            total=eh + ea,
            n_home=int(hr["n"]),
            n_away=int(ar["n"]),
            home_attack=ha,
            home_defense=hd,
            away_attack=aa,
            away_defense=ad,
            league_mu_home=mu_h,
            league_mu_away=mu_a,
            method=cfg.name,
            used_h2h=used_h2h,
            p_over_15=probs["p_over_15"],
            p_over_25=probs["p_over_25"],
            p_over_35=probs["p_over_35"],
            p_under_15=probs["p_under_15"],
            p_under_25=probs["p_under_25"],
            p_under_35=probs["p_under_35"],
        )

    def update(self, game: dict) -> None:
        home = game.get("home_team_id") or game.get("home_team")
        away = game.get("away_team_id") or game.get("away_team")
        hs, aws = game.get("home_score"), game.get("away_score")
        if not home or not away or hs is None or aws is None:
            return
        try:
            hs_f, aw_f = float(hs), float(aws)
        except (TypeError, ValueError):
            return
        dt = date_key(game.get("game_date") or game.get("date"))
        lg = str(game.get("league") or "")
        opp_elo_h = self.elo.rating(away)
        opp_elo_a = self.elo.rating(home)
        st = (game.get("espn_stats") or {}) if isinstance(game.get("espn_stats"), dict) else {}
        sh = (st.get("home") or {}) if st else {}
        sa = (st.get("away") or {}) if st else {}
        self.hist[home].append(
            MatchRec(
                date=dt, gf=hs_f, ga=aw_f, venue="H", opp=away, opp_elo=opp_elo_h,
                league=lg,
                shots=_opt_float(sh.get("totalShots")),
                sot=_opt_float(sh.get("shotsOnTarget")),
            )
        )
        self.hist[away].append(
            MatchRec(
                date=dt, gf=aw_f, ga=hs_f, venue="A", opp=home, opp_elo=opp_elo_a,
                league=lg,
                shots=_opt_float(sa.get("totalShots")),
                sot=_opt_float(sa.get("shotsOnTarget")),
            )
        )
        pair = tuple(sorted((home, away)))
        first = pair[0]
        if first == home:
            self.h2h[pair].append((dt, hs_f, aw_f, home))
        else:
            self.h2h[pair].append((dt, aw_f, hs_f, away))
        self.elo.update(home, away, hs_f, aw_f)
        self.league_home[lg].append(hs_f)
        self.league_away[lg].append(aw_f)
        self.global_home.append(hs_f)
        self.global_away.append(aw_f)
        self.n_updated += 1


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def format_plxg(pred: PLXGPrediction) -> str:
    """Face string: total (home–away)."""
    tot = round(pred.total * 2.0) / 2.0
    tot_s = str(int(tot)) if tot == int(tot) else f"{tot:.1f}"
    return f"{tot_s} ({pred.home:.1f}–{pred.away:.1f})"


def estimate_rho(rows: Iterable[dict]) -> float:
    """Moment match on 0-0 / 1-1 using predicted lambdas (select period only)."""
    n = n00 = n11 = e00 = e11 = 0.0
    for r in rows:
        eh, ea = r.get("eh"), r.get("ea")
        hs, aws = r.get("hs"), r.get("as")
        if eh is None or ea is None or hs is None or aws is None:
            continue
        n += 1
        e00 += math.exp(-float(eh) - float(ea))
        e11 += poisson_pmf(1, float(eh)) * poisson_pmf(1, float(ea))
        if float(hs) == 0 and float(aws) == 0:
            n00 += 1
        if float(hs) == 1 and float(aws) == 1:
            n11 += 1
    if n < 200 or e00 <= 0 or e11 <= 0:
        return -0.08
    # DC: P00 ≈ e00 * (1 - λh λa ρ)  →  ρ ≈ (1 - n00/n / e00) / mean(λh λa)
    # Keep in the usual soccer band.
    raw = 0.0
    if e00 > 0:
        raw += (1.0 - (n00 / n) / (e00 / n)) 
    rho = max(-0.18, min(0.05, -0.08 + 0.15 * math.tanh(raw)))
    return rho


def american_profit(odds: float, won: bool) -> float:
    if not won:
        return -1.0
    if odds < 0:
        return 100.0 / abs(odds)
    return odds / 100.0


def fit_until(games: Iterable[dict], asof: str) -> PLXGState:
    """Update on matches with game_date < asof only."""
    state = PLXGState()
    cut = str(asof)[:10]
    for g in games:
        if date_key(g.get("game_date") or g.get("date")) >= cut:
            continue
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        state.update(g)
    return state


def default_grid() -> List[PLXGConfig]:
    out: List[PLXGConfig] = []
    for window in (3, 5, 8, 10, 15):
        for half in (None, 3.0, 5.0, 8.0, 12.0):
            for form in ("mult", "avg"):
                out.append(
                    PLXGConfig(
                        name=f"w{window}_hl{half or 'flat'}_{form}",
                        window=window,
                        half_life=half,
                        venue_split=True,
                        opp_adjust=True,
                        league_env=True,
                        formulation=form,
                        h2h_weight=0.0,
                    )
                )
    return out
