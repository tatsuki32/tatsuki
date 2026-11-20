-- Rhizome Box tables (Cloudflare D1). Safe to run again: everything is IF NOT EXISTS.
-- npx wrangler d1 execute rhizomebox --remote --file schema.sql

-- One row per page. lines = the page as a JSON array of strings (line 0 is the title).
-- links, image, images, web, descr, css are worked out from lines on every save (notation.js derive).
CREATE TABLE IF NOT EXISTS pages (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  lines TEXT NOT NULL,
  pub INTEGER NOT NULL DEFAULT 0,
  pin INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  rev INTEGER NOT NULL DEFAULT 1,
  deleted INTEGER NOT NULL DEFAULT 0,
  links TEXT NOT NULL DEFAULT '[]',
  image TEXT NOT NULL DEFAULT '',
  images TEXT NOT NULL DEFAULT '[]',
  web TEXT NOT NULL DEFAULT '[]',
  descr TEXT NOT NULL DEFAULT '',
  css TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS pages_title ON pages (title COLLATE NOCASE) WHERE deleted = 0;
CREATE INDEX IF NOT EXISTS pages_pub ON pages (pub, deleted);

-- Earlier versions of a page: at most one every 5 minutes, the newest 100 kept.
CREATE TABLE IF NOT EXISTS versions (
  page TEXT NOT NULL,
  rev INTEGER NOT NULL,
  lines TEXT NOT NULL,
  at INTEGER NOT NULL,
  PRIMARY KEY (page, rev)
);

-- Sign-in: a 6-digit code mailed to the owner, then a session cookie. Only hashes are stored.
CREATE TABLE IF NOT EXISTS codes (
  hash TEXT PRIMARY KEY,
  expires INTEGER NOT NULL,
  tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  hash TEXT PRIMARY KEY,
  created INTEGER NOT NULL,
  expires INTEGER NOT NULL
);
-- How many codes were asked for, to slow down guessing and mail floods.
CREATE TABLE IF NOT EXISTS throttle (
  key TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS throttle_key ON throttle (key, at);

-- Titles and thumbnails of outside links, fetched once and kept 30 days.
CREATE TABLE IF NOT EXISTS linkmeta (
  url TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  image TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '',
  fetched INTEGER NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1
);

-- Pictures pasted into pages (the files themselves are in R2).
CREATE TABLE IF NOT EXISTS files (
  name TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  size INTEGER NOT NULL,
  created INTEGER NOT NULL
);
