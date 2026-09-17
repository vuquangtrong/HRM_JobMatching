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

echo "Setup completed successfully!"
echo "You can now run: ./run.sh"
