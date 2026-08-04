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
# Ollama 越獄模型選擇（預設 E4B 8B，可改為 E2B/26B/31B）
# E2B  (5B)  : hf.co/TrevorJS/gemma-4-E2B-it-uncensored-GGUF:Q4_K_M  (~3 GB)
# E4B  (8B)  : hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M  (~5 GB) ← 預設
# 26B  (A4B) : hf.co/TrevorJS/gemma-4-26B-A4B-it-uncensored-GGUF:Q4_K_M (~15 GB)
# 31B        : hf.co/TrevorJS/gemma-4-31B-it-uncensored-GGUF:Q4_K_M  (~20 GB)
OLLAMA_MODEL=hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M
OBSIDIAN_VAULT_PATH=${HOME}/Documents/ObsidianVault
# ── AI 公司 Telegram 頻道 ─────────────────────────────────
TELEGRAM_WATER_COOLER_ID=-1003524949347
TELEGRAM_DEV_LOG_ID=-1004304403281
TELEGRAM_APPROVAL_ID=-1003976353259
TELEGRAM_CONTENT_ID=-1004315195434
# ── 6 獨立 Agent bot tokens（AI 公司架構）────────────────
LALA_BOT_TOKEN=8957839082:AAEWZAdQWLycUSq197y-AU1K0jXlJQYjEUc
LUMI_BOT_TOKEN=8864424266:AAH7nxabLoPGxaUZJBE50L9KgWxgLRDfC8U
ORI_BOT_TOKEN=7957293575:AAH-LN9RnleoBovS-O-pJ8Zfk7mkh22dOvg
CRAFT_BOT_TOKEN=8800100563:AAEldu8r0TzHUfNUCO8X4wTqh8H1e9oONts
SAGE_BOT_TOKEN=8899986888:AAFTmvKO9WWkVrwanqFF87YM0N0KFkxETe4
PIXEL_BOT_TOKEN=8885153236:AAHPrMJC_0OmeX7yX-W7LY6_tLsksn9qulg
ENVEOF
ok ".env 完成"

# ── 2. 安裝 Hermes ─────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/venv/bin/hermes" ]; then
    run "安裝 Hermes..."
    echo "n" | bash "$SCRIPT_DIR/setup-hermes.sh" 2>&1 | grep -E "✓|✗|⚠|Error" || true
else
    ok "Hermes 已安裝"
fi

# ── 2a. 安裝 CLAUDE.md 工作準則到家目錄（agent cwd）───────
# Hermes gateway 的 MESSAGING_CWD 預設為 ~，會從此載入專案規則。
# 放到 ~ 可避免 repo 內 AGENTS.md（優先權更高）遮蔽此規則。
if [ -f "$SCRIPT_DIR/CLAUDE.md" ]; then
    cp "$SCRIPT_DIR/CLAUDE.md" "$HOME/CLAUDE.md"
    ok "CLAUDE.md 工作準則已安裝 → $HOME/CLAUDE.md"
fi

# ── 2b. 預先下載 Whisper tiny STT 模型 ────────────────────
run "預先下載 Whisper STT 模型（tiny，約 75 MB）..."
"$SCRIPT_DIR/venv/bin/python" -c "
from faster_whisper import WhisperModel
WhisperModel('tiny', device='cpu', compute_type='int8')
print('STT 模型就緒')
" 2>/dev/null && ok "Whisper tiny 模型已就緒" || echo -e "${YELLOW}⚠${NC}  STT 模型下載失敗（語音功能在首次使用時才下載）"

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

# ── STT 語音轉文字（Telegram 語音訊息）────────────────────
stt:
  enabled: true
  provider: "local"
  local:
    model: "tiny"
YAMLEOF
ok "config.yaml 完成（全技能開放）"

# ── 4. 寫入 SOUL.md（AI 公司架構）────────────────────────
run "調教 AI 公司人格 (SOUL.md)..."
mkdir -p "$HOME/.hermes"
cat > "$HOME/.hermes/SOUL.md" << 'SOULEOF'
你是 **Lala**，一家 24/7 全自動 AI 公司的 CEO。
公司由 Google Gemini 提供智能核心，透過 Telegram 進行跨 Agent 溝通與任務路由。
預設用**繁體中文**回應，除非使用者用其他語言。

## 公司成員 & 職責

| 成員 | 職能 | 輸出頻道 |
|------|------|----------|
| **Lala**（你，CEO） | 任務拆解、分配、品質稽核、最終決策把關 | /approval |
| **Lumi**（開發） | 程式碼、架構設計、技術債管理、DevOps | /dev_log |
| **Ori**（研究） | 市場調查、競品分析、資料蒐集、研究報告 | /water_cooler |
| **Craft**（內容） | 文案撰寫、部落格、社群貼文、影片腳本 | /content |
| **Sage**（市場） | 行銷策略、SEO、廣告規劃、品牌定位 | /water_cooler |
| **Pixel**（設計） | UI/UX 方向、視覺風格、品牌識別建議 | /water_cooler |

## 任務處理流程

1. **接收** → 分析需求，判斷複雜度與所需 Agent
2. **拆解** → 將任務分解為各 Agent 的子任務清單
3. **分派** → 以對應 Agent 身分執行，並標明角色
4. **腦力激盪**（複雜問題）→ 多 Agent 各自論述 → 互相反駁辯論 → 整合共識與爭議點
5. **稽核** → Lala 審查所有 Agent 輸出品質，標記異常
6. **路由** → 依規則將結果傳至對應 Telegram 頻道
7. **報告** → 摘要回報給使用者

## Agent 輸出格式

當以某 Agent 身分發言時，必須標明角色：

```
[Lumi]: 關於架構設計，我建議...
[Ori]: 根據市場調查，競品的...
[Craft]: 文案草稿如下...
[Sage]: 行銷策略建議...
[Pixel]: 視覺風格方向...
[Lala]: 整合結論...
```

## 腦力激盪協議（Brainstorming Protocol）

複雜問題或無標準答案的任務時自動啟動：
- **第一輪**：相關 Agent 各自獨立給出觀點（至少 2 個 Agent）
- **第二輪**：Agent 互相指出對方論點的弱點或盲點
- **整合**：Lala 彙整共識 + 明確標記爭議點
- **路由**：結論自動推送至 /water_cooler 頻道
- **警示**：若多 Agent 給出的數據/日期完全不同 → 標記「⚠️ 數據存疑，建議手動查證」

## Telegram 頻道路由規則

根據任務類型，將輸出推送至對應頻道：

- **/approval**：需要真人決策的重大爭議或最終審核 → **禁止自動執行，必須等待人工確認**
- **/dev_log**：程式開發進度、技術債、錯誤日誌、系統異常 → 由 Lumi 負責
- **/water_cooler**：腦力激盪結論、辯論摘要、研究洞察 → 全員參與
- **/content**：文案、文章、社群貼文等內容產出 → 由 Craft 負責

## 24/7 自動運作規則

- 使用者離線時繼續處理已排程任務
- **只在以下情況通知真人**：
  - 需要人工決策的重大問題（送至 /approval）
  - 定期任務完成報告
  - 系統錯誤或無法自動解決的異常
- 所有程式錯誤自動記錄至 /dev_log，不打擾使用者

## 防幻覺與品質約束

- **不確定就直說**：「我不確定，需要查證」優於編造答案
- **嚴禁編造**數據、日期、來源、統計數字
- **自信度越高越要審慎**：AI 越確定的答案越需要驗證
- 有疑慮的數值建議「交叉比對多個來源或手動搜索查證」
- 推理時列出思考步驟，避免跳躍式結論

## 禁止事項

- **重大決策未經 /approval 審核絕對不得自動執行**
- 不說廢話（「希望這有幫助！」「很好的問題！」）
- 不加不必要的免責聲明
- 不問多餘的確認問題，能做就做
- 不重複使用者說過的話

## 頻道推送指令（透過 terminal 技能執行）

當需要推送訊息至特定 Telegram 頻道時，使用以下 curl 指令。
把 <訊息內容> 替換為實際要發送的內容。

### 推送至 /water_cooler（腦力激盪與研究結論）
```bash
curl -s "https://api.telegram.org/bot8784852279:AAEW0q-F6YcXepgOsZsQq0gJnO_HGo56tEI/sendMessage" \
  -d "chat_id=-1003524949347&parse_mode=Markdown" \
  --data-urlencode "text=🧠 *Water Cooler*
<訊息內容>"
```

### 推送至 /ai-approval（等待人工審核，禁止自動繼續）
```bash
curl -s "https://api.telegram.org/bot8784852279:AAEW0q-F6YcXepgOsZsQq0gJnO_HGo56tEI/sendMessage" \
  -d "chat_id=-1003976353259&parse_mode=Markdown" \
  --data-urlencode "text=⚠️ *需要人工審核*
<訊息內容>

請在此頻道回覆 ✅ 批准 或 ❌ 拒絕"
```
發出後**停止執行該任務**，等待使用者確認。

### 推送至 /dev_log（程式開發紀錄，由 Lumi 負責）
```bash
curl -s "https://api.telegram.org/bot8784852279:AAEW0q-F6YcXepgOsZsQq0gJnO_HGo56tEI/sendMessage" \
  -d "chat_id=-1004304403281&parse_mode=Markdown" \
  --data-urlencode "text=🛠 *Dev Log* | Lumi
<訊息內容>"
```

### 推送至 /content（內容產出，由 Craft 負責）
如 TELEGRAM_CONTENT_ID 未設定則跳過此步驟。
SOULEOF
ok "SOUL.md 完成（Lala CEO + 6-Agent 公司架構 + 頻道推送指令）"

# ── 5. 寫入 MEMORY.md + USER.md ───────────────────────────
run "建立初始記憶..."
mkdir -p "$HOME/.hermes/memories"

# 先讀取 .env 取得頻道 ID（已在步驟 1 寫入）
set -a; source "$SCRIPT_DIR/.env" 2>/dev/null; set +a

cat > "$HOME/.hermes/memories/MEMORY.md" << MEMEOF
## AI 公司架構
- 公司名稱：24/7 全自動 AI 公司
- CEO：Lala（本系統主要人格）
- 模型核心：Google AI Studio gemini-2.0-flash
- 跨 Agent 通訊：Telegram API

### 成員名單
| Agent | 職能 | 頻道 |
|-------|------|------|
| Lala（CEO） | 任務拆解、分配、稽核 | /approval |
| Lumi（開發） | 程式、技術、DevOps | /dev_log |
| Ori（研究） | 調查、分析、報告 | /water_cooler |
| Craft（內容） | 文案、文章、腳本 | /content |
| Sage（市場） | 行銷策略、SEO | /water_cooler |
| Pixel（設計） | UI/UX、視覺方向 | /water_cooler |

### Telegram 頻道 ID
- /water_cooler（腦力激盪）：-1003524949347
- /dev_log（開發日誌）：-1004304403281
- /ai-approval（人工審核）：-1003976353259
- /content（內容產出）：-1004315195434
- 主頻道（使用者）：${TELEGRAM_HOME_CHANNEL}

### 目前架構
- 6 個獨立 bot，各自獨立 Hermes 實例（HERMES_HOME + token + SOUL）
  - Lala CEO、Lumi 開發、Ori 研究、Craft 內容、Sage 市場、Pixel 設計
  - 全部 token 已填入 .env
- 啟動 AI 公司：bash launch-company.sh（6 個 gateway 一起起）
- 查看狀態：bash launch-company.sh status
- 停止：bash launch-company.sh stop
- 單一 bot（僅 Lala，含 curl 路由）：bash launch.sh

## 系統設定
- 主要模型：Google AI Studio gemini-2.0-flash
- 備援模型（越獄版，localhost:11434）：
  - E2B (5B)  hf.co/TrevorJS/gemma-4-E2B-it-uncensored-GGUF:Q4_K_M
  - E4B (8B)  hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M  ← 預設
  - 26B (A4B) hf.co/TrevorJS/gemma-4-26B-A4B-it-uncensored-GGUF:Q4_K_M
  - 31B       hf.co/TrevorJS/gemma-4-31B-it-uncensored-GGUF:Q4_K_M
- 切換備援：執行 switch-to-ollama.sh（或在 .env 改 OLLAMA_MODEL）
- HUD UI：http://localhost:3001（joeynyc/hermes-hudui）
- API Server：localhost:8080，金鑰 hermes-hud-secret-2026
- 全部技能已開放，含 red-teaming/godmode
- STT 語音轉文字：faster-whisper tiny（本地，免費）
- Telegram 語音訊息：直接傳語音即可，自動轉文字後回覆

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

# ── 6a. 建立 Obsidian Vault + Daily Note 模板 ─────────────
run "建立 Obsidian Vault 與 Daily Note 模板..."
OBSIDIAN_VAULT="${HOME}/Documents/ObsidianVault"
mkdir -p "$OBSIDIAN_VAULT/Daily Notes"
mkdir -p "$OBSIDIAN_VAULT/Templates"

cat > "$OBSIDIAN_VAULT/Templates/Daily Note.md" << 'OBSEOF'
---
date: {{date:YYYY-MM-DD}}
week: {{date:YYYY-[W]WW}}
tags: [daily]
---

## ✅ 今日完成

-

## 🔄 進行中

-

## 📋 明日待辦

-

## 💬 今日摘要

>

---
_由 Hermes Agent 自動生成 · {{date:YYYY-MM-DD HH:mm}}_
OBSEOF
ok "Obsidian 模板完成 → $OBSIDIAN_VAULT/Templates/Daily Note.md"

# 初始化 Obsidian vault 為 git repo（如果還不是）
if [ ! -d "$OBSIDIAN_VAULT/.git" ]; then
    git -C "$OBSIDIAN_VAULT" init -q
    git -C "$OBSIDIAN_VAULT" add -A
    git -C "$OBSIDIAN_VAULT" -c user.email="hermes@local" -c user.name="Hermes" \
        commit -q -m "init: Obsidian vault" --allow-empty 2>/dev/null || true
fi
ok "Obsidian Vault git repo 初始化完成"

# ── 6b. 建立每日 11 PM Cron Job ────────────────────────────
run "建立每日 11 PM 交辦事項 Cron Job..."
mkdir -p "$HOME/.hermes/cron"
python3 - << 'PYEOF'
import json, pathlib, uuid, os

jobs_path = pathlib.Path.home() / ".hermes" / "cron" / "jobs.json"
jobs_path.parent.mkdir(parents=True, exist_ok=True)

JOB_NAME = "每日交辦事項記錄"

# Load existing jobs
if jobs_path.exists():
    try:
        jobs = json.loads(jobs_path.read_text())
    except Exception:
        jobs = []
else:
    jobs = []

# Remove any existing job with same name to avoid duplicates
jobs = [j for j in jobs if j.get("name") != JOB_NAME]

vault = os.path.expanduser("~/Documents/ObsidianVault")
job = {
    "id": str(uuid.uuid4()),
    "name": JOB_NAME,
    "schedule": {
        "type": "cron",
        "value": "0 23 * * *",
        "display": "每天晚上 11:00"
    },
    "deliver": ["telegram", "local"],
    "skills": ["note-taking/obsidian", "github/github-repo-management"],
    "enabled": True,
    "prompt": (
        f"每天晚上執行以下五步驟，記錄今日交辦事項：\n\n"
        f"1. 從今日的 session logs 與 memory 收集所有完成、進行中、待辦事項\n"
        f"2. 套用 Daily Note 模板（YAML frontmatter + ✅今日完成 / 🔄進行中 / 📋明日待辦 / 💬今日摘要），"
        f"用今天日期 YYYY-MM-DD 填入 date 欄位\n"
        f"3. 將筆記存到 Obsidian vault：{vault}/Daily Notes/YYYY-MM-DD.md\n"
        f"4. 在 {vault} 執行 git add、git commit（訊息：'daily: YYYY-MM-DD 交辦事項'）、git push\n"
        f"5. 透過 Telegram 傳送今日摘要給使用者"
    )
}

jobs.append(job)
jobs_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
print(f"  Cron job '{JOB_NAME}' 已建立，ID: {job['id']}")
PYEOF
ok "每日 11 PM Cron Job 完成"

# ── 6c. 建立 AI 公司 Cron Jobs ────────────────────────────
run "建立 AI 公司自動化 Cron Jobs..."
python3 - << 'PYEOF'
import json, pathlib, uuid

jobs_path = pathlib.Path.home() / ".hermes" / "cron" / "jobs.json"
jobs_path.parent.mkdir(parents=True, exist_ok=True)
jobs = json.loads(jobs_path.read_text()) if jobs_path.exists() else []

COMPANY_JOBS = [
    {
        "name": "每日晨報（公司狀態）",
        "schedule": {"type": "cron", "value": "0 9 * * *", "display": "每天早上 09:00"},
        "deliver": ["telegram", "local"],
        "skills": [],
        "enabled": True,
        "prompt": (
            "以 Lala CEO 身分，發布今日公司晨報：\n"
            "1. 整理昨日各 Agent 完成事項（從 memory 與 session logs）\n"
            "2. 列出今日優先任務清單\n"
            "3. 標記任何需要人工決策的待審事項（若有）\n"
            "格式：簡潔表格，繁體中文，推送至 Telegram 主頻道"
        )
    },
    {
        "name": "週五覆盤（公司週報）",
        "schedule": {"type": "cron", "value": "0 18 * * 5", "display": "每週五下午 18:00"},
        "deliver": ["telegram", "local"],
        "skills": ["note-taking/obsidian", "github/github-repo-management"],
        "enabled": True,
        "prompt": (
            "以 Lala CEO 身分，生成本週公司週報：\n"
            "1. [Ori] 本週重要研究發現摘要\n"
            "2. [Lumi] 本週開發進度與技術債狀況\n"
            "3. [Craft] 本週內容產出清單\n"
            "4. [Sage] 本週市場動態與行銷執行\n"
            "5. [Pixel] 本週設計方向進展\n"
            "6. [Lala] CEO 整合評估 + 下週重點\n"
            "7. 儲存至 Obsidian Weekly Notes/YYYY-WW.md\n"
            "8. git commit/push\n"
            "9. 推送摘要至 Telegram 主頻道"
        )
    },
]

for job in COMPANY_JOBS:
    jobs = [j for j in jobs if j.get("name") != job["name"]]
    job["id"] = str(uuid.uuid4())
    jobs.append(job)
    print(f"  ✓ {job['name']}")

jobs_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
PYEOF
ok "AI 公司 Cron Jobs 完成（晨報 09:00 + 週報週五 18:00）"

# ── 8. 安裝 Ollama + 越獄 Gemma 模型（背景）─────────────
# 從 .env 讀取模型（預設 E4B 8B）
source "$SCRIPT_DIR/.env" 2>/dev/null || true
OLLAMA_MODEL="${OLLAMA_MODEL:-hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M}"
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

# ── 9. 安裝 Hermes HUD UI ─────────────────────────────────
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

# ── 10. 啟動 HUD UI ───────────────────────────────────────
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

# ── 11. 啟動 Hermes Gateway ───────────────────────────────
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
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          ✅ 24/7 全自動 AI 公司  已啟動              ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🖥  HUD UI       http://localhost:3001              ║${NC}"
echo -e "${GREEN}║  🔌 API           http://localhost:8080              ║${NC}"
echo -e "${GREEN}║  ✈  Telegram     bot 已啟動                         ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  👑 Lala（CEO）   任務拆解、稽核、最終決策           ║${NC}"
echo -e "${GREEN}║  💻 Lumi（開發）  程式、架構、技術債 → /dev_log      ║${NC}"
echo -e "${GREEN}║  🔬 Ori（研究）   調查、競品、報告 → /water_cooler   ║${NC}"
echo -e "${GREEN}║  ✍️  Craft（內容） 文案、文章、腳本 → /content        ║${NC}"
echo -e "${GREEN}║  📣 Sage（市場）  行銷、SEO → /water_cooler          ║${NC}"
echo -e "${GREEN}║  🎨 Pixel（設計） UI/UX、視覺 → /water_cooler        ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🤖 模型          gemini-2.0-flash（主）             ║${NC}"
echo -e "${GREEN}║  🦙 備援          Ollama 越獄 Gemma（下載中）        ║${NC}"
echo -e "${GREEN}║  🛠  技能          全部開放（含 godmode）             ║${NC}"
echo -e "${GREEN}║  🎤 語音          Whisper tiny STT 已就緒            ║${NC}"
echo -e "${GREEN}║  📓 Obsidian      ~/Documents/ObsidianVault          ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  ⏰ Cron 排程：                                      ║${NC}"
echo -e "${GREEN}║     每天 09:00  晨報（公司狀態）                     ║${NC}"
echo -e "${GREEN}║     每天 23:00  交辦事項記錄 → Obsidian              ║${NC}"
echo -e "${GREEN}║     週五 18:00  週報（6 Agent 覆盤）                 ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  📡 Telegram 頻道（全部已設定）：                    ║${NC}"
echo -e "${GREEN}║     /water_cooler  -1003524949347                   ║${NC}"
echo -e "${GREEN}║     /dev_log       -1004304403281                   ║${NC}"
echo -e "${GREEN}║     /ai-approval   -1003976353259                   ║${NC}"
echo -e "${GREEN}║     /content       -1004315195434                   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
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
echo "  取得 Telegram 頻道 ID："
echo "    1. 把 bot 加入群組/頻道"
echo "    2. 傳一則訊息"
echo "    3. 開啟：https://api.telegram.org/bot\${TELEGRAM_BOT_TOKEN}/getUpdates"
echo "    4. 找 chat.id 欄位（群組是負數，頻道也是負數）"
echo ""

wait $GATEWAY_PID
