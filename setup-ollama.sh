#!/usr/bin/env bash
# Install Ollama and pull the local uncensored Gemma model.
# Run this on YOUR OWN MACHINE (not inside a sandbox).
#
# Usage: ./setup-ollama.sh

set -e

MODEL="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"

echo "=== Ollama Setup — 本地越獄 Gemma 備援 ==="
echo ""

# ── 1. Install Ollama ──────────────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    echo "✓ Ollama already installed: $(ollama --version 2>/dev/null || true)"
else
    echo "→ Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "✓ Ollama installed"
fi

# ── 2. Start Ollama service ────────────────────────────────────────────────────
if ! pgrep -x ollama > /dev/null 2>&1; then
    echo "→ Starting Ollama service..."
    # Try systemd first, then manual background start
    if systemctl is-enabled ollama &>/dev/null 2>&1; then
        systemctl start ollama
    else
        OLLAMA_HOST=0.0.0.0 ollama serve &>/tmp/ollama.log &
        sleep 3
    fi
fi

# Wait until the API is up
for i in {1..10}; do
    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
        echo "✓ Ollama service is running"
        break
    fi
    sleep 2
done

# ── 3. Pull the uncensored Gemma model ────────────────────────────────────────
echo ""
echo "→ Pulling model: $MODEL"
echo "  (This may take a while — model is ~2.5 GB)"
echo ""
ollama pull "$MODEL"

echo ""
echo "✓ Model pulled successfully"
ollama list | grep -i "gemma\|TrevorJS" || true

# ── 4. Quick smoke test ───────────────────────────────────────────────────────
echo ""
echo "→ Running quick smoke test..."
REPLY=$(ollama run "$MODEL" "Say 'OK' in one word." 2>/dev/null | head -1)
echo "  Model reply: $REPLY"

# ── 5. Switch Hermes to use this model ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/switch-to-ollama.sh" ]; then
    echo ""
    read -p "Switch Hermes to Ollama now? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        bash "$SCRIPT_DIR/switch-to-ollama.sh"
    fi
fi

echo ""
echo "=== 完成 ==="
echo ""
echo "指令速查："
echo "  ./switch-to-ollama.sh    ← 切換為 Ollama（Google 額度用完時）"
echo "  ./switch-to-google.sh    ← 切回 Google AI Studio"
echo "  ./start-hermes-gateway.sh ← 啟動 Hermes（自動偵測並切換）"
