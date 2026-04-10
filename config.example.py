# =============================================
#  婚禮即時展示系統 — 設定檔範本
#  複製此檔案為 config.py 並填入你的資訊
#  cp config.example.py config.py
# =============================================

# LINE Channel Secret（在 LINE Developers 後台 > Basic settings 找到）
CHANNEL_SECRET = "your_channel_secret_here"

# LINE Channel Access Token（在 LINE Developers 後台 > Messaging API 找到）
CHANNEL_ACCESS_TOKEN = "your_channel_access_token_here"

# LINE Channel ID
CHANNEL_ID = "your_channel_id_here"

# =============================================
#  婚禮資訊
# =============================================
GROOM_NAME = "Kai"
BRIDE_NAME = "Bella"
WEDDING_DATE = "2026.05.24"

DEFAULT_DANMAKU = "🎉 即時訊息開啟囉！趕快動動手指，傳送祝福訊息或照片到 LINE 官方帳號，就可顯示在大螢幕上 📸"
NO_MESSAGE_TIMEOUT_SECONDS = 180

BROADCAST_MESSAGE = "🎉 婚禮現正開始！歡迎傳送你的祝福訊息或照片，將會即時顯示在現場大螢幕上 📸✨"

HOST = "0.0.0.0"
PORT = 8000
