// Offline shell. App files: stale-while-revalidate. Live data (GitHub, ntfy) is never cached here;
// the app keeps its own last-good copy in localStorage.
const VERSION = 'ms-v2-1';
const SHELL = [
  './', 'index.html', 'manifest.webmanifest', 'css/app.css',
  'js/app.js', 'js/api.js', 'js/logic.js', 'js/store.js', 'js/demo.js', 'js/ui.js',
  'js/views/common.js', 'js/views/now.js', 'js/views/movies.js', 'js/views/alerts.js', 'js/views/queue.js', 'js/views/settings.js',
  'demo/state.json', 'demo/laptop.json', 'demo/ntfy.jsonl', 'demo/runs.json', 'demo/settings.json', 'demo/wishlist.txt',
  'icons/icon-192.png', 'icons/apple-touch-icon.png', 'icons/favicon-64.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // api.github.com, ntfy.sh: straight to network
  e.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const hit = await cache.match(req, { ignoreSearch: true });
    const net = fetch(req).then((res) => {
      if (res && res.ok) cache.put(req, res.clone());
      return res;
    }).catch(() => null);
    if (hit) { e.waitUntil(net); return hit; }
    const res = await net;
    if (res) return res;
    if (req.mode === 'navigate') return (await cache.match('index.html')) || Response.error();
    return Response.error();
  })());
});
