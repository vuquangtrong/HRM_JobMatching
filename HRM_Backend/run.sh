#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

PORT="${PORT:-8765}"

if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Running setup_backend.sh first..."
    ./setup_backend.sh
fi

# shellcheck source=/dev/null
source .venv/bin/activate

echo "Starting HRM_Backend on port ${PORT}..."
exec python3 app.py
