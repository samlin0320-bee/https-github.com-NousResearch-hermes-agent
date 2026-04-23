#!/usr/bin/env bash
# Switch Hermes to use the local Ollama Gemma model (fallback when Google quota is exceeded).
# Usage: ./switch-to-ollama.sh
# Restore:  ./switch-to-google.sh

set -e

HERMES_CONFIG="$HOME/.hermes/config.yaml"
MODEL="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
OLLAMA_URL="http://localhost:11434/v1"

echo "Switching Hermes provider → Ollama (local Gemma)"

# Verify Ollama is running
if ! curl -sf "$OLLAMA_URL/models" > /dev/null 2>&1; then
    echo "ERROR: Ollama is not running at $OLLAMA_URL"
    echo "  Start Ollama:  ollama serve"
    echo "  Pull model:    ollama pull $MODEL"
    exit 1
fi

# Verify the model exists in Ollama
if ! ollama list 2>/dev/null | grep -q "TrevorJS/gemma-4-E4B-it-uncensored"; then
    echo "Model not found locally. Pulling now (this may take a while)..."
    ollama pull "$MODEL"
fi

# Patch config.yaml: switch provider and model
python3 - <<PYEOF
import yaml, pathlib, re

cfg_path = pathlib.Path("$HERMES_CONFIG")
with open(cfg_path) as f:
    text = f.read()

cfg = yaml.safe_load(text)
cfg.setdefault("model", {})
cfg["model"]["provider"] = "custom"
cfg["model"]["base_url"]  = "$OLLAMA_URL/"
cfg["model"]["api_key"]   = "no-key-required"
cfg["model"]["default"]   = "$MODEL"

with open(cfg_path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f"Updated {cfg_path}")
PYEOF

echo ""
echo "Done. Hermes will now use Ollama with model:"
echo "  $MODEL"
echo ""
echo "To restore Google AI Studio:  ./switch-to-google.sh"
echo "To start Hermes gateway:      hermes gateway"
