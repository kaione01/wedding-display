# 婚禮即時展示系統 — Claude Code 專案說明

## 專案簡介

婚禮賓客透過 LINE 官方帳號傳送文字或照片，即時以彈幕方式顯示在婚宴現場投影大螢幕上。

- **新郎**：Kai Jhang
- **新娘**：Bella Wu
- **婚禮日期**：2026.05.24
- **LINE Channel ID**：2009211057

---

## 整體架構（雙層部署）

```
賓客用 LINE 傳訊
        ↓
LINE Messaging API Webhook
        ↓
┌────────────────────────────────────┐
│  VPS 後端（Hostinger / Railway）    │
│  FastAPI + WebSocket + SQLite      │
│  https://YOUR_VPS_DOMAIN           │
└─────────────┬──────────────────────┘
              │ WebSocket (wss://)
              ↓
┌────────────────────────────────────┐
│  GitHub Pages 前端（免費靜態託管）  │
│  docs/display.html                 │
│  https://你的帳號.github.io/...    │
└────────────────────────────────────┘
              ↓
     現場大螢幕顯示彈幕
```

| 元件 | 技術 | 託管位置 |
|------|------|----------|
| 後端 | Python 3.11 + FastAPI + uvicorn | VPS（Hostinger KVM 1，新加坡）|
| 即時通訊 | WebSocket（FastAPI 內建） | VPS |
| 資料庫 | SQLite（wedding.db） | VPS |
| 前端展示 | 純 HTML/CSS/JS（docs/display.html） | GitHub Pages（免費）|
| LINE Webhook | LINE Messaging API | → 打到 VPS |

---

## 檔案結構

```
wedding-display/
├── CLAUDE.md                          ← 本檔案（Claude Code 讀這裡）
│
├── ── 後端（VPS 執行）──
├── main.py                            ← FastAPI 後端主程式
├── config.py                          ← 設定檔（本機用，已 .gitignore）
├── config.example.py                  ← 設定檔範本
├── requirements.txt                   ← Python 套件
├── badwords.txt                       ← 髒話詞庫
├── Dockerfile                         ← Docker 部署用
├── railway.toml                       ← Railway 平台設定
├── start.sh                           ← Mac 本機一鍵啟動
├── .env.example                       ← 環境變數範本
├── .gitignore                         ← 排除敏感檔案
│
├── ── GitHub Actions──
├── .github/
│   └── workflows/
│       └── deploy-pages.yml           ← 自動部署到 GitHub Pages
│
├── ── 前端（GitHub Pages 部署）──
└── docs/
    ├── index.html                     ← 系統首頁（含進入展示頁按鈕）
    ├── display.html                   ← 彈幕展示畫面（主畫面）
    └── config.example.js             ← 前端設定範本（填 VPS 網址後改名 config.js）
```

---

## 環境變數（VPS / Railway 部署必填）

| 變數名稱 | 說明 | 必填 |
|---------|------|------|
| `CHANNEL_SECRET` | LINE Channel Secret | ✅ |
| `CHANNEL_ACCESS_TOKEN` | LINE Channel Access Token | ✅ |
| `CHANNEL_ID` | LINE Channel ID | ✅ |
| `PORT` | 伺服器 port（Railway 自動設定，VPS 預設 8000） | 自動 |

本機開發時這些值寫在 `config.py`（不上傳 GitHub）。

---

## 部署流程（完整步驟）

### 步驟一：建立 GitHub Repository

```bash
# 在 VPS 或 Mac 上執行
cd wedding-display
git init
git remote add origin https://github.com/你的帳號/wedding-display.git

# 確認 .gitignore 有排除敏感檔案
cat .gitignore

# 第一次推送
git add .
git commit -m "init: 婚禮彈幕系統初始化"
git push -u origin main
```

### 步驟二：開啟 GitHub Pages

1. 到 GitHub Repository → **Settings** → **Pages**
2. Source 選 **GitHub Actions**
3. 儲存後，推送到 `main` 分支即自動部署

部署完成後網址格式：
```
https://你的帳號.github.io/wedding-display/
```

### 步驟三：VPS 後端部署（Hostinger）

```bash
# SSH 進入 VPS
ssh root@你的VPS_IP

# 安裝 Python 環境
apt update && apt install -y python3-pip python3-venv

# 下載程式碼
git clone https://github.com/你的帳號/wedding-display.git
cd wedding-display

# 建立虛擬環境並安裝套件
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 設定環境變數（複製範本並填入真實值）
cp config.example.py config.py
nano config.py
# 填入：CHANNEL_SECRET, CHANNEL_ACCESS_TOKEN, CHANNEL_ID

# 安裝 Nginx（反向代理 + SSL）
apt install -y nginx certbot python3-certbot-nginx

# 設定 Nginx 反向代理（參考下方設定）
# ...

# 啟動後端（用 systemd 保持常駐）
# 參考下方 systemd 設定
```

#### Nginx 設定範本（/etc/nginx/sites-available/wedding）

```nginx
server {
    listen 80;
    server_name 你的網域或IP;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
# 啟用設定
ln -s /etc/nginx/sites-available/wedding /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 申請 SSL 憑證（需要有網域名稱）
certbot --nginx -d 你的網域
```

#### systemd 服務（讓後端開機自動啟動）

建立 `/etc/systemd/system/wedding.service`：
```ini
[Unit]
Description=Wedding Display Backend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/wedding-display
ExecStart=/root/wedding-display/venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable wedding
systemctl start wedding
systemctl status wedding
```

### 步驟四：設定前端連線到 VPS

在 `docs/display.html` 中，修改第 30-31 行的常數：

```js
const DEFAULT_BACKEND_WS  = 'wss://你的VPS網域/ws';
const DEFAULT_BACKEND_API = 'https://你的VPS網域';
```

或更簡單，直接用 URL 參數開啟展示頁：
```
https://你的帳號.github.io/wedding-display/display.html?ws=wss://你的VPS網域/ws
```

修改後推送到 GitHub，GitHub Actions 自動重新部署：
```bash
git add docs/display.html
git commit -m "fix: 設定 VPS WebSocket 網址"
git push origin main
```

### 步驟五：設定 LINE Webhook

1. 到 LINE Developers → Messaging API → Webhook settings
2. Webhook URL 填入：`https://你的VPS網域/webhook`
3. 開啟「Use webhook」
4. 點「Verify」測試連線

---

## 主要 API 端點（後端 VPS）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/webhook` | LINE Webhook 接收端點 |
| GET | `/display` | 投影展示畫面（直接從 VPS 開也可以） |
| GET | `/api/bg-photos` | 取得婚紗照列表 |
| WS | `/ws` | WebSocket 彈幕推送 |
| GET | `/uploads/{file}` | 賓客上傳照片 |

---

## 前端 WebSocket URL 解析優先順序

`docs/display.html` 會依以下順序決定連線到哪個後端：

1. **URL 參數** `?ws=wss://...`（最高優先，適合臨時切換）
2. **`window.WEDDING_CONFIG`**（由 `docs/config.js` 設定，適合長期固定）
3. **同 origin 自動連線**（在同一個伺服器上開啟時自動偵測）
4. **`DEFAULT_BACKEND_WS` 常數**（寫死在 HTML 裡的預設值）

---

## 本機開發

```bash
# 安裝套件
pip install -r requirements.txt

# 設定 config.py
cp config.example.py config.py
# 編輯填入 LINE 金鑰

# 啟動後端
bash start.sh
# 或手動：python main.py

# 本機展示畫面（後端直接提供，不需 GitHub Pages）
open http://localhost:8000/display

# 開 Cloudflare Tunnel 讓 LINE Webhook 打進來
cloudflared tunnel --url http://localhost:8000
```

---

## 彈幕邏輯（display.html）

- **9 條跑道**，自動分配避免重疊（2.5 秒冷卻）
- 文字彈幕：金黃色（#F5D478），從右往左 12–18 秒飄過
- 照片彈幕：max 300×260px，contain 顯示，16–20 秒飄過
- 預設彈幕：啟動後 3 秒飄一次，超過 180 秒沒訊息再飄一次
- WebSocket 斷線自動重連（指數退避，最長 15 秒）
- 右下角顯示連線狀態（3 秒後自動隱藏）

---

## 審核機制

- **文字**：`badwords.txt` 髒話過濾，通過直接顯示，攔截靜默丟棄
- **照片**：自動顯示，無需審核

---

## 婚紗照背景設定

```bash
# 在 VPS 上將婚紗照放到 static/wedding_bg/
# 支援 .jpg .jpeg .png .webp
scp 你的照片/*.jpg root@你的VPS_IP:/root/wedding-display/static/wedding_bg/

# 重啟後端讓 API 能列到新照片
systemctl restart wedding
```

---

## 常見指令

```bash
# ── 後端管理 ──
systemctl status wedding        # 查看後端狀態
systemctl restart wedding       # 重啟後端
journalctl -u wedding -f        # 即時查看後端 log

# ── 資料庫查詢 ──
sqlite3 wedding.db "SELECT * FROM messages ORDER BY created_at DESC LIMIT 20;"

# ── 清除佔用的 port（本機開發）──
lsof -ti:8000 | xargs kill -9 2>/dev/null

# ── 防止 Mac 睡眠（本機開發）──
caffeinate -d &

# ── GitHub Pages 手動觸發部署 ──
git push origin main
# 或到 GitHub → Actions → 手動觸發 workflow
```

---

## 未來計劃

- [ ] 婚禮互動遊戲（問答、抽獎）整合到同一台 VPS
- [ ] LINE 帳號婚禮當天活動排程
- [ ] 婚禮後自動備份照片到 Google Drive
- [ ] Telegram Bot + Claude API 手機控制介面
