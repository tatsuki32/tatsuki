// Rhizome Box server: every request comes here first (static files are served from public/ through ASSETS).
// Privacy rule: anything a visitor can receive is read through visibleSql(owner), so a private page,
// its title, its images and its links never leave the server unless the owner is signed in.

import { derive, renderStatic, esc, filesOf, kindOfFile, resolveFile, FILE_PATH } from '../public/app/notation.js';
import { pageHtml, homeHtml, shellHtml, notFoundHtml } from './html.js';

const DAY = 864e5, MIN = 6e4;
const SESSION_DAYS = 90;
const CODE_MINUTES = 10;
const VERSION_GAP = 5 * MIN;
const KEEP_VERSIONS = 100;
const MAX_LINES = 5000, MAX_LINE = 10000, MAX_BYTES = 1_000_000;
const MAX_PICTURE = 10 * 1024 * 1024; // the older one-request picture upload
const MAX_FILE = 500e6;               // one file
export const CAP = 9e9;               // everything together: 9 GB, inside R2's free 10 GB (Post Studio's videos share it)
const PART = 16 * 1024 * 1024;        // big files arrive in pieces (R2 wants equal pieces of 5 MiB or more; a request may be 100 MB)
const UPLOAD_HOURS = 6;               // an upload that never finished is dropped after this
const KEEP_FILES = ['avatar.jpg'];    // used by the site itself
const META_DAYS = 30;
export const ME = 'tatsuki';

const now = () => Date.now();
const enc = new TextEncoder();
export async function sha256(s) {
  const d = await crypto.subtle.digest('SHA-256', enc.encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
const randHex = (n) => [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, '0')).join('');
const safeEq = (a, b) => { a = String(a); b = String(b); if (a.length !== b.length) return false; let r = 0; for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i); return r === 0; };
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...headers } });
const html = (body, status = 200, headers = {}) => new Response(body, { status, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin', ...headers } });
const parseJSON = (s, d) => { try { return JSON.parse(s); } catch (e) { return d; } };
export const pageUrl = (title) => '/' + encodeURIComponent(title).replace(/%20/g, '_');
const decodePath = (p) => { try { return decodeURIComponent(p.slice(1)); } catch (e) { return p.slice(1); } };
const likeEsc = (s) => s.replace(/[\\%_]/g, (c) => '\\' + c);

/* ---------- who is asking ---------- */
function cookie(req, name) {
  const m = (req.headers.get('Cookie') || '').match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
  return m ? m[1] : '';
}
async function isOwner(req, env) {
  const tok = cookie(req, 'rb');
  if (!tok || !/^[0-9a-f]{64}$/.test(tok)) return false;
  const row = await env.DB.prepare('SELECT expires FROM sessions WHERE hash = ?').bind(await sha256(tok)).first();
  return !!row && row.expires > now();
}
const wordsKeyOk = (req, env) => !!env.WORDS_KEY && safeEq((req.headers.get('Authorization') || '').replace(/^Bearer\s+/, ''), env.WORDS_KEY);
/* The Desk's word lists: it may write only pages titled "<Language> words", and only below this line. */
export const WORDS_MARK = 'The Desk adds the words below this line by itself.';
const WORDS_TITLE = /^[A-Z][A-Za-z' ()-]{1,40} words$/;
async function saveWords(env, ctx, body) {
  const title = String(body.title || '').trim();
  if (!WORDS_TITLE.test(title)) return json({ error: 'title' }, 400);
  const add = body.lines;
  if (!Array.isArray(add) || add.some((l) => typeof l !== 'string')) return json({ error: 'lines' }, 400);
  const head = Array.isArray(body.head) ? body.head.filter((l) => typeof l === 'string').slice(0, 20) : [];
  const old = await env.DB.prepare('SELECT * FROM pages WHERE title = ? COLLATE NOCASE AND deleted = 0').bind(title).first();
  let lines, id;
  if (old) {
    id = old.id;
    const cur = parseJSON(old.lines, []);
    const at = cur.indexOf(WORDS_MARK);
    lines = [...(at >= 0 ? cur.slice(0, at) : [...cur, '']), WORDS_MARK, ...add];  // the owner's own lines above the mark stay as they are
  } else {
    id = randHex(12);
    lines = [title, ...head, '', WORDS_MARK, ...add];
  }
  // setPub: the Desk turns a language's page public (on) or private (off). Otherwise the page keeps what it is.
  const pub = typeof body.setPub === 'boolean' ? (body.setPub ? 1 : 0) : (old ? null : 0);
  return savePage(env, ctx, id, { lines, pub });
}
const bearerOk = (req, env) => !!env.BACKUP_KEY && safeEq((req.headers.get('Authorization') || '').replace(/^Bearer\s+/, ''), env.BACKUP_KEY);

/* ---------- pages ---------- */
const visibleSql = (owner) => (owner ? 'deleted = 0' : 'deleted = 0 AND pub = 1');
export function entry(r, withLines = false) {
  const e = {
    id: r.id, title: r.title, pub: !!r.pub, pin: r.pin || 0, updated: r.updated, rev: r.rev,
    links: parseJSON(r.links, []), image: r.image || '', images: parseJSON(r.images, []), web: parseJSON(r.web, []), descr: r.descr || '',
  };
  if (r.css) e.css = r.css;
  if (withLines) e.lines = parseJSON(r.lines, []);
  return e;
}
async function listPages(env, owner) {
  const { results } = await env.DB.prepare(`SELECT id, title, pub, pin, updated, rev, links, image, images, web, descr, css FROM pages WHERE ${visibleSql(owner)} ORDER BY updated DESC`).all();
  return results.map((r) => entry(r));
}
async function pageById(env, id, owner) {
  return env.DB.prepare(`SELECT * FROM pages WHERE id = ? AND ${visibleSql(owner)}`).bind(id).first();
}
async function pageByTitle(env, title, owner) {
  return env.DB.prepare(`SELECT * FROM pages WHERE title = ? COLLATE NOCASE AND ${visibleSql(owner)}`).bind(title).first();
}
async function pagesByTitles(env, titles, owner) {
  const out = new Map();
  const list = [...new Set(titles.map((t) => t.toLowerCase()))];
  for (let i = 0; i < list.length; i += 80) {
    const part = list.slice(i, i + 80);
    const { results } = await env.DB.prepare(`SELECT id, title, pub, image FROM pages WHERE ${visibleSql(owner)} AND lower(title) IN (${part.map(() => '?').join(',')})`).bind(...part).all();
    for (const r of results) out.set(r.title.toLowerCase(), r);
  }
  return out;
}

function checkLines(lines) {
  if (!Array.isArray(lines) || !lines.length || lines.length > MAX_LINES) return 'lines';
  for (const l of lines) if (typeof l !== 'string' || l.length > MAX_LINE) return 'lines';
  if (JSON.stringify(lines).length > MAX_BYTES) return 'too_big';
  const t = lines[0].trim();
  if (!t) return 'no_title';
  if (t.length > 200) return 'title_long';
  if (t.startsWith('_/') || /[\u0000-\u001f]/.test(t)) return 'title_bad';
  return '';
}

/* When a title changes, links to the old title in other pages follow it (as in Scrapbox). */
function renameIn(lines, oldT, newT) {
  const q = oldT.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const reBr = new RegExp('\\[' + q + '(\\.icon(?:\\*\\d+)?)?\\]', 'gi');
  const reTag = new RegExp('(^|\\s)#' + q + '(?=$|\\s)', 'gi');
  const tagNew = /\s/.test(newT) ? null : newT;
  return lines.map((l, i) => (i === 0 ? l : l.replace(reBr, (m, ic) => '[' + newT + (ic || '') + ']').replace(reTag, (m, pre) => (tagNew ? pre + '#' + tagNew : pre + '[' + newT + ']'))));
}

async function savePage(env, ctx, id, body) {
  const lines = body.lines;
  const bad = checkLines(lines);
  if (bad) return json({ error: bad }, 400);
  const title = lines[0].trim();
  const clash = await env.DB.prepare('SELECT id FROM pages WHERE title = ? COLLATE NOCASE AND deleted = 0 AND id != ?').bind(title, id).first();
  if (clash) return json({ error: 'title_taken', id: clash.id }, 409);
  const old = await env.DB.prepare('SELECT * FROM pages WHERE id = ?').bind(id).first();
  if (old && !old.deleted && body.baseRev != null && Number(body.baseRev) !== old.rev) {
    return json({ error: 'conflict', page: entry(old, true) }, 409);
  }
  const d = derive(lines);
  const t = now();
  const pub = body.pub == null ? (old ? old.pub : 0) : (body.pub ? 1 : 0);
  const pin = body.pin == null ? (old ? old.pin : 0) : Math.max(0, Number(body.pin) || 0);
  const stmts = [];
  if (old && !old.deleted) {
    const last = await env.DB.prepare('SELECT MAX(at) AS at FROM versions WHERE page = ?').bind(id).first();
    if (!last || !last.at || t - last.at > VERSION_GAP) {
      stmts.push(env.DB.prepare('INSERT OR REPLACE INTO versions (page, rev, lines, at) VALUES (?, ?, ?, ?)').bind(id, old.rev, old.lines, t));
      stmts.push(env.DB.prepare(`DELETE FROM versions WHERE page = ? AND rev NOT IN (SELECT rev FROM versions WHERE page = ? ORDER BY rev DESC LIMIT ${KEEP_VERSIONS})`).bind(id, id));
    }
  }
  const rev = old ? old.rev + 1 : 1;
  stmts.push(env.DB.prepare(`INSERT INTO pages (id, title, lines, pub, pin, created, updated, rev, deleted, links, image, images, web, descr, css)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET title = excluded.title, lines = excluded.lines, pub = excluded.pub, pin = excluded.pin, updated = excluded.updated,
      rev = excluded.rev, deleted = 0, links = excluded.links, image = excluded.image, images = excluded.images, web = excluded.web, descr = excluded.descr, css = excluded.css`)
    .bind(id, title, JSON.stringify(lines), pub, pin, old ? old.created : t, t, rev, JSON.stringify(d.links), d.image, JSON.stringify(d.images), JSON.stringify(d.web), d.descr, d.css));
  const renamed = [];
  if (old && !old.deleted && old.title.toLowerCase() !== title.toLowerCase()) {
    const { results } = await env.DB.prepare("SELECT * FROM pages WHERE deleted = 0 AND id != ? AND lower(links) LIKE ? ESCAPE '\\'").bind(id, '%' + likeEsc(JSON.stringify(old.title.toLowerCase()).slice(1, -1)) + '%').all();
    for (const r of results) {
      const nl = renameIn(parseJSON(r.lines, []), old.title, title);
      if (JSON.stringify(nl) === r.lines) continue;
      const dd = derive(nl);
      stmts.push(env.DB.prepare('UPDATE pages SET lines = ?, links = ?, image = ?, images = ?, web = ?, descr = ?, css = ?, rev = rev + 1, updated = ? WHERE id = ?')
        .bind(JSON.stringify(nl), JSON.stringify(dd.links), dd.image, JSON.stringify(dd.images), JSON.stringify(dd.web), dd.descr, dd.css, t, r.id));
      renamed.push(r.id);
    }
  }
  await env.DB.batch(stmts);
  const row = await env.DB.prepare('SELECT * FROM pages WHERE id = ?').bind(id).first();
  if (d.web.length && ctx && ctx.waitUntil) ctx.waitUntil(fillMeta(env, d.web.map((w) => w.url)).catch(() => {}));
  return json({ page: entry(row), renamed });
}

/* ---------- sign-in ---------- */
/* One statement counts and answers, so many requests at once cannot slip past. True = over the limit. */
async function over(env, key, limit, windowMs) {
  const t = now();
  const r = await env.DB.prepare(`INSERT INTO limits (key, start, n) VALUES (?, ?, 1) ON CONFLICT(key) DO UPDATE SET
    n = CASE WHEN start <= ? THEN 1 ELSE n + 1 END, start = CASE WHEN start <= ? THEN excluded.start ELSE start END RETURNING n`)
    .bind(key, t, t - windowMs, t - windowMs).first();
  return r.n > limit;
}
const sweep = (env, ctx) => { if (Math.random() < 0.05) ctx.waitUntil(env.DB.prepare('DELETE FROM limits WHERE start < ?').bind(now() - DAY).run().catch(() => {})); };
/* An IPv6 visitor owns a whole /64, so that is what counts as one visitor. */
export function visitorNet(ip) {
  ip = String(ip || '').toLowerCase();
  const v4 = ip.match(/(\d+\.\d+\.\d+\.\d+)$/);
  if (v4) return v4[1];
  if (!ip.includes(':')) return ip;
  const [h, t] = ip.split('::'), head = h ? h.split(':') : [], tail = t ? t.split(':') : [];
  const all = t === undefined ? head : [...head, ...Array(Math.max(0, 8 - head.length - tail.length)).fill('0'), ...tail];
  return all.slice(0, 4).map((x) => (parseInt(x, 16) || 0).toString(16)).join(':') + '::/64';
}
async function hmac(env, s) {
  const k = await crypto.subtle.importKey('raw', enc.encode('rb-signin:' + env.BACKUP_KEY), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return [...new Uint8Array(await crypto.subtle.sign('HMAC', k, enc.encode(s)))].map((b) => b.toString(16).padStart(2, '0')).join('');
}
const visitorKey = async (req, env) => (await hmac(env, 'net:' + visitorNet(req.headers.get('CF-Connecting-IP')))).slice(0, 32);
/* A browser that has signed in before carries a signed mark. It opens nothing; it only keeps a share of the day's
   codes for the owner, so strangers who know the address cannot use them all up. */
async function knownDevice(req, env) {
  const m = cookie(req, 'rbd').match(/^([0-9a-f]{32})\.([0-9a-f]{64})$/);
  return !!m && !!env.BACKUP_KEY && safeEq(m[2], await hmac(env, 'device:' + m[1]));
}
async function deviceCookie(env) {
  const id = randHex(16);
  return `rbd=${id}.${await hmac(env, 'device:' + id)}; Path=/_/api/; HttpOnly; Secure; SameSite=Lax; Max-Age=${400 * 86400}`;
}
/* The code page is in plain sight, so strangers will try it. A code works only in the browser that asked for it:
   nobody else can use it or burn it. */
const askCookie = (v, age) => `rbq=${v}; Path=/_/api/; HttpOnly; Secure; SameSite=Strict; Max-Age=${age}`;
const CODES_A_DAY = 30, CODES_A_DAY_KNOWN = 20, TRIES = 5;
async function login(req, env, ctx, deps) {
  const b = await req.json().catch(() => ({}));
  const email = String(b.email || '').trim().toLowerCase().slice(0, 254);
  sweep(env, ctx);
  if (await over(env, 'ask-' + await visitorKey(req, env), 5, 10 * MIN)) return json({ error: 'slow_down' }, 429);
  const ask = randHex(32);
  // The answer never depends on the address: not its text, its limit or the time it takes.
  if (env.OWNER_EMAIL && email && email === String(env.OWNER_EMAIL).trim().toLowerCase()) ctx.waitUntil(sendCode(req, env, deps, ask).catch((e) => console.error('sign-in code', e && e.message)));
  return json({ ok: true }, 200, { 'Set-Cookie': askCookie(ask, CODE_MINUTES * 60) });
}
async function sendCode(req, env, deps, ask) {
  const known = await knownDevice(req, env);
  if (await over(env, known ? 'codes-known' : 'codes-new', known ? CODES_A_DAY_KNOWN : CODES_A_DAY, DAY)) return;
  const n = crypto.getRandomValues(new Uint32Array(1))[0] % 1e6;
  const code = String(n).padStart(6, '0');
  await env.DB.prepare('DELETE FROM codes WHERE expires < ?').bind(now()).run();
  await env.DB.prepare('INSERT INTO codes (hash, expires) VALUES (?, ?)').bind(await sha256('ask:' + ask) + ':' + await sha256('code:' + ask + ':' + code), now() + CODE_MINUTES * MIN).run();
  await deps.sendMail(env, {
    to: env.OWNER_EMAIL,
    subject: `Rhizome Box sign-in code: ${code}`,
    text: `Your sign-in code is ${code}\n\nIt works for ${CODE_MINUTES} minutes, once, in the browser that asked for it.\nIf you did not ask for it, you can ignore this email.`,
  });
}
async function verify(req, env, ctx) {
  const b = await req.json().catch(() => ({}));
  const code = String(b.code || '').replace(/\D/g, '');
  sweep(env, ctx);
  if (await over(env, 'try-' + await visitorKey(req, env), 10, 10 * MIN)) return json({ error: 'slow_down' }, 429);
  const ask = cookie(req, 'rbq');
  if (!/^[0-9a-f]{64}$/.test(ask)) return json({ error: 'no_code' }, 400);
  const askHash = await sha256('ask:' + ask);
  // A try is spent before the code is looked at, so tries sent at the same moment all count.
  const row = await env.DB.prepare(`UPDATE codes SET tries = tries + 1 WHERE hash >= ? AND hash < ? AND tries < ${TRIES} AND expires > ? RETURNING hash`).bind(askHash + ':', askHash + ';', now()).first();
  // A stranger who asked for a code has no row; the answer is the same as for a wrong code.
  if (!row || code.length !== 6 || !safeEq(askHash + ':' + await sha256('code:' + ask + ':' + code), row.hash)) return json({ error: 'wrong_code' }, 400);
  const used = await env.DB.prepare('DELETE FROM codes WHERE hash = ? RETURNING hash').bind(row.hash).first();
  if (!used) return json({ error: 'wrong_code' }, 400);
  await env.DB.prepare('DELETE FROM codes').run();
  const tok = randHex(32);
  await env.DB.prepare('DELETE FROM sessions WHERE expires < ?').bind(now()).run();
  await env.DB.prepare('INSERT INTO sessions (hash, created, expires) VALUES (?, ?, ?)').bind(await sha256(tok), now(), now() + SESSION_DAYS * DAY).run();
  const res = json({ ok: true }, 200, { 'Set-Cookie': `rb=${tok}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}` });
  res.headers.append('Set-Cookie', askCookie('', 0));
  if (env.BACKUP_KEY) res.headers.append('Set-Cookie', await deviceCookie(env));
  return res;
}
async function logout(req, env) {
  const tok = cookie(req, 'rb');
  if (tok) await env.DB.prepare('DELETE FROM sessions WHERE hash = ?').bind(await sha256(tok)).run();
  return json({ ok: true }, 200, { 'Set-Cookie': 'rb=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0' });
}

/* ---------- files ---------- */
// The type comes from the ending, never from what the sender claims. Types missing here are served as a download.
const TYPES = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', avif: 'image/avif', pdf: 'application/pdf',
  mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm',
  mp3: 'audio/mpeg', m4a: 'audio/mp4', aac: 'audio/aac', wav: 'audio/wav', ogg: 'audio/ogg', oga: 'audio/ogg', flac: 'audio/flac',
};
const extOf = (n) => (String(n).match(/\.([A-Za-z0-9]{1,8})$/) || ['', 'bin'])[1].toLowerCase();
const typeOf = (n) => TYPES[extOf(n)] || 'application/octet-stream';
const newName = (ext) => `${now().toString(36)}${randHex(4)}.${ext}`;
const cleanName = (n) => String(n || '').replace(/[\u0000-\u001f\u007f]/g, '').split(/[\\/]/).pop().trim().slice(0, 150);
const SUMS = '(SELECT COALESCE(SUM(size), 0) FROM files) + (SELECT COALESCE(SUM(size), 0) FROM uploads)';

async function upload(req, env) {
  const type = (req.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  const ext = Object.keys(TYPES).find((k) => TYPES[k] === type && type.startsWith('image/'));
  if (!ext) return json({ error: 'type' }, 415);
  const buf = await req.arrayBuffer();
  if (!buf.byteLength || buf.byteLength > MAX_PICTURE) return json({ error: 'size' }, 413);
  const name = newName(ext);
  await env.DB.prepare(`INSERT INTO files (name, type, size, created) SELECT ?, ?, ?, ? WHERE ${SUMS} + ? <= ?`).bind(name, type, buf.byteLength, now(), buf.byteLength, CAP).run();
  if (!await env.DB.prepare('SELECT 1 AS ok FROM files WHERE name = ?').bind(name).first()) return json({ error: 'full' }, 507);
  try { await env.FILES.put(name, buf, { httpMetadata: { contentType: type } }); }
  catch (e) { await env.DB.prepare('DELETE FROM files WHERE name = ?').bind(name).run(); throw e; }
  return json({ url: FILE_PATH + name, name });
}

/* Any file, in pieces: start (the room is checked and held), part 1…n, done. Nothing counts until done. */
async function dropUpload(env, u) {
  if (u.upload) { try { await env.FILES.resumeMultipartUpload(u.name, u.upload).abort(); } catch (e) {} }
  await env.DB.prepare('DELETE FROM uploads WHERE name = ?').bind(u.name).run();
}
async function dropStale(env) {
  const { results } = await env.DB.prepare('SELECT * FROM uploads WHERE created < ?').bind(now() - UPLOAD_HOURS * 36e5).all();
  for (const u of results) await dropUpload(env, u);
}
async function startUpload(env, b) {
  const orig = cleanName(b.name) || 'file';
  const size = Number(b.size);
  if (!Number.isInteger(size) || size < 1) return json({ error: 'empty' }, 400);
  if (size > MAX_FILE) return json({ error: 'size', max: MAX_FILE }, 413);
  await dropStale(env);
  const name = newName(extOf(orig)), type = typeOf(name);
  // checked and held in one statement, so two uploads at once cannot both take the last room
  await env.DB.prepare(`INSERT INTO uploads (name, orig, size, upload, created) SELECT ?, ?, ?, '', ? WHERE ${SUMS} + ? <= ?`).bind(name, orig, size, now(), size, CAP).run();
  if (!await env.DB.prepare('SELECT 1 AS ok FROM uploads WHERE name = ?').bind(name).first()) return json({ error: 'full', ...(await sums(env)) }, 507);
  try {
    const up = await env.FILES.createMultipartUpload(name, { httpMetadata: { contentType: type } });
    await env.DB.prepare('UPDATE uploads SET upload = ? WHERE name = ?').bind(up.uploadId, name).run();
  } catch (e) { await env.DB.prepare('DELETE FROM uploads WHERE name = ?').bind(name).run(); throw e; }
  return json({ name, part: PART, parts: Math.ceil(size / PART) });
}
const uploadRow = (env, name) => env.DB.prepare('SELECT * FROM uploads WHERE name = ?').bind(name).first();
async function putPart(req, env, name, n) {
  const u = await uploadRow(env, name);
  if (!u) return json({ error: 'not_found' }, 404);
  const total = Math.ceil(u.size / PART);
  if (n < 1 || n > total) return json({ error: 'part' }, 400);
  const want = n < total ? PART : u.size - (total - 1) * PART;
  if (Number(req.headers.get('Content-Length')) !== want || !req.body) return json({ error: 'size' }, 400);
  const part = await env.FILES.resumeMultipartUpload(name, u.upload).uploadPart(n, req.body);
  return json({ n, etag: part.etag });
}
async function finishUpload(env, name, b) {
  const u = await uploadRow(env, name);
  if (!u) {
    // the answer to an earlier "done" got lost on the way
    const f = await env.DB.prepare('SELECT 1 AS ok FROM files WHERE name = ?').bind(name).first();
    return f ? json({ url: FILE_PATH + name, name, kind: kindOfFile(name) }) : json({ error: 'not_found' }, 404);
  }
  const total = Math.ceil(u.size / PART);
  const parts = Array.isArray(b.parts) ? b.parts.map((p) => ({ partNumber: Number(p.n), etag: String(p.etag) })).sort((x, y) => x.partNumber - y.partNumber) : [];
  if (parts.length !== total || parts.some((p, i) => p.partNumber !== i + 1)) return json({ error: 'parts' }, 400);
  let size = 0;
  const there = await env.FILES.head(name); // joined earlier, but the row was not written
  if (there) size = there.size;
  else size = (await env.FILES.resumeMultipartUpload(name, u.upload).complete(parts)).size;
  if (size !== u.size) { await env.FILES.delete(name); await dropUpload(env, { name }); return json({ error: 'size' }, 400); }
  await env.DB.batch([
    env.DB.prepare('INSERT OR REPLACE INTO files (name, type, size, created) VALUES (?, ?, ?, ?)').bind(name, typeOf(name), size, now()),
    env.DB.prepare('INSERT OR REPLACE INTO fileinfo (name, orig) VALUES (?, ?)').bind(name, u.orig),
    env.DB.prepare('DELETE FROM uploads WHERE name = ?').bind(name),
  ]);
  return json({ url: FILE_PATH + name, name, kind: kindOfFile(name) });
}

/* How much room is used. The Desk (backup key) gets the numbers only; the list of files is for the owner. */
async function sums(env) {
  const f = await env.DB.prepare('SELECT COALESCE(SUM(size), 0) AS used, COUNT(*) AS count FROM files').first();
  const u = await env.DB.prepare('SELECT COALESCE(SUM(size), 0) AS uploading FROM uploads').first();
  return { cap: CAP, maxFile: MAX_FILE, used: f.used, count: f.count, uploading: u.uploading };
}
async function storage(env, withFiles) {
  await dropStale(env);
  const out = await sums(env);
  const { results } = await env.DB.prepare('SELECT f.name, f.size, f.created, i.orig FROM files f LEFT JOIN fileinfo i ON i.name = f.name ORDER BY f.size DESC').all();
  out.kinds = {};
  for (const r of results) { const k = kindOfFile(r.name), e = out.kinds[k] || (out.kinds[k] = { n: 0, size: 0 }); e.n++; e.size += r.size; }
  if (!withFiles) return json(out);
  const use = await pagesUsing(env);
  out.files = results.map((r) => ({ name: r.name, orig: r.orig || '', kind: kindOfFile(r.name), size: r.size, created: r.created, keep: KEEP_FILES.includes(r.name), pages: use.get(r.name) || [] }));
  return json(out);
}
async function pagesUsing(env) {
  const { results } = await env.DB.prepare("SELECT id, title, pub, lines FROM pages WHERE deleted = 0 AND lines LIKE '%/files/%' ORDER BY updated DESC").all();
  const use = new Map();
  for (const r of results) for (const n of filesOf(parseJSON(r.lines, []))) { if (!use.has(n)) use.set(n, []); use.get(n).push({ id: r.id, title: r.title, pub: !!r.pub }); }
  return use;
}
async function deleteFile(env, name) {
  if (KEEP_FILES.includes(name)) return json({ error: 'keep' }, 409);
  await env.FILES.delete(name);
  await env.DB.batch([env.DB.prepare('DELETE FROM files WHERE name = ?').bind(name), env.DB.prepare('DELETE FROM fileinfo WHERE name = ?').bind(name)]);
  return json({ ok: true, ...(await sums(env)) });
}
/* Make the list match what is really in R2. */
async function recount(env) {
  const real = new Map(); let cursor;
  do { const l = await env.FILES.list({ cursor, limit: 1000 }); l.objects.forEach((o) => real.set(o.key, o)); cursor = l.truncated ? l.cursor : undefined; } while (cursor);
  const { results } = await env.DB.prepare('SELECT name, size, created FROM files').all();
  const { results: going } = await env.DB.prepare('SELECT name FROM uploads').all();
  const known = new Map(results.map((r) => [r.name, r])), busy = new Set(going.map((r) => r.name));
  let added = 0, removed = 0, fixed = 0;
  for (const [n, o] of real) {
    const r = known.get(n);
    if (!r && !busy.has(n)) { added++; await env.DB.prepare('INSERT OR IGNORE INTO files (name, type, size, created) VALUES (?, ?, ?, ?)').bind(n, typeOf(n), o.size, +new Date(o.uploaded || now())).run(); }
    else if (r && r.size !== o.size) { fixed++; await env.DB.prepare('UPDATE files SET size = ? WHERE name = ?').bind(o.size, n).run(); }
  }
  for (const r of results) if (!real.has(r.name) && r.created < now() - 10 * MIN) { removed++; await env.DB.prepare('DELETE FROM files WHERE name = ?').bind(r.name).run(); }
  return json({ added, removed, fixed, ...(await sums(env)) });
}

/* bytes=a-b, a-, -n → { offset, length }; null = the whole file; false = not satisfiable. Videos on iPhone need this. */
function rangeOf(h, size) {
  const m = String(h || '').match(/^bytes=(\d*)-(\d*)$/);
  if (!m || (!m[1] && !m[2])) return null;
  let a, b;
  if (!m[1]) { if (!+m[2]) return false; a = Math.max(0, size - +m[2]); b = size - 1; }
  else { a = +m[1]; b = m[2] ? Math.min(+m[2], size - 1) : size - 1; }
  return a >= size || a > b ? false : { offset: a, length: b - a + 1 };
}
async function serveFile(req, env, name, owner) {
  const none = () => new Response('Not found', { status: 404 });
  if (!/^[\w.-]+$/.test(name)) return none();
  if (!owner && !bearerOk(req, env) && !await onPublicPage(env, name)) return none();
  const head = await env.FILES.head(name);
  if (!head) return none();
  const type = TYPES[extOf(name)];
  const h = { 'Content-Type': type || 'application/octet-stream', 'Cache-Control': owner ? 'private, max-age=3600' : 'public, max-age=86400', 'X-Content-Type-Options': 'nosniff', 'Accept-Ranges': 'bytes' };
  if (!type) h['Content-Security-Policy'] = "default-src 'none'; sandbox";
  if (kindOfFile(name) !== 'image') {
    const i = await env.DB.prepare('SELECT orig FROM fileinfo WHERE name = ?').bind(name).first();
    const fn = encodeURIComponent((i && i.orig) || name).replace(/['()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
    h['Content-Disposition'] = `${type ? 'inline' : 'attachment'}; filename*=UTF-8''${fn}`;
  }
  const r = rangeOf(req.headers.get('Range'), head.size);
  if (r === false) return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${head.size}` } });
  h['Content-Length'] = String(r ? r.length : head.size);
  if (r) h['Content-Range'] = `bytes ${r.offset}-${r.offset + r.length - 1}/${head.size}`;
  if (req.method === 'HEAD') return new Response(null, { status: r ? 206 : 200, headers: h });
  const obj = await env.FILES.get(name, r ? { range: r } : undefined);
  if (!obj) return none();
  return new Response(obj.body, { status: r ? 206 : 200, headers: h });
}
async function onPublicPage(env, name) {
  const { results } = await env.DB.prepare("SELECT lines FROM pages WHERE deleted = 0 AND pub = 1 AND lines LIKE ? ESCAPE '\\' LIMIT 50").bind('%/files/' + likeEsc(name) + '%').all();
  return results.some((r) => filesOf(parseJSON(r.lines, [])).includes(name));
}

/* ---------- titles of outside links ---------- */
export function safeTarget(u) {
  let x; try { x = new URL(u); } catch (e) { return false; }
  if (!/^https?:$/.test(x.protocol) || x.username || x.password) return false;
  if (x.port && x.port !== '80' && x.port !== '443') return false;
  const h = x.hostname.toLowerCase();
  if (h === 'localhost' || h.endsWith('.local') || h.endsWith('.internal') || h.endsWith('.localhost') || !h.includes('.')) return false;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(h) || h.includes(':') || h.startsWith('[')) return false; // no raw IP addresses
  return true;
}
const decodeEnt = (s) => s.replace(/&(#x?[0-9a-f]+|amp|lt|gt|quot|apos|#39);/gi, (m, e) => {
  const k = e.toLowerCase();
  if (k === 'amp') return '&'; if (k === 'lt') return '<'; if (k === 'gt') return '>'; if (k === 'quot') return '"'; if (k === 'apos' || k === '#39') return "'";
  const n = k.startsWith('#x') ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10);
  return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : m;
});
export function readMeta(htmlText, base) {
  const meta = (key) => {
    const re = new RegExp(`<meta[^>]+(?:property|name)=["']${key}["'][^>]*>`, 'i');
    const tag = htmlText.match(re); if (!tag) return '';
    const c = tag[0].match(/content=["']([^"']*)["']/i); return c ? decodeEnt(c[1]).trim() : '';
  };
  const t = meta('og:title') || meta('twitter:title') || decodeEnt(((htmlText.match(/<title[^>]*>([^<]*)<\/title>/i) || [])[1] || '').trim());
  let img = meta('og:image') || meta('twitter:image');
  try { if (img) img = new URL(img, base).href; } catch (e) { img = ''; }
  if (img && !/^https:\/\//.test(img)) img = '';
  return { title: t.replace(/\s+/g, ' ').slice(0, 300), image: img.slice(0, 1000), site: meta('og:site_name').slice(0, 100) };
}
async function fetchMeta(url) {
  const host = new URL(url).hostname.replace(/^www\./, '');
  if (/(^|\.)youtube\.com$|^youtu\.be$/.test(host)) {
    const r = await fetch('https://www.youtube.com/oembed?format=json&url=' + encodeURIComponent(url), { signal: AbortSignal.timeout(6000) }).catch(() => null);
    if (r && r.ok) { const j = await r.json().catch(() => null); if (j && j.title) return { title: String(j.title).slice(0, 300), image: String(j.thumbnail_url || '').slice(0, 1000), site: 'YouTube' }; }
  }
  let target = url;
  for (let hop = 0; hop < 4; hop++) {
    if (!safeTarget(target)) return null;
    const r = await fetch(target, { redirect: 'manual', signal: AbortSignal.timeout(6000), headers: { 'User-Agent': 'RhizomeBox/1.0 (+https://rhizomebox.tany.im) link preview', Accept: 'text/html' } }).catch(() => null);
    if (!r) return null;
    if (r.status >= 300 && r.status < 400 && r.headers.get('Location')) { target = new URL(r.headers.get('Location'), target).href; continue; }
    if (!r.ok || !/text\/html/i.test(r.headers.get('Content-Type') || '')) return null;
    const reader = r.body.getReader(); let got = 0; const chunks = [];
    while (got < 300_000) { const { done, value } = await reader.read(); if (done) break; chunks.push(value); got += value.length; }
    reader.cancel().catch(() => {});
    const buf = new Uint8Array(got); let o = 0; for (const c of chunks) { buf.set(c.subarray(0, Math.min(c.length, got - o)), o); o += c.length; }
    return readMeta(new TextDecoder().decode(buf), target);
  }
  return null;
}
export async function fillMeta(env, urls, limit = 6) {
  const t = now(); let n = 0;
  for (const url of [...new Set(urls)]) {
    if (n >= limit) break;
    if (!safeTarget(url)) continue;
    const have = await env.DB.prepare('SELECT fetched FROM linkmeta WHERE url = ?').bind(url).first();
    if (have && t - have.fetched < META_DAYS * DAY) continue;
    n++;
    const m = await fetchMeta(url).catch(() => null);
    await env.DB.prepare('INSERT OR REPLACE INTO linkmeta (url, title, image, site, fetched, ok) VALUES (?, ?, ?, ?, ?, ?)')
      .bind(url, m ? m.title : '', m ? m.image : '', m ? m.site : '', t, m ? 1 : 0).run();
  }
}
async function linkMeta(env, urls, owner) {
  urls = urls.filter((u) => typeof u === 'string' && u.length < 2000).slice(0, 100);
  if (!owner) {
    const { results } = await env.DB.prepare('SELECT web FROM pages WHERE deleted = 0 AND pub = 1').all();
    const pub = new Set(); results.forEach((r) => parseJSON(r.web, []).forEach((w) => pub.add(w.url)));
    urls = urls.filter((u) => pub.has(u));
  }
  const out = {};
  for (let i = 0; i < urls.length; i += 80) {
    const part = urls.slice(i, i + 80);
    if (!part.length) continue;
    const { results } = await env.DB.prepare(`SELECT url, title, image, site FROM linkmeta WHERE ok = 1 AND url IN (${part.map(() => '?').join(',')})`).bind(...part).all();
    results.forEach((r) => { out[r.url] = { title: r.title, image: r.image, site: r.site }; });
  }
  return out;
}

/* ---------- server-made pages ---------- */
async function userCss(env, owner) {
  const s = await env.DB.prepare("SELECT css FROM pages WHERE deleted = 0 AND lower(title) = 'settings'").first();
  let css = s ? s.css : '';
  if (owner) { const m = await env.DB.prepare('SELECT css FROM pages WHERE deleted = 0 AND lower(title) = ?').bind(ME).first(); if (m && m.css) css += '\n' + m.css; }
  return css.replace(/<\/(style)/gi, '<\\/$1');
}
function staticCtx(map, owner) {
  const link = (t, label) => {
    const p = map.get(t.toLowerCase());
    if (p) return `<a class="page-link${p.pub ? '' : ' priv'}" href="${esc(pageUrl(p.title))}">${esc(label)}</a>`;
    return owner ? `<a class="page-link empty-page-link" href="${esc(pageUrl(t))}">${esc(label)}</a>` : `<span class="hid">${esc(label)}</span>`;
  };
  const img = (u, cls) => `<img class="image ${esc(cls || '')}" src="${esc(resolveFile(u))}" alt="" loading="lazy">`;
  const icon = (name, n) => {
    const p = map.get(name.toLowerCase());
    if (p && p.image) return `<a class="page-link icon-link" href="${esc(pageUrl(p.title))}">${`<img class="icon" src="${esc(resolveFile(p.image))}" alt="${esc(name)}">`.repeat(n)}</a>`;
    return link(name, name);
  };
  return { link, img, icon };
}
async function related(env, row, owner) {
  const links = parseJSON(row.links, []);
  const map = await pagesByTitles(env, links, owner);
  const direct = [...map.values()].filter((p) => p.id !== row.id);
  const { results: back } = await env.DB.prepare(`SELECT id, title, pub, image, descr FROM pages WHERE ${visibleSql(owner)} AND id != ? AND lower(links) LIKE ? ESCAPE '\\' LIMIT 60`)
    .bind(row.id, '%' + likeEsc(JSON.stringify(row.title.toLowerCase()).slice(1, -1)) + '%').all();
  const seen = new Set(direct.map((p) => p.id)); const list = [...direct];
  for (const p of back) if (!seen.has(p.id)) { seen.add(p.id); list.push(p); }
  return { map, list };
}

/* ---------- the router ---------- */
export async function handle(req, env, ctx, deps) {
  const url = new URL(req.url);
  const path = url.pathname;
  const method = req.method;
  const base = env.BASE_URL || url.origin;
  const stage = !!env.STAGE;

  if (path.startsWith('/app/') || path === '/favicon.svg' || path === '/og.png') return env.ASSETS.fetch(req);
  // Search Console ownership check for https://rhizomebox.tany.im/ (keep it, or the property stops being verified)
  if (path === '/google452bda1af1e31bf3.html') return new Response('google-site-verification: google452bda1af1e31bf3.html', { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  if (path === '/robots.txt') {
    const body = stage ? 'User-agent: *\nDisallow: /\n' : `User-agent: *\nDisallow: /_/\nAllow: /\nSitemap: ${base}/sitemap.xml\n`;
    return new Response(body, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }
  const owner = await isOwner(req, env);

  if (path === '/sitemap.xml') {
    if (stage) return new Response('Not found', { status: 404 });
    const { results } = await env.DB.prepare('SELECT title, updated FROM pages WHERE deleted = 0 AND pub = 1 ORDER BY updated DESC LIMIT 45000').all();
    const x = results.map((r) => `<url><loc>${esc(base + pageUrl(r.title))}</loc><lastmod>${new Date(r.updated).toISOString()}</lastmod></url>`).join('');
    return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>${esc(base)}/</loc></url>${x}</urlset>`, { headers: { 'Content-Type': 'application/xml; charset=utf-8', 'Cache-Control': 'public, max-age=600' } });
  }
  if (path.startsWith('/files/')) {
    if (method !== 'GET' && method !== 'HEAD') return new Response('Method not allowed', { status: 405 });
    return serveFile(req, env, path.slice(7), owner);
  }

  if (path.startsWith('/_/api/')) {
    const api = path.slice(7);
    const write = method !== 'GET' && method !== 'HEAD';
    // Writes need the custom header: a form or image tag on another site cannot send it.
    if (write && req.headers.get('X-RB') !== '1') return json({ error: 'header' }, 403);
    if (api === 'login' && method === 'POST') return login(req, env, ctx, deps);
    if (api === 'verify' && method === 'POST') return verify(req, env, ctx);
    if (api === 'logout' && method === 'POST') return logout(req, env);
    if (api === 'me') return json({ owner });
    if (api === 'export' && method === 'GET') {
      if (!owner && !bearerOk(req, env)) return json({ error: 'auth' }, 401);
      const { results } = await env.DB.prepare('SELECT * FROM pages ORDER BY created').all();
      const { results: files } = await env.DB.prepare('SELECT f.name, f.type, f.size, f.created, i.orig FROM files f LEFT JOIN fileinfo i ON i.name = f.name ORDER BY f.created').all();
      return json({ at: now(), pages: results.map((r) => ({ ...entry(r, true), created: r.created, deleted: !!r.deleted })), files });
    }
    if (api === 'desk/words' && method === 'PUT') {
      if (!wordsKeyOk(req, env)) return json({ error: 'auth' }, 401);
      return saveWords(env, ctx, await req.json().catch(() => ({})));
    }
    if (api === 'index' && method === 'GET') {
      const mark = owner && env.BACKUP_KEY && !(await knownDevice(req, env)) ? { 'Set-Cookie': await deviceCookie(env) } : {};
      return json({ pages: await listPages(env, owner), owner }, 200, mark);
    }
    if (api === 'search' && method === 'GET') {
      const q = (url.searchParams.get('q') || '').trim().slice(0, 100);
      if (!q) return json({ ids: [] });
      const like = '%' + likeEsc(q.toLowerCase()) + '%';
      const { results } = await env.DB.prepare(`SELECT id FROM pages WHERE ${visibleSql(owner)} AND (lower(title) LIKE ? ESCAPE '\\' OR lower(lines) LIKE ? ESCAPE '\\') ORDER BY updated DESC LIMIT 200`).bind(like, like).all();
      return json({ ids: results.map((r) => r.id) });
    }
    if (api === 'linkmeta' && method === 'GET') return json(await linkMeta(env, url.searchParams.getAll('u'), owner));
    let m;
    if ((m = api.match(/^page\/([a-z0-9-]{6,40})$/))) {
      if (method === 'GET') {
        const r = await pageById(env, m[1], owner);
        return r ? json({ page: entry(r, true) }) : json({ error: 'not_found' }, 404);
      }
      if (!owner) return json({ error: 'auth' }, 401);
      if (method === 'PUT') return savePage(env, ctx, m[1], await req.json().catch(() => ({})));
      if (method === 'DELETE') {
        await env.DB.prepare('UPDATE pages SET deleted = 1, updated = ?, rev = rev + 1 WHERE id = ?').bind(now(), m[1]).run();
        return json({ ok: true });
      }
    }
    if ((m = api.match(/^versions\/([a-z0-9-]{6,40})(?:\/(\d+))?$/)) && method === 'GET') {
      if (!owner) return json({ error: 'auth' }, 401);
      if (m[2]) {
        const v = await env.DB.prepare('SELECT lines, at FROM versions WHERE page = ? AND rev = ?').bind(m[1], Number(m[2])).first();
        return v ? json({ lines: parseJSON(v.lines, []), at: v.at }) : json({ error: 'not_found' }, 404);
      }
      const { results } = await env.DB.prepare('SELECT rev, at FROM versions WHERE page = ? ORDER BY rev DESC').bind(m[1]).all();
      return json({ versions: results });
    }
    if (api === 'files' && method === 'POST') {
      if (!owner) return json({ error: 'auth' }, 401);
      return upload(req, env);
    }
    if (api === 'storage' && method === 'GET') {
      if (!owner && !bearerOk(req, env)) return json({ error: 'auth' }, 401);
      return storage(env, owner);
    }
    if (api.startsWith('files/') || api === 'storage/recount') {
      if (!owner) return json({ error: 'auth' }, 401);
      if (api === 'storage/recount' && method === 'POST') return recount(env);
      if (api === 'files/start' && method === 'POST') return startUpload(env, await req.json().catch(() => ({})));
      if ((m = api.match(/^files\/part\/([\w.-]+)\/(\d{1,4})$/)) && method === 'PUT') return putPart(req, env, m[1], Number(m[2]));
      if ((m = api.match(/^files\/done\/([\w.-]+)$/)) && method === 'POST') return finishUpload(env, m[1], await req.json().catch(() => ({})));
      if ((m = api.match(/^files\/upload\/([\w.-]+)$/)) && method === 'DELETE') { const u = await uploadRow(env, m[1]); if (u) await dropUpload(env, u); return json({ ok: true }); }
      if ((m = api.match(/^files\/([\w.-]+)$/)) && method === 'DELETE') return deleteFile(env, m[1]);
    }
    return json({ error: 'not_found' }, 404);
  }

  if (method !== 'GET' && method !== 'HEAD') return new Response('Method not allowed', { status: 405 });
  const css = await userCss(env, owner);
  const common = { owner, stage, base, css, me: ME };

  if (path === '/') {
    const { results } = await env.DB.prepare(`SELECT id, title, pub, pin, updated, image, descr FROM pages WHERE ${visibleSql(owner)} ORDER BY (pin > 0) DESC, CASE WHEN pin > 0 THEN pin END, updated DESC LIMIT 300`).all();
    return html(homeHtml({ ...common, pages: results }));
  }
  if (path === '/_/login') return Response.redirect(base + '/_/code', 302); // the address before 2026-11-24
  if (path.startsWith('/_/')) {
    const view = path.slice(3);
    if (!['links', 'web', 'images', 'graph', 'code', 'new', ...(owner ? ['storage'] : [])].includes(view)) return html(notFoundHtml(common), 404);
    return html(shellHtml({ ...common, view }));
  }
  const raw = decodePath(path);
  // "_" in an address stands for a space, but a title may also really contain "_": try both.
  const row = (await pageByTitle(env, raw, owner)) || (raw.includes('_') ? await pageByTitle(env, raw.replace(/_/g, ' '), owner) : null);
  const title = raw.replace(/_/g, ' ');
  if (!row) {
    if (owner) return html(shellHtml({ ...common, view: 'new', newTitle: title }));
    return html(notFoundHtml(common), 404);
  }
  // one address per page: /<title> with spaces written as _
  if (path !== pageUrl(row.title)) return Response.redirect(base + pageUrl(row.title), 301);
  const { map, list } = await related(env, row, owner);
  const lines = parseJSON(row.lines, []);
  const body = renderStatic(lines, staticCtx(map, owner));
  return html(pageHtml({ ...common, page: entry(row, true), body, related: list }));
}
