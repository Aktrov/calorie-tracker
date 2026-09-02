/* calorie-tracker service worker — minimal offline shell.
   Network-first for navigations and API calls (so data stays fresh).
   Stale-while-revalidate for static assets: serve the cached copy instantly,
   but always re-fetch in the background so edits land on the next load without
   needing a CACHE version bump. */
const CACHE = "calorie-tracker-v11";
const CORE = ["", "history", "profile", "static/logo.svg", "static/styles.css", "static/bg.svg", "static/bg-dark.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => {
      const base = self.registration.scope;
      return c.addAll(CORE.map((p) => base + p)).catch(() => {});
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const isStatic = url.pathname.includes("/static/");
  if (isStatic) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const fetching = fetch(req)
          .then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
            return res;
          })
          .catch(() => hit);
        return hit || fetching;
      })
    );
    return;
  }

  event.respondWith(
    fetch(req)
      .then((res) => {
        if (req.mode === "navigate") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match(self.registration.scope)))
  );
});
