"""Optional paid-provider fallback; caller supplies an environment-managed key."""
from datetime import datetime
import math
import re
import unicodedata


def _name(value):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    text = re.sub("[^a-z0-9]", "", text)
    return {"laclippers": "losangelesclippers"}.get(text, text)


def fetch_rows(league, players, sport_key, markets, api_key, cap, get):
    if not api_key or not sport_key or not markets:
        return []
    # No credential or credential-bearing URL is logged or returned.
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
    reverse = {value: key for key, value in markets.items()}
    output = []
    requested = 0
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
                if not ours.tzinfo or not theirs.tzinfo or abs((ours-theirs).total_seconds()) > 300:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            matched[name] = player
        if not matched or requested >= max(0, int(cap)):
            continue
        requested += 1
        try:
            response = get(base + "/events/" + str(event["id"]) + "/odds",
                           params={"apiKey": api_key, "regions": "us", "markets": ",".join(markets.values()),
                                   "oddsFormat": "american"}, timeout=10)
            response.raise_for_status()
            body = response.json()
        except Exception:
            continue
        seen = set()
        for book in body.get("bookmakers") or []:
            offers = {}
            for market in book.get("markets") or []:
                prop = reverse.get(market.get("key"))
                if not prop:
                    continue
                for offer in market.get("outcomes") or []:
                    player = matched.get(_name(offer.get("description")))
                    side = str(offer.get("name") or "").upper()
                    if not player or side not in ("OVER", "UNDER"):
                        continue
                    try:
                        line, price = float(offer["point"]), float(offer["price"])
                        if not math.isfinite(line) or not math.isfinite(price) or price == 0:
                            continue
                    except (KeyError, ValueError, TypeError):
                        continue
                    key = (str(player["player_id"]), prop, line)
                    offers.setdefault(key, {})[side] = price
            for (player_id, prop, line), prices in offers.items():
                if set(prices) != {"OVER", "UNDER"} or (player_id, prop) in seen:
                    continue
                player = next(p for p in matched.values() if str(p["player_id"]) == player_id)
                game = player["next_game"]
                output.append({"player_id": player_id, "prop_type": prop, "line": line,
                               "odds_over": prices["OVER"], "odds_under": prices["UNDER"],
                               "event_id": game["event_id"], "event_start": game["start_time"],
                               "line_source": "the_odds_api"})
                seen.add((player_id, prop))
    return output
