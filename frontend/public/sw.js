/* Lexora HRM service worker (Stream G4).
 *
 * What it does, honestly:
 *  - keeps an OFFLINE SHELL: the app page itself and its built files, so the self-service
 *    screens (Mobile Home, Punch, Leave, Pay stubs) open without a network
 *  - keeps the LAST GOOD COPY of a short allow-list of read-only API answers those screens
 *    need (punch context, leave balances and requests, payslip list, my tickets, my prefs,
 *    employee list), network-first — so offline you see what you saw last time
 *  - never caches anything else: no POST/PUT, no PDFs, no files, no other API routes
 *  - the queue of offline punches lives in IndexedDB in the page (src/pwa/queue.ts), not here
 *  - `clearOfflineCaches()` (called on logout) wipes every cache below
 * Bump VERSION to ship a new worker; the page shows "a new version is ready" and reloads.
 */
const VERSION = "lx-sw-v3";   // v3: Lexora brand icons
const SHELL = `${VERSION}-shell`;
const ASSETS = `${VERSION}-assets`;
const API = `${VERSION}-api`;

const BRAND_FILES = ["/icon.svg", "/icon-192.png", "/icon-512.png", "/favicon.ico"];
const SHELL_URLS = ["/", "/index.html", "/manifest.webmanifest", ...BRAND_FILES];
const API_ALLOW = [
  "/api/time/mobile/context", "/api/leave/balances", "/api/leave/requests", "/api/leave/types",
  "/api/payroll/payslips", "/api/engage/me/prefs", "/api/engage/helpdesk/tickets",
  "/api/engage/settings", "/api/employees", "/api/assets/mine",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS).catch(() => null)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
  if (event.data === "CLEAR_CACHES") event.waitUntil(caches.keys().then((ks) => Promise.all(ks.map((k) => caches.delete(k)))));
});

function isApiAllowed(url) {
  return API_ALLOW.some((p) => url.pathname === p || url.pathname.startsWith(p + "?") || url.pathname.startsWith(p + "/"));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 1. Navigations: network first, fall back to the cached app page (SPA shell).
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const c = await caches.open(SHELL);
        c.put("/index.html", fresh.clone()).catch(() => null);
        return fresh;
      } catch (e) {
        const c = await caches.open(SHELL);
        return (await c.match("/index.html")) || (await c.match("/")) || Response.error();
      }
    })());
    return;
  }

  // 2. Built files (hashed): cache first, refresh in the background.
  if (url.pathname.startsWith("/assets/") || BRAND_FILES.includes(url.pathname) || url.pathname === "/manifest.webmanifest") {
    event.respondWith((async () => {
      const c = await caches.open(ASSETS);
      const hit = await c.match(req);
      const refresh = fetch(req).then((r) => { if (r && r.ok) c.put(req, r.clone()); return r; }).catch(() => null);
      return hit || (await refresh) || Response.error();
    })());
    return;
  }

  // 3. The short allow-list of API reads: network first, last good copy when offline.
  if (url.pathname.startsWith("/api/") && isApiAllowed(url)) {
    event.respondWith((async () => {
      const c = await caches.open(API);
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok) c.put(req, fresh.clone()).catch(() => null);
        return fresh;
      } catch (e) {
        const hit = await c.match(req);
        if (hit) {
          const h = new Headers(hit.headers);
          h.set("X-Lexora-Offline", "1");
          return new Response(await hit.clone().blob(), { status: 200, headers: h });
        }
        return new Response(JSON.stringify({ detail: "You are offline and this screen has no saved copy yet." }),
          { status: 503, headers: { "Content-Type": "application/json", "X-Lexora-Offline": "1" } });
      }
    })());
  }
});
