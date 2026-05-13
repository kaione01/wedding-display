"""
婚禮即時展示系統 — 後端伺服器
Bella & Kai Wedding Live Display
"""

import asyncio
import base64
import csv
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiofiles
import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import (
    QUIZ_URL,
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
WHOAMI        = "我的ID14131928"   # 取得自己的 LINE user_id（給管理員用）

# ── 問答遊戲關鍵字 ──
QUIZ_KEYWORD  = "遊戲"          # 賓客傳這個字就收到遊戲連結

# ── 座位查詢關鍵字 ──
SEAT_KEYWORD  = "當天我坐哪裡"
CONFIRM_DONE  = "查詢完畢"
CONFIRM_YES   = "✅ 就是我"
CONFIRM_NO    = "❌ 不是我"
CANCEL_KEYWORDS = {"取消", "結束查詢", "不查了", "離開"}

# 找不到資料時的二段式按鈕回覆
IS_GROOM_FAMILY  = "✅ 男方親友"
NOT_GROOM_FAMILY = "❌ 不是"
GROOM_RELATIVE   = "👨‍👩‍👧 親戚家人"
GROOM_FRIEND     = "🍻 新郎朋友"
CHOICE_NONE_OF   = "都不是"      # 多筆命中時的「都不是」按鈕 text

# 別名搜尋時忽略的贅字前綴（依長度排序，長的先）
_FILLER_PREFIXES = ("我叫做", "我叫", "我是", "我", "叫", "是", "找", "查")

# 管理員 LINE user_id（未匹配賓客 / 異常時通知）；可設定多個，用逗號分隔
ADMIN_USER_IDS = [u.strip() for u in os.environ.get("ADMIN_USER_IDS", "").split(",") if u.strip()]

# 主 event loop reference（lifespan 啟動時設定，供 thread 計時器使用）
MAIN_LOOP: asyncio.AbstractEventLoop | None = None


# ─────────────────────────────────────────────
# 賓客資料（guests.csv）
# ─────────────────────────────────────────────
GUESTS_CSV = BASE_DIR / "guests.csv"


def load_guests() -> list[dict]:
    guests = []
    if not GUESTS_CSV.exists():
        print("[警告] guests.csv 不存在，座位查詢功能停用")
        return guests
    # utf-8-sig 自動 strip BOM，避免第一欄 key 變成 ﻿桌名
    with open(GUESTS_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            guests.append(row)
    print(f"[賓客] 已載入 {len(guests)} 筆賓客資料")
    return guests


GUESTS: list[dict] = load_guests()


def _normalize_query(s: str) -> str:
    """去除贅字前綴（「我是」「我叫」等），最多剝一層。"""
    s = s.strip()
    for w in _FILLER_PREFIXES:
        if s.startswith(w) and len(s) > len(w):
            return s[len(w):].strip()
    return s


def find_guests(query: str) -> tuple[list[dict], int]:
    """多層匹配，回傳 (命中清單, tier)。
    tier 1: 完全相等（姓名或別名）— case-insensitive
    tier 2: 去除贅字前綴後完全相等 — case-insensitive
    tier 3: 子字串雙向匹配（最寬鬆，可能多筆）— case-insensitive
    無命中時回 ([], 0)
    """
    query = query.strip()
    if not query:
        return [], 0

    normalized = _normalize_query(query)
    q_low = query.casefold()
    n_low = normalized.casefold() if normalized else ""

    tier1, tier2, tier3 = [], [], []

    for g in GUESTS:
        names = [g["姓名"]] + [a.strip() for a in g["別名"].split("|") if a.strip()]
        names = [n for n in names if n]

        matched = 0  # 此 guest 的最高 tier
        for name in names:
            name_low = name.casefold()
            if name_low == q_low:
                matched = 1
                break
            if n_low and name_low == n_low:
                matched = max(matched, 2)
                continue
            if (q_low in name_low) or (name_low in q_low):
                matched = max(matched, 3)

        if matched == 1:
            tier1.append(g)
        elif matched == 2:
            tier2.append(g)
        elif matched == 3:
            tier3.append(g)

    if tier1:
        return tier1, 1
    if tier2:
        return tier2, 2
    return tier3, 3 if tier3 else 0


def find_guests_by_contact(query: str) -> list[dict]:
    """以聯絡方式（電話 / LINE ID）反查賓客。
    為避免誤判，query 至少 4 個字元；以子字串雙向匹配（case-insensitive）。
    """
    query = query.strip()
    if len(query) < 4:
        return []
    q_low = query.casefold()
    results = []
    for g in GUESTS:
        contact = (g.get("聯絡方式") or "").strip()
        if not contact:
            continue
        c_low = contact.casefold()
        if q_low in c_low or c_low in q_low:
            results.append(g)
    return results


def merge_dedup(*lists: list[dict]) -> list[dict]:
    """合併多個 guest list 並依「桌名+姓名」去重，保留先出現的。"""
    seen = set()
    out = []
    for lst in lists:
        for g in lst:
            key = (g.get("桌名", ""), g.get("姓名", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(g)
    return out


def _sort_candidates(matches: list[dict]) -> list[dict]:
    """排序：男方親友（有特殊回覆=請聯繫新郎爸爸）放後面。
    因為他們的回覆是引導去找新郎爸爸，對用戶較不友善，所以放後面。
    其餘維持原順序（stable sort）。
    """
    return sorted(matches, key=lambda g: 1 if (g.get("特殊回覆") or "").strip() else 0)


def _format_candidate_line(g: dict, idx: int) -> str:
    """格式化候選人顯示：'N. 姓名' 或 'N. 姓名（別名）'"""
    display = g["姓名"]
    aliases = [a.strip() for a in g["別名"].split("|") if a.strip()]
    if aliases and aliases[0].casefold() not in display.casefold():
        display += f"（{aliases[0]}）"
    return f"{idx}. {display}"


CHOICE_BUTTON_LIMIT = 5   # 超過此數量 → 文字列全部，按鈕只顯示前 N 個


def _build_choice_buttons(candidates: list[dict], limit: int = CHOICE_BUTTON_LIMIT) -> list[dict]:
    """產生候選人選擇按鈕（最多 limit 個 + 都不是 + 取消）。
    Label 顯示「N. 姓名」方便對照文字清單序號；
    Text 直接送姓名，讓對話框看起來自然（用戶回覆「黃伯淵」而非「選擇1」）。
    """
    buttons = [
        {"label": f"{i}. {g['姓名']}"[:20], "text": g["姓名"]}
        for i, g in enumerate(candidates[:limit], 1)
    ]
    buttons.append({"label": "❌ 都不是", "text": CHOICE_NONE_OF})
    buttons.append({"label": "🚪 取消",   "text": "取消"})
    return buttons


def _build_choice_message(matches: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """產生多筆命中時的訊息文字、按鈕、儲存用 candidates 清單。
    回傳 (message_text, buttons, candidates_to_store)
    """
    sorted_matches = _sort_candidates(matches)
    n = len(sorted_matches)

    if n <= CHOICE_BUTTON_LIMIT:
        # 5 筆以內：清單與按鈕一致
        lines = [f"找到 {n} 位符合的賓客，請選擇您是哪一位："]
    else:
        # 超過 5 筆：文字列全部 + 按鈕只給前 5
        lines = [
            f"找到 {n} 位符合的賓客 😊",
            "（範圍較大，建議輸入更完整姓名縮小範圍）",
            "",
        ]

    for i, g in enumerate(sorted_matches, 1):
        lines.append(_format_candidate_line(g, i))

    if n > CHOICE_BUTTON_LIMIT:
        lines.append("")
        lines.append(f"請點下方按鈕，或直接回覆數字（例：「{min(7, n)}」）")

    buttons = _build_choice_buttons(sorted_matches, limit=CHOICE_BUTTON_LIMIT)
    return "\n".join(lines), buttons, sorted_matches


# ─────────────────────────────────────────────
# 座位查詢對話狀態
# ─────────────────────────────────────────────
conversation_state: dict[str, str]   = {}   # user_id -> "waiting_name" | "confirming" | "waiting_choice" | "asking_groom_side" | "asking_relation" | "waiting_contact"
conversation_temp:  dict[str, dict]  = {}   # user_id -> {"name"/"matched"/"candidates": ...}
danmaku_suppressed_users: set[str]   = set()  # 查詢期間靜默此 user 彈幕
conversation_timers: dict[str, threading.Timer] = {}
groom_friend_users: set[str]         = set()  # 標記已表態為「新郎朋友」的 user（跨 state 持久）

QUERY_TIMEOUT_SECONDS = 60   # 1 分鐘無操作自動取消


# ─────────────────────────────────────────────
# 資料庫
# ─────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
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
    con.execute("PRAGMA journal_mode=WAL")
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


def _resize_image_bytes(data: bytes, max_dim: int = 1920, quality: int = 85) -> bytes:
    """壓縮圖片：等比縮放最長邊到 max_dim、JPEG quality 85，並修正 EXIF orientation。
    失敗時回傳原始 bytes（不阻擋顯示）。
    """
    try:
        from io import BytesIO
        from PIL import Image, ImageOps

        img = Image.open(BytesIO(data))
        # 修正手機照片的旋轉 EXIF（不然會橫躺）
        img = ImageOps.exif_transpose(img)
        # 縮放
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        # JPEG 不支援 alpha
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"[圖片壓縮] 失敗，沿用原檔: {e}")
        return data


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


async def send_line_push(user_id: str, text: str):
    """主動推送訊息給特定用戶（不需 reply token）。"""
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{LINE_API_BASE}/message/push", headers=headers, json=payload)
    except Exception as e:
        print(f"[LINE] 推送訊息例外: {e}")


async def send_line_reply_with_quickreply(reply_token: str, text: str, buttons: list[dict]):
    """回覆訊息並附帶 Quick Reply 按鈕。
    buttons 格式: [{"label": "按鈕文字", "text": "按下後發送的訊息"}]
    """
    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    items = [
        {
            "type": "action",
            "action": {"type": "message", "label": b["label"], "text": b["text"]},
        }
        for b in buttons
    ]
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text,
                "quickReply": {"items": items},
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(f"{LINE_API_BASE}/message/reply", headers=headers, json=payload)
    except Exception as e:
        print(f"[LINE] Quick Reply 回覆例外: {e}")


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
# 座位查詢：計時器 & 狀態清除
# ─────────────────────────────────────────────
def _clear_query_state(user_id: str):
    """清除該用戶的查詢狀態（計時器到期或完成時呼叫）。"""
    conversation_state.pop(user_id, None)
    conversation_temp.pop(user_id, None)
    danmaku_suppressed_users.discard(user_id)
    groom_friend_users.discard(user_id)
    t = conversation_timers.pop(user_id, None)
    if t:
        t.cancel()


def _on_query_timeout(user_id: str):
    """計時器到期：清除狀態並 push 提示訊息（在 thread 中呼叫）。"""
    if user_id not in conversation_state:
        return
    _clear_query_state(user_id)
    if MAIN_LOOP is None:
        print(f"[座位查詢] {user_id} 逾時但 MAIN_LOOP 未就緒，無法 push")
        return
    asyncio.run_coroutine_threadsafe(
        send_line_push(user_id, "查詢已逾時 ⏰\n若需要請重新傳「當天我坐哪裡」🙏"),
        MAIN_LOOP,
    )
    print(f"[座位查詢] {user_id} 逾時取消")


def start_query_timer(user_id: str):
    cancel_query_timer(user_id)
    t = threading.Timer(QUERY_TIMEOUT_SECONDS, _on_query_timeout, args=[user_id])
    t.daemon = True
    t.start()
    conversation_timers[user_id] = t


def cancel_query_timer(user_id: str):
    t = conversation_timers.pop(user_id, None)
    if t:
        t.cancel()


def log_unmatched_guest(user_id: str, sender: str, name: str, contact: str):
    """記錄找不到資料的賓客（以 user_id 去重，覆寫舊值）。"""
    log_path = BASE_DIR / "unmatched_guests.json"

    # 讀取現有資料；型別不對直接 reset 為 dict
    data: dict[str, dict] = {}
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
            else:
                print(f"[座位查詢] 既有 log 格式不對（type={type(loaded).__name__}），重建為 dict")
        except Exception as e:
            print(f"[座位查詢] 讀取未匹配 log 失敗: {e}")

    data[user_id] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sender":     sender,
        "name":       name,
        "contact":    contact,
    }

    try:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[座位查詢] 寫入未匹配 log 失敗: {e}")

    print(f"[座位查詢] 未匹配記錄 → {sender}（{name}）/ {contact}")


async def notify_admins_unmatched(sender: str, name: str, contact: str):
    """未匹配賓客時，推播提醒管理員。"""
    if not ADMIN_USER_IDS:
        return
    ts = datetime.now().strftime("%H:%M")
    msg = (
        f"⚠️ 賓客查無資料\n"
        f"時間：{ts}\n"
        f"LINE 名稱：{sender}\n"
        f"提供姓名：{name}\n"
        f"聯絡方式：{contact}"
    )
    for uid in ADMIN_USER_IDS:
        try:
            await send_line_push(uid, msg)
        except Exception as e:
            print(f"[座位查詢] 通知管理員 {uid} 失敗: {e}")


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
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
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
async def webhook(request: Request, bg: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_line_signature(body, signature):
        print("[警告] 收到無效簽名的 Webhook 請求")
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body.decode("utf-8"))
    bg.add_task(_process_webhook_events, payload)
    return {"status": "ok"}


async def _process_webhook_events(payload: dict):
    """總入口：迭代所有 events，每個 event 各自隔離，避免單一錯誤吃掉整個 batch。"""
    for event in payload.get("events", []):
        reply_token = event.get("replyToken", "")
        try:
            await _handle_one_event(event)
        except Exception as exc:
            import traceback
            print(f"[Webhook ERROR] event 處理失敗: {exc}")
            traceback.print_exc()
            # 嘗試告訴用戶系統有問題（不會永遠卡住）
            if reply_token:
                try:
                    await send_line_reply(
                        reply_token,
                        "抱歉，系統暫時忙碌 😢\n請稍候再試，或回覆「取消」結束目前操作"
                    )
                except Exception:
                    pass


async def _handle_one_event(event: dict):
    global last_message_time, danmaku_active, session_start_time

    if event.get("type") != "message":
        return

    user_id = event.get("source", {}).get("userId", "")
    reply_token = event.get("replyToken", "")
    message = event.get("message", {})
    msg_type = message.get("type", "")
    sender = await get_user_display_name(user_id)

    if msg_type == "text":
        text = message.get("text", "").strip()
        if not text:
            return

        emojis = message.get("emojis", [])
        if emojis:
            text = text.replace("(emoji)", "❤️")

        state = conversation_state.get(user_id)

        # ─────────────────────────────────────
        # 優先 1：取消指令（任何狀態都能逃出）
        # ─────────────────────────────────────
        if text in CANCEL_KEYWORDS and state:
            _clear_query_state(user_id)
            await send_line_reply(reply_token, "已取消查詢 😊\n若需要請再傳「當天我坐哪裡」")
            print(f"[座位查詢] {sender} 主動取消")
            return

        # ─────────────────────────────────────
        # 優先 2：SEAT_KEYWORD —— 無論在哪個狀態，重新開始
        # ─────────────────────────────────────
        if text == SEAT_KEYWORD:
            _clear_query_state(user_id)
            conversation_state[user_id] = "waiting_name"
            # 無論彈幕當下是否開啟，進入查詢流程就靜默該 user，避免後續才開彈幕導致漏抓
            danmaku_suppressed_users.add(user_id)
            start_query_timer(user_id)
            if danmaku_active:
                reply_text = (
                    "🎉 目前彈幕發送中！\n"
                    "為您查詢座位，期間您的訊息暫不顯示在彈幕上 😊\n\n"
                    "請告訴我您是？\n"
                    "（中文姓名、英文名、姓氏都可以查 ✨）"
                )
            else:
                reply_text = (
                    "請告訴我您是？\n"
                    "（中文姓名、英文名、姓氏都可以查 ✨）"
                )
            await send_line_reply_with_quickreply(
                reply_token,
                reply_text,
                [{"label": "❌ 取消查詢", "text": "取消"}],
            )
            print(f"[座位查詢] {sender} 開始查詢")
            return

        # ─────────────────────────────────────
        # 優先 3：對話狀態處理
        # ─────────────────────────────────────
        if state == "waiting_name":
            cancel_query_timer(user_id)
            matches, tier = find_guests(text)

            if not matches:
                # 如果已表態為「新郎朋友」（再查仍不到）→ 直接顯示聯繫新郎本人
                if user_id in groom_friend_users:
                    _clear_query_state(user_id)
                    await send_line_reply(
                        reply_token,
                        f"嗯…還是找不到「{text}」😢\n"
                        "建議您直接聯繫新郎本人確認入座 🙏",
                    )
                    print(f"[座位查詢] {sender}（新郎朋友）重查仍無 → 引導找新郎本人")
                    return

                # 第一次找不到 → 先問是不是男方親友
                conversation_state[user_id] = "asking_groom_side"
                conversation_temp[user_id] = {"name": text}
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    f"抱歉，找不到「{text}」😢\n"
                    "請問您是新郎那邊的「男方親友」嗎？",
                    [
                        {"label": "✅ 是，男方親友", "text": IS_GROOM_FAMILY},
                        {"label": "❌ 不是",         "text": NOT_GROOM_FAMILY},
                        {"label": "🚪 取消",         "text": "取消"},
                    ],
                )
                print(f"[座位查詢] {sender} 查無「{text}」→ 詢問是否男方親友")

            elif len(matches) == 1:
                g = matches[0]
                conversation_state[user_id] = "confirming"
                conversation_temp[user_id] = {"matched": g}
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    f"您是「{g['姓名']}」嗎？",
                    [
                        {"label": "✅ 就是我", "text": CONFIRM_YES},
                        {"label": "❌ 不是我", "text": CONFIRM_NO},
                        {"label": "🚪 取消",   "text": "取消"},
                    ],
                )
                print(f"[座位查詢] {sender} → 單筆命中（tier {tier}）{g['姓名']}/{g['桌名']}")

            else:
                # 多筆命中：依排序規則（男方親友後排）+ 按 5 筆門檻分流
                msg_text, buttons, sorted_candidates = _build_choice_message(matches)
                conversation_state[user_id] = "waiting_choice"
                conversation_temp[user_id] = {"candidates": sorted_candidates}
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(reply_token, msg_text, buttons)
                print(f"[座位查詢] {sender} → 多筆命中（{len(sorted_candidates)}）")
            return

        if state == "confirming":
            cancel_query_timer(user_id)
            if text == CONFIRM_YES or text == CONFIRM_DONE:
                matched = conversation_temp.get(user_id, {}).get("matched")
                _clear_query_state(user_id)
                if matched:
                    seat_msg = matched["特殊回覆"] or f"您被安排在【{matched['桌名']}】，姓名：{matched['姓名']} 😊"
                    await send_line_reply(reply_token, f"{seat_msg}\n祝您今天愉快 🎊")
                    print(f"[座位查詢] {sender} 確認本人 → 公布座位 {matched['桌名']}/{matched['姓名']}")
                else:
                    await send_line_reply(reply_token, "好的！祝您今天愉快 🎊")
                    print(f"[座位查詢] {sender} 確認完畢（無 matched 資料）")
            elif text == CONFIRM_NO:
                conversation_state[user_id] = "waiting_name"
                conversation_temp.pop(user_id, None)
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "抱歉比錯了 😅\n請再提供完整一點的姓名讓我重查～",
                    [{"label": "🚪 取消", "text": "取消"}],
                )
                print(f"[座位查詢] {sender} 否認比對結果，重查")
            else:
                # 不認得輸入 → 重發按鈕（不洩漏桌號）
                matched = conversation_temp.get(user_id, {}).get("matched")
                start_query_timer(user_id)
                if matched:
                    prompt = f"請點選下方按鈕：\n您是「{matched['姓名']}」嗎？"
                else:
                    prompt = "請點選下方按鈕回答 😊"
                await send_line_reply_with_quickreply(
                    reply_token,
                    prompt,
                    [
                        {"label": "✅ 就是我", "text": CONFIRM_YES},
                        {"label": "❌ 不是我", "text": CONFIRM_NO},
                        {"label": "🚪 取消",   "text": "取消"},
                    ],
                )
            return

        if state == "waiting_choice":
            cancel_query_timer(user_id)
            candidates = conversation_temp.get(user_id, {}).get("candidates", [])

            if text == CHOICE_NONE_OF or text == CONFIRM_NO or text == "都不是":
                conversation_state[user_id] = "waiting_name"
                conversation_temp.pop(user_id, None)
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "好，請再提供完整一點的姓名（含姓氏）讓我重查～",
                    [{"label": "🚪 取消", "text": "取消"}],
                )
                print(f"[座位查詢] {sender} 多筆都不是 → 重查")
                return

            # 解析：1) 姓名精準匹配  2) 純數字  3) 「選擇N」（向後相容）
            chosen = None
            # 1. 姓名匹配
            for c in candidates:
                if c["姓名"] == text:
                    chosen = c
                    break
            # 2. 純數字
            if not chosen and text.isdigit():
                try:
                    idx = int(text) - 1
                    if 0 <= idx < len(candidates):
                        chosen = candidates[idx]
                except ValueError:
                    pass
            # 3. 「選擇N」舊格式
            if not chosen and text.startswith("選擇"):
                num_str = text[2:].strip()
                if num_str.isdigit():
                    try:
                        idx = int(num_str) - 1
                        if 0 <= idx < len(candidates):
                            chosen = candidates[idx]
                    except ValueError:
                        pass

            if chosen:
                seat_msg = chosen["特殊回覆"] or f"您被安排在【{chosen['桌名']}】，姓名：{chosen['姓名']} 😊"
                _clear_query_state(user_id)
                await send_line_reply(reply_token, f"{seat_msg}\n祝您今天愉快 🎊")
                print(f"[座位查詢] {sender} 多筆選擇 → {chosen['姓名']}/{chosen['桌名']}")
            else:
                # 不認得輸入 → 重發按鈕
                start_query_timer(user_id)
                buttons = _build_choice_buttons(candidates)
                hint = "請點選下方選項 😊" if len(candidates) <= CHOICE_BUTTON_LIMIT else f"請點選下方按鈕，或直接回覆 1~{len(candidates)} 的數字 😊"
                await send_line_reply_with_quickreply(reply_token, hint, buttons)
            return

        # ── 新增：詢問是不是男方親友 ──
        if state == "asking_groom_side":
            cancel_query_timer(user_id)
            if text == IS_GROOM_FAMILY:
                conversation_state[user_id] = "asking_relation"
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "好的！請問您是？",
                    [
                        {"label": "👨‍👩‍👧 親戚家人", "text": GROOM_RELATIVE},
                        {"label": "🍻 新郎朋友",     "text": GROOM_FRIEND},
                        {"label": "🚪 取消",         "text": "取消"},
                    ],
                )
                print(f"[座位查詢] {sender} 表態為男方親友 → 詢問身份")
            elif text == NOT_GROOM_FAMILY:
                # 不是男方親友 → 提示提供聯絡方式 / 姓氏 重新查詢
                conversation_state[user_id] = "waiting_contact"
                # conversation_temp 保留 {"name": ...}
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "好的 😊\n"
                    "請提供您於表單填寫的「聯繫電話」或「LINE ID」，\n"
                    "或是您的「姓氏」，讓我再為您確認一次！",
                    [{"label": "🚪 取消", "text": "取消"}],
                )
                print(f"[座位查詢] {sender} 不是男方親友 → 等待聯絡方式 / 姓氏")
            else:
                # 不認得輸入 → 重發按鈕
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "請點選下方按鈕回答：您是新郎那邊的「男方親友」嗎？",
                    [
                        {"label": "✅ 是，男方親友", "text": IS_GROOM_FAMILY},
                        {"label": "❌ 不是",         "text": NOT_GROOM_FAMILY},
                        {"label": "🚪 取消",         "text": "取消"},
                    ],
                )
            return

        # ── 新增：詢問是親戚還是朋友 ──
        if state == "asking_relation":
            cancel_query_timer(user_id)
            if text == GROOM_RELATIVE:
                _clear_query_state(user_id)
                await send_line_reply(
                    reply_token,
                    "好的！細節請聯繫新郎爸爸現場確認入座 🙏\n他會協助您安排位置 😊",
                )
                print(f"[座位查詢] {sender} 親戚家人 → 引導找新郎爸爸")
            elif text == GROOM_FRIEND:
                conversation_state[user_id] = "waiting_name"
                conversation_temp.pop(user_id, None)
                groom_friend_users.add(user_id)  # 標記，下次找不到時直接引導找新郎本人
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "好的！請再告訴我您是？\n"
                    "（中文姓名、英文名、姓氏都可以查 ✨）",
                    [{"label": "🚪 取消", "text": "取消"}],
                )
                print(f"[座位查詢] {sender} 新郎朋友 → 等待重輸姓名")
            else:
                # 不認得輸入 → 重發按鈕
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    "請點選下方按鈕回答：",
                    [
                        {"label": "👨‍👩‍👧 親戚家人", "text": GROOM_RELATIVE},
                        {"label": "🍻 新郎朋友",     "text": GROOM_FRIEND},
                        {"label": "🚪 取消",         "text": "取消"},
                    ],
                )
            return

        if state == "waiting_contact":
            cancel_query_timer(user_id)

            # 用姓氏 / 聯絡方式 再查一次
            name_matches, _ = find_guests(text)
            contact_matches = find_guests_by_contact(text)
            all_matches = merge_dedup(name_matches, contact_matches)

            if len(all_matches) == 1:
                g = all_matches[0]
                conversation_state[user_id] = "confirming"
                conversation_temp[user_id] = {"matched": g}
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(
                    reply_token,
                    f"找到您了！🎉\n您是「{g['姓名']}」嗎？",
                    [
                        {"label": "✅ 就是我", "text": CONFIRM_YES},
                        {"label": "❌ 不是我", "text": CONFIRM_NO},
                        {"label": "🚪 取消",   "text": "取消"},
                    ],
                )
                print(f"[座位查詢] {sender} 由聯絡方式/姓氏命中 → {g['姓名']}/{g['桌名']}")
                return

            if len(all_matches) > 1:
                msg_text, buttons, sorted_candidates = _build_choice_message(all_matches)
                conversation_state[user_id] = "waiting_choice"
                conversation_temp[user_id] = {"candidates": sorted_candidates}
                start_query_timer(user_id)
                await send_line_reply_with_quickreply(reply_token, msg_text, buttons)
                print(f"[座位查詢] {sender} 由聯絡方式/姓氏命中多筆（{len(sorted_candidates)}）")
                return

            # 仍然找不到 → 記 log + 通知管理員
            name = conversation_temp.get(user_id, {}).get("name", "（未提供）")
            log_unmatched_guest(user_id, sender, name, text)
            _clear_query_state(user_id)
            await send_line_reply(
                reply_token,
                "還是找不到您的資料 😢\n已通知工作人員，現場會協助您入座，謝謝您！",
            )
            asyncio.create_task(notify_admins_unmatched(sender, name, text))
            print(f"[座位查詢] 未匹配已記錄：{sender}（{name}）/ {text}")
            return

        # ─────────────────────────────────────
        # 優先 4：CONFIRM_DONE / 殘留按鈕（已無 state，靜默忽略）
        # ─────────────────────────────────────
        _ORPHAN_BUTTON_TEXTS = (
            CONFIRM_DONE, CONFIRM_YES, CONFIRM_NO,
            IS_GROOM_FAMILY, NOT_GROOM_FAMILY,
            GROOM_RELATIVE, GROOM_FRIEND,
            CHOICE_NONE_OF, "都不是",
        )
        if text in _ORPHAN_BUTTON_TEXTS or text.startswith("選擇"):
            return

        # ─────────────────────────────────────
        # 優先 5：全域暗號 / 關鍵字
        # ─────────────────────────────────────
        if text == WHOAMI:
            await send_line_reply(reply_token, f"您的 LINE user_id 是：\n{user_id}")
            print(f"[WHOAMI] {sender} → {user_id}")
            return

        if text == SECRET_START:
            danmaku_active = True
            session_start_time = datetime.now()
            con = sqlite3.connect(DB_PATH)
            con.execute("PRAGMA journal_mode=WAL")
            count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            con.close()
            await send_line_reply(reply_token, f"✅ 彈幕已開啟，目前累積 {count} 則訊息")
            await manager.broadcast({"type": "session_start", "session_start": session_start_time.isoformat()})
            print("[暗號] 彈幕開啟")
            return

        if text == SECRET_STATUS:
            status = "✅ 開啟中" if danmaku_active else "⏹️ 關閉中（靜默模式）"
            start_str = session_start_time.strftime("%H:%M") if session_start_time else "尚未開啟"
            con = sqlite3.connect(DB_PATH)
            con.execute("PRAGMA journal_mode=WAL")
            count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            con.close()
            await send_line_reply(reply_token, f"📊 彈幕狀態：{status}\n本次開啟時間：{start_str}\n累積訊息：{count} 則")
            return

        if text == SECRET_STOP:
            danmaku_active = False
            await send_line_reply(reply_token, "⏹️ 彈幕已關閉")
            await manager.broadcast({"type": "session_stop"})
            print("[暗號] 彈幕關閉")
            return

        if text == QUIZ_KEYWORD:
            await send_line_reply(reply_token, f"🎮 婚禮問答遊戲開始囉！\n\n點擊連結加入：\n{QUIZ_URL}\n\n輸入暱稱就可以參加！")
            print(f"[遊戲] {sender} 索取遊戲連結")
            return

        # ─────────────────────────────────────
        # 優先 6：彈幕廣播（普通訊息）
        # ─────────────────────────────────────
        # 雙重保險：若還在查詢流程中（理論上前面 state handler 已 return 不會到這），不廣播
        if user_id in conversation_state:
            return

        if not danmaku_active:
            # 沒進入任何流程、沒匹配任何關鍵字、彈幕也沒開 → 不要沉默
            # 統一回覆「看不懂」+ 通知 admin，避免 state 丟失或用戶誤入時卡住
            await send_line_reply(
                reply_token,
                "抱歉，我看不懂這個訊息 😢\n"
                "若想查詢您的座位，請輸入「當天我坐哪裡」開始 🙏"
            )
            asyncio.create_task(notify_admins_unmatched(sender, "(未進入查詢流程)", text))
            print(f"[Fallback] 無法處理訊息：{sender}：{text[:50]}")
            return

        if not is_clean(text):
            return

        if user_id in danmaku_suppressed_users:
            return

        save_message("text", sender, content=text)
        await manager.broadcast({"type": "text", "sender_name": sender, "content": text})
        last_message_time = datetime.now()
        print(f"[訊息] {sender}：{text[:30]}{'...' if len(text) > 30 else ''}")

    elif msg_type == "image":
        # 查座位流程中不接受圖片廣播（避免在問答途中誤觸）
        if user_id in conversation_state:
            return
        if not danmaku_active:
            return
        if user_id in danmaku_suppressed_users:
            return

        msg_id = message.get("id", "")
        img_data = await download_image_content(msg_id)
        if not img_data:
            return

        # 壓縮 / 縮放 → 跑在 thread pool 避免阻塞 event loop
        original_size = len(img_data)
        loop = asyncio.get_running_loop()
        img_data = await loop.run_in_executor(None, _resize_image_bytes, img_data, 1920, 85)
        new_size = len(img_data)

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
        print(f"[照片] {sender} 上傳照片：{original_size//1024}KB → {new_size//1024}KB")

    elif msg_type == "sticker":
        # 查座位流程中不接受貼圖廣播
        if user_id in conversation_state:
            return
        if not danmaku_active:
            return
        if user_id in danmaku_suppressed_users:
            return

        sticker_id = message.get("stickerId", "")
        sticker_type = message.get("stickerType", "static")

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
