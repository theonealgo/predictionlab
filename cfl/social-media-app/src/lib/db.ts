// node:sqlite is built into Node.js 22.5+ (enable with --experimental-sqlite)
// The dev/build/start scripts in package.json set NODE_OPTIONS automatically.
import { DatabaseSync } from 'node:sqlite';
import path from 'path';

const DB_PATH = path.join(process.cwd(), 'social_media.db');

// Module-level singleton — persists across requests in the same server process
let _db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (!_db) {
    _db = new DatabaseSync(DB_PATH);
    // PRAGMA via exec (node:sqlite has no .pragma() helper)
    _db.exec('PRAGMA journal_mode = WAL');
    _db.exec('PRAGMA foreign_keys = ON');
    initializeSchema(_db);
  }
  return _db;
}

function initializeSchema(db: DatabaseSync): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS social_jobs (
      id                   TEXT PRIMARY KEY,
      name                 TEXT NOT NULL,
      url                  TEXT NOT NULL,
      screenshot_selector  TEXT NOT NULL,
      text_selector        TEXT NOT NULL,
      template_name        TEXT NOT NULL DEFAULT 'Cinematic Default',
      platforms            TEXT NOT NULL,   -- JSON array e.g. ["twitter","instagram"]
      schedule_time        TEXT NOT NULL,   -- "HH:MM" daily
      is_active            INTEGER NOT NULL DEFAULT 1,
      created_at           TEXT NOT NULL,
      last_run             TEXT,
      last_status          TEXT            -- "success" | "partial" | "pending" | "error"
    );

    CREATE TABLE IF NOT EXISTS post_history (
      id                TEXT PRIMARY KEY,
      job_id            TEXT NOT NULL,
      job_name          TEXT NOT NULL,
      caption           TEXT,
      hashtags          TEXT,
      screenshot_path   TEXT,
      extracted_text    TEXT,
      video_path        TEXT,
      export_path       TEXT,
      captions_path     TEXT,
      voiceover_path    TEXT,
      template_name     TEXT,
      credits_used      INTEGER NOT NULL DEFAULT 0,
      watermark_applied INTEGER NOT NULL DEFAULT 0,
      video_notes       TEXT,
      platforms         TEXT NOT NULL,   -- JSON array
      status            TEXT NOT NULL,
      platform_results  TEXT,            -- JSON array of PlatformResult
      created_at        TEXT NOT NULL,
      FOREIGN KEY (job_id) REFERENCES social_jobs(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS plan_subscription (
      id              INTEGER PRIMARY KEY CHECK (id = 1),
      plan_code       TEXT NOT NULL,
      plan_name       TEXT NOT NULL,
      monthly_credits INTEGER NOT NULL,
      updated_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS monthly_credit_usage (
      month_key     TEXT PRIMARY KEY,  -- YYYY-MM
      credits_used  INTEGER NOT NULL DEFAULT 0,
      updated_at    TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS social_connections (
      provider          TEXT PRIMARY KEY,
      account_label     TEXT,
      access_token      TEXT,
      refresh_token     TEXT,
      token_expires_at  TEXT,
      metadata_json     TEXT,
      connected_at      TEXT NOT NULL,
      updated_at        TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS oauth_states (
      state          TEXT PRIMARY KEY,
      provider       TEXT NOT NULL,
      code_verifier  TEXT,
      created_at     TEXT NOT NULL
    );
  `);

  ensureLegacyColumns(db);
  seedDefaultPlan(db);
}

function ensureLegacyColumns(db: DatabaseSync): void {
  addColumnIfMissing(
    db,
    'social_jobs',
    "template_name TEXT NOT NULL DEFAULT 'Cinematic Default'",
  );

  addColumnIfMissing(db, 'post_history', 'video_path TEXT');
  addColumnIfMissing(db, 'post_history', 'export_path TEXT');
  addColumnIfMissing(db, 'post_history', 'captions_path TEXT');
  addColumnIfMissing(db, 'post_history', 'voiceover_path TEXT');
  addColumnIfMissing(db, 'post_history', 'template_name TEXT');
  addColumnIfMissing(db, 'post_history', 'credits_used INTEGER NOT NULL DEFAULT 0');
  addColumnIfMissing(
    db,
    'post_history',
    'watermark_applied INTEGER NOT NULL DEFAULT 0',
  );
  addColumnIfMissing(db, 'post_history', 'video_notes TEXT');
}

function addColumnIfMissing(
  db: DatabaseSync,
  tableName: string,
  columnDefinition: string,
): void {
  const columnName = columnDefinition.trim().split(/\s+/)[0];
  const rows = db
    .prepare(`PRAGMA table_info(${tableName})`)
    .all() as Array<{ name: string }>;

  if (rows.some((r) => r.name === columnName)) return;
  db.exec(`ALTER TABLE ${tableName} ADD COLUMN ${columnDefinition}`);
}

function seedDefaultPlan(db: DatabaseSync): void {
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO plan_subscription (id, plan_code, plan_name, monthly_credits, updated_at)
    VALUES (1, 'unlimited_series', 'Unlimited Series', 5000, ?)
    ON CONFLICT(id) DO UPDATE SET
      plan_code = excluded.plan_code,
      plan_name = excluded.plan_name,
      monthly_credits = excluded.monthly_credits,
      updated_at = excluded.updated_at
  `).run(now);
}
