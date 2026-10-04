# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Commonly Used Commands

**Install dependencies:**
```bash
pip install -e .
```

**Run the main application:**
```bash
python NHL77FINAL.py
```
The application will be available at `http://localhost:5000`.

**Linting and Testing:**
There are no explicitly defined commands for linting or running tests. It is recommended to add standard Python linting tools like `ruff` or `flake8` and a testing framework like `pytest`.

## High-level Code Architecture and Structure

This repository contains a multi-sport game prediction platform for NFL, NBA, NHL, MLB, WNBA, and NCAA Football.

### Core Components

*   **Main Application (`NHL77FINAL.py`):** A Flask application that serves as the main entry point for the platform.
*   **Database (`sports_predictions_original.db`):** An SQLite database that stores all sports data, predictions, and results.
*   **Models (`models/`):** This directory contains all the trained machine learning models for each sport. The models are built using a 4-model ensemble system (Elo, XGBoost, CatBoost, Meta).
*   **Schedules (`nhlschedules.py`):** This file contains the NHL schedule data, which is imported by the main application. Other sports have their own schedule files.
*   **Templates (`templates/`):** Standard Flask templates for rendering the HTML pages.
*   **Static Assets (`static/`):** Contains CSS, JavaScript, and images.

### Architecture Overview

The platform is designed as a unified Flask application with a modular, object-oriented backend. The core logic is separated into different modules for data collection, storage, modeling, and API layers.

*   **Data Collection:** A dual-source system automatically switches between user-provided Excel files for the regular season and official league APIs for playoffs.
*   **Machine Learning Pipeline:** The pipeline uses sport-specific feature engineering and modeling techniques. It includes cross-validation, a backtesting framework, and a league-average fallback mechanism.
*   **Prediction Generation:** Predictions are generated using `generate_real_predictions.py` from trained ensemble models.

### Important Notes

*   The `TO_DELETE/` directory contains a large number of old scripts, backups, and other files that are not part of the production application and can be safely deleted.
*   The project uses Python 3.11 and a list of dependencies can be found in `pyproject.toml`.
