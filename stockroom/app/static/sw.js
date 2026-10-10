// Minimal service worker so phones offer "Add to Home screen". Network only; no offline caching of stock data.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
