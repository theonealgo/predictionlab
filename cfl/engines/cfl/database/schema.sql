CREATE TABLE IF NOT EXISTS cfl_teams (
  team_id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  short_name TEXT,
  elo REAL DEFAULT 1500,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cfl_games (
  game_id TEXT PRIMARY KEY,
  cfl_id INTEGER,
  game_date TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  status TEXT DEFAULT 'scheduled',
  round_name TEXT,
  source TEXT,
  rest_home INTEGER,
  rest_away INTEGER,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cfl_team_stats (
  team_name TEXT PRIMARY KEY,
  games_played INTEGER DEFAULT 0,
  wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0,
  points_for REAL DEFAULT 0,
  points_against REAL DEFAULT 0,
  off_eff REAL DEFAULT 1.0,
  def_eff REAL DEFAULT 1.0,
  to_diff REAL DEFAULT 0,
  form_last5 REAL DEFAULT 0.5,
  qb_rating REAL DEFAULT 1.0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cfl_player_stats (
  player_id TEXT PRIMARY KEY,
  team_name TEXT,
  name TEXT NOT NULL,
  position TEXT,
  pass_yards REAL,
  rush_yards REAL,
  qb_rating REAL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS cfl_predictions (
  pred_id TEXT PRIMARY KEY,
  game_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  model_name TEXT NOT NULL,
  home_win_prob REAL,
  away_win_prob REAL,
  predicted_home_score REAL,
  predicted_away_score REAL,
  model_spread REAL,
  model_total REAL,
  pick_ml TEXT,
  confidence REAL,
  explanation TEXT,
  FOREIGN KEY(game_id) REFERENCES cfl_games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_cfl_games_date ON cfl_games(game_date);
CREATE INDEX IF NOT EXISTS idx_cfl_preds_game ON cfl_predictions(game_id);
