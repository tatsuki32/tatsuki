/*
 * VINEPRO ボード — claude.ai の window.claude（db / user / room / sample）を自前サーバーにつなぐアダプター。
 * index.html の本体は claude.ai 版と同じまま動く。ここで足しているのは、右上のアカウントメニューだけ。
 */
(function () {
  "use strict";
  const PEER = "p" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
  const H = { "Content-Type": "application/json", "X-VB": "1" };
  const clone = (o) => (o == null ? o : JSON.parse(JSON.stringify(o)));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  function toLogin() { location.href = "/login?next=" + encodeURIComponent(location.pathname); }
  async function api(method, path, body) {
    let res;
    try { res = await fetch(path, { method, headers: H, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" }); }
    catch (e) { throw { code: "unavailable", message: String(e) }; }
    if (res.status === 401) { toLogin(); throw { code: "unauthenticated" }; }
    let j = null; try { j = await res.json(); } catch (e) {}
    if (!res.ok) throw { code: (j && j.error) || "http_" + res.status, message: (j && j.message) || "" };
    return j;
  }

  const meP = api("GET", "/api/me");
  let profiles = {};
  const profilesP = api("GET", "/api/profiles").then((p) => (profiles = p)).catch(() => ({}));

  function avatar(name, color) {
    const ch = (name || "?").trim().charAt(0).toUpperCase() || "?";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="${color}"/><text x="16" y="21.5" font-size="15" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif" font-weight="700">${ch.replace(/[<&"]/g, "")}</text></svg>`;
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  // ================= realtime stream =================
  const cache = {};      // col -> Map(id -> data)
  const seqOf = {};      // col -> last applied seq
  const loading = {};    // col -> Promise
  const buffered = {};   // col -> events received while loading
  const listeners = {};  // col -> Set({next, error})
  let peers = [];
  const peerCbs = new Set();

  function snap(col, changes) {
    const docs = [...cache[col].entries()].map(([id, d]) => docObj(id, d));
    return { docs, size: docs.length, empty: !docs.length, metadata: { fromCache: false, hasPendingWrites: false }, docChanges: () => changes };
  }
  function docObj(id, d) { const f = clone(d); return { id, exists: true, data: () => f, metadata: { fromCache: false, hasPendingWrites: false } }; }
  function emit(col, changes, force) {
    if (!changes.length && !force) return;
    for (const l of listeners[col] || []) { try { l.next(snap(col, changes)); } catch (e) { console.error(e); } }
  }

  async function load(col) {
    buffered[col] = [];
    const p = (async () => {
      const r = await api("GET", "/api/db/" + col);
      const old = cache[col] || new Map();
      const fresh = new Map(r.docs.map((d) => [d.id, d.data]));
      const changes = [];
      for (const [id, d] of fresh) {
        if (!old.has(id)) changes.push({ type: "added", doc: docObj(id, d) });
        else if (!same(old.get(id), d)) changes.push({ type: "modified", doc: docObj(id, d) });
      }
      for (const [id, d] of old) if (!fresh.has(id)) changes.push({ type: "removed", doc: docObj(id, d) });
      cache[col] = fresh; seqOf[col] = r.seq;
      emit(col, changes, !old.size); // the first snapshot is delivered even when the collection is empty
      const evs = buffered[col]; buffered[col] = null;
      for (const ev of evs) apply(ev);
    })();
    loading[col] = p;
    try { await p; } finally { if (loading[col] === p) loading[col] = null; }
  }
  function apply(ev) {
    const col = ev.col;
    if (!cache[col]) return;
    if (buffered[col]) { buffered[col].push(ev); return; }
    if (ev.seq <= (seqOf[col] || 0)) return;
    seqOf[col] = ev.seq;
    const map = cache[col]; const had = map.has(ev.id);
    if (ev.type === "delete") {
      if (!had) return;
      if (ev.origin === PEER) { map.delete(ev.id); return; }
      const old = map.get(ev.id); map.delete(ev.id);
      emit(col, [{ type: "removed", doc: docObj(ev.id, old) }]);
      return;
    }
    // our own write coming back: the board already shows it, so only refresh the copy here
    if (ev.origin === PEER) { map.set(ev.id, ev.data); return; }
    if (had && same(map.get(ev.id), ev.data)) return;
    map.set(ev.id, ev.data);
    emit(col, [{ type: had ? "modified" : "added", doc: docObj(ev.id, ev.data) }]);
  }

  let es = null; let everConnected = false;
  function connect() {
    es = new EventSource("/api/stream?peer=" + PEER);
    es.addEventListener("hello", (m) => {
      const d = JSON.parse(m.data);
      setPeers(d.peers);
      // after a reconnect (or server restart) re-read everything we follow
      if (everConnected) for (const col of Object.keys(cache)) load(col).catch(() => {});
      everConnected = true;
      sendPresence(true);
    });
    es.addEventListener("doc", (m) => apply(JSON.parse(m.data)));
    es.addEventListener("peers", (m) => setPeers(JSON.parse(m.data).peers));
    es.addEventListener("presence", (m) => {
      const d = JSON.parse(m.data); const p = peers.find((x) => x.peer === d.peer); if (!p) return;
      p.presence = d.presence; for (const cb of peerCbs) cb({ peers: peers.map(viewPeer), joined: [], left: [] });
    });
    es.addEventListener("auth", () => { es.close(); location.reload(); });
    es.addEventListener("reload", () => location.reload());
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) setTimeout(() => { api("GET", "/api/me").then(connect).catch(() => setTimeout(connect, 5000)); }, 3000);
    };
  }

  // ================= db =================
  function collection(col) {
    return {
      path: col,
      onSnapshot(next, error) {
        const l = { next, error };
        (listeners[col] = listeners[col] || new Set()).add(l);
        if (!cache[col]) {
          cache[col] = new Map();
          load(col).catch((e) => { for (const x of listeners[col]) if (x.error) x.error(e); });
        } else if (!loading[col]) {
          const docs = [...cache[col].entries()].map(([id, d]) => ({ type: "added", doc: docObj(id, d) }));
          setTimeout(() => next(snap(col, docs)), 0);
        }
        return () => listeners[col].delete(l);
      },
      doc(id) {
        const path = "/api/db/" + col + "/" + encodeURIComponent(id);
        function write(method, data) {
          return api(method, path, method === "DELETE" ? { origin: PEER } : { data, origin: PEER });
        }
        return {
          id, path: col + "/" + id,
          async get() {
            if (cache[col] && !loading[col] && cache[col].has(id)) return docObj(id, cache[col].get(id));
            const r = await api("GET", "/api/db/" + col); const d = r.docs.find((x) => x.id === id);
            return d ? docObj(id, d.data) : { id, exists: false, data: () => undefined, metadata: {} };
          },
          set(data) { return write("PUT", data); },
          update(data) {
            return write("PATCH", data).catch((e) => { if (e.code === "not_found") e.code = "invalid_argument_missing"; throw e; });
          },
          delete() { return write("DELETE", null); },
        };
      },
    };
  }
  const db = { collection, doc: (p) => { const [c, i] = p.split("/"); return collection(c).doc(i); } };

  // ================= user =================
  const user = {
    id: async () => (await meP).id,
    can: async (what) => { const me = await meP; return what === "data.write" ? me.canEdit : true; },
    isOwner: async () => (await meP).isOwner,
    canEdit: async () => (await meP).canEdit,
    me: async () => { const me = await meP; return { id: me.id, name: me.name, color: me.color, avatarUrl: avatar(me.name, me.color), email: me.email, isOwner: me.isOwner, canEdit: me.canEdit }; },
    profiles: async (ids) => {
      const me = await meP;
      if ([].concat(ids).some((i) => !profiles[i])) { try { profiles = await api("GET", "/api/profiles"); } catch (e) {} }
      return Object.fromEntries([].concat(ids).map((i) => {
        const p = profiles[i] || { name: "", color: "#888" };
        return [i, { id: i, name: p.name, color: p.color, avatarUrl: avatar(p.name, p.color), email: null, isMe: i === me.id, guest: false }];
      }));
    },
  };

  // ================= room (presence & cursors) =================
  let myPresence = {}; let presT = 0; let presLast = 0; let presDirty = false;
  function sendPresence(force) {
    if (!presDirty && !force) return;
    presDirty = false; presLast = Date.now();
    api("POST", "/api/presence", { peer: PEER, presence: myPresence }).catch(() => {});
  }
  function viewPeer(p) { return { id: p.peer, kind: "viewer", by: p.by, isMe: p.peer === PEER, presence: p.presence || {} }; }
  function setPeers(list) {
    const before = new Set(peers.map((p) => p.peer)); const after = new Set(list.map((p) => p.peer));
    const joined = list.filter((p) => !before.has(p.peer)).map(viewPeer);
    const left = peers.filter((p) => !after.has(p.peer)).map(viewPeer);
    peers = list;
    for (const cb of peerCbs) cb({ peers: peers.map(viewPeer), joined, left });
  }
  const room = {
    presence(obj) {
      myPresence = Object.assign({}, myPresence, obj); presDirty = true;
      const wait = 90 - (Date.now() - presLast);
      clearTimeout(presT);
      if (wait <= 0) sendPresence(); else presT = setTimeout(sendPresence, wait);
      return Promise.resolve();
    },
    onPeers(cb) { peerCbs.add(cb); cb({ peers: peers.map(viewPeer), joined: peers.map(viewPeer), left: [] }); return () => peerCbs.delete(cb); },
  };

  // ================= sample (translation) =================
  const sample = {
    async json(prompt) {
      const r = await api("POST", "/api/sample", { prompt });
      return r.result;
    },
  };

  window.claude = {
    use: async (name) => {
      const me = await meP;
      if (name === "db") return db;
      if (name === "user") return user;
      if (name === "room") return room;
      if (name === "sample") return me.translate && me.canEdit ? sample : null;
      return null;
    },
  };
  meP.then(() => connect()).catch(() => {});

  // ================= account menu (top right) =================
  const T = {
    ja: { history: "編集履歴", members: "メンバー管理", password: "パスワード変更", logout: "ログアウト", owner: "管理者", editor: "編集できる", viewer: "見るだけ",
      cur: "今のパスワード", nw: "新しいパスワード（8文字以上）", save: "変更する", cancel: "やめる", changed: "パスワードを変更しました", bad: "今のパスワードが違います", short: "8文字以上にしてください" },
    vi: { history: "Lịch sử chỉnh sửa", members: "Quản lý thành viên", password: "Đổi mật khẩu", logout: "Đăng xuất", owner: "Quản trị", editor: "Được chỉnh sửa", viewer: "Chỉ xem",
      cur: "Mật khẩu hiện tại", nw: "Mật khẩu mới (từ 8 ký tự)", save: "Đổi", cancel: "Huỷ", changed: "Đã đổi mật khẩu", bad: "Mật khẩu hiện tại không đúng", short: "Cần từ 8 ký tự" },
    en: { history: "Edit history", members: "Members", password: "Change password", logout: "Sign out", owner: "Admin", editor: "Can edit", viewer: "View only",
      cur: "Current password", nw: "New password (8+ characters)", save: "Change", cancel: "Cancel", changed: "Password changed", bad: "Current password is wrong", short: "Use 8 or more characters" },
  };
  const tt = (k) => { const l = document.documentElement.lang; return (T[l] || T.ja)[k]; };

  const css = `
  #app{grid-template-columns:minmax(0,1fr)}
  .vb-acct{flex:none;display:flex}
  .vb-acct>button{display:grid;place-items:center;width:32px;height:32px;padding:0;border:1px solid var(--rule,#ccc);border-radius:50%;background:var(--chrome,#fff);cursor:pointer}
  .vb-acct>button img{width:26px;height:26px;border-radius:50%}
  .vb-acct>button[aria-expanded="true"]{outline:2px solid var(--accent,#2B55C9)}
  .vb-menu{position:fixed;z-index:1000;width:min(260px,calc(100vw - 24px));background:var(--chrome,#fff);color:var(--fg,#111);border:1px solid var(--rule,#ccc);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.18);padding:6px;display:flex;flex-direction:column;box-sizing:border-box}
  .vb-menu .who{padding:6px 10px 8px;font-size:12px;color:var(--muted,#666);border-bottom:1px solid var(--rule,#ddd);margin-bottom:4px;overflow-wrap:anywhere}
  .vb-menu .who b{display:block;font-size:14px;color:var(--fg,#111)}
  .vb-menu a,.vb-menu button{all:unset;cursor:pointer;padding:8px 10px;border-radius:6px;font-size:13px}
  .vb-menu a:hover,.vb-menu button:hover,.vb-menu a:focus-visible,.vb-menu button:focus-visible{background:var(--chrome-2,#eee)}
  .vb-dlg{position:fixed;inset:0;z-index:2000;display:grid;place-items:center;background:rgba(0,0,0,.35);padding:16px}
  .vb-dlg form{background:var(--chrome,#fff);color:var(--fg,#111);border-radius:12px;padding:18px;width:min(340px,100%);display:flex;flex-direction:column;gap:10px;box-sizing:border-box}
  .vb-dlg input{font:inherit;padding:8px 10px;border:1px solid var(--rule,#ccc);border-radius:8px;background:var(--paper,#fff);color:#111}
  .vb-dlg .row{display:flex;gap:8px;justify-content:flex-end}
  .vb-dlg button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--rule,#ccc);background:var(--chrome-2,#eee);color:var(--fg,#111);cursor:pointer}
  .vb-dlg button.pri{background:var(--accent,#2B55C9);color:var(--accent-ink,#fff);border-color:transparent}
  .vb-dlg .msg{font-size:12px;color:var(--danger,#b42a2a);min-height:1em}
`;

  async function mountMenu() {
    const me = await meP;
    const top = document.querySelector("header.top"); if (!top) return;
    const st = document.createElement("style"); st.textContent = css; document.head.append(st);
    const wrap = document.createElement("div"); wrap.className = "vb-acct";
    const btn = document.createElement("button"); btn.type = "button";
    const img = document.createElement("img"); img.src = avatar(me.name, me.color); img.alt = "";
    btn.title = me.name; btn.setAttribute("aria-haspopup", "menu"); btn.setAttribute("aria-expanded", "false");
    btn.append(img); wrap.append(btn); top.append(wrap);
    let menu = null;
    function close() { if (menu) { menu.remove(); menu = null; btn.setAttribute("aria-expanded", "false"); } }
    btn.addEventListener("click", (e) => {
      e.stopPropagation(); if (menu) return close();
      menu = document.createElement("div"); menu.className = "vb-menu";
      const who = document.createElement("div"); who.className = "who";
      const b = document.createElement("b"); b.textContent = me.name; who.append(b, document.createTextNode(`${me.email} · ${tt(me.role)}`)); menu.append(who);
      const link = (href, label) => { const a = document.createElement("a"); a.href = href; a.textContent = label; menu.append(a); };
      link("/admin#history", tt("history"));
      if (me.isOwner) link("/admin#members", tt("members"));
      const pw = document.createElement("button"); pw.textContent = tt("password"); pw.onclick = () => { close(); passwordDialog(); }; menu.append(pw);
      const lo = document.createElement("button"); lo.textContent = tt("logout");
      lo.onclick = async () => { try { await api("POST", "/api/logout", {}); } catch (e) {} location.href = "/login"; };
      menu.append(lo); document.body.append(menu); btn.setAttribute("aria-expanded", "true");
      const r = btn.getBoundingClientRect();
      const w = menu.offsetWidth;
      menu.style.top = r.bottom + 6 + "px"; menu.style.left = Math.max(12, Math.min(r.right - w, innerWidth - w - 12)) + "px";
    });
    document.addEventListener("click", (e) => { if (menu && !wrap.contains(e.target) && !menu.contains(e.target)) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    addEventListener("resize", close);
  }
  function passwordDialog() {
    const d = document.createElement("div"); d.className = "vb-dlg";
    d.innerHTML = `<form><b></b><input type="password" autocomplete="current-password" required><input type="password" autocomplete="new-password" minlength="8" required><div class="msg"></div><div class="row"><button type="button" class="c"></button><button class="pri"></button></div></form>`;
    const f = d.querySelector("form"); const [cur, nw] = f.querySelectorAll("input"); const msg = f.querySelector(".msg");
    f.querySelector("b").textContent = tt("password"); cur.placeholder = tt("cur"); nw.placeholder = tt("nw");
    f.querySelector(".c").textContent = tt("cancel"); f.querySelector(".pri").textContent = tt("save");
    f.querySelector(".c").onclick = () => d.remove();
    f.onsubmit = async (e) => {
      e.preventDefault(); msg.textContent = "";
      if (nw.value.length < 8) { msg.textContent = tt("short"); return; }
      try { await api("POST", "/api/me/password", { current: cur.value, new: nw.value }); d.remove(); alert(tt("changed")); }
      catch (err) { msg.textContent = err.code === "bad_current" ? tt("bad") : err.code; }
    };
    document.body.append(d); cur.focus();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountMenu); else mountMenu();
})();
