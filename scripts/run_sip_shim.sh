#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"

mkdir -p "$PROJECT_ROOT/logs"

export ENABLE_HTTP="${ENABLE_HTTP:-0}"
export ENABLE_SIP="${ENABLE_SIP:-1}"
export HTTP_ROOT="${HTTP_ROOT:-$PROJECT_ROOT/http}"
export SIP_REMOTE_HOST="${SIP_REMOTE_HOST:-185.45.152.164}"
export SIP_REMOTE_PORT="${SIP_REMOTE_PORT:-5060}"
export AVAYA_EXTENSION="${AVAYA_EXTENSION:-373316-100}"
export AVAYA_DOMAIN="${AVAYA_DOMAIN:-pbx.zadarma.com}"
export SIP_ADVERTISE_HOST="${SIP_ADVERTISE_HOST:-192.168.80.10}"
export SIP_ADVERTISE_PORT="${SIP_ADVERTISE_PORT:-5060}"

exec /opt/homebrew/bin/python3 "$PROJECT_ROOT/avaya-shim/avaya_shim.py" \
  >> "$PROJECT_ROOT/logs/avaya-sip-shim.log" \
  2>> "$PROJECT_ROOT/logs/avaya-sip-shim.err"

