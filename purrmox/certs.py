"""TLS certificate expiry checks."""
import socket
import ssl
import threading
import time


def check_cert(host, port=443, timeout=4):
    """Return the days until the certificate expires, or an error description."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert()
        expires = ssl.cert_time_to_seconds(cert["notAfter"])
        return {"days": int((expires - time.time()) // 86400), "error": None}
    except ssl.SSLCertVerificationError:
        return {"days": None, "error": "invalid or self-signed"}
    except (OSError, ValueError, KeyError):
        return {"days": None, "error": "unreachable"}


class CertChecker:
    """Periodically check a list of certificates in a background thread."""

    def __init__(self, entries, interval=3600):
        self.entries = entries or []
        self.interval = interval
        self.results = []
        self._lock = threading.Lock()

    def refresh(self):
        results = []
        for e in self.entries:
            res = check_cert(e["host"], e.get("port", 443))
            res.update({"name": e.get("name") or e["host"], "host": e["host"]})
            results.append(res)
        with self._lock:
            self.results = results

    def start(self):
        if not self.entries:
            return

        def loop():
            while True:
                self.refresh()
                time.sleep(self.interval)
        threading.Thread(target=loop, daemon=True).start()

    def get(self):
        with self._lock:
            return list(self.results)
