const CACHE = 'dealwatch-static-v4';
const CORE = ['/', '/index.html', '/search.html', '/catalogue.html', '/style.css', '/manifest.webmanifest', '/icon.svg'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(CORE)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim())
));
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== location.origin) return;
  // Navigation and HTML must check the network first, otherwise a returning
  // shopper can remain stuck on an old catalogue after a deployment.
  const url = new URL(event.request.url);
  if (event.request.mode === 'navigate' || url.pathname.match(/\.(?:html|css|js)$/)) {
    event.respondWith(fetch(event.request).then(response => {
      const copy = response.clone();
      if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, copy));
      return response;
    }).catch(() => caches.match(event.request).then(cached => cached || caches.match('/index.html'))));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {
    const copy = response.clone();
    if (response.ok && url.pathname.match(/\.(?:css|js|svg|webmanifest)$/)) caches.open(CACHE).then(cache => cache.put(event.request, copy));
    return response;
  }).catch(() => caches.match('/index.html'))));
});
