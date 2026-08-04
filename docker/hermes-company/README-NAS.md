# hermes-company — MINKANAS 部署手冊（給 AI / 使用者）

> 24/7 全自動 AI 公司（Lala CEO + Lumi/Ori/Craft/Sage/Pixel 5 Agent）在 Synology
> MINKANAS 上的部署。遵循本 NAS 慣例：Container Manager 專案、bind mount、改檔免重建、
> 不動現有正式服務。由 Claude 整理，對齊 `\\MINKANAS\docker\NAS_AI操作手冊.md`。

---

## 0. 安全與影響評估（先讀）

- 本 stack **只新增隔離容器**（hermes-lala/lumi/ori/craft/sage/pixel + 一個 hermes-ollama）。
- **不發布任何對外埠**（agent 只做對 Telegram 的 outbound polling）→ **不影響 `video-transcript`
  正式服務**、不佔用 8888 / 5001 等既有埠。
- 資料全走 **bind mount** → `\\MINKANAS\docker\hermes-company\data\<agent>\`，File Station 可直接看。
- 機密（token、Google 金鑰）只放 `hermes-company\.env`，**不進 git**（`.gitignore` 已含 `.env`）。
- 既有 `\\MINKANAS\docker\ollama\` 若已有 ollama 容器，可改用它，見 §5。

---

## 1. 放置位置

```
\\MINKANAS\docker\hermes-company\        ← 本專案（新資料夾，不動 hermes-stack/hermes1/hermes2）
  ├── docker-compose.company.yml
  ├── Dockerfile、docker/company-entrypoint.sh
  ├── .env                               ← 機密（自己建，見 §3）
  └── data/                              ← 自動生成，各 agent 的 HERMES_HOME
      ├── lala/  lumi/  ori/  craft/  sage/  pixel/   (各含 SOUL.md/config.yaml/memories)
      └── ollama/                        (越獄 Gemma 模型)
```

---

## 2. 取得程式碼（擇一）

**方式 A：SSH + git（NAS 有 git 才行）**
```bash
ssh samlin320@192.168.31.11
cd /volume1/docker
git clone -b claude/setup-hermes-agent-rwV11 \
  https://github.com/samlin0320-bee/https-github.com-NousResearch-hermes-agent.git hermes-company
cd hermes-company
```

**方式 B：SSH + curl 下載壓縮包（群暉多半沒 git，用這個）**
```bash
ssh samlin320@192.168.31.11
cd /volume1/docker
curl -L -o hc.tar.gz \
  "https://github.com/samlin0320-bee/https-github.com-NousResearch-hermes-agent/archive/refs/heads/claude/setup-hermes-agent-rwV11.tar.gz"
tar xzf hc.tar.gz
mv https-github.com-NousResearch-hermes-agent-* hermes-company
cd hermes-company && rm -f ../hc.tar.gz
```

**方式 C：File Station 上傳** — 電腦上下載 repo zip，解壓後把整個資料夾拖進
`\\MINKANAS\docker\hermes-company\`。

---

## 3. 建立 .env（機密，只在 NAS 本地）

```bash
cp .env.company.example .env
nano .env      # 或 File Station 直接編輯
```
填入實際值：`GOOGLE_AI_STUDIO_API_KEY`、`TELEGRAM_ALLOWED_USERS`、四個頻道 ID、六個 bot token。
（頻道 ID：water_cooler -1003524949347、dev_log -1004304403281、ai-approval -1003976353259、
content -1004315195434。）

---

## 4. 啟動（擇一）

**方式 A：SSH CLI（最直接）**
```bash
sudo docker compose -f docker-compose.company.yml up -d --build   # 首次 build 5~15 分鐘
sudo docker compose -f docker-compose.company.yml ps
sudo docker compose -f docker-compose.company.yml logs -f lala
```

**方式 B：Container Manager 專案（GUI，本 NAS 慣例）**
1. Container Manager → 專案 → 新增
2. 專案名稱 `hermes-company`；路徑選 `/docker/hermes-company`
3. 來源選「使用現有的 docker-compose.yml」→ 選 `docker-compose.company.yml`
4. 建置 → 完成後 6 個 agent + ollama 會自動啟動、開機自動重啟

> 註：CLI 與 GUI 二選一即可。若用 GUI 專案，日後停/啟/重建都在 Container Manager 操作，
> 專案欄不會是「-」（避免 video-transcript 那種「先刪再建專案」的坑）。

---

## 5. Ollama 備援（可選）

Google 額度用完時 agent 會自動切 Ollama。模型要先拉一次：
```bash
sudo docker exec -it hermes-ollama ollama pull hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M
```
若想**改用既有 `\\MINKANAS\docker\ollama\` 的容器**：把 compose 內 `ollama` 服務移除，並把各
agent 的 `OLLAMA_BASE_URL` 改成既有容器位址（例如 `http://<既有ollama容器名>:11434/v1`，需同一
Docker 網路）。

---

## 6. Telegram 一次性設定（手機，需使用者本人）

把 6 個 bot 都設為對應群組的**管理員**（比關 privacy mode 快，設管理員即收得到群組訊息）：

| Bot | 群組 |
|-----|------|
| Lala | /ai-approval、/water_cooler、主頻道 |
| Lumi | /dev_log、/water_cooler |
| Craft | /content、/water_cooler |
| Ori / Sage / Pixel | /water_cooler |

驗證：在 /water_cooler 打 `@Lala 幫我規劃一個賣手工咖啡的品牌`，看到各 Agent 討論即成功。

---

## 7. 日常維運

```bash
# 狀態 / log / 重啟單一 agent / 全停
sudo docker compose -f docker-compose.company.yml ps
sudo docker compose -f docker-compose.company.yml logs -f lumi
sudo docker compose -f docker-compose.company.yml restart pixel
sudo docker compose -f docker-compose.company.yml down
```

- **改人格免重建**：直接編輯 `data/<agent>/SOUL.md`（File Station 可改）→ `restart <agent>` 生效。
  （注意：`company-entrypoint.sh` 每次啟動會重寫 SOUL/config；要永久改人格請改
  `docker/company-entrypoint.sh` 內對應段落後重建。）
- **更新程式**：`git pull`（或重新下載）→ `up -d --build`。
- 備份：動 compose 前 `cp docker-compose.company.yml docker-compose.company.yml.bak_YYYYMMDD`。

## 8. 故障排除

| 狀況 | 解法 |
|------|------|
| build 失敗（ARM NAS） | 本 Dockerfile 含 playwright chromium，ARM 機型可能需移除該行；貼錯誤給 AI |
| bot 群組沒反應 | 把 bot 設群組管理員（§6）|
| agent 一直重啟 | `logs -f <agent>` 看錯誤；多半是 .env token/金鑰沒填或填錯 |
| Google 429 額度 | 先 §5 拉 ollama 模型，會自動切備援 |
| 埠衝突 | 本 stack 預設不開埠；若手動開 11434 注意勿撞既有 ollama |
