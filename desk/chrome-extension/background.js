// Tatsuki's Desk — time on the page in front of you. Counts while Chrome is in front and you are active, or the tab is
// playing sound. Sends only to this Mac's Desk, and keeps what it could not send until the Desk is back.
const ENDPOINT = "http://localhost:4747/api/web/time";
const IDLE_AFTER = 60;     // seconds without input before you count as away
const MAX_GAP = 150;       // seconds: a longer silence means the Mac slept, so that time is not counted
const MAX_QUEUE = 20000;
const SEND_BATCH = 500;
const KEEP_PARAMS = {"youtube.com": ["v"]};

// What follows ? and # never leaves the browser. The Desk trims again on arrival.
function cleanUrl(raw) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (["localhost", "127.0.0.1", "[::1]"].includes(u.hostname)) return null;
  const keep = KEEP_PARAMS[u.hostname.replace(/^www\./, "")] || [];
  const q = new URLSearchParams();
  for (const k of keep) if (u.searchParams.has(k)) q.set(k, u.searchParams.get(k));
  const s = q.toString();
  return `${u.protocol}//${u.host}${u.pathname}${s ? "?" + s : ""}`;
}

// Why the last look counted or not, as one word. Never an address or a title.
let why = "";
async function current() {
  const win = await chrome.windows.getLastFocused({populate: true}).catch(() => null);
  why = !win ? "no-window" : !win.focused ? "not-focused" : win.incognito ? "incognito" : "";
  if (why) return null;
  const tab = (win.tabs || []).find(t => t.active);
  why = !tab ? "no-tab" : !tab.url ? "no-url" : "";
  if (why) return null;
  const url = cleanUrl(tab.url);
  if (!url) { why = "not-a-site"; return null; }
  const idle = await chrome.idle.queryState(IDLE_AFTER);
  if (idle === "locked" || (idle === "idle" && !tab.audible)) { why = idle; return null; }
  why = "counted";
  return {url, title: (tab.title || "").slice(0, 500), audible: !!tab.audible};
}

async function note(kind) {
  const {diag = {}} = await chrome.storage.local.get("diag");
  diag[kind] = (diag[kind] || 0) + 1;
  diag.last = Math.round(Date.now() / 1000);
  await chrome.storage.local.set({diag});
}

async function closeSpan(cur, now) {
  const end = now - cur.seen > MAX_GAP ? cur.seen : now;
  if (end - cur.start < 1) return;
  const {queue = []} = await chrome.storage.local.get("queue");
  queue.push({id: crypto.randomUUID(), start: cur.start, end, url: cur.url, title: cur.title, audible: cur.audible});
  await chrome.storage.local.set({queue: queue.slice(-MAX_QUEUE)});
}

// Every event comes here. A worker that was stopped between events finds the open span again in storage.
async function step() {
  const now = Date.now() / 1000;
  const {cur = null} = await chrome.storage.session.get("cur");
  const next = await current();
  await note(why);
  const slept = cur && now - cur.seen > MAX_GAP;
  const same = cur && next && !slept && cur.url === next.url;
  if (cur && !same) await closeSpan(cur, now);
  if (same) {
    cur.seen = now;
    cur.title = next.title || cur.title;
    cur.audible = cur.audible || next.audible;
    await chrome.storage.session.set({cur});
  } else {
    await chrome.storage.session.set({cur: next ? {...next, start: now, seen: now} : null});
  }
}

// Long spans are cut each minute, so a crash loses a minute at most and the Desk stays up to date.
async function cut() {
  await note("tick");
  const now = Date.now() / 1000;
  const {cur = null} = await chrome.storage.session.get("cur");
  if (cur) {
    await closeSpan(cur, now);
    await chrome.storage.session.set({cur: null});
  }
  await step();
}

async function send() {
  const {queue = [], diag} = await chrome.storage.local.get(["queue", "diag"]);
  if (!queue.length && !diag) return;
  const batch = queue.slice(0, SEND_BATCH);
  try {
    const r = await fetch(ENDPOINT, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({spans: batch, diag})});
    if (!r.ok) return;
  } catch { return; }
  const sent = new Set(batch.map(s => s.id));
  const {queue: latest = []} = await chrome.storage.local.get("queue");
  await chrome.storage.local.set({queue: latest.filter(s => !sent.has(s.id))});
}

// One at a time: two events at once would otherwise both close the same span.
let chain = Promise.resolve();
const serial = fn => { chain = chain.then(fn, fn).catch(() => {}); return chain; };

function start() {
  chrome.idle.setDetectionInterval(IDLE_AFTER);
  chrome.alarms.create("tick", {periodInMinutes: 1});
  serial(step);
}

chrome.runtime.onInstalled.addListener(start);
chrome.runtime.onStartup.addListener(start);
chrome.tabs.onActivated.addListener(() => serial(step));
chrome.tabs.onUpdated.addListener((_, change) => { if (change.url || change.title || "audible" in change) serial(step); });
chrome.tabs.onRemoved.addListener(() => serial(step));
chrome.windows.onFocusChanged.addListener(() => serial(step));
chrome.idle.onStateChanged.addListener(() => serial(step));
chrome.alarms.onAlarm.addListener(a => { if (a.name === "tick") { serial(cut); serial(send); } });
