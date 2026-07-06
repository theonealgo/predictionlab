import os


LEAGUE_CONFIG = {
    "MLB": {"espn_sport": "baseball", "espn_league": "mlb", "dist": "poisson"},
    "NBA": {"espn_sport": "basketball", "espn_league": "nba", "dist": "normal"},
    "NHL": {"espn_sport": "hockey", "espn_league": "nhl", "dist": "normal"},
    "NFL": {"espn_sport": "football", "espn_league": "nfl", "dist": "normal"},
    "SOCCER": {"espn_sport": "soccer", "espn_league": "eng.1", "dist": "normal"},
    "NCAAB": {"espn_sport": "basketball", "espn_league": "mens-college-basketball", "dist": "normal"},
    "WNBA": {"espn_sport": "basketball", "espn_league": "wnba", "dist": "normal"},
    "NCAAF": {"espn_sport": "football", "espn_league": "college-football", "dist": "normal"},
    "NCAAW": {"espn_sport": "basketball", "espn_league": "womens-college-basketball", "dist": "normal"},
}

SUPPORTED_LEAGUES = list(LEAGUE_CONFIG.keys())

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
ODDS_API_BASE = os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4").strip()
ODDS_ENGINE_URL = os.getenv("ODDS_ENGINE_URL", "").strip().rstrip("/")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))
# Max games per league to pull player-prop odds for per refresh. Each event
# costs (markets x regions) requests against The Odds API quota, so keep modest.
ODDS_EVENTS_CAP = int(os.getenv("ODDS_EVENTS_CAP", "10"))

# ── Real player-prop lines via ESPN's free core API (DraftKings) ────────────
# Player props are sourced from ESPN's free, undocumented core API — no paid
# key and no monthly request cap (the same free ESPN source used elsewhere).
# ESPN_PROP_PROVIDER_ID selects the sportsbook (100 = DraftKings).
ESPN_PROP_PROVIDER_ID = os.getenv("ESPN_PROP_PROVIDER_ID", "100")
# Max games per league to pull player props for per refresh (ESPN is free, so
# this can comfortably cover a full daily slate).
ESPN_EVENTS_CAP = int(os.getenv("ESPN_EVENTS_CAP", "20"))
# How long to reuse a fetched real-lines snapshot before re-hitting ESPN.
REAL_LINES_TTL = int(os.getenv("REAL_LINES_TTL", "3600"))
# Candidate roster-player pool size per league used for prop matching. Larger
# pool -> more overlap with the players a book actually posts props for.
TOP_PLAYER_POOL = int(os.getenv("TOP_PLAYER_POOL", "120"))
DEBUG_PLAYER_VALIDATION = os.getenv("DEBUG_PLAYER_VALIDATION", "").strip().lower() in ("1", "true", "yes", "on")
