// A stand-in for Cloudflare D1, R2 and static files, so the whole server can be tested in Node.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { handle } from '../src/app.js';

class Stmt {
  constructor(db, sql, args = []) { this.db = db; this.sql = sql; this.args = args; }
  bind(...a) { return new Stmt(this.db, this.sql, a); }
  async first() { return this.db.prepare(this.sql).get(...this.args) ?? null; }
  async all() { return { results: this.db.prepare(this.sql).all(...this.args) }; }
  async run() { this.db.prepare(this.sql).run(...this.args); return { success: true }; }
}
export function fakeD1() {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(new URL('../schema.sql', import.meta.url), 'utf8'));
  return {
    raw: db,
    prepare: (sql) => new Stmt(db, sql),
    async batch(stmts) { db.exec('BEGIN'); try { for (const s of stmts) await s.run(); db.exec('COMMIT'); } catch (e) { db.exec('ROLLBACK'); throw e; } return []; },
  };
}
export function fakeR2() {
  const m = new Map(), ups = new Map(); let n = 0;
  const read = async (v) => (v instanceof ReadableStream ? Buffer.from(await new Response(v).arrayBuffer()) : Buffer.from(v));
  const obj = (k) => { const o = m.get(k); return { key: k, size: o.body.length, body: o.body, httpMetadata: o.httpMetadata, uploaded: new Date() }; };
  return {
    m, ups,
    async put(k, v, o) { m.set(k, { body: await read(v), httpMetadata: o?.httpMetadata || {} }); },
    async head(k) { return m.has(k) ? obj(k) : null; },
    async get(k, o) { if (!m.has(k)) return null; const x = obj(k); if (o?.range) x.body = x.body.subarray(o.range.offset, o.range.offset + o.range.length); return x; },
    async delete(k) { m.delete(k); },
    async list() { return { objects: [...m.keys()].map(obj), truncated: false }; },
    async createMultipartUpload(k, o) { const id = 'up' + (++n); ups.set(id, { k, o, parts: new Map() }); return { uploadId: id }; },
    resumeMultipartUpload(k, id) {
      const u = () => { const x = ups.get(id); if (!x || x.k !== k) throw new Error('no such upload'); return x; };
      return {
        async uploadPart(i, v) { const b = await read(v); u().parts.set(i, b); return { partNumber: i, etag: 'e' + i }; },
        async complete(parts) { const x = u(); const body = Buffer.concat(parts.map((p) => { if (p.etag !== 'e' + p.partNumber) throw new Error('etag'); return x.parts.get(p.partNumber); })); m.set(k, { body, httpMetadata: x.o?.httpMetadata || {} }); ups.delete(id); return { size: body.length }; },
        async abort() { u(); ups.delete(id); },
      };
    },
  };
}

export function makeEnv(extra = {}) {
  const mails = [];
  const env = {
    DB: fakeD1(), FILES: fakeR2(), OWNER_EMAIL: 'owner@example.com', BACKUP_KEY: 'k'.repeat(40),
    ASSETS: { fetch: async () => new Response('asset') }, ...extra,
  };
  const deps = { sendMail: async (e, m) => { mails.push(m); } };
  const waits = [];
  const ctx = { waitUntil: (p) => waits.push(p) };
  const call = async (method, path, { body, cookie, headers = {}, raw } = {}) => {
    const h = new Headers(headers);
    if (cookie) h.set('Cookie', 'rb=' + cookie);
    if (body !== undefined && !raw) h.set('Content-Type', 'application/json');
    if (raw && body !== undefined && !h.has('Content-Length')) h.set('Content-Length', String(body.length)); // as the network would
    const req = new Request('https://rb.test' + path, { method, headers: h, body: body === undefined ? undefined : raw ? body : JSON.stringify(body) });
    return handle(req, env, ctx, deps);
  };
  const W = { 'X-RB': '1' };
  async function signIn() {
    await call('POST', '/_/api/login', { body: { email: 'Owner@Example.com' }, headers: W });
    const code = mails.at(-1).subject.match(/(\d{6})/)[1];
    const r = await call('POST', '/_/api/verify', { body: { code }, headers: W });
    return r.headers.get('Set-Cookie').match(/rb=([0-9a-f]{64})/)[1];
  }
  return { env, mails, call, signIn, W, waits };
}
