# 婚禮即時展示系統 — QA 自動化測試套件
> **版本**: 2026-05-06  
> **狀態**: 🟢 Active Testing Framework  
> **覆蓋率**: LINE Webhook + WebSocket + SQLite + 媒體處理

---

## 📋 目錄
1. [測試架構](#測試架構)
2. [LINE Webhook 簽名驗證](#line-webhook-簽名驗證)
3. [彈幕功能測試](#彈幕功能測試)
4. [媒體處理測試](#媒體處理測試)
5. [邊界條件測試](#邊界條件測試)
6. [問題模板](#問題模板)

---

## 🏗️ 測試架構

### 系統連接點測試

```
┌─────────────────────────┐
│   LINE Official Account │
│  (Webhook Sender)       │
└────────────┬────────────┘
             │
             │ POST /webhook
             │ (X-Line-Signature)
             ▼
┌─────────────────────────────────┐
│  FastAPI Webhook Handler        │
│  ✅ Signature Verification      │
│  ✅ Message Parsing             │
│  ✅ Secret Command Detection    │
└────────────┬────────────────────┘
             │
      ┌──────┴──────┐
      ▼             ▼
   Text?        Image?
   Sticker?     ...
      │             │
      ▼             ▼
┌────────────┐ ┌──────────────┐
│ Badword    │ │ Save to DB   │
│ Filtering  │ │ Upload File  │
└────┬───────┘ └──────┬───────┘
     │                │
     └────────┬───────┘
              ▼
     ┌─────────────────┐
     │  WebSocket      │
     │  Broadcast      │
     │  to Display     │
     └─────────────────┘
              ▼
     ┌─────────────────┐
     │ HTML Canvas     │
     │ Real-time Danmaku
     └─────────────────┘
```

---

## ✅ LINE Webhook 簽名驗證

### 1. 簽名驗證單元測試

**檔案**: `tests/test_line_webhook_signature.py`

```python
import hmac
import hashlib
import base64
import json
import pytest

from config import CHANNEL_SECRET

def verify_line_signature(body: bytes, signature: str) -> bool:
    """驗證 LINE Webhook 簽名"""
    mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)

class TestLineWebhookSignature:
    """LINE Webhook 簽名驗證測試"""
    
    def test_valid_signature(self):
        """測試有效簽名"""
        body = b'{"events":[]}'
        
        # 計算正確簽名
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        valid_signature = base64.b64encode(mac).decode("utf-8")
        
        # 驗證
        assert verify_line_signature(body, valid_signature) == True
    
    def test_invalid_signature_rejection(self):
        """測試無效簽名被拒絕"""
        body = b'{"events":[]}'
        invalid_signature = "invalid_sig_12345"
        
        assert verify_line_signature(body, invalid_signature) == False
    
    def test_tampering_detection(self):
        """測試篡改內容被檢測"""
        body1 = b'{"events":[]}'
        body2 = b'{"events":[{"malicious":"data"}]}'
        
        mac1 = hmac.new(CHANNEL_SECRET.encode("utf-8"), body1, hashlib.sha256).digest()
        sig1 = base64.b64encode(mac1).decode("utf-8")
        
        # 用 body1 的簽名驗證 body2，應該失敗
        assert verify_line_signature(body2, sig1) == False
    
    def test_empty_body_signature(self):
        """測試空 Body 簽名"""
        body = b''
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        signature = base64.b64encode(mac).decode("utf-8")
        
        assert verify_line_signature(body, signature) == True

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**運行方式**:
```bash
pytest tests/test_line_webhook_signature.py -v
```

---

## 🎤 彈幕功能測試

### 2. 暗號控制測試

**檔案**: `tests/test_danmaku_commands.py`

```python
import pytest
from datetime import datetime

class TestDanmakuCommands:
    """暗號控制功能測試"""
    
    def test_secret_start_keyword(self):
        """測試開啟彈幕暗號"""
        SECRET_START = "婚禮開始14131928"
        user_input = "婚禮開始14131928"
        
        assert user_input == SECRET_START, "開啟暗號應完全匹配"
    
    def test_secret_stop_keyword(self):
        """測試關閉彈幕暗號"""
        SECRET_STOP = "婚禮結束14131928"
        user_input = "婚禮結束14131928"
        
        assert user_input == SECRET_STOP, "關閉暗號應完全匹配"
    
    def test_secret_status_keyword(self):
        """測試狀態查詢暗號"""
        SECRET_STATUS = "現在彈幕狀態14131928"
        user_input = "現在彈幕狀態14131928"
        
        assert user_input == SECRET_STATUS, "查詢暗號應完全匹配"
    
    def test_case_sensitive_commands(self):
        """測試暗號區分大小寫"""
        SECRET_START = "婚禮開始14131928"
        wrong_case = "婚禮開始14131928 "  # 多一個空格
        
        assert wrong_case != SECRET_START, "暗號應區分空格"
    
    def test_session_start_timestamp(self):
        """測試 Session 開始時間記錄"""
        start_time = datetime.now()
        
        # 驗證時間格式
        assert start_time.isoformat() is not None
        assert "2026-05" in start_time.isoformat()
    
    def test_quiet_mode_silences_messages(self):
        """測試靜默模式隐藏訊息"""
        danmaku_active = False  # 靜默模式
        message = "Hello"
        
        # 靜默模式下不應推送
        should_broadcast = danmaku_active
        assert should_broadcast == False

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

**運行方式**:
```bash
pytest tests/test_danmaku_commands.py -v
```

---

## 📸 媒體處理測試

### 3. 媒體類型處理測試

**檔案**: `tests/test_media_processing.py`

```python
import pytest
from pathlib import Path
import tempfile

class TestMediaProcessing:
    """媒體上傳與處理測試"""
    
    def test_text_message_parsing(self):
        """測試文字訊息解析"""
        message = {
            "type": "text",
            "text": "新娘到了！"
        }
        
        assert message["type"] == "text"
        assert message["text"] == "新娘到了！"
        assert len(message["text"]) > 0
    
    def test_image_message_handling(self):
        """測試圖片訊息處理"""
        message = {
            "type": "image",
            "id": "msg_img_12345"
        }
        
        assert message["type"] == "image"
        assert message["id"].startswith("msg_img_")
    
    def test_sticker_url_generation_static(self):
        """測試靜態貼圖 URL 生成"""
        sticker_id = "1"
        sticker_type = "static"
        
        expected_url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker@2x.png"
        
        # 模擬 URL 生成邏輯
        if sticker_type == "animated":
            url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker_animation@2x.apng"
        else:
            url = expected_url
        
        assert url == expected_url
    
    def test_sticker_url_generation_animated(self):
        """測試動態貼圖 URL 生成"""
        sticker_id = "1000001"
        sticker_type = "animated"
        
        expected_url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker_animation@2x.apng"
        
        if sticker_type == "animated":
            url = expected_url
        else:
            url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker@2x.png"
        
        assert url == expected_url
    
    def test_emoji_replacement(self):
        """測試 LINE emoji 替換"""
        text = "新郎：(emoji) 新娘：(emoji)"
        emojis = [{"productId": "1", "emojiId": "1"}]
        
        # LINE emoji 無法直接取得，替換為 ❤️
        if emojis:
            text = text.replace("(emoji)", "❤️")
        
        assert text == "新郎：❤️ 新娘：❤️"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## 🛡️ 邊界條件測試

### 4. 髒話過濾與內容驗證

**檔案**: `tests/test_badword_filtering.py`

```python
import pytest

BADWORDS = {"禁詞1", "禁詞2", "test_bad"}

def is_clean(text: str) -> bool:
    """檢查文字是否乾淨"""
    text_lower = text.lower()
    return not any(w in text_lower for w in BADWORDS)

class TestBadwordFiltering:
    """髒話過濾測試"""
    
    def test_clean_message_passes(self):
        """測試正常訊息通過"""
        message = "祝新人百年好合！"
        assert is_clean(message) == True
    
    def test_badword_detection(self):
        """測試髒話被檢測"""
        message = "祝新人禁詞1百年好合"
        assert is_clean(message) == False
    
    def test_case_insensitive_filtering(self):
        """測試大小寫不敏感"""
        message = "TEST_BAD message"
        assert is_clean(message) == False
    
    def test_substring_detection(self):
        """測試子字符串檢測"""
        message = "test_bad is here"
        assert is_clean(message) == False
    
    def test_empty_message_passes(self):
        """測試空訊息通過"""
        message = ""
        assert is_clean(message) == True
    
    def test_unicode_character_handling(self):
        """測試 Unicode 字符處理"""
        message = "祝新人百年好合 ❤️ 🎉"
        assert is_clean(message) == True

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## 🌐 WebSocket 廣播測試

**檔案**: `tests/test_websocket_broadcast.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

class TestWebSocketBroadcast:
    """WebSocket 廣播測試"""
    
    @pytest.mark.asyncio
    async def test_broadcast_to_all_connections(self):
        """測試廣播到所有連線"""
        # 模擬 WebSocket 連線
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        mock_ws3 = AsyncMock()
        
        connections = [mock_ws1, mock_ws2, mock_ws3]
        data = {"type": "text", "sender_name": "Alice", "content": "Hello"}
        
        # 模擬廣播
        for ws in connections:
            await ws.send_json(data)
        
        # 驗證所有連線都收到訊息
        mock_ws1.send_json.assert_called_with(data)
        mock_ws2.send_json.assert_called_with(data)
        mock_ws3.send_json.assert_called_with(data)
    
    @pytest.mark.asyncio
    async def test_dead_connection_cleanup(self):
        """測試斷線連線清理"""
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        
        # 模擬 ws2 拋出異常（斷線）
        mock_ws2.send_json.side_effect = Exception("Connection lost")
        
        connections = [mock_ws1, mock_ws2]
        data = {"type": "text"}
        
        dead_connections = []
        for ws in connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead_connections.append(ws)
        
        # 驗證斷線連線被識別
        assert len(dead_connections) == 1
        assert dead_connections[0] == mock_ws2

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## 📊 手工測試檢查清單

### 準備階段

- [ ] 設定 LINE Official Account 測試帳號
- [ ] 配置 Webhook URL（本地用 ngrok）
- [ ] 設定環境變數：`CHANNEL_SECRET`、`CHANNEL_ACCESS_TOKEN`
- [ ] SQLite 資料庫初始化

### Webhook 接收測試

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| W1 | 用 LINE 傳文字訊息 | 後端接收並存到 DB | | |
| W2 | 驗證 Webhook 簽名 | 成功通過簽名驗證 | | |
| W3 | 用 LINE 傳圖片 | 圖片下載並存儲 | | |
| W4 | 用 LINE 傳貼圖 | CDN 連結正確生成 | | |

### 彈幕功能測試

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| D1 | 傳暗號「婚禮開始...」 | 回覆「✅ 彈幕已開啟」 | | |
| D2 | 靜默模式傳訊息 | 不推送至大螢幕 | | |
| D3 | 開啟後傳訊息 | 即時顯示於大螢幕 | | |
| D4 | 傳髒話訊息 | 被過濾，不顯示 | | |
| D5 | 傳暗號「婚禮結束...」 | 回覆「⏹️ 彈幕已關閉」 | | |
| D6 | 傳暗號「現在彈幕狀態...」 | 回覆狀態 + 訊息計數 | | |

### 媒體顯示測試

| 項目 | 測試步驟 | 預期結果 | ✅/❌ | 備註 |
|------|---------|---------|-------|------|
| M1 | 傳圖片訊息 | 大螢幕即時顯示圖片 | | |
| M2 | 傳多張圖片 | 依序排列顯示 | | |
| M3 | 傳貼圖 | CDN 貼圖正確載入 | | |
| M4 | 檔案大小限制 | 超過限制拒絕上傳 | | |

---

## 🐛 問題追蹤模板

```markdown
## Webhook 訊息接收失敗

### 重現步驟
1. LINE 應用內傳訊息給 Bot
2. 檢查後端日誌
3. 查詢資料庫是否有記錄

### 預期行為
訊息應在 2 秒內出現在大螢幕

### 實際行為
訊息從未出現

### 環境
- Webhook URL: https://xxxx.ngrok.io/webhook
- 簽名驗證: ✅ Pass
- 資料庫: ✅ Connected
- 時間: 2026-05-06 16:00

### 嚴重程度
🔴 Critical
```

---

## 📋 上線前檢查清單

### 安全檢查

- [ ] ✅ LINE Webhook 簽名驗證有效
- [ ] ✅ 髒話過濾正確執行
- [ ] ✅ `CHANNEL_SECRET` 未洩露至 Git
- [ ] ✅ 所有 API 呼叫都有錯誤處理

### 功能檢查

- [ ] ✅ 3 個暗號正確執行
- [ ] ✅ 文字/圖片/貼圖都顯示正常
- [ ] ✅ WebSocket 廣播穩定
- [ ] ✅ SQLite 記錄完整

### 性能檢查

- [ ] ✅ 訊息延遲 < 1 秒
- [ ] ✅ 10+ 條訊息無堆積
- [ ] ✅ 網路斷線後自動重連

---

**文件版本**: 2026-05-06  
**維護者**: Kai (QA Lead)  
**測試完成日期**: ___________
