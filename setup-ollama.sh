#!/usr/bin/env bash
# Install Ollama and pull a local uncensored Gemma 4 model.
# Run this on YOUR OWN MACHINE (not inside a sandbox).
#
# Usage:
#   ./setup-ollama.sh            # interactive model selection
#   ./setup-ollama.sh e2b        # force E2B (5B)
#   ./setup-ollama.sh e4b        # force E4B (8B, default)
#   ./setup-ollama.sh 26b        # force 26B (A4B)
#   ./setup-ollama.sh 31b        # force 31B

set -e

# ── Model registry ─────────────────────────────────────────────────────────────
declare -A MODEL_NAMES=(
    [e2b]="hf.co/TrevorJS/gemma-4-E2B-it-uncensored-GGUF:Q4_K_M"
    [e4b]="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
    [26b]="hf.co/TrevorJS/gemma-4-26B-A4B-it-uncensored-GGUF:Q4_K_M"
    [31b]="hf.co/TrevorJS/gemma-4-31B-it-uncensored-GGUF:Q4_K_M"
)
declare -A MODEL_SIZES=(
    [e2b]="~3 GB (5B 參數，速度最快)"
    [e4b]="~5 GB (8B 參數，速度/品質均衡) ← 推薦"
    [26b]="~15 GB (26B 參數，需 16GB+ VRAM)"
    [31b]="~20 GB (31B 參數，需 24GB+ VRAM)"
)

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║      Ollama Setup — Gemma 4 越獄版本安裝             ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Select model ───────────────────────────────────────────────────────────────
CHOICE="${1:-}"

if [ -z "$CHOICE" ]; then
    echo "請選擇要安裝的模型："
    echo ""
    echo "  1) E2B  ${MODEL_SIZES[e2b]}"
    echo "  2) E4B  ${MODEL_SIZES[e4b]}"
    echo "  3) 26B  ${MODEL_SIZES[26b]}"
    echo "  4) 31B  ${MODEL_SIZES[31b]}"
    echo ""
    read -rp "輸入 1-4（預設 2）: " sel
    sel="${sel:-2}"
    case "$sel" in
        1) CHOICE="e2b";;
        2) CHOICE="e4b";;
        3) CHOICE="26b";;
        4) CHOICE="31b";;
        *) echo "無效選擇，使用 E4B"; CHOICE="e4b";;
    esac
fi

CHOICE="${CHOICE,,}"
MODEL="${MODEL_NAMES[$CHOICE]}"
if [ -z "$MODEL" ]; then
    echo "錯誤：未知的模型代號 '$CHOICE'（可用：e2b, e4b, 26b, 31b）"
    exit 1
fi

echo "✓ 選擇模型：$MODEL"
echo "  大小：${MODEL_SIZES[$CHOICE]}"
echo ""

# ── 1. Install Ollama ──────────────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    echo "✓ Ollama 已安裝：$(ollama --version 2>/dev/null || true)"
else
    echo "→ 安裝 Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "✓ Ollama 安裝完成"
fi

# ── 2. Start Ollama service ────────────────────────────────────────────────────
if ! pgrep -x ollama > /dev/null 2>&1; then
    echo "→ 啟動 Ollama 服務..."
    if systemctl is-enabled ollama &>/dev/null 2>&1; then
        systemctl start ollama
    else
        OLLAMA_HOST=0.0.0.0 ollama serve &>/tmp/ollama.log &
        sleep 3
    fi
fi

for i in {1..10}; do
    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
        echo "✓ Ollama 服務運行中"
        break
    fi
    sleep 2
done

# ── 3. Pull model ─────────────────────────────────────────────────────────────
echo ""
echo "→ 下載模型：$MODEL"
echo "  （${MODEL_SIZES[$CHOICE]}，請耐心等候）"
echo ""
ollama pull "$MODEL"

echo ""
echo "✓ 模型下載完成"
ollama list | grep -i "gemma\|TrevorJS" || true

# ── 4. Smoke test ─────────────────────────────────────────────────────────────
echo ""
echo "→ 快速測試..."
TEST_REPLY=$(ollama run "$MODEL" "Say OK in one word." 2>/dev/null | head -1 || true)
echo "  模型回應：${TEST_REPLY:-（無回應，可能需要更多記憶體）}"

# ── 5. Update .env ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    # Update OLLAMA_MODEL in .env
    if grep -q "^OLLAMA_MODEL=" "$ENV_FILE"; then
        sed -i "s|^OLLAMA_MODEL=.*|OLLAMA_MODEL=$MODEL|" "$ENV_FILE"
    else
        echo "OLLAMA_MODEL=$MODEL" >> "$ENV_FILE"
    fi
    echo "✓ .env 已更新：OLLAMA_MODEL=$MODEL"
fi

# ── 6. Switch Hermes config ───────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/switch-to-ollama.sh" ]; then
    echo ""
    read -rp "立即切換 Hermes 使用此模型？[Y/n] " yn
    yn="${yn:-y}"
    if [[ "$yn" =~ ^[Yy]$ ]]; then
        OLLAMA_MODEL="$MODEL" bash "$SCRIPT_DIR/switch-to-ollama.sh"
    fi
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ 完成                                             ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  模型：$MODEL"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  指令速查：                                          ║"
echo "║    ./switch-to-ollama.sh   ← 切換越獄 Gemma         ║"
echo "║    ./switch-to-google.sh   ← 切回 Google            ║"
echo "║    ./setup-ollama.sh e2b   ← 改裝 E2B (5B)          ║"
echo "║    ./setup-ollama.sh e4b   ← 改裝 E4B (8B)          ║"
echo "║    ./setup-ollama.sh 26b   ← 改裝 26B (A4B)         ║"
echo "║    ./setup-ollama.sh 31b   ← 改裝 31B               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
