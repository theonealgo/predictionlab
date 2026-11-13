"""Independent player-stat leader pools; never rank by EV or overall fame."""
from collections import Counter
from datetime import datetime
import statistics
from zoneinfo import ZoneInfo

from prop_models import observations

VERSION = "prop-market-top100-last10-v1"
LIMIT = 100
SAMPLE = 10


def select_market_players(players, league, markets, allowed, as_of=None, limit=LIMIT):
    as_of = as_of or datetime.now(ZoneInfo("America/New_York"))
    if as_of.tzinfo is None:
        raise ValueError("Ranking cutoff must be timezone-aware")
    if limit < 1:
        raise ValueError("Market player limit must be positive")
    unique = {}
    for player in players:
        identity = str(player.get("player_id") or "")
        if identity:
            unique.setdefault(identity, player)
    selected, categories = {}, {}
    for market in sorted(set(markets)):
        candidates, missing = [], 0
        for identity, player in unique.items():
            if not allowed(player, market):
                continue
            records = observations(player, market, as_of)[-SAMPLE:]
            if len(records) < SAMPLE:
                missing += 1
                continue
            average = statistics.mean(row["actual"] for row in records)
            candidates.append((average, identity, player))
        candidates.sort(key=lambda item: (-item[0], str(item[2].get("name") or "").casefold(), item[1]))
        for rank, (average, identity, player) in enumerate(candidates[:limit], 1):
            row = selected.setdefault(identity, {**player, "market_rankings": {}})
            row["market_rankings"][market] = {
                "rank": rank, "average": average, "sample": SAMPLE,
                "basis": "last_10_verified_games", "as_of": as_of.date().isoformat(),
            }
        categories[market] = {
            "eligible_with_history": len(candidates), "selected": min(limit, len(candidates)),
            "missing_history": missing, "limit": limit, "sample": SAMPLE,
        }
    rows = sorted(selected.values(), key=lambda row: (str(row.get("name") or "").casefold(), str(row["player_id"])))
    return rows, {
        "version": VERSION, "league": league, "raw_players": len(unique),
        "eligible_players": len(rows), "per_market_cap": limit, "ranking_games": SAMPLE,
        "basis": "last_10_verified_games", "by_market": categories,
        "roles": dict(Counter(row.get("role") or "" for row in rows)),
    }
