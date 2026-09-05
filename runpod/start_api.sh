#!/usr/bin/env bash
set -euo pipefail

export AI_TWIN_ROOT="${AI_TWIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
export AI_TWIN_STORAGE="${AI_TWIN_STORAGE:-/workspace/zaskaleta-storage}"
export AI_TWIN_OUTPUT="${AI_TWIN_OUTPUT:-$AI_TWIN_STORAGE/api_jobs}"
export AI_TWIN_PYTHON="${AI_TWIN_PYTHON:-$(command -v python)}"

mkdir -p "$AI_TWIN_STORAGE" "$AI_TWIN_OUTPUT"

python -m pip install -r "$AI_TWIN_ROOT/runpod/requirements-api.txt"
python "$AI_TWIN_ROOT/runpod/connection_readiness.py" --require-runtime-env
exec uvicorn runpod.api_server:app --host 0.0.0.0 --port "${PORT:-8000}" --app-dir "$AI_TWIN_ROOT"
