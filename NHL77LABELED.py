#!/usr/bin/env python3 # This tells the computer to run this file using Python 3
""" # This starts a big block of text to describe what the file is about
predictionlab.io - Multi-Sport Prediction Platform # The name of the website
================================================== # Just a decorative line
Complete platform with Dashboard, Predictions, and Results pages for all sports. # Explains that this file runs the whole website for sports predictions
5-Model System: Glicko-2, TrueSkill, Elo, XGBoost, Ensemble # Lists the 5 different math models used to make predictions
""" # This ends the big block of text

from flask import Flask, render_template, render_template_string, request, jsonify, redirect, url_for, Response, send_from_directory, abort, has_request_context # Imports tools from Flask to build the web server and send web pages to users
from flask_login import current_user # Imports a tool to check if a user is logged in
from werkzeug.middleware.proxy_fix import ProxyFix # Imports a tool to help the web server work correctly behind other servers
import json # Imports a tool to read and write data in JSON format, which is like a dictionary for computers
import sys # Imports a tool to interact with the computer's system
import re # Imports a tool to search for specific text patterns
import csv # Imports a tool to read and write spreadsheet files (like Excel)
import io # Imports a tool to handle reading and writing data in memory
import uuid # Imports a tool to generate unique random ID numbers
import importlib # Imports a tool to load other Python files while the program is running
import importlib.util # More tools for loading other Python files
import glob # Imports a tool to find files on the computer that match a certain name pattern
import types # Imports a tool to check the type of a variable (like if it's a function or a number)
from collections import defaultdict # Imports a special dictionary that automatically creates a default value if a key is missing
from flask_cors import CORS # Imports a tool to allow other websites to request data from our server safely
import sqlite3 # Imports a tool to read and write to our database file (where all the sports data is stored)
import pandas as pd # Imports a powerful tool for analyzing tables of data
import numpy as np # Imports a powerful tool for doing math with numbers and arrays
from datetime import datetime, timedelta # Imports tools to work with dates and times
from zoneinfo import ZoneInfo # Imports a tool to handle different time zones
import logging # Imports a tool to write down messages so we can see what the program is doing
import nfl_data_py as nfl # Imports a special tool to download NFL football data
from nhlschedules import get_nhl_2025_schedule # Imports a function to get the schedule for NHL hockey games
import requests # Imports a tool to download web pages and data from the internet
from nba_sportsdata_api import NBASportsDataAPI # Imports our custom tool to get NBA basketball data
from nhl_api import NHLAPI # Imports our custom tool to get NHL hockey data
from value_predictor import ValuePredictor # Imports our custom tool that tries to find good bets
from ats_system import ATSSystem # Imports our custom tool for "Against The Spread" betting analysis
from soccer_models import build_soccer_model_bundle # Imports a function to build the prediction models for Soccer

# V2 PREDICTION SYSTEM - Upgraded architecture # A comment saying the next section is for the new, better prediction system
import os as _os_v2 # Imports the operating system tools and renames it to _os_v2 so it doesn't get confused with anything else
_V2_BASE = _os_v2.path.dirname(_os_v2.path.abspath(__file__)) # Finds the exact folder path where this file is currently saved on the computer
try: # Tells the computer to try the next block of code, and if it crashes, don't stop the whole program
    from prediction_system_v2 import AdvancedPredictor # Tries to import the new AdvancedPredictor tool
    V2_PREDICTORS = {} # Creates an empty dictionary to hold our new prediction models
    # Load trained models for supported sports # A comment explaining what the next loop does
    for sport in ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAF', 'NCAAB']: # Loops through a list of 6 major sports
        try: # Tells the computer to try loading the model for the current sport, and if it fails, just skip it
            _model_path = _os_v2.path.join(_V2_BASE, 'models', f'{sport}_v2') # Builds the file path to where the model for this sport is saved
            V2_PREDICTORS[sport] = AdvancedPredictor.load(sport, _model_path) # Loads the advanced model from the file path and saves it in our dictionary
            print(f"✅ Loaded {sport} v2 predictor (Glicko-2 + Ensemble + Calibration)") # Prints a success message to the screen if it worked
        except Exception as e: # If loading the model failed, catch the error
            print(f"⚠️ {sport} v2 model not found at {_model_path}: {e}") # Print a warning message saying the model couldn't be loaded
    HAS_V2_SYSTEM = len(V2_PREDICTORS) > 0 # Checks if we successfully loaded at least one model, and saves True or False
except ImportError as e: # If the first "try" failed because prediction_system_v2 doesn't exist at all, catch the error here
    print(f"⚠️ V2 prediction system not available: {e}") # Print a warning message that the whole V2 system is missing
    V2_PREDICTORS = {} # Set the dictionary to be empty since we don't have any models
    HAS_V2_SYSTEM = False # Set this to False since the system isn't available

logging.basicConfig(level=logging.INFO) # Sets up our logging tool to only show important messages (INFO) and hide minor ones (DEBUG)
logger = logging.getLogger(__name__) # Creates a specific logger for this file so we know where messages came from


def _init_datadog_tracing(): # Defines a function to set up Datadog, which is a tool that monitors the server to see if it's running smoothly
    """Enable ddtrace before Flask app import side-effects (no-op unless DD_TRACE_ENABLED).""" # A comment explaining what this function does
    flag = (_os_v2.environ.get('DD_TRACE_ENABLED') or '').lower() # Checks the computer's secret environment variables to see if tracing is turned on
    if flag not in ('1', 'true', 'yes') and not _os_v2.environ.get('DD_API_KEY'): # If the flag isn't turned on, and we don't have an API key (password) for Datadog
        return # Stop running this function right here and do nothing else
    try: # Try to set up Datadog tracing
        from ddtrace import config, patch_all # Import the Datadog tools
        patch_all() # Tells Datadog to start watching everything the program does automatically
        config.service = _os_v2.environ.get('DD_SERVICE', 'predictionlab') # Sets the name of our service to 'predictionlab' so Datadog knows who we are
        config.env = _os_v2.environ.get('DD_ENV', 'production') # Tells Datadog if this is a test server or the real production server
        if _os_v2.environ.get('DD_VERSION'): # Checks if we have a specific version number saved
            config.version = _os_v2.environ['DD_VERSION'] # Tells Datadog what version of the code we are running
        logger.info('[datadog] ddtrace enabled service=%s env=%s', config.service, config.env) # Prints a message saying Datadog is successfully turned on
    except Exception as _dde: # If anything above crashed, catch the error
        logger.warning('[datadog] ddtrace init failed: %s', _dde) # Print a warning saying Datadog failed to turn on


_init_datadog_tracing() # Actually calls the function we just defined above to set up Datadog

import time as _time # Imports a tool to work with time, like checking what time it is, and renames it to _time
import copy as _copy # Imports a tool to make exact copies of lists and dictionaries
# NOTE: several MODULE-LEVEL blocks reference the bare global name `threading` # A long comment explaining why the next tool is imported
# (the odds/predictions prewarm thread starts and _persist_predictions_to_disk's # (continued explanation)
# threading.get_ident()). Those are NOT covered by the function-local `import # (continued explanation)
# threading` statements elsewhere, nor by the aliased `import threading as # (continued explanation)
# _preds_thr`. Without this top-level import they raised a swallowed NameError, # (continued explanation)
# silently disabling the prewarmers AND the predictions disk cache on every boot. # (continued explanation)
import threading # Imports a tool that lets the computer do multiple tasks at the exact same time
try: # Try to load tools for working with images
    from PIL import Image, ImageDraw, ImageFont # Imports tools to open images, draw on them, and write text on them
    _HAS_PIL = True # Sets a variable to True to remember that we have the image tools
except Exception: # If importing the image tools fails
    Image = ImageDraw = ImageFont = None # Set the tools to None (nothing)
    _HAS_PIL = False # Set the variable to False to remember we don't have the image tools

# ── Module-level HTTP request cache (15-min TTL) ────────────────────────────── # A decorative comment marking a section where we set up caching (saving data so we don't have to download it again)
_API_CACHE: dict = {} # Creates an empty dictionary to save data we download from the internet
_API_TTL = 900  # seconds # Says that we should keep downloaded data for 900 seconds (15 minutes) before downloading it fresh again
_PREDICTIONS_CACHE: dict = {} # Creates an empty dictionary to save the math model predictions so we don't have to calculate them every time someone visits the page
_V2_PREDICTION_CACHE: dict = {} # Creates another empty dictionary to save predictions from the new V2 models
_V2_PREDICTION_TTL_SECONDS = 900 # Says we should keep the V2 predictions for 900 seconds (15 minutes)
_PREDICTIONS_TTL_BY_SPORT = { # Creates a dictionary that sets a different time limit for how long to save predictions for each sport
    'NHL': 180, # Keep hockey predictions for 180 seconds (3 minutes)
    'NBA': 180, # Keep basketball predictions for 180 seconds (3 minutes)
    'NCAAB': 180, # Keep college basketball predictions for 180 seconds (3 minutes)
    'NCAAW': 180, # Keep women's college basketball predictions for 180 seconds (3 minutes)
    'MLB': 240, # Keep baseball predictions for 240 seconds (4 minutes)
    'NFL': 300, # Keep football predictions for 300 seconds (5 minutes)
    'NCAAF': 300, # Keep college football predictions for 300 seconds (5 minutes)
    'WNBA': 240, # Keep women's basketball predictions for 240 seconds (4 minutes)
    'SOCCER': 240, # Keep soccer predictions for 240 seconds (4 minutes)
}
_SPORT_RESULTS_CACHE: dict = {} # Creates an empty dictionary to save the results of past games
_SPORT_RESULTS_TTL_BY_SPORT = { # Creates a dictionary that sets a different time limit for how long to save past results for each sport
    'NHL': 300, # Keep hockey results for 300 seconds (5 minutes)
    'NBA': 600, # Keep basketball results for 600 seconds (10 minutes)
    'NCAAB': 240, # Keep college basketball results for 240 seconds (4 minutes)
    'NCAAW': 240, # Keep women's college basketball results for 240 seconds (4 minutes)
    'MLB': 300, # Keep baseball results for 300 seconds (5 minutes)
    'NCAAF': 300, # Keep college football results for 300 seconds (5 minutes)
    'NFL': 300, # Keep football results for 300 seconds (5 minutes)
    'WNBA': 300, # Keep women's basketball results for 300 seconds (5 minutes)
    'SOCCER': 300, # Keep soccer results for 300 seconds (5 minutes)
}
_SOCCER_MODEL_CACHE: dict = {} # Creates an empty dictionary to save soccer models specifically
_SOCCER_MODEL_TTL = 900 # Keep the soccer models for 900 seconds (15 minutes)
_LANDING_BANNER_CACHE = {'ts': 0, 'messages': []} # Creates a dictionary to save the banner messages shown on the homepage
_LANDING_BANNER_TTL = 900 # Keep the homepage banner messages for 900 seconds (15 minutes)
_DAILY_REPORT_CACHE = {'ts': 0, 'date': None, 'html': None} # Creates a dictionary to save the daily report webpage so we don't have to rebuild it
_DAILY_REPORT_TTL = 300 # Keep the daily report webpage for 300 seconds (5 minutes)
_SPORT_PREDICTIONS_PAGE_CACHE: dict = {} # Creates an empty dictionary to save entire web pages of predictions
_SPORT_PREDICTIONS_PAGE_TTL = { # Creates a dictionary to set how long to save entire web pages of predictions for each sport
    'SOCCER': 300, # Keep the soccer predictions web page for 300 seconds (5 minutes)
    'MLB': 240, # Keep the baseball predictions web page for 240 seconds (4 minutes)
    'NHL': 180, # Keep the hockey predictions web page for 180 seconds (3 minutes)
    'NBA': 180, # Keep the basketball predictions web page for 180 seconds (3 minutes)
    'NFL': 240, # Keep the football predictions web page for 240 seconds (4 minutes)
    'NCAAB': 240, # Keep the college basketball predictions web page for 240 seconds (4 minutes)
    'NCAAW': 240, # Keep the women's college basketball predictions web page for 240 seconds (4 minutes)
    'NCAAF': 240, # Keep the college football predictions web page for 240 seconds (4 minutes)
    'WNBA': 240, # Keep the women's basketball predictions web page for 240 seconds (4 minutes)
}
_MANUAL_BANNER_ITEMS = [ # Creates a list of top-performing models to show off in a banner at the top of the website
    {'label': 'NHL ⭐ Grinder2', 'pct': '83.3%', 'record': '40-8'}, # Shows the Grinder2 hockey model's winning percentage and record
    {'label': '🎲 NBA O/U (XSharp)', 'pct': '82.6%', 'record': '247/299'}, # Shows the XSharp basketball over/under model's winning percentage and record
    {'label': 'MLB 🎯 Moneyline (Sharp Consensus)', 'pct': '60.0%', 'record': '60-40'}, # Shows the Sharp Consensus baseball moneyline model's winning percentage and record
    {'label': 'NHL 📊 Edge', 'pct': '56.5%', 'record': '113-87'}, # Shows the Edge hockey model's winning percentage and record
] # Ends the list of top-performing models
