-- CQA schema. Идемпотентно: CREATE ... IF NOT EXISTS. Версия схемы — PRAGMA user_version (cqa/db/conn.py).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS datasets (
  group_id      TEXT PRIMARY KEY,
  source        TEXT NOT NULL,               -- api | file | manual | autotest
  fetched_at    TEXT NOT NULL,
  total_records INTEGER NOT NULL DEFAULT 0,
  raw_path      TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
  uid                   TEXT PRIMARY KEY,    -- "{group_id}:{conversation_id}"
  group_id              TEXT NOT NULL REFERENCES datasets(group_id),
  external_id           TEXT NOT NULL,
  channel               TEXT NOT NULL,
  language              TEXT NOT NULL,
  content_hash          TEXT NOT NULL,
  normalizer_version    TEXT NOT NULL,
  facts_json            TEXT,
  operator_name         TEXT,
  audio_status          TEXT NOT NULL DEFAULT 'none',   -- none | pending | ready | failed
  audio_path            TEXT,
  audio_shift_sec       REAL NOT NULL DEFAULT 0,
  parent_uid            TEXT,                -- для автотестов
  mutation_id           TEXT,
  current_evaluation_id INTEGER,
  created_at            TEXT NOT NULL,
  UNIQUE (group_id, external_id)
);

-- Секреты (audio_url с sig=). Читает только cqa/ingest/audio.py. Никогда не отдаётся в API, журнал или экспорт.
CREATE TABLE IF NOT EXISTS conversation_secrets (
  uid        TEXT PRIMARY KEY REFERENCES conversations(uid) ON DELETE CASCADE,
  audio_url  TEXT,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  uid        TEXT NOT NULL REFERENCES conversations(uid) ON DELETE CASCADE,
  idx        INTEGER NOT NULL,
  mid        TEXT NOT NULL,
  speaker    TEXT NOT NULL,
  text       TEXT NOT NULL,
  ts_raw     TEXT NOT NULL,
  offset_sec REAL,
  audio_sec  REAL,
  segment    TEXT NOT NULL,
  recorded   INTEGER NOT NULL DEFAULT 1,
  after_end  INTEGER NOT NULL DEFAULT 0,
  event_type TEXT,
  dup_of     TEXT,
  PRIMARY KEY (uid, idx)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(uid UNINDEXED, mid UNINDEXED, text);

CREATE TABLE IF NOT EXISTS checklists (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  active_version_id INTEGER,
  frozen_version_id INTEGER
);

-- Версия неизменяема: каждое сохранение — новая строка.
CREATE TABLE IF NOT EXISTS checklist_versions (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  checklist_id         TEXT NOT NULL REFERENCES checklists(id),
  version              INTEGER NOT NULL,
  status               TEXT NOT NULL DEFAULT 'draft',  -- draft | published | frozen
  config_json          TEXT NOT NULL,
  config_hash          TEXT NOT NULL,
  criteria_hashes_json TEXT NOT NULL,
  parent_id            INTEGER,
  comment              TEXT,
  created_at           TEXT NOT NULL,
  UNIQUE (checklist_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  kind                 TEXT NOT NULL,       -- batch | single | dry_run | gold | autotest | cross_check
  checklist_version_id INTEGER NOT NULL REFERENCES checklist_versions(id),
  judge_profile        TEXT NOT NULL,
  model                TEXT NOT NULL,
  prompt_version       TEXT NOT NULL,
  params_json          TEXT NOT NULL DEFAULT '{}',
  status               TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed | cancelled
  units_total          INTEGER NOT NULL DEFAULT 0,
  units_done           INTEGER NOT NULL DEFAULT 0,
  units_failed         INTEGER NOT NULL DEFAULT 0,
  units_cached         INTEGER NOT NULL DEFAULT 0,
  cost_usd             REAL NOT NULL DEFAULT 0,
  metrics_json         TEXT,
  created_at           TEXT NOT NULL,
  started_at           TEXT,
  finished_at          TEXT
);

CREATE TABLE IF NOT EXISTS evaluations (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id               INTEGER REFERENCES runs(id),
  uid                  TEXT NOT NULL REFERENCES conversations(uid),
  checklist_version_id INTEGER NOT NULL REFERENCES checklist_versions(id),
  profile              TEXT NOT NULL,
  model                TEXT NOT NULL,
  prompt_version       TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | partial | failed
  total                REAL,                -- исходный итог (NULL — не рассчитывается)
  score_json           TEXT,                -- ScoreResult исходной оценки
  gross                INTEGER NOT NULL DEFAULT 0,
  gross_json           TEXT,
  needs_review         INTEGER NOT NULL DEFAULT 0,
  final_total          REAL,                -- итог с учётом решений по апелляциям
  final_score_json     TEXT,
  cost_usd             REAL NOT NULL DEFAULT 0,
  latency_ms           INTEGER NOT NULL DEFAULT 0,
  created_at           TEXT NOT NULL,
  finished_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_evaluations_uid ON evaluations(uid);

-- После завершения строку не меняем: решения по апелляциям — в decisions.
CREATE TABLE IF NOT EXISTS criterion_results (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  evaluation_id      INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
  criterion_id       TEXT NOT NULL,
  criterion_hash     TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
  verdict            TEXT,
  category           TEXT,
  points             REAL,
  weight             REAL,
  applicable         INTEGER,
  evidence_json      TEXT,
  checked_range_json TEXT,
  reasoning          TEXT,
  explanation        TEXT,
  how_to_fix         TEXT,
  validation_json    TEXT,
  attempts           INTEGER NOT NULL DEFAULT 0,
  needs_review       INTEGER NOT NULL DEFAULT 0,
  second_json        TEXT,                -- перекрёстная проверка
  llm_call_id        INTEGER,
  cache_hit          INTEGER NOT NULL DEFAULT 0,
  error              TEXT,
  UNIQUE (evaluation_id, criterion_id)
);

CREATE TABLE IF NOT EXISTS llm_cache (
  key           TEXT PRIMARY KEY,            -- sha256 канонического запроса
  profile       TEXT NOT NULL,
  model         TEXT NOT NULL,
  response_text TEXT NOT NULL,
  parsed_json   TEXT,
  usage_json    TEXT,
  cost_usd      REAL,
  created_at    TEXT NOT NULL,
  hits          INTEGER NOT NULL DEFAULT 0
);

-- Журнал вызовов: прозрачность процесса («система реально анализирует»).
CREATE TABLE IF NOT EXISTS llm_calls (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                  TEXT NOT NULL,
  purpose             TEXT NOT NULL,
  run_id              INTEGER,
  evaluation_id       INTEGER,
  criterion_result_id INTEGER,
  uid                 TEXT,
  criterion_id        TEXT,
  profile             TEXT NOT NULL,
  provider            TEXT NOT NULL,
  model               TEXT NOT NULL,
  cache_key           TEXT,
  cache_hit           INTEGER NOT NULL DEFAULT 0,
  request_json        TEXT NOT NULL,
  response_text       TEXT,
  status              TEXT NOT NULL,       -- ok | http_error | invalid_json | refused | truncated | validation_failed
  error               TEXT,
  finish_reason       TEXT,
  prompt_tokens       INTEGER NOT NULL DEFAULT 0,
  cached_tokens       INTEGER NOT NULL DEFAULT 0,
  completion_tokens   INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
  cost_usd            REAL,
  latency_ms          INTEGER NOT NULL DEFAULT 0,
  attempt             INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_eval ON llm_calls(evaluation_id);

CREATE TABLE IF NOT EXISTS appeals (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  evaluation_id         INTEGER NOT NULL REFERENCES evaluations(id),
  criterion_result_id   INTEGER NOT NULL REFERENCES criterion_results(id),
  criterion_id          TEXT NOT NULL,
  comment               TEXT NOT NULL,
  counter_evidence_json TEXT,              -- ["m12", ...]
  author_role           TEXT NOT NULL DEFAULT 'operator',
  status                TEXT NOT NULL DEFAULT 'open',   -- open | decided
  ai_opinion_json       TEXT,
  ai_call_id            INTEGER,
  created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  appeal_id       INTEGER NOT NULL UNIQUE REFERENCES appeals(id),
  action          TEXT NOT NULL,           -- confirm | change
  new_verdict     TEXT,
  reason          TEXT NOT NULL,
  reviewer        TEXT NOT NULL DEFAULT 'reviewer',
  prev_final_total REAL,
  new_final_total  REAL,
  make_precedent  INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gold_labels (
  external_id  TEXT NOT NULL,
  criterion_id TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'gold',
  options      TEXT NOT NULL,              -- 'Да|Нет'
  family       TEXT,
  PRIMARY KEY (external_id, criterion_id, source)
);

CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);

-- P1
CREATE TABLE IF NOT EXISTS signals (
  evaluation_id  INTEGER PRIMARY KEY REFERENCES evaluations(id) ON DELETE CASCADE,
  contact_reason TEXT,
  resolved       INTEGER,
  unanswered_json TEXT,                    -- [{text, mid}]
  churn_risk     INTEGER,
  missed_opportunity TEXT,
  llm_call_id    INTEGER
);

CREATE TABLE IF NOT EXISTS compliance_cases (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  criterion_result_id INTEGER NOT NULL REFERENCES criterion_results(id),
  status              TEXT NOT NULL DEFAULT 'open',
  note                TEXT,
  created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS autotests (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  base_uid    TEXT NOT NULL,
  uid         TEXT NOT NULL UNIQUE,
  mutation_id TEXT NOT NULL,
  expect_json TEXT NOT NULL,
  last_run_id INTEGER,
  passed      INTEGER
);

-- P2
CREATE TABLE IF NOT EXISTS precedents (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  appeal_id            INTEGER REFERENCES appeals(id),
  criterion_id         TEXT NOT NULL,
  fragment             TEXT NOT NULL,
  verdict              TEXT NOT NULL,
  why                  TEXT NOT NULL,
  added_in_version_id  INTEGER,
  created_at           TEXT NOT NULL
);

-- P3
CREATE TABLE IF NOT EXISTS trainer_sessions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  base_uid      TEXT NOT NULL,
  persona_json  TEXT NOT NULL,
  transcript_json TEXT NOT NULL DEFAULT '[]',
  status        TEXT NOT NULL DEFAULT 'active',  -- active | finished
  result_evaluation_id INTEGER,
  created_at    TEXT NOT NULL
);
