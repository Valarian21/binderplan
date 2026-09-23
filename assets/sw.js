/* Binderplan – Service Worker (Phase 6, 10.09.2026).
 *
 * Hält die App-Hülle (/app, assets/*) und die Daten der zuletzt geöffneten Binder vor, damit
 * die Werkbank vor dem Regal auch ohne Netz aufgeht. Strategie: Netz zuerst, Cache als
 * Rückfall – die Dateien tragen ihre Version in der Adresse (?v=), Bilder überlässt er dem
 * Browser-Cache (immutable-Header). Schreibzugriffe werden nicht gepuffert: wer offline eine
 * Karte legt, sieht „Fehler beim Speichern" und der Binder holt sich beim nächsten Netz den
 * Stand vom Server (updated_at-Prüfung). */
const CACHE = 'bp-huelle-4';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
// Abmelden räumt den Cache – auf einem geteilten Gerät darf der nächste Nutzer keine fremden Binder sehen.
self.addEventListener('message', (e) => {
  if (e.data === 'leeren') e.waitUntil(caches.delete(CACHE));
});

function huelle(p) { return p === '/app' || p.startsWith('/app/') || (p.startsWith('/assets/') && /\.(js|css|woff2)(\?|$)/.test(p)); }
function daten(url) {
  const p = url.pathname;
  return p === '/api/meta' || p === '/api/binders' || /^\/api\/binders\/[^/]+$/.test(p) || p === '/api/auth/me';
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (!(huelle(url.pathname) || daten(url))) return;
  e.respondWith(
    fetch(req).then((antwort) => {
      if (antwort && antwort.ok) {
        const kopie = antwort.clone();
        caches.open(CACHE).then((c) => c.put(req, kopie)).catch(() => {});
      }
      return antwort;
    }).catch(() => caches.match(req, { ignoreVary: true }).then((treffer) => {
      if (!treffer) return new Response('', { status: 503, statusText: 'offline' });
      // Kennzeichnen: die App sagt dann „Server nicht erreichbar", statt einen alten
      // Anmeldestand als aktuellen auszugeben.
      const h = new Headers(treffer.headers); h.set('X-Bp-Cache', '1');
      return treffer.blob().then((b) => new Response(b, { status: treffer.status, statusText: treffer.statusText, headers: h }));
    }))
  );
});
