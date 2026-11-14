/* Offline: the page and seed come from the network when it answers (so an update shows at once) and from the cache when offline.
   Icons and the manifest come from the cache first. /api/ always goes to the network. */
const CACHE = "routine-v2";
const SHELL = ["./", "index.html", "manifest.webmanifest", "seed.json", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.includes("/api/")) return;
  const fresh = e.request.mode === "navigate" || /\.(html|json)$/.test(u.pathname) || u.pathname.endsWith("/");
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = () => c.match(e.request, { ignoreSearch: true }).then(r => r || c.match("index.html"));
    const net = () => fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; });
    if (fresh) return net().catch(hit);
    return (await c.match(e.request, { ignoreSearch: true })) || net();
  }));
});
