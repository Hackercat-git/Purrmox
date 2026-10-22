#!/usr/bin/env bash
# Install the Purrmox agent on macOS for the current user, without sudo (launchd LaunchAgent).
# It starts when this user is logged in and, unlike a root daemon, keeps local-network access (macOS privacy rules).
#
# Environment variables:
#   PORT      port the agent listens on (default 9100)
#   SOURCE    pip requirement for the agent (default: the GitHub main branch archive)
# An existing token in ~/purrmox-agent/token is kept, so you can place your own secret there first.
# Use --uninstall to remove the LaunchAgent. Data in ~/purrmox-agent is kept.
set -euo pipefail

PORT="${PORT:-9100}"
SOURCE="${SOURCE:-purrmox[agent] @ https://github.com/Hackercat-git/Purrmox/archive/refs/heads/main.zip}"
DIR="$HOME/purrmox-agent"
LABEL="nl.purrmox.agent"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS; use install-unix.sh on Linux." >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST"
  echo "LaunchAgent removed. Delete $DIR yourself when you no longer need it."
  exit 0
fi

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "Python 3.9 or newer is required (xcode-select --install or brew install python)." >&2
  exit 1
fi

mkdir -p "$DIR" "$HOME/Library/LaunchAgents"
[ -x "$DIR/venv/bin/python" ] || "$PYTHON" -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$SOURCE"

NEW_TOKEN=""
if [ ! -s "$DIR/token" ]; then
  NEW_TOKEN="$("$DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  (umask 077 && printf '%s' "$NEW_TOKEN" > "$DIR/token")
fi
chmod 600 "$DIR/token"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${DIR}/venv/bin/python</string>
    <string>-m</string><string>purrmox.agent</string>
    <string>--port</string><string>${PORT}</string>
    <string>--token-file</string><string>${DIR}/token</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>${DIR}/agent.log</string>
</dict>
</plist>
EOF

launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo
echo "Purrmox agent installed for $(id -un) on port ${PORT}."
if [ -n "$NEW_TOKEN" ]; then
  echo "Shared secret (shown once, put it in the 'hosts' section of the Purrmox config.yaml):"
  echo "  $NEW_TOKEN"
else
  echo "Existing shared secret kept in $DIR/token."
fi
echo "Check: curl http://localhost:${PORT}/healthz"
