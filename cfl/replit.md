# jackpotpicks.bet - Multi-Sport Prediction System

## Overview
jackpotpicks.bet is a multi-sport game prediction platform for NFL, NBA, NHL, MLB, WNBA, and NCAA Football. It uses machine learning to forecast game winners and totals, employing a dual-source data collection strategy (API for playoffs, Excel for regular season) and automated data pipelines. The platform is designed for sale as a production-ready sports analytics system.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### Unified Platform Design
The platform operates as a unified Flask application, running on a single port. It features a sport selector landing page where each sport is completely separate with no shared data visualization. Active sports are displayed as "Live Now," while others are marked "Coming Soon." Navigation is handled by a responsive navbar, optimized for mobile.

### Frontend
The Flask web application uses Jinja2 templates, offering a responsive mobile-first design with a clean, modern aesthetic. It includes a sport selector landing page, sport-specific prediction pages displaying all games from the season start, and results pages for model backtesting. The landing page dynamically calculates and displays real ensemble accuracy.

### Backend
The backend utilizes a modular, object-oriented design, separating concerns across data collection, storage, modeling, and API layers. It includes a comprehensive logging system and uses the `schedule` library for automated tasks. SQLite serves as the local database, and models/scalers are persisted using pickle.

### Machine Learning Pipeline
The core ML pipeline uses sport-specific Elo K-factors, enhanced with Margin of Victory (MoV) and Offensive/Defensive Split Elo adjustments. XGBoost models with heavily regularized, sport-specific hyperparameters are used to prevent overfitting. Feature engineering is extensive and tailored for each sport (e.g., NFL deep lag features, NHL goalie differentials). The system employs confidence-based weighting for NFL and dynamic ensemble weighting (XGBoost, Elo, Logistic Regression) for other sports. The pipeline includes cross-validation, a robust backtesting framework, and a league-average fallback mechanism.

### Data Collection
A dual-source system automatically switches between user-provided Excel files for the regular season and official league APIs for playoffs (MLB Stats API, NHL API, NBA, NFL). It includes rate limiting and error handling.

### Schedule Architecture
Sport-specific schedules are managed in a `schedules/` directory, each with a function to return a list of game dictionaries. Schedules are loaded into the database, clearing old data for yearly updates. Each sport has a hardcoded season start date, ensuring isolated schedule management within the unified platform.

### Prediction Generation & Automation
Predictions are generated using `generate_real_predictions.py` from trained ensemble models for all upcoming games, with training metadata persisted. Daily data updates, prediction generation, and weekly model retraining are automated via a configurable, non-blocking scheduler.

### NBA Model (2025-26 Season)
The NBA prediction system for the 2025-26 season features trained models (Elo, XGBoost, CatBoost, Logistic Regression, Meta Ensemble). Predictions directly use model probabilities (no inversion/fade strategy). XGBoost and CatBoost models achieve approximately 58.5% accuracy. The system displays all 346 games (Oct 21 - Dec 7, 2025), including completed ones, while training data from the 2024-25 season is used internally.

### NHL Model (2025-26 Season)
The NHL prediction system uses advanced ensemble models (Elo, XGBoost, CatBoost, Meta Ensemble) with comprehensive feature engineering (5/10-game rolling windows, home/away splits, rest days, strength of schedule, head-to-head history, goalie save percentage differential, winning/losing streaks, momentum indicators). Training uses 3,975 games from 3 NHL seasons (2022-23, 2023-24, 2024-25) fetched from NHL API via `fetch_nhl_historical_data.py`, stored in `nhl_historical_data.pkl`. Training employs time-based 80/20 split, exponential recency weighting (alpha=2.5), and optimized Elo K-factor=45.

**Recent Accuracy Improvements (Oct 30, 2025):**
- **Training on 2024 season only** (1,378 games) instead of mixed 2022-2025 data - better matches recent game patterns
- **Confidence-based ensemble weighting**: Models with higher confidence get more weight dynamically per game
- **Isotonic regression calibration** on validation set to fix overconfident predictions
- **Home ice bias correction** (-3% adjustment) to reduce home team over-prediction
- **Enhanced features**: goalie save percentage differential, winning/losing streaks, momentum indicators
- **Time-based train/val/test split** (70/15/15) with recency weighting (recent games weighted 12x more)
- **Early stopping** on validation set to prevent overfitting
- All 4 models available separately: XGBoost, CatBoost, Elo, Meta (user picks best performer)

The system displays 1,132 games for the 2025-26 season (season=2025 in database). **CRITICAL**: The season=2025 NHL schedule in the database must NEVER be modified - it is the current season prediction schedule shown to users.

## External Dependencies

### Core ML/Data Libraries
- `pandas`
- `numpy`
- `scikit-learn`
- `xgboost`

### Data Collection
- `nhl-api-py`
- `pybaseball`
- `openpyxl`
- `requests`
- `beautifulsoup4`
- `trafilatura`

### Web Interface
- `flask`
- `flask-login`
- `flask-wtf`
- `werkzeug`

### Utilities
- `schedule`
- `sqlite3`
- `logging`

### Data Sources
- NHL API (via `nhl-api-py`)
- NBA API
- NFL API
- MLB Stats API
- Excel Schedules (user-provided)