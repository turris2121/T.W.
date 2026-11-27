/* Offline support: safety page always available; data is network-first. */
var CACHE = "tw-v3";
var CORE = ["./", "index.html", "safety.html", "style.css", "app.js", "manifest.webmanifest", "icons/icon.svg",
            "data/historical_tsunamis.json", "data/risk_profiles.json", "config/locations.json"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(CORE); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  // network first, fall back to cache (so alerts are fresh when online)
  e.respondWith(fetch(req).then(function (res) {
    if (res.ok && new URL(req.url).origin === location.origin) {
      var copy = res.clone();
      caches.open(CACHE).then(function (c) { c.put(req, copy); });
    }
    return res;
  }).catch(function () {
    return caches.match(req, { ignoreSearch: true }).then(function (r) { return r || caches.match("safety.html"); });
  }));
});
