const CACHE = 'jarvis-pwa-v3';
const SHELL = [
  '/',
  '/mobile',
  '/manifest.json',
  '/pages/device_control.html',
  '/pages/memory_browser.html',
  '/pages/calendar.html',
  '/pages/documents.html',
  '/pages/health.html',
  '/pages/notifications.html',
  '/pages/automations.html',
  '/pages/skills.html',
  '/pages/branches.html',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api') || url.pathname === '/ws') {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});

self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type !== 'show-notification') {
    return;
  }
  self.registration.showNotification(data.title || 'Jarvis', {
    body: data.body || '',
    tag: data.tag || 'jarvis-notification',
    renotify: false,
  });
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow('/');
      }
      return undefined;
    })
  );
});
