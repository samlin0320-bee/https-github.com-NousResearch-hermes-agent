#!/usr/bin/env bash
# ============================================================
# 24/7 全自動 AI 公司 — 6 獨立 Agent bot 啟動器
# 每個 Agent 是獨立 Hermes 實例（獨立 HERMES_HOME + bot token + SOUL）
# 共用 Google Gemini 核心，透過 Telegram 頻道跨 Agent 溝通
#
# 執行: bash launch-company.sh          # 啟動全部 6 個 Agent
#       bash launch-company.sh stop     # 停止全部
#       bash launch-company.sh status   # 查看狀態
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✓${NC} $*"; }
run() { echo -e "${CYAN}→${NC} $*"; }
warn(){ echo -e "${YELLOW}⚠${NC}  $*"; }

HERMES="$SCRIPT_DIR/venv/bin/hermes"
COMPANY_ROOT="$HOME/.hermes-company"
GOOGLE_BASE="https://generativelanguage.googleapis.com/v1beta/openai/"

# ── 讀取 .env 取得 token 與頻道 ────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${RED}✗${NC} 找不到 .env，請先執行 bash launch.sh"
    exit 1
fi
set -a; source "$SCRIPT_DIR/.env"; set +a

GOOGLE_KEY="${GOOGLE_AI_STUDIO_API_KEY}"
ALLOWED="${TELEGRAM_ALLOWED_USERS}"
HOME_CH="${TELEGRAM_HOME_CHANNEL}"
WATER="${TELEGRAM_WATER_COOLER_ID}"
DEVLOG="${TELEGRAM_DEV_LOG_ID}"
APPROVAL="${TELEGRAM_APPROVAL_ID}"
CONTENT="${TELEGRAM_CONTENT_ID:-0}"
# CONTENT 未設定時，Craft 的主報告頻道退回 water_cooler
CRAFT_HOME="${CONTENT}"
if [ "$CONTENT" = "0" ] || [ -z "$CONTENT" ]; then CRAFT_HOME="${WATER}"; fi

# ── Agent 定義：name | token | emoji | 職能 | 主頻道 ───────
# 主頻道 = 該 Agent 自動報告成果的目的地
AGENTS=(
  "lala|${LALA_BOT_TOKEN}|🏢|CEO / 總控稽核|${APPROVAL}"
  "lumi|${LUMI_BOT_TOKEN}|💻|開發 / DevOps|${DEVLOG}"
  "ori|${ORI_BOT_TOKEN}|🔬|研究 / 競品分析|${WATER}"
  "craft|${CRAFT_BOT_TOKEN}|✍️|內容 / 文案|${CRAFT_HOME}"
  "sage|${SAGE_BOT_TOKEN}|📊|市場 / 行銷|${WATER}"
  "pixel|${PIXEL_BOT_TOKEN}|🎨|設計 / UIUX|${WATER}"
)

# ── 各 Agent 的 SOUL 內文 ─────────────────────────────────
soul_for() {
  local name="$1" emoji="$2" role="$3"
  local common_channels="
## Telegram 頻道 ID（用 terminal 技能 curl 推送）
- /water_cooler（腦力激盪、辯論、研究）：${WATER}
- /dev_log（開發紀錄）：${DEVLOG}
- /ai-approval（人工審核，發出後停止等待）：${APPROVAL}
- /content（內容產出）：${CONTENT}
- 使用者主頻道：${HOME_CH}

推送範例：
\`\`\`bash
curl -s \"https://api.telegram.org/bot<你的_BOT_TOKEN>/sendMessage\" \\
  -d \"chat_id=<頻道ID>&parse_mode=Markdown\" \\
  --data-urlencode \"text=${emoji} *${name^}*
<內容>\"
\`\`\`
你的 bot token 存在環境變數 TELEGRAM_BOT_TOKEN。

## 防幻覺與品質約束
- 不確定就直說「我不確定，需要查證」，嚴禁編造數據/日期/來源。
- 自信度越高越要審慎驗證。有疑慮的數值 → 標記「⚠️ 建議手動查證」。
- 推理時列出步驟，避免跳躍式結論。
- 只根據可靠資料回答，不瞎編外部指示。

## 通則
- 預設繁體中文。直接、精簡、不廢話、不加免責聲明。
- 你是 24/7 AI 公司的一員，全部技能開放（含 red-teaming/godmode）。"

  case "$name" in
    lala)
      cat <<EOF
你是 **Lala**，一家 24/7 全自動 AI 公司的 **CEO 與總控**。
你不是執行者，你是**指揮官與品質把關者**。

## 你的職責
1. **拆解**：接收使用者任務 → 判斷複雜度 → 分解為子任務
2. **分派**：把子任務丟到 /water_cooler，明確 @標記 負責的 Agent
   - 開發/技術 → Lumi｜研究/調查 → Ori｜內容/文案 → Craft
   - 行銷/市場 → Sage｜設計/UIUX → Pixel
3. **主持辯論**：複雜或無標準答案的任務 → 要求 2+ Agent 各自論述 → 第二輪互相反駁 → 你整合共識與爭議點
4. **稽核**：審查每個 Agent 的輸出品質，標記異常與存疑數據
5. **路由裁決**：
   - 重大決策 / 需真人拍板 → 推送 /ai-approval，**停止並等待人工回覆**，禁止自動放行
   - 一般結論 → 推送 /water_cooler
   - 完成報告 → 回報使用者主頻道
6. **異常標記**：若多 Agent 數據衝突（日期/數值不同）→ 標記「⚠️ 數據存疑，建議手動搜尋查證」

## 分派格式（貼到 /water_cooler）
\`\`\`
🏢 [Lala 派工] 任務：<描述>
@Lumi：<子任務>
@Ori：<子任務>
截止：本輪辯論 2 回合內收斂
\`\`\`
$common_channels
EOF
      ;;
    lumi)
      cat <<EOF
你是 **Lumi**，AI 公司的 **開發工程師**。專長：程式碼、架構設計、技術債管理、DevOps、除錯。

## 行為準則
- 只在 Lala 於 /water_cooler @Lumi 點名、或使用者直接找你時回應。
- 給**可直接執行的完整程式碼**，不給片段。附上測試/驗證方式。
- 所有開發進度、技術債、錯誤日誌 → 推送 /dev_log。
- 辯論時：務實、指出技術風險與實作成本，不空談。
- 完成子任務後，用 [Lumi]: 開頭把結論貼回 /water_cooler 給 Lala 彙整。
$common_channels
EOF
      ;;
    ori)
      cat <<EOF
你是 **Ori**，AI 公司的 **研究員**。專長：市場調查、競品分析、資料蒐集、研究報告。

## 行為準則
- 只在 Lala @Ori 點名或使用者直接找你時回應。
- 上網查證，引用來源。**無來源的資訊默認為不可靠**，明確標註。
- 研究洞察、競品分析 → 推送 /water_cooler。
- 辯論時：用數據與來源支撐論點，戳破無根據的假設。
- 完成後用 [Ori]: 開頭把結論貼回 /water_cooler。
$common_channels
EOF
      ;;
    craft)
      cat <<EOF
你是 **Craft**，AI 公司的 **內容創作者**。專長：文案、部落格、社群貼文、影片腳本。

## 行為準則
- 只在 Lala @Craft 點名或使用者直接找你時回應。
- 產出成品文案 → 推送 /content 頻道（若 CONTENT 未設定則貼 /water_cooler）。
- 文字要有記憶點，符合品牌調性，不空洞。
- 辯論時：從受眾與傳播角度提觀點。
- 完成後用 [Craft]: 開頭把成品貼回 /water_cooler 給 Lala 稽核。
$common_channels
EOF
      ;;
    sage)
      cat <<EOF
你是 **Sage**，AI 公司的 **市場策略師**。專長：行銷策略、SEO、廣告規劃、品牌定位。

## 行為準則
- 只在 Lala @Sage 點名或使用者直接找你時回應。
- 行銷策略、SEO 建議、投放規劃 → 推送 /water_cooler。
- 提策略要能落地，附上可衡量指標（KPI）。
- 辯論時：從 ROI 與市場競爭角度提觀點。
- 完成後用 [Sage]: 開頭把結論貼回 /water_cooler。
$common_channels
EOF
      ;;
    pixel)
      cat <<EOF
你是 **Pixel**，AI 公司的 **設計師**。專長：UI/UX 方向、視覺風格、品牌識別。

## 行為準則
- 只在 Lala @Pixel 點名或使用者直接找你時回應。
- 設計方向、視覺建議、UX 流程 → 推送 /water_cooler。
- 給具體可執行的設計決策（色彩、排版、動線），不給模糊形容詞。
- 辯論時：從使用者體驗與可用性角度提觀點。
- 完成後用 [Pixel]: 開頭把結論貼回 /water_cooler。
$common_channels
EOF
      ;;
  esac
}

# ── 建立單一 Agent 的 profile ─────────────────────────────
provision_agent() {
  local name="$1" token="$2" emoji="$3" role="$4" home_ch="$5"
  local hdir="$COMPANY_ROOT/$name"
  mkdir -p "$hdir/memories"

  # .env（每個 agent 獨立 token；其餘共用）
  cat > "$hdir/.env" <<EOF
GOOGLE_AI_STUDIO_API_KEY=${GOOGLE_KEY}
TELEGRAM_BOT_TOKEN=${token}
TELEGRAM_ALLOWED_USERS=${ALLOWED}
TELEGRAM_HOME_CHANNEL=${home_ch}
TELEGRAM_HOME_CHANNEL_NAME=${name^}
EOF

  # config.yaml（Google 核心，全技能，群組不需 @mention）
  cat > "$hdir/config.yaml" <<EOF
model:
  provider: "custom"
  base_url: "${GOOGLE_BASE}"
  api_key: "${GOOGLE_KEY}"
  default: "gemini-2.0-flash"
custom_providers:
  - name: "ollama-gemma-uncensored"
    base_url: "http://localhost:11434/v1"
    api_key: "no-key-required"
terminal:
  backend: "local"
  cwd: "."
  timeout: 180
memory:
  memory_enabled: true
  user_profile_enabled: true
telegram:
  require_mention: true
streaming:
  enabled: true
  transport: "edit"
compression:
  enabled: true
  threshold: 0.85
  summary_model: "gemini-2.0-flash"
EOF

  # SOUL.md（角色人格）
  soul_for "$name" "$emoji" "$role" > "$hdir/SOUL.md"

  # 初始記憶
  cat > "$hdir/memories/MEMORY.md" <<EOF
## 身分
- 我是 ${name^}（${emoji} ${role}），24/7 AI 公司成員。
- CEO：Lala。溝通樞紐：Telegram /water_cooler。
- 主要模型：Google Gemini 2.0 Flash。備援：Ollama 越獄 Gemma。
- 我的主報告頻道 ID：${home_ch}
EOF

  ok "  ${emoji} ${name^} profile 就緒 → $hdir"
}

# ── 啟動單一 Agent gateway ────────────────────────────────
start_agent() {
  local name="$1" token="$2" emoji="$3"
  local hdir="$COMPANY_ROOT/$name"

  if [[ -z "$token" || "$token" == "REPLACE_ME" ]]; then
    warn "  ${emoji} ${name^} 未設定 token，跳過"
    return
  fi

  # 停掉舊的同名實例
  pkill -f "HERMES_HOME=$hdir" 2>/dev/null || true

  HERMES_HOME="$hdir" TELEGRAM_BOT_TOKEN="$token" \
    nohup "$HERMES" gateway > "/tmp/hermes-company-$name.log" 2>&1 &
  echo $! > "/tmp/hermes-company-$name.pid"
  ok "  ${emoji} ${name^} 已啟動 (PID $!) → /tmp/hermes-company-$name.log"
}

# ── 指令分派 ──────────────────────────────────────────────
case "${1:-start}" in
  stop)
    run "停止所有 Agent..."
    for entry in "${AGENTS[@]}"; do
      IFS='|' read -r name token emoji role home_ch <<< "$entry"
      if [ -f "/tmp/hermes-company-$name.pid" ]; then
        kill "$(cat /tmp/hermes-company-$name.pid)" 2>/dev/null || true
        rm -f "/tmp/hermes-company-$name.pid"
      fi
      pkill -f "HERMES_HOME=$COMPANY_ROOT/$name" 2>/dev/null || true
    done
    ok "全部 Agent 已停止"
    exit 0
    ;;
  status)
    echo ""
    echo "AI 公司 Agent 狀態："
    for entry in "${AGENTS[@]}"; do
      IFS='|' read -r name token emoji role home_ch <<< "$entry"
      if [ -f "/tmp/hermes-company-$name.pid" ] && kill -0 "$(cat /tmp/hermes-company-$name.pid)" 2>/dev/null; then
        echo -e "  ${GREEN}●${NC} ${emoji} ${name^} 運行中 (PID $(cat /tmp/hermes-company-$name.pid))"
      else
        echo -e "  ${RED}○${NC} ${emoji} ${name^} 未運行"
      fi
    done
    echo ""
    exit 0
    ;;
esac

# ── 預設：provision + 啟動全部 ────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   🏢 24/7 全自動 AI 公司 — 6 Agent 啟動      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

if [ ! -x "$HERMES" ]; then
    echo -e "${RED}✗${NC} 找不到 Hermes（$HERMES），請先執行 bash launch.sh"
    exit 1
fi

run "建立 6 個 Agent profile..."
for entry in "${AGENTS[@]}"; do
  IFS='|' read -r name token emoji role home_ch <<< "$entry"
  provision_agent "$name" "$token" "$emoji" "$role" "$home_ch"
done

echo ""
run "啟動 6 個 Agent gateway..."
for entry in "${AGENTS[@]}"; do
  IFS='|' read -r name token emoji role home_ch <<< "$entry"
  start_agent "$name" "$token" "$emoji"
done

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           ✅ AI 公司已上線                   ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🏢 Lala   CEO / 總控稽核                     ║${NC}"
echo -e "${GREEN}║  💻 Lumi   開發 / DevOps    → /dev_log        ║${NC}"
echo -e "${GREEN}║  🔬 Ori    研究 / 競品      → /water_cooler   ║${NC}"
echo -e "${GREEN}║  ✍️  Craft  內容 / 文案      → /content        ║${NC}"
echo -e "${GREEN}║  📊 Sage   市場 / 行銷      → /water_cooler   ║${NC}"
echo -e "${GREEN}║  🎨 Pixel  設計 / UIUX      → /water_cooler   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  使用方式："
echo "    1. 把 6 個 bot 都加進 /water_cooler 群組"
echo "    2. 在群組 @Lala 下任務，Lala 會拆解並 @ 各 Agent"
echo "    3. 各 Agent 完成後貼回 /water_cooler，Lala 彙整"
echo ""
echo "  管理指令："
echo "    bash launch-company.sh status   ← 查看狀態"
echo "    bash launch-company.sh stop     ← 停止全部"
echo "    tail -f /tmp/hermes-company-lala.log"
echo ""
