#!/usr/bin/env bash
#
# Purrmox installer for Debian and Ubuntu. Run as root inside the container or VM that
# should host the dashboard. The script is idempotent: running it again upgrades Purrmox.
#
#   curl -fsSL https://raw.githubusercontent.com/Hackercat-git/Purrmox/main/install/install.sh | bash
#
# Optional environment variables:
#   REPO_URL   git repository to install from (default: the official repository)
#   BRANCH     branch or tag to install (default: main)
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Hackercat-git/Purrmox.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/purrmox
CONF_DIR=/etc/purrmox
DATA_DIR=/var/lib/purrmox
SERVICE_USER=purrmox

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "This script must be run as root."
command -v apt-get >/dev/null 2>&1 || fail "Only Debian and Ubuntu are supported (apt-get not found)."

info "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates

info "Creating service user and directories"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$CONF_DIR" "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chown root:"$SERVICE_USER" "$CONF_DIR"
chmod 750 "$CONF_DIR"

info "Fetching Purrmox ($BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" reset --quiet --hard FETCH_HEAD
else
  rm -rf "$APP_DIR"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

info "Installing the application into a virtual environment"
[ -x "$APP_DIR/venv/bin/python" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet --upgrade "$APP_DIR"

ln -sf "$APP_DIR/venv/bin/purrmox-setup" /usr/local/bin/purrmox-setup
ln -sf "$APP_DIR/venv/bin/purrmox-hashpw" /usr/local/bin/purrmox-hashpw
ln -sf "$APP_DIR/install/update.sh" /usr/local/bin/purrmox-update

info "Installing the systemd service"
if [ ! -f "$CONF_DIR/purrmox.env" ]; then
  printf '# Secrets for Purrmox. Keep this file private.\nDISCORD_BOT_TOKEN=\n' > "$CONF_DIR/purrmox.env"
  chmod 600 "$CONF_DIR/purrmox.env"
fi
cp "$APP_DIR/install/purrmox.service" /etc/systemd/system/purrmox.service
cp "$APP_DIR/config.example.yaml" "$CONF_DIR/config.example.yaml"
systemctl daemon-reload
systemctl enable purrmox >/dev/null 2>&1

if [ -f "$CONF_DIR/config.yaml" ]; then
  chown "$SERVICE_USER:$SERVICE_USER" "$CONF_DIR/config.yaml"
  chmod 600 "$CONF_DIR/config.yaml"
  systemctl restart purrmox
  info "Purrmox is running. Check it with: systemctl status purrmox"
else
  cat <<MSG

Purrmox is installed, but it still needs a configuration file.

  1. Create it interactively:   purrmox-setup --output $CONF_DIR/config.yaml
  2. Give the service access:   chown $SERVICE_USER:$SERVICE_USER $CONF_DIR/config.yaml
  3. Start the dashboard:       systemctl start purrmox
  4. Follow the log:            journalctl -u purrmox -f

To enable Discord alerts, put DISCORD_BOT_TOKEN in $CONF_DIR/purrmox.env.
To upgrade later, run:          purrmox-update
MSG
fi
