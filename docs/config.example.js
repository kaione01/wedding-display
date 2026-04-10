/**
 * 婚禮彈幕系統 — 前端設定檔範本
 *
 * 使用方法：
 *   1. 複製此檔案為 config.js
 *   2. 填入你的 VPS 網址
 *   3. 在 display.html 的 <head> 內加入：
 *      <script src="config.js"></script>
 *      （需加在 display.html 的 </head> 之前）
 *
 * 或者，也可以直接在瀏覽器網址後面加參數：
 *   https://你的帳號.github.io/wedding-display/display.html?ws=wss://你的VPS網址/ws
 */

window.WEDDING_CONFIG = {
  // WebSocket 伺服器位址（必填）
  // 格式：wss://你的網域/ws  （HTTPS 用 wss://，HTTP 用 ws://）
  wsUrl: 'wss://YOUR_VPS_DOMAIN/ws',

  // API 後端位址（必填）
  // 格式：https://你的網域
  apiUrl: 'https://YOUR_VPS_DOMAIN',
};
