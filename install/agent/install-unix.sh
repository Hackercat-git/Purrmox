#!/usr/bin/env bash
# Install the Purrmox agent on macOS (launchd) or Linux (systemd). Run as root: sudo ./install-unix.sh
#
# Environment variables:
#   PORT      port the agent listens on (default 9100)
#   SOURCE    pip requirement for the agent (default: the GitHub main branch archive)
#   RUN_USER  account the agent runs as (default: the user who called sudo, or "purrmox" on Linux)
# Use --uninstall to remove the service. Data in the install directory is kept.
set -euo pipefail

PORT="${PORT:-9100}"
SOURCE="${SOURCE:-purrmox[agent] @ https://github.com/Hackercat-git/Purrmox/archive/refs/heads/main.zip}"
OS="$(uname -s)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

case "$OS" in
  Darwin)
    DIR="/usr/local/purrmox-agent"
    LABEL="nl.purrmox.agent"
    PLIST="/Library/LaunchDaemons/${LABEL}.plist"
    ;;
  Linux)
    DIR="/opt/purrmox-agent"
    PLIST="/etc/systemd/system/purrmox-agent.service"
    ;;
  *)
    echo "Unsupported operating system: $OS" >&2
    exit 1
    ;;
esac

if [ "${1:-}" = "--uninstall" ]; then
  if [ "$OS" = "Darwin" ]; then
    launchctl bootout "system/${LABEL}" 2>/dev/null || true
  else
    systemctl disable --now purrmox-agent 2>/dev/null || true
    systemctl daemon-reload
  fi
  rm -f "$PLIST"
  echo "Service removed. Delete $DIR yourself when you no longer need it."
  exit 0
fi

RUN_USER="${RUN_USER:-${SUDO_USER:-}}"
if [ "$OS" = "Linux" ] && { [ -z "$RUN_USER" ] || [ "$RUN_USER" = "root" ]; }; then
  RUN_USER="purrmox"
  id "$RUN_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
fi
if [ -z "$RUN_USER" ] || [ "$RUN_USER" = "root" ]; then
  echo "Set RUN_USER to a non-root account (the agent only reads system metrics)." >&2
  exit 1
fi

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "Python 3.9 or newer is required (macOS: xcode-select --install or brew install python)." >&2
  exit 1
fi

mkdir -p "$DIR"
[ -x "$DIR/venv/bin/python" ] || "$PYTHON" -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/venv/bin/python" -m pip install --quiet --upgrade "$SOURCE"

NEW_TOKEN=""
if [ ! -f "$DIR/token" ]; then
  NEW_TOKEN="$("$DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  (umask 077 && printf '%s' "$NEW_TOKEN" > "$DIR/token")
fi
chown "$RUN_USER" "$DIR/token"
chmod 600 "$DIR/token"

CMD=("$DIR/venv/bin/python" -m purrmox.agent --port "$PORT" --token-file "$DIR/token")

if [ "$OS" = "Darwin" ]; then
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>UserName</key><string>${RUN_USER}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${CMD[0]}</string><string>${CMD[1]}</string><string>${CMD[2]}</string>
    <string>${CMD[3]}</string><string>${CMD[4]}</string><string>${CMD[5]}</string><string>${CMD[6]}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/tmp/purrmox-agent.log</string>
</dict>
</plist>
EOF
  chown root:wheel "$PLIST"
  chmod 644 "$PLIST"
  launchctl bootout "system/${LABEL}" 2>/dev/null || true
  launchctl bootstrap system "$PLIST"
  echo "If macOS asks whether Python may accept incoming connections, choose Allow."
else
  cat > "$PLIST" <<EOF
[Unit]
Description=Purrmox agent
After=network-online.target
Wants=network-online.target

[Service]
User=${RUN_USER}
ExecStart=${CMD[*]}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now purrmox-agent
  systemctl restart purrmox-agent
fi

echo
echo "Purrmox agent installed on port ${PORT}."
if [ -n "$NEW_TOKEN" ]; then
  echo "Shared secret (shown once, put it in the 'hosts' section of the Purrmox config.yaml):"
  echo "  $NEW_TOKEN"
else
  echo "Existing shared secret kept in $DIR/token (readable by ${RUN_USER} and root)."
fi
echo "Check: curl http://localhost:${PORT}/healthz"
