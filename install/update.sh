#!/usr/bin/env bash
# Upgrade an existing Purrmox installation to the latest version of the configured branch.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "This script must be run as root." >&2; exit 1; }
exec bash /opt/purrmox/install/install.sh
