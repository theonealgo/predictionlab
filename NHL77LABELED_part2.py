_SHARE_IMAGE_CACHE_DIR = _os_v2.path.join(_os_v2.path.dirname(_os_v2.path.abspath(__file__)), '.cache', 'share_images') # Sets the folder path where we save images that people can share on social media
_SHARE_TOKEN_RE = re.compile(r'^[a-f0-9]{32}$') # Creates a rule to check if a share token (a secret code) is exactly 32 letters and numbers long
_SHARE_IMAGE_TTL_SECONDS = 3600 # Tells the computer to keep shareable images for 3600 seconds (1 hour) before deleting them
_SHARE_IMAGE_MAX_ITEMS = 500 # Sets a limit to only save 500 shareable images at a time so we don't run out of space
_PROPS_ENGINE_MODULE = None # Creates a blank placeholder for a tool that handles player prop bets
_PROPS_CONFIG_MODULE = None # Creates a blank placeholder for the settings for the player prop bets
# Standalone props live under backend/app; must not use top-level name "app" (root app.py shadows it). # A comment explaining why we use a specific name for the next variable
_STANDALONE_PROPS_PKG = "_standalone_player_props" # Sets the name of the folder where the player prop bet code is saved


_PL_BOOK_ODDS_LIMIT_BY_SPORT = { # Creates a dictionary that sets a limit on how many betting odds we download from sportsbooks for each sport
    'SOCCER': 40, # We only download the top 40 odds for soccer
    'NBA': 80, # We download up to 80 odds for basketball
    'MLB': 80, # We download up to 80 odds for baseball
    'NHL': 60, # We download up to 60 odds for hockey
    'NFL': 60, # We download up to 60 odds for football
    'WNBA': 50, # We download up to 50 odds for women's basketball
    'NCAAB': 40, # We download up to 40 odds for college basketball
    'NCAAW': 40, # We download up to 40 odds for women's college basketball
    'NCAAF': 40, # We download up to 40 odds for college football
}

_OFFSEASON_SPORTS_HINT = { # Creates a dictionary of helpful messages to show users when a sport is out of season
    'NCAAB': 'College basketball picks return when the season schedule is live on ESPN (typically November–April).', # Message for college basketball
    'NCAAW': "Women's college basketball picks return when the season schedule is live on ESPN (typically November–April).", # Message for women's college basketball
    'NFL': 'NFL picks return when the regular season schedule is published (typically September–February).', # Message for football
    'NCAAF': 'College football picks return when the fall schedule is live on ESPN (typically August–January).', # Message for college football
}


def _daily_results_game_count(daily_results) -> int: # Defines a new function to count how many games happened in a day
    if not daily_results: # If there are no results given
        return 0 # Just return 0 games
    return sum(len(dd.get('games') or []) for dd in daily_results.values()) # Otherwise, count up all the games in all the groups and return the total number


def _recent_result_dates(daily_results, *, yesterday=None, limit=7, recent_window_days=21): # Defines a new function to find the dates of recent games
    """Prefer recent graded days (through yesterday); fall back to older dates if none.""" # A comment explaining the function's goal
    if not daily_results: # If there are no results given
        return [] # Return an empty list
    if yesterday is None: # If we weren't given a specific "yesterday" date
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d') # Calculate what yesterday's date was and save it
    ydt = parse_date(yesterday) or (datetime.now() - timedelta(days=1)) # Turn yesterday's date into a special computer time object
    cutoff_dt = ydt - timedelta(days=recent_window_days) # Calculate a cutoff date (like 21 days ago) so we don't look too far back

    def _date_key_dt(dk): # Defines a tiny helper function inside this function
        return parse_date(dk) or datetime.min # It just turns a date string into a computer time object, or a very old default time

    recent = sorted( # Starts sorting a list of recent dates
        (
            d for d in daily_results.keys() # Look through all the dates we have results for
            if d and daily_results[d].get('games') # Only keep dates that actually have games
            and (_dk := _date_key_dt(d)) >= cutoff_dt and _dk <= ydt # And only keep dates that are between our cutoff and yesterday
        ),
        key=_date_key_dt, # Sort them using their actual dates
        reverse=True, # Sort them from newest to oldest
    )
    if recent: # If we found any recent dates
        return recent[:limit] # Return the top ones (up to our limit, like 7 days)
    dates = sorted( # If we didn't find any recent dates, sort all the dates that happened on or before yesterday
        (d for d in daily_results.keys() if d and _date_key_dt(d) <= ydt),
        key=_date_key_dt,
        reverse=True,
    )
    if not dates: # If we still don't have any dates
        dates = sorted( # Just sort literally all the dates we have
            (d for d in daily_results.keys() if d),
            key=_date_key_dt,
            reverse=True,
        )
    return dates[:limit] # Return the top dates up to our limit


def _picks_display_dates(grouped_predictions, today_date): # Defines a function to figure out which dates to show on the predictions calendar
    """Dates for picks nav + default visible day (must have upcoming games when possible).""" # A comment explaining the function
    if not grouped_predictions: # If there are no predictions
        return [], today_date # Return an empty list and today's date
    upcoming = [] # Create an empty list to hold future dates
    for dk in sorted(grouped_predictions.keys()): # Loop through all the dates we have predictions for in order
        if not dk or dk == 'TBD': # If the date is missing or says "To Be Determined"
            continue # Skip it
        games = grouped_predictions[dk] # Get the games for this date
        if any(isinstance(g, dict) and g.get('home_score') is None for g in games): # Look to see if there is at least one game on this date that hasn't finished yet (no score)
            upcoming.append(dk) # If there is, add this date to our upcoming list
    if upcoming: # If we found some upcoming dates
        if today_date in upcoming: # If today is one of those dates
            default = today_date # We'll show today by default
        else: # Otherwise
            future = [d for d in upcoming if d >= today_date] # Find all dates that are today or in the future
            default = future[0] if future else upcoming[-1] # Pick the closest future date, or the last upcoming date
        return upcoming, default # Return our list of upcoming dates and the default date to show
    all_dates = sorted(d for d in grouped_predictions.keys() if d and d != 'TBD') # If there were no upcoming games, just get all valid dates
    if not all_dates: # If there are no valid dates
        return [], today_date # Return empty and today
    window = all_dates[-14:] # Get the last 14 dates (2 weeks)
    default = today_date if today_date in window else window[-1] # Pick today if it's in the window, or the last date in the window
    return window, default # Return the 2-week window and the default date


def _results_page_html_usable(html: str) -> bool: # Defines a function to check if a saved web page is good enough to show users
    if not html: # If there's no web page code at all
        return False # Say it's not usable
    low = html.lower() # Make a copy of the web page code in all lowercase letters to make it easier to search
    if any( # Checks if ANY of the following bad phrases are in the web page code
        phrase in low
        for phrase in (
            'class="no-data"', # Bad phrase: no data
            'moneyline results are temporarily unavailable', # Bad phrase: unavailable
            'results could not be loaded because no completed', # Bad phrase: couldn't load
            'no results data available yet', # Bad phrase: no data yet
        )
    ):
        return False # If we found a bad phrase, say the page is not usable
    if 'game-card' in low or 'week-section' in low: # If the page has game cards or a week section
        return True # Say it is usable!
    # Snapshot-only page: season banner without recent game cards. # A comment explaining a special case
    if 'season performance' in low and 'moneyline accuracy by model' in low: # If the page has a season summary
        return True # Say it is usable!
    return False # If none of the good stuff was there, say it's not usable


def _trim_cache(cache: dict, ttl: float, max_entries: int = 200) -> None: # Defines a function to clean out old saved data so our computer doesn't run out of memory
    """Evict expired entries then, if still over max_entries, drop the oldest ones.""" # Explains that it throws away old stuff
    now = _time.time() # Gets the exact time right now
    expired = [k for k, v in cache.items() if isinstance(v, dict) and (now - v.get('ts', now)) > ttl] # Makes a list of all the saved items that are older than our time limit (ttl)
    for k in expired: # Loops through all the expired items
        cache.pop(k, None) # Deletes them from our saved memory
    if len(cache) > max_entries: # If we still have too many items saved
        sorted_keys = sorted( # Sort the items from oldest to newest
            (k for k, v in cache.items() if isinstance(v, dict)),
            key=lambda k: cache[k].get('ts', 0)
        )
        for k in sorted_keys[:len(cache) - max_entries]: # Loop through the oldest items until we are back under our maximum limit
            cache.pop(k, None) # Delete them


def _cleanup_share_image_cache(): # Defines a function to clean up old shareable images
    """Remove stale or excess share-image JSON files (disk-backed for multi-worker processes).""" # Explains what this does
    try: # Try to do the next block
        import os as _os # Imports a tool to interact with the computer's files
        _os.makedirs(_SHARE_IMAGE_CACHE_DIR, exist_ok=True) # Makes sure the folder for our images actually exists
    except OSError: # If we get an error making the folder
        return # Stop running the function
    now_ts = _time.time() # Get the current time
    paths = [] # Create an empty list to hold the file paths
    try: # Try to do the next block
        for fn in _os.listdir(_SHARE_IMAGE_CACHE_DIR): # Loop through all the files in our image folder
            if not fn.endswith('.json'): # If the file isn't a .json file
                continue # Skip it
            path = _os.path.join(_SHARE_IMAGE_CACHE_DIR, fn) # Build the full path to the file
            try: # Try the next block
