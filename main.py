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
    DEFAULT_DANMAKU,
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
STATIC_DIR = BASE_DIR / "static"

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
    print("[彈幕] 發送初始預設彈幕")
    await manager.broadcast({"type": "default", "content": DEFAULT_DANMAKU})

    while True:
        await asyncio.sleep(30)
        elapsed = (datetime.now() - last_message_time).total_seconds()
        if elapsed >= NO_MESSAGE_TIMEOUT_SECONDS:
            print(f"[彈幕] {NO_MESSAGE_TIMEOUT_SECONDS}秒沒有新訊息，重送預設彈幕")
            await manager.broadcast({"type": "default", "content": DEFAULT_DANMAKU})
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
        message = event.get("message", {})
        msg_type = message.get("type", "")
        sender = await get_user_display_name(user_id)

        if msg_type == "text":
            text = message.get("text", "").strip()
            if not text or not is_clean(text):
                continue
            save_message("text", sender, content=text)
            await manager.broadcast({"type": "text", "sender_name": sender, "content": text})
            last_message_time = datetime.now()
            print(f"[訊息] {sender}：{text[:30]}{'...' if len(text) > 30 else ''}")

        elif msg_type == "image":
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

    return {"status": "ok"}


# ─────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
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
    html_path = STATIC_DIR / "display.html"
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
