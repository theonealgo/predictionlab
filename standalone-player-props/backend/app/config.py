import os


LEAGUE_CONFIG = {
    "MLB":    {"espn_sport": "baseball",    "espn_league": "mlb",                          "dist": "poisson"},
    "NBA":    {"espn_sport": "basketball",  "espn_league": "nba",                          "dist": "normal"},
    "NHL":    {"espn_sport": "hockey",      "espn_league": "nhl",                          "dist": "normal"},
    "NFL":    {"espn_sport": "football",    "espn_league": "nfl",                          "dist": "normal"},
    "SOCCER": {"espn_sport": "soccer",      "espn_league": "eng.1",                        "dist": "normal"},
    "NCAAB":  {"espn_sport": "basketball",  "espn_league": "mens-college-basketball",      "dist": "normal"},
    "WNBA":   {"espn_sport": "basketball",  "espn_league": "wnba",                         "dist": "normal"},
    "NCAAF":  {"espn_sport": "football",    "espn_league": "college-football",             "dist": "normal"},
    "NCAAW":  {"espn_sport": "basketball",  "espn_league": "womens-college-basketball",    "dist": "normal"},
    # Individual-athlete sports — competitor structure uses athlete.displayName
    # instead of team.displayName; no homeAway designation.
    "TENNIS": {"espn_sport": "tennis",  "espn_league": "wta",  "dist": "normal",
               "individual": True, "tennis_groupings": True,
               # ATP + WTA — same as sports/TENNIS.py picks page
               "espn_sources": [
                   {"espn_sport": "tennis", "espn_league": "atp", "tennis_groupings": True},
                   {"espn_sport": "tennis", "espn_league": "wta", "tennis_groupings": True},
               ]},
    "UFC":    {"espn_sport": "mma",     "espn_league": "ufc",  "dist": "normal",
               "individual": True},
    # Golf is a tournament format; competitors are individual players,
    # picks use head-to-head pairings generated from the field.
    "GOLF":   {"espn_sport": "golf",    "espn_league": "pga",  "dist": "normal",
               "individual": True, "tournament": True,
               "espn_sources": [{"espn_sport": "golf", "espn_league": "pga", "tournament": True}]},
}

SUPPORTED_LEAGUES = list(LEAGUE_CONFIG.keys())

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
ODDS_API_BASE = os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4").strip()
ODDS_ENGINE_URL = os.getenv("ODDS_ENGINE_URL", "").strip().rstrip("/")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))
DEBUG_PLAYER_VALIDATION = os.getenv("DEBUG_PLAYER_VALIDATION", "").strip().lower() in ("1", "true", "yes", "on")
