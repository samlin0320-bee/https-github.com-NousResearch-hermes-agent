#!/usr/bin/env bash
# Switch Hermes back to Google AI Studio (primary provider).
# Usage: ./switch-to-google.sh

set -e

HERMES_CONFIG="$HOME/.hermes/config.yaml"
GOOGLE_MODEL="gemini-2.0-flash"
GOOGLE_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"

# Read the API key from .env in this directory (if present)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
GOOGLE_API_KEY=""
if [ -f "$ENV_FILE" ]; then
    GOOGLE_API_KEY=$(grep -E '^GOOGLE_AI_STUDIO_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)
fi

if [ -z "$GOOGLE_API_KEY" ]; then
    echo "ERROR: GOOGLE_AI_STUDIO_API_KEY not found in .env"
    echo "Set it first: echo 'GOOGLE_AI_STUDIO_API_KEY=your-key' >> .env"
    exit 1
fi

echo "Switching Hermes provider → Google AI Studio (Gemini)"

python3 - <<PYEOF
import yaml, pathlib

cfg_path = pathlib.Path("$HERMES_CONFIG")
with open(cfg_path) as f:
    cfg = yaml.safe_load(f) or {}

cfg.setdefault("model", {})
cfg["model"]["provider"] = "custom"
cfg["model"]["base_url"]  = "$GOOGLE_BASE_URL"
cfg["model"]["api_key"]   = "$GOOGLE_API_KEY"
cfg["model"]["default"]   = "$GOOGLE_MODEL"

with open(cfg_path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

print(f"Updated {cfg_path}")
PYEOF

echo ""
echo "Done. Hermes will now use Google AI Studio with model:"
echo "  $GOOGLE_MODEL"
echo ""
echo "To switch to Ollama (quota fallback): ./switch-to-ollama.sh"
echo "To start Hermes gateway:              hermes gateway"
