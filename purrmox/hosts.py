"""Polls Purrmox agents on machines outside Proxmox (Windows, macOS, Linux) and caches the result."""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from purrmox.links import safe_url


def slug(name):
    """Stable identifier for a host name, safe for URLs and the history table."""
    return re.sub(r"[^\w.-]+", "-", str(name).strip()).strip("-")[:64] or "host"


def resolve_token(host_cfg):
    """Return the agent token from the configuration or from the environment variable ``token_env``."""
    return host_cfg.get("token") or os.environ.get(host_cfg.get("token_env") or "", "")


def validate(hosts):
    """Return a list of problems with the ``hosts`` configuration (empty when it is valid)."""
    problems, seen = [], set()
    if not isinstance(hosts, list):
        return ["'hosts' must be a list"]
    for i, h in enumerate(hosts, 1):
        if not isinstance(h, dict) or not h.get("name") or not safe_url(h.get("url")):
            problems.append(f"hosts[{i}] needs a 'name' and an http(s) 'url'")
            continue
        if not resolve_token(h):
            problems.append(f"hosts[{i}] ({h['name']}) needs a 'token' or a 'token_env' that is set")
        key = slug(h["name"])
        if key in seen:
            problems.append(f"hosts[{i}] ({h['name']}) has a duplicate name")
        seen.add(key)
    return problems


def _num(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _text(value, limit=120):
    return str(value)[:limit] if value is not None else None


def normalize(raw):
    """Keep only known, correctly typed fields from an agent response."""
    disks = [{"mount": _text(d.get("mount"), 60), "used": _num(d.get("used")), "total": _num(d.get("total"))}
             for d in raw.get("disks") or [] if isinstance(d, dict)][:12]
    battery = raw.get("battery")
    if isinstance(battery, dict):
        battery = {"percent": _num(battery.get("percent")), "plugged": bool(battery.get("plugged"))}
    else:
        battery = None
    temp = raw.get("temp")
    return {
        "hostname": _text(raw.get("hostname")), "os": _text(raw.get("os")), "platform": _text(raw.get("platform"), 20),
        "ip": _text(raw.get("ip"), 45), "version": _text(raw.get("version"), 20),
        "cpu": round(_num(raw.get("cpu")), 1), "cores": int(_num(raw.get("cores"))),
        "mem_used": _num(raw.get("mem_used")), "mem_total": _num(raw.get("mem_total")),
        "disk_used": _num(raw.get("disk_used")), "disk_total": _num(raw.get("disk_total")),
        "disks": disks, "uptime": int(_num(raw.get("uptime"))),
        "battery": battery, "temp": temp if isinstance(temp, (int, float)) else None,
    }


class HostPoller:
    """Fetch ``/metrics`` from every configured agent in parallel on a fixed interval."""

    def __init__(self, hosts, interval=30, timeout=4):
        self.hosts = [dict(h, id=slug(h["name"])) for h in hosts or []]
        self.interval, self.timeout = interval, timeout
        self.results = []
        self._last = {}
        self._lock = threading.Lock()

    def __bool__(self):
        return bool(self.hosts)

    def start(self):
        if self.hosts:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.refresh()
            except Exception:  # the loop must never stop
                pass
            time.sleep(self.interval)

    def _one(self, h):
        base = {"id": h["id"], "name": h["name"], "note": h.get("note"), "link": safe_url(h.get("link")),
                "online": False, "error": None, "ms": 0, "seen": None}
        start = time.time()
        try:
            r = requests.get(h["url"].rstrip("/") + "/metrics", timeout=self.timeout,
                             headers={"Authorization": "Bearer " + resolve_token(h)})
            if r.status_code == 401:
                base["error"] = "Agent rejected the token"
            else:
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise ValueError("unexpected response")
                base.update(normalize(data), online=True, ms=round((time.time() - start) * 1000), seen=int(time.time()))
                self._last[h["id"]] = base
                return base
        except requests.RequestException as e:
            base["error"] = f"Agent unreachable: {e.__class__.__name__}"
        except ValueError:
            base["error"] = "Agent returned invalid data"
        # Keep the last known figures so the card does not go blank while the host is down.
        last = self._last.get(h["id"])
        if last:
            base = dict(last, online=False, error=base["error"], ms=0)
        return base

    def refresh(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self._one, self.hosts))
        with self._lock:
            self.results = results

    def get(self):
        with self._lock:
            return [dict(r) for r in self.results]
