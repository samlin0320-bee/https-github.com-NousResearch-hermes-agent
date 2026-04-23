#!/usr/bin/env bash
# ============================================================
# Hermes Agent — 一鍵全自動安裝並啟動
# 執行: bash launch.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✓${NC} $*"; }
run() { echo -e "${CYAN}→${NC} $*"; }
warn(){ echo -e "${YELLOW}⚠${NC} $*"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║    ⚕  Hermes Agent — 全自動啟動      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ── 1. 寫入 .env ──────────────────────────────────────────
run "設定憑證 (.env)..."
cat > "$SCRIPT_DIR/.env" << 'ENVEOF'
# 主要模型：Google AI Studio (Gemini)
GOOGLE_AI_STUDIO_API_KEY=AIzaSyBk9rji3VE4T_eoec17ggS9EZexe5952F4

# Telegram
TELEGRAM_BOT_TOKEN=8784852279:AAEW0q-F6YcXepgOsZsQq0gJnO_HGo56tEI
TELEGRAM_ALLOWED_USERS=2023931975
TELEGRAM_HOME_CHANNEL=2023931975
TELEGRAM_HOME_CHANNEL_NAME=Home

# API Server (HUD UI)
API_SERVER_ENABLED=true
API_SERVER_KEY=hermes-hud-secret-2026
API_SERVER_PORT=8080
API_SERVER_HOST=0.0.0.0
API_SERVER_CORS_ORIGINS=http://localhost:3001,http://localhost:3000,http://127.0.0.1:3001

# 除錯
WEB_TOOLS_DEBUG=false
VISION_TOOLS_DEBUG=false
ENVEOF
ok ".env 完成"

# ── 2. 安裝 Hermes ─────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/venv/bin/hermes" ]; then
    run "安裝 Hermes..."
    echo "n" | bash "$SCRIPT_DIR/setup-hermes.sh" 2>&1 | grep -E "✓|✗|⚠|Error" || true
else
    ok "Hermes 已安裝"
fi

# ── 3. 寫入 ~/.hermes/config.yaml ─────────────────────────
run "設定 Hermes 模型 (Google AI Studio → gemini-2.0-flash)..."
mkdir -p "$HOME/.hermes"
cat > "$HOME/.hermes/config.yaml" << 'YAMLEOF'
model:
  provider: "custom"
  base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
  api_key: "AIzaSyBk9rji3VE4T_eoec17ggS9EZexe5952F4"
  default: "gemini-2.0-flash"

custom_providers:
  - name: "ollama-gemma"
    base_url: "http://localhost:11434/v1"
    api_key: "no-key-required"

terminal:
  backend: "local"
  cwd: "."
  timeout: 180
  lifetime_seconds: 300

session_reset:
  mode: "both"
  at_hour: 4
  idle_minutes: 1440
  notify: true

telegram:
  require_mention: false

streaming:
  enabled: true
  transport: "edit"
  edit_interval: 0.4
  buffer_threshold: 40
  cursor: " ▉"

compression:
  enabled: true
  threshold: 0.85
  summary_model: "gemini-2.0-flash"
YAMLEOF
ok "config.yaml 完成"

# ── 4. 安裝 Ollama + 拉取模型 (背景執行，不阻塞啟動) ────────
OLLAMA_MODEL="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"

install_ollama_bg() {
    if ! command -v ollama &>/dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh > /tmp/ollama-install.log 2>&1 || true
    fi
    if command -v ollama &>/dev/null; then
        pgrep -x ollama > /dev/null 2>&1 || (OLLAMA_HOST=0.0.0.0 ollama serve > /tmp/ollama.log 2>&1 &)
        sleep 3
        ollama pull "$OLLAMA_MODEL" > /tmp/ollama-pull.log 2>&1 \
            && echo "[Ollama] 模型下載完成" >> /tmp/ollama-pull.log \
            || echo "[Ollama] 下載失敗，請手動執行: ollama pull $OLLAMA_MODEL" >> /tmp/ollama-pull.log
    fi
}
run "Ollama 備援模型安裝中 (背景執行，不影響啟動)..."
install_ollama_bg &
OLLAMA_BG_PID=$!
ok "Ollama 背景安裝已開始 (log: /tmp/ollama-pull.log)"

# ── 5. 安裝 Hermes HUD UI ──────────────────────────────────
HUD_DIR="$(dirname "$SCRIPT_DIR")/hermes-hudui"
if [ ! -d "$HUD_DIR" ]; then
    run "安裝 Hermes HUD UI..."
    git clone https://github.com/joeynyc/hermes-hudui.git "$HUD_DIR" -q
    (cd "$HUD_DIR" && bash install.sh 2>&1 | grep -E "✔|✗|Error" || true)
    ok "HUD UI 安裝完成"
elif [ ! -f "$HUD_DIR/venv/bin/hermes-hudui" ]; then
    run "重新安裝 HUD UI..."
    (cd "$HUD_DIR" && bash install.sh 2>&1 | grep -E "✔|✗|Error" || true)
    ok "HUD UI 安裝完成"
else
    ok "HUD UI 已安裝"
fi

# ── 6. 啟動 HUD UI ────────────────────────────────────────
run "啟動 HUD UI (port 3001)..."
pkill -f "hermes-hudui" 2>/dev/null || true
sleep 1
(cd "$HUD_DIR" && source venv/bin/activate && hermes-hudui --port 3001 > /tmp/hermes-hudui.log 2>&1) &
HUD_PID=$!
sleep 3
if curl -sf http://localhost:3001 -o /dev/null 2>/dev/null; then
    ok "HUD UI 啟動成功 → http://localhost:3001"
else
    warn "HUD UI 啟動中... (log: /tmp/hermes-hudui.log)"
fi

# ── 7. 啟動 Hermes Gateway (Telegram + API Server) ────────
run "啟動 Hermes Gateway (Telegram bot + API server)..."
pkill -f "hermes gateway" 2>/dev/null || true
sleep 1

set -a; source "$SCRIPT_DIR/.env"; set +a

"$SCRIPT_DIR/venv/bin/hermes" gateway > /tmp/hermes-gateway.log 2>&1 &
GATEWAY_PID=$!
sleep 4

# 確認 gateway 狀態
if grep -q "Telegram.*connected\|✓.*telegram\|polling" /tmp/hermes-gateway.log 2>/dev/null; then
    ok "Telegram bot 連線成功"
elif grep -q "403\|not in allowlist\|Failed to connect" /tmp/hermes-gateway.log 2>/dev/null; then
    warn "Telegram API 被此環境封鎖 → 請在自己的機器上執行此腳本"
else
    ok "Gateway 已啟動"
fi

# ── 8. 完成，顯示所有網址 ─────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           ✅ 全部啟動完成                ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🖥  HUD UI   http://localhost:3001       ║${NC}"
echo -e "${GREEN}║  🔌 API      http://localhost:8080        ║${NC}"
echo -e "${GREEN}║  ✈  Telegram bot 已啟動 (token 已設定)   ║${NC}"
echo -e "${GREEN}║  🤖 模型     gemini-2.0-flash (主要)      ║${NC}"
echo -e "${GREEN}║  🦙 備援     Ollama Gemma (背景下載中)    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  停止所有服務: kill $GATEWAY_PID $HUD_PID"
echo "  Ollama log:   tail -f /tmp/ollama-pull.log"
echo "  Gateway log:  tail -f /tmp/hermes-gateway.log"
echo "  HUD log:      tail -f /tmp/hermes-hudui.log"
echo ""

# 保持 gateway 在前景
wait $GATEWAY_PID
