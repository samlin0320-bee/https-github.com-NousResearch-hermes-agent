#!/usr/bin/env bash
# Start the Hermes gateway (Telegram + API server for HUD UI).
# Automatically falls back to Ollama if Google AI Studio returns quota errors.
#
# Usage:
#   ./start-hermes-gateway.sh           # start normally
#   ./start-hermes-gateway.sh --ollama  # force Ollama from the start

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_HERMES="$SCRIPT_DIR/venv/bin/hermes"
HERMES="${VENV_HERMES:-hermes}"

# Ensure we source .env so env vars are available
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [[ "$1" == "--ollama" ]]; then
    echo "Force-switching to Ollama..."
    bash "$SCRIPT_DIR/switch-to-ollama.sh"
fi

echo "Starting Hermes gateway..."
echo "  Telegram:   enabled (bot token configured)"
echo "  API server: http://0.0.0.0:${API_SERVER_PORT:-8080} (for HUD UI)"
echo ""
echo "Press Ctrl+C to stop."
echo ""

# Run the gateway; restart on quota errors with Ollama fallback
while true; do
    "$HERMES" gateway 2>&1 | tee /tmp/hermes-gateway.log &
    HERMES_PID=$!

    # Monitor for Google quota errors
    while kill -0 $HERMES_PID 2>/dev/null; do
        if grep -q "quota\|RESOURCE_EXHAUSTED\|429\|rate limit" /tmp/hermes-gateway.log 2>/dev/null; then
            echo ""
            echo "Google AI Studio quota exceeded — switching to Ollama fallback..."
            kill $HERMES_PID 2>/dev/null
            bash "$SCRIPT_DIR/switch-to-ollama.sh"
            > /tmp/hermes-gateway.log
            break
        fi
        sleep 10
    done

    wait $HERMES_PID 2>/dev/null
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ]; then
        break
    fi
    echo "Gateway exited with code $EXIT_CODE, restarting in 5s..."
    sleep 5
done
