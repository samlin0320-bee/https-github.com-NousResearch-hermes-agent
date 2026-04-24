# ============================================================================
# WSL2 + Ubuntu Installer for Hermes Agent
# ============================================================================
# Installs WSL2 with Ubuntu on Windows 10 (2004+) or Windows 11,
# then optionally installs Hermes Agent inside Ubuntu.
#
# Usage (run as Administrator):
#   .\install-wsl2.ps1
#   .\install-wsl2.ps1 -Distro Ubuntu-24.04 -SkipHermes
#
# Or from PowerShell (Admin) via one-liner:
#   irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install-wsl2.ps1 | iex
#
# ============================================================================

param(
    [string]$Distro = "Ubuntu",
    [switch]$SkipHermes,
    [switch]$SkipRestart,
    [string]$HermesBranch = "main"
)

$ErrorActionPreference = "Stop"

# ============================================================================
# Helper functions
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "│         ⚕ Hermes Agent — WSL2 + Ubuntu Setup           │" -ForegroundColor Magenta
    Write-Host "├─────────────────────────────────────────────────────────┤" -ForegroundColor Magenta
    Write-Host "│  Sets up WSL2 with Ubuntu and installs Hermes Agent.    │" -ForegroundColor Magenta
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info  { param([string]$m) Write-Host "→ $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "✓ $m" -ForegroundColor Green }
function Write-Warn  { param([string]$m) Write-Host "⚠ $m" -ForegroundColor Yellow }
function Write-Err   { param([string]$m) Write-Host "✗ $m" -ForegroundColor Red }

# ============================================================================
# Prerequisite checks
# ============================================================================

function Assert-AdminPrivileges {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Err "This script must be run as Administrator."
        Write-Info "Right-click PowerShell → 'Run as administrator', then re-run the script."
        exit 1
    }
    Write-Ok "Running as Administrator"
}

function Assert-WindowsVersion {
    Write-Info "Checking Windows version..."
    $build = [System.Environment]::OSVersion.Version.Build
    $major = [System.Environment]::OSVersion.Version.Major

    if ($major -lt 10 -or ($major -eq 10 -and $build -lt 19041)) {
        Write-Err "WSL2 requires Windows 10 version 2004 (build 19041) or later."
        Write-Info "Current build: $build"
        Write-Info "Update Windows via Settings → Windows Update."
        exit 1
    }
    Write-Ok "Windows build $build — WSL2 supported"
}

# ============================================================================
# WSL2 installation
# ============================================================================

function Get-WslStatus {
    try {
        $out = wsl --status 2>&1
        return $out -join " "
    } catch {
        return ""
    }
}

function Install-Wsl2 {
    Write-Info "Checking WSL installation..."

    # wsl.exe --install is available on Windows 10 2004+ / Windows 11
    $wslExe = "$env:SystemRoot\System32\wsl.exe"
    $wslInstalled = (Get-Command wsl -ErrorAction SilentlyContinue) -ne $null

    if ($wslInstalled) {
        $status = Get-WslStatus
        if ($status -match "WSL version") {
            Write-Ok "WSL is already installed"
            return $false  # No restart needed
        }
    }

    Write-Info "Installing WSL2 with $Distro (this may take a few minutes)..."
    Write-Info "Windows will download the Linux kernel and $Distro automatically."
    Write-Host ""

    try {
        # --no-launch prevents auto-launching after install so we can continue the script
        wsl --install --distribution $Distro --no-launch
        if ($LASTEXITCODE -ne 0) {
            throw "wsl --install exited with code $LASTEXITCODE"
        }
        Write-Ok "WSL2 and $Distro installed"
        return $true  # Restart required
    } catch {
        Write-Err "wsl --install failed: $_"
        Write-Info "Falling back to manual feature enablement..."
        Enable-WslFeatureManually
        return $true
    }
}

function Enable-WslFeatureManually {
    Write-Info "Enabling Windows Subsystem for Linux feature..."
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null

    Write-Info "Enabling Virtual Machine Platform feature..."
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null

    Write-Ok "Windows features enabled"
    Write-Warn "You must RESTART your computer before continuing."
    Write-Info "After restart, open PowerShell (Admin) and run:"
    Write-Info "  wsl --set-default-version 2"
    Write-Info "  wsl --install --distribution $Distro"
}

function Set-Wsl2Default {
    Write-Info "Setting WSL default version to 2..."
    try {
        wsl --set-default-version 2 | Out-Null
        Write-Ok "WSL2 set as default"
    } catch {
        Write-Warn "Could not set WSL default version: $_"
    }
}

function Update-WslKernel {
    Write-Info "Updating WSL kernel..."
    try {
        wsl --update 2>&1 | Out-Null
        Write-Ok "WSL kernel up to date"
    } catch {
        Write-Warn "WSL kernel update skipped (may need internet connection)"
    }
}

function Get-InstalledDistros {
    try {
        $list = wsl --list --quiet 2>&1 | Where-Object { $_ -and $_.Trim() -ne "" }
        return $list
    } catch {
        return @()
    }
}

function Wait-DistroReady {
    param([string]$DistroName)

    Write-Info "Waiting for $DistroName to be ready..."
    $maxAttempts = 30
    $attempt = 0

    while ($attempt -lt $maxAttempts) {
        $distros = Get-InstalledDistros
        $found = $distros | Where-Object { $_ -match [regex]::Escape($DistroName.Split("-")[0]) }
        if ($found) {
            Write-Ok "$DistroName is ready"
            return $true
        }
        Start-Sleep -Seconds 2
        $attempt++
        if ($attempt % 5 -eq 0) {
            Write-Info "Still waiting... ($attempt/$maxAttempts)"
        }
    }

    Write-Warn "$DistroName not detected after waiting. Continuing anyway..."
    return $false
}

# ============================================================================
# Ubuntu initial configuration
# ============================================================================

function Initialize-Ubuntu {
    Write-Info "Initializing Ubuntu (first-time setup)..."
    Write-Host ""
    Write-Host "  You will be prompted to create a Linux username and password." -ForegroundColor Yellow
    Write-Host "  Choose any username/password — this is for your Ubuntu account only." -ForegroundColor Yellow
    Write-Host ""

    try {
        # Launch Ubuntu to trigger first-run OOBE
        wsl --distribution $Distro -- bash -c "echo Ubuntu initialization complete" 2>&1 | Out-Null
    } catch { }
}

function Update-UbuntuPackages {
    Write-Info "Updating Ubuntu package lists..."
    try {
        wsl --distribution $Distro -- bash -c "sudo apt-get update -qq 2>/dev/null && sudo apt-get upgrade -y -qq 2>/dev/null"
        Write-Ok "Ubuntu packages updated"
    } catch {
        Write-Warn "Package update failed — you can run 'sudo apt update && sudo apt upgrade' inside Ubuntu later"
    }
}

function Install-UbuntuEssentials {
    Write-Info "Installing essential packages (git, curl, build tools)..."
    try {
        wsl --distribution $Distro -- bash -c @"
            export DEBIAN_FRONTEND=noninteractive
            sudo apt-get install -y -qq git curl wget build-essential python3-dev libffi-dev 2>/dev/null
"@
        Write-Ok "Essential packages installed"
    } catch {
        Write-Warn "Some packages may not have installed — run 'sudo apt install git curl build-essential' in Ubuntu"
    }
}

# ============================================================================
# Hermes Agent installation inside Ubuntu
# ============================================================================

function Install-HermesInUbuntu {
    if ($SkipHermes) {
        Write-Info "Skipping Hermes Agent installation (-SkipHermes)"
        return
    }

    Write-Host ""
    Write-Info "Installing Hermes Agent inside Ubuntu..."
    Write-Host ""

    $installCmd = "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/$HermesBranch/scripts/install.sh | bash"

    try {
        wsl --distribution $Distro -- bash -c $installCmd
        Write-Ok "Hermes Agent installed in Ubuntu"
    } catch {
        Write-Warn "Hermes Agent auto-install failed."
        Write-Info "You can install it manually inside Ubuntu:"
        Write-Info "  wsl"
        Write-Info "  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"
    }
}

# ============================================================================
# Completion & launch
# ============================================================================

function Show-Completion {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Green
    Write-Host "│              ✓ WSL2 + Ubuntu Ready!                     │" -ForegroundColor Green
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Green
    Write-Host ""

    Write-Host "Useful commands:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   wsl                   " -NoNewline -ForegroundColor Green
    Write-Host "Open Ubuntu shell"
    Write-Host "   wsl --list --verbose  " -NoNewline -ForegroundColor Green
    Write-Host "List installed distros and WSL versions"
    Write-Host "   wsl --shutdown        " -NoNewline -ForegroundColor Green
    Write-Host "Stop all running WSL distros"
    Write-Host "   wsl --unregister Ubuntu" -NoNewline -ForegroundColor Green
    Write-Host " Remove Ubuntu (destructive!)"
    Write-Host ""

    if (-not $SkipHermes) {
        Write-Host "Inside Ubuntu, run Hermes Agent with:" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "   hermes              " -NoNewline -ForegroundColor Green
        Write-Host "Start chatting"
        Write-Host "   hermes setup        " -NoNewline -ForegroundColor Green
        Write-Host "Configure API keys"
        Write-Host ""
    }

    Write-Host "Open Ubuntu now with:  " -NoNewline -ForegroundColor Yellow
    Write-Host "wsl" -ForegroundColor White
    Write-Host ""
}

function Prompt-Restart {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Yellow
    Write-Host "│            ↺ Restart Required                           │" -ForegroundColor Yellow
    Write-Host "├─────────────────────────────────────────────────────────┤" -ForegroundColor Yellow
    Write-Host "│  Windows needs to restart to finish enabling WSL2.      │" -ForegroundColor Yellow
    Write-Host "│  After restart, Ubuntu will auto-complete its setup.    │" -ForegroundColor Yellow
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Yellow
    Write-Host ""

    if ($SkipRestart) {
        Write-Info "Skipping automatic restart (-SkipRestart). Restart manually when ready."
        return
    }

    $response = Read-Host "Restart now? [Y/n]"
    if ($response -eq "" -or $response -match "^[Yy]") {
        Write-Info "Restarting in 10 seconds... (close this window to cancel)"
        Start-Sleep -Seconds 10
        Restart-Computer -Force
    } else {
        Write-Info "Restart manually when ready. Then open Ubuntu from the Start menu."
    }
}

# ============================================================================
# Main
# ============================================================================

function Main {
    Write-Banner
    Assert-AdminPrivileges
    Assert-WindowsVersion

    $needsRestart = Install-Wsl2

    if ($needsRestart) {
        Prompt-Restart
        return
    }

    Set-Wsl2Default
    Update-WslKernel

    # Check if distro is already installed
    $distros = Get-InstalledDistros
    $alreadyInstalled = $distros | Where-Object { $_ -match [regex]::Escape($Distro.Split("-")[0]) }

    if (-not $alreadyInstalled) {
        Write-Info "Installing $Distro distribution..."
        try {
            wsl --install --distribution $Distro --no-launch
            Wait-DistroReady -DistroName $Distro
        } catch {
            Write-Warn "Auto-install failed. Install from Microsoft Store: search '$Distro'"
        }
    } else {
        Write-Ok "$Distro is already installed"
    }

    Update-UbuntuPackages
    Install-UbuntuEssentials
    Install-HermesInUbuntu
    Show-Completion
}

try {
    Main
} catch {
    Write-Host ""
    Write-Err "Setup failed: $_"
    Write-Host ""
    Write-Info "Common fixes:"
    Write-Info "  1. Ensure you are running as Administrator"
    Write-Info "  2. Enable virtualization in BIOS/UEFI (Intel VT-x / AMD-V)"
    Write-Info "  3. Windows 10 version 2004 (build 19041) or Windows 11 required"
    Write-Info "  4. Try: wsl --install --distribution Ubuntu  (from Admin PowerShell)"
    Write-Host ""
}
