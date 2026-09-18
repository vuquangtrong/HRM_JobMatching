#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "=== Setting up HRM_Backend Python Virtual Environment ==="

if command -v uv &> /dev/null; then
    echo "Found 'uv'. Creating virtual environment with uv..."
    uv venv .venv
    # shellcheck source=/dev/null
    source .venv/bin/activate
    echo "Installing dependencies with uv..."
    uv pip install -r requirements.txt
else
    echo "'uv' not found in PATH, using python3 -m venv..."
    python3 -m venv .venv
    # shellcheck source=/dev/null
    source .venv/bin/activate
    echo "Upgrading pip..."
    pip install --upgrade pip setuptools wheel 2>/dev/null || true
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

_confirm() {
    local msg="$1"
    local default="${2:-n}"
    if [ ! -t 0 ]; then
        return 1
    fi
    local reply=""
    read -r -p "${msg} " reply || true
    case "${reply:-$default}" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

echo ""
echo "=== Setting up Local LLM (Ollama) for extraction ==="

HRM_SETUP_LLM="${HRM_SETUP_LLM:-1}"
HRM_LLM_MODEL="${HRM_LLM_MODEL:-qwen2.5:3b}"
OLLAMA_SERVE_URL="http://localhost:11434"

if [ "${HRM_SETUP_LLM}" != "1" ]; then
    echo "Skipping LLM setup (HRM_SETUP_LLM != 1). The backend will use deterministic regex taxonomy extraction as baseline."
else
    if ! command -v ollama &> /dev/null; then
        echo "Ollama is NOT installed."
        if _confirm "Install Ollama now via the official install script? [y/N]"; then
            curl -fsSL https://ollama.com/install.sh | sh
            export PATH="$PATH:/usr/local/bin"
        else
            echo "Skipping installation. To run LLM-driven extraction later:"
            echo "  curl -fsSL https://ollama.com/install.sh | sh"
        fi
    fi

    if command -v ollama &> /dev/null; then
        if ! curl -fsS "${OLLAMA_SERVE_URL}/api/tags" > /dev/null 2>&1; then
            echo "Starting and waiting for 'ollama serve'..."
            mkdir -p "./.llm"
            nohup ollama serve > "./.llm/ollama.log" 2>&1 &
            for _ in $(seq 1 30); do
                if curl -fsS "${OLLAMA_SERVE_URL}/api/tags" > /dev/null 2>&1; then
                    break
                fi
                sleep 1
            done
        fi

        if curl -fsS "${OLLAMA_SERVE_URL}/api/tags" > /dev/null 2>&1; then
            if ! ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq "${HRM_LLM_MODEL}"; then
                echo "Model '${HRM_LLM_MODEL}' is not pulled yet (~2 GB)."
                if _confirm "Pull it now? [y/N]"; then
                    ollama pull "${HRM_LLM_MODEL}"
                else
                    echo "You can pull it later with: ollama pull ${HRM_LLM_MODEL}"
                fi
            else
                echo "Model '${HRM_LLM_MODEL}' already available."
            fi
        else
            echo "WARNING: Ollama server did not become ready. See ./.llm/ollama.log"
        fi
    fi
fi

echo ""
echo "Setup completed successfully!"
echo "You can now run: ./run.sh"
echo ""
echo "Set LLM_ENABLED=0 to run the backend without the local LLM."
echo "Set HRM_LLM_MODEL=<tag> or HRM_SETUP_LLM=0 to control LLM setup."