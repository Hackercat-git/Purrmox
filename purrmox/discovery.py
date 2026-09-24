"""Background task that collects IP addresses and open ports and caches them."""
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from purrmox.links import safe_scheme, safe_url
from purrmox.proxmox import guest_ip, node_ip, summarize

HTTPS_PORTS = {443, 8443, 8006}
PORT_NAMES = {22: "SSH", 80: "HTTP", 443: "HTTPS"}
WEB_PORTS = {80, 81, 443, 3000, 3001, 5678, 8000, 8080, 8096, 8123, 8443, 9000, 8006}


def port_open(ip, port, timeout=0.6):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def guess_scheme(port):
    """Guess the URL scheme for a well-known port; None if it is probably not a web port."""
    if port in HTTPS_PORTS:
        return "https"
    if port in WEB_PORTS:
        return "http"
    return None


class Discovery:
    """Resolve guest and node IPs and scan configured ports on a fixed interval."""

    def __init__(self, client, cfg, interval=60):
        self.client = client
        self.cfg = cfg
        self.interval = interval
        self.scan_ports = [int(p) for p in cfg.get("scan_ports", [])]
        self.overrides = {int(k): v for k, v in (cfg.get("guests") or {}).items()}
        self.node_ips = {}
        self.guests = {}
        self._lock = threading.Lock()
        self._resources = []
        self.updated = None

    def update_resources(self, summary):
        with self._lock:
            self._resources = summary

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self._refresh()
            except Exception:  # the loop must never stop
                pass
            time.sleep(self.interval)

    def _refresh(self):
        with self._lock:
            summary = self._resources
        if not summary:
            try:
                summary = summarize(self.client.resources())
            except Exception:
                return
        with ThreadPoolExecutor(max_workers=8) as pool:
            node_futs = {n["name"]: pool.submit(node_ip, self.client, n["name"]) for n in summary["nodes"]}
            guest_futs = {}
            for g in summary["guests"]:
                if g["running"]:
                    kind = "qemu" if g["type"] == "VM" else "lxc"
                    guest_futs[g["id"]] = pool.submit(guest_ip, self.client, g["node"], g["id"], kind)
            node_ips = {k: f.result() for k, f in node_futs.items()}
            ips = {k: (self.overrides.get(k, {}).get("ip") or f.result()) for k, f in guest_futs.items()}

            scan = {}
            if self.scan_ports:
                for gid, ip in ips.items():
                    if ip:
                        for port in self.scan_ports:
                            scan[(gid, port)] = pool.submit(port_open, ip, port)
            open_ports = {}
            for (gid, port), f in scan.items():
                if f.result():
                    open_ports.setdefault(gid, []).append(port)

        result = {}
        for gid, ip in ips.items():
            result[gid] = {"ip": ip, "ports": self._build_ports(gid, ip, open_ports.get(gid, []))}
        with self._lock:
            self.node_ips = node_ips
            self.guests = result
            self.updated = time.time()

    def _build_ports(self, gid, ip, opened):
        """Merge scanned ports with the per-guest overrides from the configuration."""
        entries = {}
        for port in opened:
            entries[port] = {"port": port, "scheme": guess_scheme(port), "label": PORT_NAMES.get(port),
                             "open": True, "url": None}
        for item in (self.overrides.get(gid, {}).get("ports") or []):
            port = int(item["port"]) if isinstance(item, dict) else int(item)
            e = entries.setdefault(port, {"port": port, "scheme": guess_scheme(port),
                                          "label": PORT_NAMES.get(port), "open": None, "url": None})
            if isinstance(item, dict):
                e["scheme"] = safe_scheme(item.get("scheme")) or e["scheme"]
                e["label"] = item.get("label", e["label"])
                e["url"] = safe_url(item.get("url"))
            if self.scan_ports and port in self.scan_ports:
                e["open"] = port in opened
        return sorted(entries.values(), key=lambda x: x["port"])

    def apply(self, data):
        """Enrich a status snapshot with IPs, ports, notes and custom URLs."""
        with self._lock:
            node_ips, guests = dict(self.node_ips), dict(self.guests)
        for n in data["nodes"]:
            n["ip"] = node_ips.get(n["name"])
        for g in data["guests"]:
            ov = self.overrides.get(g["id"], {})
            info = guests.get(g["id"], {})
            g["ip"] = ov.get("ip") or info.get("ip")
            g["ports"] = info.get("ports", [])
            g["note"] = ov.get("note")
            g["urls"] = [{"label": u.get("label") or u.get("url"), "url": safe_url(u.get("url"))}
                         for u in (ov.get("urls") or []) if isinstance(u, dict) and safe_url(u.get("url"))]
        return data
