CREATE TABLE IF NOT EXISTS games (
  id SERIAL PRIMARY KEY,
  game_id TEXT UNIQUE NOT NULL,
  sport TEXT NOT NULL,
  league TEXT,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  start_time TIMESTAMP NOT NULL,
  status TEXT DEFAULT 'scheduled'
);

CREATE TABLE IF NOT EXISTS team_stats (
  id SERIAL PRIMARY KEY,
  sport TEXT NOT NULL,
  team TEXT NOT NULL,
  offense REAL,
  defense REAL,
  updated_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS team_stats_unique ON team_stats (sport, team);

CREATE TABLE IF NOT EXISTS model_outputs (
  id SERIAL PRIMARY KEY,
  game_id TEXT UNIQUE NOT NULL,
  win_prob_home REAL NOT NULL,
  win_prob_away REAL NOT NULL,
  expected_home_score REAL NOT NULL,
  expected_away_score REAL NOT NULL,
  spread REAL NOT NULL,
  total REAL NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS odds_lines (
  id SERIAL PRIMARY KEY,
  game_id TEXT UNIQUE NOT NULL,
  moneyline_home INTEGER,
  moneyline_away INTEGER,
  spread REAL,
  spread_price_home INTEGER,
  spread_price_away INTEGER,
  total REAL,
  total_over_price INTEGER,
  total_under_price INTEGER,
  vig REAL DEFAULT 0.04,
  source TEXT DEFAULT 'engine',
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS line_movement (
  id SERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  market TEXT NOT NULL,
  prev_line TEXT,
  new_line TEXT,
  reason TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bets (
  id SERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  market TEXT NOT NULL,
  side TEXT NOT NULL,
  stake REAL NOT NULL,
  price INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_exposure (
  id SERIAL PRIMARY KEY,
  game_id TEXT NOT NULL,
  market TEXT NOT NULL,
  home_liability REAL DEFAULT 0,
  away_liability REAL DEFAULT 0,
  over_liability REAL DEFAULT 0,
  under_liability REAL DEFAULT 0,
  updated_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO games (game_id, sport, league, home_team, away_team, start_time)
VALUES
  ('NBA_SAMPLE_1', 'NBA', 'NBA', 'Boston Celtics', 'Miami Heat', NOW() + INTERVAL '1 day'),
  ('NHL_SAMPLE_1', 'NHL', 'NHL', 'Toronto Maple Leafs', 'Montreal Canadiens', NOW() + INTERVAL '1 day'),
  ('MLB_SAMPLE_1', 'MLB', 'MLB', 'New York Yankees', 'Boston Red Sox', NOW() + INTERVAL '2 days'),
  ('SOCCER_SAMPLE_1', 'SOCCER', 'English Premier League', 'Arsenal', 'Chelsea', NOW() + INTERVAL '2 days')
ON CONFLICT DO NOTHING;

INSERT INTO team_stats (sport, team, offense, defense)
VALUES
  ('NBA', 'Boston Celtics', 118.4, 111.2),
  ('NBA', 'Miami Heat', 111.1, 110.5),
  ('NHL', 'Toronto Maple Leafs', 3.4, 2.7),
  ('NHL', 'Montreal Canadiens', 2.8, 3.2),
  ('MLB', 'New York Yankees', 4.9, 3.9),
  ('MLB', 'Boston Red Sox', 4.6, 4.2),
  ('SOCCER', 'Arsenal', 1.9, 0.9),
  ('SOCCER', 'Chelsea', 1.6, 1.1)
ON CONFLICT DO NOTHING;
