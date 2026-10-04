CREATE TABLE IF NOT EXISTS players (
  player_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  elo REAL DEFAULT 1500,
  elo_hard REAL DEFAULT 1500,
  elo_clay REAL DEFAULT 1500,
  elo_grass REAL DEFAULT 1500
);
CREATE TABLE IF NOT EXISTS matches (
  match_id TEXT PRIMARY KEY,
  match_date TEXT NOT NULL,
  tournament TEXT,
  surface TEXT,
  player_a TEXT NOT NULL,
  player_b TEXT NOT NULL,
  winner TEXT,
  sets_a INTEGER,
  sets_b INTEGER,
  games_a INTEGER,
  games_b INTEGER,
  status TEXT DEFAULT 'scheduled'
);
CREATE TABLE IF NOT EXISTS predictions (
  pred_id TEXT PRIMARY KEY,
  match_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  model_name TEXT NOT NULL,
  win_prob_a REAL,
  proj_sets_a REAL,
  proj_sets_b REAL,
  proj_games_total REAL,
  straight_sets_prob REAL,
  ou_games_line REAL,
  ou_pick TEXT,
  confidence REAL
);
CREATE TABLE IF NOT EXISTS grades (
  grade_id TEXT PRIMARY KEY,
  pred_id TEXT NOT NULL,
  winner_correct INTEGER,
  ou_result TEXT,
  result TEXT,
  graded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  model_name TEXT NOT NULL,
  accuracy REAL,
  brier REAL,
  notes TEXT
);
