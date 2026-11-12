"""Read-only fixture counts for the existing shared soccer navigation."""
from datetime import date, timedelta
from html import unescape
import re
from soccer_league_catalog import SOCCER_LEAGUE_ENDPOINTS, soccer_espn_slug


def counts(payload, day):
    today = date.fromisoformat(day)
    tomorrow = today+timedelta(days=1)
    buckets = {str(today): {}, str(tomorrow): {}}
    names = {slug: name for name, slug in SOCCER_LEAGUE_ENDPOINTS.items()}
    seen = set()
    for card in (payload or {}).get("cards") or []:
        html = card.get("html") or ""
        match = re.search(r'data-league=["\']([^"\']*)', html)
        if not match:
            continue
        label = unescape(match.group(1))
        league = names.get(soccer_espn_slug(label))
        if not league:
            continue
        when = str(card.get("date") or "")[:10]
        if when not in buckets:
            continue
        identity = re.search(r'data-game-id=["\']([^"\']+)', html)
        key = (when, league, identity.group(1) if identity else
               (card.get("away"), card.get("home"), html))
        if key in seen:
            continue
        seen.add(key)
        buckets[when][league] = buckets[when].get(league, 0)+1
    return {"today": buckets[str(today)], "tomorrow": buckets[str(tomorrow)],
            "today_date": str(today), "tomorrow_date": str(tomorrow),
            "available": payload is not None}
