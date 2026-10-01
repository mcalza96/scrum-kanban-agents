-- trace.db: derived, rebuildable. Source of truth = session files + events.jsonl.

CREATE TABLE session (
  id TEXT PRIMARY KEY,
  project TEXT, source TEXT, title TEXT, model TEXT, parent_id TEXT,
  start_ms INTEGER, end_ms INTEGER, wall_min REAL,
  prompts INTEGER,            -- user prompts in the session; one-shot means 1
  ingested_at TEXT
);

-- A task = one user prompt and everything the agent did until the next prompt.
CREATE TABLE task (
  session_id TEXT, n INTEGER,
  project TEXT, agent TEXT, ticket TEXT, kind TEXT,   -- kind: work | return | other
  prompt_head TEXT,
  start_ms INTEGER, end_ms INTEGER,
  wall_min REAL, tool_min REAL, other_min REAL,       -- other = model + waiting
  steps INTEGER, calls INTEGER,
  reads INTEGER, edits INTEGER, shell INTEGER,
  suite_runs INTEGER, suite_min REAL,
  rereads INTEGER,            -- extra reads of a file already read in this task
  failed INTEGER,             -- tool calls that ended in error
  longest_call_min REAL, longest_gap_min REAL,
  tokens_in INTEGER, tokens_out INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER,
  cost REAL,
  PRIMARY KEY (session_id, n)
);

CREATE TABLE step (
  session_id TEXT, task_n INTEGER, n INTEGER,
  start_ms INTEGER, end_ms INTEGER, dur_s REAL, gap_s REAL,
  tool TEXT, summary TEXT, ok INTEGER, is_suite INTEGER,
  PRIMARY KEY (session_id, task_n, n)
);

-- Facts only humans/PM know, from events.jsonl.
CREATE TABLE verdict (
  project TEXT, ticket TEXT, round INTEGER, verdict TEXT, cause TEXT,
  session_id TEXT, at TEXT, note TEXT
);
CREATE TABLE log (            -- the agent's own narrative ("logbook")
  project TEXT, session_id TEXT, task_n INTEGER, ticket TEXT, at TEXT, text TEXT
);

-- Views -------------------------------------------------------------------

CREATE VIEW v_agent AS
SELECT project, agent, kind, COUNT(*) tasks,
       ROUND(AVG(wall_min),1) avg_min, ROUND(SUM(wall_min),0) total_min,
       ROUND(AVG(suite_runs),1) avg_suites, ROUND(AVG(rereads),1) avg_rereads,
       SUM(failed) failed, ROUND(SUM(cost),2) cost
FROM task GROUP BY project, agent, kind;

CREATE VIEW v_ticket AS
SELECT project, ticket,
       SUM(kind='work') work_tasks, SUM(kind='return') return_tasks,
       ROUND(SUM(CASE WHEN kind='work' THEN wall_min END),0) work_min,
       ROUND(SUM(CASE WHEN kind='return' THEN wall_min END),0) return_min,
       ROUND(SUM(wall_min),0) total_min, SUM(suite_runs) suites,
       MIN(datetime(start_ms/1000,'unixepoch','localtime')) first_seen
FROM task WHERE ticket IS NOT NULL GROUP BY project, ticket;

CREATE VIEW v_day AS
SELECT project, date(start_ms/1000,'unixepoch','localtime') day, COUNT(*) tasks,
       ROUND(AVG(wall_min),1) avg_min, ROUND(AVG(suite_runs),1) avg_suites,
       SUM(kind='return') returns, ROUND(SUM(cost),2) cost
FROM task GROUP BY project, day;

CREATE VIEW v_one_shot AS
SELECT project, date(start_ms/1000,'unixepoch','localtime') day, COUNT(*) sessions,
       SUM(prompts=1) one_shot, SUM(prompts>1) multi_task, MAX(prompts) max_prompts
FROM session WHERE parent_id IS NULL GROUP BY project, day;

CREATE VIEW v_return_cause AS
SELECT project, cause, COUNT(*) returns FROM verdict
WHERE verdict='returned' GROUP BY project, cause;

CREATE VIEW v_outliers AS        -- things worth a look
SELECT project, session_id, n, agent, ticket, wall_min, suite_runs, rereads, failed,
       longest_call_min, longest_gap_min,
       TRIM(CASE WHEN longest_call_min >= 5 THEN 'long-call ' ELSE '' END ||
            CASE WHEN suite_runs >= 10 THEN 'many-suites ' ELSE '' END ||
            CASE WHEN rereads >= 15 THEN 'many-rereads ' ELSE '' END ||
            CASE WHEN wall_min >= 60 THEN 'long-task ' ELSE '' END ||
            CASE WHEN failed >= 5 THEN 'many-failures' ELSE '' END) flags
FROM task
WHERE longest_call_min >= 5 OR suite_runs >= 10 OR rereads >= 15 OR wall_min >= 60 OR failed >= 5;
