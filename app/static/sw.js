// Service worker minimal : rend l'application installable et met en cache les fichiers statiques.
// Les pages et les données passent toujours par le réseau (jamais de points périmés).
const CACHE = "cr-static-v5";
const ASSETS = ["/static/style.css?v=5", "/static/app.js?v=3", "/static/icon.svg", "/static/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "GET" && url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
  }
});
