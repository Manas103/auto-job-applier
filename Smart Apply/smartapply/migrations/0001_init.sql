-- Smart Apply tracker. One row per application attempt on a pipeline job.
-- Secrets never live here: account passwords are in the Windows Credential Manager (vault.py).

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
  id             INTEGER PRIMARY KEY,
  job_id         INTEGER NOT NULL UNIQUE,      -- pipeline jobs.id
  company        TEXT NOT NULL,
  title          TEXT NOT NULL,
  url            TEXT NOT NULL,
  ats            TEXT,                          -- workday | greenhouse | lever | ashby | smartrecruiters | ... | NULL
  ats_id         TEXT,
  ats_params     TEXT NOT NULL DEFAULT '{}',    -- JSON from intake.detect_ats
  role_folder    TEXT NOT NULL,
  slug           TEXT NOT NULL,
  workdir        TEXT NOT NULL,
  state          TEXT NOT NULL DEFAULT 'queued',
  reason         TEXT,                          -- needs_human reason / failure summary
  target_id      TEXT,                          -- CDP target of the application tab
  tab_url        TEXT,
  tenant         TEXT,                          -- Workday tenant (account key)
  generation     TEXT,                          -- Workday markup generation: old | new
  application_id TEXT,                          -- Workday draft application id
  last_step      INTEGER NOT NULL DEFAULT 0,    -- last page completed (read-back passed)
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  ready_at       TEXT,
  applied_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_apps_state ON applications(state);

CREATE TABLE IF NOT EXISTS app_events (
  id         INTEGER PRIMARY KEY,
  app_id     INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  ts         TEXT NOT NULL,                     -- UTC
  ts_local   TEXT NOT NULL,                     -- local with offset (day counting)
  event      TEXT NOT NULL,
  from_state TEXT,
  to_state   TEXT,
  detail     TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_app ON app_events(app_id, id);

CREATE TABLE IF NOT EXISTS fields (
  id         INTEGER PRIMARY KEY,
  app_id     INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
  page       TEXT NOT NULL DEFAULT '',
  name       TEXT NOT NULL,
  label      TEXT NOT NULL DEFAULT '',
  intended   TEXT,
  observed   TEXT,
  status     TEXT NOT NULL,                     -- filled | failed | left_for_human | skipped
  note       TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fields_app ON fields(app_id);

CREATE TABLE IF NOT EXISTS costs (
  id            INTEGER PRIMARY KEY,
  app_id        INTEGER REFERENCES applications(id) ON DELETE SET NULL,
  purpose       TEXT NOT NULL,                  -- answers | agent | other
  model         TEXT NOT NULL DEFAULT '',
  cost_usd      REAL NOT NULL DEFAULT 0,
  num_turns     INTEGER NOT NULL DEFAULT 0,
  session_id    TEXT,
  subtype       TEXT,
  duration_s    REAL NOT NULL DEFAULT 0,
  ts            TEXT NOT NULL,
  ts_local      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
  id            INTEGER PRIMARY KEY,
  ats           TEXT NOT NULL,
  tenant        TEXT NOT NULL,                  -- e.g. Workday tenant; the vault key is ats:tenant
  email         TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  verified_at   TEXT,
  last_login_at TEXT,
  note          TEXT,
  UNIQUE(ats, tenant)
);

CREATE TABLE IF NOT EXISTS answer_memory (
  id          INTEGER PRIMARY KEY,
  question    TEXT NOT NULL,                    -- normalized question text
  answer      TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'confirmed', -- confirmed (human reviewed) | inferred
  ats         TEXT,
  created_at  TEXT NOT NULL,
  UNIQUE(question, ats)
);
