/*
 * ローカル開発用のモック: claude.ai の window.claude（db / user / room / sample）を
 * ブラウザだけで再現する。データは ../data/all.json を初期値にして localStorage に保存する。
 * 本番（claude.ai や自前ホスティング）では読み込まないこと。
 */
(function () {
  const KEY = "vb-mock-db";
  const listeners = {}; // col -> Set(fn)
  let store = null;     // { col: { id: doc } }
  const ready = (async () => {
    try { store = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { store = null; }
    if (!store) {
      const res = await fetch("../data/all.json");
      const all = await res.json();
      store = {};
      for (const col of Object.keys(all)) {
        store[col] = {};
        for (const d of all[col]) { const { id, ...rest } = d; store[col][id] = rest; }
      }
      persist();
    }
  })();
  function persist() { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {} }
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function merge(a, b) {
    const o = Object.assign({}, a);
    for (const k in b) o[k] = b[k] && typeof b[k] === "object" && !Array.isArray(b[k]) && o[k] && typeof o[k] === "object" ? merge(o[k], b[k]) : b[k];
    return o;
  }
  function snapDoc(id, data) { const frozen = clone(data); return { id, exists: true, data: () => frozen, metadata: { fromCache: false, hasPendingWrites: false } }; }
  function emit(col, changes) {
    const docs = Object.entries(store[col] || {}).map(([id, d]) => snapDoc(id, d));
    for (const fn of listeners[col] || []) fn({ docs, size: docs.length, empty: !docs.length, metadata: { fromCache: false, hasPendingWrites: false }, docChanges: () => changes });
  }
  function collection(col) {
    return {
      path: col,
      onSnapshot(next) {
        (listeners[col] = listeners[col] || new Set()).add(next);
        ready.then(() => {
          const docs = Object.entries(store[col] || {}).map(([id, d]) => snapDoc(id, d));
          next({ docs, size: docs.length, empty: !docs.length, metadata: { fromCache: false, hasPendingWrites: false }, docChanges: () => docs.map((doc) => ({ type: "added", doc })) });
        });
        return () => listeners[col].delete(next);
      },
      doc(id) {
        return {
          id, path: col + "/" + id,
          async get() { await ready; const d = (store[col] || {})[id]; return d ? snapDoc(id, d) : { id, exists: false, data: () => undefined, metadata: {} }; },
          async set(data) { await ready; store[col] = store[col] || {}; const isNew = !store[col][id]; store[col][id] = clone(data); persist(); emit(col, [{ type: isNew ? "added" : "modified", doc: snapDoc(id, data) }]); },
          async update(data) { await ready; const cur = (store[col] || {})[id]; if (!cur) throw { code: "invalid_argument", message: "no such document" }; store[col][id] = merge(cur, clone(data)); persist(); emit(col, [{ type: "modified", doc: snapDoc(id, store[col][id]) }]); },
          async delete() { await ready; const cur = (store[col] || {})[id]; if (!cur) return; delete store[col][id]; persist(); emit(col, [{ type: "removed", doc: snapDoc(id, cur) }]); },
        };
      },
    };
  }
  const db = { collection, doc: (path) => { const [c, i] = path.split("/"); return collection(c).doc(i); } };
  const user = {
    id: async () => "u_local_dev", can: async () => true, isOwner: async () => true, canEdit: async () => true,
    me: async () => ({ id: "u_local_dev", name: "Local", avatarUrl: "", color: "#2B55C9", email: null, isOwner: true, canEdit: true }),
    profiles: async (ids) => Object.fromEntries([].concat(ids).map((i) => [i, { id: i, name: "Local", avatarUrl: "", color: "#2B55C9", email: null, isMe: true, guest: false }])),
  };
  window.claude = {
    use: async (name) => {
      if (name === "db") return db;
      if (name === "user") return user;
      return null; // room（同時編集のカーソル）と sample（自動翻訳）はローカルでは無効
    },
  };
  window.__resetMockDb = () => { localStorage.removeItem(KEY); location.reload(); };
})();
