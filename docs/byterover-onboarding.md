# ByteRover — Long-Term Memory for Hermes

ByteRover gives Hermes persistent, cross-session memory. It stores project
knowledge (patterns, decisions, conventions) as human-readable Markdown files
and retrieves them automatically when relevant.

This guide covers installation, first-run onboarding, daily usage, and
configuration.

---

## Table of Contents

1. [Installation](#installation)
2. [Onboarding (first-run setup)](#onboarding)
3. [Usage](#usage)
4. [Recall Modes](#recall-modes)
5. [Cloud Sync](#cloud-sync)
6. [Configuration Reference](#configuration-reference)
7. [Troubleshooting](#troubleshooting)

---

## Installation

### 1. Install the `brv` CLI

```bash
npm install -g byterover-cli
```

Verify it's available:

```bash
brv --version
```

### 2. Run the Hermes setup script

```bash
# From the byterover-cli repo
sh scripts/hermes-setup.sh
```

This script does the following:

| Step | What it does |
|------|-------------|
| Check `brv` | Verifies `brv` is on PATH or at `~/.brv-cli/bin/brv` |
| Install skill | Writes `SKILL.md` to `~/.hermes/skills/byterover/` |
| Register connector | Runs `brv connectors install Hermes --type skill` |
| Setup cron job | Adds daily knowledge mining (9 AM) to `~/.hermes/cron/jobs.json` |
| Onboarding marker | Decides whether to trigger the first-run walkthrough (see below) |
| Restart gateway | Runs `hermes gateway restart` to pick up the new skill |

#### Options

```bash
sh scripts/hermes-setup.sh --skip-onboarding
```

Use `--skip-onboarding` to mark onboarding as complete immediately. This is
useful for CI, Docker images, or automated deployments where you don't want
the interactive walkthrough. The flag is also applied automatically in
non-interactive shells (no TTY) and CI environments (`$CI` is set).

---

## Onboarding

When you start your first conversation after installation, Hermes walks you
through a setup flow. This works identically in CLI mode (`hermes chat`)
and chat platforms (WhatsApp, Telegram, Discord).

The number of steps depends on which provider you choose:

- **ByteRover (free):** 2 steps — provider, then storage
- **OpenRouter / Anthropic:** 3 steps — provider, then **model**, then storage

### Step 1: Choose a provider

Hermes asks you to pick an AI provider that powers ByteRover's query and
curate operations:

```
ByteRover Setup (Step 1/2)

1. ByteRover (free, no key needed)
2. OpenRouter (paste API key after number)
3. Anthropic (paste API key after number)
4. Skip for now
```

**How to reply:**

| Reply with | What happens |
|------------|-------------|
| `1` or `byterover` | Connects the free ByteRover provider — skips model selection |
| `2 sk-or-v1-your-key` | Connects OpenRouter with your API key — proceeds to model selection |
| `3 sk-ant-your-key` | Connects Anthropic with your API key — proceeds to model selection |
| `4` or `skip` | Skips setup, uses default provider. You can configure later. |

> If you paste just an API key without a number, Hermes auto-detects the
> provider from the key prefix (`sk-or-` = OpenRouter, `sk-ant-` = Anthropic).

### Step 1b: Choose a model (non-ByteRover providers only)

If you chose OpenRouter or Anthropic, Hermes fetches the available models
from your provider and asks you to pick one:

```
Provider connected! Choose a model:

Available models for OpenRouter:
  1. anthropic/claude-sonnet-4-5
  2. google/gemini-2.5-pro
  3. ...

Reply with a number or model name.
```

**How to reply:**

| Reply with | What happens |
|------------|-------------|
| A number (e.g. `1`) | Selects the corresponding model from the list |
| A model name (e.g. `claude-sonnet-4-5`) | Selects that model directly |

> This step is skipped when you choose ByteRover as the provider, because
> ByteRover uses its own default model automatically.

### Step 2: Choose storage

After the provider is connected, Hermes asks where to store your memory:

```
ByteRover Setup (Step 2/2)

🔒 Local only (default) — private, works offline, fully functional
☁️ ByteRover Cloud (free tier) — sync across devices, share with your
   team, browse/edit in dashboard, automatic backup
   → Get your key at app.byterover.dev/settings/keys

Reply "local" or paste your ByteRover cloud key.
```

**How to reply:**

| Reply with | What happens |
|------------|-------------|
| `local` or `1` | Stores memory locally at `~/.hermes/byterover/.brv/context-tree/` |
| Your cloud API key | Connects to ByteRover Cloud (login, select space, sync) |
| `cloud` or `2` | Hermes asks you to paste your cloud key |

If you choose cloud, Hermes automatically runs the full connection sequence:
login, fetch spaces, select the first available space, and pull existing data.
If anything fails, it gracefully falls back to local storage.

### After onboarding

Once setup is complete, Hermes remembers your choices. The onboarding
walkthrough won't appear again. To re-run it:

```bash
rm ~/.hermes/.byterover-onboarded
rm -f ~/.hermes/byterover/.setup-step
```

---

## Usage

### Saving knowledge ("remember this")

Tell Hermes to remember something and it will save it to ByteRover:

```
You: remember that we use JWT with 24h expiry, tokens stored in httpOnly cookies
Hermes: ✓ Saved to memory.
```

Hermes calls `brv curate` behind the scenes. You can also reference files:

```
You: remember the auth pattern in src/middleware/auth.ts
```

### Querying memory ("what do we...")

Ask Hermes about past decisions or patterns:

```
You: what's our authentication strategy?
Hermes: Based on your project memory, you use JWT with 24h expiry...
```

In `hybrid` and `context` recall modes (default), Hermes also automatically
queries ByteRover before responding — you don't even need to ask.

### Manual commands

You can ask Hermes to run any ByteRover operation. Some examples:

| What you want | What to say |
|---------------|-------------|
| Check status | "check ByteRover status" |
| Push to cloud | "push memory to cloud" |
| Pull from cloud | "pull latest from cloud" |
| Switch provider | "connect OpenRouter with key sk-or-..." |
| Switch cloud space | "switch to space X on team Y" |
| List models | "list available ByteRover models" |
| Switch model | "switch ByteRover model to claude-sonnet-4-5" |

### Automatic features

ByteRover works in the background to keep your memory up to date:

| Feature | What it does | When it runs |
|---------|-------------|-------------|
| **Auto-enrich** | Queries memory for context relevant to your message | Before each response (hybrid/context mode) |
| **Auto-curate** | Extracts insights from conversations worth remembering | After each turn (background, non-blocking) |
| **Auto-flush** | Saves important patterns before context compression | When conversation gets too long and is compressed |
| **Knowledge miner** | Reviews yesterday's sessions for patterns to save | Daily at 9 AM (cron job) |

### Where is my data stored?

All knowledge is stored as human-readable Markdown files:

```
~/.hermes/byterover/.brv/context-tree/
├── architecture/
│   └── auth-strategy.md
├── patterns/
│   └── error-handling.md
└── decisions/
    └── database-choice.md
```

You can read, edit, or version-control these files directly.

---

## Recall Modes

Control how ByteRover integrates with your conversations via
`~/.hermes/config.yaml`:

```yaml
byterover:
  recall_mode: hybrid   # hybrid | context | tools | off
```

| Mode | Auto-enrich | Manual tools | Best for |
|------|:-----------:|:------------:|----------|
| `hybrid` (default) | Yes | Yes | Full experience — memory is always available |
| `context` | Yes | Yes | Same as hybrid (auto-inject + manual access) |
| `tools` | No | Yes | Manual control only — you decide when to query/curate |
| `off` | No | No | Disable ByteRover entirely |

---

## Cloud Sync

ByteRover Cloud lets you sync memory across devices and share with your team.

### Initial setup (if you skipped during onboarding)

```
You: connect ByteRover cloud
Hermes: Paste your ByteRover cloud API key. Get one at app.byterover.dev/settings/keys

You: sk-brv-your-key-here
Hermes: ✓ Connected to cloud space 'my-project'. Data synced!
```

### Day-to-day sync

```
You: push memory to cloud     # Upload local changes
You: pull latest from cloud   # Download team changes
```

### Cloud connection flow

Behind the scenes, cloud setup runs these steps:

```
brv login --api-key <key>
brv space list
brv space switch --team <team> --name <space>
brv pull
```

---

## Configuration Reference

### Files

| File | Purpose |
|------|---------|
| `~/.hermes/.byterover-onboarded` | Onboarding completion marker |
| `~/.hermes/byterover/.setup-step` | Current onboarding step (`provider` or `storage`) |
| `~/.hermes/byterover/.brv/context-tree/` | Knowledge base (Markdown files) |
| `~/.hermes/byterover/logs/brv.log` | Operation log |
| `~/.hermes/skills/byterover/SKILL.md` | Skill definition |
| `~/.hermes/cron/jobs.json` | Scheduled jobs (including knowledge miner) |
| `~/.hermes/config.yaml` | Recall mode setting (`byterover.recall_mode`) |

### Provider management

```
You: switch to Anthropic provider with key sk-ant-...
You: switch to OpenRouter with key sk-or-...
You: connect ByteRover provider      # free, no key
```

### Model management

```
You: list ByteRover models
You: switch ByteRover model to <model-name>
```

---

## Troubleshooting

### "brv CLI not found"

Install it:

```bash
npm install -g byterover-cli
```

Or check if it's at `~/.brv-cli/bin/brv` and add it to your PATH.

### ByteRover doesn't activate

1. Check that `brv` is installed: `brv --version`
2. Check that the setup script was run: `ls ~/.hermes/skills/byterover/SKILL.md`
3. Check recall mode isn't off: `grep recall_mode ~/.hermes/config.yaml`
4. Restart the gateway: `hermes gateway restart`

### Re-run onboarding

```bash
rm ~/.hermes/.byterover-onboarded
rm -f ~/.hermes/byterover/.setup-step
```

Start a new conversation — the setup walkthrough will appear again.

### Check status

Ask Hermes:

```
You: check ByteRover status
```

Or run directly:

```bash
brv status
```

This shows authentication state, current provider, connected space, and sync
status.

### Common errors

| Error | Fix |
|-------|-----|
| "Not authenticated" | Run `brv login --api-key <key>` or ask Hermes to connect cloud |
| "No provider connected" | Ask Hermes to connect a provider (e.g., "connect ByteRover provider") |
| "Token expired / invalid" | Re-authenticate: ask Hermes to login again |
| "Connection failed" | Kill any stuck brv processes and retry |
