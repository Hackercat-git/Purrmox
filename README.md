# Purrmox

A lightweight, self-hosted status dashboard for [Proxmox VE](https://www.proxmox.com/en/proxmox-virtual-environment) homelabs.
Purrmox gives you a single, fast overview of your nodes, virtual machines and containers, and keeps an eye on your
services, backups and certificates, with optional Discord alerts.

## Features

- **Live overview** of every node, VM and LXC container: status, CPU, memory, storage, uptime and tags.
- **Automatic discovery** of guest IP addresses and open ports, with clickable links to web interfaces.
- **Every machine in one console**: Windows, macOS and Linux machines outside Proxmox report CPU, RAM, disks,
  battery and uptime through a small agent, with the same history and alerts as your nodes.
- **Service checks** over HTTP or TCP, with latency and clickable cards.
- **History** of CPU and memory usage per node and guest (SQLite, configurable retention).
- **Discord alerts** through a bot: stopped guests, offline nodes, unreachable services, high RAM or storage usage,
  outdated backups and expiring TLS certificates, with thresholds and cooldowns to prevent spam.
- **Backup awareness**: shows the age of the latest backup for every guest.
- **Certificate monitoring** for any host you list.
- **Export** of the inventory as Markdown or JSON.
- **Optional power actions** (start, shut down, reboot) using a separate write token; disabled by default.
- **Secure by default**: read-only API token, Basic authentication with a hashed password and brute-force limiting,
  refusal to listen on the network without authentication, and strict validation of user-supplied links.

## Quick install on Proxmox VE

Run the following command in the **shell of your Proxmox host** (as root). It creates a small unprivileged Debian
container, installs Purrmox, and creates a dedicated read-only API user and token for it.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hackercat-git/Purrmox/main/install/create-lxc.sh)"
```

The script asks for confirmation before it changes anything. When it finishes it prints the dashboard URL and a
generated login (only the password hash is stored). The API token secret is passed directly from Proxmox to the
container and is never displayed.

You can customize the container with environment variables, for example:

```bash
CTID=250 CT_IP=192.168.1.50/24 CT_GW=192.168.1.1 CT_MEMORY=768 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/Hackercat-git/Purrmox/main/install/create-lxc.sh)"
```

Use `--yes` to accept all defaults without prompting, or `--no-token` to skip the API token step. Run
`create-lxc.sh --help` for the full list of options.

> As with any script you pipe into a shell, review it first:
> [`install/create-lxc.sh`](install/create-lxc.sh) and [`install/install.sh`](install/install.sh).

### Install on an existing Debian or Ubuntu machine

As root, on the machine that should host Purrmox:

```bash
curl -fsSL https://raw.githubusercontent.com/Hackercat-git/Purrmox/main/install/install.sh | bash
purrmox-setup --output /etc/purrmox/config.yaml
chown purrmox:purrmox /etc/purrmox/config.yaml
systemctl start purrmox
```

### Upgrading

Inside the container or machine that runs Purrmox:

```bash
purrmox-update
```

From the Proxmox host: `pct exec <CTID> -- purrmox-update`.

## Monitoring Windows, macOS and other machines (agent)

Proxmox only knows its own nodes and guests. To see every other machine in the same dashboard, run the small
**Purrmox agent** on it. The agent serves `GET /metrics` (CPU, RAM, disks, uptime, battery, temperature where
available) and protects it with a shared secret; Purrmox polls it every `poll_seconds` and adds the machine to the
"Other machines" section, to the history graphs, to the Markdown/JSON export, and to Discord/Telegram alerts
(offline, RAM and storage thresholds).

**Windows** (elevated PowerShell; needs Python 3.9+; installs as a startup task running as SYSTEM):

```powershell
.\install\agent\install-windows.ps1 -AllowFrom 192.168.1.50   # IP of the machine that runs Purrmox
```

**macOS and Linux** (installs a launchd daemon or systemd service):

```bash
sudo ./install/agent/install-unix.sh
```

Each script prints a shared secret once. Add the machine to `config.yaml`, keeping the secret in an environment
variable (for the container install: `/etc/purrmox/purrmox.env`):

```yaml
hosts:
  - {name: "Milan PC", url: "http://192.168.1.60:9100", token_env: "AGENT_TOKEN_PC", note: "Windows desktop"}
```

Run the agent by hand instead with `pip install "purrmox[agent]"`, set `PURRMOX_AGENT_TOKEN` (at least 16
characters) and start `purrmox-agent --port 9100`. Use `--uninstall` (unix) or `-Uninstall` (Windows) to remove it.

The agent is read-only and has no commands, only metrics. Traffic is plain HTTP, so keep it on your LAN or
Tailscale and restrict the firewall to the Purrmox server (the Windows script does this with `-AllowFrom`).

## Creating the API token manually

Purrmox only needs read access. On the Proxmox host:

```bash
pveum user add purrmox@pve
pveum acl modify / --users purrmox@pve --roles PVEAuditor
pveum user token add purrmox@pve dash --privsep 0
```

Copy the token secret that is displayed; Proxmox shows it only once. Use `purrmox@pve!dash` as the token ID.

If you create the token with privilege separation enabled (`--privsep 1`), grant the `PVEAuditor` role to the
token as well as to the user, because the effective permissions are the intersection of both.

## Configuration

Configuration lives in a YAML file (default `config.yaml`, or the path given with `--config` or `PURRMOX_CONFIG`).
Generate one with `purrmox-setup`, or copy [`config.example.yaml`](config.example.yaml), which documents every option:
services, bookmarks, port scanning, per-guest notes and links, certificate checks, alert thresholds and more.

### Discord alerts

1. Create a bot in the [Discord developer portal](https://discord.com/developers/applications) and invite it to your
   server with permission to view and send messages in the target channel.
2. Set `discord.enabled: true`, `guild_id` and `channel_id` in the configuration.
3. Provide the bot token through the `DISCORD_BOT_TOKEN` environment variable. In the container installation, add it to
   `/etc/purrmox/purrmox.env` and run `systemctl restart purrmox`.

Purrmox verifies that the channel belongs to the configured server before it posts, and disables alerts otherwise.

## Running from source

```bash
python3 -m venv venv
. venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml   # then edit it
purrmox --config config.yaml
```

With the default `listen.host` of `127.0.0.1`, the dashboard is available at http://127.0.0.1:8080.
To listen on the network, set `listen.host: "0.0.0.0"` and configure `auth` (create a hash with `purrmox-hashpw`).

### Docker

```bash
docker build -t purrmox .
docker run -d --name purrmox -p 8080:8080 -v "$PWD/data:/data" purrmox
```

Place your `config.yaml` in `./data` and set `listen.host: "0.0.0.0"` with `auth` enabled.

## Development

```bash
pip install -e .
python -m unittest discover -s tests -v
```

## Security notes

- Agents require a bearer token (compared in constant time), expose metrics only, and the Windows installer limits
  the firewall rule to private networks and one allowed address.
- Prefer the read-only `PVEAuditor` token. Power actions require a separate token, authentication, and are off by default.
- Keep Purrmox behind your LAN or a VPN. Do not expose it directly to the internet.
- The configuration file contains the API token secret and is created with owner-only permissions.
- Never commit `config.yaml` or `purrmox.env`; both are ignored by git.

## License

[MIT](LICENSE) © Hackercat
