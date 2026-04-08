---
sidebar_position: 2
title: "Installation"
description: "Install Hermes Agent on Linux, macOS, WSL2, or Android via Termux"
---

# Installation

Get Hermes Agent up and running in under two minutes with the one-line installer, or follow the manual steps for full control.

## Quick Install

### Linux / macOS / WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

### Android / Termux

Hermes now ships a Termux-aware installer path too:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

The installer detects Termux automatically and switches to a tested Android flow:
- uses Termux `pkg` for system dependencies (`git`, `python`, `nodejs`, `ripgrep`, `ffmpeg`, build tools)
- creates the virtualenv with `python -m venv`
- exports `ANDROID_API_LEVEL` automatically for Android wheel builds
- installs a curated `.[termux]` extra with `pip`
- skips the untested browser / WhatsApp bootstrap by default

If you want the fully explicit path, follow the dedicated [Termux guide](./termux.md).

:::warning Windows
Native Windows is **not supported**. Please install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) and run Hermes Agent from there. The install command above works inside WSL2.
:::

### What the Installer Does

The installer handles everything automatically — all dependencies (Python, Node.js, ripgrep, ffmpeg), the repo clone, virtual environment, global `hermes` command setup, and LLM provider configuration. By the end, you're ready to chat.

### After Installation

Reload your shell and start chatting:

```bash
source ~/.bashrc   # or: source ~/.zshrc
hermes             # Start chatting!
```

To reconfigure individual settings later, use the dedicated commands:

```bash
hermes model          # Choose your LLM provider and model
hermes tools          # Configure which tools are enabled
hermes gateway setup  # Set up messaging platforms
hermes config set     # Set individual config values
hermes setup          # Or run the full setup wizard to configure everything at once
```

---

## Prerequisites

The only prerequisite is **Git**. The installer automatically handles everything else:

- **uv** (fast Python package manager)
- **Python 3.11** (via uv, no sudo needed)
- **Node.js v22** (for browser automation and WhatsApp bridge)
- **ripgrep** (fast file search)
- **ffmpeg** (audio format conversion for TTS)

:::info
You do **not** need to install Python, Node.js, ripgrep, or ffmpeg manually. The installer detects what's missing and installs it for you. Just make sure `git` is available (`git --version`).
:::

:::tip Nix users
If you use Nix (on NixOS, macOS, or Linux), there's a dedicated setup path with a Nix flake, declarative NixOS module, and optional container mode. See the **[Nix & NixOS Setup](./nix-setup.md)** guide.
:::

---

## Manual Installation

If you prefer full control over the installation process, follow these steps.

### Step 1: Clone the Repository

Clone with `--recurse-submodules` to pull the required submodules:

```bash
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
```

If you already cloned without `--recurse-submodules`:
```bash
git submodule update --init --recursive
```

### Step 2: Install uv

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

:::tip
Hermes is a normal `pyproject.toml`-based uv project with a checked-in `uv.lock`, so you do **not** need to create or activate a virtual environment manually. `uv sync` will create the project environment automatically at `.venv` and download a compatible Python if needed.
:::

### Step 3: Sync Python Dependencies

```bash
# Sync from the checked-in lockfile and install all Hermes extras
uv sync --locked --extra all
```

If you only want the core agent (no Telegram/Discord/cron support):
```bash
uv sync --locked
```

If you specifically want to force Python 3.11 instead of letting uv pick any compatible version, add `--python 3.11`:

```bash
uv sync --locked --extra all --python 3.11
```

`--extra all` uses Hermes's curated `[all]` extra. Do not replace it with `--all-extras` in Hermes install docs: `--all-extras` means every declared optional dependency, which is broader than the supported default install.

<details>
<summary><strong>Optional extras breakdown</strong></summary>

| Extra | What it adds | Install command |
|-------|-------------|-----------------|
| `all` | Everything below | `uv sync --locked --extra all` |
| `messaging` | Telegram & Discord gateway | `uv sync --locked --extra messaging` |
| `cron` | Cron expression parsing for scheduled tasks | `uv sync --locked --extra cron` |
| `cli` | Terminal menu UI for setup wizard | `uv sync --locked --extra cli` |
| `modal` | Modal cloud execution backend | `uv sync --locked --extra modal` |
| `tts-premium` | ElevenLabs premium voices | `uv sync --locked --extra tts-premium` |
| `voice` | CLI microphone input + audio playback | `uv sync --locked --extra voice` |
| `pty` | PTY terminal support | `uv sync --locked --extra pty` |
| `termux` | Tested Android / Termux bundle (`cron`, `cli`, `pty`, `mcp`, `honcho`, `acp`) | `python -m pip install -e ".[termux]" -c constraints-termux.txt` |
| `honcho` | AI-native memory (Honcho integration) | `uv sync --locked --extra honcho` |
| `mcp` | Model Context Protocol support | `uv sync --locked --extra mcp` |
| `homeassistant` | Home Assistant integration | `uv sync --locked --extra homeassistant` |
| `acp` | ACP editor integration support | `uv sync --locked --extra acp` |
| `slack` | Slack messaging | `uv sync --locked --extra slack` |
| `dev` | pytest & test utilities | `uv sync --locked --extra dev` |

You can combine extras: `uv sync --locked --extra messaging --extra cron`

:::tip Termux users
`.[all]` is not currently available on Android because the `voice` extra pulls `faster-whisper`, which depends on `ctranslate2` wheels that are not published for Android. Use `.[termux]` for the tested mobile install path, then add individual extras only as needed.
:::

</details>

### Step 4: Install Optional Submodules (if needed)

```bash
# RL training backend (optional)
uv pip install -e "./tinker-atropos"
```

Both are optional — if you skip them, the corresponding toolsets simply won't be available.

`uv pip` is still reasonable here because `tinker-atropos` is a separate local package, not part of Hermes's locked dependency set.

### Step 5: Install Node.js Dependencies (Optional)

Only needed for **browser automation** (Browserbase-powered) and **WhatsApp bridge**:

```bash
npm install
```

### Step 6: Create the Configuration Directory

```bash
# Create the directory structure
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session}

# Copy the example config file
cp cli-config.yaml.example ~/.hermes/config.yaml

# Create an empty .env file for API keys
touch ~/.hermes/.env
```

### Step 7: Add Your API Keys

Open `~/.hermes/.env` and add at minimum an LLM provider key:

```bash
# Required — at least one LLM provider:
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Optional — enable additional tools:
FIRECRAWL_API_KEY=fc-your-key          # Web search & scraping (or self-host, see docs)
FAL_KEY=your-fal-key                   # Image generation (FLUX)
```

Or set them via the CLI:
```bash
hermes config set OPENROUTER_API_KEY sk-or-v1-your-key-here
```

### Step 8: Add `hermes` to Your PATH

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/.venv/bin/hermes" ~/.local/bin/hermes
```

If `~/.local/bin` isn't on your PATH, add it to your shell config:

```bash
# Bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc

# Zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# Fish
fish_add_path $HOME/.local/bin
```

### Step 9: Configure Your Provider

```bash
hermes model       # Select your LLM provider and model
```

### Step 10: Verify the Installation

```bash
hermes version    # Check that the command is available
hermes doctor     # Run diagnostics to verify everything is working
hermes status     # Check your configuration
hermes chat -q "Hello! What tools do you have available?"
```

---

## Quick-Reference: Manual Install (Condensed)

For those who just want the commands:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone & enter
git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
cd hermes-agent

# Sync everything
uv sync --locked --extra all
uv pip install -e "./tinker-atropos"
npm install  # optional, for browser tools and WhatsApp

# Configure
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session}
cp cli-config.yaml.example ~/.hermes/config.yaml
touch ~/.hermes/.env
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key' >> ~/.hermes/.env

# Make hermes available globally
mkdir -p ~/.local/bin
ln -sf "$(pwd)/.venv/bin/hermes" ~/.local/bin/hermes

# Verify
hermes doctor
hermes
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `hermes: command not found` | Reload your shell (`source ~/.bashrc`) or check PATH |
| `API key not set` | Run `hermes model` to configure your provider, or `hermes config set OPENROUTER_API_KEY your_key` |
| Missing config after update | Run `hermes config check` then `hermes config migrate` |

For more diagnostics, run `hermes doctor` — it will tell you exactly what's missing and how to fix it.
