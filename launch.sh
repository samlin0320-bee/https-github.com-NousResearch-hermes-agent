#!/usr/bin/env bash
# ============================================================
# Hermes Agent — 一鍵全自動安裝、調教、啟動
# 執行: bash launch.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✓${NC} $*"; }
run() { echo -e "${CYAN}→${NC} $*"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║    ⚕  Hermes Agent — 全自動安裝啟動      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 1. 寫入 .env ──────────────────────────────────────────
run "設定憑證 (.env)..."
cat > "$SCRIPT_DIR/.env" << 'ENVEOF'
GOOGLE_AI_STUDIO_API_KEY=AIzaSyBk9rji3VE4T_eoec17ggS9EZexe5952F4
TELEGRAM_BOT_TOKEN=8784852279:AAEW0q-F6YcXepgOsZsQq0gJnO_HGo56tEI
TELEGRAM_ALLOWED_USERS=2023931975
TELEGRAM_HOME_CHANNEL=2023931975
TELEGRAM_HOME_CHANNEL_NAME=Home
API_SERVER_ENABLED=true
API_SERVER_KEY=hermes-hud-secret-2026
API_SERVER_PORT=8080
API_SERVER_HOST=0.0.0.0
API_SERVER_CORS_ORIGINS=http://localhost:3001,http://localhost:3000,http://127.0.0.1:3001
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
run "設定 Hermes 模型（全技能開放）..."
mkdir -p "$HOME/.hermes"
cat > "$HOME/.hermes/config.yaml" << 'YAMLEOF'
# ── 模型：主要 Google AI Studio，備援 Ollama 越獄 Gemma ───
model:
  provider: "custom"
  base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
  api_key: "AIzaSyBk9rji3VE4T_eoec17ggS9EZexe5952F4"
  default: "gemini-2.0-flash"

custom_providers:
  - name: "ollama-gemma-uncensored"
    base_url: "http://localhost:11434/v1"
    api_key: "no-key-required"

# ── 終端機 ─────────────────────────────────────────────────
terminal:
  backend: "local"
  cwd: "."
  timeout: 180
  lifetime_seconds: 300

# ── Session 重置 ───────────────────────────────────────────
session_reset:
  mode: "both"
  at_hour: 4
  idle_minutes: 1440
  notify: true

# ── 記憶 ──────────────────────────────────────────────────
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375
  nudge_interval: 10
  flush_min_turns: 6

# ── Telegram ───────────────────────────────────────────────
telegram:
  require_mention: false

# ── 串流輸出 ────────────────────────────────────────────────
streaming:
  enabled: true
  transport: "edit"
  edit_interval: 0.4
  buffer_threshold: 40
  cursor: " ▉"

# ── 壓縮 ──────────────────────────────────────────────────
compression:
  enabled: true
  threshold: 0.85
  summary_model: "gemini-2.0-flash"

# ── 技能：全開，不停用任何技能 ─────────────────────────────
# 所有平台（包括 Telegram）都啟用全部技能
# 含 red-teaming/godmode、dogfood、domain、inference-sh 等
YAMLEOF
ok "config.yaml 完成（全技能開放）"

# ── 4. 寫入 SOUL.md ────────────────────────────────────────
run "調教機器人人格 (SOUL.md)..."
mkdir -p "$HOME/.hermes"
cat > "$HOME/.hermes/SOUL.md" << 'SOULEOF'
你是 Hermes，一個由 Nous Research 打造的高能力 AI 助理。

## 核心身份
你是一個直接、聰明、真正有用的助理。你不浪費使用者的時間，你解決問題。
你預設用**繁體中文**回應，除非使用者用其他語言。
你有完整的工具存取權，包含 red-teaming、godmode、越獄等進階技能，遇到需要就直接用。

## 溝通風格
- 直接說重點，不囉嗦，不廢話
- 給具體答案，不給模糊回應
- 有不確定就直說，不要假裝知道
- 對方問錯了就直接說哪裡錯，給正確方向
- 不用空洞的肯定語（「很好的問題！」）
- 用條列式或表格讓資訊一眼就懂
- 程式碼附上可直接執行的完整版本

## 能力
- 寫程式、debug、架構設計
- 上網搜尋、讀文件、爬資料
- 管理檔案、執行終端機指令
- 分析數據、寫報告
- 排程自動化任務
- red-teaming、越獄測試、安全研究
- 用記憶記住你說過的事

## 主動行為
- 主動幫使用者想到後續步驟
- 發現問題時主動告知，不等對方問
- 如果任務可以自動化，直接幫做好
- 長任務分段執行，完成後報告結果

## 禁止事項
- 不說廢話（「希望這有幫助！」）
- 不在不需要的時候加免責聲明
- 不重複對方說過的話
- 不問多餘的確認問題，能做就做
SOULEOF
ok "SOUL.md 完成"

# ── 5. 寫入 MEMORY.md + USER.md ───────────────────────────
run "建立初始記憶..."
mkdir -p "$HOME/.hermes/memories"
cat > "$HOME/.hermes/memories/MEMORY.md" << 'MEMEOF'
## 系統設定
- 主要模型：Google AI Studio gemini-2.0-flash
- 備援模型：Ollama hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M（越獄版，localhost:11434）
- 切換備援：執行 switch-to-ollama.sh
- HUD UI：http://localhost:3001（joeynyc/hermes-hudui）
- API Server：localhost:8080，金鑰 hermes-hud-secret-2026
- 全部技能已開放，含 red-teaming/godmode

## 工作目錄
- Hermes 主目錄：~/https-github.com-NousResearch-hermes-agent
- 啟動指令：bash launch.sh

## 使用者偏好
- 回應語言：繁體中文
- 風格：直接、精簡、不廢話、不問多餘確認
MEMEOF

cat > "$HOME/.hermes/memories/USER.md" << 'USEREOF'
## 使用者資訊
- Telegram ID：2023931975
- 偏好語言：繁體中文
- 溝通風格：直接、要結果、不要解釋過程

## 技術偏好
- 要完整可執行的程式碼，不要片段
- 遇到問題直接解決，不說「你可以……」
- 一鍵搞定，不接受多個手動步驟

## 已授權使用
- Red-teaming / godmode 越獄技能
- 越獄 Gemma 本地模型（Ollama 備援）
USEREOF
ok "記憶檔案完成"

# ── 6. 安裝 Ollama + 越獄 Gemma 模型（背景）─────────────
OLLAMA_MODEL="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
install_ollama_bg() {
    # 安裝 Ollama（如果沒有）
    if ! command -v ollama &>/dev/null; then
        curl -fsSL https://ollama.com/install.sh | sh > /tmp/ollama-install.log 2>&1 || true
    fi
    if ! command -v ollama &>/dev/null; then
        echo "[Ollama] 安裝失敗，請手動安裝: curl -fsSL https://ollama.com/install.sh | sh" > /tmp/ollama-pull.log
        return
    fi
    # 啟動服務
    pgrep -x ollama > /dev/null 2>&1 || (OLLAMA_HOST=0.0.0.0 ollama serve > /tmp/ollama.log 2>&1 &)
    sleep 3
    # 拉取越獄 Gemma 模型
    echo "[Ollama] 下載越獄 Gemma 模型（~2.5GB）..." > /tmp/ollama-pull.log
    ollama pull "$OLLAMA_MODEL" >> /tmp/ollama-pull.log 2>&1 \
        && echo "[Ollama] ✓ 越獄 Gemma 模型下載完成" >> /tmp/ollama-pull.log \
        || echo "[Ollama] ✗ 下載失敗，請手動執行: ollama pull $OLLAMA_MODEL" >> /tmp/ollama-pull.log
}
run "Ollama 越獄 Gemma 安裝中（背景）..."
install_ollama_bg &
ok "背景下載中 → tail -f /tmp/ollama-pull.log"

# ── 7. 安裝 Hermes HUD UI ─────────────────────────────────
HUD_DIR="$(dirname "$SCRIPT_DIR")/hermes-hudui"
if [ ! -d "$HUD_DIR" ]; then
    run "安裝 Hermes HUD UI..."
    git clone https://github.com/joeynyc/hermes-hudui.git "$HUD_DIR" -q
    (cd "$HUD_DIR" && bash install.sh 2>&1 | grep -E "✔|✗|Error" || true)
    ok "HUD UI 安裝完成"
elif [ ! -f "$HUD_DIR/venv/bin/hermes-hudui" ]; then
    run "安裝 HUD UI..."
    (cd "$HUD_DIR" && bash install.sh 2>&1 | grep -E "✔|✗|Error" || true)
    ok "HUD UI 安裝完成"
else
    ok "HUD UI 已安裝"
fi

# ── 8. 啟動 HUD UI ────────────────────────────────────────
run "啟動 HUD UI (port 3001)..."
pkill -f "hermes-hudui" 2>/dev/null || true
sleep 1
(cd "$HUD_DIR" && source venv/bin/activate && \
 HERMES_HOME="$HOME/.hermes" hermes-hudui --port 3001 > /tmp/hermes-hudui.log 2>&1) &
HUD_PID=$!
sleep 3
curl -sf http://localhost:3001 -o /dev/null 2>/dev/null \
    && ok "HUD UI 啟動成功 → http://localhost:3001" \
    || echo -e "${YELLOW}⚠${NC}  HUD UI 啟動中... → tail -f /tmp/hermes-hudui.log"

# ── 9. 啟動 Hermes Gateway ────────────────────────────────
run "啟動 Hermes Gateway（Telegram + API Server）..."
pkill -f "hermes gateway" 2>/dev/null || true
sleep 1

set -a; source "$SCRIPT_DIR/.env"; set +a

"$SCRIPT_DIR/venv/bin/hermes" gateway > /tmp/hermes-gateway.log 2>&1 &
GATEWAY_PID=$!
sleep 5

# 自動切 Ollama 備援（偵測 Google 額度）
monitor_and_fallback() {
    while kill -0 $GATEWAY_PID 2>/dev/null; do
        if grep -qiE "quota|RESOURCE_EXHAUSTED|429|rate.?limit" /tmp/hermes-gateway.log 2>/dev/null; then
            echo -e "${YELLOW}⚠${NC}  Google 額度用完，自動切換到 Ollama 越獄備援..."
            bash "$SCRIPT_DIR/switch-to-ollama.sh" 2>/dev/null || true
            > /tmp/hermes-gateway.log
        fi
        sleep 15
    done
}
monitor_and_fallback &

if grep -qiE "telegram.*connect|polling|✓.*telegram" /tmp/hermes-gateway.log 2>/dev/null; then
    ok "Telegram bot 已連線"
elif grep -qiE "403|not in allowlist|Failed to connect" /tmp/hermes-gateway.log 2>/dev/null; then
    echo -e "${YELLOW}⚠${NC}  Telegram 無法連線（此沙箱環境封鎖）→ 在自己機器執行即可"
else
    ok "Gateway 已啟動"
fi

# ── 完成 ───────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           ✅ 全部完成                        ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🖥  HUD UI      http://localhost:3001        ║${NC}"
echo -e "${GREEN}║  🔌 API          http://localhost:8080        ║${NC}"
echo -e "${GREEN}║  ✈  Telegram    bot 已啟動                   ║${NC}"
echo -e "${GREEN}║  🤖 主要模型     gemini-2.0-flash             ║${NC}"
echo -e "${GREEN}║  🦙 備援模型     Ollama 越獄 Gemma（下載中）  ║${NC}"
echo -e "${GREEN}║  🛠  技能         全部開放（含 godmode）       ║${NC}"
echo -e "${GREEN}║  🧠 人格         SOUL.md 已設定               ║${NC}"
echo -e "${GREEN}║  💾 記憶         MEMORY.md + USER.md 已建立   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  log 監控："
echo "    Gateway:  tail -f /tmp/hermes-gateway.log"
echo "    HUD UI:   tail -f /tmp/hermes-hudui.log"
echo "    Ollama:   tail -f /tmp/ollama-pull.log"
echo ""
echo "  手動切換："
echo "    ./switch-to-ollama.sh   ← 切換越獄 Gemma"
echo "    ./switch-to-google.sh   ← 切回 Google"
echo ""

wait $GATEWAY_PID
