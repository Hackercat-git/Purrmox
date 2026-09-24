#!/usr/bin/env bash
#
# Create a Purrmox LXC container on a Proxmox VE host. Run this as root on the Proxmox host shell:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hackercat-git/Purrmox/main/install/create-lxc.sh)"
#
# The script creates a small unprivileged Debian container, installs Purrmox in it and (optionally)
# creates a dedicated read-only API token for it. The token secret goes straight from Proxmox into the
# container's configuration file; it is never printed.
#
# Options:
#   -y, --yes         accept all defaults without prompting (creates the API token)
#   --no-token        do not create an API token; configure Purrmox manually afterwards
#   -h, --help        show this help
#
# Settings can be overridden through environment variables, for example:
#   CTID=250 CT_IP=192.168.1.50/24 CT_GW=192.168.1.1 bash create-lxc.sh
#
#   CTID            container ID                       (default: next free ID)
#   CT_HOSTNAME     container host name                (default: purrmox)
#   CT_MEMORY       memory in MiB                      (default: 512)
#   CT_CORES        CPU cores                          (default: 1)
#   CT_DISK         root disk size in GiB              (default: 4)
#   CT_STORAGE      storage for the root disk          (default: first storage that allows containers)
#   CT_BRIDGE       network bridge                     (default: vmbr0)
#   CT_IP           "dhcp" or an address like 192.168.1.50/24 (default: dhcp)
#   CT_GW           gateway, required for a static address
#   TEMPLATE_STORAGE storage holding container templates (default: local)
#   PVE_HOST        address Purrmox uses to reach the API (default: this host's primary IP)
#   REPO_URL, BRANCH  source repository and branch
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Hackercat-git/Purrmox.git}"
BRANCH="${BRANCH:-main}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/Hackercat-git/Purrmox/${BRANCH}}"
CT_HOSTNAME="${CT_HOSTNAME:-purrmox}"
CT_MEMORY="${CT_MEMORY:-512}"
CT_CORES="${CT_CORES:-1}"
CT_DISK="${CT_DISK:-4}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_IP="${CT_IP:-dhcp}"
CT_GW="${CT_GW:-}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
API_USER="purrmox@pve"
API_TOKEN_NAME="dash"

ASSUME_YES=0
CREATE_TOKEN=ask
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --no-token) CREATE_TOKEN=no ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Ask a yes/no question. Reads from the terminal so the script also works when piped from curl.
confirm() {
  [ "$ASSUME_YES" -eq 1 ] && return 0
  local answer
  read -r -p "$1 [Y/n] " answer < /dev/tty || return 0
  case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

[ "$(id -u)" -eq 0 ] || fail "Run this script as root on the Proxmox host."
for cmd in pct pveam pvesm pveum pvesh; do
  command -v "$cmd" >/dev/null 2>&1 || fail "'$cmd' not found. This script must run on a Proxmox VE host."
done

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
if pct status "$CTID" >/dev/null 2>&1; then
  fail "Container ID $CTID is already in use. Set CTID to a free ID."
fi

if [ -z "${CT_STORAGE:-}" ]; then
  CT_STORAGE="$(pvesm status --content rootdir | awk 'NR>1 {print $1; exit}')"
  [ -n "$CT_STORAGE" ] || fail "No storage that can hold containers was found. Set CT_STORAGE."
fi

NET="name=eth0,bridge=${CT_BRIDGE}"
if [ "$CT_IP" = "dhcp" ]; then
  NET="${NET},ip=dhcp"
else
  [ -n "$CT_GW" ] || fail "A static address requires CT_GW (the gateway)."
  NET="${NET},ip=${CT_IP},gw=${CT_GW}"
fi

cat <<SUMMARY

Purrmox will create this container:
  ID        $CTID
  Hostname  $CT_HOSTNAME
  Resources ${CT_CORES} core(s), ${CT_MEMORY} MiB RAM, ${CT_DISK} GiB disk on '${CT_STORAGE}'
  Network   ${CT_BRIDGE}, ${CT_IP}

SUMMARY
confirm "Continue?" || fail "Aborted."

info "Preparing the Debian container template"
pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -n 1)"
[ -n "$TEMPLATE" ] || fail "Could not find a Debian 12 template."
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

info "Creating container $CTID"
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" \
  --memory "$CT_MEMORY" \
  --cores "$CT_CORES" \
  --rootfs "${CT_STORAGE}:${CT_DISK}" \
  --net0 "$NET" \
  --ostype debian \
  --unprivileged 1 \
  --onboot 1 \
  --tags purrmox
pct start "$CTID"

info "Waiting for the container network"
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 \
  || fail "The container has no working network or DNS. Check the bridge, address and gateway."

info "Installing Purrmox inside the container"
pct exec "$CTID" -- bash -c "apt-get update -y && apt-get install -y --no-install-recommends curl ca-certificates"
pct exec "$CTID" -- bash -c "curl -fsSL '${RAW_BASE}/install/install.sh' | REPO_URL='${REPO_URL}' BRANCH='${BRANCH}' bash"

CT_ADDRESS="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"

if [ "$CREATE_TOKEN" = ask ]; then
  echo
  echo "Purrmox needs a read-only Proxmox API token. This script can create a dedicated user"
  echo "('${API_USER}') with the PVEAuditor role and a token named '${API_TOKEN_NAME}'."
  if confirm "Create the API token and configure Purrmox automatically?"; then CREATE_TOKEN=yes; else CREATE_TOKEN=no; fi
fi

if [ "$CREATE_TOKEN" = yes ]; then
  info "Creating the read-only API user and token"
  PVE_HOST="${PVE_HOST:-$(ip -4 route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')}"
  [ -n "$PVE_HOST" ] || fail "Could not determine this host's IP address. Set PVE_HOST."

  pveum user list --output-format json | grep -q "\"userid\":\"${API_USER}\"" \
    || pveum user add "$API_USER" --comment "Purrmox read-only dashboard user"
  pveum acl modify / --users "$API_USER" --roles PVEAuditor

  if pveum user token list "$API_USER" --output-format json | grep -q "\"tokenid\":\"${API_TOKEN_NAME}\""; then
    warn "Token ${API_USER}!${API_TOKEN_NAME} already exists; its secret cannot be shown again."
    warn "Remove it with 'pveum user token remove ${API_USER} ${API_TOKEN_NAME}' and run this script again,"
    warn "or run 'purrmox-setup' inside container ${CTID} to enter an existing token."
  else
    SECRET="$(pveum user token add "$API_USER" "$API_TOKEN_NAME" --privsep 0 --output-format json \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])')"
    info "Writing the Purrmox configuration"
    printf '%s\n' "$SECRET" | pct exec "$CTID" -- /opt/purrmox/venv/bin/purrmox-setup \
      --output /etc/purrmox/config.yaml --host "$PVE_HOST" --token-id "${API_USER}!${API_TOKEN_NAME}" \
      --secret-stdin --username admin --generate-password --force
    unset SECRET
    pct exec "$CTID" -- bash -c "chown purrmox:purrmox /etc/purrmox/config.yaml && systemctl restart purrmox"
  fi
else
  warn "Skipping the API token. Configure Purrmox with: pct exec ${CTID} -- purrmox-setup --output /etc/purrmox/config.yaml"
fi

cat <<DONE

Purrmox is installed in container ${CTID}.
  Dashboard: http://${CT_ADDRESS}:8080
  Login:     the user and password shown above (only the hash is stored)
  Log:       pct exec ${CTID} -- journalctl -u purrmox -f
  Upgrade:   pct exec ${CTID} -- purrmox-update

Optional Discord alerts: add DISCORD_BOT_TOKEN to /etc/purrmox/purrmox.env inside the container,
enable 'discord' in /etc/purrmox/config.yaml and restart the service.
DONE
