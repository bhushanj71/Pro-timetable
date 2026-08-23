/* ProfSchedule AI service worker.
   Served from the site root so its scope covers every page — a worker under
   /static/ could only control /static/. */

const CACHE = "profschedule-v1";
const SHELL = ["/static/css/style.css", "/static/js/app.js", "/static/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
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

/* A fetch handler is REQUIRED for installability — Chrome will not fire
   beforeinstallprompt without one, so there'd be no "Add to Home Screen".
   Network-first: the schedule must never be served stale, but cached static
   assets keep the shell usable on a flaky connection. */
self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Never cache API responses — a stale timetable is worse than no timetable.
  if (url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && (url.pathname.startsWith("/static/") || url.pathname === "/manifest.json")) {
          const copy = response.clone();
          caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match("/static/css/style.css")))
  );
});

/* ---------------- Push ---------------- */
self.addEventListener("push", (event) => {
  let data = { title: "ProfSchedule AI", body: "You have a reminder.", url: "/dashboard" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (_) {
    if (event.data) data.body = event.data.text();
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      // Collapses repeat pushes for the same reminder instead of stacking.
      tag: data.tag || "profschedule-reminder",
      renotify: true,
      requireInteraction: false,
      data: { url: data.url || "/dashboard" },
      vibrate: [180, 80, 180],
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.url || "/dashboard";

  // Focus an existing window rather than piling up new ones.
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
