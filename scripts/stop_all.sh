#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"

launchctl remove com.codex.avaya-sip-shim 2>/dev/null || true
cd "$PROJECT_ROOT"
docker compose down

