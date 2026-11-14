// go.tany.im — short links for Tatsuki's Desk (Post Studio).
// A click is counted (time, country, referring site, kind of device, robot or not) and sent on with a 302.
// No IP address and no cookie is kept. The /api/ part answers only to Desk's key (DESK_KEY secret).

import * as accounts from "./accounts.js";

const ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"; // no l, o, 0, 1: easy to read out loud
const BOTS = /bot|crawl|spider|slurp|preview|facebookexternalhit|meta-externalagent|facebot|twitterbot|linkedinbot|slackbot|telegrambot|whatsapp|discordbot|skypeuripreview|embedly|quora link|pinterest|vkshare|redditbot|applebot|google-inspectiontool|headless|curl|wget|python-requests|httpclient|okhttp|go-http/i;
const SOURCES = new Set(["instagram", "threads", "x", "facebook", "linkedin", "note", "site", "reddit", "youtube", "tiktok", "other"]);
const DAY = 86400000;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

function same(a, b) {
  // compares without stopping early, so the time taken says nothing about the key
  if (typeof a !== "string" || typeof b !== "string" || !b) return false;
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

function code(n = 6) {
  const bytes = crypto.getRandomValues(new Uint8Array(n));
  return [...bytes].map((b) => ALPHABET[b % ALPHABET.length]).join("");
}

function withUtm(original, source, campaign) {
  const u = new URL(original);
  if ([...u.searchParams.keys()].some((k) => k.startsWith("utm_"))) return u.toString(); // the person set their own
  u.searchParams.set("utm_source", source || "other");
  u.searchParams.set("utm_medium", "social");
  if (campaign) u.searchParams.set("utm_campaign", campaign);
  return u.toString();
}

function device(ua) {
  if (/iPad|Tablet/i.test(ua)) return "tablet";
  if (/Mobi|Android|iPhone|iPod/i.test(ua)) return "mobile";
  return "desktop";
}

async function make(env, rec) {
  let original;
  try {
    original = new URL(String(rec.dest || "")).toString();
  } catch {
    return json({ error: "Not a link" }, 400);
  }
  if (!/^https?:$/.test(new URL(original).protocol) || original.length > 2000) return json({ error: "Not a link" }, 400);
  if (new URL(original).hostname === "go.tany.im") return json({ error: "That is already a short link" }, 400);
  const source = SOURCES.has(rec.source) ? rec.source : "other";
  const campaign = String(rec.campaign || "").slice(0, 60);
  const label = String(rec.label || "").slice(0, 120);
  // the same link for the same place and post is made once, so asking again gives the same short link
  const old = await env.DB.prepare("SELECT code FROM links WHERE original = ? AND source = ? AND campaign = ? AND off = 0").bind(original, source, campaign).first();
  if (old) return json({ code: old.code, short: `https://go.tany.im/${old.code}` });
  for (let i = 0; i < 5; i++) {
    const c = code();
    const r = await env.DB.prepare("INSERT OR IGNORE INTO links (code, dest, original, source, campaign, label, created) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .bind(c, withUtm(original, source, campaign), original, source, campaign, label, Date.now()).run();
    if (r.meta.changes) return json({ code: c, short: `https://go.tany.im/${c}` });
  }
  return json({ error: "Try again" }, 503);
}

async function stats(env, url) {
  const days = Math.min(Math.max(parseInt(url.searchParams.get("days") || "30", 10) || 30, 1), 365);
  const since = Date.now() - days * DAY;
  const links = (await env.DB.prepare("SELECT code, original, source, campaign, label, created FROM links WHERE off = 0 ORDER BY created DESC LIMIT 300").all()).results;
  const per = (await env.DB.prepare("SELECT code, SUM(bot = 0) AS people, SUM(bot) AS bots, MAX(at) AS last FROM clicks WHERE at >= ? GROUP BY code").bind(since).all()).results;
  const byDay = (await env.DB.prepare("SELECT code, CAST(at / 86400000 AS INTEGER) AS day, COUNT(*) AS n FROM clicks WHERE bot = 0 AND at >= ? GROUP BY code, day").bind(since).all()).results;
  const where = (await env.DB.prepare("SELECT code, country, COUNT(*) AS n FROM clicks WHERE bot = 0 AND at >= ? GROUP BY code, country").bind(since).all()).results;
  const from = (await env.DB.prepare("SELECT code, referrer, COUNT(*) AS n FROM clicks WHERE bot = 0 AND at >= ? GROUP BY code, referrer").bind(since).all()).results;
  const kind = (await env.DB.prepare("SELECT code, device, COUNT(*) AS n FROM clicks WHERE bot = 0 AND at >= ? GROUP BY code, device").bind(since).all()).results;
  return json({ days, since, links, per, byDay, where, from, kind });
}

async function api(request, env, url) {
  const key = (request.headers.get("authorization") || "").replace(/^Bearer /, "");
  if (!same(key, env.DESK_KEY)) return json({ error: "forbidden" }, 403);
  const path = url.pathname.slice(5);
  if (request.method === "POST" && path === "links") return make(env, await request.json().catch(() => ({})));
  if (request.method === "GET" && path === "stats") return stats(env, url);
  if (request.method === "POST" && path === "oauth/start") {
    const r = await accounts.start(env, (await request.json().catch(() => ({}))).provider);
    return json(r, r.error ? 400 : 200);
  }
  if (request.method === "GET" && path === "accounts") return json(await accounts.list(env));
  if (request.method === "DELETE" && path.startsWith("accounts/")) return json(await accounts.remove(env, decodeURIComponent(path.slice(9))));
  if (request.method === "DELETE" && path.startsWith("links/")) {
    await env.DB.prepare("UPDATE links SET off = 1 WHERE code = ?").bind(path.slice(6)).run();
    return json({ ok: true });
  }
  return json({ error: "not found" }, 404);
}

const page = (title, body, status) =>
  new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>` +
    `<body style="font-family:system-ui,sans-serif;max-width:32em;margin:15vh auto;padding:0 16px;color:#222"><p>${body}</p>`, { status, headers: { "content-type": "text/html; charset=utf-8" } });

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) return api(request, env, url);
    const gone = url.pathname.match(/^\/oauth\/(threads)-(uninstall|delete)$/);
    if (gone) return json(await accounts.forget(env, gone[1]));
    const back = url.pathname.match(/^\/oauth\/([a-z]+)$/);
    if (back) {
      const r = await accounts.callback(env, back[1], url);
      return page(r.ok ? "Connected" : "Not connected", r.text.replace(/[<>&]/g, ""), r.ok ? 200 : 400);
    }
    const c = url.pathname.slice(1).toLowerCase();
    if (!c) return Response.redirect("https://tany.im/", 302);
    if (!/^[a-z0-9]{4,12}$/.test(c)) return page("Not found", "This link does not exist.", 404);
    const link = await env.DB.prepare("SELECT dest FROM links WHERE code = ? AND off = 0").bind(c).first();
    if (!link) return page("Not found", "This link does not exist or was turned off.", 404);
    if (request.method === "GET" || request.method === "HEAD") {
      const ua = request.headers.get("user-agent") || "";
      let ref = "";
      try { ref = new URL(request.headers.get("referer") || "").hostname; } catch {}
      // counted after the answer is sent, so the jump is not slowed down
      ctx.waitUntil(env.DB.prepare("INSERT INTO clicks (code, at, country, referrer, device, bot) VALUES (?, ?, ?, ?, ?, ?)")
        .bind(c, Date.now(), (request.cf && request.cf.country) || "", ref.slice(0, 120), device(ua), BOTS.test(ua) || request.method === "HEAD" ? 1 : 0).run());
    }
    return new Response(null, { status: 302, headers: { location: link.dest, "cache-control": "no-store", "referrer-policy": "no-referrer-when-downgrade" } });
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(accounts.refreshAll(env));
    ctx.waitUntil(env.DB.prepare("DELETE FROM oauth_states WHERE created < ?").bind(Date.now() - 3600000).run());
  },
};
