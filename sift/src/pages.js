// The pages, as strings. No libraries, no web fonts. Sizes 12/14/16/20/28, weights 400/700, spacing 4/8/16/24/40.
// Gold only for the current place, the main button and outside links.
import { PLANS, COMPETITORS } from "./plans.js";
import { esc } from "./mail.js";

const CSS = `
:root{--bg:#1c1d21;--surface:#26282d;--surface-2:#30323a;--ink:#f2f2f4;--muted:#a0a1aa;--line:#3a3c44;--gold:#d4b25c;--gold-ink:#1c1d21;--bad:#e8a0a0}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif}
a{color:var(--gold)}
.wrap{max-width:960px;margin:0 auto;padding:0 16px}
.top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 0}
.brand{font-weight:700;font-size:16px;color:var(--ink);text-decoration:none;letter-spacing:.02em}
.brand span{color:var(--gold)}.brand span.muted{color:var(--muted)}
.btn{display:inline-block;border:1px solid var(--line);background:var(--surface);color:var(--ink);font:inherit;font-size:14px;padding:8px 16px;border-radius:8px;cursor:pointer;text-decoration:none;line-height:1.4}
.btn:hover{background:var(--surface-2)}
.btn.main{background:var(--gold);border-color:var(--gold);color:var(--gold-ink);font-weight:700}
.btn.small{font-size:12px;padding:4px 8px}
.btn[disabled]{opacity:.5;cursor:default}
.muted{color:var(--muted)}.small{font-size:12px}.bad{color:var(--bad)}
input,select{font:inherit;font-size:16px;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:0}
input:focus,select:focus,.btn:focus-visible{outline:2px solid var(--gold);outline-offset:1px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px}
h1{font-size:28px;line-height:1.3;margin:0 0 16px}h2{font-size:20px;margin:0 0 16px}h3{font-size:16px;margin:0 0 8px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:400;font-size:12px}
.scroll{overflow-x:auto}
`;

const page = (title, body, extra = "") => `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title><meta name="color-scheme" content="dark"><meta name="theme-color" content="#1c1d21">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%231c1d21'/%3E%3Cpath d='M8 10h16M11 16h10M14 22h4' stroke='%23d4b25c' stroke-width='3' stroke-linecap='round'/%3E%3C/svg%3E">
<style>${CSS}${extra}</style></head><body>${body}</body></html>`;

// ---------- landing ----------
export function landingPage() {
  const plans = Object.values(PLANS).map((p) => `
    <div class="card plan${p.id === "pro" ? " pro" : ""}">
      <h3>${esc(p.name)}</h3>
      <div class="price">${p.month ? `$${p.month}<span class="muted small"> /month</span>` : "$0"}</div>
      <div class="muted small">${p.year ? `or $${p.year} a year (2 months free)` : "No card needed"}</div>
      <p class="small">${esc(p.blurb)}</p>
      <ul>${p.perks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    </div>`).join("");
  const rows = COMPETITORS.map((c) => `<tr><td>${esc(c.name)}</td><td>${esc(c.price)}</td><td class="muted">${esc(c.note)}</td></tr>`).join("");
  return page("Sift Daily", `
<div class="wrap">
  <div class="top"><a class="brand" href="/">Sift<span>.</span>Daily</a><a class="btn" href="/login">Sign in</a></div>
  <section class="hero">
    <h1>Your sources only.<br>One email a day.</h1>
    <p class="lead muted">Paste the sites you trust. Every afternoon Sift Daily sends one short email: what's new, a one-line summary of each article, and a brief of the whole day. No feed to scroll, no algorithm choosing for you.</p>
    <p><span class="btn main" aria-disabled="true">Early access soon</span></p>
  </section>

  <section class="grid3">
    <div class="card"><h3>Paste a site, that's it</h3><p class="small muted">Sift Daily finds the site's RSS feed for you. Unsubscribe with one button, bring it back anytime.</p></div>
    <div class="card"><h3>Summaries that stay honest</h3><p class="small muted">Each line is written only from what the site itself published. Nothing is added, and every item links to the original.</p></div>
    <div class="card"><h3>Arrives, even if you forget</h3><p class="small muted">One email at the same time each day, sorted by your folders. Mark Later or make it a task straight from the email.</p></div>
  </section>

  <section>
    <h2>How it works</h2>
    <ol class="steps">
      <li><b>Add your sources.</b> <span class="muted">News sites, blogs, newsletters with a feed. Sort them into folders like World or Tech.</span></li>
      <li><b>We check them once a day.</b> <span class="muted">Only articles since the last email. Duplicates are dropped.</span></li>
      <li><b>One email lands in your inbox.</b> <span class="muted">A short brief at the top, then each article with its one-line summary and link.</span></li>
    </ol>
  </section>

  <section>
    <h2>Compared with RSS readers</h2>
    <div class="scroll"><table><thead><tr><th>Service</th><th>Price</th><th>How it works</th></tr></thead><tbody>
      ${rows}
      <tr class="me"><td><b>Sift Daily</b></td><td><b>$4/mo Pro</b></td><td>Comes to you: one email a day, AI summaries included.</td></tr>
    </tbody></table></div>
    <p class="small muted">Competitor prices from public comparison pages, November 2026. Check their sites for current prices.</p>
  </section>

  <section>
    <h2>Pricing</h2>
    <div class="grid2">${plans}</div>
  </section>

  <footer class="small muted">Sift Daily · made at tany.im</footer>
</div>`, `
.hero{padding:40px 0 24px}.lead{font-size:16px;max-width:600px}
section{margin:0 0 40px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;max-width:640px}
.steps{padding-left:24px;margin:0}.steps li{margin-bottom:8px}
.plan ul{padding-left:16px;margin:8px 0 0;font-size:14px}.plan p{margin:8px 0}
.plan.pro{border-color:var(--gold)}
.price{font-size:28px;font-weight:700}
tr.me td{background:var(--surface)}
footer{padding:24px 0 40px;border-top:1px solid var(--line)}
@media(max-width:720px){.grid3,.grid2{grid-template-columns:1fr}h1{font-size:28px}}
`);
}

// ---------- sign in ----------
export function loginPage({ error = "", next = "/app" } = {}) {
  return page("Sign in · Sift Daily", `
<div class="wrap narrow">
  <div class="top"><a class="brand" href="/">Sift<span>.</span>Daily</a></div>
  <form class="card" method="post" action="/login">
    <h2>Sign in</h2>
    <label class="small muted" for="k">Passphrase</label>
    <input id="k" name="key" type="password" autocomplete="current-password" required autofocus>
    <input type="hidden" name="next" value="${esc(next)}">
    ${error ? `<p class="bad small">${esc(error)}</p>` : ""}
    <p><button class="btn main" type="submit">Sign in</button></p>
    <p class="small muted">Accounts open with early access. For now this is the owner's sign-in.</p>
  </form>
</div>`, `.narrow{max-width:420px}form input{width:100%;margin:4px 0 8px}`);
}

// ---------- the email's Later / task links ----------
export function markPage({ bad = false, item, mark, done } = {}) {
  const word = { later: "Later", task: "a task", read: "Read" }[mark] || "";
  const body = bad
    ? `<h2>This link doesn't work</h2><p class="muted">It may be old. Open the app to mark the article.</p><p><a class="btn" href="/app">Open Sift Daily</a></p>`
    : done
      ? `<h2>Done</h2><p>${esc(item.title)}</p><p class="muted">Marked as ${esc(word)}.</p><p><a class="btn" href="/app">Open Sift Daily</a> <a class="btn" href="${esc(item.url)}" rel="noopener">Read the article</a></p>`
      : `<h2>Mark as ${esc(word)}?</h2><p>${esc(item.title)}</p><form method="post"><button class="btn main" type="submit">Mark as ${esc(word)}</button></form>`;
  return page("Sift Daily", `<div class="wrap narrow"><div class="top"><a class="brand" href="/">Sift<span>.</span>Daily</a></div><div class="card">${body}</div></div>`, `.narrow{max-width:520px}`);
}

// ---------- the app ----------
export function appPage() {
  return page("Sift Daily", `
<div class="wrap">
  <div class="top">
    <a class="brand" href="/">Sift<span>.</span>Daily</a>
    <nav class="tabs" role="tablist"><button class="tab" data-tab="news" role="tab">News</button><button class="tab" data-tab="sources" role="tab">Sources</button><span id="adminLink"></span><a class="tab" href="/logout">Sign out</a></nav>
  </div>
  <div id="note" class="small muted" role="status"></div>
  <main id="view"></main>
</div>
<script>${APP_JS}</script>`, `
.tabs{display:flex;gap:4px;flex-wrap:wrap}.tab{background:none;border:0;color:var(--muted);font:inherit;font-size:14px;padding:8px;cursor:pointer;text-decoration:none;border-bottom:2px solid transparent}
#adminLink{display:contents}.tab.on{color:var(--ink);border-bottom-color:var(--gold)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0 16px}
.brief{border-left:3px solid var(--gold);background:var(--surface);padding:16px;border-radius:0 8px 8px 0;margin-bottom:24px;font-size:14px}
.folder{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:24px 0 8px}
.item{padding:16px 0;border-bottom:1px solid var(--line)}
.item a.t{color:var(--ink);font-weight:700;text-decoration:none;font-size:16px;line-height:1.4;overflow-wrap:anywhere}
.item a.t:hover{text-decoration:underline}
.item .line{font-size:14px;margin:4px 0 0}.item .meta{font-size:12px;color:var(--muted);margin-top:4px}
.item .acts{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.item.read a.t{color:var(--muted);font-weight:400}
.btn.on{border-color:var(--gold);color:var(--gold)}
.add{display:flex;gap:8px;flex-wrap:wrap}.add input{flex:1 1 220px}
.found{margin-top:16px}.found .card{margin-bottom:8px}.found ol{margin:8px 0;padding-left:24px;font-size:14px}
.src{display:flex;gap:8px;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.src .n{flex:1 1 200px;min-width:0;overflow-wrap:anywhere}
.src .acts{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.src select,.src input.f{font-size:14px;padding:4px 8px}
@media(max-width:520px){.top{flex-wrap:wrap;gap:8px}.tabs{width:100%;justify-content:space-between}}
details summary{cursor:pointer;color:var(--muted);font-size:14px;margin:16px 0 8px}
.empty{padding:40px 0;text-align:center}
`);
}

const APP_JS = `
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const S = { tab: "news", day: null, later: false, folder: "", me: null, feeds: [], data: null, found: null };
async function call(path, opt = {}) {
  const r = await fetch(path, { ...opt, headers: { "content-type": "application/json", ...(opt.headers || {}) }, body: opt.body ? JSON.stringify(opt.body) : undefined });
  if (r.status === 401) { location.href = "/login"; throw new Error("sign in"); }
  const v = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(v.error || ("error " + r.status));
  return v;
}
function note(s, bad) { const n = $("#note"); n.textContent = s || ""; n.className = "small " + (bad ? "bad" : "muted"); }
function when(ms) { if (!ms) return ""; const d = new Date(ms); return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }); }
function dayName(d) { const [y, m, dd] = d.split("-").map(Number); return new Date(y, m - 1, dd).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }); }

function tabs() {
  document.querySelectorAll(".tab[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === S.tab));
  $("#adminLink").innerHTML = S.me && S.me.role === "owner" ? '<a class="tab" href="/admin">Admin</a>' : "";
}

async function loadNews() {
  const q = S.later ? "?later=1" : (S.day ? "?day=" + S.day : "");
  S.data = await call("/api/items" + q);
  if (!S.day) S.day = S.data.day;
}

function newsView() {
  const d = S.data || { items: [], days: [] };
  const folders = [...new Set(d.items.map((i) => i.folder))];
  const items = d.items.filter((i) => !S.folder || i.folder === S.folder);
  const days = d.days.some((x) => x.day === S.day) ? d.days : [{ day: S.day, n: 0 }, ...d.days];
  let h = '<div class="bar">';
  h += '<button class="btn small' + (!S.later ? " on" : "") + '" data-act="all">All</button><button class="btn small' + (S.later ? " on" : "") + '" data-act="later">Later</button>';
  if (!S.later) h += '<select id="day" aria-label="Day">' + days.map((x) => '<option value="' + x.day + '"' + (x.day === S.day ? " selected" : "") + ">" + dayName(x.day) + " (" + x.n + ")</option>").join("") + "</select>";
  if (folders.length > 1) h += '<select id="folder" aria-label="Folder"><option value="">All folders</option>' + folders.map((f) => '<option' + (f === S.folder ? " selected" : "") + ">" + esc(f) + "</option>").join("") + "</select>";
  h += "</div>";
  if (d.brief && !S.later) h += '<div class="brief"><div class="small muted">Today in brief</div>' + esc(d.brief) + "</div>";
  if (!items.length) {
    h += '<div class="empty muted">' + (S.later ? "Nothing saved for later." : S.feeds.length ? "No new articles for this day yet. Sources are checked at 2:30 PM." : "Add your first source in Sources.") + "</div>";
    return h;
  }
  let last = null;
  for (const it of items) {
    if (it.folder !== last) { h += '<div class="folder">' + esc(it.folder) + "</div>"; last = it.folder; }
    h += '<div class="item' + (it.mark === "read" ? " read" : "") + '" data-id="' + it.id + '">';
    h += '<a class="t" href="' + esc(it.url) + '" target="_blank" rel="noopener" data-open="1">' + esc(it.title) + "</a>";
    if (it.line) h += '<p class="line">' + esc(it.line) + "</p>";
    h += '<div class="meta">' + esc(it.site) + (it.published ? " · " + when(it.published) : "") + "</div>";
    h += '<div class="acts">' + ["read", "later", "task"].map((m) => '<button class="btn small' + (it.mark === m ? " on" : "") + '" data-mark="' + m + '">' + ({ read: "Read", later: "Later", task: "Make it a task" })[m] + "</button>").join("") + "</div></div>";
  }
  return h;
}

function sourcesView() {
  const lim = S.me ? S.me.plan.feeds : 0;
  const active = S.feeds.filter((f) => f.active);
  const off = S.feeds.filter((f) => !f.active);
  const folders = [...new Set(["World", "Tech", "Japan", ...S.feeds.map((f) => f.folder)])];
  let h = '<h2>Sources</h2><p class="small muted">' + active.length + " of " + lim + " on the " + esc(S.me ? S.me.plan.name : "") + " plan. Checked every day at 2:30 PM, emailed at 3:00 PM.</p>";
  h += '<form class="add" id="addForm"><input id="url" type="url" inputmode="url" placeholder="Paste a site or feed address" aria-label="Site or feed address" required><button class="btn main" type="submit">Find feed</button></form>';
  h += '<datalist id="folders">' + folders.map((f) => "<option>" + esc(f) + "</option>").join("") + "</datalist>";
  if (S.found) {
    h += '<div class="found">';
    if (S.found.why) h += '<p class="bad small">' + esc(S.found.why) + "</p>";
    S.found.found.forEach((f, i) => {
      h += '<div class="card"><b>' + esc(f.title) + '</b><div class="small muted" style="overflow-wrap:anywhere">' + esc(f.url) + "</div><ol>" + f.sample.map((t) => "<li>" + esc(t) + "</li>").join("") + "</ol>";
      h += '<div class="add"><input class="f" list="folders" id="fold' + i + '" value="' + esc(S.folder || "World") + '" aria-label="Folder"><button class="btn main" data-sub="' + i + '">Subscribe</button></div></div>';
    });
    h += "</div>";
  }
  h += '<div style="margin-top:24px">';
  if (!active.length) h += '<p class="muted">No sources yet.</p>';
  for (const f of active) {
    h += '<div class="src" data-id="' + f.id + '"><div class="n"><b>' + esc(f.name) + '</b><div class="small ' + (f.lastError ? "bad" : "muted") + '">' + (f.lastError ? "Last check failed: " + esc(f.lastError) : f.lastOk ? "Checked " + when(f.lastOk) + " · " + (f.lastCount || 0) + " new" : "Not checked yet") + "</div></div>";
    h += '<div class="acts"><input class="f" list="folders" value="' + esc(f.folder) + '" data-folder="1" aria-label="Folder" size="10"><button class="btn small" data-off="1">Unsubscribe</button></div></div>';
  }
  h += "</div>";
  if (off.length) {
    h += "<details><summary>Unsubscribed (" + off.length + ")</summary>";
    for (const f of off) h += '<div class="src" data-id="' + f.id + '"><div class="n">' + esc(f.name) + '</div><div class="acts"><button class="btn small" data-on="1">Resubscribe</button></div></div>';
    h += "</details>";
  }
  h += '<div class="card" style="margin-top:40px"><h3>Today\\'s email</h3><p class="small muted">Check the sources now, or send the email now instead of waiting for 3:00 PM.</p><div class="bar"><button class="btn" data-run="fetch">Check sources now</button><button class="btn" data-run="mail">Send today\\'s email now</button></div></div>';
  return h;
}

function render() {
  tabs();
  $("#view").innerHTML = S.tab === "news" ? newsView() : sourcesView();
}

async function go(tab) {
  S.tab = tab; history.replaceState(null, "", "#" + tab);
  try { if (tab === "news") await loadNews(); else S.feeds = await call("/api/feeds"); } catch (e) { note(e.message, true); }
  render();
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("button, a");
  if (!t) return;
  try {
    if (t.dataset.tab) return go(t.dataset.tab);
    if (t.dataset.act) { e.preventDefault(); S.later = t.dataset.act === "later"; await loadNews(); return render(); }
    const item = t.closest(".item");
    if (item && t.dataset.mark) {
      const it = S.data.items.find((x) => x.id === item.dataset.id);
      const mark = it.mark === t.dataset.mark ? null : t.dataset.mark;
      await call("/api/items/" + it.id, { method: "PUT", body: { mark } });
      it.mark = mark; note(mark === "task" ? "It goes to your Desk tasks within a few minutes." : "");
      return render();
    }
    if (item && t.dataset.open) {
      const it = S.data.items.find((x) => x.id === item.dataset.id);
      if (!it.mark) { it.mark = "read"; call("/api/items/" + it.id, { method: "PUT", body: { mark: "read" } }).catch(() => {}); setTimeout(render, 300); }
      return;
    }
    if (t.dataset.sub !== undefined) {
      e.preventDefault();
      const f = S.found.found[Number(t.dataset.sub)];
      t.disabled = true;
      const folder = $("#fold" + t.dataset.sub).value;
      await call("/api/feeds", { method: "POST", body: { url: f.url, site: f.site, name: f.title, folder } });
      S.found = null; S.feeds = await call("/api/feeds"); S.me = await call("/api/me");
      note("Subscribed to " + f.title + ". New articles come in at the next check.");
      return render();
    }
    const src = t.closest(".src");
    if (src && (t.dataset.off || t.dataset.on)) {
      await call("/api/feeds/" + src.dataset.id, { method: "PUT", body: { active: !!t.dataset.on } });
      S.feeds = await call("/api/feeds"); S.me = await call("/api/me");
      return render();
    }
    if (t.dataset.run) {
      t.disabled = true; note(t.dataset.run === "fetch" ? "Checking your sources…" : "Sending…");
      const r = await call("/api/run/" + t.dataset.run, { method: "POST" });
      if (t.dataset.run === "fetch") note(r.sources + (r.sources === 1 ? " source" : " sources") + " checked, " + r.added + " new" + (r.failed ? ", " + r.failed + " failed" : "") + ".");
      else note(r.sent ? "Sent " + r.sent + " articles." : r.skipped ? "Not sent: " + r.skipped + "." : "Not sent: " + (r.error || "unknown") + ".", !r.sent);
      S.feeds = await call("/api/feeds"); return render();
    }
  } catch (err) { note(err.message, true); render(); }
});

document.addEventListener("submit", async (e) => {
  if (e.target.id !== "addForm") return;
  e.preventDefault();
  const b = e.target.querySelector("button"); b.disabled = true; note("Looking for a feed…");
  try { S.found = await call("/api/feeds/find", { method: "POST", body: { url: $("#url").value } }); note(""); } catch (err) { note(err.message, true); }
  render();
});

document.addEventListener("change", async (e) => {
  try {
    if (e.target.id === "day") { S.day = e.target.value; await loadNews(); return render(); }
    if (e.target.id === "folder") { S.folder = e.target.value; return render(); }
    if (e.target.dataset.folder) {
      const id = e.target.closest(".src").dataset.id;
      await call("/api/feeds/" + id, { method: "PUT", body: { folder: e.target.value } });
      S.feeds = await call("/api/feeds"); note("Folder changed.");
    }
  } catch (err) { note(err.message, true); }
});

(async () => {
  try { S.me = await call("/api/me"); S.feeds = await call("/api/feeds"); } catch (e) { return note(e.message, true); }
  go(location.hash === "#sources" || !S.feeds.length ? "sources" : "news");
})();
`;

// ---------- admin ----------
export function adminPage() {
  return page("Admin · Sift Daily", `
<div class="wrap">
  <div class="top"><a class="brand" href="/">Sift<span>.</span>Daily <span class="muted small">Admin</span></a><nav><a class="btn small" href="/app">App</a> <a class="btn small" href="/logout">Sign out</a></nav></div>
  <div id="note" class="small muted" role="status"></div>
  <section class="stats" id="stats"></section>
  <section><h2>Users</h2><div class="scroll"><table id="users"></table></div></section>
  <section><h2>Plans</h2><div class="scroll"><table id="plans"></table></div><p class="small muted">Plans are set in sift/src/plans.js. Sign-ups and payments are not open yet.</p></section>
  <section><h2>Recent runs</h2><div class="scroll"><table id="runs"></table></div></section>
</div>
<script>${ADMIN_JS}</script>`, `
section{margin:0 0 40px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.stat .v{font-size:28px;font-weight:700}.stat .k{font-size:12px;color:var(--muted)}
td.no{color:var(--bad)}
@media(max-width:720px){.stats{grid-template-columns:repeat(2,1fr)}}
`);
}

const ADMIN_JS = `
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const when = (ms) => ms ? new Date(ms).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
let D = null;
async function load() {
  const r = await fetch("/api/admin");
  if (!r.ok) { $("#note").textContent = "Couldn't load (" + r.status + ")."; return; }
  D = await r.json();
  const users = D.users;
  $("#stats").innerHTML = [
    ["Users", users.length], ["Paying", users.filter((u) => u.role !== "owner" && u.plan !== "free").length],
    ["Monthly revenue", "$" + D.mrr], ["AI cost, 30 days", "$" + D.ai30.usd.toFixed(4)],
  ].map(([k, v]) => '<div class="card stat"><div class="v">' + esc(v) + '</div><div class="k">' + esc(k) + "</div></div>").join("");
  $("#users").innerHTML = "<thead><tr><th>User</th><th>Plan</th><th>Sources</th><th>Articles, 7 days</th><th>Last email</th><th>Last seen</th><th>Email</th></tr></thead><tbody>" + users.map((u) =>
    "<tr><td>" + esc(u.name || u.id) + (u.role === "owner" ? ' <span class="muted small">owner</span>' : "") + '</td><td><select data-user="' + esc(u.id) + '" aria-label="Plan">' + D.plans.map((p) => "<option value=" + p.id + (p.id === u.plan ? " selected" : "") + ">" + esc(p.name) + "</option>").join("") + "</select></td><td>" + u.feeds + (u.failing ? ' <span class="bad small">' + u.failing + " failing</span>" : "") + "</td><td>" + u.items7 + "</td><td>" + when(u.lastMail) + "</td><td>" + when(u.lastSeen) + "</td><td>" + (u.hasEmail ? "set" : '<span class="bad">missing</span>') + "</td></tr>").join("") + "</tbody>";
  $("#plans").innerHTML = "<thead><tr><th>Plan</th><th>Monthly</th><th>Yearly</th><th>Sources</th><th>AI summaries</th></tr></thead><tbody>" + D.plans.map((p) => "<tr><td>" + esc(p.name) + "</td><td>$" + p.month + "</td><td>$" + p.year + "</td><td>" + p.feeds + "</td><td>" + (p.ai ? "Yes" : "No") + "</td></tr>").join("") + "</tbody>";
  $("#runs").innerHTML = "<thead><tr><th>When</th><th>What</th><th>User</th><th>Result</th><th>AI tokens</th></tr></thead><tbody>" + D.runs.map((r) => "<tr><td>" + when(r.at) + "</td><td>" + esc(r.kind) + "</td><td>" + esc(r.userId || "") + "</td><td" + (r.ok ? "" : ' class="no"') + ">" + esc(r.detail) + "</td><td>" + (r.tin || r.tout ? r.tin + " / " + r.tout : "") + "</td></tr>").join("") + "</tbody>";
}
document.addEventListener("change", async (e) => {
  const id = e.target.dataset.user;
  if (!id) return;
  const r = await fetch("/api/admin/users/" + encodeURIComponent(id), { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ plan: e.target.value }) });
  $("#note").textContent = r.ok ? "Plan changed." : "Couldn't change the plan.";
  load();
});
load();
`;
