#!/usr/bin/env python3
"""
Automated Weekly Model Retraining Script
Retrains all sports models with latest game data to maintain accuracy
Run this every Sunday night via cron job
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pickle
import logging
from pathlib import Path

# Import ML libraries
try:
    import xgboost as xgb
    from catboost import CatBoostClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
    HAS_ML = True
except ImportError:
    HAS_ML = False
    print("⚠️  ML libraries not installed. Install with: pip install xgboost catboost scikit-learn")

# Import feature engineering modules
try:
    from nhl_feature_engineering import NHLFeatureEngineer
    from nba_feature_engineering import NBAFeatureEngineer
    HAS_FEATURES = True
except ImportError:
    HAS_FEATURES = False
    print("⚠️  Feature engineering modules not found")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WeeklyModelRetrainer:
    def __init__(self, db_path='sports_predictions_original.db', models_dir='models'):
        self.db_path = db_path
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(exist_ok=True)
        
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def get_training_data(self, sport, min_games=100):
        """Fetch completed games for training"""
        conn = self.get_connection()
        
        # Get games with results from past season
        query = '''
            SELECT home_team_id, away_team_id, home_score, away_score, game_date
            FROM games
            WHERE sport = ?
            AND home_score IS NOT NULL
            AND away_score IS NOT NULL
            ORDER BY game_date ASC
        '''
        
        df = pd.read_sql_query(query, conn, params=(sport,))
        conn.close()
        
        if len(df) < min_games:
            logger.warning(f"Insufficient data for {sport}: {len(df)} games (need {min_games})")
            return None
        
        logger.info(f"Loaded {len(df)} completed {sport} games for training")
        return df
    
    def engineer_features(self, df, sport):
        """Engineer features for each game"""
        if not HAS_FEATURES:
            logger.warning("Feature engineering modules not available, using basic features")
            return self._basic_features(df)
        
        features_list = []
        targets = []
        
        if sport == 'NHL':
            engineer = NHLFeatureEngineer(self.db_path)
        elif sport == 'NBA':
            engineer = NBAFeatureEngineer(self.db_path)
        else:
            return self._basic_features(df)
        
        for idx, row in df.iterrows():
            try:
                game_date = row['game_date']
                home_team = row['home_team_id']
                away_team = row['away_team_id']
                
                # Generate features
                features = engineer.engineer_features(home_team, away_team, game_date)
                features_list.append(features)
                
                # Target: 1 if home team won, 0 otherwise
                target = 1 if row['home_score'] > row['away_score'] else 0
                targets.append(target)
                
            except Exception as e:
                logger.debug(f"Error engineering features for game {idx}: {e}")
                continue
        
        if not features_list:
            logger.warning(f"No features engineered for {sport}, falling back to basic")
            return self._basic_features(df)
        
        # Convert to DataFrame
        X = pd.DataFrame(features_list)
        y = np.array(targets)
        
        logger.info(f"Engineered {X.shape[1]} features for {len(X)} {sport} games")
        return X, y
    
    def _basic_features(self, df):
        """Fallback: Basic features using win percentage"""
        # Calculate rolling win rates
        teams = pd.unique(df[['home_team_id', 'away_team_id']].values.ravel())
        team_wins = {team: 0 for team in teams}
        team_games = {team: 0 for team in teams}
        
        features = []
        targets = []
        
        for idx, row in df.iterrows():
            home = row['home_team_id']
            away = row['away_team_id']
            
            # Feature: home team win rate and away team win rate
            home_win_rate = team_wins.get(home, 0) / max(1, team_games.get(home, 1))
            away_win_rate = team_wins.get(away, 0) / max(1, team_games.get(away, 1))
            
            features.append([home_win_rate, away_win_rate, home_win_rate - away_win_rate])
            
            # Target
            home_won = 1 if row['home_score'] > row['away_score'] else 0
            targets.append(home_won)
            
            # Update stats
            team_games[home] = team_games.get(home, 0) + 1
            team_games[away] = team_games.get(away, 0) + 1
            
            if home_won:
                team_wins[home] = team_wins.get(home, 0) + 1
            else:
                team_wins[away] = team_wins.get(away, 0) + 1
        
        X = pd.DataFrame(features, columns=['home_win_rate', 'away_win_rate', 'win_rate_diff'])
        y = np.array(targets)
        
        return X, y
    
    def train_models(self, X, y, sport):
        """Train XGBoost, CatBoost, and Meta ensemble"""
        if not HAS_ML:
            logger.error("ML libraries not available, skipping training")
            return None
        
        # Use TimeSeriesSplit for temporal validation
        tscv = TimeSeriesSplit(n_splits=5)
        
        # Train/test split (use last 20% as test set)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        
        logger.info(f"Training {sport} models on {len(X_train)} games, testing on {len(X_test)}")
        
        # Scale features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # Train XGBoost
        logger.info("Training XGBoost...")
        xgb_model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric='logloss'
        )
        xgb_model.fit(X_train_scaled, y_train)
        xgb_acc = xgb_model.score(X_test_scaled, y_test)
        logger.info(f"XGBoost accuracy: {xgb_acc:.3f}")
        
        # Train CatBoost
        logger.info("Training CatBoost...")
        cat_model = CatBoostClassifier(
            iterations=200,
            depth=5,
            learning_rate=0.05,
            random_state=42,
            verbose=False
        )
        cat_model.fit(X_train_scaled, y_train)
        cat_acc = cat_model.score(X_test_scaled, y_test)
        logger.info(f"CatBoost accuracy: {cat_acc:.3f}")
        
        # Train Meta Model (Logistic Regression on model outputs)
        logger.info("Training Meta Ensemble...")
        xgb_probs = xgb_model.predict_proba(X_train_scaled)[:, 1]
        cat_probs = cat_model.predict_proba(X_train_scaled)[:, 1]
        
        meta_features = np.column_stack([xgb_probs, cat_probs])
        meta_model = LogisticRegression(random_state=42)
        meta_model.fit(meta_features, y_train)
        
        # Test meta model
        xgb_test_probs = xgb_model.predict_proba(X_test_scaled)[:, 1]
        cat_test_probs = cat_model.predict_proba(X_test_scaled)[:, 1]
        meta_test_features = np.column_stack([xgb_test_probs, cat_test_probs])
        meta_acc = meta_model.score(meta_test_features, y_test)
        logger.info(f"Meta Ensemble accuracy: {meta_acc:.3f}")
        
        return {
            'xgboost': xgb_model,
            'catboost': cat_model,
            'meta': meta_model,
            'scaler': scaler,
            'feature_names': X.columns.tolist(),
            'accuracies': {
                'xgboost': xgb_acc,
                'catboost': cat_acc,
                'meta': meta_acc
            },
            'trained_date': datetime.now().strftime('%Y-%m-%d'),
            'training_samples': len(X_train)
        }
    
    def save_models(self, models_dict, sport):
        """Save trained models to disk"""
        model_path = self.models_dir / f"{sport.lower()}_ensemble.pkl"
        
        with open(model_path, 'wb') as f:
            pickle.dump(models_dict, f)
        
        logger.info(f"✅ Saved {sport} models to {model_path}")
    
    def retrain_sport(self, sport):
        """Complete retraining pipeline for one sport"""
        logger.info(f"\n{'='*60}")
        logger.info(f"RETRAINING {sport} MODELS")
        logger.info(f"{'='*60}")
        
        # Load data
        df = self.get_training_data(sport)
        if df is None:
            return False
        
        # Engineer features
        try:
            X, y = self.engineer_features(df, sport)
        except Exception as e:
            logger.error(f"Feature engineering failed for {sport}: {e}")
            return False
        
        # Train models
        try:
            models = self.train_models(X, y, sport)
            if models is None:
                return False
        except Exception as e:
            logger.error(f"Model training failed for {sport}: {e}")
            return False
        
        # Save models
        try:
            self.save_models(models, sport)
        except Exception as e:
            logger.error(f"Failed to save {sport} models: {e}")
            return False
        
        # Print summary
        logger.info(f"\n📊 {sport} Training Summary:")
        logger.info(f"   Training samples: {models['training_samples']}")
        logger.info(f"   Features: {len(models['feature_names'])}")
        logger.info(f"   XGBoost accuracy: {models['accuracies']['xgboost']:.1%}")
        logger.info(f"   CatBoost accuracy: {models['accuracies']['catboost']:.1%}")
        logger.info(f"   Meta accuracy: {models['accuracies']['meta']:.1%}")
        
        return True
    
    def retrain_all(self):
        """Retrain all sports models"""
        logger.info("\n" + "🔄 " + "="*58)
        logger.info("   WEEKLY MODEL RETRAINING - " + datetime.now().strftime('%Y-%m-%d %H:%M'))
        logger.info("="*60 + "\n")
        
        sports = ['NHL', 'NBA', 'NFL', 'MLB', 'NCAAF', 'NCAAB']
        results = {}
        
        for sport in sports:
            try:
                success = self.retrain_sport(sport)
                results[sport] = 'SUCCESS' if success else 'FAILED'
            except Exception as e:
                logger.error(f"Unexpected error retraining {sport}: {e}")
                results[sport] = 'ERROR'
        
        # Summary
        logger.info("\n" + "="*60)
        logger.info("RETRAINING COMPLETE")
        logger.info("="*60)
        for sport, status in results.items():
            emoji = '✅' if status == 'SUCCESS' else '❌'
            logger.info(f"{emoji} {sport:10s}: {status}")
        logger.info("="*60 + "\n")


def main():
    """Main execution"""
    retrainer = WeeklyModelRetrainer()
    retrainer.retrain_all()


if __name__ == "__main__":
    main()
