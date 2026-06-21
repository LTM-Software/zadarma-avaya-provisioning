#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"

cd "$PROJECT_ROOT"
docker compose up -d avaya-shim avaya-syslog
docker compose ps

