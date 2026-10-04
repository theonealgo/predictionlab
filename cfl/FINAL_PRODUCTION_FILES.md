# Production Files - Ready for Publishing

## Main Application
- **NHL77FINAL.py** - Main Flask application (runs on port 5000)

## Database
- **sports_predictions_original.db** - Complete database with all sports data, predictions, and results

## Schedules
- **nhlschedules.py** - NHL schedule data (imported by main app)

## Models (18 files in models/ directory)
All trained ML models for predictions:
- NHL models (10): catboost, xgboost, elo, logistic, meta, ensemble, calibrators, features
- NBA models (3): catboost, ensemble, features
- NFL models (1): ensemble
- MLB models (1): ensemble  
- NCAA Football models (1): ensemble
- Other: schedules.py, trained_feature_names.pkl

## Templates (7 HTML files in templates/ directory)
- base.html - Base template
- dashboard.html - Main dashboard
- index.html - Landing page
- login.html - Login page
- signup.html - Signup page
- sport_predictions.html - Predictions page
- results.html - Results page

## Static Assets (static/ directory)
- css/ - All stylesheets
- js/ - All JavaScript
- jackpot_logo.png - Logo
- sports_background.png - Background image

## Configuration
- .replit - Replit configuration
- pyproject.toml or requirements.txt - Python dependencies

---

**Total Essential Files: ~40**

Everything else has been moved to TO_DELETE/ folder and can be safely deleted.

## How to Publish
1. Test the app is working (it should be running now)
2. Click "Publish" button in Replit
3. Delete TO_DELETE/ folder after confirming app works

## App Features
- Multi-sport predictions (NHL, NFL, NBA, MLB, NCAAF, NCAAB)
- 4-model ensemble system (Elo, XGBoost, CatBoost, Meta)
- Dashboard with accuracy metrics
- Predictions page with upcoming games
- Results page with historical performance
