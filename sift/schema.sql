-- Sift Daily (sift.tany.im). Apply: npx wrangler d1 execute sift --remote --file schema.sql
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT,
  name TEXT,
  role TEXT NOT NULL DEFAULT 'user',      -- owner | user
  plan TEXT NOT NULL DEFAULT 'free',      -- free | pro (see PLANS in src/plans.js)
  tz TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
  mail INTEGER NOT NULL DEFAULT 1,        -- 0 = no daily email
  createdAt INTEGER NOT NULL,
  lastSeen INTEGER
);

CREATE TABLE IF NOT EXISTS feeds (
  id TEXT PRIMARY KEY,
  userId TEXT NOT NULL,
  url TEXT NOT NULL,                      -- the RSS or Atom address
  site TEXT,
  name TEXT NOT NULL,
  folder TEXT NOT NULL DEFAULT 'General',
  active INTEGER NOT NULL DEFAULT 1,      -- 0 = unsubscribed (kept so it can come back)
  addedAt INTEGER NOT NULL,
  lastTry INTEGER,
  lastOk INTEGER,
  lastError TEXT,
  lastCount INTEGER,
  UNIQUE (userId, url)
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  userId TEXT NOT NULL,
  feedId TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  descr TEXT,
  published INTEGER,
  fetched INTEGER NOT NULL,
  day TEXT NOT NULL,                      -- the user's local date it arrived (YYYY-MM-DD)
  line TEXT,                              -- the one-line summary (Pro)
  mark TEXT,                              -- read | later | task
  markedAt INTEGER,
  taskSent INTEGER,                       -- when the Desk on the Mac took it as a task
  mailed INTEGER,                         -- when it went out in a daily email
  UNIQUE (userId, url)
);
CREATE INDEX IF NOT EXISTS items_day ON items (userId, day);
CREATE INDEX IF NOT EXISTS items_mark ON items (userId, mark);

CREATE TABLE IF NOT EXISTS digests (
  userId TEXT NOT NULL,
  day TEXT NOT NULL,
  summary TEXT,
  count INTEGER,
  sentAt INTEGER,
  PRIMARY KEY (userId, day)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at INTEGER NOT NULL,
  kind TEXT NOT NULL,                     -- fetch | summary | mail
  userId TEXT,
  ok INTEGER NOT NULL,
  detail TEXT,
  tin INTEGER DEFAULT 0,                  -- AI tokens in / out (for the cost line on /admin)
  tout INTEGER DEFAULT 0
);
