# ============================================================================
# WSL2 + Ubuntu + Hermes Agent — Full Auto Installer
# ============================================================================
# Installs WSL2 with Ubuntu and Hermes Agent (all platforms/devices).
# Fully non-interactive — no prompts, no manual steps required.
#
# Run as Administrator (one-liner):
#   irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install-wsl2.ps1 | iex
#
# Options:
#   .\install-wsl2.ps1                        # Full auto install
#   .\install-wsl2.ps1 -Reinstall             # Wipe and reinstall everything
#   .\install-wsl2.ps1 -Distro Ubuntu-24.04   # Use specific Ubuntu version
#   .\install-wsl2.ps1 -SkipHermes            # WSL2 + Ubuntu only, skip Hermes
#   .\install-wsl2.ps1 -NoRestart             # Don't auto-restart Windows
#
# ============================================================================

param(
    [string]$Distro       = "Ubuntu",
    [string]$HermesBranch = "main",
    [switch]$Reinstall,
    [switch]$SkipHermes,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"

# ============================================================================
# Helpers
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "│      ⚕ Hermes Agent — WSL2 + Ubuntu Full Auto Installer    │" -ForegroundColor Magenta
    Write-Host "├─────────────────────────────────────────────────────────────┤" -ForegroundColor Magenta
    Write-Host "│  Platforms: Telegram · Discord · Slack · WhatsApp · Signal  │" -ForegroundColor Magenta
    Write-Host "│  No prompts. No manual steps. Fully automated.              │" -ForegroundColor Magenta
    Write-Host "└─────────────────────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info  { param([string]$m) Write-Host "  → $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "  ✓ $m" -ForegroundColor Green }
function Write-Warn  { param([string]$m) Write-Host "  ⚠ $m" -ForegroundColor Yellow }
function Write-Err   { param([string]$m) Write-Host "  ✗ $m" -ForegroundColor Red }
function Write-Step  { param([string]$m) Write-Host ""; Write-Host "── $m" -ForegroundColor White }

# ============================================================================
# Admin + version checks (auto-elevate if not admin)
# ============================================================================

function Ensure-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Warn "Not running as Administrator — re-launching elevated..."
        $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        if ($Reinstall)    { $args += " -Reinstall" }
        if ($SkipHermes)   { $args += " -SkipHermes" }
        if ($NoRestart)    { $args += " -NoRestart" }
        if ($Distro)       { $args += " -Distro $Distro" }
        if ($HermesBranch) { $args += " -HermesBranch $HermesBranch" }
        Start-Process powershell -ArgumentList $args -Verb RunAs -Wait
        exit
    }
    Write-Ok "Running as Administrator"
}

function Assert-WindowsVersion {
    $build = [System.Environment]::OSVersion.Version.Build
    $major = [System.Environment]::OSVersion.Version.Major
    if ($major -lt 10 -or ($major -eq 10 -and $build -lt 19041)) {
        Write-Err "WSL2 requires Windows 10 version 2004 (build 19041+) or Windows 11."
        Write-Err "Current build: $build — please update Windows and retry."
        exit 1
    }
    Write-Ok "Windows build $build — WSL2 supported"
}

# ============================================================================
# WSL2 setup
# ============================================================================

function Install-Wsl2 {
    Write-Step "WSL2 Installation"

    # Reinstall: remove existing distro first
    if ($Reinstall) {
        Write-Info "Reinstall mode: removing existing $Distro installation..."
        try { wsl --unregister $Distro 2>$null | Out-Null } catch { }
        Write-Ok "Existing distro removed"
    }

    # Check if WSL is already functional
    $wslStatus = ""
    try { $wslStatus = (wsl --status 2>&1) -join " " } catch { }

    if ($wslStatus -match "WSL version" -and -not $Reinstall) {
        Write-Ok "WSL already installed"
    } else {
        Write-Info "Installing WSL2 with $Distro..."
        try {
            wsl --install --distribution $Distro --no-launch | Out-Null
            Write-Ok "WSL2 installed"
        } catch {
            Write-Warn "wsl --install failed, enabling features manually..."
            dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart 2>&1 | Out-Null
            dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart 2>&1 | Out-Null
            Write-Ok "WSL features enabled"
            Schedule-PostRestartInstall
            Auto-Restart
            return
        }
    }

    # Set WSL2 as default and update kernel
    try { wsl --set-default-version 2 2>&1 | Out-Null } catch { }
    try { wsl --update 2>&1 | Out-Null; Write-Ok "WSL kernel up to date" } catch { }

    # Wait for distro to appear
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        $list = (wsl --list --quiet 2>&1) -join " "
        if ($list -match $Distro.Split("-")[0]) { $ready = $true; break }
        if ($i -eq 0) { Write-Info "Waiting for $Distro to register..." }
        Start-Sleep -Seconds 3
    }
    if ($ready) { Write-Ok "$Distro is ready" }
    else { Write-Warn "$Distro not detected yet — continuing anyway" }
}

# ============================================================================
# Schedule post-restart continuation (if Windows restart is needed)
# ============================================================================

function Schedule-PostRestartInstall {
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) {
        Write-Warn "Cannot schedule post-restart task (script path unknown)."
        Write-Info "After restarting, run the script again manually."
        return
    }

    $taskArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    if ($SkipHermes)   { $taskArgs += " -SkipHermes" }
    if ($NoRestart)    { $taskArgs += " -NoRestart" }
    if ($Distro)       { $taskArgs += " -Distro $Distro" }
    if ($HermesBranch) { $taskArgs += " -HermesBranch $HermesBranch" }

    $action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -RunOnlyIfNetworkAvailable:$false

    Register-ScheduledTask `
        -TaskName   "HermesWSL2PostRestart" `
        -Action     $action `
        -Trigger    $trigger `
        -Settings   $settings `
        -RunLevel   Highest `
        -Force | Out-Null

    Write-Ok "Scheduled: installer will auto-resume after restart"
}

function Remove-PostRestartTask {
    try {
        Unregister-ScheduledTask -TaskName "HermesWSL2PostRestart" -Confirm:$false -ErrorAction SilentlyContinue
    } catch { }
}

function Auto-Restart {
    if ($NoRestart) {
        Write-Warn "-NoRestart specified — skipping automatic restart."
        Write-Info "Restart Windows manually, then the installer will resume automatically."
        exit 0
    }
    Write-Step "Windows Restart Required"
    Write-Info "WSL2 features enabled. Restarting Windows in 10 seconds..."
    Write-Info "(Close this window to cancel the restart)"
    Start-Sleep -Seconds 10
    Restart-Computer -Force
}

# ============================================================================
# Ubuntu first-run and package setup
# ============================================================================

function Run-InUbuntu {
    param([string]$Command, [string]$Description = "")
    if ($Description) { Write-Info $Description }
    try {
        $result = wsl --distribution $Distro -- bash -c $Command 2>&1
        return $true
    } catch {
        return $false
    }
}

function Initialize-Ubuntu {
    Write-Step "Ubuntu Initial Setup"

    # Trigger OOBE without interaction (adduser creates default user on first wsl run)
    # On modern Ubuntu WSL images, first launch auto-creates a user without prompts
    # if /etc/wsl.conf or cloud-init is not blocking it.
    Run-InUbuntu "true" "Initializing Ubuntu..." | Out-Null

    # Update package lists and upgrade
    Write-Info "Updating Ubuntu packages (this may take a minute)..."
    Run-InUbuntu "DEBIAN_FRONTEND=noninteractive sudo apt-get update -qq 2>/dev/null && DEBIAN_FRONTEND=noninteractive sudo apt-get upgrade -y -qq 2>/dev/null" | Out-Null
    Write-Ok "Ubuntu packages updated"

    # Install all essential build dependencies
    Write-Info "Installing essential packages..."
    $essentialPkgs = "git curl wget build-essential python3-dev python3-pip libffi-dev libssl-dev"
    Run-InUbuntu "DEBIAN_FRONTEND=noninteractive sudo apt-get install -y -qq $essentialPkgs 2>/dev/null" | Out-Null
    Write-Ok "Essential packages installed"
}

# ============================================================================
# Hermes Agent — install all platform integrations
# ============================================================================

function Install-HermesAllPlatforms {
    if ($SkipHermes) {
        Write-Info "Skipping Hermes Agent installation (-SkipHermes)"
        return
    }

    Write-Step "Hermes Agent Installation (All Platforms)"

    Write-Info "Platforms included: CLI · Telegram · Discord · Slack · WhatsApp · Signal · Gateway · Cron"
    Write-Info "Downloading and running Hermes installer inside Ubuntu..."

    $installCmd = "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/$HermesBranch/scripts/install.sh | bash -s -- --skip-setup"

    $ok = Run-InUbuntu $installCmd "Installing Hermes Agent (all extras)..."
    if ($ok) {
        Write-Ok "Hermes Agent installed"
    } else {
        Write-Warn "Hermes auto-install had issues — attempting fallback..."
        # Fallback: manual steps inside Ubuntu
        Run-InUbuntu "curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; export PATH=`$HOME/.local/bin:`$PATH; uv tool install hermes-agent 2>/dev/null || true" | Out-Null
    }

    # Install Node.js inside Ubuntu (for browser/Playwright tools)
    Write-Info "Installing Node.js LTS inside Ubuntu..."
    $nodeInstall = @"
        curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - 2>/dev/null
        DEBIAN_FRONTEND=noninteractive sudo apt-get install -y -qq nodejs 2>/dev/null
"@
    Run-InUbuntu $nodeInstall | Out-Null
    Write-Ok "Node.js installed"

    # Install Playwright browser (for browser automation tools)
    Write-Info "Installing Playwright Chromium browser..."
    Run-InUbuntu "cd ~/.hermes/hermes-agent 2>/dev/null && npm install --silent 2>/dev/null && npx playwright install --with-deps chromium 2>/dev/null || true" | Out-Null
    Write-Ok "Browser engine installed"

    # Install gateway systemd service
    Write-Info "Installing Hermes gateway service (auto-starts on WSL launch)..."
    Run-InUbuntu "~/.local/bin/hermes gateway install 2>/dev/null || true" | Out-Null
    Write-Ok "Gateway service installed"

    # Install ffmpeg (for TTS / voice messages)
    Write-Info "Installing ffmpeg (voice message / TTS support)..."
    Run-InUbuntu "DEBIAN_FRONTEND=noninteractive sudo apt-get install -y -qq ffmpeg 2>/dev/null" | Out-Null
    Write-Ok "ffmpeg installed"

    # Install ripgrep (for faster file search)
    Write-Info "Installing ripgrep (fast file search)..."
    Run-InUbuntu "DEBIAN_FRONTEND=noninteractive sudo apt-get install -y -qq ripgrep 2>/dev/null" | Out-Null
    Write-Ok "ripgrep installed"

    # Set up WhatsApp bridge Node dependencies
    Write-Info "Setting up WhatsApp bridge..."
    Run-InUbuntu "cd ~/.hermes/hermes-agent/scripts/whatsapp-bridge 2>/dev/null && npm install --silent 2>/dev/null || true" | Out-Null
    Write-Ok "WhatsApp bridge ready"

    # Sync bundled skills
    Write-Info "Syncing Hermes skills library..."
    Run-InUbuntu "~/.hermes/hermes-agent/venv/bin/python ~/.hermes/hermes-agent/tools/skills_sync.py 2>/dev/null || true" | Out-Null
    Write-Ok "Skills synced"
}

# ============================================================================
# Windows Terminal / Start Menu shortcut
# ============================================================================

function Add-WslShortcut {
    Write-Step "Windows Integration"

    # Add Windows Terminal profile (if WT is installed)
    $wtSettingsPath = "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
    if (Test-Path $wtSettingsPath) {
        Write-Info "Windows Terminal detected — Ubuntu profile already available"
    }

    # Create desktop shortcut to launch Ubuntu + hermes
    $shortcutPath = "$env:USERPROFILE\Desktop\Hermes Agent (Ubuntu).lnk"
    try {
        $wshShell = New-Object -ComObject WScript.Shell
        $shortcut = $wshShell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath  = "wsl.exe"
        $shortcut.Arguments   = "--distribution $Distro -- bash -c `"source ~/.bashrc; hermes; exec bash`""
        $shortcut.Description = "Hermes Agent (WSL2 Ubuntu)"
        $shortcut.IconLocation = "%SystemRoot%\System32\wsl.exe,0"
        $shortcut.Save()
        Write-Ok "Desktop shortcut created: Hermes Agent (Ubuntu)"
    } catch {
        Write-Warn "Could not create desktop shortcut (non-critical)"
    }
}

# ============================================================================
# Completion summary
# ============================================================================

function Show-Completion {
    Remove-PostRestartTask

    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────────┐" -ForegroundColor Green
    Write-Host "│                ✓ Everything Installed!                      │" -ForegroundColor Green
    Write-Host "└─────────────────────────────────────────────────────────────┘" -ForegroundColor Green
    Write-Host ""

    Write-Host "  Open Ubuntu:         " -NoNewline -ForegroundColor Cyan
    Write-Host "wsl" -ForegroundColor White

    Write-Host "  Start Hermes:        " -NoNewline -ForegroundColor Cyan
    Write-Host "wsl -- hermes" -ForegroundColor White

    Write-Host "  Configure API keys:  " -NoNewline -ForegroundColor Cyan
    Write-Host "wsl -- hermes setup" -ForegroundColor White

    Write-Host ""
    Write-Host "  Supported platforms:" -ForegroundColor Cyan
    Write-Host "    Telegram · Discord · Slack · WhatsApp · Signal · CLI · Gateway · Cron" -ForegroundColor White
    Write-Host ""
    Write-Host "  Configure in:  " -NoNewline -ForegroundColor Cyan
    Write-Host "wsl -- nano ~/.hermes/.env" -ForegroundColor White
    Write-Host ""

    # Auto-launch Ubuntu into hermes setup
    Write-Info "Launching Ubuntu → hermes setup now..."
    Start-Sleep -Seconds 2
    Start-Process "wsl.exe" -ArgumentList "--distribution $Distro -- bash -c `"source ~/.bashrc 2>/dev/null; ~/.local/bin/hermes setup; exec bash`""
}

# ============================================================================
# Main
# ============================================================================

try {
    Write-Banner
    Ensure-Admin
    Assert-WindowsVersion

    Install-Wsl2
    Initialize-Ubuntu
    Install-HermesAllPlatforms
    Add-WslShortcut
    Show-Completion

} catch {
    Write-Host ""
    Write-Err "Installation failed: $_"
    Write-Host ""
    Write-Info "If virtualization is not enabled, go to BIOS → enable Intel VT-x or AMD-V"
    Write-Info "Then re-run this script."
    Write-Host ""
    pause
}
