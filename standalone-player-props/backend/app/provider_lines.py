"""Optional paid-provider fallback; caller supplies an environment-managed key."""
from datetime import datetime
import math
import re
import unicodedata


def _name(value):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    text = re.sub("[^a-z0-9]", "", text)
    return {"laclippers": "losangelesclippers"}.get(text, text)


def _flatten_markets(markets):
    """Map Odds API market key → internal prop_type.

    `markets` is {prop_type: market_key} or {prop_type: [market_key, ...]}.
    """
    reverse = {}
    keys = []
    for prop, value in (markets or {}).items():
        items = value if isinstance(value, (list, tuple)) else (value,)
        for key in items:
            if not key:
                continue
            reverse[str(key)] = prop
            keys.append(str(key))
    seen = set()
    ordered = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return reverse, ordered


_YES_NO_PROPS = frozenset({"anytime_td", "first_td", "last_td"})


def fetch_rows(league, players, sport_key, markets, api_key, cap, get):
    if not api_key or not sport_key or not markets:
        return []
    # No credential or credential-bearing URL is logged or returned.
    reverse, market_keys = _flatten_markets(markets)
    if not market_keys:
        return []
    base = "https://api.the-odds-api.com/v4/sports/" + sport_key
    try:
        response = get(base + "/events", params={"apiKey": api_key}, timeout=10)
        response.raise_for_status()
        events = response.json()
    except Exception:
        return []
    by_name = {}
    duplicates = set()
    for player in players:
        name = _name(player.get("name"))
        if name in by_name:
            duplicates.add(name)
        by_name[name] = player
    output = []
    requested = 0
    # Odds API bills per market key; batch to keep URLs short.
    batches = [market_keys[i:i + 8] for i in range(0, len(market_keys), 8)]
    for event in events if isinstance(events, list) else []:
        matched = {}
        for name, player in by_name.items():
            game = player.get("next_game") or {}
            if name in duplicates or not str(game.get("event_id") or "").isdigit():
                continue
            pair = {_name(event.get("home_team")), _name(event.get("away_team"))}
            if {_name(player.get("team")), _name(game.get("opponent"))} != pair:
                continue
            try:
                ours = datetime.fromisoformat(str(game["start_time"]).replace("Z", "+00:00"))
                theirs = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
                if not ours.tzinfo or not theirs.tzinfo or abs((ours - theirs).total_seconds()) > 300:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            matched[name] = player
        if not matched or requested >= max(0, int(cap)):
            continue
        requested += 1
        seen = set()
        for batch in batches:
            try:
                response = get(
                    base + "/events/" + str(event["id"]) + "/odds",
                    params={
                        "apiKey": api_key,
                        "regions": "us",
                        "markets": ",".join(batch),
                        "oddsFormat": "american",
                    },
                    timeout=10,
                )
                response.raise_for_status()
                body = response.json() or {}
            except Exception:
                continue
            for book in body.get("bookmakers") or []:
                offers = {}
                yes_offers = {}
                for market in book.get("markets") or []:
                    prop = reverse.get(market.get("key"))
                    if not prop:
                        continue
                    for offer in market.get("outcomes") or []:
                        player = matched.get(_name(offer.get("description")))
                        side = str(offer.get("name") or "").upper()
                        if not player:
                            continue
                        try:
                            price = float(offer["price"])
                            if not math.isfinite(price) or price == 0:
                                continue
                        except (KeyError, ValueError, TypeError):
                            continue
                        # Anytime / first / last TD are Yes/No markets.
                        if prop in _YES_NO_PROPS and side in ("YES", "NO"):
                            try:
                                point = offer.get("point")
                                line = float(point) if point is not None else 0.5
                                if not math.isfinite(line):
                                    line = 0.5
                            except (TypeError, ValueError):
                                line = 0.5
                            key = (str(player["player_id"]), prop, line)
                            yes_offers.setdefault(key, {})[side] = price
                            continue
                        if side not in ("OVER", "UNDER"):
                            continue
                        try:
                            line = float(offer["point"])
                            if not math.isfinite(line):
                                continue
                        except (KeyError, ValueError, TypeError):
                            continue
                        key = (str(player["player_id"]), prop, line)
                        offers.setdefault(key, {})[side] = price
                for (player_id, prop, line), prices in offers.items():
                    # Keep one bookmaker per player/prop/line (first wins).
                    # Distinct lines (main + alternate) are all kept.
                    if set(prices) != {"OVER", "UNDER"} or (player_id, prop, line) in seen:
                        continue
                    player = next(p for p in matched.values() if str(p["player_id"]) == player_id)
                    game = player["next_game"]
                    output.append({
                        "player_id": player_id,
                        "prop_type": prop,
                        "line": line,
                        "odds_over": prices["OVER"],
                        "odds_under": prices["UNDER"],
                        "event_id": game["event_id"],
                        "event_start": game["start_time"],
                        "line_source": "the_odds_api",
                    })
                    seen.add((player_id, prop, line))
                for (player_id, prop, line), prices in yes_offers.items():
                    if "YES" not in prices or (player_id, prop, line) in seen:
                        continue
                    player = next(p for p in matched.values() if str(p["player_id"]) == player_id)
                    game = player["next_game"]
                    row = {
                        "player_id": player_id,
                        "prop_type": prop,
                        "line": line,
                        "odds_over": prices["YES"],
                        "odds_under": prices.get("NO"),
                        "event_id": game["event_id"],
                        "event_start": game["start_time"],
                        "line_source": "the_odds_api",
                    }
                    if prices.get("NO") is None:
                        row["mlb_over_only"] = True
                    output.append(row)
                    seen.add((player_id, prop, line))
    return output
