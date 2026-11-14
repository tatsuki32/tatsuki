// Sift Daily — sift.tany.im
// Pages: /  (what it is, plans), /app (your news and sources), /admin (owner only).
// Crons (UTC): 07:30 = 14:30 Ho Chi Minh, fetch + summaries; 08:00 = 15:00, the daily email.
// Secrets (never in this repo): SIFT_KEY (owner passphrase, also the Desk's sync key), ANTHROPIC_KEY, MAIL_TO (the owner's inbox).
import { EmailMessage } from "cloudflare:email";
import { discover, get, parseFeed, cleanUrl } from "./feed.js";
import { summarize, MAX_ITEMS, LANGS } from "./summary.js";
import { digestEmail, mime } from "./mail.js";
import { PLANS, plan, AI_PRICE } from "./plans.js";
import { landingPage, appPage, adminPage, loginPage, markPage } from "./pages.js";

// the sender is "sift" at the site's own domain (Email Routing must be on for that domain)
const sender = (env) => "sift" + "@" + new URL(env.BASE_URL || "https://sift.tany.im").hostname.split(".").slice(-2).join(".");
const SESSION_DAYS = 60;
const FEEDS_PER_RUN = 40; // Workers allow 50 outside requests per run; the rest is for the AI call
const KEEP_DAYS = 90;
const DAY = 864e5;

// ---------- small helpers ----------
const json = (v, status = 200) => new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const html = (body, status = 200, extra = {}) => new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-frame-options": "DENY", "referrer-policy": "same-origin", ...extra } });
const rid = () => crypto.randomUUID().replace(/-/g, "").slice(0, 16);
export const localDay = (ms, tz) => new Intl.DateTimeFormat("en-CA", { timeZone: tz || "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));

async function hmac(key, msg) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg));
  return [...new Uint8Array(sig)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function same(a, b) {
  a = String(a || ""); b = String(b || "");
  if (!a || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function log(env, kind, userId, ok, detail, tin = 0, tout = 0) {
  await env.DB.prepare("INSERT INTO runs (at, kind, userId, ok, detail, tin, tout) VALUES (?, ?, ?, ?, ?, ?, ?)").bind(Date.now(), kind, userId, ok ? 1 : 0, String(detail || "").slice(0, 500), tin, tout).run();
}

// ---------- sign-in ----------
async function ensureOwner(env) {
  await env.DB.prepare("INSERT OR IGNORE INTO users (id, email, name, role, plan, createdAt) VALUES ('owner', ?, 'Owner', 'owner', 'pro', ?)").bind(env.MAIL_TO || null, Date.now()).run();
  if (env.MAIL_TO) await env.DB.prepare("UPDATE users SET email = ? WHERE id = 'owner' AND (email IS NULL OR email = '')").bind(env.MAIL_TO).run();
}

async function sessionCookie(env, uid) {
  const exp = Date.now() + SESSION_DAYS * DAY;
  const sig = await hmac(env.SIFT_KEY, `s:${uid}:${exp}`);
  return `sift=${uid}.${exp}.${sig}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_DAYS * 86400}`;
}

async function who(req, env) {
  if (!env.SIFT_KEY) return null;
  const m = (req.headers.get("cookie") || "").match(/(?:^|;\s*)sift=([^;]+)/);
  if (!m) return null;
  const [uid, exp, sig] = m[1].split(".");
  if (!uid || Number(exp) < Date.now() || !same(sig, await hmac(env.SIFT_KEY, `s:${uid}:${exp}`))) return null;
  const u = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(uid).first();
  if (u && (!u.lastSeen || Date.now() - u.lastSeen > 3600e3)) await env.DB.prepare("UPDATE users SET lastSeen = ? WHERE id = ?").bind(Date.now(), uid).run();
  return u;
}

// writes from a page must come from this site
const sameSite = (req, env) => {
  const o = req.headers.get("origin");
  if (!o) return true;
  const ok = [new URL(req.url).origin];
  try { if (env && env.BASE_URL) ok.push(new URL(env.BASE_URL).origin); } catch { /* not set */ }
  return ok.includes(o);
};

// ---------- fetching ----------
export function pickNew(items, feed, now) {
  const fresh = items.filter((i) => !i.published || i.published > now - 7 * DAY);
  if (!feed.lastOk) return fresh.filter((i) => !i.published || i.published > now - 2 * DAY).slice(0, 5); // a new source starts with its last 2 days, 5 at most
  return fresh;
}

async function fetchFeed(env, user, feed, now) {
  try {
    const page = await get(feed.url);
    const f = parseFeed(page.body, feed.url);
    const fresh = pickNew(f.items, feed, now);
    const day = localDay(now, user.tz);
    let added = 0;
    if (fresh.length) {
      const st = env.DB.prepare("INSERT OR IGNORE INTO items (id, userId, feedId, url, title, descr, published, fetched, day) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)");
      const res = await env.DB.batch(fresh.map((i) => st.bind(rid(), user.id, feed.id, i.url, i.title, i.desc || null, i.published || null, now, day)));
      added = res.reduce((n, r) => n + (r.meta.changes || 0), 0);
    }
    await env.DB.prepare("UPDATE feeds SET lastTry = ?, lastOk = ?, lastError = NULL, lastCount = ?, site = COALESCE(site, ?) WHERE id = ?").bind(now, now, added, f.site || null, feed.id).run();
    return { ok: true, added };
  } catch (e) {
    await env.DB.prepare("UPDATE feeds SET lastTry = ?, lastError = ? WHERE id = ?").bind(now, String(e.message).slice(0, 200), feed.id).run();
    return { ok: false, added: 0, error: e.message };
  }
}

async function summarizeUser(env, user, now) {
  if (!plan(user.plan).ai) return { skipped: "plan" };
  const { results } = await env.DB.prepare(
    "SELECT items.id, items.title, items.descr, feeds.name AS site FROM items JOIN feeds ON feeds.id = items.feedId WHERE items.userId = ? AND items.line IS NULL AND items.mailed IS NULL AND items.fetched > ? ORDER BY items.fetched DESC LIMIT ?"
  ).bind(user.id, now - 3 * DAY, MAX_ITEMS).all();
  if (!results.length) return { skipped: "nothing new" };
  try {
    const s = await summarize(env, results, null, user.lang || "en");
    const st = env.DB.prepare("UPDATE items SET line = ? WHERE id = ?");
    await env.DB.batch(results.map((it, i) => st.bind(s.lines[i] || "", it.id)));
    const day = localDay(now, user.tz);
    await env.DB.prepare("INSERT INTO digests (userId, day, summary, count) VALUES (?, ?, ?, ?) ON CONFLICT (userId, day) DO UPDATE SET summary = excluded.summary, count = excluded.count").bind(user.id, day, s.brief, results.length).run();
    await log(env, "summary", user.id, true, `${results.length} articles`, s.tin, s.tout);
    return { done: results.length };
  } catch (e) {
    await log(env, "summary", user.id, false, e.message, e.tin || 0, e.tout || 0);
    return { error: e.message };
  }
}

export async function fetchUser(env, user, now = Date.now()) {
  const { results: feeds } = await env.DB.prepare("SELECT * FROM feeds WHERE userId = ? AND active = 1 ORDER BY COALESCE(lastTry, 0) LIMIT ?").bind(user.id, FEEDS_PER_RUN).all();
  let added = 0, failed = 0;
  for (const f of feeds) {
    const r = await fetchFeed(env, user, f, now);
    added += r.added;
    if (!r.ok) failed++;
  }
  await log(env, "fetch", user.id, failed === 0, `${feeds.length} sources, ${added} new, ${failed} failed`);
  const s = await summarizeUser(env, user, now);
  return { sources: feeds.length, added, failed, summary: s };
}

async function fetchAll(env) {
  const now = Date.now();
  const { results: users } = await env.DB.prepare("SELECT * FROM users").all();
  for (const u of users) await fetchUser(env, u, now);
  // tidy: old articles go, ones marked Later or task stay
  await env.DB.prepare("DELETE FROM items WHERE fetched < ? AND (mark IS NULL OR mark = 'read')").bind(now - KEEP_DAYS * DAY).run();
  await env.DB.prepare("DELETE FROM runs WHERE at < ?").bind(now - KEEP_DAYS * DAY).run();
}

// ---------- the daily email ----------
const markLink = async (env, base, id, mark) => `${base}/m/${id}/${mark}/${await hmac(env.SIFT_KEY, `m:${id}:${mark}`)}`;

export async function mailUser(env, user, base, now = Date.now()) {
  if (!user.email) return { skipped: "no email address" };
  if (!env.MAIL) return { skipped: "no MAIL binding" };
  const { results } = await env.DB.prepare(
    "SELECT items.*, feeds.name AS site, feeds.folder FROM items JOIN feeds ON feeds.id = items.feedId WHERE items.userId = ? AND items.mailed IS NULL AND items.fetched > ? AND (items.mark IS NULL OR items.mark != 'read') ORDER BY feeds.folder, items.published DESC"
  ).bind(user.id, now - 3 * DAY).all();
  if (!results.length) {
    await log(env, "mail", user.id, true, "nothing new, no email");
    return { skipped: "nothing new" };
  }
  const day = localDay(now, user.tz);
  const d = await env.DB.prepare("SELECT summary FROM digests WHERE userId = ? AND day = ?").bind(user.id, day).first();
  const ai = plan(user.plan).ai;
  const groups = [];
  for (const it of results) {
    let g = groups.find((x) => x.folder === it.folder);
    if (!g) groups.push((g = { folder: it.folder, items: [] }));
    g.items.push({ title: it.title, url: it.url, site: it.site, line: ai ? it.line : "", later: await markLink(env, base, it.id, "later"), task: await markLink(env, base, it.id, "task") });
  }
  const m = digestEmail({ day, brief: ai && d ? d.summary : "", groups, total: results.length, appUrl: base + "/app" });
  const id = rid();
  try {
    const from = sender(env);
    await env.MAIL.send(new EmailMessage(from, user.email, mime({ from, fromName: "Sift Daily", to: user.email, subject: m.subject, text: m.text, html: m.html, id })));
  } catch (e) {
    await log(env, "mail", user.id, false, e.message);
    return { error: e.message };
  }
  const st = env.DB.prepare("UPDATE items SET mailed = ? WHERE id = ?");
  await env.DB.batch(results.map((it) => st.bind(now, it.id)));
  await env.DB.prepare("INSERT INTO digests (userId, day, count, sentAt) VALUES (?, ?, ?, ?) ON CONFLICT (userId, day) DO UPDATE SET sentAt = excluded.sentAt").bind(user.id, day, results.length, now).run();
  await log(env, "mail", user.id, true, `${results.length} articles`);
  return { sent: results.length };
}

async function mailAll(env) {
  const base = env.BASE_URL || "https://sift.tany.im";
  const { results: users } = await env.DB.prepare("SELECT * FROM users WHERE mail = 1").all();
  for (const u of users) await mailUser(env, u, base);
}

// ---------- API ----------
async function body(req) {
  try { return await req.json(); } catch { return {}; }
}

const FOLDER = (s) => String(s || "").replace(/\s+/g, " ").trim().slice(0, 30) || "General";

async function api(req, env, user, path) {
  const m = req.method;
  const p = path.split("/").filter(Boolean); // ["api", ...]
  if (m !== "GET" && !sameSite(req, env)) return json({ error: "wrong origin" }, 403);

  if (p[1] === "me" && m === "GET") {
    const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM feeds WHERE userId = ? AND active = 1").bind(user.id).first();
    return json({ id: user.id, role: user.role, plan: plan(user.plan), feeds: n.n, tz: user.tz, mail: !!user.mail, hasEmail: !!user.email, lang: user.lang || "en", langs: Object.entries(LANGS).map(([id, l]) => ({ id, name: l.name })) });
  }
  if (p[1] === "me" && m === "PUT") {
    const b = await body(req);
    if (b.lang !== undefined && !LANGS[b.lang]) return json({ error: "no such language" }, 400);
    if (b.lang !== undefined) await env.DB.prepare("UPDATE users SET lang = ? WHERE id = ?").bind(b.lang, user.id).run();
    return json({ ok: true });
  }

  if (p[1] === "feeds") {
    if (p[2] === "find" && m === "POST") return json(await discover((await body(req)).url));
    if (!p[2] && m === "GET") {
      const { results } = await env.DB.prepare("SELECT * FROM feeds WHERE userId = ? ORDER BY active DESC, folder, name").bind(user.id).all();
      return json(results);
    }
    if (!p[2] && m === "POST") {
      const b = await body(req);
      const url = cleanUrl(b.url);
      if (!url) return json({ error: "That address can't be used." }, 400);
      const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM feeds WHERE userId = ? AND active = 1").bind(user.id).first();
      const lim = plan(user.plan).feeds;
      if (n.n >= lim) return json({ error: `Your plan has room for ${lim} sources. Unsubscribe one first.` }, 400);
      const old = await env.DB.prepare("SELECT id FROM feeds WHERE userId = ? AND url = ?").bind(user.id, url).first();
      if (old) {
        await env.DB.prepare("UPDATE feeds SET active = 1, folder = ?, name = ? WHERE id = ?").bind(FOLDER(b.folder), String(b.name || "").slice(0, 80) || url, old.id).run();
        return json({ id: old.id, back: true });
      }
      const id = rid();
      await env.DB.prepare("INSERT INTO feeds (id, userId, url, site, name, folder, addedAt) VALUES (?, ?, ?, ?, ?, ?, ?)").bind(id, user.id, url, cleanUrl(b.site) || null, String(b.name || "").slice(0, 80) || new URL(url).hostname, FOLDER(b.folder), Date.now()).run();
      return json({ id });
    }
    if (p[2] && m === "PUT") {
      const b = await body(req);
      const f = await env.DB.prepare("SELECT * FROM feeds WHERE id = ? AND userId = ?").bind(p[2], user.id).first();
      if (!f) return json({ error: "not found" }, 404);
      if (b.active === true && !f.active) {
        const n = await env.DB.prepare("SELECT COUNT(*) AS n FROM feeds WHERE userId = ? AND active = 1").bind(user.id).first();
        if (n.n >= plan(user.plan).feeds) return json({ error: "Your plan is full. Unsubscribe one first." }, 400);
      }
      await env.DB.prepare("UPDATE feeds SET name = ?, folder = ?, active = ? WHERE id = ?").bind(b.name !== undefined ? String(b.name).slice(0, 80) || f.name : f.name, b.folder !== undefined ? FOLDER(b.folder) : f.folder, b.active !== undefined ? (b.active ? 1 : 0) : f.active, f.id).run();
      return json({ ok: true });
    }
  }

  if (p[1] === "items" && m === "GET") {
    const u = new URL(req.url);
    const later = u.searchParams.get("later") === "1";
    const day = u.searchParams.get("day") || localDay(Date.now(), user.tz);
    const q = later
      ? env.DB.prepare("SELECT items.*, feeds.name AS site, feeds.folder FROM items JOIN feeds ON feeds.id = items.feedId WHERE items.userId = ? AND items.mark = 'later' ORDER BY items.markedAt DESC LIMIT 200").bind(user.id)
      : env.DB.prepare("SELECT items.*, feeds.name AS site, feeds.folder FROM items JOIN feeds ON feeds.id = items.feedId WHERE items.userId = ? AND items.day = ? ORDER BY feeds.folder, items.published DESC LIMIT 300").bind(user.id, day);
    const { results } = await q.all();
    const d = later ? null : await env.DB.prepare("SELECT summary, sentAt FROM digests WHERE userId = ? AND day = ?").bind(user.id, day).first();
    const { results: days } = await env.DB.prepare("SELECT day, COUNT(*) AS n FROM items WHERE userId = ? GROUP BY day ORDER BY day DESC LIMIT 30").bind(user.id).all();
    const ai = plan(user.plan).ai;
    return json({ day, items: results.map((r) => ({ ...r, line: ai ? r.line : null })), brief: ai && d ? d.summary : "", sentAt: d ? d.sentAt : null, days });
  }
  if (p[1] === "items" && p[2] && m === "PUT") {
    const mark = (await body(req)).mark;
    if (![null, "read", "later", "task"].includes(mark)) return json({ error: "bad mark" }, 400);
    const r = await env.DB.prepare("UPDATE items SET mark = ?, markedAt = ?, taskSent = CASE WHEN ? = 'task' THEN taskSent ELSE NULL END WHERE id = ? AND userId = ?").bind(mark, Date.now(), mark, p[2], user.id).run();
    return json({ ok: r.meta.changes > 0 });
  }

  if (p[1] === "run" && m === "POST") {
    if (p[2] === "fetch") return json(await fetchUser(env, user));
    if (p[2] === "mail") return json(await mailUser(env, user, env.BASE_URL || new URL(req.url).origin));
  }

  if (p[1] === "admin") {
    if (user.role !== "owner") return json({ error: "owner only" }, 403);
    if (p[2] === "users" && p[3] && m === "PUT") {
      const b = await body(req);
      if (b.plan && !PLANS[b.plan]) return json({ error: "no such plan" }, 400);
      await env.DB.prepare("UPDATE users SET plan = COALESCE(?, plan), mail = COALESCE(?, mail) WHERE id = ?").bind(b.plan || null, b.mail === undefined ? null : b.mail ? 1 : 0, p[3]).run();
      return json({ ok: true });
    }
    if (!p[2] && m === "GET") {
      const now = Date.now();
      const { results: users } = await env.DB.prepare(
        `SELECT u.id, u.name, u.role, u.plan, u.mail, u.createdAt, u.lastSeen, (u.email IS NOT NULL AND u.email != '') AS hasEmail,
           (SELECT COUNT(*) FROM feeds f WHERE f.userId = u.id AND f.active = 1) AS feeds,
           (SELECT COUNT(*) FROM feeds f WHERE f.userId = u.id AND f.active = 1 AND f.lastError IS NOT NULL) AS failing,
           (SELECT COUNT(*) FROM items i WHERE i.userId = u.id AND i.fetched > ?) AS items7,
           (SELECT MAX(sentAt) FROM digests d WHERE d.userId = u.id) AS lastMail
         FROM users u ORDER BY u.createdAt`
      ).bind(now - 7 * DAY).all();
      const { results: runs } = await env.DB.prepare("SELECT * FROM runs ORDER BY at DESC LIMIT 40").all();
      const t = await env.DB.prepare("SELECT COALESCE(SUM(tin), 0) AS tin, COALESCE(SUM(tout), 0) AS tout FROM runs WHERE at > ?").bind(now - 30 * DAY).first();
      const cost = (t.tin * AI_PRICE.in + t.tout * AI_PRICE.out) / 1e6;
      const mrr = users.reduce((s, u) => s + (u.role === "owner" ? 0 : plan(u.plan).month), 0);
      return json({ users, runs, ai30: { tin: t.tin, tout: t.tout, usd: Math.round(cost * 10000) / 10000 }, mrr, plans: Object.values(PLANS) });
    }
  }
  return json({ error: "not found" }, 404);
}

// the Desk on the Mac picks up "Make it a task" here (x-sift-key = SIFT_KEY)
async function deskApi(req, env, path) {
  if (!env.SIFT_KEY || !same(req.headers.get("x-sift-key"), env.SIFT_KEY)) return json({ error: "no" }, 401);
  if (path === "/api/desk/tasks" && req.method === "GET") {
    const { results } = await env.DB.prepare("SELECT items.id, items.url, items.title, items.line, items.descr, items.markedAt, feeds.name AS site FROM items JOIN feeds ON feeds.id = items.feedId WHERE items.userId = 'owner' AND items.mark = 'task' AND items.taskSent IS NULL ORDER BY items.markedAt LIMIT 50").all();
    return json(results);
  }
  const m = path.match(/^\/api\/desk\/tasks\/([a-f0-9]+)\/sent$/);
  if (m && req.method === "POST") {
    await env.DB.prepare("UPDATE items SET taskSent = ? WHERE id = ? AND userId = 'owner'").bind(Date.now(), m[1]).run();
    return json({ ok: true });
  }
  return json({ error: "not found" }, 404);
}

// ---------- the Later / task links in the email ----------
async function markRoute(req, env, path) {
  const m = path.match(/^\/m\/([a-f0-9]+)\/(later|task|read)\/([a-f0-9]{32})$/);
  if (!m || !env.SIFT_KEY || !same(m[3], await hmac(env.SIFT_KEY, `m:${m[1]}:${m[2]}`))) return html(markPage({ bad: true }), 404);
  const it = await env.DB.prepare("SELECT id, title, url, mark FROM items WHERE id = ?").bind(m[1]).first();
  if (!it) return html(markPage({ bad: true }), 404);
  if (req.method === "POST") {
    if (!sameSite(req, env)) return html(markPage({ bad: true }), 403);
    await env.DB.prepare("UPDATE items SET mark = ?, markedAt = ? WHERE id = ?").bind(m[2], Date.now(), it.id).run();
    return html(markPage({ item: it, mark: m[2], done: true }));
  }
  // a GET only shows the button: mail scanners open links, people press buttons
  return html(markPage({ item: it, mark: m[2], done: it.mark === m[2] }));
}

// ---------- routing ----------
export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path === "/") return html(landingPage(), 200, { "cache-control": "public, max-age=300" });
    if (path === "/robots.txt") return new Response("User-agent: *\nDisallow: /app\nDisallow: /admin\nDisallow: /m/\nDisallow: /api/\n", { headers: { "content-type": "text/plain" } });
    if (path.startsWith("/m/")) return markRoute(req, env, path);
    if (path.startsWith("/api/desk/")) return deskApi(req, env, path);

    if (path === "/login") {
      if (req.method === "POST") {
        if (!sameSite(req, env)) return html(loginPage({ error: "Please try again." }), 403);
        const f = await req.formData();
        if (!env.SIFT_KEY || !same(f.get("key"), env.SIFT_KEY)) return html(loginPage({ error: "That passphrase didn't match." }), 401);
        await ensureOwner(env);
        const next = String(f.get("next") || "/app");
        return new Response(null, { status: 303, headers: { location: next === "/admin" ? "/admin" : "/app", "set-cookie": await sessionCookie(env, "owner") } });
      }
      return html(loginPage({ next: url.searchParams.get("next") || "/app" }));
    }
    if (path === "/logout") return new Response(null, { status: 303, headers: { location: "/", "set-cookie": "sift=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0" } });

    const user = await who(req, env);
    if (path === "/app" || path === "/admin") {
      if (!user) return Response.redirect(`${url.origin}/login?next=${path}`, 303);
      if (path === "/admin" && user.role !== "owner") return html("Not found", 404);
      return html(path === "/app" ? appPage() : adminPage());
    }
    if (path.startsWith("/api/")) {
      if (!user) return json({ error: "sign in" }, 401);
      return api(req, env, user, path);
    }
    return html("Not found", 404);
  },

  async scheduled(event, env, ctx) {
    if (event.cron === "0 8 * * *") ctx.waitUntil(mailAll(env));
    else ctx.waitUntil(fetchAll(env));
  },
};
