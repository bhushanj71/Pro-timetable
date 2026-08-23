/* ProfSchedule AI service worker.
   Scope is the site root, which is why this file must be served from /sw.js
   rather than /static/js/ — a worker can only control paths at or below its
   own location. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

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

  // Focus an existing tab if the app is already open, rather than piling up
  // new windows every time a reminder is tapped.
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
