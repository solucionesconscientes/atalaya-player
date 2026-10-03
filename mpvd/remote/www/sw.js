// Minimal service worker: caches the app shell so the PWA opens instantly; API calls always go to the network.
const CACHE = 'mpv-uos-remote-v4';
// H49/G6 · /i18n.js lleva las cadenas en el idioma de ESTE móvil: entra en la caché como el resto del armazón,
// así que sin red la página sigue saliendo traducida (y si faltara, en castellano, que es la regla).
const SHELL = ['/', '/app.js', '/style.css', '/i18n.js', '/manifest.webmanifest', '/icon.svg', '/icon-192.png',
  '/downloads', '/downloads.js'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname === '/events') return;
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});
