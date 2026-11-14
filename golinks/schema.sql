-- go.tany.im. No IP address and no cookie is ever stored.
CREATE TABLE IF NOT EXISTS links (
  code TEXT PRIMARY KEY,        -- the part after go.tany.im/
  dest TEXT NOT NULL,           -- where it goes, with the UTM already added
  original TEXT NOT NULL,       -- the link as the person gave it
  source TEXT NOT NULL DEFAULT '', -- instagram, threads, x, facebook, linkedin, note, site, reddit, other
  campaign TEXT NOT NULL DEFAULT '', -- the Post Studio post id, or a word the person chose
  label TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL,
  off INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clicks (
  code TEXT NOT NULL,
  at INTEGER NOT NULL,          -- milliseconds
  country TEXT NOT NULL DEFAULT '',
  referrer TEXT NOT NULL DEFAULT '', -- the host only
  device TEXT NOT NULL DEFAULT '',   -- mobile, tablet, desktop
  bot INTEGER NOT NULL DEFAULT 0     -- link previews and crawlers, kept apart from people
);
CREATE INDEX IF NOT EXISTS clicks_code_at ON clicks (code, at);
CREATE INDEX IF NOT EXISTS links_created ON links (created);
CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, provider TEXT NOT NULL, created INTEGER NOT NULL);
-- connected social accounts. token is AES-GCM encrypted with the TOKEN_KEY secret; Desk never receives it
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,          -- provider:account
  provider TEXT NOT NULL, account TEXT NOT NULL, handle TEXT NOT NULL DEFAULT '',
  token TEXT NOT NULL, expires INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL, problem TEXT NOT NULL DEFAULT ''
);
-- approved posts waiting for their time. text is exactly what the person approved
CREATE TABLE IF NOT EXISTS queue (
  id TEXT PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', media TEXT NOT NULL DEFAULT '[]',
  at INTEGER NOT NULL, status TEXT NOT NULL, tries INTEGER NOT NULL DEFAULT 0, link TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL, done INTEGER, post TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS queue_due ON queue (status, at);
