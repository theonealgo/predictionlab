# ==============================================================================
# NHL77LABELED_BUGS.py 
# This file contains the exact pieces of code causing your specific errors.
# Every section includes the EXACT FILE, LINE NUMBER, and snippet to fix them.
# ==============================================================================

# ------------------------------------------------------------------------------
# BUG 1: GOLF, UFC, AND TENNIS MISSING PREDICTIONS
# 📍 Exact Location: NHL77FINAL.py (Lines 17150 to 17167)
# ------------------------------------------------------------------------------
# --- BEFORE (Broken - Hides the error) ---
# try:
#     from sports import TENNIS as _tennis_sport
# ...
# except Exception as _e_newsports:
#     print(f"⚠️ New sports modules not loaded: {_e_newsports}")

# --- AFTER (The Fix snippet to find the real bug) ---
# Go to Line 17150 in NHL77FINAL.py and add a '#' in front of 'try:' and the 'except:' lines.
# This disables the shield. Run the server again to see the real crash error, fix that typo, 
# and then turn the shield back on!


# ------------------------------------------------------------------------------
# BUG 2: SOCCER CHART VIEW MISSING "H2H LAST 10"
# 📍 Exact Location: NHL77FINAL.py (Lines 1218 to 1224) 
# AND sports/SOCCER.py (Line 771 UI generator)
# ------------------------------------------------------------------------------
# The Problem: The backend calculates H2H for Soccer correctly at Line 1218 in NHL77FINAL.py, 
# but the custom Soccer results UI (in sports/SOCCER.py around line 771) doesn't have 
# the HTML code written to display the `h2h_last10_total` column on the screen!


# ------------------------------------------------------------------------------
# BUG 3: MLB ALL-STARS CAUSING 502 SERVER CRASHES
# 📍 Exact Location: NHL77FINAL.py (Line 2555)
# ------------------------------------------------------------------------------
# --- BEFORE (Broken) ---
#   'Texas Rangers': 'tex', 'Toronto Blue Jays': 'tor', 'Washington Nationals': 'wsh',

# --- AFTER (The Fix snippet) ---
# Go to line 2555 in NHL77FINAL.py and add the missing All-Star abbreviations:
#   'Texas Rangers': 'tex', 'Toronto Blue Jays': 'tor', 'Washington Nationals': 'wsh',
#   'American League All-Stars': 'al', 'National League All-Stars': 'nl',


# ------------------------------------------------------------------------------
# BUG 4: WNBA MISSING 2 MODELS (ONLY HAS 3)
# 📍 Exact Location: NHL77FINAL.py (Line 46)
# ------------------------------------------------------------------------------
# (NOT A BUG): On Line 46, you will see this loop:
# for sport in ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAF', 'NCAAB']:
# WNBA has not been upgraded to the V2 5-model AI system yet. The /models folder 
# does not have a WNBA_v2 file. Your 3-model system is working perfectly. Do NOT
# add WNBA to this list or your server will crash looking for a file that doesn't exist.


# ------------------------------------------------------------------------------
# BUG 5: EDGE VALUE PAGE NOT WORKING
# 📍 Exact Location: templates/base.html (Line 324) AND NHL77FINAL.py (Line 18020)
# ------------------------------------------------------------------------------
# The Problem: The Edge Value page actually DOES exist! It is located at:
# @app.route('/edge-performance') on Line 18020 in NHL77FINAL.py.
# If your navigation menu is clicking to a broken "/edge" link, you just need to
# open templates/base.html (Line 324) and change the link:
#
# --- BEFORE (Broken link in base.html) ---
# {l:'Edge Performance',h:'/edge'}
#
# --- AFTER (The Fix snippet) ---
# {l:'Edge Performance',h:'/edge-performance'}


# ------------------------------------------------------------------------------
# BUG 6: ALL SPORTS RESULTS PAGE MISSING NCAAW ML & PL
# 📍 Exact Location: NHL77FINAL.py (Line 16511)
# ------------------------------------------------------------------------------
# --- BEFORE (Broken) ---
#     if sport in ['NCAAB', 'NCAAW', 'NCAAF', 'MLB', 'WNBA', 'SOCCER']:
#         # Server tries to read 'moneyline' or 'spread'
#         moneyline = the_api_data['moneyline']
#         spread = the_api_data['spread']

# --- AFTER (The Fix snippet) ---
# Around line 16511, tell the code to default to 'N/A' using python's .get() so it doesn't blank out:
#         safe_moneyline = the_api_data.get('moneyline', 'N/A')
#         safe_spread = the_api_data.get('spread', 'N/A')


# ------------------------------------------------------------------------------
# BUG 7: WNBA MISSING GRINDER2 & TAKEDOWN LABELS
# 📍 Exact Location: NHL77FINAL.py (Lines 7190 to 7200)
# ------------------------------------------------------------------------------
# The Problem: WNBA used to have Grinder2 and Takedown labels on its cards. 
# But in the new codebase, these labels are ONLY attached if a V2 model exists!
# 
# Look at Lines 7190 to 7199 (inside the `elif v2_pred:` block):
#    game['glicko2_prob'] = v2_pred.get('glicko2_prob')
#    game['trueskill_prob'] = v2_pred.get('trueskill_prob')
#
# Because WNBA does NOT have a V2 model (see Bug 4), it skips those lines and goes 
# straight to the `else:` block on Line 7200. The `else` block calculates basic Elo, 
# XGBoost, and Ensemble... but completely forgets to attach the Grinder2 and Takedown 
# variables to the game! That is why they suddenly disappeared from your WNBA cards.


# ------------------------------------------------------------------------------
# BUG 8: SOCCER PICKS MISSING NOINDEX TAG
# 📍 Exact Location: templates/espn_predictions_template.html (Line 15)
# ------------------------------------------------------------------------------
# The Problem: The HTML template automatically hides empty pages from Google by using 
# a "noindex" tag, but it doesn't do it specifically for Soccer pages if they DO have predictions.
#
# --- BEFORE (Broken) ---
# {% if grouped_predictions is defined and not grouped_predictions %}<meta name="robots" content="noindex, follow">{% endif %}
#
# --- AFTER (The Fix snippet) ---
# Go to Line 15 in templates/espn_predictions_template.html and tell it to ALSO hide it if the sport is SOCCER:
# {% if (grouped_predictions is defined and not grouped_predictions) or sport == 'SOCCER' %}<meta name="robots" content="noindex, follow">{% endif %}


# ------------------------------------------------------------------------------
# BUG 9: NCAAW PL SPREAD/TOTAL MISSING FROM RESULTS PAGE (BLANK DASHES)
# 📍 Exact Location: NHL77FINAL.py (Line 9091 and Line 9132)
# ------------------------------------------------------------------------------
# The Problem: NCAAW doesn't have a V2 model, so its "PL Spread" (our_spread) relies 
# purely on historical Head-to-Head data (Line 3462). But college teams rarely play 
# out-of-conference opponents more than once, so H2H always fails! When our_spread is blank, 
# the grading script completely skips PL grading for NCAAW, resulting in the blank "—".
#
# --- BEFORE (Broken - Line 9091) ---
#                     ps = _safe_float(g.get('our_spread'))
#
# --- AFTER (The Fix snippet for SPREAD - Line 9091) ---
#                     ps = _safe_float(g.get('our_spread'))
#                     if ps is None and sport == 'NCAAW' and xs is not None:
#                         ps = xs
#
# --- BEFORE (Broken - Line 9132) ---
#                     pt = _safe_float(g.get('our_total'))
#
# --- AFTER (The Fix snippet for OVER/UNDER - Line 9132) ---
#                     pt = _safe_float(g.get('our_total'))
#                     if pt is None and sport == 'NCAAW' and xt is not None:
#                         pt = xt
