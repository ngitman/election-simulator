#!/usr/bin/env bash
# Run API from repo root so `backend` package and data_loader imports resolve.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec uvicorn backend.main:app --reload "$@"
