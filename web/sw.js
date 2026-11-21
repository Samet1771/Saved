// App-shell cache only. /api and /media are never cached.
const CACHE = 'saved-shell-v5';
const SHELL = [
  './', 'index.html', 'styles.css', 'app.js', 'manifest.webmanifest',
  'js/api.js', 'js/util.js', 'js/store.js', 'js/router.js', 'js/library.js', 'js/reorder.js',
  'js/feed.js', 'js/media.js', 'js/details.js', 'js/itempage.js', 'js/collections.js', 'js/settings.js', 'js/activity.js',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const scope = new URL(self.registration.scope).pathname;
  const rel = url.pathname.slice(scope.length);
  if (rel.startsWith('api/') || rel.startsWith('media/') || rel.startsWith('dev/')) return;
  // Network first so a LAN deploy is picked up immediately; cache is the offline fallback.
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req.mode === 'navigate' ? './' : req, copy));
      }
      return res;
    }).catch(() => caches.match(req.mode === 'navigate' ? './' : req, { ignoreSearch: true })
      .then((r) => r || caches.match('./'))),
  );
});
