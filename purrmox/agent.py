"""Small metrics agent for machines that are not managed by Proxmox (Windows, macOS, Linux).

Run it on every extra machine; the Purrmox dashboard polls ``GET /metrics`` with a bearer token.
Requires the optional dependency ``psutil`` (``pip install "purrmox[agent]"``).
"""
import argparse
import hmac
import json
import os
import platform
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from purrmox import __version__

DEFAULT_PORT = 9100
TOKEN_ENV = "PURRMOX_AGENT_TOKEN"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _psutil():
    try:
        import psutil
    except ImportError:
        sys.exit("The Purrmox agent needs psutil: pip install \"purrmox[agent]\" (or pip install psutil).")
    return psutil


def os_label():
    """Human-readable operating system name, for example 'Windows 11' or 'macOS 14.5'."""
    system = platform.system()
    if system == "Darwin":
        return "macOS " + (platform.mac_ver()[0] or "")
    if system == "Windows":
        release = platform.release()
        try:  # Python reports Windows 11 as "10"; build numbers from 22000 are Windows 11.
            if release == "10" and int(platform.version().split(".")[2]) >= 22000:
                release = "11"
        except (IndexError, ValueError):
            pass
        return f"Windows {release}"
    if system == "Linux":
        try:
            return platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except (AttributeError, OSError):
            return "Linux " + platform.release()
    return f"{system} {platform.release()}".strip()


def primary_disk_path():
    """Path of the main volume: the system drive on Windows, the data volume on macOS, / elsewhere."""
    if os.name == "nt":
        return (os.environ.get("SystemDrive") or "C:") + "\\"
    # On APFS, "/" is a small sealed system volume; user data lives on the Data volume.
    if sys.platform == "darwin" and os.path.isdir("/System/Volumes/Data"):
        return "/System/Volumes/Data"
    return "/"


def local_ip():
    """Best-effort LAN address (no packets are sent by a UDP connect)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


class Collector:
    """Gather system metrics. CPU load is sampled in the background so requests never block."""

    def __init__(self, psutil):
        self.ps = psutil
        self.cpu = 0.0
        self.disk_path = primary_disk_path()
        self.hostname = socket.gethostname()
        self.os = os_label()
        self.psutil_ready = threading.Event()
        self.ps.cpu_percent(interval=None)  # prime the counter
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self):
        while True:
            self.cpu = self.ps.cpu_percent(interval=2)
            self.psutil_ready.set()

    def _disks(self):
        out, seen = [], set()
        for part in self.ps.disk_partitions(all=False):
            if "cdrom" in part.opts or part.fstype == "" or part.mountpoint in seen:
                continue
            if part.mountpoint.startswith(("/System/Volumes/", "/snap/", "/boot")) \
                    and part.mountpoint != self.disk_path:
                continue
            seen.add(part.mountpoint)
            try:
                u = self.ps.disk_usage(part.mountpoint)
            except OSError:
                continue
            if u.total:
                out.append({"mount": part.mountpoint, "used": u.used, "total": u.total})
        return out

    def _battery(self):
        try:
            b = self.ps.sensors_battery()
        except Exception:  # unsupported platform or missing driver
            return None
        return {"percent": round(b.percent), "plugged": bool(b.power_plugged)} if b else None

    def _temp(self):
        try:
            sensors = self.ps.sensors_temperatures()  # Linux and FreeBSD only
        except (AttributeError, Exception):
            return None
        values = [t.current for entries in sensors.values() for t in entries if t.current]
        return round(max(values), 1) if values else None

    def snapshot(self):
        mem = self.ps.virtual_memory()
        try:
            main = self.ps.disk_usage(self.disk_path)
            disk_used, disk_total = main.used, main.total
        except OSError:
            disk_used = disk_total = 0
        return {
            "agent": "purrmox-agent", "version": __version__,
            "hostname": self.hostname, "os": self.os, "platform": sys.platform, "ip": local_ip(),
            "cpu": round(self.cpu, 1), "cores": self.ps.cpu_count(logical=True) or 0,
            "mem_used": mem.total - mem.available, "mem_total": mem.total,
            "disk_used": disk_used, "disk_total": disk_total, "disks": self._disks(),
            "uptime": int(time.time() - self.ps.boot_time()),
            "battery": self._battery(), "temp": self._temp(),
        }


def make_handler(collector, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PurrmoxAgent"

        def _send(self, code, body, ctype="application/json"):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path != "/metrics":
                return self._send(404, {"error": "not found"})
            given = self.headers.get("Authorization", "")
            expected = "Bearer " + token
            if not hmac.compare_digest(given.encode(), expected.encode()):
                return self._send(401, {"error": "invalid token"})
            self._send(200, collector.snapshot())

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

    return Handler


def read_token(args):
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(TOKEN_ENV, "").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="purrmox-agent",
                                     description="Metrics agent for Windows, macOS and Linux machines.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on (default: %(default)s)")
    parser.add_argument("--token-file", help=f"file with the shared secret (alternative to ${TOKEN_ENV})")
    parser.add_argument("--version", action="version", version=f"Purrmox agent {__version__}")
    args = parser.parse_args(argv)

    token = read_token(args)
    if len(token) < 16:
        sys.exit(f"Set a shared secret of at least 16 characters in ${TOKEN_ENV} or --token-file. "
                 "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    collector = Collector(_psutil())
    collector.psutil_ready.wait(timeout=5)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(collector, token))
    print(f"Purrmox agent {__version__} on {collector.hostname} ({collector.os}) "
          f"listening on http://{args.host}:{args.port}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
