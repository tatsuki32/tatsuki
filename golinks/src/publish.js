// Posting for Post Studio: a queue of approved posts, sent at their time by the cron (every 5 minutes) or at once.
// Only what the person approved in Desk is in the queue, with its words as they were approved.
// Pictures wait in KV (MEDIA) under random names for two days, because Instagram and Threads fetch them by URL.

import { open } from "./accounts.js";

const GRAPH = "https://graph.facebook.com/v23.0";
const THREADS = "https://graph.threads.net/v1.0";
const MEDIA_TTL = 2 * 86400;
const enc = encodeURIComponent;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rid(n = 24) {
  return [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// --- pictures ---

export async function putMedia(env, request) {
  const type = request.headers.get("content-type") || "";
  if (!/^image\/(jpeg|png)$/.test(type)) return { error: "JPEG or PNG only" };
  const body = await request.arrayBuffer();
  if (body.byteLength > 20 * 1024 * 1024) return { error: "Too large" };
  const key = `${rid()}.${type.endsWith("png") ? "png" : "jpg"}`;
  await env.MEDIA.put(key, body, { expirationTtl: MEDIA_TTL, metadata: { type } });
  return { key, url: `https://go.tany.im/m/${key}` };
}

export async function getMedia(env, key) {
  if (!/^[0-9a-f]{48}\.(jpg|png)$/.test(key)) return null;
  const v = await env.MEDIA.getWithMetadata(key, { type: "arrayBuffer" });
  if (!v.value) return null;
  return new Response(v.value, { headers: { "content-type": (v.metadata && v.metadata.type) || "image/jpeg", "cache-control": "public, max-age=3600" } });
}

// --- the queue ---

export async function add(env, rec) {
  const acc = await env.DB.prepare("SELECT id, provider FROM accounts WHERE id = ?").bind(String(rec.account || "")).first();
  if (!acc) return { error: "That account is not connected" };
  const kind = ["post", "story"].includes(rec.kind) ? rec.kind : "post";
  if (acc.provider === "threads" && kind === "story") return { error: "Threads has no stories" };
  const media = (Array.isArray(rec.media) ? rec.media : []).filter((k) => /^[0-9a-f]{48}\.(jpg|png)$/.test(k)).slice(0, 10);
  if (acc.provider === "instagram" && !media.length) return { error: "Instagram needs a picture" };
  const text = String(rec.text || "").slice(0, 10000);
  const at = Number(rec.at) > 0 ? Number(rec.at) : Date.now();
  const id = `q-${rid(8)}`;
  await env.DB.prepare(
    "INSERT INTO queue (id, account, kind, text, media, at, status, tries, created, post, target) VALUES (?, ?, ?, ?, ?, ?, 'waiting', 0, ?, ?, ?)"
  ).bind(id, acc.id, kind, text, JSON.stringify(media), at, Date.now(), String(rec.post || "").slice(0, 40), String(rec.target || "").slice(0, 40)).run();
  return { id, at };
}

export async function list(env, url) {
  const since = Date.now() - 30 * 86400000;
  const rows = (await env.DB.prepare(
    "SELECT id, account, kind, text, media, at, status, tries, link, error, created, post, target, done FROM queue WHERE created > ? ORDER BY at DESC LIMIT 300"
  ).bind(since).all()).results;
  return { items: rows.map((r) => ({ ...r, media: JSON.parse(r.media || "[]") })) };
}

export async function cancel(env, id) {
  const r = await env.DB.prepare("UPDATE queue SET status = 'cancelled' WHERE id = ? AND status = 'waiting'").bind(id).run();
  return r.meta.changes ? { ok: true } : { error: "Already sent or not found" };
}

export async function move(env, id, at) {
  if (!(Number(at) > 0)) return { error: "No time" };
  const r = await env.DB.prepare("UPDATE queue SET at = ? WHERE id = ? AND status = 'waiting'").bind(Number(at), id).run();
  return r.meta.changes ? { ok: true } : { error: "Already sent or not found" };
}

// --- sending ---

async function call(url, init) {
  const r = await fetch(url, init);
  const v = await r.json().catch(() => ({}));
  if (!r.ok || v.error) throw new Error((v.error && (v.error.error_user_msg || v.error.message)) || `answered ${r.status}`);
  return v;
}

const post = (url, fields) => call(url, { method: "POST", body: new URLSearchParams(fields) });

async function waitReady(url, field, ok, bad) {
  // Meta prepares the picture before it can be published; this asks every 3 seconds for up to a minute
  for (let i = 0; i < 20; i++) {
    const v = await call(url);
    if (v[field] === ok) return;
    if (bad.includes(v[field])) throw new Error(`The picture was not accepted (${v[field]}${v.error_message ? ": " + v.error_message : ""})`);
    await sleep(3000);
  }
  throw new Error("The picture took too long to prepare");
}

async function instagram(env, item, token) {
  const ig = item.account.split(":")[1];
  const urls = item.media.map((k) => `https://go.tany.im/m/${k}`);
  const ready = (id) => waitReady(`${GRAPH}/${id}?fields=status_code&access_token=${enc(token)}`, "status_code", "FINISHED", ["ERROR", "EXPIRED"]);
  let creation;
  if (item.kind === "story") {
    // one story per picture; the first gets published here, the rest follow in order
    const ids = [];
    for (const u of urls) {
      const c = await post(`${GRAPH}/${ig}/media`, { image_url: u, media_type: "STORIES", access_token: token });
      await ready(c.id);
      ids.push((await post(`${GRAPH}/${ig}/media_publish`, { creation_id: c.id, access_token: token })).id);
    }
    return { id: ids[0] };
  }
  if (urls.length === 1) {
    creation = (await post(`${GRAPH}/${ig}/media`, { image_url: urls[0], caption: item.text, access_token: token })).id;
  } else {
    const kids = [];
    for (const u of urls) kids.push((await post(`${GRAPH}/${ig}/media`, { image_url: u, is_carousel_item: "true", access_token: token })).id);
    for (const k of kids) await ready(k);
    creation = (await post(`${GRAPH}/${ig}/media`, { media_type: "CAROUSEL", children: kids.join(","), caption: item.text, access_token: token })).id;
  }
  await ready(creation);
  const id = (await post(`${GRAPH}/${ig}/media_publish`, { creation_id: creation, access_token: token })).id;
  const link = await call(`${GRAPH}/${id}?fields=permalink&access_token=${enc(token)}`).then((v) => v.permalink).catch(() => "");
  return { id, link };
}

async function threads(env, item, token) {
  const me = item.account.split(":")[1];
  const urls = item.media.map((k) => `https://go.tany.im/m/${k}`);
  const ready = (id) => waitReady(`${THREADS}/${id}?fields=status,error_message&access_token=${enc(token)}`, "status", "FINISHED", ["ERROR", "EXPIRED"]);
  let creation;
  if (!urls.length) {
    creation = (await post(`${THREADS}/${me}/threads`, { media_type: "TEXT", text: item.text, access_token: token })).id;
  } else if (urls.length === 1) {
    creation = (await post(`${THREADS}/${me}/threads`, { media_type: "IMAGE", image_url: urls[0], text: item.text, access_token: token })).id;
  } else {
    const kids = [];
    for (const u of urls) kids.push((await post(`${THREADS}/${me}/threads`, { media_type: "IMAGE", image_url: u, is_carousel_item: "true", access_token: token })).id);
    for (const k of kids) await ready(k);
    creation = (await post(`${THREADS}/${me}/threads`, { media_type: "CAROUSEL", children: kids.join(","), text: item.text, access_token: token })).id;
  }
  await ready(creation);
  const id = (await post(`${THREADS}/${me}/threads_publish`, { creation_id: creation, access_token: token })).id;
  const link = await call(`${THREADS}/${id}?fields=permalink&access_token=${enc(token)}`).then((v) => v.permalink).catch(() => "");
  return { id, link };
}

export async function send(env, id) {
  // the row is claimed first, so two runs never send the same post twice
  const claim = await env.DB.prepare("UPDATE queue SET status = 'sending', tries = tries + 1 WHERE id = ? AND status = 'waiting'").bind(id).run();
  if (!claim.meta.changes) return { error: "Already sent or not waiting" };
  const row = await env.DB.prepare("SELECT * FROM queue WHERE id = ?").bind(id).first();
  const item = { ...row, media: JSON.parse(row.media || "[]") };
  try {
    const acc = await env.DB.prepare("SELECT provider, token FROM accounts WHERE id = ?").bind(item.account).first();
    if (!acc) throw new Error("The account is no longer connected");
    const token = await open(env, acc.token);
    const r = acc.provider === "instagram" ? await instagram(env, item, token) : await threads(env, item, token);
    await env.DB.prepare("UPDATE queue SET status = 'posted', link = ?, error = '', done = ? WHERE id = ?").bind(r.link || "", Date.now(), id).run();
    return { ok: true, link: r.link || "" };
  } catch (e) {
    // one more try at the next run, then it stops and waits for the person
    const again = row.tries < 2;
    await env.DB.prepare("UPDATE queue SET status = ?, error = ? WHERE id = ?").bind(again ? "waiting" : "failed", String(e.message).slice(0, 400), id).run();
    return { error: e.message };
  }
}

export async function due(env) {
  const rows = (await env.DB.prepare("SELECT id FROM queue WHERE status = 'waiting' AND at <= ? ORDER BY at LIMIT 10").bind(Date.now()).all()).results;
  for (const r of rows) await send(env, r.id);
  // a post stuck in 'sending' for 20 minutes (the run was cut off) is put back once
  await env.DB.prepare("UPDATE queue SET status = 'waiting' WHERE status = 'sending' AND tries < 2 AND created < ? AND at < ?").bind(Date.now() - 1200000, Date.now() - 1200000).run();
}
