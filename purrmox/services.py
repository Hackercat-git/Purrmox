"""Background health checks for services (HTTP or TCP), run in parallel and cached."""
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from purrmox.links import safe_url

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def check_http(url, timeout=3):
    """Return whether the URL answers with a non-5xx status, plus latency in milliseconds."""
    start = time.time()
    try:
        r = requests.get(url, timeout=timeout, verify=False, allow_redirects=True)
        ok, status = r.status_code < 500, r.status_code
    except requests.RequestException:
        ok, status = False, None
    return {"ok": ok, "status": status, "ms": round((time.time() - start) * 1000)}


def check_tcp(target, timeout=3):
    """Return whether a TCP connection to ``host:port`` succeeds."""
    start = time.time()
    try:
        host, port = target.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=timeout):
            ok = True
    except (OSError, ValueError):
        ok = False
    return {"ok": ok, "status": None, "ms": round((time.time() - start) * 1000)}


class ServiceChecker:
    """Check all configured services on a fixed interval."""

    def __init__(self, services, interval=30):
        self.services = services or []
        self.interval = interval
        self.results = []
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            self.refresh()
            time.sleep(self.interval)

    def _one(self, s):
        if s.get("tcp"):
            res = check_tcp(s["tcp"])
        elif s.get("url"):
            res = check_http(s["url"])
        else:
            res = {"ok": False, "status": None, "ms": 0}
        res["name"] = s.get("name", "?")
        res["link"] = safe_url(s.get("link") or s.get("url"))
        return res

    def refresh(self):
        if not self.services:
            return
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self._one, self.services))
        with self._lock:
            self.results = results

    def get(self):
        with self._lock:
            return list(self.results)
