// Offline library: keeps the catalog, the reader and the books on the device.
// make_server.py copies this file next to index.html, so it serves the whole library folder.
//
// Two caches (the site's own storage, not the browser's HTTP cache):
//   epublib-shell  the catalog page, the reader, fonts, covers, icons
//   epublib-books  the EPUB files
// Pages and the library list are asked from the server first (so changes show up at once) and
// taken from the cache when it can't be reached; everything else comes from the cache first.
// The catalog page decides what to download or drop (see "Offline library" in index.html):
// its own requests carry "x-lib-sync: 1" and go straight to the network.
"use strict";
const SHELL = "epublib-shell", BOOKS = "epublib-books";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

// The same address however its letters are encoded (Python's quote() and the browser differ)
function canon(url) {
  const u = new URL(url);
  const path = u.pathname.split("/").map(s => {
    try { return encodeURIComponent(decodeURIComponent(s)); } catch { return s; }
  }).join("/");
  return u.origin + path;
}

const isBook = u => /\.epub$/i.test(u.pathname);
const isFresh = u => /(\/|\.html|\.webmanifest|\/library\.json)$/i.test(u.pathname);

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET" || req.headers.get("x-lib-sync")) return;
  const u = new URL(req.url);
  if (u.origin !== location.origin || u.pathname.startsWith("/__")) return;
  const scope = new URL(self.registration.scope);
  if (!u.pathname.startsWith(scope.pathname)) return;
  if (isBook(u)) e.respondWith(book(req));
  else if (isFresh(u) || req.mode === "navigate") e.respondWith(networkFirst(req));
  else e.respondWith(cacheFirst(req));
});

async function networkFirst(req) {
  const key = canon(req.url);
  const cache = await caches.open(SHELL);
  try {
    const r = await fetch(req, { cache: "no-cache" });
    if (r.ok) cache.put(key, r.clone()).catch(() => {});
    return r;
  } catch (err) {
    const hit = await cache.match(key) ||
                (key.endsWith("/") && await cache.match(key + "index.html"));
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(req) {
  const key = canon(req.url);
  const cache = await caches.open(SHELL);
  const hit = await cache.match(key);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok) cache.put(key, r.clone()).catch(() => {});
  return r;
}

// A book opened once stays on the device (the catalog drops it again when it changes or is removed)
async function book(req) {
  const key = canon(req.url);
  const cache = await caches.open(BOOKS);
  const hit = await cache.match(key);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok) cache.put(key, r.clone()).catch(() => {});
  return r;
}
