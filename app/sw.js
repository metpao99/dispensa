// Cache dell'app per l'uso offline. Le chiamate /api/ vanno sempre al NAS.
// Quando modifichi i file dell'app, cambia il numero di versione.
const CACHE = "dispensa-nas-v11";
const FILES = ["./", "index.html", "app.js", "zxing.min.js", "manifest.webmanifest",
  "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || caches.match("index.html")))
  );
});

// Notifiche: il segnale arriva vuoto, il testo si chiede al NAS.
self.addEventListener("push", e => {
  e.waitUntil((async () => {
    let title = "Prodotti in scadenza", body = "Apri la Dispensa per vedere cosa scade.";
    try {
      const r = await fetch("/api/alerts", { credentials: "same-origin", cache: "no-store" });
      if (r.ok){ const a = await r.json(); if (a.count){ title = a.title; body = a.body; } else { body = "Per ora nessun prodotto in scadenza."; } }
    } catch {}
    await self.registration.showNotification(title, { body, icon: "icons/icon-192.png", badge: "icons/icon-192.png", tag: "scadenze" });
  })());
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then(ws => ws.length ? ws[0].focus() : self.clients.openWindow("./")));
});
