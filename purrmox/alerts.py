"""Decides when a Discord notification is sent, avoiding spam through thresholds and cooldowns."""
import time

from purrmox.proxmox import disk_percent, mem_percent


class Alerts:
    def __init__(self, notifier, cfg):
        a = (cfg or {}).get("alerts") or {}
        self.n = notifier
        self.ram = a.get("ram_percent", 90)
        self.disk = a.get("disk_percent", 90)
        self.backup_hours = a.get("backup_max_age_hours", 48)
        self.cert_days = a.get("cert_warn_days", 14)
        self.ignore = {int(x) for x in (a.get("ignore_guests") or [])}
        self.backup_ignore = {int(x) for x in (a.get("backup_ignore") or [])}
        self.cooldown = a.get("cooldown_minutes", 360) * 60
        self.state = {}
        self.sent = {}
        self.counts = {}
        self.ready = False
        self.started = time.time()

    def _cool(self, key, seconds=None):
        """Return True if a notification for ``key`` may be sent now, and start its cooldown."""
        now = time.time()
        if now - self.sent.get(key, 0) < (seconds or self.cooldown):
            return False
        self.sent[key] = now
        return True

    def evaluate(self, summary, error, services, backups, certs):
        """Compare the latest snapshot with the previous state and send notifications for changes."""
        prev = self.state.get("proxmox_error")
        if self.ready and error and not prev:
            self.n.send("Proxmox unreachable", error, "bad", ping=True)
        if self.ready and prev and not error:
            self.n.send("Proxmox reachable again", "", "ok")
        self.state["proxmox_error"] = bool(error)
        if summary:
            self._nodes(summary)
            self._guests(summary)
            self.ready = True
        self._services(services)
        if summary and time.time() - self.started > 120 and self._cool("daily", 86400):
            self._daily(summary, backups, certs)

    def _nodes(self, summary):
        for n in summary["nodes"]:
            key = "node:" + n["name"]
            was = self.state.get(key)
            if self.ready and was is True and not n["online"]:
                self.n.send(f"Node offline: {n['name']}", "The node is no longer responding.", "bad", ping=True)
            if self.ready and was is False and n["online"]:
                self.n.send(f"Node back online: {n['name']}", "", "ok")
            self.state[key] = n["online"]
            if not n["online"]:
                continue
            for label, pct, thr in (("RAM", mem_percent(n), self.ram), ("Storage", disk_percent(n), self.disk)):
                ck = f"{label}:{n['name']}"
                self.counts[ck] = self.counts.get(ck, 0) + 1 if pct >= thr else 0
                if self.counts[ck] >= 3 and self._cool(ck):
                    self.n.send(f"{label} almost full: {n['name']}", f"{pct:.0f}% in use (threshold {thr}%).",
                                "warn", [("Node", n["name"]), (label, f"{pct:.0f}%")])

    def _guests(self, summary):
        for g in summary["guests"]:
            key = f"guest:{g['id']}"
            was = self.state.get(key)
            name = f"{g['name']} ({g['type']} {g['id']})"
            if self.ready and g["id"] not in self.ignore:
                if was is True and not g["running"]:
                    self.n.send(f"Stopped: {name}", f"On node {g['node']}.", "bad", ping=True)
                elif was is False and g["running"]:
                    self.n.send(f"Started: {name}", f"On node {g['node']}.", "ok")
            self.state[key] = g["running"]

    def _services(self, services):
        for s in services or []:
            key = "svc:" + s["name"]
            fails = self.counts.get(key, 0)
            fails = 0 if s["ok"] else fails + 1
            self.counts[key] = fails
            down = self.state.get(key + ":down", False)
            if fails >= 2 and not down:
                self.state[key + ":down"] = True
                if self.ready:
                    self.n.send(f"Service down: {s['name']}", "Two consecutive checks failed.", "bad", ping=True)
            elif s["ok"] and down:
                self.state[key + ":down"] = False
                if self.ready:
                    self.n.send(f"Service recovered: {s['name']}", "", "ok")

    def _daily(self, summary, backups, certs):
        if backups is not None:
            limit = time.time() - self.backup_hours * 3600
            old = []
            for g in summary["guests"]:
                if g["id"] in self.backup_ignore:
                    continue
                b = backups.get(g["id"])
                if not b or b["last"] < limit:
                    old.append(f"{g['name']} ({g['id']})" + ("" if b else " - never backed up"))
            if old:
                self.n.send("Outdated backups", "\n".join(old)[:3000], "warn")
        soon = [f"{c['name']}: {c['days']} days left" for c in certs or []
                if c.get("days") is not None and c["days"] < self.cert_days]
        if soon:
            self.n.send("Certificate expiring soon", "\n".join(soon), "warn", ping=True)
