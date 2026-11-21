// Rhizome Box server: every request comes here first (static files are served from public/ through ASSETS).
// Privacy rule: anything a visitor can receive is read through visibleSql(owner), so a private page,
// its title, its images and its links never leave the server unless the owner is signed in.

import { derive, renderStatic, esc, isImg, FILE_PATH } from '../public/app/notation.js';
import { pageHtml, homeHtml, shellHtml, notFoundHtml } from './html.js';

const DAY = 864e5, MIN = 6e4;
const SESSION_DAYS = 90;
const CODE_MINUTES = 10;
const VERSION_GAP = 5 * MIN;
const KEEP_VERSIONS = 100;
const MAX_LINES = 5000, MAX_LINE = 10000, MAX_BYTES = 1_000_000;
const MAX_FILE = 10 * 1024 * 1024;
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
async function throttled(env, key, limit, windowMs) {
  const t = now();
  await env.DB.prepare('DELETE FROM throttle WHERE at < ?').bind(t - DAY).run();
  const r = await env.DB.prepare('SELECT COUNT(*) AS n FROM throttle WHERE key = ? AND at > ?').bind(key, t - windowMs).first();
  if (r.n >= limit) return true;
  await env.DB.prepare('INSERT INTO throttle (key, at) VALUES (?, ?)').bind(key, t).run();
  return false;
}
async function login(req, env, deps) {
  const b = await req.json().catch(() => ({}));
  const email = String(b.email || '').trim().toLowerCase();
  if (await throttled(env, 'login', 5, 10 * MIN) || await throttled(env, 'login-day', 20, DAY)) return json({ error: 'slow_down' }, 429);
  // Same answer whether or not the address is the owner's, so the address cannot be guessed from here.
  if (env.OWNER_EMAIL && email && email === String(env.OWNER_EMAIL).trim().toLowerCase()) {
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1e6).padStart(6, '0');
    await env.DB.prepare('DELETE FROM codes').run();
    await env.DB.prepare('INSERT INTO codes (hash, expires) VALUES (?, ?)').bind(await sha256('code:' + code), now() + CODE_MINUTES * MIN).run();
    await deps.sendMail(env, {
      to: env.OWNER_EMAIL,
      subject: `Rhizome Box sign-in code: ${code}`,
      text: `Your sign-in code is ${code}\n\nIt works for ${CODE_MINUTES} minutes, once.\nIf you did not ask for it, you can ignore this email.`,
    });
  }
  return json({ ok: true });
}
async function verify(req, env) {
  const b = await req.json().catch(() => ({}));
  const code = String(b.code || '').replace(/\D/g, '');
  if (await throttled(env, 'verify', 10, 10 * MIN)) return json({ error: 'slow_down' }, 429);
  const row = await env.DB.prepare('SELECT * FROM codes LIMIT 1').first();
  if (!row || row.expires < now()) return json({ error: 'no_code' }, 400);
  if (code.length !== 6 || !safeEq(await sha256('code:' + code), row.hash)) {
    if (row.tries + 1 >= 5) await env.DB.prepare('DELETE FROM codes').run();
    else await env.DB.prepare('UPDATE codes SET tries = tries + 1 WHERE hash = ?').bind(row.hash).run();
    return json({ error: 'wrong_code' }, 400);
  }
  await env.DB.prepare('DELETE FROM codes').run();
  const tok = randHex(32);
  await env.DB.prepare('DELETE FROM sessions WHERE expires < ?').bind(now()).run();
  await env.DB.prepare('INSERT INTO sessions (hash, created, expires) VALUES (?, ?, ?)').bind(await sha256(tok), now(), now() + SESSION_DAYS * DAY).run();
  return json({ ok: true }, 200, { 'Set-Cookie': `rb=${tok}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}` });
}
async function logout(req, env) {
  const tok = cookie(req, 'rb');
  if (tok) await env.DB.prepare('DELETE FROM sessions WHERE hash = ?').bind(await sha256(tok)).run();
  return json({ ok: true }, 200, { 'Set-Cookie': 'rb=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0' });
}

/* ---------- files ---------- */
const TYPES = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'image/avif': 'avif' };
async function upload(req, env) {
  const type = (req.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  const ext = TYPES[type];
  if (!ext) return json({ error: 'type' }, 415);
  const buf = await req.arrayBuffer();
  if (!buf.byteLength || buf.byteLength > MAX_FILE) return json({ error: 'size' }, 413);
  const name = `${now().toString(36)}${randHex(4)}.${ext}`;
  await env.FILES.put(name, buf, { httpMetadata: { contentType: type } });
  await env.DB.prepare('INSERT INTO files (name, type, size, created) VALUES (?, ?, ?, ?)').bind(name, type, buf.byteLength, now()).run();
  return json({ url: FILE_PATH + name, name });
}
async function serveFile(req, env, name, owner) {
  if (!/^[\w.-]+$/.test(name)) return new Response('Not found', { status: 404 });
  if (!owner && !bearerOk(req, env)) {
    const used = await env.DB.prepare("SELECT 1 AS ok FROM pages WHERE deleted = 0 AND pub = 1 AND images LIKE ? ESCAPE '\\' LIMIT 1").bind('%' + likeEsc('/files/' + name) + '"%').first();
    if (!used) return new Response('Not found', { status: 404 });
  }
  const obj = await env.FILES.get(name);
  if (!obj) return new Response('Not found', { status: 404 });
  return new Response(obj.body, { headers: { 'Content-Type': obj.httpMetadata?.contentType || 'application/octet-stream', 'Cache-Control': owner ? 'private, max-age=3600' : 'public, max-age=86400', 'X-Content-Type-Options': 'nosniff' } });
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
  const img = (u, cls) => `<img class="image ${esc(cls || '')}" src="${esc(u.replace(/^https?:\/\/[^/]+(\/files\/)/, '$1'))}" alt="" loading="lazy">`;
  const icon = (name, n) => {
    const p = map.get(name.toLowerCase());
    if (p && p.image) return `<a class="page-link icon-link" href="${esc(pageUrl(p.title))}">${`<img class="icon" src="${esc(p.image.replace(/^https?:\/\/[^/]+(\/files\/)/, '$1'))}" alt="${esc(name)}">`.repeat(n)}</a>`;
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
    if (api === 'login' && method === 'POST') return login(req, env, deps);
    if (api === 'verify' && method === 'POST') return verify(req, env);
    if (api === 'logout' && method === 'POST') return logout(req, env);
    if (api === 'me') return json({ owner });
    if (api === 'export' && method === 'GET') {
      if (!owner && !bearerOk(req, env)) return json({ error: 'auth' }, 401);
      const { results } = await env.DB.prepare('SELECT * FROM pages ORDER BY created').all();
      const { results: files } = await env.DB.prepare('SELECT name, type, size, created FROM files ORDER BY created').all();
      return json({ at: now(), pages: results.map((r) => ({ ...entry(r, true), created: r.created, deleted: !!r.deleted })), files });
    }
    if (api === 'desk/words' && method === 'PUT') {
      if (!wordsKeyOk(req, env)) return json({ error: 'auth' }, 401);
      return saveWords(env, ctx, await req.json().catch(() => ({})));
    }
    if (api === 'index' && method === 'GET') return json({ pages: await listPages(env, owner), owner });
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
    return json({ error: 'not_found' }, 404);
  }

  if (method !== 'GET' && method !== 'HEAD') return new Response('Method not allowed', { status: 405 });
  const css = await userCss(env, owner);
  const common = { owner, stage, base, css, me: ME };

  if (path === '/') {
    const { results } = await env.DB.prepare(`SELECT id, title, pub, pin, updated, image, descr FROM pages WHERE ${visibleSql(owner)} ORDER BY (pin > 0) DESC, CASE WHEN pin > 0 THEN pin END, updated DESC LIMIT 300`).all();
    return html(homeHtml({ ...common, pages: results }));
  }
  if (path.startsWith('/_/')) {
    const view = path.slice(3);
    if (!['links', 'web', 'images', 'graph', 'login', 'new'].includes(view)) return html(notFoundHtml(common), 404);
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
