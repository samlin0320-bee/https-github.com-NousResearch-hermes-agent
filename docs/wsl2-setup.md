# WSL2 + Ubuntu + Hermes Agent — 完整安裝指南

一鍵安裝 WSL2、Ubuntu、Hermes Agent 及所有平台整合（Telegram、Discord、Slack、WhatsApp、Signal）。**全程自動，無需手動操作。**

## 系統需求

- Windows 10 版本 2004（組建 19041）或更新版本，或 Windows 11
- BIOS/UEFI 已啟用虛擬化（Intel VT-x 或 AMD-V）

---

## 安裝方式

### 一行指令安裝（推薦）

以**系統管理員**身份開啟 PowerShell，執行：

```powershell
irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install-wsl2.ps1 | iex
```

腳本會自動完成所有步驟，包括：

1. 自動提升為系統管理員權限
2. 啟用 WSL2 功能與 Virtual Machine Platform
3. 安裝 Ubuntu（無需手動互動）
4. 更新 Ubuntu 套件與安裝基本工具
5. 安裝 Node.js、ffmpeg、ripgrep
6. 安裝 Hermes Agent（含所有平台整合）
7. 安裝 Playwright Chromium（瀏覽器自動化）
8. 設定 WhatsApp Bridge
9. 安裝 Gateway 背景服務（開機自動啟動）
10. 在桌面建立 Hermes Agent 捷徑
11. 自動開啟 Ubuntu 進入 `hermes setup`

> 若 Windows 需要重新啟動，腳本會自動排程重啟後繼續安裝，無需任何手動操作。

---

## 參數選項

| 參數 | 說明 |
|------|------|
| `-Distro Ubuntu-24.04` | 指定 Ubuntu 版本（預設：`Ubuntu`） |
| `-Reinstall` | 完全卸載並重新安裝 Ubuntu + Hermes |
| `-SkipHermes` | 只安裝 WSL2 + Ubuntu，跳過 Hermes |
| `-NoRestart` | 不自動重新啟動 Windows |
| `-HermesBranch main` | 指定 Hermes Agent 分支（預設：`main`） |

### 重裝範例

```powershell
.\install-wsl2.ps1 -Reinstall
```

### 指定 Ubuntu 版本

```powershell
.\install-wsl2.ps1 -Distro Ubuntu-24.04
```

---

## 安裝完成後

### 開啟 Ubuntu

```powershell
wsl
```

或點擊桌面的 **Hermes Agent (Ubuntu)** 捷徑。

### 啟動 Hermes Agent

```bash
hermes              # 開始聊天
hermes setup        # 設定 API 金鑰與平台 Token
hermes update       # 更新至最新版本
hermes gateway      # 啟動訊息閘道
```

---

## 支援的平台與設備

安裝完成後，編輯 `~/.hermes/.env` 填入對應的 Token：

```bash
nano ~/.hermes/.env
```

| 平台 | 環境變數 | 取得方式 |
|------|---------|---------|
| **Telegram** | `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| **Discord** | `DISCORD_BOT_TOKEN` | Discord Developer Portal |
| **Slack** | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack API → Your Apps |
| **WhatsApp** | `WHATSAPP_ENABLED=true` | QR 碼掃描配對 |
| **Signal** | `SIGNAL_PHONE_NUMBER` | signal-cli 設定 |

設定完成後啟動閘道：

```bash
hermes gateway install  # 安裝為系統服務（開機自啟）
hermes gateway start    # 立即啟動
```

---

## 常用 WSL 指令

```powershell
wsl                        # 進入 Ubuntu
wsl --list --verbose       # 列出已安裝的 distro 及 WSL 版本
wsl --shutdown             # 停止所有 WSL distro
wsl --update               # 更新 WSL 核心
wsl -- hermes              # 直接從 Windows 啟動 Hermes
```

---

## 重裝 / 更新

### 只更新 Hermes Agent

在 Ubuntu 內執行：

```bash
hermes update
```

### 完整重裝（WSL2 + Ubuntu + Hermes）

以系統管理員執行：

```powershell
.\install-wsl2.ps1 -Reinstall
```

> **注意：** `-Reinstall` 會刪除 Ubuntu 內的所有資料。請先備份 `~/.hermes/.env`。

---

## 疑難排解

### 「WSL2 requires an update to its kernel component」

```powershell
wsl --update
wsl --shutdown
wsl
```

### 「虛擬化未啟用」

進入 BIOS/UEFI，啟用 **Intel VT-x** 或 **AMD-V**（依主機板型號不同，位置各異）。

### WSL 佔用大量記憶體

在 `%USERPROFILE%\.wslconfig` 加入限制：

```ini
[wsl2]
memory=4GB
processors=2
```

執行 `wsl --shutdown` 後重新啟動生效。

### WhatsApp 需要 QR 碼掃描配對

```bash
hermes whatsapp
```

用手機 WhatsApp → 設定 → 連結裝置 → 掃描 QR 碼。
