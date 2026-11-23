// Reading the person's own Instagram account for the Desk's Followers screen (desk/igfans.py): who commented, who wrote,
// who tagged, the audience counts, and the public numbers of one professional account looked up by ID.
// Read only. The text of comments and messages is dropped here and never leaves this Worker; tokens neither.
// Every answer carries what Meta says about the rate limit, so the Desk can measure what one account costs.

import { open } from "./accounts.js";

const GRAPH = "https://graph.facebook.com/v23.0";
const enc = encodeURIComponent;

export class MetaError extends Error {
  constructor(message, code, sub, meter) { super(message); this.code = code; this.sub = sub; this.meter = meter; }
}

const parse = (h) => { try { return h ? JSON.parse(h) : null; } catch { return null; } };

// the highest percentage in each header: app = the whole app's hour, buc = this Instagram account; wait = minutes until Meta answers again
function read(meter, res) {
  meter.calls++;
  const app = parse(res.headers.get("x-app-usage"));
  if (app) meter.app = Math.max(meter.app || 0, ...["call_count", "total_cputime", "total_time"].map((k) => Number(app[k]) || 0));
  const buc = parse(res.headers.get("x-business-use-case-usage"));
  if (buc) for (const list of Object.values(buc)) for (const x of Array.isArray(list) ? list : []) {
    meter.buc = Math.max(meter.buc || 0, ...["call_count", "total_cputime", "total_time"].map((k) => Number(x[k]) || 0));
    meter.wait = Math.max(meter.wait || 0, Number(x.estimated_time_to_regain_access) || 0);
  }
}

async function get(path, params, token, meter) {
  const u = new URL(`${GRAPH}/${path}`);
  for (const [k, v] of Object.entries(params)) if (v !== "" && v != null) u.searchParams.set(k, v);
  let r, v;
  try {
    r = await fetch(u, { headers: { authorization: `Bearer ${token}` } });
  } catch {
    meter.calls++;
    throw new MetaError("Could not reach Meta", 0, 0, meter);
  }
  read(meter, r);
  v = await r.json().catch(() => ({}));
  if (!r.ok || v.error) {
    const e = v.error || {};
    throw new MetaError(String(e.error_user_msg || e.message || `Meta answered ${r.status}`).slice(0, 300), Number(e.code) || r.status, Number(e.error_subcode) || 0, meter);
  }
  return v;
}

async function tokenOf(env, igId) {
  const row = await env.DB.prepare("SELECT token FROM accounts WHERE id = ?").bind(`instagram:${igId}`).first();
  if (!row) throw new MetaError("That Instagram account is not connected", 1001, 0, { calls: 0 });
  return open(env, row.token);
}

const after = (v) => (v.paging && v.paging.next && v.paging.cursors && v.paging.cursors.after) || "";
const who = (c) => c.username || (c.from && c.from.username) || "";
const when = (t) => Date.parse(t) || 0;

const READS = {
  async profile(id, q, token, m) {
    const v = await get(id, { fields: "username,name,followers_count,follows_count,media_count" }, token, m);
    return { username: v.username || "", name: v.name || "", followers: v.followers_count ?? null, following: v.follows_count ?? null, posts: v.media_count ?? null };
  },
  async media(id, q, token, m) {
    const v = await get(`${id}/media`, { fields: "id,timestamp,permalink,media_type,like_count,comments_count", limit: "50", after: q.after }, token, m);
    return { items: (v.data || []).map((x) => ({ id: x.id, at: when(x.timestamp), link: x.permalink || "", type: x.media_type || "", likes: x.like_count ?? null, comments: x.comments_count ?? null })), after: after(v) };
  },
  async comments(id, q, token, m) {
    const v = await get(`${q.media}/comments`, { fields: "id,username,from,timestamp,replies.limit(50){id,username,from,timestamp}", limit: "50", after: q.after }, token, m);
    const items = [];
    for (const c of v.data || []) {
      items.push({ id: c.id, u: who(c), at: when(c.timestamp) });
      for (const r of (c.replies && c.replies.data) || []) items.push({ id: r.id, u: who(r), at: when(r.timestamp) });
    }
    return { items, after: after(v) };
  },
  async tags(id, q, token, m) {
    const v = await get(`${id}/tags`, { fields: "id,username,timestamp,permalink", limit: "50", after: q.after }, token, m);
    return { items: (v.data || []).map((x) => ({ id: x.id, u: x.username || "", at: when(x.timestamp), link: x.permalink || "" })), after: after(v) };
  },
  // who wrote and when, never what
  async dms(id, q, token, m) {
    const pages = await get("me/accounts", { fields: "id,access_token,instagram_business_account", limit: "100" }, token, m);
    const page = (pages.data || []).find((p) => p.instagram_business_account && p.instagram_business_account.id === id);
    let v, last;
    for (const [chats, each] of [[25, 20], [10, 10], [5, 5]]) {
      try {
        v = await get(`${page ? page.id : id}/conversations`, { platform: "instagram", fields: `id,updated_time,participants,messages.limit(${each}){id,from,created_time}`, limit: String(chats), after: q.after }, page ? page.access_token : token, m);
        break;
      } catch (e) {
        last = e;
        if (!/reduce the amount of data|unknown error/i.test(e.message)) throw e;
      }
    }
    if (!v) throw last;
    const items = (v.data || []).map((c) => {
      const people = Object.fromEntries(((c.participants && c.participants.data) || []).map((p) => [p.id, p.username || ""]));
      return { id: c.id, updated: when(c.updated_time), items: ((c.messages && c.messages.data) || [])
        .filter((x) => x.from && x.from.id !== id).map((x) => ({ id: x.id, u: x.from.username || people[x.from.id] || "", at: when(x.created_time) })) };
    });
    return { items, after: after(v) };
  },
  async audience(id, q, token, m) {
    const v = await get(`${id}/insights`, { metric: "follower_demographics", period: "lifetime", metric_type: "total_value", breakdown: q.by }, token, m);
    const b = (((v.data || [])[0] || {}).total_value || {}).breakdowns || [];
    return { by: q.by, items: ((b[0] || {}).results || []).map((x) => ({ k: (x.dimension_values || []).join(" "), n: Number(x.value) || 0 })) };
  },
  // a personal, private, age-gated or missing account is not an error: Meta just does not show it
  async discover(id, q, token, m) {
    const media = q.media === "1" ? ",media.limit(12){timestamp,media_type,like_count,comments_count,permalink}" : "";
    let v;
    try {
      v = await get(id, { fields: `business_discovery.username(${q.u}){username,name,biography,website,followers_count,follows_count,media_count${media}}` }, token, m);
    } catch (e) {
      if (e.code === 110 || e.sub === 2207013) return { found: false };
      throw e;
    }
    const d = v.business_discovery;
    if (!d) return { found: false };
    return { found: true, u: d.username || q.u, name: d.name || "", bio: d.biography || "", web: d.website || "", followers: d.followers_count ?? null, following: d.follows_count ?? null, posts: d.media_count ?? null,
      media: ((d.media && d.media.data) || []).map((x) => ({ at: when(x.timestamp), type: x.media_type || "", likes: x.like_count ?? null, comments: x.comments_count ?? null, link: x.permalink || "" })) };
  },
};

const OK = {
  after: /^[A-Za-z0-9_=-]{0,600}$/, media: /^\d{0,30}$/, u: /^[A-Za-z0-9._]{1,30}$/, by: /^(country|city|age|gender)$/,
};

// always 200 with ok: true or false, so the Desk also gets the rate-limit numbers of a call that failed
export async function handle(env, igId, what, params) {
  const run = Object.hasOwn(READS, what) ? READS[what] : null;
  const q = { after: params.get("after") || "", media: params.get("media") || "", u: params.get("u") || "", by: params.get("by") || "" };
  if (!run || !OK.after.test(q.after) || !OK.media.test(q.media) || (what === "comments" && !q.media) || (what === "discover" && !OK.u.test(q.u)) || (what === "audience" && !OK.by.test(q.by))) {
    return { ok: false, error: "Not a question this Worker can ask", code: 1000, meter: { calls: 0 } };
  }
  const meter = { calls: 0, app: null, buc: null, wait: 0 };
  try {
    return { ok: true, ...(await run(igId, q, await tokenOf(env, igId), meter)), meter };
  } catch (e) {
    return { ok: false, error: e instanceof MetaError ? e.message : "go.tany.im had a problem", code: e.code || 0, sub: e.sub || 0, meter };
  }
}
