# 部署指南 — 24/7 全自動 AI 公司

> 一份走完所有步驟的指南。挑一條路線做即可。

---

## 你有兩條路線

| 路線 | 適合 | 電腦要開著嗎 |
|------|------|------------|
| **A. Windows + WSL** | 現在最快、你在電腦前 | 要（電腦關了 bot 就離線）|
| **B. NAS + Docker** | 真正 24/7 | 不用（NAS 自己跑）|

---

## 路線 A：Windows + WSL

### A-1. 修好 WSL（虛擬化已啟用才做）

工作管理員 → 效能 → CPU → 確認「虛擬化 / 模擬：已啟用」。

以**系統管理員**開 PowerShell，逐行執行：

```powershell
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
bcdedit /set hypervisorlaunchtype auto
```

**重新開機。**

重開後：
```powershell
wsl --set-default-version 2
wsl --install -d Ubuntu
```
第一次會要你設 Linux 使用者名稱 + 密碼（打密碼不顯示是正常的）。

### A-2. 修 WSL 的 DNS（常見問題）

進 WSL（`wsl`）後，若 clone 出現 `Could not resolve host`，逐行執行：

```bash
sudo rm -f /etc/resolv.conf
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
echo "nameserver 1.1.1.1" | sudo tee -a /etc/resolv.conf
```

讓它永久生效（避免重開又壞）：
```bash
sudo bash -c 'cat > /etc/wsl.conf <<EOF
[network]
generateResolvConf = false
EOF'
```

測試：`ping -c 2 github.com` 有回應即可。

### A-3. 抓程式並啟動

```bash
cd ~
git clone -b claude/setup-hermes-agent-rwV11 https://github.com/samlin0320-bee/https-github.com-NousResearch-hermes-agent.git hermes
cd hermes
bash launch.sh            # 安裝 + 主 bot + HUD UI
bash launch-company.sh    # 啟動 6 個 Agent
```

管理：
```bash
bash launch-company.sh status   # 看誰在跑
bash launch-company.sh stop     # 全部停
```

---

## 路線 B：NAS + Docker

### B-1. NAS 準備

- **Synology**：控制台開 SSH；套件中心裝「Container Manager」
- **QNAP**：控制台開 SSH；App Center 裝「Container Station」

### B-2. SSH 連進 NAS（Windows PowerShell 即可，不需 WSL）

```powershell
ssh 你的NAS帳號@NAS的IP
```

### B-3. 抓程式 + 建立密鑰檔

```bash
cd /volume1/docker 2>/dev/null || cd ~
git clone -b claude/setup-hermes-agent-rwV11 https://github.com/samlin0320-bee/https-github.com-NousResearch-hermes-agent.git hermes
cd hermes
cp .env.company.example .env
```

編輯 `.env`（`nano .env`），填入實際值：Google 金鑰、你的 Telegram ID、4 個頻道 ID、6 個 bot token。
（這些值不在 git 裡，需自行填入；問設定的人索取。）

### B-4. 啟動

```bash
sudo docker compose -f docker-compose.company.yml up -d --build   # 首次 build 約 5~15 分鐘
sudo docker compose -f docker-compose.company.yml ps              # 看狀態
sudo docker compose -f docker-compose.company.yml logs -f lala    # 看 log（Ctrl+C 離開）
```

停止：`sudo docker compose -f docker-compose.company.yml down`

---

## 兩條路都做完後：Telegram 一次性設定（手機上）

把 6 個 bot 都設成 3 個群組的**管理員**（比關 privacy mode 快，設管理員後 bot 就收得到群組訊息）：

> 群組 → 點群組名稱 → 管理員 → 新增管理員 → 搜尋 bot → 逐一加入

6 個 bot × 對應群組：

| Bot | 加入的群組 |
|-----|-----------|
| Lala | /ai-approval、/water_cooler、主頻道 |
| Lumi | /dev_log、/water_cooler |
| Ori / Sage / Pixel | /water_cooler |
| Craft | /content、/water_cooler |

---

## 驗證成功

在 `/water_cooler` 群組打：

```
@Lala 幫我規劃一個賣手工咖啡的品牌
```

Lala 會拆解任務、@ 各 Agent、大家開始討論。**看到它們對話 = 成功。**

---

## 頻道對應表

| 頻道 | Chat ID | 用途 |
|------|---------|------|
| /water_cooler | -1003524949347 | 腦力激盪、辯論、研究 |
| /dev_log | -1004304403281 | Lumi 開發紀錄 |
| /ai-approval | -1003976353259 | 人工審核（Lala 推送後停下等你）|
| /content | -1004315195434 | Craft 內容產出 |

## 常見問題

| 問題 | 解法 |
|------|------|
| WSL `HCS_E_CONNECTION_TIMEOUT` | 見 A-1，多半是缺 VirtualMachinePlatform，補上重開 |
| WSL `Could not resolve host` | 見 A-2，修 DNS |
| bot 在群組沒反應 | 把 bot 設成群組管理員（見上方一次性設定）|
| Google 額度用完 | 各 agent 自動切 Ollama 備援（需先拉模型）|
| PowerShell `&&` 報錯 | 舊版 PowerShell 用 `;` 分隔，或改用 WSL 內的 bash |
