"""
婚禮即時展示系統 — 後端伺服器
Bella & Kai Wedding Live Display
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import (
    BROADCAST_MESSAGE,
    CHANNEL_ACCESS_TOKEN,
    CHANNEL_SECRET,
    DEFAULT_DANMAKU_MESSAGES,
    HOST,
    NO_MESSAGE_TIMEOUT_SECONDS,
    PORT,
)

# ─────────────────────────────────────────────
# 路徑設定
# ─────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
BG_DIR     = BASE_DIR / "static" / "wedding_bg"
DB_PATH    = BASE_DIR / "wedding.db"
STATIC_DIR  = BASE_DIR / "static"
DOCS_DIR    = BASE_DIR / "docs"

LINE_API_BASE = "https://api.line.me/v2/bot"
LINE_DATA_API = "https://api-data.line.me/v2/bot"

# ─────────────────────────────────────────────
# WebSocket 連線管理
# ─────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        print(f"[WS] 展示畫面已連線，目前 {len(self.connections)} 個連線")

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)
        print(f"[WS] 展示畫面斷線，目前 {len(self.connections)} 個連線")

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
last_message_time = datetime.now()

# ─────────────────────────────────────────────
# 暗號開關狀態
# ─────────────────────────────────────────────
danmaku_active = False          # 預設靜默模式
session_start_time: datetime | None = None   # 本次開啟時間

SECRET_START  = "婚禮開始14131928"
SECRET_STOP   = "婚禮結束14131928"
SECRET_STATUS = "現在彈幕狀態14131928"

# ── 問答遊戲關鍵字 ──
QUIZ_KEYWORD  = "遊戲"          # 賓客傳這個字就收到遊戲連結
QUIZ_URL      = "https://laden-yang-connectors-tubes.trycloudflare.com/quiz/play"


# ─────────────────────────────────────────────
# 資料庫
# ─────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            type       TEXT    NOT NULL,
            sender     TEXT    NOT NULL,
            content    TEXT,
            file_path  TEXT,
            created_at TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    con.commit()
    con.close()


def save_message(type_: str, sender: str, content: str = None, file_path: str = None):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO messages (type, sender, content, file_path) VALUES (?,?,?,?)",
        (type_, sender, content, file_path),
    )
    con.commit()
    con.close()


# ─────────────────────────────────────────────
# 髒話過濾
# ─────────────────────────────────────────────
def load_badwords() -> set[str]:
    path = BASE_DIR / "badwords.txt"
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text("utf-8").splitlines() if line.strip()}


BADWORDS = load_badwords()


def is_clean(text: str) -> bool:
    text_lower = text.lower()
    return not any(w in text_lower for w in BADWORDS)


# ─────────────────────────────────────────────
# LINE API 工具函數
# ─────────────────────────────────────────────
def verify_line_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def get_user_display_name(user_id: str) -> str:
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{LINE_API_BASE}/profile/{user_id}", headers=headers)
            if resp.status_code == 200:
                return resp.json().get("displayName", "匿名賓客")
    except Exception as e:
        print(f"[LINE] 取得用戶名稱失敗: {e}")
    return "匿名賓客"


async def download_image_content(message_id: str) -> bytes | None:
    headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{LINE_DATA_API}/message/{message_id}/content",
                headers=headers,
            )
            if resp.status_code == 200:
                return resp.content
    except Exception as e:
        print(f"[LINE] 下載圖片失敗: {e}")
    return None


async def send_line_reply(reply_token: str, text: str):
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{LINE_API_BASE}/message/reply", headers=headers, json=payload)
    except Exception as e:
        print(f"[LINE] 回覆訊息例外: {e}")


async def send_line_broadcast(text: str):
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"messages": [{"type": "text", "text": text}]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{LINE_API_BASE}/broadcast", headers=headers, json=payload)
            if resp.status_code == 200:
                print("[LINE] 廣播訊息發送成功")
            else:
                print(f"[LINE] 廣播訊息失敗: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[LINE] 廣播訊息例外: {e}")


# ─────────────────────────────────────────────
# 背景任務：預設彈幕
# ─────────────────────────────────────────────
async def default_danmaku_loop():
    global last_message_time
    await asyncio.sleep(3)
    msg_index = 0
    print("[彈幕] 發送初始預設彈幕")
    await manager.broadcast({"type": "default", "content": DEFAULT_DANMAKU_MESSAGES[0]})

    while True:
        await asyncio.sleep(30)
        elapsed = (datetime.now() - last_message_time).total_seconds()
        if elapsed >= NO_MESSAGE_TIMEOUT_SECONDS:
            msg_index = (msg_index + 1) % len(DEFAULT_DANMAKU_MESSAGES)
            msg = DEFAULT_DANMAKU_MESSAGES[msg_index]
            print(f"[彈幕] 重送預設彈幕（第{msg_index+1}條）")
            await manager.broadcast({"type": "default", "content": msg})
            last_message_time = datetime.now()


# ─────────────────────────────────────────────
# 應用程式生命週期
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(exist_ok=True)
    BG_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    print("=" * 50)
    print("🎉 婚禮即時展示系統啟動")
    print(f"📺 展示畫面：http://localhost:{PORT}/display")
    print("=" * 50)

    asyncio.create_task(_delayed_broadcast())
    asyncio.create_task(default_danmaku_loop())
    yield
    print("系統關閉")


async def _delayed_broadcast():
    await asyncio.sleep(2)
    await send_line_broadcast(BROADCAST_MESSAGE)


# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────
app = FastAPI(lifespan=lifespan, title="婚禮即時展示系統")


# ─────────────────────────────────────────────
# LINE Webhook
# ─────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    global last_message_time

    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_line_signature(body, signature):
        print("[警告] 收到無效簽名的 Webhook 請求")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body.decode("utf-8"))

    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue

        user_id = event.get("source", {}).get("userId", "")
        reply_token = event.get("replyToken", "")
        message = event.get("message", {})
        msg_type = message.get("type", "")
        sender = await get_user_display_name(user_id)

        if msg_type == "text":
            text = message.get("text", "").strip()
            if not text:
                continue

            # 處理 LINE 專屬 emoji：用真實 Unicode emoji 替換
            emojis = message.get("emojis", [])
            if emojis:
                # LINE emoji 無法直接取得圖片，用 ❤️ 替換每個 (emoji) 佔位符
                text = text.replace("(emoji)", "❤️")

            # 暗號：開啟彈幕
            if text == SECRET_START:
                global danmaku_active, session_start_time
                danmaku_active = True
                session_start_time = datetime.now()
                con = sqlite3.connect(DB_PATH)
                count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                con.close()
                await send_line_reply(reply_token, f"✅ 彈幕已開啟，目前累積 {count} 則訊息")
                await manager.broadcast({"type": "session_start", "session_start": session_start_time.isoformat()})
                print("[暗號] 彈幕開啟")
                continue

            # 暗號：查詢狀態
            if text == SECRET_STATUS:
                status = "✅ 開啟中" if danmaku_active else "⏹️ 關閉中（靜默模式）"
                start_str = session_start_time.strftime("%H:%M") if session_start_time else "尚未開啟"
                con = sqlite3.connect(DB_PATH)
                count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                con.close()
                await send_line_reply(reply_token, f"📊 彈幕狀態：{status}\n本次開啟時間：{start_str}\n累積訊息：{count} 則")
                continue

            # 暗號：關閉彈幕
            if text == SECRET_STOP:
                danmaku_active = False
                await send_line_reply(reply_token, "⏹️ 彈幕已關閉")
                await manager.broadcast({"type": "session_stop"})
                print("[暗號] 彈幕關閉")
                continue

            # 問答遊戲連結
            if text == QUIZ_KEYWORD:
                await send_line_reply(
                    reply_token,
                    f"🎮 婚禮問答遊戲開始囉！\n\n點擊連結加入：\n{QUIZ_URL}\n\n輸入暱稱就可以參加！"
                )
                print(f"[遊戲] {sender} 索取遊戲連結")
                continue

            # 靜默模式：不存不推
            if not danmaku_active:
                continue

            if not is_clean(text):
                continue

            save_message("text", sender, content=text)
            await manager.broadcast({"type": "text", "sender_name": sender, "content": text})
            last_message_time = datetime.now()
            print(f"[訊息] {sender}：{text[:30]}{'...' if len(text) > 30 else ''}")

        elif msg_type == "image":
            # 靜默模式：不存不推
            if not danmaku_active:
                continue

            msg_id = message.get("id", "")
            img_data = await download_image_content(msg_id)
            if not img_data:
                continue
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{msg_id}.jpg"
            filepath = UPLOAD_DIR / filename
            async with aiofiles.open(filepath, "wb") as f:
                await f.write(img_data)
            save_message("image", sender, file_path=str(filepath))
            await manager.broadcast({
                "type": "image",
                "sender_name": sender,
                "file_path": f"/uploads/{filename}",
            })
            last_message_time = datetime.now()
            print(f"[照片] {sender} 上傳了一張照片")

        elif msg_type == "sticker":
            # 靜默模式：不存不推
            if not danmaku_active:
                continue

            sticker_id = message.get("stickerId", "")
            sticker_type = message.get("stickerType", "static")

            # LINE 貼圖 CDN（靜態貼圖用 png，動態用 apng）
            if sticker_type == "animated":
                sticker_url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker_animation@2x.apng"
            else:
                sticker_url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker@2x.png"

            save_message("sticker", sender, content=sticker_url)
            await manager.broadcast({
                "type": "sticker",
                "sender_name": sender,
                "sticker_url": sticker_url,
            })
            last_message_time = datetime.now()
            print(f"[貼圖] {sender} 傳了貼圖 {sticker_id}")

    return {"status": "ok"}


# ─────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # 告知前端目前狀態與本次 session 開始時間
    await websocket.send_json({
        "type": "session_info",
        "active": danmaku_active,
        "session_start": session_start_time.isoformat() if session_start_time else None,
    })
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ─────────────────────────────────────────────
# 頁面與 API
# ─────────────────────────────────────────────
@app.get("/display", response_class=HTMLResponse)
async def display_page():
    html_path = DOCS_DIR / "display.html"
    return HTMLResponse(content=html_path.read_text("utf-8"))


@app.get("/")
async def root():
    return {"message": "婚禮即時展示系統運行中 🎉", "display": "/display"}


@app.get("/api/bg-photos")
async def bg_photos():
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    photos = [f.name for f in sorted(BG_DIR.iterdir()) if f.suffix.lower() in exts]
    return JSONResponse(photos)


# ─────────────────────────────────────────────
# 靜態檔案
# ─────────────────────────────────────────────
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/bg",      StaticFiles(directory=str(BG_DIR)),     name="bg")
app.mount("/static",  StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─────────────────────────────────────────────
# 啟動
# ─────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
