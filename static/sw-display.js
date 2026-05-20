// Service Worker for 彈幕大螢幕 PWA
// 策略：Network-Only — 永遠從網路抓，不快取任何東西。
// 這樣改 code 之後立刻生效，不需要清快取。
self.addEventListener('fetch', event => {
  event.respondWith(fetch(event.request));
});
