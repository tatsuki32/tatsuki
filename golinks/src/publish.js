// Posting for Post Studio: a queue of approved posts, sent at their time by the cron (every 5 minutes) or at once.
// Only what the person approved in Desk is in the queue, with its words as they were approved.
// Pictures wait in KV (MEDIA) under random names for two days, because Instagram and Threads fetch them by URL.

import { open } from "./accounts.js";
import { xCall } from "./xauth.js";

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

// --- videos: R2 (binding VIDEOS), sent from Desk in parts, kept two days; served with Range for Meta's fetchers ---

const VKEY = /^[0-9a-f]{48}\.mp4$/;
const isVideo = (k) => VKEY.test(k);

export async function videoStart(env) {
  if (!env.VIDEOS) return { error: "Videos need Cloudflare R2, which is not turned on for go.tany.im yet" };
  const key = `${rid()}.mp4`;
  const up = await env.VIDEOS.createMultipartUpload(key, { httpMetadata: { contentType: "video/mp4" } });
  return { key, upload: up.uploadId };
}

export async function videoPart(env, key, upload, n, request) {
  if (!env.VIDEOS || !isVideo(key) || !(n >= 1 && n <= 100)) return { error: "bad part" };
  // read the part first: R2 needs to know its length, and a streamed body from the request does not carry it
  const part = await env.VIDEOS.resumeMultipartUpload(key, upload).uploadPart(n, await request.arrayBuffer());
  return { partNumber: part.partNumber, etag: part.etag };
}

export async function videoDone(env, key, upload, parts) {
  if (!env.VIDEOS || !isVideo(key) || !Array.isArray(parts) || !parts.length) return { error: "bad upload" };
  await env.VIDEOS.resumeMultipartUpload(key, upload).complete(parts.map((p) => ({ partNumber: Number(p.partNumber), etag: String(p.etag) })));
  return { key, url: `https://go.tany.im/m/${key}` };
}

export async function getVideo(env, key, request) {
  if (!env.VIDEOS || !isVideo(key)) return null;
  const head = await env.VIDEOS.head(key);
  if (!head || Date.now() - head.uploaded.getTime() > MEDIA_TTL * 1000) return null;
  const obj = await env.VIDEOS.get(key, { range: request.headers, onlyIf: request.headers });
  if (!obj) return null;
  const h = new Headers({ "content-type": "video/mp4", "accept-ranges": "bytes", "cache-control": "public, max-age=3600", etag: obj.httpEtag });
  if (!("body" in obj)) return new Response(null, { status: 304, headers: h });
  if (obj.range && request.headers.get("range")) {
    const start = obj.range.offset ?? 0, len = obj.range.length ?? obj.size - start;
    h.set("content-range", `bytes ${start}-${start + len - 1}/${obj.size}`);
    h.set("content-length", String(len));
    return new Response(obj.body, { status: 206, headers: h });
  }
  h.set("content-length", String(obj.size));
  return new Response(obj.body, { headers: h });
}

export async function cleanVideos(env) {
  if (!env.VIDEOS) return;
  const old = Date.now() - MEDIA_TTL * 1000;
  let cursor;
  do {
    const l = await env.VIDEOS.list({ cursor, limit: 500 });
    const gone = l.objects.filter((o) => o.uploaded.getTime() < old).map((o) => o.key);
    if (gone.length) await env.VIDEOS.delete(gone);
    cursor = l.truncated ? l.cursor : undefined;
  } while (cursor);
}

// --- the queue ---

export async function add(env, rec) {
  const acc = await env.DB.prepare("SELECT id, provider FROM accounts WHERE id = ?").bind(String(rec.account || "")).first();
  if (!acc) return { error: "That account is not connected" };
  const kind = ["post", "story"].includes(rec.kind) ? rec.kind : "post";
  if (acc.provider === "threads" && kind === "story") return { error: "Threads has no stories" };
  if (acc.provider === "facebook" && kind === "story" && !(Array.isArray(rec.media) && rec.media.length)) return { error: "A story needs a picture" };
  const media = (Array.isArray(rec.media) ? rec.media : []).filter((k) => /^[0-9a-f]{48}\.(jpg|png|mp4)$/.test(k)).slice(0, 10);
  if (media.some(isVideo) && media.length > 1) return { error: "One video per post" };
  if (acc.provider === "facebook" && kind === "story" && media.some(isVideo)) return { error: "Video stories on a Facebook Page are not supported yet" };
  if (acc.provider === "instagram" && !media.length) return { error: "Instagram needs a picture" };
  if (["x", "linkedin"].includes(acc.provider) && kind === "story") return { error: "No stories there" };
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
    "SELECT id, account, kind, text, media, at, status, tries, link, error, created, post, target, done, creation FROM queue WHERE created > ? ORDER BY at DESC LIMIT 300"
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

// A video is prepared by Meta for minutes, longer than one run may wait. So it goes in two steps: the first run makes
// the container and marks the row 'processing'; each later run (every 5 minutes) asks once and publishes when ready.
async function videoContainer(item, token) {
  const [provider, me] = item.account.split(":");
  const u = `https://go.tany.im/m/${item.media[0]}`;
  if (provider === "instagram") {
    const f = item.kind === "story" ? { media_type: "STORIES", video_url: u } : { media_type: "REELS", video_url: u, caption: item.text, share_to_feed: "true" };
    return (await post(`${GRAPH}/${me}/media`, { ...f, access_token: token })).id;
  }
  return (await post(`${THREADS}/${me}/threads`, { media_type: "VIDEO", video_url: u, text: item.text, access_token: token })).id;
}

async function videoFinish(item, token) {
  // returns null while Meta is still preparing it
  const [provider, me] = item.account.split(":");
  const base = provider === "instagram" ? GRAPH : THREADS;
  const field = provider === "instagram" ? "status_code" : "status";
  const v = await call(`${base}/${item.creation}?fields=${field}${provider === "threads" ? ",error_message" : ""}&access_token=${enc(token)}`);
  if (["ERROR", "EXPIRED"].includes(v[field])) throw new Error(`The video was not accepted (${v[field]}${v.error_message ? ": " + v.error_message : ""})`);
  if (v[field] !== "FINISHED") return null;
  const id = (await post(`${base}/${me}/${provider === "instagram" ? "media_publish" : "threads_publish"}`, { creation_id: item.creation, access_token: token })).id;
  const link = item.kind === "story" ? "" : await call(`${base}/${id}?fields=permalink&access_token=${enc(token)}`).then((x) => x.permalink).catch(() => "");
  return { id, link };
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

// a Facebook Page: one picture goes up as a photo post; several are uploaded unpublished and attached to one post.
// A story is one Page photo story per picture.
async function facebook(env, item, token) {
  const page = item.account.split(":")[1];
  const urls = item.media.map((k) => `https://go.tany.im/m/${k}`);
  const unpublished = async (u) => (await post(`${GRAPH}/${page}/photos`, { url: u, published: "false", access_token: token })).id;
  let id;
  if (item.media.some(isVideo)) {
    // a Page takes the video by URL and prepares it on its own; the post appears when it is ready
    id = (await post(`${GRAPH}/${page}/videos`, { file_url: urls[0], description: item.text, published: "true", access_token: token })).id;
    const link = await call(`${GRAPH}/${id}?fields=permalink_url&access_token=${enc(token)}`).then((v) => v.permalink_url ? (v.permalink_url.startsWith("http") ? v.permalink_url : "https://www.facebook.com" + v.permalink_url) : "").catch(() => "");
    return { id, link };
  }
  if (item.kind === "story") {
    const ids = [];
    for (const u of urls) ids.push((await post(`${GRAPH}/${page}/photo_stories`, { photo_id: await unpublished(u), access_token: token })).post_id);
    return { id: ids[0], link: "" };
  }
  if (!urls.length) {
    id = (await post(`${GRAPH}/${page}/feed`, { message: item.text, access_token: token })).id;
  } else if (urls.length === 1) {
    const r = await post(`${GRAPH}/${page}/photos`, { url: urls[0], caption: item.text, published: "true", access_token: token });
    id = r.post_id || r.id;
  } else {
    const fields = { message: item.text, access_token: token };
    let i = 0;
    for (const u of urls) fields[`attached_media[${i++}]`] = JSON.stringify({ media_fbid: await unpublished(u) });
    id = (await post(`${GRAPH}/${page}/feed`, fields)).id;
  }
  const link = await call(`${GRAPH}/${id}?fields=permalink_url&access_token=${enc(token)}`).then((v) => v.permalink_url).catch(() => "");
  return { id, link };
}

// LinkedIn: the person's profile. Pictures go up first (images API), then one post with them.
const LI = "https://api.linkedin.com/rest";
const LI_VERSION = "202609";
// LinkedIn's "little text": these characters mean something there, so they are written with a backslash
const liText = (t) => t.replace(/[\\|{}@\[\]()<>#*_~]/g, (c) => "\\" + c);

async function linkedin(env, item, token) {
  if (item.media.some(isVideo)) throw new Error("Videos on LinkedIn are not supported yet");
  const author = `urn:li:person:${item.account.split(":")[1]}`;
  const h = { authorization: `Bearer ${token}`, "LinkedIn-Version": LI_VERSION, "X-Restli-Protocol-Version": "2.0.0", "content-type": "application/json" };
  const images = [];
  for (const k of item.media) {
    const init = await fetch(`${LI}/images?action=initializeUpload`, { method: "POST", headers: h, body: JSON.stringify({ initializeUploadRequest: { owner: author } }) });
    const v = await init.json().catch(() => ({}));
    if (!init.ok || !v.value) throw new Error(`LinkedIn would not take the picture (${init.status}${v.message ? ": " + v.message : ""})`);
    const bytes = await env.MEDIA.get(k, { type: "arrayBuffer" });
    if (!bytes) throw new Error("The picture has expired; approve the post again");
    const up = await fetch(v.value.uploadUrl, { method: "PUT", headers: { authorization: `Bearer ${token}` }, body: bytes });
    if (!up.ok) throw new Error(`The picture did not upload to LinkedIn (${up.status})`);
    images.push(v.value.image);
  }
  const body = {
    author, commentary: liText(item.text), visibility: "PUBLIC", lifecycleState: "PUBLISHED", isReshareDisabledByAuthor: false,
    distribution: { feedDistribution: "MAIN_FEED", targetEntities: [], thirdPartyDistributionChannels: [] },
  };
  if (images.length === 1) body.content = { media: { id: images[0] } };
  if (images.length > 1) body.content = { multiImage: { images: images.map((id) => ({ id })) } };
  const r = await fetch(`${LI}/posts`, { method: "POST", headers: h, body: JSON.stringify(body) });
  if (!r.ok) {
    const v = await r.json().catch(() => ({}));
    throw new Error(`LinkedIn said no (${r.status}${v.message ? ": " + v.message : ""})`);
  }
  const id = r.headers.get("x-restli-id") || "";
  return { id, link: id ? `https://www.linkedin.com/feed/update/${id}/` : "" };
}

// X: pictures through the v2 media upload, then the post. Each post is paid for (pay-per-use).
async function x(env, item) {
  if (item.media.some(isVideo)) throw new Error("Videos on X are not supported yet");
  const ids = [];
  for (const k of item.media.slice(0, 4)) {
    const bytes = await env.MEDIA.get(k, { type: "arrayBuffer" });
    if (!bytes) throw new Error("The picture has expired; approve the post again");
    const fd = new FormData();
    fd.append("media", new Blob([bytes], { type: k.endsWith(".png") ? "image/png" : "image/jpeg" }), k);
    fd.append("media_category", "tweet_image");
    const v = await xCall(env, "POST", "https://api.x.com/2/media/upload", fd);
    const id = (v.data && v.data.id) || v.media_id_string;
    if (!id) throw new Error("X would not take the picture" + (v.title ? `: ${v.title}${v.detail ? " — " + v.detail : ""}` : ""));
    ids.push(id);
  }
  const v = await xCall(env, "POST", "https://api.x.com/2/tweets", ids.length ? { text: item.text, media: { media_ids: ids } } : { text: item.text });
  if (!v.data) throw new Error("X said no" + (v.title ? `: ${v.title}${v.detail ? " — " + v.detail : ""}` : ""));
  const me = item.account.split(":")[1];
  return { id: v.data.id, link: `https://x.com/i/web/status/${v.data.id}` || `https://x.com/${me}` };
}

const PUBLISH = { instagram, threads, facebook, linkedin, x };

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
    if (!PUBLISH[acc.provider]) throw new Error(`Posting to ${acc.provider} is not ready yet`);
    if (item.media.some(isVideo) && acc.provider !== "facebook") {
      const creation = await videoContainer(item, token);
      await env.DB.prepare("UPDATE queue SET status = 'processing', creation = ?, error = '' WHERE id = ?").bind(creation, id).run();
      return { ok: true, processing: true };
    }
    const r = await PUBLISH[acc.provider](env, item, token);
    await env.DB.prepare("UPDATE queue SET status = 'posted', link = ?, error = '', done = ? WHERE id = ?").bind(r.link || "", Date.now(), id).run();
    return { ok: true, link: r.link || "" };
  } catch (e) {
    // one more try at the next run, then it stops and waits for the person
    const again = row.tries < 2;
    await env.DB.prepare("UPDATE queue SET status = ?, error = ? WHERE id = ?").bind(again ? "waiting" : "failed", String(e.message).slice(0, 400), id).run();
    return { error: e.message };
  }
}

// videos Meta is still preparing: asked once per run; given up after 40 minutes
async function processing(env) {
  const rows = (await env.DB.prepare("SELECT * FROM queue WHERE status = 'processing' ORDER BY at LIMIT 10").all()).results;
  for (const row of rows) {
    const item = { ...row, media: JSON.parse(row.media || "[]") };
    try {
      const acc = await env.DB.prepare("SELECT provider, token FROM accounts WHERE id = ?").bind(item.account).first();
      if (!acc) throw new Error("The account is no longer connected");
      const r = await videoFinish(item, await open(env, acc.token));
      if (r) await env.DB.prepare("UPDATE queue SET status = 'posted', link = ?, error = '', done = ? WHERE id = ?").bind(r.link || "", Date.now(), row.id).run();
      else if (Date.now() - Math.max(row.at, row.created) > 40 * 60000) throw new Error("Meta took too long to prepare the video");
    } catch (e) {
      await env.DB.prepare("UPDATE queue SET status = 'failed', error = ? WHERE id = ?").bind(String(e.message).slice(0, 400), row.id).run();
    }
  }
}

export async function due(env) {
  await processing(env);
  const rows = (await env.DB.prepare("SELECT id FROM queue WHERE status = 'waiting' AND at <= ? ORDER BY at LIMIT 10").bind(Date.now()).all()).results;
  for (const r of rows) await send(env, r.id);
  // a post stuck in 'sending' for 20 minutes (the run was cut off) is put back once
  await env.DB.prepare("UPDATE queue SET status = 'waiting' WHERE status = 'sending' AND tries < 2 AND created < ? AND at < ?").bind(Date.now() - 1200000, Date.now() - 1200000).run();
}
