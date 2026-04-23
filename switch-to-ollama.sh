#!/usr/bin/env bash
# Switch Hermes to use a local Ollama Gemma 4 uncensored model.
# Model is read from OLLAMA_MODEL env var → .env → defaults to E4B (8B).
#
# Usage:
#   ./switch-to-ollama.sh              # use OLLAMA_MODEL from env/.env
#   ./switch-to-ollama.sh e2b          # force E2B (5B)
#   ./switch-to-ollama.sh e4b          # force E4B (8B)
#   ./switch-to-ollama.sh 26b          # force 26B (A4B)
#   ./switch-to-ollama.sh 31b          # force 31B
# Restore:
#   ./switch-to-google.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_CONFIG="$HOME/.hermes/config.yaml"
OLLAMA_URL="http://localhost:11434/v1"

# ── Model registry ─────────────────────────────────────────────────────────────
declare -A MODEL_IDS=(
    [e2b]="hf.co/TrevorJS/gemma-4-E2B-it-uncensored-GGUF:Q4_K_M"
    [e4b]="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
    [26b]="hf.co/TrevorJS/gemma-4-26B-A4B-it-uncensored-GGUF:Q4_K_M"
    [31b]="hf.co/TrevorJS/gemma-4-31B-it-uncensored-GGUF:Q4_K_M"
)

# ── Resolve model ──────────────────────────────────────────────────────────────
# Priority: CLI arg → env OLLAMA_MODEL → .env file → default E4B
if [ -n "${1:-}" ]; then
    KEY="${1,,}"
    if [ -n "${MODEL_IDS[$KEY]:-}" ]; then
        MODEL="${MODEL_IDS[$KEY]}"
    else
        # Assume raw model ID was passed
        MODEL="$1"
    fi
elif [ -n "${OLLAMA_MODEL:-}" ]; then
    MODEL="$OLLAMA_MODEL"
elif [ -f "$SCRIPT_DIR/.env" ]; then
    ENV_MODEL=$(grep "^OLLAMA_MODEL=" "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
    MODEL="${ENV_MODEL:-hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M}"
else
    MODEL="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
fi

echo "Switching Hermes → Ollama (local Gemma 4 越獄版)"
echo "  模型：$MODEL"

# ── Verify Ollama is running ───────────────────────────────────────────────────
if ! curl -sf "$OLLAMA_URL/models" > /dev/null 2>&1; then
    echo ""
    echo "ERROR: Ollama 未在 $OLLAMA_URL 運行"
    echo "  啟動：OLLAMA_HOST=0.0.0.0 ollama serve &"
    echo "  等待後再試"
    exit 1
fi

# ── Pull model if not present ─────────────────────────────────────────────────
MODEL_SHORT=$(echo "$MODEL" | grep -oP 'gemma-4-\K[^/:-]+' | head -1 || echo "$MODEL")
if ! ollama list 2>/dev/null | grep -qi "$MODEL_SHORT"; then
    echo "模型不在本地，開始下載..."
    ollama pull "$MODEL"
fi

# ── Patch config.yaml ─────────────────────────────────────────────────────────
python3 - <<PYEOF
import yaml, pathlib

cfg_path = pathlib.Path("$HERMES_CONFIG")
if not cfg_path.exists():
    print(f"Warning: config not found at {cfg_path}")
    exit(0)

with open(cfg_path) as f:
    text = f.read()

cfg = yaml.safe_load(text) or {}
cfg.setdefault("model", {})
cfg["model"]["provider"] = "custom"
cfg["model"]["base_url"]  = "$OLLAMA_URL/"
cfg["model"]["api_key"]   = "no-key-required"
cfg["model"]["default"]   = "$MODEL"

with open(cfg_path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f"✓ config.yaml 已更新 → Ollama {cfg['model']['default']}")
PYEOF

echo ""
echo "✓ 完成。目前模型："
echo "  $MODEL"
echo ""
echo "切回 Google：  ./switch-to-google.sh"
echo "啟動 Gateway： hermes gateway"
