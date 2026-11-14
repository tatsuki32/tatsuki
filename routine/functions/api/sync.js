/* POST /api/sync — the Routine app (phone) and desk/routine_sync.py (Mac) each send all their docs and get back the merged state.
   GET /api/sync returns the state. Both need the header x-sync-key = the SYNC_KEY secret.
   Storage: one KV value "state" = {routines:[...], routinelog:[...]} in the ROUTINE namespace.
   Merge, per doc id: the newer updatedAt wins. A day doc (id YYYY-MM-DD) is joined instead: for every routine (up to 5, keyed by
   its id), every check from either side is kept (earliest time), and its run ("<id>Run") is the one that ended last. So a run on the phone and checks on the Mac never erase each other.
   (An unchecked box can come back if the other side still has it; that is the price of never losing a check.) */
const DAY = /^\d{4}-\d\d-\d\d$/;
const MAX_BODY = 2_000_000;

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
  .slice(0, 5000);

function joinMarks(a = {}, b = {}) {
  const out = { ...a };
  for (const [k, v] of Object.entries(b || {})) if (!out[k] || (v && v < out[k])) out[k] = v;
  return out;
}

function mergeDoc(a, b, isLog) {
  if (!a) return b;
  if (!b) return a;
  const newer = (+b.updatedAt || 0) > (+a.updatedAt || 0) ? b : a;
  if (!(isLog && DAY.test(a.id))) return newer;
  const out = { ...newer };
  const isObj = v => v && typeof v === "object" && !Array.isArray(v);
  // every routine (up to 5): "<id>" holds that day's checks, "<id>Run" its run
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (k === "totals" || k === "id") continue;
    if (k.endsWith("Run")) {
      const ra = a[k], rb = b[k];
      if (ra || rb) out[k] = !ra ? rb : !rb ? ra : (+rb.end || 0) > (+ra.end || 0) ? rb : ra;
    } else if (isObj(a[k]) || isObj(b[k])) {
      out[k] = joinMarks(isObj(a[k]) ? a[k] : {}, isObj(b[k]) ? b[k] : {});
    }
  }
  out.updatedAt = Math.max(+a.updatedAt || 0, +b.updatedAt || 0);
  return out;
}

function mergeList(a, b, isLog) {
  const m = new Map(clean(a).map(d => [d.id, d]));
  for (const d of clean(b)) m.set(d.id, mergeDoc(m.get(d.id), d, isLog));
  return [...m.values()];
}

export async function onRequest({ request, env }) {
  if (!env.ROUTINE || !env.SYNC_KEY) return json({ error: "not set up" }, 500);
  if (!(await same(request.headers.get("x-sync-key") || "", env.SYNC_KEY))) return json({ error: "locked" }, 401);
  let state = (await env.ROUTINE.get("state", "json")) || { routines: [], routinelog: [] };
  if (request.method === "POST") {
    const text = await request.text();
    if (text.length > MAX_BODY) return json({ error: "too large" }, 413);
    let body;
    try { body = JSON.parse(text); } catch { return json({ error: "bad json" }, 400); }
    const next = { routines: mergeList(state.routines, body.routines, false), routinelog: mergeList(state.routinelog, body.routinelog, true) };
    if (JSON.stringify(next) !== JSON.stringify(state)) {
      state = next;
      await env.ROUTINE.put("state", JSON.stringify(state));
    }
  } else if (request.method !== "GET") {
    return json({ error: "method" }, 405);
  }
  return json({ ...state, at: Date.now() });
}
