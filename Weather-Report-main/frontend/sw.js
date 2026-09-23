const CACHE_NAME = "weathergpt-shell-v16"; // v16: weather-synchronized in-card hero scene

const SHELL_FILES = [
  "/",
  "/index.html",
  "/style.css",
  "/script.js",
  "/voice-output.js",
  "/radar-map.js",
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon.png",
  "/icons/logo.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never cache API calls — weather data, transcription, and reports must
  // always be fresh. Let these hit the network normally.
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // Navigations (including PWA start_url "/?source=pwa") fall back to the
  // cached shell on network failure — previously a first-ever offline
  // launch could miss the cache because of the query string.
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches
          .match("/")
          .then((cached) => cached || caches.match("/index.html"))
      )
    );
    return;
  }

  // App shell: cache-first, falling back to network, so the UI loads
  // instantly (and still loads offline) even though live data won't.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;

      return fetch(event.request)
        .then((response) => {
          if (event.request.method === "GET" && response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => cached);
    })
  );
});
