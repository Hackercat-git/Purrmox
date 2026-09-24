"""Background task that polls Proxmox, records history and drives alerting."""
import copy
import threading
import time

from purrmox.proxmox import ProxmoxError, list_backups, summarize


class Monitor:
    def __init__(self, client, discovery, checker, certs, history, alerts, poll_seconds=30):
        self.client, self.discovery, self.checker = client, discovery, checker
        self.certs, self.history, self.alerts = certs, history, alerts
        self.poll_seconds = poll_seconds
        self.summary = None
        self.error = None
        self.updated = None
        self.backups = None
        self._backup_ts = 0
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # the loop must never stop
                with self._lock:
                    self.error = f"Internal error: {e.__class__.__name__}: {e}"
            time.sleep(self.poll_seconds)

    def poll(self):
        """Fetch the current state once and update history and alerts."""
        summary, error, resources = None, None, None
        try:
            resources = self.client.resources()
            summary = summarize(resources)
        except ProxmoxError as e:
            error = str(e)
        if summary:
            self.discovery.update_resources(summary)
            self.history.record(summary)
            if time.time() - self._backup_ts > 600:
                self.backups = list_backups(self.client, resources)
                self._backup_ts = time.time()
        with self._lock:
            self.summary, self.error, self.updated = summary, error, time.time()
        self.alerts.evaluate(summary, error, self.checker.get(), self.backups, self.certs.get())

    def get(self):
        """Return a deep copy of the latest snapshot."""
        with self._lock:
            return {"summary": copy.deepcopy(self.summary), "error": self.error,
                    "updated": self.updated, "backups": copy.deepcopy(self.backups)}
