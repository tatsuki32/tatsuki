// The panel beside Instagram. It opens one profile at a time and saves what you decided to the Desk on this Mac.
// Each Unfollow is one press of yours for the person on screen (press.js). There is no list run, no timer and no retry.
const API = "http://localhost:4747/api/igfans/cleanup";
const KEYS = {unfollow: "Alt+N", keep: "Alt+K", later: "Alt+L", undo: "Alt+Z"};
const $ = id => document.getElementById(id);
const num = n => Number(n || 0).toLocaleString("en-US");
const DAY = 86400000, GAP = 6000;  // a held key must not press twice
const WHY = {
  "other-page": "The Instagram tab shows another page. Press Open on Instagram again.",
  "other-account": "Instagram is not signed in as this account. Nothing was pressed.",
  "no-button": "No Following button on this page. The account may be gone. Press Later, or Keep.",
  "no-menu": "The Following menu did not open. Nothing was unfollowed. Do this one by hand.",
  "not-sure": "The page did not confirm it. Look at Instagram, then press the by-hand button if it worked.",
  "blocked": "Instagram asked to slow down or sign in. The panel stops for a day.",
  "no-tab": "The Instagram tab was closed. Press Open on Instagram again."
};
const day = t => t ? new Date(t).toLocaleDateString("en-US", {year: "numeric", month: "short", day: "numeric"}) : "";
const good = u => /^[a-z0-9._]{1,30}$/.test(u);
let view = null, busy = false, last = 0, pause = 0, account = "", undo = [], stop = "", opened = "";

const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };

async function call(body) {
  const r = await fetch(body ? API : API + "?account=" + encodeURIComponent(account), body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {cache: "no-store"});
  const v = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(v.error || "The Desk did not answer");
  return v;
}

// One tab is reused for all profiles, so no tab of yours is replaced.
async function open(u) {
  if (!good(u)) return;
  opened = u;
  const url = "https://www.instagram.com/" + u + "/";
  const {tab} = await chrome.storage.session.get("tab");
  if (tab != null) {
    try { await chrome.tabs.update(tab, {url, active: true}); return; } catch { /* that tab was closed */ }
  }
  const t = await chrome.tabs.create({url});
  await chrome.storage.session.set({tab: t.id});
}

// The one press. Returns "ok", or the reason nothing (more) was pressed.
async function unfollow(u) {
  const {tab} = await chrome.storage.session.get("tab");
  if (tab == null) return "no-tab";
  let r;
  try { [r] = await chrome.scripting.executeScript({target: {tabId: tab}, func: pressUnfollow, args: [u, account]}); } catch { return "other-page"; }
  return (r && r.result) || "not-sure";
}

async function act(name) {
  if (busy || !view || !name) return;
  const person = view.queue[0];
  if (name === "undo" ? !undo.length : !person || limit()) return;
  if (name !== "undo" && opened !== person.u) return;  // decide about the person you are looking at
  if (Date.now() < pause && name === "unfollow") return;
  if (name === "unfollow" && Date.now() - last < GAP) { stop = "A few seconds between two people, please."; draw(); return; }
  busy = true; stop = ""; draw();
  try {
    if (name === "unfollow") {
      const r = await unfollow(person.u);
      last = Date.now();
      if (r === "blocked") { pause = last + DAY; await chrome.storage.local.set({pause}); }
      if (r === "no-tab" || r === "other-page") opened = "";
      if (r !== "ok" && r !== "already") { stop = WHY[r] || WHY["not-sure"]; busy = false; draw(); return; }
      name = "done";
    }
    if (name === "undo") {
      const u = undo.pop();
      view = await call({account, op: "undo", u});
      const i = view.queue.findIndex(r => r.u === u);
      if (i > 0) view.queue.unshift(...view.queue.splice(i, 1));
      await open(view.queue[0] ? view.queue[0].u : u);
    } else {
      view = await call({account, op: name, u: person.u});
      undo.push(person.u);
      if (view.queue[0] && !limit()) await open(view.queue[0].u); else opened = "";
    }
  } catch (e) { stop = e.message; }
  busy = false; draw();
}

const limit = () => view.today >= view.perDay;

function button(name, label, cls = "") {
  const b = el("button", {className: cls, disabled: busy}, el("span", {textContent: label}));
  if (KEYS[name]) b.append(el("kbd", {textContent: KEYS[name]}));
  b.onclick = () => act(name);
  return b;
}

function draw() {
  const m = $("main"); m.replaceChildren();
  if (!view) { m.append(el("p", {textContent: stop || "Loading…"})); if (stop) m.append(retry()); return; }
  $("count").textContent = `${num(view.left)} to go · today ${num(view.today)} of ${num(view.perDay)}`;
  $("fill").style.width = Math.min(100, view.today / view.perDay * 100) + "%";
  const bar = $("fill").parentElement; bar.ariaValueMax = view.perDay; bar.ariaValueNow = view.today;
  $("perDay").value = String(view.perDay); $("over").value = String(view.over);
  const p = view.queue[0];
  if (stop) m.append(el("p", {className: "sub", role: "status", textContent: stop}));
  if (!view.ready) m.append(el("p", {textContent: "Import followers on the Desk first."}), retry());
  else if (!p) m.append(el("p", {textContent: "Nobody left in this list."}), el("p", {className: "sub", textContent: `${num(view.done)} unfollowed · ${num(view.keep.length)} kept`}));
  else if (Date.now() < pause) m.append(el("p", {textContent: "Stopped until " + new Date(pause).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) + ", because Instagram asked to slow down."}));
  else if (limit()) m.append(el("p", {textContent: "That is today’s number. The next person is here tomorrow."}));
  else {
    const card = el("div", {className: "card"}, el("h1", {textContent: "@" + p.u}), el("p", {className: "sub", textContent: "You followed on " + day(p.since) + " · does not follow back"}));
    if (p.again) card.append(el("p", {className: "sub", textContent: "You marked this one before, but the newest export still lists it."}));
    m.append(card);
    if (opened !== p.u) {
      const b = el("button", {className: "primary", disabled: busy}, el("span", {textContent: "Open on Instagram"}));
      b.onclick = async () => { await open(p.u); draw(); };
      m.append(b);
    } else {
      m.append(el("div", {className: "acts"}, button("unfollow", busy ? "Working…" : "Unfollow, next", "primary"), button("keep", "Keep"), button("later", "Later"), button("done", "I unfollowed by hand")));
    }
  }
  if (undo.length) m.append(button("undo", "Back to @" + undo[undo.length - 1]), el("p", {className: "sub", textContent: "Back changes the list only. It does not follow anyone again."}));
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
  try { view = await call({account, op: "settings", perDay: Number($("perDay").value), over: Number($("over").value)}); opened = ""; stop = ""; } catch (e) { stop = e.message; }
  draw();
}

$("account").onchange = () => { account = $("account").value; undo = []; opened = ""; chrome.storage.local.set({account}); load(); };
$("perDay").onchange = saveSettings;
$("over").onchange = saveSettings;
chrome.runtime.onMessage.addListener(m => { if (m && m.name) act(m.name === "done" ? "unfollow" : KEYS[m.name] ? m.name : ""); });
chrome.storage.local.get(["account", "pause"]).then(s => { account = s.account || ""; pause = s.pause || 0; load(); });
