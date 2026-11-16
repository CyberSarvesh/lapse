// Lapse service worker: shows reminder notifications and opens the right page when tapped (D31).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  const title = data.title || "Lapse reminder";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    tag: data.tag || "lapse",
    renotify: true,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data,
    actions: Array.isArray(data.actions) ? data.actions.slice(0, 2) : [],
  }));
});

self.addEventListener("notificationclick", (event) => {
  const data = event.notification.data || {};
  event.notification.close();
  // Buttons open the same one-time confirmation pages as the email buttons.
  const url = event.action === "renew" ? data.renewUrl
    : event.action === "snooze" ? data.snoozeUrl
    : data.url || "/";
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const same = windows.find((w) => w.url === new URL(url, self.location.origin).href);
    if (same) return same.focus();
    return self.clients.openWindow(url);
  })());
});
