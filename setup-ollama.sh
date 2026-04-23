#!/usr/bin/env bash
# Install Ollama and pull the local Gemma uncensored model.
# Run this on your own machine when Google AI Studio quota is exceeded.
#
# Usage: ./setup-ollama.sh

set -e

MODEL="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"

echo "=== Ollama Setup for Hermes Fallback ==="
echo ""

# Install Ollama if not present
if ! command -v ollama &>/dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Ollama already installed: $(ollama --version)"
fi

echo ""
echo "Starting Ollama service..."
# Start Ollama in background if not already running
if ! pgrep -x ollama > /dev/null 2>&1; then
    ollama serve &
    OLLAMA_PID=$!
    sleep 3
    echo "Ollama started (PID $OLLAMA_PID)"
else
    echo "Ollama is already running"
fi

echo ""
echo "Pulling model: $MODEL"
echo "(This may take a while — model is ~2.5GB)"
ollama pull "$MODEL"

echo ""
echo "=== Ollama setup complete ==="
echo ""
echo "Model available: $MODEL"
echo ""
echo "To switch Hermes to use this model:"
echo "  ./switch-to-ollama.sh"
echo ""
echo "To restore Google AI Studio:"
echo "  ./switch-to-google.sh"
