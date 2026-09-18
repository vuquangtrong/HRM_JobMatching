#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

PY="${PYTHON:-python3}"
if command -v "$PY" >/dev/null 2>&1; then
    CAN_READ_CONFIG=1
else
    CAN_READ_CONFIG=0
fi

get_cfg() {
    if [ "${CAN_READ_CONFIG}" = "1" ]; then
        "$PY" "$DIR/get_config.py" "$1" "$2"
    else
        printf '%s' "$2"
    fi
}

# Defaults come from HRM_Backend/config.json (config.py / get_config.py);
# environment variables still take precedence.
PORT="${PORT:-$(get_cfg server.port 8765)}"
LLM_ENABLED="${LLM_ENABLED:-$(get_cfg llm.enabled 1)}"
LLM_BASE_URL="${LLM_BASE_URL:-$(get_cfg llm.base_url http://localhost:11434)}"
LLM_MODEL="${LLM_MODEL:-$(get_cfg llm.model qwen2.5:3b)}"

if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Running setup_backend.sh first..."
    ./setup_backend.sh
fi

# shellcheck source=/dev/null
source .venv/bin/activate

llm_running() {
    if command -v curl &> /dev/null; then
        curl -fsS "${LLM_BASE_URL}/api/tags" > /dev/null 2>&1
    elif command -v ollama &> /dev/null; then
        ollama list > /dev/null 2>&1
    else
        return 1
    fi
}

if [ "${LLM_ENABLED}" = "1" ]; then
    if llm_running; then
        echo "Local LLM server already running at ${LLM_BASE_URL}."
    else
        if command -v ollama &> /dev/null; then
            echo "Local LLM server not running. Starting 'ollama serve'..."
            mkdir -p "./.llm"
            nohup ollama serve > "./.llm/ollama.log" 2>&1 &
            for _ in $(seq 1 30); do
                if llm_running; then
                    break
                fi
                sleep 1
            done
        fi
        if llm_running; then
            echo "Local LLM server ready at ${LLM_BASE_URL}."
        else
            echo "NOTICE: Local LLM server is not available. Using deterministic regex taxonomy extraction as baseline."
        fi
    fi

    if llm_running && command -v ollama &> /dev/null; then
        if ! ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq "${LLM_MODEL}"; then
            echo "WARNING: model '${LLM_MODEL}' is not pulled. Run: ollama pull ${LLM_MODEL}"
        fi
    fi
else
    echo "LLM extraction disabled (LLM_ENABLED=0). Running with deterministic regex taxonomy extraction."
fi

echo "Starting HRM_Backend on port ${PORT}..."
exec python3 app.py