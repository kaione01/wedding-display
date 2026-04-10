#!/bin/bash
# 婚禮即時展示系統 — Mac 一鍵啟動
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "\n${BOLD}${CYAN}================================================${NC}"
echo -e "${BOLD}${CYAN}  🎉 婚禮即時展示系統  |  Bella & Kai  ${NC}"
echo -e "${BOLD}${CYAN}================================================${NC}\n"

# 1. Python 3
command -v python3 &>/dev/null || { echo -e "${RED}❌ 請先安裝 Python3${NC}"; exit 1; }
echo -e "${GREEN}✓ $(python3 --version)${NC}"

# 2. 套件
echo -e "${YELLOW}▶ 安裝套件...${NC}"
pip3 install -r requirements.txt -q --disable-pip-version-check
echo -e "${GREEN}✓ 套件已就緒${NC}"

# 3. 確認金鑰
grep -q "your_channel_secret_here" config.py && { echo -e "${RED}❌ 請在 config.py 填入 CHANNEL_SECRET${NC}"; exit 1; }
grep -q "your_channel_access_token_here" config.py && { echo -e "${RED}❌ 請在 config.py 填入 CHANNEL_ACCESS_TOKEN${NC}"; exit 1; }
echo -e "${GREEN}✓ LINE 設定已填入${NC}"

# 4. 婚紗照
mkdir -p static/wedding_bg uploads
COUNT=$(find static/wedding_bg -maxdepth 1 \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" \) 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT" -eq 0 ] && echo -e "${YELLOW}⚠️  static/wedding_bg/ 尚無照片${NC}" || echo -e "${GREEN}✓ 找到 $COUNT 張婚紗照${NC}"

# 5. 清除佔用的 port
lsof -ti:8000 | xargs kill -9 2>/dev/null || true

# 6. 啟動伺服器
echo -e "\n${YELLOW}▶ 啟動伺服器...${NC}"
python3 main.py &
SERVER_PID=$!
sleep 3
kill -0 $SERVER_PID 2>/dev/null || { echo -e "${RED}❌ 伺服器啟動失敗${NC}"; exit 1; }
echo -e "${GREEN}✓ 伺服器已啟動${NC}"
open "http://localhost:8000/display"

# 7. Cloudflare Tunnel
if ! command -v cloudflared &>/dev/null; then
  echo -e "${YELLOW}▶ 下載 cloudflared...${NC}"
  ARCH=$(uname -m)
  URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-$( [ "$ARCH" = "arm64" ] && echo arm64 || echo amd64).tgz"
  TMP=$(mktemp -d); curl -L -s "$URL" -o "$TMP/cf.tgz"; tar -xzf "$TMP/cf.tgz" -C "$TMP"
  chmod +x "$TMP/cloudflared"; mv "$TMP/cloudflared" "$SCRIPT_DIR/cloudflared"; rm -rf "$TMP"
  CF_CMD="$SCRIPT_DIR/cloudflared"
else
  CF_CMD="cloudflared"
fi

cleanup() {
  kill $SERVER_PID 2>/dev/null || true
  [ -n "$TUNNEL_PID" ] && kill $TUNNEL_PID 2>/dev/null || true
  rm -f .tunnel.log
  echo -e "\n${GREEN}✓ 系統已關閉${NC}\n${CYAN}💡 賓客照片存在 uploads/ 資料夾${NC}\n"
  exit 0
}
trap cleanup INT TERM

echo -e "\n${YELLOW}▶ 建立 Tunnel，請稍候...${NC}"
"$CF_CMD" tunnel --url http://localhost:8000 2>&1 | tee .tunnel.log &
TUNNEL_PID=$!

WEBHOOK_URL=""
for i in $(seq 1 30); do
  sleep 1
  URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' .tunnel.log 2>/dev/null | head -1)
  [ -n "$URL" ] && { WEBHOOK_URL="${URL}/webhook"; break; }
done

echo ""
if [ -n "$WEBHOOK_URL" ]; then
  echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}"
  echo -e "${BOLD}${GREEN}║   ✅ 系統就緒！請複製以下 Webhook URL       ║${NC}"
  echo -e "${BOLD}${GREEN}╠══════════════════════════════════════════════╣${NC}"
  echo -e "${BOLD}${CYAN}  $WEBHOOK_URL${NC}"
  echo -e "${BOLD}${GREEN}╠══════════════════════════════════════════════╣${NC}"
  echo -e "${YELLOW}  LINE Developers → Messaging API → Webhook URL${NC}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}"
  echo "$WEBHOOK_URL" > .last_webhook_url.txt
else
  echo -e "${RED}⚠️  無法取得 Tunnel 網址，請查看 log 中的 trycloudflare.com 網址${NC}"
fi

echo -e "\n${YELLOW}  按 Ctrl+C 關閉系統${NC}\n"
wait $TUNNEL_PID
