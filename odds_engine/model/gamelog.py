"""Verified prior-date NBA counting-stat observations, not model probabilities."""
from datetime import datetime, timezone
import math
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
COUNTS = {"points": "PTS", "rebounds": "REB", "assists": "AST"}


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def makes(value):
    try:
        made, attempted = map(number, str(value).split("-"))
    except (TypeError, ValueError):
        return None
    if made is None or attempted is None or not made.is_integer() or not attempted.is_integer():
        return None
    return made if 0 <= made <= attempted else None


def observations(payload, player_id, as_of):
    """Keep regular-season completed appearances strictly before the prediction ET date."""
    if as_of.tzinfo is None:
        raise ValueError("Prediction cutoff must be timezone-aware")
    cutoff = as_of.astimezone(ET).date()
    labels = {label: i for i, label in enumerate(payload.get("labels") or [])}
    if not {"MIN", "PTS", "REB", "AST", "3PT"} <= set(labels):
        raise ValueError("Required official stat columns are unavailable")
    metadata = payload.get("events") or {}
    records = {}
    for season in payload.get("seasonTypes") or []:
        if "regular season" not in str(season.get("displayName") or "").lower():
            continue
        for category in season.get("categories") or []:
            for item in category.get("events") or []:
                identity = str(item.get("eventId") or "")
                event = metadata.get(identity) or {}
                try:
                    start = datetime.fromisoformat(str(event["gameDate"]).replace("Z", "+00:00"))
                    if start.tzinfo is None or start.astimezone(ET).date() >= cutoff:
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                if str(event.get("gameResult") or "").upper() not in {"W", "L"}:
                    continue
                values = item.get("stats") or []
                if len(values) <= max(labels.values()):
                    continue
                minutes = number(values[labels["MIN"]])
                if minutes is None or minutes <= 0:
                    continue
                stats = {name: number(values[labels[label]]) for name, label in COUNTS.items()}
                stats["threes"] = makes(values[labels["3PT"]])
                if any(value is None or value < 0 or not value.is_integer() for value in stats.values()):
                    continue
                if stats["points"] < 3 * stats["threes"]:
                    continue
                row = {"player_id": str(player_id), "event_id": identity,
                       "date_et": start.astimezone(ET).date().isoformat(),
                       "start_utc": start.astimezone(timezone.utc).isoformat(), "minutes": minutes,
                       **{name: int(value) for name, value in stats.items()}}
                if identity in records and records[identity] != row:
                    raise ValueError("Conflicting official observations for the same event")
                records[identity] = row
    return sorted(records.values(), key=lambda row: (row["date_et"], row["start_utc"], row["event_id"]))
