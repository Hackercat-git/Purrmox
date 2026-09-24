"""Proxmox VE API client (read-only by default) and helpers that summarize its data."""
import requests
import urllib3


class ProxmoxError(Exception):
    """Raised when the Proxmox API cannot be reached or rejects a request."""


class ProxmoxClient:
    def __init__(self, host, token_id, token_secret, port=8006, verify_ssl=False, timeout=5, scheme="https"):
        self.base = f"{scheme}://{host}:{port}/api2/json"
        self.verify = verify_ssl
        self.timeout = timeout
        self.headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(self, method, path, data=None):
        try:
            r = requests.request(method, self.base + path, headers=self.headers, data=data,
                                 verify=self.verify, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["data"]
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            hints = {
                401: "The token ID or secret is invalid (check the user, realm and token name).",
                403: "The token lacks permissions (grant the PVEAuditor role on path / "
                     "to both the user and the token).",
            }
            raise ProxmoxError(f"HTTP {code}. " + hints.get(code, str(e))) from e
        except requests.RequestException as e:
            raise ProxmoxError(f"Proxmox is unreachable: {e}") from e

    def _get(self, path):
        return self._request("GET", path)

    def post(self, path, data=None):
        return self._request("POST", path, data=data or {})

    def resources(self):
        """Return nodes, VMs, containers and storage in a single call."""
        return self._get("/cluster/resources")


def _pct(used, total):
    return round(used / total * 100, 1) if total else 0


def summarize(resources):
    """Convert raw API resources into a compact structure for the frontend."""
    nodes, guests = [], []
    for r in resources:
        kind = r.get("type")
        if kind == "node":
            nodes.append({
                "name": r.get("node"),
                "online": r.get("status") == "online",
                "cpu": round(r.get("cpu", 0) * 100, 1),
                "mem_used": r.get("mem", 0),
                "mem_total": r.get("maxmem", 0),
                "disk_used": r.get("disk", 0),
                "disk_total": r.get("maxdisk", 0),
                "cores": r.get("maxcpu", 0),
                "uptime": r.get("uptime", 0),
            })
        elif kind in ("qemu", "lxc"):
            if r.get("template"):
                continue
            guests.append({
                "id": r.get("vmid"),
                "name": r.get("name"),
                "type": "VM" if kind == "qemu" else "CT",
                "node": r.get("node"),
                "running": r.get("status") == "running",
                "cpu": round(r.get("cpu", 0) * 100, 1),
                "mem_used": r.get("mem", 0),
                "mem_total": r.get("maxmem", 0),
                "disk_used": r.get("disk", 0),
                "disk_total": r.get("maxdisk", 0),
                "cores": r.get("maxcpu", 0),
                "tags": [t for t in (r.get("tags") or "").split(";") if t],
                "uptime": r.get("uptime", 0),
            })
    nodes.sort(key=lambda n: n["name"] or "")
    guests.sort(key=lambda g: g["id"] or 0)
    return {"nodes": nodes, "guests": guests}


def mem_percent(item):
    return _pct(item.get("mem_used", 0), item.get("mem_total", 0))


def disk_percent(item):
    return _pct(item.get("disk_used", 0), item.get("disk_total", 0))


def _first_ipv4(addresses):
    for a in addresses:
        ip = a.split("/")[0]
        if ip and not ip.startswith("127.") and ":" not in ip:
            return ip
    return None


def guest_ip(client, node, vmid, kind):
    """Find the IPv4 address of a VM or container, or None if it cannot be determined."""
    try:
        if kind == "lxc":
            ifaces = client._get(f"/nodes/{node}/lxc/{vmid}/interfaces")
            addrs = [i.get("inet", "") for i in ifaces if i.get("name") != "lo"]
            ip = _first_ipv4(addrs)
            if ip:
                return ip
            cfg = client._get(f"/nodes/{node}/lxc/{vmid}/config")
            for key, val in cfg.items():
                if key.startswith("net") and "ip=" in val:
                    for part in val.split(","):
                        if part.startswith("ip="):
                            return _first_ipv4([part[3:]])
        else:
            data = client._get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
            for iface in data.get("result", []):
                if iface.get("name") == "lo":
                    continue
                addrs = [a.get("ip-address", "") for a in iface.get("ip-addresses", [])
                         if a.get("ip-address-type") == "ipv4"]
                ip = _first_ipv4(addrs)
                if ip:
                    return ip
    except ProxmoxError:
        return None
    return None


def node_ip(client, node):
    """Return the IPv4 address of a node (the first bridge that has an address)."""
    try:
        nets = client._get(f"/nodes/{node}/network")
    except ProxmoxError:
        return None
    for n in nets:
        if n.get("type") == "bridge" and n.get("address"):
            return n["address"]
    return None


def list_backups(client, resources):
    """Return the latest backup per guest ID, or None if no backup storage could be read."""
    result, seen = {}, False
    for r in resources:
        if r.get("type") != "storage" or "backup" not in (r.get("content") or ""):
            continue
        if r.get("status") not in (None, "available"):
            continue
        try:
            items = client._get(f"/nodes/{r['node']}/storage/{r['storage']}/content?content=backup")
        except ProxmoxError:
            continue
        seen = True
        for it in items:
            vmid, ctime = it.get("vmid"), it.get("ctime", 0)
            if vmid is None:
                continue
            cur = result.get(int(vmid))
            if not cur or ctime > cur["last"]:
                result[int(vmid)] = {"last": ctime, "size": it.get("size", 0), "storage": r["storage"]}
    return result if seen else None
