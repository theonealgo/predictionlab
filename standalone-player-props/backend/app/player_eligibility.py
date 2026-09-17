"""Sport-aware player prop eligibility (pool before EV).

Eligibility order:
  sport → league → position/role → depth/relevance pool → valid markets → then EV.

Does not invent players, markets, or projections. Does not rank the pool by EV.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Target: top N relevant players per role (not top N by EV).
TOP_PER_ROLE = 35

# When synthesizing lines (no sportsbook feed), only depth-chart ranks
# at or below these caps are featured. WRs run deep on NFL depth charts
# (WR1–WR5+ are common prop targets); QBs stay tighter.
SYNTHETIC_MAX_DEPTH_BY_ROLE: Dict[str, int] = {
    "QB": 2,
    "RB": 3,
    "WR": 6,
    "TE": 3,
    "FB": 2,
}
# Fallback when role has no specific cap (non-football).
SYNTHETIC_MAX_DEPTH_RANK = 3

# ── Position → role ───────────────────────────────────────────────────────

_NFL_POS_TO_ROLE = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "HB": "RB",
    "WR": "WR",
    "TE": "TE",
}
_NFL_BLOCKED = frozenset({
    "C", "G", "OG", "OT", "T", "OL", "LS", "RS",
    "DE", "DT", "NT", "DL", "EDGE",
    "LB", "ILB", "OLB", "MLB",
    "CB", "S", "SS", "FS", "DB", "NB",
    "K", "PK", "P", "LS", "KR",
})

_CFL_POS_TO_ROLE = {
    **_NFL_POS_TO_ROLE,
    "SB": "WR",  # slotback
    "R": "WR",
}

_NBA_POS_TO_ROLE = {
    "G": "G",
    "PG": "G",
    "SG": "G",
    "F": "F",
    "SF": "F",
    "PF": "F",
    "C": "C",
    "G-F": "G",
    "F-G": "F",
    "F-C": "F",
    "C-F": "C",
}

_NHL_POS_TO_ROLE = {
    "C": "F",
    "LW": "F",
    "RW": "F",
    "W": "F",
    "F": "F",
    "D": "D",
    "LD": "D",
    "RD": "D",
    "G": "G",
}

_MLB_POS_TO_ROLE = {
    "P": "P",
    "SP": "P",
    "RP": "P",
    "CP": "P",
    "C": "H",
    "1B": "H",
    "2B": "H",
    "3B": "H",
    "SS": "H",
    "LF": "H",
    "CF": "H",
    "RF": "H",
    "OF": "H",
    "DH": "H",
    "IF": "H",
    "UTIL": "H",
}

_SOCCER_POS_TO_ROLE = {
    "F": "F",
    "FW": "F",
    "ST": "F",
    "CF": "F",
    "M": "M",
    "MF": "M",
    "AM": "M",
    "DM": "M",
    "CM": "M",
    "D": "D",
    "DF": "D",
    "CB": "D",
    "FB": "D",
    "LB": "D",
    "RB": "D",
    "G": "G",
    "GK": "G",
}

# ── Role → default (primary) markets for synthetic/internal lines ─────────
# These are the markets we invent when books are unavailable. Unusual
# markets (e.g. WR rushing) are NEVER synthesized — only attached when a
# real sportsbook line exists for that player.

_PRIMARY_MARKET: Dict[str, Dict[str, str]] = {
    "NFL": {
        "QB": "passing_yards",
        "RB": "rushing_yards",
        "WR": "receiving_yards",
        "TE": "receiving_yards",
    },
    "NCAAF": {
        "QB": "passing_yards",
        "RB": "rushing_yards",
        "WR": "receiving_yards",
        "TE": "receiving_yards",
    },
    "CFL": {
        "QB": "passing_yards",
        "RB": "rushing_yards",
        "WR": "receiving_yards",
        "TE": "receiving_yards",
    },
    "NBA": {"G": "points", "F": "points", "C": "points"},
    "WNBA": {"G": "points", "F": "points", "C": "points"},
    "NCAAB": {"G": "points", "F": "points", "C": "points"},
    "NCAAW": {"G": "points", "F": "points", "C": "points"},
    "NHL": {"F": "shots_on_goal", "D": "shots_on_goal", "G": ""},
    "MLB": {"P": "strikeouts", "H": "hits"},
    "SOCCER": {"F": "shots", "M": "shots", "D": "shots", "G": ""},
    "UFC": {"Fighter": ""},
}

# ── Role → allowed markets ────────────────────────────────────────────────
# Default/synthetic markets ⊆ allowed. Markets listed only here (not in
# primary) may appear when a real sportsbook line exists for that player.

_ROLE_MARKETS: Dict[str, Dict[str, Set[str]]] = {
    "NFL": {
        "QB": {
            "passing_yards", "rushing_yards", "receptions", "receiving_yards",
        },
        "RB": {
            "rushing_yards", "receptions", "receiving_yards",
        },
        # WR: receiving only by default. rushing_yards allowed ONLY with a
        # real book line (see market_allowed / filter_props_by_eligibility).
        "WR": {
            "receptions", "receiving_yards",
        },
        "TE": {
            "receptions", "receiving_yards",
        },
    },
    "NCAAF": {
        "QB": {"passing_yards", "rushing_yards"},
        "RB": {"rushing_yards", "receptions", "receiving_yards"},
        "WR": {"receptions", "receiving_yards"},
        "TE": {"receptions", "receiving_yards"},
    },
    "CFL": {
        "QB": {"passing_yards", "rushing_yards"},
        "RB": {"rushing_yards", "receptions", "receiving_yards"},
        "WR": {"receptions", "receiving_yards"},
        "TE": {"receptions", "receiving_yards"},
    },
    "NBA": {
        "G": {"points", "rebounds", "assists", "threes"},
        "F": {"points", "rebounds", "assists", "threes"},
        "C": {"points", "rebounds", "assists", "threes"},
    },
    "WNBA": {
        "G": {"points", "rebounds", "assists", "threes"},
        "F": {"points", "rebounds", "assists", "threes"},
        "C": {"points", "rebounds", "assists", "threes"},
    },
    "NCAAB": {
        "G": {"points", "rebounds", "assists", "threes"},
        "F": {"points", "rebounds", "assists", "threes"},
        "C": {"points", "rebounds", "assists", "threes"},
    },
    "NCAAW": {
        "G": {"points", "rebounds", "assists", "threes"},
        "F": {"points", "rebounds", "assists", "threes"},
        "C": {"points", "rebounds", "assists", "threes"},
    },
    "NHL": {
        "F": {"goals", "assists", "points", "shots_on_goal"},
        "D": {"assists", "points", "shots_on_goal"},
        "G": set(),
    },
    "MLB": {
        "P": {"strikeouts"},
        "H": {"hits", "runs", "rbis", "home_runs", "walks"},
    },
    "SOCCER": {
        "F": {"goals", "assists", "shots", "shots_on_target"},
        "M": {"goals", "assists", "shots", "shots_on_target"},
        "D": {"shots", "shots_on_target", "assists"},
        "G": set(),
    },
    "UFC": {
        "Fighter": set(),
    },
}

# Unusual markets that may only appear when a real sportsbook line exists.
_BOOK_ONLY_MARKETS: Dict[str, Dict[str, Set[str]]] = {
    "NFL": {
        "WR": {"rushing_yards"},
        "TE": {"rushing_yards"},
        "QB": {"receptions", "receiving_yards"},
    },
    "NCAAF": {
        "WR": {"rushing_yards"},
        "TE": {"rushing_yards"},
    },
    "CFL": {
        "WR": {"rushing_yards"},
    },
}

_REAL_LINE_SOURCES = frozenset({"espn_props", "odds_api", "the_odds_api"})


def normalize_league(league: str) -> str:
    return (league or "").strip().upper()


def position_to_role(league: str, position: Optional[str]) -> Optional[str]:
    """Map ESPN position abbreviation to a prop role. None = not eligible."""
    lg = normalize_league(league)
    raw = (position or "").strip().upper()
    if not raw:
        return None
    if lg in ("NFL", "NCAAF"):
        if raw in _NFL_BLOCKED:
            return None
        return _NFL_POS_TO_ROLE.get(raw)
    if lg == "CFL":
        if raw in _NFL_BLOCKED:
            return None
        return _CFL_POS_TO_ROLE.get(raw)
    if lg in ("NBA", "WNBA", "NCAAB", "NCAAW"):
        return _NBA_POS_TO_ROLE.get(raw) or (
            "G" if "G" in raw else "F" if "F" in raw else "C" if "C" in raw else None
        )
    if lg == "NHL":
        return _NHL_POS_TO_ROLE.get(raw)
    if lg == "MLB":
        return _MLB_POS_TO_ROLE.get(raw)
    if lg == "SOCCER":
        return _SOCCER_POS_TO_ROLE.get(raw)
    if lg == "UFC":
        return "Fighter"
    return None


def allowed_markets_for_role(league: str, role: Optional[str]) -> Set[str]:
    lg = normalize_league(league)
    if not role:
        return set()
    return set((_ROLE_MARKETS.get(lg) or {}).get(role) or set())


def book_only_markets_for_role(league: str, role: Optional[str]) -> Set[str]:
    lg = normalize_league(league)
    if not role:
        return set()
    return set((_BOOK_ONLY_MARKETS.get(lg) or {}).get(role) or set())


def primary_market_for_role(league: str, role: Optional[str]) -> Optional[str]:
    """Default market used for synthetic/internal lines (never EV-ranked)."""
    lg = normalize_league(league)
    if not role:
        return None
    pt = (_PRIMARY_MARKET.get(lg) or {}).get(role) or ""
    return pt or None


def is_real_line_source(line_source: Optional[str]) -> bool:
    return (line_source or "").strip().lower() in _REAL_LINE_SOURCES


def market_allowed(
    league: str,
    role: Optional[str],
    prop_type: str,
    *,
    line_source: Optional[str] = None,
) -> bool:
    """True when this role may carry this market.

    Book-only unusual markets (e.g. WR Rush Yds) require a real sportsbook
    line_source. Synthetic/internal generators must not invent those.
    """
    pt = (prop_type or "").strip().lower()
    if not pt:
        return False
    base = allowed_markets_for_role(league, role)
    book_only = book_only_markets_for_role(league, role)
    if pt in base:
        return True
    if pt in book_only and is_real_line_source(line_source):
        return True
    return False


def relevance_score(player: Dict[str, Any]) -> float:
    """Non-EV relevance for pool selection (depth chart, experience, usage)."""
    score = 0.0
    # Depth chart rank dominates (1 = starter). Missing rank → penalize.
    try:
        depth = int(player.get("depth_rank") or 0)
    except (TypeError, ValueError):
        depth = 0
    if depth > 0:
        score += max(0.0, 120.0 - (depth - 1) * 35.0)
    else:
        score -= 40.0

    exp = player.get("experience_years")
    try:
        if exp is not None:
            score += min(float(exp), 15.0) * 3.0
    except (TypeError, ValueError):
        pass
    for key, weight in (
        ("projected_minutes", 0.35),
        ("avg_minutes", 0.35),
        ("usage_score", 25.0),
        ("usage_rate", 40.0),
        ("prop_frequency", 15.0),
        ("top50_score", 0.15),
    ):
        try:
            v = float(player.get(key) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            score += v * weight
    try:
        j = int(str(player.get("jersey") or "99").lstrip("0") or "99")
        score += max(0.0, 40.0 - min(j, 99)) * 0.05
    except (TypeError, ValueError):
        pass
    try:
        rank = int(player.get("consensus_rank") or 0)
        if 0 < rank < 500:
            score += max(0.0, 200.0 - rank)
    except (TypeError, ValueError):
        pass
    return score


def select_top_pool(
    players: Sequence[Dict[str, Any]],
    *,
    league: str,
    per_role: int = TOP_PER_ROLE,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Keep top `per_role` players per role by relevance (not EV).

    Players without a resolvable role are dropped. Depth-chart starters
    rank above depth / practice-squad names.
    """
    lg = normalize_league(league)
    raw_n = len(players)
    by_role: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    no_role = 0
    for p in players:
        role = p.get("role") or position_to_role(lg, p.get("position"))
        if not role:
            no_role += 1
            continue
        enriched = dict(p)
        enriched["role"] = role
        enriched["league"] = lg
        enriched["_relevance"] = relevance_score(enriched)
        by_role[role].append(enriched)

    kept: List[Dict[str, Any]] = []
    role_counts: Dict[str, int] = {}
    for role, rows in by_role.items():
        rows.sort(
            key=lambda x: (
                -float(x.get("_relevance") or 0.0),
                int(x.get("depth_rank") or 99),
                x.get("name") or "",
            )
        )
        limit = per_role
        if lg in ("NCAAB", "NCAAW"):
            limit = min(per_role, 25)
        chosen = rows[: max(1, int(limit))]
        role_counts[role] = len(chosen)
        for r in chosen:
            r.pop("_relevance", None)
            kept.append(r)

    kept.sort(key=lambda x: (x.get("role") or "", int(x.get("depth_rank") or 99), x.get("name") or ""))
    audit = {
        "league": lg,
        "raw_players": raw_n,
        "removed_no_role": no_role,
        "removed_by_pool_cap": max(0, (raw_n - no_role) - len(kept)),
        "eligible_players": len(kept),
        "roles": role_counts,
        "per_role_cap": per_role,
    }
    return kept, audit


def filter_props_by_eligibility(
    props: Sequence[Dict[str, Any]],
    players_by_id: Dict[str, Dict[str, Any]],
    *,
    league: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Drop props whose player/role/market combination is invalid."""
    lg = normalize_league(league)
    out: List[Dict[str, Any]] = []
    suppressed = 0
    reasons: Dict[str, int] = defaultdict(int)
    for prop in props:
        pid = str(prop.get("player_id") or "")
        p = players_by_id.get(pid)
        if not p:
            suppressed += 1
            reasons["missing_player"] += 1
            continue
        role = p.get("role") or position_to_role(lg, p.get("position"))
        pt = str(prop.get("prop_type") or "")
        src = prop.get("line_source")
        if not role:
            suppressed += 1
            reasons["no_role"] += 1
            continue
        if not market_allowed(lg, role, pt, line_source=src):
            suppressed += 1
            reasons["market_not_for_role"] += 1
            continue
        # Synthetic props: when depth rank is known, require featured depth
        # for that role (WR goes deeper than QB). Depth 99 = not on chart.
        if not is_real_line_source(src) and lg in ("NFL", "NCAAF", "CFL"):
            try:
                depth = int(p.get("depth_rank") or 0)
            except (TypeError, ValueError):
                depth = 0
            max_depth = SYNTHETIC_MAX_DEPTH_BY_ROLE.get(
                str(role or ""), SYNTHETIC_MAX_DEPTH_RANK
            )
            if depth > max_depth:
                suppressed += 1
                reasons["depth_not_featured"] += 1
                continue
        line = prop.get("line")
        if line is None and prop.get("line_for_calc") is None:
            suppressed += 1
            reasons["missing_line"] += 1
            continue
        row = dict(prop)
        row["role"] = role
        row["position"] = p.get("position") or ""
        out.append(row)
    audit = {
        "props_in": len(props),
        "props_out": len(out),
        "suppressed": suppressed,
        "suppress_reasons": dict(reasons),
    }
    return out, audit


def roles_for_league(league: str) -> List[str]:
    lg = normalize_league(league)
    return sorted((_ROLE_MARKETS.get(lg) or {}).keys())


def markets_catalog(league: str) -> Dict[str, List[str]]:
    lg = normalize_league(league)
    out: Dict[str, List[str]] = {}
    for role, mkts in (_ROLE_MARKETS.get(lg) or {}).items():
        combined = set(mkts) | book_only_markets_for_role(lg, role)
        out[role] = sorted(combined)
    return out
