-- Phone-bench results: one row per (device run, size, engine). `data` is the page's run object as JSON.
CREATE TABLE IF NOT EXISTS bench_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  session TEXT NOT NULL,
  device TEXT NOT NULL,
  size TEXT NOT NULL,
  engine TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bench_runs_session ON bench_runs (session);
