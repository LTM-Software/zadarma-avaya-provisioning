#!/bin/zsh
set -euo pipefail

LABEL="com.codex.avaya-sip-shim"
SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
USER_ID="$(id -u)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST="$LAUNCH_AGENTS/$LABEL.plist"

mkdir -p "$LAUNCH_AGENTS"

launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || true
launchctl remove "$LABEL" 2>/dev/null || true

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$SCRIPT_DIR/run_sip_shim.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$PROJECT_ROOT/logs/launchd-sip-shim.out</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT_ROOT/logs/launchd-sip-shim.err</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST"
launchctl bootstrap "gui/$USER_ID" "$PLIST"
launchctl kickstart -k "gui/$USER_ID/$LABEL"
launchctl print "gui/$USER_ID/$LABEL" | grep -E "state|pid|last exit code" || true
