#!/usr/bin/env bash
set -euo pipefail

# Render injects PORT at runtime. Fallback helps local smoke tests.
PORT="${PORT:-8000}"

exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}"
