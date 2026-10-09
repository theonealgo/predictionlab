CREATE TABLE IF NOT EXISTS ufc_fighters (
  fighter_id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  elo REAL DEFAULT 1500,
  record_summary TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ufc_fights (
  fight_id TEXT PRIMARY KEY,
  event_id TEXT,
  event_name TEXT,
  fight_date TEXT NOT NULL,
  home_fighter TEXT NOT NULL,
  away_fighter TEXT NOT NULL,
  home_id TEXT,
  away_id TEXT,
  winner TEXT,
  status TEXT DEFAULT 'scheduled',
  home_record TEXT,
  away_record TEXT,
  source TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ufc_predictions (
  pred_id TEXT PRIMARY KEY,
  fight_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  model_name TEXT NOT NULL,
  home_win_prob REAL,
  away_win_prob REAL,
  pick_ml TEXT,
  confidence REAL,
  prob_source TEXT,
  home_ml INTEGER,
  away_ml INTEGER,
  books_count INTEGER DEFAULT 0,
  explanation TEXT,
  FOREIGN KEY(fight_id) REFERENCES ufc_fights(fight_id)
);

CREATE INDEX IF NOT EXISTS idx_ufc_fights_date ON ufc_fights(fight_date);
CREATE INDEX IF NOT EXISTS idx_ufc_preds_fight ON ufc_predictions(fight_id);
