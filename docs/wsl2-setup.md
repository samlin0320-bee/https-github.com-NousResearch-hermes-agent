# WSL2 + Ubuntu Setup Guide

This guide walks you through installing WSL2 (Windows Subsystem for Linux 2) with Ubuntu on Windows, then running Hermes Agent inside it.

## Requirements

- Windows 10 version 2004 (build 19041) or later, **or** Windows 11
- Virtualization enabled in BIOS/UEFI (Intel VT-x / AMD-V)
- Administrator privileges

To check your Windows build: press `Win + R`, type `winver`, press Enter.

---

## Option A: Automated Install (Recommended)

Open **PowerShell as Administrator** and run:

```powershell
irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install-wsl2.ps1 | iex
```

This script will:
1. Verify Windows version compatibility
2. Enable WSL2 and install Ubuntu
3. Update Ubuntu packages and install essential tools
4. Install Hermes Agent inside Ubuntu

**Parameters**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Distro` | `Ubuntu` | Linux distro to install (`Ubuntu`, `Ubuntu-24.04`, `Ubuntu-22.04`, etc.) |
| `-SkipHermes` | off | Skip Hermes Agent installation |
| `-SkipRestart` | off | Skip automatic restart prompt |
| `-HermesBranch` | `main` | Hermes Agent git branch to install |

Example with options:

```powershell
.\install-wsl2.ps1 -Distro Ubuntu-24.04 -SkipHermes
```

---

## Option B: Manual Install

### Step 1 — Enable WSL2

Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

This single command enables WSL, installs the Linux kernel, sets WSL2 as default, and downloads Ubuntu.

> If you need a specific Ubuntu version:
> ```powershell
> wsl --install --distribution Ubuntu-24.04
> ```

### Step 2 — Restart Windows

WSL2 requires a restart to finish enabling the Virtual Machine Platform feature.

### Step 3 — Complete Ubuntu First-Run Setup

After restarting, Ubuntu will open automatically and ask you to:
1. Create a **Linux username** (can be different from your Windows username)
2. Set a **Linux password**

### Step 4 — Update Ubuntu

Inside the Ubuntu terminal:

```bash
sudo apt update && sudo apt upgrade -y
```

### Step 5 — Install Hermes Agent

Inside the Ubuntu terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

---

## Using WSL2

### Launch Ubuntu

From anywhere in Windows:

```powershell
wsl
```

Or search "Ubuntu" in the Start menu.

### Useful WSL Commands

```powershell
# List installed distros and their WSL version
wsl --list --verbose

# Stop all running distros
wsl --shutdown

# Set a specific distro as default
wsl --set-default Ubuntu

# Run a single command without entering the shell
wsl -- ls ~

# Open a specific distro
wsl --distribution Ubuntu-24.04
```

### Accessing Windows Files from Ubuntu

Your Windows drives are mounted at `/mnt/`:

```bash
ls /mnt/c/Users/YourName/
```

### Accessing Ubuntu Files from Windows

In File Explorer, navigate to `\\wsl$\Ubuntu\home\<username>\`  
Or type `\\wsl$` in the address bar to browse all distros.

---

## Running Hermes Agent in WSL2

After installation, open Ubuntu (`wsl`) and run:

```bash
hermes          # Start chatting
hermes setup    # Configure API keys and settings
hermes update   # Update to latest version
```

Your Hermes config lives at `~/.hermes/` inside Ubuntu.

---

## Troubleshooting

### "WSL2 requires an update to its kernel component"

Run in PowerShell (Admin):

```powershell
wsl --update
```

### "Virtualization not enabled"

Enable Intel VT-x or AMD-V in your BIOS/UEFI settings. Steps vary by manufacturer — search for your motherboard/laptop model + "enable virtualization BIOS".

### WSL2 won't start after Windows update

```powershell
wsl --shutdown
wsl --update
wsl
```

### Reset Ubuntu (keeps WSL2, removes Ubuntu data)

```powershell
wsl --unregister Ubuntu
wsl --install --distribution Ubuntu
```

> **Warning:** This deletes all files inside Ubuntu. Back up anything important first.

### Check WSL and kernel versions

```powershell
wsl --version
```

---

## Uninstalling WSL2

To remove a specific distro:

```powershell
wsl --unregister Ubuntu
```

To fully remove WSL from Windows:

```powershell
dism.exe /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux
dism.exe /online /disable-feature /featurename:VirtualMachinePlatform
```

Then restart Windows.
