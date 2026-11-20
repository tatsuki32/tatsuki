/* POST /api/sync — the Cards app (phone) and desk/cards_sync.py (Mac) each send all their docs and get back the merged state.
   GET /api/sync returns the state. Both need the header x-sync-key = the SYNC_KEY secret.
   Storage: one KV value "state" = {cards:[...], cardlog:[...]} in the CARDS namespace.
   Merge, per doc id: a card = the newer updatedAt wins (a review on either side bumps it). A day of review records (id YYYY-MM-DD)
   is joined: every entry from either side is kept, told apart by card id + time, so a review on the phone and one on the Mac
   never erase each other. Only cards travel here: a word, its meaning, the one sentence it was met in. */
const DAY = /^\d{4}-\d\d-\d\d$/;
const MAX_BODY = 8_000_000;
const MAX_DOCS = 20000;

const json = (v, status = 200) => new Response(JSON.stringify(v), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

async function same(a, b) {
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([a, b].map(s => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < u.length; i++) d |= u[i] ^ v[i];
  return d === 0;
}

const clean = list => (Array.isArray(list) ? list : [])
  .filter(d => d && typeof d === "object" && !Array.isArray(d) && typeof d.id === "string" && d.id.length > 0 && d.id.length <= 100)
  .slice(0, MAX_DOCS);

function mergeDoc(a, b, isLog) {
  if (!a) return b;
  if (!b) return a;
  const newer = (+b.updatedAt || 0) > (+a.updatedAt || 0) ? b : a;
  if (!(isLog && DAY.test(a.id))) return newer;
  const seen = new Set(), entries = [];
  for (const e of [...(Array.isArray(a.entries) ? a.entries : []), ...(Array.isArray(b.entries) ? b.entries : [])]) {
    if (!e || typeof e !== "object") continue;
    const k = `${e.c}|${e.at}`;
    if (seen.has(k)) continue;
    seen.add(k);
    entries.push(e);
  }
  entries.sort((x, y) => (+x.at || 0) - (+y.at || 0));
  return { ...newer, entries, updatedAt: Math.max(+a.updatedAt || 0, +b.updatedAt || 0) };
}

function mergeList(a, b, isLog) {
  const m = new Map(clean(a).map(d => [d.id, d]));
  for (const d of clean(b)) m.set(d.id, mergeDoc(m.get(d.id), d, isLog));
  return [...m.values()];
}

export async function onRequest({ request, env }) {
  if (!env.CARDS || !env.SYNC_KEY) return json({ error: "not set up" }, 500);
  if (!(await same(request.headers.get("x-sync-key") || "", env.SYNC_KEY))) return json({ error: "locked" }, 401);
  let state = (await env.CARDS.get("state", "json")) || { cards: [], cardlog: [] };
  if (request.method === "POST") {
    const text = await request.text();
    if (text.length > MAX_BODY) return json({ error: "too large" }, 413);
    let body;
    try { body = JSON.parse(text); } catch { return json({ error: "bad json" }, 400); }
    const next = { cards: mergeList(state.cards, body.cards, false), cardlog: mergeList(state.cardlog, body.cardlog, true) };
    if (JSON.stringify(next) !== JSON.stringify(state)) {
      state = next;
      await env.CARDS.put("state", JSON.stringify(state));
    }
  } else if (request.method !== "GET") {
    return json({ error: "method" }, 405);
  }
  return json({ ...state, at: Date.now() });
}
