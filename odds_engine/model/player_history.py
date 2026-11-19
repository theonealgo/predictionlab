"""Normalize verified prior-date counting/yards observations without sport scaling."""
from datetime import datetime
import math
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NAMES = {
    "points": ("points",), "rebounds": ("totalrebounds", "rebounds"), "assists": ("assists",),
    "threes": ("threepointfieldgoalsmade", "threepointfieldgoalsmadethreepointfieldgoalsattempted"),
    "steals": ("steals",), "blocks": ("blocks",), "turnovers": ("turnovers",),
    "field_goals": ("fieldgoalsmade", "fgmade"),
    "twos": ("twopointfieldgoalsmade", "fieldgoalsmade"),
    "fga": ("fieldgoalsattempted", "fga"),
    "twopa": ("twopointfieldgoalsattempted",),
    "threepa": ("threepointfieldgoalsattempted", "threepointattempts"),
    "ftm": ("freethrowsmade", "ftmade"),
    "fta": ("freethrowsattempted", "ftattempted"),
    "pra": ("points",), "points_rebounds": ("points",), "points_assists": ("points",),
    "rebounds_assists": ("rebounds", "totalrebounds"), "stocks": ("steals",),
    "passing_yards": ("passingyards",), "rushing_yards": ("rushingyards",),
    "receiving_yards": ("receivingyards",), "receptions": ("receptions",),
    "passing_tds": ("passingtouchdowns", "passingtd", "passingtds"),
    "completions": ("completions", "passingcompletions"),
    "pass_attempts": ("passingattempts", "passattempts", "attempts"),
    "interceptions": ("interceptions", "passinginterceptions", "ints"),
    "longest_pass": ("longestpass", "longpass", "passinglong"),
    "rush_attempts": ("rushingattempts", "rushattempts", "carries"),
    "rushing_tds": ("rushingtouchdowns", "rushingtd", "rushingtds"),
    "longest_rush": ("longestrush", "rushinglong"),
    "receiving_tds": ("receivingtouchdowns", "receivingtd", "receivingtds"),
    "longest_reception": ("longestreception", "receivinglong"),
    "anytime_td": ("touchdowns", "totaltouchdowns", "rushingtouchdowns"),
    "rush_rec_yards": ("rushingyards", "receivingyards"),
    "pass_rush_yards": ("passingyards", "rushingyards"),
    "shots_on_goal": ("shotsongoal", "shotstotal"), "shots": ("totalshots", "shots"),
    "shots_on_target": ("shotsontarget",), "goals": ("goals",), "hits": ("hits",),
    "runs": ("runs",), "rbis": ("rbis", "runsbattedin"), "home_runs": ("homeruns",),
    "walks": ("walks", "basesonballs"), "strikeouts": ("strikeouts",),
}
LABELS = {"points": "PTS", "rebounds": "REB", "assists": "AST", "threes": "3PT",
          "steals": "STL", "blocks": "BLK", "turnovers": "TO",
          "field_goals": "FGM", "twos": "2PM", "fga": "FGA", "twopa": "2PA",
          "threepa": "3PA", "ftm": "FT", "fta": "FTA",
          "pra": "PRA", "points_rebounds": "P+R", "points_assists": "P+A",
          "rebounds_assists": "R+A", "stocks": "STL+BLK",
          "shots_on_goal": "SOG", "shots": "SH", "goals": "G", "hits": "H", "runs": "R",
          "rbis": "RBI", "home_runs": "HR", "walks": "BB", "strikeouts": "K", "receptions": "REC",
          "passing_yards": "YDS", "rushing_yards": "YDS", "receiving_yards": "YDS",
          "passing_tds": "TD", "rushing_tds": "TD", "receiving_tds": "TD",
          "completions": "CMP", "pass_attempts": "ATT", "interceptions": "INT",
          "rush_attempts": "CAR", "longest_pass": "LNG", "longest_rush": "LNG",
          "longest_reception": "LNG"}


def read_history(body, prop_type, now, minimum=5, limit=60):
    labels = body.get("labels") or []
    names = [re.sub("[^a-z]", "", str(value).lower()) for value in body.get("names") or []]
    candidates = [i for i, name in enumerate(names) if name in NAMES.get(prop_type, ())]
    if not candidates and LABELS.get(prop_type) in labels:
        candidates = [labels.index(LABELS[prop_type])]
    if len(candidates) != 1 or (prop_type == "strikeouts" and "AB" in labels) \
            or (prop_type == "hits" and "IP" in labels):
        return None
    index = candidates[0]
    records = {}
    cutoff = now.astimezone(ET).date()
    for season in body.get("seasonTypes") or []:
        label = str(season.get("displayName") or "").lower()
        if any(word in label for word in ("postseason", "playoff", "preseason", "exhibition")):
            continue
        regular = "regular season" in label
        categories = season.get("categories") or []
        rows = list(season.get("events") or []) if regular else []
        for category in categories:
            category_label = str(category.get("displayName") or "").lower()
            if any(word in category_label for word in ("postseason", "playoff", "preseason", "exhibition")):
                continue
            if regular or "regular season" in category_label:
                rows.extend(category.get("events") or [])
        for row in rows:
            event = (body.get("events") or {}).get(str(row.get("eventId"))) or {}
            try:
                stamp = datetime.fromisoformat(str(event["gameDate"]).replace("Z", "+00:00"))
                if not stamp.tzinfo or stamp.astimezone(ET).date() >= cutoff \
                        or str(event.get("gameResult") or "").upper() not in ("W", "L", "OTL", "SOL"):
                    continue
                # NCAA's category can say Regular Season Stats even for its
                # March championship games. Do not silently include those.
                note = str(event.get("eventNote") or "").lower()
                if not regular and stamp.month in (3, 4) and any(word in note for word in ("championship", "tournament")):
                    continue
                raw = row["stats"][index]
                if raw in (None, "", "--") or isinstance(raw, bool):
                    continue
                if prop_type == "threes":
                    made, attempts = map(float, str(raw).split("-"))
                    if not made.is_integer() or not attempts.is_integer() or not 0 <= made <= attempts:
                        continue
                    value = made
                else:
                    value = float(raw)
                if not math.isfinite(value):
                    continue
                if "MIN" in labels and not float(row["stats"][labels.index("MIN")]) > 0:
                    continue
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            records[str(row["eventId"])] = (stamp, value)
    recent = sorted(((stamp, value, identity) for identity, (stamp, value) in records.items()), reverse=True)[:limit]
    return {"recent_form": [value for stamp, value, identity in recent[:20]], "history_verified": True,
            "observations": [{"date": stamp.isoformat(), "actual": value, "event_id": identity}
                             for stamp, value, identity in recent]} if len(recent) >= minimum else None
