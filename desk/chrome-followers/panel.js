// The panel beside Instagram. It opens one profile at a time and saves what you decided to the Desk on this Mac.
// Only people you pressed Unfollow for are unfollowed (press.js), one after another while this panel is open, up to the day's
// number. Stop drops the rest, closing the panel does too, and any surprise on the page stops it. Nothing starts by itself.
const API = "http://localhost:4747/api/igfans/cleanup";
const KEYS = {done: "unfollow", keep: "keep", later: "later", undo: "undo"};  // manifest command -> action on the first person
const $ = id => document.getElementById(id);
const num = n => Number(n || 0).toLocaleString("en-US");
const DAY = 86400000, GAP = 3000;  // between two people
const WHY = {
  "other-page": "Instagram opened another page. Nothing was pressed.",
  "other-account": "Instagram is not signed in as this account. Nothing was pressed.",
  "no-button": "No Following button on this page. The account may be gone. Press Keep or Later.",
  "no-menu": "The Following menu did not open. Nothing was unfollowed. Do this one by hand.",
  "not-sure": "The page did not confirm it. Look at Instagram, then press the by-hand button if it worked.",
  "blocked": "Instagram asked to slow down or sign in. The panel stops for a day.",
  "no-tab": "The Instagram tab was closed. Nothing was pressed."
};
const day = t => t ? new Date(t).toLocaleDateString("en-US", {year: "numeric", month: "short", day: "numeric"}) : "";
const good = u => /^[a-z0-9._]{1,30}$/.test(u);
let view = null, busy = "", pumping = false, pause = 0, account = "", undo = [], stop = "", failed = "", behind = true, query = "";
const ticked = new Set(), waiting = [];  // waiting: pressed, not done yet; kept nowhere else

const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };

async function call(body) {
  const r = await fetch(body ? API : API + "?account=" + encodeURIComponent(account), body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {cache: "no-store"});
  const v = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(v.error || "The Desk did not answer");
  return v;
}

// One tab of the panel's own is reused for all profiles and stays behind, so your screen does not change. Returns its id.
async function open(u) {
  const url = "https://www.instagram.com/" + u + "/", active = !behind;
  const {tab} = await chrome.storage.session.get("tab");
  if (tab != null) {
    try { await chrome.tabs.update(tab, active ? {url, active} : {url}); return tab; } catch { /* that tab was closed */ }
  }
  const t = await chrome.tabs.create({url, active});
  await chrome.storage.session.set({tab: t.id});
  return t.id;
}

async function arrived(id, u) {
  let seen = "no-button";
  await new Promise(r => setTimeout(r, 500));  // the old page still counts as complete for a moment
  for (const end = Date.now() + 12000; Date.now() < end;) {
    const t = await chrome.tabs.get(id).catch(() => null);
    if (!t) return "no-tab";
    if (t.status === "complete") {
      const p = new URL(t.url || "about:blank");
      if (/^\/(challenge|accounts\/)/.test(p.pathname)) return "blocked";
      if (p.hostname === "www.instagram.com" && p.pathname.replace(/\/$/, "") === "/" + u) return "";
      seen = "other-page";
    }
    await new Promise(r => setTimeout(r, 200));
  }
  return seen;
}

// Opens this one person behind and unfollows. Returns "ok", or the reason nothing (more) was pressed. The page never comes to the front.
async function unfollow(u) {
  const id = await open(u), bad = await arrived(id, u);
  if (bad) return bad;
  try {
    const [r] = await chrome.scripting.executeScript({target: {tabId: id}, func: pressUnfollow, args: [u, account]});
    return (r && r.result) || "not-sure";
  } catch { return "other-page"; }
}

const hourFull = () => view.hour >= view.perHour && Date.now() < view.hourFrom;  // looked at again when the Desk answers
const limit = () => view.today >= view.perDay || hourFull();
const left = () => Math.min(view.perDay - view.today, view.perHour - view.hour) - waiting.length - (busy ? 1 : 0);  // presses still free

async function act(name, u) {
  if (!view || !name) return;
  if (name === "undo") { if (busy || waiting.length || !undo.length) return; return save("undo", undo.pop()); }
  const person = u ? view.queue.find(r => r.u === u) : shown().find(r => r.u !== busy && !waiting.includes(r.u));
  if (!person || !good(person.u) || busy === person.u) return;
  if (name === "done") { if (!limit()) await save("done", person.u); return; }
  if (name !== "unfollow") return save(name, person.u);  // Keep and Later also work while others are being unfollowed
  const i = waiting.indexOf(person.u);
  if (i >= 0) waiting.splice(i, 1);  // a second press takes it back
  else if (Date.now() >= pause && left() > 0) waiting.push(person.u);
  stop = ""; draw(); pump();
}

// One after another. A person whose page is odd is passed over; anything about the account stops the rest.
async function pump() {
  if (pumping || !waiting.length) return;
  pumping = true;
  await navigator.locks.request("unfollow", {ifAvailable: true}, async lock => {
    if (!lock) { stop = "Another window of this panel is working."; waiting.length = 0; return; }
    while (waiting.length && !limit() && Date.now() >= pause) {
      const u = busy = waiting.shift();
      draw();
      let r;
      try { r = await unfollow(u); } catch { r = "not-sure"; }
      if (r === "ok" || r === "already") { if (!await save("done", u)) { waiting.length = 0; } }
      else {
        stop = "@" + u + ": " + (WHY[r] || WHY["not-sure"]); failed = u;
        if (r === "blocked") { pause = Date.now() + DAY; await chrome.storage.local.set({pause}); }
        if (r !== "no-button" && r !== "no-menu") { if (waiting.length) stop += " The other " + num(waiting.length) + " were not started."; waiting.length = 0; }
      }
      busy = "";
      draw();
      if (waiting.length) await new Promise(ok => setTimeout(ok, GAP));
    }
  }).catch(e => { stop = e.message; waiting.length = 0; });
  busy = ""; pumping = false;
  if (limit() || Date.now() < pause) waiting.length = 0;
  draw();
  pump();  // pressed in the last moment
}

async function save(op, u) {
  try {
    const v = await call({account, op, u});
    view = v;
    if (op !== "undo") undo.push(u);
    if (failed === u) { failed = ""; stop = ""; }
    ticked.delete(u);
    if (op !== "undo" && waiting.includes(u)) waiting.splice(waiting.indexOf(u), 1);
    draw();
    return true;
  } catch (e) { stop = e.message; draw(); return false; }
}

async function keepTicked() {
  for (const u of [...ticked]) await save("keep", u);
  ticked.clear(); draw();
}

const shown = () => view.queue.filter(r => r.u.includes(query));

function button(name, label, u, cls = "", off = false) {
  const b = el("button", {className: cls, disabled: off, textContent: label});
  if (u) b.ariaLabel = label + " @" + u;
  b.onclick = () => act(name, u);
  return b;
}

function row(p) {
  const working = busy === p.u, at = waiting.indexOf(p.u), tick = el("input", {type: "checkbox", checked: ticked.has(p.u), ariaLabel: "Tick @" + p.u});
  tick.onchange = () => { tick.checked ? ticked.add(p.u) : ticked.delete(p.u); draw(); };
  const name = el("a", {href: "https://www.instagram.com/" + p.u + "/", target: "_blank", textContent: "@" + p.u, title: "Look at the profile"});
  const who = el("label", {className: "who"}, tick, el("span", {}, name, el("span", {className: "sub", textContent: day(p.since) + (p.again ? " · marked before" : "")})));
  const li = el("li", {className: working ? "working" : ""}, who, button("unfollow", working ? "Working…" : at >= 0 ? "Waiting " + (at + 1) : "Unfollow", p.u, at >= 0 ? "wide on" : "wide", working || at < 0 && left() <= 0), button("later", "Later", p.u, "", working));
  if (failed === p.u) li.append(button("done", "I unfollowed by hand", p.u, "full"));
  return li;
}

function draw() {
  const m = $("main"), top = document.scrollingElement.scrollTop; m.replaceChildren();
  if (!view) { m.append(el("p", {textContent: stop || "Loading…"})); if (stop) m.append(retry()); return; }
  $("count").textContent = `${num(view.left)} to go · this hour ${num(view.hour)} of ${num(view.perHour)} · today ${num(view.today)} of ${num(view.perDay)}`;
  $("fill").style.width = Math.min(100, view.today / view.perDay * 100) + "%";
  const bar = $("fill").parentElement; bar.ariaValueMax = view.perDay; bar.ariaValueNow = view.today;
  $("perDay").value = String(view.perDay); $("perHour").value = String(view.perHour); $("over").value = String(view.over);
  $("list").value = view.list; $("overRow").hidden = view.list !== "notback"; $("behind").checked = behind;
  for (const id of ["account", "perDay", "perHour", "over", "list"]) $(id).disabled = !!busy || waiting.length > 0;
  $("now").textContent = !busy && waiting.length ? num(waiting.length) + " waiting" : busy ? "Unfollowing @" + busy + (waiting.length ? " · " + num(waiting.length) + " waiting" : "") : "";
  if (stop) {
    const n = el("p", {className: "note", role: "status", textContent: stop + " "});
    if (failed) { const a = el("a", {href: "#", textContent: "Show the page"}); a.onclick = async e => { e.preventDefault(); const {tab} = await chrome.storage.session.get("tab"); chrome.tabs.update(tab, {active: true}).catch(() => {}); }; n.append(a); }
    m.append(n);
  }
  const tools = el("div", {className: "tools"});
  if (waiting.length) { const b = el("button", {className: "primary", textContent: "Stop, drop the " + num(waiting.length) + " waiting"}); b.onclick = () => { waiting.length = 0; draw(); }; tools.append(b); }
  if (ticked.size) { const b = el("button", {className: "primary", textContent: "Keep the " + num(ticked.size) + " ticked"}); b.onclick = keepTicked; tools.append(b); }
  if (undo.length) { const b = button("undo", "Back to @" + undo[undo.length - 1], "", "", !!busy || waiting.length > 0); b.title = "Changes the list only. It does not follow anyone again."; tools.append(b); }
  if (tools.children.length) m.append(tools);
  const list = shown();
  if (!view.ready) m.append(el("p", {textContent: "Import followers on the Desk first."}), retry());
  else if (!view.queue.length && view.list === "left" && view.exports < 2) m.append(el("p", {textContent: "This list needs two exports. Import a newer one on the Desk."}));
  else if (!view.queue.length) m.append(el("p", {textContent: "Nobody left in this list."}), el("p", {className: "sub", textContent: `${num(view.done)} unfollowed · ${num(view.keep.length)} kept`}));
  else if (Date.now() < pause) m.append(el("p", {textContent: "Stopped until " + new Date(pause).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) + ", because Instagram asked to slow down."}));
  else if (view.today < view.perDay && hourFull()) m.append(el("p", {textContent: "That is this hour’s number. You can press again from " + new Date(view.hourFrom).toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"}) + "."}), el("p", {className: "sub", textContent: "Nothing goes on by itself."}));
  else if (limit()) m.append(el("p", {textContent: "That is today’s number. The next people are here tomorrow."}));
  else if (!list.length) m.append(el("p", {textContent: "Nobody matches."}));
  else m.append(el("ul", {}, ...list.slice(0, 100).map(row)), ...(list.length > 100 ? [el("p", {className: "sub", textContent: `${num(list.length - 100)} more after these`})] : []));
  document.scrollingElement.scrollTop = top;
}

function retry() { const b = el("button", {}, el("span", {textContent: "Try again"})); b.onclick = load; return b; }

async function load() {
  stop = "";
  try {
    view = await call();
    account = view.account;
    $("account").replaceChildren(...view.accounts.map(a => el("option", {value: a, textContent: "@" + a, selected: a === account})));
  } catch (e) { view = null; stop = "The Desk is not running on this Mac."; }
  draw();
}

async function saveSettings() {
  try { view = await call({account, op: "settings", perDay: Number($("perDay").value), perHour: Number($("perHour").value), over: Number($("over").value), list: $("list").value}); stop = ""; } catch (e) { stop = e.message; }
  draw();
}

$("account").onchange = () => { account = $("account").value; undo = []; ticked.clear(); stop = failed = ""; chrome.storage.local.set({account}); load(); };
$("perDay").onchange = saveSettings;
$("over").onchange = saveSettings;
$("perHour").onchange = saveSettings;
setInterval(() => { if (view && !busy && !waiting.length && view.hour) load(); }, 60000);  // only the numbers: the list and the counts
$("list").onchange = saveSettings;
chrome.runtime.onMessage.addListener(m => { if (m && Object.hasOwn(KEYS, m.name)) act(KEYS[m.name]); });
$("find").oninput = () => { query = $("find").value.trim().toLowerCase().replace(/^@/, ""); if (view) draw(); };
$("behind").onchange = () => { behind = $("behind").checked; chrome.storage.local.set({behind}); };
chrome.storage.local.get(["account", "pause", "behind"]).then(s => { account = s.account || ""; pause = s.pause || 0; behind = s.behind !== false; load(); });
