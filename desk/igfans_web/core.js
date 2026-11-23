/* Follower analysis, pure and on its own: no Desk, no network, no storage. The Desk screen uses it now; a public page
   that works only inside the visitor's browser can use the same file later.
   Input is Instagram's official export (JSON). Nothing here talks to Instagram. */

export const DAY = 86400000;
const NAME = /^[a-z0-9._]{1,30}$/;
const MAX_ENTRY = 200 * 1024 * 1024; // one file inside the ZIP, unpacked
const WANTED = /(?:^|\/)(followers(?:_(\d+))?|following(?:_(\d+))?)\.(json|html)$/i;

const has = Object.hasOwn; // an ID can be "constructor"

export class ExportError extends Error {}

/* ---------- reading the export ---------- */

export function cleanName(v) {
  const s = String(v == null ? "" : v).trim().replace(/^@/, "").toLowerCase();
  return NAME.test(s) && s !== "__proto__" ? s : "";
}

// one row of the export. followers: string_list_data[0].value; newer following files: the name is in title or only in href
function row(x) {
  const d = (x && Array.isArray(x.string_list_data) && x.string_list_data[0]) || {};
  let name = cleanName(d.value) || cleanName(x && x.title);
  if (!name && typeof d.href === "string") {
    const m = /instagram\.com\/(?:_u\/)?([^/?#]+)/i.exec(d.href);
    if (m) name = cleanName(m[1]);
  }
  const ts = Number(d.timestamp);
  return name ? [name, Number.isFinite(ts) && ts > 1e9 && ts < 1e11 ? Math.round(ts) * 1000 : 0] : null;
}

export function parseList(json, kind) {
  const list = Array.isArray(json) ? json : json && json[kind === "followers" ? "relationships_followers" : "relationships_following"];
  if (!Array.isArray(list)) throw new ExportError(`The ${kind} file is not in the shape Instagram exports`);
  const out = {};
  let bad = 0;
  for (const x of list) {
    const r = row(x);
    if (!r) bad++;
    else if (!has(out, r[0]) || r[1]) out[r[0]] = r[1];
  }
  return { people: out, bad };
}

// files: [{name, text}] → {followers, following, warnings}
export function readFiles(files) {
  const got = { followers: {}, following: {} }, seen = { followers: false, following: false }, warnings = [], parts = [];
  let html = false, bad = 0;
  for (const f of files) {
    const m = WANTED.exec(f.name);
    if (!m) continue;
    if (m[4].toLowerCase() === "html") { html = true; continue; }
    const kind = m[1].toLowerCase().startsWith("followers") ? "followers" : "following";
    let json;
    try { json = JSON.parse(f.text); } catch (e) { throw new ExportError(`${f.name} could not be read. Please export again`); }
    const r = parseList(json, kind);
    Object.assign(got[kind], r.people);
    bad += r.bad;
    seen[kind] = true;
    if (kind === "followers") parts.push(Number(m[2] || 1));
  }
  if (!seen.followers && html) throw new ExportError("This export is HTML. Please export again and choose JSON");
  if (!seen.followers) throw new ExportError("No followers file was found. In the export, pick \"Followers and following\" and JSON");
  if (!Object.keys(got.followers).length) throw new ExportError("The followers file is empty");
  const top = Math.max(...parts);
  if (new Set(parts).size !== top) throw new ExportError(`A followers file is missing (found ${parts.sort((a, b) => a - b).join(", ")}). Drop the whole ZIP`);
  if (!seen.following) warnings.push("The export has no following list, so mutuals cannot be worked out");
  if (bad) warnings.push(`${bad} rows had no readable ID and were left out`);
  if (!Object.values(got.followers).some(Boolean)) warnings.push("The export has no follow dates");
  return { followers: got.followers, following: got.following, hasFollowing: seen.following, warnings };
}

// the account an export belongs to, from Instagram's own file name: instagram-<id>-2026-11-24-xxxx.zip
export function accountFromName(name) {
  const m = /^instagram-([a-z0-9._]{1,30})-\d{4}-\d{2}-\d{2}/i.exec(String(name || ""));
  return m ? cleanName(m[1]) : "";
}

// a ZIP is read through its central directory, and only the follower files are unpacked: a large export stays cheap
export async function readZip(blob) {
  const bytes = async (a, b) => new Uint8Array(await blob.slice(a, b).arrayBuffer());
  const tailAt = Math.max(0, blob.size - 65557), tail = await bytes(tailAt, blob.size);
  const tv = new DataView(tail.buffer);
  let end = -1;
  for (let i = tail.length - 22; i >= 0; i--) if (tv.getUint32(i, true) === 0x06054b50) { end = i; break; }
  if (end < 0) throw new ExportError("This is not a ZIP file");
  const count = tv.getUint16(end + 10, true), size = tv.getUint32(end + 12, true), at = tv.getUint32(end + 16, true);
  if (count === 0xffff || at === 0xffffffff) throw new ExportError("This ZIP is too large to read here. Export only \"Followers and following\"");
  const dir = await bytes(at, at + size), dv = new DataView(dir.buffer), files = [];
  for (let i = 0, p = 0; i < count && p + 46 <= dir.length; i++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const flags = dv.getUint16(p + 8, true), method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
    const nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true), local = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(dir.subarray(p + 46, p + 46 + nl));
    p += 46 + nl + xl + cl;
    const m = WANTED.exec(name);
    if (!m) continue;
    if (m[4].toLowerCase() === "html") { files.push({ name, text: "" }); continue; }
    if (flags & 1) throw new ExportError("This ZIP has a password");
    if (usize > MAX_ENTRY) throw new ExportError(`${name} is too large`);
    const head = await bytes(local, local + 30), hv = new DataView(head.buffer);
    if (head.length < 30 || hv.getUint32(0, true) !== 0x04034b50) throw new ExportError("This ZIP is damaged");
    const from = local + 30 + hv.getUint16(26, true) + hv.getUint16(28, true);
    const part = blob.slice(from, from + csize);
    let text;
    if (method === 0) text = await part.text();
    else if (method === 8) text = await inflate(part, name);
    else throw new ExportError(`${name} uses a ZIP method this page cannot read`);
    files.push({ name, text });
  }
  return files;
}

async function inflate(part, name) {
  const chunks = [];
  let n = 0;
  try {
    const r = part.stream().pipeThrough(new DecompressionStream("deflate-raw")).getReader();
    for (;;) {
      const { done, value } = await r.read();
      if (done) break;
      n += value.length;
      if (n > MAX_ENTRY) throw new ExportError(`${name} is too large`);
      chunks.push(value);
    }
  } catch (e) {
    if (e instanceof ExportError) throw e;
    throw new ExportError("This ZIP is damaged");
  }
  return new Blob(chunks).text();
}

// what the person dropped (one ZIP, or the JSON files)
export async function readDrop(list) {
  const files = [...list];
  const zips = files.filter((f) => /\.zip$/i.test(f.name));
  if (zips.length > 1) throw new ExportError("Drop one ZIP at a time");
  const read = zips.length ? await readZip(zips[0]) : await Promise.all(files.filter((f) => WANTED.test(f.name)).map(async (f) => ({ name: f.name, text: await f.text() })));
  return { ...readFiles(read), account: zips.length ? accountFromName(zips[0].name) : "" };
}

// the same lists give the same mark, so one export counts once however often it is dropped
export function fingerprint(followers, following) {
  const one = (o) => Object.keys(o).sort().map((k) => `${k}:${o[k]}`).join("\n");
  const s = one(followers) + "\n--\n" + one(following);
  let a = 0xdeadbeef, b = 0x41c6ce57;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    a = Math.imul(a ^ c, 2654435761);
    b = Math.imul(b ^ c, 1597334677);
  }
  a = Math.imul(a ^ (a >>> 16), 2246822507) ^ Math.imul(b ^ (b >>> 13), 3266489909);
  b = Math.imul(b ^ (b >>> 16), 2246822507) ^ Math.imul(a ^ (a >>> 13), 3266489909);
  return (b >>> 0).toString(16).padStart(8, "0") + (a >>> 0).toString(16).padStart(8, "0");
}

/* ---------- comparing ---------- */

const keys = (o) => Object.keys(o || {});
const oldest = (o) => keys(o).reduce((m, k) => (o[k] && (!m || o[k] < m) ? o[k] : m), 0);

// looks like the export did not cover "All time": far fewer people, and the oldest follow date jumped forward.
// known = the count on the profile, when it is known. Returns the reason, or "".
export function looksPartial(now, prev, known) {
  const n = keys(now).length, p = keys(prev).length;
  if (prev && p) {
    const lost = keys(prev).filter((k) => !has(now, k)).length, a = oldest(prev), b = oldest(now);
    if (lost >= 20 && lost > p * 0.1 && a && b && b - a > 30 * DAY) return `${lost} people are gone and the oldest follow date moved from ${day(a)} to ${day(b)}. The date range may not be "All time"`;
    if (lost >= 20 && lost > p * 0.4) return `${lost} of ${p} people are gone at once. The export may be incomplete`;
  }
  if (known && known >= 50 && n < known * 0.9) return `The export has ${n} people but the profile says ${known}. The date range may not be "All time"`;
  return "";
}

const day = (t) => new Date(t).toISOString().slice(0, 10);

// people who came, people who are gone, and people whose ID changed (gone and new with one and the same follow time)
export function diff(prev, now) {
  let added = keys(now).filter((k) => !has(prev, k)), gone = keys(prev).filter((k) => !has(now, k));
  const at = (list, o) => { const m = new Map(); for (const k of list) if (o[k]) m.set(o[k], [...(m.get(o[k]) || []), k]); return m; };
  const A = at(added, now), G = at(gone, prev), renamed = [];
  for (const [t, g] of G) {
    const a = A.get(t);
    if (a && a.length === 1 && g.length === 1) renamed.push({ from: g[0], to: a[0] });
  }
  const from = new Set(renamed.map((r) => r.from)), to = new Set(renamed.map((r) => r.to));
  added = added.filter((k) => !to.has(k));
  gone = gone.filter((k) => !from.has(k));
  return { added, gone, renamed };
}

// imports oldest first; a partial one is never compared
export function history(imports) {
  const list = (imports || []).filter((x) => !x.partial).slice().sort((a, b) => a.at - b.at), out = [];
  for (let i = 1; i < list.length; i++) out.push({ from: list[i - 1].at, to: list[i].at, id: list[i].id, ...diff(list[i - 1].followers, list[i].followers) });
  return out.reverse();
}

/* ---------- fans ---------- */

export const WEIGHTS = { comment: 3, dm: 2, tag: 4 };
export const HALF = 90; // days until a point is worth half
export const WINDOW = 365; // days looked at

// events: [{u, k: comment|dm|tag, at}] → Map(u → {score, comment, dm, tag, last})
export function scores(events, now, weights) {
  const w = { ...WEIGHTS, ...(weights || {}) }, m = new Map();
  for (const e of events || []) {
    const age = (now - e.at) / DAY, u = cleanName(e.u);
    if (!u || !has(WEIGHTS, e.k) || !(age <= WINDOW)) continue;
    const x = m.get(u) || { score: 0, comment: 0, dm: 0, tag: 0, last: 0 };
    x[e.k]++;
    x.score += (Number(w[e.k]) || 0) * Math.pow(0.5, Math.max(0, age) / HALF);
    if (e.at > x.last) x.last = e.at;
    m.set(u, x);
  }
  return m;
}

/* ---------- the table ---------- */

// imports, events, profiles {u: {found, name, bio, followers, …}} → one row per person
export function table({ imports, events, profiles, now, weights, own }) {
  const good = (imports || []).filter((x) => !x.partial).slice().sort((a, b) => a.at - b.at);
  const latest = good[good.length - 1] || null;
  const F = latest ? latest.followers : {}, G = latest ? latest.following : {}, s = scores(events, now, weights), P = profiles || {};
  const names = new Set([...keys(F), ...keys(G), ...s.keys()]);
  names.delete(cleanName(own));
  const rows = [];
  for (const u of names) {
    const f = has(F, u), g = has(G, u), x = s.get(u), pr = has(P, u) ? P[u] : null, p = pr && pr.found ? pr : null;
    rows.push({
      u, follower: f, following: g, rel: !latest ? "unknown" : f && g ? "mutual" : f ? "fan" : g ? "idol" : "none",
      since: f ? F[u] || 0 : 0, score: x ? Math.round(x.score * 10) / 10 : 0, comment: x ? x.comment : 0, dm: x ? x.dm : 0, tag: x ? x.tag : 0, last: x ? x.last : 0,
      name: p ? p.name || "" : "", bio: p ? p.bio || "" : "", followers: p && p.followers != null ? p.followers : null, posts: p && p.posts != null ? p.posts : null,
      web: p ? p.web || "" : "", checked: pr ? pr.at || 0 : 0, found: !!p,
    });
  }
  return { latest, hasFollowing: !!(latest && latest.hasFollowing !== false), rows };
}

export const TABS = {
  fans: (r) => r.score > 0, all: (r) => r.follower, mutual: (r) => r.rel === "mutual",
  notback: (r) => r.rel === "idol", youdont: (r) => r.rel === "fan",
};
const BY = {
  score: (a, b) => b.score - a.score || b.last - a.last, newest: (a, b) => b.since - a.since, oldest: (a, b) => (a.since || Infinity) - (b.since || Infinity),
  followers: (a, b) => (b.followers ?? -1) - (a.followers ?? -1), name: () => 0,
};
export function pick(rows, tab, sort, q) {
  const s = String(q || "").trim().replace(/^@/, "").toLowerCase();
  return rows.filter((r) => TABS[tab](r) && (!s || r.u.includes(s) || r.name.toLowerCase().includes(s) || r.bio.toLowerCase().includes(s)))
    .sort((a, b) => BY[sort](a, b) || (a.u < b.u ? -1 : 1));
}

/* ---------- CSV ---------- */

const REL = { mutual: "Mutual", fan: "Follows you", idol: "You follow", none: "Neither", unknown: "Unknown" };
export function csv(rows, hasFollowing = true) {
  // a cell that starts like a formula is kept as text: names and bios come from other people
  const q = (v) => { let s = v == null ? "" : String(v); if (typeof v !== "number" && /^\s*[=+\-@\t\r]/.test(s)) s = "'" + s; return `"${s.replace(/"/g, '""')}"`; };
  const head = ["ID", "Profile", "Name", "Follows you", "You follow", "Relation", "Followed you on (UTC)", "Fan score", "Comments", "DMs", "Tags", "Last reaction (UTC)", "Followers", "Posts", "Bio", "Website"];
  const d = (t) => (t ? day(t) : "");
  return "﻿" + [head, ...rows.map((r) => [r.u, `https://www.instagram.com/${r.u}/`, r.name, r.follower ? "Yes" : "No", hasFollowing ? (r.following ? "Yes" : "No") : "", hasFollowing ? REL[r.rel] : "",
    d(r.since), r.score, r.comment, r.dm, r.tag, d(r.last), r.followers, r.posts, r.bio, r.web])].map((x) => x.map(q).join(",")).join("\r\n");
}
